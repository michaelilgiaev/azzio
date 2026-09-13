/* Azzio window switcher -- pure selection decisions (no GTK/X runtime).
 *
 * The two decisions the alt-tab overlay makes, factored out of the GTK key handler and
 * show_switcher() so they can be unit-tested headless (tests/test_switch_logic.c) -- the two
 * reported bugs lived here:
 *
 *   - az_switch_direction: which way a key event moves the selection. A real Shift+Tab
 *     arrives as GDK_KEY_ISO_Left_Tab (and Shift+Tab-as-Tab carries GDK_SHIFT_MASK); both
 *     must map to backward. Keeping this pure means the "shift+tab doesn't go back" routing
 *     is provable without an X server.
 *
 *   - az_switch_start_index: the fresh-open selection. The strip is FIXED rank order (not
 *     MRU), so a hardcoded start always landed on the same tile regardless of focus. This
 *     anchors the start on the currently-focused window's index instead.
 *
 * These use only GDK_KEY_* / GDK_*_MASK constants (header-only from gdk/gdkkeysyms.h and
 * gdk/gdktypes.h) -- no GTK objects, no X calls -- so the test links against glib alone.
 */
#ifndef AZ_SWITCH_LOGIC_H
#define AZ_SWITCH_LOGIC_H

/* Map a key-press (keyval + modifier state) to a selection step:
 *   +1  forward  (Tab, KP_Tab without shift; Right)
 *   -1  backward (ISO_Left_Tab always; Tab/KP_Tab with GDK_SHIFT_MASK; Left)
 *    0  not a navigation key (caller handles digits/Escape/Return/etc. separately)
 * state is the GdkEventKey.state mask; only the shift bit matters here (Alt/Mod bits are
 * ignored, so a held-Alt Tab still reads as forward). */
int az_switch_direction(unsigned int keyval, unsigned int state);

/* The fresh-open selection index for an n-tile strip.
 *   dir:            +1 if opened forward (Alt+Tab), -1 if backward (Alt+Shift+Tab).
 *   focused_index:  index of the currently-focused window within the strip, or < 0 (or >= n)
 *                   when the focused window is not one of the tiles.
 *
 * When the focused window IS in the strip, the start is anchored on it and stepped once in
 * `dir` (forward -> the next tile, backward -> the previous tile), wrapping into [0,n). This
 * is the fix for "alt+tab doesn't start where it should": a tap moves relative to the current
 * window instead of always jumping to a fixed tile.
 *
 * When it is NOT in the strip, this falls back to the legacy Windows-like default
 * (forward -> index 1 when n>1 else 0; backward -> n-1) so unfocused-root cases are unchanged.
 * Always returns a valid index in [0,n) for n>0, and 0 for n<=0. */
int az_switch_start_index(int n, int dir, int focused_index);

#endif /* AZ_SWITCH_LOGIC_H */
