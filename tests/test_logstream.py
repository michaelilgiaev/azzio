"""logstream -- the _Tee that mirrors build output into logs/compile-full.log.

Why these tests matter: every print() and sys.stderr.write in the build funnels
through the _Tee installed here. Two behaviors are load-bearing and have each
regressed before:

  1. write_split() must send DIFFERENT bytes to the terminal and the log -- the
     terminal copy is width-clipped so a long pacstrap line does not wrap and
     desync the pinned progress bar, but compile-full.log must keep the FULL untruncated
     line. A prior change routed the clipped copy through plain write() and
     silently cut the tail off every wide mkarchiso line in the log. If
     write_split ever collapses back to writing the same text to both, or returns
     the log length instead of the terminal length, the terminal wraps again.

  2. A write must survive the log going away (closed / read-only / disk full)
     WITHOUT killing the terminal -- the human watching must keep seeing output
     even after logging breaks. Only (ValueError, OSError) are swallowed; any
     other exception is a real bug and must still propagate.

Everything here is pure in-memory stream behavior (io.StringIO stands in for the
terminal and the log); install() is exercised against a tmp_path file with
sys.stdout/sys.stderr saved and restored. No network, no subprocess, no tty.
"""

from __future__ import annotations

import io
import sys

import pytest

import logstream
from logstream import _Tee


# --- write(): mirror the SAME bytes to both streams ------------------------

def test_write_mirrors_both():
    term, log = io.StringIO(), io.StringIO()
    t = _Tee(term, log)
    n = t.write("hello")
    assert n == 5
    assert term.getvalue() == "hello"
    assert log.getvalue() == "hello"


def test_write_returns_terminal_count_not_log_count():
    # n comes from the terminal write; the log write's return value is discarded.
    term, log = io.StringIO(), io.StringIO()
    t = _Tee(term, log)
    assert t.write("abc") == 3


# --- write_split(): DIFFERENT bytes per stream (named past regression) ------

def test_write_split_independent():
    # The clipped terminal copy and the full log copy must stay independent.
    term, log = io.StringIO(), io.StringIO()
    t = _Tee(term, log)
    n = t.write_split("clip", "clip-full-untruncated")
    assert term.getvalue() == "clip"
    assert log.getvalue() == "clip-full-untruncated"
    # Return value is the TERMINAL length (4), NOT the log length (21).
    assert n == 4


def test_write_split_does_not_leak_log_text_to_terminal():
    # Regression guard: the full line must never appear on the terminal.
    term, log = io.StringIO(), io.StringIO()
    t = _Tee(term, log)
    t.write_split("short", "short-plus-a-very-long-tail-that-would-wrap")
    assert "tail" not in term.getvalue()
    assert "tail" in log.getvalue()


# --- error containment: only (ValueError, OSError) are swallowed -----------

class _RaisingLog:
    """A log stream whose write/flush raise a chosen exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def write(self, text: str) -> int:
        raise self._exc

    def flush(self) -> None:
        raise self._exc


def test_write_swallows_valueerror():
    # A closed log raises ValueError on write; the terminal must stay alive and
    # the terminal byte count must still be returned.
    term = io.StringIO()
    t = _Tee(term, _RaisingLog(ValueError("I/O operation on closed file")))
    n = t.write("keep-going")
    assert n == len("keep-going")
    assert term.getvalue() == "keep-going"


def test_write_swallows_oserror():
    term = io.StringIO()
    t = _Tee(term, _RaisingLog(OSError("No space left on device")))
    n = t.write("still-here")
    assert n == len("still-here")
    assert term.getvalue() == "still-here"


def test_write_propagates_other_exceptions():
    # A KeyError is NOT in (ValueError, OSError); it must propagate, not be hidden.
    term = io.StringIO()
    t = _Tee(term, _RaisingLog(KeyError("boom")))
    with pytest.raises(KeyError):
        t.write("x")
    # The terminal write happened before the log write blew up.
    assert term.getvalue() == "x"


def test_write_split_swallows_log_valueerror():
    term = io.StringIO()
    t = _Tee(term, _RaisingLog(ValueError("closed")))
    n = t.write_split("term-copy", "full-log-copy")
    assert n == len("term-copy")
    assert term.getvalue() == "term-copy"


def test_write_split_propagates_other_exceptions():
    term = io.StringIO()
    t = _Tee(term, _RaisingLog(RuntimeError("nope")))
    with pytest.raises(RuntimeError):
        t.write_split("a", "b")


# --- flush(): terminal always flushed, log errors swallowed ----------------

def test_flush_flushes_terminal_and_swallows_closed_log():
    class _RecordingTerm(io.StringIO):
        flushed = 0

        def flush(self) -> None:  # type: ignore[override]
            type(self).flushed += 1

    term = _RecordingTerm()
    t = _Tee(term, _RaisingLog(ValueError("closed")))
    t.flush()  # must not raise despite the log flush blowing up
    assert term.flushed == 1


def test_flush_propagates_non_swallowed_log_error():
    term = io.StringIO()
    t = _Tee(term, _RaisingLog(RuntimeError("nope")))
    with pytest.raises(RuntimeError):
        t.flush()


# --- isatty()/fileno(): delegate to the terminal, ignore the log -----------

class _FakeStream:
    def __init__(self, atty: bool, fd: int) -> None:
        self._atty = atty
        self._fd = fd

    def isatty(self) -> bool:
        return self._atty

    def fileno(self) -> int:
        return self._fd


def test_isatty_delegates_to_terminal():
    # The log answers the opposite; the _Tee must report the TERMINAL's answer.
    term = _FakeStream(atty=True, fd=1)
    log = _FakeStream(atty=False, fd=99)
    t = _Tee(term, log)
    assert t.isatty() is True


def test_fileno_delegates_to_terminal():
    term = _FakeStream(atty=True, fd=7)
    log = _FakeStream(atty=False, fd=99)
    t = _Tee(term, log)
    assert t.fileno() == 7


# --- install(): swap sys.stdout/sys.stderr, append to compile-full.log -------------

@pytest.fixture
def restore_std_streams():
    saved_out, saved_err = sys.stdout, sys.stderr
    try:
        yield
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err


def test_install_wraps_both_streams_in_tee(tmp_path, monkeypatch, restore_std_streams):
    logpath = tmp_path / "compile-full.log"
    monkeypatch.setattr(logstream.paths, "FULL_LOG", logpath)
    logfile = logstream.install()
    try:
        assert isinstance(sys.stdout, _Tee)
        assert isinstance(sys.stderr, _Tee)
        # stdout tees the pristine terminal stream into the returned log handle.
        assert sys.stdout._log is logfile
        assert sys.stdout._term is sys.__stdout__
        assert sys.stderr._term is sys.__stderr__
    finally:
        logfile.close()


def test_install_returns_open_appendable_handle(tmp_path, monkeypatch, restore_std_streams):
    logpath = tmp_path / "compile-full.log"
    monkeypatch.setattr(logstream.paths, "FULL_LOG", logpath)
    logfile = logstream.install()
    try:
        assert logfile.closed is False
    finally:
        logfile.close()


def test_install_appends_does_not_truncate(tmp_path, monkeypatch, restore_std_streams):
    # compile.sh truncates compile-full.log at launch; install() opens in append mode, so a
    # pre-existing "PRE" survives and the first write lands after it -> "PREPOST".
    logpath = tmp_path / "compile-full.log"
    logpath.write_text("PRE")
    monkeypatch.setattr(logstream.paths, "FULL_LOG", logpath)
    logfile = logstream.install()
    try:
        sys.stdout.write("POST")
        logfile.flush()
        assert logpath.read_text() == "PREPOST"
    finally:
        logfile.close()


def test_install_write_reaches_log_file(tmp_path, monkeypatch, restore_std_streams):
    # A write through the swapped-in stdout is mirrored into the log file in real
    # time (each write is flushed), so tail -f sees it immediately.
    logpath = tmp_path / "compile-full.log"
    monkeypatch.setattr(logstream.paths, "FULL_LOG", logpath)
    logfile = logstream.install()
    try:
        sys.stderr.write("via-stderr\n")
        logfile.flush()
        assert logpath.read_text() == "via-stderr\n"
    finally:
        logfile.close()


# --- run_teed(): child output MUST reach compile-full.log (the real-time bug fix) ----
#
# The bug: install() swaps sys.stdout/sys.stderr to a _Tee at the PYTHON-OBJECT
# layer only -- it does NOT dup2 the real fds 1/2. So a child spawned by a bare
# subprocess.run (no stdout=/stderr=) inherits the numeric fds and its output
# NEVER traverses the _Tee -> permanently absent from compile-full.log (the log looked
# frozen for the whole makepkg compile / pacman -Sw download). run_teed pipes the
# child and pumps its output through sys.stdout (the tee), so it lands in the log.
# These run REAL short children (python3 -c ...) against a tmp compile-full.log.

def test_run_teed_child_stdout_reaches_log(tmp_path, monkeypatch, restore_std_streams):
    # A child's stdout, which a bare subprocess.run would send to the inherited fd
    # (bypassing the tee), must appear in compile-full.log when run through run_teed.
    logpath = tmp_path / "compile-full.log"
    monkeypatch.setattr(logstream.paths, "FULL_LOG", logpath)
    logfile = logstream.install()
    try:
        rc = logstream.run_teed(
            [sys.executable, "-c", "print('child-line-one'); print('child-line-two')"]
        )
        logfile.flush()
    finally:
        logfile.close()
    assert rc == 0
    text = logpath.read_text()
    assert "child-line-one" in text
    assert "child-line-two" in text


def test_run_teed_child_stderr_folded_into_log(tmp_path, monkeypatch, restore_std_streams):
    # stderr=STDOUT folds the child's stderr into the same stream, so an error the
    # child prints to stderr also reaches compile-full.log (not just its stdout).
    logpath = tmp_path / "compile-full.log"
    monkeypatch.setattr(logstream.paths, "FULL_LOG", logpath)
    logfile = logstream.install()
    try:
        logstream.run_teed(
            [sys.executable, "-c", "import sys; sys.stderr.write('err-on-stderr\\n')"]
        )
        logfile.flush()
    finally:
        logfile.close()
    assert "err-on-stderr" in logpath.read_text()


def test_run_teed_returns_child_exit_code(tmp_path, monkeypatch, restore_std_streams):
    # The caller keeps its own returncode handling (raise / branch), so run_teed
    # must return the child's real exit code, not swallow it.
    logpath = tmp_path / "compile-full.log"
    monkeypatch.setattr(logstream.paths, "FULL_LOG", logpath)
    logfile = logstream.install()
    try:
        rc = logstream.run_teed([sys.executable, "-c", "import sys; sys.exit(7)"])
    finally:
        logfile.close()
    assert rc == 7


def test_run_teed_splits_carriage_return_frames(tmp_path, monkeypatch, restore_std_streams):
    # pacman/makepkg redraw progress with \r, not \n. run_teed splits on BOTH so
    # the frames become separate log lines instead of collapsing into one giant
    # line (or being swallowed until the phase ends). A child that writes "a\rb\rc"
    # with no trailing newline must yield three lines in the log.
    logpath = tmp_path / "compile-full.log"
    monkeypatch.setattr(logstream.paths, "FULL_LOG", logpath)
    logfile = logstream.install()
    try:
        logstream.run_teed(
            [sys.executable, "-c", "import sys; sys.stdout.write('a\\rb\\rc')"]
        )
        logfile.flush()
    finally:
        logfile.close()
    # Each \r ends a frame; the trailing "c" (no newline) is flushed on EOF.
    assert logpath.read_text() == "a\nb\nc\n"


# --- run_teed(): heartbeat keeps the log ticking during a SILENT child --------
#
# The bug this guards: a long child (rustc/gcc link, a stalled keyserver, a big
# `pacman -Sw` gap) emits NOTHING for minutes, so compile-full.log and the terminal look
# frozen -- the user can't tell "working" from "hung". A daemon thread prints a
# '... still running' line once the gap since the last output exceeds `heartbeat`
# seconds. These use a tiny interval and a child that sleeps to force the gap.

def test_run_teed_heartbeat_fires_during_silence(tmp_path, monkeypatch, restore_std_streams):
    # A child that sleeps ~0.6s with NO output, heartbeat=0.2s -> at least one
    # '... still running' line must appear in the log even though the child was silent.
    logpath = tmp_path / "compile-full.log"
    monkeypatch.setattr(logstream.paths, "FULL_LOG", logpath)
    logfile = logstream.install()
    try:
        rc = logstream.run_teed(
            [sys.executable, "-c", "import time; time.sleep(0.6)"], heartbeat=0.2
        )
        logfile.flush()
    finally:
        logfile.close()
    assert rc == 0
    assert "still running" in logpath.read_text()


def test_run_teed_heartbeat_disabled_with_zero(tmp_path, monkeypatch, restore_std_streams):
    # heartbeat=0 turns the thread into a no-op: a silent child produces NO
    # heartbeat line. (Also proves heartbeat is popped, not forwarded to Popen --
    # Popen would raise TypeError on an unknown kwarg.)
    logpath = tmp_path / "compile-full.log"
    monkeypatch.setattr(logstream.paths, "FULL_LOG", logpath)
    logfile = logstream.install()
    try:
        rc = logstream.run_teed(
            [sys.executable, "-c", "import time; time.sleep(0.4)"], heartbeat=0
        )
        logfile.flush()
    finally:
        logfile.close()
    assert rc == 0
    assert "still running" not in logpath.read_text()


def test_run_teed_heartbeat_quiet_when_child_is_chatty(tmp_path, monkeypatch, restore_std_streams):
    # A child that prints steadily (gap always < heartbeat) must NOT trigger a
    # heartbeat -- the liveness line is only for genuine silence.
    logpath = tmp_path / "compile-full.log"
    monkeypatch.setattr(logstream.paths, "FULL_LOG", logpath)
    logfile = logstream.install()
    try:
        logstream.run_teed(
            [sys.executable, "-c",
             "import time\n"
             "for i in range(6):\n"
             "    print('tick', i, flush=True)\n"
             "    time.sleep(0.05)"],
            heartbeat=0.5,
        )
        logfile.flush()
    finally:
        logfile.close()
    text = logpath.read_text()
    assert "tick 5" in text
    assert "still running" not in text


def test_run_teed_passes_cwd_kwarg(tmp_path, monkeypatch, restore_std_streams):
    # cwd/env/... pass straight through to Popen; verify the child actually runs in
    # the given cwd (makepkg relies on cwd=recipe_dir).
    logpath = tmp_path / "compile-full.log"
    monkeypatch.setattr(logstream.paths, "FULL_LOG", logpath)
    logfile = logstream.install()
    workdir = tmp_path / "work"
    workdir.mkdir()
    try:
        logstream.run_teed(
            [sys.executable, "-c", "import os; print(os.getcwd())"], cwd=str(workdir)
        )
        logfile.flush()
    finally:
        logfile.close()
    # realpath: macOS/tmp symlinks differ; compare resolved paths.
    import os
    assert os.path.realpath(str(workdir)) in logpath.read_text()
