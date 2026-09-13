"""Azzio File Manager -- the vendored, version-controlled source + config in one package.

Azzio File Manager is Azzio's OWN GTK file manager. This package OWNS it end to end:

  * SOURCE. The full source tree is vendored under packages/file_manager/source/ (a `git clone`
    pinned by SOURCE_COMMIT below, committed straight into the Azzio repo and relicensed GPL-3.0).
    It carries NO semver -- it is our own thing, not a versioned upstream drop-in. The ONE Azzio
    behaviour change (always show the fully-resolved, symlink-dereferenced path in the location
    bar/title, which the vendored code base has no config lever for) is applied DIRECTLY to the
    committed C source (source/thunar/thunar-window.c) rather than as a build-time patch, so the
    modification is itself version-controlled and auditable with `git diff`. There is no separate
    .patch artifact. pkgbuild.pkgbuild_file_manager() builds this vendored tree from source (via
    ./autogen.sh, since a git checkout ships no generated ./configure) and makepkg drops the
    package into the offline repo, exactly like calamares/librewolf. makepkg._emit_recipes copies
    SOURCE_DIR into the recipe dir at build time (see pkgbuild.recipe_source_trees), so the
    vendored tree itself is never touched by the build. The built package is `pkgname=file_manager`
    and provides+conflicts+replaces the stock `thunar` package, so it IS the file manager on the
    image and the Xfce plugins that depend on `thunar` bind to it.

  * CONFIG. The Azzio-taste configuration (the pieces are split into focused submodules and
    re-exported here, INCLUDING home_directory, the home-layout data -- the file manager's sidebar
    and the on-disk home layout are built from the same list). NOTE: the config targets the ON-DISK
    paths the built binary actually reads (it still keeps the upstream ~/.config/Thunar/* names,
    the `thunar` xfconf channel and the thunar.desktop launcher) -- those are the binary's runtime
    contract, not our brand, so they are left as-is:

      - settings.py -- the preferences (view, TEXT-ENTRY location bar, shortcuts side pane, no
        expandable-folder arrows, split view off, removable-volume management off, the ~20% zoom
        bump) rendered into BOTH thunarrc AND the Xfconf channel XML from one table, plus the
        file-manager-scoped gtk.css that carries the font half of the +20% bump in scale-relative
        em. Also the hidden-bookmarks/hidden-devices arrays that remove the Devices/Network/
        Computer/Recent side-pane clutter.
      - sidebar.py  -- ~/.config/gtk-3.0/bookmarks, the shortcuts pane, built from
        packages/file_manager/home_directory (the SAME set as the on-disk home layout).
      - actions.py  -- ~/.config/Thunar/uca.xml (Edit with gedit on any file, Edit with gimp on
        images, Create Link via zenity, Open Terminal Here via kitty) + the `link` helper script.
      - launcher.py -- the thunar.desktop override (Name="Azzio File Manager", custom Azzio icon) + icon files.
      - templates.py -- ~/.config/user-dirs.dirs, the XDG user-dirs pointer (maps the XDG dirs to
        the Azzio home layout; keeps XDG_TEMPLATES_DIR at the stock $HOME/ -- the ~/Templates dir
        was deleted per the user's request, so no "Create Document" template set ships).

This package is consumed by compiler._emit_apps exactly like the other app packages (emit_plan()
in the builder/dest/mode/owner shape, plus the asset/render extras kitty uses).

WHAT LANDS WHERE:
  HOME files (owner "home", chowned 1000:998 and mirrored into /etc/skel):
    ~/.config/Thunar/thunarrc, ~/.config/xfce4/xfconf/xfce-perchannel-xml/thunar.xml,
    ~/.config/gtk-3.0/gtk.css, ~/.config/gtk-3.0/bookmarks, ~/.config/Thunar/uca.xml
  SYSTEM files (owner "root"):
    the `link` helper (/usr/local/bin/azzio-link, executable), the custom icon (scalable SVG
    + PNG rasterizations), and the thunar.desktop override (a package-owned path -> staged for
    the post-pacstrap install hook via pacman.ISO_APP_OVERRIDES, not the overlay).

The suppression of the extra file-manager/Xfce app-menu launchers (Bulk Rename, file-manager
Preferences, About Xfce, Removable Drives) is done in pacman.ISO_APP_OVERRIDES via NoDisplay
overrides (see pacman.py), not here -- those are separate package-owned .desktop files.
"""

from __future__ import annotations

from pathlib import Path

from . import actions
from . import icons
from . import launcher
from . import live_sidebar
from . import locale
from . import menu_cleanup
from . import settings
from . import sidebar
from . import templates

# --- vendored source facts --------------------------------------------------
# The Azzio File Manager source tree is committed under packages/file_manager/source/ (a git clone
# pinned by SOURCE_COMMIT below, relicensed GPL-3.0 -- the lineage is GPL-2.0-or-later, so "or
# later" lets us ship it under GPL-3.0). makepkg._emit_recipes copies SOURCE_DIR into the recipe
# dir as ./SOURCE_SUBDIR at build time, and pkgbuild.pkgbuild_file_manager's prepare() copies it
# from there into a writable build tree. SOURCE_SUBDIR is a stable relative string so the recipe
# fingerprint does not depend on the absolute path (host vs container).
#
# There is deliberately NO version constant here: Azzio File Manager is OUR OWN thing, not a
# versioned upstream drop-in, so it carries no semver -- the pkgver is a plain monotonic integer
# set in pkgbuild.pkgbuild_file_manager. The git commit is the only pinned source fact.
#   Lineage: https://gitlab.xfce.org/xfce/thunar  (commit SOURCE_COMMIT)
# The ONE Azzio behaviour change (always show the fully-resolved, symlink-dereferenced path in
# the location bar/title) is applied DIRECTLY to the committed C source (source/thunar/
# thunar-window.c), so the modification is itself version-controlled and auditable with git diff.
# There is no separate .patch artifact and no build-time patch step -- the tree already carries it.
SOURCE_COMMIT = "05a586b8a0608b0d855e3153fcb5207b0b091ef5"
SOURCE_SUBDIR = "source"                       # the vendored tree dirname, and its name in $srcdir
SOURCE_DIR = Path(__file__).resolve().parent / SOURCE_SUBDIR

# Re-export the public constants callers/tests reach for (paths + the icon name), so
# `from packages import file_manager; file_manager.FILE_MANAGER_RC_PATH` works like the flat modules.
FILE_MANAGER_RC_PATH = settings.FILE_MANAGER_RC_PATH
XFCONF_FILE_MANAGER_PATH = settings.XFCONF_FILE_MANAGER_PATH
GTK_CSS_PATH = settings.GTK_CSS_PATH
GTK_BOOKMARKS_PATH = sidebar.GTK_BOOKMARKS_PATH
UCA_PATH = actions.UCA_PATH
LINK_SCRIPT_DEST = actions.LINK_SCRIPT_DEST
FILE_MANAGER_DESKTOP_PATH = launcher.FILE_MANAGER_DESKTOP_PATH
FILE_MANAGER_ICON_NAME = launcher.FILE_MANAGER_ICON_NAME
ICON_ASSET = launcher.ICON_ASSET
ICON_SCALABLE_PATH = launcher.ICON_SCALABLE_PATH
ICON_PNG_SIZES = launcher.ICON_PNG_SIZES
LIVE_SIDEBAR_SYNC_DEST = live_sidebar.SYNC_SCRIPT_DEST
USER_DIRS_PATH = templates.USER_DIRS_PATH
ICON_THEME_NAME = icons.ICON_THEME_NAME
ICON_THEME_DIR = icons.ICON_THEME_DIR

_CONF = 0o644
_EXEC = 0o755


def emit_plan() -> list[dict]:
    """Return the emit plan for the whole file-manager CONFIG, in the builder/dest/mode/owner
    shape compiler._emit_apps consumes (with the "asset"/"render" extras for the icon). HOME
    files are skel-mirrored; the `link` script and icon are root-owned system files; the
    thunar.desktop entry's dest matches an ISO_APP_OVERRIDES target so it is staged for the
    post-pacstrap install hook. Returns FRESH dicts so a caller cannot mutate module state.

    NOTE: this plan is the CONFIG only. The file-manager BINARY is produced from the vendored
    source (SOURCE_DIR) by the makepkg stage via pkgbuild.pkgbuild_file_manager, not here."""
    plan: list[dict] = [
        # --- HOME config files (skel-mirrored) ---
        {   # thunarrc: the classic GKeyFile (fresh-profile seed + no-xfconfd fallback).
            "builder": settings.file_manager_rc,
            "dest": settings.FILE_MANAGER_RC_PATH,
            "mode": _CONF,
            "owner": "home",
        },
        {   # the Xfconf channel XML: the runtime store the file-manager binary actually reads.
            "builder": settings.xfconf_channel_xml,
            "dest": settings.XFCONF_FILE_MANAGER_PATH,
            "mode": _CONF,
            "owner": "home",
        },
        {   # the file-manager-scoped gtk.css (font half of the +20% bump).
            "builder": settings.gtk_css,
            "dest": settings.GTK_CSS_PATH,
            "mode": _CONF,
            "owner": "home",
        },
        {   # the sidebar shortcuts (GTK bookmarks) built from home_directory.
            "builder": sidebar.gtk_bookmarks,
            "dest": sidebar.GTK_BOOKMARKS_PATH,
            "mode": _CONF,
            "owner": "home",
        },
        {   # the custom actions (Edit with gedit/gimp, Create Link, Open Terminal Here).
            "builder": actions.uca_xml,
            "dest": actions.UCA_PATH,
            "mode": _CONF,
            "owner": "home",
        },
        # --- SYSTEM files (root-owned) ---
        {   # the `link` helper the Create Link action calls (executable).
            "builder": actions.link_script,
            "dest": actions.LINK_SCRIPT_DEST,
            "mode": _EXEC,
            "owner": "root",
        },
        {   # the custom icon: scalable SVG master (our asset, our icon name).
            "builder": None,
            "asset": launcher.ICON_ASSET,
            "dest": launcher.ICON_SCALABLE_PATH,
            "mode": _CONF,
            "owner": "root",
        },
        {   # the thunar.desktop override (package-owned dest -> staged post-pacstrap).
            "builder": launcher.file_manager_desktop,
            "dest": launcher.FILE_MANAGER_DESKTOP_PATH,
            "mode": _CONF,
            "owner": "root",
        },
    ]
    # The NoDisplay overrides that hide the extra file-manager/Xfce launchers (Bulk Rename,
    # file-manager Preferences, Removable Drives, About Xfce) from the application menu (PROMPT
    # task 3). Each is a package-owned .desktop, so its dest matches an ISO_APP_OVERRIDES target and
    # compiler._emit_apps stages the body for the post-pacstrap install hook. The body is a
    # fixed string per launcher, so a default-arg lambda captures it as the builder.
    for dest, body in menu_cleanup.builders():
        plan.append({
            "builder": (lambda b=body: b),
            "dest": dest,
            "mode": _CONF,
            "owner": "root",
        })
    # PNG rasterizations of the icon at the standard sizes (so the loader has a source at
    # every size without a theme-cache rebuild). Each renders the SAME asset SVG.
    for size in launcher.ICON_PNG_SIZES:
        png_dir = launcher.ICON_PNG_DIR.format(size=size)
        plan.append({
            "builder": None,
            "render": {"asset": launcher.ICON_ASSET, "size": size},
            "dest": f"{png_dir}/{launcher.FILE_MANAGER_ICON_NAME}.png",
            "mode": _CONF,
            "owner": "root",
        })
    # The gettext .mo override catalog (relabels "Places" -> "Home Directory", the built-in
    # default-opener to "Edit with %s", and the Create Folder/Document labels -- PROMPT batch
    # items 3/7/8). A BINARY blob (bytes_builder), shipped ROOT-owned at the standard system
    # locale path for EACH locale the ISO generates, so it takes effect with the session LANG.
    # Both dests are in pacman.ISO_APP_OVERRIDES: the en_GB path is package-owned (our
    # file_manager package ships it), so it is NoExtract'd + planted post-pacstrap -- planting it
    # in the overlay aborts pacstrap ("thunar.mo exists in filesystem"). compiler._emit_apps
    # redirects the bytes to the post-pacstrap staging dir when the dest matches an override target.
    for loc in locale.LOCALES:
        plan.append({
            "builder": None,
            "bytes_builder": locale.mo_bytes,
            "dest": locale.mo_path(loc),
            "mode": _CONF,
            "owner": "root",
        })
    # The live-sidebar sync helper (regenerates the GTK bookmarks from the ACTUAL home contents
    # at runtime, so additions show up in the sidebar -- PROMPT). Root-owned executable; wired
    # into session startup by packages/openbox's autostart.
    plan += live_sidebar.emit_plan()
    # The XDG user-dirs pointer (~/.config/user-dirs.dirs). Folded in from the templates submodule:
    # a single HOME file (owner "home", skel-mirrored). The ~/Templates "Create Document" set is
    # gone -- the user asked for the Templates dir to be deleted -- so only the XDG pointer ships
    # (XDG_TEMPLATES_DIR back at the stock $HOME/, other XDG dirs mapped to the Azzio home layout).
    plan += templates.emit_plan()
    # The Azzio icon theme -- the file manager's folder / file / toolbar / home / mount-point / and
    # right-click CONTEXT-MENU icons (PROMPT: new directory icons, text-file icon, toolbar
    # arrows + search + zoom, username icon, mount-point symbol, and the right-click menu icons).
    # A separate icon theme that Inherits Adwaita so OUR names (folder, folder-documents, user-home,
    # go-*, system-search, zoom-*, view-*, text-x-generic, folder-*, and the edit-*/document-*
    # menu names) win while everything else falls through to Adwaita. All root-owned (a new,
    # non-package-owned theme dir). gtk-icon-theme-name points at "Azzio" (packages/azzio/theme +
    # packages/openbox).
    plan += icons.emit_plan()
    return plan
