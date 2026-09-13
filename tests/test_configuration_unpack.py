"""packages.backup -- the `unpack` command (restore side of the backup system).

`unpack` reverses a `backup`-made ``.tar.gz.gpg`` archive, putting its contents BACK
where they belong: the home archive restores into ~/, and the password store restores
into ~/Vault/ (re-encrypted so the `passwords` manager can unlock it). These tests pin
the RESTORE contract the distribution PROMPT (step two) specifies:

  * `unpack backup.tar.gz.gpg`    -> home dirs land directly in ~/, symlinks recreated
                                     AS links pointing where they used to;
  * `unpack passwords.tar.gz.gpg` -> the store lands at ~/Vault/passwords.txt.gpg and
                                     decrypts back to the original plaintext;
  * a non-``.tar.gz.gpg`` argument, or a missing file, is REJECTED with a clear error;
  * an UNKNOWN ``*.tar.gz.gpg`` is restored into ~/ (home-relative) -- documented choice;
  * an existing file at a restored path is OVERWRITTEN (least-surprising restore policy);
  * a member whose path would escape the destination (``../``) is refused (traversal guard).

They build REAL gpg archives (gpg is the app's declared dep and is on the test host), so
the actual encrypt/decrypt/extract path is exercised, not just the source text.
"""

from __future__ import annotations

import os
import shutil

import pytest

import paths
from packages.backup import backup as backup_app
from packages.backup import unpack as up
from packages.backup import archive as arch


_PASS = "correct horse battery staple"


def _has_gpg():
    return shutil.which("gpg") is not None


requires_gpg = pytest.mark.skipif(not _has_gpg(), reason="gpg not installed on the test host")


# --- fixtures: a fake home with the two real archives -----------------------
def _make_source_home(tmp_path, vault_secret="gmail\thunter2\n"):
    """A fake HOME with dirs, a symlink, and a real gpg vault store."""
    home = tmp_path / "src"
    home.mkdir()
    (home / "Documents").mkdir()
    (home / "Documents" / "note.txt").write_text("hello world")
    (home / "Pictures").mkdir()
    (home / "Pictures" / "pic.txt").write_text("img")
    (home / "Link").symlink_to(home / "Documents")
    vault = home / "Vault"
    vault.mkdir()
    plain = vault / "passwords.txt"
    plain.write_text(vault_secret)
    assert arch.gpg_encrypt_file(str(plain), str(vault / "passwords.txt.gpg"), _PASS)
    plain.unlink()
    return home


def _build_archives(tmp_path):
    """Build the real backup.tar.gz.gpg + passwords.tar.gz.gpg for a fresh source home;
    return (home_archive_path, passwords_archive_path, vault_secret)."""
    secret = "gmail\thunter2\nbank\tswordfish\n"
    home = _make_source_home(tmp_path, vault_secret=secret)
    entries = backup_app.select_entries(str(home))
    home_arc = str(home / backup_app.HOME_ARCHIVE_NAME)
    assert backup_app.build_home_archive(str(home), entries, home_arc, _PASS)
    pw_arc = str(home / backup_app.PASSWORDS_ARCHIVE_NAME)
    assert backup_app.build_passwords_archive(str(home), _PASS, pw_arc) == "ok"
    return home_arc, pw_arc, secret


def _fresh_home(tmp_path, name="dst"):
    home = tmp_path / name
    home.mkdir()
    return home


def _run_unpack(argv, home, passphrase):
    """Invoke up.main(argv) with HOME set and the prompt fed `passphrase` (unpack asks
    once, no confirm). Returns the exit code.

    We patch ``up.archive.prompt_passphrase`` -- ``up.archive`` is the very module object
    the entry script calls into (the flat app imports its sibling as a top-level
    ``archive`` module via sys.path, which is NOT the same object as
    ``packages.backup.archive``), so this reaches the real call site."""
    responses = [passphrase] if passphrase is not None else []

    def fake_prompt(confirm=True):
        return responses.pop(0)

    old_home = os.environ.get("HOME")
    old_prompt = up.archive.prompt_passphrase
    os.environ["HOME"] = str(home)
    up.archive.prompt_passphrase = fake_prompt
    try:
        return up.main(argv)
    finally:
        up.archive.prompt_passphrase = old_prompt
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:  # pragma: no cover - HOME is always set in CI
            del os.environ["HOME"]


# --- classify() / validate_arg(): the name + argument contract --------------
def test_classify_maps_the_two_known_archives_and_defaults_unknown():
    """The two deliverables are recognised by exact name; anything else is "unknown"
    (restored home-relative). Pin the mapping so the destinations cannot silently drift."""
    assert up.classify("backup.tar.gz.gpg") == "home"
    assert up.classify("passwords.tar.gz.gpg") == "passwords"
    assert up.classify("something-else.tar.gz.gpg") == "unknown"


def test_validate_arg_rejects_non_archive_and_missing(tmp_path):
    """A non-``.tar.gz.gpg`` argument and a missing file are both rejected (with a message);
    a real ``*.tar.gz.gpg`` file validates."""
    assert up.validate_arg("/tmp/foo.zip") is not None            # wrong extension
    assert up.validate_arg(str(tmp_path / "gone.tar.gz.gpg")) is not None  # missing
    good = tmp_path / "x.tar.gz.gpg"
    good.write_text("not really gpg, but exists")
    assert up.validate_arg(str(good)) is None


def test_main_rejects_bad_extension_and_missing_file(tmp_path):
    """End-to-end: `unpack` exits non-zero on a non-archive arg and on a missing file,
    without prompting for a passphrase."""
    home = _fresh_home(tmp_path)
    assert _run_unpack(["/tmp/not_an_archive.zip"], home, None) == 1
    assert _run_unpack([str(tmp_path / "missing.tar.gz.gpg")], home, None) == 1


def test_main_with_no_args_is_usage_error():
    """No argument prints usage and exits non-zero (the archive is required)."""
    assert up.main([]) == 1


# --- the RESTORE destinations (the heart of step two) -----------------------
@requires_gpg
def test_unpack_home_archive_restores_into_home_with_symlink(tmp_path):
    """`unpack backup.tar.gz.gpg` drops the top-level folders straight back into ~/ and
    recreates the symlink AS a link pointing where it used to."""
    home_arc, _pw, _secret = _build_archives(tmp_path)
    dst = _fresh_home(tmp_path)
    assert _run_unpack([home_arc], dst, _PASS) == 0
    assert (dst / "Documents" / "note.txt").read_text() == "hello world"
    assert (dst / "Pictures").is_dir()
    link = dst / "Link"
    assert link.is_symlink(), "the symlink must be restored AS a link"
    assert os.readlink(str(link)).endswith("Documents")


@requires_gpg
def test_unpack_passwords_archive_restores_store_to_vault(tmp_path):
    """`unpack passwords.tar.gz.gpg` restores the store to ~/Vault/passwords.txt.gpg (where
    the `passwords` manager expects it) and it decrypts back to the ORIGINAL plaintext with
    the same passphrase."""
    _home, pw_arc, secret = _build_archives(tmp_path)
    dst = _fresh_home(tmp_path)
    assert _run_unpack([pw_arc], dst, _PASS) == 0
    store = dst / "Vault" / "passwords.txt.gpg"
    assert store.exists(), "store must be restored under ~/Vault/"
    # It must decrypt back to the original plaintext (i.e. the user can reach passwords).
    out = tmp_path / "roundtrip.txt"
    assert arch.gpg_decrypt_to_file(str(store), str(out), _PASS)
    assert out.read_text() == secret


@requires_gpg
def test_unpack_unknown_archive_restores_home_relative(tmp_path):
    """An UNKNOWN ``*.tar.gz.gpg`` (not one of our two) is restored into ~/ home-relative --
    the documented default. Rename the home archive to an unknown name and confirm its
    contents still land in ~/."""
    home_arc, _pw, _secret = _build_archives(tmp_path)
    unknown = os.path.join(os.path.dirname(home_arc), "mystery.tar.gz.gpg")
    os.rename(home_arc, unknown)
    dst = _fresh_home(tmp_path)
    assert _run_unpack([unknown], dst, _PASS) == 0
    assert (dst / "Documents" / "note.txt").read_text() == "hello world"


@requires_gpg
def test_restore_overwrites_existing_files(tmp_path):
    """OVERWRITE POLICY: a restore makes the target match the backup, so an existing file at
    a restored path is overwritten (the least-surprising behaviour for "put my files
    back")."""
    home_arc, _pw, _secret = _build_archives(tmp_path)
    dst = _fresh_home(tmp_path)
    # Pre-seed a stale version of a file the archive will restore.
    (dst / "Documents").mkdir()
    (dst / "Documents" / "note.txt").write_text("STALE CONTENT")
    assert _run_unpack([home_arc], dst, _PASS) == 0
    assert (dst / "Documents" / "note.txt").read_text() == "hello world"


@requires_gpg
def test_unpack_rejects_wrong_passphrase(tmp_path):
    """A wrong passphrase makes gpg fail; `unpack` exits non-zero and restores nothing."""
    home_arc, _pw, _secret = _build_archives(tmp_path)
    dst = _fresh_home(tmp_path)
    assert _run_unpack([home_arc], dst, "the WRONG passphrase") == 1
    assert not (dst / "Documents").exists()


# --- the traversal guard ----------------------------------------------------
def test_is_within_blocks_paths_that_escape_the_destination(tmp_path):
    """The extractor only writes members that stay inside the destination; a ``../`` name
    resolving outside it is refused (defence against a malicious/corrupt archive)."""
    base = tmp_path / "home"
    base.mkdir()
    assert up._is_within(str(base), str(base / "Documents" / "x"))
    assert up._is_within(str(base), str(base))
    assert not up._is_within(str(base), str(tmp_path / "outside"))
    assert not up._is_within(str(base), str(base / ".." / "escape"))


# --- step five item 4: the unpack output is MINIMAL -------------------------
@requires_gpg
def test_unpack_output_is_minimal_no_header_no_rows(tmp_path, capsys):
    """Step five item 4: `unpack` output is MINIMAL. There is NO "Azzio unpack" header,
    NO rule, and NO Archive:/Restore: rows -- the prompt is just the (live) keyboard line +
    "Passphrase:" (printed inside prompt_passphrase, covered by the live-keyboard tests). Only
    the compact "Restoring ..." progress + the final "Restored into ..." line remain, with no
    empty spacer lines. (Here prompt_passphrase is stubbed by _run_unpack, so we pin the
    surrounding output: the header/rows are gone and the summary stays.)"""
    home_arc, _pw, _secret = _build_archives(tmp_path)
    dst = _fresh_home(tmp_path)
    assert _run_unpack([home_arc], dst, _PASS) == 0
    out = capsys.readouterr().out
    # The header/rows are gone.
    assert "Azzio unpack" not in out
    assert "Archive:" not in out and "Restore:" not in out
    # No horizontal RULE line (a whole line that is a run of the rule glyph or ASCII dashes).
    # We check for a rule LINE, not a bare '-', so a hyphen inside a path (e.g. the pytest
    # tmp dir) does not trip it.
    assert "─" not in out                                # no box-drawing rule anywhere
    for line in out.splitlines():
        assert not (len(line) >= 8 and set(line) == {"-"}), f"stray rule line: {line!r}"
    # The compact progress + summary remain.
    assert "Restoring ..." in out
    assert f"Restored into {dst}" in out
    # No blank spacer lines (matches the stripped `backup` output).
    assert not any(line == "" for line in out.splitlines()), repr(out)


# --- regression: a RELATIVE archive path resolves against the caller's cwd ---
@requires_gpg
def test_unpack_relative_archive_resolves_against_caller_cwd(tmp_path):
    """Regression for the #1 unpack bug: `unpack backup.tar.gz.gpg` (a RELATIVE name)
    typed from the directory that holds the archive must find and restore it.

    THE BUG (reproduced here at the app level). The launcher did `cd '{LIB_DIR}'` before
    exec'ing python, so by the time unpack.py saw the relative arg the process cwd was
    LIB_DIR -- NOT the user's dir -- and the archive (sitting in the user's dir) was not
    there: "Error: no such file: backup.tar.gz.gpg" even with it right in front of you. The
    launcher tests (test_configuration_backup.py) pin the STRUCTURAL fix (no cd); this test
    pins the app BEHAVIOUR that the no-cd launcher then delivers, and DEMONSTRATES the bug
    mechanics with the two cwd worlds below.

    We DON'T just chdir to the archive dir and pass the basename -- that would pass even
    against the old code (the basename resolves against whatever cwd is). Instead we make
    the two worlds explicit:

      * BUGGY WORLD -- process cwd is a LIB_DIR-like dir that does NOT hold the archive
        (what the stray `cd` produced). The relative name must FAIL to resolve there
        (validate_arg returns an error) -- that is exactly why the cd was fatal.
      * FIXED WORLD -- process cwd is the user's dir where the archive lives (what the
        no-cd launcher preserves). The SAME relative name now resolves, restores, exits 0.
    """
    home_arc, _pw, _secret = _build_archives(tmp_path)
    arc_dir = os.path.dirname(home_arc)
    arc_name = os.path.basename(home_arc)          # relative: "backup.tar.gz.gpg"
    lib_like = tmp_path / "lib_like"               # stands in for LIB_DIR (no archive here)
    lib_like.mkdir()
    dst = _fresh_home(tmp_path)

    old_cwd = os.getcwd()
    try:
        # BUGGY WORLD: cwd is the LIB_DIR-like dir -> the relative name does NOT resolve
        # (abspath(arc_name) points into lib_like, where the archive isn't). This is the
        # failure the launcher's `cd` caused.
        os.chdir(str(lib_like))
        assert up.validate_arg(os.path.abspath(arc_name)) is not None
        assert _run_unpack([arc_name], dst, _PASS) == 1
        assert not (dst / "Documents").exists(), "nothing should have been restored"

        # FIXED WORLD: cwd is the user's dir (the no-cd launcher preserves it) -> the SAME
        # relative name resolves against cwd, restores, and exits 0.
        os.chdir(arc_dir)
        assert up.validate_arg(os.path.abspath(arc_name)) is None
        assert _run_unpack([arc_name], dst, _PASS) == 0
    finally:
        os.chdir(old_cwd)
    assert (dst / "Documents" / "note.txt").read_text() == "hello world"


def test_main_abspaths_the_relative_arg_before_validating(tmp_path):
    """Unit-level guard that main() resolves the archive arg to an ABSOLUTE path before
    validating (so a relative name is looked up against the caller's cwd, not LIB_DIR).
    Pin it in the source so the belt-and-braces abspath cannot be silently dropped."""
    src = (paths.BACKUP_DIR / "unpack.py").read_text(encoding="utf-8")
    assert "os.path.abspath(argv[0])" in src


# --- shipping: the entry lives in the package and is discovered -------------
def test_unpack_source_ships_and_parses():
    """unpack.py is a real source in the package (so packaging.py's module discovery ships
    it) and is valid Python."""
    import ast
    src = (paths.BACKUP_DIR / "unpack.py").read_text(encoding="utf-8")
    ast.parse(src)
    assert "def main(" in src
