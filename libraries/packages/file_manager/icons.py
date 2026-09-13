"""Azzio icon theme -- the file manager's folder / file / toolbar icons (PROMPT: new directory
icons, text-file icon, toolbar arrows + search, home/username icon, mount-point symbol).

WHY A WHOLE ICON THEME (not "ship under the stock name in hicolor"). The file manager (and the
whole session) run with the ADWAITA icon theme (packages/azzio/theme + packages/openbox set
gtk-icon-theme-name). Folder/file/toolbar icons are resolved BY NAME through GtkIconTheme:
`folder`, `folder-documents`, `user-home`, `go-previous`, `system-search`, `text-x-generic`, etc.
Adwaita already defines every one of those names, and it is searched BEFORE hicolor -- so dropping
our SVGs into hicolor under the same names would be dead (Adwaita's are found first). The robust,
upgrade-proof fix is a SEPARATE icon theme, "Azzio", whose index.theme has `Inherits=Adwaita`:

  * Our overrides sit ABOVE Adwaita in resolution, so OUR `folder`, `folder-documents`, ... win.
  * Everything we DON'T ship (every other icon in the UI) falls through to Adwaita unchanged.
  * Nothing is package-owned (the "Azzio" theme dir is new), so it needs no NoExtract dance and an
    adwaita-icon-theme upgrade cannot revert it -- exactly the "a new name a theme upgrade can't
    revert" rule the file-manager launcher icon already follows.

packages/azzio/theme + packages/openbox point gtk-icon-theme-name at "Azzio" (not "Adwaita") so
the theme is active. The GTK *widget* theme stays Adwaita-dark/Adwaita (light vs dark is a widget
concern; the icon theme is shared by both and just inherits Adwaita).

NAME MAPPING (SVG asset -> icon name -> hicolor CONTEXT dir). Standard freedesktop names win over
Adwaita's; the folder dirs with no standard name use a folder-<type> name (matching the stock
folder-documents / folder-music style, NO "azzio-" prefix -- the prefix was dropped as a nicer
convention) that the vendored file manager maps by basename (see thunar-file.c
thunar_file_get_icon_name + the azzio_folder_dirs table):

  folder                     folder.svg              places      generic folder (base of the set)
  folder-documents           folder-documents.svg    places      Documents
  folder-download            folder-download.svg     places      Downloads
  folder-music               folder-music.svg        places      Music
  folder-pictures            folder-pictures.svg     places      Pictures
  folder-videos              folder-videos.svg       places      Videos
  user-desktop               user-desktop.svg        places      Desktop
  user-home                  user-home.svg           places      the "main" home root (sidebar top)
  folder-projects            folder-projects.svg     places      Projects   (no standard name)
  folder-vault               folder-vault.svg        places      Vault      (no standard name)
  folder-ignore              folder-ignore.svg       places      Ignore     (no standard name)
  folder-mount               folder-mount.svg        places      Shared + Mounts (mount-point symbol)
  folder-cache               folder-cache.svg        places      Cache  shortcut (-> .cache)
  folder-config              folder-config.svg       places      Config shortcut (-> .config)
  folder-trash               folder-trash.svg        places      Trash  shortcut (-> .local/share/Trash/files)
  folder-local               folder-local.svg        places      Local  shortcut (-> .local)
  folder-ssh                 folder-ssh.svg          places      SSH    shortcut (-> .ssh)
  text-x-generic             text-x-generic.svg      mimetypes   plain text / generic file
  emblem-symbolic-link       emblem-symbolic-link    emblems     the symlink arrow overlay (folders + files)
  go-previous                go-previous.svg         actions     toolbar Back  (Left arrow)
  go-next                    go-next.svg             actions     toolbar Forward (Right arrow)
  go-up                      go-up.svg               actions     toolbar Up
  system-search              system-search.svg       actions     toolbar Search
  zoom-in                    zoom-in.svg             actions     toolbar/menu Zoom In
  zoom-out                   zoom-out.svg            actions     toolbar/menu Zoom Out
  zoom-original              zoom-original.svg        actions     toolbar/menu Normal Size
  view-refresh               view-refresh.svg         actions     toolbar Reload
  view-grid                  view-grid.svg            actions     view switcher: icon view
  view-list                  view-list.svg            actions     view switcher: detailed list
  view-compact               view-compact.svg         actions     view switcher: compact list

  -- CONTEXT-MENU action icons (the right-click menu on a folder/file/background). These are the
     names the menu items resolve (thunar-action-manager.c's action-entry table). Adwaita defines
     them all, so WITHOUT these overrides the right-click menu shows Adwaita glyphs -- exactly the
     "the icons inside also need to be changed into our set" fix. --
  document-open              document-open.svg        actions     "Open"
  edit-cut                   edit-cut.svg             actions     "Cut"
  edit-copy                  edit-copy.svg            actions     "Copy"
  edit-paste                 edit-paste.svg           actions     "Paste" + "Paste Into Folder"
  edit-delete                edit-delete.svg          actions     "Delete"
  user-trash                 user-trash.svg           actions     "Move to Trash"
  document-properties        document-properties.svg  actions     "Properties..."
  folder-new                 folder-new.svg           actions     "Create New Folder..."
  document-new               document-new.svg         actions     "Create New Document..."
  utilities-terminal         utilities-terminal.svg   actions     uca "Open Terminal Here" <icon>

THE TOOLBAR -symbolic PROBLEM (PROMPT: "left right up search icons were not applied"). The file
manager's TOOLBAR requests the *symbolic* variant of every icon name when its
misc-symbolic-icons-in-toolbar pref is on -- and that pref DEFAULTS TO TRUE (verified in
thunar-preferences.c: the toolbar appends "-symbolic" via thunar_window_toolbar_get_icon_name).
So the toolbar looks up `go-previous-symbolic`, `system-search-symbolic`, `zoom-in-symbolic`, ...
which we did not ship -> they fell through to Adwaita, which is why the toolbar arrows/search were
unchanged. Two belt-and-braces fixes, BOTH applied:
  1. settings.py sets misc-symbolic-icons-in-toolbar=false so the toolbar uses the plain names.
  2. This module ALSO ships a `<name>-symbolic` alias (same SVG) for every ACTIONS icon, so the
     toolbar gets an Azzio icon even if that pref is ever flipped back on. The alias is the SAME
     source SVG under the "<name>-symbolic" name (see SYMBOLIC_ALIAS_CONTEXTS).

HOW IT SHIPS. Same pattern as launcher.py / application_menu: each SVG asset is the single source
of truth; we install it to the theme's scalable/<context>/ dir AND rasterize PNGs at the standard
sizes there, all root-owned (a new theme dir, not package-owned -> straight into the overlay, no
NoExtract). emit_plan() returns builder/dest/mode/owner + the "asset"/"render" extras that
compiler._emit_apps consumes; file_manager.emit_plan() folds this in. A generated index.theme
declares the theme and its Directories so GtkIconTheme indexes it.
"""

from __future__ import annotations

# The icon theme NAME (gtk-icon-theme-name points here; see packages/azzio/theme + openbox) and
# its root under the standard system icon path.
ICON_THEME_NAME = "Azzio"
ICON_THEME_DIR = f"/usr/share/icons/{ICON_THEME_NAME}"

# Where the assets live in the repo (each is the single source of truth for one icon).
ASSET_DIR = "icons/file_manager_icons"

# The PNG sizes rasterized alongside the scalable master (matches the launcher/application_menu
# set) so the loader has a source at every common size without a theme-cache rebuild.
ICON_PNG_SIZES = (16, 22, 24, 32, 48, 64, 128, 256)

# The full icon set: (svg asset basename, installed icon name, hicolor context dir). The icon
# name is what GtkIconTheme resolves; the context dir is the freedesktop category (places /
# mimetypes / actions) the name belongs to and must be listed in index.theme's Directories.
# (svg_basename, icon_name, context)
ICONS: tuple[tuple[str, str, str], ...] = (
    # Folders (places). Standard freedesktop names override Adwaita; the folder-<type> ones with no
    # XDG name are ours (formerly azzio-folder-*; the prefix was dropped for a nicer convention).
    ("folder.svg", "folder", "places"),
    ("folder-documents.svg", "folder-documents", "places"),
    ("folder-download.svg", "folder-download", "places"),
    ("folder-music.svg", "folder-music", "places"),
    ("folder-pictures.svg", "folder-pictures", "places"),
    ("folder-videos.svg", "folder-videos", "places"),
    ("user-desktop.svg", "user-desktop", "places"),
    ("user-home.svg", "user-home", "places"),
    ("folder-projects.svg", "folder-projects", "places"),
    ("folder-vault.svg", "folder-vault", "places"),
    ("folder-ignore.svg", "folder-ignore", "places"),
    ("folder-mount.svg", "folder-mount", "places"),
    # The dot-location shortcut folders (Cache/Config/Trash/Local/SSH -> .cache/.config/... ). No
    # XDG type; the vendored file manager maps them by the shortcut basename ($HOME/Cache, ...).
    ("folder-cache.svg", "folder-cache", "places"),
    ("folder-config.svg", "folder-config", "places"),
    ("folder-trash.svg", "folder-trash", "places"),
    ("folder-local.svg", "folder-local", "places"),
    ("folder-ssh.svg", "folder-ssh", "places"),
    # Files (mimetypes).
    ("text-x-generic.svg", "text-x-generic", "mimetypes"),
    # Emblems (overlaid on the base icon). The symlink arrow (PROMPT: symbolic links must be
    # obviously distinct). Re-enabled in thunar-file.c thunar_file_get_emblem_names for symlinks.
    ("emblem-symbolic-link.svg", "emblem-symbolic-link", "emblems"),
    # Toolbar navigation + search (actions).
    ("go-previous.svg", "go-previous", "actions"),
    ("go-next.svg", "go-next", "actions"),
    ("go-up.svg", "go-up", "actions"),
    ("system-search.svg", "system-search", "actions"),
    # Toolbar/menu zoom + reload + view-switcher (actions). Also used by the background right-click
    # Zoom section (thunar-standard-view.c) and the toolbar view switcher.
    ("zoom-in.svg", "zoom-in", "actions"),
    ("zoom-out.svg", "zoom-out", "actions"),
    ("zoom-original.svg", "zoom-original", "actions"),
    ("view-refresh.svg", "view-refresh", "actions"),
    ("view-grid.svg", "view-grid", "actions"),
    ("view-list.svg", "view-list", "actions"),
    ("view-compact.svg", "view-compact", "actions"),
    # Right-click CONTEXT-MENU action icons (actions). The names the menu items resolve
    # (thunar-action-manager.c) -- Adwaita defines them all, so these overrides are what put OUR
    # icons in the right-click menu on a folder/file/background (PROMPT: the unfixed item).
    ("document-open.svg", "document-open", "actions"),
    ("edit-cut.svg", "edit-cut", "actions"),
    ("edit-copy.svg", "edit-copy", "actions"),
    ("edit-paste.svg", "edit-paste", "actions"),
    ("edit-delete.svg", "edit-delete", "actions"),
    ("user-trash.svg", "user-trash", "actions"),
    ("document-properties.svg", "document-properties", "actions"),
    ("folder-new.svg", "folder-new", "actions"),
    ("document-new.svg", "document-new", "actions"),
    ("utilities-terminal.svg", "utilities-terminal", "actions"),
)

# Contexts whose icons ALSO ship a "<name>-symbolic" alias (same SVG). The toolbar looks up the
# symbolic variant when misc-symbolic-icons-in-toolbar is on (it defaults TRUE upstream), so every
# ACTIONS icon needs a symbolic twin or the toolbar falls through to Adwaita (PROMPT: the toolbar
# arrows/search "were not applied"). settings.py also turns that pref off; shipping the aliases is
# the belt-and-braces half so a flipped pref cannot revert the toolbar. Only "actions" needs it --
# folders/mimetypes/emblems are never requested with a -symbolic suffix.
SYMBOLIC_ALIAS_CONTEXTS: frozenset[str] = frozenset({"actions"})

_CONF = 0o644


def _asset(svg_basename: str) -> str:
    """Repo-relative asset path for one icon SVG (the compiler resolves it under assets/)."""
    return f"{ASSET_DIR}/{svg_basename}"


def _scalable_dest(icon_name: str, context: str) -> str:
    """The scalable (SVG master) dest for an icon inside the Azzio theme."""
    return f"{ICON_THEME_DIR}/scalable/{context}/{icon_name}.svg"


def _png_dest(icon_name: str, context: str, size: int) -> str:
    """The PNG rasterization dest for an icon at a given size inside the Azzio theme."""
    return f"{ICON_THEME_DIR}/{size}x{size}/{context}/{icon_name}.png"


def _contexts() -> tuple[str, ...]:
    """The distinct hicolor context dirs our icons use (places / mimetypes / actions), in first-
    seen order -- index.theme lists a scalable/<ctx> and a <size>/<ctx> entry for each."""
    seen: list[str] = []
    for _svg, _name, ctx in ICONS:
        if ctx not in seen:
            seen.append(ctx)
    return tuple(seen)


# Map our PNG sizes to the freedesktop icon-theme Context label for each dir.
_CONTEXT_LABEL = {
    "places": "Places",
    "mimetypes": "MimeTypes",
    "actions": "Actions",
    "emblems": "Emblems",
}


def index_theme() -> str:
    """Return the Azzio theme's index.theme. Inherits Adwaita (so every icon we don't override
    falls through), and declares one scalable/<ctx> dir plus one <size>/<ctx> dir per context we
    ship, so GtkIconTheme indexes our overrides. Hidden=true keeps it out of theme pickers (it is
    an internal override theme, not a user-facing choice)."""
    contexts = _contexts()
    # Directories = scalable/<ctx> for every context, then <size>/<ctx> for every size+context.
    dirs: list[str] = [f"scalable/{c}" for c in contexts]
    for size in ICON_PNG_SIZES:
        dirs += [f"{size}x{size}/{c}" for c in contexts]

    lines = [
        "[Icon Theme]",
        "Name=Azzio",
        "Comment=Azzio icon overrides (folders, files, toolbar); inherits Adwaita",
        "Inherits=Adwaita",
        "Hidden=true",
        "Example=folder",
        "Directories=" + ",".join(dirs),
        "",
    ]
    # A scalable section per context.
    for c in contexts:
        lines += [
            f"[scalable/{c}]",
            f"Context={_CONTEXT_LABEL[c]}",
            "Size=128",
            "MinSize=8",
            "MaxSize=512",
            "Type=Scalable",
            "",
        ]
    # A fixed-size section per size+context.
    for size in ICON_PNG_SIZES:
        for c in contexts:
            lines += [
                f"[{size}x{size}/{c}]",
                f"Context={_CONTEXT_LABEL[c]}",
                f"Size={size}",
                "Type=Fixed",
                "",
            ]
    return "\n".join(lines) + "\n"


def emit_plan() -> list[dict]:
    """Return the emit plan for the Azzio icon theme -- all root-owned system files (a new,
    non-package-owned theme dir, so straight into the overlay). For each icon: the scalable SVG
    master ("asset" copy) + a PNG rasterization ("render") at each standard size. Plus the
    generated index.theme. file_manager.emit_plan() folds this into the combined file-manager plan
    that compiler._emit_apps writes."""
    plan: list[dict] = [
        {   # the theme descriptor GtkIconTheme reads to index our override dirs.
            "builder": index_theme,
            "dest": f"{ICON_THEME_DIR}/index.theme",
            "mode": _CONF,
            "owner": "root",
        },
    ]
    for svg_basename, icon_name, context in ICONS:
        asset = _asset(svg_basename)
        # The icon under its own name, plus (for actions) a "<name>-symbolic" alias from the SAME
        # SVG so the toolbar's symbolic lookup resolves to our icon too (see SYMBOLIC_ALIAS_CONTEXTS).
        names = [icon_name]
        if context in SYMBOLIC_ALIAS_CONTEXTS:
            names.append(f"{icon_name}-symbolic")
        for name in names:
            # scalable master (verbatim SVG copy).
            plan.append({
                "builder": None,
                "asset": asset,
                "dest": _scalable_dest(name, context),
                "mode": _CONF,
                "owner": "root",
            })
            # PNG rasterizations at each standard size.
            for size in ICON_PNG_SIZES:
                plan.append({
                    "builder": None,
                    "render": {"asset": asset, "size": size},
                    "dest": _png_dest(name, context, size),
                    "mode": _CONF,
                    "owner": "root",
                })
    return plan
