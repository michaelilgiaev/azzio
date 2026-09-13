"""Home-directory LAYOUT -- the single source of truth for the user's top-level folders
and convenience symlinks (and, by reference, the file manager's sidebar shortcuts).

This is a SUBMODULE of the file_manager package (packages/file_manager/home_directory.py): the folder
layout and the file manager's sidebar are built from the SAME list, so the data lives right next
to the sidebar code that consumes it. compiler._emit_homedir imports it as packages.file_manager.home_directory.

WHAT THIS SHIPS. A fixed set of top-level DIRECTORIES in the home directory (Desktop,
Downloads, Vault, Documents, Ignore, Music, Pictures, Projects, Shared, Videos) plus a handful
of convenience SYMLINKS that surface otherwise-hidden dot locations as plain top-level names:

    Trash  -> .local/share/Trash/files   (the XDG trash "files" dir)
    Cache  -> .cache
    Config -> .config
    Bashrc -> .bashrc
    Local  -> .local
    SSH    -> .ssh

WHY THIS IS A MODULE AND NOT JUST COMPILER CODE. The SAME list drives two things: the
directories/symlinks created on disk, AND the file manager's sidebar shortcuts (the shortcuts
pane lists exactly this set). Keeping the list here, as data, means the folder layout and the
file-manager sidebar can never drift -- packages/file_manager/ imports LAYOUT from this module
and builds ~/.config/gtk-3.0/bookmarks (the GTK bookmarks the file manager reads) from it. Add a
folder here and it appears both on disk and in the sidebar.

DIRECTORIES vs CONTENT FILES. Unlike the emit_plan() app packages, this data emits no
FILE CONTENT -- directories and symlinks are not text. So it does NOT expose emit_plan()
(the builder/dest/mode/owner shape compiler._emit_apps consumes). Instead it exposes plain
data (DIRECTORIES, LINKS, TRASH_DIRS) and compiler._emit_homedir() walks it with
emit.mkdir()/emit.link(). The layout is created in BOTH /home/main (the live user) AND
/etc/skel (so a Calamares-created user inherits the same tree).

RELATIVE SYMLINK TARGETS (load-bearing). Every symlink target is RELATIVE (".cache", not
"/home/main/.cache"). This is required so the SAME link is valid under /etc/skel: an
absolute /home/main/... target would dangle in /etc/skel and, after Calamares copies skel
into /home/<newuser>, would still point at /home/main. A relative target resolves against
the link's own directory, so "Cache -> .cache" is correct in every home it lands in.

THE TRASH CHAIN. "Trash -> .local/share/Trash/files" would DANGLE unless the XDG trash
spec dirs exist. So TRASH_DIRS creates the chain (.local/share/Trash/files AND
.local/share/Trash/info -- the spec requires both; `info` holds the .trashinfo metadata)
before the symlink is made, so Trash resolves to a real directory from first login.

THE .ssh DIRECTORY. Same idea for "SSH -> .ssh": .ssh was previously created only at RUNTIME
(the sshd bring-up), so the SSH shortcut dangled on a fresh system. SSH_DIR pre-creates .ssh
(at SSH_DIR_MODE = 0700, since sshd refuses a group/world-readable ~/.ssh) so it, too, resolves
from first login and is shipped by default.

Pure standard library (only data + the resolved-path helper the file manager's sidebar uses).
"""

from __future__ import annotations

# The live user's home (matches openbox.HOME / the airootfs /home/main tree). The layout is
# created here for the live user AND mirrored into /etc/skel by compiler._emit_homedir.
HOME = "/home/main"

# --- The top-level directories -------------------------------------------------
# Created in the home directory (and /etc/skel). ORDER is meaningful: it is the order the
# file manager's sidebar lists them (packages/file_manager builds bookmarks from this list), so
# the folders appear in the sidebar top-to-bottom exactly as written here. Names only (no
# leading path) -- they are created directly under the home dir.
DIRECTORIES: tuple[str, ...] = (
    "Desktop",
    "Downloads",
    "Vault",
    "Documents",
    "Ignore",
    "Music",
    "Pictures",
    "Projects",
    "Shared",
    "Videos",
)

# --- The convenience symlinks --------------------------------------------------
# name -> RELATIVE target (see the module docstring: relative so the link is valid under
# /etc/skel and in every copied-out home). Each is created directly under the home dir as
# `name -> target`, resolving against the home dir. ORDER is meaningful (sidebar order,
# after the directories above).
LINKS: tuple[tuple[str, str], ...] = (
    ("Trash", ".local/share/Trash/files"),
    ("Cache", ".cache"),
    ("Config", ".config"),
    ("Bashrc", ".bashrc"),
    ("Local", ".local"),
    ("SSH", ".ssh"),
)

# --- Absolute-target symlinks (system runtime locations) -----------------------
# A SEPARATE class of symlink whose target is an ABSOLUTE system path, NOT a relative
# home-relative one like LINKS above. "Mounts -> /run/media" surfaces the udisks
# removable-media mount ROOT (the parent that holds every user's per-user mount dir --
# /run/media/<user> -- where the desktop auto-mounts USB sticks, SD cards, etc.) as a
# plain top-level folder (PROMPT). It points at the /run/media PARENT, not the
# /run/media/main per-user subdir, so the shortcut is correct for ANY user (it is not
# tied to `main`) and shows the whole mount tree. The target is absolute on purpose:
# /run/media is the SAME path in every context (it is not under $HOME, so there is no
# relative form), so -- unlike LINKS -- it is created in the LIVE /home/main ONLY, not
# mirrored into /etc/skel (skel is copied per-user, and an absolute system path needs no
# per-user rewrite, but keeping it out of skel matches the "runtime-location" class and
# avoids a skel entry that predates the mount root existing). It appears in the sidebar
# (with its own mount icon) among the symlink group, before the pinned Trash.
ABSOLUTE_LINKS: tuple[tuple[str, str], ...] = (
    ("Mounts", "/run/media"),
)

# The Trash shortcut's name. It is itself a symlink but the spec pins it to the very END of the
# sidebar/view ordering (after every other symlink), so both the static sidebar_entries() and
# the runtime live-sidebar sync special-case it.
TRASH_LINK_NAME = "Trash"

# --- The .ssh directory (pre-created so the SSH symlink does not dangle) --------
# "SSH -> .ssh" (in LINKS) would DANGLE on a freshly built/installed system: the .ssh dir was
# previously created only at RUNTIME, when `azzio --sshd-hypervisor` first ran (install -d -m
# 700). PROMPT: ship .ssh in /home/main BY DEFAULT. So, exactly like the TRASH chain below, the
# target dir is created up front (relative to the home dir, in both /home/main and /etc/skel) --
# now the SSH shortcut resolves to a real directory from first login, before sshd is ever
# brought up. SSH_DIR_MODE pins it to 0700: sshd (and the runtime bring-up) refuse a
# group/world-accessible ~/.ssh, so the build-time dir must already be private.
SSH_DIR = ".ssh"
SSH_DIR_MODE = 0o700

# --- Sidebar pin + skip --------------------------------------------------------
# The user's home sits at the TOP of the sidebar, shown as "main" (PROMPT: "add the user to the
# top of the sidebar ... simply name it 'main'"). This is the file manager's BUILT-IN Home place
# (group PLACES_DEFAULT, sort_id 0), whose display name is the home basename -- "main" for
# /home/main. It already renders above the GTK-bookmark group, so we get "main at the top" simply
# by NOT hiding it: file_manager/settings.HIDDEN_BOOKMARKS no longer lists file:///home/main.
# (It is deliberately NOT a GTK bookmark -- a bookmark would land below the built-in Desktop and
# duplicate the built-in Home.) The label is asserted against the home basename in the tests.
SIDEBAR_USER_LABEL = "main"

# Top-level names that are created on disk but kept OUT of the sidebar. "Ignore" is a real folder
# the layout still creates (so it stays in DIRECTORIES, the on-disk source of truth), but the
# PROMPT says the sidebar must "ignore the directory 'Ignore/'", so sidebar_entries() (and the
# runtime scan in live_sidebar) drop it. Kept as data so the on-disk layout and the sidebar filter
# never drift.
SIDEBAR_SKIP: frozenset[str] = frozenset({"Ignore"})

# NOTE: there is NO ".home-directory" symlink or "Home Directory" sidebar bookmark anymore. The
# user deleted the "Home Directory" entry from the file manager's Places sidebar ("just delete
# it, we dont actually need it there is a home button"), so the previous distinct-URI symlink
# trick that backed that bookmark is gone. The built-in username Home shortcut is now SHOWN (it is
# the "main" pin above) -- file_manager/settings.HIDDEN_BOOKMARKS no longer hides file:///home/main
# (see SIDEBAR_USER_LABEL above); the file manager's Home button still navigates home as well.

# --- The XDG trash chain -------------------------------------------------------
# The trash spec's two required dirs, created (relative to the home dir) BEFORE the
# "Trash" symlink so it resolves to a real directory instead of dangling. `files` holds the
# trashed files (the Trash symlink points here); `info` holds the matching .trashinfo
# metadata. Created in both /home/main and /etc/skel.
TRASH_DIRS: tuple[str, ...] = (
    ".local/share/Trash/files",
    ".local/share/Trash/info",
)

# --- Extra (non-sidebar) directories -------------------------------------------
# Directories created in the home layout that are NOT part of the sidebar shortcut set (so they
# are deliberately kept OUT of DIRECTORIES above, which drives the sidebar). Currently EMPTY.
#
# ~/Templates used to live here (it held the file manager's "Create Document" template set). The
# user asked for it to be deleted entirely ("Delete that 'Templates/' directory, idk what it's
# created in the first place"), so it is no longer created, the template FILE set is gone, and the
# sibling packages/file_manager/templates now ships ONLY the XDG user-dirs pointer (with
# XDG_TEMPLATES_DIR back at the stock $HOME/ default). The tuple is kept (empty) so
# compiler._emit_homedir's loop over it, and the tests that reference it, stay valid.
EXTRA_DIRECTORIES: tuple[str, ...] = ()


def resolved_home_path(rel_or_link_target: str) -> str:
    """Return the ABSOLUTE, symlink-resolved home path for a layout entry's target.

    Used by packages/file_manager to point each sidebar bookmark at the REAL location (so
    entering a shortcut shows the resolved path in the file manager's location bar, not the
    symlink path). A plain directory name like "Config" whose link target is ".config" resolves to
    "/home/main/.config"; a directory like "Downloads" resolves to "/home/main/Downloads".
    The target is relative to HOME, so this just joins it onto HOME and normalizes (no
    filesystem access -- pure string, correct for the build host and the target alike)."""
    import posixpath

    return posixpath.normpath(f"{HOME}/{rel_or_link_target}")


def sidebar_entries() -> list[tuple[str, str]]:
    """Return the sidebar shortcut list as (label, absolute_resolved_target) pairs, in the
    required display ORDER (PROMPT: directories -> files -> symbolic links -> "Trash" LAST).
    This is the single list packages/file_manager turns into the GTK bookmarks file and the file
    manager renders in the shortcuts pane -- so the sidebar and the on-disk layout are the same
    set, by construction, in the same order.

    The curated layout has: real DIRECTORIES (the dirs group), no plain files, and the LINKS
    (all symlinks -- the symlinks group), of which "Trash" is forced to the very end. So the
    order is: DIRECTORIES, then the non-Trash LINKS, then Trash. (The live sidebar sync applies
    the identical dirs -> files -> symlinks -> Trash-last ordering to the ACTUAL home contents at
    runtime; see packages/file_manager/live_sidebar.)

    For a plain directory the target is the directory itself (Desktop ->
    /home/main/Desktop). For a symlink the target is what the link resolves to (Config ->
    /home/main/.config, Trash -> /home/main/.local/share/Trash/files), so opening the
    shortcut shows the real path rather than the /home/main/Config symlink path."""
    entries: list[tuple[str, str]] = []
    # NOTE: the "main" (user home) pin at the TOP of the sidebar is NOT a GTK bookmark -- it is the
    # file manager's built-in Home place (group PLACES_DEFAULT, sort_id 0, displayed as the home
    # basename "main"), which already sits above the GTK-bookmark group. We surface it by NOT
    # hiding it (file_manager/settings.HIDDEN_BOOKMARKS no longer lists file:///home/main). Adding
    # a "main" bookmark here instead would land it BELOW the built-in Desktop (wrong position) and
    # duplicate the built-in Home. See SIDEBAR_USER_LABEL.
    # 1. Real directories (the dirs group), EXCEPT any in SIDEBAR_SKIP (e.g. "Ignore", which is
    #    created on disk but kept off the sidebar per the PROMPT).
    for name in DIRECTORIES:
        if name in SIDEBAR_SKIP:
            continue
        entries.append((name, resolved_home_path(name)))
    # 2. Files: the curated set has none. (The live sync inserts real files here at runtime.)
    # 3. Symlinks (the LINKS), EXCEPT "Trash" (pinned last) and any SIDEBAR_SKIP name.
    for name, target in LINKS:
        if name == TRASH_LINK_NAME or name in SIDEBAR_SKIP:
            continue
        entries.append((name, resolved_home_path(target)))
    # 3b. Absolute-target symlinks (e.g. "Mounts" -> /run/media): same symlink group, but the
    #     target is an absolute system path, so it is used VERBATIM (not joined onto HOME).
    for name, abs_target in ABSOLUTE_LINKS:
        if name in SIDEBAR_SKIP:
            continue
        entries.append((name, abs_target))
    # 4. "Trash" LAST (it is itself a symlink, forced to the very end per the spec).
    entries.append((TRASH_LINK_NAME, resolved_home_path(dict(LINKS)[TRASH_LINK_NAME])))
    return entries
