/* Azzio application menu (C/GTK3 port) -- window, search, power row, and the
 * resident daemon (built once, kept warm, INSTANT open/close).
 *
 * This single binary is BOTH the menu and the daemon: it builds the window once
 * at login, warms it up (maps override-redirect off-screen and paints once), and
 * then shows by MOVING on-screen and hides by moving off -- never a re-map, so it
 * appears the instant Super is pressed. Control is by signal (same contract as
 * daemon.py): SIGUSR1 toggle, SIGUSR2 show, SIGTERM/INT quit. State is a pidfile
 * under XDG_RUNTIME_DIR.
 *
 * Ports menu.py (window + AppMenu), daemon.py (signal loop + debounce), the
 * power-button/scrollbar look, and standard search editing.
 */
#include <gtk/gtk.h>
#include <gdk/gdkx.h>
#include <glib/gstdio.h>
#include <X11/Xlib.h>
#include <signal.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>

#include "theme.h"
#include "applications.h"
#include "usage.h"
#include "icons.h"
#include "actions.h"
#include "application_list.h"
#include "window_watch.h"
#include "power.h"

/* Echo-swallow safety cap. A Super close-TAP makes xcape inject the Menu echo on
 * the tap's RELEASE, so the echo lands 0..(xcape -t) after the close (the hold
 * duration of the tap). We swallow EXACTLY ONE toggle after such a close (the echo
 * token), but only if it arrives within this cap -- so a lost echo (e.g. the close
 * came from Escape / an outside click, which emit NO echo, so the token is never
 * armed) can never wedge the menu. Must exceed xcape -t (0.5s, see openbox.py) with
 * margin, or a SLOW close-tap's late echo would slip past the cap and re-open.
 *
 * Unlike the old blunt window (which swallowed ANY toggle for 600ms and so ate a
 * human's rapid Super re-presses), the token is (a) armed ONLY by a Super-originated
 * close -- the only close path xcape echoes -- and (b) consumed by the FIRST toggle,
 * so at most ONE press is ever swallowed per close. Spam therefore stays responsive:
 * the echo is eaten, every deliberate re-press after it shows/hides normally. */
#define ECHO_CAP_US (600 * 1000)

typedef enum { FOCUS_APPS, FOCUS_POWER } FocusZone;

/* The power row is {Sleep, Lock, Restart, Shut Down}; TAB into the row lands on
 * "Shut Down" (the rightmost, index 3) so the commonest session action is one
 * TAB + Enter away. Left/Right then walk off it and the position is preserved. */
#define AZ_POWER_SHUTDOWN_INDEX 3

/* The Azzio-OWN icon names for the power row + search box. These are OUR icons
 * (assets/icons/{sleep,lock,restart,shutdown,search}.svg, the brand blue gradient on a
 * transparent background), shipped by packages/application_menu under these exact hicolor
 * names. We use OUR names -- NOT the generic freedesktop ones the menu used before -- so the
 * resolver (icons.c, first-match over the theme chain) returns Azzio's glyphs instead of the
 * installed Adwaita theme's generic (KDE/breeze-ish) ones the user asked to replace. Kept as
 * named constants (single source of truth) so a test can pin them equal to the Python
 * MENU_GLYPH_ICONS list and neither side can drift. */
#define AZ_ICON_SLEEP    "azzio-sleep"
#define AZ_ICON_LOCK     "azzio-lock"
#define AZ_ICON_RESTART  "azzio-restart"
#define AZ_ICON_SHUTDOWN "azzio-shutdown"
#define AZ_ICON_SEARCH   "azzio-search"

typedef struct {
    const char *icon_name;
    const char *label;
    void (*action)(void);
} PowerItem;

typedef struct {
    GtkWidget  *win;
    GtkWidget  *search_entry;
    GtkWidget  *placeholder;    /* GtkLabel overlay */
    GtkWidget  *search_overlay; /* GtkOverlay holding entry + placeholder */

    AzIcons    *icons;
    AzIcons    *small_icons;
    AzUsage    *usage;
    GPtrArray  *all_apps;       /* AzAppEntry*, owned, canonical order */
    AzAppList  *applist;
    gboolean    populated;

    FocusZone   focus_zone;
    GtkWidget  *power_buttons[4];
    int         power_index;

    /* daemon state */
    gboolean    shown;          /* on-screen? (window stays mapped when hidden) */
    gint64      last_hidden_us; /* monotonic us of last hide (echo cap) */
    gboolean    expect_echo;    /* a Super close armed one xcape echo to swallow */
    int         win_x, win_y;   /* centered on-screen position */
    AzWatcher  *watcher;
} AzMenu;

static AzMenu g_menu;   /* the single resident menu */

/* --- forward decls -------------------------------------------------------- */
static void reset_view(AzMenu *m);
static void show_menu(AzMenu *m);
static void hide_menu(AzMenu *m);
static void arm_echo(AzMenu *m);
static void set_focus_zone(AzMenu *m, FocusZone zone);

/* --- widget colour helper ------------------------------------------------- */
static void widget_bg(GtkWidget *w, const char *hex) {
    GdkRGBA c; gdk_rgba_parse(&c, hex);
    gtk_widget_override_background_color(w, GTK_STATE_FLAG_NORMAL, &c);
}
static void widget_fg(GtkWidget *w, const char *hex) {
    GdkRGBA c; gdk_rgba_parse(&c, hex);
    gtk_widget_override_color(w, GTK_STATE_FLAG_NORMAL, &c);
}
static void widget_font(GtkWidget *w, int size) {
    PangoFontDescription *fd = pango_font_description_new();
    pango_font_description_set_family(fd, AZ_FONT_FAMILY);
    pango_font_description_set_size(fd, size * PANGO_SCALE);
    gtk_widget_override_font(w, fd);
    pango_font_description_free(fd);
}

static void widget_add_class(GtkWidget *w, const char *cls) {
    gtk_style_context_add_class(gtk_widget_get_style_context(w), cls);
}

/* --- CSS: make the search box a pixel copy of the Tk Entry ----------------
 * theme.py never restyles Tk's Entry selection, so Tk uses its platform default
 * (selectbackground #c3c3c3 / selectforeground #000000 on this system -- measured);
 * we set the GTK selection to the SAME so highlighting looks identical (item B). The
 * box gets a real 1px border (DIVIDER_COLOR at rest, BORDER_COLOR when the entry has
 * focus -- Tk's highlightbackground/highlightcolor), the entry gets the TEXT_COLOR
 * caret and ipady=6 vertical padding, and the whole thing is driven from ONE provider
 * at screen scope so nothing is styled per-widget (matches theme.py exactly). */
static void install_css(void) {
    char *css = g_strdup_printf(
        /* the SURFACE box around magnifier + entry: 1px border, blue on focus */
        ".az-search-box {"
        "  background-color: %s;"
        "  border: 1px solid %s;"          /* DIVIDER_COLOR at rest */
        "  border-radius: 0;"              /* Tk highlightthickness border is square */
        "}"
        /* GtkBox does not honour :focus-within reliably in GTK3, so the menu toggles a
         * .focused class on the box from the entry's focus in/out (mirrors Tk's
         * highlightcolor swap): blue while the search entry has the caret, grey once
         * TAB moves focus to the power row. */
        ".az-search-box.focused {"
        "  border-color: %s;"              /* BORDER_COLOR (logo cyan) on focus */
        "}"
        /* the entry itself: flat, surface bg, TEXT_COLOR text + caret, ipady=6 */
        ".az-search-entry {"
        "  background-color: %s;"
        "  background-image: none;"
        "  color: %s;"
        "  caret-color: %s;"
        "  border: none;"
        "  box-shadow: none;"
        "  outline: none;"
        "  padding: 7px 2px;"              /* height to match Tk's ipady=6 box (40px) */
        "  min-height: 0;"
        "}"
        ".az-search-entry:focus { outline: none; box-shadow: none; }"
        /* Tk's default Entry selection look (measured on this host). */
        ".az-search-entry selection,"
        ".az-search-entry:focus selection {"
        "  background-color: %s;"
        "  color: %s;"
        "}"
        /* Kill GTK's edge-overshoot GLOW + undershoot shadow at the scroll limits
         * (item E): Tk's list had no bounce/glow. Belt-and-braces with the scrolled
         * window's kinetic-scrolling=FALSE in application_list.c. */
        "overshoot, undershoot { background: none; box-shadow: none; }",
        AZ_SURFACE_COLOR, AZ_DIVIDER_COLOR, AZ_BORDER_COLOR,
        AZ_SURFACE_COLOR, AZ_TEXT_COLOR, AZ_TEXT_COLOR,
        AZ_SEL_BG, AZ_SEL_FG);

    GtkCssProvider *prov = gtk_css_provider_new();
    gtk_css_provider_load_from_data(prov, css, -1, NULL);
    gtk_style_context_add_provider_for_screen(
        gdk_screen_get_default(), GTK_STYLE_PROVIDER(prov),
        GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
    g_object_unref(prov);
    g_free(css);
}

/* --- launch / activate ---------------------------------------------------- */
static void activate_entry(AzAppEntry *entry, gpointer user) {
    AzMenu *m = user;
    az_launch(entry->exec_argv);   /* watcher counts the open when its window maps */
    hide_menu(m);
}

/* --- search --------------------------------------------------------------- */
static void update_placeholder(AzMenu *m) {
    const char *t = gtk_entry_get_text(GTK_ENTRY(m->search_entry));
    gtk_widget_set_visible(m->placeholder, (t == NULL || t[0] == '\0'));
}

static void on_search_changed(GtkEditable *e, gpointer user) {
    (void)e;
    AzMenu *m = user;
    update_placeholder(m);
    if (m->focus_zone != FOCUS_APPS)
        set_focus_zone(m, FOCUS_APPS);
    az_applist_apply_filter(m->applist, gtk_entry_get_text(GTK_ENTRY(m->search_entry)));
}

/* Paint the search box border blue while the entry holds the caret, grey otherwise
 * (Tk's highlightcolor/highlightbackground swap). `box` is passed as user data. */
static gboolean on_entry_focus_in(GtkWidget *w, GdkEvent *e, gpointer box) {
    (void)w; (void)e;
    gtk_style_context_add_class(gtk_widget_get_style_context(box), "focused");
    return FALSE;
}
static gboolean on_entry_focus_out(GtkWidget *w, GdkEvent *e, gpointer box) {
    (void)w; (void)e;
    gtk_style_context_remove_class(gtk_widget_get_style_context(box), "focused");
    return FALSE;
}

/* --- app model ------------------------------------------------------------ */
/* Canonical menu order: usage frequency (usage.c), then -- only in a live session --
 * float the installer to the very top so a not-yet-installed system offers it first.
 * Every place that (re)sorts the app set goes through here so the pin can't be
 * skipped on a refresh/resort. */
static void order_apps(AzUsage *usage, GPtrArray *apps) {
    az_usage_sort_apps(usage, apps);
    if (az_is_live_session())
        az_apps_pin_first(apps, az_installer_desktop_id());
}

static void populate(AzMenu *m) {
    if (m->populated)
        return;
    az_applist_set_entries(m->applist, m->all_apps);
    m->populated = TRUE;
    az_applist_apply_filter(m->applist, gtk_entry_get_text(GTK_ENTRY(m->search_entry)));
}

/* Re-scan installed .desktop files; returns TRUE if the set changed. */
static gboolean refresh_apps(AzMenu *m) {
    GPtrArray *scanned = az_scan_applications();
    if (scanned->len == 0 && m->all_apps->len > 0) {
        g_ptr_array_free(scanned, TRUE);
        return FALSE;                        /* transient empty scan -> keep */
    }
    order_apps(m->usage, scanned);
    /* Compare desktop_id sequences. */
    gboolean same = (scanned->len == m->all_apps->len);
    for (guint i = 0; same && i < scanned->len; i++) {
        AzAppEntry *a = g_ptr_array_index(scanned, i);
        AzAppEntry *b = g_ptr_array_index(m->all_apps, i);
        if (strcmp(a->desktop_id, b->desktop_id) != 0) same = FALSE;
    }
    if (same) { g_ptr_array_free(scanned, TRUE); return FALSE; }
    g_ptr_array_free(m->all_apps, TRUE);
    m->all_apps = scanned;
    return TRUE;
}

static void resort(AzMenu *m) {
    order_apps(m->usage, m->all_apps);
    if (!m->populated)
        return;
    if (az_applist_set_entries(m->applist, m->all_apps))
        az_applist_apply_filter(m->applist,
                                gtk_entry_get_text(GTK_ENTRY(m->search_entry)));
}

static void reset_view(AzMenu *m) {
    populate(m);
    refresh_apps(m);
    set_focus_zone(m, FOCUS_APPS);
    resort(m);
    const char *q = gtk_entry_get_text(GTK_ENTRY(m->search_entry));
    if (q && q[0]) {
        gtk_entry_set_text(GTK_ENTRY(m->search_entry), "");  /* triggers re-filter */
    } else {
        az_applist_apply_filter(m->applist, "");
    }
    update_placeholder(m);
}

/* --- power row focus ------------------------------------------------------ */
static void power_button_set_focused(GtkWidget *btn, gboolean focused) {
    /* Store state on the widget; repaint via the "draw" handler that reads it. */
    g_object_set_data(G_OBJECT(btn), "focused", GINT_TO_POINTER(focused));
    gtk_widget_queue_draw(btn);
}

static void apply_power_focus(AzMenu *m) {
    for (int i = 0; i < 4; i++)
        power_button_set_focused(m->power_buttons[i], i == m->power_index);
}
static void clear_power_focus(AzMenu *m) {
    for (int i = 0; i < 4; i++)
        power_button_set_focused(m->power_buttons[i], FALSE);
}

static void set_focus_zone(AzMenu *m, FocusZone zone) {
    m->focus_zone = zone;
    if (zone == FOCUS_APPS) {
        az_applist_set_selection_enabled(m->applist, TRUE);
        clear_power_focus(m);
        gtk_widget_grab_focus(m->search_entry);
    } else {
        az_applist_set_selection_enabled(m->applist, FALSE);
        m->power_index = CLAMP(m->power_index, 0, 3);
        apply_power_focus(m);
    }
}

static void move_power_focus(AzMenu *m, int delta) {
    /* An explicit arrow press takes the highlight back from any mouse-hover after-image. */
    az_power_row_clear_sticky(m->power_buttons[0]);
    m->power_index = CLAMP(m->power_index + delta, 0, 3);
    apply_power_focus(m);
}

/* Hide the menu before a power action fires (widgets.py PowerButton._do wrapper).
 * Passed to az_power_button_new as the "before" callback. */
static void power_before_hide(gpointer user) {
    hide_menu((AzMenu *)user);
}

static void activate_power(AzMenu *m) {
    /* Fire the focused button: hide, then act (matches widgets.py _do wrapper).
     * The action pointer is stashed on the widget in build_window. */
    GtkWidget *btn = m->power_buttons[m->power_index];
    void (*action)(void) = g_object_get_data(G_OBJECT(btn), "action");
    hide_menu(m);
    if (action) action();
}

/* --- keyboard routing ----------------------------------------------------- */
static gboolean on_key_press(GtkWidget *w, GdkEventKey *e, gpointer user) {
    (void)w;
    AzMenu *m = user;
    guint kv = e->keyval;

    if (kv == GDK_KEY_Escape) { hide_menu(m); return TRUE; }
    if (kv == GDK_KEY_Super_L || kv == GDK_KEY_Super_R ||
        kv == GDK_KEY_Meta_L || kv == GDK_KEY_Meta_R) {
        /* Physical Super close (the grab delivered the press). xcape will inject the
         * Menu echo on the release, arriving here as a toggle a moment later -> arm the
         * one-shot swallow so that echo alone is eaten, not the user's re-presses. */
        arm_echo(m);
        hide_menu(m); return TRUE;
    }
    if (kv == GDK_KEY_Tab || kv == GDK_KEY_ISO_Left_Tab) {
        /* An explicit TAB takes the highlight back from any mouse-hover after-image. */
        az_power_row_clear_sticky(m->power_buttons[0]);
        if (m->focus_zone == FOCUS_APPS) {
            /* TAB into the power row lands on "Shut Down" (index 3), not wherever the
             * previous visit left the cursor. */
            m->power_index = AZ_POWER_SHUTDOWN_INDEX;
            set_focus_zone(m, FOCUS_POWER);
        } else {
            set_focus_zone(m, FOCUS_APPS);
        }
        return TRUE;
    }
    if (kv == GDK_KEY_Return || kv == GDK_KEY_KP_Enter) {
        if (m->focus_zone == FOCUS_POWER) activate_power(m);
        else az_applist_activate_selected(m->applist);
        return TRUE;
    }
    if (kv == GDK_KEY_Up) {
        if (m->focus_zone == FOCUS_APPS) az_applist_move_selection(m->applist, -1);
        return TRUE;
    }
    if (kv == GDK_KEY_Down) {
        if (m->focus_zone == FOCUS_APPS) az_applist_move_selection(m->applist, 1);
        return TRUE;
    }
    if (kv == GDK_KEY_Left) {
        if (m->focus_zone == FOCUS_POWER) move_power_focus(m, -1);
        return TRUE;
    }
    if (kv == GDK_KEY_Right) {
        if (m->focus_zone == FOCUS_POWER) move_power_focus(m, 1);
        return TRUE;
    }
    return FALSE;   /* let the entry handle typing / editing keys */
}

/* Close on click outside the window (override-redirect grab delivers it). */
static gboolean on_button_press(GtkWidget *w, GdkEventButton *e, gpointer user) {
    AzMenu *m = user;
    GtkAllocation a; gtk_widget_get_allocation(w, &a);
    /* e->x_root/y_root are screen coords; window is at win_x/win_y. */
    gboolean inside = (e->x_root >= m->win_x && e->x_root < m->win_x + a.width &&
                       e->y_root >= m->win_y && e->y_root < m->win_y + a.height);
    if (!inside) { hide_menu(m); return TRUE; }
    return FALSE;
}

/* --- show / hide / warmup (the instant path) ------------------------------ */
static void move_window(AzMenu *m, int x, int y) {
    gtk_window_move(GTK_WINDOW(m->win), x, y);
}

static void grab_all(AzMenu *m) {
    GdkWindow *gw = gtk_widget_get_window(m->win);
    if (!gw) return;
    GdkDisplay *dpy = gtk_widget_get_display(m->win);
    GdkSeat *seat = gdk_display_get_default_seat(dpy);
    gdk_seat_grab(seat, gw, GDK_SEAT_CAPABILITY_ALL, TRUE,
                  NULL, NULL, NULL, NULL);
    gtk_window_present(GTK_WINDOW(m->win));
    gtk_widget_grab_focus(m->search_entry);
}

static void ungrab_all(AzMenu *m) {
    GdkDisplay *dpy = gtk_widget_get_display(m->win);
    GdkSeat *seat = gdk_display_get_default_seat(dpy);
    gdk_seat_ungrab(seat);
}

/* Signal-receipt timestamp (us), stamped in on_sig_pipe when AZ_TIMING is set,
 * so show/hide can print an honest signal->work-done latency with no ffmpeg. */
static gint64 g_sig_recv_us = 0;
static gboolean g_timing = FALSE;

static void show_menu(AzMenu *m) {
    m->shown = TRUE;
    m->expect_echo = FALSE;    /* fresh open: no echo pending until the next close */
    reset_view(m);
    move_window(m, m->win_x, m->win_y);
    gdk_window_raise(gtk_widget_get_window(m->win));
    set_focus_zone(m, FOCUS_APPS);
    grab_all(m);
    gdk_display_sync(gtk_widget_get_display(m->win));  /* block until X applied */
    if (g_timing && g_sig_recv_us)
        g_printerr("[timing] show: signal->onscreen(X-synced) = %.2f ms\n",
                   (g_get_monotonic_time() - g_sig_recv_us) / 1000.0);
    if (m->watcher) az_watcher_refresh_index(m->watcher);
}

/* Arm the one-shot echo swallow: call ONLY from a Super-originated close (the sole
 * path xcape echoes). Escape / outside-click / launch closes must NOT arm it, or the
 * next real press within the cap would be eaten. */
static void arm_echo(AzMenu *m) {
    m->expect_echo = TRUE;
}

static void hide_menu(AzMenu *m) {
    m->last_hidden_us = g_get_monotonic_time();
    m->shown = FALSE;
    /* Clear any power-button hover: the off-screen hide never delivers the
     * leave-notify, so without this a button the pointer sat on would re-open lit. */
    for (int i = 0; i < 4; i++)
        az_power_button_clear_hover(m->power_buttons[i]);
    ungrab_all(m);
    int ox = gdk_screen_width() + AZ_OFFSCREEN_MARGIN;
    int oy = gdk_screen_height() + AZ_OFFSCREEN_MARGIN;
    move_window(m, ox, oy);
    gdk_display_flush(gtk_widget_get_display(m->win));
}

static void warmup(AzMenu *m) {
    populate(m);
    int ox = gdk_screen_width() + AZ_OFFSCREEN_MARGIN;
    int oy = gdk_screen_height() + AZ_OFFSCREEN_MARGIN;
    move_window(m, ox, oy);
    gtk_widget_show_all(m->win);          /* the one real map, off-screen */
    update_placeholder(m);
    /* Force a full paint now so the first user open is instant. */
    while (gtk_events_pending())
        gtk_main_iteration_do(FALSE);
    gdk_display_flush(gtk_widget_get_display(m->win));
    m->shown = FALSE;
    m->last_hidden_us = 0;
}

/* --- window assembly ------------------------------------------------------ */
static void build_window(AzMenu *m) {
    install_css();
    /* Tk never selects-all on focus; GTK does by default, which reads as the "weird
     * mobile select" the moment the entry is focused/clicked. Turn it off (item A). */
    g_object_set(gtk_settings_get_default(),
                 "gtk-entry-select-on-focus", FALSE, NULL);

    m->win = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_decorated(GTK_WINDOW(m->win), FALSE);
    gtk_window_set_resizable(GTK_WINDOW(m->win), FALSE);
    gtk_window_set_default_size(GTK_WINDOW(m->win),
                                AZ_DEFAULT_WIDTH, AZ_DEFAULT_HEIGHT);
    gtk_widget_set_size_request(m->win, AZ_DEFAULT_WIDTH, AZ_DEFAULT_HEIGHT);
    widget_bg(m->win, AZ_BG_COLOR);

    int sw = gdk_screen_width(), sh = gdk_screen_height();
    m->win_x = MAX(0, (sw - AZ_DEFAULT_WIDTH) / 2);
    m->win_y = MAX(0, (sh - AZ_DEFAULT_HEIGHT) / 2);

    GtkWidget *root = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    widget_bg(root, AZ_BG_COLOR);
    gtk_container_add(GTK_CONTAINER(m->win), root);

    /* --- search row (full width) --- */
    GtkWidget *search_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_margin_start(search_row, 12);
    gtk_widget_set_margin_end(search_row, 12);
    gtk_widget_set_margin_top(search_row, 12);
    gtk_widget_set_margin_bottom(search_row, 8);
    widget_bg(search_row, AZ_BG_COLOR);
    gtk_box_pack_start(GTK_BOX(root), search_row, FALSE, FALSE, 0);

    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    widget_add_class(box, "az-search-box");   /* CSS: SURFACE bg + 1px border (blue on focus) */
    gtk_box_pack_start(GTK_BOX(search_row), box, TRUE, TRUE, 0);

    GtkWidget *mag = gtk_image_new_from_pixbuf(
        az_icons_load(m->small_icons, AZ_ICON_SEARCH));
    gtk_widget_set_margin_start(mag, 8);
    gtk_widget_set_margin_end(mag, 4);
    gtk_box_pack_start(GTK_BOX(box), mag, FALSE, FALSE, 0);

    m->search_overlay = gtk_overlay_new();
    gtk_box_pack_start(GTK_BOX(box), m->search_overlay, TRUE, TRUE, 0);

    m->search_entry = gtk_entry_new();
    gtk_entry_set_has_frame(GTK_ENTRY(m->search_entry), FALSE);
    widget_add_class(m->search_entry, "az-search-entry"); /* CSS: bg/fg/caret/ipady=6 */
    widget_font(m->search_entry, AZ_FONT_SEARCH);          /* exact point size (DPI-safe) */
    gtk_container_add(GTK_CONTAINER(m->search_overlay), m->search_entry);

    m->placeholder = gtk_label_new("Search...");
    gtk_widget_set_halign(m->placeholder, GTK_ALIGN_START);
    gtk_widget_set_valign(m->placeholder, GTK_ALIGN_CENTER);
    gtk_widget_set_margin_start(m->placeholder, 6);
    widget_fg(m->placeholder, AZ_PLACEHOLDER_COLOR);
    widget_font(m->placeholder, AZ_FONT_SEARCH);
    gtk_widget_set_can_focus(m->placeholder, FALSE);
    gtk_overlay_add_overlay(GTK_OVERLAY(m->search_overlay), m->placeholder);
    gtk_overlay_set_overlay_pass_through(GTK_OVERLAY(m->search_overlay),
                                         m->placeholder, TRUE);

    g_signal_connect(m->search_entry, "changed",
                     G_CALLBACK(on_search_changed), m);
    /* Blue border while focused (the entry is focused whenever the menu is shown, so
     * it opens blue like Tk; TAB to the power row greys it). */
    g_signal_connect(m->search_entry, "focus-in-event",
                     G_CALLBACK(on_entry_focus_in), box);
    g_signal_connect(m->search_entry, "focus-out-event",
                     G_CALLBACK(on_entry_focus_out), box);

    /* --- divider --- */
    GtkWidget *div1 = gtk_drawing_area_new();
    gtk_widget_set_size_request(div1, -1, 1);
    widget_bg(div1, AZ_DIVIDER_COLOR);
    gtk_box_pack_start(GTK_BOX(root), div1, FALSE, FALSE, 0);

    /* --- app list --- */
    m->applist = az_applist_new(m->icons, activate_entry, m);
    gtk_box_pack_start(GTK_BOX(root), az_applist_widget(m->applist),
                       TRUE, TRUE, 0);

    /* --- divider --- */
    GtkWidget *div2 = gtk_drawing_area_new();
    gtk_widget_set_size_request(div2, -1, 1);
    widget_bg(div2, AZ_DIVIDER_COLOR);
    gtk_box_pack_start(GTK_BOX(root), div2, FALSE, FALSE, 0);

    /* --- power row (4 equal columns) --- */
    GtkWidget *power_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_box_set_homogeneous(GTK_BOX(power_row), TRUE);
    gtk_widget_set_margin_top(power_row, 4);
    gtk_widget_set_margin_bottom(power_row, 6);
    widget_bg(power_row, AZ_BG_COLOR);
    gtk_box_pack_start(GTK_BOX(root), power_row, FALSE, FALSE, 0);

    static const PowerItem items[4] = {
        { AZ_ICON_SLEEP,    "Sleep",     az_suspend },
        { AZ_ICON_LOCK,     "Lock",      az_lock_session },
        { AZ_ICON_RESTART,  "Restart",   az_reboot },
        { AZ_ICON_SHUTDOWN, "Shut Down", az_poweroff },
    };
    for (int i = 0; i < 4; i++) {
        GtkWidget *btn = az_power_button_new(m->small_icons, items[i].icon_name,
                                             items[i].label, items[i].action,
                                             power_before_hide, m);
        gtk_box_pack_start(GTK_BOX(power_row), btn, TRUE, TRUE, 0);
        m->power_buttons[i] = btn;
    }
    /* Each power button centres its own icon+label block in its equal-width cell
     * (see power_draw), so no shared-column alignment pass is needed. Hand each button the
     * whole row so mouse hover stays exclusive, takes over the TAB highlight, and STICKS on
     * the last-hovered button after the pointer leaves (see power.c). */
    az_power_row_set_siblings(m->power_buttons, 4);

    gtk_widget_add_events(m->win, GDK_KEY_PRESS_MASK | GDK_BUTTON_PRESS_MASK);
    g_signal_connect(m->win, "key-press-event", G_CALLBACK(on_key_press), m);
    g_signal_connect(m->win, "button-press-event", G_CALLBACK(on_button_press), m);

    /* Realize + make override-redirect (no WM chrome, unmanaged). */
    gtk_widget_realize(m->win);
    GdkWindow *gw = gtk_widget_get_window(m->win);
    gdk_window_set_override_redirect(gw, TRUE);
}

/* --- daemon: pidfile + signal loop ---------------------------------------- */
static char *pid_path(void) {
    const char *rt = g_getenv("XDG_RUNTIME_DIR");
    if (!rt || !rt[0]) rt = "/tmp";
    return g_build_filename(rt, "azzio-application-menu.pid", NULL);
}

/* Self-pipe for async-signal-safe wakeup on the GTK loop. */
static int sig_pipe[2];

static void sig_handler(int signum) {
    char b = (char)signum;
    ssize_t r = write(sig_pipe[1], &b, 1);
    (void)r;
}

static void do_toggle(AzMenu *m) {
    if (m->shown) {
        /* A SIGUSR1 while shown just closes. It is NOT armed for an echo swallow: a
         * physical Super close is delivered to the grabbed window (on_key_press arms it
         * there), so this SIGUSR1 path is a launcher/programmatic toggle with no paired
         * xcape echo -- arming here would eat the next real toggle (breaks spam). */
        hide_menu(m);
        return;
    }
    /* Hidden. Swallow at most ONE toggle -- the xcape echo of the close-tap -- and
     * only if a Super close armed the token AND it lands within the cap. Everything
     * else (including every deliberate re-press after the echo) shows immediately, so
     * rapid Super spam stays responsive. */
    gint64 since = g_get_monotonic_time() - m->last_hidden_us;
    if (m->expect_echo && since >= 0 && since < ECHO_CAP_US) {
        m->expect_echo = FALSE;      /* consume the one echo token */
        return;
    }
    m->expect_echo = FALSE;          /* stale/absent echo -> never eat a later press */
    show_menu(m);
}

static gboolean on_sig_pipe(GIOChannel *src, GIOCondition cond, gpointer user) {
    (void)cond;
    AzMenu *m = user;
    char buf[64];
    gsize n = 0;
    if (g_timing) g_sig_recv_us = g_get_monotonic_time();
    g_io_channel_read_chars(src, buf, sizeof(buf), &n, NULL);
    /* Collapse a burst to the last command (a trailing show wins). */
    char last = 0;
    for (gsize i = 0; i < n; i++) {
        if (buf[i] == SIGTERM || buf[i] == SIGINT) { gtk_main_quit(); return TRUE; }
        last = buf[i];
    }
    if (last == SIGUSR1) do_toggle(m);
    else if (last == SIGUSR2) show_menu(m);
    return TRUE;
}

static gboolean claim_pidfile(const char *path) {
    /* O_CREAT|O_EXCL single-instance; if a live daemon owns it, bow out. */
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
        if (errno != EEXIST)
            return TRUE;             /* can't lock -> run anyway */
        /* Read the owner; if alive, bow out; if stale, remove and retry. */
        char *txt = NULL;
        int other = -1;
        if (g_file_get_contents(path, &txt, NULL, NULL) && txt)
            other = atoi(g_strstrip(txt));
        g_free(txt);
        if (other > 0 && kill(other, 0) == 0)
            return FALSE;            /* a live daemon owns it */
        g_unlink(path);              /* stale -> retry create */
    }
    return TRUE;
}

static void remove_pidfile(const char *path) {
    char *txt = NULL;
    if (g_file_get_contents(path, &txt, NULL, NULL) && txt) {
        char *s = g_strstrip(txt);
        char *mine = g_strdup_printf("%d", (int)getpid());
        if (strcmp(s, mine) == 0)
            g_unlink(path);
        g_free(mine);
    }
    g_free(txt);
}

/* Provider for the watcher: fresh scanned+visible entries. */
static GPtrArray *entries_provider(gpointer user) {
    (void)user;
    return az_scan_applications();
}

int main(int argc, char **argv) {
    gtk_init(&argc, &argv);
    /* Latch the system theme (dark by default) BEFORE anything is styled: install_css()
     * and build_window() below read the AZ_*_COLOR macros, which resolve to the dark or
     * light palette per what az_theme_init() reads from the freedesktop color-scheme. The
     * daemon is restarted by `azzio theme` when the theme flips, so re-reading here on
     * launch is enough. */
    az_theme_init();
    g_timing = (g_getenv("AZ_TIMING") != NULL);

    char *pidfile = pid_path();
    if (!claim_pidfile(pidfile)) {
        g_free(pidfile);
        return 0;                    /* another daemon already runs */
    }

    AzMenu *m = &g_menu;
    m->icons = az_icons_new(AZ_ICON_SIZE);
    m->small_icons = az_icons_new(AZ_POWER_ICON_SIZE);
    m->usage = az_usage_new();
    m->all_apps = az_scan_applications();
    order_apps(m->usage, m->all_apps);

    build_window(m);
    warmup(m);

    /* Window watcher for system-wide launch counting (best-effort). */
    m->watcher = az_watcher_new(m->usage, entries_provider, NULL, (int)getpid());
    az_watcher_start(m->watcher);

    /* Self-pipe signal wiring. */
    if (pipe(sig_pipe) == 0) {
        GIOChannel *ch = g_io_channel_unix_new(sig_pipe[0]);
        g_io_channel_set_encoding(ch, NULL, NULL);
        g_io_channel_set_buffered(ch, FALSE);
        g_io_add_watch(ch, G_IO_IN, on_sig_pipe, m);
        g_io_channel_unref(ch);
        struct sigaction sa; memset(&sa, 0, sizeof(sa));
        sa.sa_handler = sig_handler;
        sigaction(SIGUSR1, &sa, NULL);
        sigaction(SIGUSR2, &sa, NULL);
        sigaction(SIGTERM, &sa, NULL);
        sigaction(SIGINT, &sa, NULL);
    }

    gtk_main();

    az_watcher_free(m->watcher);
    remove_pidfile(pidfile);
    g_free(pidfile);
    return 0;
}
