"""progress -- the weighted, pinned-to-bottom build progress bar.

The bar's numbers are what the human watches for the whole multi-minute build,
and its milestone lines are the only durable checkpoints written to compile-steps.log.
Every value here is integer arithmetic over the step weights, so an off-by-one
in the prefix sums, the width budget, or the clip boundary silently prints a
wrong percentage or a wrapped line that unsticks the pinned scroll region --
nothing crashes, the display just lies. These tests pin the pure logic:
the accum prefix sums, the _layout fill/percent math, the _clip boundary,
the milestone/phase string formats (including the exact spacing and the
U+203A separator), sub()'s monotonic clamp, and the non-TTY finalize bar.

The ProgressBar constructor opens paths.STEPS_LOG in append mode, so every
test redirects that Path into tmp_path first; nothing touches a real terminal
(tty=False) and no escape codes are asserted against the live screen.
"""

from __future__ import annotations

import io

import pytest

import progress
from progress import ProgressBar


@pytest.fixture
def steps_log(tmp_path, monkeypatch):
    """Redirect paths.STEPS_LOG (opened in the ctor) into tmp_path and return it."""
    p = tmp_path / "compile-steps.log"
    monkeypatch.setattr(progress.paths, "STEPS_LOG", p)
    return p


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Freeze the live stopwatch to a fixed elapsed value so _layout's width math is
    deterministic. The bar reads _COMPILE_START from the env and computes elapsed as
    time.time() - start_epoch; pin start_epoch to 1000 and time.time() to 1000 so the
    stopwatch always renders "[0s] " (5 visible cols) in these unit tests. Individual
    tests that assert on longer clock strings advance time.time() themselves."""
    monkeypatch.setenv("_COMPILE_START", "1000")
    monkeypatch.setattr(progress.time, "time", lambda: 1000.0)


def make_bar(steps_log, weights, tty=False):
    """Construct a bar with STEPS_LOG already redirected (see the steps_log fixture)."""
    return ProgressBar(weights, tty=tty)


# --- construction / prefix sums -------------------------------------------

def test_init_prefix_sums(steps_log):
    # accum[i] must be the running sum of weights[1..i]; the bar reads accum[i-1]
    # as "weight completed before step i", so a bad prefix sum shifts every %.
    bar = make_bar(steps_log, [0, 2, 3, 5])
    assert bar.total_steps == 3
    assert bar.total_weight == 10
    assert bar.accum == [0, 2, 5, 10]


def test_init_starts_at_zero(steps_log):
    bar = make_bar(steps_log, [0, 2, 3, 5])
    assert bar.current == 0
    assert bar.done_weight == 0
    assert bar.cur_weight == 0
    assert bar.subfrac == 0
    assert bar.label == ""


# --- _layout: the BAR line (line 2) fill/percent width math ----------------
# The pinned display is TWO left-aligned rows: line 1 is the step label
# (_step_line), line 2 is the bar + percent + stopwatch (_layout). _layout no
# longer carries the label -- the whole width after the pct/clock fields is the
# bar itself, left-aligned.

def _strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


def test_layout_is_bar_line_without_label(steps_log):
    # The bar line must NOT contain the step label -- that lives on line 1 now.
    bar = make_bar(steps_log, [0, 10])
    bar.label = "Build packages"
    out = bar._layout(80)
    assert "Build packages" not in out


def test_layout_bar_is_about_half_the_width_at_80cols(steps_log):
    # done_weight=5 of total_weight=10 -> exactly 50%. The bar is about HALF the
    # terminal width (cols // 2), NOT the whole leftover row. At cols=80 that is 40
    # cells; 50% of 40 is 20 filled / 20 empty. The pct/clock fields follow the bar
    # and the rest of the row stays blank.
    bar = make_bar(steps_log, [0, 2, 3, 5])
    bar.done_weight = 5
    bar.cur_weight = 0
    bar.subfrac = 0
    out = bar._layout(80)
    assert out.count("█") == 20   # filled block
    assert out.count("░") == 20   # light shade
    assert out.count("█") + out.count("░") == 40  # bar is ~half of the 80-col width
    assert " 50% " in out
    assert "[0s]" in out          # live stopwatch field present


def test_layout_percent_string_is_three_wide(steps_log):
    # The pct is rendered "%3d" so it is always three columns; at 50% that is
    # a leading space -> "  50% " (two spaces before the digits).
    bar = make_bar(steps_log, [0, 2, 3, 5])
    bar.done_weight = 5
    out = bar._layout(80)
    assert "  50% " in out


def test_layout_pct_clamps_to_100(steps_log):
    # eff can exceed total_weight*1000 (done_weight overshoot); pct is min()'d to 100
    # so the field never widens past three digits and the bar never over-fills.
    bar = make_bar(steps_log, [0, 2, 3, 5])
    bar.done_weight = 20          # 200% of total_weight before clamping
    bar.cur_weight = 0
    bar.subfrac = 0
    out = bar._layout(80)
    assert " 100% " in out
    assert out.count("░") == 0   # bar fully filled, no empty cells


def test_layout_zero_total_weight_no_div_by_zero(steps_log):
    # total_weight==0 must short-circuit pct/filled to 0 rather than ZeroDivisionError.
    bar = make_bar(steps_log, [0])   # total_weight == 0
    out = bar._layout(80)
    assert "   0% " in out


def test_layout_never_exceeds_cols_visible_width(steps_log):
    # The visible (non-escape) width is budgeted from cols up front; assert the
    # printable characters never exceed the terminal width even in a narrow term.
    bar = make_bar(steps_log, [0, 10])
    bar.done_weight = 0
    bar.cur_weight = 10
    bar.subfrac = 500
    out = bar._layout(40)
    assert len(_strip_ansi(out)) <= 40


def test_layout_width_holds_when_fixed_fields_exceed_cols(steps_log, monkeypatch):
    # THE failure the pure-width tests miss: when the pct+clock fields alone are WIDER
    # than the terminal (a very narrow term with a wide "[1h 04m 09s]" clock), the
    # line must STILL not exceed cols -- the clock, then the percent, are dropped
    # rather than overflowing and wrapping (which desyncs the pinned scroll region).
    monkeypatch.setenv("_COMPILE_START", "1000")
    bar = make_bar(steps_log, [0, 10])
    bar.cur_weight = 10
    bar.subfrac = 500
    monkeypatch.setattr(progress.time, "time", lambda: 1000.0 + 3849)  # wide clock
    for cols in (1, 4, 6, 8, 12, 20):
        out = bar._layout(cols)
        assert len(_strip_ansi(out)) <= cols, f"overflow at cols={cols}: {out!r}"


# --- _step_line: the LABEL line (line 1) -----------------------------------

def test_step_line_shows_label_left_aligned(steps_log):
    # Line 1 is just the current step label, left-aligned (no leading pad).
    bar = make_bar(steps_log, [0, 10])
    bar.label = "Build packages"
    out = bar._step_line(80)
    assert _strip_ansi(out).startswith("Build packages")


def test_step_line_empty_when_no_label(steps_log):
    # Before the first step there is no label; line 1 renders empty (no glyphs).
    bar = make_bar(steps_log, [0, 10])
    assert _strip_ansi(bar._step_line(80)) == ""


def test_step_line_clipped_to_width_with_ellipsis(steps_log):
    # A label wider than the terminal is truncated with an ellipsis so line 1
    # never wraps onto a second row (which would desync the 2-row scroll region).
    bar = make_bar(steps_log, [0, 10])
    bar.label = "x" * 200
    out = bar._step_line(40)
    visible = _strip_ansi(out)
    assert len(visible) <= 40
    assert visible.endswith("…")


def test_step_line_has_no_bar_glyphs_or_percent(steps_log):
    # The bar/percent/clock belong to line 2 only; line 1 must carry none of them.
    bar = make_bar(steps_log, [0, 10])
    bar.label = "Mkarchiso"
    out = bar._step_line(80)
    assert "█" not in out and "░" not in out
    assert "%" not in out
    assert "[0s]" not in out


# --- _lines: the two rows together -----------------------------------------

def test_lines_returns_step_line_then_bar_line(steps_log):
    # _lines(cols) returns (line1, line2) = (step label, bar). Both must fit cols.
    bar = make_bar(steps_log, [0, 10])
    bar.label = "Build packages"
    bar.done_weight = 0
    bar.cur_weight = 10
    bar.subfrac = 500
    line1, line2 = bar._lines(80)
    assert _strip_ansi(line1).startswith("Build packages")
    assert ("█" in line2 or "░" in line2) and "%" in line2
    assert len(_strip_ansi(line1)) <= 80
    assert len(_strip_ansi(line2)) <= 80


# --- _clip: off-by-one truncation boundary --------------------------------

def test_clip_boundary(steps_log, monkeypatch):
    # cols=10: a 10-char line is left alone (len>cols is False at equality); an
    # 11-char line becomes 9 chars + the one-char ellipsis, staying within 10.
    monkeypatch.setattr(progress.shutil, "get_terminal_size", lambda fallback=(80, 24): (10, 24))
    bar = make_bar(steps_log, [0, 5], tty=True)
    assert bar._clip("0123456789") == "0123456789"          # len 10, unchanged
    clipped = bar._clip("0123456789X")                        # len 11
    assert clipped == "012345678…"
    assert len(clipped) == 10


def test_clip_non_tty_returns_verbatim(steps_log):
    # Non-TTY output goes to a log/pipe, which must keep the full untruncated text.
    bar = make_bar(steps_log, [0, 5], tty=False)
    long = "x" * 500
    assert bar._clip(long) == long


# --- step(): milestone format + counter -----------------------------------

def test_step_advances_counter_and_weights(steps_log):
    bar = make_bar(steps_log, [0, 2, 3, 5])
    bar.step("Bootstrap")
    assert bar.current == 1
    assert bar.done_weight == 0        # accum[current-1] == accum[0] == 0
    assert bar.cur_weight == 2         # weights[1]
    assert bar.subfrac == 0
    assert bar.label == "Bootstrap"
    assert bar._base_label == "Bootstrap"


def test_step_milestone_written_to_steps_log(steps_log):
    # The milestone uses "%2d" for the step number, so step 1 of 3 renders as
    # "[  1/3 ] Bootstrap" (two spaces: one literal after '[', one from %2d).
    bar = make_bar(steps_log, [0, 2, 3, 5])
    bar.step("Bootstrap")
    # close so the append-mode file is flushed to disk before reading
    bar.cleanup()
    contents = steps_log.read_text(encoding="utf-8")
    assert contents.endswith("[  1/3 ] Bootstrap\n")


def test_step_second_step_uses_prefix_sum(steps_log):
    # After two steps, done_weight is the sum of the first step's weight (accum[1]).
    bar = make_bar(steps_log, [0, 2, 3, 5])
    bar.step("first")
    bar.step("second")
    assert bar.current == 2
    assert bar.done_weight == 2        # accum[1]
    assert bar.cur_weight == 3         # weights[2]


# --- sub(): monotonic clamp -----------------------------------------------

def test_sub_monotonic_and_clamped(steps_log):
    bar = make_bar(steps_log, [0, 10], tty=False)
    bar.sub(300)
    assert bar.subfrac == 300
    bar.sub(200)                       # lower -> ignored (monotonic)
    assert bar.subfrac == 300
    bar.sub(5000)                      # above 1000 -> clamped to 1000
    assert bar.subfrac == 1000
    bar.sub(-10)                       # below 0 -> clamped to 0, not > 1000, ignored
    assert bar.subfrac == 1000


def test_sub_done_snaps_to_full(steps_log):
    bar = make_bar(steps_log, [0, 10], tty=False)
    bar.sub(400)
    bar.sub_done()
    assert bar.subfrac == 1000


# --- phase(): sub-label prefix + separator --------------------------------

def test_phase_prefixes_base_label_with_separator(steps_log):
    # phase() must join the step's base label and the sub-phase with a U+203A
    # (single right-pointing angle quote) and NOT advance the step counter.
    bar = make_bar(steps_log, [0, 10], tty=False)
    bar.step("Pkg cache")
    before = bar.current
    bar.phase("downloading")
    assert bar.label == "Pkg cache › downloading"
    assert bar.current == before       # step counter unchanged


def test_phase_without_base_label_uses_sublabel_only(steps_log):
    # With no base label yet, phase() shows the sublabel bare (no leading separator).
    bar = make_bar(steps_log, [0, 10], tty=False)
    bar.phase("standalone")
    assert bar.label == "standalone"


def test_phase_writes_indented_subcheckpoint(steps_log):
    # The compile-steps.log line for a phase is the indented "    -> <sublabel>" form.
    bar = make_bar(steps_log, [0, 10], tty=False)
    bar.step("Pkg cache")
    bar.phase("downloading")
    bar.cleanup()
    contents = steps_log.read_text(encoding="utf-8")
    assert "    -> downloading\n" in contents


# --- TTY path: two-row scroll region + placement escapes -------------------
# These drive the ACTUAL pinned-display escape sequences (which the pure _layout/
# _step_line tests never exercise): the DECSTBM scroll region must reserve the
# BOTTOM TWO rows, draw() must paint line 1 and line 2 on two DISTINCT rows, and
# the tiny-terminal cases must never emit a row-0 / negative-row escape.

def _tty_bar(steps_log, weights, rows, cols):
    """A ProgressBar wired to a captured 'terminal' at a fixed (cols, rows), forced
    into TTY mode. Returns (bar, term) where term.getvalue() is everything painted."""
    bar = ProgressBar(weights, tty=True)
    term = io.StringIO()
    bar.term = term
    bar._size = lambda: (cols, rows)  # type: ignore[method-assign]
    return bar, term


def test_arm_reserves_bottom_two_rows_scroll_region(steps_log):
    # On a 24-row terminal the scroll region must be rows 1..22 (reserving 23 & 24
    # for the two pinned lines): the DECSTBM escape is exactly "\033[1;22r".
    bar, term = _tty_bar(steps_log, [0, 10], rows=24, cols=80)
    bar._arm()
    assert "\033[1;22r" in term.getvalue()
    # cursor is homed to the bottom of the scroll region (row 22), not the screen bottom.
    assert "\033[22;1H" in term.getvalue()


def test_draw_paints_two_distinct_rows(steps_log):
    # draw() must place line 1 (step label) on row 23 and line 2 (bar) on row 24 --
    # two DIFFERENT rows. A regression that painted both on one row (or swapped them)
    # would fail here. Assert both cursor-move+clear+content escapes are present and
    # that line 1 carries the label while line 2 carries the bar/percent.
    bar, term = _tty_bar(steps_log, [0, 10], rows=24, cols=80)
    bar.step("Build packages")
    bar.sub(500)
    out = term.getvalue()
    # line 1 at row 23: cursor move, clear, then the (bold) label.
    assert "\033[23;1H\033[K" in out
    # line 2 at row 24: cursor move, clear, then the bar/percent.
    assert "\033[24;1H\033[K" in out
    # The label appears after the row-23 move; the bar/percent after the row-24 move.
    seg23 = out.split("\033[23;1H\033[K", 1)[1]
    assert "Build packages" in seg23.split("\033[24;1H", 1)[0]
    seg24 = out.split("\033[24;1H\033[K", 1)[1]
    assert ("█" in seg24 or "░" in seg24) and "%" in seg24


def test_draw_line1_row_is_directly_above_line2_row(steps_log):
    # The two pinned rows must be adjacent (line1 == line2 - 1) so they read as a
    # single two-line block just above the scrolling output.
    bar, term = _tty_bar(steps_log, [0, 10], rows=40, cols=100)
    bar.step("Mkarchiso")
    out = term.getvalue()
    assert "\033[39;1H\033[K" in out   # line 1 on row 39
    assert "\033[40;1H\033[K" in out   # line 2 on row 40 (adjacent, bottom row)


import pytest


@pytest.mark.parametrize("rows", [1, 2, 3])
def test_tiny_terminal_never_emits_row_zero_or_negative(steps_log, rows):
    # On a 1-, 2-, or 3-row terminal the row math must stay valid: NO "\033[0;..H"
    # (row 0) and no negative row may ever be written by arm/draw/finalize/cleanup.
    # A bare rows-1 / rows-2 would emit "\033[0;1H" at rows<=2 and corrupt the screen.
    bar, term = _tty_bar(steps_log, [0, 10], rows=rows, cols=40)
    bar.init()
    bar.step("x")
    bar.sub(500)
    bar.finalize()
    bar.cleanup()
    out = term.getvalue()
    import re
    # Every cursor-position escape "\033[<row>;<col>H" must have row >= 1.
    for m in re.finditer(r"\033\[(\d+);\d+H", out):
        assert int(m.group(1)) >= 1, f"row {m.group(1)} at rows={rows}: {out!r}"
    # And the DECSTBM region top must be >= 1 too.
    for m in re.finditer(r"\033\[1;(\d+)r", out):
        assert int(m.group(1)) >= 1


def test_cleanup_clears_both_pinned_rows_on_tty(steps_log):
    # cleanup() must clear BOTH pinned rows (23 and 24 on a 24-row term) then unpin.
    bar, term = _tty_bar(steps_log, [0, 10], rows=24, cols=80)
    bar.step("Build")
    term.truncate(0); term.seek(0)   # drop the step()'s paint; capture only cleanup
    bar.cleanup()
    out = term.getvalue()
    assert "\033[23;1H\033[K" in out   # clear line 1
    assert "\033[24;1H\033[K" in out   # clear line 2
    assert "\033[r" in out             # unpin the scroll region


def test_finalize_tty_scrolls_two_lines(steps_log):
    # On a TTY, finalize() unpins then scrolls BOTH lines out permanently: a step
    # label line followed by a full bar line (each cleared with \r\033[K, ending \n).
    bar, term = _tty_bar(steps_log, [0, 10], rows=24, cols=80)
    bar.step("Done step")
    term.truncate(0); term.seek(0)
    bar.finalize()
    out = term.getvalue()
    assert "\033[r" in out                       # unpin first
    assert "Done step" in out                    # the step label line
    assert ("█" in out or "░" in out)            # the bar line
    assert out.count("\n") >= 2                   # two lines scrolled out


# --- finalize(): non-TTY ASCII completion bar -----------------------------

def test_finalize_ascii_bar(steps_log, monkeypatch):
    # Non-TTY finalize prints a plain 40-cell '#/.' bar with no escapes so it is
    # safe in compile-full.log. weights=[0,10] + one step -> 100%, all 40 cells filled.
    bar = make_bar(steps_log, [0, 10], tty=False)
    bar.step("Build")
    fake = io.StringIO()
    monkeypatch.setattr("sys.stdout", fake)
    bar.finalize()
    assert fake.getvalue() == "[" + "#" * 40 + "] 100%  Build\n"


def test_finalize_partial_ascii_bar(steps_log, monkeypatch):
    # done_weight=5 of 10 with cur_weight=0 -> finalize forces cur to full slice;
    # eff = 5*1000 + 0*1000 = 5000 -> 50%, 20 of 40 cells filled.
    bar = make_bar(steps_log, [0, 5, 5], tty=False)
    bar.step("first")                  # done=0, cur=5
    bar.step("second")                 # done=5, cur=5
    # roll back cur_weight to 0 so finalize's eff = done*1000 + 0*1000
    bar.done_weight = 5
    bar.cur_weight = 0
    bar.label = "half"
    fake = io.StringIO()
    monkeypatch.setattr("sys.stdout", fake)
    bar.finalize()
    out = fake.getvalue()
    assert out == "[" + "#" * 20 + "." * 20 + "]  50%  half\n"


# --- _log_step / cleanup: durability + swallowed errors --------------------

def test_log_step_appends_and_flushes(steps_log):
    bar = make_bar(steps_log, [0, 10], tty=False)
    bar._log_step("checkpoint one")
    bar._log_step("checkpoint two")
    # flush happens inside _log_step; read without closing to prove real-time write
    contents = steps_log.read_text(encoding="utf-8")
    assert contents == "checkpoint one\ncheckpoint two\n"


def test_log_step_swallows_closed_file(steps_log):
    # Writing after close raises ValueError inside _log_step, which is swallowed
    # (the bar must never crash the build over a broken log handle).
    bar = make_bar(steps_log, [0, 10], tty=False)
    bar.steps_log.close()
    bar._log_step("after close")        # must not raise


def test_cleanup_is_idempotent_on_closed_log(steps_log):
    # cleanup closes steps_log inside a (ValueError, OSError) guard, so a second
    # cleanup (double-close) does not raise.
    bar = make_bar(steps_log, [0, 10], tty=False)
    bar.cleanup()
    bar.cleanup()                       # must not raise


# --- non-TTY guards: no escape codes emitted on draw/init ------------------

def test_non_tty_draw_and_init_noop(steps_log, monkeypatch):
    # On a non-TTY, draw()/init()/_arm() short-circuit so no cursor/scroll escapes
    # leak into a piped log. Assert nothing is written to sys.stdout by them.
    bar = make_bar(steps_log, [0, 10], tty=False)
    fake = io.StringIO()
    monkeypatch.setattr("sys.stdout", fake)
    bar.init()
    bar.draw()
    assert fake.getvalue() == ""


# --- live stopwatch --------------------------------------------------------

def test_format_clock_matches_compile_sh_style():
    # format_clock must render the SAME way compile.sh's _format_duration does, so
    # the live ticking clock and the final "[time] Compile finished in ..." line
    # agree: seconds-only, m+ss, and h+mm+ss with zero-padded minor fields.
    assert progress.format_clock(0) == "0s"
    assert progress.format_clock(9) == "9s"
    assert progress.format_clock(12) == "12s"
    assert progress.format_clock(60) == "1m 00s"
    assert progress.format_clock(452) == "7m 32s"
    assert progress.format_clock(3600) == "1h 00m 00s"
    assert progress.format_clock(3849) == "1h 04m 09s"
    # Negative/garbage clamps to 0s rather than printing a negative duration.
    assert progress.format_clock(-5) == "0s"


def test_start_epoch_read_from_compile_start_env(steps_log, monkeypatch):
    # The bar seeds its stopwatch from _COMPILE_START (exported by compile.sh through
    # the PTY re-exec) so its elapsed time lines up with the shell's final report.
    monkeypatch.setenv("_COMPILE_START", "1234567")
    bar = make_bar(steps_log, [0, 10])
    assert bar.start_epoch == 1234567


def test_start_epoch_falls_back_to_now_when_unset(steps_log, monkeypatch):
    # Running compiler.py directly (no shim) leaves _COMPILE_START unset; the bar must
    # fall back to the current time (frozen to 1000.0 by the frozen_clock fixture),
    # not crash or render a garbage clock.
    monkeypatch.delenv("_COMPILE_START", raising=False)
    bar = make_bar(steps_log, [0, 10])
    assert bar.start_epoch == 1000


def test_layout_shows_live_stopwatch_field(steps_log, monkeypatch):
    # The pinned bar carries the elapsed time as a "[...]" field. With start_epoch at
    # 1000 and time advanced to 1000+452, it must read "[7m 32s]".
    monkeypatch.setenv("_COMPILE_START", "1000")
    bar = make_bar(steps_log, [0, 10])
    monkeypatch.setattr(progress.time, "time", lambda: 1000.0 + 452)
    out = bar._layout(80)
    assert "[7m 32s]" in out


def test_layout_stopwatch_advances_between_draws(steps_log, monkeypatch):
    # The clock ticks: two _layout() calls at different wall-clock times render
    # different elapsed values (this is what makes it "progress as it goes").
    monkeypatch.setenv("_COMPILE_START", "1000")
    bar = make_bar(steps_log, [0, 10])
    monkeypatch.setattr(progress.time, "time", lambda: 1005.0)
    first = bar._layout(80)
    monkeypatch.setattr(progress.time, "time", lambda: 1075.0)
    second = bar._layout(80)
    assert "[5s]" in first
    assert "[1m 15s]" in second
    assert first != second


def test_layout_visible_width_holds_with_wide_clock(steps_log, monkeypatch):
    # A wide stopwatch ("[1h 04m 09s]") must still be budgeted so the bar line's
    # printable width never exceeds cols (otherwise it wraps and unsticks the region).
    monkeypatch.setenv("_COMPILE_START", "1000")
    bar = make_bar(steps_log, [0, 10])
    bar.cur_weight = 10
    bar.subfrac = 500
    monkeypatch.setattr(progress.time, "time", lambda: 1000.0 + 3849)
    out = bar._layout(60)
    assert "[1h 04m 09s]" in out
    assert len(_strip_ansi(out)) <= 60


def test_start_clock_noop_on_non_tty(steps_log):
    # The ticker only exists to repaint the pinned line, which non-TTY never draws;
    # start_clock() must be a no-op there (no thread spawned).
    bar = make_bar(steps_log, [0, 10], tty=False)
    bar.start_clock()
    assert bar._clock_thread is None


def test_stop_clock_is_idempotent(steps_log):
    # teardown()/finalize() may both call stop_clock(); it must be safe to call when
    # no ticker was ever started and to call twice.
    bar = make_bar(steps_log, [0, 10], tty=False)
    bar.stop_clock()
    bar.stop_clock()   # must not raise
