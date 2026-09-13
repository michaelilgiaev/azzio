"""The on-disk install pipeline scripts, authored in Python and emitted as the
real .sh/.conf/.service files the ISO ships.

  installer_sh()             azzio-iso-installer.sh: partition, CLONE the live rootfs
                             verbatim into the target, run chroot-setup
  chroot_setup_sh()          runs inside arch-chroot: locale, bootloader, services
  setup_pkgs_sh()            live-ISO oneshot: firewall tweaks
  first_boot_sh/service/conf first-boot-once mechanism on the installed system

WHY A VERBATIM ROOTFS CLONE (not pacstrap): the Azzio desktop shell is a set of
COMPILED C daemons + generated helper binaries (the application-menu daemon, the
window-switcher daemon, the terminal UI, the OSD, the /usr/local/bin launchers, the
wallpapers, /etc/xdg/azzio-picom.conf, the timedate app, ...). The compiler emits
them as root-owned files straight into the ISO airootfs; NONE are owned by a pacman
package. So a fresh `pacstrap` of packages.x86_64 + a hand-copy of a few files (the
old approach) left every one of those binaries ABSENT on the installed system: X and
OpenBox came up to a gray root window, but the autostart's guarded daemon launches
silently no-op'd and the Super-key menu launcher failed ("Openbox error"). The
Calamares GUI never had this bug because its `unpackfs` module rsyncs the ENTIRE live
squashfs rootfs verbatim into the target. This scripted installer now does the same
thing -- rsync the live running `/` into `/mnt` -- so the CLI/SSH install reaches the
exact same end-state as Calamares by construction (same binaries, same desktop, same
config), instead of trying to re-enumerate every root-owned artifact by hand.
"""

from __future__ import annotations

import installer_identity
from packages.calamares import calamares_shellprocess as _csp
from packages.calamares.locale import _detect_and_apply_locale_block


# Paths excluded from the live-rootfs -> target rsync. The kernel/virtual filesystems
# (/proc, /sys, /dev) hold no real files; the runtime trees (/run -- which on archiso
# holds the cow/bootmnt SquashFS overlay under /run/archiso -- and /tmp, /var/tmp) are
# transient; /mnt is the TARGET itself (excluding it prevents the rsync from recursing
# into its own destination); /media and /lost+found are irrelevant. This is the same set
# archiso/Calamares' unpackfs skip for a faithful clone. Kept as a constant so the bash
# generator stays readable and the exclude set is unit-testable in one place.
LIVE_ROOTFS_RSYNC_EXCLUDES = (
    "/proc", "/sys", "/dev", "/run", "/mnt", "/media", "/tmp", "/var/tmp", "/lost+found",
)


# --- The disk installer (runs in the live session) --------------------------
def installer_sh() -> str:
    body = """\
#!/bin/bash

set -o pipefail

cd /

# ANSI color codes
LIGHT_BLUE='\\033[1;34m'
RED='\\033[1;31m'
RESET='\\033[0m'

echo -e "${LIGHT_BLUE}Welcome to Azzio Installation${RESET}"
echo -e "${RED}WARNING:${RESET} This will erase everything on the targeted disk using wipefs -a, removing all filesystem, RAID, and partition-table signatures${RESET}"
echo "Select an installation option:"
echo "1. Automatically detect largest disk (excludes USB drives) and install azzio"
echo "2. Manually select disk to erase and install azzio"
# Non-interactive pre-seed (used by `azzio-install --cli --auto` / `--disk`, so an SSH
# install can run unattended): if AZ_INSTALL_CHOICE is set we use it instead of prompting;
# AZ_INSTALL_DISK pre-answers the manual device prompt. Unset -> the interactive read runs,
# so a plain `azzio-install --cli` over SSH still works step by step.
if [ -n "$AZ_INSTALL_CHOICE" ]; then
    choice="$AZ_INSTALL_CHOICE"
    echo "Enter option (1 or 2): $choice (pre-seeded)"
else
    read -p "Enter option (1 or 2): " choice
fi

# Convert size strings to bytes
convert_to_bytes() {
    local size=$1
    local unit=${size: -1}
    local num=${size%[A-Za-z]*}

    case $unit in
        T) awk "BEGIN {printf \\"%.0f\\", $num * 1024 * 1024 * 1024 * 1024}" ;;
        G) awk "BEGIN {printf \\"%.0f\\", $num * 1024 * 1024 * 1024}" ;;
        M) awk "BEGIN {printf \\"%.0f\\", $num * 1024 * 1024}" ;;
        K) awk "BEGIN {printf \\"%.0f\\", $num * 1024}" ;;
        *) awk "BEGIN {printf \\"%.0f\\", $num}" ;;
    esac
}

if [ "$choice" = "2" ]; then
    echo "Available disks:"
    echo "----------------"
    lsblk -d -e7,11 -o NAME,SIZE,MODEL | while read -r line; do
        echo "$line"
    done
    echo "----------------"
    if [ -n "$AZ_INSTALL_DISK" ]; then
        manual_disk="$AZ_INSTALL_DISK"
        echo "Enter the device name (e.g., sda or nvme0n1): $manual_disk (pre-seeded)"
    else
        read -p "Enter the device name (e.g., sda or nvme0n1): " manual_disk
    fi
    if [ ! -b "/dev/$manual_disk" ]; then
        echo "Invalid disk selected!"
        exit 1
    fi

    if mount | grep -q "/dev/$manual_disk"; then
        echo "Selected disk is mounted. Aborting."
        exit 1
    fi

    largest_disk="/dev/$manual_disk"
else
    echo "Searching for largest storage device..."

    largest_size=0
    largest_disk=""

    while read -r disk hotplug size; do
        if [[ "$hotplug" -eq 1 || "$disk" == loop* ]]; then
            continue
        fi
        if lsblk -d -o NAME,MOUNTPOINTS -n "/dev/$disk" | grep -q "[[:space:]]\\+/"; then
            echo "Skipping $disk (contains mounted partitions)"
            continue
        fi
        size_bytes=$(convert_to_bytes "$size")
        if [ "$size_bytes" -gt "$largest_size" ]; then
            largest_size=$size_bytes
            largest_disk="/dev/$disk"
        fi
    done < <(lsblk -d -o NAME,HOTPLUG,SIZE -n)

    if [ -z "$largest_disk" ]; then
        echo "No suitable disk found!"
        exit 1
    fi

    human_size=$(lsblk -d -o SIZE -n "$largest_disk")
    echo "Largest disk detected: $largest_disk ($human_size)"
fi

is_uefi=0
if [ -d "/sys/firmware/efi" ]; then
  is_uefi=1
fi

# Root filesystem. Defaults to ext4 (a plain `azzio-install --cli` is unchanged); the
# `--auto` mode pre-seeds AZ_INSTALL_FILESYSTEM=btrfs for parity with the Calamares GUI,
# whose defaultFileSystemType is btrfs. Only ext4/btrfs are supported by this scripted
# path (a flat filesystem -- no subvolumes; the rsync clone has no subvolume layout).
# Validate up front, BEFORE the wipe, so a typo aborts while the disk is still untouched
# rather than after mkfs picks the wrong type.
root_fs="${AZ_INSTALL_FILESYSTEM:-ext4}"
case "$root_fs" in
    ext4|btrfs) ;;
    *) echo "azzio-install: unsupported AZ_INSTALL_FILESYSTEM '$root_fs' (use ext4 or btrfs)"; exit 1 ;;
esac

# Collect the account / hostname / timezone answers (Calamares Users + Location parity) NOW,
# before anything destructive -- a mistyped answer costs nothing while the disk is untouched.
%IDENTITY_COLLECT%

# Final confirmation before the irreversible wipe (skipped when a disk was pre-seeded for an
# unattended install -- AZ_INSTALL_CHOICE/DISK imply "proceed without asking").
if [ -z "$AZ_INSTALL_CHOICE" ]; then
    echo
    echo -e "${RED}About to ERASE $largest_disk and install azzio as host '$az_hostname' (user '$az_username').${RESET}"
    read -rp "Type YES to proceed: " az_confirm
    if [ "$az_confirm" != "YES" ]; then
        echo "Aborted; no changes were made."
        exit 1
    fi
fi

echo "Erasing $largest_disk with 'wipefs -a'..."
wipefs -a "$largest_disk"

if [ $is_uefi -eq 1 ]; then
  echo "Partitioning $largest_disk for UEFI..."
  echo -e "g\\nn\\n\\n\\n+1G\\nt\\n1\\nn\\n\\n\\n\\nw" | fdisk "$largest_disk"
else
  echo "Partitioning $largest_disk for BIOS..."
  echo -e "g\\nn\\n\\n\\n+1M\\nt\\n4\\nn\\n\\n\\n\\nw" | fdisk "$largest_disk"
fi

if [[ $largest_disk =~ ^/dev/nvme ]]; then
    part1="${largest_disk}p1"
    part2="${largest_disk}p2"
else
    part1="${largest_disk}1"
    part2="${largest_disk}2"
fi

echo "Formatting partitions..."
if [ $is_uefi -eq 1 ]; then
  mkfs.fat -F32 "$part1"
fi
# Root filesystem per AZ_INSTALL_FILESYSTEM (validated above): ext4 default, or btrfs for
# `--auto` (parity with the Calamares GUI). -f forces btrfs over any lingering signature
# (wipefs already ran, but -f is belt-and-braces). Both are flat -- no subvolumes; genfstab
# below emits the correct entry for whichever type this is, and mkinitcpio/grub pick up
# btrfs from the installed system's own config, so nothing else needs to change per-fs.
echo "Formatting $part2 as $root_fs..."
if [ "$root_fs" = "btrfs" ]; then
  mkfs.btrfs -f "$part2"
else
  mkfs.ext4 "$part2"
fi

echo "Mounting partitions..."
mkdir -p /mnt
mount "$part2" /mnt
if [ $is_uefi -eq 1 ]; then
  mkdir -p /mnt/boot/EFI
  mount "$part1" /mnt/boot/EFI
fi

echo "Cloning the live system onto the target (this is the whole desktop)..."
# CLONE THE LIVE ROOTFS VERBATIM into the mounted target -- the Calamares `unpackfs` path,
# done with rsync. The live running `/` is the SquashFS root plus the live overlay: it already
# contains EVERYTHING the installed system needs -- every compiled Azzio daemon/binary under
# /usr/local, the wallpapers, /etc/xdg/azzio-picom.conf, the branded /usr/lib/os-release, the
# planted per-app overrides (kitty icon, gedit launcher), the fastfetch config, the first-boot
# unit + script, the /home/main desktop dotfiles (.bash_profile -> exec startx, .xinitrc,
# .config/openbox/*, themes), the getty@tty1 autologin drop-in, the /etc/{passwd,shadow,group}
# accounts (so `main` exists with the --ssh password hash on the ssh variant), the sudoers
# drop-ins, AND -- on the ssh variant -- the sshd-hypervisor-setup enable-link under
# /etc/systemd/system/multi-user.target.wants. So there is NO per-file hand-copy here anymore:
# the single rsync supersedes all of it, which is exactly why the installed CLI system now
# matches the live desktop instead of booting to a gray screen. -aAXH preserves perms/owners,
# ACLs, xattrs and hardlinks; --exclude keeps the virtual/runtime trees and the target itself
# out (see LIVE_ROOTFS_RSYNC_EXCLUDES).
#
# PROGRESS: this clone is the long step (the whole desktop -- gigabytes -- so it can sit for
# a minute or two). A bare rsync prints nothing, so the "Cloning..." line looked frozen and
# the user could not tell it apart from a hang. --info=progress2 gives the standard current-
# out-of-total readout: ONE rewriting line with the overall percentage, bytes transferred and
# transfer rate for the whole operation (NOT per-file spam like --progress). No pre-count scan
# is needed -- rsync tracks the total itself -- so it adds progress without a second traversal.
echo "  (this is the large step -- overall progress is shown below)"
rsync -aAXH --info=progress2 %RSYNC_EXCLUDES% / /mnt/

echo "Regenerating fstab for the installed disk..."
# The cloned /etc/fstab is the archiso live one (its root is the SquashFS/cow overlay, wrong
# for a real disk). Regenerate it for the ACTUAL target partitions, or the installed system
# cannot mount its own root. genfstab appends; the clone's fstab is emptied first so we do not
# stack a stale archiso entry under the real ones.
: > /mnt/etc/fstab
genfstab -U /mnt >> /mnt/etc/fstab

# Drop the install_info markers AFTER the clone so they sit ON TOP of the copied /etc (the
# chroot step reads disk / is_uefi / the identity answers from here). /etc/install_info does
# not exist on the live medium, so the rsync above never touches it -- writing it here keeps
# it robust regardless of rsync flags.
mkdir -p /mnt/etc/install_info
echo "$largest_disk" > /mnt/etc/install_info/disk
echo "$is_uefi" > /mnt/etc/install_info/is_uefi
%IDENTITY_WRITE%

# RECREATE THE VIRTUAL/RUNTIME MOUNT POINTS the rsync excluded. --exclude drops not just the
# CONTENTS of /proc /sys /dev /run /tmp but the DIRECTORY NODES themselves, so on the fresh ext4
# target these dirs do not exist. arch-chroot bind-mounts /proc onto /mnt/proc (and /sys, /dev,
# /run likewise); without the mount points it dies with "mount: /mnt/proc: mount point does not
# exist" and the whole install aborts. Recreate them empty (Calamares' mount module makes the
# same nodes). /tmp gets the world-writable sticky mode any chrooted tool expects.
mkdir -p /mnt/proc /mnt/sys /mnt/dev /mnt/run /mnt/tmp
chmod 1777 /mnt/tmp

echo "Copying chroot setup..."
cp /root/azzio/chroot-setup.sh /mnt/chroot-setup.sh
chmod +x /mnt/chroot-setup.sh

echo "Running chroot setup..."
arch-chroot /mnt /bin/bash /chroot-setup.sh
rm /mnt/chroot-setup.sh

umount -R /mnt
"""
    # Splice in the identity collection/persist fragments (Calamares Users + Location parity)
    # and the rsync exclude flags. Prefix /mnt: the installer targets the mounted new root.
    body = body.replace("%IDENTITY_COLLECT%", installer_identity.identity_collect_sh().strip("\n"))
    body = body.replace("%IDENTITY_WRITE%", installer_identity.identity_write_sh().strip("\n"))
    excludes = " ".join(f"--exclude={p}" for p in LIVE_ROOTFS_RSYNC_EXCLUDES)
    return body.replace("%RSYNC_EXCLUDES%", excludes)


# --- Runs inside the arch-chroot after the rootfs clone ---------------------
def chroot_setup_sh() -> str:
    chroot = f"""\
#!/bin/bash

{_detect_and_apply_locale_block()}

# FRESH MACHINE-ID. The rootfs clone copied the LIVE /etc/machine-id verbatim, so every
# system installed from this ISO would otherwise share one id -- breaking DHCP leases,
# systemd journal ids and anything keyed on machine-id. Empty the file so systemd
# regenerates a unique id on the installed system's first boot (the documented reset:
# `systemd-machine-id-setup` reads an empty/absent file and mints a new one).
: > /etc/machine-id

pacman-key --init
pacman-key --populate archlinux

# Mark setup complete
touch /var/log/.locale_set

# UNDO THE ARCHISO MKINITCPIO STATE BEFORE building the initramfs. The verbatim clone carries
# archiso's mkinitcpio artifacts, and a plain `mkinitcpio -P` on them yields an UNBOOTABLE
# installed system. This is the SAME reset the Calamares path runs post-unpackfs -- and to
# guarantee the two install paths can never drift, the CLI does not re-derive it: it EMBEDS the
# exact shared command block from packages/calamares (a single source of truth). That block:
#   A. reinstates /boot/vmlinuz-linux from /usr/lib/modules/<kver>/vmlinuz (mkarchiso empties
#      /boot; the `linux` package's install hook that would repopulate it never runs offline),
#   B. replaces the ARCHISO preset (PRESETS=('archiso') + the archiso.conf HOOKS drop-in) with
#      the STOCK `linux` preset and drops archiso.conf, so `mkinitcpio -P` below builds a
#      disk-bootable image against the installed /etc/mkinitcpio.conf.
{_csp._mkinitcpio_reset_command()}

# Regenerate the initramfs for the INSTALLED system (now against the stock preset + the
# installed /etc/mkinitcpio.conf, so it boots from the real disk).
mkinitcpio -P

is_uefi=$(cat /etc/install_info/is_uefi)
disk=$(cat /etc/install_info/disk)

if [ $is_uefi -eq 1 ]; then
  grub-install --target=x86_64-efi --bootloader-id=grub_uefi --recheck --efi-directory=/boot/EFI
else
  grub-install --target=i386-pc "$disk"
fi

# Auto-boot the FIRST menu entry with no wait (matches the Calamares grubcfg path).
# GRUB_DEFAULT=0 selects the first generated entry, GRUB_TIMEOUT=0 boots it
# immediately, and GRUB_TIMEOUT_STYLE=hidden shows no menu (hold SHIFT/ESC to
# reveal it). Rewrite the key if present, else append it, so this is idempotent
# regardless of the stock /etc/default/grub the `grub` package shipped.
set_grub_default() {{
  key="$1"; val="$2"
  if grep -q "^#\\?${{key}}=" /etc/default/grub; then
    sed -i "s|^#\\?${{key}}=.*|${{key}}=${{val}}|" /etc/default/grub
  else
    echo "${{key}}=${{val}}" >> /etc/default/grub
  fi
}}
set_grub_default GRUB_DEFAULT 0
set_grub_default GRUB_TIMEOUT 0
set_grub_default GRUB_TIMEOUT_STYLE hidden

grub-mkconfig -o /boot/grub/grub.cfg

systemctl enable NetworkManager

# FIRST-BOOT ONESHOT (NTP-on-first-boot). The compiler stages these three files ONLY under
# /root/azzio on the ISO (never at their runtime paths), so the verbatim clone does NOT place
# them -- we must install them from /root/azzio here (the old pacstrap installer hand-copied
# them the same way). Home paths use /home/main because this runs BEFORE the identity rename;
# the identity step below re-points the unit's ExecStart + the script's CONFIG_FILE to
# /home/$az_login if the account was renamed (see installer_identity.identity_chroot_sh).
mkdir -p /home/main/.config/first-boot
cp /root/azzio/first-boot-setup.sh /home/main/.config/first-boot/first-boot-setup.sh
cp /root/azzio/first-boot-setup.conf /home/main/.config/first-boot/first-boot-setup.conf
cp /root/azzio/first-boot-setup.service /etc/systemd/system/first-boot-setup.service
chown 1000:998 /home/main/.config
chmod 755 /home/main/.config/first-boot/first-boot-setup.sh
chmod 644 /etc/systemd/system/first-boot-setup.service
# 644, not world-writable: first-boot-setup.service runs as root (no User=), so root rewrites
# First_Boot=TRUE->FALSE here just fine -- there is no reason to make this config world-writable.
chmod 644 /home/main/.config/first-boot/first-boot-setup.conf
systemctl enable first-boot-setup.service

# NOTE: there is deliberately NO recursive world-open chmod sweep over /home here. The verbatim
# rootfs clone (rsync -aAXH) already reproduced the live home's correct perms and ownership
# (dirs 755, files their real modes, owned main:main); a blanket chmod would only RE-INTRODUCE
# a world-writable $HOME (the local security hole this unification removed) and mark every file
# executable. Calamares does not do it, so neither does the CLI path. If a post-rename chown is
# ever needed it is handled per-account by the identity step below (usermod/mv preserve
# ownership), not with a recursive world-open chmod.

pacman -Sy
%IDENTITY_CHROOT%

# STRIP THE LIVE-ONLY INSTALLER STATE FROM THE CLONE. The verbatim clone inherited the LIVE
# OpenBox autostart, whose live-only lines (a) RE-LAUNCH the disk-erasing installer at every
# login and (b) force a fixed us,il keyboard over the chosen region layout -- and it inherited
# the installer's Desktop icon / application-menu entry / privileged wrapper. Calamares deletes
# all of this post-unpackfs; the CLI clone must too -- and to guarantee the two paths never
# drift, the CLI does NOT re-derive the cleanup: it EMBEDS the SAME shared command block
# Calamares' shellprocess emits (packages/calamares.installer_cleanup_command), just applied to
# the CHOSEN login's home instead of the literal /home/main. This runs AFTER the identity step
# so `main` may have been renamed and /home/main moved to /home/$az_login. Re-derive the login
# from install_info here so the block is self-contained even if the rename block was skipped,
# and ensure the target .config/openbox dir exists (it does after the clone+rename, but the
# mkdir is a cheap guard) before the shared block's `cp` writes the installed autostart.
az_login="$(cat /etc/install_info/username 2>/dev/null)"
az_login="${{az_login:-main}}"
mkdir -p "/home/$az_login/.config/openbox" /etc/skel/.config/openbox
{_csp.installer_cleanup_command("/home/$az_login")}

echo -e "\\e[94mazzio disk installation complete, you can reboot now.\\e[0m"
"""
    # Apply the collected identity (user/passwords/hostname/timezone) as the LAST step, AFTER
    # the `main`-hardcoded home setup above (so those lines still see the original account and
    # /home/main) and AFTER the static locale block (so the chosen timezone override wins).
    # Spliced by name to avoid f-string brace-escaping the fragment's shell `${...}`/`{...}`.
    return chroot.replace("%IDENTITY_CHROOT%", installer_identity.identity_chroot_sh().rstrip("\n"))


# --- Live-ISO post-boot tweaks ----------------------------------------------
def setup_pkgs_sh() -> str:
    """Live-ISO oneshot: firewall setup + SSH host keys.

    Firewall baseline (the Azzio default the `azzio network firewall` command later
    manages live): ENABLED, incoming DENY (silent drop, not reject -- no ICMP telling a
    scanner the box is here), outgoing ALLOW, and port 49154 explicitly DENIED. 49154 is
    the Azzio timedate home page (Flask on localhost:49154); it must stay reachable ONLY
    by the machine itself, so the deny rule guarantees it is never exposed off-box even if a
    later rule loosens the default. The sshd variant opens :22 afterwards via its own oneshot
    (system.SSHD_HYPERVISOR_SETUP_SERVICE runs `ufw allow ssh`), so ssh works there without
    weakening this base policy."""
    return """\
#!/bin/bash

# Fix firewall configuration
sudo ufw enable
sudo ufw default deny incoming
sudo ufw default allow outgoing
# Close the timedate home-page port (localhost:49154) to everything off-box.
sudo ufw deny 49154

# Generate SSH host keys so sshd can complete the handshake
sudo ssh-keygen -A
"""


# --- First-boot-once mechanism (installed system) ---------------------------
def first_boot_conf() -> str:
    return """\
# Set to TRUE to enable first boot shell script.
# as the name suggests, first boot will only run once after boot and then disable itself.
# This file is checked upon startup.
First_Boot=TRUE
"""


def first_boot_service() -> str:
    return """\
[Unit]
Description=First boot configuration
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/home/main/.config/first-boot/first-boot-setup.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""


def first_boot_sh() -> str:
    return """\
#!/bin/bash

CONFIG_FILE="/home/main/.config/first-boot/first-boot-setup.conf"

# Check if configuration file exists and contains First_Boot=TRUE
if grep -q '^First_Boot=TRUE' "$CONFIG_FILE"; then
    echo "First boot setup enabled. Running setup..."

    # Wait up to 15 seconds for internet connection
    timeout 15s bash -c "until ping -c 1 archlinux.org >/dev/null 2>&1; do sleep 1; done" || { echo "No internet connection after 15s"; }
    [ $? -eq 0 ] && timedatectl set-ntp true

    # Set First_Boot=FALSE
    sed -i 's/^First_Boot=TRUE/First_Boot=FALSE/' "$CONFIG_FILE"
    echo "First boot setup complete. Config updated."
else
    echo "First boot setup not enabled. Skipping."
fi
"""
