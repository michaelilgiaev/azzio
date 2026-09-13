/* Azzio window switcher -- the resident daemon (alt-tab overlay). See the package
 * README/design. Structure ported from application_menu/menu.c: build the overlay once at
 * login, warm it off-screen, and show by MOVING on-screen / hide by moving off (never a
 * re-map) so the first Alt+Tab is instant. Control by signal, state in a pidfile.
 *
 * Signal contract (driven by launcher.py):
 *   SIGUSR1 = advance forward + show   (A-Tab)
 *   SIGUSR2 = advance backward + show   (A-S-Tab)
 *   SIGTERM/SIGINT = quit
 *
 * While shown the daemon grabs the seat, so it sees the Alt release itself: releasing Alt
 * commits (raise + activate the selected window); Tab/Shift+Tab move the selection;
 * Escape cancels. A timer re-captures thumbnails so tiles stream live. */
#include <gtk/gtk.h>
#include <gdk/gdkx.h>
#include <gdk/gdkkeysyms.h>
#include <glib/gstdio.h>          /* g_unlink */
#include <X11/Xlib.h>
#include <X11/Xatom.h>
#include <X11/keysym.h>          /* XK_Shift_L / XK_Shift_R for the physical-shift keymap probe */
#include <X11/XKBlib.h>           /* XkbLockGroup -- keep the layout US while the overlay is up */
#include <signal.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>

#include "layout.h"
#include "windows.h"
#include "ordering.h"
#include "theme.h"
#include "switch_logic.h"

/* Pin the mirrored keysym/mask values in switch_logic.c against the REAL GDK headers (included
 * here transitively via gtk). If GDK ever renumbered these, the pure module -- which cannot
 * include the GDK headers without dragging the whole toolchain into its headless test -- would
 * silently disagree; these asserts make that a compile error instead. */
_Static_assert(GDK_KEY_Tab          == 0xff09, "GDK_KEY_Tab drift vs switch_logic.c");
_Static_assert(GDK_KEY_KP_Tab       == 0xff89, "GDK_KEY_KP_Tab drift vs switch_logic.c");
_Static_assert(GDK_KEY_ISO_Left_Tab == 0xfe20, "GDK_KEY_ISO_Left_Tab drift vs switch_logic.c");
_Static_assert(GDK_KEY_Left         == 0xff51, "GDK_KEY_Left drift vs switch_logic.c");
_Static_assert(GDK_KEY_Right        == 0xff53, "GDK_KEY_Right drift vs switch_logic.c");
_Static_assert(GDK_SHIFT_MASK       == (1u<<0), "GDK_SHIFT_MASK drift vs switch_logic.c");

#define OFFSCREEN_MARGIN 4000
/* Two refresh cadences while shown (see the two timers below):
 *   THUMB_MS -- stream fresh thumbnail frames into the EXISTING tiles, in place. Cheap (just
 *               XComposite captures, no widget rebuild), so it runs fast enough that the live
 *               content reads as smooth motion instead of a 5fps slideshow.
 *   LIST_MS  -- re-enumerate the window list (which forks `xprop` per window -- EXPENSIVE), only
 *               to notice a window that opened or closed. That is a rare event, so it runs slowly
 *               and stays off the smooth-streaming hot path. */
#define THUMB_MS          50      /* ~20fps live-thumbnail streaming (in place, no rebuild) */
#define LIST_MS          500      /* window-set (open/close) re-scan cadence (forks xprop) */

typedef struct {
    GtkWidget *win;
    AzStrip   *strip;
    gboolean   shown;
    gboolean   warm;              /* strip has been populated at least once (see warmup/show) */
    gboolean   navigated;         /* user moved the selection (Tab/arrow) since this show opened */
    int        last_dir;          /* dir of the current show, for the post-show re-anchor */
    guint      thumb_id;          /* fast in-place thumbnail timer, 0 when hidden */
    guint      list_id;           /* slow window-list re-scan timer, 0 when hidden */
    guint      idle_reload_id;    /* one-shot post-show reload (kept off the hot path), 0 if none */
    int        saved_group;       /* XKB layout group active before we forced US on show (-1 = none) */
    gboolean   shift_held;        /* event-driven latch: a physical Shift key is currently down */
} AzSwitcher;

static AzSwitcher g_sw;

static void commit_switcher(AzSwitcher *s);   /* fwd: used by the anti-pin Alt-state check */
static unsigned long active_window_xid(AzSwitcher *s);  /* fwd: used by the post-show re-anchor */

/* ---- live-window refresh ------------------------------------------------ */
/* Re-enumerate the managed windows (forks xprop) and hand them to the strip. az_strip_set_windows
 * rebuilds ONLY if the set changed; an unchanged set just streams new frames in place. */
static void reload_windows(AzSwitcher *s) {
    GPtrArray *wins = az_windows_list();
    az_strip_set_windows(s->strip, wins);
    az_windows_free(wins);
}

/* Fast tick: stream fresh thumbnail frames into the existing tiles (smooth, no rebuild). */
static gboolean on_thumb_tick(gpointer user) {
    AzSwitcher *s = user;
    if (!s->shown) { s->thumb_id = 0; return G_SOURCE_REMOVE; }
    az_strip_refresh_thumbnails(s->strip);
    return G_SOURCE_CONTINUE;
}

/* Slow tick: re-scan the window LIST so a window opened/closed while the overlay is up appears/
 * disappears. Kept slow because it forks xprop per window; the fast path never does. */
static gboolean on_list_tick(gpointer user) {
    AzSwitcher *s = user;
    if (!s->shown) { s->list_id = 0; return G_SOURCE_REMOVE; }
    reload_windows(s);
    return G_SOURCE_CONTINUE;
}

/* Re-enumerate the real window set NOW and fix up the selection against it. This is the heavy work
 * (forks xprop + captures every tile's XComposite pixmap, ~150-220ms) that show_switcher deferred
 * so the overlay could paint instantly off the WARM strip; running it replaces that possibly-stale
 * set with the current one. Cancels any pending idle first so it is idempotent whether reached via
 * the idle or forced synchronously from commit_switcher.
 *
 * Selection fix-up has two cases:
 *   - The user has NOT navigated yet (fresh open): re-anchor the focus-relative start against the
 *     new set (a window opened/closed since the strip was warmed -> the start still lands on the
 *     tile next to the focused window).
 *   - The user HAS navigated (pressed Tab/Shift+Tab/arrow): their chosen WINDOW is intentional, so
 *     preserve it by XID -- find where that window sits in the new set and keep it selected. Never
 *     re-anchor over a deliberate navigation (that was the "commit re-anchor discards the user's
 *     Tab" bug). If the chosen window vanished, az_strip_set_windows already clamped the index into
 *     range, so we leave that clamped selection as the graceful fallback. */
static void refresh_and_reanchor(AzSwitcher *s) {
    if (s->idle_reload_id) { g_source_remove(s->idle_reload_id); s->idle_reload_id = 0; }
    unsigned long chosen = s->navigated ? az_strip_selected_xid(s->strip) : 0;
    reload_windows(s);
    s->warm = TRUE;
    int n = az_strip_count(s->strip);
    if (n <= 0) return;
    if (s->navigated) {
        int idx = az_strip_index_of_xid(s->strip, chosen);
        if (idx >= 0) az_strip_select(s->strip, idx);   /* keep the user's window; else clamp stands */
    } else {
        int focused_index = az_strip_index_of_xid(s->strip, active_window_xid(s));
        az_strip_select(s->strip, az_switch_start_index(n, s->last_dir, focused_index));
    }
}

/* One-shot reload scheduled by show_switcher AFTER the overlay is already on-screen, so the paint
 * is not blocked by the heavy enumeration/capture (the "delayed / not snappy" fix). It refreshes
 * the set + re-anchors the selection to the correct tile a frame later.
 *
 * NOTE on ordering: this is a DEFAULT-priority idle, which sits BELOW GDK's event dispatch, so a
 * very fast Alt release CAN be delivered (committing) before this idle runs. That does not cause a
 * stale/wrong or crashing commit, because commit_switcher force-runs refresh_and_reanchor() itself
 * if this idle is still pending -- so a commit always operates on the freshly-loaded set -- and
 * activate_window traps X errors so even a since-closed xid is a safe no-op. Keeping this at
 * default priority (not G_PRIORITY_HIGH) is deliberate: it lets GTK paint the overlay first. */
static gboolean on_idle_reload(gpointer user) {
    AzSwitcher *s = user;
    s->idle_reload_id = 0;               /* clear BEFORE refresh so it does not try to re-cancel us */
    if (!s->shown) return G_SOURCE_REMOVE;
    refresh_and_reanchor(s);
    return G_SOURCE_REMOVE;
}

/* ---- show / hide (by move, like the menu) ------------------------------- */
static void move_window(AzSwitcher *s, int x, int y) {
    gtk_window_move(GTK_WINDOW(s->win), x, y);
}

static void center_on_primary(AzSwitcher *s, int *out_x, int *out_y) {
    GdkDisplay *dpy = gtk_widget_get_display(s->win);
    GdkMonitor *mon = gdk_display_get_primary_monitor(dpy);
    if (!mon) mon = gdk_display_get_monitor(dpy, 0);
    GdkRectangle geo = { 0, 0, gdk_screen_width(), gdk_screen_height() };
    if (mon) gdk_monitor_get_geometry(mon, &geo);
    GtkRequisition req;
    gtk_widget_get_preferred_size(s->win, NULL, &req);
    int w = req.width  > 0 ? req.width  : 400;
    int h = req.height > 0 ? req.height : 200;
    *out_x = geo.x + (geo.width  - w) / 2;
    *out_y = geo.y + (geo.height - h) / 2;
}

/* Is a physical Shift key (either side) currently held DOWN? Reads the raw hardware key bitmap via
 * XQueryKeymap, NOT the event's modifier bits.
 *
 * WHY THIS EXISTS -- the "Alt+Shift+Tab goes forward" bug: Azzio binds Alt+Shift to the
 * grp:alt_shift_toggle language switch. With that binding live, the Shift key (while Alt is held)
 * is bound to the XKB group-switch ACTION, so it stops acting as a plain Shift modifier -- the Tab
 * event that follows arrives WITHOUT GDK_SHIFT_MASK (XKB consumed the Shift for the toggle), and
 * with the group pinned to US the held Shift contributes NEITHER ShiftMask NOR the group bit, so
 * neither the event state nor XQueryPointer's mask can see it. XQueryKeymap sidesteps all of that:
 * it reports the physical up/down state of the Shift keys directly from the hardware bitmap, immune
 * to how XKB has remapped the modifier. The keycodes are resolved from the keysyms (not hardcoded)
 * so this survives a non-default keymap.
 *
 * SCOPE -- this is used ONLY for the ONE show-time decision (seed the latch + pick the opening
 * direction), never per in-overlay keypress. That matters: a single/slow XQueryKeymap read IS
 * reliable, but during a FAST held-Alt+Shift Tab burst the per-event snapshot transiently reads
 * Shift as UP (shiftHW would flicker 1,1,0,0) and racing it per keypress made later Tabs step
 * forward nondeterministically. The in-overlay direction instead uses s->shift_held, an
 * event-driven latch fed by the ordered Shift key press/release events (see on_key_press/release),
 * which cannot miss a physically-held Shift. */
static gboolean shift_physically_down(AzSwitcher *s) {
    GdkDisplay *gdpy = gtk_widget_get_display(s->win);
    Display *dpy = GDK_DISPLAY_XDISPLAY(gdpy);
    char keys[32];
    XQueryKeymap(dpy, keys);
    KeyCode kl = XKeysymToKeycode(dpy, XK_Shift_L);
    KeyCode kr = XKeysymToKeycode(dpy, XK_Shift_R);
    gboolean l = kl && (keys[kl >> 3] & (1 << (kl & 7)));
    gboolean r = kr && (keys[kr >> 3] & (1 << (kr & 7)));
    return (l || r) ? TRUE : FALSE;
}

/* Read the XKB locked layout group (0 = us, 1 = il here), or -1 if XKB is unavailable. */
static int current_group(AzSwitcher *s) {
    GdkDisplay *gdpy = gtk_widget_get_display(s->win);
    Display *dpy = GDK_DISPLAY_XDISPLAY(gdpy);
    XkbStateRec st;
    if (XkbGetState(dpy, XkbUseCoreKbd, &st) != Success) return -1;
    return st.locked_group;
}

/* Force the keyboard layout back to group 0 (US). Cheap, idempotent -- safe to call on every
 * keypress while the overlay is up. */
static void force_us_group(AzSwitcher *s) {
    GdkDisplay *gdpy = gtk_widget_get_display(s->win);
    Display *dpy = GDK_DISPLAY_XDISPLAY(gdpy);
    XkbLockGroup(dpy, XkbUseCoreKbd, 0);
    XSync(dpy, False);
}

/* On show: remember the group the user was in, then pin the layout to US so that Alt+Shift can no
 * longer flip to Hebrew WHILE the switcher is up. This is the fix for "Alt+Shift+Tab goes forward
 * instead of backward": Azzio binds Alt+Shift to the grp:alt_shift_toggle language switch, so on
 * a physical keyboard XKB eats the Shift as the group-toggle chord and OpenBox sees only plain
 * Alt+Tab. Holding the group at US for the lifetime of the overlay stops the toggle, so the daemon
 * (which holds the seat grab) sees a real Shift+Tab -> ISO_Left_Tab -> backward. The user's rule:
 * "when the window switcher is on it shouldn't switch languages". The global Alt+Shift Hebrew
 * toggle is untouched everywhere else -- we only override it for the duration of the overlay and
 * restore the previous group on hide. */
static void lock_us_group(AzSwitcher *s) {
    s->saved_group = current_group(s);   /* -1 if XKB unavailable -> restore becomes a no-op */
    force_us_group(s);
}

/* On hide: restore the layout group the user had before we pinned US, so Hebrew (or whatever they
 * were on) comes right back. If we never saved one (XKB unavailable), do nothing. */
static void restore_group(AzSwitcher *s) {
    if (s->saved_group < 0) return;
    GdkDisplay *gdpy = gtk_widget_get_display(s->win);
    Display *dpy = GDK_DISPLAY_XDISPLAY(gdpy);
    XkbLockGroup(dpy, XkbUseCoreKbd, s->saved_group);
    XSync(dpy, False);
    s->saved_group = -1;
}

static void grab_seat(AzSwitcher *s) {
    GdkWindow *gw = gtk_widget_get_window(s->win);
    if (!gw) return;
    GdkDisplay *dpy = gtk_widget_get_display(s->win);
    GdkSeat *seat = gdk_display_get_default_seat(dpy);
    gdk_seat_grab(seat, gw, GDK_SEAT_CAPABILITY_ALL, TRUE,
                  NULL, NULL, NULL, NULL);
    gtk_window_present(GTK_WINDOW(s->win));
}

static void ungrab_seat(AzSwitcher *s) {
    GdkDisplay *dpy = gtk_widget_get_display(s->win);
    GdkSeat *seat = gdk_display_get_default_seat(dpy);
    gdk_seat_ungrab(seat);
}

/* Is a physical Alt (Mod1) currently held down? Queries the REAL X modifier state instead of
 * trusting that we saw the key-release event.
 *
 * WHY THIS EXISTS -- the "it pinned itself" bug: the switcher is shown by SIGNAL (the A-Tab
 * launcher), and only AFTER show_switcher() grabs the seat does the daemon start receiving key
 * events. Alt was already held when OpenBox fired the A-Tab binding, and OpenBox owned the
 * keyboard grab up to that instant. If the user releases Alt during the brief handoff (before OUR
 * grab is fully in place), that release is delivered to OpenBox / dropped, NOT to us -- so
 * on_key_release() never fires and the overlay stays up ("pinned") until a click or Escape. The
 * gesture contract is "hold Alt to keep it, let Alt go to dismiss", so the moment our grab is
 * live we ask the server directly whether Alt is still down; if it is NOT, the release already
 * happened and we commit immediately. This makes releasing Alt ALWAYS dismiss, with no pin. */
static gboolean alt_is_down(AzSwitcher *s) {
    GdkDisplay *gdpy = gtk_widget_get_display(s->win);
    Display *dpy = GDK_DISPLAY_XDISPLAY(gdpy);
    Window root = DefaultRootWindow(dpy);
    Window r, c; int rx, ry, wx, wy; unsigned int mask = 0;
    if (!XQueryPointer(dpy, root, &r, &c, &rx, &ry, &wx, &wy, &mask))
        return FALSE;               /* query failed -> treat as released (safer: no pin) */
    return (mask & Mod1Mask) != 0;  /* Mod1 == Alt on the standard X modifier map */
}

/* Idle check run once, right after the overlay is shown + grabbed: if Alt is no longer held the
 * release slipped past our grab, so commit now (the anti-pin guard -- see alt_is_down). */
static gboolean on_check_alt_still_held(gpointer user) {
    AzSwitcher *s = user;
    if (s->shown && !alt_is_down(s))
        commit_switcher(s);
    return G_SOURCE_REMOVE;
}

/* Raise + activate a window with a proper _NET_ACTIVE_WINDOW client message so the WM
 * (OpenBox) focuses and unminimizes it -- the same path a taskbar click uses. */
static void activate_window(AzSwitcher *s, unsigned long xid) {
    if (xid == 0) return;
    GdkDisplay *gdpy = gtk_widget_get_display(s->win);
    Display *dpy = GDK_DISPLAY_XDISPLAY(gdpy);
    Window root = DefaultRootWindow(dpy);
    XEvent ev; memset(&ev, 0, sizeof(ev));
    ev.xclient.type = ClientMessage;
    ev.xclient.window = (Window)xid;
    ev.xclient.message_type = XInternAtom(dpy, "_NET_ACTIVE_WINDOW", False);
    ev.xclient.format = 32;
    ev.xclient.data.l[0] = 2;            /* source: pager/direct user action */
    ev.xclient.data.l[1] = CurrentTime;
    /* Trap X errors around the raise: the selected xid may have been destroyed between when the
     * strip last enumerated it and this commit (a window closed while the overlay was up, or the
     * warm strip carried a since-closed window). XRaiseWindow on a dead window raises BadWindow,
     * and GDK's default error handler is FATAL (it calls exit()), so an unguarded raise would
     * crash the whole daemon on that race. Trapping makes a stale-xid commit a harmless no-op --
     * the WM simply keeps the current focus -- exactly as thumbnail.c traps its own capture
     * round-trips for the same "the window can vanish underneath us" reason. */
    gdk_error_trap_push();
    XSendEvent(dpy, root, False,
               SubstructureRedirectMask | SubstructureNotifyMask, &ev);
    XRaiseWindow(dpy, (Window)xid);
    XSync(dpy, False);                   /* surface any BadWindow now, inside the trap */
    gdk_error_trap_pop_ignored();
}

/* The window the WM currently reports as focused (_NET_ACTIVE_WINDOW on the root), or 0 if
 * unset/unavailable. Used to anchor the fresh-open selection on the user's current window so a
 * tap of Alt+Tab moves relative to it (see show_switcher / az_switch_start_index). */
static unsigned long active_window_xid(AzSwitcher *s) {
    GdkDisplay *gdpy = gtk_widget_get_display(s->win);
    Display *dpy = GDK_DISPLAY_XDISPLAY(gdpy);
    Window root = DefaultRootWindow(dpy);
    Atom prop = XInternAtom(dpy, "_NET_ACTIVE_WINDOW", False);
    Atom actual_type; int actual_format;
    unsigned long nitems = 0, bytes_after = 0;
    unsigned char *data = NULL;
    unsigned long xid = 0;
    if (XGetWindowProperty(dpy, root, prop, 0, 1, False, XA_WINDOW,
                           &actual_type, &actual_format, &nitems, &bytes_after,
                           &data) == Success) {
        if (data && nitems >= 1 && actual_format == 32)
            xid = *(unsigned long *)(void *)data;
        if (data) XFree(data);
    }
    return xid;
}

static void hide_switcher(AzSwitcher *s) {
    if (!s->shown) return;
    s->shown = FALSE;
    if (s->thumb_id)       { g_source_remove(s->thumb_id);       s->thumb_id = 0; }
    if (s->list_id)        { g_source_remove(s->list_id);        s->list_id = 0; }
    if (s->idle_reload_id) { g_source_remove(s->idle_reload_id); s->idle_reload_id = 0; }
    restore_group(s);      /* give back the layout group (Hebrew) the user had before we pinned US */
    ungrab_seat(s);
    move_window(s, gdk_screen_width() + OFFSCREEN_MARGIN,
                   gdk_screen_height() + OFFSCREEN_MARGIN);
    gdk_display_flush(gtk_widget_get_display(s->win));
}

/* Commit: activate the selected window, then hide.
 *
 * If the post-show reload is still pending (a FAST Alt release beat the default-priority idle --
 * GDK events outrank it), run it synchronously HERE first, so the committed selection is anchored
 * on the CURRENT window set rather than the possibly-stale warm strip we mapped instantly. Without
 * this, a quick tap could commit a tile that a since-closed window used to occupy -- the wrong
 * window, and (before activate_window trapped X errors) a BadWindow crash on its dead xid. This
 * closes the stale-commit race while keeping the instant paint (the idle stays low-priority so the
 * common, non-fast-release case still paints before reloading). */
static void commit_switcher(AzSwitcher *s) {
    if (s->idle_reload_id) refresh_and_reanchor(s);
    unsigned long xid = az_strip_selected_xid(s->strip);
    hide_switcher(s);
    activate_window(s, xid);
}

/* Show (or, if already shown, just advance). dir: +1 forward, -1 backward.
 *
 * SNAPPINESS: the overlay must appear the instant Alt+Tab is pressed. The heavy work -- listing
 * windows (forks xprop) and capturing every tile's live XComposite pixmap -- costs ~150-220ms, so
 * it is DEFERRED to an idle (on_idle_reload) that runs AFTER the overlay is already mapped. This
 * function does only the cheap part (~4ms): pick the focus-anchored start on the WARM strip, then
 * move on-screen + grab. The strip is kept populated between uses (warmup seeds it; each show's
 * idle-reload refreshes it), so there is always a correct, current-enough set of tiles to show
 * immediately. This is what fixes "delayed / not snappy" -- and with it the quick Alt+Shift+Tab
 * that used to feel dead (it was landing during the pre-map stall) now registers, because the
 * overlay is live before the user can react.
 *
 * On a fresh open the start is anchored on the focused window and stepped one in `dir` (forward ->
 * next tile, backward -> previous), so one tap flips to the adjacent window; az_switch_start_index
 * (unit-tested) does the math and falls back to the legacy Windows-like default when the focused
 * window is not a tile. */
static void show_switcher(AzSwitcher *s, int dir) {
    if (s->shown) {
        /* Already up: another A-Tab/A-S-Tab advances the selection. That is a deliberate move, so
         * mark it navigated (a pending reload must then preserve this window, not re-anchor). */
        s->navigated = TRUE;
        az_strip_select(s->strip, az_strip_selected(s->strip) + dir);
        return;
    }
    s->navigated = FALSE;                /* fresh open: the start is the focus-anchored default */
    s->shift_held = FALSE;               /* fresh open: clear the latch before seeding it below */

    /* First-chord correction for Alt+Shift+Tab. On a physical keyboard the OPENING Alt+Shift+Tab
     * never reaches the daemon as backward: XKB fires the grp:alt_shift_toggle on the chord and
     * eats the Shift, so OpenBox sees a plain Alt+Tab and runs the launcher's --next (dir=+1). But
     * the Shift key is still physically held at the instant we show, so read the hardware directly:
     * if Shift is down on a fresh open, the user meant BACKWARD -- flip dir so a single lone
     * Alt+Shift+Tab opens on the previous window, matching the in-overlay Shift+Tab behaviour.
     *
     * The Shift-DOWN that started the chord happened PRE-GRAB (the daemon was not grabbing the seat
     * yet), so no on_key_press latched it -- seed the latch from this one show-time poll so it is
     * already warm if the user holds Alt+Shift and immediately bursts Tab. A single poll here is
     * safe (single/slow XQueryKeymap reads are reliable; only fast per-keypress polling raced). */
    gboolean shift_at_show = shift_physically_down(s);
    s->shift_held = shift_at_show;
    if (shift_at_show) dir = -1;

    s->last_dir = dir;
    /* Cold start ONLY: if the strip was never populated (daemon just launched and warmup's seed
     * has not happened yet), we have nothing to show, so pay the reload once. Every subsequent
     * open reuses the warm strip and skips this -- the reload happens off the hot path below. */
    if (!s->warm || az_strip_count(s->strip) == 0) {
        reload_windows(s);
        s->warm = TRUE;
    }
    int n = az_strip_count(s->strip);
    if (n <= 0) return;                  /* nothing to switch to */
    int focused_index = az_strip_index_of_xid(s->strip, active_window_xid(s));
    az_strip_select(s->strip, az_switch_start_index(n, dir, focused_index));

    int x, y;
    center_on_primary(s, &x, &y);
    move_window(s, x, y);
    gdk_window_raise(gtk_widget_get_window(s->win));
    s->shown = TRUE;
    grab_seat(s);
    /* Pin the layout to US for the lifetime of the overlay so Alt+Shift can't flip to Hebrew while
     * navigating (restored on hide). This is what makes in-overlay Alt+Shift+Tab go backward. */
    lock_us_group(s);
    /* If Shift was held at show, the opening chord ALREADY flipped the group to Hebrew before we
     * grabbed (see above), so the "saved" group we just captured is that accidental Hebrew, not
     * what the user was really in. Overwrite it with US so hide does NOT strand them in Hebrew after
     * an Alt+Shift+Tab. A DELIBERATE Alt+Shift language switch (no Tab, overlay closed) is unaffected
     * -- it never enters show_switcher. */
    if (shift_at_show) s->saved_group = 0;
    gdk_display_sync(gtk_widget_get_display(s->win));
    /* Two timers: fast in-place thumbnail streaming + a slow window-list re-scan (see the tick
     * functions). Fast one gives smooth live content; slow one catches opened/closed windows. */
    if (!s->thumb_id) s->thumb_id = g_timeout_add(THUMB_MS, on_thumb_tick, s);
    if (!s->list_id)  s->list_id  = g_timeout_add(LIST_MS,  on_list_tick,  s);
    /* Refresh the window set + thumbnails NOW that the overlay is visible (heavy work off the hot
     * path; re-anchors the selection if the set changed). One-shot -- see on_idle_reload. */
    if (!s->idle_reload_id) s->idle_reload_id = g_idle_add(on_idle_reload, s);
    /* Anti-pin guard: now that our grab is live, verify Alt is still physically held. If the
     * release slipped past during the OpenBox->daemon grab handoff, dismiss immediately so the
     * overlay never stays pinned (see alt_is_down / on_check_alt_still_held). Deferred to an idle
     * so the grab + first paint settle before we query. */
    g_idle_add(on_check_alt_still_held, s);
}

/* Map a number-key event to a 1-based tile SLOT, or 0 if the key is not a digit. 1..9 are
 * slots 1..9 and 0 is slot 10 -- so the ten leftmost tiles are directly reachable. Both the
 * number ROW (GDK_KEY_1..0) and the keypad (GDK_KEY_KP_1..0) are accepted. The strip is ordered
 * librewolf, kitty, hypervisor, the file manager, then the rest (ordering.c), so slot 1 == librewolf and
 * slot 2 == kitty exactly as the user expects ("librewolf=1, kitty=2 ... press 1 -> librewolf"):
 * the slot is the on-screen 1-based POSITION, which for the ranked apps equals their rank. */
static int digit_slot(guint keyval) {
    switch (keyval) {
        case GDK_KEY_1: case GDK_KEY_KP_1: return 1;
        case GDK_KEY_2: case GDK_KEY_KP_2: return 2;
        case GDK_KEY_3: case GDK_KEY_KP_3: return 3;
        case GDK_KEY_4: case GDK_KEY_KP_4: return 4;
        case GDK_KEY_5: case GDK_KEY_KP_5: return 5;
        case GDK_KEY_6: case GDK_KEY_KP_6: return 6;
        case GDK_KEY_7: case GDK_KEY_KP_7: return 7;
        case GDK_KEY_8: case GDK_KEY_KP_8: return 8;
        case GDK_KEY_9: case GDK_KEY_KP_9: return 9;
        case GDK_KEY_0: case GDK_KEY_KP_0: return 10;  /* 0 is the tenth slot */
        default:                           return 0;
    }
}

/* ---- key handling while shown ------------------------------------------- */
static gboolean on_key_press(GtkWidget *w, GdkEventKey *ev, gpointer user) {
    (void)w;
    AzSwitcher *s = user;
    if (!s->shown) return FALSE;

    /* Event-driven Shift latch. A physically-held Shift is the signal that a Tab means BACKWARD, but
     * it cannot be read reliably per keypress (see shift_physically_down): under the US pin the held
     * Shift shows up in neither the event state nor XQueryPointer's mask, and XQueryKeymap flickers
     * during a fast burst. The HARDWARE KEYCODE, however, is invariant no matter how XKB mangles the
     * keysym, and key press/release events are delivered in order, so latch on it: Shift down sets
     * the flag, Shift up (in on_key_release) clears it. Resolve the keycodes from the keysyms so a
     * non-default keymap still works. */
    Display *xdpy = GDK_DISPLAY_XDISPLAY(gtk_widget_get_display(s->win));
    KeyCode shift_l = XKeysymToKeycode(xdpy, XK_Shift_L);
    KeyCode shift_r = XKeysymToKeycode(xdpy, XK_Shift_R);
    if (ev->hardware_keycode == shift_l || ev->hardware_keycode == shift_r)
        s->shift_held = TRUE;

    /* The Alt+Shift language-toggle (grp:alt_shift_toggle) fires on the Shift-DOWN while Alt is
     * held and delivers ISO_Next_Group/ISO_Prev_Group here, flipping the layout to Hebrew mid
     * gesture (and eating the Shift so the following Tab loses its shift bit). Swallow it and force
     * the layout back to US so the overlay never switches language and the next Shift+Tab stays a
     * real backward step. (The latch was already set above from this same event's Shift keycode.) */
    if (ev->keyval == GDK_KEY_ISO_Next_Group || ev->keyval == GDK_KEY_ISO_Prev_Group ||
        ev->keyval == GDK_KEY_ISO_First_Group || ev->keyval == GDK_KEY_ISO_Last_Group) {
        force_us_group(s);
        return TRUE;
    }

    /* Number keys 1..9,0 jump straight to that tile (by 1-based on-screen position) and
     * activate it -- press 1 for librewolf, 2 for kitty, etc. Ignored when the slot is past the
     * last tile (fewer windows than the digit), so a stray high number is a harmless no-op
     * rather than a wrong/!last-tile activation. */
    int slot = digit_slot(ev->keyval);
    if (slot > 0) {
        if (slot <= az_strip_count(s->strip)) {
            s->navigated = TRUE;                   /* explicit pick -> preserve it across a reload */
            az_strip_select(s->strip, slot - 1);   /* 1-based slot -> 0-based index */
            commit_switcher(s);
        }
        return TRUE;                                /* swallow digits either way while shown */
    }

    /* Tab / Shift+Tab / ISO_Left_Tab / Left / Right -> move the selection. The keyval+state
     * -> direction decision is the pure, unit-tested az_switch_direction (switch_logic.c), so
     * "shift+tab goes back" is proven headless and cannot silently regress. A REAL Shift+Tab
     * arrives as ISO_Left_Tab; that maps to -1 there. az_strip_select flushes the repaint. */
    int dir = az_switch_direction(ev->keyval, ev->state);

    /* Azzio's Alt+Shift language toggle robs a Tab of its Shift: with grp:alt_shift_toggle bound,
     * XKB consumes the Shift (for the group switch) so the Tab arrives as a BARE Tab (state has no
     * shift bit) and az_switch_direction would return FORWARD -- the reported "Alt+Shift+Tab goes
     * forward" bug. Recover the intent from the event-driven latch: if this is a Tab-family key and
     * a Shift key is currently held (per s->shift_held, fed by the ordered Shift key events), force
     * backward. This survives a fast held-Alt+Shift Tab burst that polling the hardware per keypress
     * could not (see shift_physically_down / the latch in on_key_press). */
    if ((ev->keyval == GDK_KEY_Tab || ev->keyval == GDK_KEY_KP_Tab ||
         ev->keyval == GDK_KEY_ISO_Left_Tab) && s->shift_held) {
        dir = -1;
    }

    if (dir != 0) {
        /* Mark that the user deliberately moved the selection, so a subsequent reload preserves
         * THIS window (by xid) instead of snapping back to the focus-anchored fresh-open start. */
        s->navigated = TRUE;
        az_strip_select(s->strip, az_strip_selected(s->strip) + dir);
        return TRUE;
    }

    switch (ev->keyval) {
        case GDK_KEY_Escape:
            hide_switcher(s);            /* cancel: no focus change */
            return TRUE;
        case GDK_KEY_Return:
        case GDK_KEY_KP_Enter:
            commit_switcher(s);
            return TRUE;
        default:
            return FALSE;
    }
}

/* Releasing Alt commits the selection (the Windows alt-tab gesture). */
static gboolean on_key_release(GtkWidget *w, GdkEventKey *ev, gpointer user) {
    (void)w;
    AzSwitcher *s = user;
    if (!s->shown) return FALSE;

    /* Clear the Shift latch on a physical Shift-up (the counterpart to the set in on_key_press), so
     * that after the user lets Shift go a subsequent Tab in the same overlay steps forward again.
     * Matched by hardware keycode -- invariant under XKB's group remapping. */
    Display *xdpy = GDK_DISPLAY_XDISPLAY(gtk_widget_get_display(s->win));
    if (ev->hardware_keycode == XKeysymToKeycode(xdpy, XK_Shift_L) ||
        ev->hardware_keycode == XKeysymToKeycode(xdpy, XK_Shift_R))
        s->shift_held = FALSE;

    if (ev->keyval == GDK_KEY_Alt_L || ev->keyval == GDK_KEY_Alt_R ||
        ev->keyval == GDK_KEY_Meta_L || ev->keyval == GDK_KEY_Meta_R) {
        commit_switcher(s);
        return TRUE;
    }
    return FALSE;
}

/* ---- window assembly + warmup ------------------------------------------- */
static void build_window(AzSwitcher *s) {
    s->win = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_decorated(GTK_WINDOW(s->win), FALSE);
    gtk_window_set_skip_taskbar_hint(GTK_WINDOW(s->win), TRUE);
    gtk_window_set_skip_pager_hint(GTK_WINDOW(s->win), TRUE);
    gtk_window_set_type_hint(GTK_WINDOW(s->win), GDK_WINDOW_TYPE_HINT_UTILITY);
    gtk_window_set_position(GTK_WINDOW(s->win), GTK_WIN_POS_NONE);
    gtk_window_set_resizable(GTK_WINDOW(s->win), FALSE);
    gtk_widget_set_app_paintable(s->win, TRUE);

    /* RGBA visual so the rounded panel's corners are transparent, not black. */
    GdkScreen *screen = gtk_widget_get_screen(s->win);
    GdkVisual *rgba = gdk_screen_get_rgba_visual(screen);
    if (rgba) gtk_widget_set_visual(s->win, rgba);

    s->strip = az_strip_new(s->win);

    g_signal_connect(s->win, "key-press-event", G_CALLBACK(on_key_press), s);
    g_signal_connect(s->win, "key-release-event", G_CALLBACK(on_key_release), s);

    gtk_widget_realize(s->win);
    GdkWindow *gw = gtk_widget_get_window(s->win);
    gdk_window_set_override_redirect(gw, TRUE);
}

static void warmup(AzSwitcher *s) {
    move_window(s, gdk_screen_width() + OFFSCREEN_MARGIN,
                   gdk_screen_height() + OFFSCREEN_MARGIN);
    /* SEED the strip off-screen at login so the FIRST Alt+Tab is instant: this pays the heavy
     * window-list + thumbnail cost once, now, while nobody is waiting -- instead of on the first
     * show (where it was the ~150-220ms "delayed / not snappy" stall). Every show thereafter
     * reuses this warm strip and refreshes it off the hot path (see show_switcher/on_idle_reload).
     * Guarded implicitly: if no windows exist yet the strip is simply empty and the first show
     * falls back to its cold-load path. */
    reload_windows(s);
    s->warm = TRUE;
    gtk_widget_show_all(s->win);          /* the one real map, off-screen */
    while (gtk_events_pending())
        gtk_main_iteration_do(FALSE);
    gdk_display_flush(gtk_widget_get_display(s->win));
    s->shown = FALSE;
}

/* ---- daemon: pidfile + signal loop -------------------------------------- */
static char *pid_path(void) {
    const char *rt = g_getenv("XDG_RUNTIME_DIR");
    if (!rt || !rt[0]) rt = "/tmp";
    return g_build_filename(rt, "azzio-window-switcher.pid", NULL);
}

static int sig_pipe[2];

static void sig_handler(int signum) {
    char b = (char)signum;
    ssize_t r = write(sig_pipe[1], &b, 1);
    (void)r;
}

static gboolean on_sig_pipe(GIOChannel *src, GIOCondition cond, gpointer user) {
    (void)cond;
    AzSwitcher *s = user;
    char buf[64];
    gsize n = 0;
    g_io_channel_read_chars(src, buf, sizeof(buf), &n, NULL);
    char last = 0;
    for (gsize i = 0; i < n; i++) {
        if (buf[i] == SIGTERM || buf[i] == SIGINT) { gtk_main_quit(); return TRUE; }
        last = buf[i];
    }
    /* Show latency is the whole point of the snappiness work, so it is measurable: when
     * AZZIO_SWITCHER_TIMING is set we print how long show_switcher took (the signal-to-mapped
     * cost) to stderr. The live integration test (tests/integration_window_switcher_live.py) reads
     * this and asserts it stays small -- it was ~200ms when show_switcher did the window
     * enumeration + thumbnail capture inline, and is a few ms now that both are off the hot path.
     * Gated by the env var so normal runs emit nothing. */
    static int timing = -1;
    if (timing < 0) timing = (g_getenv("AZZIO_SWITCHER_TIMING") != NULL) ? 1 : 0;
    gint64 t0 = timing ? g_get_monotonic_time() : 0;
    if (last == SIGUSR1) show_switcher(s, +1);
    else if (last == SIGUSR2) show_switcher(s, -1);
    if (timing && (last == SIGUSR1 || last == SIGUSR2)) {
        fprintf(stderr, "AZZIO_SHOW_MS %.1f\n", (g_get_monotonic_time() - t0) / 1000.0);
        fflush(stderr);
    }
    return TRUE;
}

static gboolean claim_pidfile(const char *path) {
    for (int attempt = 0; attempt < 3; attempt++) {
        int fd = open(path, O_CREAT | O_EXCL | O_WRONLY, 0644);
        if (fd >= 0) {
            char pidbuf[32];
            int len = g_snprintf(pidbuf, sizeof(pidbuf), "%d", (int)getpid());
            ssize_t w = write(fd, pidbuf, len);
            (void)w;
            close(fd);
            return TRUE;
        }
        if (errno != EEXIST) return TRUE;
        char *txt = NULL;
        int other = -1;
        if (g_file_get_contents(path, &txt, NULL, NULL) && txt)
            other = atoi(g_strstrip(txt));
        g_free(txt);
        if (other > 0 && kill(other, 0) == 0) return FALSE;
        g_unlink(path);
    }
    return TRUE;
}

static void remove_pidfile(const char *path) {
    char *txt = NULL;
    if (g_file_get_contents(path, &txt, NULL, NULL) && txt) {
        char *s = g_strstrip(txt);
        char *mine = g_strdup_printf("%d", (int)getpid());
        if (strcmp(s, mine) == 0) g_unlink(path);
        g_free(mine);
    }
    g_free(txt);
}

int main(int argc, char **argv) {
    gtk_init(&argc, &argv);
    az_theme_init();

    char *pidfile = pid_path();
    if (!claim_pidfile(pidfile)) {
        g_free(pidfile);
        return 0;                        /* another daemon already runs */
    }

    AzSwitcher *s = &g_sw;
    s->saved_group = -1;                 /* no group saved until the first show pins US */
    s->shift_held = FALSE;               /* Shift latch starts clear (also reset on each fresh open) */
    build_window(s);
    warmup(s);

    if (pipe(sig_pipe) == 0) {
        GIOChannel *ch = g_io_channel_unix_new(sig_pipe[0]);
        g_io_channel_set_encoding(ch, NULL, NULL);
        g_io_channel_set_buffered(ch, FALSE);
        g_io_add_watch(ch, G_IO_IN, on_sig_pipe, s);
        g_io_channel_unref(ch);
        struct sigaction sa; memset(&sa, 0, sizeof(sa));
        sa.sa_handler = sig_handler;
        sigaction(SIGUSR1, &sa, NULL);
        sigaction(SIGUSR2, &sa, NULL);
        sigaction(SIGTERM, &sa, NULL);
        sigaction(SIGINT, &sa, NULL);
    }

    gtk_main();

    az_strip_free(s->strip);
    remove_pidfile(pidfile);
    g_free(pidfile);
    return 0;
}
