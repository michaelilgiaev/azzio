#!/usr/bin/env python3
"""Azzio window switcher -- alt-tab launcher (INSTANT, via a daemon).

Installed to /usr/local/bin/azzio-window-switcher. Bound by OpenBox to A-Tab
(--next) and A-S-Tab (--prev); see packages/openbox. The switcher runs as a resident
C/GTK3 DAEMON that builds its overlay once at login and keeps it hidden off-screen, so
each Alt+Tab is instant -- this launcher just signals the daemon:

  * --next -> SIGUSR1 (advance forward + show)
  * --prev -> SIGUSR2 (advance backward + show)

If the daemon is not running yet it is started, we wait for its PID file, then signal.
State is the daemon's PID file under XDG_RUNTIME_DIR. Pure standard library -- no pip,
no venv (Python is already on the live session). Mirrors the application-menu launcher's
daemon-signaling contract, with a direction argument added.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

# Installed daemon binary. Overridable via AZZIO_SWITCHER_DIR / AZZIO_SWITCHER_BIN for
# local testing (the compiled daemon lives directly under SWITCHER_DIR).
SWITCHER_DIR = os.environ.get(
    "AZZIO_SWITCHER_DIR", "/usr/local/lib/azzio-window-switcher"
)
DAEMON_BIN = os.environ.get(
    "AZZIO_SWITCHER_BIN",
    os.path.join(SWITCHER_DIR, "azzio-window-switcher-daemon"),
)

RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
PID_FILE = os.path.join(RUNTIME_DIR, "azzio-window-switcher.pid")


def _read_pid() -> "int | None":
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
    # --next (default) advances forward, --prev backward.
    direction = "--next"
    if len(sys.argv) > 1 and sys.argv[1] in ("--next", "--prev"):
        direction = sys.argv[1]
    show_sig = signal.SIGUSR1 if direction == "--next" else signal.SIGUSR2

    if not os.path.isfile(DAEMON_BIN):
        print(f"azzio-window-switcher: daemon binary not found at {DAEMON_BIN}",
              file=sys.stderr)
        return 1

    # --- Is the daemon already running? -----------------------------------
    pid = _read_pid()
    if pid is not None:
        _signal(pid, show_sig)
        return 0
    # Stale PID file (daemon gone) -> clean up and start a fresh one below.
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass

    # --- Start the daemon, then show --------------------------------------
    with open(os.devnull, "r+b") as null:
        subprocess.Popen(
            [DAEMON_BIN],
            stdin=null, stdout=null, stderr=null, start_new_session=True,
        )

    # Wait (briefly) for the daemon to publish its PID file, then signal it.
    for _ in range(100):
        pid = _read_pid()
        if pid is not None:
            _signal(pid, show_sig)
            return 0
        time.sleep(0.05)

    print("azzio-window-switcher: daemon did not come up in time", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
