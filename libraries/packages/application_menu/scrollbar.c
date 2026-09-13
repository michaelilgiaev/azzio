/* Azzio application menu (C port) -- the custom pill scrollbar, drawn with Cairo. */
#include "scrollbar.h"
#include "theme.h"

#include <math.h>

struct AzScrollbar {
    GtkWidget     *area;      /* GtkDrawingArea (the bar) */
    GtkAdjustment *vadj;      /* the scrolled window's vertical adjustment */
    gboolean       hover;
    gboolean       dragging;
    double         drag_dy;   /* grab offset within the thumb (px) */
};

/* ---- view fractions from the adjustment (Tk first/last) ------------------ */
static void view_fraction(AzScrollbar *s, double *first, double *last) {
    double lo = gtk_adjustment_get_lower(s->vadj);
    double up = gtk_adjustment_get_upper(s->vadj);
    double val = gtk_adjustment_get_value(s->vadj);
    double page = gtk_adjustment_get_page_size(s->vadj);
    double span = up - lo;
    if (span <= 0) { *first = 0.0; *last = 1.0; return; }
    *first = (val - lo) / span;
    *last  = (val - lo + page) / span;
}

/* Content fits entirely -> nothing to scroll (Tk: first<=0 && last>=1). */
static gboolean fits(AzScrollbar *s) {
    double f, l;
    view_fraction(s, &f, &l);
    return f <= 0.0 && l >= 1.0;
}

/* ---- geometry ------------------------------------------------------------ */
static void set_src(cairo_t *cr, const char *hex) {
    GdkRGBA c; gdk_rgba_parse(&c, hex);
    cairo_set_source_rgba(cr, c.red, c.green, c.blue, c.alpha);
}

static int widget_h(AzScrollbar *s) {
    GtkAllocation a; gtk_widget_get_allocation(s->area, &a);
    return MAX(1, a.height);
}
static int widget_w(AzScrollbar *s) {
    GtkAllocation a; gtk_widget_get_allocation(s->area, &a);
    return MAX(1, a.width);
}

/* Pixel (top,bottom) of the thumb for the current view, clamped to a minimum
 * grabbable length. */
static void thumb_span(AzScrollbar *s, double *top_out, double *bot_out) {
    int h = widget_h(s);
    double f, l;
    view_fraction(s, &f, &l);
    double top = f * h;
    double bot = l * h;
    if (bot - top < AZ_SCROLL_THUMB_MIN) {
        double mid = (top + bot) / 2.0;
        double half = AZ_SCROLL_THUMB_MIN / 2.0;
        top = mid - half;
        bot = mid + half;
        if (top < 0)  { top = 0;                    bot = AZ_SCROLL_THUMB_MIN; }
        if (bot > h)  { bot = h;                     top = h - AZ_SCROLL_THUMB_MIN; }
    }
    *top_out = round(top);
    *bot_out = round(bot);
}

/* ---- drawing ------------------------------------------------------------- */
static gboolean on_draw(GtkWidget *w, cairo_t *cr, gpointer data) {
    (void)w;
    AzScrollbar *s = data;
    if (fits(s))
        return FALSE;                 /* content fits -> draw nothing */

    int wpx = widget_w(s);
    int hpx = widget_h(s);
    double thumb_w = AZ_SCROLL_THUMB_WIDTH;
    double x0 = (wpx - thumb_w) / 2.0;
    double x1 = x0 + thumb_w;
    double r = thumb_w / 2.0;          /* pill radius = half width */
    double top, bot;
    thumb_span(s, &top, &bot);

    /* Groove behind the thumb: hover-only, spanning the full track (a pill of its
     * own), fading in on hover. */
    if (s->hover) {
        set_src(cr, AZ_SCROLL_GROOVE_COLOR);
        cairo_new_sub_path(cr);
        cairo_arc(cr, x0 + r, r,        r, G_PI, 2 * G_PI);      /* top cap */
        cairo_arc(cr, x0 + r, hpx - r,  r, 0, G_PI);             /* bottom cap */
        cairo_close_path(cr);
        cairo_rectangle(cr, x0, r, thumb_w, hpx - 2 * r);
        cairo_fill(cr);
    }

    const char *col = (s->hover || s->dragging)
                      ? AZ_SCROLL_THUMB_HOVER : AZ_SCROLL_THUMB_COLOR;
    set_src(cr, col);

    /* Pill thumb: rounded-rect (round cap + body + round cap), all one colour. */
    double th = bot - top;
    if (th < thumb_w) th = thumb_w;
    cairo_new_sub_path(cr);
    cairo_arc(cr, x1 - r, top + r,      r, -G_PI_2, 0);
    cairo_arc(cr, x1 - r, top + th - r, r, 0, G_PI_2);
    cairo_arc(cr, x0 + r, top + th - r, r, G_PI_2, G_PI);
    cairo_arc(cr, x0 + r, top + r,      r, G_PI, 3 * G_PI_2);
    cairo_close_path(cr);
    cairo_fill(cr);
    return FALSE;
}

/* ---- interaction --------------------------------------------------------- */
static void scroll_to_pixel(AzScrollbar *s, double y) {
    /* Move the view so the thumb top lands at (y - grab offset). */
    int h = widget_h(s);
    double f, l;
    view_fraction(s, &f, &l);
    double thumb_len = l - f;
    double new_top = (y - s->drag_dy) / h;
    if (new_top < 0) new_top = 0;
    if (new_top > 1.0 - thumb_len) new_top = 1.0 - thumb_len;
    double lo = gtk_adjustment_get_lower(s->vadj);
    double up = gtk_adjustment_get_upper(s->vadj);
    gtk_adjustment_set_value(s->vadj, lo + new_top * (up - lo));
}

/* Local y within the bar for a pointer event, taken from ROOT coordinates. During
 * a drag the GTK grab (see on_press) can hand us motion events whose window -- and
 * so whose e->y -- belongs to a sibling widget the pointer drifted onto; e->y_root
 * is screen-absolute and always right, so translate it against the bar window's
 * screen origin instead of trusting e->y. */
static double bar_local_y(AzScrollbar *s, double y_root) {
    GdkWindow *win = gtk_widget_get_window(s->area);
    int ox = 0, oy = 0;
    if (win) gdk_window_get_origin(win, &ox, &oy);
    return y_root - oy;
}

static gboolean on_press(GtkWidget *w, GdkEventButton *e, gpointer data) {
    (void)w;
    AzScrollbar *s = data;
    if (e->button != 1 || fits(s))
        return FALSE;
    double top, bot;
    thumb_span(s, &top, &bot);
    if (e->y >= top && e->y <= bot) {
        s->dragging = TRUE;
        s->drag_dy = e->y - top;              /* grab within the thumb */
    } else {
        /* Press on empty track: centre the thumb on the click, then drag from there. */
        s->dragging = TRUE;
        s->drag_dy = (bot - top) / 2.0;
        scroll_to_pixel(s, e->y);
    }
    /* Route ALL further pointer events here until release, even when the pointer
     * drifts off the narrow bar onto the list. Without this, motion is delivered
     * to whatever window sits under the pointer, so a real drag that wanders a few
     * px sideways stops feeding on_motion and the thumb freezes/stutters -- the
     * "laggy, not smooth when I drag it" bug. */
    gtk_grab_add(s->area);
    gtk_widget_queue_draw(s->area);
    return TRUE;
}

static gboolean on_motion(GtkWidget *w, GdkEventMotion *e, gpointer data) {
    (void)w;
    AzScrollbar *s = data;
    if (s->dragging)
        scroll_to_pixel(s, bar_local_y(s, e->y_root));
    return FALSE;
}

static gboolean on_release(GtkWidget *w, GdkEventButton *e, gpointer data) {
    (void)w; (void)e;
    AzScrollbar *s = data;
    if (s->dragging) {
        s->dragging = FALSE;
        gtk_grab_remove(s->area);
    }
    gtk_widget_queue_draw(s->area);
    return FALSE;
}

static gboolean on_enter(GtkWidget *w, GdkEventCrossing *e, gpointer data) {
    (void)w; (void)e;
    AzScrollbar *s = data;
    s->hover = TRUE;
    gtk_widget_queue_draw(s->area);
    return FALSE;
}
static gboolean on_leave(GtkWidget *w, GdkEventCrossing *e, gpointer data) {
    (void)w; (void)e;
    AzScrollbar *s = data;
    s->hover = FALSE;
    gtk_widget_queue_draw(s->area);
    return FALSE;
}

/* Repaint whenever the view changes (scroll from any source) and hide/show to
 * match "no bar when everything fits". */
static void on_adj_changed(GtkAdjustment *adj, gpointer data) {
    (void)adj;
    AzScrollbar *s = data;
    /* Reserve the track column only when there is something to scroll, giving the
     * column back to the list otherwise -- but do it by COLLAPSING THE WIDTH, never
     * by gtk_widget_set_visible. Mapping/unmapping the bar's GdkWindow on a filter
     * change forced a full relayout of the override-redirect toplevel that left the
     * ENTIRE window painted black until the next redraw (the "goes black when I
     * delete" bug). A width change relayouts the list the same way but keeps the bar
     * mapped, so no black expose. width 0 also makes on_draw a no-op via its own
     * clamp, and the pill simply has no room. */
    int want = fits(s) ? 0 : AZ_SCROLL_TRACK_WIDTH;
    int cur = -1;
    gtk_widget_get_size_request(s->area, &cur, NULL);
    if (cur != want)
        gtk_widget_set_size_request(s->area, want, -1);
    gtk_widget_queue_draw(s->area);
}

/* ---- construction -------------------------------------------------------- */
AzScrollbar *az_scrollbar_new(GtkAdjustment *vadj) {
    AzScrollbar *s = g_new0(AzScrollbar, 1);
    s->vadj = vadj;

    s->area = gtk_drawing_area_new();
    gtk_widget_set_size_request(s->area, AZ_SCROLL_TRACK_WIDTH, -1);
    gtk_widget_set_halign(s->area, GTK_ALIGN_END);
    gtk_widget_set_valign(s->area, GTK_ALIGN_FILL);
    gtk_widget_add_events(s->area, GDK_BUTTON_PRESS_MASK | GDK_BUTTON_RELEASE_MASK |
                          GDK_POINTER_MOTION_MASK | GDK_ENTER_NOTIFY_MASK |
                          GDK_LEAVE_NOTIFY_MASK);
    g_signal_connect(s->area, "draw", G_CALLBACK(on_draw), s);
    g_signal_connect(s->area, "button-press-event", G_CALLBACK(on_press), s);
    g_signal_connect(s->area, "button-release-event", G_CALLBACK(on_release), s);
    g_signal_connect(s->area, "motion-notify-event", G_CALLBACK(on_motion), s);
    g_signal_connect(s->area, "enter-notify-event", G_CALLBACK(on_enter), s);
    g_signal_connect(s->area, "leave-notify-event", G_CALLBACK(on_leave), s);

    g_signal_connect(vadj, "changed", G_CALLBACK(on_adj_changed), s);
    g_signal_connect(vadj, "value-changed", G_CALLBACK(on_adj_changed), s);
    return s;
}

GtkWidget *az_scrollbar_widget(AzScrollbar *s) { return s->area; }

void az_scrollbar_free(AzScrollbar *s) {
    if (!s) return;
    g_free(s);
}
