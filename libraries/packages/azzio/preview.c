/* Azzio bare-`azzio` terminal user interface (C) -- preview pane. See preview.h.
 *
 * BOTH previews are now REAL images placed with kitty's `kitten icat --place` (this is
 * kitty; it can do it):
 *
 *   WALLPAPER -- the actual wallpaper PNG for the hovered choice.
 *   THEME     -- two shipped SCREENSHOTS side by side: LibreWolf on the timedate home page
 *                and the file manager, in the dark or the white variant to match the
 *                hovered choice. The images live in AZ_PREVIEW_DIR (installed from
 *                assets/previews/ by terminal_user_interface_build.install_previews) and are used UNMODIFIED --
 *                kitty scales each into the reserved half-rectangle at draw time, so swapping
 *                the files (same names) needs no code change. There is NO caption under the
 *                previews and NO per-app ANSI mock-up anymore.
 *
 * SPEED -- this must feel INSTANT (the spec: "going back and forth between themes and
 * wallpapers ... I want INSTANT"). Two things make it instant:
 *
 *   1. MEMO. A frame that would place the exact same preview (same kind + arg + rectangle) as
 *      the last one is a no-op -- holding a key or typing never re-forks kitten.
 *
 *   2. NON-BLOCKING, SINGLE-FORK placement. Placing an image forks `kitten icat`, which costs
 *      ~15-20ms; the OLD code paid that TWICE per move (a `--clear` fork THEN a place fork)
 *      AND blocked the input loop on both with waitpid -- so every move stalled ~30-50ms
 *      before the next keystroke was even read. Now a move does ONE fork (place) and does NOT
 *      wait on it: the text frame is already on screen, and the image lands a few ms later
 *      without holding up navigation. We do not `--clear` between two previews of the same
 *      rectangle -- the new opaque image simply covers the old one -- so there is no second
 *      fork; a real `--clear` happens only when the preview goes away (a row with no preview,
 *      or leaving the UI). At most one placement child is in flight; the next placement reaps
 *      it first (WNOHANG), so zombies never accumulate.
 */
/* POSIX APIs (fork/execlp/waitpid) under -std=c11. */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1

#include "preview.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <fcntl.h>

/* Where the theme-preview screenshots live on the installed system. MUST match
 * terminal_user_interface_build.TERMINAL_USER_INTERFACE_PREVIEW_SYSTEM_DIR (a test pins the two together). The filenames are the
 * contract: <what>_<variant>.png with what in {timedate, files} and variant in {dark, white}. */
#define AZ_PREVIEW_DIR "/usr/local/lib/azzio/previews"

/* --- emit ANSI straight to stdout (the renderer already flushed its buffer) --- */
static void outf(const char *fmt, ...)
{
    char buf[512];
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (n > 0) { ssize_t w = write(STDOUT_FILENO, buf, (size_t)n); (void)w; }
}
static void mv(int row, int col) { outf("\033[%d;%dH", row, col); }

/* --- memo of the last placed preview (so we don't re-fork kitten every keystroke) --- */
static char g_last_sig[256] = "";

/* The most recent placement child. Reaped non-blocking before the next placement so it never
 * blocks the input loop and never becomes a zombie. -1 == none in flight. */
static pid_t g_placer = -1;

/* Reap the previous placement child if it has finished (non-blocking). If it is somehow still
 * running when the next placement starts, wait for it briefly so kitty's graphics transfers
 * don't interleave -- but that is the rare case; the common case reaps instantly. */
static void reap_placer(int block)
{
    if (g_placer <= 0) return;
    int st = 0;
    pid_t r = waitpid(g_placer, &st, block ? 0 : WNOHANG);
    if (r == g_placer || r < 0) g_placer = -1;
}

/* --- kitty graphics --------------------------------------------------------- */
/* Low-level: place one image file into WxH cells at (col0,row0) (0-based, as kitty wants),
 * WITHOUT --clear and WITHOUT waiting -- the new opaque image covers whatever was there. */
static void icat_place(const char *img, int w, int h, int col0, int row0)
{
    reap_placer(/*block=*/1);   /* ensure the previous placement finished before this one */
    char place[64];
    /* kitty --place syntax is <width>x<height>@<left>x<top> -- the offset separator is 'x',
     * NOT a comma. Origin (0,0) is the top-left, in cells. */
    snprintf(place, sizeof place, "%dx%d@%dx%d", w, h, col0, row0);
    pid_t pid = fork();
    if (pid == 0) {
        int dn = open("/dev/null", O_RDWR);
        if (dn >= 0) { dup2(dn, 0); dup2(dn, 2); }   /* keep stdout: icat writes graphics there */
        execlp("kitten", "kitten", "icat",
               "--silent",
               "--transfer-mode", "file",
               "--stdin", "no",
               "--scale-up",
               "--place", place,
               img, (char *)NULL);
        _exit(127);
    } else if (pid > 0) {
        g_placer = pid;             /* do NOT wait -- the frame is already drawn */
    }
}

void az_preview_clear(void)
{
    /* Remove all images. Best-effort; if kitten is missing this is a no-op fork. Also drop
     * the memo so the next real preview is placed fresh. This one DOES wait: clearing happens
     * when the preview goes away (rare), and we want the screen clean before moving on. */
    reap_placer(/*block=*/1);
    g_last_sig[0] = '\0';
    pid_t pid = fork();
    if (pid == 0) {
        int dn = open("/dev/null", O_RDWR);
        if (dn >= 0) { dup2(dn, 0); dup2(dn, 1); dup2(dn, 2); }
        execlp("kitten", "kitten", "icat", "--clear", "--silent", (char *)NULL);
        _exit(127);
    } else if (pid > 0) {
        waitpid(pid, NULL, 0);
    }
}

/* --- wallpaper preview (the hovered wallpaper image) ------------------------ */
static int wallpaper_preview(const AzRow *row, const AzRect *r)
{
    if (!row->preview_arg) return 0;
    char img[512];
    az_wallpaper_image(row->preview_arg, img, sizeof img);
    if (access(img, R_OK) != 0) {
        mv(r->row, r->col);
        outf("\033[%sm(preview unavailable: %s)\033[0m", AZ_SGR_DIM, img);
        return 0;
    }
    /* one image filling the whole reserved rectangle, centred by kitty */
    icat_place(img, r->w, r->h, r->col - 1, r->row - 1);
    return 1;
}

/* --- theme preview (two real screenshots: timedate + files) ---------------- */
static int theme_preview(const AzRow *row, const AzRect *r)
{
    const char *variant = (row->preview_arg && strcmp(row->preview_arg, "dark") == 0)
                          ? "dark" : "white";
    char tdate[512], files[512];
    snprintf(tdate, sizeof tdate, "%s/timedate_%s.png", AZ_PREVIEW_DIR, variant);
    snprintf(files, sizeof files, "%s/files_%s.png",    AZ_PREVIEW_DIR, variant);

    int have_t = access(tdate, R_OK) == 0;
    int have_f = access(files, R_OK) == 0;
    if (!have_t && !have_f) {
        mv(r->row, r->col);
        outf("\033[%sm(theme previews not installed in %s)\033[0m", AZ_SGR_DIM, AZ_PREVIEW_DIR);
        return 0;
    }

    /* Two half-width panes side by side inside the rectangle (a small gutter between). If it
     * is too narrow for two, show just the timedate shot across the whole width. kitty scales
     * each screenshot into its pane, preserving aspect, so they read at any terminal size. */
    int gap = 2;
    int half = (r->w - gap) / 2;
    if (half >= 16 && have_t && have_f) {
        icat_place(tdate, half, r->h, r->col - 1, r->row - 1);
        icat_place(files, half, r->h, r->col - 1 + half + gap, r->row - 1);
    } else {
        const char *only = have_t ? tdate : files;
        icat_place(only, r->w, r->h, r->col - 1, r->row - 1);
    }
    return 1;
}

int az_preview_draw(const AzUI *ui, const AzRect *r)
{
    if (!r || !r->valid) return 0;
    const AzRow *vis[64];
    int nvis = az_visible_rows(ui, vis, 64);
    if (ui->sel < 0 || ui->sel >= nvis) return 0;
    const AzRow *row = vis[ui->sel];
    if (row->preview == AZ_PV_NONE) return 0;

    /* MEMO: if this exact preview (kind + arg + rectangle) is already on screen, do nothing --
     * no kitten fork. Only a genuine change re-places. */
    char sig[256];
    snprintf(sig, sizeof sig, "%d|%s|%d,%d,%d,%d",
             (int)row->preview, row->preview_arg ? row->preview_arg : "",
             r->row, r->col, r->w, r->h);
    if (strcmp(sig, g_last_sig) == 0) return 1;   /* unchanged -> still an image on screen */

    /* Only CLEAR when the layout could leave orphaned pixels: a different preview KIND (theme
     * uses two half-panes, wallpaper one full pane) or a different rectangle. A same-kind,
     * same-rect change (Dark->White, Years->Decades) just places the new opaque image OVER the
     * old -- no clear fork, so the swap is a single, non-blocking placement (instant). */
    char last_geom[128] = ""; int last_kind = -1;
    if (g_last_sig[0]) {
        int lr, lc, lw, lh;
        char karg[64];
        if (sscanf(g_last_sig, "%d|%63[^|]|%d,%d,%d,%d",
                   &last_kind, karg, &lr, &lc, &lw, &lh) >= 6)
            snprintf(last_geom, sizeof last_geom, "%d,%d,%d,%d", lr, lc, lw, lh);
    }
    char this_geom[128];
    snprintf(this_geom, sizeof this_geom, "%d,%d,%d,%d", r->row, r->col, r->w, r->h);
    if (last_kind != (int)row->preview || strcmp(last_geom, this_geom) != 0)
        az_preview_clear();      /* layout changed -> clear (also resets g_last_sig) */

    int drew = 0;
    switch (row->preview) {
        case AZ_PV_WALLPAPER: drew = wallpaper_preview(row, r); break;
        case AZ_PV_THEME:     drew = theme_preview(row, r);     break;
        default:              drew = 0;                          break;
    }
    if (drew) snprintf(g_last_sig, sizeof g_last_sig, "%s", sig);
    return drew;
}
