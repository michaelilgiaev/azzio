"""All pacman.conf variants, generated in Python.

The variants:

  download_conf()          used by the package-cache step to fetch ISO packages on
                           ANY host (even Manjaro): hard-coded Arch mirrors, no host
                           mirrorlist, SigLevel=Never, no alpm DownloadUser.

  build_profile_conf()     the archiso profile's pacman.conf that mkarchiso's
                           internal pacstrap uses. Standard Arch base, PLUS:
                             - NoExtract usr/lib/os-release (Azzio branding wins)
                             - an injected CacheDir (the persistent build cache)
                             - optionally rewritten to a file:// local repo for
                               fully-offline rebuilds.

  installer_base_conf()    the pacman.conf shipped to the INSTALLED system
                           (/etc/pacman.conf): plain Arch defaults, multilib on.

  installer_pacstrap_conf()the transient pacman.conf the on-disk installer swaps in
                           during pacstrap: adds the file:// [pacstrap-azzio-repo]
                           so installation works fully offline from the ISO's repo.
"""

from __future__ import annotations

# --- Pinned Arch package snapshot -------------------------------------------
# The rolling Arch `core`/`extra` repos are periodically INTERNALLY INCONSISTENT for
# short windows -- a package is dropped while another still depends on it (observed:
# `elfutils` vanished from `core` while `debugedit`, a `base-devel` member, still
# required it, so every pacman transaction that pulled `base-devel` failed with "unable
# to satisfy dependency 'elfutils'"). To make the ISO BUILD-STABLE and REPRODUCIBLE, the
# package-cache download step fetches from a frozen Arch Linux Archive (ALA) DAILY
# snapshot instead of the live mirrors. Bump this date to roll the whole ISO's package
# set forward in lockstep.
#
# KEEP IN SYNC with the Dockerfile's `ARG ARCH_SNAPSHOT` (the build-host toolchain is
# pinned to the SAME snapshot there) so the host that builds the ISO and the ISO's own
# package set are drawn from one self-consistent point in time.
ARCH_SNAPSHOT = "2026/08/10"

# ALA snapshot mirror lines (both a primary and the canonical archive host as fallback).
# SigLevel=Never at download time makes the possibly-old package signatures a non-issue;
# final trust is re-established at pacstrap against the file:// local repo.
_SNAPSHOT_MIRRORS = (
    f"Server = https://archive.archlinux.org/repos/{ARCH_SNAPSHOT}/$repo/os/$arch\n"
)

# Header shared by the base/profile/pacstrap variants (verbatim Arch default).
_STD_HEADER = """\
#
# /etc/pacman.conf
#
# See the pacman.conf(5) manpage for option and repository directives

#
# GENERAL OPTIONS
#
[options]
# The following paths are commented out with their default values listed.
# If you wish to use different paths, uncomment and update the paths.
#RootDir     = /
#DBPath      = /var/lib/pacman/
{cachedir_line}#LogFile      = /var/log/pacman.log
#GPGDir      = /etc/pacman.d/gnupg/
#HookDir     = /etc/pacman.d/hooks/
HoldPkg     = pacman glibc
#XferCommand = /usr/bin/curl -L -C - -f -o %o %u
#XferCommand = /usr/bin/wget --passive-ftp -c -O %o %u
#CleanMethod = KeepInstalled
Architecture = auto

# Pacman won't upgrade packages listed in IgnorePkg and members of IgnoreGroup
{ignorepkg_line}#IgnoreGroup =

#NoUpgrade   =
{noextract_line}
# Misc options
#UseSyslog
#Color
#NoProgressBar
CheckSpace
#VerbosePkgLists
ParallelDownloads = 5
DownloadUser = alpm
#DisableSandbox

# By default, pacman accepts packages signed by keys that its local keyring
# trusts (see pacman-key and its man page), as well as unsigned packages.
SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional
#RemoteFileSigLevel = Required

# NOTE: You must run `pacman-key --init` before first using pacman; the local
# keyring can then be populated with the keys of all official Arch Linux
# packagers with `pacman-key --populate archlinux`.

#
# REPOSITORIES
#   - can be defined here or included from another file
#   - pacman will search repositories in the order defined here
#   - local/custom mirrors can be added here or in separate files
#   - repositories listed first will take precedence when packages
#     have identical names, regardless of version number
#   - URLs will have $repo replaced by the name of the current repo
#   - URLs will have $arch replaced by the name of the architecture
#
# Repository entries are of the format:
#       [repo-name]
#       Server = ServerName
#       Include = IncludePath
#
# The header [repo-name] is crucial - it must be present and
# uncommented to enable the repo.
#

# The testing repositories are disabled by default. To enable, uncomment the
# repo name header and Include lines. You can add preferred servers immediately
# after the header, and they will be used before the default mirrors.
"""

_STD_TESTING_TAIL = """\
#[core-testing]
#Include = /etc/pacman.d/mirrorlist

#[extra-testing]
#Include = /etc/pacman.d/mirrorlist

# If you want to run 32 bit applications on your x86_64 system,
# enable the multilib repositories as required here.

#[multilib-testing]
#Include = /etc/pacman.d/mirrorlist
"""

_CUSTOM_EXAMPLE = """\
# An example of a custom package repository.  See the pacman manpage for
# tips on creating your own repositories.
#[custom]
#SigLevel = Optional TrustAll
#Server = file:///home/custompkgs
"""


# Per-application system files Azzio REPLACES or SUPPRESSES. Each is owned by the
# app's own package (kitty/gedit), so it hits the SAME file-conflict wall as
# os-release below -- pre-placing our version in the airootfs overlay aborts pacstrap
# with "exists in filesystem". The identical two-step cure applies: NoExtract the path
# (so the package never owns/lays it down) and, for the ones we REPLACE, plant our
# version post-pacstrap (compiler stages the content under /root/azzio/apps/ and the
# customize hook / installer copy it into place -- see app_override_cp_sh()).
#
# Each entry: (staged_basename | None, target_abs, remove).
#   staged_basename -- file under /root/azzio/apps/ to install at target_abs (our
#                      replacement). None means we ship no replacement -- the path is
#                      only SUPPRESSED (the two stale kitty cat PNGs, which must stay
#                      gone so the scalable approved-mark SVG wins; NoExtract alone keeps
#                      them out, no post-pacstrap step needed).
#   remove          -- True for the suppress-only PNGs (no replacement planted).
# The kitty scalable SVG + the gedit .desktop are the ones the pacstrap log named
# as conflicts; the two PNGs are added so the kitty modification's icon-removal intent actually
# holds on the ISO (otherwise pacstrap re-installs the cat PNGs the overlay had removed).
ISO_APP_OVERRIDES = [
    ("kitty.svg", "/usr/share/icons/hicolor/scalable/apps/kitty.svg", False),
    (None, "/usr/share/icons/hicolor/256x256/apps/kitty.png", True),
    (None, "/usr/share/pixmaps/kitty.png", True),
    # NUKE the "kitty URL launcher" from the file manager's "Open With" submenu (the user:
    # "Remove 'kitty URL launcher' from 'Open With'"). The kitty package ships TWO .desktop files:
    # kitty.desktop (the terminal, Name=kitty -- KEEP) and kitty-open.desktop (Name="kitty URL
    # Launcher", Exec=kitty +open %U). The latter declares MimeType=...;inode/directory;text/*;
    # image/*;x-scheme-handler/... so GIO lists it as an app that can open folders/files -- which is
    # exactly why it surfaces in "Open With". Its NoDisplay=true only hides it from the APPLICATION
    # menu; "Open With" is built from the mime<->.desktop association, which ignores NoDisplay, so
    # the launcher must be REMOVED, not merely hidden. Owned by `kitty`, so it takes the same
    # NoExtract + post-pacstrap `rm -f` route as the suppress-only kitty PNGs (basename None,
    # remove True). Only kitty-open.desktop is removed; kitty.desktop (the terminal) is untouched.
    (None, "/usr/share/applications/kitty-open.desktop", True),
    ("org.gnome.gedit.desktop", "/usr/share/applications/org.gnome.gedit.desktop", False),
    # file_manager + xviewer .desktop overrides (packages/file_manager, packages/xviewer): the
    # file manager's launcher rename+icon (its on-disk name stays thunar.desktop -- the built
    # binary owns that path) and the xviewer.desktop icon override are package-owned
    # (file_manager / xviewer), so like gedit's .desktop they are NoExtract'd and the bodies
    # (staged by compiler._emit_apps from the module emit_plans) are planted post-pacstrap.
    ("thunar.desktop", "/usr/share/applications/thunar.desktop", False),
    ("xviewer.desktop", "/usr/share/applications/xviewer.desktop", False),
    # Application-menu cleanup (packages/file_manager/menu_cleanup): NoDisplay=true overrides that
    # hide the extra file-manager/Xfce launchers. Each is package-owned (file_manager /
    # libxfce4ui), so same NoExtract + post-pacstrap install. The staged basename
    # is the launcher's own .desktop name (matching the emit_plan dest via _override_basename).
    ("thunar-bulk-rename.desktop", "/usr/share/applications/thunar-bulk-rename.desktop", False),
    ("thunar-settings.desktop", "/usr/share/applications/thunar-settings.desktop", False),
    ("xfce4-about.desktop", "/usr/share/applications/xfce4-about.desktop", False),
    # File-manager gettext .mo override catalog (packages/file_manager/locale): relabels the
    # hardcoded menu strings ("Places" -> "Home", the built-in default-opener, the Create
    # Folder/Document wording). Our `file_manager` package OWNS /usr/share/locale/en_GB/
    # LC_MESSAGES/thunar.mo (the textdomain stays `thunar` -- it is the built binary's -- one of
    # 67 shipped locale catalogs), so pre-placing our catalog in the overlay hit the file-conflict
    # wall ("thunar.mo exists in filesystem" -> pacstrap abort). Same cure as the .desktop
    # overrides: NoExtract the path and plant our catalog post-pacstrap. en_US and en_IL carry no
    # package catalog (the binary's msgids are American English), so they never conflict, but we
    # route them through the SAME machinery for symmetry -- a NoExtract of an unowned path is a
    # harmless no-op and app_override_cp_sh installs them just like en_GB.
    # en_IL is the DEFAULT installed display locale (calamares seeds Asia/Jerusalem -> LANG=en_IL),
    # so its catalog is the one that makes the relabels apply out of the box; en_US covers a
    # US-region install and en_GB is the LC_TIME date locale. Staged basenames are per-locale
    # (unique under /root/azzio/apps/) and match the emit_plan dests (locale.LOCALES) below.
    ("thunar.en_US.mo", "/usr/share/locale/en_US/LC_MESSAGES/thunar.mo", False),
    ("thunar.en_GB.mo", "/usr/share/locale/en_GB/LC_MESSAGES/thunar.mo", False),
    ("thunar.en_IL.mo", "/usr/share/locale/en_IL/LC_MESSAGES/thunar.mo", False),
    # NUKE the "Devices" section from the file manager's shortcuts pane (the user: "no Devices,
    # delete it, nuke it, I dont need it there"). The file manager's DEVICES rows come from the
    # GVfs GVolumeMonitor, and `misc-volume-management=false` only stops AUTO-MOUNT -- it does NOT
    # hide drives/volumes already reported by the monitor (verified on the VM: a VirtIO drive + a
    # 9p "shared" mount still listed under Devices). There is no file-manager config to hide the
    # whole section. The decisive, machine-agnostic lever is GVfs itself: the udisks2 volume
    # monitor is registered by /usr/share/gvfs/remote-volume-monitors/udisks2.monitor. The base
    # `gvfs` package ships ONLY that one monitor file; GVfs's other volume monitors
    # (afc/goa/gphoto2/mtp) live in the separate gvfs-afc/gvfs-goa/gvfs-gphoto2/gvfs-mtp
    # subpackages, and NONE of those are in the ISO manifest (packages.x86_64). So on the built
    # image udisks2.monitor is the only volume monitor present, and removing it leaves GVfs with
    # ZERO volume monitors -- the file manager's GVolumeMonitor is empty and NO Devices section
    # renders at all, on a VM or bare metal alike.
    # (If a gvfs-* backend subpackage is ever added to the manifest, its monitor must be removed
    # here too, or its devices would reappear.) Owned by `gvfs`, so it takes the same NoExtract +
    # post-pacstrap `rm -f` route as the suppress-only kitty PNGs (basename None, remove True).
    # Trade-offs (all acceptable given the explicit "no Devices" ask): the 9p/USB mounts no
    # longer APPEAR in the pane (they stay reachable as normal paths; on-demand `gio mount` /
    # `udisksctl` still work), and Trash is unaffected (gvfsd-trash is a separate daemon).
    (None, "/usr/share/gvfs/remote-volume-monitors/udisks2.monitor", True),
    # RECLAIM the `backup` command name for OUR home-directory backup (packages/backup).
    # GNU `tar` ships a legacy incremental-backup helper at /usr/bin/backup (a symlink to
    # /usr/lib/tar/backup.sh) and its companion /usr/bin/restore. /usr/bin precedes
    # /usr/local/bin on PATH, so typing `backup` runs tar's script instead of ours (last
    # build's bug #2). We are KEEPING the name `backup` for ourselves, so tar's copies must
    # go -- we do NOT reorder PATH. Both are owned by the `tar` package, so they take the
    # same NoExtract + post-pacstrap `rm -f` route as the suppress-only kitty PNGs (basename
    # None, remove True): NoExtract keeps pacstrap from ever laying them down, and
    # app_override_cp_sh() rm -f's them after pacstrap for good measure. `restore` is tar's
    # companion to `backup`; it is removed too. (The symlink target /usr/lib/tar/backup.sh is
    # left alone -- with the /usr/bin/backup symlink gone nothing on PATH references it.)
    (None, "/usr/bin/backup", True),
    (None, "/usr/bin/restore", True),
]

# Files the ISO overrides / suppresses. pacstrap must NOT extract the owning
# package's version:
#   usr/lib/os-release   owned by `filesystem`; we replace it with the Azzio-branded
#                        file, planted post-pacstrap by customize_airootfs.sh.
#   the ISO_APP_OVERRIDES paths (kitty icon + gedit .desktop) -- see above.
# This MUST be NoExtract'd (not just overlaid): pacman's file-conflict check runs
# BEFORE extraction and is not suppressed by NoExtract, so pre-placing our copy in the
# airootfs overlay aborts pacstrap with "exists in filesystem". NoExtract keeps the
# package from owning the path; customize_airootfs.sh then lays our copy down after
# pacstrap, conflict-free (see system.CUSTOMIZE_AIROOTFS + compiler.py step 7).
_ISO_NOEXTRACT = [
    "usr/lib/os-release",
    *[target.lstrip("/") for _basename, target, _remove in ISO_APP_OVERRIDES],
]


def app_override_cp_sh(prefix: str = "", src_dir: str = "/root/azzio/apps") -> str:
    """Shell snippet that plants the ISO_APP_OVERRIDES into a pacstrapped root.

    Runs AFTER pacstrap (the NoExtract'd paths are absent, so there is no conflict):
    installs each replacement from ``src_dir`` to ``prefix``+target (0644), and removes
    any suppress-only target that a package might still have dropped. ``prefix`` is "" for
    the live customize_airootfs.sh (chroot-relative) and "/mnt" for the on-disk installer.
    The lines mirror the os-release `cp` both hooks already do."""
    lines = []
    for basename, target, remove in ISO_APP_OVERRIDES:
        dst = f"{prefix}{target}"
        if remove:
            lines.append(f"rm -f {dst}")
        else:
            lines.append(f"install -Dm644 {src_dir}/{basename} {dst}")
    return "\n".join(lines) + "\n"


# --- Frozen Azzio packages (never upgraded on the INSTALLED system) ---------
# Azzio's OWN hand-built packages are version-controlled and have NOTHING to do with the
# Arch mirrors. On the end-user box, `pacman -Syu`/-Su/-Sy must NEVER upgrade, downgrade,
# or replace them -- otherwise the instant an Arch repo ships a higher release of a package
# that shares the name, a routine update would silently overwrite our fork with stock
# upstream and undo every Azzio change. IgnorePkg is pacman's supported "do not touch these"
# lever: a listed package is skipped on -Syu with "warning: <pkg>: ignoring package upgrade".
#
# THIS LIST HOLDS ONLY REAL PACMAN PACKAGES. The user named five source dirs to freeze
# (file_manager, azzio, window_switcher, application_menu, hypervisor), but only ONE of them
# is actually a pacman package:
#
#   file_manager  -> pkgname `file_manager` (Azzio's own file manager, PKGBUILD-built --
#                    pkgbuild.py). It carries no provides/conflicts/replaces, so nothing on a mirror
#                    is linked to it by name and the old `replaces`-swap-back-to-stock-thunar trap no
#                    longer applies. Freezing `file_manager` by name is retained as belt-and-braces:
#                    IgnorePkg pins OUR package so that if a same-name repo package ever appeared, a
#                    bare -Syu could not supersede it. (Our repo being ordered first only wins the
#                    initial fresh install; IgnorePkg is what protects it forever after.)
#
#   azzio / window_switcher / application_menu / hypervisor  -> NOT pacman packages. They are
#                    C daemons / Python bundles the compiler writes straight into the airootfs
#                    under /usr/local/{bin,lib}/ via emit_plan() (azzio, azzio-window-switcher,
#                    azzio-application-menu-daemon, hypervisor); pacman never records them in its
#                    database. pacman only ever upgrades/replaces packages it OWNS, so files under
#                    /usr/local owned by no package are already invisible to -Syu -- there is no
#                    pkgname to match and no repo package that shares those names. They need no
#                    IgnorePkg entry, and adding one would be a MISLEADING no-op: pacman does not
#                    validate IgnorePkg names against installed packages, so a bogus name neither
#                    errors nor protects anything. So they are deliberately absent here.
#
# calamares and librewolf ARE also Azzio-built pacman packages (makepkg.PRODUCED), but the user
# did not name them for the freeze, so they are intentionally NOT frozen -- do not conflate this
# set with PRODUCED. Adding a future Azzio pacman package to the freeze is a one-line edit here.
FROZEN_PKGS = ("file_manager",)


def _options_block(
    cachedir: str | None,
    noextract: list[str] | None = None,
    ignorepkg: tuple[str, ...] | None = None,
) -> str:
    cachedir_line = f"CacheDir     = {cachedir}\n" if cachedir else "#CacheDir     = /var/cache/pacman/pkg/\n"
    # IgnorePkg freezes Azzio's own packages on the INSTALLED system so -Syu never
    # replaces them (see FROZEN_PKGS). Only installer_base_conf passes this; the build/
    # pacstrap/download confs leave it commented so the INITIAL install still seeds our
    # versions (freezing there would block the very install that installs ours).
    ignorepkg_line = (
        f"IgnorePkg   = {' '.join(ignorepkg)}\n" if ignorepkg else "#IgnorePkg   =\n"
    )
    # A single NoExtract line takes multiple space-separated paths. We NoExtract the
    # files the ISO overrides with its own airootfs copies so pacstrap's owning
    # package (filesystem) does not lay down a conflicting file:
    #   usr/lib/os-release -> our Azzio branding wins
    noextract_line = (
        f"NoExtract   = {' '.join(noextract)}" if noextract else "#NoExtract   ="
    )
    return _STD_HEADER.format(
        cachedir_line=cachedir_line,
        noextract_line=noextract_line,
        ignorepkg_line=ignorepkg_line,
    )


def _net_repos(multilib: bool) -> str:
    """The standard network repo sections (Include the host mirrorlist)."""
    block = "\n[core]\nInclude = /etc/pacman.d/mirrorlist\n\n[extra]\nInclude = /etc/pacman.d/mirrorlist\n"
    if multilib:
        block += "\n[multilib]\nInclude = /etc/pacman.d/mirrorlist\n"
    else:
        block += "\n#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n"
    return block


def download_conf(parallel_downloads: int = 5) -> str:
    """Host-independent configuration for the package-cache download step (cache-pkgs).

    parallel_downloads controls the ParallelDownloads line. It defaults to 5, but the
    download retry (downloader._sync_and_download) regenerates this config with a LOWER
    value on each attempt: archive.archlinux.org throttles aggressive parallel pulls
    ("too many errors from archive.archlinux.org"), and pacman exposes ParallelDownloads
    ONLY through the config file -- there is no CLI flag -- so backing off means
    re-emitting the config.

    Fetches from a PINNED Arch Linux Archive snapshot (ARCH_SNAPSHOT) and never Includes
    the host mirrorlist, so the fetch behaves identically on Manjaro, real Arch, and
    Docker AND is immune to the live repos being momentarily inconsistent (the
    `elfutils`/`base-devel` breakage that pinning fixes -- see ARCH_SNAPSHOT). SigLevel=
    Never (trust is re-established at pacstrap against the file:// repo) and no
    DownloadUser=alpm (pacman runs as root into root-owned scratch here).
    """
    mirrors = _SNAPSHOT_MIRRORS
    return f"""\
#
# Self-contained Arch Linux configuration used ONLY by the package-cache step to
# download the packages that get baked into the ISO's offline install repo.
#
# Why this file exists: the build may run on a non-Arch host (e.g. Manjaro).
# The default pacman configuration on such a host points at the wrong distro's
# repos/mirrors and lacks Arch-only packages, which aborts the build. This
# configuration is fully host-independent: it hard-codes Arch's official mirrors and
# never Includes the host mirrorlist.
#
# SigLevel = Never here only affects the *download* step. Final package trust is
# re-established at pacstrap time against the file:// [pacstrap-azzio-repo].
#
[options]
Architecture      = x86_64
HoldPkg           = pacman glibc
CheckSpace
ParallelDownloads = {parallel_downloads}
SigLevel          = Never
LocalFileSigLevel = Never
# Intentionally NO 'DownloadUser = alpm': pacman runs as root here and writes into
# root-owned scratch dirs, so the privilege-dropped alpm helper would fail.

[core]
{mirrors}
[extra]
{mirrors}
[multilib]
{mirrors}"""


def build_profile_conf(cachedir: str | None = None) -> str:
    """The archiso profile's pacman.conf for mkarchiso's internal pacstrap.

    Standard Arch base with two build-specific tweaks folded in:
      - NoExtract usr/lib/os-release -> our Azzio branding wins
      - an injected CacheDir         -> persistent build cache reuse

    Multilib is left OFF here. The offline rewrite to a file:// repo is applied
    separately (see ``switch_to_local_repo``) so this generator stays declarative.
    """
    conf = _options_block(cachedir=cachedir, noextract=_ISO_NOEXTRACT)
    conf += _STD_TESTING_TAIL
    conf += _net_repos(multilib=False)
    conf += "\n" + _CUSTOM_EXAMPLE
    return conf


def append_local_repo(conf: str, localrepo_path: str) -> str:
    """Insert the local file:// [pacstrap-azzio-repo] into a conf that KEEPS its
    network repos, ordered BEFORE [core]/[extra]. Used for ONLINE builds so
    mkarchiso's pacstrap pulls Arch packages from the mirrors AND Azzio's own
    packages from the local repo -- INCLUDING the ones we OVERRIDE.

    ORDERING IS LOAD-BEARING, and the earlier "listed last" design was WRONG: pacman
    resolving `-S <pkg>` picks the package from the FIRST repo (in config order) that
    carries the name -- it does NOT choose the globally-highest version across repos.
    Our repo ships our own `librewolf`/`calamares` and our `file_manager` package (pkgname
    `file_manager`, Azzio's own file manager; it carries no provides/conflicts/replaces).
    The manifest explicitly names `file_manager`, so pacstrap requests it by name and
    installs it from our repo; nothing else depends on, names, or pulls stock `thunar`, so
    stock extra/thunar is never referenced. For librewolf/calamares -- names that ALSO
    exist (or once existed) upstream -- placing our repo FIRST makes pacman prefer our build
    for every name we carry. That is safe here: the local repo was populated from the SAME
    pinned ALA snapshot, so every non-shipped name is byte-identical, and the only
    differences are packages we deliberately ship. (Historically, when our package was itself
    named `thunar`, repo-last let extra's stock thunar shadow ours and the live ISO booted the
    unfixed binary; repo-first fixed that. Naming the package `file_manager` removes the name
    clash entirely, so repo-first is now the whole guarantee.)"""
    if "[pacstrap-azzio-repo]" in conf:
        return conf
    section = (
        "[pacstrap-azzio-repo]\n"
        "SigLevel = Never\n"
        f"Server = file://{localrepo_path}\n\n"
    )
    # Insert immediately before the first ACTIVE network repo header so our repo
    # outranks [core]/[extra]. Match at column 0 (a real section header, anchored by
    # the preceding newline) so the commented "#[core]" example lines are never hit.
    for header in ("\n[core]\n", "\n[extra]\n", "\n[multilib]\n"):
        idx = conf.find(header)
        if idx != -1:
            cut = idx + 1  # after the leading "\n", before the header line
            return conf[:cut] + section + conf[cut:]
    # No active network repo section (shouldn't happen for a profile conf) -- fall back
    # to appending so the repo is at least present.
    return conf.rstrip("\n") + "\n\n" + section.rstrip("\n") + "\n"


def switch_to_local_repo(conf: str, localrepo_path: str) -> str:
    """Rewrite a profile pacman.conf so pacstrap installs from the local file://
    repo instead of the network mirrors -- the fully-offline rebuild path.

    Drops every network repo section ([core]/[extra]/[multilib], commented or
    not) and appends a single [pacstrap-azzio-repo] pointing at the local repo.
    SigLevel=Never: the cached packages have no .sig files and pacstrap runs with
    -G (no keyring copied into the target), so there is nothing to verify against.
    """
    out_lines: list[str] = []
    skip = False
    for line in conf.splitlines():
        stripped = line.strip()
        if stripped in ("[core]", "[extra]", "[multilib]"):
            skip = True
            continue
        if skip:
            # A repo section runs until the next blank line.
            if stripped == "":
                skip = False
            continue
        out_lines.append(line)
    out = "\n".join(out_lines).rstrip("\n")
    out += (
        "\n\n[pacstrap-azzio-repo]\n"
        "SigLevel = Never\n"
        f"Server = file://{localrepo_path}\n"
    )
    return out


def installer_base_conf() -> str:
    """The /etc/pacman.conf shipped to the INSTALLED system: plain Arch defaults
    with multilib enabled and no build tweaks -- PLUS IgnorePkg listing Azzio's own
    hand-built packages (FROZEN_PKGS) so a routine `pacman -Syu` on the end-user box
    never upgrades, downgrades, or replaces them with a stock Arch build. This is the
    same file Calamares lays down as the installed target's /etc/pacman.conf, so the
    freeze persists on the real system, not just the ephemeral live ISO session."""
    conf = _options_block(cachedir=None, noextract=None, ignorepkg=FROZEN_PKGS)
    conf += _STD_TESTING_TAIL
    conf += _net_repos(multilib=True)
    conf += "\n" + _CUSTOM_EXAMPLE
    return conf


def installer_pacstrap_conf() -> str:
    """The transient pacman.conf the on-disk installer swaps in during pacstrap:
    the standard base with the file:// offline install repo appended, and
    multilib left OFF (the installed base doesn't need it during pacstrap)."""
    conf = _options_block(cachedir=None, noextract=_ISO_NOEXTRACT)
    conf += _STD_TESTING_TAIL
    # All network repos commented out; only the local file:// repo is active.
    conf += (
        "\n#[core]\n#Include = /etc/pacman.d/mirrorlist\n"
        "\n#[extra]\n#Include = /etc/pacman.d/mirrorlist\n"
        "\n#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n"
    )
    conf += "\n" + _CUSTOM_EXAMPLE
    conf += (
        "\n[pacstrap-azzio-repo]\n"
        "SigLevel = Never\n"
        "Server = file:///mnt/pacstrap-azzio-repo/\n"
    )
    return conf
