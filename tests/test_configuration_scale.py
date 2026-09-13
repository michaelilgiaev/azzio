"""packages.openbox.scale -- the SINGLE global-scale source of truth (PROMPT Display/scale task).

Why these tests matter: the whole point is ONE scale that every app derives from. These pin:
  * the channel math (Xft.dpi / gtk-xft-dpi / Xcursor.size / GDK / Qt),
  * that the standard channels are actually wired (~/.Xresources + xrdb, the session env),
  * that the OLD per-app font constants now DERIVE from scale (a test FAILS if a second
    hardcoded scale/DPI/font-size creeps back into those apps).
"""

from __future__ import annotations

import types

from packages.openbox import scale
from packages import openbox
from packages import kitty
from packages import gedit


# --- the channel math -------------------------------------------------------

def test_scale_default_and_options():
    assert scale.GLOBAL_SCALE == 1.35
    assert scale.SCALE_OPTIONS == (1.00, 1.25, 1.35, 1.50, 1.75, 2.00)


def test_channel_math():
    assert scale.xft_dpi(1.0) == 96
    assert scale.xft_dpi(1.35) == 130          # round(96*1.35)
    assert scale.xft_dpi(2.0) == 192
    assert scale.gtk_xft_dpi(1.35) == 130 * 1024
    assert scale.xcursor_size(1.35) == round(24 * 1.35)  # 32
    assert scale.gdk_scale() == 1              # integer part stays 1 (fractional via DPI)
    assert scale.qt_scale_factor(1.35) == "1.35"


def test_scaled_helpers():
    assert scale.ui_px(100, 1.35) == 135
    assert scale.pt(7, 1.35) == 9              # openbox titlebar stock 7 -> 9 (the 25%-smaller bar)
    assert scale.pt(7, 1.0) == 7


# --- the standard channels are wired ----------------------------------------

def test_xresources_carries_the_scale():
    x = openbox.xresources()
    assert f"Xft.dpi: {scale.xft_dpi()}" in x
    assert f"Xcursor.size: {scale.xcursor_size()}" in x
    # it is shipped as a HOME file and loaded by xrdb in .xinitrc.
    assert openbox.XRESOURCES_PATH == "/home/main/.Xresources"
    assert "xrdb -merge" in openbox.xinitrc()
    plan_dests = {e["dest"] for e in openbox.emit_plan()}
    assert openbox.XRESOURCES_PATH in plan_dests


def test_session_env_carries_scale_and_avoids_double_scale():
    env = openbox.openbox_environment()
    assert f"export GDK_SCALE={scale.gdk_scale()}" in env
    assert f"export QT_SCALE_FACTOR={scale.qt_scale_factor()}" in env
    assert "export QT_AUTO_SCREEN_SCALE_FACTOR=0" in env
    # GDK_DPI_SCALE must NOT be EXPORTED (it would double-scale fonts on top of the DPI channel).
    # (The comment above the exports may mention the name; check the actual export line.)
    assert "export GDK_DPI_SCALE" not in env


# --- the old per-app constants now DERIVE from the one scale -----------------

def test_kitty_and_gedit_font_derive_from_scale():
    # Both take the STOCK baseline from scale (DPI-scaled), and stay EQUAL (kitty == gedit).
    assert kitty.KITTY_FONT_SIZE == scale.TERMINAL_EDITOR_FONT_STOCK
    assert gedit.GEDIT_FONT_SIZE == scale.TERMINAL_EDITOR_FONT_STOCK
    assert kitty.KITTY_FONT_SIZE == gedit.GEDIT_FONT_SIZE
    # a raw 18 must NOT be reintroduced (that would be a second hardcoded size).
    assert kitty.KITTY_FONT_SIZE != 18
    assert "font_size 18" not in kitty.kitty_conf()


def test_openbox_titlebar_font_derives_from_scale():
    # The DPI-blind OpenBox titlebar is explicitly scaled from the shared scale (pt(stock)
    # == 9 at the 1.35 default; stock 7 -> 9), THEN grown by openbox.TOPBAR_GROWTH for the
    # topbar/button-icon size asks. The growth lives in openbox (a topbar-only cosmetic bump),
    # NOT in scale.py, so the GLOBAL scale every other app rides is untouched -- pt(7) stays 9.
    # The factor is now 1.20 (was 1.10): the original +10% topbar PLUS a one-point nudge for
    # the "slightly bigger button icons" ask (OpenBox sizes the button glyphs to this font).
    assert scale.pt(scale.OPENBOX_TITLE_FONT_STOCK) == 9   # the scale-derived base is unchanged
    assert openbox.TITLE_FONT_SIZE == round(
        scale.pt(scale.OPENBOX_TITLE_FONT_STOCK) * openbox.TOPBAR_GROWTH)
    assert openbox.TITLE_FONT_SIZE == 11  # 9 * 1.20 -> 11 (topbar +10% plus the icon nudge)
    # and it is emitted into rc.xml.
    assert f"<size>{openbox.TITLE_FONT_SIZE}</size>" in openbox.openbox_rc_xml()


def test_app_menu_scale_header_generated_from_scale():
    # The app menu's fixed-pixel geometry scales via the generated az_scale.h ratio.
    hdr = scale.menu_scale_header(1.35)
    assert "#define AZ_UI_SCALE_NUM 135" in hdr
    assert "#define AZ_UI_SCALE_DEN 100" in hdr
    assert "AZ_SCALED(x)" in hdr
    # scale 1.0 header is 100/100 (the checked-in default the C tests compile against).
    assert "#define AZ_UI_SCALE_NUM 100" in scale.menu_scale_header(1.0)


def test_app_menu_theme_h_uses_scaled_geometry_and_correct_font_paths():
    import paths
    theme_h = (paths.LIBDIR / "packages/application_menu/theme.h").read_text()
    # geometry is wrapped in AZ_SCALED(...) (derives from the scale), not raw pixels.
    assert "AZ_SCALED(431)" in theme_h   # window width stock
    assert "AZ_SCALED(33)" in theme_h    # app icon stock
    assert '#include "az_scale.h"' in theme_h
    # The cairo-drawn fonts (app name/type via pango_cairo_create_layout in application_list.c, and the
    # power labels in power.c) do NOT inherit gtk-xft-dpi, so they are EXPLICITLY scaled with
    # AZ_SCALED(stock) -- NOT left plain (which would render at 96 DPI, too small).
    assert "#define AZ_FONT_APP_NAME AZ_SCALED(10)" in theme_h
    assert "#define AZ_FONT_APP_TYPE AZ_SCALED(8)" in theme_h
    assert "#define AZ_FONT_POWER    AZ_SCALED(9)" in theme_h
    # The SEARCH box is a GtkEntry (gtk_widget_override_font) which DOES scale via the DPI
    # channel, so it stays STOCK (wrapping it would double-scale).
    assert "#define AZ_FONT_SEARCH   10" in theme_h
    assert scale.MENU_FONT_APP_NAME_STOCK == 10
    assert scale.MENU_FONT_SEARCH_STOCK == 10


def test_only_one_scale_no_second_hardcoded_dpi():
    # There must be ONE source: no stray Xft.dpi / gtk-xft-dpi literal reintroduced OUTSIDE the
    # scale module + the files it legitimately generates. Scan the modules that were reconciled.
    import paths
    # theme.py (CLI) must NOT hardcode gtk-xft-dpi (the scale rides Xft.dpi, not settings.ini).
    theme_py = (paths.LIBDIR / "packages/azzio/theme.py").read_text()
    assert "gtk-xft-dpi" not in theme_py
    # openbox default settings.ini must NOT carry gtk-xft-dpi either (same reason).
    from packages import openbox as ob
    assert "gtk-xft-dpi" not in ob.gtk3_settings_ini_default()
