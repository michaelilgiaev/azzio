"""File manager preferences -- the view/location-bar/side-pane/sizing settings, as the ONE
source of truth rendered into BOTH forms the file manager reads.

WHY TWO FILES FROM ONE TABLE. The file manager 4.20 MIGRATED its settings to Xfconf: on a
fresh profile it reads ~/.config/Thunar/thunarrc, migrates it into the Xfconf "thunar"
channel (~/.config/xfce4/xfconf/xfce-perchannel-xml/thunar.xml), and uses Xfconf thereafter
("Your Thunar settings have been migrated to Xfconf." -- verified in the 4.20 binary). So
the RUNTIME source of truth on the real desktop (where xfconfd runs) is the channel XML,
while thunarrc is what a fresh profile migrates FROM and what the file manager falls back to
when xfconfd is absent. To be correct in BOTH cases -- and never let the two drift -- this
module keeps the settings ONCE (SETTINGS below) and renders both file_manager_rc() and
xfconf_channel_xml() from it. (The same belt-and-braces the repo uses for the gtk2/3/4
settings.ini trio.)

WHAT WE SET (PROMPT task 2 + task 7), every value VERIFIED against the installed file
manager 4.20 (the thunarrc CamelCase keys from the binary's GParamSpec names; the Xfconf
kebab-case property names + canonical stored values captured by round-tripping through
xfconf-query):

  * last-location-bar = ThunarLocationEntry -- the TEXT-ENTRY (editable path) location bar,
    always visible, instead of the breadcrumb buttons (ThunarLocationButtons). PROMPT: "Always
    show the path as an editable text path".
  * last-side-pane = ThunarShortcutsPane -- the SHORTCUTS pane (our sidebar), not the tree
    pane (ThunarTreePane) and not hidden.
  * last-view = ThunarIconView -- open in the ICON view by default (PROMPT batch item 2;
    was the details/list view). The 150% icon zoom below pairs with it.
  * misc-enable-expandable-folders = false -- NO inline +/- disclosure triangles to expand a
    directory in place. PROMPT: "disable that (the file manager's expandable behavior)".
  * misc-always-enable-split-view = false + last-splitview-separator-position pinned -- the
    split view stays OFF by default (the toggle/menu item is also dropped, see actions.py).
  * misc-volume-management = false -- do NOT auto-manage/auto-mount removable volumes, which
    keeps the "Devices" removable clutter out of the side pane. PROMPT: "not showing mounted
    volumes".
  * misc-single-click = false -- double-click to open (single-click selects), the expected
    desktop behaviour.
  * last-menubar-visible = true -- keep the menubar (the file manager's menu is lean; the user wants
    the path box + menu, not a chromeless window).
  * last-show-hidden = false -- hidden files off by default.
  * misc-folders-first = true -- list folders before files.

VIEW ORDERING (PROMPT ordering task, the folder-VIEW half -- HONEST NOTE). The required order is
directories -> files -> symbolic links -> "Trash" last. The file manager's built-in sort is
name/size/type/date with a "folders first" toggle only; it has NO native "symlinks as their own
third group" and NO "pin Trash to the bottom" in the main pane (verified against the file
manager 4.20 -- the sort keys are exactly those). So the VIEW cannot reproduce the full
four-way grouping with any stock thunarrc key: misc-folders-first=true (set above) gets us the
FIRST split (directories before files), which is as close as the file manager genuinely allows
without patching. We do
NOT fake the rest. The SIDEBAR half (the firm requirement) IS fully satisfied -- the bookmarks
order is entirely ours (dirs -> files -> symlinks -> Trash last), applied to the curated seed
(sidebar.py / home_directory) AND to the live contents at runtime (live_sidebar.py).
  * misc-show-about-templates = false + misc-max-number-of-templates = 100 -- hide the modal
    "About Templates" dialog and cap the Create Document submenu. There is no ~/Templates dir any
    more (the user asked for it to be deleted), so the submenu is empty; false just hides the
    leftover "About Templates" placeholder modal.
  * misc-resolve-links = true (PROMPT batch item 5) -- always show the fully-resolved
    (symlink-dereferenced) path in the location bar. HONEST CAVEAT: this pref was added
    UPSTREAM in the file manager 4.21.6 and is IGNORED by 4.20.x, so on the current 4.20 build entering
    a symlink still shows the symlink path in the location entry (there is no 4.20 config lever
    for it -- verified against the 4.20 source: the location entry prints g_file_get_path() of
    the as-requested GFile with no canonicalization). We ship the pref anyway: it is harmless
    on 4.20 and takes effect the instant the file manager is updated to >=4.21.6. The SIDEBAR half is
    already correct on 4.20 -- the bookmarks point at RESOLVED targets (sidebar.py), so opening
    a shortcut shows the real path today.

THE ZOOM BUMP (PROMPT task 7, scale-relative). last-icon-view-zoom-level is bumped one step
above stock to THUNAR_ZOOM_LEVEL_150_PERCENT (stock is _100_PERCENT = 48px icons; 150% =
72px). The file manager's zoom levels are RELATIVE percentage steps of the theme icon size,
NOT absolute pixels, so they COMPOSE with the global desktop scale (a scale change rescales the
base icon size and the percentage rides on top) -- exactly the "must NOT be a hardcoded absolute
pixel size" requirement. The FONT half of the +20% is a file-manager gtk.css rule in em units
(gtk_css(), also here) -- em is relative to the inherited, globally-scaled GTK font, so it
too composes with the scale. Together they make the file manager ~20% larger than stock at any
scale.

Pure standard library (returns strings). compiler._emit_apps writes both files as HOME files
(owner "home"), skel-mirrored, exactly like vlc's vlcrc.
"""

from __future__ import annotations

# The live user's home (matches openbox.HOME / the airootfs /home/main tree).
HOME = "/home/main"

# Where the file manager reads its two config forms. thunarrc is the classic GKeyFile; the
# Xfconf channel XML is the migrated runtime store (the real source of truth once xfconfd ran).
FILE_MANAGER_RC_PATH = f"{HOME}/.config/Thunar/thunarrc"
XFCONF_FILE_MANAGER_PATH = f"{HOME}/.config/xfce4/xfconf/xfce-perchannel-xml/thunar.xml"
# The file-manager-scoped GTK CSS (font half of the +20% bump). ~/.config/gtk-3.0/gtk.css is
# the per-user CSS GTK3 loads; the rules here are SELECTOR-SCOPED to the file manager's window
# node so no other GTK app is affected.
GTK_CSS_PATH = f"{HOME}/.config/gtk-3.0/gtk.css"

# The ~20% font bump, in em (relative to the inherited/globally-scaled GTK font, so it
# composes with the desktop scale rather than pinning pixels). 1.2 == +20%.
FILE_MANAGER_FONT_SCALE = 1.2

# --- The single source of truth -------------------------------------------------
# Ordered so both rendered files list settings the same way. Each entry:
#   (thunarrc_key CamelCase, xfconf_property kebab-case, kind, value)
# kind is "string" or "bool"; value is the canonical value the file manager stores (VERIFIED
# against the file manager 4.20 -- the class-name strings like ThunarLocationEntry and the
# percentage zoom enums are exactly what xfconf-query round-trips). thunarrc renders bools as
# TRUE/FALSE and strings verbatim; the Xfconf XML renders type="bool" value="true|false" /
# type="string".
_TRUE = True
_FALSE = False
SETTINGS: tuple[tuple[str, str, str, object], ...] = (
    # Default view = ICON view (PROMPT batch item 2 -- was ThunarDetailsView/list). The icon
    # zoom bump below (150%) is the sensible icon-view zoom to pair with it.
    ("LastView", "last-view", "string", "ThunarIconView"),
    ("LastLocationBar", "last-location-bar", "string", "ThunarLocationEntry"),
    ("LastSidePane", "last-side-pane", "string", "ThunarShortcutsPane"),
    ("LastIconViewZoomLevel", "last-icon-view-zoom-level", "string",
     "THUNAR_ZOOM_LEVEL_150_PERCENT"),
    ("LastDetailsViewZoomLevel", "last-details-view-zoom-level", "string",
     "THUNAR_ZOOM_LEVEL_100_PERCENT"),
    ("LastMenubarVisible", "last-menubar-visible", "bool", _TRUE),
    ("LastShowHidden", "last-show-hidden", "bool", _FALSE),
    # Use the PLAIN (non-symbolic) icon names in the toolbar. The file manager DEFAULTS this to
    # TRUE (verified in thunar-preferences.c), which makes the toolbar append "-symbolic" to every
    # icon name (thunar_window_toolbar_get_icon_name) -- so it looked up go-previous-symbolic /
    # system-search-symbolic / zoom-in-symbolic etc., which the Azzio icon theme did not ship, and
    # they fell through to Adwaita (PROMPT: the toolbar Back/Forward/Up/Search icons "were not
    # applied"). Setting it FALSE makes the toolbar use go-previous / system-search / zoom-in, which
    # the Azzio theme overrides. (icons.py ALSO ships -symbolic aliases as belt-and-braces.)
    ("MiscSymbolicIconsInToolbar", "misc-symbolic-icons-in-toolbar", "bool", _FALSE),
    ("MiscEnableExpandableFolders", "misc-enable-expandable-folders", "bool", _FALSE),
    ("MiscAlwaysEnableSplitView", "misc-always-enable-split-view", "bool", _FALSE),
    ("MiscSingleClick", "misc-single-click", "bool", _FALSE),
    ("MiscVolumeManagement", "misc-volume-management", "bool", _FALSE),
    ("MiscFoldersFirst", "misc-folders-first", "bool", _TRUE),
    # Templates submenu. misc-show-about-templates=false hides the modal "About Templates" dialog
    # (the "weird templates label"). There is no ~/Templates dir any more (the user asked for it to
    # be deleted), so the Create Document submenu is empty -- false just hides the leftover
    # placeholder modal. The cap defaults to 100 (verified in the 4.20 binary: uint, default 100) --
    # pinned so the value is explicit and a test can prove it is not an absolute-pixel-style magic number.
    ("MiscShowAboutTemplates", "misc-show-about-templates", "bool", _FALSE),
    ("MiscMaxNumberOfTemplates", "misc-max-number-of-templates", "uint", 100),
    # ALWAYS show the fully-resolved (symlink-dereferenced) path in the location bar (PROMPT
    # batch item 5). misc-resolve-links (bool, default TRUE) reassigns the current directory
    # to the resolved target so entering a symlink (e.g. ~/Cache -> ~/.cache) shows
    # /home/main/.cache, not /home/main/Cache. VERIFIED: this pref was added upstream in
    # the file manager 4.21.6 (commit 079503c0) and is IGNORED by 4.20.x -- see the module
    # docstring's honest note. It is shipped anyway (harmless on 4.20, correct the moment the
    # file manager is updated to >=4.21.6); the sidebar bookmarks already point at resolved
    # targets so the shortcut route is correct on 4.20 today.
    ("MiscResolveLinks", "misc-resolve-links", "bool", _TRUE),
)

# --- Hidden built-in side-pane shortcuts (PROMPT task 2: remove Devices/Network clutter) --
# The file manager's shortcuts pane shows built-in "Places" (Computer/Recent/Network/Trash) and
# "Devices" (File System, removable volumes) shortcuts ON TOP of our GTK bookmarks. There is
# no group-visibility boolean; instead the file manager hides individual built-ins via two
# STRING-ARRAY properties on the shortcuts model, keyed by the built-in's URI (verified against
# the file manager 4.20 by hiding them through xfconf-query and reading back the canonical
# values). A group with all its built-ins hidden disappears, so hiding these empties the
# Devices/Network/Computer/Recent clutter. We deliberately do NOT hide trash:/// -- the sidebar
# keeps a Trash entry (the PROMPT wants Trash in the sidebar; it is also provided as a
# resolved-path bookmark). misc-volume-management=false (above) already keeps removable volumes
# from appearing. These are a file-manager 4.20 XFCONF feature (arrays), so they are shipped
# ONLY in the channel XML, not thunarrc.
HIDDEN_BOOKMARKS: tuple[str, ...] = (
    "recent:///",     # the "Recent" place
    "computer:///",   # the "Computer" place
    "network:///",    # the "Network" place (Browse Network)
    "trash:///",      # the built-in Trash place -- we ship our OWN resolved-path Trash bookmark
                      # (file://.../Trash/files, PROMPT task 2), so hide the trash:/// built-in
                      # to avoid a duplicate. (Our bookmark's URI differs, so it survives.)
    # REMOVE the "Devices" section entirely, including its permanent "File System" row (PROMPT
    # batch item 1). VERIFIED against the file manager 4.20 source (thunar-shortcuts-model.c):
    # the File System row is a built-in with device==NULL and URI EXACTLY "file:///", so
    # hidden-devices (which only governs real ThunarDevice volumes/mounts) never hides it --
    # BUT thunar_shortcuts_model_get_hidden() matches the shortcut's dup'd URI against
    # hidden-bookmarks, so "file:///" here DOES hide it. And the file manager auto-hides a group
    # HEADER whose child count is 0, so with the File System row hidden and no removable volumes
    # (MiscVolumeManagement=false), the whole "Devices" heading disappears too.
    "file:///",
    # KEEP the built-in Home shortcut VISIBLE -- it IS the "main" row the PROMPT wants at the top
    # of the sidebar ("add the user to the top of the sidebar ... simply name it 'main'"). The
    # built-in Home place (group PLACES_DEFAULT, sort_id 0) is displayed as the home basename, i.e.
    # "main" for /home/main, and already sits above the GTK-bookmark group, so simply NOT hiding it
    # (its URI is exactly "file:///home/main") puts "main" at the very top. It is deliberately the
    # built-in place, not a GTK bookmark (a bookmark would land below the built-in Desktop and
    # duplicate Home). So "file:///home/main" is intentionally ABSENT from this list.
)
HIDDEN_DEVICES: tuple[str, ...] = (
    "computer:///",   # "Computer" under Devices
    "network:///",    # "Network" under Devices (Browse Network)
)
# NOTE on the "File System" (filesystem root) row under Devices: on the file manager 4.20 it is
# NOT governed by hidden-devices (it has device==NULL and no eject/hide affordance). The way it
# is actually removed is via hidden-bookmarks (its URI "file:///" is added above) -- verified
# against thunar-shortcuts-model.c (get_hidden matches the shortcut URI against
# hidden-bookmarks). With File System hidden and removable media kept out
# (MiscVolumeManagement=false), the Devices group has zero children and the file manager
# auto-hides the heading, so there is no "Devices" section and no "File System"/removable rows
# in the side pane. (If a USB volume is later plugged in, the Devices heading reappears with
# that one device -- there is no config that permanently deletes the heading widget itself.)


def _bool_file_manager_rc(value: bool) -> str:
    """thunarrc stores booleans as the tokens TRUE/FALSE (verified: the file manager
    reads/writes these exact tokens in the [Configuration] group)."""
    return "TRUE" if value else "FALSE"


def _bool_xfconf(value: bool) -> str:
    """Xfconf stores booleans as lowercase true/false in the channel XML."""
    return "true" if value else "false"


def file_manager_rc() -> str:
    """Return ~/.config/Thunar/thunarrc -- the classic GKeyFile the file manager migrates FROM
    on a fresh profile and falls back to when xfconfd is absent. One `[Configuration]` group
    with the CamelCase keys; rendered from SETTINGS so it never drifts from the Xfconf XML."""
    lines = [
        "# Azzio file-manager settings. Generated by packages/file_manager (edit the Python, not",
        "# this file). The file manager 4.20 migrates these into the Xfconf 'thunar' channel on",
        "# first run and uses Xfconf thereafter; this file is the fresh-profile seed + the",
        "# no-xfconfd fallback. The SAME values ship as xfce-perchannel-xml/thunar.xml (the",
        "# runtime store).",
        "[Configuration]",
    ]
    for rc_key, _prop, kind, value in SETTINGS:
        rendered = _bool_file_manager_rc(value) if kind == "bool" else str(value)
        lines.append(f"{rc_key}={rendered}")
    return "\n".join(lines) + "\n"


def xfconf_channel_xml() -> str:
    """Return ~/.config/xfce4/xfconf/xfce-perchannel-xml/thunar.xml -- the Xfconf channel
    store the file manager 4.20 actually reads at runtime (once xfconfd has run). The format
    (declaration `<?xml version="1.1"?>`, `<channel name="thunar" version="1.0">`,
    `<property .../>`) and the canonical values are VERIFIED against the installed file manager
    (captured by round-tripping the settings through xfconf-query). Rendered from the SAME
    SETTINGS table as file_manager_rc()."""
    lines = [
        '<?xml version="1.1" encoding="UTF-8"?>',
        "",
        "<!-- Azzio file-manager settings (Xfconf channel). Generated by packages/file_manager",
        "     (edit the Python, not this file). The runtime store the file manager 4.20 reads;",
        "     rendered from the same settings table as thunarrc. -->",
        '<channel name="thunar" version="1.0">',
    ]
    for _rc_key, prop, kind, value in SETTINGS:
        rendered = _bool_xfconf(value) if kind == "bool" else str(value)
        lines.append(
            f'  <property name="{prop}" type="{kind}" value="{rendered}"/>'
        )
    # The two string-array properties that hide the built-in Places/Devices/Network clutter.
    for prop, uris in (("hidden-bookmarks", HIDDEN_BOOKMARKS),
                       ("hidden-devices", HIDDEN_DEVICES)):
        lines.append(f'  <property name="{prop}" type="array">')
        for uri in uris:
            lines.append(f'    <value type="string" value="{uri}"/>')
        lines.append("  </property>")
    lines.append("</channel>")
    return "\n".join(lines) + "\n"


def gtk_css() -> str:
    """Return ~/.config/gtk-3.0/gtk.css -- the FONT half of the file manager's +20% bump (PROMPT
    task 7), SELECTOR-SCOPED to the file manager's window so no other GTK app is touched.

    The selector `window.thunar-window` targets the file manager's top-level window (its CSS
    node name is `thunar-window`, verified in the 4.20 binary); the descendant selectors cover
    the file area (`.standard-view`) and the sidebar (`.sidebar`/`.shortcuts-pane`). The size is
    set in EM (FILE_MANAGER_FONT_SCALE = 1.2 -> +20%), which is RELATIVE to the inherited GTK
    font -- so when the global desktop scale changes the inherited size, this rides on top of it
    and the file manager stays ~20% larger at ANY scale (never an absolute pixel size). GTK3
    loads this file for every app, but only the file manager's windows match the selector."""
    pct = f"{FILE_MANAGER_FONT_SCALE:g}em"
    return f"""\
/* Azzio file-manager font bump (~20 percent). Generated by packages/file_manager (edit the
   Python, not this file). SCOPED to the file manager's window node so no other GTK app is
   affected. The size is in em -- relative to the inherited, globally-scaled GTK font -- so it
   COMPOSES with the desktop scale instead of pinning pixels (PROMPT task 7). */
window.thunar-window,
window.thunar-window .standard-view,
window.thunar-window .standard-view .view,
window.thunar-window .sidebar,
window.thunar-window .shortcuts-pane {{
    font-size: {pct};
}}
"""
