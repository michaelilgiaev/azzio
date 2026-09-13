/* Azzio bare-`azzio` terminal user interface (C) -- running the APPLY actions INSIDE the UI.
 *
 * Every menu apply (theme/wallpaper/network/firewall) runs through here. The whole point is
 * that NOTHING drops to the real terminal anymore: the command runs with its stdout+stderr
 * CAPTURED into a heap buffer, so the caller can show the result in a centred overlay on the
 * alt screen. That is the fix for the three linked reports -- "selecting a setting turns the
 * screen black", "Firewall goes black / I can't configure it", and "Q leaves the terminal
 * full of previous commands": the terminal is never blacked out or polluted because we never
 * leave the alt screen and never let a child write to it.
 *
 * SUDO WITHOUT A BLACK SCREEN. The network/firewall applies drive privileged tools (ufw,
 * nmcli, rfkill, systemctl). On the installed system sudo wants a password, and a hidden
 * password prompt on a blanked terminal is exactly the bug. So the credential is taken by an
 * in-UI masked prompt and cached for the session with `sudo -v`; az_action_sudo_ok() reports
 * whether a credential is already active (so the UI only prompts when it must), and
 * az_action_authenticate() validates a typed password. Applies then run under a plain `sudo`
 * that finds the cached credential -- no prompt, no tty, no block.
 */
#ifndef AZ_ACTION_H
#define AZ_ACTION_H

#include <stddef.h>

/* True if a sudo credential is already usable WITHOUT a password (either sudo is passwordless
 * here, or a timestamp is still valid from a recent az_action_authenticate). Non-blocking:
 * runs `sudo -n -v`. Lets the UI skip the password prompt when it isn't needed. */
int az_action_sudo_ok(void);

/* Validate `password` by feeding it to `sudo -S -v` (refreshes the sudo timestamp so later
 * applies run without prompting). Returns 1 on success, 0 on a wrong/empty password. The
 * password is never stored -- only sudo's own timestamp is, exactly as a shell `sudo` would. */
int az_action_authenticate(const char *password);

/* Run shell command `cmdline`, capturing stdout+stderr together into a freshly malloc'd,
 * NUL-terminated string returned via *out (caller frees; may be an empty string). Returns the
 * command's exit status (0 == success), or -1 if it could not be started. stdin is /dev/null,
 * so a stray `sudo` inside the command can never sit waiting for a password on a dead tty --
 * it fails fast instead (and az_action_sudo_ok/authenticate ensure the credential is there
 * first for the needs_root applies). Runs via /bin/sh -c, like the old visible path. */
int az_action_run_capture(const char *cmdline, char **out);

/* Copy `text` to the X11 CLIPBOARD selection by piping it to `xclip -selection clipboard` (the
 * clipboard tool shipped on this X11/OpenBox build). Feeds the bytes on the child's stdin and
 * closes so xclip owns the selection, exactly as `printf '%s' text | xclip` would. Returns 1 on
 * success (xclip found and exited 0), 0 otherwise -- so the UI can show "Copied" vs a fallback.
 * No trailing newline is added (the copied command is a single line). */
int az_action_copy_clipboard(const char *text);

#endif /* AZ_ACTION_H */
