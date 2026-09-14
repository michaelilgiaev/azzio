"""Headless boot, `hypervisor ls`, `hypervisor view`, and the virtiofs pid file.

These four features are what codelis' unattended cache-build path leans on:

  * `hypervisor run --headless` boots QEMU (+ virtiofsd) with NO remote-viewer window,
    so an ISO auto-install can run on a box with no display. QEMU STILL creates the
    SPICE socket, so a later `hypervisor view` can attach.
  * `hypervisor ls` enumerates every running hypervisor VM on the host (system-wide),
    recovering each VM's dir from /proc/<pid>/cwd and its ssh port from that dir's cfg.
  * `hypervisor view` opens a viewer on THIS dir's running VM as an ATTACH -- closing
    the window leaves the VM running (unlike `run`, whose viewer close tears it down).
  * virtiofsd's pid is recorded beside its socket (virtiofs.sock.pid) so a warm VM dir
    carries it (part of the codelis cache layout) and is torn down with the socket.

The idiom mirrors test_configuration_hypervisor_terminal_restore.py: a _FakeProc that
captures Popen argv/kwargs, with _spawn_virtiofsd / ConfigWatcher / _maximize_window /
os.path.exists / sys.stdin.isatty monkeypatched so nothing real boots.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from hypervisor_helpers import make_cfg

from packages.hypervisor import checks
from packages.hypervisor import command_line_interface as cli
from packages.hypervisor import virtual_machine as vm
from packages.hypervisor import vm_instances
from packages.hypervisor.checks import HypervisorError


def _cfg(tmp_path, **overrides):
    return make_cfg(str(tmp_path), **overrides)


class _FakeProc:
    """A Popen stand-in that records argv/kwargs and looks already-exited so _launch's
    _wait_any returns at once and teardown runs. Appends every spawned argv[0] to the
    shared `spawned` list the test inspects."""

    def __init__(self, argv, spawned, **kw):
        self.argv = argv
        self.kw = kw
        self.pid = 4242
        if argv:
            spawned.append(argv[0])

    def poll(self):
        return 0

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def _neutralize_launch(monkeypatch, spawned):
    """Fake out every real side effect of _launch: instant spice socket, no virtiofsd,
    no config-watcher thread, no window maximize, non-interactive stdin, and a Popen
    that records into `spawned`."""
    monkeypatch.setattr(vm.os.path, "exists", lambda p: True)
    monkeypatch.setattr(vm, "_spawn_virtiofsd", lambda cfg: None)
    monkeypatch.setattr(vm.subprocess, "Popen",
                        lambda argv, **kw: _FakeProc(argv, spawned, **kw))
    monkeypatch.setattr(vm.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(vm.configuration_watcher, "ConfigWatcher",
                        lambda *a, **k: type("W", (), {"start": lambda s: None,
                                                       "stop": lambda s: None})())
    monkeypatch.setattr(vm, "_maximize_window", lambda *a, **k: None)
    monkeypatch.setattr(vm.sys.stdin, "isatty", lambda: False, raising=False)


# --- headless boot: QEMU yes, remote-viewer no -------------------------------

def test_headless_launch_spawns_qemu_but_not_viewer(tmp_path, monkeypatch):
    spawned: list[str] = []
    _neutralize_launch(monkeypatch, spawned)
    cfg = _cfg(tmp_path, shared=False, ssh=False)
    vm._launch(cfg, ["qemu-system-x86_64"], port=None, headless=True)
    assert "qemu-system-x86_64" in spawned, "headless boot must still start QEMU"
    assert "remote-viewer" not in spawned, (
        "headless boot must NOT spawn remote-viewer; got " + repr(spawned))


def test_non_headless_launch_still_spawns_viewer(tmp_path, monkeypatch):
    # Guard against a regression where the headless branch accidentally suppresses the
    # viewer for the NORMAL windowed boot too.
    spawned: list[str] = []
    _neutralize_launch(monkeypatch, spawned)
    cfg = _cfg(tmp_path, shared=False, ssh=False)
    vm._launch(cfg, ["qemu-system-x86_64"], port=None)  # default headless=False
    assert "qemu-system-x86_64" in spawned
    assert "remote-viewer" in spawned, "windowed boot must spawn remote-viewer"


def test_do_run_headless_skips_require_viewer(tmp_path, monkeypatch):
    # headless=True must NOT gate on a viewer being installed (the whole point on a
    # display-less host). Make require_viewer BLOW UP: if do_run(headless=True) reaches
    # it the test fails; the other require_* are stubbed to no-ops.
    def _boom():
        raise AssertionError("require_viewer must NOT be called when headless=True")

    monkeypatch.setattr(checks, "require_viewer", _boom)
    for name in ("require_writable_dir", "require_qemu", "require_ovmf",
                 "require_kvm", "require_not_running"):
        monkeypatch.setattr(checks, name, lambda *a, **k: None)
    captured = {}
    monkeypatch.setattr(vm, "build_qemu_argv", lambda *a, **k: ["qemu-system-x86_64"])
    monkeypatch.setattr(vm, "_write_viewer_ask_quit", lambda *a, **k: None)
    monkeypatch.setattr(vm, "select_ssh_port", lambda cfg: None)

    def _fake_launch(cfg, qemu, port, headless=False):
        captured["headless"] = headless

    monkeypatch.setattr(vm, "_launch", _fake_launch)
    cfg = _cfg(tmp_path, shared=False, ssh=False)
    vm.do_run(cfg, headless=True)
    assert captured.get("headless") is True


def test_do_run_non_headless_requires_viewer(tmp_path, monkeypatch):
    # The mirror: a NORMAL run DOES require a viewer. require_viewer raising must
    # propagate out of do_run when headless is not set.
    class _Stop(Exception):
        pass

    def _boom():
        raise _Stop()

    monkeypatch.setattr(checks, "require_viewer", _boom)
    for name in ("require_writable_dir", "require_qemu", "require_ovmf",
                 "require_kvm", "require_not_running"):
        monkeypatch.setattr(checks, name, lambda *a, **k: None)
    monkeypatch.setattr(vm, "_launch", lambda *a, **k: None)
    cfg = _cfg(tmp_path, shared=False, ssh=False)
    with pytest.raises(_Stop):
        vm.do_run(cfg)  # headless defaults False -> require_viewer runs -> raises


# --- cli dispatch parses --headless ------------------------------------------

def test_cli_dispatch_run_parses_headless(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(vm, "do_run",
                        lambda cfg, **kw: captured.update(kw))
    # resolve_run_disk just needs to return a disk path. Patch it on the CLASS (not the
    # instance): _dispatch_run rebuilds Config from cfg.__dict__, so an instance attr
    # would leak in as a bogus constructor kwarg.
    disk = os.path.join(str(tmp_path), "azzio.qcow2")
    monkeypatch.setattr(vm.os.path, "exists", lambda p: True)

    cfg = _cfg(tmp_path, shared=False, ssh=False)
    monkeypatch.setattr(type(cfg), "resolve_run_disk", lambda self, arg: disk, raising=False)

    cli._dispatch_run(cfg, ["azzio.qcow2", "--headless"])
    assert captured.get("headless") is True

    captured.clear()
    cli._dispatch_run(cfg, ["azzio.qcow2"])
    assert captured.get("headless") is False


# --- virtiofs.sock.pid -------------------------------------------------------

def test_virtiofs_pidfile_path_is_socket_plus_pid(tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg.virtiofs_pidfile == cfg.virtiofs_sock + ".pid"


def test_spawn_virtiofsd_writes_pidfile(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, shared=str(tmp_path))
    monkeypatch.setattr(vm, "virtiofsd_argv", lambda cfg: ["virtiofsd", "--x"])
    monkeypatch.setattr(vm.os.path, "exists", lambda p: True)

    class _P:
        pid = 9182

        def poll(self):
            return None

    monkeypatch.setattr(vm.subprocess, "Popen", lambda argv, **kw: _P())
    vm._spawn_virtiofsd(cfg)
    with open(cfg.virtiofs_pidfile, encoding="utf-8") as fh:
        assert fh.read().strip() == "9182"


def test_write_pidfile_is_best_effort_on_unwritable(tmp_path):
    # An unwritable path must not raise (a dir we cannot write to must not abort a boot).
    vm._write_pidfile(os.path.join(str(tmp_path), "no_such_dir", "x.pid"), 5)


# --- ls: system-wide running-instance enumeration ----------------------------

def test_running_instances_matches_vm_comms_and_sorts(tmp_path, monkeypatch):
    # Build two real cfg dirs so _cfg_ssh_port reads a genuine hypervisor.cfg.
    d_a = tmp_path / "azzio"
    d_b = tmp_path / "myproj"
    for d, ssh_line in ((d_a, "ssh = true\nssh_guest_to_host_port_forward = 50007\n"),
                        (d_b, "ssh = false\n")):
        os.mkdir(d)
        with open(d / "hypervisor.cfg", "w", encoding="utf-8") as fh:
            fh.write(ssh_line)

    cwds = {222: str(d_b), 333: str(d_a), 555: ""}  # 555 has no cwd -> dropped
    # _running_instances lives in vm_instances and calls _pid_cwd module-locally, so patch
    # it THERE (vm._pid_cwd is only a re-export and would not be seen by the callee).
    monkeypatch.setattr(vm_instances, "_pid_cwd", lambda pid: cwds.get(pid, ""))

    table = [
        (11, "init"),                     # not a VM
        (222, "myproj-vm"),               # VM, ssh off
        (333, "azzio-vm"),                # VM, ssh on 50007
        (444, "qemu-system-x86_64"),      # not a VM (doesn't end -vm)
        (555, "ghost-vm"),                # VM but no cwd -> skipped
    ]
    got = vm_instances._running_instances(table)
    assert [i["vm"] for i in got] == ["azzio", "myproj"], got  # sorted by name
    by = {i["vm"]: i for i in got}
    assert by["azzio"]["ssh_port"] == 50007
    assert by["azzio"]["pid"] == 333
    assert by["myproj"]["ssh_port"] is None  # ssh off -> None


def test_cfg_ssh_port_defaults_when_no_port_line(tmp_path):
    d = tmp_path
    with open(d / "hypervisor.cfg", "w", encoding="utf-8") as fh:
        fh.write("ssh = true\n")  # on, but no explicit port
    assert vm_instances._cfg_ssh_port(str(d)) == vm_instances.DEFAULT_SSH_FORWARD_PORT


def test_cfg_ssh_port_none_when_unreadable(tmp_path):
    assert vm_instances._cfg_ssh_port(str(tmp_path / "does_not_exist")) is None


def test_do_ls_prints_rows(tmp_path, monkeypatch, capsys):
    # do_ls calls _running_instances module-locally in vm_instances -> patch it there.
    monkeypatch.setattr(vm_instances, "_running_instances",
                        lambda: [{"vm": "azzio", "pid": 333, "dir": "/home/u/azzio",
                                  "ssh_port": 50007}])
    vm.do_ls(_cfg(tmp_path))
    out = capsys.readouterr().out
    assert "azzio" in out and "333" in out and "50007" in out and "/home/u/azzio" in out


def test_do_ls_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(vm_instances, "_running_instances", lambda: [])
    vm.do_ls(_cfg(tmp_path))
    assert "No hypervisor VMs are running." in capsys.readouterr().out


def test_vm_reexports_ls_names():
    # The CLI dispatch and older call sites use vm.do_ls / vm._running_instances; the
    # split must keep those names pointing at the vm_instances implementations.
    assert vm.do_ls is vm_instances.do_ls
    assert vm._running_instances is vm_instances._running_instances


# --- view: attach-only viewer ------------------------------------------------

def test_do_view_refuses_when_not_running(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "require_viewer", lambda: None)
    monkeypatch.setattr(vm, "is_running", lambda cfg: False)
    with pytest.raises(HypervisorError):
        vm.do_view(_cfg(tmp_path))


def test_do_view_refuses_without_socket(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "require_viewer", lambda: None)
    monkeypatch.setattr(vm, "is_running", lambda cfg: True)
    monkeypatch.setattr(vm.os.path, "exists", lambda p: False)  # no spice socket
    with pytest.raises(HypervisorError):
        vm.do_view(_cfg(tmp_path))


def test_do_view_attaches_and_does_not_stop_vm(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "require_viewer", lambda: None)
    monkeypatch.setattr(vm, "is_running", lambda cfg: True)
    monkeypatch.setattr(vm.os.path, "exists", lambda p: True)
    events = {"spawned": 0, "waited": 0}

    class _V:
        def wait(self):
            events["waited"] += 1

        def poll(self):
            return 0

    def _fake_spawn(cfg):
        events["spawned"] += 1
        return _V()

    monkeypatch.setattr(vm, "_spawn_viewer", _fake_spawn)
    # do_view must NOT call do_stop / cleanup: attach semantics leave the VM up. If it
    # tried to stop, this would blow up.
    monkeypatch.setattr(vm, "do_stop",
                        lambda cfg: (_ for _ in ()).throw(AssertionError("view stopped the VM")))
    vm.do_view(_cfg(tmp_path))
    assert events == {"spawned": 1, "waited": 1}


# --- _spawn_viewer wiring ----------------------------------------------------

def test_spawn_viewer_uses_spice_socket_and_devnull_stdin(tmp_path, monkeypatch):
    captured = {}

    def _popen(argv, **kw):
        captured["argv"] = argv
        captured["kw"] = kw
        return object()

    monkeypatch.setattr(vm.subprocess, "Popen", _popen)
    monkeypatch.setattr(vm, "_maximize_window", lambda *a, **k: None)
    cfg = _cfg(tmp_path)
    vm._spawn_viewer(cfg)
    assert captured["argv"][0] == "remote-viewer"
    assert any(a == f"spice+unix://{cfg.spice_sock}" for a in captured["argv"]), captured["argv"]
    assert captured["kw"].get("stdin") is subprocess.DEVNULL
