"""Filesystem layout for the build.

Mirrors the directory scheme the old compile.sh used, so the Docker bind mounts
(cache/ output/ logs/) and the on-disk artifacts land in exactly the same places:

  REPODIR/                 repo root (where compile.sh lives)
    libraries/             the COMPILER's own modules (flat: compiler, paths, emit,
                           makepkg, pacman, pkgbuild, package_discovery, ...) -- the
                           general build machinery
    libraries/packages/    EVERY package the build ships, each its OWN directory module
                           (a dir with an __init__.py): the pacman manifest file
                           (packages.x86_64) plus one directory per package. This holds
                           BOTH the things WE author (application_menu/, azzio/,
                           passwords/, and the critically-modified calamares/) AND the
                           upstream software we merely tailor (openbox/, librewolf/,
                           kitty/, gedit/, file_manager/, fastfetch/, the per-app tweaks) --
                           all pure stdlib (no requirements.txt). package_discovery
                           imports each directory-with-__init__.py, so a package is added
                           or removed just by creating/deleting its directory.
    cache/                 persistent download cache (git-ignored, survives builds)
      build/               WORKDIR on a NATIVE run: disposable mkarchiso scratch
      pkgs/                persistent package repo + synced DBs (the offline store)
      pacman-pkg/          pacstrap CacheDir injected into the profile pacman.conf
    output/                BUILDDIR: the finished .iso lands here
    logs/                  compile-full.log + compile-steps.log

In DOCKER the disposable WORKDIR is moved OUT of the bind-mounted cache/ to a
container-internal path (/tmp/azzio-build) so its root-owned mkarchiso scratch
dies with the container and can never leave root-owned files locked on the host
(a hard `docker kill` sends an untrappable SIGKILL that skips the handback). The
persistent stores (cache/pkgs, cache/pacman-pkg) and the ISO (output/) stay on
the bind mounts and are chowned back to the host user. See WORKDIR below.
"""

from __future__ import annotations

import os
from pathlib import Path

# libraries/paths.py -> repo root is two parents up (libraries/ then the repo root).
REPODIR = Path(__file__).resolve().parents[1]
LIBDIR = REPODIR / "libraries"
# The compiler's own modules now live flat in libraries/ (there is no separate
# `azzio` package anymore), so the compiler package dir IS libraries/ itself.
PKGDIR = LIBDIR
# EVERY package the build ships (baked into the ISO), each its own directory module under here.
# This is the single home for BOTH the things WE author -- the package manifest (packages.x86_64),
# the application-menu source tree + build wiring (application_menu/), the `azzio` guest command
# line interface (azzio/), the passwords manager (passwords/), the critically-modified calamares
# install config (calamares/) -- AND the upstream software we merely tailor to Azzio (openbox/,
# librewolf/, kitty/, gedit/, file_manager/, fastfetch/, the per-app tweaks). package_discovery imports
# each directory-with-__init__.py from here. All pure Python standard library, so there is NO
# shared requirements.txt here (the only one in the repo is the repo-root requirements.txt the
# compiler itself uses for its test/dev deps). (Our OWN package recipes live in the flat
# pkgbuild.py module one level up, in libraries/, next to the rest of the build machinery.)
PACKAGESDIR = LIBDIR / "packages"
ASSETSDIR = REPODIR / "assets"

# Vendored ckbcomp: a Python 3 port of the upstream Perl ckbcomp (byte-identical
# output, no Perl in the tree). Arch does not package it (Debian/Manjaro-only), yet
# Calamares' keyboard-preview page shells out to `ckbcomp`, so we ship it in the repo
# and copy it to /usr/bin at build time. It is a companion file of the calamares package
# (Calamares is the only thing that uses it), so it lives at packages/calamares/ckbcomp.py.
# It is emitted to /usr/bin/ckbcomp (no .py suffix there -- it is an executable script the
# keyboard page runs by name).
CKBCOMP_SRC = PACKAGESDIR / "calamares" / "ckbcomp.py"

CACHEDIR = REPODIR / "cache"
BUILDDIR = REPODIR / "output"
LOGDIR = REPODIR / "logs"


def in_docker() -> bool:
    return Path("/.dockerenv").exists()


# WORKDIR: the disposable mkarchiso profile + scratch tree (airootfs, the squashfs
# work/ dir, the transient sync DB). mkarchiso creates it as ROOT and mounts
# proc/sys/dev/run inside it, so its files are root-owned.
#
# In DOCKER it must live OUTSIDE the host bind mounts (cache/ output/ logs/). If it
# sat under cache/ (a bind mount), a hard `docker kill` -- which sends an
# untrappable SIGKILL, so the ownership-handback never runs -- would leave those
# root-owned files on the host, and `git clean -Xdf` / `rm -rf cache/` would then
# fail without sudo. Placing it at a container-internal path means the root-owned
# scratch dies WITH the container and never touches the host. Only the PERSISTENT
# stores (cache/pkgs, cache/pacman-pkg) and the finished ISO (output/) stay on the
# bind mounts, and those are chowned back to the host user.
#
# On a NATIVE run there are no bind mounts, so keeping it in-repo (cache/build) is
# fine and keeps everything discoverable under the repo.
if in_docker():
    WORKDIR = Path("/tmp/azzio-build")
else:
    WORKDIR = CACHEDIR / "build"

# Persistent package stores (the offline-rebuild cache).
PKG_REPO = CACHEDIR / "pkgs" / "repo"
PKG_DB = CACHEDIR / "pkgs" / "db"
PKG_SYNC_DB = PKG_DB / "sync"
# Recipe fingerprints for OUR built packages (calamares/librewolf/file_manager): one small
# JSON sidecar per package recording the hash of the recipe that produced its cached
# .pkg.tar.zst. makepkg reuses a cached own package only if this still matches the
# current recipe (see makepkg._repo_is_current) -- that is what stops a stale
# calamares, built before a new source patch, from being shipped. Kept OUT of PKG_REPO
# on purpose: PKG_REPO (and PKG_DB) are cp -r'd wholesale into the ISO payload, and
# build-only metadata has no business on the installed target's offline repo.
PKG_FINGERPRINTS = CACHEDIR / "pkgs" / "recipe-fingerprints"
LOCALREPO_INDEX = PKG_REPO / "pacstrap-azzio-repo.db"
LOCALREPO_INDEX_TAR = PKG_REPO / "pacstrap-azzio-repo.db.tar.gz"

# pacstrap's CacheDir, injected into the profile pacman.conf so the ~1200 live-ISO
# packages are reused across builds instead of re-downloaded.
PACSTRAP_CACHE = CACHEDIR / "pacman-pkg"

# makepkg's SRCDEST: the persistent cache for source=() tarballs our own packages
# pull from upstream (calamares' release tarball from Codeberg). Deliberately a
# SIBLING of the makepkg scratch (CACHEDIR / "makepkg"), NOT a subdir of it: the
# scratch is `rm -rf`'d at the start of every online build (build_own_packages),
# so a per-recipe .src under it never survives to the next run and every compile
# re-downloads the tarball -- hammering Codeberg (which throttles hard). Kept here
# instead, the tarball is fetched ONCE (first from-scratch compile) and makepkg
# skips the download on every later run when the cached file still matches the
# recipe's pinned sha256. Git-ignored like the rest of cache/.
MAKEPKG_SRC_CACHE = CACHEDIR / "makepkg-src"

# Logs.
FULL_LOG = LOGDIR / "compile-full.log"
STEPS_LOG = LOGDIR / "compile-steps.log"

# Verbatim data files.
PACKAGES_FILE = PACKAGESDIR / "packages.x86_64"
# The Azzio application-menu package (C / GTK3): the menu source files (menu.c +
# siblings, theme.h, Makefile) live DIRECTLY here alongside application_menu.py -- the
# build wiring that COMPILES them into the daemon binary and installs it, ships the
# pure-Python launcher (launcher.py), and generates the .desktop entry. The whole menu
# is OURS, so it is a package here, not a patch.
APPLICATION_MENU_DIR = PACKAGESDIR / "application_menu"
# The Azzio window-switcher package (C / GTK3): the alt-tab overlay (switcher.c +
# windows/thumbnail/layout/ordering, a Makefile) lives DIRECTLY here alongside
# window_switcher.py -- the build wiring that compiles them into the daemon binary and
# ships the launcher. It reuses four application-menu translation units as build inputs.
WINDOW_SWITCHER_DIR = PACKAGESDIR / "window_switcher"
# The Azzio timedate site (Flask Time + Calendar home page): applications.py/page.py/assets.py +
# timedate.py (the build wiring that copies them into the airootfs, installs the launcher, and
# ships the systemd service). LibreWolf lands on this page (startup + Home), so the site was
# FOLDED INTO the librewolf package as sibling submodules -- its sources live in
# packages/librewolf/. Served at localhost:49154. timedate.py reads its own sources from here.
TIMEDATE_DIR = PACKAGESDIR / "librewolf"
# The Azzio passwords package (encrypted GPG/AES256 terminal password manager): ONE flat
# directory holding the entry script (passwords.py), the one-time setup script, every working
# module (config/cryptography/model/terminal_user_interface/...), and packaging.py (the build
# wiring that copies them into the airootfs and installs the /usr/local/bin/passwords
# launcher). A pure-Python app we author, so it lives under libraries/packages/ like timedate.
# The `passwords` command unlocks a store at ~/Vault/passwords.txt.gpg (see
# packages/passwords/config.py).
PASSWORDS_DIR = PACKAGESDIR / "passwords"
# The Azzio backup package (home-directory backup -- the `backup` command): a flat
# directory holding the entry script (backup.py) and packaging.py (the build wiring
# that copies it into the airootfs and installs the /usr/local/bin/backup launcher).
# A pure-Python app we author, so it lives under libraries/packages/ like passwords.
# `backup` rolls the user's top-level home folders (skipping ~/Ignore and dot files,
# keeping symlinks as links) into ~/backup_<date>.tar.gz.gpg (GPG/AES256).
BACKUP_DIR = PACKAGESDIR / "backup"
# The Azzio hypervisor package (per-directory QEMU/KVM VM runner -- the `hypervisor`
# command): a flat directory holding the entry script (command_line_interface.py), every
# working module (configuration/configuration_schema/configuration_watcher/
# configuration_defaults/graphics/checks/qemu_command/virtual_machine) and packaging.py
# (the build wiring that copies them into the airootfs and installs the
# /usr/local/bin/hypervisor launcher). A pure-Python app we author, so
# it lives under libraries/packages/ like backup. `hypervisor` spins up a QEMU/KVM VM
# whose identity is derived from the directory it is run in (name/disk/NVRAM/shared/SSH),
# with all settings in a per-directory hypervisor.cfg. HOST-side tool -- distinct from the
# guest-side `azzio --sshd-hypervisor`.
HYPERVISOR_DIR = PACKAGESDIR / "hypervisor"
# The `azzio` guest command line interface is a Python PACKAGE now (libraries/packages/azzio/): it grew a
# `theme` subcommand (and more to come), so the single module was split into small modules
# (common, country_table, resolver, theme, sshd, command_line_interface). The single /usr/local/bin/azzio
# script that ships to the guest is reassembled from those modules by the package's
# bundle.bundle_source(); the compiler then injects the country->locale table from
# packages/calamares/locale.py between the AZZIO_CC markers (which now live in
# country_table.py). See packages/openbox openbox.azzio_command_line_interface().
#
# This dir ALSO holds the bare-`azzio` TERMINAL UI's C sources (main.c/render.c/model.c/
# preview.c, terminal_user_interface.h + siblings, Makefile) -- there is only ONE program, `azzio`, and the UI
# is C for speed, so it lives next to the Python command line interface it drives rather than in a separate
# package. packages/azzio/terminal_user_interface_build.py is the build wiring that compiles them into the
# azzio binary; bare `azzio` execs it (see packages/azzio/terminal_user_interface.py). terminal_user_interface_build's
# _csrc_files() picks up only the C inputs, never the .py modules, so the two coexist here.
AZZIO_COMMAND_LINE_INTERFACE_DIR = PACKAGESDIR / "azzio"
# The module whose source carries the AZZIO_CC_TABLE_START/END markers (the compiler
# regenerates the COUNTRY_TABLE literal between them from the single source of truth).
AZZIO_COMMAND_LINE_INTERFACE_TABLE_MODULE = AZZIO_COMMAND_LINE_INTERFACE_DIR / "country_table.py"

# Inside the archiso profile tree, the airootfs root and the azzio payload dir
# baked into the live/installed system.
AIROOTFS = WORKDIR / "work" / "x86_64" / "airootfs"


def is_root() -> bool:
    return os.geteuid() == 0
