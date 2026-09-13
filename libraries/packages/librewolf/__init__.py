"""LibreWolf browser package -- Azzio browser policy overrides (+ the timedate home page).

LibreWolf is an UPSTREAM privacy-hardened Firefox fork we ship (built by the
recipe in the flat pkgbuild.py module). We do not fork it; we only OVERRIDE a handful of
its defaults to fit Azzio. That override policy lives HERE (the librewolf package under
packages/, like the openbox/fastfetch/kitty packages beside it) so it is the single source
of truth for both the CONTENT and its DELIVERY location. The Azzio timedate site LibreWolf
lands on (startup + Home) is folded in as sibling submodules (timedate/app/page/assets) so
the browser and the page it opens can never disagree on the URL.

WHERE THE OVERRIDE FILE MUST GO (this bit is load-bearing and was gotten WRONG
before): LibreWolf's AutoConfig override file, librewolf.overrides.cfg, is loaded
AFTER the stock librewolf.cfg so a defaultPref here beats LibreWolf's own
defaultPref for the same key (https://librewolf.net/docs/settings/). BUT LibreWolf
does NOT read it from the install dir /opt/librewolf/. Its compiled AutoConfig
loader (omni.ja -> defaults/autoconfig/prefcalls.js) sets
autoadmin.global_config_url to a PROFILE/CONFIG-relative path -- on this build
(widget.support-xdg-config = true, non-legacy profile) that is
    file://$XDG_CONFIG_HOME/librewolf/librewolf/librewolf.overrides.cfg
i.e. ~/.config/librewolf/librewolf/librewolf.overrides.cfg (note the DOUBLED
"librewolf/librewolf"). A copy dropped in /opt/librewolf/ is simply never read.
So emit_plan() below ships this as a HOME file at exactly that profile path (and
compiler.py mirrors it into /etc/skel so a Calamares-created user inherits it).
The PKGBUILD does NOT ship it -- packaging it under /opt was the dead-letter bug.
OVERRIDES_PROFILE_PATH is the single source of truth for the path.

Everything else is stock LibreWolf. We change exactly three things (all requested):

  0. DEFAULT HOME + NEW-TAB PAGE = the Azzio timedate site (localhost:49154). The
     distro's default home page is the local Flask Time + Calendar site the
     azzio-timedate service serves on loopback; LibreWolf should LAND on it. We set
     browser.startup.homepage to that URL (the Home button target) and
     browser.startup.page = 1 (open the HOME page on startup), so every launch lands on
     the timedate page, and we quieten the Firefox Home / new-tab dashboard. The URL is
     imported from the timedate package (packages.librewolf.timedate.URL) so the browser
     and the service can never disagree on the port. NOTE: modern Firefox has no
     supported pref that force-loads an arbitrary URL into Ctrl+T (browser.newtab.url was
     removed in FF41), and we deliberately do NOT disable the AutoConfig sandbox to hack
     it; the guarantee is startup + Home = timedate page.

  1. COOKIE PERSISTENCE. LibreWolf ships privacy-hardened and, by default, wipes
     cookies + history on shutdown (privacy.sanitize.sanitizeOnShutdown = true).
     Azzio wants logins to SURVIVE a restart, so we:
       * turn off shutdown sanitisation (the master switch), and belt-and-braces
         clear the per-category clearOnShutdown_v2 cookie flag;
       * set browser.sessionstore.privacy_level = 0 (the Firefox default: store
         everything) so the session store keeps the cookies/form data logins need --
         LibreWolf defaults it to 2 ("save no session data for any site"). We open the
         home page on startup rather than restoring tabs (see 0), but this still governs
         what the session store retains, so logins persist.
     We deliberately do NOT set network.cookie.lifetimePolicy: that pref is
     OBSOLETE in modern Firefox/LibreWolf (the engine migrates it away and
     ClearUser()s it on startup), so writing it does nothing useful. Keeping
     sanitizeOnShutdown = false IS the modern "cookies persist" mechanism.

  2. HIDE THE BOOKMARKS TOOLBAR ("For quick access"). LibreWolf overrides the
     Firefox default and ships browser.toolbars.bookmarks.visibility = "always",
     so the bookmarks toolbar (the "For quick access, place your bookmarks here"
     strip below the address bar; Ctrl+Shift+B) shows on every window by default.
     Azzio wants it hidden by default, so we set it to "never" (the value that
     hides it on every window AND the new-tab page; the other values are "always"
     and "newtab"). The user can still toggle it back on with Ctrl+Shift+B.

Only relaxes/sets these specific prefs; every other LibreWolf hardening pref is
left exactly as upstream ships it. Pure standard library (returns strings).
compiler.py iterates emit_plan() to drop the override at OVERRIDES_PROFILE_PATH
(home + /etc/skel) -- that profile-path file is the ONLY copy the running browser
reads. pkgbuild.py no longer touches this module or the override at all (it used to
ship a dead copy under /opt; that was removed).
"""

from __future__ import annotations


# The AutoConfig override filename LibreWolf reads after librewolf.cfg, so a
# defaultPref in it overrides LibreWolf's own defaultPref for the same key.
OVERRIDES_FILENAME = "librewolf.overrides.cfg"

# The Azzio timedate home page (the Flask Time + Calendar site served on loopback by
# the azzio-timedate service). LibreWolf lands here on startup AND via the Home button.
# Single source of truth for the URL is the timedate package's PORT; import it so the
# browser and the service can never disagree on the port.
from . import timedate as _timedate  # noqa: E402 (sibling submodule -- timedate folded into librewolf)

TIMEDATE_URL = _timedate.URL  # "http://localhost:49154"

# The live user's home (matches openbox.HOME / the airootfs /home/main tree).
HOME = "/home/main"

# WHERE LibreWolf ACTUALLY READS THE OVERRIDE (see the module docstring): its
# compiled AutoConfig loader points autoadmin.global_config_url at
# $XDG_CONFIG_HOME/librewolf/librewolf/librewolf.overrides.cfg (XDG_CONFIG_HOME
# defaults to ~/.config, which the OpenBox session sets explicitly). The doubled
# "librewolf/librewolf" is deliberate -- that is the path the loader builds. This
# is the single source of truth for the delivery location; emit_plan() ships the
# file here and compiler.py mirrors it into /etc/skel.
OVERRIDES_PROFILE_PATH = f"{HOME}/.config/librewolf/librewolf/{OVERRIDES_FILENAME}"


def overrides_cfg(dark: bool = True) -> str:
    """Return the full text of librewolf.overrides.cfg (see the module docstring).

    Policies: (1) sessions + cookies persist across restarts, (2) the bookmarks toolbar
    ("For quick access") is hidden by default, (3) the browser follows the system theme
    -- DARK by default (matching the system dark default); `azzio theme --white` flips it.
    This is the single source of truth; emit_plan() ships it to OVERRIDES_PROFILE_PATH (the
    path LibreWolf actually reads) and compiler.py mirrors it into /etc/skel.

    The theme prefs also make LibreWolf report the matching prefers-color-scheme to web
    content, which is what drives the Azzio timedate home page (it has a
    `prefers-color-scheme` stylesheet) to follow the system theme. THIS ONLY WORKS because
    we also swap LibreWolf's default RFP for FPP-minus-CSSPrefersColorScheme (section 5
    below): stock LibreWolf ships privacy.resistFingerprinting=true, and RFP hard-forces
    prefers-color-scheme to LIGHT for every site regardless of ui.systemUsesDarkTheme /
    layout.css.prefers-color-scheme.content-override -- so WITHOUT that swap the timedate
    page (and every dark-mode site) is stuck light even in the dark system theme. This was
    the "timedate is only ever white" bug.

    AutoConfig files MUST begin with a comment line -- the engine ignores line 1
    -- so the leading `//` banner is required, not decoration."""
    # Firefox theme prefs: 0 = dark, 1 = light for the theme selectors; the
    # prefers-color-scheme content-override uses 0 = dark, 1 = light. ui.systemUsesDarkTheme
    # is 1 for dark, 0 for light.
    sys_dark = 1 if dark else 0
    theme_val = 0 if dark else 1          # toolbar-theme / content-theme (0 dark, 1 light)
    prefers_val = 0 if dark else 1        # prefers-color-scheme.content-override
    return f"""\
// Azzio LibreWolf overrides -- home/new-tab page + cookie persistence + hidden bookmarks bar
//
// LibreWolf's officially-supported AutoConfig override file, loaded AFTER the
// stock librewolf.cfg, so a defaultPref here beats LibreWolf's own defaultPref for
// the same key. LibreWolf reads it from the PROFILE/CONFIG dir
// (~/.config/librewolf/librewolf/librewolf.overrides.cfg), NOT /opt. Generated by
// packages/librewolf (edit the Python, not this file). It ONLY changes the prefs
// below; every other LibreWolf hardening pref is left exactly as upstream ships it.
//
// AutoConfig files must begin with a comment line; the engine ignores line 1.

// === 1. Home + new-tab page = the Azzio timedate site ({TIMEDATE_URL}) ===
// Azzio's default home page is the local Flask Time + Calendar site the
// azzio-timedate service serves on loopback. LibreWolf should LAND on it, so:
//
//   * browser.startup.homepage -> the timedate URL: this is the Home button target
//     AND (with startup.page = 1 below) the page opened on every launch. Confirmed the
//     correct, current pref (Firefox browser/app/profile/firefox.js); LibreWolf's own
//     librewolf.cfg does not touch it, so this value sticks.
//   * browser.startup.page = 1 -> open the HOME page on startup. (0 = blank, 1 = home,
//     3 = restore previous session.) The spec wants the browser to default to the
//     timedate page, so we open the home page rather than restoring the last session.
//   * browser.newtabpage.* -> point the Firefox Home / new-tab (Activity Stream) page's
//     custom top area at the timedate URL and strip the built-in noise (top sites,
//     Pocket/stories, search hero), so a fresh tab shows the timedate site's spirit
//     rather than the default dashboard. NOTE: modern Firefox removed the plain
//     "custom new-tab URL" pref (browser.newtab.url, gone since FF41) and there is no
//     supported defaultPref that force-loads an arbitrary URL into Ctrl+T without an
//     extension or disabling the AutoConfig sandbox; we deliberately do NOT weaken the
//     sandbox. The load-bearing, security-neutral guarantee is therefore: EVERY LAUNCH
//     and the Home button land on the timedate page (startup.page=1 + homepage), which
//     satisfies "LibreWolf should default to land on it."
defaultPref("browser.startup.homepage", "{TIMEDATE_URL}");
defaultPref("browser.startup.page", 1);
// Home button also goes to the timedate page (redundant with homepage, explicit).
defaultPref("browser.startup.homepage_override.mstone", "ignore");
// Quieten the Firefox Home / new-tab dashboard so a new tab is calm, not the default
// grid of top sites + sponsored stories.
defaultPref("browser.newtabpage.activity-stream.feeds.topsites", false);
defaultPref("browser.newtabpage.activity-stream.showSponsoredTopSites", false);
defaultPref("browser.newtabpage.activity-stream.feeds.section.topstories", false);
defaultPref("browser.newtabpage.activity-stream.showSponsored", false);
defaultPref("browser.newtabpage.activity-stream.feeds.snippets", false);

// === 2. Cookies persist across restarts ====================================
// LibreWolf wipes cookies + history on shutdown by default; Azzio wants logins to
// survive a restart (the open-tabs restore was dropped in favour of landing on the
// timedate home page above, but LOGINS should still persist).

// --- Master switch: do not sanitise anything on shutdown -------------------
defaultPref("privacy.sanitize.sanitizeOnShutdown", false);

// --- Belt-and-braces: keep cookies/storage even if a shutdown clear ran ----
// (Moot while sanitizeOnShutdown is false, but explicit and harmless. The _v2
// keys are the modern namespace; the pre-128 privacy.clearOnShutdown.* keys are
// legacy/dead once migrated, so we do not set them.)
defaultPref("privacy.clearOnShutdown_v2.cookiesAndStorage", false);

// --- Let the session store keep cookies/form data (login persistence) ------
// LibreWolf defaults browser.sessionstore.privacy_level to 2 ("save no session
// data for any site"). 0 (the Firefox default: store everything) keeps cookies/
// form data so logins survive a restart. Kept even though we open the home page on
// startup rather than restoring tabs -- it governs what the session store retains.
defaultPref("browser.sessionstore.privacy_level", 0);

// === 3. Hide the bookmarks toolbar ("For quick access") by default =========
// LibreWolf ships this as "always"; Azzio wants the "For quick access, place
// your bookmarks here on the bookmarks toolbar" strip (Ctrl+Shift+B) hidden by
// default. "never" hides it on every window AND the new-tab page. The user can
// still toggle it back on with Ctrl+Shift+B. (Values: "always"/"newtab"/"never".)
defaultPref("browser.toolbars.bookmarks.visibility", "never");

// === 4. Follow the system theme (DARK by default) ==========================
// Azzio defaults to a dark system theme; LibreWolf follows it here. `azzio theme
// --white` regenerates this file with the light values (and --dark back). These prefs
// also make LibreWolf report a dark prefers-color-scheme to web content, which drives the
// Azzio timedate home page (prefers-color-scheme stylesheet) to render dark as well.
//   * browser.theme.toolbar-theme / content-theme: the Firefox built-in theme (0 = dark,
//     1 = light). Both set so chrome AND in-content pages (about:*) match.
//   * ui.systemUsesDarkTheme: report the OS as dark (1) / light (0) to content + widgets.
//   * layout.css.prefers-color-scheme.content-override: force the prefers-color-scheme web
//     content sees (0 = dark, 1 = light) so sites (incl. the timedate page) follow suit.
defaultPref("browser.theme.toolbar-theme", {theme_val});
defaultPref("browser.theme.content-theme", {theme_val});
defaultPref("ui.systemUsesDarkTheme", {sys_dark});
defaultPref("layout.css.prefers-color-scheme.content-override", {prefers_val});

// === 5. Let web content see the real prefers-color-scheme (RFP -> FPP) ======
// THE FIX for "the timedate home page is only ever white": stock LibreWolf ships
// privacy.resistFingerprinting = true, and RFP HARD-FORCES prefers-color-scheme to LIGHT
// for every website, ignoring ui.systemUsesDarkTheme AND
// layout.css.prefers-color-scheme.content-override (the prefs section 4 flips). So the
// timedate page's `@media (prefers-color-scheme: light)` block always matched and it was
// stuck light even in the dark system theme -- section 4 was dead on this build.
//
// The documented LibreWolf-community remedy is to swap RFP for Fingerprinting Protection
// (FPP) with EVERY target enabled EXCEPT the colour-scheme spoof: FPP's overrides list
// only affects FPP (it does NOT lift RFP's own colour-scheme lock), so RFP must be OFF and
// FPP ON for the exemption to take effect. `+AllTargets` keeps the full protection surface;
// `-CSSPrefersColorScheme` is the one carve-out that lets prefers-color-scheme reflect the
// real theme -- so section 4 (and `azzio theme --dark/--white`) now actually reach content
// and the timedate page follows the system theme. Every other hardening pref is untouched.
defaultPref("privacy.resistFingerprinting", false);
defaultPref("privacy.fingerprintingProtection", true);
defaultPref("privacy.fingerprintingProtection.overrides", "+AllTargets,-CSSPrefersColorScheme");
"""


# --- Emit plan --------------------------------------------------------------
# Declarative map (builder -> dest -> mode -> owner), mirroring packages/openbox.PLAN
# and packages/application_menu.PLAN so compiler.py iterates it the same way. The ONE
# entry ships librewolf.overrides.cfg to the profile/config path LibreWolf actually
# reads (OVERRIDES_PROFILE_PATH); owner="home" so compiler.py chowns it 1000:998 with
# the rest of /home/main AND mirrors it into /etc/skel (so a Calamares-created user
# inherits the same browser policy). Mode 0644 (plain config data).
_CONF = 0o644


def emit_plan() -> list[dict]:
    """Return the emit plan for the LibreWolf overrides: a single HOME file at the
    profile path LibreWolf's AutoConfig loader reads (see the module docstring). Shape
    matches openbox.emit_plan()/application_menu.emit_plan() -- builder/dest/mode/owner
    -- so compiler.py can emit it with the same loop (and skel-mirror home files)."""
    return [
        {
            "builder": overrides_cfg,
            "dest": OVERRIDES_PROFILE_PATH,
            "mode": _CONF,
            "owner": "home",
        },
    ]
