"""Calamares storage-layer config builders: disks, LUKS, unpack source, mounts, fstab.

Everything about where and how the system lands on disk:
  - partition.conf       Btrfs default + LUKS1 full-disk encryption + layout
  - unpackfs.conf        the live SquashFS the OFFLINE install is copied from
  - mount.conf           per-filesystem mount options + the chroot bind mounts
  - fstab.conf           /etc/fstab (crypttab + /tmp only; real opts come from mount)
  - luksbootkeyfile.conf embed a LUKS keyfile so the encrypted root unlocks once

Re-exported by the `calamares` facade as `calamares.partition_conf`, etc.
"""

from __future__ import annotations

from .config_constants import ARCHISO_SFS


# --- 2. modules/partition.conf ---------------------------------------------
def partition_conf() -> str:
    """Partitioning: Btrfs default, LUKS2 full-disk encryption offered, sane
    EFI/swap defaults, and both "Erase disk" and "Manual" modes enabled."""
    return """\
# Partitioning behaviour for Azzio.
---
# Bootloader install location. "grub" pairs with the grubcfg + bootloader modules
# in the sequence; Calamares picks EFI vs BIOS from the running firmware.
efiSystemPartition: "/boot/efi"

# Recommended/forced sizes for the EFI System Partition (UEFI installs).
efiSystemPartitionSize: 512M
efiSystemPartitionName: EFISYSTEM

# Default filesystem for the root partition. BTRFS is the Azzio default.
# NOTE: the Calamares 3.4.x key is `defaultFileSystemType` (verified against
# upstream src/modules/partition/partition.conf) -- NOT `defaultFileSystem`.
defaultFileSystemType: "btrfs"

# Filesystems offered in the manual-partitioning "format as" dropdown. btrfs
# first so it is the default selection.
availableFileSystemTypes: [ "btrfs", "ext4", "xfs", "f2fs" ]

# Installation choices offered on the partition page. We allow wiping the whole
# disk (the common path) and full manual partitioning. "alongside" and "replace"
# are left off to keep the minimal installer focused; add them here if desired.
#   erase   -> "Erase disk" (whole-disk, offers the Encrypt checkbox)
#   manual  -> "Manual partitioning"
userSwapChoices:
    - none
    - small
    - suspend
    - file

# The default swap strategy when erasing a disk. "none" avoids a btrfs swapfile,
# which would need a dedicated NOCOW subvolume to work correctly (extra wiring we
# do not ship). The user can still pick "small"/"file"/"suspend" from the
# userSwapChoices list on the partition page if they want swap.
initialSwapChoice: none

# Install choices (whole-disk vs manual). "erase" exposes the "Encrypt system"
# checkbox; keeping "manual" lets advanced users lay out partitions by hand.
initialPartitioningChoice: erase
allowManualPartitioning: true

# --- LUKS full-disk encryption -----------------------------------------------
# Presence of luksGeneration + an encryption-capable install choice makes the
# "Encrypt system" checkbox (with a passphrase field) appear on the Erase page.
#
# luks1 (NOT luks2) on purpose. /boot lives on the encrypted btrfs root (there is
# no separate unencrypted /boot), so GRUB itself must unlock the container to read
# the kernel. GRUB <= 2.12 CANNOT open a LUKS2 container whose key slot uses
# Argon2id -- and cryptsetup's LUKS2 default PBKDF is Argon2id -- so a luks2 install
# would leave GRUB unable to unlock at all (or, on GRUB 2.14, fail on Argon2's
# memory cost). This is exactly why upstream Calamares defaults luksGeneration to
# luks1. LUKS1 always uses PBKDF2, which GRUB reads fine. Combined with the
# luksbootkeyfile module (added to the sequence above), the user types the
# passphrase ONCE at the GRUB prompt and the initramfs unlocks from the embedded
# keyfile -- fixing the previous "password twice" behaviour.
luksGeneration: luks1

# Partition layout table style. "gpt" for UEFI is standard; Calamares still falls
# back to msdos on legacy BIOS systems automatically when needed.
defaultPartitionTableType: gpt

# Do not draw partitions smaller than this in the visual editor (cosmetic).
drawNestedPartitions: false
alwaysShowPartitionLabels: true

# Ensure a fresh GPT is written when erasing (no leftover boot flags).
initialPartitionAttributes: []

# Btrfs subvolume layout applied when root is formatted btrfs. @ = root, @home =
# /home, so snapshots/rollback tooling (snapper etc.) works cleanly later.
btrfsSubvolumes:
    - mountPoint: /
      subvolume: /@
    - mountPoint: /home
      subvolume: /@home

# Require at least this much space (GiB) before install can proceed.
requiredStorage: 12.0
"""


# --- 3. modules/unpackfs.conf ----------------------------------------------
def unpackfs_conf() -> str:
    """Copy the live archiso root filesystem onto the freshly-formatted target.

    On an archiso live medium the boot device is mounted at /run/archiso/bootmnt
    and the SquashFS root image sits at arch/x86_64/airootfs.sfs under it.
    unpackfs mounts that squashfs and rsyncs it into the target -- an OFFLINE
    install with no pacman network access, consistent with the rest of Azzio.
    """
    return f"""\
# Unpack the live filesystem to the target (offline install source).
---
unpack:
    - source: "{ARCHISO_SFS}"
      sourcefs: "squashfs"
      destination: ""
"""


# --- 6a. modules/mount.conf -------------------------------------------------
def mount_conf() -> str:
    """Extra mount options applied when mounting the target for the install.
    Btrfs gets compression + noatime so the copied system is space-efficient.

    extraMounts also bind/mount the pseudo-filesystems the chrooted install jobs
    (initcpio, bootloader) need. The efivarfs entry is LOAD-BEARING for UEFI: the
    bootloader module runs `grub-install --target=x86_64-efi`, which shells out to
    efibootmgr to register the NVRAM boot entry, and efibootmgr can only do that if
    efivarfs is mounted RW at /sys/firmware/efi/efivars *inside the target chroot*.
    A fresh `sysfs` mount on /sys does NOT bring the efivarfs submount along, so
    without this explicit entry grub-install fails with:
        EFI variables are not supported on this system.
        grub-install: error: efibootmgr failed to register the boot entry: ...
    and Calamares aborts at the bootloader step. It must be listed AFTER the /sys
    (sysfs) entry so its mountpoint directory exists first. On a BIOS/non-UEFI host
    /sys/firmware/efi is absent; Calamares logs the failed extra mount and carries
    on, and BIOS grub-install (--target=i386-pc) never touches efivars anyway."""
    return """\
# Filesystem-specific mount options used while installing to / and after.
---
extraMounts:
    - device: proc
      fs: proc
      mountPoint: /proc
    - device: sys
      fs: sysfs
      mountPoint: /sys
    # efivarfs must sit UNDER /sys (mounted above) so the target chroot's
    # grub-install/efibootmgr can register the UEFI boot entry. Without it the
    # bootloader step dies with "EFI variables are not supported on this system".
    - device: efivarfs
      fs: efivarfs
      mountPoint: /sys/firmware/efi/efivars
      efi: true
    - device: /dev
      mountPoint: /dev
      options: [ bind ]
    - device: tmpfs
      fs: tmpfs
      mountPoint: /run
    - device: /run/udev
      mountPoint: /run/udev
      options: [ bind ]

# Per-filesystem mount options. btrfs: zstd compression + noatime. This is the
# module that feeds the installed system's real mount options (fstab reads them
# from here / the partition module, NOT from fstab.conf).
mountOptions:
    - filesystem: default
      options: [ defaults, noatime ]
    - filesystem: btrfs
      options: [ defaults, noatime, compress=zstd:1 ]
"""


# --- 6b. modules/fstab.conf -------------------------------------------------
def fstab_conf() -> str:
    """/etc/fstab generation.

    NOTE (Calamares 3.4.2 schema, additionalProperties:false, required:
    [tmpOptions]): fstab ONLY accepts `crypttabOptions` + `tmpOptions`. The real
    per-filesystem mount options (btrfs compress/noatime) are taken from the
    PARTITION module's mountOptionsList / mount.conf -- NOT set here. The old
    `mountOptions`/`ssdExtraMountOptions`/`efiMountOptions` keys are rejected."""
    return """\
# fstab generation for the installed system.
---
# crypttab timeout/options for LUKS-encrypted roots.
crypttabOptions: luks

# /tmp handling (required by the schema). tmpfs-backed /tmp on both HDD and SSD.
tmpOptions:
    default:
        tmpfs: true
        options: "defaults,noatime,mode=1777"
    ssd:
        tmpfs: true
        options: "defaults,noatime,mode=1777"
"""


# --- 6d3. modules/luksbootkeyfile.conf -------------------------------------
def luksbootkeyfile_conf() -> str:
    """Config for the luksbootkeyfile module (added to the exec sequence). The
    module creates /crypto_keyfile.bin on the target and `cryptsetup luksAddKey`s
    it so the initramfs `encrypt` hook unlocks the root from the embedded keyfile
    instead of prompting a SECOND time at boot -- the fix for the "password twice"
    report. See settings.conf's sequence note.

    The single valid key is `luks2Hash` (the PBKDF for the keyfile's LUKS2 key
    slot: pbkdf2 / argon2i / argon2id / default). Azzio installs LUKS1
    (partition.conf luksGeneration: luks1, so GRUB can unlock /boot on the
    encrypted root), and LUKS1 always uses PBKDF2 -- so luks2Hash has no effect
    here. We ship it explicitly as `default` for clarity and so a future switch to
    luks2 has an obvious, documented knob (set pbkdf2 to keep GRUB-openable slots).
    The module is a no-op on an unencrypted install."""
    return """\
# luksbootkeyfile: embed a LUKS keyfile in the initramfs so the encrypted root is
# unlocked automatically after GRUB's prompt (no second passphrase prompt).
---
# PBKDF for the keyfile's key slot. Only meaningful for LUKS2; Azzio uses LUKS1
# (always PBKDF2), so this is inert -- shipped as `default` for clarity.
luks2Hash: default
"""
