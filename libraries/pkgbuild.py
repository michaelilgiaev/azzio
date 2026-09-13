"""Azzio OWN package recipes, authored as configuration-as-Python.

Everything the ISO installs that is NOT in the official Arch repositories is
built from recipes WE write and maintain here -- never from the AUR or any
community source. Like the rest of azzio.config, each artifact (the PKGBUILDs
and their companion files) is held as a Python string and emitted into the build
tree by compiler.py; the emitted files are then consumed by `makepkg`, the official
Arch build tool, which produces *.pkg.tar.zst dropped into the ISO's offline
repo. No AUR helper (yay/paru/...) is used.

Two packages are built. Neither is in an official Arch repo, so both are built
in EVERY tier; --full-compile only changes the recipe librewolf uses:

  calamares   -- the graphical system installer (Manjaro-style). It USED to be an
                 official Arch package (extra/calamares), but Arch DROPPED it --
                 it is now AUR-only, and this project never builds from the AUR.
                 So it is compiled from OUR own recipe below in BOTH tiers: a
                 moderate C++/CMake build (minutes), with the release tarball
                 verified by the pinned sha256 (makepkg aborts on mismatch;
                 upstream ships no detached .sig for it). recipe_dirs() emits it
                 unconditionally now.

  librewolf   -- the privacy-hardened Firefox fork. A from-source Firefox build
                 takes 1.5-3+ hours and ~16 GB RAM, so there are TWO recipes:
                   * DEFAULT tier (`compile.sh`)          -> pkgbuild_librewolf()
                     repackages LibreWolf's official prebuilt tarball, verified by
                     BOTH a pinned sha256 AND its OpenPGP signature.
                   * FULL tier   (`compile.sh --full-compile`) -> pkgbuild_librewolf_src()
                     compiles LibreWolf from Firefox source via LibreWolf's bsys6
                     build harness.
                 recipe_dirs(full_compile) picks which pair of recipes to emit.

Pinned upstream facts (versions, URLs, checksums, signing key) live as the
constants below -- the single source of truth. All checksums were obtained by
downloading the real artifacts and hashing them, and are re-checked by makepkg
at build time (it aborts on mismatch). See update notes at the bottom.
"""

from __future__ import annotations

from pathlib import Path

# The calamares package recipe (its pinned facts, the three Azzio source patches,
# and the PKGBUILD text) lives in its own module -- the patch-authoring is large and
# self-contained. Re-exported here so the public surface stays flat: callers/tests use
# pkgbuild.CALAMARES_VERSION, pkgbuild.calamares_defaults_patch(),
# pkgbuild.pkgbuild_calamares(), etc. unchanged, and recipe_dirs() below assembles the
# calamares recipe dir from these names.
from pkgbuild_calamares import (  # noqa: F401  (re-exported for the public API)
    CALAMARES_DEFAULTS_PATCH_NAME,
    CALAMARES_FINISH_BUTTONS_PATCH_NAME,
    CALAMARES_NETWORKCFG_STATIC_PATCH_NAME,
    CALAMARES_NETWORKQ_PATCH_NAME,
    CALAMARES_REGION_KEYBOARD_PATCH_NAME,
    CALAMARES_SHA256,
    CALAMARES_VERSION,
    calamares_defaults_patch,
    calamares_finish_buttons_patch,
    calamares_networkcfg_static_patch,
    calamares_networkq_patch,
    calamares_region_keyboard_patch,
    pkgbuild_calamares,
)

# ---------------------------------------------------------------------------
# Pinned upstream facts (single source of truth). Calamares' pinned facts moved to
# pkgbuild_calamares.py (imported above) with the rest of its recipe.
# ---------------------------------------------------------------------------
# LibreWolf: upstream tag is "153.0.1-1"; pacman-legal pkgver is "153.0.1.1".
LIBREWOLF_VERSION = "153.0.1-1"
LIBREWOLF_PKGVER = "153.0.1.1"
# sha256 from upstream's published .sha256sum, re-verified by download + hash.
LIBREWOLF_SHA256 = "7b56e06071ece9e711a1c811e64129a3a14775c5fe00a4b777e5cbb0b087b5b5"
# LibreWolf release signing key -- the PRIMARY key fingerprint of
# "LibreWolf Maintainers <gpg@librewolf.net>". makepkg's validpgpkeys=() must list
# the PRIMARY key, NOT the signing subkey: the tarball's detached .sig is made by
# an ed25519 *subkey* (915585A1C36690B1 / 230FE8E0...C36690B1), and makepkg maps a
# signing subkey back to its primary and requires THAT primary to be in
# validpgpkeys. Pinning the subkey fingerprint here made makepkg abort with
# "invalid public key 662E3CDD...2B12EF16" (the primary it actually needs). Verify
# on update: `gpg --list-packets <tarball>.sig` shows the signing subkey keyid;
# `gpg --recv-keys <that keyid>` then shows the primary under `pub`.
LIBREWOLF_PGP_KEY = "662E3CDD6FE329002D0CA5BB40339DD82B12EF16"

# Azzio File Manager: built from the VENDORED source tree at packages/file_manager/source/ (a git
# clone committed into the Azzio repo, relicensed GPL-3.0). It is OUR OWN file manager, not a
# versioned upstream drop-in, so it carries NO semver -- the package is a plain monotonic pkgver=1
# (see pkgbuild_file_manager). The only change over the vendored tree is the Azzio symlink-resolve
# modification, baked DIRECTLY into the committed C source (packages/file_manager owns it; see that
# package's __init__ docstring). No tarball URL and no sha256: the source is local and
# version-controlled, so integrity comes from git, not a download hash. No build-time patch
# companion either -- the tree already carries the change. The source dirname and pinned commit
# live on the file_manager package (single source of truth for the source).
from packages import file_manager as _file_manager  # noqa: E402  (source facts live on the package)


# ---------------------------------------------------------------------------
# librewolf -- shared companion files (used by BOTH tiers)
# ---------------------------------------------------------------------------
def librewolf_desktop() -> str:
    return """\
[Desktop Entry]
Name=LibreWolf
GenericName=Web Browser
Comment=Browse the web (Azzio build, sessions/cookies persist)
Exec=/opt/librewolf/librewolf %u
Icon=librewolf
Terminal=false
Type=Application
MimeType=text/html;text/xml;application/xhtml+xml;application/xml;application/vnd.mozilla.xul+xml;application/rss+xml;application/rdf+xml;image/gif;image/jpeg;image/png;x-scheme-handler/http;x-scheme-handler/https;x-scheme-handler/ftp;x-scheme-handler/chrome;video/webm;application/x-xpinstall;
StartupNotify=true
StartupWMClass=librewolf
Categories=Network;WebBrowser;
Keywords=Internet;WWW;Browser;Web;Explorer;
Actions=new-window;new-private-window;

[Desktop Action new-window]
Name=Open a New Window
Exec=/opt/librewolf/librewolf --new-window %u

[Desktop Action new-private-window]
Name=Open a New Private Window
Exec=/opt/librewolf/librewolf --private-window %u
"""


# NOTE on the LibreWolf AutoConfig override (librewolf.overrides.cfg): it is NO LONGER
# a package companion file. LibreWolf's compiled AutoConfig loader reads it from the
# user's PROFILE dir (~/.config/librewolf/librewolf/), never from /opt, so shipping it
# in the package did nothing. It is delivered as a HOME file (mirrored into /etc/skel)
# by packages/librewolf.emit_plan(), which owns both its content AND its location. This
# recipe therefore neither generates nor installs it.


# ---------------------------------------------------------------------------
# librewolf -- DEFAULT tier (repackage the verified upstream tarball)
# ---------------------------------------------------------------------------
def pkgbuild_librewolf() -> str:
    dl = f"https://codeberg.org/api/packages/librewolf/generic/librewolf/{LIBREWOLF_VERSION}"
    tar = f"librewolf-{LIBREWOLF_VERSION}-linux-x86_64-package.tar.xz"
    return f"""\
# Maintainer: Azzio <https://github.com/michaelilgiaev/azzio>
#
# =============================================================================
# Azzio OWN PKGBUILD -- librewolf (DEFAULT tier: repackage verified upstream)
# =============================================================================
# NOT a community/AUR recipe. Written + maintained by the Azzio project.
# Generated by packages.pkgbuild.
#
# A from-source LibreWolf/Firefox compile takes 1.5-3+ hours and needs ~16 GB
# RAM. To keep the DEFAULT `compile.sh` build fast, this recipe repackages
# LibreWolf's OFFICIAL prebuilt generic-Linux tarball, verified TWO ways:
#   1. pinned sha256sum (from upstream's published .sha256sum), and
#   2. detached OpenPGP signature (.sig) against the LibreWolf release key.
# For an all-self-compiled build use `compile.sh --full-compile`, which selects
# the source recipe instead.
#
# SOURCE (fully auditable):
#   Build system : https://codeberg.org/librewolf/bsys6
#   Website      : https://librewolf.net/
#   Tarball      : {dl}/{tar}
#   Signature    : {dl}/{tar}.sig   (key {LIBREWOLF_PGP_KEY})
#   Checksum src : {dl}/{tar}.sha256sum
#   Mirror note  : dl.librewolf.net is the upstream CDN; Codeberg's package API
#                  hosts the same files (same sha256) and is the active mirror.
#   License      : MPL-2.0
# The tarball is built by LibreWolf from Firefox source + LibreWolf's public
# patch set, so the lineage traces to scrutinizable source even in this path.
#
# AZZIO CUSTOMISATION: LibreWolf clears cookies + history on shutdown by
# default; Azzio relaxes that (sessions/cookies persist) + hides the bookmarks
# toolbar via LibreWolf's supported AutoConfig override. That override is delivered
# as a HOME file at the profile path LibreWolf actually reads (NOT packaged here --
# see packages/librewolf); this recipe is otherwise stock LibreWolf.
# =============================================================================

pkgname=librewolf
pkgver={LIBREWOLF_PKGVER}
_lwver={LIBREWOLF_VERSION}
pkgrel=1
pkgdesc="Privacy-hardened Firefox fork, session/cookie persistence (Azzio build)"
arch=('x86_64')
url="https://librewolf.net/"
license=('MPL-2.0')
depends=('gtk3' 'libxt' 'mime-types' 'dbus' 'ffmpeg' 'nss' 'ttf-font'
         'libpulse' 'libnotify' 'pciutils')
options=('!strip')

_dl="{dl}"
source=(
  "librewolf-${{_lwver}}-linux-x86_64-package.tar.xz::${{_dl}}/librewolf-${{_lwver}}-linux-x86_64-package.tar.xz"
  "librewolf-${{_lwver}}-linux-x86_64-package.tar.xz.sig::${{_dl}}/librewolf-${{_lwver}}-linux-x86_64-package.tar.xz.sig"
  'librewolf.desktop'
)
# Tarball: pinned sha256 (+ GPG). .sig: GPG-checked (SKIP sha). Local .desktop:
# shipped in-repo, reviewed in packages.pkgbuild (SKIP sha). The AutoConfig override
# is NOT packaged (LibreWolf reads it from the profile dir, not /opt) -- it ships as a
# home file via packages/librewolf.emit_plan().
sha256sums=('{LIBREWOLF_SHA256}' 'SKIP' 'SKIP')
validpgpkeys=('{LIBREWOLF_PGP_KEY}')

package() {{
  # Tarball extracts to a top-level librewolf/ dir (Firefox-style layout).
  install -d "$pkgdir/opt"
  cp -a "$srcdir/librewolf" "$pkgdir/opt/librewolf"

  install -d "$pkgdir/usr/bin"
  ln -s /opt/librewolf/librewolf "$pkgdir/usr/bin/librewolf"

  install -Dm644 "$srcdir/librewolf.desktop" \\
    "$pkgdir/usr/share/applications/librewolf.desktop"

  local icon="$srcdir/librewolf/browser/chrome/icons/default/default128.png"
  [[ -f "$icon" ]] && install -Dm644 "$icon" \\
    "$pkgdir/usr/share/icons/hicolor/128x128/apps/librewolf.png"

  # NOTE: the Azzio persistence/bookmarks override is NOT installed here. LibreWolf's
  # AutoConfig loader reads librewolf.overrides.cfg from the user's PROFILE dir
  # (~/.config/librewolf/librewolf/), never from /opt, so it is delivered as a home file
  # by packages/librewolf.emit_plan() (compiler.py) instead.
}}
"""


# ---------------------------------------------------------------------------
# librewolf -- FULL tier (compile from Firefox source via bsys6)
# ---------------------------------------------------------------------------
def pkgbuild_librewolf_src() -> str:
    return f"""\
# Maintainer: Azzio <https://github.com/michaelilgiaev/azzio>
#
# =============================================================================
# Azzio OWN PKGBUILD -- librewolf (FULL-COMPILE tier: build from source)
# =============================================================================
# NOT a community/AUR recipe. Written + maintained by the Azzio project.
# Generated by packages.pkgbuild. Selected ONLY by `compile.sh --full-compile`.
#
# ///////////////////////////////////////////////////////////////////////////
#  HEAVY BUILD WARNING: a from-source LibreWolf/Firefox compile takes 1.5-3+
#  hours on a strong multi-core machine and needs ~16 GB RAM + tens of GB disk.
#  The default `compile.sh` (repackage tier) exists to avoid this.
# ///////////////////////////////////////////////////////////////////////////
#
# SOURCE (fully auditable):
#   Build system : https://codeberg.org/librewolf/bsys6   (tag {LIBREWOLF_VERSION})
#   which fetches Mozilla Firefox source (release 153.0) + LibreWolf's public
#   patch set/settings, all in the codeberg repos.
#   License      : MPL-2.0
#
# INTEGRITY: bsys6 verifies the Firefox source it downloads against Mozilla's
# published checksums as part of its own build. We pin bsys6 by git tag.
# =============================================================================

pkgname=librewolf
pkgver={LIBREWOLF_PKGVER}
_lwver={LIBREWOLF_VERSION}
pkgrel=1
pkgdesc="Privacy-hardened Firefox fork built FROM SOURCE, persistence (Azzio build)"
arch=('x86_64')
url="https://librewolf.net/"
license=('MPL-2.0')
depends=('gtk3' 'libxt' 'mime-types' 'dbus' 'ffmpeg' 'nss' 'ttf-font'
         'libpulse' 'libnotify' 'pciutils')
# The Firefox build toolchain -- the bulk of what makes the full compile heavy.
makedepends=('rust' 'clang' 'llvm' 'lld' 'nodejs' 'cbindgen' 'nasm' 'yasm'
             'python' 'python-setuptools' 'unzip' 'zip' 'gawk' 'perl' 'wget'
             'mercurial' 'git' 'make' 'pkgconf' 'gtk3' 'nss' 'gcc' 'which'
             'mesa' 'libpulse' 'dbus-glib' 'alsa-lib')
options=('!strip' '!lto' '!debug')

source=(
  "librewolf-bsys6::git+https://codeberg.org/librewolf/bsys6.git#tag=${{_lwver}}"
  'librewolf.desktop'
)
# The AutoConfig override is NOT packaged (LibreWolf reads it from the profile dir, not
# /opt) -- it ships as a home file via packages/librewolf.emit_plan().
sha256sums=('SKIP' 'SKIP')

build() {{
  cd "$srcdir/librewolf-bsys6"
  # bsys6's documented top-level targets: fetch Firefox source + LibreWolf
  # patches/settings, build, then produce the generic-linux package tree.
  #
  # `make fetch` is the ONLY network step. On an OFFLINE --full-compile rerun the
  # Azzio build sets AZZIO_OFFLINE=1 and passes makepkg --noextract, so this
  # same bsys6 tree (already populated by the prior online run's `make fetch`) is
  # reused as-is: we skip the fetch and go straight to build. If the tree were
  # gone (a wiped cache) `make build` fails loudly here -- we never silently go
  # back online. On the normal online run AZZIO_OFFLINE is unset and `make fetch`
  # populates the tree as before.
  if [[ -z "${{AZZIO_OFFLINE:-}}" ]]; then make fetch; fi
  # -j caps parallel compile jobs so the Firefox build (bsys6 -> mach) does not
  # pin every core for hours. AZZIO_JOBS is exported by makepkg (= cores -
  # reserved); it defaults to 1 if unset so an isolated recipe run stays safe.
  make build -j"${{AZZIO_JOBS:-1}}"
  make package
}}

package() {{
  cd "$srcdir/librewolf-bsys6"
  # Locate the produced package tree / tarball (bsys6 emits under its own dir).
  local tree
  tree="$(find . -maxdepth 4 -type d -name librewolf -path '*obj*' 2>/dev/null | head -1)"
  if [[ -z "$tree" ]]; then
    local tarball
    tarball="$(find . -maxdepth 3 -name 'librewolf-*.tar.xz' 2>/dev/null | head -1)"
    [[ -n "$tarball" ]] || {{ echo "librewolf-src: could not locate build output"; return 1; }}
    bsdtar -xf "$tarball" -C "$srcdir"
    tree="$srcdir/librewolf"
  fi

  install -d "$pkgdir/opt"
  cp -a "$tree" "$pkgdir/opt/librewolf"

  install -d "$pkgdir/usr/bin"
  ln -s /opt/librewolf/librewolf "$pkgdir/usr/bin/librewolf"

  install -Dm644 "$srcdir/librewolf.desktop" \\
    "$pkgdir/usr/share/applications/librewolf.desktop"

  local icon="$pkgdir/opt/librewolf/browser/chrome/icons/default/default128.png"
  [[ -f "$icon" ]] && install -Dm644 "$icon" \\
    "$pkgdir/usr/share/icons/hicolor/128x128/apps/librewolf.png"

  # NOTE: the Azzio override is NOT installed here -- LibreWolf reads it from the
  # profile dir, not /opt, so packages/librewolf.emit_plan() (compiler.py) delivers it as
  # a home file. See packages/librewolf.
}}
"""


# ---------------------------------------------------------------------------
# file_manager -- the Azzio symlink-resolve modification (baked into the VENDORED source)
# ---------------------------------------------------------------------------
# The user wants the file manager's location bar / window title to ALWAYS show the real
# filesystem path, even when a directory is reached through a symlink (e.g. the convenience link
# ~/Trash -> ~/.local/share/Trash/files created by packages/file_manager/home_directory):
# "I WANT FULL ACTUAL PATHS, /home/main/.local/share/Trash/files/". The vendored code base has NO
# config lever for this, so the behaviour is changed in the source. Rather than a build-time patch,
# the change is applied DIRECTLY to the committed C source in the vendored tree
# (packages/file_manager/source/thunar/thunar-window.c): thunar_window_set_current_directory() --
# the single chokepoint every directory change flows through -- realpath()s a symlinked directory
# and re-enters with the canonical target, so the path bar, title and history all show the real
# path. The change lives in version control (git diff shows it) -- there is no separate .patch
# artifact. See the packages/file_manager __init__ docstring for the full story. There is
# therefore no resolve_symlink_patch() builder and no patch companion in the recipe dir.


def pkgbuild_file_manager() -> str:
    src = _file_manager.SOURCE_SUBDIR
    return f"""\
# Maintainer: Azzio <https://github.com/michaelilgiaev/azzio>
#
# =============================================================================
# Azzio OWN PKGBUILD -- file_manager  (Azzio File Manager; generated by packages.pkgbuild)
# =============================================================================
# NOT a community/AUR recipe. Written + maintained by the Azzio project.
#
# Azzio File Manager is the Azzio project's own GTK file manager, shipped as the default file
# manager. It is OUR OWN thing -- not a versioned upstream drop-in -- so it carries NO semver: the
# pkgver is a plain monotonic `1`. It needs ONE behaviour the vendored code base cannot be
# configured to do: always show the fully-resolved (symlink-dereferenced) path in the location
# bar/title. This recipe builds it from the VENDORED source, which already carries that change.
#
# It is the ONLY file manager on the image: pkgname is `file_manager` and the manifest names it
# directly, so pacstrap installs it and stock extra/thunar is never referenced (no manifest package
# depends on, names, or pulls `thunar`, so no provides/conflicts/replaces shim is needed). The
# on-disk names the built binary lays down are UNCHANGED -- it is compiled from the vendored source,
# which still calls itself thunar internally, so the running binary owns ~/.config/Thunar/*,
# thunar.desktop, the `thunar` xfconf channel and textdomain. Renaming those is a separate,
# build-breaking effort (forking the vendored source), not part of dropping the shim.
#
# SOURCE (fully auditable, version-controlled):
#   The source is a git clone committed straight into the Azzio repo at
#   packages/file_manager/{src}/ (see the packages/file_manager __init__ for the pinned commit),
#   relicensed GPL-3.0. makepkg._emit_recipes copies that tree into this recipe dir as ./{src} at
#   build time; prepare() copies it from $startdir into a writable build tree. There is NO
#   source=() entry (makepkg's source array cannot name a directory) and no download. The Azzio
#   symlink-resolve change is baked directly into that committed source (thunar/thunar-window.c),
#   so there is no build-time patch step and no .patch artifact.
#   Upstream lineage: https://gitlab.xfce.org/xfce/thunar
#   License : GPL-3.0-or-later
#
# INTEGRITY: the source is local and version-controlled, so git is the integrity
# anchor -- there is nothing to download and no tarball hash.
#
# FROM SOURCE IN EVERY TIER: a moderate autotools C build (a couple of minutes). A
# git checkout ships no generated ./configure, so build() bootstraps it with
# ./autogen.sh (xdt-autogen) first. Built and dropped into the offline repo so pacstrap installs
# OUR file_manager. Our repo is ordered first and the manifest names `file_manager` directly, so
# ours is what gets installed; stock extra/thunar is never named or pulled.
# =============================================================================

pkgname=file_manager
# NO semver: this is Azzio's own file manager, not a versioned upstream drop-in, so the pkgver is
# a plain monotonic integer. Bump it (and/or pkgrel) whenever the vendored source or recipe
# changes enough to warrant a rebuild on installed systems.
pkgver=1
pkgrel=1
pkgdesc="Azzio File Manager (GTK; resolves symlink paths)"
arch=('x86_64')
url="https://github.com/michaelilgiaev/azzio"
license=('GPL-3.0-or-later')
groups=('xfce4')

# This package IS the file manager on the image. It carries NO provides/conflicts/replaces: nothing
# in the manifest depends on, names, or pulls the stock `thunar` package anymore (the two consumers
# that did -- thunar-volman, thunar-archive-plugin -- were dropped), so there is no stock thunar to
# shim for or to fence off. Our repo is ordered first and the manifest names `file_manager`
# directly, so pacstrap installs this and stock extra/thunar is never referenced.

# Runtime deps mirror what the stock file-manager binary needs (the same set extra/thunar Depends
# On), so the built package pulls exactly what the binary requires at runtime.
depends=(
  'desktop-file-utils' 'libexif' 'hicolor-icon-theme' 'libnotify'
  'pcre2' 'libgudev' 'exo' 'libxfce4util' 'libxfce4ui'
)
# Build deps: the -dev headers/tools the autotools build needs. gettext/intltool for
# the translations, xfce4-dev-tools for the xdt-autogen macros, and the autotools
# themselves (autoconf/automake/libtool/pkgconf) -- REQUIRED here because a git
# checkout (unlike a release tarball) ships NO generated ./configure, so we bootstrap
# it with ./autogen.sh.
#   glib2-devel is REQUIRED for the maintainer-mode codegen: the `if MAINTAINER_MODE`
#   rules that generate thunar-marshal.c/.h and the gdbus stubs shell out to
#   glib-genmarshal and gdbus-codegen, and those binaries live in glib2-devel (NOT the
#   glib2 runtime that gtk3 pulls in). Without it `make` dies with
#   "glib-genmarshal: command not found" / "gdbus-codegen: No such file or directory".
makedepends=(
  'gtk3' 'glib2-devel' 'gettext' 'intltool' 'gobject-introspection'
  'xfce4-dev-tools' 'autoconf' 'automake' 'libtool' 'pkgconf'
)
optdepends=(
  'gvfs: trash support, mounting with GIO'
  'tumbler: thumbnails'
)
options=('!emptydirs')

# NO source=() entry. The Azzio File Manager source is a VENDORED DIRECTORY
# (packages/file_manager/{src}), and makepkg's source=() array accepts only regular files or
# URLs -- a bare directory name triggers "<name> was not found in the build directory and is not
# a URL" (makepkg tests a local source with -f, a regular-file test a directory fails). So there
# is nothing to fetch or hash here; makepkg._emit_recipes copies the vendored tree into THIS
# recipe dir (== $startdir at build time) as ./{src}, and prepare() copies it from $startdir into
# a writable build tree itself. Integrity comes from git (the tree is version-controlled), so
# there is no download and no sha256.
source=()
sha256sums=()

prepare() {{
  # $startdir is this recipe dir (makepkg sets startdir=$PWD and we run makepkg with the recipe
  # dir as CWD). ./{src} there is the recipe-dir copy of the vendored tree. Copy it to a real,
  # writable build directory so autogen/configure/make write THERE, never back into the vendored
  # copy. -L dereferences any symlink so we copy the tree, not a link.
  rm -rf "$srcdir/build-tree"
  cp -aL "$startdir/{src}" "$srcdir/build-tree"
}}

build() {{
  cd "$srcdir/build-tree"
  # A git checkout ships no generated ./configure -- bootstrap it with autogen (xdt-autogen).
  # NOCONFIGURE=1 makes autogen.sh stop after generating ./configure so we can pass our own
  # flags below (otherwise xdt-autogen would run configure with its defaults). REQUIRED
  # VERSION is satisfied by xfce4-dev-tools in makedepends.
  NOCONFIGURE=1 ./autogen.sh
  # Match a stock FileManager build. gtk-doc/apidocs off (extra deps, pointless on the ISO).
  # --enable-maintainer-mode is REQUIRED for a git checkout: the rules that generate the
  # built sources (thunar-marshal.c/.h via glib-genmarshal, the gdbus-codegen stubs, the
  # gresource bundle) live inside `if MAINTAINER_MODE` in thunar/Makefile.am, and this
  # tree's configure.ac uses the bare AM_MAINTAINER_MODE() which DEFAULTS OFF. A release
  # tarball ships those files pre-generated so it does not matter, but a checkout does not,
  # so without this flag `make` dies with "No rule to make target 'thunar-marshal.c'".
  # xdt-autogen would normally pass this itself, but NOCONFIGURE=1 skips its configure run.
  ./configure \\
    --enable-maintainer-mode \\
    --prefix=/usr \\
    --sysconfdir=/etc \\
    --libexecdir=/usr/lib \\
    --localstatedir=/var \\
    --disable-static \\
    --disable-gtk-doc \\
    --disable-gtk-doc-html \\
    --disable-silent-rules
  # -j caps parallel compile jobs (AZZIO_JOBS is exported by makepkg, = cores -
  # reserved, default 1) so the build does not pin the whole machine.
  make -j"${{AZZIO_JOBS:-1}}"
}}

package() {{
  cd "$srcdir/build-tree"
  make DESTDIR="$pkgdir" install
}}
"""


# ---------------------------------------------------------------------------
# Recipe emission plan: (dirname, {filename: content}) tuples.
# compiler.py iterates this to write each recipe dir into the build tree, then the
# makepkg stage builds each and drops the result into the offline repo.
# ---------------------------------------------------------------------------
def recipe_dirs(full_compile: bool) -> list[tuple[str, dict[str, str]]]:
    """Which recipes to emit. BOTH calamares and librewolf are built in EVERY
    tier now -- neither is in an official Arch repo (librewolf never was;
    calamares was dropped from extra/ and is AUR-only). --full-compile only
    changes the RECIPE, not the set:

      calamares : always compiled from source (pinned-sha256 Codeberg tarball,
                  a moderate C++/CMake build of minutes). There is no prebuilt
                  Arch binary to fall back to anymore, so both tiers use the
                  same source recipe.
      librewolf : default = repackage the verified upstream binary tarball;
                  --full-compile = compile from Firefox source (1.5-3+ hours)."""
    # The .desktop is the ONLY companion file the package ships now. The AutoConfig
    # override (librewolf.overrides.cfg) is NOT packaged: LibreWolf reads it from the
    # user's PROFILE dir, not /opt, so it is delivered as a home file by
    # packages/librewolf.emit_plan() (compiler.py) instead -- shipping it under /opt did
    # nothing. See packages/librewolf.
    lw_common = {
        "librewolf.desktop": librewolf_desktop(),
    }
    calamares = ("calamares", {
        "PKGBUILD": pkgbuild_calamares(),
        CALAMARES_DEFAULTS_PATCH_NAME: calamares_defaults_patch(),
        CALAMARES_REGION_KEYBOARD_PATCH_NAME: calamares_region_keyboard_patch(),
        CALAMARES_FINISH_BUTTONS_PATCH_NAME: calamares_finish_buttons_patch(),
        CALAMARES_NETWORKQ_PATCH_NAME: calamares_networkq_patch(),
        CALAMARES_NETWORKCFG_STATIC_PATCH_NAME: calamares_networkcfg_static_patch(),
    })
    # file_manager: built from the VENDORED source, in EVERY tier -- the symlink-resolve
    # behaviour is not optional and is baked into that source. PKGBUILD is the only text
    # companion; the source TREE is copied into the recipe dir separately by
    # makepkg._emit_recipes (see recipe_source_trees), not carried here as a string.
    # NOTE: the recipe-dir KEY MUST equal the produced PACKAGE name (pkgname=file_manager) that
    # the staleness gate globs for -- makepkg._repo_has_all globs `<key>-*.pkg.tar.zst` in the
    # repo and _current_recipe_fingerprints keys the fingerprint sidecar by this name, so a key
    # that did not match the pkgname would make the gate permanently stale (it would look for
    # thunar-*.pkg.tar.zst while the built file is file_manager-*).
    file_manager = ("file_manager", {
        "PKGBUILD": pkgbuild_file_manager(),
    })
    if full_compile:
        librewolf = ("librewolf", {"PKGBUILD": pkgbuild_librewolf_src(), **lw_common})
        return [calamares, file_manager, librewolf]
    librewolf = ("librewolf", {"PKGBUILD": pkgbuild_librewolf(), **lw_common})
    # Default tier: repackage librewolf, but calamares + file_manager are still built from source.
    return [calamares, file_manager, librewolf]


def recipe_source_trees() -> dict[str, Path]:
    """Map recipe-dir name -> a local source TREE to copy into that recipe dir before makepkg
    runs. This is how a recipe consumes VENDORED, version-controlled source (a directory) that
    cannot be carried as a {filename: content} string in recipe_dirs (those are text-only and
    hashed into the recipe fingerprint). makepkg._emit_recipes copies each tree into the recipe
    dir under its own basename; the PKGBUILD's prepare() then copies it from $startdir into a
    writable build tree (makepkg's source=() array cannot name a directory, so the tree is NOT a
    source=() entry -- see pkgbuild_file_manager).

    Only file_manager uses this today: its source is the git-cloned tree vendored on the
    file_manager package (packages/file_manager.SOURCE_DIR), copied in as ./<SOURCE_SUBDIR>.
    calamares and librewolf fetch their source in-recipe (tarball / git+), so they are absent
    here. The key is the recipe-dir name "file_manager" (== the produced package name), matching
    recipe_dirs above."""
    return {"file_manager": _file_manager.SOURCE_DIR}


# ---------------------------------------------------------------------------
# Updating versions:
#   1. Bump CALAMARES_VERSION / LIBREWOLF_VERSION / LIBREWOLF_PKGVER above.
#      LIBREWOLF_VERSION is the upstream tag (e.g. "153.0.1-1");
#      LIBREWOLF_PKGVER is the pacman-legal form (dots only, e.g. "153.0.1.1").
#   2. Refresh the pinned sha256 from Codeberg's package API:
#      https://codeberg.org/api/packages/librewolf/generic/librewolf/<tag>/
#        librewolf-<tag>-linux-x86_64-package.tar.xz.sha256sum
#   3. If LibreWolf rotates its signing key, update LIBREWOLF_PGP_KEY.
#   4. Rebuild with FORCE_ONLINE=1 so the new sources are fetched.
# ---------------------------------------------------------------------------
