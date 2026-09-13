/* Azzio media OSD (C / Xlib) -- the bottom-middle cyan bar shown when the FN keys change
 * the VOLUME or the screen BRIGHTNESS.
 *
 * WHY C (this replaces the old tkinter osd_indicator.py). The Python/tkinter OSD spawned a
 * BRAND-NEW process+window on every FN press, so every increase/decrease/mute FLICKERED (a
 * window mapped, drew, and a moment later another one mapped on top). This is a single, small
 * Xlib program with NO flicker: the FIRST press maps ONE persistent window; every later press
 * just hands the new level to that already-mapped window over a socket, and it repaints IN
 * PLACE (same window, double-buffered) -- no map/unmap, no second window, no flash.
 *
 * SINGLE INSTANCE (the no-flicker mechanism). On start we try to BIND an abstract-namespace
 * Unix socket "@azzio-osd" (per-user: the name carries the uid). Exactly one process can hold
 * it -- that one becomes the RESIDENT window (the server). Any later invocation FAILS to bind,
 * so it CONNECTs instead, forwards the JSON line it was given on stdin to the resident window,
 * and exits immediately. So `azzio volume up` pressed ten times fast maps one window and sends
 * ten updates to it: the bar just grows, it never re-spawns. The resident window self-closes
 * after a hold+fade, which frees the socket for the next burst.
 *
 * PLACEMENT: BOTTOM-MIDDLE of the primary monitor (Manjaro Cinnamon style), not centered -- a
 * compact chip resting above the bottom edge, horizontally centered. Primary-monitor geometry
 * comes from RandR (the crtc marked primary), falling back to the root size on a single head.
 *
 * LOOK: a CYAN (#06B8FD, the Azzio logo cyan) bar filling to a 0..100% level, a simple DRAWN
 * icon (a speaker for volume -- with an X when muted -- or a sun for brightness), and the
 * percent as compact segmented digits. Everything is drawn with plain X primitives into an
 * off-screen Pixmap that is blitted once per frame (double-buffered => tear-free, flicker-free).
 * No font/Xft dependency: digits are drawn as filled rectangles.
 *
 * FADE + HOLD: the chip holds fully opaque for a beat, then FADES OUT (via the compositor's
 * _NET_WM_WINDOW_OPACITY property, which Cinnamon/muffin honor -- the same thing Tk's -alpha
 * set) and closes. A fresh update cancels an in-flight fade and restarts the hold, so a burst
 * of presses keeps one solid bar up. Any pointer activity also holds it open (see below).
 *
 * MOUSE DRAG: the user can hover the chip and DRAG on the bar to set the level directly; while
 * the pointer is over the chip a bright HIGHLIGHT ring is drawn so it is obvious the drag is
 * live. A drag maps the pointer x to a 0..100% and runs `azzio <kind> set <pct>` (throttled),
 * which changes the real volume/brightness AND feeds this same window the new level -- so the
 * bar tracks the drag. Hovering/dragging keeps the chip from fading until the pointer leaves.
 *
 * INVOCATION (unchanged contract with media.py): launched DETACHED with ONE JSON line on stdin,
 *     {"kind":"volume","percent":72.5,"muted":false}
 *     {"kind":"brightness","percent":50.0}
 * then the pipe is closed. If a resident window already exists this process just forwards that
 * line to it and exits (see single-instance above). No DISPLAY => exit 0 (media.py also guards).
 */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1

#include <X11/Xlib.h>
#include <X11/Xatom.h>
#include <X11/Xutil.h>
#include <X11/Xft/Xft.h>
#include <X11/extensions/Xrandr.h>

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

/* ── Look & feel (the Azzio logo cyan + a dark chip), mirroring the old indicator ──────── */
#define COL_ACCENT   0x06B8FD   /* the logo cyan -- the bar fill + the icon                  */
#define COL_BAR_EMPTY 0x20303A  /* the dim, unfilled bar track                               */
#define COL_BG       0x0A0F14   /* near-black chip background                                */
#define COL_TEXT     0xDEE4EA   /* the percent digits                                        */
#define COL_MUTED    0x78828C   /* the muted grey (muted state)                              */
#define COL_HILITE   0xBFEFFF   /* the drag highlight ring (a brighter cyan-white)           */

/* Geometry -- a compact chip: an icon box on the left, a long bar, the percent on the right. */
#define WIN_W    360
#define WIN_H    84
#define ICON_BOX 56
#define PAD      18
#define BAR_H    16
#define MARGIN_BOTTOM 96   /* gap above the bottom screen edge (Cinnamon-ish resting height) */

/* Timing (ms). Hold LONGER than the old 900ms per the spec ("stay a tiny bit longer"), then a
 * smooth fade. Pointer activity holds it open regardless (see hold logic in the loop). */
#define HOLD_MS      1500
#define FADE_MS      520
#define FADE_STEPS   26
#define MAX_LIFE_MS  8000  /* absolute backstop: the chip can never linger past this          */
#define DRAG_THROTTLE_MS 45 /* min gap between `azzio <kind> set` calls while dragging        */

/* The per-user single-instance socket name (abstract namespace: leading NUL, no filesystem
 * node). The uid is appended so two users on one X server don't collide. */
#define OSD_SOCK_BASE "azzio-osd"

/* ── tiny helpers ───────────────────────────────────────────────────────────────────────── */
static long now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* Build the abstract socket address for THIS user into `sa`; returns the addrlen. The first
 * byte of sun_path is left NUL (abstract namespace). */
static socklen_t osd_sockaddr(struct sockaddr_un *sa)
{
    memset(sa, 0, sizeof *sa);
    sa->sun_family = AF_UNIX;
    /* sun_path[0] stays '\0' -> abstract. Name after it: "azzio-osd.<uid>". */
    int n = snprintf(sa->sun_path + 1, sizeof sa->sun_path - 1, "%s.%u",
                     OSD_SOCK_BASE, (unsigned)getuid());
    if (n < 0) n = 0;
    return (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + (size_t)n);
}

/* ── the OSD state ──────────────────────────────────────────────────────────────────────── */
typedef struct {
    Display *dpy;
    int      screen;
    Window   root, win;
    GC       gc;
    Pixmap   buf;            /* off-screen double buffer (blitted once per frame)            */
    Colormap cmap;
    Visual  *visual;
    Atom     a_opacity;      /* _NET_WM_WINDOW_OPACITY (the fade)                            */

    /* Xft: anti-aliased text (the percent readout), so it looks IDENTICAL to the old tkinter
     * indicator's font-rendered label -- not a blocky hand-drawn glyph. The draw targets the
     * off-screen buffer pixmap so text is part of the same double-buffered frame. */
    XftDraw *xft;
    XftFont *font;           /* the bold percent font                                         */

    int      wx, wy;         /* current window origin (screen coords)                         */
    int      mon_x, mon_y, mon_w, mon_h;   /* primary-monitor geometry                        */

    /* content */
    char     kind[16];       /* "volume" | "brightness"                                       */
    double   percent;        /* 0..100                                                         */
    int      muted;

    /* lifetime / fade */
    long     shown_ms;       /* when the current level was last (re)shown                     */
    long     born_ms;        /* process start (for MAX_LIFE backstop)                          */
    int      fading;         /* mid fade-out?                                                  */
    int      fade_step;
    long     fade_next_ms;

    /* pointer / drag */
    int      hover;          /* pointer inside the chip?                                       */
    int      dragging;
    long     last_drag_set_ms;
} Osd;

static void set_opacity(Osd *o, double a)
{
    if (a < 0) a = 0;
    if (a > 1) a = 1;
    unsigned long v = (unsigned long)(a * 0xFFFFFFFFUL);
    XChangeProperty(o->dpy, o->win, o->a_opacity, XA_CARDINAL, 32,
                    PropModeReplace, (unsigned char *)&v, 1);
}

/* Resolve the PRIMARY monitor rectangle via RandR; fall back to the whole root. */
static void resolve_primary(Osd *o)
{
    o->mon_x = 0; o->mon_y = 0;
    o->mon_w = DisplayWidth(o->dpy, o->screen);
    o->mon_h = DisplayHeight(o->dpy, o->screen);

    int nmon = 0;
    XRRMonitorInfo *mons = XRRGetMonitors(o->dpy, o->root, True, &nmon);
    if (mons && nmon > 0) {
        int pick = 0;
        for (int i = 0; i < nmon; i++) if (mons[i].primary) { pick = i; break; }
        o->mon_x = mons[pick].x;
        o->mon_y = mons[pick].y;
        o->mon_w = mons[pick].width;
        o->mon_h = mons[pick].height;
    }
    if (mons) XRRFreeMonitors(mons);
}

/* The BOTTOM-MIDDLE origin for the chip on the primary monitor. */
static void place_bottom_middle(Osd *o)
{
    o->wx = o->mon_x + (o->mon_w - WIN_W) / 2;
    o->wy = o->mon_y + o->mon_h - WIN_H - MARGIN_BOTTOM;
}

/* Allocate an X pixel for a 0xRRGGBB literal. */
static unsigned long px(Osd *o, unsigned rgb)
{
    XColor c;
    c.red   = (unsigned short)(((rgb >> 16) & 0xFF) * 257);
    c.green = (unsigned short)(((rgb >> 8) & 0xFF) * 257);
    c.blue  = (unsigned short)((rgb & 0xFF) * 257);
    c.flags = DoRed | DoGreen | DoBlue;
    if (!XAllocColor(o->dpy, o->cmap, &c)) return BlackPixel(o->dpy, o->screen);
    return c.pixel;
}

/* ── text (Xft, anti-aliased -- identical quality to the old tkinter label) ──────────────────
 * Allocate an XftColor for a 0xRRGGBB literal (opaque). Caller frees. */
static void xft_color(Osd *o, unsigned rgb, XftColor *out)
{
    XRenderColor rc;
    rc.red   = (unsigned short)(((rgb >> 16) & 0xFF) * 257);
    rc.green = (unsigned short)(((rgb >> 8) & 0xFF) * 257);
    rc.blue  = (unsigned short)((rgb & 0xFF) * 257);
    rc.alpha = 0xFFFF;
    XftColorAllocValue(o->dpy, o->visual, o->cmap, &rc, out);
}

/* Draw `s` so its RIGHT/BOTTOM corner sits at (rx, by) -- i.e. anchor="se", exactly like the
 * old indicator's `create_text(bx1, by0 - 6, anchor="se", ...)`. Anti-aliased via Xft. */
static void draw_text_se(Osd *o, int rx, int by, const char *s, unsigned rgb)
{
    if (!o->font || !o->xft) return;
    XGlyphInfo ext;
    XftTextExtentsUtf8(o->dpy, o->font, (const FcChar8 *)s, (int)strlen(s), &ext);
    int x = rx - ext.xOff;                 /* right-align: pen start so the run ends at rx      */
    int y = by - o->font->descent;         /* bottom-align: baseline above the descent          */
    XftColor col;
    xft_color(o, rgb, &col);
    XftDrawStringUtf8(o->xft, &col, o->font, x, y, (const FcChar8 *)s, (int)strlen(s));
    XftColorFree(o->dpy, o->visual, o->cmap, &col);
}

/* ── icons (simple, cyan primitives) ────────────────────────────────────────────────────── */
static void draw_speaker(Osd *o, int cx, int cy, unsigned long col)
{
    XSetForeground(o->dpy, o->gc, col);
    /* Speaker = a small square body + a triangular cone, drawn as ONE solid shape (the cone
     * base overlaps the body's right edge so there is no seam), EXACTLY matching the old
     * indicator: rect(bx,cy-6 .. bx+8,cy+6) + polygon(bx+8,cy-6, bx+8,cy+6, bx+20,cy+14,
     * bx+20,cy-14). */
    int bx = cx - 16;
    XFillRectangle(o->dpy, o->buf, o->gc, bx, cy - 6, 8, 12);       /* body: x in [bx,bx+8]    */
    XPoint cone[4] = { {(short)(bx + 8),  (short)(cy - 6)},
                       {(short)(bx + 8),  (short)(cy + 6)},
                       {(short)(bx + 20), (short)(cy + 14)},
                       {(short)(bx + 20), (short)(cy - 14)} };
    XFillPolygon(o->dpy, o->buf, o->gc, cone, 4, Convex, CoordModeOrigin);
    if (o->muted) {
        /* an X to the right instead of sound waves */
        int mx = cx + 8;
        XSetLineAttributes(o->dpy, o->gc, 3, LineSolid, CapRound, JoinRound);
        XDrawLine(o->dpy, o->buf, o->gc, mx, cy - 8, mx + 14, cy + 8);
        XDrawLine(o->dpy, o->buf, o->gc, mx, cy + 8, mx + 14, cy - 8);
        XSetLineAttributes(o->dpy, o->gc, 1, LineSolid, CapButt, JoinMiter);
    } else {
        /* Two concentric sound-wave arcs. tkinter's create_arc takes a bounding box
         * (x0,y0,x1,y1); the X11 arc takes (x,y,w,h) -- so the same boxes are:
         *   (cx+2, cy-12)-(cx+18, cy+12) -> x=cx+2 y=cy-12 w=16 h=24
         *   (cx+8, cy-18)-(cx+28, cy+18) -> x=cx+8 y=cy-18 w=20 h=36
         * width 2, from -60 deg spanning 120 deg (X uses 64ths of a degree). */
        XSetLineAttributes(o->dpy, o->gc, 2, LineSolid, CapRound, JoinRound);
        XDrawArc(o->dpy, o->buf, o->gc, cx + 2, cy - 12, 16, 24, -60 * 64, 120 * 64);
        XDrawArc(o->dpy, o->buf, o->gc, cx + 8, cy - 18, 20, 36, -60 * 64, 120 * 64);
        XSetLineAttributes(o->dpy, o->gc, 1, LineSolid, CapButt, JoinMiter);
    }
}

static void draw_sun(Osd *o, int cx, int cy, unsigned long col)
{
    XSetForeground(o->dpy, o->gc, col);
    int r = 8;
    XFillArc(o->dpy, o->buf, o->gc, cx - r, cy - r, 2 * r, 2 * r, 0, 360 * 64);
    XSetLineAttributes(o->dpy, o->gc, 2, LineSolid, CapRound, JoinRound);
    /* Eight rays at fixed 45-degree steps. The unit (cos,sin) for those angles are a small
     * fixed set (0, +-0.7071, +-1), hardcoded so the OSD needs NO libm (no sin/cos call). */
    static const double DX[8] = { 1.0,  0.7071,  0.0, -0.7071, -1.0, -0.7071,  0.0,  0.7071 };
    static const double DY[8] = { 0.0,  0.7071,  1.0,  0.7071,  0.0, -0.7071, -1.0, -0.7071 };
    for (int k = 0; k < 8; k++) {
        int x0 = cx + (int)(DX[k] * (r + 3));
        int y0 = cy + (int)(DY[k] * (r + 3));
        int x1 = cx + (int)(DX[k] * (r + 9));
        int y1 = cy + (int)(DY[k] * (r + 9));
        XDrawLine(o->dpy, o->buf, o->gc, x0, y0, x1, y1);
    }
    XSetLineAttributes(o->dpy, o->gc, 1, LineSolid, CapButt, JoinMiter);
}

/* ── render one frame into the buffer, then blit ────────────────────────────────────────── */
static void render(Osd *o)
{
    unsigned long c_bg     = px(o, COL_BG);
    unsigned long c_accent = px(o, COL_ACCENT);
    unsigned long c_empty  = px(o, COL_BAR_EMPTY);
    unsigned long c_muted  = px(o, COL_MUTED);
    unsigned long c_hi     = px(o, COL_HILITE);

    /* clear */
    XSetForeground(o->dpy, o->gc, c_bg);
    XFillRectangle(o->dpy, o->buf, o->gc, 0, 0, WIN_W, WIN_H);

    int icon_cx = PAD + ICON_BOX / 2;
    int icon_cy = WIN_H / 2;
    unsigned long icon_col = o->muted ? c_muted : c_accent;
    if (strcmp(o->kind, "brightness") == 0)
        draw_sun(o, icon_cx, icon_cy, icon_col);
    else
        draw_speaker(o, icon_cx, icon_cy, icon_col);

    /* the bar: a dim track with a cyan fill to percent */
    int bx0 = PAD + ICON_BOX + PAD;
    int bx1 = WIN_W - PAD;
    int by0 = WIN_H / 2 - BAR_H / 2;
    XSetForeground(o->dpy, o->gc, c_empty);
    XFillRectangle(o->dpy, o->buf, o->gc, bx0, by0, (unsigned)(bx1 - bx0), BAR_H);
    double frac = o->percent / 100.0;
    if (frac < 0) frac = 0;
    if (frac > 1) frac = 1;
    int fillw = (int)((bx1 - bx0) * frac + 0.5);
    if (fillw > 0) {
        XSetForeground(o->dpy, o->gc, o->muted ? c_muted : c_accent);
        XFillRectangle(o->dpy, o->buf, o->gc, bx0, by0, (unsigned)fillw, BAR_H);
    }

    /* the drag HIGHLIGHT: while the pointer hovers the chip, outline the bar with a bright
     * ring and draw a grabber knob at the fill edge so it is obvious the drag is live. */
    if (o->hover || o->dragging) {
        XSetForeground(o->dpy, o->gc, c_hi);
        XSetLineAttributes(o->dpy, o->gc, 2, LineSolid, CapButt, JoinMiter);
        XDrawRectangle(o->dpy, o->buf, o->gc, bx0 - 3, by0 - 3,
                       (unsigned)(bx1 - bx0 + 6), (unsigned)(BAR_H + 6));
        int knobx = bx0 + fillw;
        XFillArc(o->dpy, o->buf, o->gc, knobx - 5, by0 + BAR_H / 2 - 5, 10, 10, 0, 360 * 64);
        XSetLineAttributes(o->dpy, o->gc, 1, LineSolid, CapButt, JoinMiter);
    }

    /* The readout above the right end of the bar -- "muted" when muted, else "NN%" -- drawn
     * anti-aliased and bottom/right-anchored at (bx1, by0 - 6), EXACTLY like the old tkinter
     * indicator's create_text(bx1, by0 - 6, anchor="se", font=(..,11,"bold")). */
    char label[16];
    if (o->muted)
        snprintf(label, sizeof label, "muted");
    else
        snprintf(label, sizeof label, "%d%%", (int)(o->percent + 0.5));
    draw_text_se(o, bx1, by0 - 6, label, o->muted ? COL_MUTED : COL_TEXT);

    /* blit the buffer to the window in one shot (double-buffered => no flicker) */
    XCopyArea(o->dpy, o->buf, o->win, o->gc, 0, 0, WIN_W, WIN_H, 0, 0);
    XFlush(o->dpy);
}

/* ── set the shown state (from a JSON line) and (re)arm the hold ─────────────────────────── */
static void osd_show(Osd *o, const char *kind, double percent, int muted)
{
    snprintf(o->kind, sizeof o->kind, "%s",
             (kind && strcmp(kind, "brightness") == 0) ? "brightness" : "volume");
    if (percent < 0) percent = 0;
    if (percent > 100) percent = 100;
    o->percent = percent;
    o->muted = muted ? 1 : 0;
    o->shown_ms = now_ms();
    o->fading = 0;
    set_opacity(o, 1.0);
    render(o);
}

/* Parse ONE JSON line of the tiny fixed shape {"kind":..,"percent":..,"muted":..}. We do NOT
 * pull in a JSON library: the payload is emitted by media.py in exactly this shape, so a small
 * hand parser is enough (and it degrades safely on anything unexpected). Returns 1 if it looked
 * like a usable message (had a percent or a kind), else 0. */
static int parse_line(const char *line, char *kind, size_t kn, double *percent, int *muted)
{
    int got = 0;
    const char *p;
    if ((p = strstr(line, "\"kind\""))) {
        p = strchr(p, ':');
        if (p) {
            p++;
            while (*p == ' ' || *p == '"') p++;
            size_t i = 0;
            while (*p && *p != '"' && i < kn - 1) kind[i++] = *p++;
            kind[i] = '\0';
            got = 1;
        }
    }
    if ((p = strstr(line, "\"percent\""))) {
        p = strchr(p, ':');
        if (p) { *percent = atof(p + 1); got = 1; }
    }
    if ((p = strstr(line, "\"muted\""))) {
        p = strchr(p, ':');
        if (p) {
            p++;
            while (*p == ' ') p++;
            *muted = (strncmp(p, "true", 4) == 0);
        }
    }
    return got;
}

/* Run `azzio <kind> set <pct>` detached (used by the mouse drag). Fire-and-forget: the child
 * changes the real level and feeds THIS window a fresh line, so the bar tracks the drag. */
static void spawn_set(const char *kind, int pct)
{
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    pid_t k = fork();
    if (k == 0) {
        char sp[8];
        snprintf(sp, sizeof sp, "%d", pct);
        /* detach from the OSD so it never waits on us */
        setsid();
        int devnull = open("/dev/null", O_RDWR);
        if (devnull >= 0) { dup2(devnull, 0); dup2(devnull, 1); dup2(devnull, 2); }
        execlp("azzio", "azzio", kind, "set", sp, (char *)NULL);
        _exit(127);
    }
    /* parent: reap opportunistically later via WNOHANG is overkill; the child is short-lived
     * and re-parented after setsid, so we just don't wait. */
}

/* Map a pointer x (window coords) to a 0..100 percent along the bar track. Clamps to the
 * track ends so a drag past either edge pins to 0 / 100. */
static int x_to_percent(int px_x)
{
    int bx0 = PAD + ICON_BOX + PAD;
    int bx1 = WIN_W - PAD;
    if (px_x <= bx0) return 0;
    if (px_x >= bx1) return 100;
    return (int)((double)(px_x - bx0) / (bx1 - bx0) * 100.0 + 0.5);
}

/* ── the resident window: create + run the event/timer loop ──────────────────────────────── */
static int osd_create(Osd *o)
{
    o->dpy = XOpenDisplay(NULL);
    if (!o->dpy) return -1;
    o->screen = DefaultScreen(o->dpy);
    o->root = RootWindow(o->dpy, o->screen);
    o->cmap = DefaultColormap(o->dpy, o->screen);
    o->visual = DefaultVisual(o->dpy, o->screen);

    resolve_primary(o);
    place_bottom_middle(o);

    XSetWindowAttributes attr;
    attr.override_redirect = True;                 /* unmanaged: no titlebar, no focus steal   */
    attr.background_pixel = px(o, COL_BG);
    attr.event_mask = ExposureMask | ButtonPressMask | ButtonReleaseMask |
                      PointerMotionMask | EnterWindowMask | LeaveWindowMask;
    o->win = XCreateWindow(o->dpy, o->root, o->wx, o->wy, WIN_W, WIN_H, 0,
                           CopyFromParent, InputOutput, CopyFromParent,
                           CWOverrideRedirect | CWBackPixel | CWEventMask, &attr);

    /* Ask the WM/compositor to treat us as an on-screen-display: skip taskbar/pager, keep us
     * above, and (belt and suspenders with override_redirect) never focus us. */
    Atom wtype = XInternAtom(o->dpy, "_NET_WM_WINDOW_TYPE", False);
    Atom wtype_notif = XInternAtom(o->dpy, "_NET_WM_WINDOW_TYPE_NOTIFICATION", False);
    XChangeProperty(o->dpy, o->win, wtype, XA_ATOM, 32, PropModeReplace,
                    (unsigned char *)&wtype_notif, 1);
    Atom state = XInternAtom(o->dpy, "_NET_WM_STATE", False);
    Atom st_above = XInternAtom(o->dpy, "_NET_WM_STATE_ABOVE", False);
    Atom st_skipt = XInternAtom(o->dpy, "_NET_WM_STATE_SKIP_TASKBAR", False);
    Atom st_skipp = XInternAtom(o->dpy, "_NET_WM_STATE_SKIP_PAGER", False);
    Atom states[3] = { st_above, st_skipt, st_skipp };
    XChangeProperty(o->dpy, o->win, state, XA_ATOM, 32, PropModeReplace,
                    (unsigned char *)states, 3);

    o->a_opacity = XInternAtom(o->dpy, "_NET_WM_WINDOW_OPACITY", False);

    o->buf = XCreatePixmap(o->dpy, o->win, WIN_W, WIN_H,
                           (unsigned)DefaultDepth(o->dpy, o->screen));
    o->gc = XCreateGC(o->dpy, o->buf, 0, NULL);

    /* Xft draw targets the OFF-SCREEN buffer so text joins the same double-buffered frame.
     * Font: a bold sans-serif at ~13px -- the anti-aliased look of the old tkinter
     * (TkDefaultFont, 11, "bold") label. Fall back through a couple of patterns so a minimal
     * fontconfig still resolves something rather than drawing nothing. */
    o->xft = XftDrawCreate(o->dpy, o->buf, o->visual, o->cmap);
    o->font = XftFontOpenName(o->dpy, o->screen, "sans-serif:bold:pixelsize=13");
    if (!o->font)
        o->font = XftFontOpenName(o->dpy, o->screen, "DejaVu Sans:bold:pixelsize=13");
    if (!o->font)
        o->font = XftFontOpenName(o->dpy, o->screen, "monospace:bold:pixelsize=13");

    set_opacity(o, 1.0);
    XMapRaised(o->dpy, o->win);
    o->born_ms = now_ms();
    return 0;
}

/* Advance the fade/hold state machine; returns 1 when the window should CLOSE. */
static int tick(Osd *o, long now)
{
    if (now - o->born_ms > MAX_LIFE_MS) return 1;           /* hard backstop                  */
    /* Pointer over the chip => never fade (the user may be about to drag). */
    if (o->hover || o->dragging) { o->fading = 0; o->shown_ms = now; return 0; }
    if (!o->fading) {
        if (now - o->shown_ms >= HOLD_MS) {
            o->fading = 1;
            o->fade_step = 0;
            o->fade_next_ms = now;
        }
        return 0;
    }
    /* fading: step opacity down */
    if (now >= o->fade_next_ms) {
        o->fade_step++;
        double a = 1.0 - (double)o->fade_step / FADE_STEPS;
        if (a <= 0.0) return 1;                            /* fully faded -> close           */
        set_opacity(o, a);
        o->fade_next_ms = now + (FADE_MS / FADE_STEPS);
    }
    return 0;
}

static void handle_x_event(Osd *o, XEvent *ev)
{
    switch (ev->type) {
    case Expose:
        render(o);
        break;
    case EnterNotify:
        o->hover = 1;
        o->shown_ms = now_ms();
        o->fading = 0;
        set_opacity(o, 1.0);
        render(o);
        break;
    case LeaveNotify:
        if (!o->dragging) {
            o->hover = 0;
            o->shown_ms = now_ms();   /* restart the hold from when the pointer left          */
            render(o);
        }
        break;
    case ButtonPress:
        if (ev->xbutton.button == Button1) {
            o->dragging = 1;
            o->hover = 1;
            int pct = x_to_percent(ev->xbutton.x);
            o->percent = pct;
            o->muted = 0;
            o->shown_ms = now_ms();
            o->fading = 0;
            set_opacity(o, 1.0);
            render(o);
            spawn_set(o->kind, pct);
            o->last_drag_set_ms = now_ms();
        }
        break;
    case MotionNotify:
        if (o->dragging) {
            int pct = x_to_percent(ev->xmotion.x);
            o->percent = pct;
            o->shown_ms = now_ms();
            render(o);
            long t = now_ms();
            if (t - o->last_drag_set_ms >= DRAG_THROTTLE_MS) {
                spawn_set(o->kind, pct);
                o->last_drag_set_ms = t;
            }
        }
        break;
    case ButtonRelease:
        if (ev->xbutton.button == Button1 && o->dragging) {
            o->dragging = 0;
            int pct = x_to_percent(ev->xbutton.x);
            spawn_set(o->kind, pct);          /* final, authoritative set                     */
            o->shown_ms = now_ms();
            /* if the pointer is no longer inside, let the hold run out */
            render(o);
        }
        break;
    default:
        break;
    }
}

/* Read all pending newline-delimited messages off the control socket `cfd` (accepting new
 * connections on `sfd`) and apply the last usable one. Non-blocking. */
static void drain_socket(Osd *o, int sfd)
{
    for (;;) {
        int c = accept(sfd, NULL, NULL);
        if (c < 0) break;                         /* no more pending connections              */
        char b[512];
        ssize_t n = read(c, b, sizeof b - 1);
        if (n > 0) {
            b[n] = '\0';
            /* a connection may carry one line; apply it */
            char kind[16] = {0};
            double pct = o->percent;
            int muted = o->muted;
            /* default kind = current kind if the line omits it */
            snprintf(kind, sizeof kind, "%s", o->kind);
            if (parse_line(b, kind, sizeof kind, &pct, &muted))
                osd_show(o, kind, pct, muted);
        }
        close(c);
    }
}

static void osd_run(Osd *o, int sfd)
{
    int xfd = ConnectionNumber(o->dpy);
    struct pollfd pfds[2];
    pfds[0].fd = xfd;   pfds[0].events = POLLIN;
    pfds[1].fd = sfd;   pfds[1].events = POLLIN;

    for (;;) {
        /* Process any queued X events first (XPending drains the library buffer). */
        while (XPending(o->dpy)) {
            XEvent ev;
            XNextEvent(o->dpy, &ev);
            handle_x_event(o, &ev);
        }
        long now = now_ms();
        if (tick(o, now)) break;

        /* Poll with a short timeout so the fade/hold timer advances smoothly. */
        int timeout = o->fading ? (FADE_MS / FADE_STEPS) : 60;
        int pr = poll(pfds, 2, timeout);
        if (pr < 0 && errno != EINTR) break;
        if (pr > 0) {
            if (pfds[1].revents & POLLIN) drain_socket(o, sfd);
            /* X data is handled at the top of the loop via XPending. */
        }
    }
    /* teardown */
    XUnmapWindow(o->dpy, o->win);
    XFlush(o->dpy);
    if (o->font) XftFontClose(o->dpy, o->font);
    if (o->xft) XftDrawDestroy(o->xft);
    XFreePixmap(o->dpy, o->buf);
    XFreeGC(o->dpy, o->gc);
    XDestroyWindow(o->dpy, o->win);
    XCloseDisplay(o->dpy);
}

/* Read the single JSON line media.py wrote to our stdin (blocking briefly). Returns 1 if a
 * line was read into `buf`. */
static int read_stdin_line(char *buf, size_t n)
{
    ssize_t got = 0;
    /* stdin is a pipe the launcher writes one line to then closes; read what's there. */
    size_t off = 0;
    while (off < n - 1) {
        got = read(0, buf + off, n - 1 - off);
        if (got <= 0) break;
        off += (size_t)got;
        if (memchr(buf, '\n', off)) break;
    }
    buf[off] = '\0';
    return off > 0;
}

int main(void)
{
    /* No DISPLAY => nothing to draw. Exit 0 (media.py also guards this). */
    if (!getenv("DISPLAY")) return 0;

    /* Read the payload the launcher handed us on stdin (may be empty if it died). */
    char line[512] = {0};
    int have_line = read_stdin_line(line, sizeof line);

    /* SINGLE INSTANCE. Try to BIND the per-user abstract socket. If bind succeeds we are the
     * resident window; if it fails (EADDRINUSE), a window is already up -- forward our line to
     * it and exit, so the existing bar updates IN PLACE with no flicker. */
    struct sockaddr_un sa;
    socklen_t slen = osd_sockaddr(&sa);

    int sfd = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK, 0);
    if (sfd < 0) return 0;

    if (bind(sfd, (struct sockaddr *)&sa, slen) == 0) {
        /* We are the resident instance. */
        if (listen(sfd, 16) != 0) { close(sfd); return 0; }

        Osd o;
        memset(&o, 0, sizeof o);
        snprintf(o.kind, sizeof o.kind, "volume");
        o.percent = 0;
        if (osd_create(&o) != 0) { close(sfd); return 0; }

        /* apply the initial line (if any); otherwise a bare window shows nothing useful and
         * will fade quickly -- but normally the launcher always sends one line. */
        if (have_line) {
            char kind[16] = "volume";
            double pct = 0;
            int muted = 0;
            if (parse_line(line, kind, sizeof kind, &pct, &muted))
                osd_show(&o, kind, pct, muted);
            else
                osd_show(&o, "volume", 0, 0);
        } else {
            osd_show(&o, "volume", 0, 0);
        }

        osd_run(&o, sfd);
        close(sfd);
        return 0;
    }

    /* Bind failed -> a resident window exists. Connect and forward the line to it. */
    if (errno == EADDRINUSE && have_line) {
        int cfd = socket(AF_UNIX, SOCK_STREAM, 0);
        if (cfd >= 0) {
            if (connect(cfd, (struct sockaddr *)&sa, slen) == 0) {
                size_t len = strlen(line);
                ssize_t w = write(cfd, line, len);
                (void)w;
            }
            close(cfd);
        }
    }
    close(sfd);
    return 0;
}
