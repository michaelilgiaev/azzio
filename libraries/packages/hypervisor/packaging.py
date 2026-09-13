"""Azzio hypervisor -- build wiring for the `hypervisor` command.

`hypervisor` spins up a per-directory QEMU/KVM VM (the directory you run it in IS the
VM: name/disk/NVRAM/shared-folder/SSH-port all derive from it). See __init__.py and
the module docstrings for the app itself; THIS module is only the ISO build wiring.

Mirrors packages/backup/packaging.py: our OWN package, so the sources live directly in
this dir next to the build wiring, and compiler.py iterates emit_plan() to place the
artifacts into the airootfs (root-owned system paths -- the OFFLINE Calamares install
rsyncs the live rootfs, so they carry onto the installed system with no separate
installer step). Like backup this is a PURE-PYTHON app (nothing to compile).

The app is a flat directory: the entry script (command_line_interface.py) and every module it imports sit
side by side, so emit_plan() ships each of them as its own single-file entry into
LIB_DIR (plus the /usr/local/bin/hypervisor launcher). The set of shipped modules is
discovered from the source dir -- every .py except this build wiring -- so adding or
removing a module needs no edit here.

Layers:
  * SOURCE tree -- libraries/packages/hypervisor/ (paths.HYPERVISOR_DIR):
      __init__.py                     the package init (makes the dir importable in tests)
      command_line_interface.py       THE ENTRY the launcher execs (arg parse + dispatch)
      configuration.py                CWD-derived VM identity/paths/config
      configuration_schema.py         typed hypervisor.cfg schema + validation
      configuration_watcher.py        live cfg reload with validate/revert
      configuration_defaults.py       user-wide default overrides (~/.config/azzio-hypervisor)
      graphics.py                     DRM render-node selection
      checks.py                       precondition checks + die()/HypervisorError
      qemu_command.py                 the pure QEMU argv assembler
      virtual_machine.py              install/run/share/status/stop logic
      packaging.py                    THIS module -- install paths, launcher, emit_plan()
  * INSTALLED layout (root-owned), all flat in LIB_DIR:
      /usr/local/lib/azzio-hypervisor/command_line_interface.py       the `hypervisor` entry script
      /usr/local/lib/azzio-hypervisor/<module>.py  every runtime module (flat)
      /usr/local/bin/hypervisor                     the launcher (execs command_line_interface.py)

Runtime dependencies (system binaries the app shells out to): `qemu-system-x86_64`
and `qemu-img` (qemu-full) to run/create the VM, the OVMF UEFI firmware (edk2-ovmf),
`remote-viewer` (virt-viewer) for the display, and `pgrep` (procps-ng, in base) for
the running-state check -- the first three are named in the manifest. python itself is
already present; everything else the app uses is Python standard library. No systemd
service: `hypervisor` is an interactive command, launched on demand.
"""

from __future__ import annotations

import paths

# --- Installed system paths (root-owned) ------------------------------------
# Where the app lands in the live/installed rootfs. Under /usr/local (our stuff), so
# the OFFLINE install's unpackfs rsync carries it to the target unchanged. Mirrors
# backup.LIB_DIR. The app is ONE FLAT directory: the entry script (command_line_interface.py) and every
# module it imports sit side by side here, and the entry does `sys.path.insert(0, <its
# own dir>)` so the bare `import <module>` calls resolve.
LIB_DIR = "/usr/local/lib/azzio-hypervisor"
# The entry script the `hypervisor` launcher execs. It lands in LIB_DIR beside the other
# modules; its own `sys.path.insert(0, <dir of __file__>)` makes the sibling imports
# (`import virtual_machine`, `import configuration`, ...) resolve from wherever it is run.
ENTRY_SYSTEM_PATH = f"{LIB_DIR}/command_line_interface.py"
# The bin entry point on PATH -- the actual `hypervisor` command. A tiny wrapper that
# execs the system python on the entry script's ABSOLUTE path in LIB_DIR WITHOUT changing
# directory. The no-cd is LOAD-BEARING here: `hypervisor` derives the whole VM identity
# from the caller's CURRENT WORKING DIRECTORY (Config.from_cwd()), so the launcher must
# preserve it -- a `cd` into LIB_DIR (as the passwords launcher does) would make every VM
# resolve to LIB_DIR. The sibling imports still resolve without the cd because command_line_interface.py does
# `sys.path.insert(0, <dir of __file__>)` at startup, keyed off the script's own absolute
# path rather than the cwd. Ships 0o755 (see the profile.py file_permissions map -- archiso
# would otherwise normalise it to 0644 on the squashfs).
LAUNCHER_SYSTEM_PATH = "/usr/local/bin/hypervisor"

# --- Which source files ship (in the repo) ----------------------------------
# The app is a flat directory, so we ship every .py in it EXCEPT this build wiring.
# Discovering the set (rather than listing each module) means adding or removing a
# module needs no edit here. (The unit tests live in the top-level tests/ dir, not
# beside the sources, so nothing test-related is in this scan to exclude.)
_NON_SHIPPED = frozenset({"packaging.py"})


def _shipped_module_names() -> list[str]:
    """Every runtime .py file the app ships to LIB_DIR (sorted): the whole hypervisor
    source dir minus the build wiring (packaging.py). The entry script
    (command_line_interface.py), __init__.py and every working module are all included --
    they must travel together for the flat sibling imports to resolve. (The unit tests are
    in tests/, not here.)"""
    return sorted(
        p.name
        for p in paths.HYPERVISOR_DIR.iterdir()
        if p.is_file() and p.suffix == ".py" and p.name not in _NON_SHIPPED
    )


def _read_source(name: str) -> str:
    """Read one of the app's Python sources verbatim from the hypervisor package dir."""
    return (paths.HYPERVISOR_DIR / name).read_text(encoding="utf-8")


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


def launcher_sh() -> str:
    """The `hypervisor` launcher installed on PATH.

    It execs the system python on the entry script's ABSOLUTE path in LIB_DIR,
    forwarding any arguments (so ``hypervisor install foo.iso`` / ``hypervisor -h``
    reach the script). It deliberately does NOT ``cd`` anywhere -- `hypervisor` derives
    the whole VM identity from the caller's CURRENT WORKING DIRECTORY, so the caller's
    cwd MUST be preserved (a ``cd`` into LIB_DIR would make every VM resolve to LIB_DIR;
    this is the port's single most important correctness point). The sibling imports
    still resolve without the ``cd`` because command_line_interface.py does ``sys.path.insert(0, <dir of
    __file__>)`` at startup, which keys off the script's own absolute path rather than
    the cwd. `exec` so the python process replaces the shell (clean signals -- the app
    installs SIGTERM/SIGINT handling around the viewer/VM). `"$@"` is quoted so arguments
    with spaces survive."""
    return f"""\
#!/bin/sh
# hypervisor -- run a per-directory QEMU/KVM VM (the directory you are in IS the VM).
# Generated by packages/hypervisor/packaging.py (edit the Python, not this file).
# The caller's working directory is preserved (deliberately NOT changed) so the VM
# identity resolves against it; the entry does sys.path.insert for its sibling imports,
# so it needs no help finding them.
exec python -u '{ENTRY_SYSTEM_PATH}' "$@"
"""


# --- Emit plan --------------------------------------------------------------
# Declarative list (builder -> dest -> mode), mirroring backup.emit_plan() so
# compiler.py iterates it the same way. All absolute SYSTEM paths (root-owned): the
# OFFLINE Calamares install rsyncs the live rootfs, so these carry onto the installed
# system unchanged. Every runtime .py ships as its own entry (0644) into LIB_DIR, plus
# the launcher (0755) on PATH. No systemd service (interactive command).
_EXEC = 0o755
_CONF = 0o644


def emit_plan() -> list[dict]:
    """Return the emit plan (builder/dest/mode) for compiler.py to write into the
    airootfs. One entry per shipped module file (into LIB_DIR) plus the
    /usr/local/bin/hypervisor launcher. Mirrors backup.emit_plan(); every entry is a
    single file, so the flat directory is expressed entirely here (no separate
    directory copy).

    Built fresh each call (compiler.py may call this more than once per build), so a
    mutated returned entry can never corrupt module state."""
    plan = [
        {"builder": _ModuleBuilder(name), "dest": f"{LIB_DIR}/{name}", "mode": _CONF}
        for name in _shipped_module_names()
    ]
    plan.append({"builder": launcher_sh, "dest": LAUNCHER_SYSTEM_PATH, "mode": _EXEC})
    return plan
