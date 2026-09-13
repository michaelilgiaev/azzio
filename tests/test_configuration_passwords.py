"""packages.passwords -- OUR encrypted terminal password manager (the `passwords` command).

`passwords` is a GPG/AES256 terminal password manager: the master password is the GPG
passphrase (never stored), the store is a single .gpg file at ~/Vault/passwords.txt.gpg,
and running the command unlocks it, decrypts it to a session plaintext for a curses
search/select UI, then re-encrypts on quit only if something changed and always deletes
the session plaintext. The app is ONE FLAT directory now (no pwlib/ sub-library): the
entry script and every module it imports sit side by side; packages/passwords/packaging.py
is the ISO build wiring.

Why these tests matter: like the timedate/application-menu payloads, compiler.py never
inspects the CONTENT of these builders -- it blindly iterates emit_plan() and calls
emit.write_text with the (dest, mode) each entry declares. So the declarative plan + the
launcher text ARE the contract. A wrong mode makes the launcher non-executable (typing
`passwords` then fails), a launcher that does not cd into LIB_DIR breaks the entry script's
sibling `import`s at runtime, and a store path that drifts from ~/Vault silently writes the
.gpg to the wrong place. None of that raises in Python; it only shows up as a broken
`passwords` command on the built ISO. These tests pin:

  * the emit_plan() dest/mode entries + that it does not mutate module state,
  * the launcher (execs `python passwords.py "$@"` from the install dir, executable),
  * the flat-package ship contract (every runtime module installs into LIB_DIR beside the
    entry script; the build wiring does NOT ship, and the unit tests are in tests/ anyway),
  * the ~/Vault retarget (the store and session plaintext live under ~/Vault, NOT the
    old ~/Archive, per the distribution PROMPT), and the config lives in the user's home
    (not beside the root-owned code) so the setup script can write it,
  * that gnupg + xclip (the app's two system-binary deps) are in the manifest.
"""

from __future__ import annotations

import ast
import inspect

import compiler
import paths
from packages.passwords import packaging as pw
from packages.passwords import config as pwconfig


# --- emit_plan() contract ---------------------------------------------------
# The flat app ships every runtime module (0o644) into LIB_DIR plus the launcher (0o755) on
# PATH. These KEY entries must always be present with these modes; the module set as a whole
# is discovered from the source dir, so it is pinned structurally (below) rather than as a
# frozen list.
EXPECTED_KEY_PLAN = {
    "/usr/local/lib/azzio-passwords/passwords.py": 0o644,
    "/usr/local/lib/azzio-passwords/encrypt_passwords_text_tile.py": 0o644,
    "/usr/local/lib/azzio-passwords/cryptography.py": 0o644,
    "/usr/local/lib/azzio-passwords/terminal_user_interface.py": 0o644,
    "/usr/local/lib/azzio-passwords/new_entry.py": 0o644,
    "/usr/local/bin/passwords": 0o755,
}


def test_emit_plan_dest_mode_table():
    """The declarative (dest -> mode) entries compiler.py iterates. The launcher MUST be
    executable (0o755) so typing `passwords` runs it; every module is plain data (0o644, run
    through the launcher's python, never executed directly). Pin the key entries + the two
    structural rules: every non-launcher entry is a 0o644 .py under LIB_DIR, and the build
    wiring never ships (the unit tests are not beside the sources -- they live in tests/)."""
    got = {e["dest"]: e["mode"] for e in pw.emit_plan()}
    for dest, mode in EXPECTED_KEY_PLAN.items():
        assert got.get(dest) == mode, dest
    for dest, mode in got.items():
        if dest == pw.LAUNCHER_SYSTEM_PATH:
            assert mode == 0o755
        else:
            assert dest.startswith(pw.LIB_DIR + "/") and dest.endswith(".py"), dest
            assert mode == 0o644, dest
    # The build wiring must never ship to the target; the unit tests live in the top-level
    # tests/ dir (not beside the sources), so nothing test-shaped is ever in the ship set.
    assert f"{pw.LIB_DIR}/packaging.py" not in got
    assert not any(d.rsplit("/", 1)[-1].startswith("test_") for d in got)


def test_emit_plan_builders_are_callable_and_nonempty():
    """Every entry's builder returns real content (compiler.py calls builder())."""
    for e in pw.emit_plan():
        content = e["builder"]()
        assert isinstance(content, str) and content.strip(), e["dest"]


def test_emit_plan_is_pure():
    """compiler.py may call emit_plan() more than once; it must not mutate module state or
    return aliased dicts a caller could mutate. Mirrors the timedate/openbox test."""
    a = pw.emit_plan()
    b = pw.emit_plan()
    assert a == b
    a[0]["mode"] = 0o000  # mutate the returned copy
    assert pw.emit_plan()[0]["mode"] == 0o644  # module PLAN unaffected


def test_dest_paths_are_absolute_system_paths():
    """All root-owned absolute paths under /usr/local (the OFFLINE install rsyncs the live
    rootfs, so no per-user home entry is needed -- the command is on PATH for every user)."""
    for e in pw.emit_plan():
        assert e["dest"].startswith("/usr/local/"), e["dest"]


def test_launcher_name_is_the_passwords_command():
    """The command the user types is literally `passwords`: the launcher installs to
    /usr/local/bin/passwords (on PATH). This is THE deliverable the PROMPT asks for."""
    assert pw.LAUNCHER_SYSTEM_PATH == "/usr/local/bin/passwords"
    plan = {e["dest"]: e for e in pw.emit_plan()}
    assert pw.LAUNCHER_SYSTEM_PATH in plan
    assert plan[pw.LAUNCHER_SYSTEM_PATH]["mode"] == 0o755  # must be executable


# --- launcher ---------------------------------------------------------------
def test_launcher_execs_python_entry_from_install_dir():
    """The launcher cd's into the install dir (so the entry script's sibling `import`s
    resolve) and execs the system python on passwords.py, forwarding arguments so
    `passwords -h` reaches the script. `exec` so the python process replaces the shell and
    receives the signals the app traps to shred the session plaintext."""
    sh = pw.launcher_sh()
    assert sh.startswith("#!/bin/sh")
    assert f"cd '{pw.LIB_DIR}'" in sh
    assert 'exec python -u passwords.py "$@"' in sh


# --- the flat-package ship contract -----------------------------------------
def test_flat_app_ships_every_module_beside_the_entry_script():
    """The app is one flat directory: the entry script and every module it imports install
    side by side in LIB_DIR (so the runtime sys.path insert in passwords.py resolves the bare
    sibling `import`s). Pin that emit_plan() ships each source module into LIB_DIR, and that
    the entry script's own install dir is LIB_DIR."""
    shipped = {e["dest"] for e in pw.emit_plan()}
    # Every runtime .py in the source dir (minus the build wiring) must be shipped. The unit
    # tests are not in this dir anymore -- they live in the top-level tests/ dir.
    for p in paths.PASSWORDS_DIR.iterdir():
        if p.is_file() and p.suffix == ".py" and p.name != "packaging.py":
            assert f"{pw.LIB_DIR}/{p.name}" in shipped, p.name
    assert pw.ENTRY_SYSTEM_PATH.startswith(pw.LIB_DIR + "/")


def test_flat_app_ships_the_modules_the_entry_imports():
    """The entry script does `import config`, `import cryptography`,
    `import terminal_user_interface`, `from help import HELP`, `from keyboard import ...`,
    `from model import Store`; terminal_user_interface pulls forms + new_entry; forms pulls
    clipboard. All must ship or the command crashes at import on the built ISO."""
    shipped = {e["dest"] for e in pw.emit_plan()}
    for name in ("config.py", "cryptography.py", "model.py", "terminal_user_interface.py",
                 "forms.py", "new_entry.py", "help.py", "keyboard.py", "clipboard.py",
                 "clipboard_owner.py"):
        assert f"{pw.LIB_DIR}/{name}" in shipped, name


def test_shipped_modules_parse_as_python():
    """Defense-in-depth: every shipped module is syntactically valid Python (they are copied
    verbatim and run on the target, so a syntax error would only surface there)."""
    for e in pw.emit_plan():
        if e["dest"].endswith(".py"):
            ast.parse(e["builder"](), filename=e["dest"])


# --- the ~/Vault retarget (the PROMPT's explicit requirement) ---------------
def test_store_and_session_live_under_vault_not_archive():
    """The PROMPT: the .gpg must be saved to ~/Vault/passwords.txt.gpg (NOT ~/Archive).
    Pin the encrypted store, the session plaintext, and the setup-script source all under
    ~/Vault, and that nothing still points at the old ~/Archive location."""
    import os
    vault = os.path.expanduser("~/Vault")
    assert pwconfig.DEFAULT_ENCRYPTED == f"{vault}/passwords.txt.gpg"
    assert pwconfig.DEFAULT_SESSION == f"{vault}/passwords.txt"
    assert pwconfig.DEFAULT_SOURCE == f"{vault}/passwords.txt"
    for val in (pwconfig.DEFAULT_ENCRYPTED, pwconfig.DEFAULT_SESSION,
                pwconfig.DEFAULT_SOURCE):
        assert "Archive" not in val, val
    # The default the load() fallback uses when no config file is present must be the
    # ~/Vault store -- so a fresh install with no cfg still targets the right file.
    assert pwconfig.load()["encrypted_path"] == f"{vault}/passwords.txt.gpg"


def test_config_lives_in_user_home_not_beside_root_owned_code():
    """The package installs root-owned under /usr/local/lib, which a normal user cannot
    write to. The config (paths only, no secrets) must therefore live in the user's home,
    or the setup script's save() would fail with EACCES on the installed system."""
    import os
    cfg = pwconfig.CONFIG_PATH
    # Under the user's config home, and NOT under the root-owned install dir.
    assert cfg.startswith(os.path.expanduser("~/.config")) or "XDG_CONFIG_HOME" in cfg \
        or "/.config/" in cfg
    assert not cfg.startswith("/usr/local/")
    assert cfg.endswith("azzio-passwords/passwords.cfg")


# --- the compiler actually WIRES the package in (seam coverage) -------------
def test_compiler_emit_desktop_wires_passwords_in():
    """Guard the compiler-to-package SEAM: the package module's contract can be perfect
    while compiler.py forgets to invoke it, shipping an ISO where `passwords` is missing
    entirely. The package tests above call emit_plan() themselves and would NOT catch that.
    Assert the real _emit_desktop source emits the passwords plan (which, for the now-flat
    app, ships every module -- there is no separate directory copy anymore)."""
    src = inspect.getsource(compiler._emit_desktop)
    # The emit_plan loop for passwords (writes every module + the launcher).
    assert "passwords.emit_plan()" in src
    # And the module is imported under the name the wiring uses.
    assert "from packages.passwords import packaging as passwords" in \
        inspect.getsource(compiler)


# --- runtime dependencies are in the manifest -------------------------------
def test_gnupg_and_xclip_are_in_the_manifest():
    """The app shells out to `gpg` (gnupg) and `xclip`; both must be shipped or the command
    is installed but non-functional (gpg missing -> cannot unlock; xclip missing -> copy
    silently fails). Tokenize the manifest exactly as the build does."""
    text = paths.PACKAGES_FILE.read_text()
    toks = [tok for line in text.splitlines()
            if (tok := line.split("#", 1)[0].strip())]
    assert "gnupg" in toks
    assert "xclip" in toks


# --- the streamlined, self-initializing lifecycle (the NEW PROMPT) ----------
def _entry_source() -> str:
    return (paths.PASSWORDS_DIR / "passwords.py").read_text(encoding="utf-8")


def test_entry_self_initializes_and_does_not_require_the_setup_script():
    """The PROMPT: the store is 'already initialized' -- the end user must NOT have to run
    encrypt_passwords_text_tile.py or source anything. So the shipped entry script must, on a
    missing store, CREATE one itself (an empty encrypted store) rather than printing a 'run
    the setup script' message. Pin the self-init seam so a regression to the old behaviour is
    caught at build time (it never raises in Python; it only shows as a dead-end on the ISO)."""
    src = _entry_source()
    # It creates an empty store from the model rather than pointing at the setup script.
    assert "_init_store" in src
    assert "Store([]).serialize()" in src
    # The old dead-end ("run the setup script first") must be gone from the entry script.
    assert "encrypt_passwords_text_tile.py" not in src


def test_nothing_shipped_tells_the_user_to_source_bashrc():
    """Streamlined means NO 'source ~/.bashrc' / 'open a new shell' step: `passwords` is a
    binary on PATH and self-initializes. Assert neither the entry script nor the optional
    importer still tells the user to source bashrc."""
    for name in ("passwords.py", "encrypt_passwords_text_tile.py"):
        src = (paths.PASSWORDS_DIR / name).read_text(encoding="utf-8")
        assert "source ~/.bashrc" not in src, name
        assert ".bashrc" not in src, name


def test_entry_recovers_a_stale_plaintext_on_startup():
    """The PROMPT's crash-recovery ask: if the machine dies while `passwords` is open, a
    plaintext session file can survive. On relaunch the entry script must detect it and
    (since the master password is not stored) offer to re-encrypt it -- never silently open
    over it. Pin the recovery seam exists and runs before the store is touched."""
    src = _entry_source()
    assert "_recover_stale_plaintext" in src
    # Recovery is keyed on the session plaintext still being present at startup.
    assert "os.path.exists(plain)" in src


def test_launcher_is_a_binary_on_path_not_a_bashrc_command():
    """The PROMPT: `passwords` should be a BINARY, not a bashrc alias/function. The only
    delivery mechanism is the /usr/local/bin/passwords launcher (an executable on PATH); no
    shell rc file defines it. Guard the launcher path + exec shape (the alias-free property is
    covered by there being no bashrc emitter for it anywhere)."""
    assert pw.LAUNCHER_SYSTEM_PATH == "/usr/local/bin/passwords"
    sh = pw.launcher_sh()
    assert sh.startswith("#!/bin/sh")
    assert 'exec python -u passwords.py "$@"' in sh


# --- the reworked TUI navigation (azzio-style) -----------------------------
def test_terminal_user_interface_nav_mirrors_azzio_and_drops_open_and_help():
    """The PROMPT reworks the TUI: azzio-style nav (WASD/HJKL/arrows movement, '/' search,
    ESC back, Q quit) and the 'o open' + 'h help' verbs deleted. These are behavioural, but
    pin the shipped terminal_user_interface.py source so the keymap cannot silently regress on
    the ISO."""
    src = (paths.PASSWORDS_DIR / "terminal_user_interface.py").read_text(encoding="utf-8")
    # Movement keys (WASD + HJKL + arrows) are wired.
    assert "_UP_KEYS" in src and "_DOWN_KEYS" in src
    for key in ("ord('w')", "ord('k')", "ord('j')", "curses.KEY_LEFT",
                "curses.KEY_RIGHT"):
        assert key in src, key
    # ESC goes BACK (not quit) and Q quits: the nav data pairs both verbs.
    assert "('ESC', 'back')" in src
    assert "('Q', 'quit')" in src
    assert "ord('Q')" in src
    # The deleted verbs: no 'o' persistent-toggle and no in-UI 'h' help handler remain.
    assert "self.persistent = not self.persistent" not in src
    assert "forms.show_help" not in src
