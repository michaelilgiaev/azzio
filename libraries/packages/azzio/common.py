#!/usr/bin/env python3
"""azzio guest command line interface -- shared imports + tiny privileged/host helpers.

This is the FIRST module of the `azzio` guest-CLI package (libraries/packages/azzio/).
The package is split across several small modules for maintainability (common, resolver,
theme, sshd, command_line_interface), but the thing that actually ships to the guest is a SINGLE
self-contained script at /usr/local/bin/azzio: packages.openbox.azzio_command_line_interface() BUNDLES these
modules -- in the order packages.azzio.bundle.MODULE_ORDER -- into one file (this module's
imports/header first, then each later module's body after its `# BUNDLE_START` sentinel).
So everything below lands in ONE module namespace at runtime; that is why the later modules
call these helpers by bare name with no intra-package import.

Only the standard library is used (urllib + json for the geolocation query; subprocess for
the privileged steps). No curl/jq, no pip packages. NOTE: urllib.request and random are
imported LAZILY where they are used (resolver.py), NOT here in the shared header -- so the
bare `azzio` fast path (which execs the C terminal user interface) never pays their import cost at startup.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

# BUNDLE_START -- everything ABOVE this line is the bundle header (shebang + docstring +
# imports), emitted once from THIS module; the bundler drops each later module's own
# header and keeps only what follows its `# BUNDLE_START`.


def _err(msg: str) -> None:
    """Print to stderr (stdout stays result-only, matching the old command line interface)."""
    print(msg, file=sys.stderr)


def _sudo_prefix() -> list[str]:
    """The sudo prefix for a privileged call: nothing when already root, else `sudo` --
    plus `-n` (non-interactive) when AZZIO_SUDO_NONINTERACTIVE is set.

    The terminal user interface sets that env var (see main.c): its applies run captured with
    stdin from /dev/null and wrapped in `timeout`, so an inner sudo that PROMPTED would stall
    until timeout and surface only a vague error. With `-n`, a cached credential (the UI
    secures one via its masked prompt first) is used silently, and a MISSING credential fails
    FAST with sudo's own message instead of hanging. Interactive CLI use (no env var) keeps
    the normal prompting behaviour."""
    if os.geteuid() == 0:
        return []
    if os.environ.get("AZZIO_SUDO_NONINTERACTIVE"):
        return ["sudo", "-n"]
    return ["sudo"]


def _sudo(*args: str, check: bool = True, quiet: bool = False) -> int:
    """Run a command under sudo (or directly if already root). Returns the exit code; raises
    subprocess.CalledProcessError when check and the command fails. `quiet` routes the child's
    stderr to /dev/null -- used for best-effort teardown (e.g. stopping a maybe-absent transient
    unit) whose "Unit ... not loaded" chatter would otherwise leak to the user's terminal."""
    return subprocess.run(
        [*_sudo_prefix(), *args], check=check,
        stderr=subprocess.DEVNULL if quiet else None,
    ).returncode


def _have(prog: str) -> bool:
    return any(os.access(os.path.join(d, prog), os.X_OK)
               for d in os.environ.get("PATH", "").split(os.pathsep) if d)


def _sudo_write(path: str, content: str) -> None:
    """Write `content` to a root-owned file via `sudo tee` (works unprivileged)."""
    subprocess.run([*_sudo_prefix(), "tee", path], input=content.encode(),
                   stdout=subprocess.DEVNULL, check=False)


def _sudo_write_append(path: str, content: str) -> None:
    """Append `content` to a root-owned file via `sudo tee -a`."""
    subprocess.run([*_sudo_prefix(), "tee", "-a", path], input=content.encode(),
                   stdout=subprocess.DEVNULL, check=False)


def _current_user() -> str:
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return os.environ.get("USER", "")


def _is_mountpoint(path: str) -> bool:
    return subprocess.run(["mountpoint", "-q", path],
                          stderr=subprocess.DEVNULL).returncode == 0
