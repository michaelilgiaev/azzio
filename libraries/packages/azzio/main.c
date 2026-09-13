/* Azzio bare-`azzio` terminal user interface (C) -- the driver (raw termios, input loop, actions).
 *
 * This is what `azzio` (no arguments) runs: a full-screen, CENTRED, coloured settings UI
 * for the three things a fresh machine needs -- Theme, Wallpaper, Network. It is C over
 * raw ANSI + termios so it feels INSTANT: a keystroke is a read() + a diff + one write(),
 * never an interpreter round-trip.
 *
 * NAVIGATION (per the spec): arrow keys, WASD, and HJKL all move; Enter opens/applies; Esc
 * (or the movement "back") goes up a screen; `/` focuses the search box; `q`/Ctrl-C quit. The
 * nav keys are uppercased + coloured by the renderer, and ESC is INSTANT (no wait on a bare
 * ESC -- the arrow-sequence peek is fully non-blocking).
 *
 * ACTIONS shell out to the installed `azzio` subcommands (theme/wallpaper/network), but ALWAYS
 * INSIDE the UI: the command's output is CAPTURED (action.c) and shown in a centred results
 * overlay on the alt screen -- we never drop to the real terminal. A privileged apply first
 * takes a sudo credential via a masked in-UI password prompt (cached for the session), so it
 * never blocks on a hidden prompt over a blanked terminal. That is why selecting a setting no
 * longer blacks out the screen and why quitting lands cleanly back at the shell with no leftover
 * command line interface text. So the UI adds navigation, previews and prompts, not new system behaviour.
 *
 * If stdin/stdout is not a terminal, main() prints a short pointer to the subcommands and
 * exits 0 (so `azzio </dev/null` or a pipe never breaks) -- mirroring the old Python UI.
 */
/* POSIX APIs (termios, sigaction, fork, ioctl) under -std=c11. */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1

#include "render.h"
#include "preview.h"
#include "action.h"

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/wait.h>

static struct termios g_orig_termios;
static int g_raw = 0;
static volatile sig_atomic_t g_resized = 0;
static int g_had_image = 0;      /* did the last frame place a kitty image? */

/* --- terminal mode ---------------------------------------------------------- */
/* write a NUL-terminated control string (length computed, so it can never drift). */
static void wr(const char *s) { ssize_t w = write(STDOUT_FILENO, s, strlen(s)); (void)w; }

static void enter_raw(void)
{
    if (g_raw) return;
    struct termios t = g_orig_termios;
    t.c_lflag &= ~(ECHO | ICANON | ISIG | IEXTEN);
    t.c_iflag &= ~(IXON | ICRNL | BRKINT | INPCK | ISTRIP);
    t.c_oflag &= ~(OPOST);
    t.c_cc[VMIN] = 1;
    t.c_cc[VTIME] = 0;
    tcsetattr(STDIN_FILENO, TCSAFLUSH, &t);
    /* Alt screen ON, then WIPE it (2J) and the scrollback (3J) and home the cursor, then
     * hide the cursor. The 2J+3J is what makes launching `azzio` leave NO trace of whatever
     * was on the terminal before -- the previous command line interface output is gone, not just scrolled. */
    wr("\033[?1049h\033[2J\033[3J\033[H\033[?25l");
    g_raw = 1;
}
static void leave_raw(void)
{
    if (!g_raw) return;
    /* Clear any kitty image, wipe the alt screen so nothing flashes as we drop back, SHOW the
     * cursor again, leave the alt screen, restore cooked mode. */
    az_preview_clear();
    wr("\033[2J\033[H\033[?25h\033[?1049l");
    tcsetattr(STDIN_FILENO, TCSAFLUSH, &g_orig_termios);
    g_raw = 0;
}
static void on_exit_restore(void) { leave_raw(); }
static void on_signal(int sig) { leave_raw(); signal(sig, SIG_DFL); raise(sig); }
static void on_winch(int sig) { (void)sig; g_resized = 1; }

/* current terminal size (fallback 80x24) */
static void term_size(int *rows, int *cols)
{
    struct winsize ws;
    if (ioctl(STDOUT_FILENO, TIOCGWINSZ, &ws) == 0 && ws.ws_row && ws.ws_col) {
        *rows = ws.ws_row; *cols = ws.ws_col;
    } else { *rows = 24; *cols = 80; }
}

/* --- input: read one logical key ------------------------------------------- */
enum {
    K_NONE = 0, K_UP = 256, K_DOWN, K_LEFT, K_RIGHT,
    K_ENTER, K_ESC, K_BACKSPACE, K_RESIZE, K_EOF
};

/* Read a key, decoding arrow escape sequences. Returns a K_* code or a byte (>=0). On
 * SIGWINCH mid-read it returns K_RESIZE. */
static int read_key(void)
{
    unsigned char c;
    ssize_t r = read(STDIN_FILENO, &c, 1);
    if (r <= 0) {
        if (errno == EINTR && g_resized) { g_resized = 0; return K_RESIZE; }
        if (r == 0) return K_EOF;    /* stdin closed -> quit (distinct from a bare ESC) */
        return K_NONE;
    }
    if (c == '\r' || c == '\n') return K_ENTER;
    if (c == 127 || c == 8) return K_BACKSPACE;
    if (c == 27) {
        /* Bare ESC vs a CSI/SS3 arrow sequence. A real arrow arrives as one atomic burst
         * ("\033[A"), so its bytes are ALREADY in the input buffer the instant we see the
         * ESC. So we peek NON-BLOCKING (VMIN=0, VTIME=0): if a byte is there it's a sequence,
         * if not it's a genuine ESC and we return immediately. This is what makes "ESC = go
         * back" INSTANT -- the old code armed a 100ms VTIME timer on every ESC, so a bare ESC
         * always paid ~100ms of latency before it registered. */
        unsigned char seq[2];
        struct termios t; tcgetattr(STDIN_FILENO, &t);
        cc_t vmin = t.c_cc[VMIN], vtime = t.c_cc[VTIME];
        t.c_cc[VMIN] = 0; t.c_cc[VTIME] = 0;   /* fully non-blocking peek */
        tcsetattr(STDIN_FILENO, TCSANOW, &t);
        ssize_t n0 = read(STDIN_FILENO, &seq[0], 1);
        int key = K_ESC;
        if (n0 == 1 && (seq[0] == '[' || seq[0] == 'O')) {
            ssize_t n1 = read(STDIN_FILENO, &seq[1], 1);
            if (n1 == 1) {
                switch (seq[1]) {
                    case 'A': key = K_UP; break;
                    case 'B': key = K_DOWN; break;
                    case 'C': key = K_RIGHT; break;
                    case 'D': key = K_LEFT; break;
                    default:  key = K_ESC; break;
                }
            }
        }
        t.c_cc[VMIN] = vmin; t.c_cc[VTIME] = vtime;
        tcsetattr(STDIN_FILENO, TCSANOW, &t);
        return key;
    }
    return c;
}

/* --- live auto-refresh (Default Applications) ------------------------------- */
/* The Default Applications screens resolve their rows LIVE from the installed .desktop files
 * (az_da_screen re-scans on every draw) and each row's "current handler" from mimeapps.list.
 * But the input loop BLOCKS on read_key(), so a change made OUTSIDE the UI -- the user pacman
 * -R's an app in another terminal -- would not show until the next keypress ("I deleted
 * Firefox and it didn't update; I had to exit and re-enter"). So on these screens we wake the
 * loop on a timer (poll below) and, on each idle tick, drop the status cache and redraw, which
 * re-runs the live scan -- the list then updates on its own within ~1s. Only the defaultapps
 * screens opt in (their id starts with "defaultapps"): every other screen keeps blocking, so
 * there is zero idle work / no flicker anywhere else. */
#define AZ_LIVE_REFRESH_MS 1000

static int screen_is_live(const AzUI *ui)
{
    const char *id = ui->stack[ui->depth - 1];
    return id && strncmp(id, "defaultapps", 11) == 0;
}

/* Wait for a keystroke. On a LIVE screen in BROWSE mode, cap the wait at AZ_LIVE_REFRESH_MS and
 * return 1 on timeout (caller refreshes + redraws); otherwise block until input. Returns 0 when
 * input is ready to be read (or on a signal/resize, so the caller loops and re-polls). */
static int wait_for_input_or_refresh(const AzUI *ui)
{
    if (ui->mode != AZ_MODE_BROWSE || !screen_is_live(ui))
        return 0;                       /* block in read_key() as before */
    struct pollfd pfd = { .fd = STDIN_FILENO, .events = POLLIN };
    int r = poll(&pfd, 1, AZ_LIVE_REFRESH_MS);
    if (r == 0) return 1;               /* timed out -> tell the caller to refresh */
    return 0;                           /* input ready (or EINTR): go read it */
}

/* --- run an apply command, entirely INSIDE the UI -------------------------- */
/* NOTHING drops to the real terminal. The command runs with its output CAPTURED (action.c),
 * and we either show a one-line result or, for a listing / on error, the full output in the
 * centred overlay (AZ_MODE_OUTPUT). This is the fix for "selecting a setting turns the screen
 * black", "Firewall goes black / can't configure", and "Q leaves the terminal full of
 * previous commands": no alt-screen exit, no child ever writes to the terminal.
 *
 * A command that `needs_root` requires a sudo credential first. If one isn't already active
 * we stash the command and switch to the masked PASSWORD prompt; run_pending_apply() finishes
 * it once the password validates. So the sudo prompt is a visible in-UI field, never a hidden
 * prompt on a blanked terminal. */
static char g_pending_cmd[768];      /* command awaiting a password before it can run */
static int  g_pending_show;          /* its show_output flag                          */
static int  g_pending_root;          /* whether the pending PROMPT apply needs sudo   */

static void set_output(AzUI *ui, const char *title, char *captured)
{
    free(ui->output);
    ui->output = captured;            /* takes ownership (may be NULL) */
    ui->output_scroll = 0;
    snprintf(ui->output_title, sizeof ui->output_title, "%s", title ? title : "");
}

/* Actually run `cmdline` (credential already ensured) and land the result in the UI. */
static void do_apply(AzUI *ui, const char *cmdline, int show_output)
{
    char *out = NULL;
    int rc = az_action_run_capture(cmdline, &out);
    az_status_invalidate();           /* the apply may have changed state -> refresh now */

    /* Show the full output when the row asked for it (a listing) OR when it failed (so the
     * error is visible, not swallowed). Otherwise a terse one-line result keeps the flow calm. */
    int has_text = out && out[0];
    snprintf(ui->message, sizeof ui->message, "%s",
             rc == 0 ? "Done." : "That reported an error.");
    if (show_output || rc != 0) {
        /* set_output takes ownership of whatever pointer we pass. If the command printed
         * nothing, substitute a short placeholder (and drop the empty capture) so the overlay
         * is never blank. */
        if (has_text) {
            set_output(ui, cmdline, out);
        } else {
            free(out);
            set_output(ui, cmdline,
                       strdup(rc == 0 ? "(done)" : "(the command reported an error)"));
        }
        ui->mode = AZ_MODE_OUTPUT;
    } else {
        free(out);
        ui->mode = AZ_MODE_BROWSE;
    }
}

/* Begin an apply: ensure sudo when needed (else prompt), then run it. */
static void start_apply(AzUI *ui, const char *cmdline, int needs_root, int show_output)
{
    if (needs_root && !az_action_sudo_ok()) {
        /* Need a password first: stash the command and raise the masked prompt. */
        snprintf(g_pending_cmd, sizeof g_pending_cmd, "%s", cmdline);
        g_pending_show = show_output;
        ui->mode = AZ_MODE_PASSWORD;
        ui->prompt = "Enter your password (sudo):";
        ui->input[0] = '\0';
        return;
    }
    do_apply(ui, cmdline, show_output);
}

/* After a password validates, run whatever apply was waiting on it. */
static void run_pending_apply(AzUI *ui)
{
    if (g_pending_cmd[0]) {
        char cmd[768];
        snprintf(cmd, sizeof cmd, "%s", g_pending_cmd);
        g_pending_cmd[0] = '\0';
        do_apply(ui, cmd, g_pending_show);
    }
}

/* --- navigation ------------------------------------------------------------- */
/* Go back one step. From an overlay/prompt (OUTPUT/PORT/PASSWORD) "back" just closes it and
 * returns to the menu (instantly -- there is no command running to wait on). From the menu it
 * clears a live search, else pops one screen off the stack. */
static void go_back(AzUI *ui)
{
    if (ui->mode == AZ_MODE_OUTPUT || ui->mode == AZ_MODE_PORT ||
        ui->mode == AZ_MODE_PROMPT || ui->mode == AZ_MODE_PASSWORD) {
        ui->mode = AZ_MODE_BROWSE;
        ui->input[0] = '\0';
        g_pending_cmd[0] = '\0';
        set_output(ui, "", NULL);
        return;
    }
    if (ui->query[0]) { ui->query[0] = '\0'; ui->sel = 0; return; }
    if (ui->depth > 1) { ui->depth--; ui->sel = 0; ui->message[0] = '\0'; }
}

static void activate(AzUI *ui)
{
    const AzRow *vis[64];
    int nvis = az_visible_rows(ui, vis, 64);
    if (ui->sel < 0 || ui->sel >= nvis) return;
    const AzRow *row = vis[ui->sel];
    if (row->kind == AZ_ACT_SCREEN) {
        if (az_screen_find(row->target) && ui->depth < 15) {
            ui->stack[ui->depth++] = row->target;
            ui->sel = 0; ui->query[0] = '\0'; ui->message[0] = '\0';
        }
    } else if (row->kind == AZ_ACT_PORT) {
        /* Prompt for a port number, then run "<target> <port>" (open/close/delete). We keep
         * the base command in g_pending_cmd so the port handler can append the typed number. */
        snprintf(g_pending_cmd, sizeof g_pending_cmd, "%s", row->target);
        g_pending_show = row->show_output;
        ui->mode = AZ_MODE_PORT;
        ui->prompt = "Port number:";
        ui->input[0] = '\0';
    } else if (row->kind == AZ_ACT_PROMPT) {
        /* Prompt for FREE TEXT (a path / remote name), then run "<target> <value>". Like PORT but
         * the field accepts any printable text (not just digits) and the apply honours the row's
         * needs_root (PORT always sudo's; a PROMPT row -- e.g. the Backup enable rows -- may not).
         * The row carries its own prompt label so each PROMPT row asks for the right thing. */
        snprintf(g_pending_cmd, sizeof g_pending_cmd, "%s", row->target);
        g_pending_show = row->show_output;
        g_pending_root = row->needs_root;
        ui->mode = AZ_MODE_PROMPT;
        ui->prompt = row->prompt ? row->prompt : "Value:";
        ui->input[0] = '\0';
    } else { /* AZ_ACT_APPLY */
        start_apply(ui, row->target, row->needs_root, row->show_output);
    }
}

/* Copy the hovered row's command to the clipboard via xclip. `want_base` picks WHICH command:
 * the underlying base command (x) or the azzio wrapper (c). Sets a one-line result message so
 * the user gets feedback (the copied text, or why nothing was copied). A SCREEN row has no
 * command to copy. */
static void copy_hovered(AzUI *ui, int want_base)
{
    const AzRow *vis[64];
    int nvis = az_visible_rows(ui, vis, 64);
    if (ui->sel < 0 || ui->sel >= nvis) return;
    const AzRow *row = vis[ui->sel];
    const char *cmd = want_base ? az_row_base(row) : az_row_command(row);
    const char *what = want_base ? "base command" : "azzio command";
    if (!cmd || !cmd[0]) {
        snprintf(ui->message, sizeof ui->message, "No %s to copy here.", what);
        return;
    }
    if (az_action_copy_clipboard(cmd))
        snprintf(ui->message, sizeof ui->message, "Copied: %s", cmd);
    else
        snprintf(ui->message, sizeof ui->message, "Copy failed (is xclip installed?).");
}

/* search-box editing */
static void search_key(AzUI *ui, int k)
{
    if (k == K_ENTER || k == K_ESC) { ui->mode = AZ_MODE_BROWSE; ui->searching = 0; return; }
    if (k == K_BACKSPACE) {
        size_t l = strlen(ui->query);
        if (l) ui->query[l - 1] = '\0';
        ui->sel = 0;
        return;
    }
    if (k >= 32 && k < 127) {
        size_t l = strlen(ui->query);
        if (l < sizeof ui->query - 1) { ui->query[l] = (char)k; ui->query[l + 1] = '\0'; }
        ui->sel = 0;
    }
}

/* generic single-line text-input editing into ui->input (shared by PORT + PASSWORD) */
static void input_edit(AzUI *ui, int k, int digits_only)
{
    if (k == K_BACKSPACE) {
        size_t l = strlen(ui->input);
        if (l) ui->input[l - 1] = '\0';
        return;
    }
    if (k >= 32 && k < 127) {
        if (digits_only && !isdigit(k)) return;    /* port field: digits only */
        size_t l = strlen(ui->input);
        if (l < sizeof ui->input - 1) { ui->input[l] = (char)k; ui->input[l + 1] = '\0'; }
    }
}

/* PORT prompt: Enter runs "<base cmd> <port>"; ESC cancels back to the menu. */
static void port_key(AzUI *ui, int k)
{
    if (k == K_ESC) { go_back(ui); return; }
    if (k == K_ENTER) {
        if (!ui->input[0]) { go_back(ui); return; }   /* empty -> cancel */
        char cmd[768];
        snprintf(cmd, sizeof cmd, "%s %s", g_pending_cmd, ui->input);
        g_pending_cmd[0] = '\0';
        int show = g_pending_show;
        ui->input[0] = '\0';
        /* Port applies need root; ensure the credential, then run (may re-prompt password). */
        start_apply(ui, cmd, 1, show);
        return;
    }
    input_edit(ui, k, /*digits_only=*/1);
}

/* PROMPT prompt (free text): Enter runs "<base cmd> <typed text>"; ESC cancels back to the menu.
 * Unlike PORT it accepts any printable text (a path / remote name) and honours the pending row's
 * needs_root (g_pending_root) rather than always sudo-ing -- the Backup enable rows write the
 * user's own config, so they do not need root. */
static void prompt_key(AzUI *ui, int k)
{
    if (k == K_ESC) { go_back(ui); return; }
    if (k == K_ENTER) {
        if (!ui->input[0]) { go_back(ui); return; }   /* empty -> cancel */
        char cmd[768];
        snprintf(cmd, sizeof cmd, "%s %s", g_pending_cmd, ui->input);
        g_pending_cmd[0] = '\0';
        int show = g_pending_show;
        int root = g_pending_root;
        g_pending_root = 0;
        ui->input[0] = '\0';
        start_apply(ui, cmd, root, show);
        return;
    }
    input_edit(ui, k, /*digits_only=*/0);            /* free text: any printable char */
}

/* PASSWORD prompt: Enter validates the typed password with sudo; on success run the pending
 * apply, on failure show a message and let them retry; ESC cancels. Masked on screen. */
static void password_key(AzUI *ui, int k)
{
    if (k == K_ESC) { go_back(ui); return; }
    if (k == K_ENTER) {
        if (az_action_authenticate(ui->input)) {
            ui->input[0] = '\0';
            ui->mode = AZ_MODE_BROWSE;
            run_pending_apply(ui);
        } else {
            ui->input[0] = '\0';
            snprintf(ui->message, sizeof ui->message, "Wrong password -- try again.");
        }
        return;
    }
    input_edit(ui, k, /*digits_only=*/0);
}

/* OUTPUT overlay: paging keys (up/down/j/k) scroll; anything else (Enter/ESC/q handled in the
 * main loop) closes. Returns after adjusting scroll. */
static void output_key(AzUI *ui, int k)
{
    int lines = az_output_line_count(ui);
    int page = az_output_page_rows(ui);
    int maxscroll = lines - page; if (maxscroll < 0) maxscroll = 0;
    switch (k) {
        case K_DOWN: case 'j': if (ui->output_scroll < maxscroll) ui->output_scroll++; break;
        case K_UP:   case 'k': if (ui->output_scroll > 0) ui->output_scroll--; break;
        default: break;
    }
}

/* --- no-tty fallback -------------------------------------------------------- */
static int no_tty_pointer(void)
{
    printf("azzio: no interactive terminal. Use the subcommands instead, e.g.:\n"
           "  azzio theme --dark        set the theme\n"
           "  azzio wallpaper           show / set the wallpaper\n"
           "  azzio network             network status and controls\n"
           "Run `azzio --help` for the full list.\n");
    return 0;
}

int main(void)
{
    if (!isatty(STDIN_FILENO) || !isatty(STDOUT_FILENO))
        return no_tty_pointer();

    if (tcgetattr(STDIN_FILENO, &g_orig_termios) != 0)
        return no_tty_pointer();

    /* SUDO FIX: privileged applies run `azzio <subcmd>`, which internally calls
     * `sudo <tool>`. We CAPTURE those applies with stdin from /dev/null (no hidden
     * prompt) and wrap them in `timeout 30` -- so if the inner sudo ever tried to PROMPT
     * (an expired/absent credential), it would read EOF and stall until timeout, and the
     * user would see only "reported an error". We already secure the sudo credential in
     * the UI (the masked password prompt) BEFORE running a needs_root apply, so the inner
     * sudo should use the cached timestamp. This env var tells the azzio CLI's _sudo to
     * add `-n` (non-interactive): with the credential cached it succeeds silently; if it is
     * somehow missing it FAILS FAST with sudo's own clear message instead of hanging on a
     * dead prompt. Set once here so every apply the UI spawns inherits it. */
    setenv("AZZIO_SUDO_NONINTERACTIVE", "1", 1);

    atexit(on_exit_restore);
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGHUP, on_signal);
    /* IGNORE SIGPIPE. An apply forks a child (e.g. `sudo -S -v` for the password) and we write
     * to its stdin pipe; if that child has already exited (sudo missing, a rejected password,
     * or a fork/exec race) the write hits a closed pipe and would raise SIGPIPE, whose DEFAULT
     * action terminates the process -- and a signal death does NOT run atexit(on_exit_restore),
     * so the terminal would be left in raw + alt-screen mode (a corrupted shell). Ignoring it
     * turns that write into a plain EPIPE the caller handles as "auth failed", never a crash. */
    signal(SIGPIPE, SIG_IGN);
    struct sigaction sa; memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_winch;
    sigaction(SIGWINCH, &sa, NULL);

    AzUI ui; memset(&ui, 0, sizeof ui);
    ui.stack[0] = "main";
    ui.depth = 1;

    enter_raw();

    for (;;) {
        term_size(&ui.rows, &ui.cols);
        /* If the previous frame drew a kitty image and this one won't be at the same
         * place, clearing happens inside kitten icat --clear; but on plain redraws we
         * must clear a lingering image when the hovered row has no preview. */
        AzRect pv;
        az_render(&ui, &pv);
        if (g_had_image && !(pv.valid)) { az_preview_clear(); g_had_image = 0; }
        int drew_img = az_preview_draw(&ui, &pv);
        if (drew_img) g_had_image = 1;
        /* Re-hide + PARK the cursor as the very last thing each frame. `kitten icat` (placing
         * OR clearing a preview) re-enables the cursor and leaves it at home -- that is the
         * "cursor appears at the top-left when I move" artifact. We run icat above, so we must
         * re-assert the hide AFTER it, and park the cursor in the bottom-right corner so that
         * even a terminal ignoring the hide shows nothing blinking over the UI. */
        {
            char park[32];
            int pr = ui.rows > 0 ? ui.rows : 1, pc = ui.cols > 0 ? ui.cols : 1;
            int m = snprintf(park, sizeof park, "\033[%d;%dH\033[?25l", pr, pc);
            if (m > 0) { ssize_t w = write(STDOUT_FILENO, park, (size_t)m); (void)w; }
        }

        /* On a live screen (Default Applications), don't block forever: wake on a timer so an
         * app installed/removed outside the UI shows up on its own. On an idle tick, drop the
         * status cache (so each row's "current handler" re-reads too) and redraw -- the redraw
         * re-runs the live .desktop scan, so the list stays current without a keypress. */
        if (wait_for_input_or_refresh(&ui)) {
            az_status_invalidate();
            continue;
        }

        int k = read_key();
        if (k == K_RESIZE) continue;

        /* EOF (stdin closed) always quits, from any mode -- never spin on it. Ctrl-C likewise
         * quits from the browsing modes; inside a text prompt it is swallowed (not a literal). */
        if (k == K_EOF) { leave_raw(); free(ui.output); return 0; }

        /* Route by MODE. The text prompts (SEARCH/PORT/PASSWORD) consume the key as input;
         * the OUTPUT overlay pages or closes; BROWSE is the menu. Keeping the modes on ui.mode
         * (not a pile of flags) means each key has exactly one meaning per screen. */
        if (ui.mode == AZ_MODE_SEARCH) { ui.searching = 1; search_key(&ui, k); continue; }
        if (ui.mode == AZ_MODE_PORT)     { port_key(&ui, k);     continue; }
        if (ui.mode == AZ_MODE_PROMPT)   { prompt_key(&ui, k);   continue; }
        if (ui.mode == AZ_MODE_PASSWORD) { password_key(&ui, k); continue; }
        if (ui.mode == AZ_MODE_OUTPUT) {
            /* Enter / ESC / q / left-back all CLOSE the overlay and return to the menu
             * (instant -- no command is running). Other keys page the text. */
            if (k == K_ENTER || k == K_ESC || k == 'q' || k == K_LEFT || k == 'h' || k == 'a')
                go_back(&ui);
            else
                output_key(&ui, k);
            continue;
        }

        /* BROWSE. */
        switch (k) {
            case K_UP: case 'k': case 'w':
                if (ui.sel > 0) ui.sel--;
                ui.message[0] = '\0';
                break;
            case K_DOWN: case 'j': case 's': {
                const AzRow *vis[64];
                int nvis = az_visible_rows(&ui, vis, 64);
                if (ui.sel < nvis - 1) ui.sel++;
                ui.message[0] = '\0';
                break;
            }
            case K_RIGHT: case 'l': case 'd': case ' ':
                activate(&ui); break;
            case K_ENTER:
                activate(&ui); break;
            case K_LEFT: case 'h': case 'a':
                go_back(&ui); break;
            case '/':
                ui.mode = AZ_MODE_SEARCH; ui.searching = 1; break;
            case 'c':      /* copy the hovered row's AZZIO WRAPPER command to the clipboard  */
                copy_hovered(&ui, /*want_base=*/0); break;
            case 'x':      /* copy the hovered row's BASE command to the clipboard            */
                copy_hovered(&ui, /*want_base=*/1); break;
            case K_ESC:
                /* ESC is GO BACK -- always, never quit (the spec: "ESC is 'go back' not
                 * 'quit'"). At the root there is nothing to go back to, so it is a no-op;
                 * `q` is the one, always-instant quit key. */
                go_back(&ui);
                break;
            case 'q':
            case 3:      /* Ctrl-C: raw mode has ISIG OFF, so it arrives as the byte 0x03, NOT
                          * as SIGINT -- which is why it used to do nothing. Quit like q. */
                /* q / Ctrl-C quit INSTANTLY from the menu. leave_raw() restores the terminal
                 * cleanly (shows the cursor, drops the alt screen, WIPES scrollback) and we
                 * return straight away -- no lag, and no leftover command line interface text (every apply ran
                 * inside the UI, so there is nothing in the scrollback to leak). */
                leave_raw();
                free(ui.output);
                return 0;
            default: break;
        }
    }
}
