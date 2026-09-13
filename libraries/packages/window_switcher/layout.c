/* Azzio window switcher -- the horizontal tile strip. See layout.h.
 *
 * Look mirrors the application menu's cyan-on-dark family (theme.c colours): a rounded
 * panel, tiles with a live thumbnail (or the app icon when no pixmap exists yet), the app
 * icon badged bottom-left, the title underneath, and a cyan border on the selected
 * tile. */
#include "layout.h"
#include "ordering.h"
#include "windows.h"
#include "thumbnail.h"

#include "theme.h"      /* AZ_*_COLOR, az_color(AZ_C_SELECT_BORDER) */
#include "icons.h"      /* AzIcons, az_icons_new/az_icons_load */

/* Tile geometry. These are STOCK (scale-1.0) pixel sizes wrapped in AZ_SCALED() so the whole
 * strip grows with the GLOBAL SCALE (packages/openbox/scale; the switcher reuses theme.c which
 * includes the generated az_scale.h). The switcher window is override-redirect and its metrics
 * are FIXED PIXELS, so -- unlike a normal GTK app's point fonts -- nothing scales them via
 * gtk-xft-dpi; AZ_SCALED() is what makes them scale-aware. At the 1.35 default AZ_SCALED(148)==200,
 * matching the sizes the strip shipped with before it was made scale-aware. */
#define TILE_W       AZ_SCALED(148)   /* == 200 @1.35 (the old fixed 200) */
#define TILE_H       AZ_SCALED(96)    /* ==	130 @1.35 (the old fixed 130) */
#define BADGE_PX     AZ_SCALED(27)    /* ==  36 @1.35 (the old fixed 36) */
#define STRIP_SPACING AZ_SCALED(9)    /* ==  12 @1.35 (the old fixed 12) */

/* The tile TITLE font (px). DOUBLED from the old fixed 11px AND made scale-aware: the stock
 * (scale-1.0) base 16 gives AZ_SCALED(16)==22 at the 1.35 default -- ~2x the old 11px, and it
 * tracks the scale like everything else. It is a CSS `px` size on GTK's DPI-blind path, so it
 * is scaled HERE (gtk-xft-dpi scales points, not px), exactly like the tile geometry above. */
#define TITLE_FONT_PX AZ_SCALED(16)   /* == 22 @1.35 (2x the old 11px, scale-aware) */

/* One tile's tracked widgets + identity. Kept so the live refresh can update the thumbnail
 * IN PLACE (see az_strip_refresh_thumbnails) instead of destroying and rebuilding the whole
 * strip every tick -- the rebuild was the source of the laggy, flickery "live" render. The
 * widgets are borrowed (owned by the GTK tree); icon_name is owned by the tile. */
typedef struct {
    GtkWidget    *tile;         /* the tile's vertical GtkBox */
    GtkWidget    *image;        /* the thumbnail GtkImage we swap the pixbuf on each refresh */
    unsigned long xid;          /* the window whose live pixels this tile shows */
    char         *icon_name;    /* app icon name for the fallback when no pixmap exists (owned) */
} AzTile;

struct AzStrip {
    GtkWidget *box;             /* the horizontal GtkBox of tiles */
    GPtrArray *tiles;           /* AzTile* per tile (owned; freed on clear/rebuild) */
    AzIcons   *icons;           /* icon resolver for the badge + fallback */
    int        selected;
};

/* Install the strip CSS once per display (idempotent-ish; cheap to repeat). */
static void ensure_css(void) {
    static gboolean done = FALSE;
    if (done) return;
    done = TRUE;
    char *css = g_strdup_printf(
        ".az-strip { background-color: %s; border-radius: 12px; padding: 16px; }"
        ".az-tile { background-color: %s; border-radius: 6px; padding: 6px;"
        "           border: 3px solid transparent; }"
        ".az-tile-selected { border: 3px solid %s;"
        "           background-color: %s; }"
        ".az-title { color: %s; font-size: %dpx; }",
        AZ_BG_COLOR, AZ_SURFACE_COLOR,
        az_color(AZ_C_SELECT_BORDER), az_color(AZ_C_SELECT_FILL),
        AZ_TEXT_COLOR, TITLE_FONT_PX);
    GtkCssProvider *p = gtk_css_provider_new();
    gtk_css_provider_load_from_data(p, css, -1, NULL);
    gtk_style_context_add_provider_for_screen(
        gdk_screen_get_default(), GTK_STYLE_PROVIDER(p),
        GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
    g_object_unref(p);
    g_free(css);
}

AzStrip *az_strip_new(GtkWidget *parent) {
    ensure_css();
    AzStrip *s = g_new0(AzStrip, 1);
    s->box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, STRIP_SPACING);
    gtk_style_context_add_class(gtk_widget_get_style_context(s->box), "az-strip");
    gtk_widget_set_halign(s->box, GTK_ALIGN_CENTER);
    gtk_widget_set_valign(s->box, GTK_ALIGN_CENTER);
    s->tiles = g_ptr_array_new();
    s->icons = az_icons_new(BADGE_PX);
    s->selected = 0;
    gtk_container_add(GTK_CONTAINER(parent), s->box);
    return s;
}

/* Capture the tile's picture for `w`: the LIVE thumbnail if a pixmap exists, else the app icon
 * scaled up (borrowed pixbuf), else the generic executable icon. Returns a pixbuf to show, or
 * NULL only when even the generic icon is unavailable. The returned pixbuf carries its own ref
 * (thumbnail) OR is a floating icon ref -- the caller hands it straight to gtk_image_set_from_
 * pixbuf and does NOT unref (matches the old code, which never unref'd the icon path). */
static GdkPixbuf *capture_tile_pixbuf(AzStrip *s, unsigned long xid, const char *icon_name,
                                      gboolean *is_thumb) {
    GdkPixbuf *thumb = az_thumbnail_capture(xid, TILE_W, TILE_H);
    if (thumb) { if (is_thumb) *is_thumb = TRUE; return thumb; }
    if (is_thumb) *is_thumb = FALSE;
    return az_icons_load(s->icons, icon_name ? icon_name : "");  /* borrowed; may be NULL */
}

/* Point an existing tile GtkImage at `w`'s current picture (thumbnail or icon fallback). This is
 * the IN-PLACE refresh the live tick uses -- no widget is created or destroyed, so the strip does
 * not re-layout or flicker; the tile just shows a newer frame. The thumbnail pixbuf is a fresh
 * ref we drop after handing it over; the icon fallback is a borrowed ref we must not unref. */
static void set_tile_image(AzStrip *s, GtkWidget *image, unsigned long xid, const char *icon_name) {
    gboolean is_thumb = FALSE;
    GdkPixbuf *pix = capture_tile_pixbuf(s, xid, icon_name, &is_thumb);
    if (pix) {
        gtk_image_set_from_pixbuf(GTK_IMAGE(image), pix);
        if (is_thumb) g_object_unref(pix);   /* thumbnail owns a ref; icon is borrowed */
    } else if (!gtk_image_get_pixbuf(GTK_IMAGE(image))) {
        /* Only if the image is still empty, seed the generic icon (avoid clobbering a good frame). */
        gtk_image_set_from_icon_name(GTK_IMAGE(image), "application-x-executable",
                                     GTK_ICON_SIZE_DIALOG);
    }
    gtk_widget_set_size_request(image, TILE_W, TILE_H);
}

static void tile_free(gpointer p) {
    AzTile *t = p;
    if (!t) return;
    g_free(t->icon_name);
    g_free(t);
}

/* One tile: [ overlay(thumbnail, corner icon badge) ] over [ title label ]. Returns a tracked
 * AzTile (owns icon_name; the widgets are owned by the GTK tree once packed). */
static AzTile *make_tile(AzStrip *s, const AzWinIdent *w) {
    AzTile *t = g_new0(AzTile, 1);
    t->xid = w->xid;
    t->icon_name = g_strdup(w->icon_name ? w->icon_name : "");

    GtkWidget *tile = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
    gtk_style_context_add_class(gtk_widget_get_style_context(tile), "az-tile");
    gtk_widget_set_size_request(tile, TILE_W, -1);
    t->tile = tile;

    GtkWidget *overlay = gtk_overlay_new();
    GtkWidget *img = gtk_image_new();
    t->image = img;
    set_tile_image(s, img, w->xid, t->icon_name);   /* seed the first frame */
    gtk_widget_set_halign(img, GTK_ALIGN_CENTER);
    gtk_widget_set_valign(img, GTK_ALIGN_CENTER);
    gtk_container_add(GTK_CONTAINER(overlay), img);

    /* Corner app-icon badge (bottom-left, Windows-like). */
    GdkPixbuf *badge = az_icons_load(s->icons, w->icon_name ? w->icon_name : "");
    if (badge) {
        GtkWidget *bimg = gtk_image_new_from_pixbuf(badge);
        gtk_widget_set_halign(bimg, GTK_ALIGN_START);
        gtk_widget_set_valign(bimg, GTK_ALIGN_END);
        gtk_overlay_add_overlay(GTK_OVERLAY(overlay), bimg);
    }
    gtk_box_pack_start(GTK_BOX(tile), overlay, FALSE, FALSE, 0);

    GtkWidget *label = gtk_label_new(w->display_name ? w->display_name : "");
    gtk_style_context_add_class(gtk_widget_get_style_context(label), "az-title");
    gtk_label_set_ellipsize(GTK_LABEL(label), PANGO_ELLIPSIZE_END);
    gtk_label_set_max_width_chars(GTK_LABEL(label), 18);
    gtk_label_set_xalign(GTK_LABEL(label), 0.5f);
    gtk_box_pack_start(GTK_BOX(tile), label, FALSE, FALSE, 0);

    return t;
}

static void clear_tiles(AzStrip *s) {
    GList *kids = gtk_container_get_children(GTK_CONTAINER(s->box));
    for (GList *l = kids; l; l = l->next)
        gtk_widget_destroy(GTK_WIDGET(l->data));
    g_list_free(kids);
    for (guint i = 0; i < s->tiles->len; i++)
        tile_free(g_ptr_array_index(s->tiles, i));
    g_ptr_array_set_size(s->tiles, 0);
}

/* Does the strip already show EXACTLY these windows, in this order? If so the live tick can
 * refresh thumbnails in place instead of tearing down and rebuilding every tile (the flicker
 * fix): the common case -- a periodic refresh with no window opened/closed -- rebuilds nothing. */
static gboolean same_windows(AzStrip *s, GPtrArray *windows) {
    guint n = windows ? windows->len : 0;
    if (n != s->tiles->len) return FALSE;
    for (guint i = 0; i < n; i++) {
        AzWinIdent *w = g_ptr_array_index(windows, i);
        AzTile *t = g_ptr_array_index(s->tiles, i);
        if (t->xid != w->xid) return FALSE;
    }
    return TRUE;
}

void az_strip_set_windows(AzStrip *s, GPtrArray *windows) {
    /* Unchanged window set (the periodic live refresh): just stream fresh frames into the
     * existing tiles -- no destroy/rebuild, so the strip does not flicker or re-layout. */
    if (same_windows(s, windows)) {
        az_strip_refresh_thumbnails(s);
        return;
    }
    clear_tiles(s);
    guint n = windows ? windows->len : 0;
    for (guint i = 0; i < n; i++) {
        AzWinIdent *w = g_ptr_array_index(windows, i);
        AzTile *t = make_tile(s, w);
        gtk_box_pack_start(GTK_BOX(s->box), t->tile, FALSE, FALSE, 0);
        g_ptr_array_add(s->tiles, t);
    }
    gtk_widget_show_all(s->box);
    if (s->selected >= (int)n) s->selected = (n > 0) ? (int)n - 1 : 0;
    az_strip_select(s, s->selected);
}

/* Push the strip's queued redraws to the SCREEN right now. The overlay is an override-redirect
 * window shown by MOVING it on-screen (never re-mapped -- see switcher.c), so it is not driven by
 * the usual map/expose cycle a normal toplevel gets from the WM. gtk_widget_queue_draw only
 * *queues* an invalidation; without a frame being pumped, that queued draw can sit unflushed until
 * some unrelated event (a changing thumbnail) happens to force one. That is exactly why moving the
 * SELECTION updated s->selected + the az-tile-selected CSS class correctly (the widget state was
 * right) yet the blue border did not appear to move on screen, and why the live tiles updated only
 * sparsely. Forcing the toplevel's pending updates out to the X server makes the change visible
 * immediately: queue on the toplevel, process its updates synchronously, then flush the display. */
static void az_strip_flush(AzStrip *s) {
    GtkWidget *top = gtk_widget_get_toplevel(s->box);
    if (!top || !gtk_widget_is_toplevel(top)) return;
    GdkWindow *gw = gtk_widget_get_window(top);
    if (!gw) return;
    gtk_widget_queue_draw(top);
    gdk_window_process_updates(gw, TRUE);
    gdk_display_flush(gtk_widget_get_display(top));
}

/* Stream a fresh frame into every EXISTING tile without rebuilding the widget tree. This is what
 * makes the live render smooth: the periodic tick calls this (via az_strip_set_windows when the
 * window set is unchanged) so each tile just shows newer pixels -- no widget churn, no re-layout,
 * no flicker. A minimized/covered window with no pixmap keeps its icon fallback until it maps.
 * az_strip_flush pushes the new frames to the screen so the override-redirect overlay actually
 * streams (without it the tiles only repaint when some other event forces a frame). */
void az_strip_refresh_thumbnails(AzStrip *s) {
    for (guint i = 0; i < s->tiles->len; i++) {
        AzTile *t = g_ptr_array_index(s->tiles, i);
        set_tile_image(s, t->image, t->xid, t->icon_name);
    }
    az_strip_flush(s);
}

void az_strip_select(AzStrip *s, int i) {
    int n = (int)s->tiles->len;
    if (n <= 0) { s->selected = 0; return; }
    i = ((i % n) + n) % n;                 /* wrap into [0,n) */
    s->selected = i;
    for (int k = 0; k < n; k++) {
        AzTile *t = g_ptr_array_index(s->tiles, k);
        GtkStyleContext *ctx = gtk_widget_get_style_context(t->tile);
        if (k == i) gtk_style_context_add_class(ctx, "az-tile-selected");
        else        gtk_style_context_remove_class(ctx, "az-tile-selected");
    }
    /* Flush the border change to the screen NOW. Toggling the CSS class updates the widget's
     * style correctly, but on this override-redirect overlay the resulting redraw is only queued;
     * without this the selected-tile border does not visibly move (the reported bug: "pressing Tab
     * again doesn't move to the window on the right"). */
    az_strip_flush(s);
}

int az_strip_selected(AzStrip *s) { return s->selected; }
int az_strip_count(AzStrip *s)    { return (int)s->tiles->len; }

unsigned long az_strip_selected_xid(AzStrip *s) {
    int i = s->selected;
    if (i < 0 || i >= (int)s->tiles->len) return 0;
    AzTile *t = g_ptr_array_index(s->tiles, i);
    return t->xid;
}

int az_strip_index_of_xid(AzStrip *s, unsigned long xid) {
    if (xid == 0) return -1;
    for (guint i = 0; i < s->tiles->len; i++) {
        AzTile *t = g_ptr_array_index(s->tiles, i);
        if (t->xid == xid) return (int)i;
    }
    return -1;
}

void az_strip_free(AzStrip *s) {
    if (!s) return;
    if (s->icons) az_icons_free(s->icons);
    if (s->tiles) {
        for (guint i = 0; i < s->tiles->len; i++)
            tile_free(g_ptr_array_index(s->tiles, i));
        g_ptr_array_free(s->tiles, TRUE);
    }
    g_free(s);
}
