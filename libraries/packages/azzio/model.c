/* Azzio bare-`azzio` terminal user interface (C) -- the menu MODEL + live status probes.
 *
 * This is the C counterpart of the old Python build_menu(): the whole navigable tree as
 * static data (screens -> rows), plus the status probes each row draws. The probes shell
 * out to the SAME tools the command line interface uses (gsettings / nmcli / ufw) or read the pointer files,
 * exactly like the Python status helpers did -- so the UI reflects reality and the two
 * can't drift. Nothing here touches the terminal, so the tests exercise it headless.
 *
 * ACTIONS are shell command lines run against the installed `azzio` command line interface (e.g.
 * "azzio theme --dark"): the UI drives the tested subcommands rather than re-implementing
 * system behaviour. `azzio` is on PATH on the guest; the command runs INSIDE the UI with its
 * output captured (see main.c / action.c), never dropping to the real terminal. `.needs_root`
 * marks the applies that first take a sudo credential; `.show_output` shows their output.
 */
/* POSIX APIs (fork/execvp/pipe/waitpid) under -std=c11. */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1

#include "terminal_user_interface.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>   /* strncasecmp (root-login drop-in scan) */
#include <unistd.h>
#include <sys/wait.h>
#include <fcntl.h>

/* The two shipped wallpapers + their on-disk PNG layout. Kept in lock-step with
 * wallpaper.py (WALLPAPERS_SYSTEM_DIR / WALLPAPER_IMAGE_RES); a test pins the strings. */
#define AZ_WALLPAPERS_DIR "/usr/share/wallpapers"
#define AZ_WALLPAPER_RES  "1672x941"

const char *az_wallpaper_image(const char *id, char *buf, size_t n)
{
    snprintf(buf, n, "%s/%s/contents/images/%s.png",
             AZ_WALLPAPERS_DIR, id, AZ_WALLPAPER_RES);
    return buf;
}

/* --- capture the first stdout line of a command ----------------------------
 * fork/exec (no shell) with stdin from /dev/null and stdout on a pipe; copy up to n-1
 * bytes, stop at the first newline, trim trailing whitespace. Returns the child's exit
 * status (0 == clean). Kept tiny and shell-free so a probe can't hang or be injected. */
int az_capture(const char *const argv[], char *buf, size_t n)
{
    if (n == 0) return -1;
    buf[0] = '\0';
    int pipefd[2];
    if (pipe(pipefd) != 0) return -1;
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return -1; }
    if (pid == 0) {
        /* child: stdin<-/dev/null, stdout->pipe, stderr silenced */
        int devnull = open("/dev/null", O_RDWR);
        if (devnull >= 0) { dup2(devnull, 0); dup2(devnull, 2); }
        dup2(pipefd[1], 1);
        close(pipefd[0]); close(pipefd[1]);
        if (devnull > 2) close(devnull);
        execvp(argv[0], (char *const *)argv);
        _exit(127);
    }
    close(pipefd[1]);
    size_t off = 0;
    char c;
    ssize_t r;
    int done = 0;
    while (!done && (r = read(pipefd[0], &c, 1)) > 0) {
        if (c == '\n') break;
        if (off < n - 1) buf[off++] = c;
        else { /* drain the rest without storing */ }
    }
    (void)done;
    buf[off] = '\0';
    /* drain remaining output so the child never blocks on a full pipe */
    char drain[256];
    while (read(pipefd[0], drain, sizeof drain) > 0) { }
    close(pipefd[0]);
    int status = 0;
    waitpid(pid, &status, 0);
    /* rtrim */
    while (off > 0 && isspace((unsigned char)buf[off - 1])) buf[--off] = '\0';
    if (!WIFEXITED(status)) return -1;
    return WEXITSTATUS(status);
}

/* Like az_capture but keeps the WHOLE output (up to n-1 bytes), newlines and all, so a
 * multi-line report can be scanned with strstr. Same fork/exec/no-shell contract. Needed by
 * the rfkill/bluetooth probes, whose telltale "... blocked: yes" lines are NOT on line 1.
 * Exported (declared in the .h) so the split-out Display probes (model_display.c) share it. */
int az_capture_all(const char *const argv[], char *buf, size_t n)
{
    if (n == 0) return -1;
    buf[0] = '\0';
    int pipefd[2];
    if (pipe(pipefd) != 0) return -1;
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return -1; }
    if (pid == 0) {
        int devnull = open("/dev/null", O_RDWR);
        if (devnull >= 0) { dup2(devnull, 0); dup2(devnull, 2); }
        dup2(pipefd[1], 1);
        close(pipefd[0]); close(pipefd[1]);
        if (devnull > 2) close(devnull);
        execvp(argv[0], (char *const *)argv);
        _exit(127);
    }
    close(pipefd[1]);
    size_t off = 0;
    ssize_t r;
    while (off < n - 1 && (r = read(pipefd[0], buf + off, n - 1 - off)) > 0)
        off += (size_t)r;
    buf[off] = '\0';
    char drain[256];
    while (read(pipefd[0], drain, sizeof drain) > 0) { }   /* keep child unblocked */
    close(pipefd[0]);
    int status = 0;
    waitpid(pid, &status, 0);
    if (!WIFEXITED(status)) return -1;
    return WEXITSTATUS(status);
}

/* --- probe cache (this is what makes navigation feel INSTANT) ---------------
 * Every status probe forks a tool (nmcli/ufw/systemctl/rfkill/gsettings). Called straight
 * from the draw loop, that means several forks PER KEYSTROKE -- the source of the lag. So all
 * probe calls go through az_status_cached(): it memoises each probe's last result by function
 * pointer for a short TTL, so holding a key, typing in the search box, or any redraw that is
 * not a genuine state change re-forks NOTHING. The first draw after the TTL refreshes it.
 *
 * The clock is CLOCK_MONOTONIC (immune to wall-clock jumps). The TTL is deliberately short so
 * the shown status still tracks reality within ~1.5s; an apply also busts the cache outright
 * (az_status_invalidate) so a toggle's effect shows immediately, not after the TTL. */
#include <time.h>

#define AZ_CACHE_TTL_MS 1500
#define AZ_CACHE_SLOTS  16

typedef const char *(*AzProbe)(char *, size_t);
typedef struct { AzProbe fn; long stamp_ms; char val[128]; } AzCacheSlot;
static AzCacheSlot g_cache[AZ_CACHE_SLOTS];

static long az_now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

const char *az_status_cached(const char *(*fn)(char *, size_t), char *buf, size_t n)
{
    if (!fn) { if (n) buf[0] = '\0'; return buf; }
    long now = az_now_ms();
    AzCacheSlot *slot = NULL, *free_slot = NULL;
    for (int i = 0; i < AZ_CACHE_SLOTS; i++) {
        if (g_cache[i].fn == fn) { slot = &g_cache[i]; break; }
        if (!free_slot && g_cache[i].fn == NULL) free_slot = &g_cache[i];
    }
    if (slot && (now - slot->stamp_ms) < AZ_CACHE_TTL_MS) {
        snprintf(buf, n, "%s", slot->val);       /* fresh enough -> no fork */
        return buf;
    }
    /* Miss (or stale): run the probe for real, then remember it. */
    char tmp[128];
    const char *r = fn(tmp, sizeof tmp);
    if (!r) r = "";
    if (!slot) slot = free_slot ? free_slot : &g_cache[0];  /* evict slot 0 if the table is full */
    slot->fn = fn;
    slot->stamp_ms = now;
    snprintf(slot->val, sizeof slot->val, "%s", r);
    snprintf(buf, n, "%s", slot->val);
    return buf;
}

void az_status_invalidate(void)
{
    for (int i = 0; i < AZ_CACHE_SLOTS; i++) { g_cache[i].fn = NULL; g_cache[i].stamp_ms = 0; }
}

/* True if `prog` is somewhere on PATH (mirrors _have()). Exported (declared in the .h) so the
 * split-out Display probes (model_display.c) share the one implementation. */
int az_have(const char *prog)
{
    const char *path = getenv("PATH");
    if (!path) return 0;
    char buf[1024];
    const char *p = path;
    while (*p) {
        const char *colon = strchr(p, ':');
        size_t len = colon ? (size_t)(colon - p) : strlen(p);
        if (len > 0 && len < sizeof buf - 2 - strlen(prog)) {
            memcpy(buf, p, len);
            buf[len] = '/';
            strcpy(buf + len + 1, prog);
            if (access(buf, X_OK) == 0) return 1;
        }
        if (!colon) break;
        p = colon + 1;
    }
    return 0;
}

/* --- status probes ---------------------------------------------------------- */

const char *az_status_theme(char *buf, size_t n)
{
    /* gsettings get org.gnome.desktop.interface color-scheme -> 'prefer-dark' | ...
     * Reported with a Capitalised first letter ("Dark"/"White") -- the spec wants the
     * "Current:" line and the row status to read "Dark", not "dark". */
    const char *argv[] = {"gsettings", "get", "org.gnome.desktop.interface",
                          "color-scheme", NULL};
    char raw[128] = {0};
    if (az_have("gsettings") && az_capture(argv, raw, sizeof raw) == 0) {
        if (strstr(raw, "prefer-dark")) { snprintf(buf, n, "Dark"); return buf; }
        if (strstr(raw, "prefer-light")) { snprintf(buf, n, "White"); return buf; }
    }
    snprintf(buf, n, "Dark");   /* Azzio default */
    return buf;
}

const char *az_status_wallpaper(char *buf, size_t n)
{
    /* Read the pointer file ~/.config/azzio/wallpaper; map to an id, else "custom". */
    const char *home = getenv("HOME");
    char path[512], cur[512] = {0};
    if (home) {
        snprintf(path, sizeof path, "%s/.config/azzio/wallpaper", home);
        FILE *f = fopen(path, "r");
        if (f) {
            if (fgets(cur, sizeof cur, f)) {
                size_t l = strlen(cur);
                while (l > 0 && (cur[l-1] == '\n' || cur[l-1] == ' ')) cur[--l] = '\0';
            }
            fclose(f);
        }
    }
    /* Report WITH the ".png" file type ("years.png"), per the spec -- not a bare "years". */
    const char *ids[] = {"years", "decades"};
    char img[512];
    for (size_t i = 0; i < sizeof ids / sizeof ids[0]; i++) {
        az_wallpaper_image(ids[i], img, sizeof img);
        if (cur[0] && strcmp(cur, img) == 0) { snprintf(buf, n, "%s.png", ids[i]); return buf; }
    }
    snprintf(buf, n, "%s", cur[0] ? "custom" : "years.png");
    return buf;
}

/* Wifi and Wired are ONE-OR-THE-OTHER, never both "on"/"connected" at once (the spec:
 * "if wired is connected then wifi is off, if wifi is connected then wired is disconnected").
 * Both probes read the SAME device table once and decide from a single source of truth: the
 * connected ethernet device wins. So we scan devices for (a) is any ethernet connected and
 * (b) is any wifi connected, then each probe reports its own line in light of the other. */
struct AzNet { int eth_present, eth_conn, wifi_present, wifi_conn; };

static struct AzNet az_net_scan(void)
{
    struct AzNet s = {0};
    const char *argv[] = {"nmcli", "-t", "-f", "TYPE,STATE", "device", NULL};
    char raw[1024] = {0};
    if (az_capture_all(argv, raw, sizeof raw) != 0 || !raw[0]) return s;
    for (char *line = strtok(raw, "\n"); line; line = strtok(NULL, "\n")) {
        if (strncmp(line, "ethernet:", 9) == 0) {
            s.eth_present = 1;
            if (strstr(line, ":connected")) s.eth_conn = 1;
        } else if (strncmp(line, "wifi:", 5) == 0) {
            s.wifi_present = 1;
            if (strstr(line, ":connected")) s.wifi_conn = 1;
        }
    }
    return s;
}

/* Wifi, as the Wifi screen's one "Current:" line. "connected" only when wifi is the ACTIVE
 * link; "off" whenever wired is connected (one-or-the-other) or there is no wifi hardware;
 * otherwise the radio state ("on"/"off"). */
const char *az_status_wifi(char *buf, size_t n)
{
    if (!az_have("nmcli")) { snprintf(buf, n, "unavailable"); return buf; }
    struct AzNet s = az_net_scan();
    if (s.eth_conn) { snprintf(buf, n, "off"); return buf; }   /* wired wins -> wifi off */
    if (s.wifi_conn) { snprintf(buf, n, "connected"); return buf; }
    if (!s.wifi_present) { snprintf(buf, n, "off"); return buf; }
    /* wifi present but not the active link: report the radio switch. */
    const char *argv[] = {"nmcli", "radio", "wifi", NULL};
    char raw[64] = {0};
    if (az_capture(argv, raw, sizeof raw) == 0 && raw[0])
        snprintf(buf, n, "%s", strcmp(raw, "enabled") == 0 ? "on" : "off");
    else
        snprintf(buf, n, "off");
    return buf;
}

/* Wired (ethernet), as the Wired screen's one "Current:" line. "connected" when ethernet is
 * the active link; "disconnected" when wifi is the active link (one-or-the-other) or the
 * device is simply down; "no device" when there is no ethernet at all. */
const char *az_status_wired(char *buf, size_t n)
{
    if (!az_have("nmcli")) { snprintf(buf, n, "unavailable"); return buf; }
    struct AzNet s = az_net_scan();
    if (!s.eth_present) { snprintf(buf, n, "no device"); return buf; }
    snprintf(buf, n, s.eth_conn ? "connected" : "disconnected");
    return buf;
}

const char *az_status_bluetooth(char *buf, size_t n)
{
    /* A plain ON or OFF -- never "present" (present is not a state a user can act on). The
     * default is OFF (the ISO ships bluetooth disabled). Mirrors network.py _bt_state: it is
     * ON only when the service is active AND rfkill has not blocked the radio; anything else
     * (blocked, inactive, or unreadable) reads as OFF. */
    int active = 0;
    if (az_have("systemctl")) {
        const char *argv[] = {"systemctl", "is-active", "bluetooth", NULL};
        char raw[32] = {0};
        az_capture(argv, raw, sizeof raw);        /* "active" only when running */
        active = strcmp(raw, "active") == 0;
    }
    if (az_have("rfkill")) {
        const char *argv[] = {"rfkill", "list", "bluetooth", NULL};
        char raw[512] = {0};
        if (az_capture_all(argv, raw, sizeof raw) == 0 &&
            (strstr(raw, "Soft blocked: yes") || strstr(raw, "Hard blocked: yes"))) {
            snprintf(buf, n, "off");              /* radio blocked -> off regardless */
            return buf;
        }
    }
    snprintf(buf, n, active ? "on" : "off");
    return buf;
}

const char *az_status_airplane(char *buf, size_t n)
{
    /* A plain ON or OFF. Airplane REALLY means "no networking" -- the internet actually drops
     * -- so it is driven by NetworkManager's master switch, not just the radios (a wired VM
     * has no radio to kill). `nmcli networking` prints "enabled"/"disabled"; airplane is ON
     * when it is "disabled". Mirrors network.py _airplane_is_on. rfkill is the fallback. */
    if (az_have("nmcli")) {
        const char *argv[] = {"nmcli", "networking", NULL};
        char raw[64] = {0};
        if (az_capture(argv, raw, sizeof raw) == 0 && raw[0]) {
            snprintf(buf, n, strcmp(raw, "disabled") == 0 ? "on" : "off");
            return buf;
        }
    }
    if (az_have("rfkill")) {
        const char *argv[] = {"rfkill", "list", NULL};
        char raw[2048] = {0};
        if (az_capture_all(argv, raw, sizeof raw) == 0 && strstr(raw, "blocked")) {
            /* on only if at least one radio is listed and none is left unblocked ("no"). */
            snprintf(buf, n, strstr(raw, "blocked: no") ? "off" : "on");
            return buf;
        }
    }
    snprintf(buf, n, "off");
    return buf;
}

const char *az_status_firewall(char *buf, size_t n)
{
    if (!az_have("ufw")) { snprintf(buf, n, "ufw not found"); return buf; }
    /* `sudo -n ufw status` -> "Status: active" (no password: report "needs sudo"). */
    const char *argv[] = {"sudo", "-n", "ufw", "status", NULL};
    char raw[128] = {0};
    int rc = az_capture(argv, raw, sizeof raw);
    if (rc != 0) { snprintf(buf, n, "needs sudo"); return buf; }
    /* first line is "Status: active"/"Status: inactive" */
    const char *colon = strchr(raw, ':');
    if (colon) {
        colon++;
        while (*colon == ' ') colon++;
        snprintf(buf, n, "%s", *colon ? colon : "unknown");
    } else {
        snprintf(buf, n, "unknown");
    }
    return buf;
}

const char *az_status_power(char *buf, size_t n)
{
    /* The Power screen's "Current:" line: whether any azzio power timer is pending.
     * `systemctl list-timers` lists active timers; we look for any azzio-* power timer and
     * report "timer pending" vs "no timer". A plain read (no root). Degrades to "ready". */
    if (!az_have("systemctl")) { snprintf(buf, n, "ready"); return buf; }
    const char *argv[] = {"systemctl", "list-timers", "--all", "--no-legend", NULL};
    char raw[2048] = {0};
    if (az_capture_all(argv, raw, sizeof raw) == 0 &&
        (strstr(raw, "azzio-shutdown.timer") || strstr(raw, "azzio-restart.timer") ||
         strstr(raw, "azzio-sleep.timer"))) {
        snprintf(buf, n, "timer pending");
        return buf;
    }
    snprintf(buf, n, "no timer");
    return buf;
}

/* Path of the toggleable root-login sshd drop-in (baked default-deny; the TUI/CLI flip
 * it). The `00-` prefix sorts FIRST so, with sshd's first-match-wins resolution, reading
 * this one file equals the effective policy in the shipped configuration (nothing sorts
 * before it except the main sshd_config, whose only PermitRootLogin is commented on Arch).
 * Kept as a macro so az_root_login_state() and any test read the same path. */
#define AZ_ROOT_LOGIN_DROPIN "/etc/ssh/sshd_config.d/00-azzio-root-login.conf"

const char *az_root_login_state(void)
{
    /* "allowed" / "denied" -- a PURE read of our sshd_config.d drop-in (world-readable,
     * no root, no fork). Absent file -> "denied" (the shipped default). We scan for the
     * last `PermitRootLogin` line IN THIS FILE and report allowed only for an explicit
     * "yes". Because the file is the FIRST-sorting `00-` drop-in, this equals sshd's
     * effective first-match policy in the shipped config (the Python `azzio network ssh
     * root status` additionally consults `sshd -T` for the effective value in exotic
     * setups). Returned as a static string so callers can embed it in a status line
     * without owning a buffer. AZ_ROOT_LOGIN_DROPIN can be overridden via the env var of
     * the same name so the unit test can point it at a temp fixture (host-independent). */
    const char *path = getenv("AZ_ROOT_LOGIN_DROPIN");
    if (!path || !*path) path = AZ_ROOT_LOGIN_DROPIN;
    FILE *f = fopen(path, "r");
    if (!f) return "denied";
    const char *state = "denied";
    char line[256];
    while (fgets(line, sizeof line, f)) {
        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '#' || *p == '\0' || *p == '\n') continue;
        /* case-insensitive match of the directive keyword */
        if (strncasecmp(p, "PermitRootLogin", 15) != 0) continue;
        char *v = p + 15;
        while (*v == ' ' || *v == '\t') v++;
        state = (strncasecmp(v, "yes", 3) == 0) ? "allowed" : "denied";
    }
    fclose(f);
    return state;
}

const char *az_status_ssh(char *buf, size_t n)
{
    /* The SSH Server screen's "Current:" line: whether sshd is running AND whether root
     * login is allowed (the second half is the drift-proof default-deny state the user
     * asked to see at a glance). `systemctl is-active sshd` prints "active"/"inactive"/
     * "failed" and exits 0 only when active, so we key off the printed word (falling back
     * to the exit code). The root-login half is a pure drop-in read. Degrades gracefully so
     * the cell is never blank. */
    const char *root = az_root_login_state();
    if (!az_have("systemctl")) {
        snprintf(buf, n, "systemctl not found; root login %s", root);
        return buf;
    }
    const char *argv[] = {"systemctl", "is-active", "sshd", NULL};
    char raw[32] = {0};
    az_capture(argv, raw, sizeof raw);   /* is-active exits non-zero when inactive */
    snprintf(buf, n, "sshd %s; root login %s", raw[0] ? raw : "unknown", root);
    return buf;
}

const char *az_status_firewall_policy(char *buf, size_t n)
{
    /* The firewall DEFAULT-policy screen's "Current:" line: the incoming/outgoing default.
     * `ufw status verbose` prints a line like "Default: deny (incoming), allow (outgoing),
     * disabled (routed)". We capture it (via sudo -n; "needs sudo" if no cached credential)
     * and surface just the Default: summary so the user sees the current in/out policy. */
    if (!az_have("ufw")) { snprintf(buf, n, "ufw not found"); return buf; }
    const char *argv[] = {"sudo", "-n", "ufw", "status", "verbose", NULL};
    char raw[512] = {0};
    if (az_capture_all(argv, raw, sizeof raw) != 0) { snprintf(buf, n, "needs sudo"); return buf; }
    /* Find the "Default:" line and copy its remainder. */
    const char *d = strstr(raw, "Default:");
    if (d) {
        d += 8;
        while (*d == ' ') d++;
        char *nl = strchr(d, '\n');
        size_t len = nl ? (size_t)(nl - d) : strlen(d);
        if (len >= n) len = n - 1;
        memcpy(buf, d, len);
        buf[len] = '\0';
        return buf;
    }
    snprintf(buf, n, "unknown");
    return buf;
}

const char *az_status_network(char *buf, size_t n)
{
    /* The top-level Network row says, in plain words, whether the machine can reach the
     * internet -- NOT a pile of radio/firewall jargon. "Online - Connected to Internet" when
     * NetworkManager reports full connectivity, "Offline - No Internet" otherwise. This is the
     * one thing a developer actually cares about at a glance. */
    if (az_have("nmcli")) {
        const char *argv[] = {"nmcli", "networking", "connectivity", NULL};
        char raw[32] = {0};
        if (az_capture(argv, raw, sizeof raw) == 0 && strcmp(raw, "full") == 0) {
            snprintf(buf, n, "Online - Connected to Internet");
            return buf;
        }
    }
    snprintf(buf, n, "Offline - No Internet");
    return buf;
}

const char *az_status_ip(char *buf, size_t n)
{
    /* The IP Address screen's one "Current:" line: the ACTIVE interface's IPv4 method and
     * address, e.g. "wired: manual 192.168.1.50/24" or "wired: dhcp 10.0.2.15/24". This is
     * the read side of `azzio network ip` (static/dynamic), so the terminal user interface
     * and the CLI never disagree. Finds the first CONNECTED device (ethernet preferred, as in
     * az_net_scan's one-or-the-other rule), reads its connection's ipv4.method and its live
     * IP4.ADDRESS[1]. Degrades to "no active connection" (never blank) when nothing is up or
     * nmcli is missing. */
    if (!az_have("nmcli")) { snprintf(buf, n, "unavailable"); return buf; }

    /* Find the connected device + its connection name (DEVICE:TYPE:STATE:CONNECTION). Prefer a
     * connected ethernet; fall back to any other connected device (e.g. wifi). */
    const char *dargv[] = {"nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", NULL};
    char raw[1024] = {0};
    if (az_capture_all(dargv, raw, sizeof raw) != 0 || !raw[0]) {
        snprintf(buf, n, "no active connection");
        return buf;
    }
    char dev[64] = {0}, conn[128] = {0}, kind[32] = {0};
    char fdev[64] = {0}, fconn[128] = {0}, fkind[32] = {0};  /* first non-ethernet fallback */
    for (char *line = strtok(raw, "\n"); line; line = strtok(NULL, "\n")) {
        /* fields are colon-separated; we only need device, type, state, connection. nmcli
         * escapes literal ':' as '\:' but device/type/state never contain one, and we only
         * read the connection name for display, so a plain split is sufficient here. */
        char tmp[256]; snprintf(tmp, sizeof tmp, "%s", line);
        char *d = strtok(tmp, ":");
        char *t = d ? strtok(NULL, ":") : NULL;
        char *st = t ? strtok(NULL, ":") : NULL;
        char *cn = st ? strtok(NULL, "") : NULL;   /* rest is the connection name */
        if (!d || !t || !st) continue;
        if (strcmp(st, "connected") != 0) continue;
        if (strcmp(t, "ethernet") == 0) {
            snprintf(dev, sizeof dev, "%s", d);
            snprintf(kind, sizeof kind, "%s", t);
            snprintf(conn, sizeof conn, "%s", cn ? cn : "");
            break;                                 /* ethernet wins */
        } else if (!fdev[0]) {
            snprintf(fdev, sizeof fdev, "%s", d);
            snprintf(fkind, sizeof fkind, "%s", t);
            snprintf(fconn, sizeof fconn, "%s", cn ? cn : "");
        }
    }
    if (!dev[0] && fdev[0]) {                       /* no ethernet -> use the fallback */
        snprintf(dev, sizeof dev, "%s", fdev);
        snprintf(kind, sizeof kind, "%s", fkind);
        snprintf(conn, sizeof conn, "%s", fconn);
    }
    if (!dev[0]) { snprintf(buf, n, "no active connection"); return buf; }

    /* ipv4.method for the connection (manual/auto); label it dhcp for "auto". */
    const char *label = kind[0] ? (strcmp(kind, "ethernet") == 0 ? "wired" : kind) : "net";
    char method[32] = {0};
    if (conn[0]) {
        const char *margv[] = {"nmcli", "-g", "ipv4.method", "connection", "show", conn, NULL};
        az_capture(margv, method, sizeof method);
    }
    const char *meth = strcmp(method, "manual") == 0 ? "manual"
                     : (method[0] ? "dhcp" : "");

    /* The live IPv4 address on the device (first IP4.ADDRESS). */
    const char *aargv[] = {"nmcli", "-g", "IP4.ADDRESS", "device", "show", dev, NULL};
    char addr[128] = {0};
    az_capture(aargv, addr, sizeof addr);
    /* IP4.ADDRESS can be multi-valued ("a/24 | b/64"); keep the first entry. */
    char *bar = strchr(addr, '|'); if (bar) *bar = '\0';
    char *sp = addr; while (*sp == ' ') sp++;
    char *end = sp + strlen(sp); while (end > sp && (end[-1] == ' ')) *--end = '\0';

    if (meth[0] && sp[0])      snprintf(buf, n, "%s: %s %s", label, meth, sp);
    else if (sp[0])            snprintf(buf, n, "%s: %s", label, sp);
    else if (meth[0])          snprintf(buf, n, "%s: %s", label, meth);
    else                       snprintf(buf, n, "%s: up", label);
    return buf;
}

const char *az_status_machine(char *buf, size_t n)
{
    /* The EFFECTIVE machine type as the command line interface reports it -- honouring any hard override --
     * so the terminal user interface and `azzio machine` can never disagree. `azzio machine` prints
     * "Machine type: PC" / "Machine type: Laptop" on its FIRST line; capture that and keep just
     * the type word after the colon. Degrades to "PC" (the safe default) if azzio is missing
     * or the line is malformed, so the cell is never blank. */
    const char *argv[] = {"azzio", "machine", NULL};
    char raw[128] = {0};
    if (az_have("azzio") && az_capture(argv, raw, sizeof raw) == 0) {
        const char *colon = strchr(raw, ':');
        if (colon) {
            colon++;
            while (*colon == ' ') colon++;
            if (*colon) { snprintf(buf, n, "%s", colon); return buf; }
        }
    }
    snprintf(buf, n, "PC");
    return buf;
}

/* GPU: the vendor summary line `azzio gpu` prints first (e.g. "GPU vendor(s) detected: nvidia"
 * or "GPU: generic ..."). az_capture keeps only that first line. Degrades to "unknown" if azzio
 * is missing, so the cell is never blank -- matching az_status_machine's shape. */
const char *az_status_gpu(char *buf, size_t n)
{
    const char *argv[] = {"azzio", "gpu", NULL};
    char raw[128] = {0};
    if (az_have("azzio") && az_capture(argv, raw, sizeof raw) == 0 && raw[0]) {
        snprintf(buf, n, "%s", raw);
        return buf;
    }
    snprintf(buf, n, "unknown");
    return buf;
}

/* Time & Date: the current system timezone -- `azzio timedate` (no arg) prints it on line 1. */
const char *az_status_timedate(char *buf, size_t n)
{
    const char *argv[] = {"azzio", "timedate", NULL};
    char raw[128] = {0};
    if (az_have("azzio") && az_capture(argv, raw, sizeof raw) == 0 && raw[0]) {
        snprintf(buf, n, "%s", raw);
        return buf;
    }
    snprintf(buf, n, "unknown");
    return buf;
}

/* Language: `azzio language` (no arg) prints "LANG=..." on its first line; show that. */
const char *az_status_language(char *buf, size_t n)
{
    const char *argv[] = {"azzio", "language", NULL};
    char raw[128] = {0};
    if (az_have("azzio") && az_capture(argv, raw, sizeof raw) == 0 && raw[0]) {
        snprintf(buf, n, "%s", raw);
        return buf;
    }
    snprintf(buf, n, "unknown");
    return buf;
}

const char *az_status_volume(char *buf, size_t n)
{
    /* `azzio volume get` prints "<pct>" or "<pct> muted" on its first line. Report a tidy
     * "NN%" (or "NN% (muted)") for the Volume row / its "Current:" line. Degrades to "n/a" if
     * azzio is missing or the read fails, so the cell is never blank. */
    const char *argv[] = {"azzio", "volume", "get", NULL};
    char raw[64] = {0};
    if (az_have("azzio") && az_capture(argv, raw, sizeof raw) == 0 && raw[0]) {
        int pct = atoi(raw);
        if (strstr(raw, "muted")) snprintf(buf, n, "%d%% (muted)", pct);
        else snprintf(buf, n, "%d%%", pct);
        return buf;
    }
    snprintf(buf, n, "n/a");
    return buf;
}

const char *az_status_brightness(char *buf, size_t n)
{
    /* Brightness is a LAPTOP-ONLY control. On a PC there is no backlight, so the row should say
     * so rather than show a number. We ask `azzio machine` (which honours the hard override)
     * whether this is a Laptop; if not, report "not on a PC". On a laptop, `azzio brightness
     * get` prints the percent (or "n/a" if the backlight is unreadable). */
    char mt[32] = {0};
    const char *margv[] = {"azzio", "machine", NULL};
    int is_laptop = 0;
    if (az_have("azzio") && az_capture(margv, mt, sizeof mt) == 0) {
        /* first line: "Machine type: Laptop" / "Machine type: PC" */
        if (strstr(mt, "Laptop")) is_laptop = 1;
    }
    if (!is_laptop) { snprintf(buf, n, "not on a PC"); return buf; }
    const char *argv[] = {"azzio", "brightness", "get", NULL};
    char raw[64] = {0};
    if (az_capture(argv, raw, sizeof raw) == 0 && raw[0]) {
        if (strstr(raw, "n/a")) { snprintf(buf, n, "n/a"); return buf; }
        snprintf(buf, n, "%d%%", atoi(raw));
        return buf;
    }
    snprintf(buf, n, "n/a");
    return buf;
}

const char *az_status_backup(char *buf, size_t n)
{
    /* The opt-in backup COPY targets (`azzio backup --configure` manages them). Both are OFF by
     * default -- `backup` writes its two local archives and nothing else -- so the common case
     * reads "off (local only)". We ask the configurator's own non-interactive status
     * (`azzio backup --configure --status`), which prints (see backup_targets.py):
     *     Backup targets:
     *       USB          off | ON -> <path>
     *       Google Drive off | ON -> <remote>
     * and report which are ON. Reading through the CLI (not the config file directly) keeps this
     * probe agreeing with what `--status` shows on the box. az_capture_all never blocks on stdin. */
    const char *argv[] = {"azzio", "backup", "--configure", "--status", NULL};
    char raw[512] = {0};
    if (az_have("azzio") && az_capture_all(argv, raw, sizeof raw) == 0 && raw[0]) {
        /* An enabled line is "  USB          ON -> ..." / "  Google Drive ON -> ...". Match the
         * label followed by "ON" (the disabled form is the word "off"), scanning each line so a
         * path/remote containing "on" can't cause a false positive. */
        int usb = 0, gdrive = 0;
        char *save = NULL;
        for (char *l = strtok_r(raw, "\n", &save); l; l = strtok_r(NULL, "\n", &save)) {
            char *p = l;
            while (*p == ' ' || *p == '\t') p++;
            if (strncmp(p, "USB", 3) == 0 && strstr(p, "ON")) usb = 1;
            else if (strncmp(p, "Google Drive", 12) == 0 && strstr(p, "ON")) gdrive = 1;
        }
        if (usb && gdrive) { snprintf(buf, n, "USB + Google Drive"); return buf; }
        if (usb)           { snprintf(buf, n, "USB"); return buf; }
        if (gdrive)        { snprintf(buf, n, "Google Drive"); return buf; }
        snprintf(buf, n, "off (local only)");
        return buf;
    }
    /* azzio missing or the read failed: report the DEFAULT (both off), never a blank cell. */
    snprintf(buf, n, "off (local only)");
    return buf;
}

/* Pull the value for `key` out of a `hypervisor --configure --status` dump (lines of the form
 * "key = value"). Writes the trimmed value into out (size on) and returns 1 on a hit, 0 otherwise.
 * The haystack is modified-safe (we scan a copy the caller owns) and matches the WHOLE key before
 * '=' so "ram" never matches inside "disk_size"/other keys. */
static int hv_field(const char *text, const char *key, char *out, size_t on)
{
    size_t kl = strlen(key);
    for (const char *l = text; l && *l; ) {
        const char *eol = strchr(l, '\n');
        size_t len = eol ? (size_t)(eol - l) : strlen(l);
        const char *p = l;
        while (*p == ' ' || *p == '\t') p++;            /* skip leading space */
        if ((size_t)(l + len - p) > kl && strncmp(p, key, kl) == 0) {
            const char *q = p + kl;
            while (*q == ' ' || *q == '\t') q++;
            if (*q == '=') {
                q++;
                while (*q == ' ' || *q == '\t') q++;    /* value start */
                const char *vend = (eol ? eol : l + len);
                while (vend > q && (vend[-1] == ' ' || vend[-1] == '\t' || vend[-1] == '\r'))
                    vend--;
                size_t vl = (size_t)(vend - q);
                if (vl >= on) vl = on - 1;
                memcpy(out, q, vl);
                out[vl] = '\0';
                return 1;
            }
        }
        if (!eol) break;
        l = eol + 1;
    }
    return 0;
}

const char *az_status_hypervisor(char *buf, size_t n)
{
    /* The GLOBAL defaults every NEW `hypervisor install` starts from (the per-directory VM's own
     * hypervisor.cfg still wins for that VM). `hypervisor --configure --status` prints every key as
     * "key = value" (built-in defaults with the user's ~/.config/azzio-hypervisor overrides layered
     * on). We summarise the four a user tunes most -- ram/cpus/disk/network -- into one line.
     * Reading through the CLI (not the file) keeps this agreeing with what --status shows. */
    const char *argv[] = {"hypervisor", "--configure", "--status", NULL};
    char raw[1024] = {0};
    char ram[32], cpus[32], disk[32], net[64];
    if (az_have("hypervisor") && az_capture_all(argv, raw, sizeof raw) == 0 && raw[0] &&
        hv_field(raw, "ram", ram, sizeof ram) &&
        hv_field(raw, "cpus", cpus, sizeof cpus) &&
        hv_field(raw, "disk_size", disk, sizeof disk) &&
        hv_field(raw, "network", net, sizeof net)) {
        snprintf(buf, n, "ram %s | cpus %s | disk %s | net %s", ram, cpus, disk, net);
        return buf;
    }
    /* hypervisor missing or the read failed: report the BUILT-IN defaults, never a blank cell.
     * These mirror configuration._CFG_DEFAULTS (ram 16384, cpus 16, disk 200G, network user). */
    snprintf(buf, n, "ram 16384 | cpus 16 | disk 200G | net user");
    return buf;
}

/* --- Default Applications probes ---------------------------------------------
 * The az_status_da_* probes (each category's live current-handler line) MOVED to
 * model_default_applications.c -- they sit next to az_da_screen() (the runtime candidate
 * resolution) they belong with, and moving them keeps model.c under the per-file size budget.
 * They are declared in terminal_user_interface.h, so model_tree.c's SCREENS[] still references
 * them by name; nothing else here needs them. */

/* --- Display probes ---------------------------------------------------------
 * The Display status probes (summary, global scale, and the inline Resolution/Refresh/
 * Orientation/Monitors values) MOVED to model_display.c to keep model.c under the per-file size
 * budget. They shell out through the shared az_capture/az_capture_all/az_have helpers (exported
 * above, declared in the header) and are declared in terminal_user_interface.h, so model_tree.c's
 * SCREENS[] still references them by name. */

/* --- filter (the search box) ------------------------------------------------ */
static int ci_contains(const char *hay, const char *needle)
{
    if (!needle || !*needle) return 1;
    size_t nl = strlen(needle);
    for (const char *h = hay; *h; h++) {
        size_t i = 0;
        while (i < nl && h[i] &&
               tolower((unsigned char)h[i]) == tolower((unsigned char)needle[i]))
            i++;
        if (i == nl) return 1;
    }
    return 0;
}

int az_row_matches(const AzRow *r, const char *q)
{
    if (!q || !*q) return 1;
    if (ci_contains(r->label, q)) return 1;
    if (r->status) {
        char sb[256];
        const char *s = az_status_cached(r->status, sb, sizeof sb);
        if (s && ci_contains(s, q)) return 1;
    }
    return 0;
}

/* The bash command a row teaches. APPLY -> the command verbatim; PORT -> the command with a
 * "<port>" placeholder (that is exactly what the user would type); SCREEN -> NULL. Returned
 * from a small static buffer for the PORT case (there is one hovered row at a time). */
const char *az_row_command(const AzRow *r)
{
    if (!r) return NULL;
    if (r->kind == AZ_ACT_APPLY) return r->target;
    if (r->kind == AZ_ACT_PORT) {
        static char buf[160];
        snprintf(buf, sizeof buf, "%s <port>", r->target ? r->target : "");
        return buf;
    }
    if (r->kind == AZ_ACT_PROMPT) {
        /* the free-text prompt teaches the command with a "<value>" placeholder (the path /
         * remote the user would type), mirroring the PORT "<port>" convention. */
        static char buf[200];
        snprintf(buf, sizeof buf, "%s <value>", r->target ? r->target : "");
        return buf;
    }
    return NULL;
}

/* The underlying base command (the "Base Command: $ ..." line). r->base verbatim for an APPLY;
 * for a PORT row we tack on the same "<port>" placeholder the wrapper shows (the base ufw line
 * also takes the number); NULL for a SCREEN row or any row that declared no base. */
const char *az_row_base(const AzRow *r)
{
    if (!r || !r->base) return NULL;
    if (r->kind == AZ_ACT_PORT) {
        static char buf[200];
        snprintf(buf, sizeof buf, "%s <port>", r->base);
        return buf;
    }
    if (r->kind == AZ_ACT_PROMPT) {
        static char buf[220];
        snprintf(buf, sizeof buf, "%s <value>", r->base);
        return buf;
    }
    return r->base;
}

