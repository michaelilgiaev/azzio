#!/usr/bin/env python3
"""Azzio application menu -- TOGGLE launcher (INSTANT, via a daemon).

Installed to /usr/local/bin/azzio-application-menu. Bound to the Super key by
OpenBox (see packages/openbox) and pointed at by the menu's .desktop
entry -- opening either runs this.

The menu runs as a resident DAEMON (a C/GTK3 binary that builds the window once and
keeps it hidden) so opening it is instant -- no per-click startup. This launcher just
signals the daemon:
  * daemon already running  -> SIGUSR1 = toggle (show if hidden, hide if shown)
  * daemon not running yet   -> start it, wait for it to be ready, SIGUSR2 = show

State is the daemon's PID file under XDG_RUNTIME_DIR. Pure standard library --
no pip packages, no venv (Python is already on the live session).

The launcher stayed Python when the menu itself was ported from Tkinter to C: it is a
thin bin entry point, and the daemon-signaling contract (PID file, SIGUSR1 toggle /
SIGUSR2 show) is identical, so it drives the C daemon unchanged -- it just execs the
compiled binary instead of a python module.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

# Installed daemon binary. Overridable via AZZIO_MENU_DIR / AZZIO_DAEMON_BIN for local
# testing (the compiled daemon lives directly under MENU_DIR).
MENU_DIR = os.environ.get("AZZIO_MENU_DIR", "/usr/local/lib/azzio-application-menu")
DAEMON_BIN = os.environ.get(
    "AZZIO_DAEMON_BIN",
    os.path.join(MENU_DIR, "azzio-application-menu-daemon"),
)

RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
PID_FILE = os.path.join(RUNTIME_DIR, "azzio-application-menu.pid")


def _read_pid() -> int | None:
    """Return the live daemon PID from the PID file, or None if absent/stale/dead."""
    try:
        pid = int(open(PID_FILE).read().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)  # signal 0 = liveness probe, no signal delivered
    except OSError:
        return None
    return pid


def _signal(pid: int, sig: int) -> None:
    """Best-effort signal: a dead/gone daemon must not crash the launcher."""
    try:
        os.kill(pid, sig)
    except OSError:
        pass


def main() -> int:
    if not os.path.isfile(DAEMON_BIN):
        print(f"azzio-application-menu: daemon binary not found at {DAEMON_BIN}",
              file=sys.stderr)
        return 1

    # --- Is the daemon already running? -----------------------------------
    pid = _read_pid()
    if pid is not None:
        # Alive -> toggle it and we're done (instant show/hide inside the daemon).
        _signal(pid, signal.SIGUSR1)
        return 0
    # Stale PID file (daemon gone) -> clean up and start a fresh one below.
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass

    # --- Start the daemon, then show --------------------------------------
    # Detach (start_new_session) so the launcher/panel-icon does not block. The
    # daemon writes its own PID file once its window is built and ready.
    with open(os.devnull, "r+b") as null:
        subprocess.Popen(
            [DAEMON_BIN],
            stdin=null, stdout=null, stderr=null, start_new_session=True,
        )

    # Wait (briefly) for the daemon to come up and publish its PID file, then tell
    # it to show. Poll up to ~5s in 50ms steps so a slow first start still works.
    for _ in range(100):
        pid = _read_pid()
        if pid is not None:
            _signal(pid, signal.SIGUSR2)
            return 0
        time.sleep(0.05)

    print("azzio-application-menu: daemon did not come up in time", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
