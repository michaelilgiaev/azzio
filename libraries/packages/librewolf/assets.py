"""Presentation assets for the timedate home page: the analog-clock SVG scaffold, the
full stylesheet, and the client-side script -- the three large string builders `page.py`
inlines into the ONE self-contained document it serves.

Split out of page.py so that module stays a slim orchestrator (seed values + document
assembly) and the bulky markup/styling/behaviour live on their own, keeping every file
comfortably under the project's size guideline. This is still OUR package's source: it is
installed beside applications.py and page.py (see timedate.PLAN) and imported at runtime as
`import assets`.

Nothing here holds state or touches the network; the builders are pure functions of their
arguments. `ACCENT` (the Azzio cyan) lives here as the single source of truth for the
page's highlight colour, shared by the SVG, the CSS, and imported by page.py for the seed.
"""

from __future__ import annotations

# Azzio accent (same cyan as os-release ANSI_COLOR "38;2;6;184;253"). Single source of
# truth for the page's highlight colour so the brand stays consistent across the SVG hands,
# the CSS accents, and the sun/moon body.
ACCENT = "#06B8FD"


def analog_svg() -> str:
    """The static SVG scaffold for the round analog clock: the face, the hour ticks, and
    the three hands (given ids so the script rotates them). Hands start at 12 o'clock; the
    script sets each hand's `transform` rotation every frame. Pure markup -- no per-render
    values, so the seed clock reads 12:00 until the first tick a moment later, which is
    imperceptible and never wrong once JS runs."""
    # Tick/face colours come from CSS custom properties (var(--tick*) / var(--face)) so the
    # face stays legible in BOTH the dark and light themes -- hardcoded white washed out on
    # the light background. Major ticks (12/3/6/9 and the hours) are longer and heavier.
    ticks = []
    for i in range(60):
        angle = i * 6  # degrees, 0 at top
        major = (i % 5) == 0
        length = 12 if major else 5
        width = 3 if major else 1.5
        klass = "tick major" if major else "tick"
        y1 = 8
        y2 = 8 + length
        ticks.append(
            f'<line class="{klass}" x1="100" y1="{y1}" x2="100" y2="{y2}" '
            f'stroke-width="{width}" stroke-linecap="round" '
            f'transform="rotate({angle} 100 100)"/>'
        )
    ticks_svg = "\n        ".join(ticks)
    return f"""<svg viewBox="0 0 200 200" width="200" height="200"
             class="analog-svg" role="img" aria-label="Analog clock">
        <circle class="face" cx="100" cy="100" r="98" fill="none" stroke-width="2"/>
        {ticks_svg}
        <line id="hHand" class="hand" x1="100" y1="100" x2="100" y2="52"
              stroke-width="6" stroke-linecap="round"/>
        <line id="mHand" class="hand" x1="100" y1="100" x2="100" y2="32"
              stroke-width="4" stroke-linecap="round"/>
        <line id="sHand" x1="100" y1="112" x2="100" y2="24"
              stroke="{ACCENT}" stroke-width="2" stroke-linecap="round"/>
        <circle cx="100" cy="100" r="5" fill="{ACCENT}"/>
      </svg>"""


def css() -> str:
    """All page styling (inlined into the single <style>). Calm dark theme on the Azzio
    cyan accent, with a light-mode fallback. Laid out as a centered column: the two clocks
    side by side, the sun/moon horizon under them, then the calendar."""
    return f""":root {{
    --accent: {ACCENT};
    --fg: #e8eef4;
    --fg-dim: #8fa3b6;
    --bg-0: #0b0f14;
    --bg-1: #121822;
    --grid-line: rgba(255,255,255,0.06);
    --face: rgba(255,255,255,0.12);
    --tick: rgba(255,255,255,0.35);
    --tick-major: rgba(255,255,255,0.85);
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ height: 100%; }}
  body {{
    /* Lock to the viewport: everything is arranged to FIT on one screen, no scrolling.
       The layout is two columns -- the clocks on the left, the sun/moon arc + calendar on
       the right -- centred in the viewport. On narrow/portrait screens the columns stack
       (see the media query) and the whole thing is scaled to still fit. */
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    width: 100vw;
    padding: clamp(0.75rem, 2.5vmin, 2rem);
    overflow: hidden;               /* no scroll: content is sized to fit */
    font-family: "Cantarell", "Noto Sans", system-ui, -apple-system, sans-serif;
    color: var(--fg);
    background: radial-gradient(1200px 700px at 50% -10%, var(--bg-1) 0%, var(--bg-0) 60%),
                var(--bg-0);
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
  }}
  .wrap {{
    display: flex;
    flex-direction: row;
    align-items: center;
    justify-content: center;
    gap: clamp(1.5rem, 5vw, 4rem);
    width: 100%;
    height: 100%;
    max-width: 1100px;
    user-select: none;
  }}
  .col {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }}
  .col-left {{ flex: 0 1 auto; }}
  .col-right {{
    flex: 0 1 auto;
    gap: clamp(0.75rem, 2.2vmin, 1.4rem);
  }}
  /* Left column: analog above the digital readout, stacked and centred. */
  .clocks {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: clamp(0.75rem, 2.5vmin, 1.6rem);
  }}
  .analog {{ flex: 0 0 auto; }}
  .analog-svg {{
    /* Scale with the smaller viewport dimension so it shrinks on short screens and the
       whole layout keeps fitting without scroll. */
    width: clamp(140px, 24vmin, 230px);
    height: auto;
    filter: drop-shadow(0 6px 22px rgba(0,0,0,0.45));
  }}
  .analog-svg .face {{ stroke: var(--face); }}
  .analog-svg .tick {{ stroke: var(--tick); }}
  .analog-svg .tick.major {{ stroke: var(--tick-major); }}
  .analog-svg .hand {{ stroke: var(--fg); }}
  .digital {{
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    gap: clamp(0.3rem, 1vmin, 0.55rem);
  }}
  .time {{
    font-family: "JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono", ui-monospace,
                 monospace;
    font-weight: 600;
    font-size: clamp(2.2rem, 7vmin, 4.6rem);
    line-height: 1;
    letter-spacing: 0.02em;
    display: flex;
    align-items: baseline;
    gap: 0.04em;
    font-variant-numeric: tabular-nums;
  }}
  .time .sep {{ color: var(--accent); }}
  .time .ampm {{
    font-size: 0.34em;
    font-weight: 700;
    letter-spacing: 0.14em;
    color: var(--fg-dim);
    margin-left: 0.35em;
    align-self: flex-start;
    margin-top: 0.15em;
  }}
  .dayname {{
    font-size: clamp(0.9rem, 2.6vmin, 1.5rem);
    font-weight: 600;
    letter-spacing: 0.26em;
    text-transform: uppercase;
    color: var(--accent);
  }}
  .date {{
    font-size: clamp(1rem, 3vmin, 1.9rem);
    font-weight: 300;
    letter-spacing: 0.04em;
    color: var(--fg);
  }}
  .zone {{
    margin-top: 0.15rem;
    font-size: 0.8rem;
    letter-spacing: 0.12em;
    color: var(--fg-dim);
    text-transform: uppercase;
  }}
  /* The right column (arc + calendar) shares one width so they line up, sized to the
     viewport so it fits without scrolling. */
  .col-right {{ --col-w: clamp(300px, 42vmin, 420px); }}
  .horizon {{
    width: var(--col-w);
    display: flex;
    flex-direction: column;
    align-items: center;
  }}
  #skySvg {{
    width: 100%;
    height: auto;
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 6px 22px rgba(0,0,0,0.35);
  }}
  .sky-caption {{
    margin-top: 0.35rem;
    font-size: clamp(0.68rem, 1.5vmin, 0.82rem);
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--fg-dim);
  }}
  .calendar {{
    width: var(--col-w);
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 14px;
    padding: clamp(0.6rem, 1.6vmin, 0.95rem) clamp(0.7rem, 1.8vmin, 1rem)
             clamp(0.7rem, 1.8vmin, 1.1rem);
  }}
  .cal-head {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.7rem;
  }}
  /* "Jump to current" -- shown by the script only when the viewed month is not the
     current one (see toggleTodayBtn); hidden via the native [hidden] attribute otherwise
     so it takes no space and cannot be tabbed to. A small accent pill under the header. */
  .cal-today {{
    display: block;
    margin: -0.2rem auto 0.7rem;
    padding: 0.3rem 0.9rem;
    font-size: 0.8rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--accent);
    background: rgba(6,184,253,0.10);
    border: 1px solid rgba(6,184,253,0.35);
    border-radius: 999px;
    cursor: pointer;
    animation: calTodayIn 0.18s ease-out;
  }}
  .cal-today:hover {{ background: rgba(6,184,253,0.18); }}
  .cal-today[hidden] {{ display: none; }}
  @keyframes calTodayIn {{ from {{ opacity: 0; transform: translateY(-3px); }} }}
  .cal-title {{
    font-size: 1.1rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    color: var(--fg);
    background: none;
    border: none;
    cursor: pointer;
    padding: 0.25rem 0.6rem;
    border-radius: 8px;
  }}
  .cal-title:hover {{ background: rgba(255,255,255,0.06); }}
  .cal-nav {{
    font-size: 1.4rem;
    line-height: 1;
    color: var(--fg-dim);
    background: none;
    border: none;
    cursor: pointer;
    width: 2.2rem;
    height: 2.2rem;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .cal-nav:hover {{ background: rgba(255,255,255,0.08); color: var(--fg); }}
  .cal-grid {{
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 2px;
    text-align: center;
  }}
  #calDow span {{
    font-size: clamp(0.6rem, 1.4vmin, 0.72rem);
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--fg-dim);
    padding: clamp(0.15rem, 0.6vmin, 0.3rem) 0;
  }}
  .cal-days .cell {{
    /* Slightly shorter than square so a 6-row month fits the viewport without scroll. */
    aspect-ratio: 7 / 6;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: clamp(0.78rem, 1.9vmin, 0.98rem);
    color: var(--fg);
    border-radius: 8px;
  }}
  .cal-days .cell.muted {{ color: var(--fg-dim); opacity: 0.4; }}
  .cal-days .cell.today {{
    background: var(--accent);
    color: #05131c;
    font-weight: 700;
  }}
  .cal-days .cell.weekend:not(.today):not(.muted) {{ color: #b9c7d6; }}
  @media (prefers-color-scheme: light) {{
    :root {{
      --fg: #1b2430; --fg-dim: #5a6b7b; --bg-0: #eef3f8; --bg-1: #ffffff;
      --grid-line: rgba(0,0,0,0.06);
      --face: rgba(0,0,0,0.18);
      --tick: rgba(0,0,0,0.30);
      --tick-major: rgba(0,0,0,0.70);
    }}
    .calendar {{ background: rgba(0,0,0,0.02); border-color: rgba(0,0,0,0.08); }}
    .cal-days .cell.today {{ color: #eef3f8; }}
  }}
  /* Portrait or narrow screens: the two side-by-side columns will not fit, so stack them
     (clocks on top, arc + calendar below) and centre. Everything is still sized in vmin,
     so it keeps trying to fit; if the screen is genuinely too short for the stack we allow
     scrolling as a graceful last resort rather than clipping. */
  @media (max-aspect-ratio: 1 / 1), (max-width: 720px) {{
    body {{ overflow-y: auto; }}
    .wrap {{
      flex-direction: column;
      height: auto;
      min-height: 100%;
      justify-content: center;
      gap: clamp(1rem, 3.5vmin, 2rem);
      padding: clamp(0.75rem, 2.5vmin, 1.5rem) 0;
    }}
    .analog-svg {{ width: clamp(140px, 34vw, 210px); }}
    .col-right {{ --col-w: min(420px, 88vw); }}
  }}"""


def script(seed_json: str) -> str:
    """The inline client script (the single <script>). Ticks the digital + analog clocks
    and the sun/moon arc every animation frame in the SYSTEM zone via Intl, renders the
    navigable calendar, and re-syncs to /api/now on a slow interval (drift + zone change).

    `seed_json` is the server's seed object ({zone, epoch_ms, lat}) embedded verbatim."""
    return f"""(function () {{
  "use strict";
  // Seeded by the server: the system IANA zone, the server epoch at render, and a rough
  // latitude for the sun arc.
  var state = {seed_json};

  // Offset between the server clock and this browser's clock, measured at page load and
  // refreshed by /api/now, so the displayed time follows the SERVER (system) wall clock
  // and zone even if the browser's own clock/zone is off. Start from the seed.
  var skewMs = state.epoch_ms - Date.now();

  var MONTHS = ["January","February","March","April","May","June","July","August",
                "September","October","November","December"];
  var DOW = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];

  function two(n) {{ return (n < 10 ? "0" : "") + n; }}
  function serverNow() {{ return new Date(Date.now() + skewMs); }}

  // --- Format the current instant in the SYSTEM zone via Intl (knows every IANA zone,
  // handles DST). We read individual parts so we can lay them out exactly and drive the
  // analog hands / arc from precise numeric fields.
  function partsInZone(when) {{
    var fmt;
    try {{
      fmt = new Intl.DateTimeFormat("en-GB", {{
        timeZone: state.zone,
        hour12: false, weekday: "long",
        day: "2-digit", month: "2-digit", year: "numeric",
        hour: "2-digit", minute: "2-digit", second: "2-digit"
      }});
    }} catch (e) {{
      // Unknown zone (shouldn't happen -- the server validated it) -> browser local zone
      // so the page still ticks rather than freezing.
      fmt = new Intl.DateTimeFormat("en-GB", {{
        hour12: false, weekday: "long",
        day: "2-digit", month: "2-digit", year: "numeric",
        hour: "2-digit", minute: "2-digit", second: "2-digit"
      }});
    }}
    var raw = {{}};
    fmt.formatToParts(when).forEach(function (p) {{ raw[p.type] = p.value; }});
    var hour = parseInt(raw.hour, 10);
    if (hour === 24) hour = 0;  // some engines emit "24" at midnight
    return {{
      weekday: raw.weekday,
      year: parseInt(raw.year, 10),
      month: parseInt(raw.month, 10),   // 1-12
      day: parseInt(raw.day, 10),
      hour: hour,
      minute: parseInt(raw.minute, 10),
      second: parseInt(raw.second, 10),
      // fractional second from the underlying instant, for smooth hands
      frac: (when.getMilliseconds() / 1000)
    }};
  }}

  var el = {{
    hh: document.getElementById("hh"),
    mm: document.getElementById("mm"),
    ss: document.getElementById("ss"),
    ampm: document.getElementById("ampm"),
    dayname: document.getElementById("dayname"),
    date: document.getElementById("date"),
    zone: document.getElementById("zone"),
    hHand: document.getElementById("hHand"),
    mHand: document.getElementById("mHand"),
    sHand: document.getElementById("sHand"),
    body: document.getElementById("body"),
    bodyGlow: document.getElementById("bodyGlow"),
    moonShadow: document.getElementById("moonShadow"),
    skyTop: document.getElementById("skyTop"),
    skyBottom: document.getElementById("skyBottom"),
    skyLabel: document.getElementById("skyLabel"),
    calTitle: document.getElementById("calTitle"),
    calDays: document.getElementById("calDays"),
    calPrev: document.getElementById("calPrev"),
    calNext: document.getElementById("calNext"),
    calToday: document.getElementById("calToday")
  }};

  // ================= DIGITAL + ANALOG CLOCKS =================
  function renderClocks(p) {{
    var h12 = p.hour % 12; if (h12 === 0) h12 = 12;
    var ampm = p.hour < 12 ? "AM" : "PM";
    el.hh.textContent = two(h12);
    el.mm.textContent = two(p.minute);
    el.ss.textContent = two(p.second);
    el.ampm.textContent = ampm;
    el.dayname.textContent = (p.weekday || "").toUpperCase();
    el.date.textContent = two(p.day) + " " + MONTHS[p.month - 1] + " " + p.year;
    el.zone.textContent = state.zone;
    document.title = two(h12) + ":" + two(p.minute) + " " + ampm + " \\u00b7 Azzio";

    // Smooth analog hands. Seconds include the fractional part so the hand sweeps.
    var sec = p.second + p.frac;
    var min = p.minute + sec / 60;
    var hr = (p.hour % 12) + min / 60;
    setRot(el.sHand, sec * 6);          // 360/60
    setRot(el.mHand, min * 6);          // 360/60
    setRot(el.hHand, hr * 30);          // 360/12
  }}

  function setRot(node, deg) {{
    node.setAttribute("transform", "rotate(" + deg.toFixed(3) + " 100 100)");
  }}

  // ================= SUN / MOON HORIZON ARC =================
  // A lightweight solar-position calc: from the day-of-year we get the sun's declination
  // and (with a rough latitude) the day length, which sets sunrise/sunset. We then map the
  // fraction of the way through the daylight (or the night) onto the horizon half-circle
  // and ride the body along it -- sun when up, moon when down. Not an ephemeris; just
  // enough to give the end user a real sense of where in the day/night the clock is.
  var DEG = Math.PI / 180;

  function dayOfYear(y, m, d) {{
    var days = [31,28,31,30,31,30,31,31,30,31,30,31];
    var leap = (y % 4 === 0 && y % 100 !== 0) || (y % 400 === 0);
    if (leap) days[1] = 29;
    var n = d;
    for (var i = 0; i < m - 1; i++) n += days[i];
    return n;
  }}

  // Sun declination (radians) for a day-of-year, standard approximation.
  function declination(n) {{
    return 23.44 * DEG * Math.sin(2 * Math.PI * (n + 284) / 365);
  }}

  // Sunrise/sunset as fractions of the day [0,1) in LOCAL solar-ish time, plus a flag if
  // the sun never sets / never rises (polar) so we degrade gracefully.
  function daylight(n, latDeg) {{
    var lat = latDeg * DEG;
    var dec = declination(n);
    var cosH = -Math.tan(lat) * Math.tan(dec);
    if (cosH <= -1) return {{ up: 24, rise: 0, set: 1, polar: "day" }};   // sun always up
    if (cosH >= 1)  return {{ up: 0, rise: 0.5, set: 0.5, polar: "night" }}; // always down
    var H = Math.acos(cosH) / DEG;      // half-day arc in degrees
    var hours = 2 * H / 15;             // daylight hours
    var half = hours / 24 / 2;
    return {{ up: hours, rise: 0.5 - half, set: 0.5 + half, polar: null }};
  }}

  function renderSky(p) {{
    var n = dayOfYear(p.year, p.month, p.day);
    var frac = (p.hour + p.minute / 60 + (p.second + p.frac) / 3600) / 24; // [0,1)
    var dl = daylight(n, state.lat || 31.78);

    var isDay, along;   // along in [0,1] across the visible half-circle
    if (dl.polar === "day") {{ isDay = true; along = frac; }}
    else if (dl.polar === "night") {{ isDay = false; along = frac; }}
    else if (frac >= dl.rise && frac <= dl.set) {{
      isDay = true;
      along = (frac - dl.rise) / (dl.set - dl.rise);
    }} else {{
      isDay = false;
      // Night wraps around midnight: map set->next rise onto [0,1].
      var nightLen = (1 - dl.set) + dl.rise;
      var into = frac > dl.set ? (frac - dl.set) : (1 - dl.set + frac);
      along = nightLen > 0 ? into / nightLen : 0.5;
    }}

    // Place the body on the arc: x sweeps left(30)->right(370), y follows the half-circle
    // apex (radius 170, centre y=200). t=0 at left horizon, t=1 at right horizon.
    var t = Math.max(0, Math.min(1, along));
    var ang = Math.PI * (1 - t);        // pi -> 0  (left -> right, over the top)
    var cx = 200 + 170 * Math.cos(ang);
    var cy = 200 - 170 * Math.sin(ang);

    var group = document.getElementById("bodyGroup");
    group.setAttribute("transform", "translate(" + (cx - 200).toFixed(1) + "," +
                                     (cy - 30).toFixed(1) + ")");

    if (isDay) {{
      el.body.setAttribute("fill", "#ffd257");
      el.bodyGlow.setAttribute("fill", "rgba(255,210,87,0.20)");
      el.moonShadow.setAttribute("opacity", "0");
      el.skyLabel.textContent = "daytime";
    }} else {{
      el.body.setAttribute("fill", "#dfe7f0");
      el.bodyGlow.setAttribute("fill", "rgba(223,231,240,0.12)");
      el.moonShadow.setAttribute("opacity", "0.9");   // carve a crescent
      el.skyLabel.textContent = "night";
    }}

    // Tint the sky: brighter near solar noon, deep at night. Use the sun's height (sin of
    // the arc angle when up) as a 0..1 brightness.
    var height = isDay ? Math.sin(t * Math.PI) : 0;
    var top = mix([11,26,46], [64,132,196], height);     // #0b1a2e -> lighter blue
    var bot = mix([11,15,20], [40,74,110], height * 0.6);
    el.skyTop.setAttribute("stop-color", rgb(top));
    el.skyBottom.setAttribute("stop-color", rgb(bot));
  }}

  function mix(a, b, t) {{
    t = Math.max(0, Math.min(1, t));
    return [a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t, a[2] + (b[2]-a[2])*t];
  }}
  function rgb(c) {{
    return "rgb(" + Math.round(c[0]) + "," + Math.round(c[1]) + "," + Math.round(c[2]) + ")";
  }}

  // ================= NAVIGABLE CALENDAR =================
  // viewY/viewM is the month currently DISPLAYED. It starts on today and the user can
  // page it; "today" (from the live clock) is highlighted only when it is in view. The
  // calendar records nothing -- it is a pure month display.
  var viewY = null, viewM = null;         // set on first tick from the live date
  var today = {{ y: null, m: null, d: null }};

  function buildCalendar() {{
    if (viewY === null) return;
    el.calTitle.textContent = MONTHS[viewM - 1] + " " + viewY;
    el.calDays.textContent = "";

    // Monday-first grid. JS getDay(): 0=Sun..6=Sat -> shift so Monday=0.
    var first = new Date(viewY, viewM - 1, 1);
    var lead = (first.getDay() + 6) % 7;              // blanks before day 1
    var dim = new Date(viewY, viewM, 0).getDate();     // days in this month
    var prevDim = new Date(viewY, viewM - 1, 0).getDate();

    var cells = [];
    // Leading days from the previous month (muted).
    for (var i = 0; i < lead; i++) {{
      cells.push({{ n: prevDim - lead + 1 + i, muted: true, dow: i }});
    }}
    // This month's days.
    for (var d = 1; d <= dim; d++) {{
      cells.push({{ n: d, muted: false, day: d, dow: (lead + d - 1) % 7 }});
    }}
    // Trailing days to fill the last row.
    var tail = (7 - (cells.length % 7)) % 7;
    for (var j = 1; j <= tail; j++) {{
      cells.push({{ n: j, muted: true, dow: (cells.length) % 7 }});
    }}

    var frag = document.createDocumentFragment();
    cells.forEach(function (c) {{
      var cell = document.createElement("div");
      cell.className = "cell" + (c.muted ? " muted" : "");
      // Weekend = Sat/Sun (dow 5,6 in a Monday-first grid).
      if (!c.muted && (c.dow === 5 || c.dow === 6)) cell.className += " weekend";
      if (!c.muted && viewY === today.y && viewM === today.m && c.day === today.d) {{
        cell.className += " today";
        cell.setAttribute("aria-current", "date");
      }}
      cell.textContent = c.n;
      frag.appendChild(cell);
    }});
    el.calDays.appendChild(frag);
    toggleTodayBtn();
  }}

  // Show the "Jump to current" button only while the viewed month is NOT the current one;
  // hide it (native [hidden], so it takes no space) the moment we are back on today's
  // month. Called from buildCalendar, so it stays correct after every navigation and after
  // a midnight date-rollover. Guards until the live date has seeded (today.y set).
  function toggleTodayBtn() {{
    if (!el.calToday) return;
    var onToday = (today.y !== null && viewY === today.y && viewM === today.m);
    el.calToday.hidden = onToday;
  }}

  function goMonth(delta) {{
    if (viewY === null) return;
    var m = viewM + delta;
    var y = viewY;
    while (m < 1) {{ m += 12; y--; }}
    while (m > 12) {{ m -= 12; y++; }}
    viewY = y; viewM = m;
    buildCalendar();
  }}
  function goToday() {{
    if (today.y === null) return;
    viewY = today.y; viewM = today.m;
    buildCalendar();
  }}

  el.calPrev.addEventListener("click", function () {{ goMonth(-1); }});
  el.calNext.addEventListener("click", function () {{ goMonth(1); }});
  el.calTitle.addEventListener("click", goToday);
  if (el.calToday) el.calToday.addEventListener("click", goToday);
  document.addEventListener("keydown", function (e) {{
    if (e.key === "ArrowLeft") goMonth(-1);
    else if (e.key === "ArrowRight") goMonth(1);
    else if (e.key === "Home" || e.key === "t" || e.key === "T") goToday();
  }});

  // ================= MAIN LOOP =================
  var lastDayKey = "";     // rebuild the calendar's "today" when the date rolls over
  function frame() {{
    var p = partsInZone(serverNow());
    renderClocks(p);
    renderSky(p);

    // Track the live date. On first run (or a date rollover), (re)seed today + the view.
    var key = p.year + "-" + p.month + "-" + p.day;
    if (key !== lastDayKey) {{
      var firstRun = (today.y === null);
      var wasOnToday = (!firstRun && viewY === today.y && viewM === today.m);
      today = {{ y: p.year, m: p.month, d: p.day }};
      if (firstRun) {{ viewY = p.year; viewM = p.month; }}
      // If the user was viewing "today"'s month, keep them on it across midnight;
      // otherwise leave their chosen month alone and just refresh the highlight.
      if (wasOnToday) {{ viewY = today.y; viewM = today.m; }}
      buildCalendar();
      lastDayKey = key;
    }}
    requestAnimationFrame(frame);
  }}

  // ================= SERVER RE-SYNC =================
  // Every 60s: corrects clock drift AND picks up a timezone change (the server reads
  // /etc/localtime live, so if the user changes the zone the page follows within a minute,
  // no reload). Also refines the sun-arc latitude for the (new) zone if the server sends
  // one. Never lets a fetch failure break the local tick.
  function resync() {{
    fetch("/api/now", {{ cache: "no-store" }})
      .then(function (r) {{ return r.ok ? r.json() : null; }})
      .then(function (d) {{
        if (!d) return;
        if (d.zone) state.zone = d.zone;
        if (typeof d.lat === "number") state.lat = d.lat;
        skewMs = d.epoch_ms - Date.now();
      }})
      .catch(function () {{ /* offline/hiccup: keep ticking on the last known skew/zone */ }});
  }}

  requestAnimationFrame(frame);
  setInterval(resync, 60000);
  document.addEventListener("visibilitychange", function () {{
    if (!document.hidden) resync();
  }});
}})();"""
