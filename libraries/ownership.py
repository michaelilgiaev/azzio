"""Never leave cache/ output/ logs/ root-owned and locked on the host.

Same guarantee the old compile.sh gave, ported to Python:

  * STARTUP reclaim  - before anything, chown the trees back to the host user, so a
                       tree left behind by a SIGKILL'd/power-cut prior run is
                       recovered on the next invocation.
  * CONTINUOUS reclaim - a background thread re-chowns the SAFE trees (everything
                       except the live work tree cache/build) every second, so
                       nothing stays locked mid-build for more than ~1s.
  * IMMEDIATE reclaim - right after mkarchiso returns, chown inline (and the work
                       tree too, after unmounting), while sudo is fresh.
  * FINAL reclaim    - on every exit path (atexit / signal), chown once more.

Owner resolution (identical policy to the old script):
  * root in a container -> chown to HOST_UID:HOST_GID (passed via `docker run -e`).
    If those are unset/0 we DON'T guess (a chown-to-root would relock everything);
    we warn once and disable the handback.
  * non-root native run -> chown to the invoking user, escalating via sudo since
    mkarchiso/pacstrap created the files as root.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import paths


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


class Ownership:
    def __init__(self, sudo: list[str]):
        # sudo is [] when we are root, else ["sudo", "-n"].
        self.sudo = sudo
        self.owner = self._resolve_owner()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- owner resolution ----------------------------------------------------
    def _resolve_owner(self) -> str | None:
        if paths.is_root():
            uid = os.environ.get("HOST_UID", "").strip()
            gid = os.environ.get("HOST_GID", "").strip()
            # ASCII digits only: str.isdigit() also accepts Unicode digits (e.g.
            # Arabic-Indic), which chown rejects -- reject them here instead.
            ascii_digits = set("0123456789")
            uid_ok = uid != "" and set(uid) <= ascii_digits
            gid_ok = gid != "" and set(gid) <= ascii_digits
            if not uid_ok or not gid_ok or uid == "0" or gid == "0":
                if paths.in_docker():
                    sys.stderr.write(
                        "[!] HOST_UID/HOST_GID not set: output/ cache/ logs/ will stay root-owned\n"
                        "[!] on the host. Re-run with:  -e HOST_UID=$(id -u) -e HOST_GID=$(id -g)\n"
                    )
                return None
            return f"{uid}:{gid}"
        # native, non-root: hand back to the invoking user (needs sudo).
        if not self.sudo:
            return None
        return f"{os.getuid()}:{os.getgid()}"

    def _chown(self, *args: str) -> None:
        _run(self.sudo + ["chown", *args])

    def _chmod(self, *args: str) -> None:
        _run(self.sudo + ["chmod", *args])

    # -- reclaim variants ----------------------------------------------------
    def reclaim_full(self) -> None:
        """Full recursive reclaim of all three trees (startup / immediate / final).
        Safe to call only when the work tree has no live bind mounts."""
        if not self.owner:
            return
        for d in (paths.CACHEDIR, paths.BUILDDIR, paths.LOGDIR):
            if d.is_dir():
                self._chown("-R", "--preserve-root", self.owner, str(d))
                self._chmod("-R", "u+w", str(d))

    def _reclaim_periodic(self) -> None:
        """Cheap sweep that NEVER recurses the live work tree (cache/build), which
        during mkarchiso holds the airootfs with live proc/sys/dev/run bind mounts."""
        if not self.owner:
            return
        # top inodes (non-recursive)
        for d in (paths.CACHEDIR, paths.BUILDDIR, paths.LOGDIR):
            if d.is_dir():
                self._chown(self.owner, str(d))
                self._chmod("u+w", str(d))
        # output/ and logs/ fully (no live mounts, small)
        for d in (paths.BUILDDIR, paths.LOGDIR):
            if d.is_dir():
                self._chown("-R", "--preserve-root", self.owner, str(d))
                self._chmod("-R", "u+w", str(d))
        # every child of cache/ EXCEPT the work tree
        if paths.CACHEDIR.is_dir():
            for child in paths.CACHEDIR.iterdir():
                if child == paths.WORKDIR:
                    continue
                self._chown("-R", "--preserve-root", self.owner, str(child))
                self._chmod("-R", "u+w", str(child))

    # -- continuous thread ---------------------------------------------------
    def start_continuous(self) -> None:
        if not self.owner:
            return

        def loop() -> None:
            while not self._stop.wait(1.0):
                try:
                    self._reclaim_periodic()
                except Exception:
                    pass

        self._thread = threading.Thread(target=loop, name="chowner", daemon=True)
        self._thread.start()

    def stop_continuous(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
