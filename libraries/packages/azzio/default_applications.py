"""Default Applications (PROMPT task 6) -- the XDG mimeapps.list + the exo preferred terminal.

WHAT THIS SETS. The system-wide "which app opens which file type" defaults, as the freedesktop
mimeapps.list (~/.config/mimeapps.list, the file `xdg-mime default` writes and every GTK/XDG
app consults), grouped by the categories the user specified:

  Internet:    Web = LibreWolf,  Mail = (none),  HTML = LibreWolf
  Multimedia:  Music = VLC,  Video = VLC,  Photos = xviewer
  Office:      Word = LibreOffice Writer,  Spreadsheet = LibreOffice Calc,
               PDF = LibreWolf,  Source Code = gedit
  System:      File Manager = the file manager,  Plain Text = gedit,
               Calculator = qalculate-gtk,  Terminal = kitty

SINGLE SOURCE OF TRUTH, NO MIME MAPPED TWICE. Each category is one row in CATEGORIES: a label,
the handler .desktop id, and the representative MIME types it claims. mimeapps_list() flattens
those rows into [Default Applications]; a build-time assertion (assert_no_mime_collision) fails
the build if any MIME type is claimed by two categories (e.g. PDF and Web both grabbing
application/pdf), so the defaults can never silently conflict. "Mail" and "Terminal"/"File
Manager"/"Calculator" have no single MIME type of their own -- Mail is intentionally empty, and
the latter three are wired through their real mechanisms (see below), so they contribute no
[Default Applications] rows but are recorded here as the source of truth for what they are.

THE TERMINAL (kitty), the non-MIME one. There is no MIME type for "terminal", so the terminal
default is the Xfce/exo "preferred applications" TerminalEmulator -- which is exactly what
the file manager's "Open Terminal Here" uses (`exo-open --launch TerminalEmulator`). We ship:
  * ~/.config/xfce4/helpers.rc with `TerminalEmulator=kitty` (the per-user preferred-terminal
    selection exo reads), and
  * a kitty TerminalEmulator HELPER .desktop (/usr/share/xfce4/helpers/kitty.desktop, an
    X-XFCE-Helper in the TerminalEmulator category) so exo knows how to launch kitty.
VERIFIED in the VM: with both in place, `exo-open --launch TerminalEmulator` (and with
--working-directory) opens kitty. (packages/file_manager's uca.xml ALSO runs kitty directly for
its Open Terminal Here action, so the terminal is kitty by both paths.)

THE FILE MANAGER default is the file manager via inode/directory (in CATEGORIES below) AND exo's
FileManager preferred-app is left to the mimeapps inode/directory default -- the file manager registers
itself; no extra helper is needed for the file manager.

WHERE IT GOES. mimeapps.list + helpers.rc are HOME files (owner "home", skel-mirrored); the
kitty helper .desktop is a root-owned system file. compiler emits this module's emit_plan()
alongside the others.
"""

from __future__ import annotations

# The live user's home (matches openbox.HOME / the airootfs /home/main tree).
HOME = "/home/main"

MIMEAPPS_PATH = f"{HOME}/.config/mimeapps.list"
HELPERS_RC_PATH = f"{HOME}/.config/xfce4/helpers.rc"
# The exo TerminalEmulator helper .desktop -- system-wide so every user gets it (helpers.rc,
# the per-user selection, is skel-mirrored). exo reads helpers from /usr/share/xfce4/helpers.
KITTY_HELPER_PATH = "/usr/share/xfce4/helpers/kitty.desktop"

# The terminal the exo TerminalEmulator preferred-app points at (kitty, the Azzio terminal).
TERMINAL_BIN = "kitty"

# --- The single source of truth: category -> (label, handler .desktop id, MIME types) --------
# Each row is (group, label, desktop_id, mimetypes). desktop_id "" means the category has no
# MIME handler row (Mail is empty; Terminal/File-Manager/Calculator are wired via other
# mechanisms or a single inode type). No MIME type may appear in two rows (asserted below).
# The .desktop ids are VERIFIED present on the installed system.
CATEGORIES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    # Internet
    ("Internet", "Web", "librewolf.desktop", (
        "x-scheme-handler/http", "x-scheme-handler/https")),
    ("Internet", "Mail", "", ()),  # intentionally empty (no mail client shipped)
    ("Internet", "HTML", "librewolf.desktop", (
        "text/html", "application/xhtml+xml")),
    # Multimedia
    ("Multimedia", "Music", "vlc.desktop", (
        "audio/mpeg", "audio/flac", "audio/ogg", "audio/x-wav", "audio/x-vorbis+ogg",
        "audio/mp4", "audio/aac", "audio/x-m4a")),
    ("Multimedia", "Video", "vlc.desktop", (
        "video/mp4", "video/x-matroska", "video/webm", "video/x-msvideo",
        "video/quicktime", "video/mpeg", "video/x-flv")),
    ("Multimedia", "Photos", "xviewer.desktop", (
        "image/jpeg", "image/png", "image/gif", "image/bmp", "image/tiff",
        "image/webp", "image/x-xpixmap", "image/svg+xml")),
    # Office
    ("Office", "Word", "libreoffice-writer.desktop", (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/vnd.oasis.opendocument.text",
        "application/rtf")),
    ("Office", "Spreadsheet", "libreoffice-calc.desktop", (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/vnd.oasis.opendocument.spreadsheet",
        "text/csv")),
    ("Office", "PDF", "librewolf.desktop", (
        "application/pdf",)),
    ("Office", "Source Code", "org.gnome.gedit.desktop", (
        "text/x-csrc", "text/x-chdr", "text/x-python", "text/x-shellscript",
        "application/javascript", "text/x-c++src", "application/json",
        "text/markdown", "text/xml")),
    # System
    ("System", "File Manager", "thunar.desktop", (
        "inode/directory",)),
    ("System", "Plain Text", "org.gnome.gedit.desktop", (
        "text/plain",)),
    ("System", "Calculator", "qalculate-gtk.desktop", ()),  # no MIME type of its own
    ("System", "Terminal", "kitty.desktop", ()),            # wired via exo TerminalEmulator
)


def assert_no_mime_collision() -> None:
    """Fail loudly if any MIME type is claimed by two CATEGORIES rows. A double-mapped MIME
    (e.g. application/pdf under both PDF and Web) would make the default ambiguous, so the
    build must not produce one. Called by mimeapps_list() and guarded by a test."""
    seen: dict[str, str] = {}
    for _group, label, _desktop_id, mimes in CATEGORIES:
        for mime in mimes:
            if mime in seen:
                raise AssertionError(
                    f"MIME {mime!r} mapped twice: {seen[mime]!r} and {label!r}"
                )
            seen[mime] = label


def mime_defaults() -> list[tuple[str, str]]:
    """Return the flat (mimetype, desktop_id) default-application pairs, in CATEGORIES order,
    skipping rows with no handler or no MIME types. This is the [Default Applications] body."""
    assert_no_mime_collision()
    pairs: list[tuple[str, str]] = []
    for _group, _label, desktop_id, mimes in CATEGORIES:
        if not desktop_id:
            continue
        for mime in mimes:
            pairs.append((mime, desktop_id))
    return pairs


# --- The TUI derivation (PROMPT: the azzio "Default Applications" screen derives its rows from
# THIS table, no second copy). Each category the TUI shows becomes a sub-screen; the user can
# pick a new handler from the CANDIDATES for that category, which rewrites the default. The C
# model.c rows and the `azzio default-applications` CLI are pinned to these by tests (the
# wallpaper.py <-> model.c lock-step pattern) so they cannot drift.

# A stable KEY for each category (used on the `azzio default-applications` command line and as
# the C screen id suffix), keyed by the category label. Lowercase, no spaces -- safe as a CLI
# token and a screen id. Kept here (with the label) so the CLI, the C model, and this emitter
# all speak the same identifiers.
CATEGORY_KEYS: dict[str, str] = {
    "Web": "web",
    "Mail": "mail",
    "HTML": "html",
    "Music": "music",
    "Video": "video",
    "Photos": "photos",
    "Word": "word",
    "Spreadsheet": "spreadsheet",
    "PDF": "pdf",
    "Source Code": "source-code",
    "File Manager": "file-manager",
    "Plain Text": "plain-text",
    "Calculator": "calculator",
    "Terminal": "terminal",
}

# The CURATED handlers per category -- the Azzio shipped default FIRST, then any other apps
# Azzio itself ships that fit the category. This is the SEED, not the whole list: at RUNTIME
# the TUI (and `azzio default-applications candidates`) resolves the OFFERED handlers as the
# union of {these curated ids that are actually installed} and {every OTHER installed .desktop
# that declares this category's MIME type in its MimeType=}. So the list is SELF-RESOLVING --
# install Firefox and firefox.desktop appears under Web/HTML/PDF automatically; remove it and it
# disappears -- WITHOUT it being hard-listed here (the user does not ship Firefox and asked for
# the list to update itself when apps come and go). FIREFOX IS DELIBERATELY NOT LISTED: Azzio
# ships only LibreWolf; Firefox surfaces purely via the installed+MIME resolution when present.
# The curated seed guarantees the shipped choices are always offered (and, for the non-MIME
# categories Calculator/Terminal that have no MIME to resolve against, it is the whole list). The
# `set` verb still accepts ANY installed .desktop (see
# default_applications_command_line_interface._da_set). A test
# asserts the first curated candidate of each category equals that category's CATEGORIES default.
CANDIDATES: dict[str, tuple[str, ...]] = {
    "Web": ("librewolf.desktop",),
    "Mail": (),  # no mail client shipped -- nothing to pick
    "HTML": ("librewolf.desktop", "org.gnome.gedit.desktop"),
    "Music": ("vlc.desktop",),
    "Video": ("vlc.desktop",),
    "Photos": ("xviewer.desktop", "gimp.desktop", "feh.desktop"),
    "Word": ("libreoffice-writer.desktop",),
    "Spreadsheet": ("libreoffice-calc.desktop",),
    "PDF": ("librewolf.desktop",),
    "Source Code": ("org.gnome.gedit.desktop", "vim.desktop"),
    "File Manager": ("thunar.desktop",),
    "Plain Text": ("org.gnome.gedit.desktop", "vim.desktop"),
    "Calculator": ("qalculate-gtk.desktop",),
    "Terminal": ("kitty.desktop",),
}

# The ONE .desktop directory DISCLOSED to the user on the Default Applications screens: the
# single user-writable drop-in, ~/.local/share/applications. The user pushed back HARD on the
# old three-path disclosure ("~/.local/share, /usr/local/share, /usr/share -- applications") --
# it read as "to add an app by hand, copy the same file into three places", which is nonsense.
# There is exactly ONE place a user ever needs to drop or override a .desktop: their own
# ~/.local/share/applications (it wins over the system dirs anyway, being first in XDG order).
# The two SYSTEM dirs (/usr/local/share, /usr/share) are package-managed -- never a manual drop
# target -- so they are not disclosed. IMPORTANT: this only collapses the DISPLAYED text; the
# live candidate RESOLUTION (default_applications_command_line_interface._da_desktop_dirs / the C az_da_dirs) still
# scans every XDG dir (honouring $XDG_DATA_HOME / $XDG_DATA_DIRS), so an app installed to
# /usr/share still appears as a choice. Single source of truth: the CLI mirror, the C model's
# disclosure subtitle (AZ_DA_DIRS_LINE), and the tests all name THIS one path.
DESKTOP_DIR_DISPLAY = "~/.local/share/applications"


def category_by_key(key: str) -> tuple[str, str, str, tuple[str, ...]] | None:
    """Return the CATEGORIES row (group, label, desktop_id, mimes) for a category key, or None."""
    for row in CATEGORIES:
        _group, label, _desktop_id, _mimes = row
        if CATEGORY_KEYS.get(label) == key:
            return row
    return None


def representative_mime(label: str) -> str:
    """The ONE MIME type used to query/set a category's default via xdg-mime (the first MIME in
    its CATEGORIES row). Empty for the non-MIME categories (Mail/Terminal/Calculator/File
    Manager has inode/directory) -- those are handled specially by the CLI."""
    for _group, lbl, _desktop_id, mimes in CATEGORIES:
        if lbl == label:
            return mimes[0] if mimes else ""
    return ""


def terminal_user_interface_categories() -> list[tuple[str, str, str, str]]:
    """Return the ordered rows the TUI's Default Applications screen shows, as
    (group, label, key, default_desktop_id) -- derived from CATEGORIES (the single source of
    truth). The C model and the CLI are pinned to this order/labels by tests."""
    out: list[tuple[str, str, str, str]] = []
    for _group, label, desktop_id, _mimes in CATEGORIES:
        out.append((_group, label, CATEGORY_KEYS[label], desktop_id))
    return out


def mimeapps_list() -> str:
    """Return ~/.config/mimeapps.list -- the freedesktop default-applications file. One
    `mimetype=desktop_id` line per (mime, handler) in mime_defaults(), under the
    [Default Applications] group. GTK/XDG apps read this to choose the handler; `xdg-mime
    query default <mime>` returns these."""
    lines = [
        "# Azzio default applications. Generated by packages/azzio/default_applications.py",
        "# (edit the Python, not this file). The [Default Applications] group is the",
        "# freedesktop mimetype -> handler map every GTK/XDG app consults.",
        "[Default Applications]",
    ]
    for mime, desktop_id in mime_defaults():
        lines.append(f"{mime}={desktop_id}")
    return "\n".join(lines) + "\n"


def helpers_rc() -> str:
    """Return ~/.config/xfce4/helpers.rc -- the Xfce/exo preferred-applications selection.
    Only TerminalEmulator is set (to kitty), which is what the file manager's "Open Terminal Here"
    (`exo-open --launch TerminalEmulator`) uses. Paired with the kitty helper .desktop
    (kitty_helper_desktop()) so exo knows how to launch it."""
    return (
        "# Azzio Xfce preferred applications. Generated by "
        "packages/azzio/default_applications.py\n"
        "# (edit the Python, not this file). TerminalEmulator=kitty is what the file manager's\n"
        "# 'Open Terminal Here' (exo-open --launch TerminalEmulator) opens.\n"
        f"TerminalEmulator={TERMINAL_BIN}\n"
    )


def kitty_helper_desktop() -> str:
    """Return /usr/share/xfce4/helpers/kitty.desktop -- the exo TerminalEmulator HELPER that
    teaches exo how to launch kitty (an X-XFCE-Helper in the TerminalEmulator category).
    Required for `exo-open --launch TerminalEmulator` to resolve to kitty (VERIFIED in the VM:
    with this helper + helpers.rc, exo-open opens kitty, honouring --working-directory)."""
    return f"""\
[Desktop Entry]
# Azzio kitty TerminalEmulator helper for exo. Generated by
# packages/azzio/default_applications.py (edit the Python, not this file). Lets
# `exo-open --launch TerminalEmulator` (the file manager's Open Terminal Here) open kitty.
Version=1.0
Encoding=UTF-8
Type=X-XFCE-Helper
X-XFCE-Category=TerminalEmulator
Name=kitty
Icon=kitty
X-XFCE-Commands={TERMINAL_BIN}
X-XFCE-CommandsWithParameter={TERMINAL_BIN} %s
"""


_CONF = 0o644


def emit_plan() -> list[dict]:
    """Return the emit plan for the default applications: mimeapps.list + helpers.rc (HOME,
    skel-mirrored) and the kitty exo helper .desktop (root system file). Shape matches the
    other emit_plan() modules (builder/dest/mode/owner). Returns FRESH dicts."""
    return [
        {
            "builder": mimeapps_list,
            "dest": MIMEAPPS_PATH,
            "mode": _CONF,
            "owner": "home",
        },
        {
            "builder": helpers_rc,
            "dest": HELPERS_RC_PATH,
            "mode": _CONF,
            "owner": "home",
        },
        {
            "builder": kitty_helper_desktop,
            "dest": KITTY_HELPER_PATH,
            "mode": _CONF,
            "owner": "root",
        },
    ]
