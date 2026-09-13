"""The Azzio application menu -- baked into the live/installed system.

This is OUR application menu, and it is the WHOLE shell: the desktop is OpenBox with no
panel, so this menu -- a borderless, cyan-on-dark launcher CENTERED on the screen
(search, launch-frequency ordering, power actions) -- is the only launcher surface. It
is opened by the Super key (via xcape + the OpenBox rc.xml keybind); see
packages/openbox.

The menu is a C / GTK3 program (the earlier Tkinter/Python port was replaced): the
sources live DIRECTLY in this dir (menu.c + siblings, a Makefile), and the build here
COMPILES them into a single resident binary, azzio-application-menu-daemon, installed
under MENU_LIB_DIR. That daemon keeps the window built + hidden so opening it is
INSTANT, and speaks the same PID-file + SIGUSR1(toggle)/SIGUSR2(show) protocol the old
Python daemon did -- so launcher.py (the pure-Python bin entry point) drives it
unchanged, just pointed at the binary instead of a python module.

The package is C for the menu itself plus a thin Python launcher and this build wiring.
There are no .desktop payload files checked in: the .desktop entry is generated here as
a Python string (menu_desktop()).

Layers:
  * SOURCE tree -- libraries/packages/application_menu/ (paths.APPLICATION_MENU_DIR).
    The C sources live DIRECTLY in this dir, next to this build-wiring module:
      menu.c                     the GTK3 menu + resident daemon (main())
      {application_list,applications,usage,icons,actions,window_watch,scrollbar,power}.{c,h}  its modules
      theme.h                    the shared colours/sizes header
      Makefile                   builds azzio-application-menu-daemon (+ `make test`)
      launcher.py                the launcher (signals the daemon), pure Python
  * BUILD wiring -- THIS module (application_menu.py, alongside the source) COMPILES the
    C sources into the daemon binary and copies it to MENU_LIB_DIR, installs launcher.py
    to /usr/local/bin, and writes a generated .desktop entry. The OpenBox session
    (packages/openbox) starts the daemon from its autostart and binds the Super key to
    the launcher; there is no panel applet to bake.

Build host requirements: gcc + the GTK3 dev stack (pkg-config gtk+-3.0 ...). These are
host build-time deps only; the shipped ISO carries the compiled binary plus the GTK3
RUNTIME libraries (already in the manifest), so the live system does NOT compile
anything.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import paths


# --- Installed system paths (root-owned) ------------------------------------
# Where the menu lands in the live/installed rootfs.
MENU_LIB_DIR = "/usr/local/lib/azzio-application-menu"
# The resident daemon BINARY the launcher signals (menu built once, kept hidden, so the
# menu opens INSTANTLY). Compiled from the C sources here; the launcher starts this.
MENU_DAEMON_BIN_SYSTEM_PATH = f"{MENU_LIB_DIR}/azzio-application-menu-daemon"
# The launcher (launcher.py) is installed here as the bin entry point the Super key /
# .desktop run; it finds the daemon binary at its default MENU_DIR (= MENU_LIB_DIR).
MENU_LAUNCHER_SYSTEM_PATH = "/usr/local/bin/azzio-application-menu"
MENU_DESKTOP_SYSTEM_PATH = (
    "/usr/local/share/applications/azzio-application-menu.desktop"
)

# --- Super/Meta key -> menu (handled by OpenBox) -----------------------------
# Pressing the Super key alone OPENS THIS MENU, wired entirely through OpenBox: xcape
# turns a lone Super_L tap into the chord Super_L+Menu and the OpenBox rc.xml binds
# W-Menu to MENU_LAUNCHER_SYSTEM_PATH (see packages/openbox openbox_rc_xml +
# openbox_autostart). No global-shortcut daemon or extra .desktop is involved.
#
# The daemon that keeps the menu resident (instant open) is likewise started from the
# OpenBox autostart (packages/openbox.openbox_autostart).

# Per-user seed for the launch-frequency store (usage.c's usage store file). The menu
# orders apps most-launched first; on a FRESH profile there is no history, so without
# this everything would sort alphabetically. Seeding a few counts fixes the STARTING top
# of the list to EXACTLY the first three apps a new user wants -- LibreWolf, kitty,
# Azzio File Manager (descending) -- while leaving it fully dynamic: as the user opens apps the
# window watcher bumps these counts and the order re-sorts, so the seed only decides the
# initial arrangement. Keyed by .desktop id (usage.c's key); counts are spaced so the
# intended order is unambiguous. Emitted by packages/openbox as a home-owned
# file (mirrored into /etc/skel).
MENU_USAGE_SEED_SYSTEM_PATH = (
    "/home/main/.local/share/azzio-application-menu/usage.json"
)

# desktop_id -> starting launch count. Descending so the menu's sort (-count, name) puts
# them in exactly this order at the top of a fresh menu; the tail (everything else,
# count 0) stays alphabetical. EXACTLY three are seeded per the user's request ("make it
# those three when it's first installed"): LibreWolf, kitty, Azzio File Manager. Everything else
# (GIMP included) starts in the count-0 alphabetical tail and floats up only as used.
MENU_USAGE_SEED: dict[str, int] = {
    "librewolf.desktop": 3,          # LibreWolf (browser)
    "kitty.desktop": 2,              # kitty (terminal)
    "thunar.desktop": 1,             # the file manager
}

# The menu launcher's icon glyph: the standard "application-menu" hamburger, so the
# .desktop entry and any launcher show a recognizable menu icon. Single source of truth.
MENU_ICON_NAME = "application-menu"

# --- The menu's OWN glyph icons (power row + search) -------------------------
# The power row (Sleep/Lock/Restart/Shut Down) and the search box used to resolve the
# STOCK freedesktop names (system-suspend / system-lock-screen / system-reboot /
# system-shutdown / edit-find), which land on the installed Adwaita theme's glyphs -- and
# those read as the generic KDE/breeze-ish icons the user asked to replace. Azzio now ships
# its OWN glyphs (assets/icons/{sleep,lock,restart,shutdown,search}.svg): the brand blue
# gradient on a transparent background, drawn to sit as small inline glyphs on the menu's
# dark surface (NOT app-tile badges). They are shipped under Azzio-SPECIFIC names
# (azzio-sleep, ...) that no stock theme defines, so the menu's icon resolver (icons.c,
# which walks the theme chain and returns the FIRST match) picks OURS -- exactly the
# new-name reasoning packages/xviewer + packages/file_manager use so a theme/package update cannot
# revert them. menu.c references these AZ_ICON_* names; a test pins the two lists equal.
#
# Each: the source-of-truth SVG asset -> our icon name. Shipped to the hicolor SCALABLE apps
# dir (the vector master) + rasterized to the standard PNG sizes there, all root-owned (a
# NEW name, so nothing is package-owned; goes straight in the airootfs overlay).
MENU_GLYPH_ICONS = [
    {"asset": "icons/sleep.svg",    "name": "azzio-sleep"},
    {"asset": "icons/lock.svg",     "name": "azzio-lock"},
    {"asset": "icons/restart.svg",  "name": "azzio-restart"},
    {"asset": "icons/shutdown.svg", "name": "azzio-shutdown"},
    {"asset": "icons/search.svg",   "name": "azzio-search"},
]
# The hicolor apps-dir templates (same layout kitty/xviewer/file_manager use) and the PNG sizes
# rasterized alongside the scalable master, so the resolver finds a size-appropriate raster
# whatever it asks for (it prefers >= target, else nearest -- see icons.c).
MENU_ICON_SCALABLE_DIR = "/usr/share/icons/hicolor/scalable/apps"
MENU_ICON_PNG_DIR = "/usr/share/icons/hicolor/{size}x{size}/apps"
MENU_ICON_PNG_SIZES = (16, 22, 24, 32, 48, 64, 128, 256)


# --- Source files (in the repo) ---------------------------------------------
# The menu is a C / GTK3 program: menu.c holds main() (the resident daemon) and pulls in
# the sibling translation units (application_list/applications/usage/icons/actions/window_watch/scrollbar/power)
# via the Makefile, which produces the single binary named below. launcher.py (the bin
# entry point) is pure Python and rides along. The Makefile is the single source of truth
# for HOW the binary is linked; this module just drives it and installs the result.
MENU_DAEMON_BIN_NAME = "azzio-application-menu-daemon"

# The launcher module in the source tree, installed (also) as the bin entry point.
# The menu source lives DIRECTLY in APPLICATION_MENU_DIR (no nested csrc/ dir anymore).
_SRC_LAUNCHER = Path("launcher.py")

# Host BUILD dependencies for compiling the daemon (Arch package names). These must be
# present on the build HOST before build_daemon() runs -- they are NOT shipped in the
# ISO (the live system carries the GTK3 RUNTIME libs, already in the manifest, and the
# pre-compiled binary). Single source of truth: the Dockerfile bakes these into the
# build image and compiler._check_host_deps installs them on a non-Docker Arch host, so
# `make` finds gtk/gtk.h + the pkg-config .pc files at compile time.
#   gtk3     -> the GTK3 dev headers + gtk+-3.0.pc and the whole -3.0 pkg stack the
#               Makefile's `pkg-config --cflags/--libs` line resolves (gdk/glib/pango/
#               cairo/gdk-pixbuf .pc files come with it as deps).
#   pkgconf  -> provides pkg-config itself (the Makefile shells out to it). Part of
#               base-devel, listed here for clarity + so a slimmer host still gets it.
#   gcc      -> the C compiler (also in base-devel; explicit for the same reason).
MENU_BUILD_DEPS = ["gtk3", "pkgconf", "gcc"]


def _read_source(rel: Path) -> str:
    """Read a source file from the application-menu tree as text."""
    return (paths.APPLICATION_MENU_DIR / rel).read_text(encoding="utf-8")


def launcher_py() -> str:
    """The launcher the Super key / .desktop runs (verbatim from the source tree). Pure
    Python (launcher.py); it signals the resident daemon (SIGUSR1 toggle / SIGUSR2 show)
    and finds the daemon binary at its default MENU_DIR. Installed to
    MENU_LAUNCHER_SYSTEM_PATH with the exec bit (see PLAN)."""
    return _read_source(_SRC_LAUNCHER)


def menu_desktop() -> str:
    """A standard application-launcher .desktop for the menu, landing in
    /usr/local/share/applications so the menu can be launched by name and appears in app
    scans. GENERATED here (the package ships no .desktop files) -- Exec points at the
    launcher and Icon at MENU_ICON_NAME. (The Super key is bound by OpenBox's rc.xml, not
    by this file -- see packages/openbox.)"""
    return f"""\
[Desktop Entry]
Type=Application
Name=Azzio Menu
GenericName=Application Menu
Comment=The Azzio application menu
Exec={MENU_LAUNCHER_SYSTEM_PATH}
Icon={MENU_ICON_NAME}
Terminal=false
Categories=System;Utility;
Keywords=menu;launcher;azzio;
"""


def usage_seed_json() -> str:
    """The seed launch-frequency store (usage.json) that fixes the STARTING top three of
    the menu to LibreWolf, kitty, Azzio File Manager on a fresh profile.

    Rendered in the SAME compact form the C usage store writes (json with separators
    (",", ":")) so the store reads it straight back (the emitter adds a trailing newline,
    which json parsing ignores). It stays fully dynamic afterwards: the daemon's window
    watcher bumps these counts on every real app-open and re-sorts."""
    import json

    return json.dumps(MENU_USAGE_SEED, separators=(",", ":"))


# --- Build the daemon binary ------------------------------------------------
# The menu is COMPILED, not copied. compiler.py calls build_daemon() during the desktop
# emit; it runs `make` against a private copy of the C sources (so the repo tree is never
# dirtied with .o/binary artifacts) and installs the resulting binary into the airootfs.
def _csrc_files() -> list[Path]:
    """Every C source/header/Makefile in the package dir (the build inputs).

    The sources live directly in APPLICATION_MENU_DIR now (csrc/ was flattened up); the
    build copies exactly these into a scratch dir so `make` has a clean tree and the repo
    is never polluted with object files or the binary."""
    d = paths.APPLICATION_MENU_DIR
    names = sorted(
        p.name
        for p in d.iterdir()
        if p.is_file() and (p.suffix in (".c", ".h") or p.name == "Makefile")
    )
    return [d / n for n in names]


def build_daemon(dest: Path, *, make: str = "make") -> Path:
    """Compile the C/GTK3 menu daemon and install the binary at `dest`.

    Builds in a throwaway temp dir populated with a copy of the C sources (NOT in the
    repo, so no .o/binary ever lands in version control), then copies the produced
    binary to `dest` with mode 0755. Raises CalledProcessError if the build fails --
    a broken menu MUST fail the ISO build loudly rather than ship a missing binary.

    Returns the destination path.
    """
    dest = Path(dest)
    with tempfile.TemporaryDirectory(prefix="azzio-appmenu-build-") as tmp:
        build_dir = Path(tmp)
        for src in _csrc_files():
            shutil.copy2(src, build_dir / src.name)
        # OVERWRITE the checked-in scale-1.0 az_scale.h with the real GLOBAL_SCALE ratio, so the
        # shipped menu's fixed-PIXEL geometry (theme.h AZ_SCALED(...)) derives from the single
        # scale source (packages/openbox/scale). The source-tree copy stays scale 1.0 for the C
        # tests. (The menu's point fonts scale via the DPI channel, not this header.)
        from packages.openbox import scale as _scale
        (build_dir / "az_scale.h").write_text(_scale.menu_scale_header(), encoding="utf-8")
        subprocess.run(
            [make, MENU_DAEMON_BIN_NAME],
            cwd=build_dir,
            check=True,
        )
        built = build_dir / MENU_DAEMON_BIN_NAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, dest)
        dest.chmod(0o755)
    return dest


# --- Emit plan --------------------------------------------------------------
# Declarative map (builder -> dest -> mode) so compiler.py can iterate, mirroring
# packages/openbox.PLAN. All are absolute SYSTEM paths (root-owned): the
# OFFLINE Calamares install rsyncs the live rootfs, so these carry onto the
# installed system with no separate installer step.
#
# NOTE: the daemon BINARY is NOT in this plan -- it is compiled + installed by
# build_daemon() (compiler.py calls it), because it is produced by `make`, not read as a
# content string. emit_plan() covers only the two generated TEXT artifacts.
_EXEC = 0o755
_CONF = 0o644


# The launcher INSTALLED AS THE BIN (0755, run by the Super key) and a generated
# app-launcher .desktop (0644). The daemon binary lands separately via build_daemon().
PLAN = [
    {"builder": launcher_py, "dest": MENU_LAUNCHER_SYSTEM_PATH, "mode": _EXEC},
    {"builder": menu_desktop, "dest": MENU_DESKTOP_SYSTEM_PATH, "mode": _CONF},
]


def icons_plan() -> list[dict]:
    """The emit entries for the menu's own glyph icons (power row + search): for each
    MENU_GLYPH_ICONS entry, the scalable SVG master (copied verbatim from the repo asset)
    plus a PNG rasterization at each MENU_ICON_PNG_SIZES size, all under the icon's
    Azzio-specific hicolor name and all root-owned. Same builder/dest/mode/owner + asset/
    render shape packages/xviewer uses, so compiler._emit_desktop emits them declaratively.
    Returns FRESH dicts so a caller cannot mutate module state."""
    plan: list[dict] = []
    for icon in MENU_GLYPH_ICONS:
        plan.append({   # the scalable SVG master (our asset, our icon name).
            "builder": None,
            "asset": icon["asset"],
            "dest": f"{MENU_ICON_SCALABLE_DIR}/{icon['name']}.svg",
            "mode": _CONF,
            "owner": "root",
        })
        for size in MENU_ICON_PNG_SIZES:
            png_dir = MENU_ICON_PNG_DIR.format(size=size)
            plan.append({   # PNG rasterization at a standard size.
                "builder": None,
                "render": {"asset": icon["asset"], "size": size},
                "dest": f"{png_dir}/{icon['name']}.png",
                "mode": _CONF,
                "owner": "root",
            })
    return plan


def emit_plan() -> list[dict]:
    """Return the PLAN list (builder/dest/mode) for compiler.py to emit into the
    airootfs, PLUS the glyph-icon entries (icons_plan()). Kept as a function to mirror
    packages/openbox.emit_plan(). The compiled daemon binary is installed separately by
    build_daemon(); compiler._emit_desktop handles the asset/render icon entries the same
    declarative way _emit_apps handles kitty/xviewer icons."""
    return PLAN + icons_plan()
