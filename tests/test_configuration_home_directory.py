"""packages.file_manager.home_directory -- the single source of truth for the home-directory layout
(top-level folders + convenience symlinks) that the file manager's sidebar also mirrors.

Why these tests matter: compiler._emit_homedir walks this module's plain data
(DIRECTORIES/LINKS/TRASH_DIRS) with emit.mkdir()/emit.link() into BOTH /home/main and
/etc/skel, and packages/file_manager builds the GTK bookmarks the file manager reads from the SAME data.
Two invariants are load-bearing and guarded here:

  * Symlink targets must be RELATIVE -- an absolute /home/main/... target would dangle
    under /etc/skel and in a Calamares-copied /home/<newuser>.
  * The Trash symlink's target chain (.local/share/Trash/files) must be in TRASH_DIRS so
    the link does not dangle.

The exact folder/link SET is the spec (PROMPT task 1), so it is pinned literally: a
drift here silently changes both the on-disk layout and the file-manager sidebar.
"""

from __future__ import annotations

import posixpath

from packages.file_manager import home_directory as hd


def test_directory_set_is_exactly_the_spec():
    # PROMPT task 1: these ten folders, in this order (the order is the sidebar order).
    # "Shared" is the host<->guest share mountpoint, shipped in /home/main by default.
    assert hd.DIRECTORIES == (
        "Desktop", "Downloads", "Vault", "Documents", "Ignore",
        "Music", "Pictures", "Projects", "Shared", "Videos",
    )


def test_link_set_is_exactly_the_spec():
    # PROMPT task 1: these six convenience symlinks, name -> RELATIVE target.
    assert hd.LINKS == (
        ("Trash", ".local/share/Trash/files"),
        ("Cache", ".cache"),
        ("Config", ".config"),
        ("Bashrc", ".bashrc"),
        ("Local", ".local"),
        ("SSH", ".ssh"),
    )


def test_every_symlink_target_is_relative():
    # Load-bearing: a relative target resolves against the link's own dir, so the same link
    # is valid in /home/main AND /etc/skel AND a copied-out home. An absolute target dangles.
    for name, target in hd.LINKS:
        assert not target.startswith("/"), f"{name} target must be relative, got {target!r}"
        assert not posixpath.isabs(target), name


def test_trash_symlink_target_is_covered_by_trash_dirs():
    # "Trash -> .local/share/Trash/files" would dangle unless the chain exists. The link
    # target must be one of the dirs TRASH_DIRS creates.
    trash_target = dict(hd.LINKS)["Trash"]
    assert trash_target in hd.TRASH_DIRS


def test_mounts_is_an_absolute_link_to_the_udisks_media_root():
    # PROMPT: "Mounts/" -> /run/media (the udisks media ROOT, the parent of every user's
    # /run/media/<user>). It is a SEPARATE class from LINKS: an ABSOLUTE target (/run/media is not
    # under $HOME, so it has no relative form), created in the LIVE /home/main ONLY (not
    # skel-mirrored). It carries its own mount icon.
    assert hd.ABSOLUTE_LINKS == (("Mounts", "/run/media"),)
    for name, target in hd.ABSOLUTE_LINKS:
        assert posixpath.isabs(target), name           # absolute on purpose
    # Mounts is NOT an ordinary directory nor a relative link.
    assert "Mounts" not in hd.DIRECTORIES
    assert "Mounts" not in dict(hd.LINKS)


def test_templates_directory_is_gone():
    # PROMPT: "Delete that 'Templates/' directory". It must not be created anywhere -- not a
    # sidebar dir, not an extra dir.
    assert "Templates" not in hd.DIRECTORIES
    assert "Templates" not in hd.EXTRA_DIRECTORIES
    assert hd.EXTRA_DIRECTORIES == ()


def test_trash_chain_has_both_spec_dirs():
    # The XDG trash spec requires BOTH files/ and info/ (info holds the .trashinfo metadata).
    assert ".local/share/Trash/files" in hd.TRASH_DIRS
    assert ".local/share/Trash/info" in hd.TRASH_DIRS


def test_ssh_symlink_target_is_the_precreated_ssh_dir():
    # "SSH -> .ssh" must not dangle: SSH_DIR is the (relative) dir pre-created before the link,
    # exactly like the Trash chain. It must equal the SSH link's target.
    assert hd.SSH_DIR == dict(hd.LINKS)["SSH"]
    assert not hd.SSH_DIR.startswith("/")          # relative, like every layout target


def test_ssh_dir_mode_is_private():
    # sshd refuses a group/world-accessible ~/.ssh, so the pre-created dir must be 0700.
    assert hd.SSH_DIR_MODE == 0o700


def test_home_is_the_live_user_home():
    assert hd.HOME == "/home/main"


def test_resolved_home_path_joins_and_normalizes():
    # A plain directory resolves to itself under HOME.
    assert hd.resolved_home_path("Downloads") == "/home/main/Downloads"
    # A dot target (a symlink target) resolves to the real dot location, no symlink hop.
    assert hd.resolved_home_path(".config") == "/home/main/.config"
    assert hd.resolved_home_path(".local/share/Trash/files") == \
        "/home/main/.local/share/Trash/files"


def test_sidebar_entries_are_directories_then_links_resolved():
    entries = hd.sidebar_entries()
    labels = [label for label, _ in entries]
    # PROMPT ordering: directories first (in order, EXCEPT "Ignore" which is kept off the
    # sidebar), then the symlinks (in order) -- the relative LINKS first, then the ABSOLUTE_LINKS
    # ("Mounts") -- with "Trash" forced to the very END (it is a symlink but pinned last). No plain
    # files in the curated set. ("main" is the built-in Home place, not a bookmark entry -- see
    # test_main_user_label_is_the_home_basename.)
    assert labels == [
        "Desktop", "Downloads", "Vault", "Documents",
        "Music", "Pictures", "Projects", "Shared", "Videos",
        "Cache", "Config", "Bashrc", "Local", "SSH",
        "Mounts",
        "Trash",
    ]
    targets = dict(entries)
    # A directory shortcut points at itself.
    assert targets["Desktop"] == "/home/main/Desktop"
    # A symlink shortcut points at the RESOLVED target (so the file manager shows the real path,
    # not the /home/main/Config symlink path) -- PROMPT task 2 "display the ACTUAL path".
    assert targets["Config"] == "/home/main/.config"
    assert targets["Cache"] == "/home/main/.cache"
    assert targets["Trash"] == "/home/main/.local/share/Trash/files"
    assert targets["Local"] == "/home/main/.local"
    # Bashrc is a FILE target (.bashrc); still resolved under HOME.
    assert targets["Bashrc"] == "/home/main/.bashrc"
    # SSH points at the resolved .ssh dir (a symlink shortcut, after Local, before Mounts).
    assert targets["SSH"] == "/home/main/.ssh"
    # Mounts is an ABSOLUTE-target symlink (PROMPT: "Mounts -> /run/media"): the sidebar entry
    # uses the absolute target VERBATIM (not resolved under HOME), sitting just before Trash.
    assert targets["Mounts"] == "/run/media"


def test_ignore_directory_is_created_on_disk_but_kept_out_of_sidebar():
    # PROMPT: the sidebar must "ignore the directory 'Ignore/'". But Ignore is still a real
    # top-level folder the layout creates on disk -- so it MUST stay in DIRECTORIES (the on-disk
    # source of truth) while being filtered OUT of the sidebar entries.
    assert "Ignore" in hd.DIRECTORIES                      # still created on disk
    labels = [label for label, _ in hd.sidebar_entries()]
    assert "Ignore" not in labels                          # but never on the sidebar
    assert "Ignore" in hd.SIDEBAR_SKIP                     # via the explicit skip set


def test_main_user_label_is_the_home_basename():
    # PROMPT: "add the user to the top of the sidebar ... simply name it 'main'." The user row is
    # the file manager's BUILT-IN Home place (shown as the home basename), which already sits at
    # the very top of Places -- NOT a GTK bookmark. So the label the file manager displays is the
    # basename of HOME, and that must be "main".
    assert hd.SIDEBAR_USER_LABEL == "main"
    assert hd.HOME.rsplit("/", 1)[-1] == hd.SIDEBAR_USER_LABEL


def test_main_is_not_injected_as_a_bookmark_entry():
    # The "main" row is the built-in Home place, surfaced by un-hiding it (see settings.
    # HIDDEN_BOOKMARKS), NOT by a GTK bookmark. A "main" bookmark would land below the built-in
    # Desktop and duplicate the built-in Home, so sidebar_entries() must NOT contain one.
    labels = [label for label, _ in hd.sidebar_entries()]
    assert "main" not in labels


def test_no_home_directory_bookmark_machinery():
    # The "Home Directory" sidebar bookmark was DELETED at the user's request ("just delete it,
    # we dont actually need it there is a home button"), so the ".home-directory" symlink that
    # used to back it (and its constants) must be GONE -- nothing should re-create that symlink.
    assert not hasattr(hd, "HOME_DIR_SYMLINK_NAME")
    assert not hasattr(hd, "HOME_DIR_SYMLINK_TARGET")
    assert not hasattr(hd, "HOME_DIR_BOOKMARK_URI")


def test_no_absolute_paths_leak_into_layout_names():
    # Directory names and link names are bare (no leading path) -- they are created
    # directly under the home dir.
    for name in hd.DIRECTORIES:
        assert "/" not in name, name
    for name, _ in hd.LINKS:
        assert "/" not in name, name
