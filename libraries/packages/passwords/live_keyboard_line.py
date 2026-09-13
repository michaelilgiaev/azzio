#!/usr/bin/env python3
"""A LIVE, self-refreshing keyboard-layout / Caps-Lock status line for the passphrase prompt.

The `backup` and `unpack` commands print keyboard.keyboard_status_line() ("Keyboard: us
Caps Lock: off") right before every (hidden) getpass so a wrong layout or a stuck Caps Lock
is visible before the passphrase is typed. But a STATIC print is stale the instant the user
toggles Caps Lock or switches layout WHILE the prompt is waiting -- which is exactly when it
matters. This module makes that one line LIVE: it repaints it in place every ~0.3s for as
long as the prompt is blocking, so Caps Lock / layout changes show up immediately.

THE MECHANISM (mined verbatim from the user's prototype data/backup.py -- update_layout_line):
  1. print the status line once (so the row exists on screen),
  2. ask the terminal where the cursor is now (an ANSI Device-Status-Report, "\\033[6n",
     answered on the tty as "\\033[<row>;<col>R") and remember that ROW,
  3. start a daemon thread that, every ~0.3s, SAVES the cursor, jumps to that fixed row,
     CLEARS it (so a shorter line -- e.g. Caps Lock turning off -- leaves no stale tail),
     rewrites the fresh status line, and RESTORES the cursor:
         tty.write("\\033[s\\033[{row};1H\\033[2K{status}\\033[u")
  4. run the caller's prompt callback (the getpass) while that thread refreshes,
  5. stop the thread (threading.Event) and join it, then repaint one final time so the
     line reflects the state at the moment the passphrase was submitted.

DEGRADES CLEANLY. The live redraw needs BOTH a real controlling terminal (to read the cursor
row and to own the escape sequences) AND a readable keyboard state. When there is no tty
(output piped/redirected, no /dev/tty) or the cursor row cannot be read, we fall back to the
plain single ``print`` -- the exact behaviour before this module -- and never emit an escape
sequence into a non-terminal (which would show up as garbage). All terminal I/O is guarded so
a closed/……odd tty can never crash the prompt; the worst case is the static line.

Python standard library only (termios/tty/threading/os/sys/re). No external binary.
"""

from __future__ import annotations

import os
import re
import select
import sys
import threading

# How often the live line repaints while the prompt waits (seconds). 0.3s matches the
# prototype: fast enough that a Caps Lock toggle feels instant, slow enough to be free.
REFRESH_INTERVAL_SECS = 0.3

# How long to wait for the terminal's Device-Status-Report reply before giving up and
# degrading to the STATIC line. A real terminal answers \033[6n in well under a millisecond;
# but some terminals never answer (e.g. a tmux/screen pane that swallows the query, or a pty
# with no responder). Without a bound the reply read would BLOCK FOREVER there, hanging the
# prompt -- so we cap the wait and fall back to a static print when it elapses.
_DSR_REPLY_TIMEOUT_SECS = 0.25


def _isatty(stream) -> bool:
    """True when ``stream`` is a real terminal (guarded -- a stream without isatty, e.g. a
    StringIO, is treated as NOT a tty). We only drive the cursor / escape sequences on a
    genuine tty."""
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _get_cursor_row(out_stream) -> int | None:
    """Return the current 1-based terminal ROW of the cursor, or None if it cannot be read.

    Uses the ANSI Device-Status-Report: write ``\\033[6n`` and read the terminal's reply
    ``\\033[<row>;<col>R`` from stdin in raw mode (so the reply is not line-buffered or
    echoed). Requires a real controlling terminal on BOTH stdin and ``out_stream``; anything
    missing (no tty, no termios, redirected stream) yields None and the caller degrades to a
    static print. Every step is guarded and the original terminal attributes are always
    restored -- a failure here must never leave the tty in raw mode nor crash the prompt.

    This is the get_cursor_row() from data/backup.py, hardened for the off-tty case."""
    # termios/tty are POSIX-only; on a platform without them there is no live redraw.
    try:
        import termios
        import tty as tty_module
    except Exception:
        return None
    if not (_isatty(sys.stdin) and _isatty(out_stream)):
        return None
    try:
        fd = sys.stdin.fileno()
    except Exception:
        return None
    try:
        old_settings = termios.tcgetattr(fd)
    except Exception:
        return None
    row: int | None = None
    try:
        tty_module.setraw(fd)
        out_stream.write("\033[6n")
        out_stream.flush()
        response = ""
        # Read the reply byte-by-byte until the terminating 'R'. CRUCIAL: each read is guarded
        # by select() with a short timeout, so a terminal that never answers the DSR (a
        # tmux/screen pane, a pty with no responder) makes us give up instead of BLOCKING
        # FOREVER on the read. On timeout (or a malformed/short reply) we simply leave row=None
        # and the caller degrades to the static line. A real terminal streams the whole reply
        # in one burst well within the first window.
        while "R" not in response:
            ready, _, _ = select.select([fd], [], [], _DSR_REPLY_TIMEOUT_SECS)
            if not ready:
                break  # terminal will not answer -> give up (row stays None)
            chunk = os.read(fd, 32)
            if not chunk:
                break
            response += chunk.decode("latin-1", "replace")
        match = re.search(r"\[(\d+);(\d+)R", response)
        if match:
            row = int(match.group(1))
    except Exception:
        row = None
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except Exception:
            pass
    return row


def _repaint(tty, row: int, status_line) -> None:
    """Repaint ``status_line()`` in place on the fixed ``row``: save cursor, jump to the row,
    clear it (so a now-shorter line leaves no stale tail), write the fresh status, restore
    cursor. Guarded so a closed tty during shutdown never raises."""
    try:
        tty.write(f"\033[s\033[{row};1H\033[2K{status_line()}\033[u")
        tty.flush()
    except Exception:
        pass


def prompt_with_live_keyboard_line(prompt_callback, status_line, out_stream=None):
    """Run ``prompt_callback()`` while a LIVE keyboard/Caps-Lock line refreshes in place.

    ``prompt_callback`` is a zero-arg callable that does the actual (blocking) input -- e.g.
    ``lambda: getpass("Passphrase: ")`` -- and whose return value is passed straight back.
    ``status_line`` is a zero-arg callable returning the current status text
    (keyboard.keyboard_status_line). ``out_stream`` defaults to sys.stdout.

    On a real terminal: print the status line, read the cursor row, spin a daemon thread that
    repaints that row every REFRESH_INTERVAL_SECS, run the callback, then stop+join the thread
    and repaint once more. Off a tty (or if the cursor row can't be read): just print the
    status line ONCE and run the callback -- the plain, pre-existing behaviour, with no escape
    sequences leaked into a non-terminal. Returns whatever ``prompt_callback`` returns."""
    out_stream = sys.stdout if out_stream is None else out_stream

    # Print the line once so the row exists on screen (this is also the WHOLE output in the
    # static fallback below).
    print(status_line(), file=out_stream)

    row = _get_cursor_row(out_stream)
    if row is None:
        # No usable terminal -> static line only (pre-existing behaviour). Do NOT emit any
        # ANSI: on a pipe/redirect it would be visible garbage.
        return prompt_callback()

    # The status line was printed on the row ABOVE where the cursor now sits, so target
    # row-1 (mirrors the prototype's `get_cursor_row() - 1`). Guard against the top edge.
    keyboard_row = max(1, row - 1)

    # Open the controlling terminal directly for the repaints (like the prototype). If it
    # cannot be opened, fall back to static rather than fighting the stream.
    try:
        tty = open("/dev/tty", "w")
    except OSError:
        return prompt_callback()

    stop = threading.Event()

    def _run():
        while not stop.is_set():
            _repaint(tty, keyboard_row, status_line)
            stop.wait(REFRESH_INTERVAL_SECS)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        return prompt_callback()
    finally:
        stop.set()
        thread.join(timeout=1.0)
        # One last repaint so the line reflects the state at submit time, then release the tty.
        _repaint(tty, keyboard_row, status_line)
        try:
            tty.close()
        except Exception:
            pass
