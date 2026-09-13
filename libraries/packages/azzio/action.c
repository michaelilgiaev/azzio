/* Azzio bare-`azzio` terminal user interface (C) -- apply execution + sudo credential. See action.h. */
/* POSIX APIs (fork/execvp/pipe/waitpid) under -std=c11. */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1

#include "action.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <fcntl.h>

/* --- sudo credential -------------------------------------------------------- */

int az_action_sudo_ok(void)
{
    /* `sudo -n -v`: refresh/validate the timestamp WITHOUT prompting. rc 0 means a
     * credential is already usable (passwordless sudo, or a still-valid timestamp). */
    pid_t pid = fork();
    if (pid < 0) return 0;
    if (pid == 0) {
        int dn = open("/dev/null", O_RDWR);
        if (dn >= 0) { dup2(dn, 0); dup2(dn, 1); dup2(dn, 2); if (dn > 2) close(dn); }
        execlp("sudo", "sudo", "-n", "-v", (char *)NULL);
        _exit(127);
    }
    int st = 0;
    waitpid(pid, &st, 0);
    return WIFEXITED(st) && WEXITSTATUS(st) == 0;
}

int az_action_authenticate(const char *password)
{
    if (!password) return 0;
    /* `sudo -S -v`: read the password from stdin and validate it, refreshing the timestamp so
     * subsequent `sudo` calls in this session run without prompting. -k first so a stale
     * timestamp can't mask a wrong password. */
    int pipefd[2];
    if (pipe(pipefd) != 0) return 0;
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return 0; }
    if (pid == 0) {
        dup2(pipefd[0], 0);
        int dn = open("/dev/null", O_WRONLY);
        if (dn >= 0) { dup2(dn, 1); dup2(dn, 2); if (dn > 2) close(dn); }
        close(pipefd[0]); close(pipefd[1]);
        /* -p '' silences the prompt text (we drew our own); -S reads stdin; -v validates. */
        execlp("sudo", "sudo", "-S", "-k", "-p", "", "-v", (char *)NULL);
        _exit(127);
    }
    close(pipefd[0]);
    /* Feed the password + newline, then close so sudo sees EOF. If the child already exited
     * (sudo missing / exec race) the write gets EPIPE; SIGPIPE is ignored process-wide (main.c),
     * so this returns -1 instead of killing us -- we just fall through and report failure from
     * the child's non-zero exit. We do not treat a short write as success. */
    size_t len = strlen(password);
    ssize_t w1 = write(pipefd[1], password, len);
    ssize_t w2 = (w1 >= 0) ? write(pipefd[1], "\n", 1) : -1;
    (void)w1; (void)w2;
    close(pipefd[1]);
    int st = 0;
    waitpid(pid, &st, 0);
    /* Success ONLY when sudo itself exited 0 (the credential was accepted). A write failure
     * leaves the child to exit non-zero, so this returns 0 (auth failed), never a crash. */
    return WIFEXITED(st) && WEXITSTATUS(st) == 0;
}

/* --- run a command, capturing stdout+stderr -------------------------------- */

int az_action_run_capture(const char *cmdline, char **out)
{
    if (out) *out = NULL;
    int pipefd[2];
    if (pipe(pipefd) != 0) return -1;
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return -1; }
    if (pid == 0) {
        int dn = open("/dev/null", O_RDONLY);
        if (dn >= 0) { dup2(dn, 0); if (dn > 2) close(dn); }   /* stdin from /dev/null */
        dup2(pipefd[1], 1);                                    /* stdout -> pipe       */
        dup2(pipefd[1], 2);                                    /* stderr -> same pipe  */
        close(pipefd[0]); close(pipefd[1]);
        /* BOUND every apply with `timeout` so a stuck backend (a hung nmcli/ufw) can never
         * freeze the whole UI -- the read loop below blocks until the child closes the pipe, so
         * an unbounded child would hang navigation with no way to cancel. 30s is generous (a
         * wifi/bluetooth scan finishes well within it) but caps a true hang. `timeout` is
         * coreutils, present on every Azzio (Arch) system. stderr is already merged into the
         * pipe by the dup2 above, so no `2>&1` is needed. We do NOT `exec` it (exec would
         * replace the shell and mis-handle any compound command) and add NO `|| cmd` fallback
         * (that would re-run the command on a normal non-zero exit -- privileged actions twice).
         * The model's apply commands are simple `azzio ...` invocations, so `timeout 30 <cmd>`
         * wraps the whole command cleanly. */
        char wrapped[900];
        int n = snprintf(wrapped, sizeof wrapped, "timeout 30 %s", cmdline);
        if (n > 0 && (size_t)n < sizeof wrapped)
            execl("/bin/sh", "sh", "-c", wrapped, (char *)NULL);
        execl("/bin/sh", "sh", "-c", cmdline, (char *)NULL);   /* only if cmd too long to wrap */
        _exit(127);
    }
    close(pipefd[1]);
    /* Read the whole output into a growable buffer. */
    size_t cap = 4096, len = 0;
    char *buf = malloc(cap);
    if (buf) {
        ssize_t r;
        char tmp[1024];
        while ((r = read(pipefd[0], tmp, sizeof tmp)) > 0) {
            if (len + (size_t)r + 1 > cap) {
                while (len + (size_t)r + 1 > cap) cap *= 2;
                char *nb = realloc(buf, cap);
                if (!nb) { free(buf); buf = NULL; break; }
                buf = nb;
            }
            memcpy(buf + len, tmp, (size_t)r);
            len += (size_t)r;
        }
        if (buf) buf[len] = '\0';
    } else {
        /* Out of memory: still drain so the child doesn't block, just discard. */
        char tmp[1024];
        while (read(pipefd[0], tmp, sizeof tmp) > 0) { }
    }
    close(pipefd[0]);
    int st = 0;
    waitpid(pid, &st, 0);
    if (out) *out = buf;
    else free(buf);
    if (!WIFEXITED(st)) return -1;
    return WEXITSTATUS(st);
}

/* --- clipboard (xclip) ------------------------------------------------------ */

int az_action_copy_clipboard(const char *text)
{
    if (!text) return 0;
    int pipefd[2];
    if (pipe(pipefd) != 0) return 0;
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return 0; }
    if (pid == 0) {
        /* child: stdin from the pipe; stdout/stderr silenced. xclip reads the selection data
         * from stdin, then (by default) forks a background server to hold the CLIPBOARD and the
         * foreground process exits -- so the waitpid below returns promptly. */
        dup2(pipefd[0], 0);
        int dn = open("/dev/null", O_WRONLY);
        if (dn >= 0) { dup2(dn, 1); dup2(dn, 2); if (dn > 2) close(dn); }
        close(pipefd[0]); close(pipefd[1]);
        execlp("xclip", "xclip", "-selection", "clipboard", (char *)NULL);
        _exit(127);   /* xclip missing */
    }
    close(pipefd[0]);
    /* Feed the text (no trailing newline -- it is a single command line). If the child already
     * exited (xclip missing / exec race) this hits EPIPE; SIGPIPE is ignored process-wide
     * (main.c), so we get -1 here and fall through to report failure from the child's exit. */
    size_t len = strlen(text);
    ssize_t w = len ? write(pipefd[1], text, len) : 0;
    (void)w;
    close(pipefd[1]);
    int st = 0;
    waitpid(pid, &st, 0);
    return WIFEXITED(st) && WEXITSTATUS(st) == 0;
}
