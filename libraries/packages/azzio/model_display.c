/* Azzio bare-`azzio` terminal user interface (C) -- the DISPLAY status probes.
 *
 * Split out of model.c (which grew past the per-file size budget) so each file stays under it,
 * mirroring the model_tree.c / model_default_applications.c splits. This TU owns ONLY the Display
 * screen's live-state probes: the summary (resolution @ scale), the global scale, and the inline
 * per-row Resolution/Refresh/Orientation/Monitors values -- all read from `azzio display` /
 * `xrandr`. They shell out through the shared az_capture/az_capture_all helpers and az_have (all
 * exported from model.c, declared in terminal_user_interface.h), so no probe implementation is
 * duplicated. Each probe degrades to a readable word so a cell is never blank (e.g. a bare VM
 * without xrandr). Declared in the header, so model_tree.c's SCREENS[] references them by name.
 */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1

#include "terminal_user_interface.h"

#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/wait.h>

/* --- Display probes ---------------------------------------------------------
 * The Display screen and its scale chooser show live state read from `azzio display`. The
 * summary probe reports the current resolution + scale at a glance; the scale probe reports the
 * global UI scale. Both degrade to a readable word so a cell is never blank. */
const char *az_status_display_scale(char *buf, size_t n)
{
    /* `azzio display scale get` -> "Global scale: 1.35" on its first line. Report "1.35x". */
    const char *argv[] = {"azzio", "display", "scale", "get", NULL};
    char raw[128] = {0};
    if (az_have("azzio") && az_capture(argv, raw, sizeof raw) == 0) {
        const char *colon = strchr(raw, ':');
        if (colon) {
            colon++;
            while (*colon == ' ') colon++;
            if (*colon) { snprintf(buf, n, "%sx", colon); return buf; }
        }
    }
    snprintf(buf, n, "1.35x");
    return buf;
}

const char *az_status_display(char *buf, size_t n)
{
    /* A one-line summary: the primary output's current resolution (from xrandr, the line marked
     * with a '*') and the global scale. Falls back to just the scale if xrandr is unreadable. */
    char scalebuf[64] = {0};
    az_status_display_scale(scalebuf, sizeof scalebuf);
    if (az_have("xrandr")) {
        /* grab the current mode: xrandr marks the active mode with a trailing '*'. Fork xrandr
         * and scan its output for the first "  <WxH> ... *" line (az_capture is first-line only,
         * so this reads the pipe directly). */
        const char *argv[] = {"xrandr", "--query", NULL};
        char res[32] = {0};
        char line[512];
        int pipefd[2];
        if (pipe(pipefd) == 0) {
            pid_t pid = fork();
            if (pid == 0) {
                int dn = open("/dev/null", O_RDWR);
                if (dn >= 0) { dup2(dn, 0); dup2(dn, 2); }
                dup2(pipefd[1], 1); close(pipefd[0]); close(pipefd[1]);
                if (dn > 2) close(dn);
                execvp(argv[0], (char *const *)argv);
                _exit(127);
            }
            close(pipefd[1]);
            FILE *f = fdopen(pipefd[0], "r");
            if (f) {
                while (fgets(line, sizeof line, f)) {
                    if (strchr(line, '*')) {
                        /* first token on a mode line is the resolution */
                        char *p = line;
                        while (*p == ' ' || *p == '\t') p++;
                        int i = 0;
                        while (p[i] && p[i] != ' ' && p[i] != '\t' && i < (int)sizeof res - 1) {
                            res[i] = p[i]; i++;
                        }
                        res[i] = '\0';
                        break;
                    }
                }
                fclose(f);
            } else {
                close(pipefd[0]);
            }
            int st; waitpid(pid, &st, 0); (void)st;
        }
        if (res[0]) { snprintf(buf, n, "%s @ %s", res, scalebuf); return buf; }
    }
    snprintf(buf, n, "scale %s", scalebuf);
    return buf;
}

/* The INLINE per-row Display probes. The Display screen's top "Current:" line was removed (the
 * user: "just fucking remove that 'Current: scale 1.35x' label ... add current to each line"),
 * so each row now carries its OWN current value: Global Scale keeps az_status_display_scale;
 * Resolution/Refresh/Orientation report from xrandr; Monitors reports the connected-output
 * count. All read `xrandr --query` ONCE via az_capture_all and scan it (cheap; memoised by the
 * 1.5s probe cache and only run while the Display screen is on-screen). Each degrades to a
 * readable word so a cell is never blank when xrandr is missing (e.g. a bare VM). */

/* Fill `line` (size ln) with the FIRST active xrandr mode line (the one marked with a trailing
 * '*'), or leave it empty. Returns 1 if found. Shared by the resolution/refresh probes. */
static int az_xrandr_active_mode_line(char *line, size_t ln)
{
    line[0] = '\0';
    if (!az_have("xrandr")) return 0;
    const char *argv[] = {"xrandr", "--query", NULL};
    char raw[4096] = {0};
    if (az_capture_all(argv, raw, sizeof raw) != 0 || !raw[0]) return 0;
    char *save = NULL;
    for (char *l = strtok_r(raw, "\n", &save); l; l = strtok_r(NULL, "\n", &save)) {
        if (strchr(l, '*')) { snprintf(line, ln, "%s", l); return 1; }
    }
    return 0;
}

const char *az_status_display_resolution(char *buf, size_t n)
{
    /* The active mode's resolution: the first token on the '*'-marked xrandr line (e.g. from
     * "   1920x1080     60.00*+" -> "1920x1080"). */
    char line[512];
    if (az_xrandr_active_mode_line(line, sizeof line)) {
        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        int i = 0;
        while (p[i] && p[i] != ' ' && p[i] != '\t' && i < (int)n - 1) { buf[i] = p[i]; i++; }
        buf[i] = '\0';
        if (buf[0]) return buf;
    }
    snprintf(buf, n, "(unknown)");
    return buf;
}

const char *az_status_display_refresh(char *buf, size_t n)
{
    /* The active refresh rate: on the '*'-marked xrandr line the rate is the numeric column
     * carrying the '*' (e.g. "1920x1080  60.00*+  75.00" -> "60 Hz"). Scan tokens for the one
     * containing '*' and print its integer part with " Hz". */
    char line[512];
    if (az_xrandr_active_mode_line(line, sizeof line)) {
        char *save = NULL;
        for (char *t = strtok_r(line, " \t", &save); t; t = strtok_r(NULL, " \t", &save)) {
            if (strchr(t, '*')) {
                int hz = 0;
                for (const char *c = t; *c && *c != '.' && *c != '*'; c++)
                    if (*c >= '0' && *c <= '9') hz = hz * 10 + (*c - '0');
                if (hz > 0) { snprintf(buf, n, "%d Hz", hz); return buf; }
            }
        }
    }
    snprintf(buf, n, "(unknown)");
    return buf;
}

const char *az_status_display_orientation(char *buf, size_t n)
{
    /* The primary output's rotation. xrandr prints it on the "<output> connected [primary]
     * WxH+X+Y (rotation) ..." line: the rotation word (normal/left/right/inverted) sits right
     * before the parenthesised capabilities list. Find the connected-primary line (fall back to
     * the first connected line) and read the orientation token. */
    if (az_have("xrandr")) {
        const char *argv[] = {"xrandr", "--query", NULL};
        char raw[8192] = {0};
        if (az_capture_all(argv, raw, sizeof raw) == 0 && raw[0]) {
            char *save = NULL, *chosen = NULL, *first = NULL;
            for (char *l = strtok_r(raw, "\n", &save); l; l = strtok_r(NULL, "\n", &save)) {
                if (strstr(l, " connected")) {
                    if (!first) first = l;
                    if (strstr(l, "primary")) { chosen = l; break; }
                }
            }
            if (!chosen) chosen = first;
            if (chosen) {
                /* orientation is whichever of these words appears on the line (inverted before
                 * "normal" would misread, so test the specific rotations first). */
                const char *words[] = {"left", "right", "inverted", "normal"};
                for (size_t i = 0; i < sizeof words / sizeof words[0]; i++) {
                    if (strstr(chosen, words[i])) { snprintf(buf, n, "%s", words[i]); return buf; }
                }
                snprintf(buf, n, "normal");   /* connected but no explicit token -> normal */
                return buf;
            }
        }
    }
    snprintf(buf, n, "(unknown)");
    return buf;
}

const char *az_status_display_monitors(char *buf, size_t n)
{
    /* How many outputs are connected (e.g. "1 connected" / "2 connected"). Count the
     * " connected" lines in xrandr --query. */
    if (az_have("xrandr")) {
        const char *argv[] = {"xrandr", "--query", NULL};
        char raw[8192] = {0};
        if (az_capture_all(argv, raw, sizeof raw) == 0 && raw[0]) {
            int count = 0;
            char *save = NULL;
            for (char *l = strtok_r(raw, "\n", &save); l; l = strtok_r(NULL, "\n", &save))
                if (strstr(l, " connected")) count++;
            snprintf(buf, n, "%d connected", count);
            return buf;
        }
    }
    snprintf(buf, n, "(unknown)");
    return buf;
}
