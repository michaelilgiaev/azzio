#!/usr/bin/env python3
"""azzio guest command line interface -- `mkazzioiso` (Method B: build the SSH ISO from the LIVE system).

Method A (`compile.sh --ssh="<PASSWORD>"`) rebuilds the SSH variant from the recipe. This is
Method B: a command that ships INSIDE the distribution and produces the SSH variant FROM THE
RUNNING SYSTEM -- so any packages the user installed while live are captured. Same
`--ssh="<PASSWORD>"` option, same security posture as Method A: the password becomes `main`'s
login credential, hashed sha-512 into the emitted ISO's /etc/shadow; root stays LOCKED; no
default password is ever shipped (the credential is REQUIRED -- Method B only makes the SSH
variant).

Named after `mkarchiso` (which it drives). mkarchiso is a pacstrap+overlay tool: it always
rebuilds the rootfs from a package list and lays the profile's airootfs/ on top -- it CANNOT
squash a raw live rootfs as-is. So Method B works WITH that model instead of against it:

  1. Seed a COMPLETE archiso profile from the on-system releng skeleton
     (/usr/share/archiso/configs/releng): syslinux/, efiboot/, grub/, pacman.conf,
     packages.x86_64, airootfs skeleton. Without this mkarchiso aborts on missing files.
  2. REGENERATE packages.x86_64 from `pacman -Qqe` (the currently-installed explicit
     packages) -- this is what captures packages installed while live; mkarchiso pacstraps
     them into a clean, bootable rootfs.
  3. OVERLAY a CURATED, boot-safe slice of the live state into the profile's airootfs/: the
     user trees /home, /usr/local, /opt, /etc/skel; the safe config dir /etc/calamares; and
     ONLY the exact azzio /etc FILES (users/branding/sudoers + the azzio unit FILES). NEVER
     whole /etc (the live /etc/mkinitcpio.conf lacks the archiso HOOKS, /etc/fstab carries host
     disk UUIDs -> unbootable ISO) and NEVER whole /etc/systemd/system (its enable-symlink
     forest would bake the host's enabled daemons into the live ISO). The recipe's CURATED
     enable-links are re-created instead (mirroring _link_services). See OVERLAY_SOURCES /
     OVERLAY_ETC_FILES / ENABLE_LINKS.
  4. Overlay the sshd variant's differences: /etc/shadow (main=hash, root locked), the
     sshd-hypervisor auto-setup service + its enable link, and a profiledef naming the ISO
     azzio-headed-ssh.
  5. Run `mkarchiso -v -w work -o out profile`.

Everything below lands in the single bundled /usr/local/bin/azzio namespace, so it calls the
common helpers (_err, _sudo, _sudo_write, _have) by bare name. See common.py / bundle.py.
"""

from __future__ import annotations

# BUNDLE_START

# os + subprocess are already imported in common.py's bundle header; re-imported here
# (idempotent -- rebinds the same modules) so this module also works when imported on its
# own, e.g. by the unit tests.
import os        # noqa: E402
import subprocess  # noqa: E402


# The on-system archiso releng profile: the COMPLETE skeleton mkarchiso needs (boot dirs,
# pacman.conf, packages.x86_64, airootfs skeleton). The recipe build copies the same path
# (libraries/compiler._copy_releng). Provided by the `archiso` package.
RELENG_PROFILE = "/usr/share/archiso/configs/releng"

# The live TREES overlaid on top of the pacstrap into the profile's airootfs/. USER DATA +
# local software only -- boot-safe, no system config: /home (the live user's data + config,
# the visible "current state"), /usr/local (the azzio binaries + user-installed local tools),
# /opt (user-installed local software), /etc/skel (the desktop skeleton new users inherit).
OVERLAY_SOURCES = ["/home", "/usr/local", "/opt", "/etc/skel"]

# The CURATED set of EXACT /etc FILES that carry azzio's identity -- the SAME files the recipe
# emits into airootfs/etc (users/branding/sudoers + the azzio UNIT FILES themselves, by exact
# path). We overlay individual FILES, never whole directories -- and NEVER /etc/systemd/system
# wholesale: that directory on a running (installed) system holds the host's ENABLE-SYMLINK
# forest (multi-user.target.wants/*), which would bake the installed box's enabled daemons
# (stock sshd, cloud-init, systemd-homed, VM agents, ...) into the LIVE ISO -- a boot-behaviour
# AND security regression. Instead we copy the azzio unit FILES here and re-create the recipe's
# CURATED enable-links ourselves (see ENABLE_LINKS). Boot-critical/host files (mkinitcpio.conf,
# fstab, machine-id, crypttab) are deliberately absent, or the ISO would not boot as a live
# medium. Any member missing on the running system is simply skipped.
OVERLAY_ETC_FILES = [
    "/etc/passwd",
    "/etc/group",
    "/etc/gshadow",
    "/etc/hostname",
    "/etc/sudoers.d/00-main",
    "/etc/sudoers.d/00-rootpw",
    "/etc/sudoers.d/00-secure-path",
    # azzio systemd UNIT FILES (the .service/.conf bodies -- NOT their .wants enable links).
    "/etc/systemd/system/locale-setup.service",
    "/etc/systemd/system/pkgs-setup.service",
    "/etc/systemd/system/azzio-sleep-policy.service",
    "/etc/systemd/system/azzio-timedate.service",
    "/etc/systemd/system/home-main-Shared.mount",
    "/etc/systemd/system/getty@tty1.service.d/autologin.conf",
    "/etc/systemd/logind.conf.d/10-azzio-power.conf",
    "/etc/udev/rules.d/99-azzio-sleep-policy.rules",
]

# A /etc directory that IS safe to overlay wholesale (config data, nothing boot-critical, no
# enable-symlink forest): the Calamares installer configuration tree.
OVERLAY_ETC_DIRS = ["/etc/calamares"]

# The CURATED multi-user.target.wants enable-links to CREATE in the ISO -- mirrors the recipe's
# _link_services (libraries/compiler.py) EXACTLY, so the emitted live medium enables the same
# minimal daemon set the recipe designs (NetworkManager, CUPS, spice-vdagentd, the azzio
# oneshots) rather than inheriting the host's enable state. Each entry is (symlink target,
# link name under multi-user.target.wants). The sshd-hypervisor-setup enable-link is added
# separately by _overlay_sshd_variant (it is the sshd variant's defining extra). Bluetooth is
# deliberately absent (OFF by default), and there is NO display-manager / graphical.target
# (X starts from the shell), matching the recipe.
ENABLE_LINKS = [
    ("/usr/lib/systemd/system/NetworkManager.service", "NetworkManager.service"),
    ("/usr/lib/systemd/system/org.cups.cupsd.service", "org.cups.cupsd.service"),
    ("/usr/lib/systemd/system/spice-vdagentd.service", "spice-vdagentd.service"),
    ("/etc/systemd/system/locale-setup.service", "locale-setup.service"),
    ("/etc/systemd/system/pkgs-setup.service", "pkgs-setup.service"),
    ("/etc/systemd/system/azzio-sleep-policy.service", "azzio-sleep-policy.service"),
    ("/etc/systemd/system/azzio-timedate.service", "azzio-timedate.service"),
    # The virtiofs shared-folder auto-mount, enabled on BOTH variants so --shared appears at
    # /home/main/Shared regardless of --ssh (the headed-variant coupling fix). It is a .mount
    # unit, but the enable-link is a symlink named after the unit like any .service.
    ("/etc/systemd/system/home-main-Shared.mount", "home-main-Shared.mount"),
]

# Volatile / privacy-sensitive / self paths the overlay rsync must never copy. Patterns are
# matched by rsync against the overlaid source roots. The mounted virtiofs `shared` folder holds
# HOST material (authorized_keys) and must never be baked in; caches and tmp are noise; the
# machine-id is reset by mkarchiso anyway.
# SECRET / CREDENTIAL stores under the overlaid home + config trees that must NEVER be baked
# into a DISTRIBUTED ISO. Overlaying whole /home would otherwise ship the operator's SSH
# private keys, GPG private keyring, shell history, saved passwords, cloud tokens, and browser
# credential stores -- readable by the autologin `main` user off the distributed image. This
# is an information-disclosure hole, not noise, so it is a first-class part of the exclude set.
# Patterns are matched by rsync against the overlaid source roots (leading */ so they match at
# any home depth). Kept EXHAUSTIVE on purpose: the cost of an over-broad exclude is a missing
# config file; the cost of a miss is a leaked private key.
SECRET_EXCLUDES = [
    "*/.ssh/*",                      # SSH private keys + authorized_keys + known_hosts
    "*/.gnupg/*",                    # GPG private keyring / trust db / random_seed
    "*/.bash_history",               # shell history (may contain typed secrets)
    "*/.zsh_history",
    "*/.python_history",
    "*/.local/share/keyrings/*",     # GNOME keyring (login secrets)
    "*/.config/rclone/*",            # rclone remotes (cloud tokens) -- see backup targets
    "*/.netrc",                      # machine/login/password for ftp/http tooling
    "*/.git-credentials",            # stored git HTTPS credentials
    "*/.config/git/credentials",
    "*/.password-store/*",           # `pass` password store
    "*/.aws/*",                      # AWS credentials
    "*/.docker/config.json",         # docker registry auth tokens
    "*/.mozilla/*",                  # browser profiles (saved logins, cookies, tokens)
    "*/.config/librewolf/*",         # (LibreWolf profile; the recipe ships policy, not a profile)
    "*/.config/chromium/*",
    "*/.config/google-chrome/*",
    "*/.pki/*",                      # NSS cert/key databases
    # azzio's OWN first-party secret stores (shipped by this distro's recipe):
    "*/Vault/*",                     # the `passwords` manager store: ~/Vault/passwords.txt(.gpg)
    "*/backup.tar.gz.gpg",           # the `backup` command's HOME archive
    "*/passwords.tar.gz.gpg",        # the `backup` command's password-store archive
]

RSYNC_EXCLUDES = [
    "*/.cache/*",
    "/root/.cache/*",
    "/tmp/*",
    "/var/tmp/*",
    "*/Shared/*",
    "*/.gvfs",
    "*/.local/share/Trash/*",
    "/etc/machine-id",
] + SECRET_EXCLUDES

# `pacman -Qqen` == explicitly-installed, NATIVE (in-repo) package names, one per line. This
# is the set pacstrap reinstalls into the emitted ISO, so packages added from repos while live
# are captured. NATIVE (-n) on purpose: foreign/AUR packages (`-Qqem`) are in no configured
# repo, so pacstrap inside mkarchiso could not fetch them and would abort -- filtering to
# native keeps the build sound (the trade-off: AUR packages are not re-shipped by Method B).
CURRENT_PACKAGES_CMD = ["pacman", "-Qqen"]

# The locked-password marker (matches libraries/system.LOCKED_PASSWORD). Kept inline because
# the bundled guest CLI cannot import the build-side `system` module.
_LOCKED_PASSWORD = "!"

# The sshd-hypervisor auto-setup unit -- byte-identical intent to
# libraries/system.SSHD_HYPERVISOR_SETUP_SERVICE (kept inline for the same bundling reason).
# Runs `azzio --sshd-hypervisor` at boot as root with SUDO_USER=main so the host pubkey lands
# in /home/main/.ssh, after pkgs-setup so `ufw allow ssh` wins.
SSHD_HYPERVISOR_SETUP_SERVICE = """\
[Unit]
Description=Azzio sshd-hypervisor auto-setup (install host pubkey + start sshd)
After=pkgs-setup.service
Wants=pkgs-setup.service
ConditionPathExists=/usr/local/bin/azzio

[Service]
Type=oneshot
Environment=SUDO_USER=main
ExecStart=/usr/local/bin/azzio --sshd-hypervisor
RemainAfterExit=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

# The virtiofs shared-folder auto-mount -- byte-identical to
# libraries/system.HOME_MAIN_SHARED_MOUNT (kept inline for the same bundling reason).
# A test (test_shared_mount_unit_bodies_match_across_build_paths) pins the two copies
# equal. Mounts the host ./Shared folder (virtiofs tag "shared") at /home/main/Shared
# on boot, enabled on BOTH variants -- so --shared works on the headed variant too,
# not just the ssh one (the old coupling fix).
HOME_MAIN_SHARED_MOUNT = """\
[Unit]
Description=Azzio host<->guest shared folder (virtiofs)
DefaultDependencies=no
After=local-fs-pre.target
Before=local-fs.target

[Mount]
What=shared
Where=/home/main/Shared
Type=virtiofs

[Install]
WantedBy=multi-user.target
"""


def parse_ssh_arg(args: list[str]) -> str | None:
    """Pull the `--ssh=<PASSWORD>` value out of args, or None if absent/empty. Uses
    split("=", 1) so a password containing '=' is not truncated (same rule as the rest of the
    guest CLI). Empty value -> None (the flag demands a non-empty string)."""
    for token in args:
        if token.startswith("--ssh="):
            return token.split("=", 1)[1] or None
    return None


def shadow_for(main_password_hash: str) -> str:
    """The sshd variant's /etc/shadow: `main` gets the operator's real sha-512 hash, root
    stays LOCKED. Rejects a blank/plaintext/locked value -- the flag must resolve to a proper
    crypt hash first (never a shipped default, never plaintext-in-image)."""
    if main_password_hash in ("!", "*") or not main_password_hash.startswith("$"):
        raise ValueError(
            "shadow_for: main_password_hash must be a crypt hash (starts with '$'), "
            f"never a blank/plaintext/locked password (got {main_password_hash!r})."
        )
    return (
        f"root:{_LOCKED_PASSWORD}:14871::::::\n"
        f"main:{main_password_hash}:14871::::::\n"
    )


def _hash_password(password: str) -> str:
    """Hash the --ssh password into a sha-512 crypt hash ($6$...) via `openssl passwd -6`
    (openssl is present on every system that can run mkarchiso). Python's crypt module is gone
    as of 3.13, so openssl is the portable source of the hash."""
    if not password:
        raise ValueError("_hash_password: refusing to hash an empty password")
    try:
        out = subprocess.run(
            ["openssl", "passwd", "-6", "-stdin"],
            input=password, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as e:
        raise RuntimeError(f"could not hash the --ssh password with openssl: {e}") from e
    if not out.startswith("$6$"):
        raise RuntimeError(f"openssl produced an unexpected hash: {out!r}")
    return out


def excludes_for(work_dir: str) -> list[str]:
    """The static RSYNC_EXCLUDES plus the concrete run's work dir, so an overlay rsync never
    copies the profile/ISO it is currently assembling."""
    return RSYNC_EXCLUDES + [f"{work_dir.rstrip('/')}/*"]


def _usable_cores() -> int:
    """Compile-parallelism cap for the guest tool, leaving the machine usable.

    This MIRRORS makepkg.build_jobs's DEFAULT policy on the build host -- a
    hardcoded 75% of the cores (floor(cores * 0.75)) -- but is a standalone copy on
    purpose: this module is bundled into the guest `/usr/local/bin/azzio` script,
    which has none of the build-host libraries/ modules to import. Leaves ~25% of
    cores free; a 1-core box still gets 1. NOTE: the build-host --use-each-cpu escape
    hatch is NOT mirrored here -- the guest script takes no such flag, so a guest-side
    ISO build always keeps the 75% cap."""
    try:
        cores = len(os.sched_getaffinity(0))  # affinity-aware (respects cpuset limits)
    except (AttributeError, OSError):
        cores = os.cpu_count() or 1
    cores = max(1, cores)
    return max(1, cores * 75 // 100)  # floor(cores * 0.75) via integer arithmetic


def profiledef_sh(threads: int | None = None) -> str:
    """A minimal archiso profiledef naming the artifact azzio-headed-ssh-<ver>-x86_64.iso
    and locking down the security-sensitive modes. It REPLACES the releng profiledef (same
    filename) so the boot infrastructure the releng skeleton provides is reused unchanged
    while the ISO name + shadow/sudoers modes become ours.

    `threads` caps the zstd bootstrap-tarball compression; None -> _usable_cores() so a
    guest-side ISO build leaves headroom instead of pinning every core with -T0."""
    if threads is None:
        threads = _usable_cores()
    # NOT an f-string and NOT %-formatted: the bash body below is full of ${...}/$(...)
    # braces AND date %Y/%m/%d specifiers that either mechanism would try to interpret.
    # We interpolate the one dynamic value (the zstd thread cap) with a plain str.replace
    # of the __THREADS__ sentinel, which is immune to both braces and percents.
    return ("""\
#!/usr/bin/env bash
# shellcheck disable=SC2034
# Generated by `azzio mkazzioiso` -- the SSH variant built from the live system.

iso_name="azzio-headed-ssh"
iso_label="AZZIO_$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m)"
iso_publisher="michaelilgiaev <https://github.com/michaelilgiaev/azzio>"
iso_application="Azzio Installer/Azzio Linux Live/Rescue DVD"
iso_version="$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y.%m.%d)"
install_dir="arch"
buildmodes=('iso')
bootmodes=('bios.syslinux.mbr' 'bios.syslinux.eltorito' 'uefi-ia32.systemd-boot.esp' 'uefi-x64.systemd-boot.esp' 'uefi-ia32.systemd-boot.eltorito' 'uefi-x64.systemd-boot.eltorito')
arch="x86_64"
cow_spacesize="4G"
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'zstd' '-Xcompression-level' '15' '-processors' '__THREADS__')
bootstrap_tarball_compression=('zstd' '-c' '-T__THREADS__' '--long' '-19')
file_permissions=(
  ["/etc/shadow"]="0:0:400"
  ["/etc/gshadow"]="0:0:400"
  ["/etc/sudoers.d/00-main"]="0:0:440"
  ["/root"]="0:0:750"
)
""").replace("__THREADS__", str(threads))


def mkarchiso_argv(profile_dir: str, work_dir: str, out_dir: str) -> list[str]:
    """The mkarchiso invocation that assembles the ISO -- the same shape the recipe build
    uses: `mkarchiso -v -w work -o out profile`."""
    return ["mkarchiso", "-v", "-w", work_dir, "-o", out_dir, profile_dir]


def _current_packages() -> str:
    """The currently-installed explicit NATIVE packages (`pacman -Qqen`), sorted, newline-
    joined -- the packages.x86_64 body for the emitted ISO. Captures anything installed from a
    repo while live (foreign/AUR packages are excluded; pacstrap could not fetch them)."""
    out = subprocess.run(CURRENT_PACKAGES_CMD, capture_output=True, text=True, check=True).stdout
    pkgs = sorted(p for p in out.splitlines() if p.strip())
    return "\n".join(pkgs) + "\n"


def _seed_profile_from_releng(profile_dir: str) -> int:
    """Copy the on-system releng profile skeleton into profile_dir (boot dirs, pacman.conf,
    packages.x86_64, airootfs skeleton). Returns the cp exit code."""
    # cp the CONTENTS of releng into the (already-created) profile dir.
    return _sudo("cp", "-aT", RELENG_PROFILE, profile_dir, check=False)


def _overlay_live_state(profile_dir: str, work_dir: str) -> int:
    """rsync the boot-safe live-state into profile/airootfs/ on top of the pacstrap: the user
    TREES (OVERLAY_SOURCES) + the safe config DIRS (OVERLAY_ETC_DIRS) + ONLY the exact azzio
    /etc FILES (OVERLAY_ETC_FILES). It NEVER overlays whole /etc or whole /etc/systemd/system --
    that would clobber releng's archiso boot config (unbootable ISO) and inherit the host's
    service enable-state (a security regression). Enable-links are re-created separately by
    _enable_curated_services, mirroring the recipe. --relative pins each source under airootfs
    (/home -> airootfs/home, /etc/passwd -> airootfs/etc/passwd). -aAXH preserves perms/ACLs/
    xattrs/hardlinks. Missing sources are skipped. Returns the worst rsync exit code."""
    airootfs = os.path.join(profile_dir, "airootfs")
    _sudo("mkdir", "-p", airootfs, check=False)
    worst = 0
    for src in OVERLAY_SOURCES + OVERLAY_ETC_DIRS + OVERLAY_ETC_FILES:
        if not os.path.exists(src):
            continue
        cmd = ["rsync", "-aAXH", "--relative"]
        for pat in excludes_for(work_dir):
            cmd += ["--exclude", pat]
        cmd += [src, airootfs.rstrip("/") + "/"]
        rc = _sudo(*cmd, check=False)
        worst = worst or rc
    _enable_curated_services(airootfs)
    return worst


def _enable_curated_services(airootfs: str) -> None:
    """Create the recipe's CURATED multi-user.target.wants enable-links (ENABLE_LINKS) in the
    ISO -- so the live medium enables exactly the recipe's minimal daemon set, NOT whatever the
    host had enabled. This is what keeps the wholesale-/etc/systemd/system hazard closed: we
    copy the azzio unit FILES but author the enable-state ourselves."""
    wants = os.path.join(airootfs, "etc/systemd/system/multi-user.target.wants")
    _sudo("mkdir", "-p", wants, check=False)
    for target, name in ENABLE_LINKS:
        _sudo("ln", "-sf", target, os.path.join(wants, name), check=False)


def _overlay_sshd_variant(profile_dir: str, main_hash: str) -> None:
    """Overlay the sshd variant's differences onto the assembled profile: profiledef
    (iso_name=azzio-headed-ssh), /etc/shadow (main hashed, root locked), and the
    sshd-hypervisor auto-setup service + its multi-user.target.wants enable link."""
    _sudo_write(os.path.join(profile_dir, "profiledef.sh"), profiledef_sh())
    _sudo("chmod", "0755", os.path.join(profile_dir, "profiledef.sh"), check=False)

    etc = os.path.join(profile_dir, "airootfs", "etc")
    _sudo("mkdir", "-p", etc, check=False)
    _sudo_write(os.path.join(etc, "shadow"), shadow_for(main_hash))
    _sudo("chmod", "0600", os.path.join(etc, "shadow"), check=False)

    sysd = os.path.join(etc, "systemd", "system")
    _sudo("mkdir", "-p", os.path.join(sysd, "multi-user.target.wants"), check=False)
    _sudo_write(os.path.join(sysd, "sshd-hypervisor-setup.service"),
                SSHD_HYPERVISOR_SETUP_SERVICE)
    _sudo("ln", "-sf", "/etc/systemd/system/sshd-hypervisor-setup.service",
          os.path.join(sysd, "multi-user.target.wants", "sshd-hypervisor-setup.service"),
          check=False)


def cmd_mkazzioiso(args: list[str]) -> int:
    """`azzio mkazzioiso --ssh="<PASSWORD>" [--out DIR]` -- build the azzio-sshd ISO FROM
    the running system (captures packages installed while live). The --ssh password is
    REQUIRED (Method B only makes the SSH variant) and becomes `main`'s login credential,
    hashed into the ISO's /etc/shadow."""
    if args and args[0] in ("-h", "--help", "help"):
        print("Usage: azzio mkazzioiso --ssh=\"<PASSWORD>\" [--out DIR]\n\n"
              "  Build the azzio-sshd ISO FROM THE RUNNING SYSTEM (captures packages you\n"
              "  installed while live). --ssh sets the `main` login password in the emitted\n"
              "  ISO (hashed sha-512; root stays locked). --out DIR receives the .iso\n"
              "  (default: the current directory). Run with sudo.")
        return 0

    password = parse_ssh_arg(args)
    if not password:
        _err("azzio mkazzioiso: --ssh=\"<PASSWORD>\" is required (Method B only builds "
             "the SSH variant; no default password is ever shipped).")
        return 2

    for tool in ("mkarchiso", "rsync", "openssl", "pacman"):
        if not _have(tool):
            _err(f"azzio mkazzioiso: `{tool}` not found -- install it first "
                 "(archiso provides mkarchiso).")
            return 1

    out_dir = os.getcwd()
    for i, a in enumerate(args):
        if a == "--out" and i + 1 < len(args):
            out_dir = args[i + 1]
        elif a.startswith("--out="):
            out_dir = a.split("=", 1)[1] or out_dir

    try:
        main_hash = _hash_password(password)
    except (ValueError, RuntimeError) as e:
        _err(f"azzio mkazzioiso: {e}")
        return 1

    if not os.path.isdir(RELENG_PROFILE):
        _err(f"azzio mkazzioiso: the archiso releng profile is missing at "
             f"{RELENG_PROFILE} -- install `archiso` (mkarchiso needs a complete profile).")
        return 1

    work_dir = os.path.join("/var/tmp", "mkazzioiso.work")
    profile_dir = os.path.join(work_dir, "profile")
    mk_work = os.path.join(work_dir, "work")

    _sudo("rm", "-rf", work_dir, check=False)
    _sudo("mkdir", "-p", profile_dir, mk_work, out_dir, check=False)

    print(f"[*] mkazzioiso: seeding the archiso profile from {RELENG_PROFILE} ...")
    if _seed_profile_from_releng(profile_dir) != 0:
        _err("azzio mkazzioiso: could not copy the releng profile skeleton.")
        return 1

    print("[*] mkazzioiso: capturing the currently-installed packages (pacman -Qqe) ...")
    try:
        pkgs = _current_packages()
    except (OSError, subprocess.CalledProcessError) as e:
        _err(f"azzio mkazzioiso: could not list installed packages: {e}")
        return 1
    _sudo_write(os.path.join(profile_dir, "packages.x86_64"), pkgs)

    print("[*] mkazzioiso: overlaying the live system state (configs, home, /usr/local) ...")
    if _overlay_live_state(profile_dir, work_dir) != 0:
        _err("azzio mkazzioiso: overlaying the live state failed.")
        return 1

    print("[*] mkazzioiso: overlaying the SSH variant (hashed password, sshd auto-setup) ...")
    try:
        _overlay_sshd_variant(profile_dir, main_hash)
    except ValueError as e:
        _err(f"azzio mkazzioiso: {e}")
        return 1

    print("[*] mkazzioiso: running mkarchiso (rebuilds the rootfs + overlays + squashes) ...")
    rc = _sudo(*mkarchiso_argv(profile_dir, mk_work, out_dir), check=False)
    if rc != 0:
        _err(f"azzio mkazzioiso: mkarchiso failed (exit {rc}).")
        return 1

    print(f"[OK] azzio-headed-ssh ISO written to {out_dir} -- log in as `main` with your "
          "--ssh password.")
    return 0
