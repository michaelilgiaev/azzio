"""Weighted, pinned-to-bottom progress bar.

Ported from the bash bar in the old compile.sh but simplified: because the whole
build now runs in ONE python process, we don't need the CR->LF log-tailing reader
subshell the bash version used to scrape pacman/mkarchiso progress out of a shared
PTY. Instead the long steps (package cache, mkarchiso) call ``bar.sub(permille)``
directly from the same process that parses their live output line-by-line
(see compiler.py), which is both simpler and race-free.

Layout: TWO left-aligned rows pinned to the bottom of the terminal via a DECSTBM
scroll region, so build output scrolls in the rows above them:

    Build packages (calamares, librewolf)          <- line 1: the current step
    ████████████░░░░░░░░░░░░   50% [7m 32s]         <- line 2: bar, %, stopwatch

On a non-TTY (piped to a file / docker logs without -t) it degrades to plain
milestone lines, so no escape codes leak into logs.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time

import paths


def format_clock(secs: int) -> str:
    """Format a whole-second elapsed duration for the pinned bar's live stopwatch.
    Matches compile.sh's _format_duration style so the ticking display and the
    final "[time] Compile finished in ..." line read the same: "1h 04m 09s" /
    "7m 32s" / "12s"."""
    secs = max(int(secs), 0)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


class ProgressBar:
    def __init__(self, weights: list[int], tty: bool | None = None):
        # weights[i] is the weight of step i (1-indexed; index 0 unused).
        self.weights = weights
        self.total_steps = len(weights) - 1
        self.total_weight = sum(weights)
        self.accum = [0] * len(weights)
        acc = 0
        for i in range(1, len(weights)):
            acc += weights[i]
            self.accum[i] = acc
        self.current = 0
        self.label = ""
        self.done_weight = 0
        self.cur_weight = 0
        self.subfrac = 0  # 0..1000 within the current step
        # The bar paints ONLY to the raw terminal (the pristine PTY stdout), never
        # through the stdout tee compiler.py installs -- so its ANSI escapes and █/░
        # glyphs are seen live by the human but never written into compile-full.log.
        self.term = sys.__stdout__
        if tty is None:
            tty = self.term.isatty()
        self.tty = tty
        # compile-steps.log gets each milestone/phase line in real time (compile.sh already
        # truncated it at launch; append + flush so `tail -f` shows checkpoints live).
        self.steps_log = paths.STEPS_LOG.open("a", encoding="utf-8", errors="replace")
        self._armed = False
        self._armed_rows = None  # terminal height the scroll region was last armed to
        self._base_label = ""    # current step's label, prefixed onto phase() sub-labels
        # Live stopwatch: elapsed time shown IN the pinned bar and ticking once a
        # second (start_clock) so the user watches it advance, not just a final
        # total. _COMPILE_START is exported by compile.sh BEFORE the PTY re-exec
        # (whole seconds from the epoch) so the bar's clock and compile.sh's final
        # "[time] Compile finished in ..." line agree; fall back to now if unset
        # (e.g. running compiler.py directly without the shim).
        try:
            self.start_epoch = int(os.environ.get("_COMPILE_START", "") or time.time())
        except (TypeError, ValueError):
            self.start_epoch = int(time.time())
        # draw() writes ANSI to the shared terminal from BOTH the main build thread
        # and the once-a-second clock ticker; serialize them so their escape
        # sequences never interleave and corrupt the pinned line.
        self._draw_lock = threading.Lock()
        self._clock_stop = threading.Event()
        self._clock_thread: "threading.Thread | None" = None

    def _log_step(self, line: str) -> None:
        """Append a milestone/phase line to compile-steps.log in real time."""
        try:
            self.steps_log.write(line + "\n")
            self.steps_log.flush()
        except (ValueError, OSError):
            pass

    def _emit(self, term_line: str, log_line: str, lead: str = "") -> None:
        """Scroll a milestone/phase line: the width-CLIPPED copy to the terminal (so
        a long label does not wrap and break the pinned bar's scroll region) and the
        FULL copy to compile-full.log. sys.stdout is the build's stdout tee, whose write_split
        keeps the two independent -- writing the clipped copy through plain write (an
        earlier bug) truncated these lines in the log too. Fall back to a plain
        clipped write when stdout is not the tee (logging not installed)."""
        writer = getattr(sys.stdout, "write_split", None)
        if writer is not None:
            writer(lead + term_line + "\n", lead + log_line + "\n")
        else:
            sys.stdout.write(lead + term_line + "\n")
            sys.stdout.flush()

    # -- geometry ------------------------------------------------------------
    def _size(self) -> tuple[int, int]:
        try:
            cols, rows = shutil.get_terminal_size((80, 24))
        except Exception:
            cols, rows = 80, 24
        return cols, rows

    def _clip(self, text: str) -> str:
        """Truncate a scrolling line to the terminal width so long labels/output do
        not wrap onto a second row (which desyncs the pinned scroll region and looks
        like text 'escaping' the screen). Non-TTY: no clipping (logs keep full text)."""
        if not self.tty:
            return text
        cols, _ = self._size()
        if cols > 1 and len(text) > cols:
            return text[: cols - 1] + "…"
        return text

    def _step_line(self, cols: int) -> str:
        """Render LINE 1 of the pinned display: the current step label, LEFT-aligned
        and clipped (with an ellipsis) to the terminal width so it never wraps onto a
        second row and desyncs the 2-row scroll region. Bold, no bar/percent/clock --
        those all live on line 2 (_layout). Empty (just a reset) before the first
        step, when there is no label yet."""
        cols = max(cols, 1)
        label = self.label
        if len(label) > cols:
            label = (label[: cols - 1] + "…") if cols >= 2 else label[:cols]
        # Bold label; the color codes are zero-width so the visible width == len(label).
        return f"\033[1m{label}\033[0m" if label else "\033[0m"

    def _layout(self, cols: int) -> str:
        # LINE 2 of the pinned display: [bar] pct% [clock], LEFT-aligned and hard-
        # clamped to `cols`. The step label is NOT here -- it is line 1 (_step_line).
        # The bar takes about HALF the terminal width (not the whole leftover row);
        # the pct + clock fields follow it, and the rest of the row is left blank. Every
        # visible-width term is budgeted from `cols` up front, so the printed line NEVER
        # exceeds the terminal width. Color codes are added last and are zero-width, so
        # they don't affect the budget.
        cols = max(cols, 1)
        eff = self.done_weight * 1000 + self.cur_weight * min(max(self.subfrac, 0), 1000)
        pct = min(eff // 10 // self.total_weight, 100) if self.total_weight else 0
        pctstr = f" {pct:3d}% "                       # e.g. "  24% " -> 6 visible cols
        # Live stopwatch field, e.g. " [7m 32s]". Rendered right after the percent so
        # it ticks in place next to the bar. Zero-width color codes are added when
        # the field is composited into the final line, so its width budget is just
        # the visible text.
        clockstr = f" [{format_clock(time.time() - self.start_epoch)}]"
        # The fixed fields (pct + clock) can, on a VERY narrow terminal, be wider than
        # cols on their own. Reserving them then would leave a negative bar width AND
        # still overflow the row (wrapping desyncs the pinned scroll region). So drop
        # the clock first, then the percent, until the fixed fields fit. This keeps the
        # printed width <= cols at ANY size.
        if len(pctstr) + len(clockstr) <= cols:
            pass
        elif len(pctstr) <= cols:
            clockstr = ""                              # no room for the clock
        else:
            pctstr, clockstr = "", ""                  # no room for either fixed field
        reserved = len(pctstr) + len(clockstr)
        room = max(cols - reserved, 0)
        # The bar is ~HALF the terminal width -- NOT the whole leftover row (which made
        # it span the entire screen). Clamp to what actually fits after the fixed fields.
        barw = min(room, max(0, cols // 2))
        filled = (eff * barw // (1000 * self.total_weight)) if self.total_weight else 0
        filled = min(max(filled, 0), barw)
        bar = "█" * filled + "░" * (barw - filled)
        # bar (cyan) + percent (bold) + stopwatch (dim). The color codes are
        # zero-width, so the visible line width is exactly bar + pct + clock <= cols.
        return (f"\033[36m{bar}\033[0m\033[1m{pctstr}\033[0m"
                f"\033[2m{clockstr}\033[0m")

    def _lines(self, cols: int) -> tuple[str, str]:
        """The two pinned rows as (line 1, line 2) = (step label, bar). draw() paints
        line 1 on the second-to-last terminal row and line 2 on the last row."""
        return self._step_line(cols), self._layout(cols)

    # -- pinning -------------------------------------------------------------
    def _pinned_rows(self, rows: int) -> tuple[int, int, int]:
        """Given the terminal height, return (scroll_bottom, line1_row, line2_row):
        the last scrolling row and the two pinned rows (step label, bar). Clamped so
        the row numbers are always valid 1-based rows even on a 1- or 2-row terminal
        (a bare `rows - 1` / `rows - 2` would otherwise emit `\033[0;1H` or a negative
        row and corrupt the display). The two pinned rows are the bottom two rows;
        the scroll region is everything above them (at least row 1)."""
        line2 = max(rows, 2)          # bar row: never above row 2
        line1 = line2 - 1             # step row: the row just above the bar (>= 1)
        scroll_bottom = max(line1 - 1, 1)  # last scrolling row (>= 1)
        return scroll_bottom, line1, line2

    def _arm(self) -> None:
        if not self.tty:
            return
        _, rows = self._size()
        # Reserve the LAST TWO rows for the pinned two-line display (line 1 = step
        # label, line 2 = bar) and set the DECSTBM scroll region to everything above
        # them, so build output scrolls there. Setting the region homes the cursor (a
        # DECSTBM side effect), which would make the next scrolling write land at the
        # TOP; immediately place the cursor at the bottom of the region so build output
        # appends above the pinned rows. _pinned_rows clamps the math so a tiny (1-2
        # row) terminal never yields row 0 / a negative row.
        top, _line1, _line2 = self._pinned_rows(rows)
        self.term.write(f"\033[1;{top}r\033[{top};1H")
        self.term.flush()
        self._armed = True
        self._armed_rows = rows

    def draw(self) -> None:
        if not self.tty:
            return
        # Serialize with the once-a-second clock ticker so the two threads never
        # interleave their escape sequences on the shared terminal.
        with self._draw_lock:
            cols, rows = self._size()
            # If the terminal was resized since the region was armed, the old scroll region
            # and bar row are stale -- the bar would paint on the wrong row and unstick. The
            # giant steps drive many draw()s over a long span, so a resize mid-step is likely;
            # re-arm to the new height before painting. (\033[u below restores to a saved
            # position that re-arming would clobber, so re-arm BEFORE saving the cursor.)
            if getattr(self, "_armed_rows", None) != rows:
                self._arm()
            step_line, bar_line = self._lines(cols)
            _top, line1, line2 = self._pinned_rows(rows)
            # save cursor, paint line 1 (step) on the second-to-last row and line 2
            # (bar) on the last row -- each cleared to EOL first so a shorter label/bar
            # does not leave stale glyphs from a previous, longer paint -- restore cursor.
            self.term.write(
                f"\033[s"
                f"\033[{line1};1H\033[K{step_line}"
                f"\033[{line2};1H\033[K{bar_line}"
                f"\033[u"
            )
            self.term.flush()

    # -- live stopwatch ticker ----------------------------------------------
    def start_clock(self) -> None:
        """Start a daemon thread that repaints the bar once a second so the elapsed
        stopwatch keeps ticking even during long quiet stretches (big downloads /
        the mkarchiso squash) where no step/sub/phase event fires a draw(). No-op on
        a non-TTY (the clock only lives in the pinned line, which non-TTY never paints)
        or if already running."""
        if not self.tty or self._clock_thread is not None:
            return

        def tick() -> None:
            # wait() returns True when stopped -> exit; False on the 1s timeout -> paint.
            while not self._clock_stop.wait(1.0):
                try:
                    self.draw()
                except Exception:
                    # A transient terminal write error must never take down the build.
                    pass

        self._clock_thread = threading.Thread(target=tick, name="progress-clock", daemon=True)
        self._clock_thread.start()

    def stop_clock(self) -> None:
        """Stop the stopwatch ticker (idempotent). Called from teardown so no thread
        keeps painting after the bar is unpinned."""
        self._clock_stop.set()
        t = self._clock_thread
        if t is not None:
            t.join(timeout=2.0)
            self._clock_thread = None

    def init(self) -> None:
        if self.tty:
            self._arm()
            self.draw()
            self.start_clock()  # begin ticking the elapsed-time stopwatch

    # -- step / sub ----------------------------------------------------------
    def step(self, label: str) -> None:
        self.current += 1
        self.label = label
        self.done_weight = self.accum[self.current - 1]
        self.cur_weight = self.weights[self.current]
        self.subfrac = 0
        self._base_label = label  # phase() prefixes sub-phase labels with this
        self._arm()
        # milestone line: full (unclipped) text to compile-steps.log in real time; a
        # width-clipped copy scrolls on the terminal (and into compile-full.log via the
        # stdout tee) so a long label does not wrap and break the scroll region.
        milestone = f"[ {self.current:2d}/{self.total_steps} ] {label}"
        self._log_step(milestone)
        self._emit(self._clip(milestone), milestone, lead="\n")
        self.draw()

    def sub(self, permille: int) -> None:
        """Set intra-step progress (0..1000) and repaint. Monotonic within a step."""
        permille = min(max(permille, 0), 1000)
        if permille > self.subfrac:
            self.subfrac = permille
            self.draw()

    def phase(self, sublabel: str) -> None:
        """Update the pinned bar's label to a sub-phase of the current step WITHOUT
        advancing the step counter, and drop a scrolling milestone line. Lets the two
        giant steps (package cache, mkarchiso) narrate their internal phases so the
        bar reports fine-grained progress instead of one static label for minutes."""
        text = f"{self._base_label} › {sublabel}" if getattr(self, "_base_label", "") else sublabel
        self.label = text
        line = f"    -> {sublabel}"
        self._log_step(line)   # sub-checkpoint to compile-steps.log, real time
        self._emit(self._clip(line), line)
        self.draw()

    def sub_done(self) -> None:
        """Snap the current step to 100% of its slice."""
        self.subfrac = 1000
        self.draw()

    # -- teardown ------------------------------------------------------------
    def finalize(self) -> None:
        """Print a permanent full bar as a scrolled line (the 'done' state)."""
        self.stop_clock()  # freeze the stopwatch before painting the final line
        self.subfrac = 1000
        if self.tty:
            # The final two-line display is bar glyphs/labels -> terminal only (never
            # the log). Unpin the scroll region, then scroll BOTH rows out permanently:
            # line 1 (the last step's label) above line 2 (the full █/░ bar).
            self.term.write("\033[r")  # unpin
            cols, _ = self._size()
            step_line, bar_line = self._lines(cols)
            self.term.write("\r\033[K" + step_line + "\n")
            self.term.write("\r\033[K" + bar_line + "\n")
            self.term.flush()
        else:
            # Non-tty (piped / docker logs): a plain #/. bar, no escapes -> it is
            # fine (and useful) for this completion line to land in compile-full.log via
            # the stdout tee. Matches the pre-change non-tty behaviour.
            eff = self.done_weight * 1000 + self.cur_weight * 1000
            pct = min(eff // 10 // self.total_weight, 100)
            barw = 40
            filled = min(eff * barw // (1000 * self.total_weight), barw)
            bar = "#" * filled + "." * (barw - filled)
            sys.stdout.write(f"[{bar}] {pct:3d}%  {self.label}\n")
            sys.stdout.flush()

    def cleanup(self) -> None:
        """Restore the terminal on any exit (unpin scroll region, clear BOTH pinned
        rows)."""
        self.stop_clock()  # no ticker thread may paint after teardown
        if self.tty:
            try:
                _, rows = self._size()
                _top, line1, line2 = self._pinned_rows(rows)
                # Clear the two pinned rows (step line + bar line) explicitly, then
                # unpin the scroll region and reset attributes so nothing is left
                # painted at the bottom of the screen.
                self.term.write(
                    f"\033[{line1};1H\033[K"      # clear line 1 (step)
                    f"\033[{line2};1H\033[K"      # clear line 2 (bar)
                    f"\033[r\033[0m"              # unpin + reset
                )
                self.term.flush()
            except Exception:
                pass
        try:
            self.steps_log.close()
        except (ValueError, OSError):
            pass
