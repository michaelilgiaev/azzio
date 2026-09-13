/* Azzio -- headless C unit tests for the bare-`azzio` terminal user interface ACTION layer (action.c).
 *
 * action.c runs an apply INSIDE the UI: it captures a command's output and takes a sudo
 * credential via `sudo -S -v`. Two things here are safety-critical and easy to regress, so
 * they are pinned:
 *
 *   1. SIGPIPE RESILIENCE. az_action_authenticate() writes the password into the sudo child's
 *      stdin pipe. If that child has already exited (sudo missing / rejected / a fork-exec
 *      race) the write hits a closed pipe. With SIGPIPE's default disposition that KILLS the
 *      process -- and since a signal death skips atexit(), the terminal would be left in raw +
 *      alt-screen mode (a corrupted shell). main.c installs signal(SIGPIPE, SIG_IGN); we do the
 *      same here and assert that hammering authenticate with sudo unreachable NEVER dies and
 *      always reports failure (returns 0), rather than crashing.
 *
 *   2. CAPTURE + NO DOUBLE-RUN + BOUNDED. az_action_run_capture() must capture stdout AND
 *      stderr, preserve the command's exit status, and run the command EXACTLY ONCE (an earlier
 *      `timeout ... || cmd` fallback would have re-run a normally-failing command -- privileged
 *      actions twice). It also wraps the command in `timeout` so a hang can't freeze the UI.
 *
 * No sudo is actually authenticated (we force the failing path), so this is deterministic on
 * any host.
 */
#define _POSIX_C_SOURCE 200809L

#include "action.h"

#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int failures = 0;
#define CHECK(cond) do { \
    if (!(cond)) { printf("FAIL: %s (line %d)\n", #cond, __LINE__); failures++; } \
} while (0)

/* Authenticating with sudo unreachable must never crash (SIGPIPE ignored) and must fail. */
static void test_authenticate_is_sigpipe_safe_and_fails_without_sudo(void)
{
    signal(SIGPIPE, SIG_IGN);            /* the guard main.c installs at startup */
    char *save = getenv("PATH");
    char saved[4096] = {0};
    if (save) snprintf(saved, sizeof saved, "%s", save);
    setenv("PATH", "", 1);               /* sudo not found -> child _exit(127) without reading */
    int any_success = 0;
    for (int i = 0; i < 500; i++)
        if (az_action_authenticate("password")) any_success = 1;   /* would crash pre-fix */
    /* Reaching here at all means no SIGPIPE death; and with no sudo none can succeed. */
    CHECK(any_success == 0);
    if (saved[0]) setenv("PATH", saved, 1);
}

/* Capture merges stdout+stderr and preserves a clean exit. */
static void test_capture_gets_stdout_and_stderr(void)
{
    char *out = NULL;
    int rc = az_action_run_capture("sh -c 'echo out; echo oops 1>&2'", &out);
    CHECK(rc == 0);
    CHECK(out != NULL);
    CHECK(out && strstr(out, "out") != NULL);
    CHECK(out && strstr(out, "oops") != NULL);    /* stderr captured too */
    free(out);
}

/* A failing command runs EXACTLY once and its non-zero status is preserved (no re-run). */
static void test_capture_does_not_rerun_on_failure(void)
{
    /* A unique temp file counts how many times the command body executed. */
    char tmpl[] = "/tmp/az_action_count_XXXXXX";
    int fd = mkstemp(tmpl);
    if (fd >= 0) close(fd);
    char cmd[256];
    snprintf(cmd, sizeof cmd, "sh -c 'echo x >> %s; exit 4'", tmpl);
    char *out = NULL;
    int rc = az_action_run_capture(cmd, &out);
    free(out);
    CHECK(rc == 4);                                /* the command's own status, not timeout's */
    FILE *f = fopen(tmpl, "r");
    int lines = 0;
    if (f) { int c; while ((c = fgetc(f)) != EOF) if (c == '\n') lines++; fclose(f); }
    CHECK(lines == 1);                             /* ran ONCE, never twice */
    unlink(tmpl);
}

/* az_action_sudo_ok is non-blocking and returns a boolean-ish value (0/1). With sudo missing
 * it must be 0 (no credential), and it must NOT hang. */
static void test_sudo_ok_without_sudo_is_zero(void)
{
    char *save = getenv("PATH");
    char saved[4096] = {0};
    if (save) snprintf(saved, sizeof saved, "%s", save);
    setenv("PATH", "", 1);
    CHECK(az_action_sudo_ok() == 0);
    if (saved[0]) setenv("PATH", saved, 1);
}

int main(void)
{
    test_authenticate_is_sigpipe_safe_and_fails_without_sudo();
    test_capture_gets_stdout_and_stderr();
    test_capture_does_not_rerun_on_failure();
    test_sudo_ok_without_sudo_is_zero();

    if (failures == 0) {
        printf("test_terminal_user_interface_action: all checks passed\n");
        return 0;
    }
    printf("test_terminal_user_interface_action: %d check(s) FAILED\n", failures);
    return 1;
}
