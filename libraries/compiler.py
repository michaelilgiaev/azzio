"""The compiler: assemble the archiso profile tree from the configuration-as-Python
modules, cache/stage the packages, run mkarchiso -- AND drive the whole build.

This is what the user means by "the compiler": the ordered steps that compile the
ISO. Each `bar.step(...)` is one milestone, named for the archiso/pacman/systemd
artifact it produces. Trivial overlay-emit steps are near-instant; the two giants
(package cache, mkarchiso) drive live sub-progress.

It is also the ENTRY POINT: `python3 -m compiler`. The thin compile.sh shim sets
up the PTY (via util-linux `script`) and primes sudo, then hands off here. The
high-level driver folded in below (formerly compiler.py) owns:

  * resolve the cache-first offline policy (cache_is_complete)
  * start the sudo keepalive + continuous ownership reclaim
  * run the ordered steps (run) with a live progress bar
  * on ANY exit (success / error / Ctrl-C) restore the terminal, unmount the work
    tree, and hand cache/ output/ logs/ back to the host user -- so nothing is
    ever left root-owned and locked.

The PTY/signal split: the PTY + sudo prime stay in compile.sh; here we handle
SIGINT/SIGTERM by terminating the child process group and running the teardown.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import signal

import downloader
import emit
import estimate
import logstream
import makepkg
import paths
from ownership import Ownership
from progress import ProgressBar
from packages.application_menu import application_menu
from packages.azzio import terminal_user_interface_build
from packages.azzio import default_applications
# timedate (the Flask home page) was folded into the librewolf package (LibreWolf lands on it),
# so its build wiring is imported from there now.
from packages.librewolf import timedate
from packages.passwords import packaging as passwords
from packages.backup import packaging as backup
from packages.hypervisor import packaging as hypervisor
from packages.calamares import calamares
from packages.calamares import locale
# The packages tree is DISCOVERABLE: `packages` is a namespace package (its directory has NO
# __init__.py), each package is a SUB-directory with an __init__.py, and
# package_discovery.with_emit_plan() finds every one exposing an emit_plan() (skipping any
# directory without an __init__.py, and the packages.x86_64 manifest file). The per-application
# tweaks below are collected that way in _emit_apps -- so adding packages/<newapp>/__init__.py
# with an emit_plan() ships it with no edit here, and removing one never leaves a dangling import.
import package_discovery
# The packages the compiler drives BY NAME (they expose more than emit_plan(), feed the desktop
# step, or hold vendored data/scripts), so they stay explicit imports:
#   openbox      -- the whole live desktop: many constants + emit_plan (feeds _emit_desktop)
#   librewolf    -- the browser-policy override (feeds _emit_desktop, not the app loop)
#   gedit        -- notepad-mode: emit_plan (app loop) PLUS the compiled libpeas plugin build
#   fastfetch    -- the branded logo/config (no emit_plan; config_jsonc()/logo_txt())
from packages import openbox
from packages import fastfetch
from packages import librewolf
from packages import gedit
# The home-directory LAYOUT data (dirs/links/trash; no emit_plan) lives on the file_manager
# package (the file manager's sidebar is built from the same list), so _emit_homedir reads it there.
from packages.file_manager import home_directory
# The per-application tweaks that expose ONLY emit_plan() (kitty, vlc, libreoffice, gimp,
# file_manager, xviewer) are NOT imported by name -- _emit_apps discovers them. (file_manager folds
# in the ~/Templates "Create Document" set from its templates submodule, and builds the file manager
# binary itself from vendored source, so there is no standalone templates or file_manager package.)
# The packages the
# compiler already drives explicitly (the desktop pair openbox/librewolf, plus application_menu,
# passwords, calamares, and the azzio guest command line interface) are excluded from that
# discovery so they are not emitted twice. See _EXPLICIT_PACKAGES below.
_DESKTOP_MODIFICATIONS = ("openbox", "librewolf")
# Every package the compiler emits BY NAME (so package_discovery.with_emit_plan() must skip them
# in the auto-discovered app loop). The desktop pair is emitted in _emit_desktop; application_menu
# and passwords have their own emit_plan() driven directly; calamares and azzio are not app-loop
# packages at all. Keeping this list here means a newly-dropped packages/<app>/ is auto-emitted
# unless it is added here on purpose.
_EXPLICIT_PACKAGES = ("openbox", "librewolf", "application_menu", "window_switcher", "passwords", "backup", "hypervisor", "calamares", "azzio")
import installer
import pacman
import profile
import system

# Weights: setup/emit steps carry real weight so the bar visibly advances through
# them (at weight 1 they were ~2% of the whole bar and looked frozen); the giants
# are still the bulk, sized from real log spans. Keep in sync with steps below:
# len(STEP_WEIGHTS) - 1 MUST equal the number of bar.step() calls in run(). The
# final FOUR weights belong, in order, to: the package-cache giant, the makepkg
# stage (our own calamares/librewolf; heavy in the default tier, VERY heavy with
# --full-compile), and the TWO mkarchiso giants -- one per POSSIBLE ISO variant.
# Every run builds exactly ONE variant (base OR ssh -- see _variants_for), so only one
# of those two mkarchiso passes runs; the bar is sized for the MAXIMUM of two and
# finalize() snaps it to full after the single pass, so over-sizing is safe.
STEP_WEIGHTS = [0] + [8] * 12 + [250, 120, 270, 270]

# The ISO variants a build CAN produce -- the canonical MAX set that sizes STEP_WEIGHTS's
# two mkarchiso weights. Every step up to mkarchiso is variant-independent (same packages,
# same airootfs). WHICH ONE actually builds is decided at runtime by _variants_for(), and
# it is always exactly one: the base `azzio-headed` medium WITHOUT --ssh, or the
# `azzio-headed-ssh` medium (INDIVIDUALLY, not alongside base) WHEN --ssh="<PASSWORD>"
# opts in (no default password is ever shipped). The variant KEYS stay base/sshd;
# profile.ISO_NAMES maps them to the product-line artifact names.
VARIANTS = ("base", "sshd")

# PGID of the currently-running mkarchiso child (0 = none). mkarchiso is spawned in
# its own session/process group so the signal handler can kill THAT group (and all
# its pacstrap descendants) without touching our own shell -- see on_signal below.
_ACTIVE_CHILD_PGID = 0


def _sudo() -> list[str]:
    # `-n` (non-interactive) so a chown/unmount during Ctrl-C teardown after the
    # sudo timestamp expired fails fast instead of blocking on a password prompt.
    return [] if paths.is_root() else ["sudo", "-n"]


# --- Method A: the --ssh=<PASSWORD> opt-in for the sshd ISO ------------------
# The sshd ISO is OPT-IN: it is built ONLY when `--ssh="<PASSWORD>"` is supplied with
# a non-empty string, and that password becomes the `main` login credential (hashed
# into that variant's /etc/shadow). No flag / empty string -> no sshd ISO. This is the
# security posture from data/PROMPT.md DECISION 2: no default password is ever shipped;
# the SSH variant's credential must come from the operator at build time.

def parse_ssh_flag(argv: list[str]) -> str | None:
    """Pull the `--ssh=<PASSWORD>` value out of argv, or None if absent/empty.

    Mirrors the codebase's existing value-flag precedent (command_line_interface.py
    parses --server=/--ssh= via split("=", 1)[1]) so a password containing '=' is not
    truncated. An empty value (`--ssh=`) returns None: the flag "demands a string or it
    doesn't work" -- a blank string opts OUT of the sshd ISO rather than shipping a
    blank password.
    """
    for token in argv:
        if token.startswith("--ssh="):
            value = token.split("=", 1)[1]
            return value or None
    return None


def ssh_flag_present(argv: list[str]) -> bool:
    """True if the operator wrote the `--ssh` flag AT ALL -- bare (`--ssh`), empty
    (`--ssh=`, `--ssh=""`), or with a value (`--ssh=pw`).

    This is the OTHER half of three-state detection: parse_ssh_flag() reports the VALUE
    (None when blank/absent), and this reports PRESENCE. Together they let main() tell
    "operator asked for the ssh ISO but forgot the password" (present, no value -> HARD
    STOP with an explanation) apart from "operator never mentioned ssh" (absent -> build
    the base ISO only, which is correct). A token like `--sshfoo` is NOT the flag."""
    return any(t == "--ssh" or t.startswith("--ssh=") for t in argv)


def check_ssh_flag(argv: list[str]) -> str | None:
    """Validate the --ssh flag combination, returning an ERROR MESSAGE to abort on, or
    None to proceed.

    The rule the user asked for: the flag "demands a string or it doesn't work". So a
    PRESENT-but-blank flag (`--ssh`, `--ssh=`) is a hard error -- it must stop the build
    and EXPLAIN why, rather than silently building the base ISO only (the reported bug:
    `--ssh` with no password produced no ssh ISO AND no explanation). An ABSENT flag is
    fine (base-only). A flag WITH a value is fine (the ssh ISO builds).

    Pure (argv in, message out) so main() can print+exit on it and tests can assert it."""
    if ssh_flag_present(argv) and parse_ssh_flag(argv) is None:
        return (
            'The --ssh flag needs a password: --ssh="<PASSWORD>". You passed --ssh with no '
            "value, and no default password is ever shipped, so nothing was built. Re-run "
            'with a real password, e.g. compile.sh --ssh="mysecret", or drop --ssh to build '
            "the base headed ISO (ssh disabled)."
        )
    return None


def ssh_password_hash(password: str) -> str:
    """Hash a build-time --ssh password into a sha-512 crypt hash ($6$...) for shadow.

    Never store or ship the plaintext: the image carries only this hash. Uses
    `openssl passwd -6`, present on every host that runs mkarchiso (openssl is a hard
    dependency of the archiso/pacman toolchain). Python's own crypt module is gone as
    of 3.13, so openssl is the single, portable source of a sha-512 crypt hash. A blank
    password is rejected -- the flag must resolve to a real credential before shadow.
    """
    if not password:
        raise ValueError("ssh_password_hash: refusing to hash an empty password")
    try:
        out = subprocess.run(
            ["openssl", "passwd", "-6", "-stdin"],
            input=password, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as e:
        raise RuntimeError(
            "ssh_password_hash: `openssl passwd -6` failed -- openssl is required to "
            f"hash the --ssh password ({e})."
        ) from e
    if not out.startswith("$6$"):
        raise RuntimeError(
            f"ssh_password_hash: openssl produced an unexpected hash (not $6$): {out!r}"
        )
    return out


def _variants_for(ssh_hash: str | None) -> tuple[str, ...]:
    """The ISO variants a build ACTUALLY produces this run -- exactly ONE.

    --ssh outputs the SSH-type medium INDIVIDUALLY: when an --ssh password (already
    hashed) is supplied, ONLY the `azzio-headed-ssh` ISO is built -- NOT the base ISO
    alongside it. Without --ssh, ONLY the base `azzio-headed` ISO is built. So a plain
    run and an --ssh run each produce a single, distinct medium; the base ISO is simply
    what you get when you do not opt into ssh.

    (VARIANTS stays the canonical MAX set of BOTH variants -- it sizes the progress bar's
    two mkarchiso weights -- but only one of them is ever selected per run.)"""
    if ssh_hash:
        return ("sshd",)
    return ("base",)


def kill_active_child(sudo: list[str]) -> None:
    """Kill the running mkarchiso child's process group (TERM then KILL). Root
    children (pacstrap under mkarchiso on a native run) are reaped via sudo."""
    pgid = _ACTIVE_CHILD_PGID
    if pgid <= 0:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        if sudo:
            subprocess.run(sudo + ["kill", f"-{sig}", f"-{pgid}"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def run(bar: ProgressBar, offline: bool, reclaim_after_mkarchiso,
        full_compile: bool = False, ssh_password_hash: str | None = None) -> list[Path]:
    """Execute all steps; return the paths of the built ISOs. Raises on failure.

    full_compile: when True, Azzio's own packages (librewolf) are compiled from
    source instead of repackaged from the verified upstream tarball. Passed to the
    makepkg stage below.

    ssh_password_hash: the operator's --ssh password ALREADY HASHED (sha-512 crypt),
    or None. It selects WHICH single ISO is built (DECISION 2: no default password is
    ever shipped -- the sshd variant's credential comes from the operator at build time).
    None -> ONLY the base/headed ISO. A hash -> ONLY the `azzio-headed-ssh` medium
    (built INDIVIDUALLY, NOT alongside the base ISO), whose /etc/shadow carries that hash
    for `main` and which auto-runs `azzio --sshd-hypervisor` at boot.

    Every step up to mkarchiso is variant-independent -- same packages, same shared
    airootfs -- so the shared, heavy work (package cache, own-package build) happens
    exactly once. The selected variant's differences (profiledef iso_name, the
    sshd-hypervisor auto-setup service, and its /etc/shadow) plus its single mkarchiso
    pass run in the finalize loop at the end.
    """
    W = paths.WORKDIR
    airootfs = W / "airootfs"
    ea = airootfs / "root/azzio"  # the azzio payload dir baked into the ISO
    sudo = _sudo()

    # 1 -- Reset build workspace
    bar.step("Reset build workspace")
    _unmount_worktree(sudo)
    paths.BUILDDIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(sudo + ["rm", "-rf", str(W)], check=False)
    W.mkdir(parents=True, exist_ok=True)
    # NB: we deliberately do NOT os.chdir(W) here. Every path below (emits,
    # subprocess calls, and the mkarchiso invocation with its absolute -w/-o and
    # profile args) is absolute, so the build never needs the process cwd to be the
    # workdir -- and chdir'ing into it left the interpreter (and its caller) parked
    # inside a disposable scratch tree that gets rm -rf'd on the next run/clear.sh.

    # 2 -- Sync host toolchain
    bar.step("Sync host toolchain")
    _check_host_deps(sudo, offline)

    # 3 -- Scaffold releng profile
    bar.step("Scaffold releng profile")
    _copy_releng(W)

    # 4 -- Brand boot menus (systemd-boot + syslinux)
    bar.step("Brand boot menus (systemd-boot, syslinux)")
    _brand_boot_menus(W)

    # 5 -- Stage pacstrap package manifest
    bar.step("Stage pacstrap package manifest")
    emit.copy_data("packages.x86_64", W / "packages.x86_64")

    # 6 -- Provision airootfs accounts (users/groups + /home/main, chowned for the
    # autologin `main` user the getty drops straight into on the live console).
    bar.step("Provision airootfs accounts (console autologin)")
    emit.write_text(airootfs / "etc/passwd", system.PASSWD)
    # Shadow ships LOCKED by default (both accounts, DECISION 1): no password login is
    # possible on the base/headed ISO, autologin still works. The opt-in sshd variant
    # rewrites `main`'s field to the operator's hash per-pass in _apply_variant, so this
    # shared write is the safe locked baseline both variants start from.
    emit.write_text(airootfs / "etc/shadow", system.shadow_for(None), mode=0o600)
    emit.write_text(airootfs / "etc/gshadow", system.GSHADOW, mode=0o600)
    emit.write_text(airootfs / "etc/group", system.GROUP)
    home = airootfs / "home/main"
    emit.mkdir(home)
    subprocess.run(sudo + ["chown", "-R", "1000:998", str(home)], check=False)
    # Bake the default-deny root-login sshd drop-in so root cannot log in over ssh -- on
    # the live ISO AND on any Calamares-installed target (the offline unpackfs copies it).
    # This is the root-cause fix for a Calamares install being reachable as root; the
    # runtime bring-up never runs there. See _provision_sshd_hardening.
    _provision_sshd_hardening(airootfs)

    # 7 -- Overlay branding and locale into airootfs.
    # One coherent overlay-population act: locale setup-script + service, the fastfetch
    # logo, and the os-release/hostname rebrand.
    bar.step("Overlay branding and locale")

    # locale: first-run setup script + the systemd unit that runs it.
    emit.write_exec(ea / "setup-locale.sh", locale.setup_locale_sh())
    emit.write_text(airootfs / "etc/systemd/system/locale-setup.service", system.LOCALE_SETUP_SERVICE)

    # the azzio fastfetch logo/config for the live (and installed) user.
    _emit_fastfetch(ea, home)

    # os-release rebrand:
    # Live ISO: the build pacman.conf NoExtracts usr/lib/os-release (libraries/pacman.py)
    # so the `filesystem` package's stock "Arch Linux" file never lands. We must NOT
    # pre-place our replacement in the airootfs overlay, though: mkarchiso copies the
    # overlay into the work root BEFORE pacstrap, and pacman's file-conflict check
    # (which runs before extraction and is NOT suppressed by NoExtract) then aborts
    # with "filesystem: usr/lib/os-release exists in filesystem". Instead we plant it
    # AFTER pacstrap via customize_airootfs.sh -- the same after-pacstrap ordering the
    # on-disk installer already uses (libraries/installer.py copies it into /mnt post-
    # pacstrap). The branded file is staged read-only under root/azzio/os-release and
    # the hook copies it into place inside the pacstrapped rootfs.
    emit.write_text(ea / "os-release", system.OS_RELEASE)
    # The per-app system files kitty/gedit override (kitty icon SVG, the two stale
    # cat PNGs, the gedit .desktop) are owned by their own packages, so they hit the
    # SAME conflict wall -- planting them in the overlay aborts pacstrap. They get the
    # identical after-pacstrap cure: NoExtract'd (libraries/pacman.py) and copied in by the
    # customize hook. The replacement bodies are staged under root/azzio/apps/ in
    # _emit_apps (step 8); here we append their plant/remove lines to the hook.
    emit.write_exec(airootfs / "root/customize_airootfs.sh",
                    system.CUSTOMIZE_AIROOTFS + pacman.app_override_cp_sh())
    # Overlay the releng `archiso` hostname with `azzio` (prompt + fastfetch title).
    emit.write_text(airootfs / "etc/hostname", system.HOSTNAME)

    # 8 -- Overlay the OpenBox live desktop + Calamares installer configuration.
    # The graphical live session (Manjaro-style): user configs go to BOTH the live
    # `main` home AND /etc/skel (so a Calamares-created user on the installed system
    # inherits the same desktop). The tty1 autologin override switches the releng
    # default (autologin root) to autologin `main`, whose .bash_profile execs startx
    # into an OpenBox X11 session. The Calamares configuration tree lands under /etc/calamares.
    bar.step("Overlay OpenBox desktop and Calamares configuration")
    _emit_desktop(airootfs, home)
    _emit_homedir(airootfs, home)
    _emit_apps(airootfs, home, ea)
    _emit_calamares(airootfs)
    _emit_tty1_autologin(airootfs)
    # ckbcomp: Calamares' keyboard page renders its on-screen key legends by shelling
    # out to `ckbcomp`; without it the preview draws BLANK keys ("ckbcomp not found,
    # keyboard preview disabled"). `ckbcomp` is a self-contained Python 3 port of the
    # upstream (Debian/Manjaro) Perl ckbcomp -- byte-identical output, no Perl in the
    # tree -- which Arch does NOT package, so we vendor it as a file in the calamares package
    # (libraries/packages/calamares/ckbcomp.py holds the script) and copy it verbatim into
    # /usr/bin. It needs only python (in base) and the XKB data in /usr/share/X11/xkb (shipped
    # by xkeyboard-config), both present. It lands in the live ISO (as /usr/bin/ckbcomp, no .py
    # suffix) and is copied to the target by unpackfs.
    emit.copy_data("calamares/ckbcomp.py", airootfs / "usr/bin/ckbcomp", mode=0o755)

    # 9 -- Stage installed-system pacman and pkgs service.
    # The package-management unit of the installed system: its /etc/pacman.conf, the
    # live-session setup-pkgs.sh, and the pkgs-setup.service that runs it.
    bar.step("Stage installed-system pacman and pkgs service")
    emit.write_text(airootfs / "etc/pacman.conf", pacman.installer_base_conf())
    emit.write_exec(ea / "setup-pkgs.sh", installer.setup_pkgs_sh())
    emit.write_text(airootfs / "etc/systemd/system/pkgs-setup.service", system.PKGS_SETUP_SERVICE)

    # 9 -- Enable systemd units and sudoers policy.
    # Activation/policy at profile finalization: the always-on *.target.wants symlinks
    # that enable the daemons, plus the sudoers.d drop-ins. The sshd-hypervisor
    # auto-setup service (emitted + enabled ONLY for the azzio-sshd ISO) is handled
    # per-variant in the finalize loop below, not here -- this step is variant-shared.
    bar.step("Enable systemd units and sudoers policy")
    _link_services(airootfs)
    emit.write_text(airootfs / "etc/sudoers.d/00-rootpw", system.SUDOERS_ROOTPW, mode=0o440)
    emit.write_text(airootfs / "etc/sudoers.d/00-main", system.SUDOERS_MAIN, mode=0o440)
    emit.write_text(airootfs / "etc/sudoers.d/00-secure-path", system.SUDOERS_SECURE_PATH, mode=0o440)
    # Power management (lid/button + PC-vs-laptop idle sleep), folded into this
    # unit/policy step so the STEP_WEIGHTS milestone-count invariant is untouched.
    # All root-owned under /etc + /usr/local/bin, so the OFFLINE Calamares install
    # (unpackfs rsyncs the live rootfs) carries them onto the installed system with
    # no separate installer step. The lid/power-button drop-in is STATIC; the
    # idle-sleep policy is a script+service+udev-rule that decides PC vs laptop and
    # AC state at runtime (see libraries/system.py). The service enable-symlink is
    # added in _link_services alongside the other multi-user oneshots.
    _emit_power(airootfs)

    # The virtiofs shared-folder .mount unit + its mountpoint, enabled on both
    # variants (enable-link in _link_services). Makes --shared appear on the headed
    # variant, not just the ssh one.
    _emit_shared_mount(airootfs)

    # 10 -- Emit installer payload.
    # The first-boot script/service/conf. profiledef.sh (archiso metadata at the
    # PROFILE ROOT) is NOT emitted here: its iso_name is the one thing that differs
    # per variant, so it is written per-variant in the finalize loop below. Calamares
    # (auto-launched from the OpenBox session, step 8) is the GUI installer. The scripted
    # terminal installer is ALSO emitted (as azzio-install-cli.sh under /root/azzio) so
    # `azzio-install --cli` can install over SSH with no X -- same partition/pacstrap/
    # chroot-setup pipeline as the first-boot installer, just driven from a terminal.
    bar.step("Emit installer payload")
    emit.write_exec(ea / "first-boot-setup.sh", installer.first_boot_sh())
    emit.write_text(ea / "first-boot-setup.service", installer.first_boot_service())
    emit.write_text(ea / "first-boot-setup.conf", installer.first_boot_conf())
    # The scripted (terminal/SSH) installer -- the CLI half of azzio-install. Baked under
    # /root/azzio alongside the payload it reads (packages.x86_64, chroot-setup.sh, the
    # offline repo). openbox.INSTALL_CLI_SCRIPT_PATH points azzio-install --cli at it.
    emit.write_exec(ea / "azzio-install-cli.sh", installer.installer_sh())

    # 11 -- Resolve build pacman.conf and mirrors.
    # Writes the pacstrap/mkarchiso build pacman.conf, injects the persistent CacheDir,
    # probes mirrors, and switches to the local file:// repo when offline. A distinct
    # pacman-prep stage that gates the cache download below.
    bar.step("Resolve build pacman.conf and mirrors")
    _write_build_pacman_conf(W, offline, bar)

    # 12 -- Warm pacman cache and stage installer payload (GIANT, weight 250).
    # pacman -Sw builds/indexes the local repo (drives bar.sub sub-progress), then the
    # on-disk installer's package manifest + pacman confs + chroot-setup.sh are staged.
    bar.step("Warm pacman cache and stage installer payload")
    downloader.build_cache(W, paths.CACHEDIR, offline, bar.sub, bar.phase, full_compile)
    bar.sub_done()
    bar._arm(); bar.draw()
    # stage the installer-side payload the on-disk installer needs
    emit.copy_data("packages.x86_64", ea / "packages.x86_64")
    emit.write_text(ea / "pacman-base-conf/pacman.conf", pacman.installer_base_conf())
    emit.write_text(ea / "pacstrap-azzio-conf/pacman.conf", pacman.installer_pacstrap_conf())
    emit.write_exec(ea / "chroot-setup.sh", installer.chroot_setup_sh())

    # 13 -- Build Azzio's OWN packages and fold them into the offline repo
    # (GIANT-ish, weight 120; MUCH heavier under --full-compile). BOTH calamares
    # and librewolf are built here in EVERY tier -- neither is in an Arch repo
    # (librewolf never was; calamares was dropped from extra/, now AUR-only, so
    # step 12 can no longer fetch it). Default tier: calamares from source
    # (sha256-verified) + librewolf by repackaging the verified upstream binary
    # tarball. --full-compile: calamares from source + librewolf from Firefox
    # source. Whatever is built is dropped into cache/pkgs/repo/, then we
    # RE-reconcile the index and RE-stage the repo into airootfs so mkarchiso's
    # pacstrap (and the on-disk installer) can install them.
    bar.step("Build packages (calamares, librewolf)")
    makepkg.build_own_packages(offline, full_compile, bar.sub, bar.phase)
    bar.sub_done()
    bar._arm(); bar.draw()
    _refold_own_packages_into_repo(W, full_compile)

    # The build pacman.conf was resolved at step 11, BEFORE the cache + our own packages
    # existed. Now that the local repo is complete (every pinned Arch package including
    # the lib32-* multilib set, plus our calamares/librewolf/file_manager, all indexed),
    # RE-resolve it to install purely from that file:// repo -- otherwise a cold build
    # (empty cache + foreign-host mirrors the step-11 probe cannot reach) would pacstrap
    # against a conf with no local repo and abort on "target not found: calamares" and
    # the lib32-* targets. Idempotent for the already-offline complete-cache path.
    _finalize_build_pacman_conf(W)

    # 14/15 -- Assemble the selected ISO variant (one GIANT mkarchiso pass, weight 270).
    # Exactly ONE variant is selected per run: the base ISO WITHOUT --ssh, or the ssh ISO
    # (INDIVIDUALLY) WHEN --ssh opted in (_variants_for). Every step above is variant-
    # independent, so we overlay the selected variant's tiny differences (its profiledef
    # iso_name, its /etc/shadow, and whether the sshd-hypervisor auto-setup service is
    # emitted/enabled) onto the shared airootfs and run its single mkarchiso pass.
    # mkarchiso re-copies the profile's airootfs overlay into its work tree at the start of
    # the pass, so the shadow / sshd enable-symlink for the selected variant is correctly
    # reflected in the ISO. (The loop is kept over _variants_for's result -- today one
    # element -- so nothing breaks if a future run ever selects more than one.) The ISO
    # lands in output/ with its distinct iso_name.
    isos: list[Path] = []
    for variant in _variants_for(ssh_password_hash):
        _apply_variant(W, airootfs, variant, ssh_password_hash=ssh_password_hash)
        bar.step(f"Assemble {profile.iso_name_for(variant)} ISO (mkarchiso)")
        isos.append(_run_mkarchiso(sudo, W, bar, reclaim_after_mkarchiso,
                                   iso_name=profile.iso_name_for(variant)))
    return isos


# --- helpers ---------------------------------------------------------------

def _provision_sshd_hardening(airootfs: Path) -> None:
    """Bake the DEFAULT-DENY root-login policy into the airootfs (both variants).

    data/PROMPT.md: root ssh login is OFF by default. The base ISO locks every shadow
    field, but a Calamares INSTALL sets a real root password (config_system.py), and
    sshd's compiled default is `prohibit-password` -- so on an installed box with sshd up
    root would otherwise be reachable. Writing `PermitRootLogin no` as a sshd_config.d
    drop-in HERE (in the airootfs, not just the runtime `--sshd-hypervisor` bring-up) means
    it ships on the live ISO AND is copied to every installed target by the offline
    unpackfs -- closing the gap on a Calamares install, which never runs the bring-up. The
    `00-` prefix sorts FIRST (before 10-azzio-hardening, the systemd 20-*, and Arch's stock
    99-archlinux.conf) and sshd is FIRST-match-wins per keyword, so this directive is
    AUTHORITATIVE and no later drop-in can override it. The azzio TUI/CLI toggle rewrites
    this file. New file (openssh does not own `00-azzio-root-login.conf`), so no pacstrap
    file conflict -- it can live in the overlay directly."""
    emit.write_text(airootfs / system.SSHD_ROOT_LOGIN_DROPIN_PATH,
                    system.SSHD_ROOT_LOGIN_OFF)


def _apply_variant(W: Path, airootfs: Path, variant: str,
                   ssh_password_hash: str | None = None) -> None:
    """Overlay the per-variant differences onto the shared profile tree just before
    its mkarchiso pass. Three things differ between the base and sshd ISOs:

      1. profiledef iso_name -- drives the artifact filename (azzio-headed-<ver>.iso vs
         azzio-headed-ssh-<ver>.iso). Rewritten at the profile root every pass.
      2. the sshd-hypervisor auto-setup service -- emitted AND enabled (a
         multi-user.target.wants symlink) ONLY for the sshd variant, so that ISO
         auto-runs `azzio --sshd-hypervisor` at boot. The base ISO must have
         NEITHER, so we affirmatively remove both when building it -- otherwise a
         leftover from the preceding sshd... (order is base-first today, but this
         stays correct if the order ever flips) would bleed into the base ISO.
      3. /etc/shadow -- the base ISO ships LOCKED accounts (no password login); the
         sshd ISO replaces `main`'s field with the operator's build-time hash so
         they can log in remotely with the --ssh password. Rewritten every pass so a
         hashed shadow from a preceding sshd pass never leaks into the base ISO.

    ssh_password_hash is REQUIRED (a sha-512 crypt hash) when variant == "sshd": the
    sshd ISO must never be built with the base locked shadow -- that would ship an sshd
    nobody can authenticate to, silently hiding that the credential was dropped.

    Everything else in the profile is identical across variants and already staged."""
    emit.write_exec(W / "profiledef.sh", profile.profiledef_sh(variant))
    svc = airootfs / "etc/systemd/system/sshd-hypervisor-setup.service"
    link = (airootfs / "etc/systemd/system/multi-user.target.wants"
            / "sshd-hypervisor-setup.service")
    if variant == "sshd":
        if not ssh_password_hash:
            raise ValueError(
                "_apply_variant: the sshd variant requires an --ssh password hash; "
                "refusing to build an sshd ISO with the base (locked) shadow."
            )
        # main gets the operator's real hash; root stays locked.
        emit.write_text(airootfs / "etc/shadow",
                        system.shadow_for(ssh_password_hash), mode=0o600)
        emit.write_text(svc, system.SSHD_HYPERVISOR_SETUP_SERVICE)
        emit.link("/etc/systemd/system/sshd-hypervisor-setup.service", link)
    else:
        # Base ISO: LOCK the shadow (relock even if a prior sshd pass left a hashed
        # one in the shared airootfs) and strip the sshd auto-setup unit + enable link.
        emit.write_text(airootfs / "etc/shadow", system.shadow_for(None), mode=0o600)
        link.unlink(missing_ok=True)
        svc.unlink(missing_ok=True)


def _emit_desktop(airootfs: Path, home: Path) -> None:
    """Emit the OpenBox live-session files. Each PLAN entry has an absolute dest
    (either under /home/main for the live user -- e.g. ~/.config/openbox/* -- or an
    absolute system path). User files are ALSO copied into /etc/skel so a
    Calamares-created user on the installed system inherits the same desktop
    (Manjaro-style). The /home/main tree is chowned 1000:998 by step 6 / the post-emit
    chown below."""
    skel = airootfs / "etc/skel"
    # OpenBox live-session files + the LibreWolf browser-policy override. Both use the
    # same builder/dest/mode/owner plan shape and the same home-file + /etc/skel mirror
    # rule, so they iterate through one loop. The LibreWolf entry drops
    # librewolf.overrides.cfg at the PROFILE path LibreWolf's AutoConfig loader actually
    # reads (~/.config/librewolf/librewolf/...); shipping it under /opt did nothing (the
    # loader never looks there). See packages/librewolf.emit_plan().
    for entry in openbox.emit_plan() + librewolf.emit_plan():
        content = entry["builder"]()
        dest_abs = entry["dest"]          # e.g. "/home/main/.xinitrc" or "/usr/local/bin/..."
        mode = entry["mode"]
        # airootfs-relative destination (strip leading '/').
        emit.write_text(airootfs / dest_abs.lstrip("/"), content, mode=mode)
        # Mirror HOME-relative user files into /etc/skel for installed-system users.
        if entry["owner"] == "home" and dest_abs.startswith(openbox.HOME + "/"):
            rel = dest_abs[len(openbox.HOME) + 1:]   # path under the home dir
            emit.write_text(skel / rel, content, mode=mode)
    # Installer launcher icon ("Az'" app tile), standardized as the scalable vector
    # assets/icons/azzio.svg. Ship the SVG to the hicolor SCALABLE apps dir (the vector
    # master, like kitty.svg) AND rasterize it to PNGs at /usr/share/pixmaps and the
    # hicolor 256x256 apps dir, so the Desktop/menu/autostart .desktop files
    # (Icon=azzio-installer) resolve it regardless of which path/size the icon loader
    # consults, with no theme-cache rebuild needed. Root-owned system paths.
    emit.copy_asset(openbox.INSTALLER_ICON_ASSET,
                    airootfs / openbox.INSTALLER_ICON_SCALABLE.lstrip("/"), mode=0o644)
    for icon_dest in (openbox.INSTALLER_ICON_PIXMAP, openbox.INSTALLER_ICON_HICOLOR):
        emit.render_svg_png(openbox.INSTALLER_ICON_ASSET,
                            airootfs / icon_dest.lstrip("/"),
                            openbox.INSTALLER_ICON_PNG_SIZE, mode=0o644)
    # The two Azzio wallpaper images ("years", "decades") under /usr/share/wallpapers.
    # Each ships as contents/images/<res>.png (+ a screenshot.png thumbnail and an inert
    # metadata.json kept for self-description). feh paints the "years" image as the X
    # root pixmap from the OpenBox autostart / ~/.xinitrc. Root-owned under
    # /usr/share/wallpapers.
    for pkg in openbox.WALLPAPER_PACKAGES:
        pkg_root = airootfs / openbox.WALLPAPERS_SYSTEM_DIR.lstrip("/") / pkg["id"]
        emit.write_text(pkg_root / "metadata.json",
                        openbox.wallpaper_metadata_json(pkg["id"]), mode=0o644)
        img = pkg_root / "contents" / "images" / f"{openbox.WALLPAPER_IMAGE_RES}.png"
        emit.copy_asset(pkg["asset"], img, mode=0o644)
        # screenshot.png = a thumbnail (reuse the full image).
        emit.copy_asset(pkg["asset"], pkg_root / "contents" / "screenshot.png", mode=0o644)
    # Azzio application menu (OUR menu -- the whole shell: a centered GTK3 launcher
    # opened by the Super key). The menu is a COMPILED C program:
    # build_daemon() runs `make` against a private copy of the C sources and installs the
    # resulting binary; emit_plan() then drops the two generated TEXT artifacts (the
    # pure-Python launcher installed as the bin entry point, and the .desktop). The
    # OpenBox session (openbox.py) starts the daemon binary from its autostart and binds
    # the Super key to the launcher.
    # Root-owned system paths -> the OFFLINE Calamares install rsyncs them onto the
    # installed system with no separate step.
    application_menu.build_daemon(
        airootfs / application_menu.MENU_DAEMON_BIN_SYSTEM_PATH.lstrip("/")
    )
    for entry in application_menu.emit_plan():
        target = airootfs / entry["dest"].lstrip("/")
        # The menu's own glyph icons (power row + search) ride in emit_plan() as asset/render
        # entries (icons_plan()), the same shape _emit_apps handles for kitty/xviewer: copy the
        # scalable SVG master verbatim, rasterize the PNG sizes. All are root-owned system icons
        # (a NEW hicolor name, so nothing is package-owned) -- no /etc/skel mirror needed.
        if entry.get("asset"):
            emit.copy_asset(entry["asset"], target, mode=entry["mode"])
            continue
        if entry.get("render"):
            r = entry["render"]
            emit.render_svg_png(r["asset"], target, r["size"], mode=entry["mode"])
            continue
        emit.write_text(target, entry["builder"](), mode=entry["mode"])
    # The Azzio window switcher (alt-tab): a SECOND compiled C/GTK3 daemon, OUR
    # replacement for OpenBox's built-in NextWindow list (a horizontal, Windows-like
    # overlay of LIVE window thumbnails). build_daemon() stages this package AND
    # application_menu (four reused translation units) into a scratch tree and installs the
    # binary; emit_plan() ships the pure-Python launcher (the bin entry point A-Tab runs).
    # OpenBox autostart starts the daemon + picom, and rc.xml binds A-Tab/A-S-Tab to the
    # launcher (see packages/openbox).
    from packages.window_switcher import window_switcher as window_switcher_pkg
    window_switcher_pkg.build_daemon(
        airootfs / window_switcher_pkg.SWITCHER_DAEMON_BIN_SYSTEM_PATH.lstrip("/")
    )
    for entry in window_switcher_pkg.emit_plan():
        emit.write_text(
            airootfs / entry["dest"].lstrip("/"),
            entry["builder"](),
            mode=entry["mode"],
        )
    # The bare-`azzio` TERMINAL UI (OUR C settings UI: Theme / Wallpaper / Network, opened
    # by running `azzio` with no arguments). It is part of the `azzio` package now (one
    # program, C for speed); like the menu it is a COMPILED C program: build_terminal_user_interface() runs
    # `make` against a private copy of the package's C sources and installs the resulting
    # binary under /usr/local/lib/azzio. The `azzio` command line interface (installed by openbox.PLAN
    # below) execs this binary for the no-argument case. Then install_previews() ships the
    # theme-preview screenshots (verbatim) into the sibling previews dir the UI reads at
    # runtime with kitty. Root-owned; the OFFLINE Calamares install rsyncs both onto the
    # installed system with no separate step.
    terminal_user_interface_build.build_terminal_user_interface(
        airootfs / terminal_user_interface_build.TERMINAL_USER_INTERFACE_BIN_SYSTEM_PATH.lstrip("/")
    )
    terminal_user_interface_build.install_previews(
        airootfs / terminal_user_interface_build.TERMINAL_USER_INTERFACE_PREVIEW_SYSTEM_DIR.lstrip("/")
    )
    # The media OSD indicator (bottom-middle cyan volume/brightness bar). Like the terminal UI it
    # is a COMPILED C program (on_screen_display.c -> azzio-osd), built from the SAME Makefile and installed
    # next to the UI binary. `azzio volume/brightness` launches it; it draws a single, no-flicker
    # Xlib window (so it links X11/Xrandr/Xft, on the build host per the UI build deps). Root-
    # owned; the OFFLINE Calamares install rsyncs it onto the installed system with no extra step.
    terminal_user_interface_build.build_osd(
        airootfs / terminal_user_interface_build.OSD_BIN_SYSTEM_PATH.lstrip("/")
    )
    # Azzio timedate (OUR Flask Time + Calendar home page -- the site LibreWolf lands
    # on at localhost:49154). A pure-Python app: emit_plan() copies the app sources
    # (applications.py/page.py), the launcher, and the azzio-timedate.service unit to their fixed
    # root-owned system paths. The service ENABLE-symlink is added in _link_services (like
    # the other azzio units); the OFFLINE Calamares install rsyncs all of it onto the
    # installed system so the home page also runs at boot there. Its runtime dep
    # (python-flask) is in the manifest. See packages/librewolf/timedate.py.
    for entry in timedate.emit_plan():
        emit.write_text(
            airootfs / entry["dest"].lstrip("/"),
            entry["builder"](),
            mode=entry["mode"],
        )
    # Azzio passwords (OUR encrypted GPG/AES256 terminal password manager -- the
    # `passwords` command). A pure-Python app like timedate, and now ONE FLAT directory (no
    # pwlib/ sub-library): emit_plan() writes the entry script, the optional plaintext
    # importer, every working module, and the /usr/local/bin/passwords launcher to their
    # fixed root-owned system paths -- one single-file entry each, so the whole flat app is
    # expressed by the plan alone (no separate directory copy). No systemd service -- it is
    # an interactive command, not a boot service. Its runtime deps (gnupg for gpg, xclip for
    # the clipboard) are in the manifest. The OFFLINE Calamares install rsyncs all of it
    # onto the installed system, so `passwords` works there too, unlocking a store at
    # ~/Vault/passwords.txt.gpg. See packages/passwords/packaging.py.
    for entry in passwords.emit_plan():
        emit.write_text(
            airootfs / entry["dest"].lstrip("/"),
            entry["builder"](),
            mode=entry["mode"],
        )
    # Azzio backup (OUR home-directory backup -- the `backup` command). A pure-Python
    # app like passwords and a single flat directory: emit_plan() writes the entry
    # script (and any future module) plus the /usr/local/bin/backup launcher to their
    # fixed root-owned system paths -- one single-file entry each, so the whole flat app
    # is expressed by the plan alone (no separate directory copy). No systemd service --
    # it is an interactive command. Its runtime dep (gnupg for gpg) is already in the
    # manifest. The OFFLINE Calamares install rsyncs it onto the installed system, so
    # `backup` works there too, writing ~/backup_<date>.tar.gz.gpg. See
    # packages/backup/packaging.py.
    for entry in backup.emit_plan():
        emit.write_text(
            airootfs / entry["dest"].lstrip("/"),
            entry["builder"](),
            mode=entry["mode"],
        )
    # Azzio hypervisor (OUR per-directory QEMU/KVM VM runner -- the `hypervisor`
    # command). A pure-Python app like backup and a single flat directory: emit_plan()
    # writes the entry script (command_line_interface.py) and every working module plus the
    # /usr/local/bin/hypervisor launcher to their fixed root-owned system paths -- one
    # single-file entry each, so the whole flat app is expressed by the plan alone (no
    # separate directory copy). No systemd service -- it is an interactive command. Its
    # runtime deps (qemu-full, edk2-ovmf, virt-viewer) are in the manifest. The launcher
    # deliberately does NOT cd (unlike passwords): `hypervisor` derives the VM identity
    # from the caller's CWD, which the launcher must preserve. The OFFLINE Calamares
    # install rsyncs it onto the installed system, so `hypervisor` works there too. See
    # packages/hypervisor/packaging.py.
    for entry in hypervisor.emit_plan():
        emit.write_text(
            airootfs / entry["dest"].lstrip("/"),
            entry["builder"](),
            mode=entry["mode"],
        )
    # re-assert ownership of the live user's tree (new files were added under it).
    subprocess.run(_sudo() + ["chown", "-R", "1000:998", str(home)], check=False)


def _emit_apps(airootfs: Path, home: Path, ea: Path) -> None:
    """Overlay the per-application tweaks (kitty/vlc/gedit), each a self-contained
    package module exposing emit_plan() in the same builder/dest/mode/owner shape as
    openbox/librewolf. Several extras beyond the plain write loop, all driven by keys on
    the plan entries so this stays declarative:

      * owner "home" files are ALSO mirrored into /etc/skel (like _emit_desktop), so a
        Calamares-created user inherits them; owner "root" files are system-wide.
      * entries with "asset": <rel> COPY assets/<rel> verbatim to dest (kitty's scalable
        icon SVG, whose single source of truth is the repo asset).
      * entries with "render": {"asset","size"} RASTERIZE that SVG asset to a square PNG
        at dest (kitty's in-window titlebar icon kitty.app.png).
      * entries with "remove": True are DELETED from the airootfs rather than written
        (kitty removes the two PNG icons that would otherwise outrank our SVG).
      * entries with "compile_schemas": True trigger a glib-compile-schemas pass after
        emit (gedit's gschema override is inert until the schemas are recompiled).

    The gedit notepad-mode libgedit plugin (a compiled C .so that removes the New Tab
    action, strips the headerbar buttons and makes Ctrl+W quit -- the only route on the
    gedit-technology fork, which dropped Python plugins) is BUILT and installed here too,
    right after the plan loop.

    The /home/main subtree is re-chowned 1000:998 at the end (new user files were added).
    All of this lands in the airootfs overlay, so the OFFLINE Calamares install carries it
    onto the installed system with no separate installer step."""
    skel = airootfs / "etc/skel"
    apps_stage = ea / "apps"                            # staged bodies for the customize hook
    need_compile_schemas = False

    # Package-owned system paths we REPLACE/SUPPRESS cannot go in the airootfs overlay:
    # pacstrap's file-conflict check would abort (see libraries/pacman.py). They are
    # NoExtract'd and planted post-pacstrap instead. Map override target -> staged basename
    # (None == suppress-only, no body to stage). A replacement entry writes its body under
    # root/azzio/apps/<basename> (the hook installs it); a suppress-only entry is dropped
    # here entirely (NoExtract keeps the package file out -- no overlay action needed).
    _override_basename = {target: basename
                          for basename, target, _remove in pacman.ISO_APP_OVERRIDES}

    def _skel_mirror(entry: dict, dest_abs: str) -> Path | None:
        """If entry is a HOME file, return its /etc/skel mirror path (else None)."""
        if entry["owner"] == "home" and dest_abs.startswith(openbox.HOME + "/"):
            return skel / dest_abs[len(openbox.HOME) + 1:]
        return None

    # The per-application tweaks are DISCOVERED, not hard-coded: every package exposing an
    # emit_plan() (kitty icon | vlc vlcrc | gedit .desktop + gschema | libreoffice
    # registrymodifications.xcu | gimp gimprc | file_manager thunarrc/xfconf/gtk.css/bookmarks/uca.xml
    # + icon + the ~/Templates "Create Document" set | xviewer icon | ... plus any newly-added
    # packages/<app>/__init__.py) contributes its entries here, EXCEPT the ones the compiler
    # drives by name (_EXPLICIT_PACKAGES: the desktop pair openbox/librewolf, application_menu,
    # passwords, calamares, azzio). default_applications (a packages.azzio module, the XDG
    # mimeapps + preferred terminal) is appended explicitly since it is not an app-loop package.
    # Each entry is handled the same declarative way below regardless of which package produced
    # it, so the set can grow/shrink freely.
    app_mods = package_discovery.with_emit_plan(exclude=_EXPLICIT_PACKAGES)
    app_plan: list[dict] = []
    for name in sorted(app_mods):
        app_plan += app_mods[name].emit_plan()
    app_plan += default_applications.emit_plan()
    for entry in app_plan:
        dest_abs = entry["dest"]                       # absolute path on the target
        # Package-owned override path? Redirect its body to the post-pacstrap staging dir
        # (or drop it if suppress-only) instead of writing into the conflicting overlay.
        if dest_abs in _override_basename:
            basename = _override_basename[dest_abs]
            if basename is None:                       # suppress-only (kitty cat PNGs)
                continue                               # NoExtract handles it; nothing to write
            target = apps_stage / basename             # plant body for app_override_cp_sh()
            skel_dest = None                           # system file -- never skel-mirrored
        else:
            target = airootfs / dest_abs.lstrip("/")
            # Removal entries with no override mapping: unlink instead of writing. (Kept for
            # completeness; the kitty PNG removals are handled via the override map above.)
            if entry.get("remove"):
                target.unlink(missing_ok=True)
                continue
            skel_dest = _skel_mirror(entry, dest_abs)
        # Asset-copy entries (e.g. kitty's scalable icon SVG): copy the repo asset verbatim.
        if entry.get("asset"):
            emit.copy_asset(entry["asset"], target, mode=entry["mode"])
            if skel_dest is not None:
                emit.copy_asset(entry["asset"], skel_dest, mode=entry["mode"])
            continue
        # Render entries (e.g. kitty's titlebar kitty.app.png): rasterize the SVG asset.
        if entry.get("render"):
            r = entry["render"]
            emit.render_svg_png(r["asset"], target, r["size"], mode=entry["mode"])
            if skel_dest is not None:
                emit.render_svg_png(r["asset"], skel_dest, r["size"], mode=entry["mode"])
            continue
        # Binary-content entries (e.g. the file manager's compiled gettext .mo catalog): the builder
        # returns raw bytes written verbatim (no newline normalization). System locale
        # catalogs are root-owned, so skel_dest is None for them; the mirror is handled
        # generically in case a HOME binary is ever added.
        if entry.get("bytes_builder"):
            data = entry["bytes_builder"]()
            emit.write_bytes(target, data, mode=entry["mode"])
            if skel_dest is not None:
                emit.write_bytes(skel_dest, data, mode=entry["mode"])
            continue
        content = entry["builder"]()
        emit.write_text(target, content, mode=entry["mode"])
        # Mirror HOME-relative user files into /etc/skel for installed-system users
        # (same rule as _emit_desktop). System (root) files are not skel-mirrored.
        if skel_dest is not None:
            emit.write_text(skel_dest, content, mode=entry["mode"])
        if entry.get("compile_schemas"):
            need_compile_schemas = True
    # Compile + install the gedit notepad-mode libpeas plugin (.so). This is the only
    # mechanism on the gedit-technology fork that can remove the New Tab action, strip the
    # headerbar buttons and make Ctrl+W exit (config/CSS/GSettings/accels cannot; Python
    # plugins were dropped in gedit 49.0). Built from C like the application-menu daemon;
    # the .plugin metadata that pairs with it is emitted by the plan loop above, and the
    # gschema override's active-plugins enables it. Root-owned system path.
    gedit.build_plugin(airootfs / gedit.GEDIT_PLUGIN_SO_DEST.lstrip("/"))
    # gedit's glib schema override is inert until the machine-readable gschemas.compiled
    # is regenerated. On the ISO the actual recompile happens LATER and for free: glib2 (a
    # gedit dependency) ships /usr/share/libalpm/hooks/glib-compile-schemas.hook, which
    # runs PostTransaction whenever a *.gschema.* file changes -- and by then our override
    # has been copied in AND gedit's base schema is installed, so the hook compiles both
    # together. This explicit pass is therefore a harmless no-op at profile-emit time
    # (gedit's base schema is not installed yet, so glib-compile-schemas prints "No schema
    # files found: doing nothing"); we keep it as belt-and-braces (and because the
    # live-apply path, which drops the override onto an already-installed system, DOES need
    # it). Use the command the gedit modification defines (single source of truth).
    if need_compile_schemas:
        schemas_dir = airootfs / gedit.GLIB_SCHEMAS_DIR.lstrip("/")
        subprocess.run(_sudo() + ["glib-compile-schemas", str(schemas_dir)], check=False)
    # re-assert ownership of the live user's tree (new home files were added under it).
    subprocess.run(_sudo() + ["chown", "-R", "1000:998", str(home)], check=False)


def _emit_homedir(airootfs: Path, home: Path) -> None:
    """Create the home-directory LAYOUT -- the top-level folders and convenience symlinks
    that packages.file_manager.home_directory defines as the single source of truth (and that
    the file manager's sidebar mirrors). Unlike the emit_plan() modules this emits no file CONTENT:
    directories and symlinks are not text, so it walks home_directory's plain data with
    emit.mkdir()/emit.link() rather than a builder loop.

    Everything is created in BOTH the live user's /home/main AND /etc/skel, so a
    Calamares-created user on the installed system inherits the identical layout. Symlink
    targets are RELATIVE (home_directory keeps them that way on purpose) so each link is
    valid in every home it lands in -- an absolute /home/main/... target would dangle under
    /etc/skel and in a copied-out /home/<newuser>. The XDG trash chain
    (.local/share/Trash/{files,info}) is created BEFORE the "Trash" symlink so it resolves
    to a real directory instead of dangling.

    Runs before _emit_apps's closing `chown -R 1000:998`, so the new dirs/links in
    /home/main are swept into the live user's ownership with the rest of the tree; /etc/skel
    stays root-owned (skel is copied, not owned, by Calamares)."""
    skel = airootfs / "etc/skel"
    # The live user's /home/main (the passed `home`, == airootfs/home/main) AND /etc/skel.
    roots = (home, skel)
    for root in roots:
        # 1. The top-level directories (Desktop, Downloads, ... Videos).
        for name in home_directory.DIRECTORIES:
            emit.mkdir(root / name)
        # 1b. Extra non-sidebar directories (~/Templates for the file manager Create Document set).
        for name in home_directory.EXTRA_DIRECTORIES:
            emit.mkdir(root / name)
        # 2. The XDG trash chain -- created BEFORE the Trash symlink so it does not dangle.
        for rel in home_directory.TRASH_DIRS:
            emit.mkdir(root / rel)
        # 2b. The .ssh dir -- created (at 0700) BEFORE the SSH symlink so it does not dangle and
        #     so sshd accepts it. Was previously made only at runtime by the sshd bring-up.
        emit.mkdir(root / home_directory.SSH_DIR, mode=home_directory.SSH_DIR_MODE)
        # 3. The convenience symlinks (Trash/Cache/Config/Bashrc/Local). RELATIVE targets,
        #    verbatim from home_directory.LINKS, so they resolve against the link's own
        #    directory in every home. emit.link replaces any pre-existing entry.
        for name, target in home_directory.LINKS:
            emit.link(target, root / name)
        # (No ".home-directory" symlink: the "Home Directory" sidebar bookmark it used to back
        #  was deleted at the user's request -- see file_manager/home_directory.py and file_manager/sidebar.py.)
    # 4. Absolute-target symlinks (e.g. "Mounts" -> /run/media) -- created in the LIVE
    #    /home/main ONLY, NOT /etc/skel: these point at RUNTIME system locations (the udisks mount
    #    root), so they belong to the live session, not the per-user skel template. The target is
    #    an absolute system path used verbatim (not home-relative like LINKS above).
    for name, abs_target in home_directory.ABSOLUTE_LINKS:
        emit.link(abs_target, home / name)


def _emit_calamares(airootfs: Path) -> None:
    """Write the whole Calamares configuration tree under /etc/calamares."""
    base = airootfs / "etc/calamares"
    for rel, content in calamares.emit_map().items():
        emit.write_text(base / rel, content)
    # The Calamares WINDOW ICON: rasterize the standardized "Az'" vector app tile to a REAL
    # PNG inside the branding component dir (branding/azzio/productIcon.png). branding.desc
    # names it by that branding-relative filename in `productIcon`, so Calamares resolves it
    # to an absolute path and QIcon() loads it as the window icon -- which OpenBox draws on
    # the titlebar (rc.xml titleLayout's `N`). Calamares wants a real raster FILE here (a
    # PNG QIcon loads directly -- see calamares.py PRODUCT_ICON_FILE), so we rasterize the
    # SVG rather than shipping the vector. Same source asset as the .desktop launcher icon
    # (packages/openbox.INSTALLER_ICON_ASSET), so the topbar icon matches the launcher.
    emit.render_svg_png(
        openbox.INSTALLER_ICON_ASSET,
        base / "branding" / calamares.BRANDING / calamares.PRODUCT_ICON_FILE,
        openbox.INSTALLER_ICON_PNG_SIZE,
        mode=0o644,
    )


def _emit_power(airootfs: Path) -> None:
    """Emit the power-management files (lid/power-button + PC-vs-laptop idle sleep).

    Four root-owned artifacts, all under /etc or /usr/local/bin, so the OFFLINE
    Calamares install (unpackfs rsyncs the live rootfs) carries them onto the
    installed system unchanged -- and they also govern the live ISO:

      1. STATIC logind drop-in (10-azzio-power.conf): lid does nothing, power
         button powers off. A plain /etc file, effective immediately at boot.
      2. The azzio-sleep-policy script (/usr/local/bin, 0755): decides PC vs laptop
         (battery present?) and AC state at RUNTIME and writes the idle-sleep
         drop-in (20-azzio-sleep.conf), then reloads logind.
      3. Its systemd service (azzio-sleep-policy.service): runs the script at boot;
         the enable-symlink is added in _link_services.
      4. Its udev rule: re-runs the service on AC-adapter plug/unplug so the
         15-minute idle timer arms/disarms live.

    The dynamic 20-*.conf is NOT emitted here -- the script generates it on the
    running system (its value depends on live hardware state, so baking a fixed one
    would be wrong)."""
    emit.write_text(
        airootfs / "etc/systemd/logind.conf.d/10-azzio-power.conf",
        system.LOGIND_POWER_DROPIN,
    )
    emit.write_exec(
        airootfs / "usr/local/bin/azzio-sleep-policy", system.SLEEP_POLICY_SCRIPT
    )
    emit.write_text(
        airootfs / "etc/systemd/system/azzio-sleep-policy.service",
        system.SLEEP_POLICY_SERVICE,
    )
    emit.write_text(
        airootfs / "etc/udev/rules.d/99-azzio-sleep-policy.rules",
        system.SLEEP_POLICY_UDEV_RULE,
    )


def _emit_shared_mount(airootfs: Path) -> None:
    """Write the virtiofs shared-folder .mount unit and create its mountpoint.

    The hypervisor exports the host ./Shared folder over virtiofs (mount tag
    "shared" -- an opaque identifier, stays lowercase); this unit mounts it at
    /home/main/Shared on boot for BOTH variants, so --shared works on the headed
    variant too (it no longer rides on the ssh bring-up). The mountpoint dir must
    exist for systemd to mount onto it (the home layout also ships /home/main/Shared
    -- see home_directory.DIRECTORIES); it is owned by `main` (uid 1000) via the
    closing chown in _emit_provision/_emit_apps that covers all of /home/main. The
    enable-link is added in _link_services."""
    emit.write_text(
        airootfs / "etc/systemd/system/home-main-Shared.mount",
        system.HOME_MAIN_SHARED_MOUNT,
    )
    emit.mkdir(airootfs / "home/main/Shared")


def _emit_tty1_autologin(airootfs: Path) -> None:
    """Override the releng getty@tty1 autologin so it logs in `main` (not root).
    The graphical session runs X as the unprivileged live user; `main`'s
    .bash_profile then execs startx. Root autologin would run the whole desktop
    as root, which Calamares and Qt both dislike."""
    dropin = airootfs / "etc/systemd/system/getty@tty1.service.d/autologin.conf"
    emit.write_text(dropin, system.GETTY_TTY1_AUTOLOGIN)


def _refresh_own_in_pacstrap_cache(full_compile: bool = False) -> None:
    """Refresh the packages the makepkg stage BUILT (calamares and librewolf, both
    tiers) IN the persistent pacstrap CacheDir (cache/pacman-pkg) so mkarchiso's
    pacstrap always reads the freshly-rebuilt bytes from cache -- never a stale
    copy, and never a file:// re-fetch. The downloaded Arch packages are immutable
    per version and handled by the normal cache path, so they are deliberately NOT
    touched here.

    Two failure modes this closes, both caused by makepkg NOT being reproducible
    bit-for-bit (a rebuild of calamares/librewolf yields a byte-different
    *.pkg.tar.zst under the SAME versioned filename, so its checksum in
    pacstrap-azzio-repo.db changes each build):

      1. Stale-checksum abort. pacstrap consults its CacheDir BEFORE the file://
         repo. A same-named file left by a PRIOR build fails pacstrap's checksum
         check ("invalid or corrupted package"); on /dev/null stdin it can't answer
         the "delete it? [Y/n]" prompt and aborts the whole ISO build.

      2. file:// max-file-size abort. Simply DELETING the stale copy (an earlier
         fix) forced pacstrap to re-fetch from the file:// repo -- but pacman caps
         a file:// transfer at the DB-recorded size and rejects a package that hits
         exactly that ceiling ("Exceeded the maximum allowed file size"), which the
         138 MB librewolf package does. Observed exactly this.

    Overwriting the cached copy in place with the current repo bytes gives pacstrap
    a VALID cache hit: the checksum matches (correct content) and no download
    happens (so the size cap never applies). The downloaded Arch packages are
    untouched -- they're immutable for a given version, so their cached copy always
    matches."""
    cache = paths.PACSTRAP_CACHE
    repo = paths.PKG_REPO
    if not cache.is_dir():
        return
    from makepkg import produced_names
    PRODUCED = produced_names(full_compile)          # this tier: which to REFRESH (copy in)
    # Every name the makepkg stage can EVER produce. Both tiers build the same set
    # (calamares + librewolf) now, so this union equals PRODUCED -- it is kept as a
    # union so that if the tiers ever diverge again, cleanup still spans BOTH sets.
    # It must: a byte-different rebuild under the SAME version-rel filename left in
    # this CacheDir by a prior run would fail pacstrap's checksum check, and with
    # stdin on /dev/null pacstrap cannot answer the delete prompt -- the ISO build
    # aborts. (Filename equality means a name-only staleness check would MISS it; we
    # compare CONTENT below.)
    ALL_OWN = tuple(sorted(set(produced_names(True)) | set(produced_names(False))))
    import hashlib

    def _sha(p: Path) -> str:
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    sudo = _sudo()
    # Cleanup pass (over the UNION of tiers). For every own-name, drop any cached copy
    # that does not byte-match the copy currently in the repo -- whether it is a
    # superseded VERSION (different filename) or a same-version file with different
    # BYTES (a non-reproducible makepkg rebuild yields new bytes under the same
    # versioned filename). A repo copy with matching bytes
    # is left for the refresh pass. A cached file whose name isn't in the repo at all
    # (name fully retired) is dropped too. This gives pacstrap either a valid cache hit
    # or a clean miss (it then reads the correct file from the file:// repo), never a
    # checksum-mismatch abort.
    for name in ALL_OWN:
        repo_by_name = {p.name: p for p in repo.glob(f"{name}-*.pkg.tar.zst")}
        for cached in cache.glob(f"{name}-*.pkg.tar.zst"):
            repo_copy = repo_by_name.get(cached.name)
            if repo_copy is not None and _sha(cached) == _sha(repo_copy):
                continue  # correct bytes already cached -> keep
            subprocess.run(sudo + ["rm", "-f", str(cached), str(cached) + ".sig"],
                           check=False)
    # Refresh pass (this tier's built packages only): mirror each current repo copy of a
    # TIER-BUILT package into the cache when absent (the cleanup above removed any stale
    # one). Only makepkg-built packages need their bytes forced in place -- downloaded
    # Arch packages (default-tier calamares) are re-fetched from the file:// repo on the
    # clean miss the cleanup produced, so we do NOT copy them here (that would also hit
    # the file:// max-file-size cap that motivated in-place refresh for big librewolf).
    for name in PRODUCED:
        for repo_copy in repo.glob(f"{name}-*.pkg.tar.zst"):
            cached = cache / repo_copy.name
            if cached.is_file():
                continue  # cleanup kept it only if bytes already matched -> valid hit
            print(f"    [+] Refreshing {repo_copy.name} in pacstrap cache "
                  f"(rebuilt; syncing bytes so pacstrap gets a valid cache hit).")
            subprocess.run(sudo + ["cp", "-f", str(repo_copy), str(cached)], check=False)
            # keep a matching .sig alongside if the repo has one. The offline file://
            # repo runs SigLevel = Never (libraries/pacman.py) so pacstrap does not verify
            # it -- this copy is a harmless belt-and-braces, not load-bearing.
            sig = repo_copy.with_suffix(repo_copy.suffix + ".sig")
            if sig.is_file():
                subprocess.run(sudo + ["cp", "-f", str(sig), str(cache / sig.name)], check=False)


def _refold_own_packages_into_repo(W: Path, full_compile: bool = False) -> None:
    """After makepkg drops our built package(s) into cache/pkgs/repo/, re-reconcile
    the local repo index so those packages are in pacstrap-azzio-repo.db, then
    RE-stage the repo + db into the airootfs payload dir. build_cache already
    staged the Arch packages there; this overlays our built package(s) on top so
    mkarchiso's pacstrap and the on-disk installer resolve them from the same
    offline repo. full_compile decides which packages count as OUR built ones
    (default: librewolf only; full: calamares + librewolf)."""
    pkg_repo = paths.PKG_REPO
    pkg_db = paths.PKG_DB
    # Re-run the incremental index reconcile (delta: only the 2 new packages added).
    downloader._reconcile_index(pkg_repo, lambda _p: None)
    # FORCE re-add of our OWN packages so the DB checksum tracks the just-rebuilt
    # file. _reconcile_index keys the delta by name-ver-rel: a rebuilt own package
    # keeps its version (e.g. librewolf-153.0.1-1) but makepkg is NOT reproducible,
    # so the *bytes* (hence the SHA256/CSIZE the .db records) change every build.
    # The delta sees the key already indexed and SKIPS it, leaving the DB pinned to
    # a PRIOR build's checksum while the repo file is the current one. pacstrap then
    # validates the current file against the stale DB checksum and aborts with
    # "invalid or corrupted package (checksum)" (observed exactly this on librewolf).
    # repo-add (no -n) overwrites the same-version entry, refreshing SHA256+CSIZE to
    # match the file on disk. Idempotent and cheap (2 packages).
    downloader._readd_own_packages(pkg_repo, full_compile)
    # A prior build may have cached an OLDER byte-image of our built packages in the
    # persistent pacstrap CacheDir. Refresh them IN PLACE with the freshly-rebuilt
    # bytes so mkarchiso's pacstrap gets a valid cache hit -- avoiding both the
    # checksum-mismatch abort AND the file:// max-file-size abort a delete-and-
    # refetch would trigger on the 138 MB librewolf package.
    _refresh_own_in_pacstrap_cache(full_compile)
    # Re-stage into the airootfs payload the on-disk installer copies from.
    ea = W / "airootfs" / "root/azzio"
    final_db = ea / "pacstrap-azzio-db"
    final_cache = ea / "pacstrap-azzio-repo"
    final_db.mkdir(parents=True, exist_ok=True)
    final_cache.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-r", f"{pkg_db}/.", f"{final_db}/"], check=False)
    subprocess.run(["cp", "-r", f"{pkg_repo}/.", f"{final_cache}/"], check=False)


def _unmount_worktree(sudo) -> None:
    aw = paths.AIROOTFS
    if not aw.is_dir():
        return
    for m in ("proc", "sys", "dev", "run"):
        p = aw / m
        if subprocess.run(["mountpoint", "-q", str(p)]).returncode == 0:
            subprocess.run(sudo + ["umount", "-lf", str(p)], check=False)
    subprocess.run(sudo + ["umount", "-R", str(aw)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def _check_host_deps(sudo, offline: bool) -> None:
    # archiso/git/base-devel/go are the base build toolchain; the application-menu build
    # deps (gtk3 + pkgconf + gcc) are appended because _emit_desktop COMPILES the C/GTK3
    # menu daemon (application_menu.build_daemon) later in this run -- without the GTK3
    # dev stack present here, that `make` dies with "gtk/gtk.h: No such file or directory"
    # and aborts the whole build. Listing them here also means the "already present"
    # early-return below cannot skip a host that is missing only the GTK3 dev stack.
    # + the gedit notepad-mode plugin build deps (the `gedit` pkg-config module -> the
    # gedit/GTK3/libpeas dev headers): _emit_apps COMPILES that libpeas plugin later in
    # this run, so the dev stack must be present here or `make` dies on a missing header.
    # + the bare-`azzio` C terminal UI build dep (just gcc): _emit_desktop COMPILES that
    # UI (terminal_user_interface_build.build_terminal_user_interface) later in this run. It is pure libc (no ncurses/GTK), so gcc
    # -- already pulled in by base-devel / the menu deps -- is all it needs; listed for
    # completeness so the dependency intent is explicit.
    host_pkgs = (["archiso", "git", "base-devel", "go"]
                 + application_menu.MENU_BUILD_DEPS + gedit.GEDIT_PLUGIN_BUILD_DEPS
                 + terminal_user_interface_build.TERMINAL_USER_INTERFACE_BUILD_DEPS)
    if subprocess.run(["pacman", "-Qq", *host_pkgs],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        print("    [+] Build-host dependencies already present, skipping sync (offline).")
        return
    if offline:
        sys.stderr.write(
            "[x] Missing build-host dependencies but the package cache is complete, so\n"
            "    this run must stay fully offline. Install them yourself:\n"
            f"    {'sudo ' if sudo else ''}pacman -Sy --needed {' '.join(host_pkgs)}\n"
            "    or re-run with FORCE_ONLINE=1 (or after 'git clean -Xdf').\n"
        )
        raise SystemExit(1)
    print("    [+] Installing missing build-host dependencies...")
    # Teed so the install's output reaches compile-full.log live. Preserve the old
    # check=True raise-on-failure: run_teed returns the exit code, so raise here.
    cmd = sudo + ["pacman", "-Sy", "--noconfirm", "--needed", *host_pkgs]
    rc = logstream.run_teed(cmd)
    if rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)


# Releng-inherited multi-user.target.wants enable-links Azzio must NOT ship enabled.
# The stock archiso `releng` profile enables sshd on the official Arch ISO by shipping
# airootfs/etc/systemd/system/multi-user.target.wants/sshd.service. _copy_releng copies
# releng verbatim (symlinks preserved), so that link survives onto BOTH Azzio variants
# unless stripped -- which is exactly why the DEFAULT headed ISO was booting with sshd active
# on :22 (`systemctl status sshd` -> enabled; running). ssh must be OFF on the base ISO and
# ON only on the ssh variant, where it is enabled at boot by sshd-hypervisor-setup.service
# (see _apply_variant / packages/azzio/sshd.py), NOT by this inherited stock want. So we
# delete the releng sshd want here, before any overlay; the ssh variant re-enables sshd
# through its own mechanism, leaving the stock want permanently stripped.
_RELENG_WANTS_TO_STRIP = ("sshd.service",)


def _strip_releng_wants(W: Path) -> None:
    """Remove releng-inherited multi-user.target.wants links Azzio must not ship enabled
    (currently the stock sshd.service want -- ssh is opt-in per variant, never a releng
    default). Best-effort per name so a future releng that drops one is a no-op, not a break."""
    wants = W / "airootfs/etc/systemd/system/multi-user.target.wants"
    for name in _RELENG_WANTS_TO_STRIP:
        (wants / name).unlink(missing_ok=True)


def _copy_releng(W: Path) -> None:
    src = Path("/usr/share/archiso/configs/releng")
    if not src.is_dir():
        raise SystemExit(f"[x] archiso releng profile not found at {src}; is archiso installed?")
    emit.copy_tree(src, W)
    # Strip releng's inherited sshd enable-link so the DEFAULT headed ISO ships sshd DISABLED
    # (the ssh variant re-enables it per-variant). Without this the base ISO listens on :22.
    _strip_releng_wants(W)


# The archiso stock network stack Azzio must NOT run: the releng profile enables
# systemd-networkd + systemd-resolved (its airootfs ships them enabled and drops
# /etc/systemd/network/20-{ethernet,wlan,wwan}.network DHCP match files, plus
# /etc/resolv.conf -> the resolved stub). Azzio networks via NetworkManager
# (enabled in _link_services), so shipping BOTH stacks makes them RACE for every
# interface: networkd matches the device first, DHCPs it, and NetworkManager then
# sees the link as `managed-type: 'external'` and never applies its own profiles.
#
# THE BUG this fixes (found by installing then booting the target): a MANUAL/static
# IPv4 chosen on the Calamares Network page IS written to the target as
# /etc/NetworkManager/system-connections/azzio-static.nmconnection (the networkcfg
# patch works), but the installed system still comes up on a networkd DHCP lease --
# "ip, subnet mask, nothing was modified" -- because networkd won the race and NM's
# static profile stayed inactive. (It also affected the live session, benignly:
# both stacks DHCP'd, so nobody noticed until a static address was pinned.)
#
# We MASK the units (symlink -> /dev/null) rather than delete releng's enable-links:
# a mask wins no matter how (or where) the unit was pulled in -- a lingering .socket,
# a dbus activation, or a systemd preset can re-pull a merely-`disable`d unit, but a
# masked unit cannot start at all. The list covers, for BOTH networkd and resolved:
# the .service, the wait-online unit (networkd's wait-online is masked; _link_services
# separately enables NetworkManager-wait-online.service into network-online.target so a
# NM-managed link -- not a dead networkd wait -- is what that target waits on), the
# generator, AND every
# socket that socket-activates them. Masking the .service alone is enough to stop the
# race (a socket then just fails to activate a masked service), but every one of these
# sockets is WantedBy=sockets.target -- so if we leave them un-masked they still START
# at boot and log a failed activation of the masked service each time something probes
# the socket. Masking the sockets too keeps `systemctl` clean and honours the "mask
# anything that can re-pull it" rule above. Masking a unit a given systemd build does
# not ship is a harmless no-op (a /dev/null symlink shadowing nothing), so the varlink/
# monitor/resolve-hook sockets (newer systemd) are safe to list unconditionally. These
# masks live in the airootfs, so unpackfs carries them onto the installed target too --
# one fix for the live session, the GUI install, and the CLI install alike.
_ARCHISO_NETWORK_UNITS_TO_MASK = (
    # systemd-networkd + everything that can start or re-pull it.
    "systemd-networkd.service",
    "systemd-networkd.socket",
    "systemd-networkd-wait-online.service",
    "systemd-networkd-varlink.socket",
    "systemd-networkd-varlink-metrics.socket",
    "systemd-networkd-resolve-hook.socket",
    "systemd-network-generator.service",
    # systemd-resolved + its socket-activation entry points.
    "systemd-resolved.service",
    "systemd-resolved-varlink.socket",
    "systemd-resolved-monitor.socket",
)


def _neutralize_archiso_network_stack(airootfs: Path) -> None:
    """Make NetworkManager the SOLE network manager on the live medium AND (via
    unpackfs) the installed target, by neutralizing the archiso stock networkd/resolved
    stack the releng profile ships enabled. See _ARCHISO_NETWORK_UNITS_TO_MASK for the
    full rationale (the static-IPv4 install bug). Three parts, all in the airootfs:

      1. MASK each networkd/resolved unit (/etc/systemd/system/<unit> -> /dev/null) so
         it can never start, regardless of how it was enabled.
      2. REMOVE releng's /etc/systemd/network/*.network DHCP match files, so nothing
         (e.g. a manual `systemctl start systemd-networkd`) can still grab an interface
         from them; NetworkManager owns every device instead.
      3. REPLACE the inherited /etc/resolv.conf -> /run/systemd/resolve/stub-resolv.conf
         symlink (dead once resolved is masked) with an empty real file NetworkManager
         (dns=default) rewrites at runtime -- so DNS works without systemd-resolved.

    Idempotent and defensive: masks overwrite any existing link (emit.link), the
    .network removal is per-file missing_ok, and the resolv.conf reset unlinks a
    symlink-or-file before writing. Called from _link_services (the unit/policy step)."""
    base = airootfs / "etc/systemd/system"
    emit.mkdir(base)
    for unit in _ARCHISO_NETWORK_UNITS_TO_MASK:
        # Mask: the enable-link/socket/preset is overridden by a /dev/null symlink.
        emit.link("/dev/null", base / unit)
    # Drop releng's DHCP-on-everything match files so no networkd config lingers.
    netdir = airootfs / "etc/systemd/network"
    if netdir.is_dir():
        for netfile in netdir.glob("*.network"):
            netfile.unlink(missing_ok=True)
    # Replace the resolved stub symlink with an empty file NetworkManager will own.
    # (A dangling symlink would leave the system with no DNS once resolved is masked.)
    resolv = airootfs / "etc/resolv.conf"
    if resolv.is_symlink() or resolv.exists():
        resolv.unlink()
    emit.write_text(resolv, RESOLV_CONF_PLACEHOLDER)


# The placeholder /etc/resolv.conf shipped in place of archiso's resolved-stub symlink.
# NetworkManager (dns=default, the default backend) rewrites this file with the active
# connection's nameservers at runtime; until then it is a harmless empty resolver. A
# real (non-symlink) file is REQUIRED: NetworkManager's default rc-manager refuses to
# clobber a symlink it does not own, so a leftover dangling stub symlink would leave the
# installed system with no working DNS.
RESOLV_CONF_PLACEHOLDER = (
    "# Managed by NetworkManager (Azzio networks via NetworkManager, not\n"
    "# systemd-resolved). This file is rewritten at runtime with the active\n"
    "# connection's DNS servers. See compiler._neutralize_archiso_network_stack.\n"
)


def _brand_boot_menus(W: Path) -> None:
    """Rebrand the copied releng boot menus (systemd-boot UEFI + syslinux BIOS) and
    SKIP the first-boot menu -- boot straight into the default Azzio entry.

    Runs right after _copy_releng, over the releng files it laid down. The releng
    profile is systemd-boot-only for UEFI (profiledef bootmodes list no `*.grub.*`),
    so the systemd-boot loader here IS the first-boot menu the screenshot shows.

    SKIP: the loader.conf `timeout 0` (UEFI) and archiso_sys.cfg `TIMEOUT 1` (BIOS)
    make the default entry boot immediately with no menu drawn. The menu is still
    reachable by holding a key during boot -- so it is a skip, not a removal -- and
    the branding/trim below is what it shows IF forced open.

    UEFI entries: overwrite 01/02 IN PLACE (same filenames) rather than adding
    differently-named ones alongside -- otherwise the menu shows BOTH the stock
    "Arch Linux install medium" rows AND ours (duplicated rows all reading "Arch
    Linux"). Overwriting rebrands them to Azzio.

    The extra UEFI rows beside 01/02 -- gone so a forced-open menu is clean too -- go
    via the loader.conf override + one deletion:
      * "EFI Shell"          -- systemd-boot AUTO-discovers the shellx64.efi mkarchiso
                                plants on the ESP; `auto-entries no` hides that (and
                                systemd-boot's own self-entry).
      * "Reboot Into Firmware Interface" -- systemd-boot AUTO-generates it; `auto-firmware
                                no` hides it (still reachable with the `f` key).
      * "Memtest86+"         -- NOT auto-discovered; it is the EXPLICIT releng entry
                                03-archiso-memtest86+x64.conf, so auto-entries can't hide
                                it -- we DELETE the .conf. `auto-entries no` leaves our
                                explicit 01/02 untouched. missing_ok: a future releng may
                                rename/drop it.
    """
    emit.write_text(W / "efiboot/loader/entries/01-archiso-linux.conf", system.BOOT_UEFI_LINUX)
    emit.write_text(W / "efiboot/loader/entries/02-archiso-speech-linux.conf", system.BOOT_UEFI_SPEECH)
    emit.write_text(W / "efiboot/loader/loader.conf", system.BOOT_UEFI_LOADER)
    (W / "efiboot/loader/entries/03-archiso-memtest86+x64.conf").unlink(missing_ok=True)
    # syslinux (BIOS): overlay the top-level archiso_sys.cfg (TIMEOUT 1 -> skip menu),
    # rebrand the two boot labels, and rebrand the menu head's `MENU TITLE` (releng
    # ships "Arch Linux"). BIOS syslinux has no auto-discovered extras to trim.
    emit.write_text(W / "syslinux/archiso_sys.cfg", system.BOOT_BIOS_SYSLINUX_SYS)
    emit.write_text(W / "syslinux/archiso_sys-linux.cfg", system.BOOT_BIOS_SYSLINUX)
    emit.write_text(W / "syslinux/archiso_head.cfg", system.BOOT_BIOS_SYSLINUX_HEAD)


def _emit_fastfetch(ea: Path, home: Path) -> None:
    """Write the azzio fastfetch configuration + Az' logo for the live user, and stage
    a copy under root/azzio/fastfetch so the on-disk installer can replant it
    into the installed user's ~/.config/fastfetch."""
    cfg = home / ".config/fastfetch"
    emit.write_text(cfg / "config.jsonc", fastfetch.config_jsonc())
    emit.write_text(cfg / fastfetch.LOGO_FILENAME, fastfetch.logo_txt())
    # staged copy for the installer to plant on the installed system
    staged = ea / "fastfetch"
    emit.write_text(staged / "config.jsonc", fastfetch.config_jsonc())
    emit.write_text(staged / fastfetch.LOGO_FILENAME, fastfetch.logo_txt())


def _link_services(airootfs: Path) -> None:
    # Graphical live medium WITHOUT a display manager: the tty1 autologin (overridden
    # to `main`) drops into a login shell whose ~/.bash_profile execs startx ->
    # openbox-session -> (autostart) Calamares. So there is deliberately NO
    # display-manager unit and NO graphical.target.wants here; we only enable the
    # multi-user daemons and the two azzio oneshots. X is started from the shell, not
    # by systemd.
    #
    # These enable-links are variant-independent (both ISOs get them). The sshd
    # variant's extra sshd-hypervisor-setup enable-link is added per-variant in
    # _apply_variant, just before that variant's mkarchiso pass.
    base = airootfs / "etc/systemd/system"
    emit.mkdir(base / "multi-user.target.wants")
    # bluetooth.service is DELIBERATELY NOT enabled here: Bluetooth is OFF by default on
    # Azzio. `azzio network bluetooth on` enables + starts it (and rfkill-unblocks the
    # radio) on demand; leaving it out of multi-user.target.wants keeps the radio down at
    # boot. NetworkManager (the network stack) and CUPS (printing) stay auto-enabled.
    # spice-vdagentd is the SPICE guest agent's system daemon: it bridges the
    # com.redhat.spice.0 virtio channel so the session spice-vdagent can sync the guest
    # pointer/clipboard/resolution with the SPICE client. Enabling it fixes the SPICE-guest
    # pointer regression (no hover / dropped clicks / stuck labels) -- see the spice-vdagent
    # note in packages.x86_64 and the autostart line in packages/openbox. Harmless on
    # non-SPICE systems (the daemon idles with no channel). Auto-enabled on BOTH ISOs and,
    # via unpackfs, the installed system.
    for svc in ("NetworkManager.service", "org.cups.cupsd.service", "spice-vdagentd.service"):
        emit.link(f"/usr/lib/systemd/system/{svc}", base / f"multi-user.target.wants/{svc}")
    # We enable NetworkManager with a MANUAL .wants symlink (above), which -- unlike a
    # real `systemctl enable` -- does NOT process NetworkManager.service's
    # `[Install] Also=NetworkManager-wait-online.service`. Since we ALSO mask
    # systemd-networkd-wait-online.service, nothing would otherwise be pulled into
    # network-online.target on the LIVE ISO, so locale-setup.service (which
    # Wants=+After=network-online.target -- see system.py; its IP-geo locale step wants
    # connectivity first) would proceed before the network is actually up. Enable NM's
    # own wait-online explicitly so network-online.target waits for a NetworkManager-
    # managed link. This is a background wait only -- the autologin console/desktop path
    # orders on network.target, not network-online.target, so an offline live boot is
    # NOT blocked (and nm-online caps at 60s, shorter than the masked networkd wait's
    # ~120s). (On the installed target Calamares runs a real `systemctl enable
    # NetworkManager`, which processes Also= and produces the identical symlink; this one
    # makes the live ISO behave the same and is a harmless idempotent duplicate there.)
    emit.link("/usr/lib/systemd/system/NetworkManager-wait-online.service",
              base / "network-online.target.wants/NetworkManager-wait-online.service")
    # NetworkManager is the SOLE network stack: neutralize archiso's stock
    # systemd-networkd/systemd-resolved (releng ships them enabled), which would
    # otherwise RACE NetworkManager for every interface and leave a static-IPv4
    # install stuck on a networkd DHCP lease. Masks + config removal live in the
    # airootfs, so unpackfs carries them onto the installed target too. See
    # _neutralize_archiso_network_stack for the full rationale.
    _neutralize_archiso_network_stack(airootfs)
    emit.link("/etc/systemd/system/locale-setup.service", base / "multi-user.target.wants/locale-setup.service")
    emit.link("/etc/systemd/system/pkgs-setup.service", base / "multi-user.target.wants/pkgs-setup.service")
    # PC-vs-laptop idle-sleep policy oneshot: enabled on BOTH ISOs (and, via unpackfs,
    # the installed system). Runs azzio-sleep-policy at boot to write the idle
    # drop-in for the detected chassis/AC state; the udev rule re-runs it on plug/
    # unplug. See _emit_power / libraries/system.py.
    emit.link("/etc/systemd/system/azzio-sleep-policy.service",
              base / "multi-user.target.wants/azzio-sleep-policy.service")
    # Azzio timedate home page service: the Flask Time + Calendar site (localhost:49154)
    # LibreWolf lands on. Enabled on BOTH ISOs (and, via unpackfs, the installed system)
    # so the home page is listening at boot. See timedate.service_unit() / _emit_desktop.
    emit.link(timedate.SERVICE_SYSTEM_PATH,
              base / f"multi-user.target.wants/{timedate.SERVICE_NAME}")
    # The virtiofs shared-folder auto-mount: enabled on BOTH ISOs (and, via unpackfs,
    # the installed system) so the host ./Shared folder appears at /home/main/Shared
    # on boot regardless of --ssh. This is the fix for the headed-variant coupling;
    # the unit body + mountpoint come from _emit_shared_mount. A .mount enable-link is
    # a symlink named after the unit, same mechanism as the .service links above.
    emit.link("/etc/systemd/system/home-main-Shared.mount",
              base / "multi-user.target.wants/home-main-Shared.mount")


def _switch_offline(W: Path, conf: str, localrepo: Path) -> None:
    """Drop stale partial downloads, rewrite to the local file:// repo, write it,
    and assert the rewrite actually landed (parity with the old bash guards)."""
    # A file:// directory listing must not trip over a zero-byte *.part left by an
    # interrupted -Sw; drop them first (harmless if none exist).
    for part in localrepo.glob("*.part"):
        part.unlink(missing_ok=True)
    conf = pacman.switch_to_local_repo(conf, str(localrepo))
    emit.write_text(W / "pacman.conf", conf)
    if "[pacstrap-azzio-repo]" not in conf:
        sys.stderr.write(
            "    [!] Offline conf rewrite did not inject the local repo -- check libraries/pacman.py.\n"
        )


def _write_build_pacman_conf(W: Path, offline: bool, bar: ProgressBar) -> None:
    """Write the profile pacman.conf mkarchiso's pacstrap uses. Injects the
    persistent CacheDir, and (offline) rewrites to the local file:// repo."""
    paths.PACSTRAP_CACHE.mkdir(parents=True, exist_ok=True)
    conf = pacman.build_profile_conf(cachedir=str(paths.PACSTRAP_CACHE) + "/")
    localrepo = paths.PKG_REPO
    if offline:
        print(f"    [+] Complete cache present -- building OFFLINE from {localrepo} (no mirror).")
        _switch_offline(W, conf, localrepo)
    else:
        _probe_and_maybe_switch(W, conf, localrepo, bar)


def _finalize_build_pacman_conf(W: Path) -> None:
    """RE-resolve the mkarchiso build pacman.conf now that the local repo is complete.

    _write_build_pacman_conf runs at step 11 -- BEFORE the cache warm (step 12) and the
    makepkg fold-in of our OWN packages (step 13). On a COLD build the local repo index
    does not exist yet at step 11, so _probe_and_maybe_switch cannot take its offline
    (file://-only) arm; and when the host is a foreign distro whose mirrorlist the probe
    Includes, the probe itself fails, leaving a conf with NO local repo and multilib
    commented. mkarchiso's pacstrap then aborts with "target not found: calamares" (it
    lives ONLY in the local repo) and the four lib32-* multilib targets.

    By this point (after _refold_own_packages_into_repo) cache/pkgs/repo/ holds every
    pinned Arch package -- INCLUDING the lib32-* multilib set the download step fetched --
    plus our built calamares/librewolf/file_manager, and the reconciled index exists. Switching
    the conf to that all-file:// SigLevel=Never repo makes pacstrap resolve the ENTIRE
    manifest locally, with no network Include to stall on. Idempotent: an already-offline
    conf (the complete-cache path) just gets re-written to the same local repo.
    """
    if not paths.LOCALREPO_INDEX.exists():
        # No local repo to switch to (e.g. a cold build where mkarchiso will pacstrap
        # straight from the network repos the step-11 online arm kept). Leave the conf
        # _write_build_pacman_conf already wrote untouched.
        return
    localrepo = paths.PKG_REPO
    conf = pacman.build_profile_conf(cachedir=str(paths.PACSTRAP_CACHE) + "/")
    _switch_offline(W, conf, localrepo)


def _probe_and_maybe_switch(W: Path, conf: str, localrepo: Path, bar: ProgressBar) -> None:
    # A PRESENT local repo index means step 13 already fetched every manifest package
    # (from the SAME pinned ALA snapshot) into cache/pkgs/repo/ AND the makepkg stage
    # folded our own packages (calamares/librewolf/...) into it -- so the file:// repo
    # can serve the ENTIRE pacstrap on its own. Install from it offline and DO NOT reach
    # the network, whether or not mirrors are up. This is not just an optimisation: the
    # download step caches package BODIES with SigLevel=Never and so ships NO detached
    # .sig files, while the network [core]/[extra] carry SigLevel=Required. Keeping the
    # network repos as a pacstrap source therefore forces pacman to fetch each
    # .pkg.tar.zst.sig from the pinned archive host (archive.archlinux.org) purely to
    # satisfy the signature policy -- and that single throttled host stalls ("Operation
    # too slow. Less than 1 bytes/sec"), aborting the whole transaction (the observed
    # compile failure on linux-firmware-marvell's .sig). The offline conf is all-file://
    # SigLevel=Never, so there is nothing to fetch and nothing to verify remotely.
    if paths.LOCALREPO_INDEX.exists():
        print(f"    [+] Local repo present -- building OFFLINE from {localrepo} "
              "(pinned packages already cached; no archive fetch).")
        _switch_offline(W, conf, localrepo)
        return
    # No local repo yet -- the only case that genuinely needs the network. Probe the
    # mirrors and, if reachable, keep the network [core]/[extra] so pacstrap can fetch
    # from them (nothing is cached to serve the build otherwise).
    sudo = _sudo()
    probe = W / ".netprobe-db"
    subprocess.run(["rm", "-rf", str(probe)], check=False)
    (probe / "sync").mkdir(parents=True, exist_ok=True)
    # write the network-repo conf first so the probe uses the exact mirror set.
    emit.write_text(W / "pacman.conf", conf)
    ok = subprocess.run(
        sudo + ["pacman", "-Sy", "--config", str(W / "pacman.conf"), "--dbpath", str(probe),
                "--cachedir", str(probe), "--disable-sandbox", "--noconfirm"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if ok:
        print("    [+] No local repo cached, mirrors reachable -- building online "
              "(Arch packages fetched; our own from the local repo).")
        # Even here the local file:// repo must be added: our OWN packages (calamares,
        # librewolf) live on no mirror, and we OVERRIDE stock thunar with our file_manager
        # package (provides/replaces thunar). append_local_repo lists our repo BEFORE
        # [core]/[extra] so pacman prefers ours for any name we carry -- REQUIRED because
        # pacman picks by repo order, not by highest version, so a repo listed after [extra]
        # would have extra's stock thunar (and stock librewolf) shadow ours, and the ISO
        # would ship the unfixed binaries. Safe: our repo came from the same pinned ALA snapshot, so every
        # non-overridden name is byte-identical. SigLevel=Never on the local repo, so our
        # unsigned built packages need no .sig. This is the ONLY branch that still contacts
        # the network, and only because nothing is cached yet.
        conf = pacman.append_local_repo(conf, str(localrepo))
        emit.write_text(W / "pacman.conf", conf)
    else:
        sys.stderr.write(
            f"    [!] Mirrors unreachable and no local repo cached at {localrepo} --\n"
            "        run once online to populate the cache, then offline rebuilds work.\n"
        )
    subprocess.run(["rm", "-rf", str(probe)], check=False)


def _run_mkarchiso(sudo, W: Path, bar: ProgressBar, reclaim_after, iso_name: str = "azzio-headed") -> Path:
    # temp dir cleanup (matches the old "Cleaning up temp directory" step)
    subprocess.run(["rm", "-rf", str(W / ".temp")], check=False)
    # Reset the mkarchiso work tree BEFORE every pass. This is load-bearing for the
    # two-variant build: mkarchiso guards each build step with a `_run_once` sentinel
    # file (work/base.<fn>, work/iso.<fn>) and REFUSES to remove a pre-existing work
    # dir. If the sshd pass reused the base pass's work/, every step -- airootfs build,
    # squashfs, and the final ISO write -- would be skipped as "already done", and
    # azzio-sshd-*.iso would never be written (mkarchiso even reuses the base ISO's
    # name slot). Wiping work/ first makes each variant a genuine fresh mkarchiso pass.
    # Unmount any proc/sys/dev/run mkarchiso bind-mounted under the old airootfs before
    # rm, or rm -rf would recurse into live mounts. The base pass's rm is a near-no-op
    # (step 1 already reset W); the sshd pass's rm clears the base pass's sentinels.
    _unmount_worktree(sudo)
    subprocess.run(sudo + ["rm", "-rf", str(W / "work")], check=False)
    env = dict(os.environ)
    # NOTE: mkarchiso does NOT read a MKSQUASHFS_OPTIONS env var (it appears nowhere in
    # /usr/bin/mkarchiso). The old `env["MKSQUASHFS_OPTIONS"] = "-processors 4"` here was
    # therefore INERT -- mksquashfs fell back to its default of "all processors" and pinned
    # every core (the "compile took over all CPUs" bug). mkarchiso only forwards the
    # profiledef's `airootfs_image_tool_options` array straight to mksquashfs, so the real
    # `-processors <cap>` now lives THERE (profile.profiledef_sh / mkazzioiso.profiledef_sh),
    # scaled to the shared makepkg.build_jobs cap alongside the zstd bootstrap-tarball cap.
    # Binary pipe on purpose: _drive_mkarchiso_progress wraps it in a TextIOWrapper
    # with newline="" so it can split on BOTH \r and \n (pacman redraws with \r).
    # text=True here would hand us a pre-decoded stream that TextIOWrapper rejects.
    # start_new_session=True puts mkarchiso (and its pacstrap children) in their OWN
    # process group so a Ctrl-C can group-kill THEM without hitting our shell.
    global _ACTIVE_CHILD_PGID
    # stdin from /dev/null: pacstrap under mkarchiso hits the `xorg` package group and
    # prints "Enter a selection (default=all):" on stdin. With no input it stalls for a
    # minute before defaulting; feeding EOF makes it take default=all immediately
    # instead of hanging.
    proc = subprocess.Popen(
        sudo + ["mkarchiso", "-v", "-w", str(W / "work"), "-o", str(paths.BUILDDIR), str(W)],
        env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    _ACTIVE_CHILD_PGID = proc.pid  # PID == PGID for a session leader
    try:
        _drive_mkarchiso_progress(proc, bar)
        rc = proc.wait()
    finally:
        _ACTIVE_CHILD_PGID = 0
    bar.sub_done()
    # immediate reclaim: unmount, then hand the work tree back while sudo is fresh.
    _unmount_worktree(sudo)
    reclaim_after()
    if rc != 0:
        raise SystemExit(f"[x] mkarchiso failed (exit {rc})")
    # Select the ISO THIS build produced. output/ may hold BOTH variants
    # (azzio-headed-*.iso AND azzio-headed-ssh-*.iso) when both have been built, so we
    # must not just take the first *.iso. mkarchiso names artifacts <iso_name>-<version>-<arch>
    # where <version> is YYYY.MM.DD (always starts with a DIGIT). Anchoring the glob
    # with a digit right after "{iso_name}-" makes the BASE selection exact: "azzio-headed-"
    # followed by a digit matches azzio-headed-2026...iso but NOT azzio-headed-ssh-...iso
    # ("s" is not a digit) -- so the base pass can never accidentally pick up the sshd ISO,
    # regardless of build order or mtimes.
    isos = sorted(paths.BUILDDIR.glob(f"{iso_name}-[0-9]*.iso"))
    if not isos:
        # Fall back to any .iso so a naming surprise still surfaces the artifact.
        isos = sorted(paths.BUILDDIR.glob("*.iso"))
    if not isos:
        raise SystemExit("[x] ISO build failed: no .iso found in output/")
    # Newest matching ISO (this run's), in case an older same-variant ISO lingers.
    return max(isos, key=lambda p: p.stat().st_mtime)


# pacman phase -> (base, span) sub-band within 20..820 for live mkarchiso progress.
_PACMAN_BANDS = {
    "checking keys in keyring": (20, 90),
    "checking package integrity": (110, 70),
    "loading package files": (180, 20),
    "checking for file conflicts": (200, 20),
    "checking available disk space": (220, 20),
    "installing": (240, 580),
    "upgrading": (240, 580),
    "reinstalling": (240, 580),
    "downgrading": (240, 580),
}


def _drive_mkarchiso_progress(proc, bar: ProgressBar) -> None:
    """Parse mkarchiso/pacstrap live output and drive the bar. pacman redraws its
    progress with carriage returns (not newlines), so we split on BOTH \\r and \\n
    to see each (N/M) frame live.

    Each line goes out two ways in ONE call via the stdout tee's write_split: a
    width-CLIPPED copy to the terminal (so a long line does not wrap and desync the
    pinned bar's scroll region) and the FULL untruncated line to compile-full.log. A prior
    change wrote the clipped copy through plain stdout.write, which fed the SAME
    truncated text to the log too -- silently cutting the tail off every wide
    mkarchiso line in compile-full.log. write_split keeps the two independent so the log is
    complete while the terminal stays clip-safe."""
    import io
    import re

    frame = re.compile(
        r"\(\s*(\d+)/(\d+)\)\s+(" + "|".join(re.escape(k) for k in _PACMAN_BANDS) + r")"
    )
    inpac = False
    reader = io.TextIOWrapper(proc.stdout, encoding="utf-8", errors="replace", newline="")
    buf = ""

    # Heartbeat: pacman/mksquashfs suppress their (N/M) progress frames when their
    # output is not a TTY (it is a pipe here), so whole phases -- pacstrap install and
    # the minute-long SquashFS pack -- produce many log lines but no parseable frame,
    # and the bar froze between milestone jumps. Between two milestones we creep toward
    # (but never reach) the next milestone, one notch per output line, so the bar keeps
    # visibly moving even with no frames. `hb` holds (floor, ceil) for the live phase.
    hb = {"floor": 0, "ceil": 0, "at": 0}

    def creep() -> None:
        # asymptotic: close ~1/16 of the remaining gap to the ceiling per line.
        room = hb["ceil"] - max(hb["at"], hb["floor"])
        if room > 0:
            hb["at"] = max(hb["at"], hb["floor"]) + max(1, room // 16)
            bar.sub(hb["at"])

    def phase_span(floor: int, ceil: int) -> None:
        hb["floor"], hb["ceil"], hb["at"] = floor, ceil, floor

    def emit_line(line: str) -> None:
        nonlocal inpac
        if not line:
            return
        # Terminal gets the width-clipped line (no wrap -> the pinned bar stays put);
        # compile-full.log gets the FULL line. write_split does both in one write via the tee.
        # If stdout is not the tee (e.g. logging not installed), fall back to a plain
        # clipped write so the terminal still behaves.
        writer = getattr(sys.stdout, "write_split", None)
        if writer is not None:
            writer(bar._clip(line) + "\n", line + "\n")
        else:
            sys.stdout.write(bar._clip(line) + "\n")
        if "Installing packages to" in line:
            inpac = True
            bar.sub(20)
            bar.phase("pacstrap: installing packages into airootfs")
            phase_span(20, 810)   # creep across the install phase, stop short of 820
        elif "Done! Packages installed" in line:
            inpac = False
            bar.sub(820)
            bar.phase("pacstrap done, running customize hooks")
            phase_span(840, 930)  # next visible work is SquashFS; creep toward it
        elif "Creating SquashFS image" in line:
            inpac = False
            bar.sub(840)
            bar.phase("mksquashfs: compressing root filesystem (slow)")
            phase_span(840, 925)  # the long silent pack: creep so the minute animates
        elif "Creating checksum file" in line:
            bar.sub(930)
            bar.phase("writing SquashFS checksum")
            phase_span(930, 958)
        elif "Creating ISO image" in line:
            bar.sub(960)
            bar.phase("xorriso: writing bootable ISO image")
            phase_span(960, 995)
        elif inpac and (m := frame.search(line)):
            n, mm, ph = int(m.group(1)), int(m.group(2)), m.group(3)
            base, span = _PACMAN_BANDS[ph]
            if mm > 0:
                bar.sub(base + n * span // mm)
        else:
            # no milestone, no frame -- keep the bar alive within the current phase.
            creep()

    while True:
        ch = reader.read(1)
        if not ch:
            break
        if ch in ("\n", "\r"):
            emit_line(buf)
            buf = ""
        else:
            buf += ch
    emit_line(buf)


# --- driver / entry point --------------------------------------------------
# Folded in from the old compiler.py: `python3 -m compiler` runs main() below. The
# thin compile.sh shim sets up the PTY + primes sudo, then hands off here; this
# section owns the offline decision, the sudo keepalive + ownership reclaim, the
# progress bar, the SIGINT/SIGTERM teardown, and the final ISO report.


def cache_is_complete() -> bool:
    """The cache-first verdict: a COMPLETE cache => build with zero server contact.
    Complete = local repo index symlink + at least one indexed pkg + synced DBs +
    OUR OWN built packages (calamares, librewolf) actually present.
    FORCE_ONLINE=1 overrides (re-fetch without wiping).

    The own-packages clause is load-bearing: a cache can hold all 800+ downloaded
    Arch packages, a valid index, and synced DBs yet still LACK calamares/librewolf
    (they are compiled by the makepkg stage, not downloaded -- so a fresh cache, or
    one warmed by an earlier run that died before step 14, never has them). Without
    this clause cache_is_complete() returned True, the build took the OFFLINE path,
    and makepkg.build_own_packages then refused offline because the packages it was
    supposed to produce were absent -- a permanent deadlock (offline can't build
    them; nothing ever downgrades to online to build them). Treating their absence
    as an incomplete cache makes the build go ONLINE, compile them, drop them into
    cache/pkgs/repo/, and be genuinely offline-complete on the next run."""
    if os.environ.get("FORCE_ONLINE", "0") == "1":
        return False
    if not paths.LOCALREPO_INDEX.exists():
        return False
    if not any(paths.PKG_REPO.glob("*.pkg.tar.zst")):
        return False
    if not paths.PKG_SYNC_DB.is_dir() or not any(paths.PKG_SYNC_DB.glob("*.db")):
        return False
    # Own-packages clause, recipe-AWARE: the offline repo must hold a package for
    # every own package (calamares, librewolf, file_manager) AND that package must have
    # been built from the CURRENT recipe. _repo_is_current pairs the existence check
    # with a recipe-fingerprint match, so EDITING a recipe -- e.g. adding the networkq
    # source patch to the calamares PKGBUILD -- demotes this run to online and rebuilds
    # the package, instead of silently reusing the stale binary (which shipped a
    # calamares that listed `networkq` in settings.conf but had no such module, so it
    # aborted at startup with "networkq@networkq could not be loaded"). Same spirit as
    # the manifest-coverage clause below. produced_names is tier-independent, and
    # _repo_is_current derives the fingerprint set from recipe_dirs the same way, so
    # full_compile=False is correct regardless of the eventual --full-compile flag
    # (the recipe FILES are identical across tiers for calamares/file_manager; librewolf's
    # differ, but a librewolf-recipe change correctly forces a rebuild either way).
    if not makepkg._repo_is_current(paths.PKG_REPO, full_compile=False):
        return False
    # Manifest-coverage clause (same spirit as the own-packages clause above): the
    # offline repo must hold a package file for EVERY downloadable package the
    # manifest names. A cache can pass all the structural markers yet still lack a
    # package that was ADDED to packages.x86_64 after the cache was last warmed
    # (e.g. xorg-xset) -- and an offline pacstrap then dies with "target not found:
    # <pkg>". Treating any such gap as incomplete forces this run ONLINE to fetch
    # exactly the missing packages; the next run is then genuinely offline-complete.
    # The exclusion set (our own built packages) is tier-independent, so
    # full_compile=False matches the produced_names call above.
    if downloader.missing_from_repo(paths.PKG_REPO, full_compile=False):
        return False
    # NOTE: a COMPLETE cache makes the build go offline for BOTH tiers, but the two
    # tiers then diverge inside makepkg.build_own_packages: the default tier trusts
    # the cached own packages and SKIPS makepkg, while a --full-compile offline rerun
    # RE-COMPILES librewolf from the source fetched into the makepkg scratch by the
    # prior online run (no network). So "offline" here means "no server contact",
    # not "no compile" -- the recompile stays entirely local.
    return True


class SudoKeepalive:
    """Keep the sudo timestamp warm across the long build so the trap/immediate
    `sudo -n chown` still works past sudo's short timeout. No-op when root."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if paths.is_root():
            return

        def loop() -> None:
            while not self._stop.wait(60):
                if subprocess.run(["sudo", "-n", "-v"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
                    return

        self._thread = threading.Thread(target=loop, name="sudo-keepalive", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def _stale_cache_notice(offline: bool) -> None:
    # When we are going ONLINE despite an otherwise-warm cache, say WHY: name the
    # manifest packages the offline repo is missing (the coverage clause in
    # cache_is_complete demoted this run to online precisely because these have no
    # file in cache/pkgs/repo/). Without this, adding a package to packages.x86_64
    # would silently trigger a fresh download with no explanation. Best-effort:
    # never let diagnostics abort the build.
    if offline:
        return
    try:
        if not any(paths.PKG_REPO.glob("*.pkg.tar.zst")):
            return  # no cache at all -- a normal cold build, nothing "stale".
        missing = downloader.missing_from_repo(paths.PKG_REPO, full_compile=False)
        if missing:
            shown = ", ".join(missing[:12]) + (", ..." if len(missing) > 12 else "")
            sys.stderr.write(
                f"[!] {len(missing)} manifest package(s) are not in the offline cache "
                f"and will be downloaded: {shown}\n"
                "    (packages.x86_64 gained entries since the cache was last warmed.)\n"
            )
    except OSError:
        pass


def main() -> int:
    paths.LOGDIR.mkdir(parents=True, exist_ok=True)

    # --estimate* (six variants): predict how long a build would take on this
    # machine -- COMPUTE (compiling on this CPU/RAM) and/or NETWORK (downloading
    # the components over this connection) -- then exit. Pure query: no workspace
    # reset, no sudo, no build, and NOT routed through the build-log tee (it is a
    # query, not a build, so its output belongs on the terminal, not logs/compile-full.log
    # -- this branch returns before logstream.install() below). The network modes
    # DO open a client socket for a few-second bandwidth probe, but that needs no
    # privilege and writes no build file. compile.sh routes any --estimate* arg
    # here without a PTY or sudo prime.
    if estimate.parse_estimate_flag(sys.argv[1:]) is not None:
        return estimate.run(sys.argv[1:])

    # HARD STOP: `--ssh` present but with no password. The flag demands a string; a bare
    # `--ssh` / `--ssh=` must ABORT with an explanation rather than silently building the
    # base ISO only (the reported bug). Do this BEFORE any workspace/cache setup so it is a
    # clean, side-effect-free early exit.
    ssh_flag_error = check_ssh_flag(sys.argv[1:])
    if ssh_flag_error:
        sys.stderr.write("[x] " + ssh_flag_error + "\n")
        return 2

    paths.CACHEDIR.mkdir(parents=True, exist_ok=True)

    # Python owns compile-full.log from here on: route stdout/stderr through a tee that
    # mirrors every print/stderr line into the log in real time. `script` in
    # compile.sh now only provides the PTY (its capture goes to /dev/null), so the
    # progress bar -- which paints to the RAW terminal only -- never reaches the log.
    logstream.install()

    # --full-compile: build Azzio's own packages entirely from source (incl. the
    # multi-hour LibreWolf/Firefox compile) rather than repackaging the verified
    # upstream LibreWolf tarball. Default is the fast repackage tier.
    full_compile = "--full-compile" in sys.argv[1:]
    if full_compile:
        print("[*] --full-compile: Azzio's own packages will be built ENTIRELY from source.")
        print("    This includes a LibreWolf/Firefox compile that can take 1.5-3+ hours.")

    # --use-each-cpu: lift the hardcoded 75% compile cap and use EVERY logical CPU
    # (one job per core). Recorded process-wide (and in AZZIO_USE_EACH_CPU) so
    # makepkg.build_jobs -- the ONE place the job count is decided, read by the
    # compilers AND by mkarchiso's squashfs/zstd thread cap via profile.profiledef_sh
    # -- lifts the cap everywhere at once. Default (flag absent) keeps the 75% cap so
    # the machine stays usable during a build.
    use_each_cpu = "--use-each-cpu" in sys.argv[1:]
    makepkg.set_use_each_cpu(use_each_cpu)
    if use_each_cpu:
        print("[*] --use-each-cpu: the compile will use EVERY CPU core (no 75% cap). "
              "The machine may be unresponsive during the build.")
    else:
        print(f"[*] Compile capped at 75% of CPU cores ({makepkg.build_jobs()} of "
              f"{makepkg._cpu_count()} jobs). Pass --use-each-cpu to use every core.")

    # Each run builds exactly ONE ISO. The base/headed ISO is the default. The
    # `azzio-headed-ssh` ISO is built INDIVIDUALLY (in place of the base ISO, not on top
    # of it) ONLY when `--ssh="<PASSWORD>"` supplies a non-empty string (DECISION 2 -- no
    # default password is ever shipped; the ssh variant's credential comes from the operator
    # at build time). The password is hashed HERE (sha-512 crypt) and threaded into run();
    # the plaintext never leaves this process. An empty/missing --ssh -> the base ISO.
    ssh_password = parse_ssh_flag(sys.argv[1:])
    ssh_hash = ssh_password_hash(ssh_password) if ssh_password else None
    if ssh_hash:
        print("[*] --ssh supplied: building ONLY the opt-in `azzio-headed-ssh` ISO "
              "(the base ISO is NOT built)")
        print("    (the ssh medium sets `main`'s password from --ssh, enables sshd, and "
              "opens port 22 at boot).")
    else:
        print("[*] Building ONLY the base `azzio-headed` ISO (ssh disabled). Pass "
              "--ssh=\"<PASSWORD>\" to build the opt-in `azzio-headed-ssh` ISO instead.")

    offline = cache_is_complete()
    _stale_cache_notice(offline)

    bar = ProgressBar(STEP_WEIGHTS)
    own = Ownership(_sudo())
    keep = SudoKeepalive()

    # SAFEGUARD 1: startup reclaim (recovers a tree left by a SIGKILL'd prior run).
    own.reclaim_full()
    keep.start()
    own.start_continuous()

    _torn_down = threading.Event()

    def teardown() -> None:
        # Re-entrancy guard: signal + normal/error exit paths must not double-run
        # the chown/unmount (mirrors the old _HANDED_BACK / _KILLED flags).
        if _torn_down.is_set():
            return
        _torn_down.set()
        keep.stop()
        own.stop_continuous()
        kill_active_child(_sudo())  # kill mkarchiso's OWN group, not ours
        _unmount_worktree(_sudo())
        bar.cleanup()
        own.reclaim_full()  # SAFEGUARD final

    def on_signal(signum, _frame) -> None:
        # Kill ONLY the mkarchiso child's process group (it is spawned in its own
        # session, see _run_mkarchiso), never our own group -- signalling our
        # own group would re-enter this handler and could interrupt teardown
        # mid-chown, leaving cache/build root-owned (the exact old-bash hazard).
        teardown()
        os._exit(130)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    bar.init()
    try:
        isos = run(bar, offline, full_compile=full_compile,
                   ssh_password_hash=ssh_hash,
                   reclaim_after_mkarchiso=own.reclaim_full)
    except SystemExit as e:
        teardown()
        msg = str(e)
        if msg and not msg.isdigit():
            sys.stderr.write(msg + "\n")
        return e.code if isinstance(e.code, int) else 1
    except Exception as e:
        teardown()
        sys.stderr.write(f"[x] Build failed: {e}\n")
        return 1

    bar.subfrac = 1000
    bar.finalize()
    # Report each ISO actually built with its size. Exactly one ISO is built per run --
    # the base ISO without --ssh, or the ssh ISO with it (_variants_for). The count/noun
    # are still derived from len(isos) so the report stays honest if the selected set ever
    # changes.
    noun = "ISO" if len(isos) == 1 else "ISOs"
    lines = [f"\n[ {bar.total_steps}/{bar.total_steps} ] [OK] {len(isos)} {noun} built successfully:"]
    for iso in isos:
        iso_size = subprocess.run(["du", "-h", str(iso)], capture_output=True, text=True).stdout.split("\t")[0]
        iso_path = f"output/{iso.name}" if paths.in_docker() else str(iso)
        entry = f"           - {iso_path}"
        if iso_size:
            entry += f" ({iso_size})"
        lines.append(entry)
    if paths.in_docker():
        lines.append("           The ISOs are in output/ on your host (NOT build/output/).")
    report = "\n".join(lines)
    print(report)
    with paths.STEPS_LOG.open("a") as f:
        f.write(report + "\n")

    teardown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
