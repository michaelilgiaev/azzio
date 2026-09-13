#!/usr/bin/env python3
"""Shared GPG + tar helpers for the `backup` and `unpack` commands.

Both shipped commands roll data through the SAME encrypted-archive pipeline (a
gzip-compressed tar piped through ``gpg --symmetric`` AES256), so the pipeline and
its supporting bits live here once and are imported by both entry scripts. Keeping
this in one module means the archive format, the passphrase-handling (never on argv),
and the gpg-availability check cannot drift between the two commands.

WHY tar + gpg (no rar). tar stores symlinks AS links by default (never dereferenced),
which is exactly the "save the symlink and where it points" requirement, and both
``gpg`` (gnupg) and Python's ``tarfile`` are already present in Azzio -- no
proprietary ``rar`` dependency. The passphrase is the archive's only key; it is never
written anywhere and never reaches gpg on the command line (where ``ps`` could read
it) -- it is fed over a private pipe / stdin instead.

This is Python standard library only (tarfile + subprocess + threading); the only
external binary is ``gpg``.
"""

import os
import subprocess
import sys
import tarfile
import threading
from getpass import getpass

# The flat app installs every module side by side in LIB_DIR. archive.py is imported by
# BOTH entry scripts (which each do this same insert), but it is also imported directly
# by the test suite -- so make its own directory importable here too, before the sibling
# ``import keyboard`` below, so that import resolves no matter who loads archive first
# (mirrors the bootstrap in backup.py / unpack.py; a no-op when the dir is already on the
# path).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# keyboard.py is the same self-contained libX11 readout the passwords manager ships; we
# print its one-line layout / Caps-Lock status right before every passphrase prompt
# (mirroring passwords.py) so a wrong layout or a stuck Caps Lock is visible before the
# user types a (hidden) passphrase. Degrades to "Keyboard: unknown" off X.
import keyboard  # noqa: E402  (after the sys.path bootstrap above)
# live_keyboard_line.py makes that one status line LIVE: it repaints it in place while the
# getpass is blocking, so toggling Caps Lock or switching layout at the prompt updates it
# immediately (mirrors data/backup.py's update_layout_line thread). Degrades to a single
# static print off-tty / off-X.
import live_keyboard_line  # noqa: E402  (after the sys.path bootstrap above)


# gpg flags shared by every invocation here. ``--batch --yes`` keep it non-interactive
# and let it overwrite a stale output; ``--pinentry-mode loopback`` makes gpg take the
# passphrase from the fd we hand it (via ``--passphrase-fd``) instead of trying to pop a
# pinentry dialog on a headless/piped run. Mirrors packages/passwords/cryptography.py.
_GPG_BATCH = ["gpg", "--batch", "--yes", "--pinentry-mode", "loopback"]


def home_dir():
    """The current user's home directory -- ``~`` expanded. Never a hard-coded path:
    a user may have named their account anything, so we resolve it live (``$HOME``,
    falling back to the password database) every run."""
    return os.path.expanduser("~")


def which(name):
    """Tiny ``shutil.which`` shim (kept dependency-light). Returns the path to an
    executable ``name`` on PATH, or None."""
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def have_gpg():
    """True when the ``gpg`` binary is available (the one external dependency)."""
    return which("gpg") is not None


def fmt_size(num_bytes):
    """Human-friendly byte count for the summary lines (GB/MB/KB/B)."""
    for unit, scale in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if num_bytes >= scale:
            return f"{num_bytes / scale:.2f} {unit}"
    return f"{num_bytes} B"


def prompt_passphrase(confirm=True):
    """Ask for the passphrase and return it.

    With ``confirm`` (the default, used by ``backup`` when CREATING archives) it asks
    twice and only returns once the two entries match -- a typo in a write-once
    encryption key is unrecoverable, so it is caught up front. Without ``confirm``
    (used by ``unpack``, which only DECRYPTS) a single prompt is enough: a wrong
    passphrase simply makes gpg fail, with nothing lost. An empty passphrase is always
    rejected (gpg refuses it and it defeats the encryption).

    The live keyboard layout / Caps-Lock line (keyboard.keyboard_status_line()) is shown
    immediately before EACH getpass and REPAINTS ITSELF IN PLACE while the prompt waits, so
    toggling Caps Lock or switching layout at the prompt updates the line immediately (see
    live_keyboard_line -- the same daemon-thread ANSI redraw as data/backup.py). Off a tty /
    off X it degrades to a single static "Keyboard: ..." print (the prior behaviour)."""
    while True:
        first = _prompt_with_keyboard_line(
            "Encryption passphrase: " if confirm else "Passphrase: ")
        if not first:
            print("Passphrase cannot be empty.")
            continue
        if not confirm:
            return first
        second = _prompt_with_keyboard_line("Confirm passphrase: ")
        if first == second:
            return first
        print("Passphrases do not match. Try again.")


def _prompt_with_keyboard_line(prompt):
    """getpass(``prompt``) with the LIVE keyboard/Caps-Lock line refreshing above it while it
    blocks. A thin adapter around live_keyboard_line.prompt_with_live_keyboard_line that binds
    the status source (keyboard.keyboard_status_line) and the input (getpass). Kept tiny and
    separate so the live-redraw plumbing lives in its own module and this stays readable."""
    return live_keyboard_line.prompt_with_live_keyboard_line(
        lambda: getpass(prompt), keyboard.keyboard_status_line)


def _feed_thread(write_fd, passphrase):
    """Return a started daemon thread that writes ``passphrase`` to ``write_fd`` then
    closes it. Feeding from a thread (after the gpg child already exists and is draining
    the read end) can never deadlock, even if the passphrase somehow exceeded the pipe
    buffer -- unlike writing inline before anything is reading."""

    def _feed():
        try:
            os.write(write_fd, (passphrase + "\n").encode("utf-8"))
        finally:
            os.close(write_fd)

    thread = threading.Thread(target=_feed, daemon=True)
    thread.start()
    return thread


def _cleanup_partial(path):
    """Remove a half-written file so a failed run never leaves a corrupt artifact."""
    try:
        os.remove(path)
    except OSError:
        pass


def shred_dir(tmp_dir):
    """Best-effort secure removal of a temp plaintext dir.

    Walks bottom-up so nested subdirectories are handled: every file is overwritten with
    zeros (and fsync'd) then unlinked, and every directory -- including nested ones like
    ``<tmp>/Vault`` -- is removed, so no empty skeleton is left behind. Used by both
    `backup` and `unpack` for the decrypted-password-store scratch dir: the cleartext must
    not linger after the command, and the temp dir itself is fully cleaned up. All errors
    are swallowed (this is a cleanup path, not a place to fail the run)."""
    for root, dirs, files in os.walk(tmp_dir, topdown=False):
        for name in files:
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
                with open(path, "r+b", buffering=0) as handle:
                    handle.write(b"\x00" * size)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError:
                pass
            try:
                os.remove(path)
            except OSError:
                pass
        for name in dirs:
            entry = os.path.join(root, name)
            # os.walk classifies a symlink-to-a-directory under `dirs`; rmdir would fail on
            # it (ENOTDIR) and leave the link behind, which then blocks the parent's removal.
            # Unlink links; rmdir only real directories. We never follow the link (walk does
            # not descend symlinks), so nothing outside the scratch dir is touched.
            try:
                if os.path.islink(entry):
                    os.remove(entry)
                else:
                    os.rmdir(entry)
            except OSError:
                pass
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass


def build_encrypted_tar(members, out_path, passphrase, tar_filter=None,
                        on_add=None):
    """Write a gzip-tar of ``members`` through gpg to ``out_path`` (encrypted).

    ``members`` is an iterable of ``(source_path, arcname)`` pairs: each source is
    added to the tar under ``arcname`` (so the archive is rooted where the caller wants
    -- home-relative for the home backup, ``Vault/...`` for the password store). The
    tar stream is produced in-process and piped straight to ``gpg --symmetric`` (AES256)
    so the PLAINTEXT archive never touches disk -- only the encrypted ``.tar.gz.gpg`` is
    written. The passphrase reaches gpg over a private fd (``--passphrase-fd``), never
    on argv.

    ``tar_filter`` is an optional per-entry ``tarfile`` filter (used to drop pycache).
    ``on_add(arcname)`` is an optional callback invoked after each top-level member is
    added (for progress lines). Returns True on success; on failure the partial output
    is removed and False is returned."""
    read_fd, write_fd = os.pipe()
    gpg_cmd = _GPG_BATCH + [
        "--symmetric", "--cipher-algo", "AES256",
        "--passphrase-fd", str(read_fd), "-o", out_path,
    ]
    # Spawn gpg FIRST (it is already draining the read end), THEN feed the passphrase
    # from a short-lived thread; see _feed_thread for why this ordering cannot deadlock.
    proc = subprocess.Popen(gpg_cmd, stdin=subprocess.PIPE, pass_fds=(read_fd,))
    os.close(read_fd)  # the child owns the read end now
    pass_thread = _feed_thread(write_fd, passphrase)

    try:
        with tarfile.open(fileobj=proc.stdin, mode="w:gz") as tar:
            for source_path, arcname in members:
                tar.add(source_path, arcname=arcname, recursive=True,
                        filter=tar_filter)
                if on_add is not None:
                    on_add(arcname)
    except (OSError, tarfile.TarError) as error:
        proc.stdin.close()
        proc.wait()
        pass_thread.join()
        _cleanup_partial(out_path)
        print(f"Archive failed: {error}")
        return False

    proc.stdin.close()
    returncode = proc.wait()
    pass_thread.join()
    if returncode != 0:
        _cleanup_partial(out_path)
        print(f"gpg failed (exit {returncode}).")
        return False
    return True


def gpg_decrypt_to_file(enc_path, out_path, passphrase):
    """Decrypt ``enc_path`` -> ``out_path`` with ``passphrase`` (over stdin).

    Used to test a candidate passphrase against the vault store and to recover its
    plaintext. The passphrase is written to gpg's stdin (``--passphrase-fd 0``), never
    on argv. Returns True on success. On failure (wrong passphrase or a corrupt store)
    any partial output is removed and False is returned -- so a bad passphrase leaves no
    plaintext behind. Mirrors packages/passwords/cryptography.decrypt_to_file."""
    cmd = _GPG_BATCH + ["--passphrase-fd", "0", "-o", out_path, "-d", enc_path]
    # Tighten the umask so gpg creates the plaintext as 0600 from the start (no brief
    # world-readable window) -- the same guard the passwords manager uses.
    old_umask = os.umask(0o077)
    try:
        proc = subprocess.run(cmd, input=(passphrase + "\n").encode("utf-8"),
                              capture_output=True)
    finally:
        os.umask(old_umask)
    if proc.returncode != 0:
        _cleanup_partial(out_path)
        return False
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        pass
    return True


def gpg_encrypt_file(src_path, out_path, passphrase):
    """Encrypt ``src_path`` -> ``out_path`` (symmetric AES256) with ``passphrase``.

    Used by `unpack` to re-encrypt the recovered password-store plaintext back to
    ~/Vault/passwords.txt.gpg. The passphrase is fed over stdin (never on argv); the
    output store is created 0600. Returns True on success, False otherwise. Mirrors
    packages/passwords/cryptography.encrypt so the store `unpack` writes is bit-for-bit
    the kind the passwords manager reads."""
    cmd = _GPG_BATCH + ["--symmetric", "--cipher-algo", "AES256",
                        "--passphrase-fd", "0", "-o", out_path, src_path]
    old_umask = os.umask(0o077)
    try:
        proc = subprocess.run(cmd, input=(passphrase + "\n").encode("utf-8"),
                              capture_output=True)
    finally:
        os.umask(old_umask)
    if proc.returncode != 0:
        _cleanup_partial(out_path)
        return False
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        pass
    return True


def gpg_decrypt_stream(enc_path, passphrase, dest_dir, tar_extract):
    """Decrypt ``enc_path`` and pipe the plaintext tar into ``tar_extract``.

    ``gpg -d`` writes the decrypted ``.tar.gz`` to a pipe; a reader thread opens that
    pipe as a gzip tar stream and hands the open ``tarfile.TarFile`` to the
    ``tar_extract(tar, dest_dir)`` callback (which decides membership / destination).
    The plaintext archive never lands on disk. The passphrase is fed over stdin. Returns
    True on success (gpg exit 0 AND the extraction callback did not raise); False
    otherwise. ``dest_dir`` is passed straight through to the callback."""
    read_fd, write_fd = os.pipe()
    cmd = _GPG_BATCH + ["--passphrase-fd", "0", "-d", enc_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=write_fd,
                            stderr=subprocess.PIPE)
    os.close(write_fd)  # the child owns the write end now

    extract_error = {}

    def _extract():
        try:
            with tarfile.open(fileobj=os.fdopen(read_fd, "rb"), mode="r|gz") as tar:
                tar_extract(tar, dest_dir)
        except Exception as error:  # noqa: BLE001 -- reported back to the caller below
            extract_error["error"] = error
            # Drain the pipe so gpg is never blocked writing into a dead reader.
            try:
                while os.read(read_fd, 65536):
                    pass
            except OSError:
                pass

    reader = threading.Thread(target=_extract, daemon=True)
    reader.start()

    if passphrase is not None:
        try:
            proc.stdin.write((passphrase + "\n").encode("utf-8"))
        except OSError:
            pass
    # Do NOT use proc.communicate() here: it flushes stdin unconditionally, and once we
    # have closed stdin ourselves that flush raises "flush of closed file" on the CPython
    # builds the CI runners ship (gpg reads the ciphertext from the file arg and can exit
    # before the flush, so stdin is already closed/broken). Close stdin, wait for gpg, and
    # read stderr directly -- the same close()+wait() idiom build_encrypted_tar() uses.
    # stdout is our own pipe, drained by the reader thread, so only stderr needs reading.
    try:
        proc.stdin.close()
    except OSError:
        pass
    stderr = proc.stderr.read()
    proc.stderr.close()
    proc.wait()
    reader.join()

    if "error" in extract_error:
        print(f"Extraction failed: {extract_error['error']}")
        return False
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip().splitlines()
        hint = detail[-1] if detail else f"exit {proc.returncode}"
        print(f"Decryption failed: {hint}")
        return False
    return True


def require_gpg_or_exit():
    """Print the install hint and return exit code 1 if gpg is missing, else None.
    A tiny convenience the two entry scripts share at startup."""
    if not have_gpg():
        print("Error: 'gpg' not found. Install it with: sudo pacman -S gnupg",
              file=sys.stderr)
        return 1
    return None
