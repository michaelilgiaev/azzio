"""The Azzio GLOBAL UI SCALE -- ONE source of truth every app (installed or not) obeys.

THE PROBLEM (PROMPT). The desktop "felt like" 1.35 scale, but that was a FICTION assembled from
a few independent hand-tuned font constants (the app-menu theme.h points, kitty/gedit font sizes,
the OpenBox titlebar font); EVERYTHING ELSE rendered at stock size. There was NO shared scale --
no Xft.dpi, no gtk-xft-dpi, no GDK_SCALE anywhere.

THE FIX. A single scale value lives HERE (GLOBAL_SCALE). It is carried to every app by the
STANDARD, app-agnostic desktop scaling channels, so ANY conformant app picks it up automatically
(no per-app font pokes):

  * X resources: Xft.dpi = round(96 * scale) and Xcursor.size = round(24 * scale), written to
    ~/.Xresources and loaded with `xrdb` at session start (packages/openbox .xinitrc). GTK,
    Qt and most toolkits read the X DPI from here; the cursor scales too.
  * GTK: gtk-xft-dpi = Xft.dpi * 1024 (GTK's millipoint unit) in ~/.config/gtk-3.0/settings.ini
    and gtk-4.0 (packages/azzio/theme.py + the openbox default). GTK3/4 scale ALL point sizes
    (fonts, and via the theme, most metrics) by this -- so every GTK app, including our own
    application menu, grows with the scale without shipping fixed points.
  * Session env (exported from the OpenBox session so every child inherits it): GDK_SCALE=1
    (the integer part -- we keep it 1 and do the fractional part via DPI, since GDK_SCALE only
    takes integers and 1.35 is fractional), GDK_DPI_SCALE is deliberately NOT set (it would be a
    SECOND font multiplier ON TOP of gtk-xft-dpi and double-scale), and the Qt-over-gtk3 bridge
    gets QT_SCALE_FACTOR=<scale> + QT_AUTO_SCREEN_SCALE_FACTOR=0 (explicit fractional Qt scale;
    auto-detect off so it does not fight the explicit factor) + QT_ENABLE_HIGHDPI_SCALING=1.

WHY DPI (not GDK_SCALE) FOR THE FRACTIONAL PART. On X11 GDK_SCALE is INTEGER-only (1, 2, ...), so
it cannot express 1.35. Xft.dpi / gtk-xft-dpi give smooth FRACTIONAL scaling of point sizes,
which is what actually yields a clean 1.35 on this X11 + OpenBox + GTK desktop. So the whole
fractional scale rides on the DPI channels, GDK_SCALE stays 1, and Qt gets an explicit factor.

RECONCILING THE OLD HAND-TUNED CONSTANTS. Each app that was independently bumped now DERIVES from
this scale so "at scale 1.0 it is stock, at 1.35 it matches today's look":
  * GTK apps (the application menu, gedit's UI, the file manager) scale their POINT fonts automatically via
    gtk-xft-dpi, so their font constants become STOCK (scale-1.0) values -- the DPI channel does
    the bump. (The app menu ALSO scales its fixed-PIXEL window/icon dims via ui_px(), since
    gtk-xft-dpi scales points, not pixels -- see application_menu.)
  * kitty is DPI-aware (it reads the X Xft.dpi), so its font_size becomes STOCK too and Xft.dpi
    scales it. gedit's editor-font is an explicit pt size that pango renders at the DPI, so it is
    STOCK as well. Both end up visually equal at any scale (the "kitty == gedit" invariant).
  * the OpenBox titlebar font does NOT read the X DPI (OpenBox renders it at a fixed size), so it
    stays EXPLICITLY scaled: pt(OPENBOX_TITLE_STOCK).
  * the file manager's zoom + em font already compose with the scale (relative), so they are unchanged.

CHANGING THE SCALE LATER. `azzio display scale <factor>` (packages/azzio/display) rewrites the
ONE value's downstream files (.Xresources, settings.ini, the session env) and re-applies it live
(re-run xrdb, re-export), so a scale change propagates everywhere -- the file manager (which composes) and
the app menu (once it reads the shared channel) included. The SCALE_OPTIONS below are the choices
the Display screen offers.

Pure standard library (returns numbers/strings). A test pins the channel math + the derivations.
"""

from __future__ import annotations

# THE ONE SCALE. 1.35 is the Azzio default (what the desktop was hand-tuned to look like). A
# `azzio display scale` change rewrites the downstream files from a chosen SCALE_OPTIONS value;
# this constant is the build-time default that seeds them.
GLOBAL_SCALE = 1.35

# The scale choices the Display screen offers (PROMPT: e.g. 1.00, 1.25, 1.35, 1.50, 1.75, 2.00).
SCALE_OPTIONS: tuple[float, ...] = (1.00, 1.25, 1.35, 1.50, 1.75, 2.00)

# The un-scaled reference DPI and cursor size (the freedesktop stock values at scale 1.0).
BASE_DPI = 96
BASE_CURSOR = 24


def xft_dpi(scale: float = GLOBAL_SCALE) -> int:
    """Xft.dpi for a scale: round(96 * scale). What X clients read as the screen DPI."""
    return round(BASE_DPI * scale)


def gtk_xft_dpi(scale: float = GLOBAL_SCALE) -> int:
    """gtk-xft-dpi for a scale: Xft.dpi * 1024 (GTK's 1/1024-point unit). GTK scales all point
    sizes by this, so every GTK app (incl. our menu) grows with the scale."""
    return xft_dpi(scale) * 1024


def xcursor_size(scale: float = GLOBAL_SCALE) -> int:
    """Xcursor.size for a scale: round(24 * scale). Scales the mouse cursor with the UI."""
    return round(BASE_CURSOR * scale)


def gdk_scale() -> int:
    """GDK_SCALE -- the INTEGER scale part. Always 1: 1.35 is fractional, so the whole fractional
    scale rides on the DPI channels and GDK_SCALE stays 1 (it cannot express a fraction)."""
    return 1


def qt_scale_factor(scale: float = GLOBAL_SCALE) -> str:
    """QT_SCALE_FACTOR for a scale -- an explicit fractional Qt scale for the Qt-over-gtk3 apps.
    Formatted compactly (e.g. "1.35")."""
    return f"{scale:g}"


def ui_px(stock_px: int, scale: float = GLOBAL_SCALE) -> int:
    """Scale a FIXED-PIXEL UI dimension (the app menu's window/icon sizes) by the scale --
    gtk-xft-dpi scales POINTS, not pixels, so pixel dims are scaled here at build time from the
    same source. round() keeps them integer."""
    return round(stock_px * scale)


def pt(stock_pt: int, scale: float = GLOBAL_SCALE) -> int:
    """Scale an explicitly-scaled POINT size (the OpenBox titlebar font, which does NOT read the
    X DPI) by the scale. GTK/kitty/gedit point fonts do NOT use this -- they stay stock and the
    DPI channel scales them; only the DPI-blind OpenBox titlebar is scaled here."""
    return round(stock_pt * scale)


# --- The per-app STOCK (scale-1.0) baselines, the single source the app modules import --------
# Each is chosen so `baseline * GLOBAL_SCALE` (or the DPI channel's scaling) reproduces the
# hand-tuned "looks right at 1.35" value the modules shipped before -- so at scale 1.0 the app is
# stock and at 1.35 it matches today's look (PROMPT). Keeping them HERE (not in each module) is
# what makes the scale a single source: a module that wants a size imports the baseline + the
# helper, and a test fails if a raw font/pixel size is reintroduced in a module instead.

# kitty + gedit render a POINT font at the screen DPI (they read Xft.dpi), so they take the STOCK
# size and the DPI channel scales it. 13pt @ 96dpi is stock; at Xft.dpi=130 (scale 1.35) it
# renders ~= the old hardcoded 18pt @ 96dpi. kitty stays equal to gedit (the "kitty == gedit"
# invariant) because both use this one baseline.
TERMINAL_EDITOR_FONT_STOCK = 13     # was hardcoded 18 (== round(13 * 1.35))

# The OpenBox titlebar font is DPI-BLIND (OpenBox renders it at a fixed pt), so it is scaled
# EXPLICITLY via pt(). Stock 7 -> pt(7) == 9: the titlebar was shrunk 25% (12 * 0.75 == 9),
# so the whole bar (OpenBox sizes the min/max/close buttons to the label) came down with it.
OPENBOX_TITLE_FONT_STOCK = 7        # pt(7) == 9 (12 * 0.75; the 25%-smaller titlebar font)

# The application menu is GTK: its POINT fonts scale via gtk-xft-dpi automatically, so these are
# STOCK sizes (the DPI channel bumps them). Its fixed-PIXEL dims are scaled by ui_px() instead
# (gtk-xft-dpi scales points, not pixels). The stock font sizes reproduce the old 13/10/13/12 at
# scale 1.35.
# NB: the app-row/power fonts are drawn on the DPI-BLIND pango-cairo path (see theme.h), so they
# are wrapped in AZ_SCALED() and their stock is old/1.35. APP_TYPE uses 8 (not 7) so
# AZ_SCALED(8)@1.35 == 11 keeps the subtitle-to-name size RATIO (~0.79) close to the old 10/13
# (~0.77); stock 7 would drop it to 9/14 (~0.64), a visibly thinner subtitle. The SEARCH font is
# on the DPI-aware GtkEntry path, so its stock (10) is what the DPI channel scales.
MENU_FONT_APP_NAME_STOCK = 10       # was 13; AZ_SCALED -> 14 @1.35
MENU_FONT_APP_TYPE_STOCK = 8        # was 10; AZ_SCALED -> 11 @1.35 (keeps the subtitle ratio)
MENU_FONT_SEARCH_STOCK = 10         # was 13; DPI-scaled (GtkEntry)
MENU_FONT_POWER_STOCK = 9           # was 12; AZ_SCALED -> 12 @1.35


def _scale_ratio(scale: float = GLOBAL_SCALE) -> tuple[int, int]:
    """The scale as an exact integer NUM/DEN ratio (e.g. 1.35 -> 135/100), for the C app menu's
    compile-time integer arithmetic (no float in the tiny header). Reduced is not required --
    the menu multiplies then integer-divides, so 135/100 is fine."""
    # Two decimal places cover the SCALE_OPTIONS (1.00 .. 2.00). Round to avoid float error.
    num = round(scale * 100)
    return num, 100


def menu_scale_header(scale: float = GLOBAL_SCALE) -> str:
    """Return the generated `az_scale.h` the application-menu build injects so the menu's C
    constants DERIVE from this one scale. It defines AZ_UI_SCALE_NUM/DEN (the scale as a ratio)
    and AZ_SCALED(x) = round(x * NUM / DEN) via integer math. theme.h includes it and scales its
    fixed-PIXEL geometry (window/icon/row sizes) by AZ_SCALED(); the POINT fonts are left STOCK
    and scale via gtk-xft-dpi (the DPI channel) instead. A DEFAULT copy at scale 1.0 (100/100)
    ships in the source tree so the C tests compile stock; build_daemon OVERWRITES it with this
    scaled version. `azzio display scale` triggers a rebuild path? No -- the menu binary is
    built into the ISO; a live scale change scales the menu's FONTS (via Xft.dpi, which the menu
    reads live through GTK) immediately, and its pixel geometry is fixed at the build scale (a
    deliberate, documented limitation -- the fractional font scaling is what the eye notices)."""
    num, den = _scale_ratio(scale)
    return f"""\
/* Azzio application-menu UI scale -- GENERATED from packages/openbox/scale (edit the Python, not
 * this file). The build (application_menu.build_daemon) OVERWRITES the scale-1.0 default shipped
 * in the source tree with the real GLOBAL_SCALE ratio, so the menu's fixed-PIXEL geometry in
 * theme.h derives from the single scale source. Point FONTS stay stock and scale via the DPI
 * channel (gtk-xft-dpi, from Xft.dpi). */
#ifndef AZ_SCALE_H
#define AZ_SCALE_H
#define AZ_UI_SCALE_NUM {num}
#define AZ_UI_SCALE_DEN {den}
/* round(x * NUM / DEN) with integer math (the +DEN/2 rounds to nearest). */
#define AZ_SCALED(x) (((x) * AZ_UI_SCALE_NUM + AZ_UI_SCALE_DEN / 2) / AZ_UI_SCALE_DEN)
#endif /* AZ_SCALE_H */
"""
