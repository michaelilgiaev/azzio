"""test_is_running_dir_aware.py -- checks.is_running must be DIRECTORY-aware.

Regression for the codelis wedge: `hypervisor run` in a dir whose VM name is
`codelis-claudedebug` builds a QEMU comm of `codelis-clau-vm` (the slug is trimmed to
12 chars before "-vm" so it fits the kernel's 15-char comm limit -- see
configuration._proc_name). A bare `pgrep -x codelis-clau-vm` then matches:

  * ANY OTHER `codelis-claud*` instance (same 12-char prefix -> same comm), and
  * any unrelated/orphaned process that merely carries that comm, in ANY cwd.

Because teardown (`stop`) acts by DIRECTORY/disk-path, it cannot kill such a foreign
process -- so `require_not_running` kept firing "VM already running" forever and the
launch deadlocked. is_running must therefore count a match ONLY when the process's comm
matches cfg.proc AND its /proc/<pid>/cwd resolves to cfg.dir.

The suite fakes /proc entirely (os.listdir("/proc"), open(".../comm"), os.readlink(
".../cwd")) so it is hermetic -- no real QEMU, no real pids.
"""

from __future__ import annotations

import importlib
import io
import os
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "libraries" / "packages"


@pytest.fixture()
def hv(monkeypatch):
    monkeypatch.syspath_prepend(str(PKG))
    checks = importlib.import_module("hypervisor.checks")
    importlib.reload(checks)
    return checks


def _fake_proc(monkeypatch, checks, table):
    """Point checks.is_running's /proc reads at `table`: {pid: (comm, cwd)}.

    cwd may end in " (deleted)" to exercise the unlinked-dir strip. A pid absent from
    the table (or with cwd=None) raises OSError on read, mirroring a vanished process.
    """
    pids = [str(p) for p in table]

    real_listdir = os.listdir
    def fake_listdir(path):
        if path == "/proc":
            return pids + ["not-a-pid", "cpuinfo"]  # non-numeric entries must be ignored
        return real_listdir(path)

    def fake_open(path, *a, **k):
        # only intercept /proc/<pid>/comm; delegate anything else to the real open
        if path.startswith("/proc/") and path.endswith("/comm"):
            pid = int(path.split("/")[2])
            if pid in table and table[pid][0] is not None:
                return io.StringIO(table[pid][0] + "\n")
            raise OSError("no such comm")
        return _REAL_OPEN(path, *a, **k)

    def fake_readlink(path):
        if path.startswith("/proc/") and path.endswith("/cwd"):
            pid = int(path.split("/")[2])
            if pid in table and table[pid][1] is not None:
                return table[pid][1]
            raise OSError("no such cwd")
        return os.readlink(path)

    monkeypatch.setattr(checks.os, "listdir", fake_listdir)
    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr(checks.os, "readlink", fake_readlink)


_REAL_OPEN = open


class _Cfg:
    """Minimal stand-in for Config: is_running only touches .proc and .dir."""
    def __init__(self, proc, directory):
        self.proc = proc
        self.dir = directory


def test_same_comm_in_a_DIFFERENT_dir_is_not_running(hv, monkeypatch):
    """THE BUG: a process with the same truncated comm but a foreign cwd must NOT count."""
    checks = hv
    cfg = _Cfg("codelis-clau-vm", "/home/main/Ignore/claudedebug/venv/codelis")
    # A colliding QEMU living somewhere else entirely (e.g. another codelis-claud* dir,
    # or a stale orphan). Same comm, different cwd.
    _fake_proc(monkeypatch, checks, {
        4242: ("codelis-clau-vm", "/some/other/place/venv/codelis"),
    })
    assert checks.is_running(cfg) is False


def test_same_comm_in_THIS_dir_is_running(hv, monkeypatch):
    """Positive: the real VM (comm matches AND cwd is cfg.dir) is reported running."""
    checks = hv
    d = "/home/main/Ignore/claudedebug/venv/codelis"
    cfg = _Cfg("codelis-clau-vm", d)
    _fake_proc(monkeypatch, checks, {4242: ("codelis-clau-vm", d)})
    assert checks.is_running(cfg) is True


def test_deleted_cwd_suffix_still_matches_this_dir(hv, monkeypatch):
    """A VM whose dir was removed while it runs (cwd -> '<dir> (deleted)') is still ours,
    so disk-path teardown can act on it."""
    checks = hv
    d = "/home/main/Ignore/claudedebug/venv/codelis"
    cfg = _Cfg("codelis-clau-vm", d)
    _fake_proc(monkeypatch, checks, {4242: ("codelis-clau-vm", d + " (deleted)")})
    assert checks.is_running(cfg) is True


def test_no_matching_process_is_not_running(hv, monkeypatch):
    checks = hv
    cfg = _Cfg("codelis-clau-vm", "/home/main/Ignore/claudedebug/venv/codelis")
    _fake_proc(monkeypatch, checks, {
        111: ("bash", "/home/main"),
        222: ("azzio-vm", "/home/main/Hypervisors/azzio"),  # a real OTHER VM, different comm
    })
    assert checks.is_running(cfg) is False


def test_two_claud_instances_do_not_see_each_other(hv, monkeypatch):
    """Both codelis-claudedebug and codelis-claudフoo collapse to comm 'codelis-clau-vm',
    but each is_running only sees its OWN dir."""
    checks = hv
    dbg = "/home/main/Ignore/claudedebug/venv/codelis"
    other = "/home/main/Ignore/claudextra/venv/codelis"
    _fake_proc(monkeypatch, checks, {
        10: ("codelis-clau-vm", dbg),
        20: ("codelis-clau-vm", other),
    })
    assert checks.is_running(_Cfg("codelis-clau-vm", dbg)) is True
    assert checks.is_running(_Cfg("codelis-clau-vm", other)) is True
    assert checks.is_running(_Cfg("codelis-clau-vm", "/nowhere/venv/codelis")) is False


def test_missing_proc_dir_degrades_to_not_running(hv, monkeypatch):
    """No /proc (or unreadable) must be safe: report not running, never crash."""
    checks = hv
    def boom(path):
        raise OSError("no /proc")
    monkeypatch.setattr(checks.os, "listdir", boom)
    assert checks.is_running(_Cfg("codelis-clau-vm", "/whatever")) is False
