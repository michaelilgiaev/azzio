"""packages.file_manager.live_sidebar -- the runtime GTK-bookmarks sync (PROMPT: additions to
the home dir must show up in the file manager's sidebar automatically).

Why these tests matter: the sidebar is otherwise STATIC (a build-time file), so "anything the
user adds shows up" needs a runtime helper. These pin: the required ORDER (dirs -> files ->
symlinks -> Trash last), that symlink bookmarks resolve to their targets, that the helper is
wired into session startup (both live + installed autostart), and -- by actually RUNNING the
generated shell script against a fixture home -- that its enumeration/ordering is correct and it
tracks additions AND removals.

They also pin the LIVE-UPDATE fix. Two things must both hold for Places to refresh live:

  1. The helper rewrites the regenerated bookmarks IN PLACE (same inode across regenerations),
     not via an atomic rename, so the file monitor watching it fires a CHANGED event. (On
     the file manager 4.20 / GTK 3.24 the monitor is a GFileMonitor on the bookmarks FILE, which
     the GLib inotify backend implements by watching the PARENT DIRECTORY and filtering for the
     `bookmarks` basename -- verified on the live hypervisor: the file manager's inotify fd watches the
     ~/.config/gtk-3.0 dir inode, not the file inode. An in-place `cat > "$BM"` fires
     IN_MODIFY/IN_CLOSE_WRITE for that basename -> GLib CHANGED -> GtkBookmarksManager reloads.)
     And it does NOT rewrite when nothing changed (no needless CHANGED events / flicker).

  2. The watcher must actually BE RUNNING. It is launched from the OpenBox autostart behind a
     `[ -x '/usr/local/lib/azzio/azzio-sidebar-sync' ]` guard -- so it only starts if the
     installed script is EXECUTABLE. archiso's squashfs normalizes overlay file modes to 0644
     unless a path is pinned in profile.FILE_PERMISSIONS; the script was NOT pinned, so it
     shipped 0644, the `[ -x ]` guard failed, the --watch daemon never launched, and Places
     never updated -- the reported bug (confirmed on the live box: the installed script was
     -rw-r--r-- and no sidebar-sync process was running). The pin (0:0:755) is the real fix; the
     in-place-inode logic was already correct but had nothing running to exercise it.
"""

from __future__ import annotations

import os
import subprocess

from packages.file_manager import home_directory
from packages import openbox
from packages.file_manager import live_sidebar, sidebar


# --- static seed ordering (home_directory.sidebar_entries) ------------------

def test_static_sidebar_order_is_dirs_then_symlinks_then_trash_last():
    # PROMPT: directories -> files -> symbolic links -> "Trash" LAST. The curated set has real
    # dirs (DIRECTORIES) then symlinks (LINKS), with Trash pinned to the very end.
    labels = [label for label, _ in home_directory.sidebar_entries()]
    # every real directory comes before every symlink (skipping SIDEBAR_SKIP names like "Ignore",
    # which are kept off the sidebar entirely).
    shown_dirs = [d for d in home_directory.DIRECTORIES if d not in home_directory.SIDEBAR_SKIP]
    last_dir_idx = max(labels.index(d) for d in shown_dirs)
    link_names = [n for n, _ in home_directory.LINKS if n not in home_directory.SIDEBAR_SKIP]
    first_link_idx = min(labels.index(n) for n in link_names)
    assert last_dir_idx < first_link_idx, labels
    # Trash is dead last.
    assert labels[-1] == home_directory.TRASH_LINK_NAME, labels


def test_static_sidebar_bookmarks_put_trash_last_and_no_home():
    bm = sidebar.gtk_bookmarks().splitlines()
    # NO "Home Directory" bookmark anywhere (the user deleted it from the sidebar).
    assert not any(ln.endswith(" Home Directory") for ln in bm)
    assert not any(".home-directory" in ln for ln in bm)
    assert bm[-1].endswith(" Trash")                  # Trash last
    # Trash resolves to the real trash files dir (resolved-path behaviour).
    assert bm[-1].startswith("file:///home/main/.local/share/Trash/files ")


# --- the sync helper string + wiring ----------------------------------------

def test_sync_script_is_wired_into_both_autostarts():
    # The --watch helper is launched from the SHARED autostart block, so it runs on the LIVE
    # and the INSTALLED session (additions tracked on both).
    for au in (openbox.openbox_autostart(), openbox.openbox_autostart_installed()):
        assert live_sidebar.SYNC_SCRIPT_DEST in au
        assert "--watch" in au


def test_sync_path_lock_step_with_openbox():
    # openbox holds the path as a constant (to avoid importing the file manager); it must not drift.
    assert openbox.FILE_MANAGER_SIDEBAR_SYNC == live_sidebar.SYNC_SCRIPT_DEST
    assert live_sidebar.SYNC_SCRIPT_DEST == "/usr/local/lib/azzio/azzio-sidebar-sync"


def test_sync_helper_emitted_root_owned_executable():
    plan = live_sidebar.emit_plan()
    e = next(x for x in plan if x["dest"] == live_sidebar.SYNC_SCRIPT_DEST)
    assert e["owner"] == "root"
    assert e["mode"] == 0o755
    # the whole file_manager plan includes it too.
    from packages import file_manager
    assert live_sidebar.SYNC_SCRIPT_DEST in {x["dest"] for x in file_manager.emit_plan()}


def test_sync_helper_is_pinned_executable_in_the_iso():
    """THE real live-update fix. emit_plan() installs the helper 0755, but archiso's squashfs
    normalizes overlay modes to 0644 unless the path is pinned in profile.FILE_PERMISSIONS -- and
    then the OpenBox autostart's `[ -x '<script>' ]` guard FAILS, the --watch daemon never
    launches, and Places never updates when a folder is added/removed (the reported bug, confirmed
    on the live box where the installed script was -rw-r--r-- and no watcher ran). SAME normalization
    as the compiled azzio/osd/menu-daemon binaries, which are all pinned 0755 for the same reason.
    Pin the sidebar helper 0755 both in the map and in the rendered profiledef.sh."""
    import profile
    assert profile.FILE_PERMISSIONS[live_sidebar.SYNC_SCRIPT_DEST] == "0:0:755"
    assert f'["{live_sidebar.SYNC_SCRIPT_DEST}"]="0:0:755"' in profile.profiledef_sh()


def test_autostart_launch_is_guarded_on_the_exec_bit():
    """The autostart only starts the watcher if the script is EXECUTABLE (`[ -x ... ]`), which is
    exactly why the ISO pin above matters -- a 0644 script silently skips the watcher. Pin that the
    guard is the exec test (so the pin and the guard stay coupled: drop the pin and the watcher
    stops launching)."""
    for au in (openbox.openbox_autostart(), openbox.openbox_autostart_installed()):
        assert f"[ -x '{live_sidebar.SYNC_SCRIPT_DEST}' ]" in au


def test_sync_script_is_valid_posix_sh():
    script = live_sidebar.sync_script()
    assert script.startswith("#!/bin/sh")
    # `sh -n` parses without executing -- catches a syntax slip in the generated script.
    r = subprocess.run(["sh", "-n"], input=script, text=True,
                       stderr=subprocess.PIPE)
    assert r.returncode == 0, r.stderr


# --- FUNCTIONAL: run the generated script against a fixture home -------------

def _run_sync(tmp_home, *args):
    """Write the generated script and run it with HOME=tmp_home; return the bookmarks lines."""
    script = tmp_home / "azzio-sidebar-sync"
    script.write_text(live_sidebar.sync_script())
    env = dict(os.environ, HOME=str(tmp_home))
    subprocess.run(["sh", str(script), *args], env=env, timeout=20, check=False)
    bm = tmp_home / ".config/gtk-3.0/bookmarks"
    return bm.read_text().splitlines() if bm.exists() else []


def test_functional_ordering_dirs_files_symlinks_trash_last(tmp_path):
    # Build a realistic home: real dirs, real files, symlinks (incl Trash + a user-added one),
    # a dotfile (skipped), Desktop (skipped -- built-in provides it).
    for d in ("Desktop", "Downloads", "Documents", ".cache", ".config",
              ".local/share/Trash/files", "ZebraDir", "AppleDir"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "notes.txt").write_text("")
    (tmp_path / ".bashrc").write_text("")
    (tmp_path / ".hidden_file").write_text("")
    os.symlink(".cache", tmp_path / "Cache")
    os.symlink(".config", tmp_path / "Config")
    os.symlink(".local/share/Trash/files", tmp_path / "Trash")
    os.symlink("Downloads", tmp_path / "MyLink")   # a user-added symlink

    lines = _run_sync(tmp_path)
    labels = [ln.split(" ", 1)[1] for ln in lines]
    # NO "Home Directory" bookmark (deleted from the sidebar); the list starts at the dirs.
    assert "Home Directory" not in labels
    assert not any(".home-directory" in ln for ln in lines)
    # Desktop skipped, .hidden_file skipped.
    assert "Desktop" not in labels
    assert ".hidden_file" not in labels
    # Order groups: dirs (alpha) < files < symlinks < Trash last.
    def idx(lbl):
        return labels.index(lbl)
    # dirs before files before symlinks.
    assert idx("AppleDir") < idx("Documents") < idx("Downloads") < idx("ZebraDir")
    assert idx("ZebraDir") < idx("notes.txt")               # last dir before first file
    assert idx("notes.txt") < idx("Cache")                  # file before first symlink
    assert idx("Cache") < idx("Config") < idx("MyLink")     # symlinks alpha
    assert labels[-1] == "Trash"                            # Trash dead last
    # symlink bookmarks resolve to their targets (location bar shows the real path).
    cache_line = next(ln for ln in lines if ln.endswith(" Cache"))
    assert cache_line.startswith(f"file://{tmp_path}/.cache ")
    mylink_line = next(ln for ln in lines if ln.endswith(" MyLink"))
    assert mylink_line.startswith(f"file://{tmp_path}/Downloads ")


def test_functional_ignore_directory_is_not_bookmarked(tmp_path):
    # PROMPT: the sidebar must "ignore the directory 'Ignore/'". A top-level "Ignore" folder in
    # the home dir must NOT produce a sidebar bookmark (while its siblings still do).
    for d in ("Downloads", "Ignore", "Documents"):
        (tmp_path / d).mkdir()
    lines = _run_sync(tmp_path)
    labels = [ln.split(" ", 1)[1] for ln in lines]
    assert "Ignore" not in labels                          # the Ignore dir is skipped
    assert "Downloads" in labels and "Documents" in labels  # siblings still present


def test_functional_dangling_symlink_is_not_bookmarked(tmp_path):
    # PROMPT: the sidebar lists "Symbolic Links(of directories, files, or .html files)" -- i.e. a
    # symlink is only shown if it resolves to a real directory or file. A DANGLING symlink (target
    # missing) resolves to nothing, so it must NOT appear (a broken sidebar entry otherwise).
    (tmp_path / "Downloads").mkdir()
    os.symlink("Downloads", tmp_path / "GoodLink")          # -> real dir, shown
    os.symlink("nonexistent-target", tmp_path / "DeadLink")  # dangling, skipped
    lines = _run_sync(tmp_path)
    labels = [ln.split(" ", 1)[1] for ln in lines]
    assert "GoodLink" in labels                              # resolves to a dir -> shown
    assert "DeadLink" not in labels                          # dangling -> not on the sidebar


def test_functional_bookmarks_have_no_comment_lines(tmp_path):
    (tmp_path / "Downloads").mkdir()
    lines = _run_sync(tmp_path)
    for ln in lines:
        assert ln.startswith("file://"), ln   # every line is a bookmark (GTK format)


def test_functional_addition_is_tracked_by_watch(tmp_path):
    # Prove --watch regenerates when a new top-level entry appears (the live requirement).
    (tmp_path / "Downloads").mkdir()
    script = tmp_path / "azzio-sidebar-sync"
    script.write_text(live_sidebar.sync_script())
    env = dict(os.environ, HOME=str(tmp_path))
    proc = subprocess.Popen(["sh", str(script), "--watch"], env=env)
    try:
        import time
        bm = tmp_path / ".config/gtk-3.0/bookmarks"
        # Wait for the INITIAL regen to land (Popen+sh startup has variance; poll, don't assume).
        deadline = time.time() + 8
        while time.time() < deadline and not bm.exists():
            time.sleep(0.2)
        assert bm.exists(), "initial regen never wrote the bookmarks file"
        assert "NewFolderByUser" not in bm.read_text()
        (tmp_path / "NewFolderByUser").mkdir()   # user adds a folder
        # poll for the regeneration (interval is WATCH_INTERVAL_SECS).
        deadline = time.time() + live_sidebar.WATCH_INTERVAL_SECS + 8
        appeared = False
        while time.time() < deadline:
            if "NewFolderByUser" in bm.read_text():
                appeared = True
                break
            time.sleep(0.3)
        assert appeared, "watch did not pick up the added folder"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# --- step five item 6: LIVE Places update (in-place inode rewrite) -----------

def _bookmarks_path(tmp_home):
    return tmp_home / ".config/gtk-3.0/bookmarks"


def test_regen_rewrites_bookmarks_in_place_same_inode(tmp_path):
    """Part of the live-update fix: on a change the helper rewrites the EXISTING bookmarks inode
    IN PLACE (so the file manager's GFileMonitor -- which GLib's inotify backend runs by watching the PARENT
    DIR and filtering for the `bookmarks` basename, verified on the box -- fires a CHANGED event
    and Places refreshes live) instead of renaming a fresh temp over it. (The OTHER half of the
    fix, the ISO exec-bit pin that lets the watcher actually run, is pinned above.) Pin that the
    inode is UNCHANGED across a regen that adds a
    folder, while the CONTENT did update."""
    (tmp_path / "Downloads").mkdir()
    _run_sync(tmp_path)                                   # seed
    bm = _bookmarks_path(tmp_path)
    assert bm.exists()
    inode_before = bm.stat().st_ino
    before = bm.read_text()

    (tmp_path / "NewProject").mkdir()                     # user adds a folder
    _run_sync(tmp_path)                                   # regenerate

    assert bm.stat().st_ino == inode_before, (
        "bookmarks inode changed -> an atomic rename was used; the file manager's monitor would miss it")
    after = bm.read_text()
    assert after != before and "NewProject" in after     # content really did update in place


def test_regen_tracks_removal_of_a_directory(tmp_path):
    """Places must update when a directory is REMOVED too (not just added). After deleting a
    folder and regenerating, its bookmark is gone -- still in the same inode (live-safe)."""
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Scratch").mkdir()
    _run_sync(tmp_path)
    bm = _bookmarks_path(tmp_path)
    inode_before = bm.stat().st_ino
    assert "Scratch" in bm.read_text()

    (tmp_path / "Scratch").rmdir()                        # user removes the folder
    _run_sync(tmp_path)

    text = bm.read_text()
    assert "Scratch" not in text                          # the removed folder's bookmark is gone
    assert "Downloads" in text                            # the survivor stays
    assert bm.stat().st_ino == inode_before               # still in place (live-safe)


def test_regen_does_not_rewrite_when_nothing_changed(tmp_path):
    """The install is gated on a real content change: re-running with an UNCHANGED home does
    NOT rewrite the file (no needless CHANGED event -> no Places flicker). Pin via the mtime
    (an in-place `cat >` would bump it) and the inode."""
    import time
    (tmp_path / "Downloads").mkdir()
    _run_sync(tmp_path)
    bm = _bookmarks_path(tmp_path)
    stat_before = bm.stat()
    time.sleep(1.1)                                       # ensure a rewrite would move mtime (1s granular)
    _run_sync(tmp_path)                                   # nothing changed in the home
    stat_after = bm.stat()
    assert stat_after.st_ino == stat_before.st_ino
    assert stat_after.st_mtime == stat_before.st_mtime, (
        "bookmarks were rewritten despite no change (would flicker Places every poll)")


def test_install_uses_in_place_write_not_atomic_rename(tmp_path):
    """Source-level guard: the generated script installs the bookmarks by rewriting the file in
    place (`cat "$tmp" > "$BM"`), NOT by `mv`-ing the temp over it. A future refactor that
    reintroduces an atomic rename (which breaks the file manager's live monitor) trips this."""
    script = live_sidebar.sync_script()
    assert 'cat "$tmp" > "$BM"' in script                # in-place rewrite of the watched inode
    assert 'mv -f "$tmp" "$BM"' not in script            # NOT an atomic rename over the path
    # And the change is gated on a real content difference (cmp), so it does not flicker.
    assert "cmp -s" in script


def test_entries_with_spaces_are_tracked_not_dropped_or_corrupted(tmp_path):
    """A home entry whose NAME contains spaces (e.g. "My Documents") must appear as ONE correct
    bookmark -- not be dropped, and not corrupt sibling lines. (The enumeration iterates with a
    glob, not `for x in $(ls)`, which would word-split "My Documents" into two bogus tokens.)
    This is exactly "anything the user adds shows up" for the ordinary spaced-name case."""
    # Spaced dir, a spaced symlink, and a collision set that would MERGE under word-splitting
    # ("My Docs" alongside real "My" and "Docs").
    for d in ("Downloads", "My Documents", "My", "Docs", "My Docs",
              ".local/share/Trash/files"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    os.symlink("Downloads", tmp_path / "My Link")
    os.symlink(".local/share/Trash/files", tmp_path / "Trash")

    lines = _run_sync(tmp_path)
    # Each line is "<URI> <label>": the URI is the token up to the FIRST space (GTK format), so
    # a spaced path is percent-encoded in the URI and the label is the rest.
    labels = [ln.split(" ", 1)[1] for ln in lines]
    # Every spaced/colliding entry is present exactly once, as its own bookmark.
    for expected in ("My Documents", "My Docs", "My", "Docs", "Downloads", "My Link"):
        assert labels.count(expected) == 1, (expected, labels)
    # The URI is percent-encoded (no LITERAL space in the URI token), so the line parses as one
    # bookmark; the label keeps the human-readable spaced name.
    my_docs = next(ln for ln in lines if ln.endswith(" My Documents"))
    uri, label = my_docs.split(" ", 1)
    assert label == "My Documents"
    assert " " not in uri and uri == f"file://{tmp_path}/My%20Documents"
    # The spaced symlink resolves to its target (location bar shows the real path).
    my_link = next(ln for ln in lines if ln.endswith(" My Link"))
    assert my_link.split(" ", 1)[0] == f"file://{tmp_path}/Downloads"
    # No line is malformed (each is a single "file://<uri> <label>" bookmark, URI space-free).
    for ln in lines:
        assert ln.startswith("file://"), ln
        assert " " not in ln.split(" ", 1)[0], f"URI token has a literal space: {ln!r}"
    # Trash still last even amid the spaced names.
    assert labels[-1] == "Trash"


def test_signature_detects_a_spaced_name_addition(tmp_path):
    """home_sig() must also be space-safe: adding "New Folder" changes the signature (so
    --watch fires a regen). A word-splitting home_sig would miss/misattribute it."""
    (tmp_path / "Downloads").mkdir()
    script = tmp_path / "azzio-sidebar-sync"
    script.write_text(live_sidebar.sync_script())
    env = dict(os.environ, HOME=str(tmp_path))

    def sig():
        # Source the script and print home_sig for the current HOME (no --watch loop).
        r = subprocess.run(
            ["sh", "-c", f'. "{script}" >/dev/null 2>&1; home_sig'],
            env=env, capture_output=True, text=True, timeout=15)
        return r.stdout

    before = sig()
    (tmp_path / "New Folder").mkdir()
    after = sig()
    assert before != after, "signature did not change when a spaced-name folder was added"
    assert "New Folder" in after
