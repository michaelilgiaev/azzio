"""Live file manager sidebar -- keep ~/.config/gtk-3.0/bookmarks in sync with the ACTUAL home
directory contents at runtime, AND make a running file manager's "Places" pane reflect the
change LIVE (PROMPT step five item 6: Places must POPULATE and UPDATE as directories are
added/removed, with no manual refresh).

THE PROBLEM (two layers).
  1. GTK bookmarks are a STATIC file. packages/file_manager/sidebar builds it once at BUILD time from
     home_directory's curated set, so a folder/file/symlink the user creates in $HOME AFTER
     install never shows up in the shortcuts pane on its own. "Anything added to home shows up"
     needs a RUNTIME regenerator.
  2. Even once the file IS regenerated, a running file manager only refreshes its Places pane if
     it NOTICES the file changed. Its ThunarShortcutsModel watches the bookmarks file with a
     per-file GFileMonitor (inotify). If the regenerator installs the new file with an atomic
     `mv` (a fresh inode renamed over the path), inotify keeps watching the OLD, now-unlinked
     inode and NEVER sees the new content -- so Places stays stale until the file manager
     restarts (the reported bug). The regenerator must therefore rewrite the SAME inode IN PLACE
     so an IN_MODIFY/IN_CLOSE_WRITE fires and the file manager reloads.

THE MECHANISM. A tiny POSIX-sh helper (azzio-sidebar-sync) that regenerates the bookmarks file
from the CURRENT top-level home contents, in the SAME required order as the static seed
(PROMPT: directories -> files -> symbolic links -> "Trash" LAST), pointing symlink bookmarks at
their RESOLVED targets (consistent with the resolved-path behaviour everywhere else). It runs in
two modes:
  * `azzio-sidebar-sync` (once)   -- regenerate the bookmarks now.
  * `azzio-sidebar-sync --watch`  -- regenerate now, then loop: every ~2s recompute a cheap
                                      SIGNATURE of the top-level home listing (each entry's name
                                      + type + symlink target) and regenerate only when it
                                      changed. A signature (not the dir mtime) catches an
                                      add/remove/retarget even within the same clock second
                                      (mtime is 1s-granular) and avoids a needless rewrite when
                                      nothing changed. Dependency-free (no inotify-tools in the
                                      manifest; the PROMPT explicitly allows "a lightweight
                                      periodic" watcher) and matches the repo's other
                                      autostart-launched helpers.
  In BOTH modes the install is an IN-PLACE rewrite of the existing bookmarks inode (only when the
  content actually changed), NOT an atomic rename -- so the file manager's file monitor fires and
  Places refreshes live (see regen()'s install block for the full rationale).

WIRING. The OpenBox session autostart (packages/openbox) launches
`azzio-sidebar-sync --watch &` -- both the LIVE and the INSTALLED autostart (via the shared
_openbox_autostart_common block), so additions are tracked on both. The script is a root-owned
system helper (like the other /usr/local/lib/azzio tools); it operates on the invoking user's
own $HOME, so it needs no privilege.

ORDERING (matches home_directory.sidebar_entries + the static seed):
  1. real DIRECTORIES  (alphabetical), EXCEPT Desktop (the file manager's built-in place already
     provides it at the same path -- adding ours would duplicate it, same reason sidebar.py skips
     it) and EXCEPT home_directory.SIDEBAR_SKIP ("Ignore", which the PROMPT keeps off the sidebar).
  2. regular FILES     (alphabetical).
  3. SYMLINKS          (alphabetical), each bookmarked at its RESOLVED target, EXCEPT "Trash", and
     ONLY if the link resolves to a real dir/file (a dangling link is skipped -- the PROMPT lists
     "Symbolic Links(of directories, files, or .html files)", i.e. links to a real target).
  4. "Trash" LAST      (it is a symlink but the spec pins it to the very end).
The "main" user row at the TOP of the sidebar is NOT emitted here: it is the file manager's
built-in Home place (settings.HIDDEN_BOOKMARKS keeps it visible, displayed as the home basename
"main"), which sits above this GTK-bookmark group. This scan only walks the CHILDREN of $HOME.
Hidden entries (dotfiles) are skipped -- the curated convenience symlinks (Cache/Config/...) are
NON-hidden names, so the surfaced set matches the intent without dumping every dotfile.
"""

from __future__ import annotations

from . import home_directory

# The live user's home (matches openbox.HOME / the airootfs /home/main tree). The script itself
# uses the runtime "$HOME", so it is correct for any user that inherited the config via skel.
HOME = "/home/main"

# The sync helper -- a root-owned system script next to the other /usr/local/lib/azzio tools.
# It acts on the INVOKING user's $HOME (no privilege needed).
SYNC_SCRIPT_DEST = "/usr/local/lib/azzio/azzio-sidebar-sync"

# The bookmarks file it regenerates (the same path the static seed writes).
GTK_BOOKMARKS_PATH = f"{HOME}/.config/gtk-3.0/bookmarks"

# No "main"/Home bookmark is emitted -- the "main" user row at the top is the built-in Home place
# (settings.HIDDEN_BOOKMARKS keeps it VISIBLE now, displayed as the home basename "main"), which
# renders above this GTK-bookmark group. The regenerated file starts straight at the directories.

# Top-level names the runtime scan skips, so they never reach the sidebar:
#   * "Desktop"        -- the file manager's built-in place already provides it (see sidebar.py);
#                         adding our own would duplicate it.
#   * home_directory.SIDEBAR_SKIP ("Ignore", ...) -- names the PROMPT keeps off the sidebar
#                         ("ignore the directory 'Ignore/'"). Sourced from home_directory so the
#                         runtime scan and the build-time seed apply the SAME skip set (no drift).
# "main" is NOT here and needs no handling: the user row at the top is the built-in Home place
# (settings.HIDDEN_BOOKMARKS keeps it visible), not a scanned bookmark -- the scan only walks the
# CHILDREN of $HOME, never $HOME itself.
SKIP_NAMES = ("Desktop", *sorted(home_directory.SIDEBAR_SKIP))

# The Trash shortcut name, pinned to the very end of the ordering (home_directory.TRASH_LINK_NAME).
TRASH_NAME = home_directory.TRASH_LINK_NAME

# How often --watch re-checks the home dir mtime (seconds). Small enough to feel instant, large
# enough to be free.
WATCH_INTERVAL_SECS = 2


def sync_script() -> str:
    """Return the azzio-sidebar-sync POSIX-sh helper. Regenerates the GTK bookmarks from the
    live top-level home contents in the required order (dirs -> files -> symlinks -> Trash last),
    symlinks resolved. `--watch` polls a cheap signature of the home listing and regenerates on
    change (an add/remove/retarget, caught even within the same clock second).

    Pure POSIX sh + coreutils/sed (readlink/realpath/printf/sort/cmp/cat/sed; entries are
    enumerated with a shell GLOB, not `ls`, so spaced names are safe) -- all in `base`/coreutils
    on the ISO, no inotify-tools. The bookmark URI for a symlink is `realpath -m` of the link (so
    the location bar shows the real target); for a dir/file it is the entry path, percent-encoded
    so a spaced path is a valid single-token URI. Every non-blank line is a bookmark (the GTK
    format has no comments), so the file carries none. The bookmarks are installed by an IN-PLACE rewrite of the existing inode
    (only when the content changed) so a running file manager's per-file monitor fires and Places
    refreshes live -- NOT an atomic rename, which would leave the monitor on a stale inode."""
    skip_case = "|".join(SKIP_NAMES)          # e.g. "Desktop"
    trash = TRASH_NAME
    interval = WATCH_INTERVAL_SECS
    return f"""\
#!/bin/sh
# azzio-sidebar-sync -- regenerate ~/.config/gtk-3.0/bookmarks from the CURRENT top-level home
# contents so anything the user adds to $HOME shows up in the file manager's sidebar (PROMPT).
# Generated by packages/file_manager/live_sidebar (edit the Python, not this file). Order: real
# dirs -> files -> symlinks -> "Trash" last; symlink bookmarks point at their resolved target.
# Runs as the invoking user on their own $HOME (no privilege). `--watch` polls a signature of the
# home listing and rewrites the bookmarks IN PLACE on change so the file manager's Places pane
# refreshes live.
set -u

BM="$HOME/.config/gtk-3.0/bookmarks"

# file:// URI for an absolute path. The GTK bookmarks format parses each line as
# "<URI-up-to-first-space> <label>", so a path containing a SPACE must be percent-encoded in
# the URI (a literal space would truncate the URI token and mangle the line). We encode the
# characters that would break the URI/format: '%' FIRST (so we never double-encode an escape we
# just wrote), then space, '#', '?'. The curated Azzio names have none of these, so for them
# this is a no-op; it only matters for a user-added folder like "My Documents"
# (-> file:///home/main/My%20Documents). Label stays the raw name (labels MAY contain spaces).
uri() {{
    printf 'file://%s' "$(printf '%s' "$1" \\
        | sed -e 's/%/%25/g' -e 's/ /%20/g' -e 's/#/%23/g' -e 's/?/%3F/g')"
}}

regen() {{
    dirs=""; files=""; links=""; trash=""
    # Enumerate NON-hidden top-level entries of $HOME (the convenience symlinks are non-hidden).
    # Iterate with a PATHNAME GLOB ("$HOME"/*), NOT `for name in $(ls)`: an unquoted command
    # substitution word-splits on IFS, so an entry named "My Documents" would be seen as two
    # bogus tokens (dropped or corrupting the list) -- a glob yields ONE word per real entry, so
    # spaces (and other odd characters) in a folder name are handled correctly. `*` skips
    # dotfiles by default (which we want). If the dir is empty the glob stays the literal
    # "$HOME/*" -- the `[ -e ]`/`[ -L ]` tests below fail for it, so it is skipped.
    for path in "$HOME"/*; do
        [ -e "$path" ] || [ -L "$path" ] || continue   # skip the literal glob on an empty dir
        name=${{path##*/}}                                 # basename (space-safe)
        # Skip entries the file manager's built-in places already provide (Desktop).
        case "$name" in
            {skip_case}) continue ;;
        esac
        if [ -L "$path" ]; then
            # A symlink. The sidebar lists only symlinks "of directories, files, or .html files"
            # (PROMPT) -- i.e. links that resolve to a real dir/file. A DANGLING link (target
            # missing) would be a broken sidebar row, so skip it. `[ -e ]` follows the link, so it
            # is TRUE for a dir/file target and FALSE for a dangling one.
            [ -e "$path" ] || continue
            # Bookmark its RESOLVED target so the location bar shows the real path.
            target=$(realpath -m -- "$path" 2>/dev/null || printf '%s' "$path")
            line="$(uri "$target") $name"
            if [ "$name" = "{trash}" ]; then
                trash="$line"                 # pin Trash to the very end
            else
                links="$links$line
"
            fi
        elif [ -d "$path" ]; then
            dirs="$dirs$(uri "$path") $name
"
        elif [ -e "$path" ]; then
            files="$files$(uri "$path") $name
"
        fi
    done
    # Sort each group alphabetically by label (field 2). Emit: dirs, files, links, then Trash
    # last ("main"/Home is the built-in place, not emitted here). `sort` on empty input
    # is a no-op. Assemble into a TEMP file first (so a `sort` hiccup never leaves a half-built
    # bookmarks file), then install it -- see the in-place write below.
    tmp="$BM.azzio.$$"
    mkdir -p "$(dirname "$BM")"
    if {{
        [ -n "$dirs" ]  && printf '%s' "$dirs"  | sort -k2
        [ -n "$files" ] && printf '%s' "$files" | sort -k2
        [ -n "$links" ] && printf '%s' "$links" | sort -k2
        [ -n "$trash" ] && printf '%s\\n' "$trash"
        # Force a success exit for the group: the last `[ -n ... ] && printf` above is FALSE
        # (returns 1) whenever that group is empty (e.g. no Trash symlink), which would
        # otherwise make the whole block "fail" and skip the install below, leaving no bookmarks.
        true
    }} > "$tmp" 2>/dev/null; then
        # LIVE-UPDATE FIX (PROMPT step five item 6). Only install when the content actually
        # CHANGED, and install by REWRITING $BM's EXISTING INODE IN PLACE -- NOT by `mv`.
        #
        # WHY IN PLACE. A running file manager (its ThunarShortcutsModel) watches the bookmarks
        # file with a per-file GFileMonitor (inotify). An atomic `mv tmp $BM` REPLACES the inode:
        # inotify keeps its watch on the OLD (now-unlinked) inode and NEVER sees the new file,
        # so Places would not refresh until the file manager restarts (the reported bug).
        # Truncating and rewriting the SAME inode (`cat > "$BM"`) fires IN_MODIFY/IN_CLOSE_WRITE
        # on the watched path, which GFileMonitor reports as CHANGED -> the file manager reloads
        # Places live. The file is tiny (a dozen lines) so the rewrite is effectively atomic, and
        # the file manager reloads on the post-write CLOSE event, not mid-write.
        if [ ! -e "$BM" ] || ! cmp -s "$tmp" "$BM"; then
            cat "$tmp" > "$BM" 2>/dev/null || cp -f "$tmp" "$BM" 2>/dev/null
        fi
        rm -f "$tmp" 2>/dev/null
    else
        rm -f "$tmp" 2>/dev/null
    fi
}}

# A cheap signature of the top-level home listing: the entry names plus a type marker (dir /
# symlink / other), sorted. Detecting change via this (not the dir mtime) reliably catches an
# add/remove/retarget even WITHIN the same clock second (mtime has 1s granularity on many
# filesystems, so an mtime compare can miss a fast change) -- and it also skips a needless
# rewrite when nothing changed (so the sidebar does not flicker every poll). Iterates with a
# GLOB (space-safe, like regen()); the per-entry test picks L/d/f. `*` skips dotfiles and stays
# literal on an empty dir (the `[ -e ]`/`[ -L ]` guard drops that case).
home_sig() {{
    for path in "$HOME"/*; do
        [ -e "$path" ] || [ -L "$path" ] || continue   # skip the literal glob on an empty dir
        name=${{path##*/}}
        if [ -L "$path" ]; then t=L; elif [ -d "$path" ]; then t=d; else t=f; fi
        # also fold in a symlink's target so a re-pointed link triggers a refresh.
        if [ "$t" = L ]; then
            printf '%s:%s>%s\\n' "$t" "$name" "$(readlink -- "$path" 2>/dev/null)"
        else
            printf '%s:%s\\n' "$t" "$name"
        fi
    done | sort
}}

regen
if [ "${{1:-}}" = "--watch" ]; then
    last=$(home_sig)
    while :; do
        sleep {interval}
        now=$(home_sig)
        if [ "$now" != "$last" ]; then
            last="$now"
            regen
        fi
    done
fi
"""


_EXEC = 0o755


def emit_plan() -> list[dict]:
    """Return the emit plan for the live-sidebar sync helper: a single root-owned executable
    system script. (It is wired into session startup by packages/openbox's autostart.)"""
    return [
        {
            "builder": sync_script,
            "dest": SYNC_SCRIPT_DEST,
            "mode": _EXEC,
            "owner": "root",
        },
    ]
