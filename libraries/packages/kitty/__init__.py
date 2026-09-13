"""Kitty terminal modification -- approved monochrome icon + an 18pt font_size (matching gedit).

THE FONT SIZE. Besides the icon work described below, this patch ships a partial
~/.config/kitty/kitty.conf (kitty_conf()) whose only setting is `font_size 18`, kept equal
to gedit's editor font size so the terminal and the text editor render at the same size.
kitty merges it over its built-in defaults (every unset option stays stock), exactly the
way packages/vlc ships a partial vlcrc. It is a HOME file (owner "home"), skel-mirrored
like the other per-user configs. The rest of this docstring describes the icon swap.

Kitty (the ONE terminal Azzio ships, bound to Super+Return / opened from the
application menu) draws a cute cat-face mascot by default. Azzio ships the clean
monochrome mark the kitty developers themselves APPROVE for people who do not like the
default cat -- a dark rounded-square tile with a white ">" chevron (see the kitty FAQ,
"I do not like the kitty icon", and the igrmk/whiskers project it points to). Their
approved links are kept in the asset SVG's comments so the IP is credited:
    https://sw.kovidgoyal.net/kitty/faq/#i-do-not-like-the-kitty-icon
    https://github.com/igrmk/whiskers
Kitty ships NO kitty.conf switch to select this icon, so the ONLY supported route is to
REPLACE the icon files the desktop icon loader reads and, for the in-window titlebar
icon, ship ~/.config/kitty/kitty.app.png (kitty loads it at startup to set the window
icon on X11/Wayland -- confirmed via the kitty FAQ).

SINGLE SOURCE OF TRUTH. The glyph lives as a real repo asset,
assets/icons/kitty.svg (git-tracked, survives `git clean -Xdf`, openable/eyeballable),
following the same convention as packages/fastfetch reading paths.ASSETSDIR. This
module does NOT embed the SVG text: it references the asset by path and hands
compiler._emit_apps declarative "copy this asset" / "rasterize this asset to PNG"
entries. The SVG asset is the one place the icon is defined; both the vector system
icon and every PNG (the titlebar icon) are derived from it.

WHERE THE DESKTOP ICON COMES FROM. kitty's .desktop is `Icon=kitty`, which the icon
loader resolves, in order, against the icon-theme dirs and then /usr/share/pixmaps.
The kitty package ships three files that back that name:

    /usr/share/icons/hicolor/scalable/apps/kitty.svg   (the master, vector)
    /usr/share/icons/hicolor/256x256/apps/kitty.png    (a rasterization)
    /usr/share/pixmaps/kitty.png                        (legacy fallback)

We OVERWRITE the scalable SVG with our asset and DELETE the two PNGs so nothing stale
outranks the SVG: with the same-size PNG gone, the scalable SVG is the highest-quality
source the loader has, so every surface (menu tile, Alt-Tab, window icon) renders the
approved mark. Shipping our own file into the airootfs overlay means a `pacman -Syu` of kitty
that reships its own icons cannot silently revert us on the LIVE medium (the overlay
wins at build time); on an installed system a kitty upgrade could re-drop its icon,
which is acceptable -- this is a cosmetic default, and re-running the modification restores it.

THE IN-WINDOW TITLEBAR ICON. The DESKTOP icon (files above) does NOT change the icon
kitty sets on its OWN top-level window at runtime -- that is the cat baked into the
binary. kitty's documented override is ~/.config/kitty/kitty.app.png: if present, kitty
loads it at startup and uses it as the window icon (the top-left titlebar/Alt-Tab image
the WM shows). So we rasterize the SAME asset SVG to a PNG and ship it there (owner
"home", mirrored into /etc/skel), giving the open kitty window the approved mark instead
of the cat. It is rasterized at 128px because X11 caps the OS-window icon at 128x128 --
kitty refuses a larger PNG and falls back to the WM's broken/default icon (see
KITTY_APP_ICON_SIZE).

compiler._emit_apps iterates emit_plan() (builder/dest/mode/owner shape), now honouring
three declarative extras so this module stays pure-data:
  * "asset":  copy assets/<asset> verbatim to dest (the scalable SVG).
  * "render": {"asset","size"} -- rasterize assets/<asset> to a <size>px PNG at dest
              (the titlebar kitty.app.png).
  * "remove": True -- delete dest instead of writing (the two stale cat PNGs).
No package rebuild -- the overlay simply lands on top of the kitty package's files.
"""

from __future__ import annotations

# --- The single-source-of-truth icon asset ---------------------------------
# The kitty-developer-approved monochrome mark (dark rounded tile + white ">" chevron;
# source links credited in the SVG's comments). Referenced by path the same way
# packages/fastfetch references paths.ASSETSDIR assets; the SVG is NOT inlined here so
# the art has ONE definition (the file you can open and eyeball).
ICON_ASSET = "icons/kitty.svg"

# --- Where the desktop icon loader reads `Icon=kitty` from ------------------
# The three files the kitty package ships to back the name "kitty". The SVG is the
# master we overwrite (with our asset); the two PNGs are removed so they cannot outrank
# the SVG.
ICON_SVG_PATH = "/usr/share/icons/hicolor/scalable/apps/kitty.svg"
ICON_PNG_HICOLOR_PATH = "/usr/share/icons/hicolor/256x256/apps/kitty.png"
ICON_PNG_PIXMAP_PATH = "/usr/share/pixmaps/kitty.png"

# --- The in-window titlebar icon kitty loads at startup ---------------------
# ~/.config/kitty/kitty.app.png -- kitty reads this on X11/Wayland and uses it as the
# window icon (the top-left titlebar / Alt-Tab image), overriding the cat baked into the
# binary. Rasterized from ICON_ASSET so it matches the desktop icon exactly. A HOME file
# (owner "home"): compiler.py chowns it 1000:998 and mirrors it into /etc/skel.
HOME = "/home/main"
KITTY_APP_ICON_PATH = f"{HOME}/.config/kitty/kitty.app.png"

# --- The kitty config file (font size) --------------------------------------
# ~/.config/kitty/kitty.conf -- kitty's config file (XDG_CONFIG_HOME defaults to ~/.config,
# which the OpenBox session sets). kitty ships NO kitty.conf; we ship a PARTIAL one that
# only sets font_size, merged over kitty's built-in defaults (every unset option keeps its
# default), exactly the partial-config approach packages/vlc uses for vlcrc. The value is
# 18 to MATCH gedit's editor font size (gedit.GEDIT_FONT_SIZE), so both the terminal and the
# text editor render at the same 18pt. kitty.conf's syntax is `<name> <value>` (space, no
# '='). A HOME file (owner "home"): compiler.py chowns it 1000:998 and mirrors it into
# /etc/skel so a Calamares-created user inherits the same font size.
KITTY_CONF_PATH = f"{HOME}/.config/kitty/kitty.conf"
# font_size: the STOCK (scale-1.0) point size from the single scale source
# (packages.openbox.scale.TERMINAL_EDITOR_FONT_STOCK, == gedit's baseline so "kitty == gedit"). kitty
# renders a pt font at the screen DPI (it reads Xft.dpi), so the GLOBAL SCALE's Xft.dpi channel
# bumps this automatically -- at scale 1.35 (Xft.dpi 130) it renders ~= the old hardcoded 18pt.
# Deriving it from scale.py (not a raw 18) is what keeps ONE source of truth for the scale.
from packages.openbox import scale as _scale  # noqa: E402  (single source of truth for the scale)

KITTY_FONT_SIZE = _scale.TERMINAL_EDITOR_FONT_STOCK   # STOCK pt; DPI channel scales it (== gedit)
# Square size (px) the titlebar PNG is rasterized to. MUST be 128: on X11 the maximum
# OS-window icon is 128x128, and kitty REFUSES a larger one -- it prints "The window icon
# is too large (256x256). On X11 max window icon size is: 128x128" and leaves the window
# with the WM's broken/default icon (the exact titlebar bug that was reported when this was
# 256). 128px fills any titlebar/Alt-Tab surface, so cap it here.
KITTY_APP_ICON_SIZE = 128


def _alt_number_tab_binds() -> str:
    """Return the `map alt+N goto_tab N` lines that make ALT+<number> jump to a tab.

    ALT+1..ALT+9 go to tabs 1..9; ALT+0 goes to tab 10 (the natural end of a ten-key row --
    kitty's own `goto_tab 0` means "the previously active tab", which is NOT what a user
    pressing ALT+0 in a numbered row expects, so 0 is mapped to tab 10 explicitly). kitty's
    goto_tab is 1-based and clamps to the number of open tabs, so ALT+7 with only 3 tabs open
    is a harmless no-op rather than an error. Generated from a loop so the 10 lines stay
    consistent and a test can regenerate the exact expected block."""
    # digit -> tab number: 1..9 map to themselves, 0 maps to tab 10.
    pairs = [(str(d), d) for d in range(1, 10)] + [("0", 10)]
    return "\n".join(f"map alt+{digit} goto_tab {tab}" for digit, tab in pairs)


def kitty_conf() -> str:
    """Return the partial ~/.config/kitty/kitty.conf that sets the terminal font size, the
    tab-bar position + new/close-tab binds, and the ALT+<number> tab-switch keybinds.

    font_size is merged over kitty's built-in defaults, so every other kitty option stays
    stock (the same partial-config approach packages/vlc uses for vlcrc). kitty.conf
    syntax is `<name> <value>` (space-separated, no '='). The size is kept equal to gedit's
    editor font size so the terminal and the editor match.

    Tabs (PROMPT): `tab_bar_edge top` puts the tab bar ABOVE the content (kitty defaults to
    the bottom); `ctrl+shift+t` opens a new tab (already kitty's default, pinned per PROMPT);
    `ctrl+w` closes the current tab (kitty's own close-tab default is ctrl+shift+q, so this
    bind is what actually changes behaviour -- `close_tab` closes the current tab and all its
    windows, which is the intended "close tab").

    The `map alt+N goto_tab N` lines make ALT+1..ALT+9 select tabs 1..9 and ALT+0 select tab
    10 (see _alt_number_tab_binds)."""
    return f"""\
# Azzio kitty overrides. Generated by packages/kitty (edit the Python, not this file).
# kitty reads these OVER its built-in defaults; every other kitty setting is left exactly
# as kitty ships it. font_size is {KITTY_FONT_SIZE} to match gedit's editor font size.
font_size {KITTY_FONT_SIZE}

# Tabs: put the tab bar on TOP (kitty defaults to the bottom). ctrl+shift+t opens a new tab
# (kitty's own default, pinned here per the PROMPT); ctrl+w closes the current tab (kitty's
# default close is ctrl+shift+q -- this bind is the behaviour change the PROMPT asks for).
tab_bar_edge top
map ctrl+shift+t new_tab
map ctrl+w close_tab

# ALT+<number> switches tabs: alt+1..alt+9 -> tabs 1..9, alt+0 -> tab 10. goto_tab is 1-based
# and clamps to the open-tab count, so a binding past the last tab is a harmless no-op.
{_alt_number_tab_binds()}
"""


# --- Emit plan --------------------------------------------------------------
# Declarative map (builder -> dest -> mode -> owner), the same shape compiler.py
# iterates for packages/openbox and packages/librewolf, PLUS the "asset"/"render"/"remove"
# extras documented in the module docstring. The SVG entry COPIES our asset to the
# scalable system path; the two PNG entries carry "remove": True (they are deleted so the
# scalable SVG wins); the kitty.app.png entry RENDERS the asset to a PNG in the live
# user's kitty config (owner "home", skel-mirrored) for the in-window titlebar icon; the
# kitty.conf entry writes the partial font-size config (owner "home", skel-mirrored).
_CONF = 0o644


def emit_plan() -> list[dict]:
    """Return the emit plan for the kitty icon: copy our approved-mark SVG asset over the
    system scalable icon, remove the two stale cat PNGs that would outrank it, and rasterize
    the same asset to ~/.config/kitty/kitty.app.png so the open kitty WINDOW's titlebar icon
    is the approved mark too.

    Shape matches openbox.emit_plan()/librewolf.emit_plan() (builder/dest/mode/owner),
    with the declarative extras compiler._emit_apps honours: "asset" (copy an asset file),
    "render" (rasterize an SVG asset to a PNG), and "remove" (delete the dest). builder is
    None on every entry -- there is no generated text; the icon's single source of truth is
    the SVG asset. Returns FRESH dicts so a caller cannot mutate module state."""
    return [
        {
            # Overwrite the system scalable icon with our asset SVG (the master the loader
            # rasterizes once the same-size PNG is removed).
            "builder": None,
            "asset": ICON_ASSET,
            "dest": ICON_SVG_PATH,
            "mode": _CONF,
            "owner": "root",
        },
        {
            # Remove the stale 256px cat PNG so it cannot outrank the scalable SVG.
            "builder": None,
            "dest": ICON_PNG_HICOLOR_PATH,
            "mode": _CONF,
            "owner": "root",
            "remove": True,
        },
        {
            # Remove the legacy pixmap cat PNG for the same reason.
            "builder": None,
            "dest": ICON_PNG_PIXMAP_PATH,
            "mode": _CONF,
            "owner": "root",
            "remove": True,
        },
        {
            # The in-window titlebar icon: rasterize the SAME asset to a PNG kitty loads at
            # startup. HOME file (owner "home") so compiler.py chowns it and mirrors it into
            # /etc/skel for the installed user.
            "builder": None,
            "render": {"asset": ICON_ASSET, "size": KITTY_APP_ICON_SIZE},
            "dest": KITTY_APP_ICON_PATH,
            "mode": _CONF,
            "owner": "home",
        },
        {
            # The kitty config file: a partial kitty.conf setting only font_size (18, to
            # match gedit). Text builder, HOME file (owner "home") so compiler.py chowns it
            # and mirrors it into /etc/skel -- same handling as vlc's vlcrc.
            "builder": kitty_conf,
            "dest": KITTY_CONF_PATH,
            "mode": _CONF,
            "owner": "home",
        },
    ]
