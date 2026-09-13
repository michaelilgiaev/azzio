# azzio - ISO build environment
#
# This image gives compile.sh a clean, genuine Arch Linux userland with the
# real Arch core/extra/multilib repositories. That is the whole point: the ISO
# is built with mkarchiso, which resolves the ISO's package list against the
# HOST's pacman repos. On a non-Arch host (Manjaro, Ubuntu+WSL, macOS, etc.)
# those repos are wrong or absent, so the build must happen inside Arch. This
# container provides that regardless of the machine you run it on.
#
# Build:  docker build -t azzio .
# Run:    docker run --rm -it --privileged \
#           -v "$PWD/cache:/build/cache" \
#           -v "$PWD/output:/build/output" \
#           -v "$PWD/logs:/build/logs" \
#           azzio
#         These three mounts mirror compile.sh's own directory scheme so the
#         host keeps the persistent download cache (cache/, which also holds the
#         disposable profile+scratch tree in cache/build/), the build output
#         incl. the finished ISO written directly to output/, and both build logs
#         (logs/). No stray host-side out/ dir is created.
#         (--privileged is required: mkarchiso mounts proc/sys/dev and uses
#          loop devices + squashfs inside the airootfs.)

FROM archlinux:latest

# Refresh the keyring and sync the toolchain the build needs on the build host:
#   archiso       -> mkarchiso
#   base-devel    -> makepkg and friends
#   go            -> building Go-based ISO components
#   git, sudo     -> checkout tooling; the build shells out through sudo internally
#   python        -> the build itself: compile.sh is a thin PTY/sudo shim that
#                    hands off to `python3 -m compiler` (see libraries/)
# --noconfirm keeps the build non-interactive.
# Extra tools beyond the original set, for the makepkg stage that builds Azzio's
# OWN packages (calamares, librewolf) from our recipes in packages/pkgbuild:
#   fakeroot   -> makepkg's fakeroot packaging (part of base-devel, listed for clarity)
#   gnupg      -> import + verify LibreWolf's release signing key for the .sig check
# The heavy makedepends (cmake, qt6-*, kpmcore, rust, clang, ...) are installed at
# build time by makepkg._install_host_build_deps, not baked in here, so the
# image stays small and the dep set tracks the recipes.
#
# The Azzio application menu is a COMPILED C / GTK3 program: the build COMPILES it on
# the host (compiler.build_daemon -> `make`) during _emit_desktop, so the GTK3 dev stack
# must be present in THIS image or that compile fails with "gtk/gtk.h: No such file or
# directory" and aborts the build. This is unlike the makepkg makedepends: those are
# installed at build time by makepkg, but the menu compile happens BEFORE that step, so
# its deps have to be baked in here. The gedit notepad-mode libpeas plugin
# (libgedit-modifications.so) is COMPILED the same way during _emit_apps, so ITS dev headers
# must be present here too. Keep in sync with application_menu.MENU_BUILD_DEPS +
# gedit.GEDIT_PLUGIN_BUILD_DEPS (compiler._check_host_deps installs the same set on a
# non-Docker Arch host); missing any of these aborts step 2/16 "Sync host toolchain"
# with "Missing build-host dependencies" (offline) or dies later in `make`:
#   gtk3       -> the GTK3 dev headers + gtk+-3.0.pc and the whole -3.0 pkg-config stack
#                 the menu Makefile resolves (gdk/glib/pango/cairo/gdk-pixbuf come as deps).
#   pkgconf    -> pkg-config, which the Makefile shells out to (also in base-devel).
#   gedit      -> the gedit-50 dev headers + gedit.pc / libpeas the notepad plugin's
#                 Makefile resolves (compiler._check_host_deps requires the `gedit` pkg).
#   gcc        -> the compiler both Makefiles invoke (provided by base-devel; listed in
#                 the code's dep sets, present here via base-devel).
#   libx11 / libxrandr / libxft -> the X client dev headers/libs the media OSD (osd.c ->
#                 azzio-osd) links: Xlib, RandR (primary-monitor geometry), Xft (anti-aliased
#                 percent text). The OSD is compiled during the desktop emit (build_osd), BEFORE
#                 makepkg, so these must be baked in here. Keep in sync with
#                 terminal_user_interface_build.TERMINAL_USER_INTERFACE_BUILD_DEPS.
#
# PINNED to an Arch Linux Archive (ALA) snapshot instead of the rolling live mirrors.
# The live `core`/`extra` repos are periodically INTERNALLY INCONSISTENT for short
# windows (a package is pulled while another still depends on it) -- e.g. `elfutils`
# vanished from `core` while `debugedit` (a `base-devel` member) still required it,
# making EVERY build fail with "unable to satisfy dependency 'elfutils'" no matter how
# the sync is flagged (-Sy / -Syu / -Syyu all hit the same broken DB). The ALA serves
# frozen, self-consistent DAILY snapshots, so pinning one makes the image BUILD-STABLE
# and REPRODUCIBLE regardless of the rolling repos' momentary state. Bump the date to
# roll the toolchain forward. `SigLevel = Never` is required because the snapshot's
# package signatures can predate the freshly-populated keyring's validity window.
ARG ARCH_SNAPSHOT=2026/08/10
RUN printf 'Server = https://archive.archlinux.org/repos/%s/$repo/os/$arch\n' "$ARCH_SNAPSHOT" \
        > /etc/pacman.d/mirrorlist \
    && sed -i 's/^SigLevel.*/SigLevel = Never/' /etc/pacman.conf \
    && pacman -Syy --noconfirm --needed --disable-download-timeout \
        archlinux-keyring \
        archiso \
        base-devel \
        go \
        git \
        sudo \
        python \
        fakeroot \
        gnupg \
        gtk3 \
        pkgconf \
        gedit \
        libx11 \
        libxrandr \
        libxft \
    && pacman -Scc --noconfirm

# Initialize pacman's trust database. Installing archlinux-keyring above only
# lays the key FILES on disk; it does NOT create the GnuPG trust store pacman
# checks at install time. Without this, `SigLevel = Required` (set by the ISO's
# pacman.conf) makes pacman reject every signed package: explicitly-requested
# targets then surface as the misleading "error: target not found: <pkg>", and
# pacstrap dies with "There is no secret key available to sign with." mkarchiso
# runs pacstrap internally, so the keyring must be live in this image.
#   --init     generates the local signing key and empty trust db
#   --populate imports and locally-signs the official Arch developer keys
RUN pacman-key --init \
    && pacman-key --populate archlinux

# The build runs as root inside the container (sudo calls in compile.sh become
# no-ops). Provide a passwordless sudo entry anyway so any tooling that shells
# out through sudo keeps working.
RUN echo "root ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/00-root \
    && chmod 0440 /etc/sudoers.d/00-root

# Project lives here. compile.sh writes everything under three dirs at the build
# root: cache/ (persistent downloads, plus the disposable profile+scratch tree in
# cache/build/), output/ (the finished ISO) and logs/. Bind-mount those
# to the host to persist them:
#   -v "$PWD/cache:/build/cache" -v "$PWD/output:/build/output" -v "$PWD/logs:/build/logs"
# compile.sh re-execs itself on a PTY (via util-linux `script`), then hands off
# to the Python build driver which draws a pinned progress bar. The bar and the
# `script` re-exec want a sane terminal type; a bare container has none, so give
# it xterm (its terminfo ships with the base ncurses package).
ENV TERM=xterm

# Host ownership handback. Everything the (root) build writes under the bind
# mounts cache/ output/ logs/ would otherwise be root-owned on the host and thus
# "locked" for the invoking user. compile.sh chowns those trees back to the host
# user on every exit path -- but the host uid/gid CANNOT be detected inside the
# container (COPY and the auto-created bind mounts are all root-owned), so they
# MUST be passed at run time:
#   docker run -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" ...
# Left empty here on purpose: an empty value is treated as "unset" (compile.sh
# then warns and skips), whereas a default of 0 would silently chown to root and
# leave everything locked. Do NOT default these to a number.
ENV HOST_UID="" \
    HOST_GID=""

WORKDIR /build
COPY . /build

# Build the ISO. The finished .iso appears directly in the mounted output/ dir on the host.
#
# IMPORTANT: run this image with `docker run --init` (the README run commands
# already pass it). compile.sh re-execs via `exec script ...`, so without --init
# the container's PID 1 is that `script` process; the kernel drops unhandled
# signals to PID 1 and never reaps orphans, so Ctrl-C leaves the build hanging.
# `--init` inserts tini as PID 1 to forward signals and reap orphans; the Python
# build driver's SIGINT/SIGTERM handler additionally kills the whole process group
# so pacman/mkarchiso die immediately. tini is TTY-transparent, so PTY logging works.
#
# ENTRYPOINT, not CMD: trailing `docker run azzio <args>` (e.g. --estimate,
# --full-compile) must be APPENDED to compile.sh, not REPLACE it. With a bare CMD
# and no ENTRYPOINT, Docker discards the CMD and tries to exec the flag itself, so
# `docker run azzio --estimate` fails with `exec: "--estimate": ... not found`.
# ENTRYPOINT passes the flags straight through to compile.sh's `"$@"`. The no-arg
# `docker run azzio` still runs a default full build (compile.sh treats no args as
# the default tier). To get a debug shell now use `docker run --entrypoint bash azzio`.
ENTRYPOINT ["./compile.sh"]
