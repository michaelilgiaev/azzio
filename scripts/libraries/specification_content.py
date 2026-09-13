"""
specification_content -- the one hand-authored part of the specification: SUBSYSTEMS.

Everything else in the specification (tables, metrics, per-package edition/category tags,
the layered SVG) is *computed* from real package data (specification_resolve, specification_classify,
specification_svg). SUBSYSTEMS is the exception: the grouping of the key packages by real
role, with a concrete technical capability blurb per package, which cannot be
derived from metadata alone.

Following the project's configuration-as-Python convention, that content lives here as
data. The renderer pulls the real version for every listed package from the
closure, so versions never go stale, and warns about any listed package missing
from the closure so this file cannot silently drift from reality.

To add/adjust a subsystem: edit SUBSYSTEMS. Each entry is:
    (key, title, prose, [(package_or_"a / b", capability), ...])
"""

# (key, title, prose, [(package(s), capability)])
SUBSYSTEMS = [
    (
        "kernel", "Kernel, firmware & boot",
        "The absolute foundation of the OS. The kernel is `linux` (mainline Linux, "
        "Arch patchset), shipped with matching `linux-headers` for out-of-tree "
        "module builds and the full `linux-firmware` set (plus the Marvell split-out "
        "package) for network, GPU and platform device firmware. CPU microcode is "
        "loaded early from `amd-ucode` and `intel-ucode`; `sof-firmware` provides "
        "DSP firmware for modern Intel/AMD audio. The initramfs is built by "
        "`mkinitcpio` with the `mkinitcpio-archiso` hooks that make the live medium "
        "boot, and `booster` is present as an alternative generator. The medium "
        "boots on both firmware types: BIOS via `syslinux`, UEFI via `grub`, "
        "`refind`, `efibootmgr` and the bundled `edk2-shell`; "
        "`memtest86+`/`memtest86+-efi` provide RAM diagnostics and `os-prober` "
        "detects already-installed operating systems for dual-boot menus.",
        [
            ("linux", "The Linux kernel and loadable modules"),
            ("linux-headers", "Kernel headers/build scripts for out-of-tree modules"),
            ("linux-firmware", "Device firmware blobs (Wi-Fi, GPU, etc.)"),
            ("linux-firmware-marvell", "Firmware for Marvell devices"),
            ("amd-ucode", "Early microcode updates for AMD CPUs"),
            ("intel-ucode", "Early microcode updates for Intel CPUs"),
            ("sof-firmware", "Sound Open Firmware for modern audio DSPs"),
            ("mkinitcpio", "Modular initramfs image generator"),
            ("mkinitcpio-archiso", "archiso hooks that make the live image boot"),
            ("booster", "Alternative fast initramfs generator"),
            ("grub", "GRUB2 bootloader (BIOS + UEFI)"),
            ("syslinux", "BIOS boot loaders (FAT/ext/btrfs/PXE/CD)"),
            ("refind", "Graphical EFI boot manager"),
            ("efibootmgr", "Edit UEFI boot entries from userspace"),
            ("edk2-shell", "EDK2 UEFI interactive shell"),
            ("memtest86+ / memtest86+-efi", "RAM diagnostic (BIOS and EFI builds)"),
            ("os-prober", "Detect other installed OSes for boot menus"),
        ],
    ),
    (
        "base", "Base system & shell",
        "The minimal Unix userland the desktop and installer sit on. `systemd` is "
        "the init system, service manager and journal; `base`/`base-devel` pull in "
        "the standard GNU coreutils/toolchain set, and `sudo` handles privilege "
        "escalation. Two shells ship: `bash` (the default) and `zsh` with the "
        "polished `grml-zsh-config`. Terminal editors (`nano`, `vim`), pagers "
        "(`less`), the manual system (`man-db` + `man-pages`), a terminal file "
        "manager (`mc`), a process monitor (`htop`), terminal multiplexers (`tmux`, "
        "`screen`), and staples like `rsync`, `diffutils`, `bc` and `pv` round out "
        "a self-sufficient command line.",
        [
            ("systemd", "init, service manager, journald, logind"),
            ("bash", "Default POSIX shell"),
            ("zsh", "Advanced interactive shell"),
            ("grml-zsh-config", "Batteries-included zsh setup"),
            ("sudo", "Privilege escalation"),
            ("vim / nano", "Terminal text editors"),
            ("less", "Terminal pager"),
            ("man-db / man-pages", "Manual page reader + Linux man pages"),
            ("mc", "Norton-Commander-style file manager"),
            ("htop", "Interactive process viewer"),
            ("tmux / screen", "Terminal multiplexers"),
            ("rsync", "Fast local/remote file sync"),
            ("bc / pv / diffutils", "Calculator, pipe meter, patch tools"),
            ("xdg-utils", "Desktop integration helpers"),
        ],
    ),
    (
        "display", "Display server & graphics",
        "The X11 graphics stack. `mesa` supplies the open-source OpenGL/Vulkan "
        "drivers and `libglvnd` provides vendor-neutral GL dispatch. A full X.Org "
        "server (`xorg-server`) is present alongside `xorg-xwayland`; the `xorg` "
        "group also brings the complete set of X utilities (`xrandr`, `xinput`, "
        "`setxkbmap`, etc.) and the generic `xf86-video-vesa` fallback driver. No "
        "desktop environment or display manager is shipped in this stripped-down "
        "base -- the medium boots to a console and a desktop/WM is layered on later "
        "in the overhaul.",
        [
            ("mesa", "Open-source OpenGL/Vulkan drivers"),
            ("libglvnd", "GL vendor-neutral dispatch"),
            ("xorg-server", "X.Org X11 display server"),
            ("xorg-xwayland", "Run X clients under Wayland"),
            ("xorg-xrandr / xorg-xinput", "Display + input configuration"),
            ("xf86-video-vesa", "Generic VESA fallback video driver"),
        ],
    ),
    (
        "audio", "Audio (PipeWire)",
        "Audio is served by PipeWire, the low-latency media graph that replaces "
        "PulseAudio and JACK. `pipewire-pulse` provides the PulseAudio-compatible "
        "daemon and `pipewire-alsa` the ALSA routing configuration, so both PulseAudio and "
        "ALSA clients play through PipeWire transparently. `alsa-utils` gives "
        "kernel-level mixer/control tools. `livecd-sounds` supplies accessibility "
        "sound cues on the live medium.",
        [
            ("pipewire", "Low-latency audio/video graph server"),
            ("pipewire-pulse", "PulseAudio-compatible daemon"),
            ("pipewire-alsa", "ALSA client routing into PipeWire"),
            ("alsa-utils", "Kernel ALSA mixer/control utilities"),
        ],
    ),
    (
        "networking", "Networking & VPN",
        "A broad connectivity and diagnostics stack. `networkmanager` is the "
        "connection manager, driven from the console via `nmcli`/`nmtui`. "
        "Wireless is backed by `wpa_supplicant`, the "
        "newer `iwd` daemon, and the `iw`/`wireless_tools` CLIs. Remote access and "
        "tunnelling: `openssh`, plus VPN clients `openvpn`, `openconnect` (Cisco "
        "AnyConnect) and `vpnc`; dial-up/DSL/mobile paths via `ppp`, `pptpclient`, "
        "`rp-pppoe`, `xl2tpd`, `wvdial` and `modemmanager`. DNS tooling includes "
        "`bind` utilities, the `dnsmasq` forwarder/DHCP server and the `ldns` "
        "library. Diagnostics: `tcpdump` (packet capture), `nmap` (scanning), "
        "`ethtool`, `ndisc6` (IPv6), and transfer tools `curl` and `lftp`.",
        [
            ("networkmanager", "Connection manager (nmcli/nmtui)"),
            ("iwd / wpa_supplicant", "Wi-Fi daemons"),
            ("iw / wireless_tools", "Wireless CLI configuration"),
            ("openssh", "SSH client/server"),
            ("openvpn / openconnect / vpnc", "VPN clients"),
            ("modemmanager / ppp / pptpclient", "Mobile/dial-up connectivity"),
            ("bind / dnsmasq / ldns", "DNS server/forwarder/library"),
            ("tcpdump / nmap / ethtool", "Capture, scan, NIC control"),
            ("curl / lftp", "HTTP(S)/FTP transfer clients"),
        ],
    ),
    (
        "storage", "Storage, filesystems & installation",
        "This is the core rescue-and-install mission of the medium. Two installers "
        "ship: `archinstall` (guided) and `arch-install-scripts` "
        "(`pacstrap`/`arch-chroot`). Partitioning and block-layer: `parted`, "
        "`gptfdisk`, `cryptsetup` (LUKS/dm-crypt), `lvm2`, `mdadm` (software RAID) "
        "and `dmraid`. Filesystem userspace tools cover ext2/3/4 (`e2fsprogs`), "
        "Btrfs, XFS, F2FS, exFAT, FAT (`dosfstools`), NTFS (`ntfs-3g`), NILFS, JFS "
        "and bcachefs, plus `nfs-utils`, `open-iscsi` and `nbd` for networked "
        "storage and `squashfs-tools` for the live image format. Imaging, recovery "
        "and health: `clonezilla`, `partclone`, `fsarchiver`, `testdisk`, "
        "`ddrescue`, `smartmontools` and `nvme-cli`.",
        [
            ("archinstall", "Guided Arch installer (TUI)"),
            ("arch-install-scripts", "`pacstrap` / `arch-chroot` / `genfstab`"),
            ("parted / gptfdisk", "MBR/GPT partitioning"),
            ("cryptsetup", "LUKS/dm-crypt full-disk encryption"),
            ("lvm2 / mdadm / dmraid", "LVM, software + BIOS RAID"),
            ("btrfs-progs / xfsprogs / e2fsprogs", "Btrfs, XFS, ext2/3/4 tools"),
            ("f2fs-tools / exfatprogs / ntfs-3g", "F2FS, exFAT, NTFS"),
            ("nilfs-utils / jfsutils / bcachefs-tools", "NILFS, JFS, bcachefs"),
            ("nfs-utils / open-iscsi / nbd", "Network filesystems and block devices"),
            ("squashfs-tools", "SquashFS (live image) tools"),
            ("clonezilla / partclone / fsarchiver", "Disk imaging/cloning/backup"),
            ("testdisk / ddrescue", "Partition + data recovery"),
            ("smartmontools / nvme-cli", "SMART monitoring, NVMe control"),
        ],
    ),
    (
        "security", "Security, firewall & crypto hardware",
        "Host firewalling is provided by `ufw` (a netfilter front end), enabled by "
        "the live-ISO setup with a default reject-incoming / allow-outgoing policy. "
        "Trusted-computing and hardware-token support: `tpm2-tss` (the TSS2 stack) "
        "and `tpm2-tools` for TPM 2.0; `libfido2` for FIDO2/U2F security keys; "
        "`pcsclite` smartcard middleware; and `openpgp-card-tools` for OpenPGP "
        "smartcards. `sequoia-sq` is a modern OpenPGP CLI. Full-disk encryption is "
        "available at install time via `cryptsetup` (LUKS/dm-crypt).",
        [
            ("ufw", "netfilter firewall front end"),
            ("tpm2-tss / tpm2-tools", "TPM 2.0 software stack + tools"),
            ("libfido2", "FIDO2 / U2F security-key support"),
            ("pcsclite", "PC/SC smartcard middleware"),
            ("sequoia-sq", "OpenPGP command-line tool"),
            ("openpgp-card-tools", "Manage OpenPGP smartcards"),
            ("cryptsetup", "LUKS/dm-crypt full-disk encryption"),
        ],
    ),
    (
        "devtools", "Developer toolchain & runtimes",
        "The OS ships end-user development runtimes out of the box. `python` with "
        "`python-pip`; `go`; and a complete .NET stack (`dotnet-sdk`, "
        "`dotnet-runtime`, `dotnet-host`). `git` provides version control, `neovim` "
        "a modern editor, `jq` JSON processing, and `tk` the Tcl/Tk GUI toolkit "
        "(which also backs Python's tkinter).",
        [
            ("python", "CPython interpreter"),
            ("python-pip", "Python package installer"),
            ("go", "Go compiler and toolchain"),
            ("dotnet-sdk", ".NET SDK (build + CLI)"),
            ("dotnet-runtime", ".NET runtime"),
            ("git", "Distributed version control"),
            ("neovim", "Extensible modal editor"),
            ("jq", "Command-line JSON processor"),
            ("tk", "Tcl/Tk GUI toolkit (backs tkinter)"),
        ],
    ),
    (
        "multimedia", "Multimedia & office",
        "Media playback centres on `vlc` with codec plugins for FFmpeg decode and "
        "x264/x265 (H.264/H.265) encode, plus UPnP streaming. `libreoffice-fresh` "
        "is the full office suite (documents, spreadsheets, presentations).",
        [
            ("vlc", "Multimedia player and framework"),
            ("vlc-plugin-ffmpeg", "FFmpeg-based decode for VLC"),
            ("vlc-plugin-x264 / vlc-plugin-x265", "H.264 / H.265 encoding"),
            ("vlc-plugin-upnp", "UPnP/DLNA media browsing"),
            ("libreoffice-fresh", "Full office productivity suite"),
        ],
    ),
    (
        "hardware", "Hardware, virtualization & peripherals",
        "Bluetooth is provided by the `bluez` stack (`bluetoothctl` CLI); printing "
        "by the `cups` daemon. The medium runs well as a guest under every major "
        "hypervisor: `open-vm-tools` (VMware), `qemu-guest-agent` (QEMU/KVM), "
        "`virtualbox-guest-utils-nox` (VirtualBox) and `hyperv` (Microsoft "
        "Hyper-V). Hardware inspection and control: `usbutils`, `usb_modeswitch`, "
        "`dmidecode` (DMI/SMBIOS), and Thunderbolt via `bolt`. Accessibility is "
        "served by `brltty` (braille displays) and `espeakup` (console speech).",
        [
            ("bluez / bluez-utils", "Bluetooth stack + tools"),
            ("cups", "Printing daemon"),
            ("open-vm-tools", "VMware guest integration"),
            ("qemu-guest-agent", "QEMU/KVM guest agent"),
            ("virtualbox-guest-utils-nox", "VirtualBox guest utilities"),
            ("hyperv", "Hyper-V guest tools"),
            ("usbutils / usb_modeswitch", "USB inspection + mode switching"),
            ("dmidecode", "DMI/SMBIOS hardware table dump"),
            ("bolt", "Thunderbolt device management"),
            ("brltty / espeakup", "Braille + speech accessibility"),
        ],
    ),
]
