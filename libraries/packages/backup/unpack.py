#!/usr/bin/env python3
"""The `unpack` command -- restore a `backup`-made ``.tar.gz.gpg`` archive.

`unpack` is the reverse of `backup`: it RESTORES an encrypted archive's contents BACK
where they belong, so a fresh machine gets its files and passwords back. It prompts for
the passphrase, runs ``gpg -d | tar xz``, and extracts. It restores by DESTINATION,
not into a directory named after the archive -- each of the two known archives goes to
where that data actually lives:

  unpack backup.tar.gz.gpg      -> restore the home dirs BACK INTO ~/. The home archive
                                   was built with home-relative arcnames ("Documents/...",
                                   not "/home/<user>/..."), so extracting it with ~ as the
                                   destination drops each top-level folder straight back
                                   into home, and the symlinks are recreated AS links
                                   pointing where they used to.

  unpack passwords.tar.gz.gpg   -> restore the password store to ~/Vault/. The passwords
                                   archive holds the DECRYPTED store as ``Vault/passwords.txt``;
                                   `unpack` re-encrypts that plaintext with the SAME
                                   passphrase back to ~/Vault/passwords.txt.gpg -- exactly
                                   where the `passwords` manager expects its store
                                   (packages/passwords/config.py, DEFAULT_ENCRYPTED) -- so
                                   `passwords` can immediately unlock it again.

Any other ``*.tar.gz.gpg`` (an UNKNOWN archive, not one of our two) is extracted into
~/ as well -- the same home-relative restore as ``backup.tar.gz.gpg`` (documented
choice: our archives are home-rooted, so ~ is the safe default destination; a
system-rooted archive would need a deliberate destination we do not guess at). A
non-``.tar.gz.gpg`` argument, or a missing file, is rejected with a clear error.

OVERWRITE POLICY: a restore's job is to make the target look like the backup, so an
existing file at a restored path is OVERWRITTEN (the least surprising behaviour for a
restore -- "put my files back" means the backed-up version wins). Directories are merged
into (files inside them are individually overwritten); files not present in the archive
are left untouched. This is documented and tested.

Python standard library only; the only external binary is ``gpg`` (gnupg).
"""

import os
import sys
import tempfile

# The app is a flat directory installed to LIB_DIR; make the sibling ``archive`` module
# importable whether run via the launcher (which cd's into LIB_DIR) or imported by the
# test suite as packages.backup.unpack. Mirrors packages/passwords/passwords.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import archive  # noqa: E402  (deliberately after the sys.path bootstrap above)
import user_interface  # noqa: E402  (shared CLI presentation helpers)


# The two archives `backup` produces, by exact name (see backup.py). `unpack` recognises
# these and restores each to its real home; anything else is treated as an unknown
# home-rooted archive.
HOME_ARCHIVE_NAME = "backup.tar.gz.gpg"
PASSWORDS_ARCHIVE_NAME = "passwords.tar.gz.gpg"

# Where the password store lives (kept in sync with packages/passwords/config.py and
# backup.VAULT_REL). The passwords archive restores here.
VAULT_DIR_REL = "Vault"
VAULT_PLAINTEXT_ARCNAME = "Vault/passwords.txt"
VAULT_ENCRYPTED_REL = "Vault/passwords.txt.gpg"

# The required archive suffix. `unpack` only handles our encrypted gzip-tar archives.
ARCHIVE_SUFFIX = ".tar.gz.gpg"


def home_dir():
    """The current user's home directory -- ``~`` expanded (delegates to archive)."""
    return archive.home_dir()


def _is_within(base, target):
    """True if ``target`` is ``base`` itself or lives underneath it. Used to reject
    archive members whose (possibly ``../``-laden) names would escape the destination
    directory -- a defensive guard against a malicious or corrupt archive writing
    outside ~ (or ~/Vault)."""
    base = os.path.realpath(base)
    target = os.path.realpath(target)
    return target == base or target.startswith(base + os.sep)


def _safe_members(tar, dest_dir):
    """Yield only the archive members that stay inside ``dest_dir`` once extracted.

    tar member names are joined onto ``dest_dir``; any member resolving outside it
    (via ``../`` or an absolute name) is skipped with a warning rather than allowed to
    escape. For symlinks/hardlinks the link TARGET is not policed here (a symlink may
    legitimately point anywhere, e.g. back at another home dir) -- only the member's own
    path is constrained, which is what stops a traversal write."""
    for member in tar:
        member_path = os.path.join(dest_dir, member.name)
        if not _is_within(dest_dir, member_path):
            user_interface.warn(f"skipping unsafe path in archive: {member.name}")
            continue
        yield member


def _extract_one(tar, member, dest_dir):
    """Extract a single ``member`` into ``dest_dir``, preserving symlink targets verbatim.

    Python 3.12+ made ``tar.extract`` default to the ``'data'`` extraction filter, which
    REFUSES a symlink whose target is an absolute path (a hardening default). Our backup
    deliberately records each symlink pointing WHERE IT USED TO (often an absolute path
    like /home/<user>/Documents), and the PROMPT requires the link be recreated exactly,
    so we pass the ``fully_trusted`` filter to keep the target byte-for-byte. That is safe
    here because we have ALREADY constrained every member's own path to stay inside
    ``dest_dir`` (see _safe_members / _is_within); only the link's target may point
    outside, which is the intended restore behaviour. On pre-3.12 pythons ``extract`` has
    no ``filter`` kwarg, so we fall back to the plain call."""
    try:
        tar.extract(member, path=dest_dir, filter="fully_trusted")
    except TypeError:  # Python < 3.12: no `filter` keyword
        tar.extract(member, path=dest_dir)


def _extract_home_relative(tar, dest_dir):
    """Extract every (safe) member of ``tar`` into ``dest_dir``.

    Existing files are OVERWRITTEN (see the module OVERWRITE POLICY): tarfile replaces a
    regular file in place, and for a symlink we remove any existing name first so the
    link is recreated cleanly (tarfile will not overwrite an existing path with a
    symlink otherwise). This is the home-relative restore used for backup.tar.gz.gpg and
    for unknown archives."""
    for member in _safe_members(tar, dest_dir):
        target = os.path.join(dest_dir, member.name)
        # For a link (sym or hard), clear any existing name so it is recreated as a link
        # rather than colliding. Regular files/dirs tarfile overwrites/merges in place.
        if (member.issym() or member.islnk()) and (
                os.path.islink(target) or os.path.exists(target)):
            try:
                os.remove(target)
            except OSError:
                pass
        _extract_one(tar, member, dest_dir)
        user_interface.bullet(member.name)


def restore_home(enc_path, home):
    """Restore a home-rooted archive (``backup.tar.gz.gpg`` or an unknown archive) into
    ``home``. Prompts for the passphrase, then streams gpg -> tar and extracts each
    top-level folder back into home, recreating symlinks as links. Returns True on
    success."""
    passphrase = archive.prompt_passphrase(confirm=False)
    print("Restoring ...")
    ok = archive.gpg_decrypt_stream(enc_path, passphrase, home, _extract_home_relative)
    if ok:
        print(f"Restored into {home}")
    return ok


def restore_passwords(enc_path, home):
    """Restore the password store from ``passwords.tar.gz.gpg`` to ~/Vault/.

    The archive holds the DECRYPTED store as ``Vault/passwords.txt``. We decrypt the
    archive to a private temp dir, pull that plaintext out, then RE-ENCRYPT it with the
    same passphrase to ~/Vault/passwords.txt.gpg -- exactly where the `passwords` manager
    expects its store, so it can unlock it immediately. The temp plaintext is shredded
    afterwards. An existing store is OVERWRITTEN (restore policy). Returns True on
    success."""
    passphrase = archive.prompt_passphrase(confirm=False)
    print("Restoring ...")

    tmp_dir = tempfile.mkdtemp(prefix="azzio-unpack-")
    try:
        # Extract the archive's members into the temp dir (only Vault/passwords.txt is
        # expected). gpg_decrypt_stream feeds a home-relative extractor rooted at tmp.
        if not archive.gpg_decrypt_stream(
                enc_path, passphrase, tmp_dir, _extract_home_relative):
            return False

        plain_path = os.path.join(tmp_dir, VAULT_PLAINTEXT_ARCNAME)
        if not os.path.exists(plain_path):
            print(f"Error: {os.path.basename(enc_path)} did not contain "
                  f"{VAULT_PLAINTEXT_ARCNAME}; nothing to restore.", file=sys.stderr)
            return False

        vault_dir = os.path.join(home, VAULT_DIR_REL)
        os.makedirs(vault_dir, exist_ok=True)
        store_path = os.path.join(home, VAULT_ENCRYPTED_REL)
        # Re-encrypt the recovered plaintext back to the store the passwords manager
        # reads. Same passphrase -> the user can unlock it with what they just typed.
        if not archive.gpg_encrypt_file(plain_path, store_path, passphrase):
            print("Error: could not re-encrypt the password store.", file=sys.stderr)
            return False
        user_interface.bullet(VAULT_ENCRYPTED_REL)
        print(f"Restored the password store to {store_path}")
        return True
    finally:
        archive.shred_dir(tmp_dir)


def classify(arg_basename):
    """Which restore an archive name triggers: "home", "passwords", or "unknown"
    (unknown is restored home-relative, like "home"). Pure/name-based so it is easy to
    test."""
    if arg_basename == PASSWORDS_ARCHIVE_NAME:
        return "passwords"
    if arg_basename == HOME_ARCHIVE_NAME:
        return "home"
    return "unknown"


def validate_arg(path):
    """Validate the archive argument. Returns an error string (for the user) or None if
    the path is an acceptable, existing ``*.tar.gz.gpg`` file."""
    if not path.endswith(ARCHIVE_SUFFIX):
        return (f"'{path}' is not a {ARCHIVE_SUFFIX} archive. `unpack` only restores "
                f"archives made by `backup`.")
    if not os.path.exists(path):
        return f"no such file: {path}"
    if not os.path.isfile(path):
        return f"not a file: {path}"
    return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print("Usage: unpack <archive.tar.gz.gpg>\n\n"
              "Restore a backup made by `backup`, putting its contents back where\n"
              "they belong:\n"
              "  unpack backup.tar.gz.gpg     restore your home dirs into ~/\n"
              "  unpack passwords.tar.gz.gpg  restore your password store to ~/Vault/\n\n"
              "Any other *.tar.gz.gpg archive is restored into ~/ (home-relative).\n"
              "Existing files are overwritten.")
        return 0 if argv else 1

    rc = archive.require_gpg_or_exit()
    if rc is not None:
        return rc

    # Resolve the archive argument to an ABSOLUTE path against the caller's current
    # working directory BEFORE validating it. The launcher no longer cd's into LIB_DIR,
    # so a relative name like ``backup.tar.gz.gpg`` typed from ~ already resolves against
    # the user's cwd; abspath() is belt-and-braces so validation and gpg both see the
    # same fully-qualified path regardless of how the process was launched. classify()
    # still keys off the BASENAME, so the destination mapping is unchanged.
    arg = os.path.abspath(argv[0])
    error = validate_arg(arg)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    home = home_dir()
    kind = classify(os.path.basename(arg))

    # MINIMAL output (step five item 4): NO "Azzio unpack" header, NO rule, and NO
    # Archive:/Restore: rows. The prompt shows just the LIVE keyboard/Caps-Lock line and
    # "Passphrase:" -- both printed inside prompt_passphrase(), right where the user types
    # (the keyboard line repaints itself while the prompt waits; see archive.prompt_passphrase
    # / live_keyboard_line). Then a compact "Restoring ..." + the final "Restored ..." line.
    if kind == "passwords":
        ok = restore_passwords(arg, home)
    else:
        ok = restore_home(arg, home)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
