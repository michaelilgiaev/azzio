"""window_switcher - build wiring for the Azzio alt-tab switcher daemon.

Mirrors application_menu.py: constants for the installed paths, build_daemon() (compiles
the C/GTK3 daemon in a throwaway dir so the repo tree is never dirtied), launcher_py()
(the pure-Python bin entry point, read verbatim from the source tree), and emit_plan()
(the launcher install entry; the daemon BINARY is installed by build_daemon(), not the
plan). The daemon REUSES four application-menu translation units (window_resolve/applications/
icons/theme) via the Makefile's -I../application_menu, so build_daemon() stages BOTH
package dirs into the scratch tree, preserving that relative layout.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import paths


# --- Installed system paths (root-owned) ------------------------------------
SWITCHER_LIB_DIR = "/usr/local/lib/azzio-window-switcher"
SWITCHER_DAEMON_BIN_NAME = "azzio-window-switcher-daemon"
# The resident daemon BINARY the launcher signals (overlay built once, kept hidden, so
# Alt+Tab is instant). Compiled from the C sources here.
SWITCHER_DAEMON_BIN_SYSTEM_PATH = f"{SWITCHER_LIB_DIR}/{SWITCHER_DAEMON_BIN_NAME}"
# The launcher (launcher.py) installed as the bin entry point OpenBox's A-Tab / A-S-Tab
# run; it finds the daemon binary at its default SWITCHER_DIR (= SWITCHER_LIB_DIR).
SWITCHER_LAUNCHER_SYSTEM_PATH = "/usr/local/bin/azzio-window-switcher"

# Build-host requirements (compile time only; the live system does not compile anything).
#   gtk3/pkgconf/gcc  -- the GTK3 dev stack + pkg-config + the compiler (also base-devel).
#   libxcomposite     -- XCompositeNameWindowPixmap (the live-thumbnail pixmap).
#   libxdamage        -- pulled by the composite pipeline / linked by the Makefile.
#   libxrender        -- the render extension cairo-xlib draws the pixmap through.
SWITCHER_BUILD_DEPS = ["gtk3", "pkgconf", "gcc", "libxcomposite", "libxdamage", "libxrender"]

# Runtime requirement: picom (the compositor that redirects every window to an off-screen
# pixmap so covered/minimized windows still have LIVE content to thumbnail).
SWITCHER_RUNTIME_DEPS = ["picom"]


# --- Source files (in the repo) ---------------------------------------------
_SRC_LAUNCHER = Path("launcher.py")

# The four application-menu translation units the daemon reuses (compiled from
# ../application_menu by the Makefile). Their headers ride along via the whole-dir stage.
_REUSED_APPMENU_SOURCES = ("window_resolve.c", "applications.c", "icons.c", "theme.c")


def _read_source(rel: Path) -> str:
    """Read a source file from the window-switcher tree as text."""
    return (paths.WINDOW_SWITCHER_DIR / rel).read_text(encoding="utf-8")


def launcher_py() -> str:
    """The launcher OpenBox's A-Tab / A-S-Tab run (verbatim from the source tree). Pure
    Python (launcher.py); it signals the resident daemon (--next -> SIGUSR1, --prev ->
    SIGUSR2) and finds the daemon binary at its default SWITCHER_DIR. Installed to
    SWITCHER_LAUNCHER_SYSTEM_PATH with the exec bit (see PLAN)."""
    return _read_source(_SRC_LAUNCHER)


# --- Build the daemon binary ------------------------------------------------
def _switcher_build_inputs() -> list[Path]:
    """Every C source/header/Makefile in the window-switcher package dir."""
    d = paths.WINDOW_SWITCHER_DIR
    names = sorted(
        p.name
        for p in d.iterdir()
        if p.is_file() and (p.suffix in (".c", ".h") or p.name == "Makefile")
    )
    return [d / n for n in names]


def _appmenu_build_inputs() -> list[Path]:
    """The application-menu headers (all of them, so includes resolve) plus the reused
    .c files. Staged into a sibling application_menu/ dir in the scratch tree so the
    Makefile's ../application_menu path resolves exactly as in the repo."""
    d = paths.APPLICATION_MENU_DIR
    headers = [p for p in d.iterdir() if p.is_file() and p.suffix == ".h"]
    sources = [d / n for n in _REUSED_APPMENU_SOURCES]
    return headers + sources


def build_daemon(dest: Path, *, make: str = "make") -> Path:
    """Compile the C/GTK3 switcher daemon and install the binary at `dest`.

    Stages the window-switcher sources at the scratch root and the reused application-menu
    sources under a sibling application_menu/ dir (so the Makefile's -I../application_menu
    resolves), writes the real GLOBAL_SCALE az_scale.h (theme.c includes it), then runs
    `make`. Builds in a throwaway dir so no .o/binary lands in version control. Copies the
    produced binary to `dest` at 0755. Raises CalledProcessError on a failed build -- a
    broken switcher MUST fail the ISO build loudly rather than ship a missing binary.

    Returns the destination path.
    """
    dest = Path(dest)
    with tempfile.TemporaryDirectory(prefix="azzio-switcher-build-") as tmp:
        # Mirror the REPO layout: window_switcher/ and application_menu/ as SIBLINGS, so
        # the Makefile's APP_DIR = ../application_menu resolves exactly as in the tree
        # (make runs from the window_switcher/ subdir, not the scratch root).
        root = Path(tmp)
        switcher_dir = root / "window_switcher"
        appmenu_dir = root / "application_menu"
        switcher_dir.mkdir()
        appmenu_dir.mkdir()
        for src in _switcher_build_inputs():
            shutil.copy2(src, switcher_dir / src.name)
        for src in _appmenu_build_inputs():
            shutil.copy2(src, appmenu_dir / src.name)
        # OVERWRITE the checked-in scale-1.0 az_scale.h with the real GLOBAL_SCALE ratio,
        # matching application_menu.build_daemon (theme.h AZ_SCALED geometry derives from
        # the single scale source). The switcher itself uses fixed tile sizes, but theme.c
        # -- a reused TU -- includes az_scale.h, so it must be present + real.
        from packages.openbox import scale as _scale
        (appmenu_dir / "az_scale.h").write_text(
            _scale.menu_scale_header(), encoding="utf-8"
        )
        subprocess.run([make, SWITCHER_DAEMON_BIN_NAME], cwd=switcher_dir, check=True)
        built = switcher_dir / SWITCHER_DAEMON_BIN_NAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, dest)
        dest.chmod(0o755)
    return dest


# --- Emit plan --------------------------------------------------------------
# The launcher INSTALLED AS THE BIN (0755, run by A-Tab / A-S-Tab). The daemon binary is
# NOT in this plan -- build_daemon() compiles + installs it (it is produced by `make`, not
# a content string). No .desktop: the switcher is bound by OpenBox rc.xml, never launched
# by name, so it needs no application entry.
_EXEC = 0o755

PLAN = [
    {"builder": launcher_py, "dest": SWITCHER_LAUNCHER_SYSTEM_PATH, "mode": _EXEC},
]


def emit_plan() -> list[dict]:
    """Return the PLAN list (builder/dest/mode) for compiler.py to emit into the airootfs.
    The compiled daemon binary is installed separately by build_daemon()."""
    return PLAN
