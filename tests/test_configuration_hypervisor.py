"""packages.hypervisor -- OUR per-directory QEMU/KVM VM runner (the `hypervisor` command).

`hypervisor` spins up a QEMU/KVM VM whose whole identity is derived from the directory
it is run in (name/disk/NVRAM/shared-folder/SSH-port). The app is one flat directory;
packages/hypervisor/packaging.py is the ISO build wiring. This is a HOST-side tool,
distinct from the guest-side `azzio --sshd-hypervisor`.

Why these tests matter: like the backup payload, compiler.py never inspects the CONTENT
of these builders -- it blindly iterates emit_plan() and calls emit.write_text with the
(dest, mode) each entry declares. So the declarative plan + the launcher text ARE the
build contract. A wrong mode makes the launcher non-executable; a launcher that DOES cd
would break the VM identity (every VM would resolve to LIB_DIR instead of the caller's
cwd -- the single most important correctness point of the port). None of that raises on
its own. These tests pin:

  * the emit_plan() dest/mode entries + that it does not mutate module state,
  * the launcher (execs `python <LIB_DIR>/command_line_interface.py "$@"` WITHOUT a cd, executable),
  * the flat-package ship contract (every runtime module installs into LIB_DIR; the
    build wiring does NOT ship),
  * the CWD-preservation guard (the launcher must NOT cd -- else Config.from_cwd breaks),
  * that the launcher stays executable on the ISO (profile.FILE_PERMISSIONS pin),
  * that the runtime deps (qemu-full, edk2-ovmf, virt-viewer) are in the manifest,
  * that the compiler actually wires the package in.

The app's BEHAVIOURAL contract (arg parsing, cfg schema, qemu argv, gpu selection, ...)
is pinned by the sibling test_configuration_hypervisor_*.py files.
"""

from __future__ import annotations

import ast
import inspect

import compiler
import paths
import profile
from packages.hypervisor import packaging as hv


# --- emit_plan() contract ---------------------------------------------------
EXPECTED_KEY_PLAN = {
    "/usr/local/lib/azzio-hypervisor/command_line_interface.py": 0o644,
    "/usr/local/lib/azzio-hypervisor/configuration.py": 0o644,
    "/usr/local/lib/azzio-hypervisor/virtual_machine.py": 0o644,
    "/usr/local/lib/azzio-hypervisor/qemu_command.py": 0o644,
    "/usr/local/bin/hypervisor": 0o755,
}


def test_emit_plan_dest_mode_table():
    """The declarative (dest -> mode) entries compiler.py iterates. The launcher MUST be
    executable (0o755) so typing `hypervisor` runs it; every module is plain data (0o644,
    run through the launcher's python). Pin the key entries + the two structural rules:
    every non-launcher entry is a 0o644 .py under LIB_DIR, and the build wiring never
    ships."""
    got = {e["dest"]: e["mode"] for e in hv.emit_plan()}
    for dest, mode in EXPECTED_KEY_PLAN.items():
        assert got.get(dest) == mode, dest
    for dest, mode in got.items():
        if dest == hv.LAUNCHER_SYSTEM_PATH:
            assert mode == 0o755, dest
        else:
            assert dest.startswith(hv.LIB_DIR + "/") and dest.endswith(".py"), dest
            assert mode == 0o644, dest
    assert f"{hv.LIB_DIR}/packaging.py" not in got
    assert not any(d.rsplit("/", 1)[-1].startswith("test_") for d in got)


def test_emit_plan_builders_are_callable_and_nonempty():
    """Every entry's builder returns real content (compiler.py calls builder())."""
    for e in hv.emit_plan():
        content = e["builder"]()
        assert isinstance(content, str) and content.strip(), e["dest"]


def test_emit_plan_is_pure():
    """compiler.py may call emit_plan() more than once; it must not mutate module state or
    return aliased dicts a caller could mutate. Mirrors the backup test."""
    a = hv.emit_plan()
    b = hv.emit_plan()
    assert a == b
    a[0]["mode"] = 0o000  # mutate the returned copy
    assert hv.emit_plan()[0]["mode"] == 0o644  # module PLAN unaffected


def test_dest_paths_are_absolute_system_paths():
    """All root-owned absolute paths under /usr/local (the OFFLINE install rsyncs the live
    rootfs, so no per-user home entry is needed -- the command is on PATH for every
    user)."""
    for e in hv.emit_plan():
        assert e["dest"].startswith("/usr/local/"), e["dest"]


def test_launcher_name_is_the_hypervisor_command():
    """The command the user types is literally `hypervisor`: the launcher installs to
    /usr/local/bin/hypervisor (on PATH), executable."""
    assert hv.LAUNCHER_SYSTEM_PATH == "/usr/local/bin/hypervisor"
    plan = {e["dest"]: e for e in hv.emit_plan()}
    assert hv.LAUNCHER_SYSTEM_PATH in plan
    assert plan[hv.LAUNCHER_SYSTEM_PATH]["mode"] == 0o755  # must be executable


# --- launcher (the CWD-preservation guard is the port's key correctness point) --
def test_launcher_execs_python_entry_from_install_dir_without_cd():
    """The `hypervisor` launcher execs the system python on command_line_interface.py's ABSOLUTE path in
    LIB_DIR, forwarding arguments so `hypervisor install foo.iso` reaches the script. It
    MUST NOT `cd` -- `hypervisor` derives the whole VM identity from the caller's CURRENT
    WORKING DIRECTORY (Config.from_cwd()), so a cd into LIB_DIR would make EVERY VM resolve
    to LIB_DIR. The sibling imports still resolve because command_line_interface.py does its own sys.path
    bootstrap. `exec` so the python process replaces the shell."""
    sh = hv.launcher_sh()
    assert sh.startswith("#!/bin/sh")
    assert "cd " not in sh, "the launcher must NOT cd (it would break Config.from_cwd)"
    assert f"exec python -u '{hv.LIB_DIR}/command_line_interface.py' \"$@\"" in sh
    assert hv.ENTRY_SYSTEM_PATH == f"{hv.LIB_DIR}/command_line_interface.py"


def test_entry_script_carries_the_syspath_bootstrap():
    """Because the launcher does NOT cd, the entry (command_line_interface.py) MUST insert its own directory on
    sys.path so its sibling imports (`import virtual_machine`, `import configuration`, ...)
    resolve when run by absolute path. Pin that bootstrap is present in the shipped entry."""
    src = (paths.HYPERVISOR_DIR / "command_line_interface.py").read_text(encoding="utf-8")
    assert "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))" in src


# --- the flat-package ship contract -----------------------------------------
def test_flat_app_ships_every_module_beside_the_entry_script():
    """The app is one flat directory: the entry script (and every module it imports) install
    side by side in LIB_DIR. Pin that emit_plan() ships each source module into LIB_DIR, and
    that the entry script's own install dir is LIB_DIR."""
    shipped = {e["dest"] for e in hv.emit_plan()}
    for p in paths.HYPERVISOR_DIR.iterdir():
        if p.is_file() and p.suffix == ".py" and p.name != "packaging.py":
            assert f"{hv.LIB_DIR}/{p.name}" in shipped, p.name
    assert hv.ENTRY_SYSTEM_PATH.startswith(hv.LIB_DIR + "/")


def test_shipped_modules_parse_as_python():
    """Defense-in-depth: every shipped module is syntactically valid Python (they are copied
    verbatim and run on the target, so a syntax error would only surface there)."""
    for e in hv.emit_plan():
        if e["dest"].endswith(".py"):
            ast.parse(e["builder"](), filename=e["dest"])


def test_dual_mode_sibling_imports_have_a_flat_fallback():
    """The modules import their siblings DUAL-MODE: a package-relative import under
    `if __package__:` (used by the tests / `python -m`, so there is ONE HypervisorError
    class the tests can catch) AND a flat `sys.path.insert` + bare import in the `else`
    (used when the launcher execs the module by absolute path, where there is no parent
    package). Pin the invariant that makes the launcher path safe: EVERY module that carries
    a package-relative import (`from .`) ALSO carries the flat sys.path bootstrap -- so a
    relative import can never be reached without its flat fallback. (packaging.py is exempt:
    it is build wiring, imported only as part of the package, never execed flat.)"""
    for p in paths.HYPERVISOR_DIR.iterdir():
        if p.suffix != ".py" or p.name == "packaging.py":
            continue
        src = p.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=p.name)
        has_relative = any(
            isinstance(n, ast.ImportFrom) and n.level > 0 for n in ast.walk(tree)
        )
        if has_relative:
            assert "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))" in src, (
                f"{p.name}: has a package-relative import but no flat sys.path fallback "
                "-- the launcher execs it by absolute path, where `from .` ImportErrors"
            )
            assert "if __package__:" in src, f"{p.name}: relative import not guarded by __package__"


def test_entry_runs_main_when_executed_as_a_script():
    """command_line_interface.py IS the file the /usr/local/bin/hypervisor launcher execs
    directly (there is no __main__.py). So command_line_interface.py MUST call main() under
    `if __name__ == '__main__'`, or the launcher would import the module, define main(), and
    exit 0 WITHOUT ever running the command (a silent no-op -- exactly the bug this guards).
    Mirrors packages/backup/backup.py's entry."""
    src = (paths.HYPERVISOR_DIR / "command_line_interface.py").read_text(encoding="utf-8")
    tree = ast.parse(src, filename="command_line_interface.py")
    has_main_guard = any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in tree.body
    )
    assert has_main_guard, "command_line_interface.py must run main() under `if __name__ == '__main__'`"
    assert "sys.exit(main())" in src


# --- the compiler actually WIRES the package in (seam coverage) -------------
def test_compiler_emit_desktop_wires_hypervisor_in():
    """Guard the compiler-to-package SEAM: the package's contract can be perfect while
    compiler.py forgets to invoke it, shipping an ISO where `hypervisor` is missing
    entirely."""
    src = inspect.getsource(compiler._emit_desktop)
    assert "hypervisor.emit_plan()" in src
    assert ("from packages.hypervisor import packaging as hypervisor"
            in inspect.getsource(compiler))


def test_hypervisor_is_excluded_from_auto_app_discovery():
    """`hypervisor` is emitted BY NAME in _emit_desktop, so it must be in _EXPLICIT_PACKAGES
    or the app-loop discovery would emit it a SECOND time (duplicate writes)."""
    assert "hypervisor" in compiler._EXPLICIT_PACKAGES


# --- the launcher must ship EXECUTABLE on the ISO ---------------------------
def test_hypervisor_launcher_stays_executable_on_iso():
    """Regression guard mirroring the backup/passwords launcher fix: archiso's squashfs
    normalises overlay file modes to 0644 unless the path is pinned in
    profile.FILE_PERMISSIONS. emit_plan() marks the launcher 0o755, but that is lost in the
    squashfs without this entry -- so a 0644 launcher cannot be exec'd and `hypervisor` is
    dead on the ISO and the installed system. Pin /usr/local/bin/hypervisor to 0:0:755."""
    launcher = hv.LAUNCHER_SYSTEM_PATH
    assert profile.FILE_PERMISSIONS.get(launcher) == "0:0:755", launcher
    assert f'["{launcher}"]="0:0:755"' in profile.profiledef_sh(), launcher


# --- runtime dependencies are in the manifest -------------------------------
def test_qemu_ovmf_and_viewer_are_in_the_manifest():
    """The app shells out to qemu-system-x86_64/qemu-img (qemu-full), the OVMF firmware
    (edk2-ovmf) and remote-viewer (virt-viewer); all must be shipped or the command is
    installed but non-functional. Tokenize the manifest exactly as the build does."""
    text = paths.PACKAGES_FILE.read_text()
    toks = [tok for line in text.splitlines()
            if (tok := line.split("#", 1)[0].strip())]
    for pkg in ("qemu-full", "edk2-ovmf", "virt-viewer"):
        assert pkg in toks, pkg


# --- no hard-coded user home in the app source ------------------------------
def test_no_hardcoded_home_in_the_app_source():
    """The app derives everything from the caller's cwd / $HOME at runtime; it must not
    hard-code /home/main anywhere (a user may have named their account anything)."""
    for p in paths.HYPERVISOR_DIR.iterdir():
        if p.suffix == ".py":
            assert "/home/main" not in p.read_text(encoding="utf-8"), p.name
