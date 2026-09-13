"""installer -- the on-disk install pipeline scripts.

These generators emit the real .sh/.conf/.service files the ISO ships. They are
pure string producers, but the strings are load-bearing in three brittle ways:

  1. Cross-file token contracts. `first_boot_conf()` writes the literal line
     `First_Boot=TRUE`; `first_boot_sh()` greps for `^First_Boot=TRUE` and
     `sed`s it to `First_Boot=FALSE`. If either side's spelling drifts, the
     first-boot-once mechanism silently never runs (or never disables itself)
     -- nothing in Python catches a mismatched grep token.

  2. Brace escaping. `chroot_setup_sh()` is an f-string, so every literal `{`/`}`
     that must survive into bash (the `find ... -exec chmod {} \\;` calls) is
     doubled as `{{`/`}}` in the source. A single missed doubling raises
     ValueError at import; a stray leftover `{{` ships broken bash. We assert the
     emitted text has singular braces and no `{{`/`}}` residue.

  3. Path / argv agreement. The UEFI vs BIOS grub-install target flags, the
     fdisk keystroke strings (`+1G` for the UEFI ESP, `+1M` for the BIOS boot
     partition), the nvme `p1`/`p2` vs plain `1`/`2` partition-suffix branches,
     and the first-boot service `ExecStart=` path all have to match the paths the
     installer copies files to. A wrong path fails only on real hardware.

Everything here is pure: no network, no subprocess, no filesystem writes. The one
seam we isolate is `_detect_and_apply_locale_block`, imported into the installer
module namespace, so monkeypatching `installer._detect_and_apply_locale_block`
lets us prove the locale block is spliced into `chroot_setup_sh()` at the right
spot without depending on the locale module's exact content.
"""

from __future__ import annotations

import installer


# --- every generator produces bash / configuration text ---------------------------

def test_each_generator_returns_bash():
    # A broken f-string (bad brace, missing interpolation) raises ValueError at
    # call time, and an accidental `return` of None ships an empty file. This
    # single sweep catches both across every public generator.
    shell_generators = (
        installer.installer_sh,
        installer.chroot_setup_sh,
        installer.setup_pkgs_sh,
        installer.first_boot_sh,
    )
    for gen in shell_generators:
        out = gen()
        assert isinstance(out, str) and out
        assert out.splitlines()[0] == "#!/bin/bash", gen.__name__


def test_conf_and_service_headers():
    conf = installer.first_boot_conf()
    service = installer.first_boot_service()
    assert isinstance(conf, str) and conf
    assert conf.splitlines()[0] == "# Set to TRUE to enable first boot shell script."
    assert isinstance(service, str) and service
    assert service.splitlines()[0] == "[Unit]"


# --- cross-file First_Boot token contract ----------------------------------

def test_first_boot_conf_token_is_a_full_line():
    # The conf carries the exact token the .sh side greps for. It must be a
    # standalone line so `grep -q '^First_Boot=TRUE'` anchors on it.
    conf = installer.first_boot_conf()
    assert "First_Boot=TRUE" in conf.splitlines()


def test_first_boot_sh_greps_and_flips_the_same_token():
    # The whole first-boot-once mechanism is this handshake: grep the TRUE token,
    # then sed it to FALSE so the second boot skips. Both anchored on ^.
    sh = installer.first_boot_sh()
    assert "grep -q '^First_Boot=TRUE'" in sh
    assert "sed -i 's/^First_Boot=TRUE/First_Boot=FALSE/'" in sh


# --- NO world-writable / blanket-executable home sweep (the security hole) ---
#
# The CLI installer USED to run, in chroot_setup_sh():
#     find /home/main -type f -exec chmod 666 {} \;
#     find /home/main -type d -exec chmod 777 {} \;
#     find /home/main -type f -exec chmod +x {} \;
# which made a CLI-installed $HOME world-writable (every dir 777, every file 666+x)
# -- a real LOCAL security hole (any user can write any other user's files) and it
# marked every regular file executable. Calamares does NOT do this: its unpackfs
# rsync preserves the live perms (dirs 755, files their real modes), and the
# identity rename uses usermod/mv which keep ownership. The sweep is DELETED, and
# these tests FAIL if it (or any equivalent world-writable chmod over /home) ever
# comes back -- this is the regression guard the handoff explicitly asked for.

def test_chroot_setup_has_no_world_writable_home_chmod():
    s = installer.chroot_setup_sh()
    # No blanket world-writable mode on any home path, in any form.
    assert "chmod 777" not in s, "world-writable dir chmod must never return to the installer"
    assert "chmod 666" not in s, "world-writable file chmod must never return to the installer"
    # No blanket "make every home file executable" sweep.
    assert "-type f -exec chmod +x" not in s, "blanket +x over home files must never return"
    # And specifically none of the old /home sweeps in their exact shape.
    for bad in (
        "find /home/main -type f -exec chmod 666 {} \\;",
        "find /home/main -type d -exec chmod 777 {} \\;",
        "find /home/main -type f -exec chmod +x {} \\;",
    ):
        assert bad not in s, f"the removed world-writable sweep line is back: {bad!r}"


def test_chroot_setup_no_blanket_chmod_over_any_home_path():
    # Defence beyond the literal old lines: reject ANY find-based blanket chmod that
    # targets a /home path (a renamed-login variant would be just as bad). Scan each
    # emitted line for the (find /home ... -exec chmod) shape.
    import re
    s = installer.chroot_setup_sh()
    for line in s.splitlines():
        if "find" in line and "/home" in line and "-exec chmod" in line:
            raise AssertionError(f"blanket chmod over a home path is forbidden: {line!r}")
    # A recursive world-open chown-then-chmod combo would also be a smell; there must
    # be no `chmod -R 777`/`chmod -R 666` anywhere either.
    assert not re.search(r"chmod\s+-R\s+0*7[0-7][0-7]", s)


def test_chroot_setup_has_no_leftover_double_braces():
    s = installer.chroot_setup_sh()
    assert "{{" not in s
    assert "}}" not in s


# --- PARITY: the CLI chroot reuses Calamares' shared post-clone fixups -------
#
# The whole point of the unification: the CLI install must not RE-DERIVE the
# post-clone fixups (mkinitcpio reset, installer-state cleanup) -- it must EMBED
# the exact commands Calamares' shellprocess emits, by calling the SAME shared
# producer functions. These tests fail if either path grows its own private copy,
# so a future edit to the shared function changes BOTH paths and cannot silently
# diverge them. That is the regression the handoff demanded a test for.

def test_chroot_setup_embeds_shared_mkinitcpio_reset_verbatim():
    from packages.calamares import calamares_shellprocess as csp
    s = installer.chroot_setup_sh()
    # The CLI chroot must contain the EXACT command block the shared function returns.
    assert csp._mkinitcpio_reset_command() in s, \
        "CLI chroot must embed the shared _csp._mkinitcpio_reset_command() verbatim"


def test_chroot_setup_embeds_shared_installer_cleanup_for_renamed_home():
    from packages.calamares import calamares_shellprocess as csp
    s = installer.chroot_setup_sh()
    # The CLI renames `main` to $az_login and moves the home first, so it must call
    # the shared cleanup with the CHOSEN login's home (not a hardcoded /home/main).
    assert csp.installer_cleanup_command("/home/$az_login") in s, \
        "CLI chroot must embed the shared installer_cleanup_command for /home/$az_login"


def test_calamares_and_cli_cleanup_come_from_the_same_function():
    # PARITY PROOF: Calamares' own cleanup and the CLI's cleanup are the SAME function
    # with different home args. Assert Calamares' literal-home variant equals the shared
    # producer applied to /home/main, so the two call sites cannot drift.
    from packages.calamares import calamares_shellprocess as csp
    assert csp._installer_cleanup_command() == csp.installer_cleanup_command("/home/main")


# --- chroot: the two archiso-clone fixups the CLI install shares with Calamares ----
#
# Because the CLI installer now CLONES the live archiso rootfs verbatim (like Calamares
# unpackfs), it inherits the SAME two live-only artifacts Calamares' shellprocess must undo,
# and the CLI chroot must undo them too or the installed system is (1) UNBOOTABLE (archiso
# mkinitcpio preset + empty /boot) and (2) re-launches the disk-erasing installer at every
# login (live OpenBox autostart). We reuse the Calamares single-source-of-truth constants.

def test_chroot_setup_resets_archiso_mkinitcpio_before_building():
    # The clone carries archiso's mkinitcpio state: /etc/mkinitcpio.d/linux.preset =
    # PRESETS=('archiso'), an archiso.conf HOOKS drop-in, and an EMPTY /boot (mkarchiso wipes
    # it; the kernel survives only under /usr/lib/modules/<kver>/vmlinuz). A plain `mkinitcpio
    # -P` on that state fails ("/boot/vmlinuz-linux must be readable") or builds an unbootable
    # archiso-hooked image. The chroot must, BEFORE mkinitcpio -P: reinstate the kernel from the
    # modules tree, install the STOCK linux preset, and drop archiso.conf.
    from packages.calamares import calamares_shellprocess as csp
    s = installer.chroot_setup_sh()
    # Kernel reinstated from the modules tree to /boot/vmlinuz-linux.
    assert "/usr/lib/modules" in s
    assert "-name vmlinuz" in s
    assert "/boot/vmlinuz-linux" in s
    # Stock preset content installed over the archiso one (single source of truth).
    assert "PRESETS=('default' 'fallback')" in s
    assert "/etc/mkinitcpio.d/linux.preset" in s
    # archiso conf.d drop-in removed.
    assert "/etc/mkinitcpio.conf.d/archiso.conf" in s
    # ALL of this must happen BEFORE the initramfs is generated, or it is useless. Anchor on the
    # actual `mkinitcpio -P` COMMAND (the last occurrence -- earlier ones are comment mentions).
    run_idx = s.rindex("\nmkinitcpio -P")
    assert s.index("PRESETS=('default' 'fallback')") < run_idx
    assert s.index("/etc/mkinitcpio.conf.d/archiso.conf") < run_idx


def test_chroot_setup_strips_live_only_installer_autostart():
    # The clone inherits the LIVE OpenBox autostart, whose live-only lines (a) re-launch the
    # Calamares installer at every login and (b) force a fixed us,il keyboard. Calamares
    # overwrites the autostart with the "installed" variant staged on the ISO and deletes the
    # installer launchers/wrapper; the CLI chroot must do the SAME. Reuse the staged-path and
    # installer-path constants so the two paths never drift.
    from packages.calamares import calamares_shellprocess as csp
    s = installer.chroot_setup_sh()
    # The staged "installed" autostart is copied over the inherited live one.
    assert csp.INSTALLED_AUTOSTART_SRC in s
    # The installer wrapper + menu entry are removed so the installed system never re-opens it.
    assert csp.INSTALLER_WRAPPER in s          # /usr/local/bin/azzio-install removed
    assert csp.INSTALLER_MENU_DESKTOP in s     # menu launcher removed


def test_chroot_setup_autostart_fixup_targets_the_renamed_home():
    # Unlike Calamares (which removes `main` and lets the users module recreate the account),
    # the CLI installer RENAMES `main` to the chosen login and moves /home/main -> /home/$login.
    # So the autostart overwrite must target the CHOSEN login's home, not a hardcoded
    # /home/main, or the fixup writes to a dead path and the live installer-relaunch survives.
    # The identity fragment exports $az_login; assert the autostart cleanup references it.
    s = installer.chroot_setup_sh()
    assert "/home/$az_login/.config/openbox/autostart" in s


# --- grub-install: both firmware branches present --------------------------

def test_grub_install_both_branches():
    # UEFI and BIOS installs take different grub-install targets. Both must be
    # present; a dropped branch bricks half the install base.
    s = installer.chroot_setup_sh()
    assert (
        "grub-install --target=x86_64-efi --bootloader-id=grub_uefi "
        "--recheck --efi-directory=/boot/EFI" in s
    )
    assert 'grub-install --target=i386-pc "$disk"' in s


# --- grub auto-boot first option (Task 4, shell-installer path) -------------

def test_chroot_setup_configures_grub_auto_boot():
    # The shell installer must set the same auto-boot-first-entry policy the
    # Calamares path does, BEFORE grub-mkconfig reads /etc/default/grub.
    s = installer.chroot_setup_sh()
    assert "set_grub_default GRUB_DEFAULT 0" in s        # first entry
    assert "set_grub_default GRUB_TIMEOUT 0" in s        # no wait
    assert "set_grub_default GRUB_TIMEOUT_STYLE hidden" in s
    # It must run before grub-mkconfig regenerates grub.cfg, or the change is unused.
    assert s.index("set_grub_default GRUB_TIMEOUT 0") < s.index("grub-mkconfig -o /boot/grub/grub.cfg")


def test_chroot_setup_grub_default_helper_is_idempotent(tmp_path):
    # BEHAVIORAL: the set_grub_default helper must (a) REWRITE an existing key
    # (commented or not) and (b) APPEND a missing key, leaving each set exactly once.
    # Extract the helper definition + its three invocations from the emitted script
    # (a contiguous block: `set_grub_default() { ... }` immediately followed by the
    # three `set_grub_default ...` calls) and run it against a stock-like grub file.
    import re
    import subprocess

    s = installer.chroot_setup_sh()
    start = s.index("set_grub_default() {")
    end = s.index("\n\ngrub-mkconfig -o /boot/grub/grub.cfg")
    block = s[start:end]                       # def + the three calls
    assert "set_grub_default GRUB_TIMEOUT_STYLE hidden" in block

    grub = tmp_path / "grub"
    # Stock-ish: GRUB_DEFAULT present (non-zero, to prove rewrite), GRUB_TIMEOUT
    # present, GRUB_TIMEOUT_STYLE COMMENTED (to prove the commented branch), plus an
    # unrelated line that must be preserved.
    grub.write_text(
        "GRUB_DEFAULT=saved\n"
        "GRUB_TIMEOUT=5\n"
        "#GRUB_TIMEOUT_STYLE=menu\n"
        'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n'
    )
    sandboxed = block.replace("/etc/default/grub", str(grub))
    # Run TWICE to prove idempotency (a second pass must not duplicate any line).
    res = subprocess.run(["bash", "-c", "set -e\n" + sandboxed + "\n" + sandboxed],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = grub.read_text()
    # Exactly one of each key, all carrying the auto-boot values.
    assert len(re.findall(r"(?m)^GRUB_DEFAULT=", out)) == 1
    assert len(re.findall(r"(?m)^GRUB_TIMEOUT=", out)) == 1
    assert len(re.findall(r"(?m)^GRUB_TIMEOUT_STYLE=", out)) == 1
    assert "GRUB_DEFAULT=0" in out
    assert "GRUB_TIMEOUT=0" in out
    assert "GRUB_TIMEOUT_STYLE=hidden" in out
    # The old saved/5/commented values must be gone.
    assert "GRUB_DEFAULT=saved" not in out
    assert "GRUB_TIMEOUT=5" not in out
    # The unrelated line is preserved.
    assert 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"' in out


# --- installer_sh: ANSI codes, fdisk keystrokes, partition suffixes --------

def test_installer_sh_ansi_escape_sequences():
    # The color codes are emitted as the two-char bash escape backslash-033
    # (LIGHT_BLUE, RED, RESET). These reach the terminal as ESC at runtime; in
    # the file they are the literal backslash-zero-three-three text.
    s = installer.installer_sh()
    assert "LIGHT_BLUE='\\033[1;34m'" in s
    assert "RED='\\033[1;31m'" in s
    assert "RESET='\\033[0m'" in s
    # three color variables -> three backslash-033 occurrences.
    assert s.count("\\033") == 3


def test_installer_sh_ssh_variant_carried_by_verbatim_rootfs_copy():
    # The scripted (CLI/SSH) installer CLONES the live rootfs verbatim (like Calamares
    # unpackfs), so the sshd auto-setup enable-link -- which lives under the live
    # /etc/systemd/system/multi-user.target.wants/ on the ssh medium -- is carried
    # automatically with everything else. There is NO longer a separate hand-copy of that
    # unit/link (the rsync is a superset). The --ssh password hash rides along in the live
    # /etc/shadow, also copied by the rsync. So: no explicit sshd unit copy remains, and the
    # rsync of / is what guarantees ssh parity between live and installed.
    s = installer.installer_sh()
    # The old brittle hand-copy of the ssh unit is GONE (the verbatim copy supersedes it).
    assert "cp /etc/systemd/system/sshd-hypervisor-setup.service" not in s
    # The whole-rootfs clone is what carries it (rsync of the live root into the target).
    assert "rsync" in s
    assert "/mnt" in s


# --- installer_sh: the DESKTOP is carried onto the target (the gray-screen bug) ----
#
# ROOT CAUSE of the gray-screen/Openbox-error install: the Azzio desktop shell is a set of
# COMPILED C daemons + generated helper binaries emitted as root-owned files into the ISO
# airootfs (menu daemon, window-switcher daemon, terminal UI, OSD, /usr/local/bin launchers,
# wallpapers, picom config, ...). None are owned by a pacman package, so the OLD pacstrap +
# hand-copy-a-few-files installer left them ABSENT on the installed system -> the autostart's
# guarded launches silently no-op and the Super-key menu launcher fails ("Openbox error").
# Calamares works because unpackfs rsyncs the whole live squashfs verbatim. The FIX makes the
# scripted installer do the same: rsync the live running / into the target. These tests pin
# that new contract.

def test_installer_sh_clones_live_rootfs_into_target():
    # The core fix: instead of pacstrap + hand-copy, the installer rsyncs the ENTIRE live root
    # into the mounted target, so every compiled desktop daemon/binary lands on the installed
    # system by construction (matching Calamares unpackfs). Assert an archive-mode rsync of /
    # into /mnt with the attribute-preserving flags a rootfs clone needs.
    s = installer.installer_sh()
    assert "rsync" in s
    # Archive + ACLs + xattrs + hardlinks: the flags Calamares/archiso use for a faithful clone.
    assert "-aAXH" in s or ("-a" in s and "-A" in s and "-X" in s and "-H" in s)
    # Source is the live root, destination is the mounted target root.
    assert "/mnt" in s


def test_installer_sh_clone_shows_overall_progress():
    # The clone is the long step (the whole desktop -- gigabytes). A bare rsync prints nothing,
    # so "Cloning the live system..." looked frozen for a minute or two and was indistinguishable
    # from a hang (the reported "visually stuck ... add current out of total progress bar"). The
    # clone MUST show overall current-out-of-total progress: rsync --info=progress2 gives exactly
    # that (one rewriting line with the total percentage + bytes + rate), without the per-file
    # spam of --progress. Assert the flag rides on the clone rsync.
    s = installer.installer_sh()
    assert "--info=progress2" in s
    # It must be part of the clone command, not a stray token.
    assert "rsync -aAXH --info=progress2" in s


def test_installer_sh_rsync_excludes_virtual_filesystems():
    # A rootfs rsync MUST exclude the kernel/virtual and runtime trees or it will try to copy
    # /proc, /sys, /dev, /run (the archiso cow/bootmnt overlay lives under /run/archiso) and
    # /mnt (the target itself -> infinite recursion). /tmp is transient. Assert each is excluded.
    s = installer.installer_sh()
    for virt in ("/proc", "/sys", "/dev", "/run", "/mnt", "/tmp"):
        assert f"--exclude={virt}" in s or f'--exclude="{virt}' in s or f"--exclude='{virt}" in s, \
            f"rootfs rsync must exclude {virt}"


def test_installer_sh_regenerates_fstab_for_target_disk():
    # The live fstab is the archiso one (wrong root for the installed disk). After cloning the
    # rootfs the installer MUST regenerate fstab for the REAL partitions with genfstab, or the
    # installed system cannot mount its own root.
    s = installer.installer_sh()
    assert "genfstab -U /mnt >> /mnt/etc/fstab" in s


def test_installer_sh_still_writes_install_info_for_chroot():
    # The chroot step reads disk / is_uefi (and the identity answers) from /mnt/etc/install_info.
    # The rootfs-clone rewrite must still persist these markers.
    s = installer.installer_sh()
    assert "/mnt/etc/install_info/disk" in s
    assert "/mnt/etc/install_info/is_uefi" in s


def test_installer_sh_no_longer_pacstraps():
    # The verbatim-clone approach REPLACES the fresh pacstrap; a lingering pacstrap would both
    # double the work and re-introduce the "packages only, no compiled binaries" gap. Assert the
    # pacstrap call is gone.
    s = installer.installer_sh()
    assert "pacstrap" not in s


def test_installer_sh_recreates_chroot_mount_points_before_arch_chroot():
    # REGRESSION (install died at "mount: /mnt/proc: mount point does not exist"): the rootfs
    # rsync EXCLUDES /proc /sys /dev /run /tmp, which drops not just their CONTENTS but the
    # DIRECTORY NODES. On a fresh ext4 target these dirs then do not exist, and `arch-chroot`
    # -- which bind-mounts /proc onto /mnt/proc, /sys onto /mnt/sys, etc. -- fails immediately.
    # The installer MUST recreate the empty mount points AFTER the clone and BEFORE arch-chroot.
    s = installer.installer_sh()
    for mp in ("/mnt/proc", "/mnt/sys", "/mnt/dev", "/mnt/run", "/mnt/tmp"):
        assert mp in s, f"installer must recreate the {mp} mount point before arch-chroot"
    # /tmp is world-writable+sticky; a chroot that runs anything expecting a usable /tmp needs it.
    assert "chmod 1777 /mnt/tmp" in s
    # Ordering matters: the mkdir must come BEFORE the arch-chroot line or it cannot help.
    mkdir_idx = s.index("mkdir -p /mnt/proc")
    chroot_idx = s.index("arch-chroot /mnt")
    assert mkdir_idx < chroot_idx, "mount points must be created before arch-chroot"


def test_installer_sh_mount_point_block_actually_creates_dirs(tmp_path):
    # BEHAVIORAL (string-match is necessary but NOT sufficient -- prior installs shipped green
    # tests over a broken run). Extract the real mkdir+chmod block from the emitted installer,
    # retarget /mnt at a throwaway dir, EXECUTE it, and assert the four bind-mount points that
    # arch-chroot needs now exist and /tmp is sticky-world-writable (1777). This reproduces, in
    # miniature, the exact state arch-chroot requires -- so a regression that drops the block (or
    # gets the perms wrong) fails here, not just in a grep.
    import re
    import subprocess

    s = installer.installer_sh()
    start = s.index("mkdir -p /mnt/proc")
    end = s.index("\n", s.index("chmod 1777 /mnt/tmp"))
    block = s[start:end]
    target = tmp_path / "mnt"
    target.mkdir()
    sandboxed = block.replace("/mnt/", str(target) + "/")
    res = subprocess.run(["bash", "-c", "set -e\n" + sandboxed],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    for mp in ("proc", "sys", "dev", "run", "tmp"):
        assert (target / mp).is_dir(), f"{mp} mount point was not created by the block"
    # /tmp must be world-writable + sticky (mode 1777) -- the octal perms of the created dir.
    mode = oct((target / "tmp").stat().st_mode & 0o7777)
    assert mode == "0o1777", f"/tmp mount point should be 1777, got {mode}"


def test_installer_sh_preseed_choice_and_disk_for_ssh():
    # The scripted installer is the CLI/SSH install path (azzio-install --cli). For an
    # UNATTENDED SSH install it must accept a pre-seeded disk selection via env instead of
    # the interactive `read`: AZ_INSTALL_CHOICE (1=auto, 2=manual) and AZ_INSTALL_DISK. When
    # they are unset the interactive prompts still run (a plain `--cli` over SSH works step
    # by step). Assert both env hooks exist AND the interactive read is still the fallback.
    s = installer.installer_sh()
    assert 'if [ -n "$AZ_INSTALL_CHOICE" ]; then' in s
    assert 'choice="$AZ_INSTALL_CHOICE"' in s
    assert 'read -p "Enter option (1 or 2): " choice' in s   # interactive fallback kept
    assert 'if [ -n "$AZ_INSTALL_DISK" ]; then' in s
    assert 'manual_disk="$AZ_INSTALL_DISK"' in s
    assert 'read -p "Enter the device name' in s             # interactive fallback kept


def test_installer_sh_fdisk_keystrokes_uefi_and_bios():
    # UEFI carves a +1G EFI system partition; BIOS carves a +1M BIOS-boot
    # partition. The exact fdisk keystroke pipelines differ; both must ship.
    s = installer.installer_sh()
    assert '+1G' in s
    assert '+1M' in s
    assert 'echo -e "g\\nn\\n\\n\\n+1G\\nt\\n1\\nn\\n\\n\\n\\nw" | fdisk "$largest_disk"' in s
    assert 'echo -e "g\\nn\\n\\n\\n+1M\\nt\\n4\\nn\\n\\n\\n\\nw" | fdisk "$largest_disk"' in s


def test_installer_sh_nvme_vs_sata_partition_suffix():
    # nvme devices name partitions <disk>p1/p2; sata/scsi name them <disk>1/2.
    # Both branches must exist or one disk class gets the wrong device node.
    s = installer.installer_sh()
    assert 'part1="${largest_disk}p1"' in s
    assert 'part2="${largest_disk}p2"' in s
    assert 'part1="${largest_disk}1"' in s
    assert 'part2="${largest_disk}2"' in s


def test_installer_sh_filesystem_knob_defaults_ext4_and_supports_btrfs():
    # The root filesystem is chosen by AZ_INSTALL_FILESYSTEM: ext4 by default (a plain
    # `azzio-install --cli` is unchanged) or btrfs when set (what `--auto` pre-seeds, for
    # parity with the Calamares GUI's defaultFileSystemType). The value is validated up front
    # (only ext4/btrfs) so a typo aborts BEFORE the wipe, and BOTH mkfs branches must ship.
    s = installer.installer_sh()
    assert 'root_fs="${AZ_INSTALL_FILESYSTEM:-ext4}"' in s     # default ext4, honours the env
    assert 'mkfs.btrfs -f "$part2"' in s                        # btrfs branch (forced)
    assert 'mkfs.ext4 "$part2"' in s                            # ext4 branch (default)
    # Validation guard: an unsupported value exits non-zero rather than mkfs the wrong type.
    assert 'unsupported AZ_INSTALL_FILESYSTEM' in s
    assert 'ext4|btrfs)' in s
    # The ESP (mkfs.fat) is orthogonal and still present for the UEFI branch.
    assert 'mkfs.fat -F32 "$part1"' in s


def test_installer_sh_filesystem_validation_runs_before_the_wipe(tmp_path):
    # Behavioural: the AZ_INSTALL_FILESYSTEM validation must reject a bad value with a
    # non-zero exit. Drive just the validation snippet through bash for good/bad inputs so a
    # future edit that drops the guard (and would mkfs an arbitrary/foreign type) fails here.
    import subprocess
    s = installer.installer_sh()
    start = s.index('root_fs="${AZ_INSTALL_FILESYSTEM:-ext4}"')
    end = s.index('esac', start) + len('esac')
    snippet = "#!/bin/bash\n" + s[start:end] + '\necho "OK:$root_fs"\n'
    f = tmp_path / "fsguard.sh"
    f.write_text(snippet)

    def run(value):
        env = {"AZ_INSTALL_FILESYSTEM": value} if value is not None else {}
        return subprocess.run(["bash", str(f)], capture_output=True, text=True, env=env)

    # default (unset) -> ext4, exit 0
    r = run(None)
    assert r.returncode == 0 and "OK:ext4" in r.stdout, r.stderr
    # explicit btrfs -> exit 0
    r = run("btrfs")
    assert r.returncode == 0 and "OK:btrfs" in r.stdout, r.stderr
    # explicit ext4 -> exit 0
    r = run("ext4")
    assert r.returncode == 0 and "OK:ext4" in r.stdout
    # garbage / foreign fs -> non-zero, no OK line (aborts before any mkfs)
    r = run("xfs")
    assert r.returncode != 0 and "OK:" not in r.stdout
    r = run("; rm -rf /")
    assert r.returncode != 0 and "OK:" not in r.stdout


def test_chroot_setup_resets_machine_id_for_the_clone():
    # The rootfs clone copies the LIVE /etc/machine-id verbatim. Shipping a fixed machine-id on
    # every installed system is a real bug (duplicate ids across machines break DHCP leases,
    # systemd journal ids, etc.). The chroot step must reset it (empty file) so systemd
    # regenerates a unique id on first boot.
    s = installer.chroot_setup_sh()
    # An emptied /etc/machine-id (truncate/`: >`/`echo -n`/`rm`+recreate all read as "reset").
    assert "/etc/machine-id" in s
    assert (
        ": > /etc/machine-id" in s
        or "rm -f /etc/machine-id" in s
        or "truncate -s 0 /etc/machine-id" in s
        or 'echo -n > /etc/machine-id' in s
    ), "chroot must reset /etc/machine-id so the clone gets a fresh unique id"


# --- setup_pkgs: firewall direction ----------------------------------------

def test_setup_pkgs_firewall_direction():
    # Default-DENY inbound (silent drop -- no ICMP advertising the box), default-allow
    # outbound. Swapping these silently either firewalls off the machine's own traffic or
    # opens it to the world. The timedate port (49154) is explicitly denied so the local
    # home page stays reachable only by the machine itself.
    s = installer.setup_pkgs_sh()
    assert "sudo ufw enable" in s
    assert "sudo ufw default deny incoming" in s
    assert "sudo ufw default allow outgoing" in s
    assert "sudo ufw deny 49154" in s
    # The old 'reject' policy must not linger (the spec asks for Deny).
    assert "reject incoming" not in s


# --- first-boot systemd unit -----------------------------------------------

def test_first_boot_service_execstart_and_type():
    # The unit's ExecStart must point at the exact path installer_sh copies the
    # script to, and it must be a oneshot wanted by multi-user.target or it
    # never runs at boot.
    s = installer.first_boot_service()
    assert "ExecStart=/home/main/.config/first-boot/first-boot-setup.sh" in s
    assert "Type=oneshot" in s
    assert "[Install]" in s
    assert "WantedBy=multi-user.target" in s


def test_chroot_setup_installs_first_boot_files_from_payload():
    # REGRESSION: the compiler stages first-boot-setup.{sh,service,conf} ONLY under /root/azzio
    # (never at their runtime paths), so the verbatim rootfs clone does NOT place them. The
    # chroot must install them from /root/azzio into their runtime locations BEFORE it chmods /
    # enables the unit -- otherwise `systemctl enable first-boot-setup.service` silently fails and
    # the first-boot NTP oneshot never runs. Assert the three copies exist and precede the enable.
    s = installer.chroot_setup_sh()
    assert "cp /root/azzio/first-boot-setup.sh /home/main/.config/first-boot/first-boot-setup.sh" in s
    assert "cp /root/azzio/first-boot-setup.conf /home/main/.config/first-boot/first-boot-setup.conf" in s
    assert "cp /root/azzio/first-boot-setup.service /etc/systemd/system/first-boot-setup.service" in s
    # The install must come before the enable, or the enable has nothing to enable.
    assert s.index("cp /root/azzio/first-boot-setup.service") < s.index("systemctl enable first-boot-setup.service")


def test_first_boot_service_execstart_matches_chroot_perms_target():
    # Cross-file: the path the service execs must be a path the installed system actually
    # carries. Since the installer now CLONES the live rootfs verbatim, the first-boot script
    # arrives with everything else (it lives on the live medium) -- so installer_sh no longer
    # hand-copies it. The chroot step still `chmod`s that exact path to make it executable, so
    # the ExecStart<->on-disk agreement is now asserted against the chroot setup instead.
    script = "/home/main/.config/first-boot/first-boot-setup.sh"
    assert f"ExecStart={script}" in installer.first_boot_service()
    assert script in installer.chroot_setup_sh()


# --- locale block splice (single-seam isolation) ---------------------------

def test_locale_block_spliced_between_shebang_and_pacman_key(monkeypatch):
    # chroot_setup_sh() interpolates _detect_and_apply_locale_block() by the name
    # bound in the installer module namespace, so replacing that name changes the
    # emitted script. We prove the block lands after the shebang and before the
    # keyring init -- the ordering the chroot depends on.
    monkeypatch.setattr(
        installer, "_detect_and_apply_locale_block", lambda: "SENTINEL_LOCALE_MARKER"
    )
    s = installer.chroot_setup_sh()
    assert "SENTINEL_LOCALE_MARKER" in s
    assert s.index("#!/bin/bash") < s.index("SENTINEL_LOCALE_MARKER")
    assert s.index("SENTINEL_LOCALE_MARKER") < s.index("pacman-key --init")
