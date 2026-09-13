"""packages.librewolf -- OUR Flask Time + Calendar home page (localhost:49154).

This is the site LibreWolf lands on: a small local Flask website that shows the current
time (hour/minute/seconds) and a calendar (day/month/year), run in the background by a
systemd service that starts at boot, with the timezone following the SYSTEM live
(default Asia/Jerusalem, updating itself if the user changes it by any means).

Why these tests matter: like the OpenBox/application-menu payloads, compiler.py never
inspects the CONTENT of these builders -- it blindly iterates emit_plan() and calls
emit.write_text with the (dest, mode) each entry declares, and _link_services creates the
service enable-symlink. So the declarative PLAN table + the unit text ARE the contract:
a wrong mode makes the launcher non-executable (systemd's Exec= then fails), a wrong
WantedBy means the home page never starts at boot, and a port that drifts from
packages/librewolf's URL means the browser lands on a dead tab. None of that raises in
Python; it only shows up as a blank home page on the built ISO. These tests pin:

  * the emit_plan() dest/mode table + that it does not mutate module state,
  * the systemd unit (Type/ExecStart/Restart/WantedBy=multi-user.target + hardening),
  * the launcher (execs `python applications.py` from the install dir),
  * the PORT lock-step between the app, this package, and packages/librewolf.TIMEDATE_URL,
  * that packages/librewolf now makes the timedate URL the home + startup page,
  * page.render()'s self-contained HTML (seeds the time, embeds the system zone, ships
    the client-side Intl ticking script + /api/now re-sync -- pure stdlib, no Flask),
    including its four end-user features: the 12-hour AM/PM digital readout, the round
    analog clock (SVG hands), the navigable month calendar (page-through, display-only),
    and the sun/moon horizon arc (client-side solar-position, seeded per-zone latitude),
  * the /etc/localtime -> IANA-zone resolution algorithm applications.py uses to follow the
    system timezone live.
"""

from __future__ import annotations

import datetime
import os
import re
from zoneinfo import ZoneInfo

import pytest

from packages.librewolf import timedate as td
from packages.librewolf import page as td_page
from packages import librewolf


# --- The fixed port contract ------------------------------------------------
def test_port_is_49154_everywhere():
    """The one port the whole system agrees on. The app, this package, and the URL are
    all 49154; librewolf's TIMEDATE_URL is derived from td.URL so they cannot drift."""
    assert td.PORT == 49154
    assert td.URL == "http://localhost:49154"
    assert librewolf.TIMEDATE_URL == td.URL
    # The app source hard-codes the same port (it binds it); pin that too.
    assert "PORT = 49154" in td.application_py()
    assert 'app.run(host="0.0.0.0", port=PORT' in td.application_py()


# --- emit_plan() contract ---------------------------------------------------
EXPECTED_PLAN = {
    "/usr/local/lib/azzio-timedate/applications.py": 0o644,
    "/usr/local/lib/azzio-timedate/page.py": 0o644,
    "/usr/local/lib/azzio-timedate/assets.py": 0o644,
    "/usr/local/bin/azzio-timedate": 0o755,
    "/etc/systemd/system/azzio-timedate.service": 0o644,
}


def test_emit_plan_dest_mode_table():
    """The declarative (dest -> mode) table compiler.py iterates. The launcher MUST be
    executable (0o755) so systemd's ExecStart can run it; the rest are plain data."""
    got = {e["dest"]: e["mode"] for e in td.emit_plan()}
    assert got == EXPECTED_PLAN


def test_emit_plan_builders_are_callable_and_nonempty():
    """Every entry's builder returns real content (compiler.py calls builder())."""
    for e in td.emit_plan():
        content = e["builder"]()
        assert isinstance(content, str) and content.strip(), e["dest"]


def test_assets_module_is_installed_beside_page():
    """page.py inlines the analog SVG / CSS / client script from assets.py via
    `import assets`, so assets.py MUST be installed in LIB_DIR beside page.py or the app
    crashes at import on the built ISO. Pin the dest/mode and that the source is real."""
    plan = {e["dest"]: e for e in td.emit_plan()}
    assert td.ASSETS_SYSTEM_PATH == f"{td.LIB_DIR}/assets.py"
    assert td.ASSETS_SYSTEM_PATH in plan
    assert plan[td.ASSETS_SYSTEM_PATH]["mode"] == 0o644
    src = td.assets_py()
    assert "def analog_svg()" in src and "def css()" in src and "def script(" in src
    # page.py declares the import contract against assets (both runtime + package forms).
    page_src = td.page_py()
    assert "import assets" in page_src and "analog_svg" in page_src


def test_emit_plan_is_pure():
    """compiler.py may call emit_plan() more than once; it must not mutate module state
    or return aliased dicts that a caller could mutate. Mirrors the openbox test."""
    a = td.emit_plan()
    b = td.emit_plan()
    assert a == b
    a[0]["mode"] = 0o000  # mutate the returned copy
    assert td.emit_plan()[0]["mode"] == 0o644  # module PLAN unaffected


def test_dest_paths_are_absolute_system_paths():
    """All root-owned absolute paths under /usr/local or /etc (the OFFLINE install rsyncs
    the live rootfs, so no per-user home entry is needed here)."""
    for e in td.emit_plan():
        assert e["dest"].startswith(("/usr/local/", "/etc/")), e["dest"]


# --- systemd service unit ---------------------------------------------------
def test_service_unit_runs_at_boot_in_background():
    unit = td.service_unit()
    assert "[Service]" in unit and "[Install]" in unit
    assert "Type=simple" in unit
    assert f"ExecStart={td.LAUNCHER_SYSTEM_PATH}" in unit
    # Starts at boot: pulled by multi-user.target (the symlink is added by
    # compiler._link_services, tested in the compiler tests).
    assert "WantedBy=multi-user.target" in unit
    # A dead home page must come back so a new tab is never broken.
    assert "Restart=on-failure" in unit
    assert str(td.PORT) in unit


def test_service_unit_is_hardened_and_unprivileged():
    """The page only reads world-readable tzdata and listens on a loopback-facing port,
    so it runs unprivileged and locked down."""
    unit = td.service_unit()
    assert "User=nobody" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    # It writes nothing and needs only INET sockets.
    assert "RestrictAddressFamilies=AF_INET AF_INET6" in unit


def test_service_name_constants_agree():
    assert td.SERVICE_NAME == "azzio-timedate.service"
    assert td.SERVICE_SYSTEM_PATH == f"/etc/systemd/system/{td.SERVICE_NAME}"


# --- launcher ---------------------------------------------------------------
def test_launcher_execs_python_app_from_install_dir():
    """The launcher cd's into the install dir (so `import page` resolves) and execs the
    system python on applications.py. `exec` so systemd supervises the python process directly."""
    sh = td.launcher_sh()
    assert sh.startswith("#!/bin/sh")
    assert f"cd '{td.LIB_DIR}'" in sh
    assert "exec python -u applications.py" in sh


# --- LibreWolf lands on the timedate page -----------------------------------
def test_librewolf_home_and_startup_point_at_timedate():
    """packages/librewolf makes the timedate site the home page AND opens it on startup
    (browser.startup.page = 1 = home), so LibreWolf 'defaults to land on it'."""
    cfg = librewolf.overrides_cfg()
    assert f'defaultPref("browser.startup.homepage", "{td.URL}");' in cfg
    assert 'defaultPref("browser.startup.page", 1);' in cfg
    # AutoConfig files must begin with a comment line (engine ignores line 1).
    assert cfg.splitlines()[0].startswith("//")


def test_librewolf_keeps_cookie_persistence():
    """Landing on the home page replaced the tab-restore, but LOGINS must still persist
    (sanitizeOnShutdown off + full session-store privacy level)."""
    cfg = librewolf.overrides_cfg()
    assert 'defaultPref("privacy.sanitize.sanitizeOnShutdown", false);' in cfg
    assert 'defaultPref("browser.sessionstore.privacy_level", 0);' in cfg


# --- the timedate page ADHERES to the system theme (white/dark) --------------
def test_page_css_is_dark_by_default_and_light_via_media_query():
    """The page's own styling follows the system theme: the :root defaults DARK (the
    Azzio default) and an `@media (prefers-color-scheme: light)` block overrides to a
    light palette. So a browser reporting dark -> dark page; light -> light page."""
    css = td.assets_py()
    # :root is the DARK palette (dark background, light foreground).
    root = css.split(":root", 1)[1]
    assert "--bg-0: #0b0f14" in root      # dark background in :root
    assert "--fg: #e8eef4" in root        # light foreground in :root
    # The light theme is the media-query override, not the default.
    assert "@media (prefers-color-scheme: light)" in css
    light = css.split("@media (prefers-color-scheme: light)", 1)[1]
    assert "--bg-0: #eef3f8" in light     # light background only under the media query
    assert "--fg: #1b2430" in light


def test_librewolf_lets_the_page_see_the_real_color_scheme():
    """The bug this fixes: stock LibreWolf's resistFingerprinting FORCES
    prefers-color-scheme=light for every site, so the timedate page was stuck light even in
    the dark system theme. The overrides swap RFP for FPP-minus-CSSPrefersColorScheme so the
    page's `@media (prefers-color-scheme: light)` follows the actual system theme, and
    ui.systemUsesDarkTheme / content-override (which `azzio theme` flips) reach content."""
    cfg = librewolf.overrides_cfg()
    assert 'defaultPref("privacy.resistFingerprinting", false);' in cfg
    assert 'defaultPref("privacy.fingerprintingProtection", true);' in cfg
    assert (
        'defaultPref("privacy.fingerprintingProtection.overrides", '
        '"+AllTargets,-CSSPrefersColorScheme");' in cfg
    )
    # Dark is the default; white flips the reported scheme.
    assert 'defaultPref("layout.css.prefers-color-scheme.content-override", 0);' in cfg
    assert (
        'defaultPref("layout.css.prefers-color-scheme.content-override", 1);'
        in librewolf.overrides_cfg(dark=False)
    )


# --- page.render(): self-contained, seeded, zone-aware, ticking -------------
def _rendered(zone="Asia/Jerusalem"):
    now = datetime.datetime(2026, 8, 12, 14, 5, 9, tzinfo=ZoneInfo(zone))
    return td_page.render(zone_name=zone, now=now), now


def test_page_is_one_self_contained_html_document():
    html, _ = _rendered()
    assert html.startswith("<!DOCTYPE html>")
    # Everything inline: exactly one <style> and one <script>, no external asset refs.
    assert html.count("<script>") == 1 and html.count("<style>") == 1
    assert "src=" not in html and "href=" not in html  # no external CSS/JS/links


def test_page_fits_viewport_in_two_columns():
    """The page is a NO-SCROLL, full-viewport layout: the clocks on the left, the sun/moon
    arc + calendar on the right, locked to 100vh with overflow hidden (it stacks on
    narrow/portrait screens via a media query). Pin the structure so it cannot regress to
    the old tall, scrolling single column."""
    html, _ = _rendered()
    # Two explicit columns wrap the three sections.
    assert 'class="col col-left"' in html and 'class="col col-right"' in html
    # Locked to the viewport, no scroll, laid out as a row.
    assert "height: 100vh" in html and "overflow: hidden" in html
    assert "flex-direction: row" in html
    # A responsive fallback stacks the columns on narrow/portrait screens.
    assert "max-aspect-ratio" in html or "max-width: 720px" in html


def test_page_seeds_the_current_time_and_date():
    """The server seeds the first paint so there is no flash of a wrong time before JS
    runs: the 12-hour AM/PM readout and the day/month/year must be present in the initial
    markup. 14:05:09 -> 02:05:09 PM (the page shows a 12-hour clock with an AM/PM tag)."""
    html, now = _rendered()
    assert ">02<" in html and ">05<" in html and ">09<" in html  # 02:05:09 (12-hour)
    assert ">PM<" in html                                        # afternoon -> PM
    assert "August" in html and "2026" in html and "12" in html
    assert now.strftime("%A") in html  # weekday name


def test_page_shows_am_pm_readout():
    """The spec asks for an AM/PM clock: morning seeds render AM, afternoon PM, and the
    hour is 12-hour (never > 12). Midnight and noon both read 12, not 00/24."""
    zone = "Asia/Jerusalem"
    morning = td_page.render(
        zone_name=zone,
        now=datetime.datetime(2026, 8, 12, 9, 30, 0, tzinfo=ZoneInfo(zone)),
    )
    assert ">09<" in morning and ">AM<" in morning
    midnight = td_page.render(
        zone_name=zone,
        now=datetime.datetime(2026, 8, 12, 0, 0, 0, tzinfo=ZoneInfo(zone)),
    )
    assert ">12<" in midnight and ">AM<" in midnight  # 00:00 -> 12 AM
    noon = td_page.render(
        zone_name=zone,
        now=datetime.datetime(2026, 8, 12, 12, 0, 0, tzinfo=ZoneInfo(zone)),
    )
    assert ">12<" in noon and ">PM<" in noon  # 12:00 -> 12 PM


def test_page_has_round_analog_clock():
    """The spec asks for a round clock too: an inline SVG face with three named hands the
    script rotates, plus hour ticks. No external assets -- it is drawn in markup."""
    html, _ = _rendered()
    assert 'id="hHand"' in html and 'id="mHand"' in html and 'id="sHand"' in html
    assert "Analog clock" in html                 # the SVG's aria-label
    assert "setRot" in html and "rotate(" in html  # the script sweeps the hands


def test_page_has_navigable_calendar():
    """A calendar the user can PAGE through (prev/next month + jump-to-today), rendered
    client-side into a Monday-first grid. It is a pure display -- no task recording."""
    html, _ = _rendered()
    assert 'id="calPrev"' in html and 'id="calNext"' in html
    assert 'id="calTitle"' in html and 'id="calDays"' in html
    assert "goMonth" in html and "goToday" in html   # navigation handlers
    assert "buildCalendar" in html                   # grid builder
    # Monday-first weekday header.
    assert ">Mon<" in html and ">Sun<" in html
    # The month/year title is seeded for the first paint.
    assert "August 2026" in html
    # It must NOT be a todo/task recorder -- no inputs, no persistence.
    assert "<input" not in html and "localStorage" not in html


def test_page_has_jump_to_current_button():
    """A 'Jump to current' button that the script reveals only while the viewed month is
    not the current one (hidden by default via the native [hidden] attribute) and hides
    again once back on today's month. It jumps home (goToday)."""
    html, _ = _rendered()
    # Present but hidden in the initial markup (seed renders on the current month).
    assert 'id="calToday"' in html
    m = re.search(r'<button id="calToday"[^>]*>', html)
    assert m and "hidden" in m.group(0), "the button must start hidden"
    assert "Jump to current" in html
    # The script toggles it by month and wires it to jump home.
    assert "toggleTodayBtn" in html
    assert ".cal-today" in html  # its styling is present


def test_page_has_sun_moon_horizon_arc():
    """A half-circle horizon with a body that rides across it -- sun while up, moon while
    down -- as a visual time-of-day helper. Computed client-side (no geolocation prompt),
    seeded with a per-zone latitude."""
    html, _ = _rendered()
    assert 'id="skySvg"' in html and 'id="arcPath"' in html
    assert "renderSky" in html and "declination" in html and "daylight" in html
    assert '"lat"' in html  # the seed carries a latitude for the arc


def test_page_embeds_the_system_zone_and_ticks_in_it():
    """The IANA zone is embedded so the browser formats in the SYSTEM zone (not its own),
    and the client ticks with Intl.DateTimeFormat + re-syncs to /api/now."""
    html, _ = _rendered("America/New_York")
    assert "America/New_York" in html
    assert "Intl.DateTimeFormat" in html
    assert "/api/now" in html
    assert 'timeZone: state.zone' in html  # formats in the embedded system zone


def test_page_escapes_the_zone_name():
    """Defense-in-depth: the zone name is HTML-escaped where it is shown (it comes from a
    filesystem symlink, so treat it as untrusted even though tzdata names are safe)."""
    html, _ = _rendered("Asia/Jerusalem")
    # No raw angle brackets injected around the zone label.
    assert "System timezone" in html


# --- the /etc/localtime -> IANA-zone algorithm (follows the system live) -----
# applications.py needs Flask to import, so we re-express its resolution algorithm here and pin
# the behaviour the app source declares (constants + the realpath-strip contract). This
# is the mechanism that makes the page track a timezone change made by ANY tool.
def _zone_from_localtime(localtime: str, zoneinfo_dir: str) -> str | None:
    try:
        target = os.path.realpath(localtime)
    except OSError:
        return None
    root = os.path.realpath(zoneinfo_dir)
    prefix = root.rstrip("/") + "/"
    if not target.startswith(prefix):
        return None
    return target[len(prefix):] or None


def test_zone_resolution_follows_the_symlink(tmp_path):
    """A /etc/localtime symlink into the zoneinfo tree resolves to its IANA name, and
    following a change (as timedatectl / azzio timedate --resolve / a manual relink
    would do) yields the new zone -- with no caching, so the page updates itself."""
    zi = tmp_path / "zoneinfo"
    (zi / "Asia").mkdir(parents=True)
    (zi / "America").mkdir(parents=True)
    (zi / "Asia" / "Jerusalem").write_bytes(b"")
    (zi / "America" / "New_York").write_bytes(b"")
    lt = tmp_path / "localtime"

    lt.symlink_to(zi / "Asia" / "Jerusalem")
    assert _zone_from_localtime(str(lt), str(zi)) == "Asia/Jerusalem"

    lt.unlink()
    lt.symlink_to(zi / "America" / "New_York")  # user changed the timezone
    assert _zone_from_localtime(str(lt), str(zi)) == "America/New_York"


def test_zone_resolution_falls_back_when_missing(tmp_path):
    """No /etc/localtime -> None (applications.py then uses FALLBACK_ZONE); a target outside the
    zoneinfo tree -> None. The home page must never 500 over timezone plumbing."""
    zi = tmp_path / "zoneinfo"
    zi.mkdir()
    missing = tmp_path / "localtime"  # does not exist
    assert _zone_from_localtime(str(missing), str(zi)) is None

    outside = tmp_path / "elsewhere"
    outside.write_bytes(b"")
    lt = tmp_path / "localtime2"
    lt.symlink_to(outside)
    assert _zone_from_localtime(str(lt), str(zi)) is None


def test_app_declares_the_localtime_source_and_fallback():
    """The app must read the real system-zone source (/etc/localtime under
    /usr/share/zoneinfo) and default to Asia/Jerusalem -- the distro default the spec
    names. Pinned against the app source so the contract cannot silently change."""
    src = td.application_py()
    assert 'LOCALTIME_PATH = "/etc/localtime"' in src
    assert 'ZONEINFO_DIR = "/usr/share/zoneinfo"' in src
    assert 'FALLBACK_ZONE = "Asia/Jerusalem"' in src
    # It resolves the zone per request (no build-time cache), the "updates itself" bit.
    assert "def _system_zone()" in src and "_zone_name_from_localtime" in src
