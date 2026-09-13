/* Azzio window switcher -- pure selection decisions. See switch_logic.h.
 *
 * No GTK objects and no X calls live here: only the GDK_KEY_* / GDK_*_MASK constants (which
 * are plain #defines from the GDK headers). That is what lets tests/test_switch_logic.c
 * exercise the direction + start-index logic headless, and it is why the two reported bugs
 * (shift+tab backward routing; wrong fresh-open start) are now covered by real assertions
 * instead of grepping the GTK handler's source. */
#include "switch_logic.h"

/* GDK forbids including gdk/gdkkeysyms.h or gdk/gdktypes.h directly (they #error unless
 * <gdk/gdk.h> was included first). We only need the GDK_KEY_* keysym values and GDK_SHIFT_MASK,
 * all plain #defines. Pulling <gdk/gdk.h> would drag the whole GTK/X toolchain into this
 * otherwise-pure translation unit and its headless test, so instead we mirror the exact stable
 * ABI values here. GDK keysyms ARE X11 keysyms (frozen for decades) and GDK_SHIFT_MASK is bit 0;
 * they cannot change without breaking every GTK program. A static assert in switcher.c (which
 * DOES include the real gdkkeysyms.h) pins these against the real headers so drift is caught. */
#ifndef AZ_KEY_Tab
#define AZ_KEY_Tab           0xff09u
#define AZ_KEY_KP_Tab        0xff89u
#define AZ_KEY_ISO_Left_Tab  0xfe20u
#define AZ_KEY_Left          0xff51u
#define AZ_KEY_Right         0xff53u
#define AZ_SHIFT_MASK        (1u << 0)   /* GDK_SHIFT_MASK */
#endif

int az_switch_direction(unsigned int keyval, unsigned int state) {
    switch (keyval) {
        case AZ_KEY_Tab:
        case AZ_KEY_KP_Tab:
            /* Plain Tab is forward; Shift+Tab (shift bit set) is backward. A real Shift+Tab
             * usually arrives as ISO_Left_Tab below, but some paths deliver Tab+shift. */
            return (state & AZ_SHIFT_MASK) ? -1 : +1;
        case AZ_KEY_ISO_Left_Tab:
            /* This keysym IS Shift+Tab on X -- always backward, regardless of the reported
             * shift bit (which is present on a real Shift+Tab and absent on some synthetics). */
            return -1;
        case AZ_KEY_Right:
            return +1;
        case AZ_KEY_Left:
            return -1;
        default:
            return 0;   /* not a navigation key (digits/Escape/Return handled elsewhere) */
    }
}

/* Wrap i into [0,n) with a floored modulo (works for negative i). n must be > 0. */
static int wrap(int i, int n) {
    return ((i % n) + n) % n;
}

int az_switch_start_index(int n, int dir, int focused_index) {
    if (n <= 0) return 0;
    int step = (dir >= 0) ? +1 : -1;
    if (focused_index >= 0 && focused_index < n) {
        /* Anchor on the focused window and step one in the open direction. */
        return wrap(focused_index + step, n);
    }
    /* Focus not in the strip -> legacy Windows-like default. */
    if (step > 0) return (n > 1) ? 1 : 0;
    return n - 1;
}
