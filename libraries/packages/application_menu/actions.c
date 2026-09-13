/* Azzio application menu (C port) -- side-effect actions (launch + power).
 * Port of actions.py. Everything is best-effort and swallows its own errors. */
#include "actions.h"

#include <string.h>
#include <glib.h>

/* Spawn argv detached (new session, stdio to /dev/null). Returns TRUE if
 * spawned. Mirrors actions._run_detached / launch (start_new_session=True). */
static gboolean run_detached(char **argv) {
    if (!argv || !argv[0])
        return FALSE;
    GError *err = NULL;
    gboolean ok = g_spawn_async(
        NULL, argv, NULL,
        G_SPAWN_SEARCH_PATH | G_SPAWN_STDOUT_TO_DEV_NULL |
        G_SPAWN_STDERR_TO_DEV_NULL | G_SPAWN_STDIN_FROM_DEV_NULL,
        NULL, NULL, NULL, &err);
    if (err) g_error_free(err);
    return ok;
}

/* Run the first command whose binary exists on PATH. `cmds` is a NULL-terminated
 * array of NULL-terminated argv arrays. */
static void first_available(char ***cmds) {
    for (int i = 0; cmds[i]; i++) {
        char **argv = cmds[i];
        if (!argv[0]) continue;
        char *found = g_find_program_in_path(argv[0]);
        if (found) {
            g_free(found);
            if (run_detached(argv))
                return;
        }
    }
}

void az_launch(char **argv) {
    if (!argv || !argv[0])
        return;
    /* Prefix with setsid so the child fully reparents (survives menu close),
     * matching actions.launch. */
    char *setsid = g_find_program_in_path("setsid");
    if (setsid) {
        int n = (int)g_strv_length(argv);
        char **cmd = g_new0(char *, n + 2);
        cmd[0] = g_strdup("setsid");
        for (int i = 0; i < n; i++)
            cmd[i + 1] = g_strdup(argv[i]);
        run_detached(cmd);
        g_strfreev(cmd);
        g_free(setsid);
    } else {
        run_detached(argv);
    }
}

void az_lock_session(void) {
    const char *sid = g_getenv("XDG_SESSION_ID");
    char *lock_with_sid[] = { "loginctl", "lock-session", (char *)sid, NULL };
    char *lock_no_sid[]   = { "loginctl", "lock-session", NULL };
    char *lock_all[]      = { "loginctl", "lock-sessions", NULL };
    char *xdg[]           = { "xdg-screensaver", "lock", NULL };
    char **cmds[5];
    int i = 0;
    if (sid && sid[0]) cmds[i++] = lock_with_sid;
    else               cmds[i++] = lock_no_sid;
    cmds[i++] = lock_all;
    cmds[i++] = xdg;
    cmds[i] = NULL;
    first_available(cmds);
}

void az_suspend(void) {
    char *a[] = { "systemctl", "suspend", NULL };
    char *b[] = { "loginctl", "suspend", NULL };
    char **cmds[] = { a, b, NULL };
    first_available(cmds);
}

void az_reboot(void) {
    char *a[] = { "systemctl", "reboot", NULL };
    char *b[] = { "loginctl", "reboot", NULL };
    char **cmds[] = { a, b, NULL };
    first_available(cmds);
}

void az_poweroff(void) {
    char *a[] = { "systemctl", "poweroff", NULL };
    char *b[] = { "loginctl", "poweroff", NULL };
    char **cmds[] = { a, b, NULL };
    first_available(cmds);
}
