"""packages.backup + packages.azzio -- the OPTIONAL cloud / USB backup targets (step 4).

By default `backup` writes its two local archives and nothing else -- USB and Google Drive
upload are DISABLED. The OPT-IN is `azzio backup --configure` (short `-c`): it registers a USB
mount and/or a Google Drive rclone remote into a small user-owned config, and `backup` then ALSO
copies the freshly built archives to whatever is enabled. These tests pin that contract:

  * config default is all-disabled (a missing/corrupt config -> local-only behaviour);
  * config load/save round-trips only the known keys, 0600;
  * any_target_enabled() gates the whole transfer;
  * the enabled path INVOKES the transfer (targets.copy_archives_to_targets), the disabled
    path does NOT -- verified through the real backup.main() with the transfer mocked;
  * targets.usb_target_ready() is the mount detection (unmounted -> skipped, not fatal);
  * copy_to_usb rotates the previous generation aside and copies the archives;
  * the rclone flags carry the resumable tuning (--retries 1 + --low-level-retries);
  * `rclone` is in the package manifest;
  * `azzio backup --configure`/`-c` bundles into the guest CLI and is dispatched + has usage.

stdlib-only; the transfer itself is mocked so no real rclone/USB is needed.
"""

from __future__ import annotations

import ast
import os

import paths
from packages.backup import config as cfgmod
from packages.backup import targets as tgt


# --- config: default disabled, round-trip, gating ---------------------------
def _isolate_config(tmp_path, monkeypatch):
    """Point config.CONFIG_PATH at a temp file so tests never touch the real ~/.config."""
    path = str(tmp_path / "azzio-backup" / "backup.cfg")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    return path


def test_config_default_is_all_disabled(tmp_path, monkeypatch):
    """The out-of-the-box contract: with NO config file, every target is disabled and
    any_target_enabled() is False -- so `backup` does local archives only."""
    _isolate_config(tmp_path, monkeypatch)
    cfg = cfgmod.load()
    assert cfg["usb_enabled"] is False and cfg["gdrive_enabled"] is False
    assert cfgmod.any_target_enabled(cfg) is False
    assert cfgmod.exists() is False


def test_config_corrupt_file_degrades_to_defaults(tmp_path, monkeypatch):
    """A damaged config (not JSON) must never break `backup` -- load() falls back to the
    all-disabled defaults rather than raising."""
    path = _isolate_config(tmp_path, monkeypatch)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write("{ this is not json")
    cfg = cfgmod.load()
    assert cfg == {"usb_enabled": False, "usb_root": "",
                   "gdrive_enabled": False, "gdrive_remote": ""}


def test_config_save_round_trips_known_keys_only_and_is_0600(tmp_path, monkeypatch):
    """save() persists only the four known keys (dropping any extra a caller passes) and
    the file is 0600. load() reads them back."""
    path = _isolate_config(tmp_path, monkeypatch)
    cfgmod.save({"usb_enabled": True, "usb_root": "/run/media/main/stick",
                 "gdrive_enabled": True, "gdrive_remote": "gdrive:",
                 "bogus": "dropped"})
    with open(path) as handle:
        raw = handle.read()
    assert "bogus" not in raw
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    cfg = cfgmod.load()
    assert cfg["usb_enabled"] is True and cfg["usb_root"] == "/run/media/main/stick"
    assert cfg["gdrive_enabled"] is True and cfg["gdrive_remote"] == "gdrive:"
    assert cfgmod.any_target_enabled(cfg) is True


# --- targets: USB mount detection + rotation + copy -------------------------
def test_usb_target_ready_detects_mounted_writable_dir(tmp_path):
    """usb_target_ready() is the mount detection: a present writable dir is ready; an empty
    path or a non-existent one (drive unplugged) is not."""
    good = tmp_path / "stick"
    good.mkdir()
    assert tgt.usb_target_ready(str(good)) is True
    assert tgt.usb_target_ready("") is False
    assert tgt.usb_target_ready(str(tmp_path / "not_mounted")) is False


def test_copy_to_usb_rotates_previous_and_copies(tmp_path):
    """copy_to_usb() moves an existing archive at the USB root into previous_backups/ before
    copying the new archives in (previous-generation rotation), and returns True."""
    usb = tmp_path / "stick"
    usb.mkdir()
    # A previous generation already on the stick.
    (usb / "backup.tar.gz.gpg").write_bytes(b"OLD")
    # The freshly built archives to copy.
    src = tmp_path / "home"
    src.mkdir()
    new_home = src / "backup.tar.gz.gpg"
    new_home.write_bytes(b"NEW-HOME")
    new_pw = src / "passwords.tar.gz.gpg"
    new_pw.write_bytes(b"NEW-PW")

    assert tgt.copy_to_usb([str(new_home), str(new_pw)], str(usb)) is True
    # Old one rotated aside...
    assert (usb / "previous_backups" / "backup.tar.gz.gpg").read_bytes() == b"OLD"
    # ...new ones present at the root.
    assert (usb / "backup.tar.gz.gpg").read_bytes() == b"NEW-HOME"
    assert (usb / "passwords.tar.gz.gpg").read_bytes() == b"NEW-PW"


def test_copy_to_usb_skips_unmounted_target_without_raising(tmp_path, capsys):
    """An unmounted / unwritable USB root is a WARNING and a False return -- never an
    exception (a missing stick must not fail the whole backup)."""
    missing = str(tmp_path / "nope")
    src = tmp_path / "a.tar.gz.gpg"
    src.write_bytes(b"x")
    assert tgt.copy_to_usb([str(src)], missing) is False
    assert "not mounted" in capsys.readouterr().out.lower()


def test_rclone_flags_are_resumable_no_recount():
    """The PROMPT's key rclone requirement: a multi-GB upload must never re-upload/re-count.
    Pin the resumable tuning -- chunked + --retries 1 + --low-level-retries."""
    flags = tgt.GDRIVE_RCLONE_FLAGS
    assert "--drive-chunk-size" in flags
    i = flags.index("--retries")
    assert flags[i + 1] == "1", "one top-level attempt -> no whole-file restart/re-count"
    assert "--low-level-retries" in flags


def test_normalise_remote_appends_colon_only_when_needed():
    """An rclone remote is 'name:' or 'name:path'. A bare name gets a ':' appended; a value
    that already has a ':' must NOT get a second one (regression for the double-colon bug
    where 'drive:/path/' became 'drive:/path/:'). The result always ends in ':' or '/' so a
    basename can be joined straight onto it."""
    assert tgt._normalise_remote("gdrive") == "gdrive:"
    assert tgt._normalise_remote("gdrive:") == "gdrive:"          # unchanged, no ':' doubled
    assert tgt._normalise_remote("drive:/tmp/fakedrive/") == "drive:/tmp/fakedrive/"
    assert tgt._normalise_remote("drive:sub") == "drive:sub/"     # dir prefix gets a slash
    # A joined basename addresses a file at the root/dir in every form.
    for r in ("gdrive", "gdrive:", "drive:/tmp/fakedrive/", "drive:sub"):
        joined = tgt._normalise_remote(r) + "backup.tar.gz.gpg"
        assert joined.count(":") == 1 and "::" not in joined


def test_copy_to_gdrive_uses_rclone_copy_with_the_flags(tmp_path, monkeypatch, capsys):
    """copy_to_gdrive() invokes `rclone copy <archive> <remote>` WITH the resumable flags,
    then verifies by size. Mock the rclone runner so no real Drive is needed; assert the
    copy command + flags and that a size-verified upload returns True."""
    monkeypatch.setattr(tgt, "_have_rclone", lambda: True)
    archive = tmp_path / "backup.tar.gz.gpg"
    archive.write_bytes(b"1234567890")  # 10 bytes
    calls = []

    class _Result:
        returncode = 0
        stdout = "10;backup.tar.gz.gpg\n"   # lsf --format sp -> "<size>;<name>"

    def fake_rclone(args):
        calls.append(args)
        return _Result()

    monkeypatch.setattr(tgt, "_rclone", fake_rclone)
    assert tgt.copy_to_gdrive([str(archive)], "gdrive:") is True
    # A copy call happened with the archive, the remote, and the resumable flags.
    copy_calls = [c for c in calls if c and c[0] == "copy"]
    assert copy_calls, "expected an `rclone copy`"
    c = copy_calls[0]
    assert str(archive) in c and "gdrive:" in c
    assert "--retries" in c and "--low-level-retries" in c


def test_copy_archives_to_targets_noop_when_disabled(tmp_path, monkeypatch):
    """With both targets disabled, copy_archives_to_targets() does nothing (no rclone/USB
    call) and returns True."""
    called = {"gdrive": False, "usb": False}
    monkeypatch.setattr(tgt, "copy_to_gdrive", lambda *a: called.__setitem__("gdrive", True) or True)
    monkeypatch.setattr(tgt, "copy_to_usb", lambda *a: called.__setitem__("usb", True) or True)
    cfg = {"usb_enabled": False, "gdrive_enabled": False, "usb_root": "", "gdrive_remote": ""}
    assert tgt.copy_archives_to_targets(["/x/backup.tar.gz.gpg"], cfg) is True
    assert called == {"gdrive": False, "usb": False}


# --- backup.main() integration: enabled -> transfer runs; disabled -> not ----
import shutil  # noqa: E402

_PASS = "correct horse battery staple"


def _has_gpg():
    return shutil.which("gpg") is not None


def _make_home_with_vault(tmp_path):
    from packages.backup import archive as arch
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / "Documents").mkdir()
    (home / "Documents" / "n.txt").write_text("hi")
    vault = home / "Vault"
    vault.mkdir()
    plain = vault / "passwords.txt"
    plain.write_text("site\tsecret\n")
    assert arch.gpg_encrypt_file(str(plain), str(vault / "passwords.txt.gpg"), _PASS)
    plain.unlink()
    return home


def _run_backup(home, passphrase):
    from packages.backup import backup as app
    responses = [passphrase, passphrase]
    old_home = os.environ.get("HOME")
    old_prompt = app.archive.prompt_passphrase
    os.environ["HOME"] = str(home)
    app.archive.prompt_passphrase = lambda confirm=True: responses.pop(0)
    try:
        return app.main([])
    finally:
        app.archive.prompt_passphrase = old_prompt
        if old_home is not None:
            os.environ["HOME"] = old_home


def test_backup_invokes_transfer_only_when_a_target_is_enabled(tmp_path, monkeypatch):
    """End-to-end gate through the REAL backup.main(): when the opt-in config enables a
    target the built archives are handed to targets.copy_archives_to_targets; when the
    config is the default (disabled) that function is NOT called. The transfer itself is
    mocked so no real rclone/USB runs."""
    if not _has_gpg():
        import pytest
        pytest.skip("gpg not installed on the test host")
    from packages.backup import backup as app

    seen = {"count": 0, "archives": None, "cfg": None}

    def fake_transfer(archives, cfg):
        seen["count"] += 1
        seen["archives"] = list(archives)
        seen["cfg"] = cfg
        return True

    monkeypatch.setattr(app.targets, "copy_archives_to_targets", fake_transfer)

    # 1) DISABLED (default config) -> transfer NOT called.
    monkeypatch.setattr(app.config, "load",
                        lambda: {"usb_enabled": False, "gdrive_enabled": False,
                                 "usb_root": "", "gdrive_remote": ""})
    home1 = _make_home_with_vault(tmp_path / "d1")
    assert _run_backup(home1, _PASS) == 0
    assert seen["count"] == 0, "disabled config must not invoke any transfer"

    # 2) ENABLED (gdrive on) -> transfer called once, with the built archive paths.
    monkeypatch.setattr(app.config, "load",
                        lambda: {"usb_enabled": False, "gdrive_enabled": True,
                                 "usb_root": "", "gdrive_remote": "gdrive:"})
    home2 = _make_home_with_vault(tmp_path / "d2")
    assert _run_backup(home2, _PASS) == 0
    assert seen["count"] == 1, "enabled config must invoke the transfer exactly once"
    assert any(a.endswith("backup.tar.gz.gpg") for a in seen["archives"])
    assert seen["cfg"]["gdrive_enabled"] is True


# --- the manifest carries rclone --------------------------------------------
def test_rclone_is_in_the_manifest():
    """The optional Drive upload shells out to `rclone`; it must be shipped (NOT from the
    AUR) or the opt-in cannot work. Tokenize the manifest exactly as the build does."""
    text = paths.PACKAGES_FILE.read_text()
    toks = [tok for line in text.splitlines()
            if (tok := line.split("#", 1)[0].strip())]
    assert "rclone" in toks


# --- targets/config ship with the backup package ----------------------------
def test_config_and_targets_modules_ship():
    """config.py + targets.py are real sources in the backup package, so packaging.py's
    module discovery ships them to LIB_DIR (no packaging edit needed)."""
    from packages.backup import packaging as bk
    shipped = {e["dest"] for e in bk.emit_plan()}
    assert f"{bk.LIB_DIR}/config.py" in shipped
    assert f"{bk.LIB_DIR}/targets.py" in shipped


# --- azzio backup --configure bundles + dispatches -------------------------
def _azzio_bundle():
    from packages.azzio.bundle import bundle_source
    return bundle_source()


def test_azzio_backup_configure_is_bundled_and_dispatched():
    """Step five item 5: the opt-in is now `azzio backup --configure` / `-c`. The module
    bundles into the single guest CLI script, main() dispatches the `backup` subcommand to
    cmd_backup (which routes --configure/-c to cmd_backup_setup), usage advertises it, and the
    OLD `backup-setup` surface is GONE from the bundle + usage. Pin all of that."""
    src = _azzio_bundle()
    ast.parse(src)  # the whole bundle stays valid Python with the new module in it
    assert "bundled from backup_targets.py" in src
    # The opt-in flow function survives (driven behind the new flag) ...
    assert "def cmd_backup_setup(" in src
    # ... and the new dispatcher + `backup` case wire it to --configure/-c.
    assert "def cmd_backup(" in src
    assert 'cmd == "backup"' in src
    assert '("--configure", "-c")' in src
    # The new surface is advertised in usage().
    assert "backup --configure" in src
    # The OLD name is GONE from the bundle entirely (no `backup-setup` command, no usage line).
    assert "backup-setup" not in src
    assert 'cmd == "backup-setup"' not in src


def test_azzio_backup_bare_and_unknown_flag_do_not_run_a_backup():
    """A bare `azzio backup` must NOT run a backup -- it points the user at the real `backup`
    command (usage + exit 2). An unknown flag also errors (exit 2). `--configure`/`-c` (and its
    sub-forms) route into cmd_backup_setup. Driven through the exec'd bundle so the real
    dispatch wiring is exercised (no interactive prompts hit -- only --status/--disable/errors)."""
    src = _azzio_bundle()
    ns = {"__name__": "azzio_bundle_backup_dispatch"}
    exec(compile(src, "azzio", "exec"), ns)
    main = ns["main"]

    # bare `azzio backup` -> usage + exit 2 (does NOT run a backup).
    assert main(["backup"]) == 2
    # unknown flag -> exit 2.
    assert main(["backup", "--bogus"]) == 2
    # -h/--help -> exit 0 (informational).
    assert main(["backup", "--help"]) == 0
    # `--configure --status` and `-c --status` both reach the status path (exit 0, no prompt).
    assert main(["backup", "--configure", "--status"]) == 0
    assert main(["backup", "-c", "--status"]) == 0


def test_azzio_backup_enable_usb_validates_and_enables(tmp_path, monkeypatch):
    """Step six: the TUI drives a NON-interactive `azzio backup --configure --enable-usb <PATH>`.
    It enables the USB target ONLY when PATH is a present, writable directory RIGHT NOW (the same
    check the interactive picker does), and writes the config backup.config.load() then reads.
    A missing path leaves USB disabled and exits 2 (a dead target can never enable)."""
    src = _azzio_bundle()
    ns = {"__name__": "azzio_bundle_enable_usb"}
    exec(compile(src, "azzio", "exec"), ns)

    path = str(tmp_path / "azzio-backup" / "backup.cfg")
    ns["_BACKUP_CFG_PATH"] = path
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)

    stick = tmp_path / "stick"
    stick.mkdir()
    # A present, writable mount enables through the real dispatch (main -> cmd_backup -> setup).
    assert ns["main"](["backup", "--configure", "--enable-usb", str(stick)]) == 0
    cfg = cfgmod.load()
    assert cfg["usb_enabled"] is True and cfg["usb_root"] == str(stick)
    assert cfg["gdrive_enabled"] is False   # gdrive untouched

    # A non-existent path is REFUSED (exit 2) and does not enable / does not clobber usb_root.
    missing = str(tmp_path / "not_mounted")
    assert ns["main"](["backup", "--configure", "--enable-usb", missing]) == 2
    cfg2 = cfgmod.load()
    assert cfg2["usb_enabled"] is True and cfg2["usb_root"] == str(stick)   # unchanged

    # Missing the PATH argument is a usage error (exit 2).
    assert ns["main"](["backup", "--configure", "--enable-usb"]) == 2


def test_azzio_backup_enable_gdrive_verifies_remote_before_enabling(tmp_path, monkeypatch):
    """Step six: `azzio backup --configure --enable-gdrive <REMOTE>` (non-interactive, TUI-
    driven). It enables Google Drive ONLY when rclone exists AND `rclone about <remote>` succeeds
    (the same verify the interactive flow does); a bare name gets a ':' appended; an unreachable
    remote leaves Drive disabled and exits 2. rclone is faked so no real Drive is needed."""
    src = _azzio_bundle()
    ns = {"__name__": "azzio_bundle_enable_gdrive"}
    exec(compile(src, "azzio", "exec"), ns)

    path = str(tmp_path / "azzio-backup" / "backup.cfg")
    ns["_BACKUP_CFG_PATH"] = path
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)

    # Fake rclone at the MODULE-FUNCTION level, INSIDE the exec'd namespace (not the shared
    # subprocess module -- mutating that would leak into other tests). _have("rclone") -> present;
    # _rclone_remote_ok succeeds only for "gdrive:" (the verified remote).
    ns["_have"] = lambda prog: prog == "rclone"
    ns["_rclone_remote_ok"] = lambda remote: remote == "gdrive:"

    # A bare name "gdrive" -> ':' appended -> verified -> enabled.
    assert ns["main"](["backup", "--configure", "--enable-gdrive", "gdrive"]) == 0
    cfg = cfgmod.load()
    assert cfg["gdrive_enabled"] is True and cfg["gdrive_remote"] == "gdrive:"
    assert cfg["usb_enabled"] is False   # usb untouched

    # An unreachable remote is REFUSED (exit 2) and does not enable.
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "az2" / "backup.cfg"))
    ns["_BACKUP_CFG_PATH"] = str(tmp_path / "az2" / "backup.cfg")
    assert ns["main"](["backup", "--configure", "--enable-gdrive", "nope"]) == 2
    assert cfgmod.load()["gdrive_enabled"] is False

    # Missing the REMOTE argument is a usage error (exit 2).
    assert ns["main"](["backup", "--configure", "--enable-gdrive"]) == 2


def test_azzio_backup_enable_surfaces_are_bundled_and_helpered():
    """The two non-interactive enable surfaces bundle into the guest CLI (so the TUI can call
    them) and are advertised in the help. Pins the exact flags + helper functions so the C TUI
    rows and this surface cannot drift."""
    src = _azzio_bundle()
    ast.parse(src)
    assert "--enable-usb" in src
    assert "--enable-gdrive" in src
    assert "def _enable_usb_path(" in src
    assert "def _enable_gdrive_remote(" in src


def test_azzio_backup_configure_writes_the_same_config_backup_reads(tmp_path, monkeypatch):
    """The `azzio backup --configure` module and packages/backup/config.py MUST agree on the
    config path + keys (they live in different install dirs and cannot import each other).
    Exec the bundled module in isolation, point it and config.py at the same temp path, run
    `--configure --disable` THROUGH the real dispatch (main -> cmd_backup -> cmd_backup_setup),
    and confirm backup.config.load() reads exactly what it wrote."""
    src = _azzio_bundle()
    ns = {"__name__": "azzio_bundle_test"}
    exec(compile(src, "azzio", "exec"), ns)

    path = str(tmp_path / "azzio-backup" / "backup.cfg")
    # Point BOTH sides at the same temp config path.
    ns["_BACKUP_CFG_PATH"] = path
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)

    # Seed an enabled config via backup.config.save, then have the REAL dispatch clear it:
    # main -> cmd_backup (`backup` case) -> --configure -> cmd_backup_setup(["--disable"]).
    cfgmod.save({"usb_enabled": True, "usb_root": "/run/media/main/x",
                 "gdrive_enabled": True, "gdrive_remote": "gdrive:"})
    assert ns["main"](["backup", "--configure", "--disable"]) == 0

    # backup.config (the reader) must see both targets disabled now.
    cfg = cfgmod.load()
    assert cfg["usb_enabled"] is False and cfg["gdrive_enabled"] is False
    # And the schema keys match on both sides.
    assert set(ns["_BACKUP_CFG_DEFAULTS"]) == set(cfgmod._DEFAULTS)
