"""GIMP configuration modification -- skip the first-run / introduction dialogs.

GIMP (shipped as the `gimp` package) greets a fresh profile with an introduction message
Azzio wants gone from the very first launch, exactly the way packages/libreoffice
suppresses LibreOffice's first-run popups and packages/vlc suppresses VLC's privacy
dialog:

  * "Tip of the Day"   -- the tips dialog shown on start (config-suppressible: show-tips no).
  * "Welcome to GIMP"  -- the fresh-profile welcome dialog (show-welcome-dialog no).

GIMP LOADS NORMALLY. This is a CONFIG-ONLY modification: it ships a gimprc and nothing else.
There is deliberately NO preload / warm-start / prewarming machinery (an earlier attempt at
that was reverted -- GIMP opens like any other application), NO .desktop Exec override, and
NO window helper. Opening GIMP starts a plain, stock `gimp`; only the intro dialogs are
turned off via GIMP's own configuration.

ALL THREE INTRO DIALOGS ARE SUPPRESSED (this is the fix for the "GIMP welcome popup was
never removed" regression). Earlier this modification suppressed only the Tip of the Day and
the "show each time" welcome via `(show-welcome-dialog no)`, and gave up on the version-gated
"Welcome to GIMP <ver>" ("what's new") window -- a SEPARATE top-level window that
`(show-welcome-dialog no)` does NOT govern. But that window is not un-suppressible: GIMP gates
it on gimprc's `config-version` -- on startup it shows the welcome page whenever config-version
differs from its own GIMP_VERSION (a fresh profile, where the key is absent, ALWAYS differs),
then writes `config-version = GIMP_VERSION` for next time (see GIMP app/app.c). So SEEDING
config-version to the shipped GIMP version makes a first run look like the version was already
seen, and the window never opens. Still config-only (one gimprc, no preload, no X11 helper,
GIMP loads normally) -- we just seed the one key that governs the version gate.

HOW (a preseeded gimprc, the supported analog of libreoffice's registrymodifications.xcu).
GIMP reads per-user options from ~/.config/GIMP/<ver>/gimprc, one `(key value)` s-expression
per line, merged OVER GIMP's built-in defaults (every other option stays stock). GIMP rewrites
this file itself on exit but preserves these keys, so the shipped copy is just the initial
seed. The three keys that turn the intro dialogs off:

  * (config-version "<ver>")  -- pre-declare the shipped version as already-seen, so the
                                 version-gated "Welcome to GIMP <ver>" window never opens.
  * (show-welcome-dialog no)  -- do not show the "Welcome to GIMP" dialog on start.
  * (show-tips no)            -- do not show the "Tip of the Day" dialog.

WHERE IT GOES. ~/.config/GIMP/3.2/gimprc for the live user (compiler.py chowns it 1000:998
and mirrors it into /etc/skel so a Calamares-created user inherits the same quiet first run).
The version dir is GIMP 3.2's REAL per-user config dir: `3.2`, NOT `3.0` (GIMP 3.2 reads
~/.config/GIMP/3.2/gimprc; a 3.0 dir is ignored). GIMP_VERSION_DIR pins it.

Pure standard library (returns a string). compiler.py iterates emit_plan() exactly like
packages/libreoffice and packages/vlc -- one HOME file, owner="home".
"""

from __future__ import annotations

# The live user's home (matches openbox.HOME / the airootfs /home/main tree).
HOME = "/home/main"

# GIMP 3.2's REAL per-user config dir is versioned `3.2` (verified: ~/.config/GIMP/3.0/
# gimprc is ignored, ~/.config/GIMP/3.2/gimprc is read). Pinned as the single source of
# truth for the path; emit_plan() ships the file here and compiler.py mirrors it into
# /etc/skel. XDG_CONFIG_HOME defaults to ~/.config, which the OpenBox session sets.
GIMP_VERSION_DIR = "3.2"
GIMPRC_PATH = f"{HOME}/.config/GIMP/{GIMP_VERSION_DIR}/gimprc"

# The GIMP version the ISO ships (the `gimp` package pinned in the offline cache is
# gimp-3.2.4). This is what GIMP writes to gimprc's `config-version` after a run, and it is
# THE gate for the "Welcome to GIMP <ver>" dialog: on startup GIMP compares the gimprc
# `config-version` to its own GIMP_VERSION and, when they differ (which INCLUDES a fresh
# profile where the key is absent), shows the version welcome/what's-new window -- then
# writes `config-version = GIMP_VERSION` for next time (verified in GIMP's app/app.c:
# `g_object_set (gimp->edit_config, "config-version", GIMP_VERSION, ...)` right after the
# "check if the welcome dialog should be displayed" step). So SEEDING config-version to the
# shipped version makes a first run look like "already seen this version" and the welcome
# window never appears. Kept beside GIMP_VERSION_DIR so both track the shipped GIMP.
GIMP_CONFIG_VERSION = "3.2.4"


def gimprc() -> str:
    """Return the seed gimprc that turns off GIMP's first-run intro dialogs: the Tip of the
    Day, the fresh-profile Welcome, AND the version-gated "Welcome to GIMP <ver>" window.

    Three keys are written; GIMP merges them over its built-in defaults, so every other
    option stays stock. Each line is a `(key value)` s-expression in GIMP's gimprc syntax.
    GIMP rewrites this file on exit but preserves these keys.

      * (config-version "<ver>") -- pre-declare that this profile has already seen the
        shipped GIMP version, so the version welcome/what's-new window (gated on
        config-version != GIMP_VERSION, which a fresh profile always trips) does NOT open.
        This is the piece config-only was missing before: (show-welcome-dialog no) alone
        governs the "show each time" welcome, not the version-change one -- the version gate
        is config-version, so we seed it.
      * (show-welcome-dialog no) -- do not show the Welcome dialog on start.
      * (show-tips no)           -- do not show the Tip of the Day dialog."""
    return f"""\
# Azzio GIMP config -- turn off the first-run intro dialogs (Tip of the Day, the
# fresh-profile Welcome, and the version-gated "Welcome to GIMP <ver>" window). Generated by
# packages/gimp (edit the Python, not this file). GIMP reads these keys over its
# built-in defaults; every other option is left exactly as GIMP ships it, and GIMP rewrites
# this file when an option changes. GIMP itself loads normally -- this only turns off the
# intro dialogs, it does not preload or warm GIMP.
#
# config-version is THE gate for the "Welcome to GIMP <ver>" window: GIMP shows it whenever
# the gimprc config-version differs from its own version (a fresh profile, where the key is
# absent, always differs). Seeding it to the shipped version ({GIMP_CONFIG_VERSION}) makes a
# first run look like the version was already seen, so that window never opens -- the piece
# config alone was missing before (see packages/gimp GIMP_CONFIG_VERSION).
(config-version "{GIMP_CONFIG_VERSION}")
(show-welcome-dialog no)
(show-tips no)
"""


# --- Emit plan --------------------------------------------------------------
# Declarative map (builder -> dest -> mode -> owner), the same shape compiler.py iterates
# for packages/libreoffice and packages/openbox. One HOME file (owner="home"):
# compiler.py chowns it 1000:998 with the rest of /home/main AND mirrors it into /etc/skel
# so a Calamares-created user inherits the same quiet first run. Mode 0644 (plain config).
_CONF = 0o644


def emit_plan() -> list[dict]:
    """Return the emit plan for the GIMP override: a single HOME file at
    ~/.config/GIMP/3.2/gimprc.

    Shape matches libreoffice.emit_plan() (builder/dest/mode/owner) so compiler.py can emit
    it with the same loop (and skel-mirror the home file). Returns a FRESH dict so a caller
    cannot mutate module state."""
    return [
        {
            "builder": gimprc,
            "dest": GIMPRC_PATH,
            "mode": _CONF,
            "owner": "home",
        },
    ]
