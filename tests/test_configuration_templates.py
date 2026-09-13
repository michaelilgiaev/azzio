"""packages.file_manager.templates -- the XDG user-dirs pointer for the file manager.

The templates module was FOLDED INTO the file_manager package (it is a submodule now,
packages/file_manager/templates.py, imported as `from packages.file_manager import templates`), so it is
tested here alongside the rest of the file-manager setup rather than as its own top-level package.

The user asked for ~/Templates to be deleted entirely ("Delete that 'Templates/' directory"), so the
old "Create Document" template SET (an empty text doc + the LibreOffice ODF trio) is GONE. This
module now ships ONLY ~/.config/user-dirs.dirs -- the XDG pointer -- with XDG_TEMPLATES_DIR back at
the stock "$HOME/" (no Templates dir). These tests pin: the Templates dir is not created, the XDG
pointer maps the other dirs to the Azzio home layout, there is no template-file set left, and
fm.emit_plan() still folds the (now single-entry) plan in.
"""

from __future__ import annotations

from packages import file_manager as fm
from packages.file_manager import home_directory
from packages.file_manager import templates


def test_templates_dir_is_not_created():
    # The user asked for ~/Templates to be deleted, so it is NOT in the on-disk home layout
    # (neither a sidebar dir nor an extra dir) and the module no longer exposes a TEMPLATES_DIR.
    assert "Templates" not in home_directory.EXTRA_DIRECTORIES
    assert "Templates" not in home_directory.DIRECTORIES
    assert not hasattr(templates, "TEMPLATES_DIR")
    assert not hasattr(fm, "TEMPLATES_DIR")


def test_no_template_file_set_is_shipped():
    # The old Create-Document set (text + ODF trio) is gone: no helpers, no plan entries under a
    # Templates dir. Only user-dirs.dirs ships.
    for gone in ("template_names", "text_template", "odf_bytes", "_ODF_KINDS", "TEXT_TEMPLATE_NAME"):
        assert not hasattr(templates, gone), gone
    dests = [e["dest"] for e in templates.emit_plan()]
    assert dests == [templates.USER_DIRS_PATH]
    assert not any("Templates" in d for d in dests)


def test_user_dirs_keeps_templates_at_stock_home():
    # ~/Templates was deleted, so XDG_TEMPLATES_DIR is the stock "$HOME/" (NOT "$HOME/Templates").
    # The other XDG dirs still map to the Azzio home layout so xdg-aware apps land right.
    u = templates.user_dirs_dirs()
    assert 'XDG_TEMPLATES_DIR="$HOME/"' in u
    assert 'XDG_TEMPLATES_DIR="$HOME/Templates"' not in u
    assert 'XDG_DOCUMENTS_DIR="$HOME/Documents"' in u
    assert 'XDG_MUSIC_DIR="$HOME/Music"' in u
    assert templates.USER_DIRS_PATH == "/home/main/.config/user-dirs.dirs"
    # Keep the xdg-user-dirs-update banner so the updater preserves our values.
    assert u.startswith("# This file is written by xdg-user-dirs-update")


def test_emit_plan_ships_user_dirs_home_skel_mirrored():
    plan = templates.emit_plan()
    by_dest = {e["dest"]: e for e in plan}
    entry = by_dest[templates.USER_DIRS_PATH]
    assert entry["owner"] == "home"          # a user dotfile -> skel-mirrored
    assert callable(entry["builder"])
    assert entry["mode"] == 0o644


def test_file_manager_emit_plan_folds_in_the_user_dirs_entry():
    # templates no longer has its own auto-discovered package, so fm.emit_plan() MUST carry its
    # entry (else the XDG pointer would silently stop shipping).
    fm_dests = {e["dest"] for e in fm.emit_plan()}
    for e in templates.emit_plan():
        assert e["dest"] in fm_dests, e["dest"]
    assert templates.USER_DIRS_PATH in fm_dests
