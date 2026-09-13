"""Calamares installed-system policy config builders.

The per-module configs that decide what the INSTALLED system looks like once the
files are on disk:
  - users.conf            account/hostname policy (wheel sudo, no autologin)
  - packages.conf         remove live-only packages (calamares) post-copy
  - locale.conf           locale/timezone defaults
  - keyboard.conf         English + optional region second layout
  - services-systemd.conf systemd units (NetworkManager on, bluetooth off)
  - initcpiocfg.conf      busybox HOOKS style for the encrypt hook
  - grubcfg.conf          /etc/default/grub (auto-boot first entry, cryptodisk)
  - bootloader.conf       GRUB install (UEFI + BIOS)
  - finished.conf         the Finish page "Restart now" option

Re-exported by the `calamares` facade as `calamares.users_conf`, etc.
"""

from __future__ import annotations


# --- 4. modules/users.conf --------------------------------------------------
def users_conf() -> str:
    """User/hostname policy on the INSTALLED system: wheel-group sudo, hostname
    settable in the UI, NO autologin (the live ISO autologins; the installed
    system should not).

    Every key here is one the shipped 3.4.2 users.so actually reads. The module does
    NOT validate this file against users.schema.yaml at runtime (an unknown key is
    silently dropped, a missing required key silently defaults), so three keys that
    LOOKED meaningful used to ride along here and did nothing:
      - `reuseHome`: Calamares only ever reads reuseHome from GlobalStorage, and only
        the PARTITION module writes it -- never this file. `useradd -m` already reuses
        the surviving /home/main on its own (it warns on an existing home and exits 0),
        so no config key is needed for the reuse to happen.
      - `setHostname`: only the `hostname` submap is read (Config.cpp getSubMap
        "hostname"); `setHostname` was a dead mirror.
      - top-level `userShell`: the shell is read from `user: { shell: ... }`, so a
        top-level spelling was ignored and the account fell back to the useradd default.
    They are gone; the shell now lives under `user:` where it is read.

    The Full Name ("What is your name?") field is HIDDEN (Azzio source patch in
    pkgbuild.calamares_defaults_patch): the account's GECOS/full name is not asked for.
    Hiding it required TWO coordinated source changes -- the field is hidden in UsersPage
    AND `Config::isReady()` no longer requires a non-empty full name -- because isReady()
    hard-requires a non-empty full name by default, so hiding the field WITHOUT relaxing
    isReady() leaves fullName empty forever and Next permanently greyed. The same patch
    RENAMES the four field-prompt labels to short captions -- "Username:", "Hostname:",
    "Username Password:", "Root Password:" -- re-words the reuse-password checkbox label
    to "Use username password for root password.", sets the hostname field's placeholder
    to "azzio" and the login field's placeholder to "main", and makes an empty username
    or hostname a required-field error ("User parameter must include at least one
    character." / "Hostname parameter must include at least two characters."), which shows
    the field error and disables Next until filled.
    Per PROMPT.md the login name IS SEEDED to "main" (the Username field DEFAULTS to
    containing "main", not merely hinting it) and the hostname is SEEDED to "azzio"; for
    each, its "main"/"azzio" placeholder is the fallback hint shown only if the field is
    cleared (in which case the required-field error appears and Next is blocked). Defaults,
    all Azzio: login "main", hostname "azzio", the user password empty (skippable -> a
    skipped/empty password becomes a LOCKED "*" account via the SetPasswordJob patch), and
    the reuse-password checkbox CHECKED (doReusePassword: true). The "Require strong
    passwords." checkbox is removed (allowWeakPasswords: false + the patch force-hides it)."""
    return """\
# User account configuration for the installed system.
---
# The created user's default groups. wheel drives sudo (see sudoersGroup).
defaultGroups:
    - wheel
    - audio
    - video
    - storage
    - network
    - lp
    - input
    - power

# The `user:` submap. The shell is read ONLY from here (top-level `userShell` is not a
# schema key and is silently ignored), so the created account gets /bin/bash.
user:
    shell: /bin/bash

# autologinGroup is a schema-REQUIRED key (the group added to the user when autologin is
# on). doAutologin is false on the installed system, so this group is never actually
# applied -- but the key must be present for the file to match the module's contract.
autologinGroup: autologin

# Grant sudo to members of this group (a /etc/sudoers.d/10-installer drop-in is
# written enabling it).
sudoersGroup: wheel
setRootPassword: true

# doReusePassword: true makes the "Use the same password for the administrator account."
# checkbox on the users page START CHECKED (UsersPage seeds the checkbox from Config's
# m_reuseUserPasswordForRoot, which this key sets). So by default root reuses the
# user's password: the user fills ONE password box and root mirrors it. With the user
# password left empty (the Azzio default -- the field is skippable), root reuses that
# empty password and Calamares' SetPasswordJob LOCKS the account (usermod -p '!'),
# which our source patch broadens from root-only to any empty password -- so a skipped
# password yields a locked "*" account rather than a passwordless login. The prompt's
# "root password boolean must default to True (checkbox checked)" maps to this key.
doReusePassword: true

# Autologin OFF on the installed system (live ISO autologins, installed does not).
doAutologin: false

# Let the user pick the hostname on the users page, seeded with this template.
# writeHostsFile keeps /etc/hosts in sync with the chosen name.
#
# `template: "azzio"` is a LITERAL (no ${...} macros), so Calamares' hostname
# suggestion always expands to exactly "azzio" no matter what the user types in
# the Full Name / Login fields. Combined with our calamares source patch
# (azzio-calamares-defaults.patch), which seeds this template as the INITIAL
# hostname at module load AND marks it "custom" so the auto-derive path is
# skipped, the hostname field (relabelled "Hostname:") shows "azzio" by
# default and stays "azzio" as the other inputs change. (Upstream default is
# "${first}-${product}", which recomputes the hostname on every name keystroke --
# that reactive default is exactly what the patch/template override disables.)
# `location: EtcFile` is the schema's enum spelling (enum: [None, EtcFile, Hostnamed,
# Transient]); the runtime lookup is case-insensitive so it resolves to EtcHostname.
hostname:
    location: EtcFile
    writeHostsFile: true
    template: "azzio"

# Password policy. NO `passwordRequirements` block ON PURPOSE: the user password is
# SKIPPABLE (the PROMPT wants it to default empty and, if skipped, become a locked "*"
# account). A `passwordRequirements.minLength: 1` would register a length check that
# marks an EMPTY password Invalid -> Config::isReady() would block Next and the field
# could not be skipped. With no checks registered, passwordStatus() returns Valid for
# ANY password including empty, so an empty password never blocks Next. The locking of
# a skipped (empty) password is done by our SetPasswordJob source patch (usermod -p '!').
#
# allowWeakPasswords:false HIDES the "Require strong passwords." checkbox (UsersPage only
# shows it when permitWeakPasswords() is true). The PROMPT wants that checkbox removed;
# our source patch ALSO force-hides it, so this is belt-and-suspenders. Because no
# password checks are configured, there is nothing to enforce anyway -- empty/weak
# passwords still pass. allowWeakPasswordsDefault is irrelevant with the box hidden.
allowWeakPasswords: false
allowWeakPasswordsDefault: false
"""


# --- 5. modules/packages.conf ----------------------------------------------
def packages_conf() -> str:
    """Pacman backend used ONLY to remove live-only packages from the installed
    target after the filesystem copy. calamares itself and the live desktop-
    installer glue have no place on the installed system, so we drop them. No
    network install happens (unpackfs already populated the root)."""
    return """\
# Post-install package cleanup (remove live-only bits). Pacman backend.
---
backend: pacman

pacman:
    # Do not refresh/sync from the network on the installed target; we only
    # remove the live-only packages copied over from the ISO.
    disable_download_timeout: true
    num_retries: 0

# skip_if_no_internet keeps this from failing an offline install if a later
# online operation were ever added.
skip_if_no_internet: false
update_db: false
update_system: false

# Operations run against the target after unpackfs. We only remove the INSTALLER
# itself (calamares has no place on an installed system); the desktop (openbox,
# xorg, kitty, librewolf, ...) is KEPT so the installed system boots to the same
# graphical environment as the live medium. Nothing is installed over the network.
# `try_remove` (not `remove`) so an absent package does not fail the step.
operations:
    - try_remove:
        - calamares
"""


# --- 6c. modules/locale.conf ------------------------------------------------
def locale_conf() -> str:
    """Locale/timezone selection defaults for the installed system."""
    return """\
# Locale + timezone defaults (user can change these on the locale page).
---
# Seed timezone. Azzio defaults to Asia/Jerusalem; the locale page can still
# override it. (IANA zone name is "Jerusalem".)
region: "Asia"
zone: "Jerusalem"

# Where the keyboard/locale live in the target.
localeConfMappings:
    - LANG
    - LC_ALL
"""


# --- 6c2. modules/keyboard.conf --------------------------------------------
def keyboard_conf() -> str:
    """Keyboard page: English ("us") is always the active layout; when the user
    picks a NON-English region on the Location page, the region's native layout is
    added as a switchable SECOND (Alt+Shift), live in the installer and persisted to
    the target. This is driven by the Azzio region-keyboard SOURCE PATCH
    (packages/pkgbuild.calamares_region_keyboard_patch), enabled by the
    `regionSecondLayout: true` key below.

    HOW IT WORKS (and why guessLayout is now TRUE, reversing the earlier fix):
      * The patched locale module publishes the selected zone's ISO-3166 country
        code to GlobalStorage as "locationCountry".
      * On Keyboard-page activation, the patched keyboard module's
        guessRegionKeyboardLayout() reads "locationCountry", maps it to the region's
        xkb layout (its own table, covering Latin-script langs like Spanish/French
        that upstream's non-ascii-layouts does NOT), makes the region layout the
        PRIMARY with "us" force-added as the ADDITIONAL layout, and applies it live
        -- so the emitted order is "us,<region>" (English first/active) and the
        "Type here to test" box switches scripts on Alt+Shift. English-speaking
        regions (US/GB/AU/...) get English only.
      * `guessLayout: true` is REQUIRED for guessLocaleKeyboardLayout() (which the
        patch extends) to run at all -- it early-returns when guessLayout is false.
        The earlier "keep us, never guess" fix (guessLayout:false) is superseded:
        the guess no longer produces a lone non-ASCII layout (the old Hebrew-only,
        blank-key bug) because English is always force-kept as the primary/active
        ASCII layout; the region language is only ever the SECOND, Alt+Shift layout.

    Default region is Asia/Jerusalem (modules/locale.conf), so out of the box the
    installer shows English + Hebrew with Alt+Shift. Move the region to
    America/El_Salvador and it becomes English + Spanish; Asia/Riyadh -> English +
    Arabic; an English-speaking region -> English only.

    useLocale1:false keeps the module reading/writing the plain
    /etc/X11/xorg.conf.d/00-keyboard.conf (Azzio is OpenBox/X11); the `configure`
    block leaves the per-DE keyboard integrations off (the layout is read from that xkb
    file directly, so none of them is needed)."""
    return """\
# Keyboard configuration for the Azzio installer.
---
# Where to write the X11 keyboard configuration on the target (systemd-localed default).
xOrgConfFileName: "/etc/X11/xorg.conf.d/00-keyboard.conf"

# Path used to convert X11 keymaps to kbd format for the console.
convertedKeymapPath: "/usr/share/kbd/keymaps/xkb"

# Manage the plain xorg.conf.d file directly instead of going through
# systemd-localed. Azzio is OpenBox/X11 and the layout is read from
# /etc/X11/xorg.conf.d/00-keyboard.conf.
useLocale1: false

# Enable the locale/region guess. REQUIRED so the Azzio region-keyboard patch's
# guessRegionKeyboardLayout() runs (guessLocaleKeyboardLayout() early-returns when
# this is false). It no longer auto-selects a lone Hebrew layout: English is always
# force-kept as the primary/active layout and the region language is only ever the
# switchable SECOND layout (see regionSecondLayout).
guessLayout: true

# Azzio: region-driven second keyboard layout. When the user selects a non-English
# region on the Location page, add that region's native xkb layout as a switchable
# SECOND layout (English "us" stays first/active; group switch is Alt+Shift), applied
# to the LIVE installer session and persisted to the target. English-speaking regions
# get English only. Implemented by calamares_region_keyboard_patch(); this key is the
# opt-in switch it reads (upstream/other distros default it to false).
regionSecondLayout: true

# Azzio runs OpenBox on X11, and the layout is read from the plain xkb
# xorg.conf.d file we manage (useLocale1:false) -- so none of Calamares' per-DE
# keyboard integrations need configuring here (both left off below).
configure:
    kwin: false
    gnome: false
"""


# --- 6d. modules/services.conf ---------------------------------------------
def services_conf() -> str:
    """Enable NetworkManager on the installed system (Azzio networks via NM,
    not dhcpcd/systemd-networkd), and DISABLE bluetooth (off by default -- matches the
    live ISO, where compiler._link_services leaves bluetooth.service out of
    multi-user.target.wants; `azzio network bluetooth on` turns it on on demand).

    The archiso stock systemd-networkd/systemd-resolved stack is NOT disabled here:
    it is MASKED (and its /etc/systemd/network/*.network + resolv.conf stub removed)
    in the airootfs by compiler._neutralize_archiso_network_stack, which unpackfs then
    carries onto the installed target. A mask beats a services-systemd `disable` action
    (which a lingering .socket / dbus-activation / preset can defeat) and needs no
    per-target step -- so this module only asserts the POSITIVE side (NetworkManager on).
    Without that neutralization networkd would RACE NetworkManager for every interface
    and a static-IPv4 install would come up on a networkd DHCP lease instead (the
    "ip/subnet mask not applied" install bug).

    NOTE: in Calamares 3.4.2 this module's real name is `services-systemd` (its
    module.desc `name:` field, verified against the installed module). The configuration
    file must therefore be modules/services-systemd.conf and the exec-sequence
    entry must read `services-systemd`; using the bare `services` makes Calamares
    fail to find the module and abort at startup. The schema is
    additionalProperties:false and defines ONLY a `units:` array of
    {name, action, mandatory} -- the older `services:`/`targets:`/`disable:` keys
    are rejected by validation."""
    return """\
# systemd unit state applied to the installed system.
---
units:
    - name: NetworkManager
      mandatory: true
    - name: bluetooth
      action: disable
      mandatory: false
    - name: cups
      mandatory: false
"""


# --- 6d2. modules/initcpiocfg.conf -----------------------------------------
def initcpiocfg_conf() -> str:
    """Configure the target's /etc/mkinitcpio.conf before `initcpio` runs
    mkinitcpio -P. Calamares' initcpiocfg module INJECTS the encryption/btrfs/lvm
    hooks it needs based on the chosen layout, which is what makes a LUKS-encrypted
    or btrfs root actually bootable -- without regenerating the initramfs with the
    `encrypt` hook, an encrypted root cannot be unlocked at boot.

    We set only `useSystemdHook: false` -- the VALID initcpiocfg key that keeps the
    classic busybox-based HOOKS layout (the `encrypt` hook, not sd-encrypt), which
    matches the archiso live initramfs and GRUB's cryptodisk unlock we configure in
    grubcfg. The layout-driven hook injection happens regardless. NOTE: an earlier
    version emitted `kernel: ""` here -- that is an `initcpio`-module key, NOT an
    initcpiocfg key (whose schema is additionalProperties:false), so it was silently
    ignored and would fail strict schema validation. It is removed. (initcpio itself
    also needs no configuration.)"""
    return """\
# initcpiocfg configuration for the installed system. Calamares injects the
# encrypt/lvm2/btrfs hooks required by the selected partition layout; we only pin
# the busybox (non-systemd) hook style so the `encrypt` hook + GRUB cryptodisk
# unlock line up with the rest of the install.
---
useSystemdHook: false
"""


# --- 6e. modules/grubcfg.conf ----------------------------------------------
def grubcfg_conf() -> str:
    """Write /etc/default/grub before the bootloader module runs grub-install +
    grub-mkconfig. Enables cryptodisk so a LUKS-encrypted root can be unlocked
    by GRUB at boot, and boots straight into the first menu entry with no wait.

    AUTO-BOOT the first option (the user's request "GRUB automatically goes into
    the first option during boot"):
      * GRUB_DEFAULT: 0        -- select the FIRST generated menu entry. (Was
        "saved", which boots whatever grub-reboot/last-boot recorded -- a moving
        target with no GRUB_SAVEDEFAULT set; pinning 0 always picks the top entry.)
      * GRUB_TIMEOUT: 0        -- do not wait; boot the default immediately.
      * GRUB_TIMEOUT_STYLE: "hidden" -- show no menu at all before booting (with a
        0 timeout "menu" would still flash the list for a frame; "hidden" goes
        straight in, and the user can still hold SHIFT/ESC to reveal the menu)."""
    return """\
# /etc/default/grub contents written before grub-install / grub-mkconfig.
---
overwrite: true

# Key/value pairs merged into /etc/default/grub. Schema requires GRUB_TIMEOUT and
# GRUB_DEFAULT. GRUB_ENABLE_CRYPTODISK is set automatically by the module when a
# crypt device is present, but we set it explicitly too (harmless).
# GRUB_DEFAULT 0 + GRUB_TIMEOUT 0 + hidden style == boot the first entry at once.
defaults:
    GRUB_TIMEOUT: 0
    GRUB_DEFAULT: 0
    GRUB_TIMEOUT_STYLE: "hidden"
    GRUB_DISTRIBUTOR: "Azzio Linux"
    GRUB_ENABLE_CRYPTODISK: "y"

# Kernel command line. The module OVERWRITES GRUB_CMDLINE_LINUX_DEFAULT with the
# kernel_params list below (setting it inside `defaults:` would be clobbered), so
# put boot args here.
kernel_params: [ "quiet" ]

# Keep the distributor string above (snake_case is the real key; camelCase is
# silently ignored).
keep_distributor: true
"""


# --- 6f. modules/bootloader.conf -------------------------------------------
def bootloader_conf() -> str:
    """Bootloader install. GRUB on both UEFI and BIOS (matches grubcfg + the
    on-disk installer's grub-install flow). efiBootloaderId names the EFI entry."""
    return """\
# Bootloader installation (GRUB, UEFI + BIOS).
---
# efi | bios | none ; grub selects GRUB for both firmware types.
efiBootLoader: "grub"

# NOTE: the ESP mount point is NOT set here. The bootloader module reads it from
# globalstorage (populated by the partition module from partition.conf's
# efiSystemPartition) -- the bootloader schema does not define an efiSystemPartition
# key, so setting one here is a dead key. partition.conf already supplies /boot/efi.

# Names for the GRUB EFI boot entry and its install directory.
efiBootloaderId: "azzio"

# Install GRUB even if an existing entry is present.
installEFIFallback: true

# BIOS/GRUB target names.
grubInstall: "grub-install"
grubMkconfig: "grub-mkconfig"
grubCfg: "/boot/grub/grub.cfg"
grubProbe: "grub-probe"
# NOTE: kernel/initramfs paths are NOT set here -- the bootloader schema is
# additionalProperties:false and derives them from the target automatically.
# Adding kernel:/img:/fallback: keys would fail schema validation.
"""


# --- 6g. modules/finished.conf ---------------------------------------------
def finished_conf() -> str:
    """The Finish ("All done.") page. Without this configuration the page shows only a
    bare "Done" button and cannot restart into the new system -- the user asked for
    a Reboot option there. `restartNowMode: user-unchecked` shows a "Restart now"
    checkbox (defaulting to unchecked, so it never reboots unexpectedly); when the
    user ticks it and clicks Done, Calamares runs restartNowCommand.

    restartNowCommand uses `systemctl -i reboot` (the module's own documented value):
    `-i` (--ignore-inhibitors) guarantees the reboot proceeds even if a session
    inhibitor is held. We do NOT enable notifyOnFinished (the installer runs as root
    via pkexec and cannot reliably reach the live user's session bus). Schema is
    additionalProperties:false; only restartNowMode/restartNowCommand/
    restartNowChecked/restartNowEnabled/notifyOnFinished are valid keys."""
    return """\
# Finish page: offer a "Restart now" option so the user can boot straight into the
# freshly installed system (unchecked by default -- never reboots unless ticked).
---
restartNowMode: user-unchecked
restartNowCommand: "systemctl -i reboot"
notifyOnFinished: false
"""
