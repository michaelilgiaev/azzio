"""packages.application_menu -- OUR application menu, baked into the ISO.

The desktop is OpenBox with no panel, so this menu is the WHOLE shell -- a borderless
launcher CENTERED on the screen, opened by the Super key (via xcape + the OpenBox rc.xml
keybind).

The menu is a COMPILED C / GTK3 program now (the earlier Tkinter/Python port was
replaced). The C sources live directly in the package dir with a Makefile; the build
wiring here COMPILES them into a single resident daemon binary
(azzio-application-menu-daemon) and installs it under MENU_LIB_DIR. A thin Python
launcher (installed as the bin entry point) signals that daemon.

These pin the contract that (a) the build compiles + installs the daemon binary and
emits the launcher + .desktop to the fixed system paths the launcher/session expect,
(b) the constants shared with packages/openbox agree, (c) the launcher execs the
BINARY (not a python module), and (d) the C menu keeps its pinned behaviour -- most
importantly, TAB lands on "Shut Down".

The menu dismisses via a global pointer/keyboard grab plus an outside-click hit-test,
with focus-loss and Escape backing it up. There is NO pin and NO panel-icon highlight bar
anymore (both were removed with the panel).
"""

from __future__ import annotations

import configparser
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from packages.application_menu import application_menu as am
from packages import openbox as desktop

CSRC_DIR = Path(am.paths.APPLICATION_MENU_DIR)


# --- Structure: the Python menu is gone, the C sources are flattened up ------

def test_python_menu_modules_are_deleted():
    # The whole Tkinter menu (menu.py + siblings + daemon.py + its tests) was removed
    # when the C port landed. Only the pure-Python LAUNCHER and this build-wiring module
    # remain as Python in the package. Guard against any of them creeping back.
    gone = [
        "menu.py", "applist.py", "widgets.py", "theme.py", "applications.py", "winwatch.py",
        "icons.py", "usage.py", "actions.py", "editing.py", "xfocus.py", "daemon.py",
        "test_menu.py", "conftest.py",
    ]
    for name in gone:
        assert not (CSRC_DIR / name).exists(), f"{name} should have been deleted"
    # The only Python left is the package __init__, the launcher, and the build wiring
    # (the package now carries an __init__.py like every other packages/ subdirectory).
    pyfiles = sorted(p.name for p in CSRC_DIR.glob("*.py"))
    assert pyfiles == ["__init__.py", "application_menu.py", "launcher.py"], pyfiles


def test_csrc_dir_is_flattened_up():
    # The C sources were moved OUT of a nested csrc/ up into the package dir, and csrc/
    # was deleted. menu.c + the Makefile now sit directly beside application_menu.py.
    assert not (CSRC_DIR / "csrc").exists(), "csrc/ should have been removed"
    assert (CSRC_DIR / "menu.c").is_file()
    assert (CSRC_DIR / "Makefile").is_file()
    assert (CSRC_DIR / "theme.h").is_file()
    # A representative spread of the sibling translation units is present at top level.
    for name in ("application_list.c", "applications.c", "usage.c", "icons.c", "actions.c",
                 "window_watch.c", "scrollbar.c", "power.c", "theme.c"):
        assert (CSRC_DIR / name).is_file(), name


def test_app_rows_show_category_as_header_and_name_as_description():
    # PROMPT: each application row must display its CATEGORY as the (big) header and the project
    # NAME as the (small) description -- the reverse of upstream, which put the Name on top. The
    # row painter (application_list.c on_draw) sets the header line in font_name and the sub line
    # in font_sub; the swap is: font_name renders entry->type_label (the humanised category) and
    # font_sub renders entry->name.
    c = (CSRC_DIR / "application_list.c").read_text(encoding="utf-8")
    header = 'pango_layout_set_font_description(lay, l->font_name);'
    sub = 'pango_layout_set_font_description(lay, l->font_sub);'
    header_idx = c.index(header)
    sub_idx = c.index(sub)
    assert header_idx < sub_idx, "header (font_name) line is painted before the description (font_sub)"
    # The text set right after selecting each font is the swapped field. Look at the window of
    # source between each font selection and the next, and assert which entry field it draws.
    header_block = c[header_idx:sub_idx]
    sub_block = c[sub_idx:sub_idx + 400]
    assert "pango_layout_set_text(lay, r->entry->type_label, -1);" in header_block, \
        "the header must render the category (type_label)"
    assert "pango_layout_set_text(lay, r->entry->name, -1);" in sub_block, \
        "the description must render the project name"
    # And the old wiring (Name on the header line) must be gone from the header block.
    assert "pango_layout_set_text(lay, r->entry->name, -1);" not in header_block


# --- The menu follows the system theme (dark by default) --------------------

def test_menu_theme_is_runtime_selected_not_hardcoded():
    # The menu must FOLLOW the system dark/light theme at runtime, not bake one palette in.
    # theme.h declares the runtime accessor + roles; theme.c carries BOTH palettes and reads
    # the freedesktop color-scheme; the AZ_*_COLOR macros expand to the accessor so every
    # existing call site is theme-correct.
    h = (CSRC_DIR / "theme.h").read_text(encoding="utf-8")
    c = (CSRC_DIR / "theme.c").read_text(encoding="utf-8")
    # theme.h: the accessor + the macros resolving to it (not a bare hex literal anymore).
    assert "const char *az_color(AzColorRole role);" in h
    assert "int az_theme_init(void);" in h
    assert "#define AZ_BG_COLOR" in h and "az_color(AZ_C_BG)" in h
    # theme.c: both palettes + the color-scheme probe + the dark default.
    assert "AZ_PALETTE_DARK" in c and "AZ_PALETTE_LIGHT" in c
    assert "org.gnome.desktop.interface color-scheme" in c   # the freedesktop signal
    assert "prefer-dark" in c
    # dark is the fallback default (az_dark initialised to 1).
    assert "static int az_dark = 1;" in c


def test_menu_main_latches_theme_before_styling():
    # main() must call az_theme_init() (so the AZ_*_COLOR macros resolve to the right
    # palette) BEFORE build_window()/install_css() style anything.
    src = (CSRC_DIR / "menu.c").read_text(encoding="utf-8")
    init_idx = src.index("az_theme_init()")
    build_idx = src.index("build_window(m)")
    assert init_idx < build_idx, "az_theme_init() must run before build_window()"


def test_menu_makefile_builds_theme_object():
    # theme.o must be in the Makefile OBJS or the runtime-theme code is never linked.
    mk = (CSRC_DIR / "Makefile").read_text(encoding="utf-8")
    assert "theme.o" in mk


def test_scrollbar_pill_is_twice_as_big():
    # The user asked for the app-menu scrollbar "twice as big". The pill's two WIDTH
    # constants in theme.h were doubled together (visible thumb 4 -> 8, reserved column
    # 9 -> 18), so the pill stays centered in its column and the proportions are unchanged,
    # only larger. THUMB_MIN is the pill's minimum VERTICAL length (grab-ability), NOT a
    # thickness axis, so it is deliberately left as-is (doubling it would change scroll feel,
    # not the bar's visible size). All three are AZ_SCALED() so they still follow the UI scale.
    h = (CSRC_DIR / "theme.h").read_text(encoding="utf-8")
    assert "#define AZ_SCROLL_THUMB_WIDTH  AZ_SCALED(8)" in h    # was 4, doubled
    assert "#define AZ_SCROLL_TRACK_WIDTH  AZ_SCALED(18)" in h   # was 9, doubled with it
    assert "#define AZ_SCROLL_THUMB_MIN    AZ_SCALED(24)" in h   # vertical min, unchanged
    # The old (half-size) widths must be gone, or the bar was not actually grown.
    assert "AZ_SCROLL_THUMB_WIDTH  AZ_SCALED(4)" not in h
    assert "AZ_SCROLL_TRACK_WIDTH  AZ_SCALED(9)" not in h


# --- Emit plan: the launcher + .desktop (the daemon binary is compiled) ------

def test_emit_plan_targets_expected_system_paths():
    # emit_plan() covers the two generated TEXT artifacts: the launcher (run by the
    # Super key / .desktop) and the app-launcher .desktop. The daemon BINARY is NOT in
    # the plan -- it is compiled + installed by build_daemon() -- so it must NOT appear
    # here, but its install path is still a defined constant under MENU_LIB_DIR.
    dests = {e["dest"] for e in am.emit_plan()}
    assert am.MENU_LAUNCHER_SYSTEM_PATH in dests
    assert am.MENU_DESKTOP_SYSTEM_PATH in dests
    assert am.MENU_DAEMON_BIN_SYSTEM_PATH not in dests   # compiled, not text-emitted
    assert am.MENU_DAEMON_BIN_SYSTEM_PATH == f"{am.MENU_LIB_DIR}/azzio-application-menu-daemon"


def test_launcher_is_executable_desktop_is_conf():
    modes = {e["dest"]: e["mode"] for e in am.emit_plan()}
    assert modes[am.MENU_LAUNCHER_SYSTEM_PATH] == 0o755   # runnable by the icon
    assert modes[am.MENU_DESKTOP_SYSTEM_PATH] == 0o644


def test_emitted_content_is_nonempty():
    # Only the TEXT-builder entries (launcher + .desktop) carry a builder; the glyph-icon
    # entries (icons_plan()) are asset/render copies with builder None, checked separately.
    for e in am.emit_plan():
        if e.get("builder") is None:
            continue
        assert e["builder"]().strip(), f"empty content for {e['dest']}"


def test_menu_uses_custom_azzio_glyph_icons_not_stock_names():
    # The power row + search used to resolve the STOCK freedesktop names, which land on the
    # installed Adwaita theme's generic (KDE/breeze-ish) glyphs the user asked to replace.
    # menu.c must now reference OUR Azzio icon names (azzio-*) and NONE of the old stock ones,
    # so the resolver returns our brand glyphs instead.
    src = _menu_c()
    for stock in ("system-suspend", "system-lock-screen", "system-reboot",
                  "system-shutdown", "edit-find"):
        assert stock not in src, f"stock icon name {stock!r} still referenced in menu.c"
    for name in ("azzio-sleep", "azzio-lock", "azzio-restart", "azzio-shutdown",
                 "azzio-search"):
        assert name in src, f"custom icon name {name!r} missing from menu.c"


def test_menu_glyph_icon_names_match_between_c_and_python():
    # The C AZ_ICON_* defines (menu.c) and the Python MENU_GLYPH_ICONS list must name the
    # SAME icons -- the Python side ships them under those hicolor names, the C side asks the
    # resolver for them, so a drift on either side would ship an icon the menu never requests
    # (or request one never shipped -> the grey placeholder). Pin the two lists equal.
    src = _menu_c()
    # The C names, read from the #define table (value in quotes).
    c_names = dict(re.findall(r'#define\s+(AZ_ICON_\w+)\s+"([^"]+)"', src))
    assert c_names == {
        "AZ_ICON_SLEEP":    "azzio-sleep",
        "AZ_ICON_LOCK":     "azzio-lock",
        "AZ_ICON_RESTART":  "azzio-restart",
        "AZ_ICON_SHUTDOWN": "azzio-shutdown",
        "AZ_ICON_SEARCH":   "azzio-search",
    }, c_names
    # Every Python-shipped glyph name is referenced by a C define, and vice versa.
    py_names = {icon["name"] for icon in am.MENU_GLYPH_ICONS}
    assert set(c_names.values()) == py_names
    # The power table uses the SLEEP/LOCK/RESTART/SHUTDOWN defines (order pinned elsewhere);
    # the search box uses the SEARCH define.
    table = re.search(r"PowerItem\s+items\[4\]\s*=\s*\{(.*?)\};", src, re.S).group(1)
    assert re.findall(r"AZ_ICON_\w+", table) == [
        "AZ_ICON_SLEEP", "AZ_ICON_LOCK", "AZ_ICON_RESTART", "AZ_ICON_SHUTDOWN"]
    assert "az_icons_load(m->small_icons, AZ_ICON_SEARCH)" in src


def test_glyph_icon_assets_exist_and_are_svg():
    # Each MENU_GLYPH_ICONS asset is a real SVG in the repo, in the Azzio brand gradient
    # (#06B8FD, the logo cyan mid-stop) so the glyphs share the house identity. A missing or
    # off-brand asset would ship a wrong/placeholder icon.
    assets_dir = Path(am.paths.ASSETSDIR)
    for icon in am.MENU_GLYPH_ICONS:
        p = assets_dir / icon["asset"]
        assert p.is_file(), f"missing icon asset {icon['asset']}"
        text = p.read_text(encoding="utf-8")
        assert "<svg" in text and "</svg>" in text, f"{icon['asset']} is not SVG"
        assert "#06B8FD" in text, f"{icon['asset']} missing the Azzio brand cyan"


def test_glyph_icons_plan_ships_scalable_master_and_pngs_root_owned():
    # icons_plan() must ship, for each glyph icon: the scalable SVG master (asset-copied to
    # the hicolor scalable apps dir under the azzio-* name) PLUS a PNG rasterization at each
    # standard size -- all root-owned (a NEW hicolor name, nothing package-owned). This is the
    # same shape packages/xviewer uses, which compiler._emit_desktop emits declaratively.
    plan = am.icons_plan()
    by_dest = {e["dest"]: e for e in plan}
    for icon in am.MENU_GLYPH_ICONS:
        svg_dest = f"{am.MENU_ICON_SCALABLE_DIR}/{icon['name']}.svg"
        assert svg_dest in by_dest, f"scalable master not shipped for {icon['name']}"
        svg_entry = by_dest[svg_dest]
        assert svg_entry["asset"] == icon["asset"]
        assert svg_entry["owner"] == "root" and svg_entry["mode"] == 0o644
        for size in am.MENU_ICON_PNG_SIZES:
            png_dest = f"{am.MENU_ICON_PNG_DIR.format(size=size)}/{icon['name']}.png"
            assert png_dest in by_dest, f"missing {size}px PNG for {icon['name']}"
            r = by_dest[png_dest]
            assert r["render"] == {"asset": icon["asset"], "size": size}
            assert r["owner"] == "root"
    # Every glyph icon appears in the full emit_plan() too (icons_plan() is folded in).
    all_dests = {e["dest"] for e in am.emit_plan()}
    for icon in am.MENU_GLYPH_ICONS:
        assert f"{am.MENU_ICON_SCALABLE_DIR}/{icon['name']}.svg" in all_dests


def test_desktop_entry_launches_the_installed_launcher():
    # The generated .desktop must Exec the installed launcher and carry a menu icon,
    # so launching it by name (or the Super key) runs our menu.
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_file(io.StringIO(am.menu_desktop()))
    entry = cp["Desktop Entry"]
    assert entry["Exec"] == am.MENU_LAUNCHER_SYSTEM_PATH
    assert entry["Type"] == "Application"


def test_constants_match_desktop_module():
    # openbox.py binds the Super key to the menu LAUNCHER and starts the DAEMON BINARY
    # from the OpenBox autostart; a drift in these paths would wire the session to a path
    # we never installed.
    assert desktop.MENU_LAUNCHER == am.MENU_LAUNCHER_SYSTEM_PATH
    assert desktop.MENU_DAEMON_BIN == am.MENU_DAEMON_BIN_SYSTEM_PATH
    # The launcher rc.xml keybind and the autostart daemon line reference exactly these.
    assert am.MENU_LAUNCHER_SYSTEM_PATH in desktop.openbox_rc_xml()
    assert am.MENU_DAEMON_BIN_SYSTEM_PATH in desktop.openbox_autostart()


# --- The launcher drives the compiled daemon, not a python module -----------

def test_launcher_runs_the_daemon_binary_not_python():
    # The menu now runs as a resident C DAEMON (built once, kept hidden) so the icon
    # opens it INSTANTLY. The launcher (pure Python bin entry point) starts the compiled
    # BINARY directly and signals it -- it does NOT exec a python module or interpreter.
    src = am.launcher_py()
    assert src.startswith("#!/usr/bin/env python3")            # the launcher is Python
    assert am.MENU_LIB_DIR in src                              # default install dir
    assert "azzio-application-menu-daemon" in src             # ...runs the binary under it
    # It starts the binary as its own argv[0] (no `sys.executable` / `python3` prefix).
    assert "[DAEMON_BIN]" in src
    assert "sys.executable" not in src
    # The daemon-signalling contract is unchanged (PID file + SIGUSR1/SIGUSR2).
    assert "SIGUSR1" in src and "SIGUSR2" in src


def test_launcher_is_a_single_instance_toggle():
    # A second click must CLOSE the menu, not open another: the launcher tracks a PID
    # file and SIGUSR1-toggles the live instance instead of stacking a new window.
    src = am.launcher_py()
    assert "PID_FILE" in src                        # tracks the running instance
    assert "azzio-application-menu.pid" in src
    assert "XDG_RUNTIME_DIR" in src                 # PID file under the runtime dir
    assert "os.kill(pid, 0)" in src                 # is the recorded instance alive?
    assert "SIGUSR1" in src                         # toggle (show/hide) on second click
    assert "SIGUSR2" in src                         # force-show right after auto-start


# --- The C menu keeps its pinned behaviour ----------------------------------

def _menu_c() -> str:
    return (CSRC_DIR / "menu.c").read_text(encoding="utf-8")


def test_menu_tab_lands_on_shut_down():
    # THE headline behaviour: pressing TAB moves focus into the power row and lands on
    # "Shut Down" (the rightmost of {Sleep, Lock, Restart, Shut Down}, index 3), so the
    # commonest session action is one TAB + Enter away. Pin both the button ORDER (so
    # index 3 really is Shut Down) and the TAB handler forcing that index on entry.
    src = _menu_c()
    # The power row order: Shut Down is the 4th (index 3) button. Pin it from the actual
    # PowerItem initializer table (not free-floating strings in comments): grab the
    # `PowerItem items[4] = { ... };` block and read its labels in order.
    table = re.search(r"PowerItem\s+items\[4\]\s*=\s*\{(.*?)\};", src, re.S)
    assert table, "could not find the PowerItem items[4] table in menu.c"
    order = re.findall(r'"([^"]+)"\s*,\s*az_\w+\s*\}', table.group(1))
    assert order == ["Sleep", "Lock", "Restart", "Shut Down"], order
    # A named constant pins the Shut Down index to 3.
    assert "AZ_POWER_SHUTDOWN_INDEX 3" in src
    # The TAB handler, when entering the power zone, forces the index to Shut Down.
    tab_block = src.split("GDK_KEY_Tab", 1)[1].split("return TRUE;", 1)[0]
    assert "power_index = AZ_POWER_SHUTDOWN_INDEX" in tab_block
    assert "set_focus_zone(m, FOCUS_POWER)" in tab_block


def test_menu_has_tab_focus_toggle_between_search_and_power():
    # TWO keyboard focus zones toggled with TAB: the default is the search box + app
    # list (arrows navigate apps), and TAB moves focus to the power row (arrows move
    # between the power buttons); TAB again returns to the default. Pin the wiring.
    src = _menu_c()
    assert "FOCUS_APPS" in src and "FOCUS_POWER" in src   # the two zones
    assert "set_focus_zone" in src                         # moves + repaints focus
    assert "GDK_KEY_Tab" in src                            # TAB is bound
    assert "az_applist_set_selection_enabled" in src       # app-list dims when TAB'd away


def test_menu_is_borderless_and_centered():
    # The menu must be chromeless (override-redirect, no titlebar) and CENTERED on the
    # screen (there is no panel to anchor to anymore).
    src = _menu_c()
    assert "gdk_window_set_override_redirect" in src        # no window chrome
    # Centered placement: (screen - size) / 2 on both axes.
    assert "win_x" in src and "win_y" in src


def test_menu_closes_on_outside_click_and_escape():
    # The menu dismisses when anything outside it is pressed (a global grab + a
    # hit-test), and Escape / a physical Super press also close it.
    src = _menu_c()
    assert "gdk_seat_grab" in src                           # global pointer/keyboard grab
    assert "on_button_press" in src                         # outside-click hit-test handler
    assert "GDK_KEY_Escape" in src                          # Escape dismisses


def test_menu_open_is_instant_warmup_maps_once_offscreen():
    # Opening the menu must be INSTANT even on the FIRST Super press: the expensive part
    # under X is the MAP itself. The daemon maps the window ONCE, OFF-screen, at login
    # (warmup), then HIDES by moving it off-screen and SHOWS by moving it back -- never
    # re-mapping.
    src = _menu_c()
    assert "warmup" in src                                  # one-time off-screen map at login
    assert "shown" in src                                   # tracks shown/hidden explicitly


def test_menu_is_a_single_instance_daemon():
    # The daemon is single-instance (PID file) and speaks the launcher's protocol:
    # SIGUSR1 = toggle, SIGUSR2 = show. Same contract the launcher relies on.
    src = _menu_c()
    assert "azzio-application-menu.pid" in src             # same PID file the launcher reads
    assert "SIGUSR1" in src and "SIGUSR2" in src            # toggle / show
    assert "claim_pidfile" in src                           # single-instance guard


# --- build_daemon(): compiles the C sources into the installed binary --------

def _have_toolchain() -> bool:
    if shutil.which("gcc") is None and shutil.which("cc") is None:
        return False
    if shutil.which("make") is None or shutil.which("pkg-config") is None:
        return False
    return subprocess.run(
        ["pkg-config", "--exists", "gtk+-3.0"],
    ).returncode == 0


def test_build_daemon_inputs_are_the_c_sources():
    # The build copies exactly the C sources/headers + the Makefile into its scratch dir
    # (so the repo tree is never dirtied). No Python is compiled; the Makefile drives it.
    names = {p.name for p in am._csrc_files()}
    assert "menu.c" in names
    assert "Makefile" in names
    assert "theme.h" in names
    assert not any(n.endswith(".py") for n in names)        # only C inputs
    # The binary name the build produces matches the installed daemon path's basename.
    assert am.MENU_DAEMON_BIN_NAME == os.path.basename(am.MENU_DAEMON_BIN_SYSTEM_PATH)


def test_build_daemon_does_not_pollute_the_repo_tree():
    # build_daemon() must build in a TEMP dir, not in the source tree -- no object files
    # or binary may be left behind next to the sources (they would otherwise get tracked
    # / shipped). Assert the tree is clean of build artifacts before AND after a build.
    def _artifacts():
        return sorted(
            p.name for p in CSRC_DIR.iterdir()
            if p.suffix == ".o" or p.name == am.MENU_DAEMON_BIN_NAME
        )

    assert _artifacts() == [], f"stale build artifacts in the source tree: {_artifacts()}"
    if not _have_toolchain():
        pytest.skip("no gcc/GTK3 toolchain on this host")
    out = CSRC_DIR / "_test_build_out" / am.MENU_DAEMON_BIN_NAME
    try:
        am.build_daemon(out)
        assert out.is_file() and os.access(out, os.X_OK)    # produced an executable
        assert _artifacts() == [], f"build polluted the source tree: {_artifacts()}"
    finally:
        shutil.rmtree(CSRC_DIR / "_test_build_out", ignore_errors=True)


def test_gtk3_build_deps_are_provisioned_on_the_build_host():
    # REGRESSION GUARD: the menu is compiled DURING the ISO build (build_daemon -> make)
    # BEFORE the makepkg makedepends step, so its GTK3 dev deps must be provisioned by the
    # build-host toolchain, not deferred. This test would fail (not just skip on a bare
    # host) if the Dockerfile or the host-dep check ever dropped them again -- exactly the
    # gap that let a green dev-host suite hide a broken Docker build.
    #
    # 1. The single source of truth names the GTK3 dev stack.
    assert "gtk3" in am.MENU_BUILD_DEPS, am.MENU_BUILD_DEPS

    repo = Path(am.paths.APPLICATION_MENU_DIR).parents[2]   # .../libraries/packages/x -> repo root

    # 2. The Docker build image (where the real ISO is built) bakes them in -- the menu
    #    compile runs before makepkg._install_host_build_deps, so they can't be deferred.
    dockerfile = (repo / "Dockerfile").read_text(encoding="utf-8")
    for dep in am.MENU_BUILD_DEPS:
        if dep == "gcc":
            continue                                        # gcc rides in via base-devel
        assert re.search(rf"^\s*{re.escape(dep)}\s*\\?\s*$", dockerfile, re.M), (
            f"Dockerfile must install '{dep}' (needed to compile the menu daemon)"
        )

    # 3. compiler._check_host_deps installs the SAME set on a non-Docker Arch host (and so
    #    its 'already present' early-return can't skip a host missing only the GTK3 stack).
    compiler_src = (repo / "libraries" / "compiler.py").read_text(encoding="utf-8")
    assert "application_menu.MENU_BUILD_DEPS" in compiler_src, (
        "compiler._check_host_deps must fold in application_menu.MENU_BUILD_DEPS"
    )


# --- Usage seed (contract: LibreWolf, kitty, File Manager) -------------------

def test_usage_seed_orders_the_default_top_three():
    # A fresh profile has no launch history, so the menu would sort alphabetically. The
    # seed store fixes the STARTING top THREE to LibreWolf, kitty, File Manager (descending),
    # keyed by .desktop id -- EXACTLY three per the user's request. (The file manager replaced
    # Dolphin as the file manager.)
    seed = json.loads(am.usage_seed_json())
    ranked = sorted(seed.items(), key=lambda kv: -kv[1])
    assert [k for k, _ in ranked] == [
        "librewolf.desktop",
        "kitty.desktop",
        "thunar.desktop",
    ], ranked
    assert len(seed) == 3, seed
    assert "gimp.desktop" not in seed
    assert "systemsettings.desktop" not in seed
    # The old Dolphin seed must be gone (Dolphin was dropped).
    assert "org.kde.dolphin.desktop" not in seed


def test_usage_seed_matches_store_format_and_is_home_owned():
    # The seed must be byte-for-byte what the usage store writes (compact json with
    # separators (",", ":")), so the store reads it straight back.
    seed_txt = am.usage_seed_json()
    assert seed_txt == json.dumps(am.MENU_USAGE_SEED, separators=(",", ":"))
    assert " " not in seed_txt  # compact form -> no spaces after ',' or ':'

    # It is emitted as a per-user (home-owned) data file so a fresh profile inherits it
    # (and compiler.py mirrors it into /etc/skel for Calamares-installed users).
    plan = {e["dest"]: e for e in desktop.emit_plan()}
    assert am.MENU_USAGE_SEED_SYSTEM_PATH in plan, am.MENU_USAGE_SEED_SYSTEM_PATH
    entry = plan[am.MENU_USAGE_SEED_SYSTEM_PATH]
    assert entry["owner"] == "home"          # chowned 1000:998 + mirrored to skel
    assert entry["mode"] == 0o644
    assert entry["dest"].startswith(desktop.HOME + "/")  # under /home/main
    assert entry["builder"]() == seed_txt
