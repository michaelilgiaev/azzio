#!/usr/bin/env python3
"""The `backup` command -- Azzio's home-directory + password-store backup.

Running `backup` prompts ONCE for a passphrase and then produces TWO encrypted
archives in your home directory:

  ~/backup.tar.gz.gpg      the HOME archive. It gathers every TOP-LEVEL directory in
                           ``~`` (resolved live, never a hard-coded path), SKIPPING the
                           ``Ignore`` directory and every dot file / dot directory
                           (hidden config, out of scope), and it keeps SYMLINKS as
                           links (recording where they point) so a restore recreates
                           the exact same link. Home-relative arcnames ("Documents/...",
                           not "/home/<user>/...") so ``unpack`` drops each folder
                           straight back into home.

  ~/passwords.tar.gz.gpg   the PASSWORD-STORE archive. The store is the single file
                           ~/Vault/passwords.txt.gpg (see packages/passwords/config.py,
                           DEFAULT_ENCRYPTED). The passphrase you type is ALSO tried
                           against that store (it is itself gpg-encrypted with your
                           passwords master password):
                             * if it DECRYPTS the store, the passwords archive is built
                               from the DECRYPTED contents, so after a restore you can
                               actually reach your passwords again;
                             * if it does NOT (wrong master password), the run does NOT
                               fail -- ~/backup.tar.gz.gpg is still made and a warning
                               notes the store could not be included;
                             * if the store does not exist at all, the passwords archive
                               is skipped gracefully (a note is printed).

Both archives are encrypted with the SAME passphrase you typed (one prompt).

WHY tar + gpg (no rar), and how the passphrase is kept off the process list, live in
the shared archive.py helper this script imports. This stays Python standard library
only; the only external binary is ``gpg`` (gnupg), already in the Azzio manifest.
The cloud upload / rotation / GitHub / QEMU machinery from the original prototype in
``data/backup.py`` is deliberately NOT here -- we build this up one focused step at a
time.
"""

import os
import sys
import tempfile
import time

# The app is a flat directory installed to LIB_DIR; make the sibling ``archive`` module
# importable whether run via the launcher (which cd's into LIB_DIR) or imported by the
# test suite as packages.backup.backup. Mirrors packages/passwords/passwords.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import archive  # noqa: E402  (deliberately after the sys.path bootstrap above)
import config   # noqa: E402  (opt-in cloud/USB target config; default: all disabled)
import targets  # noqa: E402  (the optional cloud/USB copy step)
import user_interface  # noqa: E402  (shared CLI presentation helpers)


# The one directory in HOME we never back up (a deliberate dumping ground). Matched by
# exact top-level name, case-sensitively -- only ``~/Ignore`` is skipped, not a nested
# ``Foo/Ignore``.
IGNORE_DIR_NAME = "Ignore"

# The encrypted password store `backup` also archives, relative to HOME. Kept in sync
# with packages/passwords/config.py (DEFAULT_ENCRYPTED = ~/Vault/passwords.txt.gpg): the
# passwords manager reads/writes its store there, and `unpack passwords.tar.gz.gpg`
# restores it back to exactly this path.
VAULT_REL = "Vault/passwords.txt.gpg"

# The top-level home directory that holds the password store (``Vault``). It is EXCLUDED
# from the home archive because the store is handled by its OWN archive
# (~/passwords.tar.gz.gpg): archiving ~/Vault here too would bundle the encrypted store a
# second time and let `unpack backup.tar.gz.gpg` scatter it back separately from the
# dedicated passwords restore. Keeping the two archives' responsibilities disjoint (home
# files vs. the password store) is the least-surprising split. Derived from VAULT_REL so
# it cannot drift.
VAULT_DIR_NAME = VAULT_REL.split("/", 1)[0]

# The two deliverables (in HOME). Names are FIXED -- no date stamp -- so `unpack` can
# recognise them by name and restore each to the right place. They are also EXCLUDED from
# the home selection (see select_entries): both live at the top level of HOME, so without
# this a SECOND `backup` run would bundle the previous run's archives into the new one,
# growing it every time.
HOME_ARCHIVE_NAME = "backup.tar.gz.gpg"
PASSWORDS_ARCHIVE_NAME = "passwords.tar.gz.gpg"
_OWN_ARCHIVES = frozenset({HOME_ARCHIVE_NAME, PASSWORDS_ARCHIVE_NAME})

# __pycache__ / compiled Python are never worth archiving; dropped everywhere in the
# tree (the top-level dot-file rule already hides most clutter, but these can sit inside
# a backed-up project dir).
_ALWAYS_SKIP_NAMES = {"__pycache__"}
_ALWAYS_SKIP_SUFFIXES = (".pyc", ".pyo")


def home_dir():
    """The current user's home directory -- ``~`` expanded (delegates to archive)."""
    return archive.home_dir()


def _is_hidden(name):
    """A dot file / dot directory (hidden config we skip at the top level)."""
    return name.startswith(".")


def select_entries(home):
    """Return the sorted top-level entries in ``home`` to back up.

    The selection rule (the PROMPT's spec):
      * every top-level entry in HOME is a candidate,
      * EXCEPT the ``Ignore`` directory,
      * EXCEPT dot files / dot directories (hidden config),
      * EXCEPT the ``Vault`` directory (the password store has its OWN archive -- see
        VAULT_DIR_NAME -- so it is not bundled into the home archive too),
      * EXCEPT our own two deliverables (``backup.tar.gz.gpg`` / ``passwords.tar.gz.gpg``)
        left in HOME by a previous run, so re-running never archives the last run's output,
      * and symlinks ARE included (kept as links; tar records their target).

    Returns bare NAMES (relative to ``home``); the archiver adds them with ``arcname``
    so the archive is rooted at the home dir, not at ``/``."""
    entries = []
    for name in os.listdir(home):
        if name in (IGNORE_DIR_NAME, VAULT_DIR_NAME):
            continue
        if name in _OWN_ARCHIVES:
            continue
        if _is_hidden(name):
            continue
        entries.append(name)
    return sorted(entries)


def _tar_filter(tarinfo):
    """Per-entry filter for ``tarfile.add(recursive=True)``.

    Drops __pycache__ dirs and ``.pyc``/``.pyo`` files anywhere in the tree. Everything
    else is kept verbatim -- crucially, tarfile does NOT dereference symlinks here
    (``TarInfo`` for a link has ``type == SYMTYPE`` and carries its ``linkname``), so a
    symlink is stored AS a link together with where it points. Returning ``None``
    excludes the entry."""
    base = os.path.basename(tarinfo.name)
    if base in _ALWAYS_SKIP_NAMES:
        return None
    if tarinfo.name.endswith(_ALWAYS_SKIP_SUFFIXES):
        return None
    return tarinfo


def prompt_passphrase():
    """Ask for the archive passphrase twice and return it once the two match (the
    write path -- a typo in a write-once key is unrecoverable)."""
    return archive.prompt_passphrase(confirm=True)


def build_home_archive(home, entries, out_path, passphrase):
    """Write ~/backup.tar.gz.gpg: the gzip-tar of ``entries`` (under ``home``), rooted
    at the home dir (home-relative arcnames), piped through gpg. Prints one clean bullet
    per top-level item as it is added (live feedback for a large home). Returns True on
    success."""
    members = ((os.path.join(home, name), name) for name in entries)
    return archive.build_encrypted_tar(
        members, out_path, passphrase, tar_filter=_tar_filter,
        on_add=user_interface.bullet,
    )


def build_passwords_archive(home, passphrase, out_path):
    """Write ~/passwords.tar.gz.gpg from the vault store, if the passphrase unlocks it.

    The store ~/Vault/passwords.txt.gpg is itself gpg-encrypted (with the user's
    passwords master password). We try the SAME passphrase the user gave `backup`
    against it:
      * store missing            -> return "missing" (caller prints a skip note),
      * passphrase does not match -> return "mismatch" (caller warns, run still ok),
      * passphrase matches        -> decrypt to a private temp plaintext, archive THAT
                                     (as ``Vault/passwords.txt`` inside the archive), and
                                     return "ok". `unpack` re-encrypts on restore, so the
                                     recovered data ends up back at ~/Vault/.

    The decrypted plaintext is written under a 0700 temp dir and shredded in a finally,
    so the cleartext password list never lingers on disk."""
    store_path = os.path.join(home, VAULT_REL)
    if not os.path.exists(store_path):
        return "missing"

    # Decrypt into a private (0700) temp dir; gpg writes the plaintext 0600. If the
    # passphrase is wrong, gpg_decrypt_to_file removes the partial output and returns
    # False -- we treat that as "mismatch" (do not fail the whole run).
    tmp_dir = tempfile.mkdtemp(prefix="azzio-backup-")
    plain_path = os.path.join(tmp_dir, "passwords.txt")
    try:
        if not archive.gpg_decrypt_to_file(store_path, plain_path, passphrase):
            return "mismatch"
        # Archive the DECRYPTED store as Vault/passwords.txt so `unpack` can re-encrypt
        # it back to ~/Vault/passwords.txt.gpg. One member, no pycache filter needed.
        members = [(plain_path, "Vault/passwords.txt")]
        ok = archive.build_encrypted_tar(members, out_path, passphrase)
        return "ok" if ok else "failed"
    finally:
        archive.shred_dir(tmp_dir)


def _tilde(home, path):
    """``path`` shown home-relative as ``~/name`` when it is inside ``home`` (tidier than
    a full ``/home/<user>/...`` in the output), else the path unchanged."""
    if path == home:
        return "~"
    prefix = home.rstrip("/") + "/"
    return "~/" + path[len(prefix):] if path.startswith(prefix) else path


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print("Usage: backup\n\n"
              "Create TWO encrypted archives in your home directory from one\n"
              "passphrase prompt:\n"
              "  ~/backup.tar.gz.gpg     your home dir's top-level folders (skipping\n"
              "                          'Ignore' and hidden dot files, symlinks kept\n"
              "                          as links).\n"
              "  ~/passwords.tar.gz.gpg  your password store (~/Vault/passwords.txt.gpg),\n"
              "                          included only if the passphrase unlocks it.\n\n"
              "Restore either with:  unpack <archive>")
        return 0

    rc = archive.require_gpg_or_exit()
    if rc is not None:
        return rc

    home = home_dir()
    entries = select_entries(home)
    have_vault = os.path.exists(os.path.join(home, VAULT_REL))
    # Only bail out entirely when there is genuinely nothing to do -- no home dirs to
    # archive AND no password store to include. If the home selection is empty but a vault
    # exists, we still go on and attempt the passwords archive (a user whose only backable
    # thing is their password store must still get passwords.tar.gz.gpg).
    if not entries and not have_vault:
        print(f"Nothing to back up in {home} "
              f"(everything is hidden or in '{IGNORE_DIR_NAME}', and no password store).")
        return 0

    # NO header / plan block: the output is deliberately STRIPPED DOWN (step five item 3).
    # There is no "Azzio backup" banner, no rule, and no Home/Items/Store/Skip rows -- and
    # crucially no "Skip: ... 'Vault' ..." line, which was factually WRONG (the password store
    # IS backed up, into ~/passwords.tar.gz.gpg). We go straight to the passphrase prompt (its
    # live keyboard line is printed inside prompt_passphrase, right where the user types) and
    # then compact per-archive result lines + one final summary. No empty spacer lines.
    passphrase = prompt_passphrase()

    start = time.time()
    written = []   # the local archive paths written, for the summary count + optional copy

    # 1) The home archive -- the primary deliverable. Built only when there is something to
    #    put in it; if it is attempted and FAILS, the whole run fails.
    if entries:
        print("Building the home archive ...")
        home_out = os.path.join(home, HOME_ARCHIVE_NAME)
        if not build_home_archive(home, entries, home_out, passphrase):
            return 1
        home_size = os.path.getsize(home_out) if os.path.exists(home_out) else 0
        user_interface.result_line("home archive", _tilde(home, home_out),
                                   archive.fmt_size(home_size))
        written.append(home_out)

    # 2) The passwords archive -- best-effort. A missing store or a non-matching
    #    passphrase warns but never fails the run.
    print("Building the password-store archive ...")
    pw_out = os.path.join(home, PASSWORDS_ARCHIVE_NAME)
    status = build_passwords_archive(home, passphrase, pw_out)
    if status == "ok":
        pw_size = os.path.getsize(pw_out) if os.path.exists(pw_out) else 0
        user_interface.result_line("password store", _tilde(home, pw_out),
                                   archive.fmt_size(pw_size))
        written.append(pw_out)
    elif status == "missing":
        # NB: this fires ONLY when the store file genuinely does not exist -- it is not the
        # old (wrong) "Vault is skipped" claim. Worded to avoid implying ~/Vault is excluded
        # from the backup (the store IS backed up whenever it is present).
        user_interface.note(f"no password store to archive "
                            f"(~/{VAULT_REL} not found).")
    elif status == "mismatch":
        user_interface.warn(f"the passphrase did not unlock ~/{VAULT_REL}; "
                            "password store NOT included.")
    else:  # "failed" -- gpg/tar error building the passwords archive
        user_interface.warn("could not build the password-store archive.")

    # 3) Optional cloud / USB copy -- ONLY when the user opted in via `azzio backup
    #    --configure` (config default is all-disabled -> this whole block is skipped and
    #    behaviour is exactly the local-only backup). The copy is best-effort: a
    #    failed/absent target warns but never fails the run, since the local archives are the
    #    primary deliverable and always remain.
    cfg = config.load()
    if written and config.any_target_enabled(cfg):
        targets.copy_archives_to_targets(written, cfg)

    count = len(written)
    print(f"Done in {int(time.time() - start)}s. "
          f"{count} archive{'' if count == 1 else 's'} written to {home}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
