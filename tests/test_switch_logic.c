/* Azzio window switcher -- selection-logic unit tests. Compiled against the SHIPPING
 * switch_logic.c (via tests/Makefile). Pure asserts, no framework; non-zero exit on failure.
 *
 * These are BEHAVIORAL tests for the two decisions the alt-tab overlay makes -- the exact
 * places the two reported bugs lived:
 *
 *   1. keyval+state -> direction  (az_switch_direction): a real Shift+Tab arrives as
 *      ISO_Left_Tab, and Shift+Tab as plain Tab carries GDK_SHIFT_MASK; both must map to -1
 *      (backward). Plain Tab / Right map to +1. This is unit-testable WITHOUT an X server, so
 *      the "shift+tab doesn't go back" routing can never silently regress again (the prior
 *      round shipped source-contract grep tests that passed while the bug shipped).
 *
 *   2. the fresh-open START index (az_switch_start_index): the strip is FIXED rank order, not
 *      MRU, so hardcoding start=1 always landed on the second tile (kitty) regardless of which
 *      window was focused -- "alt+tab gets confused, doesn't start where it should". The fix
 *      anchors the start on the currently-focused window's index: forward -> next tile,
 *      backward -> previous tile (wrapping); it falls back to the legacy default when the
 *      focused window is not in the strip (focused_index < 0). Asserted for N=1,2,3,5.
 */
#include "switch_logic.h"
#include <gdk/gdk.h>          /* real GDK_KEY_* / GDK_*_MASK -- so the test drives switch_logic
                              * with the exact keysyms a live GTK event carries, not literals. */
#include <glib.h>
#include <stdio.h>

static int failures = 0;
#define CHECK(cond, msg) do { \
    if (cond) { g_print("  ok   %s\n", msg); } \
    else      { g_print("  FAIL %s\n", msg); failures++; } \
} while (0)

/* ---- 1. keyval+state -> direction -------------------------------------- */
static void test_direction(void) {
    g_print("direction:\n");
    /* Forward: plain Tab, keypad Tab, Right. */
    CHECK(az_switch_direction(GDK_KEY_Tab, 0) == +1, "Tab (no shift) -> +1");
    CHECK(az_switch_direction(GDK_KEY_KP_Tab, 0) == +1, "KP_Tab (no shift) -> +1");
    CHECK(az_switch_direction(GDK_KEY_Right, 0) == +1, "Right -> +1");
    /* Backward: ISO_Left_Tab (what a REAL Shift+Tab delivers), Tab+shift, Left. */
    CHECK(az_switch_direction(GDK_KEY_ISO_Left_Tab, GDK_SHIFT_MASK) == -1,
          "ISO_Left_Tab (real Shift+Tab) -> -1");
    CHECK(az_switch_direction(GDK_KEY_ISO_Left_Tab, 0) == -1,
          "ISO_Left_Tab with no shift bit -> -1 (it IS shift-tab)");
    CHECK(az_switch_direction(GDK_KEY_Tab, GDK_SHIFT_MASK) == -1,
          "Tab + GDK_SHIFT_MASK -> -1");
    CHECK(az_switch_direction(GDK_KEY_KP_Tab, GDK_SHIFT_MASK) == -1,
          "KP_Tab + shift -> -1");
    CHECK(az_switch_direction(GDK_KEY_Left, 0) == -1, "Left -> -1");
    /* Real-world combined state: Alt (Mod1) held too. Mod1 bit must not change the answer. */
    CHECK(az_switch_direction(GDK_KEY_ISO_Left_Tab, GDK_MOD1_MASK | GDK_SHIFT_MASK) == -1,
          "ISO_Left_Tab + Alt + Shift -> -1 (real held-Alt Shift+Tab)");
    CHECK(az_switch_direction(GDK_KEY_Tab, GDK_MOD1_MASK) == +1,
          "Tab + Alt (no shift) -> +1 (real held-Alt Tab)");
    /* Non-navigation keys: 0 (not handled here). */
    CHECK(az_switch_direction(GDK_KEY_a, 0) == 0, "'a' -> 0 (not a nav key)");
    CHECK(az_switch_direction(GDK_KEY_Escape, 0) == 0, "Escape -> 0");
    CHECK(az_switch_direction(GDK_KEY_Return, 0) == 0, "Return -> 0");
}

/* ---- 2. fresh-open start index ----------------------------------------- */
/* Anchored on the focused window's index: forward -> focused+1, backward -> focused-1,
 * wrapping into [0,n). focused_index < 0 means "focus not in strip" -> legacy default. */
static void test_start_anchored_forward(void) {
    g_print("start (anchored, forward):\n");
    /* n=5, focus at each index, dir=+1 -> should land on focus+1 (wrap). */
    CHECK(az_switch_start_index(5, +1, 0) == 1, "n5 focus0 fwd -> 1");
    CHECK(az_switch_start_index(5, +1, 1) == 2, "n5 focus1 fwd -> 2");
    CHECK(az_switch_start_index(5, +1, 2) == 3, "n5 focus2 fwd -> 3");
    CHECK(az_switch_start_index(5, +1, 4) == 0, "n5 focus4 fwd -> 0 (wrap)");
}
static void test_start_anchored_backward(void) {
    g_print("start (anchored, backward):\n");
    CHECK(az_switch_start_index(5, -1, 0) == 4, "n5 focus0 back -> 4 (wrap)");
    CHECK(az_switch_start_index(5, -1, 1) == 0, "n5 focus1 back -> 0");
    CHECK(az_switch_start_index(5, -1, 4) == 3, "n5 focus4 back -> 3");
}
static void test_start_small_counts(void) {
    g_print("start (small N):\n");
    /* n=1: only index 0 exists, any direction/focus -> 0. */
    CHECK(az_switch_start_index(1, +1, 0) == 0, "n1 fwd -> 0");
    CHECK(az_switch_start_index(1, -1, 0) == 0, "n1 back -> 0");
    CHECK(az_switch_start_index(1, +1, -1) == 0, "n1 fwd no-focus -> 0");
    /* n=2, focus 0: fwd -> 1, back -> 1 (wrap of 0-1). */
    CHECK(az_switch_start_index(2, +1, 0) == 1, "n2 focus0 fwd -> 1");
    CHECK(az_switch_start_index(2, -1, 0) == 1, "n2 focus0 back -> 1 (wrap)");
    CHECK(az_switch_start_index(2, +1, 1) == 0, "n2 focus1 fwd -> 0 (wrap)");
    /* n=3, focus 1: fwd -> 2, back -> 0. */
    CHECK(az_switch_start_index(3, +1, 1) == 2, "n3 focus1 fwd -> 2");
    CHECK(az_switch_start_index(3, -1, 1) == 0, "n3 focus1 back -> 0");
    CHECK(az_switch_start_index(3, -1, 0) == 2, "n3 focus0 back -> 2 (wrap)");
}
static void test_start_no_focus_fallback(void) {
    g_print("start (no focus in strip -> legacy default):\n");
    /* focused_index < 0: fall back to the old Windows-like default so behavior is unchanged
     * when the focused window is not one of the tiles (e.g. focus on the desktop/root). */
    CHECK(az_switch_start_index(5, +1, -1) == 1, "n5 fwd no-focus -> 1 (legacy)");
    CHECK(az_switch_start_index(5, -1, -1) == 4, "n5 back no-focus -> 4 (legacy n-1)");
    CHECK(az_switch_start_index(1, +1, -1) == 0, "n1 fwd no-focus -> 0 (legacy)");
    CHECK(az_switch_start_index(2, +1, -1) == 1, "n2 fwd no-focus -> 1 (legacy)");
    CHECK(az_switch_start_index(2, -1, -1) == 1, "n2 back no-focus -> 1 (legacy n-1)");
}
static void test_start_guards(void) {
    g_print("start (guards):\n");
    CHECK(az_switch_start_index(0, +1, 0) == 0, "n0 -> 0 (no windows)");
    CHECK(az_switch_start_index(0, -1, -1) == 0, "n0 back no-focus -> 0");
    /* An out-of-range focused_index (>= n) is treated as "not in strip" -> legacy default. */
    CHECK(az_switch_start_index(5, +1, 9) == 1, "n5 fwd focus>=n -> 1 (legacy)");
}

int main(void) {
    test_direction();
    test_start_anchored_forward();
    test_start_anchored_backward();
    test_start_small_counts();
    test_start_no_focus_fallback();
    test_start_guards();
    if (failures) { g_printerr("%d failure(s)\n", failures); return 1; }
    g_print("all switch-logic tests passed\n");
    return 0;
}
