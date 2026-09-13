"""Calamares master settings.conf builder.

settings.conf is the top-level Calamares configuration: the module search paths,
the branding component, the second `shellprocess` instance, and the ordered
show/exec `sequence` that drives the whole install. It is big and self-contained,
so it lives in its own module. Re-exported by the `calamares` facade as
`calamares.settings_conf`.
"""

from __future__ import annotations


# --- 1. settings.conf -------------------------------------------------------
def settings_conf() -> str:
    """The top-level Calamares configuration: where to find modules, the branding
    component, and the ordered `sequence` of show (UI) and exec (work) phases.

    Every module named here has a configuration emitted below, or needs none (welcome,
    summary, finished, machineid, hwclock, networkcfg, mount, umount, fstab,
    localecfg have no required per-module configuration for our flow -- the ones we DO
    configure, including keyboard, are listed in emit_map()).
    """
    return """\
# Calamares master configuration for Azzio Linux.
---
# Directories scanned for module descriptors. Absolute paths are the system
# install locations from the `calamares` package; "modules" is relative to this
# settings.conf so our /etc/calamares/modules/*.conf overrides are picked up.
modules-search: [ local, /usr/lib/calamares/modules ]

# instances: run the `shellprocess` module a SECOND time with a different configuration.
# The default (id == module name) instance uses modules/shellprocess.conf (the
# pre-users/pre-initcpio fixups). The `desparse` instance -- referenced as
# `shellprocess@desparse` in the sequence -- uses modules/shellprocess-desparse.conf
# (mark /boot no-compress + rewrite the kernel/initramfs so GRUB can read them).
#
# The per-instance config-file key is `config:` -- NOT `configuration:`. Calamares'
# Settings.cpp InstanceDescription::fromSettings() reads `m.value("config")`; if that
# key is absent the instance's config filename SILENTLY DEFAULTS to `<module>.conf`
# (here shellprocess.conf). So a `configuration:` typo does NOT error -- it makes
# `shellprocess@desparse` re-run the DEFAULT shellprocess.conf (the mkinitcpio reset)
# instead of the desparse commands, the /boot fixup never runs, and the installed
# system fails to boot with "premature end of file /@/boot/vmlinuz-linux". This exact
# typo silently disabled the boot fix once already; keep it `config:`.
instances:
- id: desparse
  module: shellprocess
  config: shellprocess-desparse.conf

# The ordered install sequence. `show` phases render UI pages; `exec` phases do
# the actual work with a progress bar. Only modules with a configuration below (or that
# need none) appear here -- no dangling module names.
sequence:
- show:
  - welcome
  - locale
  - keyboard
  - partition
  # Azzio "Network" page (networkq QML view module, added by the
  # azzio-calamares-networkq source patch): Automatic (DHCP, the default) vs a
  # Manual static IPv4 (address / subnet mask / gateway / DNS 1 / DNS 2). It writes
  # its choice to GlobalStorage; the patched `networkcfg` exec job (below) turns a
  # manual choice into a 0600 static NetworkManager profile on the target. Placed
  # immediately BEFORE `users` (per the user's request), so the page order reads
  # partition -> network -> user account -> summary. Show-order is independent of the
  # exec order below: `networkcfg` still runs at exec time regardless of where the
  # page sits in the show sequence.
  - networkq
  - users
  - summary
- exec:
  - partition
  - mount
  - unpackfs
  # Remove the live rootfs's baked-in `main` account so the users module can
  # recreate it (see shellprocess.conf / the module note above). MUST run after
  # unpackfs (the target must exist) and before users.
  - shellprocess
  - machineid
  # luksbootkeyfile: when the user encrypted the disk, create /crypto_keyfile.bin
  # on the target root and `cryptsetup luksAddKey` it as a second LUKS key slot,
  # so the initramfs `encrypt` hook can unlock the root from the embedded keyfile
  # instead of RE-PROMPTING for the passphrase. THIS is the fix for the
  # "type the password twice at boot" report: GRUB still prompts once to read
  # /boot (it lives on the encrypted btrfs root), but the initramfs no longer
  # prompts a second time. It is a built-in Calamares C++ job (globalstorage
  # access -- it reads the passphrase the partition page captured, which a
  # shellprocess step cannot). MUST run BEFORE `fstab` (fstab points crypttab at
  # the keyfile only if it already exists) and BEFORE `initcpiocfg` (which adds
  # /crypto_keyfile.bin to mkinitcpio FILES= only if the file is present). No-op
  # on an unencrypted install or one with an unencrypted separate /boot.
  - luksbootkeyfile
  - fstab
  - locale
  - keyboard
  - localecfg
  - users
  - networkcfg
  - hwclock
  - initcpiocfg
  - initcpio
  - services-systemd
  - grubcfg
  - bootloader
  - packages
  # Make /boot GRUB-readable: mark it no-compress (chattr +C) and rewrite the kernel
  # + initramfs UNCOMPRESSED. The target btrfs is mounted compress=zstd:1 (mount.conf),
  # so unpackfs stores /boot/vmlinuz-linux as zstd-compressed extents, and GRUB's
  # btrfs driver -- which cannot decompress zstd -- reads it short: the install
  # completes but the target fails to boot with "premature end of file
  # /@/boot/vmlinuz-linux". (An earlier revision misdiagnosed this as a trailing
  # sparse hole; a plain in-place rewrite left the file compressed, so it never
  # booted. See calamares_shellprocess._boot_desparsify_command.)
  #
  # ORDERING invariant: keep this the LAST step that touches /boot -- after every
  # step that writes a /boot file (initcpio writes the initramfs; the `packages`
  # pacman transaction COULD, via mkinitcpio/kernel install hooks, rewrite /boot if
  # its removal set ever changes), immediately before `umount`. That way the fixup
  # always runs on the FINAL on-disk /boot state, so no later step can leave a
  # compressed file behind. (As currently configured `packages` only try_removes
  # calamares, which does not itself trigger the mkinitcpio hook -- but pinning this
  # last makes the "boot files are readable" invariant robust to future changes in
  # the removal set or step order. The chattr +C additionally keeps any file a
  # future step or update writes into /boot uncompressed.) grub.cfg (grubcfg/
  # bootloader, above) records only PATHS, not extents/lengths, so writing it before
  # this step is fine. Second shellprocess instance; its configuration is
  # modules/shellprocess-desparse.conf (see instances:).
  - shellprocess@desparse
  - umount
- show:
  - finished

# Branding component (branding/azzio/branding.desc).
branding: azzio

# Require the "Yes, I understand the installer will DESTROY data" checkbox before
# the destructive exec phase can run.
prompt-install: true

# The target is unpacked from the live medium, so nothing is installed to the
# host. Never touch the running live system's mounts / bootloader.
dont-chroot: false

# On finish, offer restart but do not force it.
disable-cancel: false
disable-cancel-during-exec: false
"""
