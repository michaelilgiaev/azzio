"""Azzio backup -- build wiring for the `backup` and `unpack` commands.

`backup` rolls the current user's top-level home folders (skipping ``Ignore`` and
hidden dot files, keeping symlinks as links) into ~/backup.tar.gz.gpg AND archives the
password store into ~/passwords.tar.gz.gpg, both GPG-encrypted with one passphrase.
`unpack` reverses either archive, restoring the home dirs into ~/ and the password
store into ~/Vault/. See __init__.py, backup.py, unpack.py and the shared archive.py
for the apps themselves; THIS module is only the ISO build wiring.

Mirrors packages/passwords/packaging.py: our OWN package, so the sources live
directly in this dir next to the build wiring, and compiler.py iterates emit_plan()
to place the artifacts into the airootfs (root-owned system paths -- the OFFLINE
Calamares install rsyncs the live rootfs, so they carry onto the installed system
with no separate installer step). Like passwords this is a PURE-PYTHON app (nothing
to compile).

The app is a flat directory: the entry script and any module it imports sit side by
side, so emit_plan() ships each of them as its own single-file entry into LIB_DIR
(plus the /usr/local/bin/backup launcher). The set of shipped modules is discovered
from the source dir -- every .py except this build wiring -- so adding or removing a
module needs no edit here.

Layers:
  * SOURCE tree -- libraries/packages/backup/ (paths.BACKUP_DIR):
      __init__.py                     the package init (makes the dir importable in tests)
      backup.py                       the `backup` entry script (creates the two archives)
      unpack.py                       the `unpack` entry script (restores an archive)
      archive.py                      shared tar+gpg helper (imported by both entries)
      packaging.py                    THIS module -- install paths, launchers, emit_plan()
  * INSTALLED layout (root-owned), all flat in LIB_DIR:
      /usr/local/lib/azzio-backup/backup.py    the `backup` entry script
      /usr/local/lib/azzio-backup/unpack.py    the `unpack` entry script
      /usr/local/lib/azzio-backup/archive.py   the shared helper
      /usr/local/lib/azzio-backup/<module>.py  any future working module
      /usr/local/bin/backup                      the `backup` launcher (execs backup.py)
      /usr/local/bin/unpack                      the `unpack` launcher (execs unpack.py)

Runtime dependencies (system binaries the app shells out to): `gpg` (gnupg) to
encrypt the archive -- already named in the manifest (it is also the passwords
manager's dep). `tar`/gzip come from the Python standard library's ``tarfile``, and
python itself is already present; everything else the app uses is standard library.
No systemd service: `backup` is an interactive command, launched on demand.
"""

from __future__ import annotations

import paths

# --- Installed system paths (root-owned) ------------------------------------
# Where the app lands in the live/installed rootfs. Under /usr/local (our stuff), so
# the OFFLINE install's unpackfs rsync carries it to the target unchanged. Mirrors
# passwords.LIB_DIR. The app is ONE FLAT directory: the entry script (and any module
# it grows) sit side by side here, and the entry does `sys.path.insert(0, <its own
# dir>)` so future bare `import <module>` calls resolve.
LIB_DIR = "/usr/local/lib/azzio-backup"
# The entry script the `backup` launcher execs. It lands in LIB_DIR beside the other
# modules; its own `sys.path.insert(0, <dir of __file__>)` makes the sibling
# `import archive` resolve from wherever it is run.
ENTRY_SYSTEM_PATH = f"{LIB_DIR}/backup.py"
# The entry script the `unpack` launcher execs (the restore command). Same flat dir,
# same sibling `import archive`.
UNPACK_ENTRY_SYSTEM_PATH = f"{LIB_DIR}/unpack.py"
# The bin entry points on PATH -- the actual `backup` and `unpack` commands. Each is a
# tiny wrapper that execs the system python on its entry script's ABSOLUTE path in
# LIB_DIR WITHOUT changing directory (so the caller's cwd is preserved and a relative
# archive arg resolves against it; the sibling imports resolve via the entry's own
# sys.path bootstrap). Both ship 0o755 (see the profile.py file_permissions map --
# archiso would otherwise normalise them to 0644 on the squashfs; that was the launcher
# executable-bit bug).
LAUNCHER_SYSTEM_PATH = "/usr/local/bin/backup"
UNPACK_LAUNCHER_SYSTEM_PATH = "/usr/local/bin/unpack"

# --- Which source files ship (in the repo) ----------------------------------
# The app is a flat directory, so we ship every .py in it EXCEPT this build wiring.
# Discovering the set (rather than listing each module) means adding or removing a
# module needs no edit here. (The unit tests live in the top-level tests/ dir, not
# beside the sources, so nothing test-related is in this scan to exclude.)
_NON_SHIPPED = frozenset({"packaging.py"})


def _shipped_module_names() -> list[str]:
    """Every runtime .py file the app ships to LIB_DIR (sorted): the whole backup
    source dir minus the build wiring (packaging.py). The entry script, __init__.py
    and any working module are all included. (The unit tests are in tests/, not here.)"""
    return sorted(
        p.name
        for p in paths.BACKUP_DIR.iterdir()
        if p.is_file() and p.suffix == ".py" and p.name not in _NON_SHIPPED
    )


def _read_source(name: str) -> str:
    """Read one of the app's Python sources verbatim from the backup package dir."""
    return (paths.BACKUP_DIR / name).read_text(encoding="utf-8")


class _ModuleBuilder:
    """A zero-arg builder that reads module `name`'s source verbatim on each call
    (late, so an edit to the source is always reflected). Two builders for the same
    module compare EQUAL (equality keyed on the module name) so emit_plan() is a pure
    function whose repeated results are equal -- unlike a bare lambda, which is unique
    per creation."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self) -> str:
        return _read_source(self.name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _ModuleBuilder) and other.name == self.name

    def __hash__(self) -> int:
        return hash(self.name)


def _launcher_sh(command: str, entry: str, summary: str) -> str:
    """A tiny launcher installed on PATH as ``command``.

    It execs the system python on the entry script's ABSOLUTE path in LIB_DIR,
    forwarding any arguments (so ``command --help`` / ``unpack <file>`` reach the
    script). It deliberately does NOT ``cd`` anywhere -- the caller's working directory
    is PRESERVED, so a RELATIVE archive argument (``unpack backup.tar.gz.gpg`` typed from
    ``~``) resolves against the user's cwd, not against LIB_DIR (that ``cd`` was bug #1
    for ``unpack``). The sibling ``import archive`` still resolves without the ``cd``,
    because both entry scripts do ``sys.path.insert(0, <dir of __file__>)`` at startup,
    which keys off the script's own absolute path rather than the cwd. `exec` so the
    python process replaces the shell (clean signals). `"$@"` is quoted so arguments with
    spaces survive."""
    return f"""\
#!/bin/sh
# {command} -- {summary}
# Generated by packages/backup/packaging.py (edit the Python, not this file).
# The caller's working directory is preserved (deliberately not changed) so a relative
# archive path resolves against it; the entry does sys.path.insert for its sibling
# imports, so it needs no help finding them.
exec python -u '{LIB_DIR}/{entry}' "$@"
"""


def launcher_sh() -> str:
    """The `backup` launcher (execs backup.py -- creates the two encrypted archives)."""
    return _launcher_sh("backup", "backup.py",
                        "launch the Azzio home-directory + password backup.")


def unpack_launcher_sh() -> str:
    """The `unpack` launcher (execs unpack.py -- restores a .tar.gz.gpg archive)."""
    return _launcher_sh("unpack", "unpack.py",
                        "restore an Azzio backup archive.")


# --- Emit plan --------------------------------------------------------------
# Declarative list (builder -> dest -> mode), mirroring passwords.emit_plan() so
# compiler.py iterates it the same way. All absolute SYSTEM paths (root-owned): the
# OFFLINE Calamares install rsyncs the live rootfs, so these carry onto the installed
# system unchanged. Every runtime .py ships as its own entry (0644) into LIB_DIR, plus
# the launcher (0755) on PATH. No systemd service (interactive command).
_EXEC = 0o755
_CONF = 0o644


def emit_plan() -> list[dict]:
    """Return the emit plan (builder/dest/mode) for compiler.py to write into the
    airootfs. One entry per shipped module file (into LIB_DIR) plus the
    /usr/local/bin/backup launcher. Mirrors passwords.emit_plan(); every entry is a
    single file, so the flat directory is expressed entirely here (no separate
    directory copy).

    Built fresh each call (compiler.py may call this more than once per build), so a
    mutated returned entry can never corrupt module state."""
    plan = [
        {"builder": _ModuleBuilder(name), "dest": f"{LIB_DIR}/{name}", "mode": _CONF}
        for name in _shipped_module_names()
    ]
    plan.append({"builder": launcher_sh, "dest": LAUNCHER_SYSTEM_PATH, "mode": _EXEC})
    plan.append({"builder": unpack_launcher_sh,
                 "dest": UNPACK_LAUNCHER_SYSTEM_PATH, "mode": _EXEC})
    return plan
