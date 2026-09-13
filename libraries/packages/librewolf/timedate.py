"""Azzio timedate -- build wiring for the Flask Time + Calendar home page.

This is the LibreWolf default home page: a small local Flask website (applications.py + page.py,
right beside this module) that shows the current time (hour/minute/seconds) and a
calendar (day/month/year), served at localhost:49154. It runs in the BACKGROUND as a
systemd service that starts at boot, and LibreWolf lands on it on startup / Home / new
tab (see packages/librewolf). The timezone follows the SYSTEM live -- default
Asia/Jerusalem (set by Calamares), updating itself if the user changes it by any means
(applications.py reads /etc/localtime on every request).

Mirrors packages/application_menu/application_menu.py: our OWN package, so the sources
live directly in this dir next to the build wiring, and compiler.py iterates emit_plan()
to place the artifacts into the airootfs (root-owned system paths -- the OFFLINE
Calamares install rsyncs the live rootfs, so they carry onto the installed system with
no separate installer step). Unlike the menu, this is a PURE-PYTHON app (nothing to
compile): emit_plan() just copies the .py sources + the launcher + the .service unit.

Layers:
  * SOURCE tree -- libraries/packages/librewolf/ (paths.TIMEDATE_DIR):
      applications.py      the Flask app (routes: / , /api/now , /healthz); reads the system zone
      page.py     assembles the self-contained page (seed values + document)
      assets.py   the bulky builders page.py inlines: analog-clock SVG, CSS, client script
      timedate.py THIS module -- the install paths, the systemd unit, and emit_plan()
  * INSTALLED layout (root-owned):
      /usr/local/lib/azzio-timedate/{applications,page,assets}.py   the app
      /usr/local/bin/azzio-timedate                    a 3-line launcher (python applications.py)
      /etc/systemd/system/azzio-timedate.service       the background service (boot)
    The service enable-symlink (multi-user.target.wants) is added by compiler._link_services.

Runtime dependency: python-flask (added to packages.x86_64 AZZIO ADDITIONS). python is
already in the manifest. Flask's built-in server is fine here -- a single loopback client
(the local browser) on a stateless page.
"""

from __future__ import annotations

import paths

# --- Fixed contract: the port the whole system agrees on --------------------
# The one port LibreWolf's home/new-tab URL and this service both use. Kept in lock-step
# with packages/librewolf/applications.PORT and packages/librewolf.TIMEDATE_URL (a test pins it).
PORT = 49154
URL = f"http://localhost:{PORT}"

# --- Installed system paths (root-owned) ------------------------------------
# Where the app lands in the live/installed rootfs. Under /usr/local (our stuff), so the
# OFFLINE install's unpackfs rsync carries it to the target unchanged.
LIB_DIR = "/usr/local/lib/azzio-timedate"
APPLICATION_SYSTEM_PATH = f"{LIB_DIR}/applications.py"
PAGE_SYSTEM_PATH = f"{LIB_DIR}/page.py"
# page.py imports the bulky markup/style/script builders from assets.py; it must land beside
# applications.py/page.py in LIB_DIR or the app crashes at `import assets`.
ASSETS_SYSTEM_PATH = f"{LIB_DIR}/assets.py"
# The bin entry point the systemd unit runs: a tiny wrapper that execs the app with the
# system python, from LIB_DIR (so `import page` / `import assets` resolves). Executable.
LAUNCHER_SYSTEM_PATH = "/usr/local/bin/azzio-timedate"
SERVICE_NAME = "azzio-timedate.service"
SERVICE_SYSTEM_PATH = f"/etc/systemd/system/{SERVICE_NAME}"

# --- Source files (in the repo) ---------------------------------------------
_SRC_APPLICATION = "applications.py"
_SRC_PAGE = "page.py"
_SRC_ASSETS = "assets.py"


def _read_source(name: str) -> str:
    """Read one of the app's Python sources from the timedate package dir as text."""
    return (paths.TIMEDATE_DIR / name).read_text(encoding="utf-8")


def application_py() -> str:
    """The Flask application (applications.py), verbatim from the source tree. Installed to
    APPLICATION_SYSTEM_PATH. Reads the system timezone live and serves the page + /api/now."""
    return _read_source(_SRC_APPLICATION)


def page_py() -> str:
    """The page renderer (page.py), verbatim from the source tree. Installed to
    PAGE_SYSTEM_PATH beside applications.py (applications.py does `from page import render`)."""
    return _read_source(_SRC_PAGE)


def assets_py() -> str:
    """The presentation assets (assets.py) -- the analog-clock SVG, the stylesheet, and the
    client script that page.py inlines. Verbatim from the source tree, installed to
    ASSETS_SYSTEM_PATH beside page.py (page.py does `from assets import ...`)."""
    return _read_source(_SRC_ASSETS)


def launcher_sh() -> str:
    """A tiny launcher installed as the bin entry point the systemd unit runs.

    It cd's into LIB_DIR (so `import page` inside applications.py resolves without packaging)
    and execs the system python on applications.py, which binds 0.0.0.0:PORT. `exec` so the
    python process replaces the shell and systemd tracks it directly (clean stop/restart).
    `-u` (unbuffered) so logs reach the journal promptly."""
    return f"""\
#!/bin/sh
# azzio-timedate -- launch the Flask Time + Calendar home page (localhost:{PORT}).
# Generated by packages/librewolf/timedate.py (edit the Python, not this file).
cd '{LIB_DIR}' || exit 1
exec python -u applications.py
"""


def service_unit() -> str:
    """The systemd unit that runs the home page in the BACKGROUND, started at boot.

    Design (matches the spec "run in the background, start as the machine starts"):
      * Type=simple, ExecStart the launcher -- the Flask server runs in the foreground of
        its own process, which systemd supervises.
      * WantedBy=multi-user.target: a SYSTEM service (not per-user), so it is up before/
        independently of the graphical session -- the browser just connects to the
        already-listening loopback port. Enabled via a multi-user.target.wants symlink
        (compiler._link_services), exactly like the other azzio oneshots; the OFFLINE
        install carries both the unit and the symlink onto the target via unpackfs, so it
        also starts at boot on the INSTALLED system.
      * After=network.target: not strictly required for a loopback bind, but it lets the
        loopback interface settle and orders us sanely at boot.
      * Restart=on-failure: if the app ever crashes, bring the home page back so a new tab
        is never dead. A modest RestartSec avoids a hot loop if something is persistently
        wrong (e.g. the port is taken).
      * Hardening: DynamicUser is NOT used (the app reads /etc/localtime, which is world-
        readable anyway, and we want zero setup); instead we drop privileges to `nobody`
        and lock the process down (no new privs, read-only system, private tmp) since it
        needs nothing but to read tzdata and listen on a port. ProtectSystem=strict with
        no writable paths -- the app writes nothing.
    """
    return f"""\
[Unit]
Description=Azzio timedate home page (Flask Time + Calendar at localhost:{PORT})
Documentation=https://github.com/michaelilgiaev/azzio
After=network.target

[Service]
Type=simple
ExecStart={LAUNCHER_SYSTEM_PATH}
Restart=on-failure
RestartSec=3
# Drop privileges: the page needs only to read world-readable tzdata and listen on a
# loopback-facing port. Run unprivileged and locked down.
User=nobody
Group=nobody
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
RestrictAddressFamilies=AF_INET AF_INET6
RestrictNamespaces=true
LockPersonality=true
MemoryDenyWriteExecute=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


# --- Emit plan --------------------------------------------------------------
# Declarative map (builder -> dest -> mode), mirroring application_menu.PLAN so
# compiler.py iterates it the same way. All absolute SYSTEM paths (root-owned): the
# OFFLINE Calamares install rsyncs the live rootfs, so these carry onto the installed
# system unchanged. The service ENABLE-symlink is added separately in
# compiler._link_services (like the other azzio units).
_EXEC = 0o755
_CONF = 0o644

PLAN = [
    {"builder": application_py, "dest": APPLICATION_SYSTEM_PATH, "mode": _CONF},
    {"builder": page_py, "dest": PAGE_SYSTEM_PATH, "mode": _CONF},
    {"builder": assets_py, "dest": ASSETS_SYSTEM_PATH, "mode": _CONF},
    {"builder": launcher_sh, "dest": LAUNCHER_SYSTEM_PATH, "mode": _EXEC},
    {"builder": service_unit, "dest": SERVICE_SYSTEM_PATH, "mode": _CONF},
]


def emit_plan() -> list[dict]:
    """Return the PLAN (builder/dest/mode) for compiler.py to emit into the airootfs.
    Kept as a function to mirror application_menu.emit_plan()/openbox.emit_plan(). The
    service enable-symlink is added by compiler._link_services.

    Returns FRESH dict copies (not the module-level PLAN entries) so a caller that
    mutates a returned entry cannot corrupt module state -- compiler.py may call this more
    than once per build."""
    return [dict(entry) for entry in PLAN]
