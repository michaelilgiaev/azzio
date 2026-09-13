#!/usr/bin/env python3
"""azzio guest command line interface -- the bare-`azzio` launcher for the C TERMINAL UI.

WHY THIS IS NOW A THIN SHIM. Running `azzio` with no arguments opens a full-screen text
UI for the three things a fresh machine needs tuned -- the colour Theme, the desktop
Wallpaper, and the Network. It used to be implemented HERE in Python/curses, but that felt
laggy: every keystroke redrew through the interpreter. The UI was rewritten in C (the C
sources now live in THIS package, libraries/packages/azzio/, built to
/usr/local/lib/azzio/azzio) so it drives the terminal with raw ANSI + termios and
feels INSTANT. This module is now just the launch path: bare `azzio` EXECs that binary.

WHY exec (not subprocess). os.execv REPLACES this Python process with the C UI, so there is
no lingering interpreter, no extra process, and the C program owns the real terminal
directly (its raw-mode / alt-screen handling is clean and its sudo-prompting actions run on
the true tty). The C UI in turn shells back to the SAME `azzio` subcommands for every
action (azzio theme --dark, azzio wallpaper --years.png, azzio network firewall enable,
...), so the behaviour is still the tested command line interface -- the C layer adds navigation + previews.

STARTUP COST. The heavy stdlib imports the command line interface only needs for geolocation (urllib.request,
random) are LAZY (resolver.py), so reaching this exec costs only the interpreter cold-start
(~13ms) before the process becomes the C binary -- effectively 0ms to the user.

GRACEFUL DEGRADATION. With no usable terminal (piped stdin/stdout, no TERM), we do NOT exec
the UI: we print a short pointer to the subcommands and return 0, so `azzio </dev/null`, a
cron job, or a dumb pipe never breaks. (The C binary guards this too, but checking here
avoids even the exec.) If the binary is somehow missing, we fall back to the same pointer
rather than crashing.
"""

from __future__ import annotations

# BUNDLE_START

# The compiled C UI binary (built + installed by packages.azzio.terminal_user_interface_build.build_terminal_user_interface; a test
# pins this against terminal_user_interface_build.TERMINAL_USER_INTERFACE_BIN_SYSTEM_PATH so the launcher and the build cannot drift).
TERMINAL_USER_INTERFACE_BIN = "/usr/local/lib/azzio/azzio"


def _tty_ok() -> bool:
    """True when stdin AND stdout are real terminals and TERM is set -- the precondition for
    the full-screen UI. Guards run_terminal_user_interface so a non-interactive invocation degrades gracefully
    (we skip the exec and print a pointer instead)."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM"))


def _pointer() -> int:
    """Print a short pointer to the subcommands and return 0. Shown when there is no usable
    terminal, or when the compiled UI binary is missing -- so bare `azzio` never crashes."""
    print("azzio: no interactive terminal. Use the subcommands instead, e.g.:\n"
          "  azzio theme --dark        set the theme\n"
          "  azzio wallpaper           show / set the wallpaper\n"
          "  azzio network             network status and controls\n"
          "Run `azzio --help` for the full list.")
    return 0


def run_terminal_user_interface(argv=None) -> int:
    """Entry point for the bare `azzio` command: launch the C full-screen UI by EXECing the
    compiled binary (this process becomes the UI). Returns 0 only on the fallback paths --
    on a successful exec it never returns. Falls back to a pointer message when there is no
    terminal or the binary is absent."""
    if not _tty_ok():
        return _pointer()
    if not (os.path.exists(TERMINAL_USER_INTERFACE_BIN) and os.access(TERMINAL_USER_INTERFACE_BIN, os.X_OK)):
        # Built-but-missing should not happen on a real Azzio system (the ISO ships it),
        # but never crash: point the user at the subcommands.
        return _pointer()
    try:
        os.execv(TERMINAL_USER_INTERFACE_BIN, [TERMINAL_USER_INTERFACE_BIN])
    except OSError:
        return _pointer()
    return 0  # unreachable after a successful execv
