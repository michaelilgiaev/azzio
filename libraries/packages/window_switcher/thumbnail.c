/* Azzio window switcher -- live thumbnail capture via XComposite. See thumbnail.h.
 *
 * picom (started from the OpenBox autostart) redirects every window to an off-screen
 * pixmap; here we name that pixmap, wrap it in a cairo Xlib surface, pull it into a
 * GdkPixbuf, and scale it into the tile. Every X round-trip is wrapped in a trapped
 * error handler because a window can unmap/close between enumeration and capture -- that
 * must yield NULL (icon fallback), never an Xlib abort. */
#include "thumbnail.h"

#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/Xcomposite.h>
#include <cairo.h>
#include <cairo-xlib.h>
#include <gdk/gdk.h>

/* ---- trapped X errors --------------------------------------------------- */
static volatile int x_err = 0;           /* set on ANY trapped error */
static volatile int x_err_code = 0;      /* the error_code of the last trapped error */
static int trap_handler(Display *d, XErrorEvent *e) {
    (void)d;
    x_err = 1;
    x_err_code = e->error_code;
    return 0;
}

/* Ensure `win` is redirected so its backing pixmap can be named. picom redirects windows
 * for its OWN painting (a Manual redirect on root's subwindows), but that does NOT let a
 * SECOND client name the pixmap: XCompositeNameWindowPixmap then returns BadMatch (verified
 * under picom v13). The switcher must therefore add its OWN CompositeRedirectAutomatic
 * reference on each window before naming it. Automatic is cooperative -- it leaves picom's
 * Manual redirect in charge of actually painting the window to screen, so the desktop is
 * visually unchanged.
 *
 * CRUCIAL: each XCompositeRedirectWindow(Automatic) SUCCEEDS and bumps a server-side
 * reference count -- a repeat call from this same client does NOT error (contrary to a
 * naive reading of the protocol; it just returns success and increments the count). The
 * capture path runs on a ~200ms timer, so redirecting on every tick would leak references
 * without bound. We therefore redirect each window EXACTLY ONCE and NEVER unredirect: the
 * server releases our reference automatically when the window is destroyed or this client
 * exits. The set of already-redirected XIDs is remembered in a static hash set, and once an
 * XID is in it we never touch its redirect again -- not even on error.
 *
 * This "redirect once, never forget" rule is deliberate: a window listed in
 * _NET_CLIENT_LIST but currently UNMAPPED (minimized) is redirected fine, yet has no
 * backing pixmap, so NameWindowPixmap returns BadMatch for it every tick. If BadMatch made
 * us drop the XID and re-redirect next tick, a minimized window (the very thing Alt+Tab
 * exists to reach) would leak one reference per tick. So we keep the XID remembered and
 * simply fall back to the app icon each tick until the window maps and grows a pixmap.
 * (An XID the server later recycles for a different window is the only downside -- that new
 * window shows its icon instead of a live thumbnail -- which is rare, self-limited to one
 * window, and crucially LEAKS NOTHING.) */
static GHashTable *redirected_set(void) {
    static GHashTable *set = NULL;   /* set of XIDs (as pointers) we have redirected */
    if (!set) set = g_hash_table_new(g_direct_hash, g_direct_equal);
    return set;
}

static void ensure_redirected(Display *dpy, Window win) {
    GHashTable *set = redirected_set();
    if (g_hash_table_contains(set, GSIZE_TO_POINTER(win))) return;  /* already ours */
    x_err = 0; x_err_code = 0;
    XCompositeRedirectWindow(dpy, win, CompositeRedirectAutomatic);
    XSync(dpy, False);
    if (!x_err) g_hash_table_add(set, GSIZE_TO_POINTER(win));
    /* On error, leave it recorded for the caller's check and do NOT remember the XID. */
}

/* Shared display + a one-time XComposite presence check. NULL display -> no capture. */
static Display *display(gboolean *has_composite) {
    static Display *dpy = NULL;
    static gboolean checked = FALSE;
    static gboolean composite = FALSE;
    if (!checked) {
        checked = TRUE;
        dpy = XOpenDisplay(NULL);
        if (dpy) {
            int ev = 0, er = 0;
            composite = XCompositeQueryExtension(dpy, &ev, &er) ? TRUE : FALSE;
        }
    }
    if (has_composite) *has_composite = composite;
    return dpy;
}

/* Scale `src` to fit inside (max_w,max_h), preserving aspect. Never upscales past the
 * source (a tiny window stays small, centered by the caller). Returns a new ref. */
static GdkPixbuf *scale_fit(GdkPixbuf *src, int max_w, int max_h) {
    int w = gdk_pixbuf_get_width(src), h = gdk_pixbuf_get_height(src);
    if (w <= 0 || h <= 0) return NULL;
    double s = (double)max_w / w;
    double sh = (double)max_h / h;
    if (sh < s) s = sh;
    if (s > 1.0) s = 1.0;                 /* don't blow up small windows */
    int nw = (int)(w * s + 0.5), nh = (int)(h * s + 0.5);
    if (nw < 1) nw = 1;
    if (nh < 1) nh = 1;
    return gdk_pixbuf_scale_simple(src, nw, nh, GDK_INTERP_BILINEAR);
}

GdkPixbuf *az_thumbnail_capture(unsigned long xid, int max_w, int max_h) {
    gboolean have_composite = FALSE;
    Display *dpy = display(&have_composite);
    if (!dpy || !have_composite || xid == 0 || max_w <= 0 || max_h <= 0)
        return NULL;

    Window win = (Window)xid;
    GdkPixbuf *result = NULL;
    Pixmap pixmap = 0;
    cairo_surface_t *surf = NULL;
    GdkPixbuf *shot = NULL;

    XErrorHandler prev = XSetErrorHandler(trap_handler);
    x_err = 0;

    XWindowAttributes attr;
    if (!XGetWindowAttributes(dpy, win, &attr) || x_err) goto out;
    if (attr.map_state != IsViewable && attr.map_state != IsUnmapped) goto out;
    if (attr.width <= 0 || attr.height <= 0) goto out;

    /* Take our own redirect reference so NameWindowPixmap can name the pixmap (see the
     * comment on ensure_redirected -- without this picom's redirect alone gives BadMatch). */
    ensure_redirected(dpy, win);
    if (x_err) goto out;                  /* a real redirect failure (e.g. window gone) */

    /* Name the window's backing pixmap (now that it is redirected for this client). */
    x_err = 0; x_err_code = 0;
    pixmap = XCompositeNameWindowPixmap(dpy, win);
    XSync(dpy, False);
    /* BadMatch here is normal for a redirected-but-unmapped (minimized) window -- it simply
     * has no pixmap yet, so we fall back to the icon this tick. We must NOT drop the redirect
     * on BadMatch: doing so would re-redirect the window every tick and leak a reference per
     * tick for the whole time it stays minimized (see ensure_redirected). */
    if (x_err || pixmap == 0) goto out;

    surf = cairo_xlib_surface_create(dpy, pixmap, attr.visual,
                                     attr.width, attr.height);
    if (!surf || cairo_surface_status(surf) != CAIRO_STATUS_SUCCESS) goto out;

    shot = gdk_pixbuf_get_from_surface(surf, 0, 0, attr.width, attr.height);
    if (!shot) goto out;

    result = scale_fit(shot, max_w, max_h);

out:
    if (shot)   g_object_unref(shot);
    if (surf)   cairo_surface_destroy(surf);
    if (pixmap) { XFreePixmap(dpy, pixmap); XSync(dpy, False); }
    XSetErrorHandler(prev);
    return result;
}
