"""packages.window_switcher -- OUR alt-tab window switcher, baked into the ISO.

OpenBox's built-in NextWindow switcher is a vertical, icon-only list; this package
replaces it with a horizontal, Windows-like overlay of LIVE window thumbnails, ordered
librewolf / kitty / hypervisor / file manager / alphabetical. Like the application menu it is a
COMPILED C / GTK3 resident daemon (built once, kept hidden) driven by a thin Python
launcher that signals it (--next -> SIGUSR1, --prev -> SIGUSR2).

These pin the packaging contract (pure, no X/GTK): the install paths, that emit_plan()
ships the launcher executable, that the launcher text carries the direction flags + the
signal mapping, and that the build-dep list names the XComposite stack the live
thumbnails require. The C-level ordering + resolver behaviour is covered by the headless
C tests (tests/test_ordering.c, tests/test_window_resolve.c).

BEHAVIORAL coverage of the two alt-tab bugs (shift+tab must go BACK; a fresh open must start
relative to the CURRENTLY-FOCUSED window, not always the same fixed tile) lives in
tests/test_switch_logic.c and is EXECUTED from here via `make -C tests` -- see
test_switch_logic_c_behaviour_passes below. That runs the real assertions (Tab->+1,
Shift+Tab/ISO_Left_Tab->-1, focus-anchored start for N=1,2,3,5), unlike the source-contract
checks further down which only prove a call is present in the source. A grep test passed once
while the bug shipped; the compiled test would not.
"""

import shutil
import subprocess

import paths

from packages.window_switcher import window_switcher as ws

TESTS_DIR = paths.REPODIR / "tests"


def _layout_c() -> str:
    """The switcher tile-strip C source (layout.c) as text."""
    return (paths.WINDOW_SWITCHER_DIR / "layout.c").read_text(encoding="utf-8")


def _switch_logic_c() -> str:
    """The pure selection-logic C source (switch_logic.c) as text."""
    return (paths.WINDOW_SWITCHER_DIR / "switch_logic.c").read_text(encoding="utf-8")


def _switcher_c() -> str:
    """The daemon C source (switcher.c) as text."""
    return (paths.WINDOW_SWITCHER_DIR / "switcher.c").read_text(encoding="utf-8")


def test_install_paths_are_under_usr_local():
    assert ws.SWITCHER_DAEMON_BIN_SYSTEM_PATH == (
        "/usr/local/lib/azzio-window-switcher/azzio-window-switcher-daemon"
    )
    assert ws.SWITCHER_LAUNCHER_SYSTEM_PATH == "/usr/local/bin/azzio-window-switcher"


def test_emit_plan_ships_launcher_executable():
    plan = ws.emit_plan()
    dests = {e["dest"]: e for e in plan}
    assert ws.SWITCHER_LAUNCHER_SYSTEM_PATH in dests
    assert dests[ws.SWITCHER_LAUNCHER_SYSTEM_PATH]["mode"] == 0o755


def test_emit_plan_does_not_ship_the_daemon_binary():
    # The binary is produced by build_daemon() (make), not a content builder in the plan.
    dests = [e["dest"] for e in ws.emit_plan()]
    assert ws.SWITCHER_DAEMON_BIN_SYSTEM_PATH not in dests


def test_launcher_text_parses_next_and_prev():
    txt = ws.launcher_py()
    assert "--next" in txt and "--prev" in txt
    assert "SIGUSR1" in txt and "SIGUSR2" in txt


def test_launcher_text_is_executable_shebang():
    txt = ws.launcher_py()
    assert txt.startswith("#!/usr/bin/env python3")


def test_build_deps_include_xcomposite_stack():
    for dep in ("gtk3", "pkgconf", "gcc", "libxcomposite", "libxrender", "libxdamage"):
        assert dep in ws.SWITCHER_BUILD_DEPS


def test_runtime_deps_require_picom():
    assert "picom" in ws.SWITCHER_RUNTIME_DEPS


# --- Live-render flush contract (layout.c) ----------------------------------
# The overlay is an override-redirect window shown by MOVING it on-screen (never re-mapped),
# so a gtk_widget_queue_draw is only QUEUED -- nothing pumps the frame -- and the change does
# not reach the screen until some unrelated event forces one. That is why moving the SELECTION
# updated the widget state correctly yet the highlighted-tile border did not visibly move (the
# reported "pressing Tab again doesn't move to the window on the right" bug), and why the live
# thumbnails streamed only sparsely. The fix pushes the toplevel's pending updates to the X
# server after a selection change AND after a thumbnail refresh. These pin that the flush stays
# wired on both paths so a refactor cannot silently reintroduce the frozen-overlay bug (this
# behaviour is GTK/X-level and cannot be exercised by these headless unit tests, so we pin it at
# the source, the same way the C ordering/resolver behaviour is covered by the headless C tests).
def test_selection_change_flushes_to_screen():
    src = _layout_c()
    sel = src[src.index("void az_strip_select("):]
    sel = sel[: sel.index("\n}") + 2]
    assert "az_strip_flush(s)" in sel, (
        "az_strip_select must flush the border change to the screen, or the selected-tile "
        "highlight will not visibly move on the override-redirect overlay"
    )


def test_thumbnail_refresh_flushes_to_screen():
    src = _layout_c()
    ref = src[src.index("void az_strip_refresh_thumbnails("):]
    ref = ref[: ref.index("\n}") + 2]
    assert "az_strip_flush(s)" in ref, (
        "az_strip_refresh_thumbnails must flush so the live tiles actually stream on the "
        "override-redirect overlay instead of updating only when another event forces a frame"
    )


def test_flush_helper_forces_pending_updates_out():
    # The helper must both process the toplevel's queued updates and flush the display -- a bare
    # gtk_widget_queue_draw does NOT repaint this override-redirect window on its own.
    src = _layout_c()
    assert "static void az_strip_flush(AzStrip *s)" in src
    helper = src[src.index("static void az_strip_flush(AzStrip *s)"):]
    helper = helper[: helper.index("\n}") + 2]
    assert "gdk_window_process_updates" in helper
    assert "gdk_display_flush" in helper


# --- BEHAVIORAL: compile + RUN the pure selection-logic C test --------------
# This is the real coverage for the two bugs: it executes tests/test_switch_logic.c, which
# asserts the actual keyval+state -> direction decision (Tab->+1, Shift+Tab/ISO_Left_Tab->-1)
# and the focus-anchored fresh-open start index for N=1,2,3,5. A pass here means the compiled
# logic genuinely does the right thing -- not merely that some function is called in the source.
def _have_c_toolchain() -> bool:
    if shutil.which("gcc") is None and shutil.which("cc") is None:
        return False
    if shutil.which("make") is None or shutil.which("pkg-config") is None:
        return False
    return subprocess.run(["pkg-config", "--exists", "gtk+-3.0"]).returncode == 0


def _run_c_test(target: str):
    """`make -C tests <target>` then run ./<target>; return the CompletedProcess of the run.
    Builds into the tests dir (its Makefile already keeps the package trees clean)."""
    build = subprocess.run(
        ["make", "-C", str(TESTS_DIR), target],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, f"build of {target} failed:\n{build.stdout}\n{build.stderr}"
    return subprocess.run(
        [str(TESTS_DIR / target)], capture_output=True, text=True,
    )


def test_switch_logic_c_behaviour_passes():
    if not _have_c_toolchain():
        import pytest
        pytest.skip("no gcc/GTK3 toolchain on this host")
    run = _run_c_test("test_switch_logic")
    # The C test prints "all switch-logic tests passed" and exits 0 on success; any failed
    # assertion prints "FAIL ..." and exits non-zero.
    assert run.returncode == 0, f"switch-logic behaviour FAILED:\n{run.stdout}\n{run.stderr}"
    assert "all switch-logic tests passed" in run.stdout


def test_ordering_c_behaviour_passes():
    # Also run the ordering behaviour test through the same path, so `bash tests.sh` exercises
    # the compiled strip order (not just Python packaging).
    if not _have_c_toolchain():
        import pytest
        pytest.skip("no gcc/GTK3 toolchain on this host")
    run = _run_c_test("test_ordering")
    assert run.returncode == 0, f"ordering behaviour FAILED:\n{run.stdout}\n{run.stderr}"
    assert "all ordering tests passed" in run.stdout


# --- WIRING: the daemon must actually USE the tested pure logic --------------
# The behavioral C test only proves switch_logic.c is correct; these prove the GTK daemon
# routes through it (so the tested logic is what actually runs), and that Bug 2's fix -- a
# focus-anchored start -- is in place rather than the old hardcoded index.
def test_key_handler_routes_through_pure_direction():
    src = _switcher_c()
    assert '#include "switch_logic.h"' in src
    # on_key_press must derive the step from az_switch_direction (not an inline shift check),
    # so Tab/Shift+Tab/ISO_Left_Tab all move via the unit-tested decision.
    press = src[src.index("on_key_press("):]
    press = press[: press.index("\n}") + 2]
    assert "az_switch_direction(ev->keyval, ev->state)" in press, (
        "on_key_press must move the selection via az_switch_direction so the shift+tab-goes-"
        "back routing is the same code the headless test proves"
    )


def test_fresh_open_start_is_focus_anchored():
    src = _switcher_c()
    show = src[src.index("static void show_switcher("):]
    show = show[: show.index("\n}") + 2]
    # The fresh-open branch must compute the start from the focused window's tile index via the
    # tested az_switch_start_index -- NOT the old hardcoded `(dir >= 0) ? (n > 1 ? 1 : 0) ...`.
    assert "az_switch_start_index(" in show
    assert "az_strip_index_of_xid(" in show
    assert "active_window_xid(" in show
    assert "(dir >= 0) ? (n > 1 ? 1 : 0) : (n - 1)" not in show, (
        "show_switcher still hardcodes the fixed start index; it must anchor on the focused "
        "window (az_switch_start_index) so Alt+Tab starts where the user expects"
    )


def test_switch_logic_source_has_no_gtk_runtime_dependency():
    # switch_logic.c must stay pure (no <gdk/gdk.h>, no X includes) so its test links glib-only
    # and the logic is provable headless. It mirrors the GDK keysym values locally instead.
    src = _switch_logic_c()
    assert "#include <gdk/gdk.h>" not in src
    assert "#include <X11" not in src
    assert "AZ_KEY_ISO_Left_Tab" in src  # the mirrored keysym it switches on
