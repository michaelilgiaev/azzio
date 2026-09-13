"""Azzio passwords -- build wiring for the `passwords` command.

`passwords` is an encrypted (GPG AES256) terminal password manager: the master
password is the GPG passphrase (never stored), the store is a single .gpg file at
~/Vault/passwords.txt.gpg, and running `passwords` unlocks it, decrypts it to a
session plaintext for the search/select curses UI, then re-encrypts on quit only if
something changed and always deletes the session plaintext. See __init__.py and the module
docstrings for the app itself; THIS module is only the ISO build wiring.

Mirrors packages/librewolf/timedate.py: our OWN package, so the sources live directly in
this dir next to the build wiring, and compiler.py iterates emit_plan() to place the
artifacts into the airootfs (root-owned system paths -- the OFFLINE Calamares install
rsyncs the live rootfs, so they carry onto the installed system with no separate
installer step). Like timedate this is a PURE-PYTHON app (nothing to compile).

The app is ONE FLAT directory (there is no pwlib/ sub-library anymore): the entry script
and every module it imports sit side by side, so emit_plan() ships each of them as its own
single-file entry into LIB_DIR (plus the /usr/local/bin/passwords launcher). The set of
shipped modules is discovered from the source dir -- every .py except this build wiring
(the unit tests live in the top-level tests/ dir, not here) -- so adding or removing a
module needs no edit here.

Layers:
  * SOURCE tree -- libraries/packages/passwords/ (paths.PASSWORDS_DIR):
      __init__.py                     the package init (makes the dir importable in tests)
      passwords.py                    the `passwords` entry script (self-inits + UI driver)
      encrypt_passwords_text_tile.py  optional importer: bulk-encrypt an existing plaintext
      config/cryptography/model/clipboard/clipboard_owner/forms/new_entry/
        terminal_user_interface/keyboard/help  the working modules
      packaging.py                    THIS module -- install paths, launcher, emit_plan()
  * INSTALLED layout (root-owned), all flat in LIB_DIR:
      /usr/local/lib/azzio-passwords/passwords.py                   the entry script
      /usr/local/lib/azzio-passwords/encrypt_passwords_text_tile.py the optional importer
      /usr/local/lib/azzio-passwords/<module>.py                    each working module
      /usr/local/bin/passwords                        the launcher (execs passwords.py)

Runtime dependencies (system binaries the app shells out to): `gpg` (gnupg) for
encrypt/decrypt and `xclip` for the clipboard -- BOTH are named in the manifest
(packages.x86_64 AZZIO ADDITIONS). python is already there; everything else the app
uses is Python standard library (curses, subprocess, ctypes, json, re). No systemd
service: `passwords` is an interactive command, launched on demand, not a boot service.
"""

from __future__ import annotations

import paths

# --- Installed system paths (root-owned) ------------------------------------
# Where the app lands in the live/installed rootfs. Under /usr/local (our stuff), so the
# OFFLINE install's unpackfs rsync carries it to the target unchanged. Mirrors
# timedate.LIB_DIR. The whole app is ONE FLAT directory now (no pwlib/ sub-library): the
# entry script and every module it imports sit side by side here, and the entry does
# `sys.path.insert(0, <its own dir>)` so those bare `import <module>` calls resolve.
LIB_DIR = "/usr/local/lib/azzio-passwords"
# The entry script the launcher execs. It does `sys.path.insert(0, <its own dir>)` then
# `import config`/`import terminal_user_interface`/..., so it must land in LIB_DIR beside
# the modules it imports.
ENTRY_SYSTEM_PATH = f"{LIB_DIR}/passwords.py"
# The OPTIONAL importer (`encrypt_passwords_text_tile.py`): bulk-encrypts an EXISTING
# plaintext list into the ~/Vault store. Ships beside the entry script for power users, but
# is NOT on the normal path -- `passwords` self-initializes an empty store on first run, so
# a fresh user never has to touch this.
SETUP_SYSTEM_PATH = f"{LIB_DIR}/encrypt_passwords_text_tile.py"
# The bin entry point on PATH -- the actual `passwords` command. A tiny wrapper that execs
# the system python on the entry script from LIB_DIR (so the sibling `import`s resolve).
LAUNCHER_SYSTEM_PATH = "/usr/local/bin/passwords"

# --- Which source files ship (in the repo) ----------------------------------
# The app is a flat directory, so we ship every .py in it EXCEPT this build wiring.
# Discovering the set (rather than listing each module) means adding or removing a module
# needs no edit here -- the new module ships automatically, matching the rest of the tree's
# "add/remove a file freely" convention. (The unit tests live in the top-level tests/ dir,
# not beside the sources, so nothing test-related is in this scan to exclude.)
_NON_SHIPPED = frozenset({"packaging.py"})


def _shipped_module_names() -> list[str]:
    """Every runtime .py file the app ships to LIB_DIR (sorted), i.e. the whole passwords
    source dir minus the build wiring (packaging.py). The entry script, the optional importer,
    __init__.py and every working module are all included -- they must travel together for the
    flat sibling imports to resolve. (The unit tests are in tests/, not this dir.)"""
    return sorted(
        p.name
        for p in paths.PASSWORDS_DIR.iterdir()
        if p.is_file() and p.suffix == ".py" and p.name not in _NON_SHIPPED
    )


def _read_source(name: str) -> str:
    """Read one of the app's Python sources verbatim from the passwords package dir."""
    return (paths.PASSWORDS_DIR / name).read_text(encoding="utf-8")


class _ModuleBuilder:
    """A zero-arg builder that reads module `name`'s source verbatim on each call (late, so an
    edit to the source is always reflected). Two builders for the same module compare EQUAL
    (equality keyed on the module name, not object identity) so emit_plan() is a pure function
    whose repeated results are equal -- unlike a bare lambda, which is unique per creation."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self) -> str:
        return _read_source(self.name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _ModuleBuilder) and other.name == self.name

    def __hash__(self) -> int:
        return hash(self.name)


def launcher_sh() -> str:
    """A tiny launcher installed on PATH as the `passwords` command.

    It cd's into LIB_DIR (so the entry script's sibling `import`s resolve without installing
    anything as a site package) and execs the system python on passwords.py, forwarding any
    arguments (so `passwords -h` reaches the script). `exec` so the python process replaces
    the shell (clean signals -- the app installs SIGTERM/SIGHUP handlers to shred the session
    plaintext, and exec lets them fire on the python process itself). `"$@"` is quoted so
    arguments with spaces survive."""
    return f"""\
#!/bin/sh
# passwords -- launch the Azzio encrypted password manager.
# Generated by packages/passwords/packaging.py (edit the Python, not this file).
cd '{LIB_DIR}' || exit 1
exec python -u passwords.py "$@"
"""


# --- Emit plan --------------------------------------------------------------
# Declarative list (builder -> dest -> mode), mirroring timedate.PLAN so compiler.py iterates
# it the same way. All absolute SYSTEM paths (root-owned): the OFFLINE Calamares install
# rsyncs the live rootfs, so these carry onto the installed system unchanged. Every runtime
# .py ships as its own entry (0644) into LIB_DIR, plus the launcher (0755) on PATH. No
# systemd service (interactive command).
_EXEC = 0o755
_CONF = 0o644


def emit_plan() -> list[dict]:
    """Return the emit plan (builder/dest/mode) for compiler.py to write into the airootfs.
    One entry per shipped module file (into LIB_DIR) plus the /usr/local/bin/passwords
    launcher. Mirrors timedate.emit_plan()/openbox.emit_plan(); every entry is a single file,
    so the flat directory is expressed entirely here (no separate directory copy).

    Built fresh each call (compiler.py may call this more than once per build), so a mutated
    returned entry can never corrupt module state."""
    plan = [
        {"builder": _ModuleBuilder(name), "dest": f"{LIB_DIR}/{name}", "mode": _CONF}
        for name in _shipped_module_names()
    ]
    plan.append({"builder": launcher_sh, "dest": LAUNCHER_SYSTEM_PATH, "mode": _EXEC})
    return plan
