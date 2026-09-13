"""virtiofs shared-folder wiring -- the fix for the reported --shared/--ssh coupling.

The BUG: `--shared` only made the folder appear inside the ssh ISO variant, because
the share was mounted only as a side effect of the ssh bring-up service. Nothing on
the headed variant mounted it, so `--shared` silently did nothing there.

The FIX moves the transport to virtiofs and, on the HOST, spawns a `virtiofsd`
daemon whenever `shared` is set -- REGARDLESS of `ssh`. These tests pin that
decoupling at the pure command-builder level (`virtiofsd_argv`), plus the
`require_virtiofsd` precondition, so the coupling cannot silently come back.
"""

from __future__ import annotations

import pytest

from hypervisor_helpers import make_cfg

from packages.hypervisor import virtual_machine as vm
from packages.hypervisor import checks
from packages.hypervisor.checks import HypervisorError


def _cfg(tmp_path, **overrides):
    return make_cfg(str(tmp_path), **overrides)


def test_no_daemon_when_shared_off(tmp_path):
    # shared off -> no daemon command at all (nothing to export).
    cfg = _cfg(tmp_path, shared=False, ssh=False)
    assert vm.virtiofsd_argv(cfg) == []


def test_daemon_spawned_when_shared_on_regardless_of_ssh(tmp_path):
    # THE regression guard: the daemon (== the share actually working) depends ONLY
    # on shared, never on ssh. Both ssh=False and ssh=True must produce the daemon,
    # pointed at the same host dir and the same vhost-user socket.
    for ssh in (False, True):
        cfg = _cfg(tmp_path, shared=True, ssh=ssh)
        argv = vm.virtiofsd_argv(cfg)
        assert argv, f"expected a virtiofsd command with ssh={ssh}"
        joined = " ".join(argv)
        assert "virtiofsd" in joined                # the daemon binary is invoked
        assert cfg.shared_path in joined            # exports the host share dir
        assert cfg.virtiofs_sock in joined          # on the vhost-user socket QEMU reads


def test_daemon_runs_as_root_via_sudo(tmp_path):
    # THE writable-share guard. virtiofsd must run as ROOT: only root can setfsuid()
    # to the guest's credentials before a host create, so an UNPRIVILEGED daemon lets
    # the guest read but NOT create files -- a silent regression from the old 9p share.
    # The daemon is therefore spawned through sudo. `sudo` must be argv[0] (nothing may
    # precede it) and the real virtiofsd binary must come straight after.
    cfg = _cfg(tmp_path, shared=True)
    argv = vm.virtiofsd_argv(cfg)
    assert argv[0] == "sudo", f"virtiofsd must run via sudo (as root); got {argv[0]!r}"
    # the token right after sudo (skipping any sudo flags) is the virtiofsd binary
    binary = next(a for a in argv[1:] if not a.startswith("-"))
    assert binary.endswith("virtiofsd")


def test_daemon_sets_socket_group_so_nonroot_qemu_can_connect(tmp_path):
    # A root-owned vhost-user socket is srwx------ root and the (non-root) QEMU cannot
    # open it. --socket-group hands the socket to the invoking user's primary group so
    # QEMU connects with no post-hoc chmod. The group is passed in (keeps the builder
    # PURE and testable); a concrete group name must reach the flag.
    cfg = _cfg(tmp_path, shared=True)
    argv = vm.virtiofsd_argv(cfg, socket_group="staff")
    assert "--socket-group=staff" in argv


def test_no_socket_group_flag_when_group_unknown(tmp_path):
    # If the primary group cannot be resolved, emit no --socket-group (virtiofsd rejects
    # an empty group); the daemon still runs as root and the caller falls back to chmod.
    cfg = _cfg(tmp_path, shared=True)
    argv = vm.virtiofsd_argv(cfg, socket_group=None)
    assert not any(a.startswith("--socket-group") for a in argv)


def test_daemon_exports_custom_path(tmp_path):
    custom = "/mnt/host/project"
    cfg = _cfg(tmp_path, shared=custom)
    assert custom in " ".join(vm.virtiofsd_argv(cfg))


def test_require_virtiofsd_raises_when_binary_absent(tmp_path, monkeypatch):
    # When --shared is requested but no virtiofsd binary exists anywhere (not on
    # PATH, not at the well-known libexec paths), the check must fail CLEANLY
    # (HypervisorError -> 'hypervisor: ...' exit 1), never crash.
    monkeypatch.setattr(checks.shutil, "which", lambda _b: None)
    monkeypatch.setattr(checks.os.path, "exists", lambda _p: False)
    with pytest.raises(HypervisorError):
        checks.require_virtiofsd()


def test_require_virtiofsd_passes_when_binary_on_path(tmp_path, monkeypatch):
    # Present on PATH -> no error, and the resolved path is what which() returned.
    monkeypatch.setattr(checks.shutil, "which",
                        lambda b: "/usr/bin/virtiofsd" if b == "virtiofsd" else None)
    checks.require_virtiofsd()  # must not raise
    assert checks.virtiofsd_binary() == "/usr/bin/virtiofsd"


def test_virtiofsd_binary_falls_back_to_libexec(tmp_path, monkeypatch):
    # Not on PATH but present at the well-known /usr/lib location -> found there.
    monkeypatch.setattr(checks.shutil, "which", lambda _b: None)
    monkeypatch.setattr(checks.os.path, "exists",
                        lambda p: p == "/usr/lib/virtiofsd")
    assert checks.virtiofsd_binary() == "/usr/lib/virtiofsd"
