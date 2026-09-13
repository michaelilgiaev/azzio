#!/usr/bin/env python3
"""azzio guest command line interface -- `azzio gpu` (detect the GPU, resolve its drivers).

The ISO ships a GENERIC graphics stack (mesa) that works on every machine and hypervisor.
`azzio gpu --resolve` looks at the machine's actual PCI display controller(s), and INSTALLS
the matching vendor + developer/compute drivers from the baked-in OFFLINE pacman repo (so it
works with no network). Outdated packages are out of scope -- `sudo pacman -Syu` handles that.

Detection is root-free: it reads the PCI class + vendor id straight from sysfs
(/sys/bus/pci/devices/*/{class,vendor}); a display controller has class 0x03xxxx. A VM /
hypervisor GPU (QXL, virtio-gpu, VMware) has no vendor driver, so it resolves to "generic"
and mesa already covers it. Standard library only; bundled into /usr/local/bin/azzio
(see common.py). Called by name from command_line_interface.py (cmd -> cmd_gpu).
"""

from __future__ import annotations
import os
import subprocess

# BUNDLE_START

# PCI vendor id (as sysfs reports it, lowercase 0x-prefixed) -> our vendor key.
# 0x1022 is AMD's "host bridge" vendor id; some APUs expose the GPU function under it, so
# both AMD ids map to "amd".
VENDOR_IDS: dict[str, str] = {
    "0x10de": "nvidia",
    "0x1002": "amd",
    "0x1022": "amd",
    "0x8086": "intel",
}

# vendor key -> {"base": [graphics driver packages], "dev": [developer/compute packages]}.
# "common" is applied IN ADDITION for any detected vendor (the vendor-neutral loader/tools).
# Every package here is BAKED INTO the ISO (packages.x86_64) so --resolve is an offline install.
# lib32-* need multilib (the build enables it). Package names are the current Arch repo names.
DRIVER_MAP: dict[str, dict[str, list[str]]] = {
    "intel": {
        "base": ["vulkan-intel", "lib32-vulkan-intel",
                 "intel-media-driver", "libva-intel-driver"],
        "dev":  ["intel-compute-runtime"],
    },
    "amd": {
        "base": ["xf86-video-amdgpu", "vulkan-radeon", "lib32-vulkan-radeon"],
        "dev":  ["rocm-opencl-runtime", "rocm-hip-runtime", "opencl-mesa"],
    },
    # NVIDIA ships the OPEN kernel module (nvidia-open-dkms). As of the current driver
    # series (6xx) Arch's repos carry ONLY the open module -- the proprietary/closed kernel
    # module was discontinued upstream and is not in extra/ (nvidia-dkms is now just a
    # Provides alias for nvidia-open-dkms). DKMS builds it against the shipped kernel;
    # nvidia-utils/settings/cuda/opencl are module-agnostic.
    "nvidia": {
        "base": ["nvidia-open-dkms", "nvidia-utils", "lib32-nvidia-utils", "nvidia-settings"],
        "dev":  ["cuda", "opencl-nvidia"],
    },
    "common": {
        "base": ["vulkan-icd-loader", "lib32-vulkan-icd-loader",
                 "vulkan-mesa-layers", "libva", "libva-utils", "libvdpau-va-gl"],
        "dev":  ["vulkan-tools", "clinfo", "vulkan-headers", "opencl-headers"],
    },
}


# The NVIDIA open kernel module, baked into the ISO via packages.x86_64. Arch's current
# driver series ships only this (the proprietary/closed kernel module was discontinued and
# is not in the repos), so there is no proprietary package to fall back to without the AUR.
NVIDIA_OPEN = "nvidia-open-dkms"


def detect_vendors(sysfs_root: str = "/sys/bus/pci/devices") -> list[str]:
    """The sorted, unique vendor keys of the machine's PCI DISPLAY controllers (class
    0x03xxxx). Empty list => no known-vendor GPU (a VM/hypervisor generic device); mesa
    already covers that. Root-free: reads sysfs only. Unreadable tree => empty list."""
    found: set[str] = set()
    try:
        entries = sorted(os.listdir(sysfs_root))
    except OSError:
        return []
    for name in entries:
        base = os.path.join(sysfs_root, name)
        try:
            with open(os.path.join(base, "class"), encoding="utf-8") as fh:
                klass = fh.read().strip().lower()
            with open(os.path.join(base, "vendor"), encoding="utf-8") as fh:
                vendor = fh.read().strip().lower()
        except OSError:
            continue
        # Display controller class is 0x03xxxx (VGA, 3D, other display).
        if not klass.startswith("0x03"):
            continue
        key = VENDOR_IDS.get(vendor)
        if key:
            found.add(key)
    return sorted(found)


# The OFFLINE pacman repo baked into the live ISO (downloader.py builds it; installer.py
# copies it to /mnt for on-disk installs). On the live ISO the drivers are HERE, so --resolve
# is a local install with no network.
LIVE_REPO = "/root/azzio/pacstrap-azzio-repo"


def wanted_packages(vendors: list[str]) -> list[str]:
    """Every baked-in driver package (vendor base+dev, plus the common base+dev) for the
    detected vendors, order-stable and deduped. No vendor => empty (mesa already covers a
    generic GPU, so there is nothing to resolve)."""
    if not vendors:
        return []
    out: list[str] = []
    for v in [*vendors, "common"]:
        spec = DRIVER_MAP.get(v)
        if not spec:
            continue
        for pkg in spec.get("base", []) + spec.get("dev", []):
            if pkg not in out:
                out.append(pkg)
    return out


def installed_packages(pkgs: list[str]) -> set[str]:
    """The subset of `pkgs` already installed, via `pacman -Q <pkg>...` (a nonzero exit for
    the missing ones is expected; we parse the names it DID report). Empty on any failure."""
    if not pkgs:
        return set()
    try:
        res = subprocess.run(["pacman", "-Q", *pkgs],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, check=False)
    except OSError:
        return set()
    names: set[str] = set()
    for line in res.stdout.splitlines():
        parts = line.split()
        if parts:
            names.add(parts[0])
    return names & set(pkgs)


def _offline_conf(repo_dir: str) -> str:
    """A transient pacman.conf that activates ONLY the file:// offline repo (SigLevel=Never,
    matching pacman.py's installer conf), so --resolve never mutates the system config. The
    [options] block is minimal; pacman fills the rest from built-ins."""
    return (
        "[options]\n"
        "HoldPkg = pacman glibc\n"
        "Architecture = auto\n"
        f"\n[pacstrap-azzio-repo]\n"
        "SigLevel = Never\n"
        f"Server = file://{repo_dir}/\n"
    )


def pacman_install_argv(pkgs: list[str], repo_dir: str) -> list[str]:
    """The argv to install `pkgs` from the offline repo. Writes the transient conf to a
    fixed /tmp path (pollution goes to /tmp per the repo rules) and points pacman at it."""
    conf_path = "/tmp/azzio-gpu-pacman.conf"
    return ["pacman", "-Sy", "--needed", "--noconfirm",
            "--config", conf_path, *pkgs]


def _install_argv_for(pkgs: list[str]) -> list[str] | None:
    """The pacman argv to install `pkgs`, writing the transient offline conf first. Uses the
    file:// offline repo when it exists (the live ISO), else the system repos (an installed,
    networked machine). Returns None only if the transient conf could not be written."""
    repo_dir = LIVE_REPO
    if os.path.isdir(repo_dir):
        conf = _offline_conf(repo_dir)
        try:
            with open("/tmp/azzio-gpu-pacman.conf", "w", encoding="utf-8") as fh:
                fh.write(conf)
        except OSError as exc:
            _err(f"azzio gpu: could not write transient pacman.conf: {exc}")
            return None
        return pacman_install_argv(pkgs, repo_dir)
    _err("azzio gpu: offline repo not found; installing from configured repos")
    return ["pacman", "-Sy", "--needed", "--noconfirm", *pkgs]


def _run_install(pkgs: list[str]) -> int:
    """Install the resolved driver packages under sudo from the offline repo (or the system
    repos when the offline repo dir is absent, e.g. an installed networked machine)."""
    if not pkgs:
        print("azzio gpu: nothing to install (drivers already present)")
        return 0
    argv = _install_argv_for(pkgs)
    if argv is None:
        return 1
    return _sudo(*argv, check=False)


def _print_status(vendors: list[str]) -> None:
    """Human status: what GPUs were detected and which driver packages are present/missing."""
    if not vendors:
        print("GPU: generic (no NVIDIA/AMD/Intel PCI display controller detected)")
        print("  mesa already provides the generic driver; nothing to resolve.")
        return
    print(f"GPU vendor(s) detected: {', '.join(vendors)}")
    pkgs = wanted_packages(vendors)
    have = installed_packages(pkgs)
    for pkg in pkgs:
        mark = "installed" if pkg in have else "MISSING"
        print(f"  {pkg:32s} {mark}")


def gpu_usage() -> None:
    print(
        "Usage: azzio gpu [--resolve | --list]\n"
        "\n"
        "Detect the machine's GPU and resolve its drivers from the baked-in offline repo.\n"
        "\n"
        "  --resolve    Install the missing vendor + developer drivers for the detected GPU.\n"
        "               NVIDIA installs the open kernel module (nvidia-open-dkms) -- Arch's\n"
        "               current driver series ships only the open module.\n"
        "  --list       Print the full vendor -> driver package map.\n"
        "  --help       Show this help.\n"
        "  (no option)  Print the detected GPU and which driver packages are present/missing.\n"
        "\n"
        "Outdated drivers are not updated here; run `sudo pacman -Syu` for that.\n"
    )


def _print_map() -> None:
    for v in ("nvidia", "amd", "intel", "common"):
        spec = DRIVER_MAP[v]
        print(f"{v}:")
        print(f"  base: {' '.join(spec['base'])}")
        print(f"  dev:  {' '.join(spec.get('dev', []))}")


def cmd_gpu(args: list[str]) -> int:
    """Dispatch `azzio gpu ...`. No option -> status; --resolve installs missing drivers;
    --list prints the map; --help prints usage."""
    opt = args[0] if args else ""
    if opt in ("--help", "-h", "help"):
        gpu_usage()
        return 0
    if opt == "--list":
        _print_map()
        return 0
    vendors = detect_vendors()
    if opt == "":
        _print_status(vendors)
        return 0
    if opt == "--resolve":
        if not vendors:
            print("GPU: generic -- no vendor driver needed (mesa covers it).")
            return 0
        pkgs = wanted_packages(vendors)
        have = installed_packages(pkgs)
        missing = [p for p in pkgs if p not in have]
        print(f"Resolving GPU drivers for: {', '.join(vendors)}")
        if not missing:
            print("All matching drivers already installed; nothing to do.")
            return 0
        print("Installing: " + " ".join(missing))
        return _run_install(missing)
    _err(f"azzio gpu: unknown option: {opt}")
    gpu_usage()
    return 2
