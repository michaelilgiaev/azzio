"""Zombie VM whose working directory was DELETED must still be stoppable.

THE BUG (reported against codelis):
  A VM launched in /path/codelis runs QEMU with comm "codelis-vm". If that dir is
  deleted while the VM still runs, the kernel makes /proc/<pid>/cwd read back as
  "/path/codelis (deleted)". `hypervisor ls` then derived the VM name from that
  suffixed basename -> slug "codelis-deleted", and `hypervisor stop codelis-deleted`
  (or `stop <pid>`) re-derived the process comm from that slug -> "codelis-dele-vm"
  (15-char comm cap), which no longer equals the LIVE comm "codelis-vm". So the
  pkill/-x match missed and stop reported "not running" -- an unkillable zombie.

THE FIX (two layers, both asserted here):
  1. `_pid_cwd` STRIPS the kernel's " (deleted)" suffix, so a deleted-dir VM slugs
     back to its real name ("codelis") and its recomputed proc name matches the live
     comm again.
  2. stop/view resolve the target to the ACTUAL running pid and act on that pid, so
     a target given by name/pid is killed even when its dir is entirely gone.

These are PURE-function tests -- they drive the enumeration with a fake proc table
and monkeypatch /proc/<pid>/cwd, so no real VM is needed.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "libraries" / "packages"


@pytest.fixture()
def hv(monkeypatch):
    """Import the hypervisor package with its packages dir on sys.path."""
    monkeypatch.syspath_prepend(str(PKG))
    vm_instances = importlib.import_module("hypervisor.vm_instances")
    configuration = importlib.import_module("hypervisor.configuration")
    importlib.reload(configuration)
    importlib.reload(vm_instances)
    return vm_instances, configuration


def test_pid_cwd_strips_deleted_suffix(hv, monkeypatch):
    """readlink returning '<dir> (deleted)' must be reported as the clean '<dir>'."""
    vm_instances, _ = hv
    monkeypatch.setattr(
        vm_instances.os, "readlink",
        lambda p: "/home/main/Ignore/claudedebug/venv/codelis (deleted)",
    )
    assert vm_instances._pid_cwd(1234) == "/home/main/Ignore/claudedebug/venv/codelis"


def test_deleted_dir_vm_slugs_to_real_name(hv, monkeypatch):
    """A running VM whose dir was deleted still lists under its REAL slug, and the
    proc name recomputed from that slug matches the LIVE comm -- so stop can find it."""
    vm_instances, configuration = hv

    # The live QEMU: comm as launched from the (now-deleted) dir basename "codelis".
    live_comm = configuration._proc_name(configuration._slugify("codelis"))  # "codelis-vm"
    fake_pid = 654874

    # Fake the world: one process with that comm, whose cwd readlink is deleted-suffixed,
    # and whose dir has no vm_name override / ssh cfg.
    monkeypatch.setattr(
        vm_instances, "_scan_proc_table", lambda: [(fake_pid, live_comm)]
    )
    monkeypatch.setattr(
        vm_instances.os, "readlink",
        lambda p: "/home/main/Ignore/claudedebug/venv/codelis (deleted)",
    )
    monkeypatch.setattr(vm_instances, "_vm_name_in_cfg", lambda d: "")
    monkeypatch.setattr(vm_instances, "_cfg_ssh_port", lambda d: None)

    insts = vm_instances._running_instances()
    assert len(insts) == 1
    inst = insts[0]

    # BEFORE the fix this was "codelis-deleted"; the dir carried the " (deleted)" suffix.
    assert inst["vm"] == "codelis", f"expected real slug, got {inst['vm']!r}"
    assert inst["pid"] == fake_pid

    # The clincher: the proc name the stop path recomputes from this instance's dir
    # basename must equal the LIVE comm, or pkill -x will miss it.
    recomputed = configuration._proc_name(
        configuration._slugify(configuration.os.path.basename(inst["dir"]))
    )
    assert recomputed == live_comm, (
        f"recomputed proc {recomputed!r} != live comm {live_comm!r} "
        "-- stop would report 'not running'"
    )

    # And it must still resolve by that clean name AND by pid.
    assert vm_instances._resolve_target("codelis")["pid"] == fake_pid
    assert vm_instances._resolve_target(str(fake_pid))["pid"] == fake_pid


def test_do_stop_kills_zombie_by_resolved_pid(monkeypatch):
    """do_stop must SIGTERM the exact resolved pid, so a deleted-dir zombie dies even
    if its recomputed comm would not match a pkill -x."""
    monkeypatch.syspath_prepend(str(PKG))
    vm = importlib.import_module("hypervisor.virtual_machine")
    importlib.reload(vm)

    fake_pid = 654874
    killed: list[tuple[int, int]] = []

    # Resolve the target to our fake instance (dir already deleted-stripped to a clean path).
    monkeypatch.setattr(
        vm, "_resolve_target",
        lambda arg: {"vm": "codelis", "pid": fake_pid,
                     "dir": "/gone/venv/codelis", "ssh_port": None},
    )
    # Config.from_dir must not touch the real fs -- stub it to a tiny shim carrying the
    # attributes do_stop reads.
    class _Cfg:
        vm = "codelis"
        proc = "codelis-dele-vm"          # deliberately the WRONG (truncated) comm
        virtiofs_sock = "/gone/virtiofs.sock"
    monkeypatch.setattr(vm.Config, "from_dir", staticmethod(lambda d: _Cfg()))

    # Record os.kill calls; report the pid alive until a SIGTERM/SIGKILL is delivered.
    def fake_kill(pid, sig):
        if sig == 0:
            # alive until we have sent a real terminating signal
            if any(k[0] == pid and k[1] != 0 for k in killed):
                raise ProcessLookupError
            return
        killed.append((pid, sig))
    monkeypatch.setattr(vm.os, "kill", fake_kill)
    # is_running must NOT be what saves us -- force it to lie "not running" for the wrong
    # comm, proving the pid path is what detects/kills the zombie.
    monkeypatch.setattr(vm, "is_running", lambda cfg: False)
    # Neutralise the comm/virtiofsd pkills (they can't match anything in the test env).
    monkeypatch.setattr(vm.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(vm.time, "sleep", lambda s: None)

    vm.do_stop(_Cfg(), "codelis")

    assert (fake_pid, vm.signal.SIGTERM) in killed, (
        "do_stop did not SIGTERM the resolved pid -- zombie would survive"
    )
