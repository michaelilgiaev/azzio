"""packages.gedit -- notepad mode: one window per file, NO tabs, minimal headerbar, Ctrl+W exits.

Why these tests matter: gedit 50 (the gedit-technology fork) produces tabs and a full
headerbar in several ways, and notepad mode defeats them with FOUR things -- the launcher
override (--standalone --new-window + DBusActivatable=false), the show-tabs-mode='never'
schema override, an active-plugins override enabling our compiled libpeas plugin, and the
plugin itself (removes New Tab, strips the headerbar, makes Ctrl+W exit). If any regresses
gedit silently goes back to tabs / a full headerbar / a double-exit Ctrl+W. compiler
._emit_apps also relies on the override entry carrying "compile_schemas": True and on
build_plugin() compiling the .so, so those are pinned too.
"""

from __future__ import annotations

from packages import gedit


def test_emit_plan_has_desktop_schema_and_plugin_metadata():
    plan = gedit.emit_plan()
    assert len(plan) == 3
    by_dest = {e["dest"]: e for e in plan}
    assert gedit.DESKTOP_ENTRY_PATH in by_dest
    assert gedit.GSCHEMA_OVERRIDE_PATH in by_dest
    assert gedit.GEDIT_PLUGIN_METADATA_DEST in by_dest


def test_all_entries_are_root_owned_system_conf():
    # All three are system-wide files (a launcher, a schema override, a plugin .plugin),
    # root-owned, plain data.
    for entry in gedit.emit_plan():
        assert entry["owner"] == "root", entry["dest"]
        assert entry["mode"] == 0o644, entry["dest"]


def test_desktop_overrides_the_stock_gedit_launcher_path():
    entry = next(e for e in gedit.emit_plan() if e["dest"] == gedit.DESKTOP_ENTRY_PATH)
    assert entry["builder"] is gedit.desktop_entry
    # It must overwrite the SAME path the gedit package ships, or the stock (tab) launcher
    # still wins.
    assert gedit.DESKTOP_ENTRY_PATH == "/usr/share/applications/org.gnome.gedit.desktop"


def test_desktop_exec_forces_standalone_new_window():
    # The core "opened twice = another window": every launch is an independent process
    # (--standalone) with a fresh window (--new-window). %U passes the file(s).
    out = gedit.desktop_entry()
    exec_lines = [ln for ln in out.splitlines() if ln.startswith("Exec=")]
    assert exec_lines, "no Exec line"
    main_exec = exec_lines[0]
    assert main_exec == "Exec=gedit --standalone --new-window %U"


def test_desktop_disables_dbus_activation():
    # Load-bearing: with DBusActivatable=true the desktop launch routes files over D-Bus
    # into the running gedit as TABS and ignores the Exec flags. Must be false.
    out = gedit.desktop_entry()
    assert "DBusActivatable=false" in out
    assert "DBusActivatable=true" not in out


def test_desktop_actions_are_also_standalone():
    # The right-click "New Window"/"New Document" actions must stay independent too.
    out = gedit.desktop_entry()
    assert "Exec=gedit --standalone --new-window" in out
    assert "Exec=gedit --standalone --new-document" in out


def test_desktop_keeps_stock_identity_fields():
    # We only change launch behaviour; icon/mime/categories must stay so gedit still looks
    # and associates exactly as the package intends.
    out = gedit.desktop_entry()
    assert out.splitlines()[0] == "[Desktop Entry]"
    assert "Icon=org.gnome.gedit" in out
    assert "MimeType=text/plain;application/x-zerosize;" in out
    assert "Categories=GNOME;GTK;Utility;TextEditor;" in out


def test_schema_override_hides_the_tab_bar():
    # One tab source: the notebook tab bar. The override sets its default to 'never' under
    # the correct schema, so no tab strip is ever shown.
    out = gedit.gschema_override()
    assert gedit.GEDIT_UI_SCHEMA == "org.gnome.gedit.preferences.ui"
    assert f"[{gedit.GEDIT_UI_SCHEMA}]" in out
    assert gedit.SHOW_TABS_MODE == "never"
    assert "show-tabs-mode='never'" in out


def test_schema_override_enables_the_notepad_plugin():
    # The plugin (which removes New Tab, strips the headerbar and makes Ctrl+W exit) must be
    # enabled via active-plugins -- ADDED to gedit's default plugin set, not replacing it.
    out = gedit.gschema_override()
    assert gedit.GEDIT_PLUGINS_SCHEMA == "org.gnome.gedit.plugins"
    assert f"[{gedit.GEDIT_PLUGINS_SCHEMA}]" in out
    assert gedit.MODIFICATIONS_PLUGIN_MODULE == "gedit-modifications"
    # our module is present AND gedit's stock defaults are kept.
    assert "'gedit-modifications'" in out
    for stock in gedit.GEDIT_DEFAULT_PLUGINS:
        assert f"'{stock}'" in out
    assert gedit.ACTIVE_PLUGINS[-1] == "gedit-modifications"


def test_schema_override_sets_editor_font_from_scale():
    # Azzio pins a fixed editor font at the STOCK baseline from the single scale source (the
    # GLOBAL SCALE's DPI channel bumps it -- at 1.35 it renders ~= the old 18pt). use-default-font
    # MUST be false (else gedit ignores editor-font), and editor-font is 'Monospace <stock>'.
    from packages.openbox import scale
    out = gedit.gschema_override()
    assert gedit.GEDIT_EDITOR_SCHEMA == "org.gnome.gedit.preferences.editor"
    assert f"[{gedit.GEDIT_EDITOR_SCHEMA}]" in out
    # DERIVES from scale (== kitty's baseline; not a raw 18).
    assert gedit.GEDIT_FONT_SIZE == scale.TERMINAL_EDITOR_FONT_STOCK
    assert gedit.GEDIT_FONT_SIZE != 18
    assert gedit.GEDIT_EDITOR_FONT == f"Monospace {gedit.GEDIT_FONT_SIZE}"
    assert gedit.GEDIT_USE_DEFAULT_FONT is False
    # use-default-font off (so editor-font wins) and editor-font set to the scale baseline.
    assert "use-default-font=false" in out
    assert "use-default-font=true" not in out
    assert f"editor-font='Monospace {gedit.GEDIT_FONT_SIZE}'" in out


def test_schema_override_sets_dark_and_light_editor_style_schemes():
    # gedit follows the system theme: the GTK dark flag handles the chrome, and gedit 50 keeps
    # TWO editor style-scheme keys (dark + light variant) and auto-picks between them by that
    # flag. Seed BOTH with schemes the fork actually ships ('oblivion' dark / 'classic' light);
    # 'classic-dark' does NOT exist in this fork, so it must not be used.
    out = gedit.gschema_override()
    assert gedit.GEDIT_STYLE_SCHEME_DARK == "oblivion"
    assert gedit.GEDIT_STYLE_SCHEME_LIGHT == "classic"
    assert gedit.GEDIT_STYLE_SCHEME_DARK_KEY == "style-scheme-for-dark-theme-variant"
    assert gedit.GEDIT_STYLE_SCHEME_LIGHT_KEY == "style-scheme-for-light-theme-variant"
    assert "style-scheme-for-dark-theme-variant='oblivion'" in out
    assert "style-scheme-for-light-theme-variant='classic'" in out
    assert "classic-dark" not in out


def test_schema_override_entry_triggers_recompile():
    # A glib override file is INERT until glib-compile-schemas runs; the override entry must
    # carry the flag compiler._emit_apps keys off, and the command must target the schemas
    # dir.
    entry = next(e for e in gedit.emit_plan() if e["dest"] == gedit.GSCHEMA_OVERRIDE_PATH)
    assert entry.get("compile_schemas") is True
    assert gedit.RUN_COMPILE_SCHEMAS == ["glib-compile-schemas", gedit.GLIB_SCHEMAS_DIR]
    assert gedit.GLIB_SCHEMAS_DIR == "/usr/share/glib-2.0/schemas"


def test_override_filename_sorts_after_stock_schema():
    # glib applies overrides in filename order; a leading digit >= the stock schema's ensures
    # our value wins. Our file is 90_-prefixed.
    assert gedit.GSCHEMA_OVERRIDE_PATH.rsplit("/", 1)[1].startswith("90_")
    assert gedit.GSCHEMA_OVERRIDE_PATH.endswith(".gschema.override")


def test_plugin_metadata_is_generated_from_python():
    # The .plugin INI is GENERATED by plugin_metadata() (this Python is the single source of
    # truth -- there is no static .plugin file in the tree); its Module id must equal the
    # active-plugins id or the override enables a plugin gedit cannot find.
    entry = next(e for e in gedit.emit_plan() if e["dest"] == gedit.GEDIT_PLUGIN_METADATA_DEST)
    assert entry["builder"] is gedit.plugin_metadata
    assert gedit.GEDIT_PLUGIN_METADATA_DEST == "/usr/lib/gedit/plugins/gedit-modifications.plugin"
    out = gedit.plugin_metadata()
    assert out.splitlines()[0] == "[Plugin]"
    assert f"Module={gedit.MODIFICATIONS_PLUGIN_MODULE}" in out
    # The descriptive fields come through so gedit's plugin list is populated.
    assert f"Name={gedit.GEDIT_PLUGIN_NAME}" in out
    assert f"Description={gedit.GEDIT_PLUGIN_DESCRIPTION}" in out
    assert f"IAge={gedit.GEDIT_PLUGIN_IAGE}" in out


def test_no_static_plugin_file_in_source_tree():
    # Standard: the plugin dir holds ONLY Python-adjacent build inputs (C + Makefile). The
    # .plugin manifest must NOT exist as a static file -- it is emitted by plugin_metadata().
    assert not (gedit.GEDIT_PLUGIN_SRC_DIR / gedit.GEDIT_PLUGIN_METADATA_NAME).exists()


def test_plugin_source_tree_is_present_and_buildable():
    # build_plugin() compiles these; the C source + Makefile must exist so the ISO build can
    # produce the .so. (The actual compile happens on the build host in compiler._emit_apps;
    # here we just assert the inputs exist and the wiring points at gedit's plugin dir.) The
    # .plugin manifest is generated (see test_plugin_metadata_is_generated_from_python), not a
    # build input.
    srcs = {p.name for p in gedit._plugin_src_files()}
    assert "gedit_modifications.c" in srcs
    assert "Makefile" in srcs
    assert gedit.GEDIT_PLUGIN_SO_DEST == "/usr/lib/gedit/plugins/libgedit-modifications.so"
    # The plugin C source must actually do the three notepad-mode jobs (guards against a
    # stubbed-out plugin): disable new-tab, strip the headerbar buttons, rebind close.
    csrc = (gedit.GEDIT_PLUGIN_SRC_DIR / "gedit_modifications.c").read_text(encoding="utf-8")
    assert "GeditWindowActivatable" in csrc
    assert "new-tab" in csrc
    assert "win.open" in csrc and "win.save" in csrc      # headerbar buttons hidden
    assert "gtk_window_get_titlebar" in csrc
    assert "gtk_widget_destroy" in csrc                   # Ctrl+W -> destroy window -> exit


def test_build_deps_include_gedit_devstack():
    # The plugin build needs the gedit pkg-config module (pulls in gtk3 + libpeas dev
    # headers) present on the build host; compiler._check_host_deps installs these.
    assert "gedit" in gedit.GEDIT_PLUGIN_BUILD_DEPS
