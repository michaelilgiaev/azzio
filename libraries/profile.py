"""profiledef.sh -- the archiso profile definition mkarchiso sources.

mkarchiso REQUIRES this to be a bash script it can `source` (it reads iso_name,
bootmodes, file_permissions, etc. as shell variables), so we can't make it Python.
Instead we AUTHOR it in Python (the values live in a dict/list here) and emit the
bash file. That keeps the source of truth in Python like everything else.

Notably this carries the zstd squashfs workaround for the sporadic
"xz uncompress failed with error code 9" and the file_permissions map that locks
down shadow/gshadow/sudoers in the ISO.
"""

from __future__ import annotations

# ISO base names per build variant. mkarchiso names the artifact
# <iso_name>-<version>-<arch>.iso, so these drive the two output filenames:
#   base -> azzio-headed-<ver>-x86_64.iso      (the normal live/install medium)
#   sshd -> azzio-headed-ssh-<ver>-x86_64.iso  (same, but ssh is ENABLED and `main`
#                                                has the operator's --ssh password)
#
# "headed" is the product LINE and "-ssh" its sub-flavour. This leaves room for a future
# "headless" line (azzio-headless) without disturbing the base/sshd variant KEYS the build
# branches on -- those stay `base`/`sshd`; only the artifact NAME carries the product line.
# The two ISOs are separated in output/ by the digit-anchored glob "{iso_name}-[0-9]*.iso":
# "azzio-headed-2026..." matches base, "azzio-headed-ssh-..." does not (the char after
# "azzio-headed-" is 's', not a digit), exactly as before the rename.
ISO_NAME = "azzio-headed"
ISO_NAME_SSHD = "azzio-headed-ssh"

# The set of recognized build variants -> iso_name. compiler.run loops over the
# runtime-selected variant (compiler._variants_for), calling iso_name_for to name the ISO.
# Exactly one is built per run: the base ISO by default, or the sshd ISO INDIVIDUALLY
# (in place of base, not alongside it) when --ssh is supplied.
ISO_NAMES = {
    "base": ISO_NAME,
    "sshd": ISO_NAME_SSHD,
}

ISO_PUBLISHER = "michaelilgiaev <https://github.com/michaelilgiaev/azzio>"
ISO_APPLICATION = "Azzio Installer/Azzio Linux Live/Rescue DVD"
INSTALL_DIR = "arch"


def iso_name_for(variant: str = "base") -> str:
    """The mkarchiso iso_name for a build variant (unknown -> base 'azzio-headed')."""
    return ISO_NAMES.get(variant, ISO_NAME)

BOOTMODES = (
    "bios.syslinux.mbr",
    "bios.syslinux.eltorito",
    "uefi-ia32.systemd-boot.esp",
    "uefi-x64.systemd-boot.esp",
    "uefi-ia32.systemd-boot.eltorito",
    "uefi-x64.systemd-boot.eltorito",
)

# path -> "owner:group:octal" baked into the squashfs by mkarchiso.
FILE_PERMISSIONS = {
    "/etc/shadow": "0:0:400",
    "/etc/gshadow": "0:0:400",
    # sudoers drop-ins: archiso normalizes overlay modes, so pin these to 0440
    # (the sudo convention) rather than letting them ship 0644. compiler.py emits
    # them 0440 but that mode is lost in the squashfs without an entry here.
    "/etc/sudoers.d/00-main": "0:0:440",
    "/etc/sudoers.d/00-rootpw": "0:0:440",
    "/root": "0:0:750",
    "/root/azzio": "0:0:750",
    "/root/.automated_script.sh": "0:0:755",
    "/root/.gnupg": "0:0:700",
    "/usr/local/bin/choose-mirror": "0:0:755",
    "/usr/local/bin/Installation_guide": "0:0:755",
    "/usr/local/bin/livecd-sound": "0:0:755",
    # The Calamares launcher the OpenBox autostart runs on live login. archiso
    # NORMALIZES overlay file modes when it packs the squashfs -- only paths listed
    # here keep an explicit mode. Without this entry the wrapper ships 0644
    # (non-executable), so the autostart's `[ -x ... ]` guard skips it and Calamares
    # never auto-launches. THIS is what breaks the live installer.
    "/usr/local/bin/azzio-install": "0:0:755",
    "/usr/local/bin/azzio": "0:0:755",
    # The Azzio application-menu launcher (run by the Super key via OpenBox's rc.xml
    # keybind). SAME archiso mode-normalization as
    # azzio-install above: application_menu.PLAN emits it 0755, but the squashfs ships
    # it 0644 (non-executable) unless pinned here -- and then the Super key runs a
    # non-executable file and the menu never opens.
    "/usr/local/bin/azzio-application-menu": "0:0:755",
    # The Azzio window-switcher launcher (run by OpenBox's A-Tab/A-S-Tab
    # <action name="Execute"> -- OUR replacement for the built-in NextWindow list). SAME
    # archiso mode-normalization as azzio-application-menu above: window_switcher.PLAN emits
    # it 0755, but the squashfs ships it 0644 (non-executable) unless pinned here -- and then
    # OpenBox's /bin/sh -c on the launcher fails with "Permission denied", which OpenBox
    # surfaces as an error popup INSTEAD of the alt-tab overlay (the reported bug). Verified
    # 0644 on the built ISO.
    "/usr/local/bin/azzio-window-switcher": "0:0:755",
    # The Azzio timedate launcher (run by azzio-timedate.service, which ExecStart's it
    # to serve the Flask Time + Calendar home page at localhost:49154). SAME archiso mode-
    # normalization as azzio-install above: timedate.PLAN emits it 0755, but the squashfs
    # ships it 0644 (non-executable) unless pinned here -- and then systemd fails the unit
    # with status=203/EXEC (Permission denied) and the home page never listens, so a new
    # tab / the browser home page lands on a dead port. Verified on the built ISO.
    "/usr/local/bin/azzio-timedate": "0:0:755",
    # The Azzio `passwords` launcher (the encrypted terminal password manager the user
    # runs by typing `passwords`). SAME archiso mode-normalization as azzio-install above:
    # packages/passwords/packaging.PLAN emits it 0755, but the squashfs ships it 0644
    # (non-executable) unless pinned here -- and then typing `passwords` fails with
    # "Permission denied" (the shell needs the exec bit to run it) on BOTH the live ISO and
    # the installed system. Root-owned on PATH, so every user gets the command.
    "/usr/local/bin/passwords": "0:0:755",
    # The Azzio `backup`/`unpack` launchers (the home-directory backup the user runs by
    # typing `backup`, and the restore command `unpack`). SAME archiso mode-normalization as
    # azzio-install/passwords above: packages/backup/packaging.emit_plan() emits both 0755,
    # but the squashfs ships them 0644 (non-executable) unless pinned here -- and then typing
    # `backup` (or `unpack`) fails with "command not found"/"Permission denied" even by full
    # path (this was the last build's bug #1). Root-owned on PATH, so every user gets them.
    "/usr/local/bin/backup": "0:0:755",
    "/usr/local/bin/unpack": "0:0:755",
    # The Azzio `hypervisor` launcher (the per-directory QEMU/KVM VM runner the user runs
    # by typing `hypervisor`). SAME archiso mode-normalization as passwords/backup above:
    # packages/hypervisor/packaging.emit_plan() emits it 0755, but the squashfs ships it 0644
    # (non-executable) unless pinned here -- and then typing `hypervisor` fails with
    # "Permission denied". Root-owned on PATH, so every user gets the command.
    "/usr/local/bin/hypervisor": "0:0:755",
    # The COMPILED application-menu daemon binary (built by application_menu.build_daemon
    # and started from the OpenBox autostart). Same archiso mode-normalization: it is
    # installed 0755, but the squashfs would ship it 0644 unless pinned -- and the
    # autostart's `[ -x ... ]` guard would then skip it, so the menu is never pre-built
    # and the first Super press does nothing / starts nothing.
    "/usr/local/lib/azzio-application-menu/azzio-application-menu-daemon": "0:0:755",
    # The COMPILED window-switcher daemon binary (built by window_switcher.build_daemon and
    # started from the OpenBox autostart, which keeps the alt-tab overlay hidden so the first
    # Alt+Tab is instant). Same archiso mode-normalization as the menu daemon above: it is
    # installed 0755, but the squashfs would ship it 0644 unless pinned -- and then the
    # autostart's `[ -x ... ]` guard skips it, so the daemon is never pre-built and Alt+Tab
    # starts nothing.
    "/usr/local/lib/azzio-window-switcher/azzio-window-switcher-daemon": "0:0:755",
    # The COMPILED bare-`azzio` TERMINAL UI binary (built by terminal_user_interface_build.build_terminal_user_interface from the
    # azzio package's C sources and EXEC'd by the `azzio` command line interface for the no-argument case).
    # Same archiso mode-normalization
    # as the menu daemon above: it is installed 0755, but the squashfs would ship it 0644
    # unless pinned -- and then the `azzio` launcher's os.access(..., X_OK) guard fails and
    # bare `azzio` silently falls back to the pointer message instead of opening the UI.
    "/usr/local/lib/azzio/azzio": "0:0:755",
    # The media OSD indicator (/usr/local/lib/azzio/azzio-osd), the bottom-middle cyan
    # volume/brightness bar `azzio volume/brightness` launches. A COMPILED C binary now (on_screen_display.c),
    # built + installed by terminal_user_interface_build.build_osd() like the terminal UI binary.
    # Same archiso mode-normalization as that binary: the build installs it 0755, but the squashfs
    # would ship it 0644 unless pinned -- and then media.py's os.access(..., X_OK) guard fails and
    # the FN keys change the volume/brightness with NO on-screen bar.
    "/usr/local/lib/azzio/azzio-osd": "0:0:755",
    # The live file-manager sidebar sync helper (/usr/local/lib/azzio/azzio-sidebar-sync), which
    # regenerates ~/.config/gtk-3.0/bookmarks from the live home contents and (with --watch)
    # keeps the file manager's Places pane in sync. SAME archiso mode-normalization as the binaries above:
    # live_sidebar.emit_plan() emits it 0755, but the squashfs ships it 0644 unless pinned here --
    # and then the OpenBox autostart's `[ -x '/usr/local/lib/azzio/azzio-sidebar-sync' ]` guard
    # FAILS, so the --watch daemon never launches and Places never updates when a folder is added
    # or removed (the reported "Places does not update" bug: the file-monitor theory was sound,
    # but the watcher that rewrites the file was never even running because it shipped non-exec).
    "/usr/local/lib/azzio/azzio-sidebar-sync": "0:0:755",
    # The OpenBox session autostart (~/.config/openbox/autostart). openbox-session runs
    # it via /bin/sh, but it carries a shebang and openbox.PLAN emits it 0755, so pin it
    # executable here too (archiso would otherwise normalize it to 0644). Pin both the
    # live-user copy (1000:998) and the /etc/skel copy (root-owned).
    "/home/main/.config/openbox/autostart": "1000:998:755",
    "/etc/skel/.config/openbox/autostart": "0:0:755",
    # The live-session Desktop "Azzio Linux Installer" launcher. Same archiso mode-
    # normalization as azzio-install above: compiler.py emits it 0755, but the squashfs
    # ships it 0644 unless pinned here. Shipping it EXECUTABLE means a file manager that
    # honours the exec bit launches it on double-click without a "not trusted" prompt.
    # Both the live-user copy (uid 1000:998) and the /etc/skel copy (root-owned) are
    # pinned.
    "/home/main/Desktop/azzio-install.desktop": "1000:998:755",
    "/etc/skel/Desktop/azzio-install.desktop": "0:0:755",
    # Vendored ckbcomp (libraries/packages/calamares/ckbcomp.py), a Python 3 port of the
    # upstream Perl ckbcomp. Same archiso mode-normalization as azzio-install above: without
    # an explicit 0755 here it ships 0644, Calamares' `QProcess::start("ckbcomp")`
    # cannot execute it, and the keyboard-page preview stays BLANK ("ckbcomp not
    # found, keyboard preview disabled"). This entry keeps the exec bit so the preview
    # renders key legends.
    "/usr/bin/ckbcomp": "0:0:755",
    "/etc/sudoers.d/00-secure-path": "0:0:440",
    "/root/azzio/setup-locale.sh": "0:0:755",
    "/etc/systemd/system/locale-setup.service": "0:0:644",
    "/root/azzio/setup-pkgs.sh": "0:0:755",
    "/etc/systemd/system/pkgs-setup.service": "0:0:644",
}


def profiledef_sh(variant: str = "base", threads: int | None = None) -> str:
    # `threads` caps the zstd bootstrap-tarball compression below. Left as -T0 it
    # would grab EVERY logical core and freeze the machine for the whole compress
    # pass; we instead pin it to the ONE shared, headroom-leaving cap so the same
    # rule that governs the compilers (makepkg.build_jobs) governs mkarchiso too.
    # Imported lazily so this module keeps no import-time dependency on the makepkg
    # build stage (and callers can still inject an explicit count for tests).
    if threads is None:
        import makepkg
        threads = makepkg.build_jobs()
    bootmodes = " ".join(f"'{m}'" for m in BOOTMODES)
    perms = "\n".join(f'  ["{p}"]="{v}"' for p, v in FILE_PERMISSIONS.items())
    iso_name = iso_name_for(variant)
    return f"""\
#!/usr/bin/env bash
# shellcheck disable=SC2034
#
# Generated by profile.py -- edit the Python, not this file.

iso_name="{iso_name}"
iso_label="AZZIO_$(date --date="@${{SOURCE_DATE_EPOCH:-$(date +%s)}}" +%Y%m)"
iso_publisher="{ISO_PUBLISHER}"
iso_application="{ISO_APPLICATION}"
iso_version="$(date --date="@${{SOURCE_DATE_EPOCH:-$(date +%s)}}" +%Y.%m.%d)"
install_dir="{INSTALL_DIR}"
buildmodes=('iso')
bootmodes=({bootmodes})
arch="x86_64"
cow_spacesize="4G"
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"

### This line fixes an odd bug that appeared out of nowhere
### \"\"\"FATAL ERROR: xz uncompress failed with error code 9\"\"\"
airootfs_image_tool_options=('-comp' 'zstd' '-Xcompression-level' '15' '-processors' '{threads}')
###

bootstrap_tarball_compression=('zstd' '-c' '-T{threads}' '--long' '-19')
file_permissions=(
{perms}
)
"""
