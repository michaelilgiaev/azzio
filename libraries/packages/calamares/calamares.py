"""Calamares installer configuration, authored as configuration-as-Python strings.

Azzio boots to a minimal OpenBox live session and auto-launches Calamares
(Manjaro-style) to install Azzio Linux to disk. Calamares 3.4.2 reads:

  /etc/calamares/settings.conf          -- module search paths + the sequence
  /etc/calamares/modules/<name>.conf    -- one configuration per module in the sequence
  /etc/calamares/branding/azzio/*      -- product branding + slideshow

Every builder below returns the exact text of one of those files. The install is
OFFLINE by design: the target root is unpacked from the live SquashFS by the
`unpackfs` module (NOT pacstrapped over the network), matching how the rest of
Azzio installs. Btrfs is the DEFAULT filesystem and full-disk LUKS encryption
is offered as a toggle in the partition page.

Style note: Calamares configuration files are YAML (settings.conf, branding.desc, and
every modules/*.conf). They are emitted verbatim as the strings below. The
`emit_map()` at the bottom returns {relative path under /etc/calamares -> content}
so compiler.py can iterate and write the whole tree with emit.write_text.

MODULE LAYOUT (this file is a thin FACADE). The individual builders live in focused
submodules and are re-exported here so the public surface stays flat -- callers use
`calamares.settings_conf()`, `calamares.users_conf()`, `calamares.BRANDING`, etc.
exactly as before, and never need to know which submodule a builder lives in:

  config_constants   BRANDING / PRODUCT / PRODUCT_ICON_FILE / ARCHISO_SFS (shared)
  config_settings    settings.conf (module search paths + the show/exec sequence)
  config_storage     partition / unpackfs / mount / fstab / luksbootkeyfile
  config_system      users / packages / locale / keyboard / services / initcpiocfg /
                     grubcfg / bootloader / finished (installed-system policy)
  config_branding    branding.desc + show.qml
  calamares_shellprocess  the post-unpackfs target fixups (own file; see below)

This mirrors how the rest of the codebase is organized (packages/azzio/ is one
package split into many small modules re-exported from its __init__), and keeps every
file well under the project's per-file line ceiling and single-purpose.

Calamares 3.4.x configuration-key notes (all VERIFIED against the calamares 3.4.2
module schemas we build from source -- these were bugs caught in review):
  - partition.conf: `defaultFileSystemType` (NOT defaultFileSystem) sets the
    default fs. LUKS is offered when `luksGeneration: luks2` is present with an
    encryption-capable install choice; the "Encrypt system" checkbox appears
    automatically. No `enableLuksAutomatedPartitioning` key is needed.
  - unpackfs.conf: sourcefs must be "squashfs" (with the airootfs.sfs path), not
    "filesystem" (which is not a recognized type). See ARCHISO_SFS.
  - The module is named `services-systemd` (its module.desc `name:` is
    "services-systemd", verified against the shipped 3.4.2 module). BOTH the exec
    sequence entry AND the per-module configuration file must use that exact name
    (services-systemd.conf) or Calamares aborts at startup with "Initialization
    Failed" (an unknown module name in the sequence stops the whole install).
    Its schema allows ONLY a `units:` array.
  - fstab.conf allows ONLY `crypttabOptions` + `tmpOptions` (tmpOptions required);
    real mount options come from the partition module / mount.conf.
  - grubcfg.conf `defaults:` requires GRUB_TIMEOUT + GRUB_DEFAULT; kernel args go
    in the top-level `kernel_params:` (defaults' GRUB_CMDLINE_LINUX_DEFAULT is
    overwritten by the module). `keep_distributor` is snake_case.
  - bootloader.conf is additionalProperties:false: kernel:/img:/fallback: are NOT
    valid keys (derived from the target automatically).
  - initcpiocfg + initcpio MUST be in the exec sequence or a LUKS/btrfs root is
    unbootable (the copied-from-live initramfs lacks the encrypt hook).
  - branding.desc style keys are Capitalized (SidebarBackground, ...).
  - The `sequence` lists ONLY modules configured below or needing none.
  - shellprocess: the OFFLINE install copies the live rootfs (which already has
    the `main` user, uid 1000, baked into /etc/passwd) via unpackfs. The `users`
    module then unconditionally runs `useradd -m -U -s /bin/bash -c <name> main`
    inside the target and ABORTS with exit code 9 ("user 'main' already exists")
    -- the users module has NO skip/reuse-existing-account option (verified
    against the 3.4.2 users.so). So a shellprocess step (dontChroot:false -> runs
    in the target chroot) drops the baked-in `main` account/group BEFORE `users`
    runs, letting the users module recreate `main` with the user-chosen password.
    Its home /home/main is left intact: Calamares' users module runs `useradd -m`
    unconditionally, which on an already-existing home merely WARNS ("home directory
    already exists ... not copying skel") and still exits 0 -- so the account is
    recreated and the files are reused, with no config key needed. (The `reuseHome`
    GlobalStorage flag the module can act on is set by the PARTITION module, never
    from users.conf; a users.conf `reuseHome` key is not in the schema and is
    ignored -- see users_conf().) shellprocess `script`
    is a list of command strings; a leading "-" ignores that command's failure so
    a variant rootfs (no such line) never aborts the install.
"""

from __future__ import annotations

# Shared constants (BRANDING, PRODUCT, PRODUCT_ICON_FILE, ARCHISO_SFS). Defined in
# config_constants so every builder submodule can import them without importing this
# facade (which would be circular). Re-exported so `calamares.BRANDING` etc. resolve.
from .config_constants import (  # noqa: F401  (re-exported for the public API)
    ARCHISO_SFS,
    BRANDING,
    PRODUCT,
    PRODUCT_ICON_FILE,
)

# The config-file builders, grouped by what part of the install they configure. Each
# returns the exact text of one emitted file. Re-exported here (see emit_map()).
from .config_settings import settings_conf  # noqa: F401  (re-exported)
from .config_storage import (  # noqa: F401  (re-exported)
    fstab_conf,
    luksbootkeyfile_conf,
    mount_conf,
    partition_conf,
    unpackfs_conf,
)
from .config_system import (  # noqa: F401  (re-exported)
    bootloader_conf,
    finished_conf,
    grubcfg_conf,
    initcpiocfg_conf,
    keyboard_conf,
    locale_conf,
    packages_conf,
    services_conf,
    users_conf,
)
from .config_branding import (  # noqa: F401  (re-exported)
    branding_desc,
    branding_show_qml,
)

# The shellprocess module (the post-unpackfs `main`-account removal + archiso
# mkinitcpio-preset reset) lives in its own file -- it is the most intricate part of
# the install. Re-exported here so the public surface stays flat:
# calamares.shellprocess_conf / .LIVE_USER / .STOCK_LINUX_PRESET, and the internal
# _mkinitcpio_reset_command the tests pin.
from .calamares_shellprocess import (  # noqa: F401  (re-exported for the public API)
    LIVE_USER,
    STOCK_LINUX_PRESET,
    _boot_desparsify_command,
    _mkinitcpio_reset_command,
    shellprocess_conf,
    shellprocess_desparsify_conf,
)


# --- 3b. modules/shellprocess.conf -----------------------------------------
# The shellprocess configuration (LIVE_USER, STOCK_LINUX_PRESET, _mkinitcpio_reset_command,
# shellprocess_conf) is defined in packages/calamares/calamares_shellprocess.py and imported
# above. It is emitted below via emit_map()'s shellprocess_conf().


# --- 8. emit map ------------------------------------------------------------
def emit_map() -> dict[str, str]:
    """Return {relative path under /etc/calamares -> file content} so compiler.py
    can iterate and write the whole configuration tree with emit.write_text, e.g.:

        for rel, content in calamares.emit_map().items():
            emit.write_text(airootfs / "etc/calamares" / rel, content)

    Every module named in the settings.conf `sequence` either has its configuration
    here or needs none (welcome, summary, finished, machineid, hwclock,
    networkcfg, umount, localecfg use built-in defaults).
    """
    return {
        "settings.conf": settings_conf(),
        "modules/partition.conf": partition_conf(),
        "modules/unpackfs.conf": unpackfs_conf(),
        "modules/shellprocess.conf": shellprocess_conf(),
        "modules/shellprocess-desparse.conf": shellprocess_desparsify_conf(),
        "modules/users.conf": users_conf(),
        "modules/packages.conf": packages_conf(),
        "modules/mount.conf": mount_conf(),
        "modules/fstab.conf": fstab_conf(),
        "modules/locale.conf": locale_conf(),
        "modules/keyboard.conf": keyboard_conf(),
        "modules/initcpiocfg.conf": initcpiocfg_conf(),
        "modules/luksbootkeyfile.conf": luksbootkeyfile_conf(),
        "modules/services-systemd.conf": services_conf(),
        "modules/grubcfg.conf": grubcfg_conf(),
        "modules/bootloader.conf": bootloader_conf(),
        "modules/finished.conf": finished_conf(),
        f"branding/{BRANDING}/branding.desc": branding_desc(),
        f"branding/{BRANDING}/show.qml": branding_show_qml(),
    }
