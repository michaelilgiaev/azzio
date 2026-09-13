#!/usr/bin/env python3
"""Azzio timedate -- the Flask Time + Calendar home page (served at localhost:49154).

This IS the page LibreWolf lands on: a small, pretty, easy-on-the-eyes local website
that shows the current TIME (hour, minute, seconds) and a CALENDAR (day, month, year).
Nothing else -- "very very basic" per the spec.

Two load-bearing design choices:

  1. TIMEZONE FOLLOWS THE SYSTEM, LIVE. The distro's timezone is configured by
     Calamares at install (default Asia/Jerusalem), but the user may change it later by
     ANY means -- the Calamares Location page, `timedatectl set-timezone`,
     `azzio timedate --resolve`, or hand-editing the /etc/localtime symlink. All of
     those converge on ONE ground truth: the /etc/localtime symlink's target under
     /usr/share/zoneinfo (that is literally how the system stores "the timezone"). So
     this app resolves the zone by reading that symlink on EVERY request (`_system_zone`)
     rather than caching a build-time value -- it therefore updates itself to match
     whatever the system currently says, with no restart and no separate config. The
     resolved IANA zone name (e.g. "Asia/Jerusalem") is handed to the browser so the
     clock renders in the SYSTEM zone regardless of the browser's own locale/timezone.

  2. THE CLOCK TICKS CLIENT-SIDE. The server does not stream time; it renders the page
     once with the resolved zone, and a tiny bit of JS uses Intl.DateTimeFormat with
     that zone to advance hour/minute/seconds and the date every second. This keeps the
     server trivial (one route + a health check) and the seconds hand smooth without
     polling. `/api/now` is provided too (JSON), so the page can re-sync to the server's
     wall clock periodically and so the zone is picked up again if it changed.

Pure Flask + Python standard library (zoneinfo). Bound to 0.0.0.0:49154 so the local
browser reaches it at localhost:49154; it serves only the loopback-facing home page and
holds no state. Run in the background by the azzio-timedate systemd service (see
timedate.py -> SERVICE_UNIT), started at boot.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, Response, jsonify

# The single, fixed port the whole system agrees on: LibreWolf's home/new-tab URL
# (packages/librewolf) and the systemd service both point here. localhost:49154.
PORT = 49154

# Where the OS stores "the current timezone": /etc/localtime is a symlink into the
# zoneinfo database, and its target path *is* the IANA zone name. Reading it live is how
# this app follows the system zone no matter how it was changed (Calamares, timedatectl,
# azzio timedate --resolve, a manual symlink). Kept as constants so a test can pin them.
LOCALTIME_PATH = "/etc/localtime"
ZONEINFO_DIR = "/usr/share/zoneinfo"

# Last-resort zone if /etc/localtime is missing or unreadable (e.g. a broken system, or
# running the app off-target for a demo). Matches the distro default so the page still
# shows a sensible, correct-for-Azzio time rather than erroring. The system symlink,
# when present, always wins over this.
FALLBACK_ZONE = "Asia/Jerusalem"

app = Flask(__name__)


def _zone_name_from_localtime() -> str | None:
    """Resolve the IANA zone name from the /etc/localtime symlink, or None.

    /etc/localtime points at /usr/share/zoneinfo/<Area>/<Location> (e.g.
    .../Asia/Jerusalem); the part AFTER the zoneinfo dir is the IANA name. We resolve the
    real path (following intermediate symlinks) and strip the zoneinfo prefix. Returns
    None if the file is absent, is not under the zoneinfo tree, or cannot be read -- the
    caller then falls back. This is deliberately defensive: the home page must never 500
    just because the timezone plumbing is in an odd state."""
    try:
        target = os.path.realpath(LOCALTIME_PATH)
    except OSError:
        return None
    zoneinfo_root = os.path.realpath(ZONEINFO_DIR)
    prefix = zoneinfo_root.rstrip("/") + "/"
    if not target.startswith(prefix):
        return None
    name = target[len(prefix):]
    # A valid IANA name looks like "Asia/Jerusalem" or "UTC"; reject anything empty or a
    # stray relative/posix-right-file artifact.
    return name or None


def _system_zone() -> tuple[str, ZoneInfo]:
    """Return (zone_name, ZoneInfo) for the CURRENT system timezone, live.

    Order of truth: the /etc/localtime symlink target (what the OS actually uses), then
    the FALLBACK_ZONE constant. Whatever name we settle on, we load its ZoneInfo; if even
    that fails (corrupt tzdata), we fall back once more to the constant so this never
    raises. Called on every request so the page tracks changes with no restart."""
    name = _zone_name_from_localtime() or FALLBACK_ZONE
    try:
        return name, ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return FALLBACK_ZONE, ZoneInfo(FALLBACK_ZONE)


@app.route("/")
def index() -> Response:
    """The home page: current time + calendar, rendered once with the system zone.

    We compute 'now' in the resolved zone server-side purely to seed the first paint (so
    there is no flash of a wrong time before JS runs); the client then ticks it forward
    every second in that same zone. The zone NAME is embedded so the browser formats in
    the system's zone, not its own."""
    name, tz = _system_zone()
    now = datetime.datetime.now(tz)
    html = _render_page(zone_name=name, now=now)
    return Response(html, mimetype="text/html")


@app.route("/api/now")
def api_now() -> Response:
    """JSON snapshot of the server's wall clock in the system zone, so the page can
    re-sync (drift correction), re-read the zone if it changed, and refine the sun/moon
    arc's latitude for the (possibly new) zone. Small and cache-proof (the client fetches
    it on a slow interval)."""
    from page import seed_latitude  # local import: page.py sits beside this file

    name, tz = _system_zone()
    now = datetime.datetime.now(tz)
    return jsonify(
        {
            "zone": name,
            "lat": seed_latitude(name),
            "iso": now.isoformat(),
            "epoch_ms": int(now.timestamp() * 1000),
            "year": now.year,
            "month": now.month,
            "day": now.day,
            "hour": now.hour,
            "minute": now.minute,
            "second": now.second,
        }
    )


@app.route("/healthz")
def healthz() -> Response:
    """Trivial liveness endpoint for the systemd service / manual checks."""
    return Response("ok\n", mimetype="text/plain")


def _render_page(*, zone_name: str, now: datetime.datetime) -> str:
    """Return the full HTML document (inline CSS + JS -- one self-contained file, no
    static assets to ship). Seeded with the server's `now` and the system `zone_name`;
    the inline script takes over ticking. Kept in its own module (page.py) so this app
    file stays about wiring and the markup/styling lives on its own."""
    from page import render  # local import: page.py sits beside this file
    return render(zone_name=zone_name, now=now)


def main() -> None:
    """Run the production server. Uses Flask's built-in server bound to 0.0.0.0:PORT;
    this is a single-client, loopback-facing home page (the local browser), so the
    dev server is entirely adequate and keeps the dependency surface to just Flask.
    Threaded so /api/now during a page load never blocks the initial render."""
    # `debug=False`, no reloader: this runs headless under systemd; the reloader would
    # fork a second process and the debugger would expose a console on the port.
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
