"""The timedate home page markup + styling (one self-contained HTML document).

Split out of applications.py so the Flask wiring stays small and the look lives on its own. This
is the site LibreWolf lands on: a calm, self-contained TIME + DATE page -- no todos, no
task recording, just a really good clock and calendar for the end user.

What the page shows (all client-side, all in the SYSTEM zone):

  * A DIGITAL clock with a 12-hour AM/PM readout (HH:MM:SS + AM/PM), steady separators
    (no blinking -- it read as annoying), over the weekday and the full date.
  * A ROUND ANALOG clock: an SVG face with hour/minute/second hands that sweep smoothly.
  * A NAVIGABLE CALENDAR: a month grid you can page back/forward through (and jump home
    to today). It only DISPLAYS months -- it records nothing. Today is highlighted; when
    you are on the current month the highlight tracks the live date across midnight.
  * A SUN / MOON HORIZON ARC: a half-circle horizon with a body that rides across it --
    the sun while it is up, the moon while it is down -- positioned by the local solar
    day so the end user gets an at-a-glance sense of where in the day/night they are.

Everything is inlined into ONE document (exactly one <style>, one <script>, no external
assets) so it renders instantly offline the moment the browser lands on it. The server
seeds the first paint with the system-zone `now` (so there is no flash of a wrong time);
the inline script then advances everything every animation frame using Intl.DateTimeFormat
pinned to the SYSTEM zone name, and re-syncs to /api/now on a slow interval so it corrects
drift and picks up a timezone change without a reload.

The clock/calendar/arc geometry is pure math over the wall-clock parts Intl hands us, so
nothing here needs the network or a geolocation prompt. The sun arc uses a lightweight
solar-position calc at a latitude derived from the system zone (falling back to the
distro default) -- enough to place the body sensibly, not an ephemeris.

This module stays a slim orchestrator: it computes the seed values and assembles the one
document. The three bulky string builders it inlines -- the analog-clock SVG, the full
stylesheet, and the client script -- live in the sibling `assets` module (installed beside
this file; see timedate.PLAN) so no source file gets unwieldy.
"""

from __future__ import annotations

import datetime
import html
import json

# assets.py sits beside this file. At runtime the launcher cd's into LIB_DIR so it is a
# top-level module (`import assets`); when this module is imported as part of the
# packages.librewolf package (e.g. the test suite), it resolves as a sibling instead. Try
# both so the same source works in place and installed.
try:
    from assets import ACCENT, analog_svg, css, script
except ImportError:  # imported as packages.librewolf.page
    from .assets import ACCENT, analog_svg, css, script

# Approximate latitudes for the IANA zones we realistically ship with (distro default is
# Asia/Jerusalem). The sun-arc calc only needs a rough latitude to place the body on the
# horizon; anything within a few degrees looks right. The browser refines this from its
# own resolved offset, but we seed a sane value per zone so the FIRST paint is correct and
# so a headless render (no browser) is still sensible. Unknown zones fall back to the
# distro default's latitude via seed_latitude().
_ZONE_LATITUDE = {
    "Asia/Jerusalem": 31.78,
    "Asia/Tel_Aviv": 32.08,
    "UTC": 0.0,
    "Europe/London": 51.51,
    "Europe/Paris": 48.85,
    "Europe/Berlin": 52.52,
    "Europe/Moscow": 55.75,
    "America/New_York": 40.71,
    "America/Chicago": 41.85,
    "America/Los_Angeles": 34.05,
    "Asia/Tokyo": 35.68,
    "Australia/Sydney": -33.87,
}
_DEFAULT_LATITUDE = _ZONE_LATITUDE["Asia/Jerusalem"]


def seed_latitude(zone_name: str) -> float:
    """A rough latitude for the seed sun-arc, by zone. Only affects the seed paint and the
    /api/now refinement (the client places the body from it); unknown zones use the distro
    default so it is never wildly off. Public so applications.py can send it on /api/now, keeping
    the zone->latitude table a single source of truth here in the page module."""
    return _ZONE_LATITUDE.get(zone_name, _DEFAULT_LATITUDE)


def render(*, zone_name: str, now: datetime.datetime) -> str:
    """Return the complete HTML document for the given system zone and seed time.

    `zone_name` is the IANA name (e.g. "Asia/Jerusalem") the browser formats in;
    `now` seeds the initial values so the page is correct before JS runs. Everything is
    inlined -- one response, no second request needed to look right."""
    # Values embedded for the first paint. The script overwrites them on its first tick.
    seed = {
        "zone": zone_name,
        "epoch_ms": int(now.timestamp() * 1000),
        "lat": seed_latitude(zone_name),
    }
    # Human seeds for the noscript / pre-JS render (12-hour digital readout).
    hour12 = now.hour % 12 or 12
    ampm = "AM" if now.hour < 12 else "PM"
    hh = f"{hour12:02d}"
    mm = f"{now.minute:02d}"
    ss = f"{now.second:02d}"
    day_name = now.strftime("%A")
    date_line = now.strftime("%d %B %Y")
    month_title = now.strftime("%B %Y")
    safe_zone = html.escape(zone_name)
    seed_json = json.dumps(seed)

    # The three large builders live in the sibling `assets` module; build them once here
    # and inline into the single <style>/<svg>/<script> so the document stays self-contained.
    css_block = css()
    analog_block = analog_svg()
    script_block = script(seed_json)

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{hh}:{mm} {ampm} · Azzio</title>
<style>
{css_block}
</style>
</head>
<body>
  <main class="wrap" role="main" aria-label="Current time and date">
   <div class="col col-left">
    <section class="clocks">
      <div class="analog" aria-hidden="true">
{analog_block}
      </div>
      <div class="digital">
        <div class="time" id="clock">
          <span id="hh">{hh}</span><span class="sep">:</span><span id="mm">{mm}</span><span class="sep">:</span><span id="ss">{ss}</span><span class="ampm" id="ampm">{ampm}</span>
        </div>
        <div class="dayname" id="dayname">{html.escape(day_name)}</div>
        <div class="date" id="date">{html.escape(date_line)}</div>
        <div class="zone" id="zone" title="System timezone">{safe_zone}</div>
      </div>
    </section>
   </div>

   <div class="col col-right">
    <section class="horizon" aria-label="Sun and moon position">
      <svg id="skySvg" viewBox="0 0 400 210" width="400" height="210"
           preserveAspectRatio="xMidYMax meet" role="img"
           aria-label="Sun and moon over the horizon">
        <defs>
          <linearGradient id="skyGrad" x1="0" y1="0" x2="0" y2="1">
            <stop id="skyTop" offset="0%" stop-color="#0b1a2e"/>
            <stop id="skyBottom" offset="100%" stop-color="#0b0f14"/>
          </linearGradient>
        </defs>
        <rect x="0" y="0" width="400" height="200" fill="url(#skyGrad)"/>
        <path id="arcPath" d="M 30 200 A 170 170 0 0 1 370 200"
              fill="none" stroke="rgba(255,255,255,0.14)" stroke-width="2"
              stroke-dasharray="3 6"/>
        <line x1="0" y1="200" x2="400" y2="200"
              stroke="rgba(255,255,255,0.25)" stroke-width="2"/>
        <g id="bodyGroup">
          <circle id="bodyGlow" cx="200" cy="30" r="26" fill="rgba(6,184,253,0.0)"/>
          <circle id="body" cx="200" cy="30" r="13" fill="{ACCENT}"/>
          <circle id="moonShadow" cx="200" cy="30" r="13" fill="#0b0f14"
                  opacity="0" transform="translate(6,0)"/>
        </g>
      </svg>
      <div class="sky-caption"><span id="skyLabel">daytime</span></div>
    </section>

    <section class="calendar" aria-label="Calendar">
      <div class="cal-head">
        <button id="calPrev" class="cal-nav" type="button"
                aria-label="Previous month">&#8249;</button>
        <button id="calTitle" class="cal-title" type="button"
                aria-label="Jump to current month" title="Jump to today">{month_title}</button>
        <button id="calNext" class="cal-nav" type="button"
                aria-label="Next month">&#8250;</button>
      </div>
      <button id="calToday" class="cal-today" type="button" hidden
              aria-label="Jump to the current month">Jump to current</button>
      <div class="cal-grid" id="calDow" aria-hidden="true">
        <span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span><span>Sat</span><span>Sun</span>
      </div>
      <div class="cal-grid cal-days" id="calDays"></div>
    </section>
   </div>
  </main>

<script>
{script_block}
</script>
</body>
</html>
"""
    return body
