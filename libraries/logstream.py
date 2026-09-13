"""Own the build logs from Python instead of letting util-linux `script` do it.

Why this exists: compile.sh re-execs on a PTY so pacman/mkarchiso keep their live
progress redraws and so the process is a real terminal (the progress bar only
paints to a tty). Previously `script` also MIRRORED that whole PTY into
logs/compile-full.log -- which meant the pinned progress bar's ANSI escapes and █/░
glyphs were copied straight into the log, where they do not belong (the bar is
for the human watching the terminal, not the log).

The fix: `script` now writes its capture to /dev/null (it is kept ONLY for the
PTY), and Python owns compile-full.log itself. Every `print()` / `sys.stderr.write` in
the build funnels through the _Tee installed here, which writes to BOTH the real
terminal AND compile-full.log, flushing each write (real-time, tail-able). The progress
bar deliberately bypasses this tee -- it writes to the raw terminal
(`sys.__stdout__`) only, so its escape codes never reach the log. See
progress.ProgressBar (self.term) and steps._drive_mkarchiso_progress.
"""

from __future__ import annotations

import io
import subprocess
import sys
import threading
import time
from typing import TextIO

import paths


class _Tee:
    """A minimal text stream that duplicates every write to a terminal stream and
    a log file, flushing both each time so the log stays real-time (tail -f works)
    and nothing is lost on a hard Ctrl-C exit."""

    def __init__(self, term: TextIO, logfile: TextIO) -> None:
        self._term = term
        self._log = logfile

    def write(self, text: str) -> int:
        n = self._term.write(text)
        self._term.flush()
        try:
            self._log.write(text)
            self._log.flush()
        except (ValueError, OSError):
            pass  # log closed/unwritable -> keep the terminal alive regardless
        return n

    def write_split(self, term_text: str, log_text: str) -> int:
        """Write DIFFERENT bytes to the terminal and the log in one call.

        Used by the mkarchiso driver: the terminal copy is width-clipped so a long
        pacstrap line does not wrap and desync the pinned progress bar's scroll
        region, but the log must keep the FULL untruncated line. Writing the clipped
        copy through plain write() (as a prior change did) silently cut the tail off
        every wide mkarchiso line in compile-full.log. This keeps them independent."""
        n = self._term.write(term_text)
        self._term.flush()
        try:
            self._log.write(log_text)
            self._log.flush()
        except (ValueError, OSError):
            pass
        return n

    def flush(self) -> None:
        self._term.flush()
        try:
            self._log.flush()
        except (ValueError, OSError):
            pass

    # A few programs/libs probe these; delegate to the terminal stream.
    def isatty(self) -> bool:
        return self._term.isatty()

    def fileno(self) -> int:
        return self._term.fileno()


def run_teed(cmd: list[str], **kw) -> int:
    """Run a child process and MIRROR its output into compile-full.log in real time,
    returning the exit code.

    The problem this solves: install() swaps sys.stdout/sys.stderr to a _Tee at the
    PYTHON-OBJECT layer -- it does NOT os.dup2() the real fds 1/2. So a child spawned
    by subprocess.run without stdout=/stderr= inherits the numeric fds (under
    compile.sh those are the slave PTY, whose `script` capture goes to /dev/null),
    its bytes reach the terminal but NEVER traverse the _Tee, and so are permanently
    absent from compile-full.log -- the log looks frozen for the whole of a long child
    (the makepkg compile, `pacman -Sw`'s download). mkarchiso already dodges this by
    piping its output and re-emitting it through the tee; this is that pattern made
    reusable for the other noisy children (makepkg, pacman -S/-Sy/-Sw).

    We wrap the pipe in a TextIOWrapper with newline="" and split on BOTH \\r and \\n
    (same as steps._drive_mkarchiso_progress): pacman/makepkg redraw progress with a
    carriage return, not a newline, so a plain readline() would swallow every partial
    frame into one giant line (or block until the phase ends). Each completed line is
    written through sys.stdout -- the _Tee -- so it lands on the terminal AND in
    compile-full.log with the tee's per-line flush, i.e. tail-able in real time.

    kwargs (cwd, env, ...) pass straight through to Popen -- EXCEPT ``heartbeat``,
    which is popped off here (see below). stdout/stderr/stdin are fixed here
    (stdout=PIPE, stderr=STDOUT to fold both streams into the log, stdin from
    DEVNULL so an unattended child hitting a prompt takes its default instead of
    blocking on the closed PTY). The caller keeps its OWN returncode handling (raise
    / branch); this only returns the int.

    Heartbeat: a long child can go SILENT for minutes (rustc/gcc linking, a stalled
    keyserver fetch, a big `pacman -Sw` between packages). With no output the log and
    terminal look frozen and the user can't tell "working" from "hung". A daemon
    thread here watches the wall-clock gap since the last emitted line and, once it
    exceeds ``heartbeat`` seconds, prints a '... still running (Ns elapsed)' line
    through the same tee so BOTH the terminal and compile-full.log keep ticking. Pass
    ``heartbeat=0`` to disable (e.g. for a child that is expected to be brief and
    whose own progress redraws already prove liveness). Default is 20s."""
    heartbeat = kw.pop("heartbeat", 20)
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw,
    )

    # last_line[0] = monotonic seconds of the most recent emitted line. The reader
    # loop stamps it on every completed line; the heartbeat thread reads it. A single
    # element list is the shared cell (no lock needed: one writer, one reader, and a
    # torn read at worst prints one spurious/early heartbeat, which is harmless).
    last_line = [time.monotonic()]
    stop = threading.Event()

    def beat() -> None:
        if heartbeat <= 0:
            return
        start = time.monotonic()
        while not stop.wait(heartbeat):
            quiet = time.monotonic() - last_line[0]
            if quiet >= heartbeat:
                elapsed = int(time.monotonic() - start)
                # Through sys.stdout (the _Tee) -> terminal AND compile-full.log.
                sys.stdout.write(f"    ... still running ({elapsed}s elapsed, quiet {int(quiet)}s)\n")

    hb = threading.Thread(target=beat, name="run-teed-heartbeat", daemon=True)
    hb.start()

    reader = io.TextIOWrapper(proc.stdout, encoding="utf-8", errors="replace", newline="")
    buf = ""
    try:
        while True:
            ch = reader.read(1)
            if not ch:
                break
            if ch in ("\n", "\r"):
                sys.stdout.write(buf + "\n")  # sys.stdout is the _Tee -> terminal + compile-full.log
                last_line[0] = time.monotonic()
                buf = ""
            else:
                buf += ch
        if buf:
            sys.stdout.write(buf + "\n")
    finally:
        stop.set()
    return proc.wait()


def install() -> TextIO:
    """Open compile-full.log (append; compile.sh already truncated it at launch) and route
    sys.stdout + sys.stderr through a _Tee so all build output is mirrored into the
    log in real time. Returns the open log file handle so the caller can keep a
    reference (closed implicitly at process exit; every write is already flushed).

    The progress bar captures sys.__stdout__ BEFORE this swap is even relevant --
    it always uses the pristine terminal stream -- so bar escapes never enter the
    tee and never reach the log."""
    logfile = paths.FULL_LOG.open("a", encoding="utf-8", errors="replace")
    sys.stdout = _Tee(sys.__stdout__, logfile)
    sys.stderr = _Tee(sys.__stderr__, logfile)
    return logfile
