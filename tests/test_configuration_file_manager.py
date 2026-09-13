"""packages.file_manager -- the Azzio File Manager (file-manager-based) setup (PROMPT task 2/4/7).

Why these tests matter: the file manager's config was authored against VERIFIED facts from the
installed file manager 4.20 (the thunarrc keys, the Xfconf channel property names + canonical
values, the uca.xml schema, the sidebar built-in URIs). Each of those is a silent-regression
trap -- a drifted key/value or a malformed uca.xml is accepted by the build but breaks the
feature at runtime. These lock the load-bearing details:

  * thunarrc AND the Xfconf channel XML render the SAME settings (they must not drift; the file
    manager reads the channel at runtime and thunarrc on a fresh profile / no-xfconfd).
  * the location bar is the text entry, the side pane is the shortcuts pane, expandable
    folders + split view are off, removable-volume management is off.
  * the uca.xml is WELL-FORMED XML with all four actions (an unescaped `&&` once dropped the
    Create Link + Open Terminal actions), gedit on any file, gimp on images only, and the
    folder actions carry <range> (required to appear on the folder background).
  * the sidebar bookmarks come from home_directory (resolved paths), Desktop is not duplicated
    with the built-in, and the built-in Computer/Network/Recent/Trash are hidden.
  * the launcher is renamed to "File Manager" with the custom icon; the icon uses a
    private name (the .desktop id and the binary stay `thunar`).
"""

from __future__ import annotations

import re
from xml.dom import minidom

from packages import file_manager as fm
from packages.file_manager import home_directory
from packages.file_manager import actions, launcher, locale, menu_cleanup, settings, sidebar


# --- helpers for reading the vendored C source ------------------------------
# Several C functions in the fork carry a forward DECLARATION (ends in ";") as well as the
# DEFINITION (its signature is followed by "\n{"). Splitting on the bare signature grabs the
# decl first and slices the wrong region, so anchor on the definition: the signature line
# ending in ")" immediately followed by "\n{". And because the Azzio removal markers are big
# explanatory comments that mention words like "mount"/"emblem"/"drag", strip C comments
# before asserting a token is ABSENT so the doc comment can never satisfy (or defeat) a check.


def _c_definition_body(src: str, signature: str) -> str:
    """Return the brace body of the DEFINITION whose signature is followed by '\\n{'.

    `signature` is the text up to (but not including) the "\\n{"; it must end with the
    closing ")" of the parameter list. This deliberately skips any forward declaration,
    whose identical signature text is instead followed by ";".
    """
    anchor = signature + "\n{"
    assert anchor in src, f"definition not found for: {signature!r}"
    after = src.split(anchor, 1)[1]
    return after.split("\n}", 1)[0]


def _strip_c_comments(code: str) -> str:
    """Drop /* ... */ and // ... comments so ABSENT-token asserts test CODE, not commentary."""
    out = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    out = re.sub(r"//[^\n]*", "", out)
    return out


# --- thunarrc + xfconf channel (settings.py) --------------------------------

def test_thunarrc_and_xfconf_render_the_same_settings():
    # The two files must carry identical values (the file manager migrates thunarrc -> xfconf
    # and uses the channel at runtime; a drift means the fresh-profile seed and the runtime
    # store disagree). Compare the shared SETTINGS table's presence in both.
    rc = settings.file_manager_rc()
    xml = settings.xfconf_channel_xml()
    for rc_key, prop, kind, value in settings.SETTINGS:
        if kind == "bool":
            assert f"{rc_key}={'TRUE' if value else 'FALSE'}" in rc, rc_key
            assert f'name="{prop}" type="bool" value="{"true" if value else "false"}"' in xml, prop
        else:
            # string OR uint (misc-max-number-of-templates): thunarrc renders the value
            # verbatim; the xfconf XML carries the kind as the type= attribute.
            assert f"{rc_key}={value}" in rc, rc_key
            assert f'name="{prop}" type="{kind}" value="{value}"' in xml, prop


def test_location_bar_is_the_text_entry():
    # PROMPT: always show the path as an editable text path. ThunarLocationEntry is the
    # text-entry bar (ThunarLocationButtons is the breadcrumb we do NOT want).
    assert ("LastLocationBar", "last-location-bar", "string", "ThunarLocationEntry") in settings.SETTINGS
    assert "ThunarLocationButtons" not in settings.file_manager_rc()


def test_side_pane_is_the_shortcuts_pane():
    assert ("LastSidePane", "last-side-pane", "string", "ThunarShortcutsPane") in settings.SETTINGS


def test_expandable_folders_and_split_view_are_off():
    # PROMPT task 2: disable the expandable-folder tree arrows and the split view.
    d = {rc: value for rc, _p, _k, value in settings.SETTINGS}
    assert d["MiscEnableExpandableFolders"] is False
    assert d["MiscAlwaysEnableSplitView"] is False


def test_removable_volume_management_is_off():
    # PROMPT task 2: do not show mounted (removable) volumes -- volume management off.
    d = {rc: value for rc, _p, _k, value in settings.SETTINGS}
    assert d["MiscVolumeManagement"] is False


def test_toolbar_uses_plain_not_symbolic_icons():
    # PROMPT: the toolbar Back/Forward/Up/Search icons "were not applied". The file manager
    # defaults misc-symbolic-icons-in-toolbar to TRUE, which makes the toolbar request the
    # "-symbolic" variant of each name -- names the Azzio theme did not ship, so they fell through
    # to Adwaita. Setting it FALSE makes the toolbar use the plain names the Azzio theme overrides.
    d = {rc: value for rc, _p, _k, value in settings.SETTINGS}
    assert d["MiscSymbolicIconsInToolbar"] is False
    # rendered false in BOTH forms.
    assert "MiscSymbolicIconsInToolbar=FALSE" in settings.file_manager_rc()
    assert ('<property name="misc-symbolic-icons-in-toolbar" type="bool" value="false"/>'
            in settings.xfconf_channel_xml())


def test_zoom_bump_is_relative_percent_not_absolute_pixels():
    # PROMPT task 7: the icon zoom bump must be a RELATIVE step (composes with the global
    # scale), never an absolute pixel size. The value is a THUNAR_ZOOM_LEVEL_*_PERCENT enum.
    d = {rc: value for rc, _p, _k, value in settings.SETTINGS}
    assert d["LastIconViewZoomLevel"] == "THUNAR_ZOOM_LEVEL_150_PERCENT"
    # no bare pixel number pinned anywhere in the settings values
    for _rc, _p, _k, value in settings.SETTINGS:
        assert not (isinstance(value, str) and value.rstrip("0123456789") == "" and value), value


def test_xfconf_channel_is_wellformed_xml_and_hides_builtins():
    xml = settings.xfconf_channel_xml()
    dom = minidom.parseString(xml)  # raises if not well-formed
    ch = dom.getElementsByTagName("channel")[0]
    assert ch.getAttribute("name") == "thunar"
    # hidden-bookmarks hides Computer/Network/Recent/Trash; hidden-devices hides Computer/Network.
    assert "recent:///" in settings.HIDDEN_BOOKMARKS
    assert "computer:///" in settings.HIDDEN_BOOKMARKS
    assert "network:///" in settings.HIDDEN_BOOKMARKS
    assert "trash:///" in settings.HIDDEN_BOOKMARKS  # built-in trash hidden; our resolved one stays
    assert "computer:///" in settings.HIDDEN_DEVICES
    assert "network:///" in settings.HIDDEN_DEVICES
    # the arrays are rendered as type="array" with <value> children
    assert '<property name="hidden-bookmarks" type="array">' in xml
    assert '<value type="string" value="recent:///"/>' in xml


def test_gtk_css_font_bump_is_scoped_and_relative():
    # PROMPT task 7: the font bump is file-manager-SCOPED (selector on the thunar-window node)
    # and RELATIVE (em, composes with the global scale) -- never an absolute px size.
    css = settings.gtk_css()
    assert "window.thunar-window" in css
    assert "em;" in css                  # relative unit
    assert "px" not in css               # no absolute pixels
    assert f"{settings.FILE_MANAGER_FONT_SCALE:g}em" in css


# --- File manager refinements batch (settings.py) ---------------------------

def test_default_view_is_icon_view():
    # PROMPT batch item 2: default view = Icon view (was ThunarDetailsView/list).
    d = {rc: value for rc, _p, _k, value in settings.SETTINGS}
    assert d["LastView"] == "ThunarIconView"
    assert "ThunarDetailsView" != d["LastView"]
    # thunarrc carries it (not the list view as the default).
    assert "LastView=ThunarIconView" in settings.file_manager_rc()


def test_devices_and_file_system_removed_via_hidden_bookmarks():
    # PROMPT batch item 1: remove the "Devices" section entirely, INCLUDING the permanent
    # "File System" row. VERIFIED against thunar-shortcuts-model.c: File System has the URI
    # "file:///" and is hidden via hidden-bookmarks (NOT hidden-devices); with it hidden and no
    # removable volumes, the whole Devices heading auto-hides.
    assert "file:///" in settings.HIDDEN_BOOKMARKS
    xml = settings.xfconf_channel_xml()
    assert '<value type="string" value="file:///"/>' in xml


def test_main_user_home_row_is_shown_at_the_top_not_hidden():
    # PROMPT: "add the user to the top of the sidebar ... simply name it 'main'." The user row is
    # the file manager's BUILT-IN Home place (group PLACES_DEFAULT, sort_id 0, displayed as the
    # home basename "main"), which already sits at the very top of Places. So its URI
    # (file:///home/main) must NOT be hidden -- un-hiding it IS how "main" appears at the top.
    assert f"file://{settings.HOME}" not in settings.HIDDEN_BOOKMARKS
    assert settings.HOME.rsplit("/", 1)[-1] == "main"     # the built-in Home displays as "main"
    # It is the built-in place, NOT a GTK bookmark: no "main"/"Home Directory" bookmark line, and
    # no leftover ".home-directory" URI anywhere.
    bm = sidebar.gtk_bookmarks()
    assert not any(ln.endswith(" main") for ln in bm.splitlines())
    assert not any(ln.endswith(" Home Directory") for ln in bm.splitlines())
    assert ".home-directory" not in bm
    # the old replacement-bookmark constants/label are gone from the sidebar module.
    assert not hasattr(sidebar, "HOME_BOOKMARK_LABEL")
    assert not hasattr(sidebar, "HOME_BOOKMARK_URI")


def test_bookmarks_menu_and_ctrl_d_removed_from_vendored_source():
    # PROMPT: remove "Bookmarks" (also "CTRL+D"). The side pane is driven SOLELY by the Azzio-
    # generated ~/.config/gtk-3.0/bookmarks (the hardcoded home scan), so the user-facing Bookmarks
    # feature is excised from the vendored fork:
    #   * the "_Bookmarks" top-level menu is no longer created (neither in the menubar nor the
    #     toolbar "hamburger" menu) -- both create_menu(... BOOKMARKS_MENU ...) calls are gone,
    #   * CTRL+D ("<Primary>D") is unbound from the "Add Bookmark" (sendto-shortcuts) action,
    #   * the "Send To -> Side Pane (Add Bookmark)" entry is dropped from the Send To submenu.
    win_c = (fm.SOURCE_DIR / "thunar" / "thunar-window.c").read_text()
    am_c = (fm.SOURCE_DIR / "thunar" / "thunar-action-manager.c").read_text()
    # The _Bookmarks menu is never built (the only two create-menu call sites are removed; the
    # bare action-entry row and internal loader may remain, but nothing surfaces them).
    assert "G_CALLBACK (thunar_window_update_bookmarks_menu), window->menubar)" not in win_c
    assert "G_CALLBACK (thunar_window_update_bookmarks_menu), menu)" not in win_c
    # CTRL+D is unbound from Add Bookmark: the sendto-shortcuts row no longer carries "<Primary>D".
    add_bookmark_line = next(
        ln for ln in am_c.splitlines() if "ThunarShortcutsPane/sendto-shortcuts" in ln
    )
    assert "<Primary>D" not in add_bookmark_line
    # The "Send To -> Side Pane (Add Bookmark)" menu item is gone from the Send To submenu.
    assert "Side Pane (Add Bookmark)" not in am_c


def test_go_menu_removed_from_topbar_and_its_accelerators_disabled():
    # PROMPT: remove the "Go" option from the TOPBAR, and within it disable Alt+Up, Alt+Home and
    # Ctrl+L -- while KEEPING "/" (opens the location entry) and Ctrl+F (search). The Go menu and
    # its accelerators are baked into the vendored fork's thunar-window.c:
    #   * the Go menu is dropped from the MENUBAR only -- the create_menu(... GO_MENU ...,
    #     window->menubar) call is gone; the window/hamburger-menu Go submenu stays, so the
    #     actions remain reachable without a keyboard shortcut,
    #   * open-parent (<Alt>Up), open-home (<Alt>Home) and open-location (<Primary>l) have their
    #     accelerator strings blanked in the action-entry table,
    #   * <Alt>d (open-location-alt) and <Primary>f (search) are deliberately LEFT intact, and
    #     "/" opens the location entry via a separate GTK type-ahead path (thunar-standard-view.c),
    #     not via any accelerator, so it is unaffected.
    win_c = (fm.SOURCE_DIR / "thunar" / "thunar-window.c").read_text()
    sv_c = (fm.SOURCE_DIR / "thunar" / "thunar-standard-view.c").read_text()

    # (1) The Go menu is no longer added to the TOPBAR menubar...
    assert "G_CALLBACK (thunar_window_update_go_menu), window->menubar)" not in win_c
    # ...but the window/hamburger-menu Go submenu is KEPT (still created into that popup `menu`),
    # so the navigation actions stay available from the menu even without their shortcuts.
    assert "G_CALLBACK (thunar_window_update_go_menu), menu)" in win_c

    # (2) The three named accelerators are blanked (accel string ""). Assert per action row so a
    # future re-add of the key is caught precisely.
    def _entry_line(action_path: str) -> str:
        # The action-entry row is the one defining this "<Actions>/..." path (registered with a
        # trailing quote+comma so a longer sibling path like "open-location-alt" is not matched).
        return next(ln for ln in win_c.splitlines() if f'"{action_path}",' in ln)

    for action_path, dead_accel in (
        ("<Actions>/ThunarWindow/open-parent", "<Alt>Up"),
        ("<Actions>/ThunarWindow/open-home", "<Alt>Home"),
        ("<Actions>/ThunarWindow/open-location", "<Primary>l"),
    ):
        line = _entry_line(action_path)
        assert dead_accel not in line, f"{action_path} still binds {dead_accel}"

    # (3) The KEPT bindings are still present: <Alt>d (alt location opener) and <Primary>f (search).
    assert '"<Actions>/ThunarWindow/open-location-alt",' in win_c
    assert "<Alt>d" in _entry_line("<Actions>/ThunarWindow/open-location-alt")
    assert "<Primary>f" in _entry_line("<Actions>/ThunarWindow/search")

    # (4) "/" still opens the location entry -- it is handled directly in the standard view
    # (GDK_KEY_slash -> start-open-location), NOT by the removed <Primary>l accelerator.
    assert "GDK_KEY_slash" in sv_c
    assert "start-open-location" in sv_c


def test_view_menu_removed_from_topbar_and_its_accelerators_disabled():
    # PROMPT: same treatment as "Go" -- remove the "View" option from the TOPBAR, and disable
    # Ctrl+R, F3, Ctrl+B, Ctrl+E, Ctrl+M while keeping their menu items/defaults permanent. Keep
    # Ctrl+H, Ctrl++, Ctrl+-, Ctrl+0, Ctrl+1, Ctrl+2, Ctrl+3. All baked into thunar-window.c:
    #   * the View menu is dropped from the MENUBAR only -- the create_menu(... VIEW_MENU ...,
    #     window->menubar) call is gone; the window/hamburger-menu View submenu stays (created into
    #     the popup `menu`), so the actions + their menu items remain reachable without a shortcut,
    #   * reload (<Primary>r), toggle-split-view (F3), view-side-pane-shortcuts (<Primary>b),
    #     view-side-pane-tree (<Primary>e) and view-menubar (<Primary>m) have their accelerator
    #     strings blanked in the action-entry table (the callbacks/menu items are untouched, so the
    #     defaults still work by click; F5 still reloads via the reload-alt row),
    #   * the KEPT keys -- <Primary>h (show hidden), <Primary>plus/minus/0 (zoom) and
    #     <Primary>1/2/3 (view-as) -- are asserted still bound so a future edit can't drop them.
    win_c = (fm.SOURCE_DIR / "thunar" / "thunar-window.c").read_text()

    # (1) The View menu is no longer added to the TOPBAR menubar...
    assert "G_CALLBACK (thunar_window_update_view_menu), window->menubar)" not in win_c
    # ...but the window/hamburger-menu View submenu is KEPT (still created into that popup `menu`).
    assert "G_CALLBACK (thunar_window_update_view_menu), menu)" in win_c

    def _entry_line(action_path: str) -> str:
        # The action-entry row is the one defining this "<Actions>/..." path (registered with a
        # trailing quote+comma so a longer sibling path is not matched).
        return next(ln for ln in win_c.splitlines() if f'"{action_path}",' in ln)

    # (2) The five named accelerators are blanked (accel string ""). Assert per action row so a
    # future re-add of the key is caught precisely.
    for action_path, dead_accel in (
        ("<Actions>/ThunarWindow/reload", "<Primary>r"),
        ("<Actions>/ThunarWindow/toggle-split-view", "F3"),
        ("<Actions>/ThunarWindow/view-side-pane-shortcuts", "<Primary>b"),
        ("<Actions>/ThunarWindow/view-side-pane-tree", "<Primary>e"),
        ("<Actions>/ThunarWindow/view-menubar", "<Primary>m"),
    ):
        line = _entry_line(action_path)
        assert dead_accel not in line, f"{action_path} still binds {dead_accel}"

    # (3) The KEPT bindings are still present on their own rows.
    for action_path, live_accel in (
        ("<Actions>/ThunarWindow/show-hidden", "<Primary>h"),
        ("<Actions>/ThunarWindow/zoom-in", "<Primary>plus"),
        ("<Actions>/ThunarWindow/zoom-out", "<Primary>minus"),
        ("<Actions>/ThunarWindow/zoom-reset", "<Primary>0"),
        ("<Actions>/ThunarWindow/view-as-icons", "<Primary>1"),
        ("<Actions>/ThunarWindow/view-as-detailed-list", "<Primary>2"),
        ("<Actions>/ThunarWindow/view-as-compact-list", "<Primary>3"),
    ):
        assert live_accel in _entry_line(action_path), f"{action_path} lost {live_accel}"

    # (4) F5 still reloads even though Ctrl+R is gone (the reload-alt-1 row keeps "F5").
    assert "F5" in _entry_line("<Actions>/ThunarWindow/reload-alt-1")


def test_edit_menu_removed_from_topbar_and_its_accelerators_disabled():
    # PROMPT: same treatment as "Go"/"View" -- remove the "Edit" option from the TOPBAR, and
    # disable Ctrl+S ("Select by Pattern...") and Shift+Ctrl+I ("Invert Selection"); the rest of
    # the Edit shortcuts are fine. Baked into the vendored fork:
    #   * the Edit menu is dropped from the MENUBAR only -- the create_menu(... EDIT_MENU ...,
    #     window->menubar) call is gone; the window/hamburger-menu Edit submenu stays (created into
    #     the popup `menu`), so the actions + their menu items remain reachable without the topbar,
    #   * select-by-pattern (<Primary>s) and invert-selection (<Primary><shift>I) have their
    #     accelerator strings blanked in thunar-standard-view.c's action-entry table (the callbacks/
    #     menu items are untouched, so both still work by click from the Edit submenu),
    #   * the KEPT Edit keys -- undo/redo (<Primary>z / <Primary><shift>z), select-all (<Primary>a),
    #     cut/copy/paste (<Primary>x/c/v) and rename (F2) -- are asserted still bound so a future
    #     edit can't silently drop them.
    win_c = (fm.SOURCE_DIR / "thunar" / "thunar-window.c").read_text()
    sv_c = (fm.SOURCE_DIR / "thunar" / "thunar-standard-view.c").read_text()
    am_c = (fm.SOURCE_DIR / "thunar" / "thunar-action-manager.c").read_text()

    # (1) The Edit menu is no longer added to the TOPBAR menubar...
    assert "G_CALLBACK (thunar_window_update_edit_menu), window->menubar)" not in win_c
    # ...but the window/hamburger-menu Edit submenu is KEPT (still created into that popup `menu`),
    # so undo/redo/cut/copy/paste/select-all stay available from the menu even without the topbar.
    assert "G_CALLBACK (thunar_window_update_edit_menu), menu)" in win_c

    def _sv_entry_line(action_path: str) -> str:
        # The standard-view action-entry row defining this "<Actions>/..." path (trailing
        # quote+comma so a longer sibling path is not matched).
        return next(ln for ln in sv_c.splitlines() if f'"{action_path}",' in ln)

    def _win_entry_line(action_path: str) -> str:
        return next(ln for ln in win_c.splitlines() if f'"{action_path}",' in ln)

    def _am_entry_line(action_path: str) -> str:
        return next(ln for ln in am_c.splitlines() if f'"{action_path}",' in ln)

    # (2) The two named accelerators are blanked (accel string ""). Assert per action row so a
    # future re-add of the key is caught precisely.
    for action_path, dead_accel in (
        ("<Actions>/ThunarStandardView/select-by-pattern", "<Primary>s"),
        ("<Actions>/ThunarStandardView/invert-selection", "<Primary><shift>I"),
    ):
        line = _sv_entry_line(action_path)
        assert dead_accel not in line, f"{action_path} still binds {dead_accel}"

    # (3) The KEPT Edit bindings are still present on their own rows. Undo/Redo/Preferences and
    # the Edit-menu label live on the window entries; Select All on the standard-view entries;
    # Cut/Copy/Paste and Rename on the action-manager entries.
    assert "<Primary>Z" in _win_entry_line("<Actions>/ThunarActionManager/undo")
    assert "<Primary><shift>Z" in _win_entry_line("<Actions>/ThunarActionManager/redo")
    assert "<Primary>a" in _sv_entry_line("<Actions>/ThunarStandardView/select-all-files")
    assert "<Primary>X" in _am_entry_line("<Actions>/ThunarActionManager/cut")
    assert "<Primary>C" in _am_entry_line("<Actions>/ThunarActionManager/copy")
    assert "<Primary>V" in _am_entry_line("<Actions>/ThunarActionManager/paste")
    assert "F2" in _am_entry_line("<Actions>/ThunarStandardView/rename")


def test_file_menu_removed_from_topbar_and_the_empty_topbar_collapses():
    # PROMPT: same treatment as "Go"/"View"/"Edit" -- remove the "File" option from the TOPBAR and
    # disable Ctrl+T (New Tab), Ctrl+N (New Window), Shift+Ctrl+W (Close All Windows); the rest of
    # the File shortcuts are fine. AND: File is the last top-level menu, so with it gone "we don't
    # have anything else left on that topbar, so it should collapse". Baked into thunar-window.c:
    #   * the File menu is dropped from the MENUBAR only -- the create_menu(... FILE_MENU ...,
    #     window->menubar) call is gone; the window/hamburger-menu File submenu stays (created into
    #     the popup `menu`), so New Tab/New Window/Close All etc. remain reachable without the topbar,
    #   * new-tab (<Primary>t), new-window (<Primary>n) and close-all-windows (<Primary><Shift>w)
    #     have their accelerator strings blanked in the action-entry table (callbacks/menu items are
    #     untouched, so they still work by click from the File submenu),
    #   * the KEPT File keys -- close-tab (<Primary>w) and close-window (<Primary>q) -- are asserted
    #     still bound so a future edit can't silently drop them,
    #   * COLLAPSE: the menubar now holds no top-level menu, so it is force-hidden at init
    #     (window->menubar_visible = FALSE, unconditionally -- the last-menubar-visible pref, which
    #     defaults TRUE, is not even fetched), the "Menubar" toggle is removed from BOTH the View
    #     submenu and the toolbar right-click menu (so it can't re-show the empty strip), and F10 is
    #     rerouted to the same popup menu instead of un-hiding the empty menubar. (A later task
    #     removed the location-toolbar hamburger button that used to surface this menu; F10 -- via
    #     thunar_window_action_menu -- is now the entry point. See the dedicated toolbar test.)
    win_c = (fm.SOURCE_DIR / "thunar" / "thunar-window.c").read_text()

    def _entry_line(action_path: str) -> str:
        # The action-entry row defining this "<Actions>/..." path (trailing quote+comma so a longer
        # sibling path -- e.g. close-window vs close-all-windows -- is not matched).
        return next(ln for ln in win_c.splitlines() if f'"{action_path}",' in ln)

    # (1) The File menu is no longer added to the TOPBAR menubar...
    assert "G_CALLBACK (thunar_window_update_file_menu), window->menubar)" not in win_c
    # ...but the window/hamburger-menu File submenu is KEPT (still created into that popup `menu`),
    # so New Tab/New Window/Close-All stay available from the menu even without the topbar.
    assert "G_CALLBACK (thunar_window_update_file_menu), menu)" in win_c

    # (2) The three named accelerators are blanked (accel string ""). Assert per action row so a
    # future re-add of the key is caught precisely.
    for action_path, dead_accel in (
        ("<Actions>/ThunarWindow/new-tab", "<Primary>t"),
        ("<Actions>/ThunarWindow/new-window", "<Primary>n"),
        ("<Actions>/ThunarWindow/close-all-windows", "<Primary><Shift>w"),
    ):
        assert dead_accel not in _entry_line(action_path), f"{action_path} still binds {dead_accel}"

    # (3) The KEPT File bindings are still present on their own rows.
    assert "<Primary>w" in _entry_line("<Actions>/ThunarWindow/close-tab")
    assert "<Primary>q" in _entry_line("<Actions>/ThunarWindow/close-window")

    # (4) COLLAPSE -- the empty topbar is force-hidden at init regardless of the saved pref, and that
    # pref (which defaults TRUE) is no longer fetched at all.
    assert "window->menubar_visible = FALSE;" in win_c
    assert '"last-menubar-visible", &last_menubar_visible,' not in win_c

    # (5) The "Menubar" toggle can no longer re-show the empty strip: it is gone from BOTH the View
    # submenu and the toolbar right-click menu (no VIEW_MENUBAR toggle item is created anywhere).
    assert "THUNAR_WINDOW_ACTION_VIEW_MENUBAR), G_OBJECT (window)" not in win_c

    # (6) F10 (open-file-menu) is rerouted to the hamburger menu instead of un-hiding the menubar --
    # the handler now delegates to thunar_window_action_menu and no longer force-shows the menubar.
    # Anchor on the DEFINITION (signature + "\n{"), not the forward declaration ("...*window);").
    f10_body = win_c.split("thunar_window_action_open_file_menu (ThunarWindow *window)\n{", 1)[1]
    f10_body = f10_body.split("\n}", 1)[0]
    assert "return thunar_window_action_menu (window);" in f10_body
    assert "gtk_widget_set_visible (window->menubar, TRUE)" not in f10_body


def test_show_hidden_files_added_to_right_click_menu():
    # PROMPT: add "Show Hidden Files" to the menu that opens with the right mouse click. The
    # empty-space context menu is built in thunar_standard_view_context_menu (thunar-standard-view.c);
    # it now appends the window's SHOW_HIDDEN toggle (reflecting the current per-view state) after
    # the sort/arrange items. Only on the empty-space branch -- the file-selection menu is for file
    # operations, not global view toggles.
    sv_c = (fm.SOURCE_DIR / "thunar" / "thunar-standard-view.c").read_text()
    # The toggle is appended using the window's SHOW_HIDDEN action entry + the view's current state.
    assert "THUNAR_WINDOW_ACTION_SHOW_HIDDEN" in sv_c
    assert "thunar_window_get_action_entry (THUNAR_WINDOW (window), THUNAR_WINDOW_ACTION_SHOW_HIDDEN)" in sv_c
    assert "thunar_view_get_show_hidden (THUNAR_VIEW (standard_view))" in sv_c


def test_location_toolbar_drops_hamburger_and_home_and_moves_search_into_home_slot():
    # PROMPT (new task): the location toolbar has (left of the path entry) home, up, forward, back
    # and the hamburger button, plus the search button on the RIGHT of the entry. Remove the
    # hamburger, remove the home button, and move search to where home was -- i.e. search becomes
    # the last item on the LEFT cluster, immediately before the location bar. Baked into
    # thunar-window.c's thunar_window_location_toolbar_create:
    #   * the hamburger button (location_toolbar_item_menu / ACTION_MENU toggle) is no longer
    #     created, and every widget-pointer use of it is gone (the menubar-toggle visibility line,
    #     the deactivate toggle-off, the overflow-menu skip, the struct field). The ACTION_MENU
    #     *action* + thunar_window_action_menu stay (F10 still opens that popup, now anchored on the
    #     "up"/parent button),
    #   * the home button (location_toolbar_item_home / ACTION_OPEN_HOME toolbar item) and its
    #     middle-click handler thunar_window_open_home_clicked are gone; the OPEN_HOME *action* +
    #     the Go-menu Home item are untouched,
    #   * the SEARCH toggle is created in the left cluster right after the "up"/parent button (where
    #     home used to be), NOT in the post-location-bar "remaining items" block.
    win_c = (fm.SOURCE_DIR / "thunar" / "thunar-window.c").read_text()

    # (1) The hamburger button widget is gone: no creation, no struct field, no dangling uses.
    assert "location_toolbar_item_menu" not in win_c
    assert "THUNAR_WINDOW_ACTION_MENU, FALSE" not in win_c  # the toggle-item creation call
    # ...but the Menu ACTION + its popup builder stay, so F10 still opens the menu.
    assert "G_CALLBACK (thunar_window_action_menu)" in win_c
    assert "G_CALLBACK (thunar_window_update_go_menu), menu)" in win_c  # popup still populated

    # (2) The home button widget + its click handler are gone; the OPEN_HOME action stays.
    assert "location_toolbar_item_home" not in win_c
    assert "thunar_window_open_home_clicked" not in win_c
    assert "G_CALLBACK (thunar_window_action_open_home)" in win_c  # Go-menu Home item kept

    # (3) Search moved into home's old slot: its creation is in the left cluster, before the
    # location-bar tool_item (anchored by the "add the location bar to the toolbar" comment) and
    # before the NEW_WINDOW item -- not in the trailing block after the bar. (The NEW_TAB toolbar
    # item that used to be the next-created item is GONE: tabs are completely disabled, see
    # test_new_tabs_are_completely_disabled -- so NEW_WINDOW is now the item right after search.)
    search_create = 'window->location_toolbar_item_search = thunar_window_create_toolbar_toggle_item_from_action (window, THUNAR_WINDOW_ACTION_SEARCH,'
    assert win_c.count(search_create) == 1  # created exactly once (not left behind in two places)
    # anchor on the toolbar CREATION CALLS (unique full call strings), not the action-entry table
    # near the top of the file where these ACTION_ enum names also appear.
    new_window_create = "thunar_window_create_toolbar_item_from_action (window, THUNAR_WINDOW_ACTION_NEW_WINDOW, item_order++)"
    parent_create = "window->location_toolbar_item_parent = thunar_window_create_toolbar_item_from_action"
    assert win_c.count(new_window_create) == 1
    assert win_c.count(parent_create) == 1
    search_pos = win_c.index(search_create)
    parent_pos = win_c.index(parent_create)
    new_window_pos = win_c.index(new_window_create)
    location_bar_pos = win_c.index("/* add the location bar to the toolbar */")
    assert parent_pos < search_pos < new_window_pos < location_bar_pos, "search must sit after 'up', before the path bar"
    # ...and IMMEDIATELY after 'up' (home's exact old slot): nothing else is created between the
    # parent-button line and the search line.
    between = win_c[win_c.index("\n", parent_pos) + 1 : search_pos]
    assert "create_toolbar" not in between, "search must be the item right after 'up', no item in between"


def test_default_last_toolbar_items_string_matches_the_new_layout():
    # ROOT CAUSE of "search didn't move" (new-task follow-up): the toolbar layout is decided by TWO
    # things that must agree -- (a) the build order in thunar-window.c's
    # thunar_window_location_toolbar_create (fixed by the test above), and (b) the PERSISTED order
    # string "last-toolbar-items", which thunar_window_location_toolbar_load_items replays on top,
    # REORDERING the still-existing widgets to match itself. On a fresh profile (the hypervisor's
    # thunar.xml has no such property) the app falls back to the COMPILED DEFAULT of that property
    # in thunar-preferences.c. If that default still lists the old layout, it drags search back to
    # the end (its old post-reload slot) and re-hides nothing -- which is exactly why the removals
    # "took" (menu/open-home widgets no longer exist to be placed) but the MOVE silently reverted.
    #
    # So the default string must match the new left cluster: drop "menu" and "open-home" entirely,
    # and put "search" in home's old slot -- immediately after "open-parent". Search stays visible.
    prefs_c = (fm.SOURCE_DIR / "thunar" / "thunar-preferences.c").read_text()

    # Pull the default value of the "last-toolbar-items" GParamSpec. It is a run of adjacent C
    # string literals; concatenate them so line-wrapping in the source doesn't matter.
    anchor = prefs_c.index('g_param_spec_string ("last-toolbar-items"')
    # The default is the 3rd string arg (name, nick, blurb=NULL, default). Grab the literal run that
    # starts after the "LastToolbarItems" nick and the NULL blurb, up to the closing paren line.
    spec = prefs_c[anchor: prefs_c.index("EXO_PARAM_READWRITE", anchor)]
    literals = re.findall(r'"([^"]*)"', spec)
    # literals[0] = "last-toolbar-items", [1] = "LastToolbarItems"; the rest are the default's parts.
    default = "".join(literals[2:])
    items = [tok.split(":")[0] for tok in default.split(",") if tok]

    # (1) the two removed buttons are gone from the persisted default too.
    assert "menu" not in items, f"hamburger 'menu' still in default toolbar order: {default}"
    assert "open-home" not in items, f"'open-home' still in default toolbar order: {default}"
    # ...and "new-tab" is gone too -- tabs are completely disabled, so there is no New Tab toolbar
    # item to persist an order for (a stale "new-tab:0" would be a dead token).
    assert "new-tab" not in items, f"'new-tab' still in default toolbar order: {default}"

    # (2) search sits in home's OLD slot: immediately after open-parent, and it is still visible.
    assert "open-parent:1,search:1" in default, (
        f"search must be persisted right after 'up'/open-parent and visible; got: {default}"
    )

    # (3) search is NOT left at the trailing (old) position after reload; the tail is the path bar
    # then a hidden reload, with nothing after it.
    assert default.rstrip().endswith("location-bar:1,reload:0"), (
        f"search must no longer trail after reload; tail was: {default}"
    )
    assert items.count("search") == 1  # not duplicated across old + new slots


def test_new_tabs_are_completely_disabled():
    # NEW PROMPT: "Completely disable new tabs; additionally, when I right click on a folder there
    # is 'Open in new Tab', please remove that option entirely." Tabs are killed at the single
    # choke point every new-tab path funnels through, and the "Open in new Tab" context item is
    # dropped from EVERY right-click menu (main view, side pane, tree). Baked into the vendored fork:
    win_c = (fm.SOURCE_DIR / "thunar" / "thunar-window.c").read_text()
    am_c = (fm.SOURCE_DIR / "thunar" / "thunar-action-manager.c").read_text()
    sc_c = (fm.SOURCE_DIR / "thunar" / "thunar-shortcuts-view.c").read_text()
    tv_c = (fm.SOURCE_DIR / "thunar" / "thunar-tree-view.c").read_text()

    # (1) The choke point: thunar_window_notebook_add_new_tab no longer inserts a notebook page --
    # every "open in a new tab" caller (New Tab action/toolbar, "Open in new Tab", middle-click,
    # the open-new-tab signals, `thunar --tab`) runs through it, so neutralizing it here disables
    # tab creation everywhere. It navigates the CURRENT view instead (set_current_directory) and
    # must NOT call insert_page. Anchor on the DEFINITION body (signature + "\n{").
    body = win_c.split("thunar_window_notebook_add_new_tab (ThunarWindow        *window,", 1)[1]
    body = body.split("\n{", 1)[1].split("\n}", 1)[0]
    # Match the CALL form ("...insert_page (window, ...") so the word appearing in this function's
    # explanatory comment doesn't trip the guard.
    assert "thunar_window_notebook_insert_page (window" not in body, "add_new_tab must not insert a tab page"
    assert "thunar_window_set_current_directory (window, directory)" in body, (
        "add_new_tab must navigate the current view instead of opening a tab"
    )

    # (2) The "New Tab" TOOLBAR item is gone (only New Window remains in that slot).
    assert "thunar_window_create_toolbar_item_from_action (window, THUNAR_WINDOW_ACTION_NEW_TAB" not in win_c

    # (3) The "New Tab" MENU items are gone from BOTH the F10/File submenu and the tab context menu
    # (the only two xfce_gtk_menu_item_new_from_action_entry(... NEW_TAB ...) call sites).
    assert "get_action_entry (THUNAR_WINDOW_ACTION_NEW_TAB)" not in win_c

    # (4) "Open in new Tab" is removed from EVERY context menu: it is never APPENDED anywhere. The
    # action row + the builder `case` may remain defined in the action manager (dead, harmless), but
    # nothing surfaces it -- so no line that calls append_menu_item may name OPEN_IN_TAB (in the
    # action manager, side pane or tree), and the enum is gone entirely from the two view builders.
    for src in (am_c, sc_c, tv_c):
        for ln in src.splitlines():
            if "append_menu_item" in ln:
                assert "OPEN_IN_TAB" not in ln, ln
    assert "THUNAR_ACTION_MANAGER_ACTION_OPEN_IN_TAB" not in sc_c
    assert "THUNAR_ACTION_MANAGER_ACTION_OPEN_IN_TAB" not in tv_c

    # (5) The Ctrl+Shift+P accelerator on the (now unsurfaced) open-in-new-tab row is blanked, so the
    # keyboard can't trigger a tab either. Assert on that row precisely.
    oit_row = next(ln for ln in am_c.splitlines()
                   if '"<Actions>/ThunarActionManager/open-in-new-tab",' in ln)
    assert "<Primary><shift>P" not in oit_row, "Ctrl+Shift+P must be unbound from Open in new Tab"

    # (6) Ctrl+T stays blanked on the (now unsurfaced) new-tab action row -- a regression guard so a
    # future edit can't quietly rebind it. The row still exists (the callback is harmless now), but
    # carries no accelerator.
    new_tab_row = next(ln for ln in win_c.splitlines() if '"<Actions>/ThunarWindow/new-tab",' in ln)
    assert "<Primary>t" not in new_tab_row


def test_drag_drop_cannot_add_a_persisted_sidebar_shortcut():
    # PROMPT: the home scan (~/.config/gtk-3.0/bookmarks, regenerated from /home/main) must be the
    # ONLY way a row appears on the side pane. Dropping a folder BETWEEN shortcut rows used to add
    # AND persist a user bookmark: thunar_shortcuts_view_drag_data_received (the DROP_BEFORE/AFTER
    # branch) called thunar_shortcuts_view_drop_uri_list(view, drop_file_list, path) -> ...model_add
    # -> ...save_bookmarks, writing to gtk-3.0/bookmarks. That external-add branch is neutralized in
    # the vendored fork so no drop can create a sidebar row.
    view_c = (fm.SOURCE_DIR / "thunar" / "thunar-shortcuts-view.c").read_text()
    # The add-a-shortcut CALL SITE is gone (the drop_uri_list() function may remain defined/dead;
    # what must not exist is the invocation that feeds it the dropped file list).
    assert "thunar_shortcuts_view_drop_uri_list (view, view->drop_file_list, path)" not in view_c
    # And nothing else invokes model_add from the view (the only persisting add-to-sidebar path).
    assert "thunar_shortcuts_model_add (" not in view_c
    # The drop-INTO-an-existing-folder path (copy/move/link -> thunar_dnd_perform) is UNTOUCHED --
    # that is normal file management, not adding a sidebar row.
    assert "thunar_dnd_perform (widget, file, view->drop_file_list" in view_c


def test_templates_prefs_hide_about_and_cap():
    # PROMPT batch item 8: hide the modal "About Templates" dialog + cap the submenu.
    d = {rc: value for rc, _p, _k, value in settings.SETTINGS}
    assert d["MiscShowAboutTemplates"] is False
    assert d["MiscMaxNumberOfTemplates"] == 100
    # the uint pref renders as type="uint" in the xfconf XML.
    assert '<property name="misc-max-number-of-templates" type="uint" value="100"/>' \
        in settings.xfconf_channel_xml()


def test_resolve_links_pref_present_but_documented_as_4_21_only():
    # PROMPT batch item 5: ship misc-resolve-links=true (resolves the symlink path in the
    # location bar). It is a 4.21.6+ pref (ignored by 4.20), documented honestly in the module.
    d = {rc: value for rc, _p, _k, value in settings.SETTINGS}
    assert d["MiscResolveLinks"] is True
    # the honesty note is present in the module docstring/comments (no silent faking).
    assert "4.21.6" in settings.__doc__ or "4.21.6" in open(settings.__file__).read()


# --- gettext .mo override (locale.py) ---------------------------------------

def test_mo_overrides_relabel_the_hardcoded_strings():
    # The .mo catalog relabels the hardcoded file-manager strings. The shortcuts sidebar section
    # header "Places" is renamed to "Home" (user request, step SEVEN) -- it is a hardcoded
    # gettext msgid in the thunar binary (verified via `strings /usr/bin/thunar`), so the
    # catalog is the supported lever. Assert that override plus the others.
    o = locale.OVERRIDES
    assert o["Places"] == "Home"                                 # sidebar header rename (step 7)
    assert o['_Open With "%s"'] == "_Edit with %s"               # item 7 (built-in -> "Edit with gedit")
    assert o["Create _Folder..."] == "Create New _Folder..."     # item 8 wording
    assert o["Create _Document"] == "Create New _Document..."     # item 8 wording
    # App identity: the product name "Thunar" -> "File Manager" in every gettext-wrapped
    # display string (application name, Preferences title, About blurb). PROMPT: rename the file
    # manager from "Azzio File Manager" to simply "File Manager".
    assert o["Thunar"] == "File Manager"
    assert o["Thunar Preferences"] == "File Manager Preferences"
    assert o[
        "Thunar is a fast and easy to use file manager\n"
        "for the Xfce Desktop Environment."
    ].startswith("File Manager is a fast")
    # the old "Azzio File Manager" product name must not linger in any override value.
    assert not any("Azzio File Manager" in v for v in o.values())


def test_window_title_is_fixed_file_manager_in_vendored_source():
    # PROMPT: the Openbox title bar must show ONLY "File Manager" -- not the folder name and not
    # the product-name suffix. The title is a BARE C literal (gettext .mo overrides cannot reach
    # it), so thunar_window_update_title in the vendored fork's thunar-window.c is patched to set a
    # fixed "File Manager" and the upstream folder-name/"%s - %s"-suffix logic is dropped.
    win_c = (fm.SOURCE_DIR / "thunar" / "thunar-window.c").read_text()
    assert 'gtk_window_set_title (GTK_WINDOW (window), "File Manager");' in win_c
    # The old folder+suffix builders are gone (either the "Azzio File Manager" relabel or the
    # original "Thunar" literal would put the folder name / product name back in the title bar).
    assert 'g_strdup_printf ("%s - %s", name, "Azzio File Manager")' not in win_c
    assert 'g_strdup_printf ("%s - %s", name, "Thunar")' not in win_c


def test_help_menu_removed_from_vendored_source():
    # PROMPT: remove the file-manager Help menu's About and Contents items, and the Help menu
    # itself. The whole Help menu lives in thunar-window.c (built programmatically, no .ui file):
    # a top-level _Help menu whose submenu is populated with _Contents + _About. Excising it means
    # the two THUNAR_WINDOW_ACTION_HELP_MENU create-menu calls (menubar + right-click menu), the
    # thunar_window_update_help_menu populator, the _Contents/_About action-entry rows and their
    # callbacks, and the three enum values are all gone from the vendored fork.
    win_c = (fm.SOURCE_DIR / "thunar" / "thunar-window.c").read_text()
    win_h = (fm.SOURCE_DIR / "thunar" / "thunar-window.h").read_text()
    # The Help menu is no longer created in either the menubar or the context menu.
    assert "THUNAR_WINDOW_ACTION_HELP_MENU" not in win_c
    assert "THUNAR_WINDOW_ACTION_HELP_MENU" not in win_h
    # About + Contents items, their populator and callbacks are gone.
    assert "thunar_window_update_help_menu" not in win_c
    assert "thunar_window_action_contents" not in win_c
    assert "thunar_window_action_about" not in win_c
    assert "THUNAR_WINDOW_ACTION_CONTENTS" not in win_c and "THUNAR_WINDOW_ACTION_CONTENTS" not in win_h
    assert "THUNAR_WINDOW_ACTION_ABOUT" not in win_c and "THUNAR_WINDOW_ACTION_ABOUT" not in win_h
    # No stray "_Help"/"_Contents"/"_About" menu labels remain from the removed action entries.
    assert 'N_ ("_Help")' not in win_c
    assert 'N_ ("_Contents")' not in win_c
    assert 'N_ ("_About")' not in win_c


def test_mo_bytes_are_a_valid_gettext_catalog(tmp_path):
    # The pure-Python .mo generator must produce a catalog real gettext can read (the file
    # manager uses C gettext). Write it and load it back with Python's gettext (same binary format).
    import gettext
    d = tmp_path / "en_US" / "LC_MESSAGES"
    d.mkdir(parents=True)
    (d / "thunar.mo").write_bytes(locale.mo_bytes())
    t = gettext.translation("thunar", localedir=str(tmp_path), languages=["en_US"])
    for msgid, msgstr in locale.OVERRIDES.items():
        assert t.gettext(msgid) == msgstr, msgid


def test_places_header_renames_to_home_under_the_default_en_IL_locale(tmp_path):
    # The concrete step-7 contract: under the DEFAULT installed locale (en_IL, seeded by
    # calamares' Asia/Jerusalem region), the catalog resolves the "Places" sidebar header msgid
    # to "Home". This mirrors the on-box `LANG=en_IL gettext -d thunar "Places"` -> "Home" check.
    import gettext
    d = tmp_path / "en_IL" / "LC_MESSAGES"
    d.mkdir(parents=True)
    (d / "thunar.mo").write_bytes(locale.mo_bytes())
    t = gettext.translation("thunar", localedir=str(tmp_path), languages=["en_IL"])
    assert t.gettext("Places") == "Home"


def test_mo_catalog_shipped_under_generated_locales_root_owned():
    # The catalog is shipped at the standard system locale path for BOTH generated locales
    # (en_US display + en_GB date), root-owned (a system catalog, not a dotfile).
    plan = fm.emit_plan()
    by_dest = {e["dest"]: e for e in plan}
    for loc in locale.LOCALES:
        p = locale.mo_path(loc)
        assert p in by_dest, p
        assert by_dest[p]["owner"] == "root"
        assert by_dest[p]["bytes_builder"] is locale.mo_bytes
    # en_IL is the DEFAULT installed locale (calamares seeds Asia/Jerusalem), so the catalog
    # MUST ship there too or the overrides never apply out of the box -- en_US/en_GB miss it.
    assert set(locale.LOCALES) == {"en_US", "en_GB", "en_IL"}


def test_gtk_menu_images_enabled_for_open_with_icons():
    # PROMPT batch item 6: "Open With" entries show app icons only if gtk-menu-images=true.
    # It lives in ~/.config/gtk-3.0/settings.ini (the openbox shipped default + the `azzio
    # theme` CLI, kept byte-for-byte in lock-step -- see test_configuration_theme). Assert the
    # openbox default (a plain module) and the BUNDLED CLI (theme.py is a bundle module that
    # needs common.py's imports, so it is exec'd from the bundle) both carry it.
    import types
    from packages import openbox
    from packages.azzio.bundle import bundle_source
    assert "gtk-menu-images=true" in openbox.gtk3_settings_ini_default()
    cli = types.ModuleType("azzio_cli")
    exec(compile(bundle_source(), "azzio_cli", "exec"), cli.__dict__)
    assert "gtk-menu-images=true" in cli.gtk3_settings_ini(True)
    assert "gtk-menu-images=true" in cli.gtk3_settings_ini(False)


# --- uca.xml + link script (actions.py) -------------------------------------

def test_uca_xml_is_wellformed_with_the_three_actions():
    # A malformed uca.xml (e.g. an unescaped &&) makes the file manager drop actions silently. After the
    # batch (item 7), "Edit with gedit" is NOT a uca action anymore -- it comes from the built-in
    # default-opener relabelled by the gettext .mo -- so the uca set is gimp + Create Link +
    # Open Terminal (in that order).
    dom = minidom.parseString(actions.uca_xml())
    names = [n.firstChild.data for n in dom.getElementsByTagName("name")]
    assert names == [
        "Edit with gimp",
        "Create Link (Website URL or Directory or File)",
        "Open Terminal Here",
    ]
    # gedit must NOT reappear as a uca action (that was the duplicate we removed).
    assert "Edit with gedit" not in names


def test_uca_gimp_on_images_only():
    # PROMPT batch item 7: keep "Edit with gimp" on IMAGES only. (gedit is handled by the .mo
    # relabel of the built-in default-opener, tested in test_configuration_file_manager_locale-style
    # asserts below, not as a uca action.)
    dom = minidom.parseString(actions.uca_xml())
    acts = dom.getElementsByTagName("action")
    gimp_act = acts[0]
    assert gimp_act.getElementsByTagName("name")[0].firstChild.data == "Edit with gimp"
    gimp_conds = {c.tagName for c in gimp_act.childNodes if c.nodeType == c.ELEMENT_NODE}
    assert "image-files" in gimp_conds
    assert "text-files" not in gimp_conds  # gimp ONLY on images
    assert "directories" not in gimp_conds


def test_uca_folder_actions_carry_range_for_background_visibility():
    # VERIFIED: without <range>, the folder-only actions (Create Link, Open Terminal Here) do
    # NOT appear on the folder background. Every action must carry <range> (now three actions).
    xml = actions.uca_xml()
    assert xml.count("<range></range>") == 3  # one per action (gimp, Create Link, Open Terminal)


def test_uca_create_link_and_terminal_target_the_right_commands():
    xml = actions.uca_xml()
    # Create Link calls the shipped link helper by absolute path; Open Terminal runs kitty.
    assert actions.LINK_SCRIPT_DEST in xml
    assert "zenity --entry" in xml            # prompts for name + target
    assert f"{actions.TERMINAL_BIN} --working-directory %f" in xml
    # the && in the Create Link command is XML-escaped (else the file is malformed)
    assert "&amp;&amp;" in xml


def test_link_script_matches_prompt_behaviour():
    # PROMPT task 2: URL -> <name>.html redirect; path -> symlink <name> -> target (realpath'd).
    script = actions.link_script()
    assert script.startswith("#!/usr/bin/env bash")
    assert 'window.location.href' in script          # the HTML redirect
    assert "ln -s -- " in script                      # the symlink branch
    assert 'realpath -- ' in script                   # realpath the existing target
    assert 'www.*' in script                          # www. counts as a URL
    assert "scheme" not in script or "://" in script  # URL scheme detection present


# --- sidebar (sidebar.py) ---------------------------------------------------

def test_sidebar_bookmarks_come_from_home_directory_resolved():
    # PROMPT task 2: sidebar entries point at RESOLVED targets, driven by home_directory.
    bm = sidebar.gtk_bookmarks()
    # Config resolves to .config (not the /home/main/Config symlink path).
    assert "file:///home/main/.config Config" in bm
    assert "file:///home/main/.cache Cache" in bm
    assert "file:///home/main/.local/share/Trash/files Trash" in bm
    assert "file:///home/main/Downloads Downloads" in bm


def test_sidebar_skips_desktop_to_avoid_builtin_duplicate():
    # The file manager shows a built-in Desktop at the same path; adding our own would DUPLICATE it,
    # so sidebar.py skips Desktop (the built-in serves it). Verified in the VM.
    bm = sidebar.gtk_bookmarks()
    assert "Desktop" in sidebar._BUILTIN_PROVIDED
    assert " Desktop\n" not in bm  # no "... Desktop" bookmark line


def test_sidebar_bookmarks_have_no_comment_lines():
    # The GTK bookmarks format parses EVERY non-blank line as a bookmark; a comment would show
    # as a bogus sidebar entry.
    for line in sidebar.gtk_bookmarks().splitlines():
        if line.strip():
            assert line.startswith("file://"), line


def test_sidebar_covers_the_full_layout_set_minus_desktop():
    # Every home_directory sidebar label except the built-in-provided ones appears.
    bm = sidebar.gtk_bookmarks()
    for label, _target in home_directory.sidebar_entries():
        if label in sidebar._BUILTIN_PROVIDED:
            continue
        assert f" {label}\n" in bm or bm.rstrip().endswith(f" {label}"), label


# --- launcher (launcher.py) -------------------------------------------------

def test_file_manager_desktop_renamed_and_custom_icon():
    # The launcher is renamed to the product name "File Manager" + custom icon. PROMPT: rename the
    # file manager from "Azzio File Manager" to simply "File Manager".
    d = launcher.file_manager_desktop()
    assert "Name=File Manager\n" in d
    # the visible Name line is the product name, not the stock "Thunar File Manager"
    assert "Name=Thunar File Manager" not in d
    # and not the old "Azzio File Manager" product name either.
    assert "Name=Azzio File Manager" not in d
    assert f"Icon={launcher.FILE_MANAGER_ICON_NAME}\n" in d
    # StartupWMClass pins the switcher's window->.desktop resolver to this launcher via the
    # primary (StartupWMClass) match, so the Alt-Tab tile shows the custom icon. The running
    # Thunar window's WM_CLASS res_class is "Thunar" (g_set_application_name("Thunar")).
    assert "StartupWMClass=Thunar\n" in d
    # stock Exec + actions preserved (binary + .desktop id stay `thunar`)
    assert "Exec=thunar %U" in d
    assert "Actions=open-home;open-computer;open-trash;" in d


# --- menu cleanup (menu_cleanup.py) -----------------------------------------

def test_menu_cleanup_hides_the_extra_launchers():
    # Bulk Rename, File Manager Preferences, About Xfce hidden via NoDisplay=true. The Removable
    # Drives (thunar-volman-settings) launcher is NOT hidden here: the thunar-volman plugin that
    # owned it was dropped from the manifest, so the launcher is never installed to begin with.
    basenames = {b for b, _n, _e, _i in menu_cleanup.SUPPRESSED}
    assert "thunar-bulk-rename.desktop" in basenames
    assert "thunar-settings.desktop" in basenames
    assert "xfce4-about.desktop" in basenames
    assert "thunar-volman-settings.desktop" not in basenames
    for dest, body in menu_cleanup.builders():
        assert "NoDisplay=true" in body, dest
        assert dest.startswith("/usr/share/applications/")


# --- emit_plan (file_manager/__init__.py) -----------------------------------

def test_emit_plan_owners_and_paths():
    plan = fm.emit_plan()
    by_dest = {e["dest"]: e for e in plan}
    # HOME (skel-mirrored) config files
    for home_path in (settings.FILE_MANAGER_RC_PATH, settings.XFCONF_FILE_MANAGER_PATH, settings.GTK_CSS_PATH,
                      sidebar.GTK_BOOKMARKS_PATH, actions.UCA_PATH):
        assert by_dest[home_path]["owner"] == "home", home_path
    # SYSTEM (root) files: the link script (executable), the icon SVG, the .desktop overrides
    assert by_dest[actions.LINK_SCRIPT_DEST]["owner"] == "root"
    assert by_dest[actions.LINK_SCRIPT_DEST]["mode"] == 0o755  # executable
    assert by_dest[launcher.ICON_SCALABLE_PATH]["owner"] == "root"
    assert by_dest[launcher.FILE_MANAGER_DESKTOP_PATH]["owner"] == "root"


def test_emit_plan_ships_icon_svg_and_png_rasterizations():
    plan = fm.emit_plan()
    # the scalable SVG asset entry
    svg = next(e for e in plan if e["dest"] == launcher.ICON_SCALABLE_PATH)
    assert svg.get("asset") == launcher.ICON_ASSET
    # a PNG render per configured size
    for size in launcher.ICON_PNG_SIZES:
        dest = f"/usr/share/icons/hicolor/{size}x{size}/apps/{launcher.FILE_MANAGER_ICON_NAME}.png"
        e = next(x for x in plan if x["dest"] == dest)
        assert e.get("render") == {"asset": launcher.ICON_ASSET, "size": size}


def test_emit_plan_desktop_overrides_match_iso_app_overrides():
    # The package-owned .desktop dests (thunar.desktop + the four NoDisplay overrides) must be
    # in pacman.ISO_APP_OVERRIDES so compiler stages them post-pacstrap (not the overlay).
    import pacman
    override_targets = {t for _b, t, _r in pacman.ISO_APP_OVERRIDES}
    desktop_dests = [e["dest"] for e in fm.emit_plan()
                     if e["dest"].startswith("/usr/share/applications/")]
    assert desktop_dests, "expected some .desktop overrides"
    for dest in desktop_dests:
        assert dest in override_targets, dest


def test_mo_locale_catalog_dests_are_iso_app_overrides():
    # REGRESSION (build broke with "thunar: .../en_GB/LC_MESSAGES/thunar.mo exists in
    # filesystem"): the `thunar` package OWNS the en_GB locale catalog, so every locale .mo
    # dest the file manager emits MUST be in pacman.ISO_APP_OVERRIDES -- otherwise compiler._emit_apps
    # plants it in the airootfs overlay and pacstrap's pre-extraction file-conflict check
    # aborts the whole ISO build. This pins the fix so the .mo cannot regress back into the
    # overlay path.
    import pacman
    override_targets = {t for _b, t, _r in pacman.ISO_APP_OVERRIDES}
    mo_dests = [e["dest"] for e in fm.emit_plan()
                if e["dest"].startswith("/usr/share/locale/")
                and e["dest"].endswith("/thunar.mo")]
    assert set(mo_dests) == {locale.mo_path(l) for l in locale.LOCALES}, mo_dests
    for dest in mo_dests:
        assert dest in override_targets, dest
    # And each is NoExtract'd (the property that actually prevents the pacstrap conflict).
    noextract = pacman._ISO_NOEXTRACT
    for dest in mo_dests:
        assert dest.lstrip("/") in noextract, dest


def test_icon_asset_exists():
    import paths
    assert (paths.ASSETSDIR / launcher.ICON_ASSET).is_file()


def test_trash_delete_is_permanent_not_re_trash():
    # NEW PROMPT: "Trying to delete something in Trash doesn't work, it drops it into Trash, which
    # makes it increment itself by .2 (e.g. 'LibreOffice Impress.2.2.2.2....odp' -- can't delete
    # it)." Azzio's sidebar "Trash" bookmark opens the PHYSICAL spool path
    # (file://.../.local/share/Trash/files), not the virtual trash:/// scheme -- so a file there is
    # still scheme `file` (is_local + can_be_trashed both TRUE) and the stock safety nets miss it,
    # and Delete calls g_file_trash() on an already-trashed file, which GIO re-trashes with a ".2"
    # collision suffix. The fork adds a predicate for "physically inside the XDG trash spool" and
    # wires it into BOTH the unlink safety net (makes Delete permanent) and the menu label logic
    # (shows "Delete", not "Move to Trash"). Baked into the vendored fork:
    gio_h = (fm.SOURCE_DIR / "thunar" / "thunar-gio-extensions.h").read_text()
    gio_c = (fm.SOURCE_DIR / "thunar" / "thunar-gio-extensions.c").read_text()
    app_c = (fm.SOURCE_DIR / "thunar" / "thunar-application.c").read_text()
    am_c = (fm.SOURCE_DIR / "thunar" / "thunar-action-manager.c").read_text()

    # (1) The header DECLARES the new predicate.
    assert "thunar_g_file_is_in_trash_dir (GFile *file);" in gio_h, (
        "thunar-gio-extensions.h must declare thunar_g_file_is_in_trash_dir"
    )

    # (2) gio-extensions.c DEFINES it: native-only (trash:/// is is_trashed()'s job), and a
    # descendant of a "Trash" dir built from the XDG data dir. Anchor on the DEFINITION body
    # (signature + "\n{"). Match CALL forms so words in the function's doc comment can't trip a guard.
    gio_body = gio_c.split("\nthunar_g_file_is_in_trash_dir (GFile *file)", 1)[1]
    gio_body = gio_body.split("\n{", 1)[1].split("\n}", 1)[0]
    assert "g_file_is_native (file)" in gio_body, "is_in_trash_dir must restrict to native file:// paths"
    assert 'g_build_filename (g_get_user_data_dir (), "Trash", NULL)' in gio_body, (
        "is_in_trash_dir must build the trash path from g_get_user_data_dir()/Trash"
    )
    assert "thunar_g_file_is_descendant (file, trash_dir)" in gio_body, (
        "is_in_trash_dir must test descendancy of the trash spool"
    )

    # (3) thunar_application_unlink_files forces a PERMANENT unlink when a file is physically inside
    # the trash spool -- this is what actually stops the re-trash, regardless of which UI path
    # reached it. Anchor on the DEFINITION body and match the CALL form "permanently = TRUE" guarded
    # by the predicate call.
    app_body = app_c.split("\nthunar_application_unlink_files (ThunarApplication", 1)[1]
    app_body = app_body.split("\n{", 1)[1].split("\n}", 1)[0]
    assert "thunar_g_file_is_in_trash_dir (thunar_file_get_file (lp->data))" in app_body, (
        "unlink_files must test each file with thunar_g_file_is_in_trash_dir"
    )
    # The predicate and the force-permanent assignment both live in the loop.
    assert "permanently = TRUE" in app_body, "unlink_files must be able to force permanently=TRUE"

    # (4) thunar_action_manager_show_trash returns FALSE when the parent folder is the trash -- by
    # the virtual scheme (is_trashed) OR the physical spool (is_in_trash_dir) -- so the menu reads
    # "Delete" (permanent) rather than "Move to Trash" inside the trash. Anchor on the DEFINITION
    # body; match the CALL form so the explanatory comment's bare "is_in_trash_dir()" can't satisfy it.
    am_body = am_c.split("\nthunar_action_manager_show_trash (ThunarActionManager *action_mgr)", 1)[1]
    am_body = am_body.split("\n{", 1)[1].split("\n}", 1)[0]
    assert "thunar_g_file_is_in_trash_dir (thunar_file_get_file (action_mgr->parent_folder))" in am_body, (
        "show_trash must test the parent folder with thunar_g_file_is_in_trash_dir"
    )
    assert "thunar_file_is_trashed (action_mgr->parent_folder)" in am_body, (
        "show_trash must also catch the virtual trash:/// scheme via is_trashed"
    )
    # The trash-detection guard must early-return FALSE (so "Move to Trash" is suppressed). The
    # predicate closes the `if (...)` condition and the very next statement is `return FALSE;`.
    after_guard = am_body.split(
        "thunar_g_file_is_in_trash_dir (thunar_file_get_file (action_mgr->parent_folder))", 1
    )[1]
    assert after_guard.lstrip().startswith(")\n    return FALSE;"), (
        "the in-trash guard in show_trash must immediately return FALSE"
    )


# --- Emblems / mount-point icon removed (thunar-file.c) ----------------------
def test_only_the_symlink_emblem_survives_at_the_single_chokepoint():
    # PROMPT: "Symbolic links must have their icon by an arrow of a sort." So the ONE place every
    # view + the mount-point overlay derive their emblem list (thunar_file_get_emblem_names) now
    # emits EXACTLY the symbolic-link emblem for symlinks and nothing else -- the custom emblems,
    # the cant-read / cant-write badges AND the mount-point GMount overlay all stay suppressed
    # (mount points get their own BASE icon instead). Anchor on the DEFINITION body so a mention in
    # a doc comment cannot satisfy it.
    file_c = (fm.SOURCE_DIR / "thunar" / "thunar-file.c").read_text()
    body = _c_definition_body(file_c, "thunar_file_get_emblem_names (ThunarFile *file)")
    code = _strip_c_comments(body)
    # The symlink arrow is emitted, gated on the file actually being a symlink, and it is the ONLY
    # list-building call in the body (a single g_list_prepend of the symbolic-link constant).
    assert "thunar_file_is_symlink (file)" in code, "the arrow must be gated on is_symlink"
    assert "EMBLEM_NAME_SYMBOLIC_LINK" in code, "the symlink emblem must be emitted"
    assert code.count("g_list_prepend") == 1 and "g_list_append" not in code, (
        "get_emblem_names must build ONLY the single symlink emblem"
    )
    # A non-symlink still returns NULL (nothing drawn).
    assert "return NULL;" in code
    # Every OTHER emblem source stays gone: no permission badges, no mount overlay.
    for token in ("EMBLEM_NAME_CANT_READ", "EMBLEM_NAME_CANT_WRITE", "emblem_names",
                  "g_mount_get_icon", "is_mountpoint"):
        assert token not in code, token


# --- Devices section removed in C (thunar-shortcuts-model.c) -----------------
def test_devices_section_removed_in_vendored_source():
    # PROMPT batch item 1: remove the "Devices" section ENTIRELY (belt-and-suspenders with the
    # xfconf hidden-bookmarks in test_devices_and_file_system_removed_via_hidden_bookmarks). The
    # vendored shortcut_devices() now only takes the device-monitor ref (so finalize's unref stays
    # valid) and adds NO header / NO "File System" row / NO device rows / connects NO add/remove
    # signals. Anchor on its DEFINITION body.
    sm_c = (fm.SOURCE_DIR / "thunar" / "thunar-shortcuts-model.c").read_text()
    # Anchor on the DEFINITION (its signature is followed by "\n{"); the identical forward
    # declaration earlier in the file ends in ";" and is skipped.
    code = _strip_c_comments(
        _c_definition_body(sm_c, "thunar_shortcuts_model_shortcut_devices (ThunarShortcutsModel *model)")
    )
    # The device monitor is still acquired (finalize unconditionally unrefs it).
    assert "thunar_device_monitor_get ()" in code
    # But NO File System row and NO Devices header/group are added here any more.
    assert "file:///" not in code, "shortcut_devices must not add the File System row"
    assert "THUNAR_SHORTCUT_GROUP_DEVICES" not in code, "shortcut_devices must add no Devices group rows"
    # And it wires up NONE of the device add/remove/change signal handlers.
    assert "device-added" not in code and "device-removed" not in code, (
        "shortcut_devices must connect no device signals"
    )


# --- "Send To" removed from the context menus (thunar-window.c / -standard-view.c) ---------
def test_send_to_section_removed_from_context_menus():
    # PROMPT: delete "Send To". The context menu is assembled from section flags passed to
    # thunar_menu_add_sections; dropping THUNAR_MENU_SECTION_SENDTO from the two menu-building
    # sites removes the "Send To" submenu from BOTH the folder/file right-click menu
    # (thunar-standard-view.c) and the background/window menu (thunar-window.c).
    win_c = (fm.SOURCE_DIR / "thunar" / "thunar-window.c").read_text()
    sv_c = (fm.SOURCE_DIR / "thunar" / "thunar-standard-view.c").read_text()
    # The background/window file menu (thunar_window_update_file_menu) no longer requests SENDTO.
    win_body = _c_definition_body(
        win_c,
        "thunar_window_update_file_menu (ThunarWindow *window,\n"
        "                                GtkWidget    *menu)",
    )
    assert "THUNAR_MENU_SECTION_OPEN" in win_body, "sanity: still building the window file menu"
    assert "THUNAR_MENU_SECTION_SENDTO" not in win_body, (
        "the window/background context menu must not add the Send To section"
    )
    # The file/folder context menu (thunar_standard_view_context_menu). Reduce both source files to
    # their whitespace-collapsed form so the ONLY thing that matters is whether SENDTO sits in a
    # section-flags OR chain (that is the context-menu request), independent of indentation.
    sv_flat = " ".join(sv_c.split())
    # SENDTO must NOT be OR'd into the context-menu section list (would follow OPEN | ...).
    assert "THUNAR_MENU_SECTION_OPEN | THUNAR_MENU_SECTION_SENDTO" not in sv_flat, (
        "the file/folder context menu must not add the Send To section"
    )
    win_flat = " ".join(win_c.split())
    assert "THUNAR_MENU_SECTION_SENDTO | THUNAR_MENU_SECTION_CREATE_NEW_FILES" not in win_flat


# --- Azzio-branded check/radio menu indicators (thunar-application.c CSS) -----------------
def test_toggle_menu_indicators_are_branded_in_app_css():
    # PROMPT: the "Show Hidden Files" toggle and the "Arrange Items" sort check/radio items need
    # our look. Those indicators are drawn by the GTK widget theme's CSS, not the icon theme, so
    # they are styled in the file manager's OWN app-scoped CSS provider (thunar_application_load_css,
    # added for_screen so it reaches popup menus, and only in the file manager process). The checked
    # state is the Azzio cyan-to-blue; the nodes are the GTK3 "menuitem check"/"menuitem radio".
    app_c = (fm.SOURCE_DIR / "thunar" / "thunar-application.c").read_text()
    css = _c_definition_body(app_c, "thunar_application_load_css (void)")
    assert "menuitem check" in css and "menuitem radio" in css, (
        "the app CSS must style the check/radio menu indicators"
    )
    assert ":checked" in css, "the checked state must be styled"
    # Uses the Azzio brand colours (the mid cyan and the deep blue gradient stop).
    assert "#06B8FD" in css or "#0064F9" in css, "the toggle indicator must use the Azzio brand"


# --- "main" sidebar row uses its own folder icon (thunar-shortcuts-model.c) --
def test_home_sidebar_row_uses_its_folder_icon_not_go_home():
    # PROMPT: "The 'main' directory on the sidebar must be its directory icon." Upstream forced the
    # Home place's gicon to the generic "go-home" action glyph, which the shortcuts icon renderer
    # draws IN PREFERENCE to the file's own icon. Dropping that line leaves gicon NULL, so the row
    # falls through to the file icon -> thunar_file_get_icon_name -> "user-home" (our Azzio home
    # folder). Assert the home entry no longer sets a go-home gicon.
    sm_c = (fm.SOURCE_DIR / "thunar" / "thunar-shortcuts-model.c").read_text()
    code = _strip_c_comments(sm_c)
    # The generic go-home glyph must not be forced anywhere in the model any more.
    assert 'g_themed_icon_new ("go-home")' not in code, (
        "the home row must not override its icon with go-home"
    )


# --- Properties dialog: Emblems + Highlight pages removed --------------------
def test_properties_emblems_and_highlight_pages_removed():
    # PROMPT: in Properties, delete the "Emblems" and "Highlight" pages -- both the tabs AND their
    # functionality. Neither notebook page is appended any more, so there is no access path. The
    # Permissions page is still appended (the dialog goes free-space row -> Permissions).
    pd_c = (fm.SOURCE_DIR / "thunar" / "thunar-properties-dialog.c").read_text()
    # No notebook page is built from the emblem chooser or a highlight grid: the only appended
    # pages left are the (kept) general/permissions ones. Assert the emblem/highlight widgets are
    # never handed to gtk_notebook_append_page.
    appended_pages = re.findall(
        r"gtk_notebook_append_page \(GTK_NOTEBOOK \(dialog->notebook\), ([^,]+),", pd_c
    )
    assert appended_pages, "expected to still find the (kept) notebook page appends"
    for appended in appended_pages:
        assert "emblem" not in appended.lower(), appended
        assert "highlight" not in appended.lower(), appended
    # The emblem chooser is never even constructed any more (belt-and-suspenders: no page CAN
    # be built from it).
    assert "thunar_emblem_chooser_new" not in _strip_c_comments(pd_c), (
        "the emblem chooser must not be constructed at all"
    )
    # The Highlight page's example box is never created, so NOTHING on a LIVE path may call
    # colorize_example_box (it dereferences the NULL box). In particular the single-file update
    # must no longer colour it from show_file_highlight_tab. The colorize helper + its callers
    # may still be DEFINED (dead code), but they must be unreachable: assert no code path guards
    # a colorize call on show_file_highlight_tab, and the widget is never assigned.
    update_body = _strip_c_comments(
        _c_definition_body(pd_c, "thunar_properties_dialog_update_single (ThunarPropertiesDialog *dialog)")
    )
    assert "colorize_example_box" not in update_body, (
        "the single-file update must not colour the (never-created) highlight example box"
    )
    assert "show_file_highlight_tab" not in update_body, (
        "show_file_highlight_tab must no longer drive the single-file update"
    )
    assert "dialog->example_box =" not in _strip_c_comments(pd_c), (
        "example_box is never created -- any colorize call would dereference NULL"
    )
    # The Permissions page is still there.
    assert "dialog->permissions_chooser" in pd_c
    # The removal is documented with the AZZIO marker (so the intent is auditable).
    assert "AZZIO" in pd_c and "Emblems" in pd_c and "Highlight" in pd_c


# --- Path entry: primary directory icon removed + drag disabled -------------
def test_path_entry_primary_icon_cleared_and_drag_is_a_noop():
    # PROMPT: the tiny draggable directory icon on the left of the location bar is "weird and
    # buggy -- remove it". update_icon() now clears the PRIMARY icon (NULL) in the normal case but
    # KEEPS the search-mode magnifier; the icon-press handler is gutted to a no-op return FALSE.
    pe_c = (fm.SOURCE_DIR / "thunar" / "thunar-path-entry.c").read_text()
    # update_icon clears the primary icon for the non-search case.
    assert "gtk_entry_set_icon_from_icon_name (GTK_ENTRY (path_entry), GTK_ENTRY_ICON_PRIMARY, NULL);" in pe_c
    # ...but the search magnifier is still set when in search mode.
    assert "system-search" in pe_c, "search-mode magnifier must be kept"
    # The icon-press handler is a no-op (no drag is ever started from it). Anchor on the
    # DEFINITION (signature followed by "\n{"); the forward declaration ends in ";" and is
    # skipped. The full param list disambiguates it from any other icon-press symbol.
    press_signature = (
        "thunar_path_entry_icon_press_event (GtkEntry            *entry,\n"
        "                                    GtkEntryIconPosition icon_pos,\n"
        "                                    GdkEventButton      *event,\n"
        "                                    gpointer             user_data)"
    )
    press_body = _strip_c_comments(_c_definition_body(pe_c, press_signature))
    assert "return FALSE;" in press_body
    assert "drag_begin" not in press_body and "gtk_drag_begin" not in press_body, (
        "icon-press must not start a drag"
    )
    # The AZZIO marker documents the removal.
    assert "AZZIO" in pe_c


# --- Azzio folder basename -> icon-name table (thunar-file.c) ----------------
def test_azzio_folder_basename_icon_table_maps_the_unnamed_home_dirs():
    # The home dirs with no XDG type (Projects, Vault, Ignore), the mount points (Shared, Mounts)
    # and the dot-location SHORTCUTS (Cache/Config/Trash/Local/SSH -> .cache/.config/...) get our
    # OWN icon names via a basename table in thunar_file_get_icon_name, checked ONLY after the XDG
    # table misses and ONLY for a direct child of $HOME. Shared + Mounts share the mount-point
    # symbol (we removed the mount emblem, so the base icon carries it).
    file_c = (fm.SOURCE_DIR / "thunar" / "thunar-file.c").read_text()
    assert "azzio_folder_dirs[]" in file_c, "the Azzio basename->icon table must exist"
    # The mapping pins each dir/shortcut to its icon name.
    for basename, icon in (
        ("Projects", "folder-projects"),
        ("Vault", "folder-vault"),
        ("Ignore", "folder-ignore"),
        ("Shared", "folder-mount"),
        ("Mounts", "folder-mount"),
        ("Cache", "folder-cache"),
        ("Config", "folder-config"),
        ("Trash", "folder-trash"),
        ("Local", "folder-local"),
        ("SSH", "folder-ssh"),
    ):
        assert f'{{ "{basename}", "{icon}" }}' in file_c, (basename, icon)
    # The lookup builds "$HOME/<basename>" and only fires when the XDG table left the default.
    assert "g_build_filename (azzio_home" in file_c
    assert 'strcmp (*special_names, "folder") == 0' in file_c or "*special_names == NULL" in file_c
    # On a match it CACHES the name and RETURNS directly (bypassing the has_icon-gated check_names
    # loop), so a cold GtkIconTheme lookup cache can't make it fall back to the generic folder icon
    # until a reload -- the "icons only fix themselves after a right-click" symptom (PROMPT). The
    # home ("user-home") branch does the same for the same reason.
    assert "file->icon_name = g_strdup (azzio_folder_dirs[i].icon_name);" in file_c
    assert 'file->icon_name = g_strdup ("user-home");' in file_c
