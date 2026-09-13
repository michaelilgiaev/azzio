"""pacman -- the pacman.conf variants.

These are pure string generators/transforms. The offline/online repo switching
(switch_to_local_repo, append_local_repo) is exactly the brittle string surgery
where a dropped line or a missed section silently produces a configuration that either
contacts the network when it must not, or fails to find the local repo.
"""

from __future__ import annotations

import pacman


# --- download_conf: host-independent fetch configuration --------------------------

def test_download_conf_never_includes_host_mirrorlist():
    # The whole point: fetch identically on Manjaro/Arch/Docker. Including the host
    # mirrorlist would point at the wrong distro's repos.
    conf = pacman.download_conf()
    assert "Include = /etc/pacman.d/mirrorlist" not in conf


def test_download_conf_trust_and_repos():
    conf = pacman.download_conf()
    assert "SigLevel          = Never" in conf
    assert "[core]" in conf and "[extra]" in conf and "[multilib]" in conf
    # Hard-coded PINNED Arch Linux Archive snapshot, not a host mirrorlist -- so the fetch
    # is host-independent AND immune to the live repos being momentarily inconsistent.
    assert "archive.archlinux.org" in conf
    assert pacman.ARCH_SNAPSHOT in conf


def test_download_conf_has_no_active_download_user():
    # pacman runs as root into root-owned scratch here; the privilege-dropped alpm
    # helper would fail, so no ACTIVE DownloadUser directive may be set. (The conf
    # DOES carry a comment explaining the omission, so match only non-comment lines.)
    for line in pacman.download_conf().splitlines():
        code = line.split("#", 1)[0].strip()
        assert not code.startswith("DownloadUser"), line


# --- build_profile_conf: mkarchiso's internal pacstrap ---------------------

def test_build_profile_conf_injects_cachedir():
    conf = pacman.build_profile_conf(cachedir="/build/cache/pacman-pkg")
    assert "CacheDir     = /build/cache/pacman-pkg" in conf


def test_build_profile_conf_without_cachedir_leaves_it_commented():
    conf = pacman.build_profile_conf(cachedir=None)
    assert "#CacheDir     = /var/cache/pacman/pkg/" in conf
    assert "CacheDir     = /build" not in conf


def test_build_profile_conf_noextracts_os_release():
    # os-release must be NoExtract'd so the Azzio branding wins over `filesystem`.
    conf = pacman.build_profile_conf()
    assert "NoExtract   = usr/lib/os-release" in conf


def test_noextract_covers_every_app_override():
    # Every kitty/gedit system file Azzio overrides is owned by its package, so it
    # MUST be NoExtract'd -- otherwise pacstrap's file-conflict check aborts the build with
    # "exists in filesystem" (the exact failure this fix addresses). Guard that BOTH the
    # live-ISO profile conf and the on-disk installer's pacstrap conf list all of them.
    for conf in (pacman.build_profile_conf(), pacman.installer_pacstrap_conf()):
        noextract_line = next(l for l in conf.splitlines()
                              if l.startswith("NoExtract   ="))
        for _basename, target, _remove in pacman.ISO_APP_OVERRIDES:
            assert target.lstrip("/") in noextract_line, target


def test_app_override_cp_sh_plants_replacements_and_removes_suppressed():
    # The post-pacstrap hook installs each replacement from the staging dir and removes the
    # suppress-only paths. Prefix distinguishes the live chroot ("") from the installer /mnt.
    live = pacman.app_override_cp_sh()
    assert "install -Dm644 /root/azzio/apps/kitty.svg "\
           "/usr/share/icons/hicolor/scalable/apps/kitty.svg" in live
    assert "install -Dm644 /root/azzio/apps/org.gnome.gedit.desktop "\
           "/usr/share/applications/org.gnome.gedit.desktop" in live
    # Suppress-only cat PNGs: removed, never installed.
    assert "rm -f /usr/share/icons/hicolor/256x256/apps/kitty.png" in live
    assert "rm -f /usr/share/pixmaps/kitty.png" in live
    # The "kitty URL launcher" (kitty-open.desktop) is REMOVED so it stops appearing in the file
    # manager's "Open With" (NoDisplay does not hide it there; only removal does). Its Name is
    # "kitty URL Launcher" and it declares MimeType inode/directory + text/* + scheme handlers.
    assert "rm -f /usr/share/applications/kitty-open.desktop" in live
    # ...but the kitty TERMINAL launcher (kitty.desktop) must NOT be touched -- only the URL opener.
    assert "kitty.desktop" not in live.replace("kitty-open.desktop", "")
    assert "install -Dm644 /root/azzio/apps/None" not in live  # no body staged for removals
    # Installer variant targets the mounted new root.
    mnt = pacman.app_override_cp_sh("/mnt")
    assert "/mnt/usr/share/applications/org.gnome.gedit.desktop" in mnt
    assert "rm -f /mnt/usr/share/pixmaps/kitty.png" in mnt
    # Staged basenames of the REPLACEMENT entries must be unique: they all land in the same
    # /root/azzio/apps/ staging dir, so a collision would silently overwrite one body with
    # another's (e.g. two locales sharing "thunar.mo"). The en_US/en_GB catalogs are the case
    # that forced per-locale basenames -- guard the invariant.
    staged = [b for b, _t, remove in pacman.ISO_APP_OVERRIDES if not remove and b is not None]
    assert len(staged) == len(set(staged)), staged


def test_file_manager_and_xviewer_desktop_overrides_are_planted():
    # The file-manager rename/icon .desktop (its on-disk name stays thunar.desktop), the xviewer
    # icon .desktop, and the three NoDisplay suppressions (Bulk Rename, file-manager Preferences,
    # About Xfce) are all package-owned, so they must be planted post-pacstrap
    # from the staging dir. (Their bodies are staged by compiler._emit_apps from the module
    # emit_plans.)
    live = pacman.app_override_cp_sh()
    for name in (
        "thunar.desktop",
        "xviewer.desktop",
        "thunar-bulk-rename.desktop",
        "thunar-settings.desktop",
        "xfce4-about.desktop",
    ):
        assert (f"install -Dm644 /root/azzio/apps/{name} "
                f"/usr/share/applications/{name}") in live, name
    # File-manager gettext .mo override catalogs: the en_GB path is package-owned, so every locale
    # catalog is planted post-pacstrap from the staging dir (per-locale staged basenames).
    # en_IL is the DEFAULT installed locale (calamares seeds Asia/Jerusalem -> LANG=en_IL), so
    # its catalog is what makes the overrides actually apply out of the box.
    for basename, target in (
        ("thunar.en_US.mo", "/usr/share/locale/en_US/LC_MESSAGES/thunar.mo"),
        ("thunar.en_GB.mo", "/usr/share/locale/en_GB/LC_MESSAGES/thunar.mo"),
        ("thunar.en_IL.mo", "/usr/share/locale/en_IL/LC_MESSAGES/thunar.mo"),
    ):
        assert (f"install -Dm644 /root/azzio/apps/{basename} {target}") in live, basename


def test_dolphin_is_gone_from_manifest_and_file_manager_present():
    # PROMPT: Dolphin dropped, our file_manager added (+ companions), xviewer added. The manifest
    # lists our OWN package name `file_manager`, NOT `thunar` -- so stock thunar must NOT appear as
    # a bare manifest token. The two Xfce plugins that used to bind to it (thunar-volman,
    # thunar-archive-plugin) were dropped, so no `thunar`-prefixed package name appears at all.
    import paths
    toks = [line.split("#", 1)[0].strip()
            for line in paths.PACKAGES_FILE.read_text().splitlines()]
    toks = [t for t in toks if t]
    assert "dolphin" not in toks
    assert "file_manager" in toks
    assert "thunar" not in toks          # our package is named file_manager; the bare name is not listed
    assert "thunar-volman" not in toks           # plugin dropped
    assert "thunar-archive-plugin" not in toks   # plugin dropped
    assert "tumbler" in toks
    assert "zenity" in toks
    assert "exo" in toks
    assert "xviewer" in toks


def test_build_profile_conf_multilib_off():
    conf = pacman.build_profile_conf()
    assert "\n[multilib]\n" not in conf
    assert "#[multilib]" in conf


# --- installer_base_conf: the installed system's /etc/pacman.conf -----------

def test_installer_base_conf_enables_multilib():
    conf = pacman.installer_base_conf()
    assert "\n[multilib]\n" in conf
    # No build-only tweaks leak into the installed configuration.
    assert "NoExtract   = usr/lib/os-release" not in conf


def _ignorepkg_directive(conf):
    """The single active `IgnorePkg = ...` directive line in a conf, or None if it is
    commented/absent. Skips the explanatory comment line (which also says 'IgnorePkg')."""
    for line in conf.splitlines():
        stripped = line.strip()
        if stripped.startswith("IgnorePkg"):
            return stripped
    return None


def test_installer_base_conf_freezes_every_azzio_package():
    # The installed /etc/pacman.conf must list every frozen Azzio package in IgnorePkg so a
    # routine `pacman -Syu` never replaces our hand-built fork with a stock Arch build.
    conf = pacman.installer_base_conf()
    directive = _ignorepkg_directive(conf)
    assert directive is not None, "installer_base_conf must emit an active IgnorePkg line"
    listed = directive.split("=", 1)[1].split()
    for pkg in pacman.FROZEN_PKGS:
        assert pkg in listed, f"{pkg} must be frozen in the installed IgnorePkg"


def test_installer_base_conf_ignorepkg_is_single_sourced_from_frozen_pkgs():
    # The directive is generated verbatim from FROZEN_PKGS -- adding a future Azzio package to
    # that one constant is all it takes to freeze it (no second edit to the conf text).
    conf = pacman.installer_base_conf()
    assert _ignorepkg_directive(conf) == f"IgnorePkg   = {' '.join(pacman.FROZEN_PKGS)}"


def test_installer_base_conf_freezes_file_manager_specifically():
    # file_manager (Azzio's own file manager) is the one named source dir that is a real pacman
    # package. It carries no provides/replaces, so nothing on a mirror is linked to it by name;
    # the freeze is retained as belt-and-braces so a same-name repo package could never supersede
    # it on a bare -Syu. Frozen by OUR package name.
    assert "file_manager" in pacman.FROZEN_PKGS
    assert "file_manager" in _ignorepkg_directive(pacman.installer_base_conf())


def test_non_pacman_azzio_apps_are_not_in_ignorepkg():
    # azzio / window_switcher / application_menu / hypervisor ship under /usr/local via
    # emit_plan(), are not pacman packages, and MUST NOT be listed: a bogus IgnorePkg name is a
    # misleading no-op. Guard against someone "helpfully" adding the source-dir names.
    directive = _ignorepkg_directive(pacman.installer_base_conf()) or ""
    for name in ("azzio", "window_switcher", "window-switcher", "application_menu",
                 "application-menu", "azzio-application-menu-daemon",
                 "azzio-window-switcher", "hypervisor"):
        assert name not in directive.split()


def test_build_and_pacstrap_confs_do_not_freeze_packages():
    # The INITIAL install must still seed Azzio's own versions, so the transient build/pacstrap
    # confs leave IgnorePkg commented -- freezing there could block the very install of ours.
    for conf in (pacman.build_profile_conf(),
                 pacman.build_profile_conf(cachedir="/build/cache/pacman-pkg"),
                 pacman.installer_pacstrap_conf()):
        assert _ignorepkg_directive(conf) is None
        assert "#IgnorePkg   =" in conf


# --- append_local_repo: online build, keep network repos + add local -------

def test_append_local_repo_adds_section():
    out = pacman.append_local_repo("[options]\n[core]\n", "/mnt/repo")
    assert "[pacstrap-azzio-repo]" in out
    assert "Server = file:///mnt/repo" in out
    assert "SigLevel = Never" in out
    # Network repo is kept.
    assert "[core]" in out


def test_append_local_repo_is_idempotent():
    once = pacman.append_local_repo("[core]\n", "/mnt/repo")
    twice = pacman.append_local_repo(once, "/mnt/repo")
    assert once == twice
    assert once.count("[pacstrap-azzio-repo]") == 1


def test_append_local_repo_ordered_before_network_repos():
    # Our local repo carries packages that ALSO exist upstream (librewolf, calamares). pacman
    # picks a package from the FIRST repo in config order that has the name -- it does NOT pick
    # the globally-highest version across repos. So our repo MUST precede [core]/[extra] for our
    # builds of those names to win. (file_manager is our own name that exists on no mirror, so it
    # cannot clash; ordering matters for the same-named librewolf/calamares.)
    conf = pacman.build_profile_conf()
    out = pacman.append_local_repo(conf, "/mnt/repo")
    local = out.index("[pacstrap-azzio-repo]")
    assert "\n[core]\n" in out and "\n[extra]\n" in out, "network repos must be kept"
    assert local < out.index("\n[core]\n"), "local repo must precede [core]"
    assert local < out.index("\n[extra]\n"), "local repo must precede [extra]"


# --- switch_to_local_repo: fully-offline rebuild ---------------------------

def test_switch_to_local_repo_drops_network_repos():
    conf = pacman.build_profile_conf()
    out = pacman.switch_to_local_repo(conf, "/mnt/repo")
    # Every active network repo section header is gone.
    assert "\n[core]\n" not in out
    assert "\n[extra]\n" not in out
    assert "\n[multilib]\n" not in out


def test_switch_to_local_repo_appends_single_local_repo():
    conf = pacman.build_profile_conf()
    out = pacman.switch_to_local_repo(conf, "/srv/azrepo")
    assert out.count("[pacstrap-azzio-repo]") == 1
    assert "Server = file:///srv/azrepo" in out
    assert out.rstrip().endswith("Server = file:///srv/azrepo")


def test_switch_to_local_repo_keeps_options_block():
    conf = pacman.build_profile_conf()
    out = pacman.switch_to_local_repo(conf, "/srv/azrepo")
    # The [options] section and its directives survive the surgery.
    assert "[options]" in out
    assert "HoldPkg     = pacman glibc" in out


# --- installer_pacstrap_conf: transient offline-install configuration -------------

def test_installer_pacstrap_conf_only_local_repo_active():
    conf = pacman.installer_pacstrap_conf()
    # Network repos all commented, local file:// repo active.
    assert "#[core]" in conf and "#[extra]" in conf
    assert "[pacstrap-azzio-repo]" in conf
    assert "Server = file:///mnt/pacstrap-azzio-repo/" in conf


# --- _options_block: the [options] header with the two toggled directives ---

def test_options_block_falsy_cachedir_leaves_commented():
    # cachedir is truthy-tested (`if cachedir`); an empty string is falsy, so the
    # default commented CacheDir line must survive and no active line be injected.
    ob = pacman._options_block(cachedir="")
    assert "#CacheDir     = /var/cache/pacman/pkg/" in ob
    # No uncommented CacheDir directive leaked in.
    for line in ob.splitlines():
        assert not line.startswith("CacheDir")


def test_options_block_truthy_cachedir_injected():
    ob = pacman._options_block(cachedir="/build/cache/pacman-pkg")
    assert "CacheDir     = /build/cache/pacman-pkg" in ob
    assert "#CacheDir     = /var/cache/pacman/pkg/" not in ob


def test_options_block_empty_noextract_leaves_commented():
    # noextract is truthy-tested too; an empty list is falsy -> the commented
    # placeholder "#NoExtract   =" is emitted, never an active empty NoExtract.
    ob = pacman._options_block(cachedir=None, noextract=[])
    assert "#NoExtract   =" in ob
    for line in ob.splitlines():
        assert not line.startswith("NoExtract")


def test_options_block_noextract_paths_injected():
    ob = pacman._options_block(cachedir=None, noextract=["usr/lib/os-release"])
    assert "NoExtract   = usr/lib/os-release" in ob


def test_options_block_no_ignorepkg_leaves_commented():
    # ignorepkg defaults to None (and an empty tuple is falsy) -> the commented
    # placeholder "#IgnorePkg   =" is emitted, never an active empty IgnorePkg. This is
    # what the build/pacstrap confs rely on so the INITIAL install still seeds ours.
    for arg in (None, ()):
        ob = pacman._options_block(cachedir=None, ignorepkg=arg)
        assert "#IgnorePkg   =" in ob
        for line in ob.splitlines():
            assert not line.startswith("IgnorePkg")


def test_options_block_ignorepkg_generalizes_to_multiple_packages():
    # The freeze is single-sourced from one constant, so adding a future Azzio package must be
    # a one-line edit. Prove the rendering handles MORE than one name (space-separated, the
    # pacman-native IgnorePkg format) -- e.g. the day librewolf/calamares are added to the set.
    ob = pacman._options_block(cachedir=None, ignorepkg=("file_manager", "librewolf"))
    assert "IgnorePkg   = file_manager librewolf" in ob


# --- _net_repos: multilib active vs commented -------------------------------

def test_net_repos_multilib_true_active():
    block = pacman._net_repos(multilib=True)
    assert "\n[core]\n" in block and "\n[extra]\n" in block
    # Multilib section header is uncommented (active).
    assert "\n[multilib]\n" in block
    assert "#[multilib]" not in block


def test_net_repos_multilib_false_commented():
    block = pacman._net_repos(multilib=False)
    assert "\n[core]\n" in block and "\n[extra]\n" in block
    # Multilib header is commented out, not active.
    assert "\n[multilib]\n" not in block
    assert "#[multilib]" in block
    assert "#Include = /etc/pacman.d/mirrorlist" in block
