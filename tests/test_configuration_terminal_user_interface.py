"""The bare-`azzio` TERMINAL UI -- now a COMPILED C program (Theme / Wallpaper / Network).

Running `azzio` with NO arguments opens a full-screen text UI so a developer new to Linux
can tune the three things a fresh machine needs -- Theme, Wallpaper, Network -- with arrow
keys (or WASD / HJKL), a search box at the top and nav hints at the bottom, everything
centred and coloured in the Azzio logo cyan. It used to be Python/curses but felt laggy, so
it was rewritten in C. The C sources now live INSIDE the azzio package
(libraries/packages/azzio/, built by terminal_user_interface_build.py) -- there is only ONE program, `azzio`,
and the UI is C for speed; the Python `azzio` command line interface just EXECs that binary for the no-argument
case.

These tests pin, against BOTH the bundled Python launcher (the /usr/local/bin/azzio
artifact) AND the C source tree + its build wiring:

  * bare `azzio` dispatches to run_terminal_user_interface, which EXECs the compiled UI binary (not curses);
  * the launcher's binary path is the SAME one the build installs (no drift);
  * graceful degradation: with no terminal, run_terminal_user_interface prints a pointer and returns 0 instead
    of exec-ing anything (so `azzio </dev/null` / a pipe never throws);
  * the build wiring compiles exactly the C sources into the installed binary, without
    polluting the repo tree, and the binary is pinned executable in the ISO file_permissions;
  * the spec's specifics live in the C source: the accent is the logo cyan #06B8FD, the nav
    line advertises WASD / HJKL / arrows (packed + uppercased) with q-to-quit / ESC-to-back,
    Network is the FIRST option, the entry title is "Azzio Settings", the Wallpaper screen
    names the wallpaper DIRECTORY and previews the hovered image, and the Theme screen previews
    real LibreWolf + file-manager screenshots (shipped, unmodified) and discloses that kitty is
    exempt -- with the "Current:" state shown once at the top and no per-row status echo.

The interactive DRAWING itself is exercised by an interactive smoke run (and the C model is
unit-tested headless by tests/Makefile's test_terminal_user_interface_model). Here we keep to the launcher wiring
+ the source contract so the suite stays deterministic and tty-free.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import types

import pytest

from packages.azzio.bundle import bundle_source, MODULE_ORDER
from packages.azzio import terminal_user_interface_build
from packages import openbox as desktop
import paths
import profile as profiledef


# The C UI sources now live INSIDE the azzio package (one program, C for speed) -- there is
# no separate azzio_terminal_user_interface package anymore.
TERMINAL_USER_INTERFACE_SRC_DIR = paths.AZZIO_COMMAND_LINE_INTERFACE_DIR


def _command_line_interface():
    """Exec the bundled azzio command line interface in a fresh module namespace (as shipped)."""
    mod = types.ModuleType("azzio_cli_terminal_user_interface_test")
    exec(compile(bundle_source(), "azzio_command_line_interface", "exec"), mod.__dict__)
    return mod


def _src(name: str) -> str:
    text = (TERMINAL_USER_INTERFACE_SRC_DIR / name).read_text(encoding="utf-8")
    # model.c was split (it grew past the size budget): the UI infrastructure stays in model.c,
    # the static screen TREE (ROWS_* + SCREENS[]) moved to model_tree.c, the RUNTIME Default
    # Applications screens + their status probes moved to model_default_applications.c, and the
    # Display status probes moved to model_display.c. The tests treat "the model" as one thing, so
    # requesting model.c transparently returns ALL of them concatenated -- a content check for a
    # row/screen/probe finds it wherever it now lives.
    if name == "model.c":
        for extra in ("model_tree.c", "model_default_applications.c", "model_display.c"):
            text += "\n" + (TERMINAL_USER_INTERFACE_SRC_DIR / extra).read_text(encoding="utf-8")
    return text


# --- dispatch wiring: bare azzio -> run_terminal_user_interface -> exec the C binary ------------

def test_bare_azzio_dispatches_to_the_terminal_user_interface():
    """No-argument azzio must route to run_terminal_user_interface, and the top-level usage must mention the UI."""
    src = desktop.azzio_command_line_interface()
    assert 'cmd == ""' in src
    assert "return run_terminal_user_interface(argv)" in src
    assert "full-screen UI" in src  # advertised in usage()


def test_run_terminal_user_interface_execs_the_compiled_binary():
    """run_terminal_user_interface must EXEC the compiled C UI (the fast, instant path) -- not start curses."""
    src = bundle_source()
    assert "os.execv(TERMINAL_USER_INTERFACE_BIN" in src
    assert "import curses" not in src        # the Python curses UI is gone


def test_launcher_binary_path_matches_the_build():
    """The path run_terminal_user_interface execs must be the SAME path the build installs the binary to, so the
    two can never drift."""
    command_line_interface = _command_line_interface()
    assert command_line_interface.TERMINAL_USER_INTERFACE_BIN == terminal_user_interface_build.TERMINAL_USER_INTERFACE_BIN_SYSTEM_PATH
    assert command_line_interface.TERMINAL_USER_INTERFACE_BIN == "/usr/local/lib/azzio/azzio"


def test_bare_main_uses_run_terminal_user_interface(monkeypatch):
    """command_line_interface.main([]) must call run_terminal_user_interface (bare azzio == the UI)."""
    command_line_interface = _command_line_interface()
    called = {}
    monkeypatch.setattr(command_line_interface, "run_terminal_user_interface", lambda argv=None: (called.setdefault("hit", True), 0)[1])
    assert command_line_interface.main([]) == 0
    assert called.get("hit") is True


def test_terminal_user_interface_is_bundled_before_cli():
    """terminal_user_interface.py (the launcher) must be bundled before command_line_interface.py, whose main() dispatches to it."""
    assert "terminal_user_interface.py" in MODULE_ORDER
    assert MODULE_ORDER.index("terminal_user_interface.py") < MODULE_ORDER.index("command_line_interface.py")


# --- graceful degradation with no terminal ----------------------------------

def test_run_terminal_user_interface_without_tty_prints_pointer(monkeypatch, capsys):
    """No usable terminal -> run_terminal_user_interface must NOT exec anything; it prints a pointer and rc 0."""
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_tty_ok", lambda: False)
    # If it tried to exec, this would raise; assert it returns cleanly instead.
    monkeypatch.setattr(command_line_interface.os, "execv",
                        lambda *a, **k: pytest.fail("must not exec without a tty"))
    assert command_line_interface.run_terminal_user_interface([]) == 0
    out = capsys.readouterr().out
    assert "no interactive terminal" in out
    for sub in ("azzio theme", "azzio wallpaper", "azzio network"):
        assert sub in out


def test_run_terminal_user_interface_missing_binary_falls_back(monkeypatch, capsys):
    """A tty but a MISSING binary must fall back to the pointer, never crash."""
    command_line_interface = _command_line_interface()
    monkeypatch.setattr(command_line_interface, "_tty_ok", lambda: True)
    monkeypatch.setattr(command_line_interface.os.path, "exists", lambda p: False)
    assert command_line_interface.run_terminal_user_interface([]) == 0
    assert "no interactive terminal" in capsys.readouterr().out


# --- the build wiring: compile the C sources, no pollution, pinned exec ------

def test_build_terminal_user_interface_inputs_are_the_c_sources():
    """build_terminal_user_interface copies exactly the C sources/headers + the Makefile (no Python) into a
    scratch dir; the produced binary name matches the installed path's basename."""
    names = {p.name for p in terminal_user_interface_build._csrc_files()}
    assert "main.c" in names
    assert "render.c" in names
    assert "model.c" in names
    assert "model_tree.c" in names           # the static screen tree (split from model.c)
    assert "model_default_applications.c" in names    # the runtime Default Applications screens + probes
    assert "model_display.c" in names        # the Display status probes (split from model.c)
    assert "preview.c" in names
    assert "action.c" in names       # apply execution + in-UI sudo credential
    assert "Makefile" in names
    assert "terminal_user_interface.h" in names
    assert "action.h" in names
    assert not any(n.endswith(".py") for n in names)     # only C inputs
    assert terminal_user_interface_build.TERMINAL_USER_INTERFACE_BIN_NAME == os.path.basename(terminal_user_interface_build.TERMINAL_USER_INTERFACE_BIN_SYSTEM_PATH)


def test_theme_preview_assets_exist_and_ship_unmodified(tmp_path):
    """PROMPT: use the real screenshots in assets/previews/ (same names, don't modify them).
    All four contract images must exist as assets, and install_previews must copy them BYTE-FOR-
    BYTE into the runtime preview dir (no resizing at build time -- the C scales at draw time)."""
    # every contract asset exists in the repo
    for rel in terminal_user_interface_build.PREVIEW_ASSETS:
        assert (paths.ASSETSDIR / rel).is_file(), f"missing preview asset {rel}"
    # the four expected names (timedate/files x dark/white) are exactly the contract
    assert {os.path.basename(r) for r in terminal_user_interface_build.PREVIEW_ASSETS} == {
        "timedate_dark.png", "timedate_white.png", "files_dark.png", "files_white.png",
    }
    # install_previews copies them verbatim (identical bytes) into the dest dir
    written = terminal_user_interface_build.install_previews(tmp_path)
    assert len(written) == len(terminal_user_interface_build.PREVIEW_ASSETS)
    for rel in terminal_user_interface_build.PREVIEW_ASSETS:
        src = paths.ASSETSDIR / rel
        dst = tmp_path / os.path.basename(rel)
        assert dst.is_file()
        assert dst.read_bytes() == src.read_bytes(), f"{rel} was modified during install"


def test_preview_dir_contract_matches_between_build_and_c():
    """The C reads the screenshots from a hard-coded AZ_PREVIEW_DIR; it MUST equal the dir the
    build ships them to (terminal_user_interface_build.TERMINAL_USER_INTERFACE_PREVIEW_SYSTEM_DIR), or the previews are 'not installed'
    at runtime. Also pin the dir under the binary's lib dir so both travel together."""
    preview = _src("preview.c")
    assert f'"{terminal_user_interface_build.TERMINAL_USER_INTERFACE_PREVIEW_SYSTEM_DIR}"' in preview
    assert terminal_user_interface_build.TERMINAL_USER_INTERFACE_PREVIEW_SYSTEM_DIR.startswith(terminal_user_interface_build.TERMINAL_USER_INTERFACE_LIB_DIR)


def _have_c_toolchain() -> bool:
    return ((shutil.which("gcc") or shutil.which("cc")) is not None
            and shutil.which("make") is not None)


def test_build_terminal_user_interface_compiles_and_does_not_pollute_the_repo_tree():
    """build_terminal_user_interface() must build in a TEMP dir, not the source tree -- no .o/binary left behind
    next to the sources (they would otherwise get tracked / shipped)."""
    def _artifacts():
        return sorted(p.name for p in TERMINAL_USER_INTERFACE_SRC_DIR.iterdir()
                      if p.suffix == ".o" or p.name == terminal_user_interface_build.TERMINAL_USER_INTERFACE_BIN_NAME)

    assert _artifacts() == [], f"stale build artifacts in the source tree: {_artifacts()}"
    if not _have_c_toolchain():
        pytest.skip("no C toolchain on this host")
    out = TERMINAL_USER_INTERFACE_SRC_DIR / "_test_build_out" / terminal_user_interface_build.TERMINAL_USER_INTERFACE_BIN_NAME
    try:
        terminal_user_interface_build.build_terminal_user_interface(out)
        assert out.is_file() and os.access(out, os.X_OK)   # produced an executable
        assert _artifacts() == [], f"build polluted the source tree: {_artifacts()}"
    finally:
        shutil.rmtree(TERMINAL_USER_INTERFACE_SRC_DIR / "_test_build_out", ignore_errors=True)


def test_terminal_user_interface_binary_is_pinned_executable_in_the_iso():
    """archiso normalizes overlay modes, so the compiled binary must be pinned 0755 in the
    profile file_permissions or bare `azzio` would find it non-executable and fall back to
    the pointer instead of opening the UI."""
    perms = profiledef.FILE_PERMISSIONS
    assert terminal_user_interface_build.TERMINAL_USER_INTERFACE_BIN_SYSTEM_PATH in perms
    assert perms[terminal_user_interface_build.TERMINAL_USER_INTERFACE_BIN_SYSTEM_PATH].endswith(":755")


def test_terminal_user_interface_binary_is_pure_libc_no_ncurses_no_gtk():
    """The terminal UI BINARY (azzio) is pure libc + raw ANSI (previews shell out to kitty at
    runtime): its link recipe uses no ncurses and no GTK. (The media OSD, a SEPARATE binary, does
    link X -- see the next test -- but the terminal UI itself does not.)"""
    # The $(BIN) link line must carry no library discovery / no ncurses / no gtk linkage.
    recipe = "\n".join(ln for ln in _src("Makefile").splitlines()
                       if not ln.lstrip().startswith("#"))
    # the terminal UI is linked from its objects with just $(LDFLAGS) -- no -l libs on that rule
    assert "$(OBJS) -o $@ $(LDFLAGS)" in recipe
    assert "-lncurses" not in recipe
    assert "-lgtk" not in recipe
    assert "gtk+-3.0" not in recipe


def test_osd_build_deps_include_the_x_libraries():
    """The media OSD (azzio-osd) is an Xlib program, so the build deps grew beyond gcc to add
    the X client libraries it links (libx11 for Xlib, libxrandr for the primary-monitor
    geometry, libxft for anti-aliased text). gcc stays first."""
    deps = terminal_user_interface_build.TERMINAL_USER_INTERFACE_BUILD_DEPS
    assert deps[0] == "gcc"
    assert {"libx11", "libxrandr", "libxft"} <= set(deps)
    # the OSD link rule pulls the X libs in via pkg-config (with a plain -l fallback)
    recipe = "\n".join(ln for ln in _src("Makefile").splitlines()
                       if not ln.lstrip().startswith("#"))
    assert "$(OSD_BIN)" in recipe
    assert "x11" in recipe and "xrandr" in recipe and "xft" in recipe
    # the OSD source itself is a build input
    assert "on_screen_display.c" in {p.name for p in terminal_user_interface_build._csrc_files()}


def test_osd_x_build_deps_are_provisioned_on_the_build_host():
    """REGRESSION GUARD (mirrors the menu-daemon one): the OSD is compiled DURING the ISO build
    (build_osd -> make) BEFORE the makepkg makedepends step, so its X dev deps must be baked into
    the build-host toolchain. This fails (not just skips) if the Dockerfile or _check_host_deps
    ever drops them -- the gap that would let a green dev-host suite hide a broken Docker build
    (on_screen_display.c not compiling for want of X11/Xrandr/Xft headers)."""
    import re
    from pathlib import Path

    deps = terminal_user_interface_build.TERMINAL_USER_INTERFACE_BUILD_DEPS
    repo = Path(paths.AZZIO_COMMAND_LINE_INTERFACE_DIR).parents[2]   # .../libraries/packages/azzio -> repo root
    # The Docker build image bakes the X dev libs in (the OSD compile runs before makepkg).
    dockerfile = (repo / "Dockerfile").read_text(encoding="utf-8")
    for dep in ("libx11", "libxrandr", "libxft"):
        assert dep in deps, f"{dep} missing from the build deps"
        assert re.search(rf"^\s*{re.escape(dep)}\s*\\?\s*$", dockerfile, re.M), (
            f"Dockerfile must install '{dep}' (needed to compile the media OSD)"
        )
    # compiler._check_host_deps folds in the SAME set on a non-Docker Arch host.
    compiler_src = (repo / "libraries" / "compiler.py").read_text(encoding="utf-8")
    assert "terminal_user_interface_build.TERMINAL_USER_INTERFACE_BUILD_DEPS" in compiler_src


# --- the spec's specifics, pinned in the C source ---------------------------

def test_accent_is_the_azzio_logo_cyan():
    """PROMPT: use the Azzio logo colour (cyan-ish, #06B8FD) to differentiate elements and
    ease navigation. The accent RGB (6,184,253) must be the accent SGR in the palette."""
    header = _src("terminal_user_interface.h")
    assert "#06B8FD" in header
    assert "38;2;6;184;253" in header    # the accent as a truecolor foreground
    # the nav keys are coloured with the accent, too
    assert "AZ_SGR_KEYCAP" in header


def test_nav_advertises_wasd_hjkl_and_arrows_uppercased():
    """PROMPT: navigation is WASD / HJKL / arrows; uppercase the keys, keep the labels, and
    pack them tight ("WASD HJKL ←↑→↓ move"). Also advertise q-to-quit and ESC-to-back. The
    renderer's nav line must list them, and the input loop must accept all three families."""
    render = _src("render.c")
    # the movement key-caps, uppercased (drawn as tight clusters via capgroup)
    for k in ('"W"', '"A"', '"S"', '"D"', '"H"', '"J"', '"K"', '"L"'):
        assert k in render, f"missing nav keycap {k}"
    # the plain (test-facing) nav string names the PACKED clusters + the verbs incl. q/quit
    for token in ("WASD HJKL", "move", "ENTER", "Q quit", "ESC back", "search"):
        assert token in render, f"missing nav token {token!r}"
    # the input loop binds WASD + HJKL + arrows to movement/back/open, and q to quit
    main = _src("main.c")
    assert "case K_UP: case 'k': case 'w':" in main
    assert "case K_DOWN: case 'j': case 's':" in main
    assert "case K_LEFT: case 'h': case 'a':" in main
    assert "case K_RIGHT: case 'l': case 'd':" in main
    assert "case 'q':" in main


def test_esc_is_back_and_q_ctrlc_quit_instantly():
    """PROMPT (newest): ESC is 'go back', NOT quit; q is the one instant quit; Ctrl-C must ALSO
    quit cleanly (raw mode disables ISIG, so Ctrl-C arrives as the byte 0x03 and must be handled
    explicitly). The nav label for ESC therefore reads 'back' everywhere (no depth relabelling)."""
    main = _src("main.c")
    render = _src("render.c")
    # ESC handler goes back (never a quit branch of its own anymore).
    esc_block = main[main.index("case K_ESC:"):main.index("case 'q':")]
    assert "go_back(&ui)" in esc_block
    assert "return 0" not in esc_block, "ESC must NOT quit -- it only goes back"
    # q and Ctrl-C (0x03) quit instantly from the menu; EOF (stdin closed) quits from ANY mode,
    # handled up front so it can never spin (it is a top-level `if`, not a switch case now).
    assert "case 'q':" in main
    assert "case 3:" in main            # Ctrl-C as a raw byte
    assert "k == K_EOF" in main         # stdin closed -> quit, never spin (checked before dispatch)
    # the nav label for ESC is a fixed 'back' (not depth-dependent 'quit').
    assert 'verb(&t, "ESC", "back")' in render


def test_status_probes_are_cached_for_instant_navigation():
    """PROMPT: it must feel SNAP INSTANT. Every status probe forks a tool; called straight from
    the draw loop that is several forks per keystroke. All probe calls go through a short-TTL
    memo (az_status_cached) so navigation re-forks nothing, and an apply busts the cache so a
    toggle shows immediately."""
    model = _src("model.c")
    render = _src("render.c")
    main = _src("main.c")
    # the cache + its invalidation exist in the model
    assert "az_status_cached" in model
    assert "az_status_invalidate" in model
    # the renderer reads EVERY probe through the cache, never calling the fn pointer directly
    assert "az_status_cached(scr->current" in render
    assert "az_status_cached(vis[i]->status" in render
    assert "vis[i]->status(sbuf" not in render      # no direct (uncached) probe call
    assert "scr->current(sb" not in render
    # an apply invalidates the cache so the new state shows on the next frame
    assert "az_status_invalidate()" in main


def test_default_applications_screens_auto_refresh_live():
    """PROMPT (newest): the Default Applications list must update IN REAL TIME -- the user removed
    Firefox in another terminal and the list did not change until they exited and re-entered. The
    input loop blocks on read_key(), so these screens (whose rows resolve live from the installed
    .desktop files) opt into a timed wakeup: on an idle tick the loop drops the status cache and
    redraws, which re-runs the live scan -- so an install/removal shows on its own within ~1s. No
    other screen polls (zero idle work / no flicker elsewhere)."""
    main = _src("main.c")
    # a live-screen predicate keyed on the defaultapps screen id, and a bounded (poll) wait.
    assert "screen_is_live" in main
    assert 'strncmp(id, "defaultapps", 11)' in main
    assert "poll(" in main and "AZ_LIVE_REFRESH_MS" in main
    # only BROWSE mode on a live screen polls; everything else blocks as before.
    assert "ui->mode != AZ_MODE_BROWSE || !screen_is_live(ui)" in main
    # on an idle tick the loop busts the cache and redraws (so current-handler re-reads too).
    wait_block = main[main.index("wait_for_input_or_refresh(&ui)"):]
    assert "az_status_invalidate();" in wait_block[:200]
    assert "continue;" in wait_block[:200]


def test_network_status_is_plain_online_offline():
    """PROMPT: replace "wifi enabled, firewall active" with a plain Online/Offline headline, and
    make Bluetooth/Airplane read as a simple on/off (never "present"; default off)."""
    model = _src("model.c")
    assert "Online - Connected to Internet" in model
    assert "Offline - No Internet" in model
    # the top-level Network row reads connectivity, not a radio+firewall soup
    assert "networking" in model and "connectivity" in model
    # bluetooth never RETURNS the old confusing "present" value (the word may still appear in a
    # comment explaining why it was dropped -- match the code that produced it, not prose).
    assert 'snprintf(buf, n, "present")' not in model
    # airplane now reads NetworkManager's master switch (`nmcli networking`) so a wired machine
    # reports airplane correctly (the internet really being down), not just the radios.
    assert '"nmcli", "networking"' in model


def test_wifi_and_wired_status_are_mutually_exclusive_in_c():
    """PROMPT (newest): wifi and wired must not both read on/connected -- one-or-the-other. The
    C probes share a single device scan and the connected ethernet link wins (wifi -> off)."""
    model = _src("model.c")
    assert "az_net_scan" in model                 # single source of truth for both probes
    # wired wins: when ethernet is connected the wifi probe reports off
    assert "s.eth_conn" in model


def test_every_apply_runs_inside_the_ui_no_terminal_drop():
    """PROMPT (newest): selecting a setting must NOT black out the terminal, and Q must exit
    cleanly (no leftover command line interface text). So every apply runs captured INSIDE the alt screen -- there
    is no '?1049l' (leave alt screen) around an apply anymore, and applies go through action.c's
    capture, shown in the OUTPUT overlay."""
    main = _src("main.c")
    action = _src("action.c")
    # the old "drop to the real terminal for the apply" path is gone.
    assert "run_apply_visible" not in main
    # applies are captured (no child writes to the terminal) ...
    assert "az_action_run_capture" in main
    assert "az_action_run_capture" in action
    # ... and a privileged apply secures a sudo credential via an in-UI prompt, never a hidden
    # prompt on a blanked screen.
    assert "AZ_MODE_PASSWORD" in main
    assert "az_action_sudo_ok" in main


def test_firewall_ports_are_configurable_in_the_ui():
    """PROMPT (newest): the Firewall screen must LIST ports in the UI and let you configure them
    (open/close/delete) without dropping to a shell. model.c has the list row (show_output) and
    the AZ_ACT_PORT rows; main.c prompts for the port."""
    model = _src("model.c")
    main = _src("main.c")
    assert "firewall port list" in model
    assert "AZ_ACT_PORT" in model                 # open/close/delete by typing a port
    assert "AZ_MODE_PORT" in main                 # the in-UI port prompt


def test_settings_show_their_bash_command():
    """PROMPT (newest): each setting is accompanied by the bash command that invokes it, so the
    user learns to do it without the UI. The renderer draws the row's command; model.c exposes
    it via az_row_command."""
    model = _src("model.c")
    render = _src("render.c")
    assert "az_row_command" in model
    assert "az_row_command" in render


def test_esc_go_back_is_instant():
    """PROMPT (newest): ESC (go back) must be INSTANT. The ESC decode no longer arms a 100ms
    VTIME timer -- it peeks fully non-blocking (VMIN=0, VTIME=0), so a bare ESC returns at once."""
    main = _src("main.c")
    esc_decode = main[main.index("if (c == 27)"):main.index("return key;")]
    assert "VTIME] = 0" in esc_decode
    assert "VTIME] = 1" not in esc_decode          # no 100ms wait on a bare ESC anymore


def test_entry_title_and_first_option():
    """PROMPT: rename the entry screen to "Azzio Settings" and make Network the FIRST option.
    (The earlier "no branding" rule was about ASCII-art / a logo banner -- a plain window title
    is not that; the spec explicitly asks for this title.)"""
    model = _src("model.c")
    # the entry screen is titled "Azzio Settings"
    assert '"Azzio Settings"' in model
    # Network is the first main-menu row (before Theme / Wallpaper)
    net = model.index('.label="Network"')
    theme = model.index('.label="Theme"')
    wall = model.index('.label="Wallpaper"')
    assert net < theme < wall, "Network must be the first main-menu option"
    # No ASCII-art / logo banner: the renderer draws no multi-line art block (the branding
    # rule was about art, not the plain window title the spec now asks for).
    render = _src("render.c")
    for arty in ("____", "\\\\", "|__|", "____/"):
        assert arty not in render, f"looks like ASCII-art banner in the renderer: {arty!r}"


def test_main_screen_has_no_move_hint_subtitle():
    """PROMPT: remove the 'Move with the arrow keys, Enter to open.' subtitle from the main
    screen (the bottom nav hints already say how to move)."""
    model = _src("model.c")
    assert "Move with the arrow keys" not in model


def test_wallpaper_screen_names_dir_and_previews_the_image():
    """PROMPT: Wallpaper shows the directory path AND previews the hovered image (kitty)."""
    model = _src("model.c")
    preview = _src("preview.c")
    assert "/usr/share/wallpapers" in model               # the dir, shown as the subtitle
    assert "AZ_PV_WALLPAPER" in model                      # wallpaper rows request a preview
    # the preview shells out to kitty's icat with a placement
    assert "kitten" in preview and "icat" in preview
    assert "--place" in preview
    # the wallpaper image path matches wallpaper.py's layout
    assert "contents/images" in model and "1672x941" in model


def test_theme_screen_previews_real_screenshots_and_disclaims_kitty():
    """PROMPT: Theme previews are the REAL shipped screenshots (LibreWolf on the timedate home
    page + the file manager), placed with kitty, sized for the terminal and used UNMODIFIED. The screen
    discloses kitty is exempt and shows Current at the top; there is NO caption under them."""
    model = _src("model.c")
    preview = _src("preview.c")
    assert "AZ_PV_THEME" in model
    # the kitty disclaimer is the Theme screen subtitle
    assert "Kitty does not follow the system theme" in model
    # the previews are the shipped screenshot files (timedate = LibreWolf home page, files =
    # the file manager), placed via kitty's icat with a placement -- not ANSI mock-ups.
    assert "kitten" in preview and "icat" in preview and "--place" in preview
    assert "timedate_%s.png" in preview and "files_%s.png" in preview
    assert "dark" in preview and "white" in preview        # the two variants
    # the images are used unmodified (no image editing in the C -- just placement/scaling)
    for banned in ("convert", "resize", "mogrify"):
        assert banned not in preview, f"previews must not modify images ({banned})"
    # NO caption text under the theme previews anymore (the old mock-up caption is gone)
    assert "Preview: dark" not in preview and "Preview: white" not in preview
    # "Current: ..." is drawn at the top of the screen by the renderer
    assert "Current:" in _src("render.c")


def test_theme_and_wallpaper_rows_have_no_status_echo():
    """PROMPT: drop the per-row status ('white' after each theme option, 'years' after each
    wallpaper option) -- 'Current:' already shows it. The rows must carry no .status, and the
    screens must instead supply a screen-level .current probe."""
    model = _src("model.c")
    # the Theme/Wallpaper apply rows are defined with a preview but WITHOUT a .status field
    for row_target in ('.target="azzio theme --dark"', '.target="azzio wallpaper --years.png"'):
        assert row_target in model
    # the screen-level "Current:" probe is wired for both
    assert ".current=az_status_theme" in model
    assert ".current=az_status_wallpaper" in model
    # the renderer draws "Current:" from the SCREEN probe, not rows[0].status
    render = _src("render.c")
    assert "scr->current" in render


def test_actions_shell_back_to_the_azzio_subcommands():
    """Every apply row runs the SAME tested `azzio` subcommand (the C UI adds navigation,
    not new system behaviour)."""
    model = _src("model.c")
    for cmd in (
        "azzio theme --dark",
        "azzio theme --white",
        "azzio wallpaper --years.png",
        "azzio wallpaper --decades.png",
        "azzio network firewall enable",
        "azzio network firewall port list",
        "azzio network wifi on",
    ):
        assert cmd in model, f"missing action: {cmd}"


def test_backup_entry_in_the_model_and_cli_surface():
    """Step six: a "Backup" entry in the top menu drives the SAME opt-in `azzio backup
    --configure` flow, OFF BY DEFAULT, streamlined for a new user. Pin (a) the C model has a
    reachable "backup" screen off ROWS_MAIN with the az_status_backup Current: probe and the two
    non-interactive applies + the two AZ_ACT_PROMPT enable rows, and (b) the bundled CLI carries
    the non-interactive --enable-usb / --enable-gdrive surface the enable rows call."""
    model = _src("model.c")
    # the ROWS_MAIN row + the backup screen id + the explanatory subtitle
    assert '.label="Backup"' in model
    assert '.target="backup"' in model
    assert '.id="backup"' in model
    assert "Off by default" in model                        # the streamlined, opt-in framing
    # the status probe (declared in the header, defined in model.c)
    assert "az_status_backup" in model
    assert "az_status_backup" in _src("terminal_user_interface.h")
    # the four rows' azzio wrappers (status/disable non-interactive; enable-* prompt-driven)
    for cmd in (
        "azzio backup --configure --status",
        "azzio backup --configure --disable",
        "azzio backup --configure --enable-usb",
        "azzio backup --configure --enable-gdrive",
    ):
        assert cmd in model, f"missing backup row target: {cmd}"
    # the enable rows are AZ_ACT_PROMPT (free-text path/remote, not digits) with a prompt label
    assert "AZ_ACT_PROMPT" in model
    assert "USB mount path:" in model
    # the non-interactive enable surface the enable rows call is in the bundled guest CLI
    src = bundle_source()
    assert "--enable-usb" in src and "--enable-gdrive" in src
    assert "def _enable_usb_path(" in src and "def _enable_gdrive_remote(" in src


def test_backup_status_probe_reads_the_configure_status():
    """az_status_backup summarises the opt-in targets by asking the configurator's own
    non-interactive `azzio backup --configure --status` (so the TUI's Current: line and the CLI
    can't disagree), and reports the DEFAULT "off (local only)" when both are off / azzio is
    missing -- never a blank cell."""
    model = _src("model.c")
    assert '"azzio", "backup", "--configure", "--status"' in model
    assert "off (local only)" in model          # the default / fallback line
    assert "USB + Google Drive" in model        # the both-on summary


def test_free_text_prompt_mode_wired_for_enable_rows():
    """The enable rows need a FREE-TEXT prompt (a path / remote), distinct from the digits-only
    PORT prompt. Pin AZ_ACT_PROMPT + AZ_MODE_PROMPT and that main.c dispatches it to a prompt
    handler that runs the typed value appended to the target (honouring the row's needs_root, not
    always sudo)."""
    header = _src("terminal_user_interface.h")
    render_h = (TERMINAL_USER_INTERFACE_SRC_DIR / "render.h").read_text(encoding="utf-8")
    main = _src("main.c")
    assert "AZ_ACT_PROMPT" in header            # the new action kind
    assert "AZ_MODE_PROMPT" in render_h         # the new input mode
    assert "prompt_key" in main                 # the free-text prompt handler
    assert "if (ui.mode == AZ_MODE_PROMPT)" in main   # dispatched in the input loop
    # the handler honours the row's needs_root via g_pending_root (not a hard-coded sudo like PORT)
    assert "g_pending_root" in main


def test_everything_is_centred():
    """PROMPT: center navigation, center the search box, center everything. The renderer's
    layout helper centres each element."""
    render = _src("render.c")
    assert "center_col" in render
    assert "put_center" in render
    # the search box, rows block, and nav are all placed via the centring helper
    assert render.count("center_col") >= 4


def test_hovered_row_shows_base_and_wrapper_command_lines():
    """PROMPT: under each setting, show TWO lines -- "Base Command: $ <base>" over
    "Azzio Wrapper: $ azzio ..." -- with the labels AND the "$" prompt WHITE and the commands
    CYAN (the user: 'Base Command: $' and 'Azzio Wrapper: $' are to be white). The "$ " now
    lives in the WHITE label (not the cyan value), so the prompt renders white. The renderer
    draws both via az_row_base / az_row_command; model.c exposes az_row_base."""
    render = _src("render.c")
    model = _src("model.c")
    header = _src("terminal_user_interface.h")
    # the two exact labels the spec wants -- now INCLUDING the "$ " prompt, so it is white.
    assert '"Base Command: $ "' in render
    assert '"Azzio Wrapper: $ "' in render
    # both command accessors feed the lines
    assert "az_row_base" in render and "az_row_command" in render
    # the base accessor is a real, exported model function
    assert "az_row_base" in model and "az_row_base" in header
    # the label (incl. "$ ") is white (TEXT) and the command cyan (ACCENT): the two-tone helper
    # is used with AZ_SGR_TEXT for the label and AZ_SGR_ACCENT for the command.
    assert "put_center_labeled" in render
    # The CALL SITE renders the white "<label>: $ " with AZ_SGR_TEXT and the bare command with
    # AZ_SGR_ACCENT (base/cmd passed straight, no "$ %s" prefix): so the "$" prompt is white.
    assert 'put_center_labeled(&b, ui, rows - 4, AZ_SGR_TEXT, "Base Command: $ ",' in render
    assert '                               AZ_SGR_ACCENT, base);' in render
    assert 'put_center_labeled(&b, ui, rows - 3, AZ_SGR_TEXT, "Azzio Wrapper: $ ",' in render
    assert '                               AZ_SGR_ACCENT, cmd);' in render
    # every apply/port row carries a .base in the model (the underlying tool command)
    assert ".base=" in model


def test_c_and_x_copy_commands_via_xclip():
    """PROMPT: hovering a setting, `c` copies the azzio wrapper and `x` copies the base command,
    using xclip (the clipboard tool shipped on this X11 build). The keys are advertised on the
    nav line; main.c binds them; action.c copies through xclip; xclip is in the manifest."""
    render = _src("render.c")
    main = _src("main.c")
    action = _src("action.c")
    # advertised on the nav line (plain string + the drawn verbs)
    assert "C copy cmd" in render and "X copy base" in render
    # the input loop binds c (wrapper) and x (base) in BROWSE mode
    assert "case 'c':" in main and "case 'x':" in main
    assert "copy_hovered" in main
    # c copies the wrapper (want_base=0), x copies the base (want_base=1)
    assert "az_row_command" in main and "az_row_base" in main
    # the copy goes through xclip's CLIPBOARD selection
    assert "az_action_copy_clipboard" in action
    assert '"xclip"' in action and '"clipboard"' in action
    # xclip must be a shipped package so the feature works on the ISO
    pkgs = (TERMINAL_USER_INTERFACE_SRC_DIR.parent / "packages.x86_64").read_text(encoding="utf-8")
    assert any(ln.strip() == "xclip" for ln in pkgs.splitlines()), "xclip must be in packages.x86_64"


def test_subtitles_explain_wrapped_commands_and_wallpaper_dir_is_cyan():
    """PROMPT: the top labels must explain WHAT commands are wrapped; and the wallpaper subtitle
    becomes "Wallpapers directory: /usr/share/wallpapers/", coloured cyan and placed tight above
    "Current:". The renderer honours a per-screen subtitle_accent flag for that cyan/tight case."""
    model = _src("model.c")
    render = _src("render.c")
    header = _src("terminal_user_interface.h")
    # explanatory subtitles name the tools they wrap
    assert "Wraps gsettings" in model            # theme
    assert "nmcli" in model and "rfkill" in model and "ufw" in model  # network family
    assert "wpctl" in model                        # volume
    # the wallpaper subtitle is the directory path with a trailing slash, flagged accent
    assert "Wallpapers directory: " in model
    assert "/usr/share/wallpapers" in model
    assert ".subtitle_accent=1" in model
    assert "subtitle_accent" in header
    # the renderer draws the accented subtitle in the cyan and tight (no blank spacer)
    assert "subtitle_accent" in render
