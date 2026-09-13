"""checks.py - the die() helper and precondition checks.

Ported from libraries/common.sh's require_* functions. Each check raises
HypervisorError (caught in command_line_interface.py, printed as 'hypervisor: <msg>' and exit 1)
instead of calling `exit` directly, so the checks stay composable and testable.
"""

from __future__ import annotations

import os
import shutil
import subprocess


class HypervisorError(Exception):
    """A user-facing error. command_line_interface.py prints it and exits 1."""


def die(msg: str) -> "typing.NoReturn":  # noqa: F821
    raise HypervisorError(msg)


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def require_writable_dir(cfg) -> None:
    if not os.access(cfg.dir, os.W_OK):
        die(f"current directory is not writable: {cfg.dir}")


def require_qemu() -> None:
    for b in ("qemu-system-x86_64", "qemu-img"):
        if not _have(b):
            die(f"{b} missing -- sudo pacman -S qemu-full")


def require_ovmf(cfg) -> None:
    if not os.access(cfg.code, os.R_OK):
        die(f"OVMF_CODE missing: {cfg.code} -- sudo pacman -S edk2-ovmf")
    if not os.access(cfg.vars_tmpl, os.R_OK):
        die(f"OVMF_VARS template missing: {cfg.vars_tmpl} -- sudo pacman -S edk2-ovmf")


def require_kvm() -> None:
    if not os.path.exists("/dev/kvm"):
        die("/dev/kvm missing -- enable virtualization / load kvm modules")
    if not (os.access("/dev/kvm", os.R_OK) and os.access("/dev/kvm", os.W_OK)):
        die("/dev/kvm not accessible -- join the 'kvm' group and re-login")


def require_viewer() -> None:
    if not _have("remote-viewer"):
        die("remote-viewer missing -- sudo pacman -S virt-viewer")


# The standalone Rust virtiofsd ships its binary here, NOT on PATH (it is a
# libexec-style daemon). We accept either PATH or this well-known location.
_VIRTIOFSD_PATHS = ("/usr/lib/virtiofsd", "/usr/libexec/virtiofsd")


def virtiofsd_binary() -> str:
    """Path to the virtiofsd daemon binary, or '' if none is installed. Checks PATH
    first, then the well-known libexec locations the `virtiofsd` package uses."""
    on_path = shutil.which("virtiofsd")
    if on_path:
        return on_path
    for p in _VIRTIOFSD_PATHS:
        if os.path.exists(p):
            return p
    return ""


def require_virtiofsd() -> None:
    """The shared folder now rides virtiofs, which needs the virtiofsd daemon. Fail
    cleanly (not a crash) when --shared is requested but the daemon is absent."""
    if not virtiofsd_binary():
        die("virtiofsd missing (needed for the shared folder) -- sudo pacman -S virtiofsd")


def is_running(cfg) -> bool:
    """True if a process whose comm matches the VM's PROC name is alive.

    Mirrors `pgrep -x "$PROC"`: an EXACT match against the (15-char-capped)
    process comm, so two VMs in two dirs never see each other as running.
    """
    try:
        subprocess.run(
            ["pgrep", "-x", cfg.proc],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def require_not_running(cfg) -> None:
    if is_running(cfg):
        die(f"VM '{cfg.vm}' already running.")


def require_free_space(cfg, need_bytes: int = 8 * 1024 * 1024 * 1024) -> None:
    st = os.statvfs(cfg.dir)
    avail = st.f_bavail * st.f_frsize
    if avail < need_bytes:
        die(
            f"low free space in {cfg.dir}: {avail // 1024 // 1024} MiB avail, "
            f"need >= {need_bytes // 1024 // 1024} MiB"
        )
