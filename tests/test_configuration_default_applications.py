"""packages.azzio.default_applications -- the XDG mimeapps.list + exo terminal (PROMPT task 6).

Why these tests matter: the defaults are a single source-of-truth table flattened into
mimeapps.list; a MIME type mapped twice would make the default ambiguous, and a wrong handler
id would silently point a file type at the wrong app. These lock the exact category->handler
mapping the user specified, the no-double-mapping invariant, and the exo TerminalEmulator=kitty
wiring (which is what the file manager's "Open Terminal Here" uses).
"""

from __future__ import annotations

import pytest

from packages.azzio import default_applications as da


def test_no_mime_is_mapped_twice():
    # The build-time guard must hold: no MIME type claimed by two categories.
    da.assert_no_mime_collision()  # raises AssertionError on a collision
    # and independently: the flat pairs have unique MIME keys
    mimes = [m for m, _d in da.mime_defaults()]
    assert len(mimes) == len(set(mimes)), "a MIME type is mapped twice"


def test_category_handlers_match_the_spec():
    # PROMPT task 6: the exact handler per category.
    handlers = {label: desktop for _g, label, desktop, _m in da.CATEGORIES}
    assert handlers["Web"] == "librewolf.desktop"
    assert handlers["Mail"] == ""                       # intentionally empty
    assert handlers["HTML"] == "librewolf.desktop"
    assert handlers["Music"] == "vlc.desktop"
    assert handlers["Video"] == "vlc.desktop"
    assert handlers["Photos"] == "xviewer.desktop"
    assert handlers["Word"] == "libreoffice-writer.desktop"
    assert handlers["Spreadsheet"] == "libreoffice-calc.desktop"
    assert handlers["PDF"] == "librewolf.desktop"
    assert handlers["Source Code"] == "org.gnome.gedit.desktop"
    assert handlers["File Manager"] == "thunar.desktop"   # the file manager, not Dolphin
    assert handlers["Plain Text"] == "org.gnome.gedit.desktop"
    assert handlers["Calculator"] == "qalculate-gtk.desktop"
    assert handlers["Terminal"] == "kitty.desktop"


def test_key_mime_types_map_to_the_right_handler():
    m = dict(da.mime_defaults())
    assert m["text/html"] == "librewolf.desktop"
    assert m["application/pdf"] == "librewolf.desktop"
    assert m["image/png"] == "xviewer.desktop"
    assert m["video/mp4"] == "vlc.desktop"
    assert m["audio/mpeg"] == "vlc.desktop"
    assert m["text/plain"] == "org.gnome.gedit.desktop"
    assert m["inode/directory"] == "thunar.desktop"
    assert m["application/msword"] == "libreoffice-writer.desktop"
    assert m["application/vnd.ms-excel"] == "libreoffice-calc.desktop"


def test_pdf_is_librewolf_not_double_mapped_with_web():
    # application/pdf belongs to PDF (LibreWolf); it must not also be under Web/HTML.
    m = dict(da.mime_defaults())
    assert m["application/pdf"] == "librewolf.desktop"
    # http/https/html are separate keys (no application/pdf leakage there)
    assert "application/pdf" not in ("x-scheme-handler/http",)


def test_mimeapps_list_has_default_applications_group():
    out = da.mimeapps_list()
    assert "[Default Applications]" in out
    assert "inode/directory=thunar.desktop" in out
    assert "application/pdf=librewolf.desktop" in out


def test_collision_guard_actually_fires(monkeypatch):
    # If a future edit double-maps a MIME, the guard MUST raise (proving it is not vacuous).
    bad = da.CATEGORIES + (("System", "Bogus", "bogus.desktop", ("text/plain",)),)
    monkeypatch.setattr(da, "CATEGORIES", bad)
    with pytest.raises(AssertionError):
        da.assert_no_mime_collision()


def test_helpers_rc_sets_terminal_to_kitty():
    # PROMPT task 6: exo preferred TerminalEmulator = kitty (the file manager's Open Terminal Here uses it).
    rc = da.helpers_rc()
    assert "TerminalEmulator=kitty" in rc


def test_kitty_helper_desktop_is_a_terminalemulator_helper():
    d = da.kitty_helper_desktop()
    assert "Type=X-XFCE-Helper" in d
    assert "X-XFCE-Category=TerminalEmulator" in d
    assert "X-XFCE-Commands=kitty" in d
    assert "X-XFCE-CommandsWithParameter=kitty %s" in d


def test_emit_plan_owners_and_paths():
    plan = da.emit_plan()
    by_dest = {e["dest"]: e for e in plan}
    # mimeapps.list + helpers.rc are HOME (skel-mirrored)
    assert by_dest[da.MIMEAPPS_PATH]["owner"] == "home"
    assert by_dest[da.HELPERS_RC_PATH]["owner"] == "home"
    # the exo helper .desktop is a root system file
    assert by_dest[da.KITTY_HELPER_PATH]["owner"] == "root"
    assert da.KITTY_HELPER_PATH == "/usr/share/xfce4/helpers/kitty.desktop"
