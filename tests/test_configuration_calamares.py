"""packages.calamares -- the Calamares 3.4.2 installer configuration tree.

Every builder here returns the verbatim YAML text of one file Calamares reads at
runtime. Python never parses these strings, so a wrong filename, a clobbered exec
name, a camelCase key where the schema wants snake_case, or a key the schema
rejects (additionalProperties:false) produces a configuration that TYPE-CHECKS fine in
Python but makes Calamares abort at startup with "Initialization Failed" or
silently ignore a setting. Nothing in the build catches it -- the ISO builds,
boots, and only dies when a user clicks Install. These tests are the only place
those literal contracts are checked, so they parse the emitted YAML and assert
the exact keys/values/filenames the shipped Calamares schemas require.
"""

from __future__ import annotations

import re

import pytest
import yaml

from packages.calamares import calamares


# The files Calamares reads, relative to /etc/calamares. Any drift here means
# a module in the sequence has no configuration (or an orphan configuration exists).
EXPECTED_FILES = {
    "settings.conf",
    "modules/partition.conf",
    "modules/unpackfs.conf",
    "modules/shellprocess.conf",
    "modules/shellprocess-desparse.conf",
    "modules/users.conf",
    "modules/packages.conf",
    "modules/mount.conf",
    "modules/fstab.conf",
    "modules/locale.conf",
    "modules/keyboard.conf",
    "modules/initcpiocfg.conf",
    "modules/luksbootkeyfile.conf",
    "modules/services-systemd.conf",
    "modules/grubcfg.conf",
    "modules/bootloader.conf",
    "modules/finished.conf",
    "branding/azzio/branding.desc",
    "branding/azzio/show.qml",
}


def _settings_exec_list() -> list:
    """Return the ordered `exec` module names from settings.conf."""
    doc = yaml.safe_load(calamares.settings_conf())
    for phase in doc["sequence"]:
        if "exec" in phase:
            return phase["exec"]
    raise AssertionError("no exec phase in settings.conf sequence")


def _settings_show_list() -> list:
    """Return the union of all `show` module names from settings.conf."""
    doc = yaml.safe_load(calamares.settings_conf())
    names: list = []
    for phase in doc["sequence"]:
        if "show" in phase:
            names.extend(phase["show"])
    return names


# --- emit_map shape ---------------------------------------------------------

def test_emit_map_has_exactly_expected_files():
    m = calamares.emit_map()
    assert set(m) == EXPECTED_FILES
    assert len(m) == len(EXPECTED_FILES) == 19


def test_emit_map_values_are_nonempty_strings():
    # An import-time f-string ValueError or an accidental None return would show
    # up here before it ever reached disk.
    for rel, content in calamares.emit_map().items():
        assert isinstance(content, str) and content.strip(), rel


# --- the fatal filename guard ----------------------------------------------

def test_services_filename_is_services_systemd():
    m = calamares.emit_map()
    assert "modules/services-systemd.conf" in m
    assert "modules/services.conf" not in m


def test_services_conf_wired_to_right_path():
    # The services-systemd.conf slot must carry services_conf()'s text, not some
    # other module's, or NetworkManager is never enabled on the installed system.
    assert calamares.emit_map()["modules/services-systemd.conf"] == calamares.services_conf()


def test_services_conf_schema_only_units():
    doc = yaml.safe_load(calamares.services_conf())
    assert set(doc) == {"units"}
    names = {u["name"] for u in doc["units"]}
    assert names == {"NetworkManager", "bluetooth", "cups"}
    nm = next(u for u in doc["units"] if u["name"] == "NetworkManager")
    assert nm["mandatory"] is True
    # Bluetooth is OFF by default on the installed system too (matches the live ISO):
    # the unit is present but with action=disable so Calamares disables it on the target.
    bt = next(u for u in doc["units"] if u["name"] == "bluetooth")
    assert bt["action"] == "disable"


def test_services_conf_does_not_touch_sshd_auto_setup():
    # The ssh headed variant enables sshd-hypervisor-setup in the airootfs, which unpackfs
    # copies verbatim onto the installed system -- so the INSTALLED ssh headed system runs the
    # sshd bring-up at first boot too (ssh in both live AND installed, as the user asked).
    # Calamares' services-systemd config must therefore NOT disable it (nor stock sshd);
    # a stray disable here would silently kill ssh on the installed ssh headed system.
    doc = yaml.safe_load(calamares.services_conf())
    names = {u["name"] for u in doc["units"]}
    assert "sshd-hypervisor-setup" not in names
    assert "sshd" not in names and "ssh" not in names


# --- settings.conf sequence -------------------------------------------------

def test_settings_sequence_uses_services_systemd():
    execs = _settings_exec_list()
    assert "services-systemd" in execs
    assert "services" not in execs


def test_settings_exec_ordering_constraints():
    execs = _settings_exec_list()
    # partition must format before anything is mounted or unpacked onto it.
    assert execs.index("partition") < execs.index("mount") < execs.index("unpackfs")
    # initcpiocfg writes HOOKS, initcpio regenerates the initramfs, then the
    # bootloader is installed -- get this wrong and a LUKS/btrfs root is unbootable.
    assert execs.index("initcpiocfg") < execs.index("initcpio") < execs.index("bootloader")
    assert execs.index("grubcfg") < execs.index("bootloader")


def test_network_page_in_show_sequence_before_users():
    # The Azzio "Network" page (networkq QML view module, added by the
    # azzio-calamares-networkq source patch) is shown immediately BEFORE `users` (per the
    # user's request), so the page order reads partition -> network -> user account ->
    # summary. It is a `show` (UI) step, not an exec step. Show-order is independent of the
    # exec order: `networkcfg` still runs at exec time regardless of where the page sits.
    show = _settings_show_list()
    assert "networkq" in show
    assert show.index("partition") < show.index("networkq") < show.index("users")
    assert show.index("users") < show.index("summary")
    # Immediately before users -- nothing between them in the show sequence.
    assert show.index("networkq") + 1 == show.index("users")


def test_network_static_job_runs_at_exec_after_users():
    # The target-side write is done by the STOCK `networkcfg` exec job (patched to also
    # emit a static profile from the page's GlobalStorage values). It must already be in
    # the exec sequence, after `users` (the page ran) -- we do NOT add a second job.
    execs = _settings_exec_list()
    assert "networkcfg" in execs
    assert execs.index("users") < execs.index("networkcfg")
    # And there is exactly one networkcfg entry (no accidental duplicate job).
    assert execs.count("networkcfg") == 1


def test_networkq_page_ships_no_emitted_conf():
    # networkq is a compiled-in view module whose networkq.conf is shipped INSIDE the
    # calamares package (by the source patch), NOT emitted under /etc/calamares by
    # emit_map(). So it is deliberately absent from emit_map() -- which is why the page
    # does not appear in EXPECTED_FILES and the emit-map count stays 19. This pins that
    # intent so a future change that adds a stray modules/networkq.conf is caught.
    assert "modules/networkq.conf" not in calamares.emit_map()


def test_luksbootkeyfile_runs_before_fstab_and_initcpiocfg():
    # The double-password fix: luksbootkeyfile creates /crypto_keyfile.bin +
    # luksAddKey. It MUST run before fstab (which points crypttab at the keyfile
    # only if it exists) and before initcpiocfg (which adds the keyfile to
    # mkinitcpio FILES= only if it exists). It also must run after unpackfs (the
    # target root must exist to write the keyfile onto).
    execs = _settings_exec_list()
    assert "luksbootkeyfile" in execs
    assert execs.index("unpackfs") < execs.index("luksbootkeyfile")
    assert execs.index("luksbootkeyfile") < execs.index("fstab")
    assert execs.index("luksbootkeyfile") < execs.index("initcpiocfg")


def test_luksbootkeyfile_conf_schema():
    # The module's only valid key is luks2Hash. Azzio uses LUKS1 so it is inert,
    # but it must parse and carry a recognized value.
    doc = yaml.safe_load(calamares.luksbootkeyfile_conf())
    assert set(doc) == {"luks2Hash"}
    assert doc["luks2Hash"] in ("default", "pbkdf2", "argon2i", "argon2id")


def _instance_config_stems() -> dict:
    """Map a custom-instance configuration STEM (e.g. 'shellprocess-desparse') to the
    `module@id` token it is used as in the sequence (e.g. 'shellprocess@desparse'),
    read from settings.conf's `instances:` block. Lets the orphan check recognise a
    per-instance configuration file whose name does not equal any bare module name.

    The per-instance key is `config` (what Calamares' Settings.cpp actually reads);
    an entry missing it silently defaults to `<module>.conf`, so a `configuration:`
    typo makes the instance run the WRONG config (this once disabled the boot fix)."""
    doc = yaml.safe_load(calamares.settings_conf())
    out = {}
    for inst in doc.get("instances", []):
        cfg = inst["config"]
        stem = cfg[:-len(".conf")] if cfg.endswith(".conf") else cfg
        out[stem] = f"{inst['module']}@{inst['id']}"
    return out


def test_instances_use_config_key_not_configuration():
    # REGRESSION GUARD for the boot bug that shipped TWICE: the per-instance config
    # filename key MUST be `config`. Calamares Settings.cpp reads m.value("config");
    # if it is misspelled `configuration` (or anything else) Calamares does NOT error
    # -- the instance's config filename SILENTLY defaults to `<module>.conf`, so
    # `shellprocess@desparse` re-runs the DEFAULT shellprocess.conf (mkinitcpio reset)
    # instead of the /boot de-compress/de-sparsify, and the installed system fails to
    # boot with "premature end of file /@/boot/vmlinuz-linux". Assert every instance
    # entry uses `config` and never the silent-default-inducing `configuration`.
    doc = yaml.safe_load(calamares.settings_conf())
    instances = doc.get("instances", [])
    assert instances, "expected at least the shellprocess@desparse instance"
    for inst in instances:
        assert "config" in inst, (
            f"instance {inst.get('id')!r} is missing the `config:` key -- Calamares "
            f"would default its config to {inst.get('module')}.conf and run the wrong "
            f"commands"
        )
        assert "configuration" not in inst, (
            f"instance {inst.get('id')!r} uses the WRONG key `configuration:` -- "
            f"Calamares reads `config:`; this silently disables the instance's real "
            f"config (the exact boot-fix regression)"
        )
    # And specifically: each custom instance must point at its own conf file.
    by_id = {i["id"]: i for i in instances}
    assert by_id["desparse"]["config"] == "shellprocess-desparse.conf"


def test_configured_modules_referenced_in_sequence():
    # Every modules/<x>.conf we emit must name a module (or a declared instance)
    # that actually appears in the settings.conf sequence (show or exec). An orphan
    # configuration is dead weight; a missing one means a configured module never runs.
    seq_names = set(_settings_exec_list()) | set(_settings_show_list())
    inst = _instance_config_stems()
    for rel in calamares.emit_map():
        if rel.startswith("modules/") and rel.endswith(".conf"):
            stem = rel[len("modules/"):-len(".conf")]
            # A per-instance configuration (shellprocess-desparse.conf) is referenced via
            # its module@id token (shellprocess@desparse); a plain configuration via its
            # bare module name.
            if stem in inst:
                assert inst[stem] in seq_names, inst[stem]
            else:
                assert stem in seq_names, stem


# --- partition.conf ---------------------------------------------------------

def test_partition_filesystem_key_spelling():
    d = yaml.safe_load(calamares.partition_conf())
    # Calamares 3.4.x uses defaultFileSystemType; the old defaultFileSystem is a
    # dead key that leaves the default silently wrong.
    assert d["defaultFileSystemType"] == "btrfs"
    assert "defaultFileSystem" not in d
    assert d["availableFileSystemTypes"][0] == "btrfs"
    # luks1, NOT luks2: /boot is on the encrypted btrfs root, so GRUB must unlock
    # the container, and GRUB <= 2.12 cannot open LUKS2 + Argon2id (cryptsetup's
    # luks2 default). luks1 is PBKDF2 and GRUB-openable. This + the luksbootkeyfile
    # module is the "password twice" fix; a drift back to luks2 would break the
    # GRUB unlock. (Matches upstream Calamares' own default.)
    assert d["luksGeneration"] == "luks1"


def test_partition_btrfs_subvolumes():
    d = yaml.safe_load(calamares.partition_conf())
    pairs = {(s["mountPoint"], s["subvolume"]) for s in d["btrfsSubvolumes"]}
    assert ("/", "/@") in pairs
    assert ("/home", "/@home") in pairs


def test_partition_supplies_efi_system_partition():
    # The ESP mount point lives HERE (bootloader.conf reads it from globalstorage),
    # so partition.conf must be the one that sets it.
    d = yaml.safe_load(calamares.partition_conf())
    assert d["efiSystemPartition"] == "/boot/efi"


# --- mount.conf -------------------------------------------------------------

def _extra_mount_points(doc) -> set:
    return {m["mountPoint"] for m in doc["extraMounts"]}


def test_mount_binds_the_standard_pseudo_filesystems():
    # proc/sys/dev/run must all be mounted into the target chroot so the
    # bootloader + initcpio jobs (which run chrooted) can see the running kernel's
    # interfaces. A missing one silently breaks a chrooted command.
    d = yaml.safe_load(calamares.mount_conf())
    pts = _extra_mount_points(d)
    assert {"/proc", "/sys", "/dev", "/run"} <= pts


def test_mount_includes_efivarfs_for_uefi_grub_install():
    # THE bootloader-install fix. grub-install (UEFI) shells out to efibootmgr to
    # register the NVRAM boot entry, and efibootmgr needs efivarfs mounted RW at
    # /sys/firmware/efi/efivars INSIDE the target chroot. A fresh sysfs mount does
    # NOT carry the efivarfs submount, so without an explicit entry grub-install
    # dies with "EFI variables are not supported on this system" /
    # "efibootmgr failed to register the boot entry" and Calamares aborts at the
    # bootloader step. This asserts the explicit efivarfs extraMount exists.
    d = yaml.safe_load(calamares.mount_conf())
    efivars = [m for m in d["extraMounts"]
               if m.get("mountPoint") == "/sys/firmware/efi/efivars"]
    assert efivars, "mount.conf must mount efivarfs for UEFI grub-install"
    entry = efivars[0]
    assert entry["fs"] == "efivarfs"
    assert entry["device"] == "efivarfs"


def test_mount_efivarfs_is_efi_only():
    # The entry MUST carry `efi: true` so Calamares' mount module drops it on a
    # legacy-BIOS install (where /sys/firmware/efi does not exist). Without this
    # flag a BIOS install still boots fine but logs a spurious "Cannot mount
    # efivarfs" warning; with it the efivarfs mount is a clean no-op off UEFI.
    # (Calamares sorts extraMounts lexically by mountPoint at runtime, so list
    # ORDER is irrelevant -- /sys sorts before /sys/firmware/... regardless and
    # the mountpoint dir is created on demand; the efi flag is the real contract.)
    d = yaml.safe_load(calamares.mount_conf())
    entry = next(m for m in d["extraMounts"]
                 if m.get("mountPoint") == "/sys/firmware/efi/efivars")
    assert entry.get("efi") is True


# --- unpackfs.conf ----------------------------------------------------------

def test_unpackfs_source_and_sourcefs():
    d = yaml.safe_load(calamares.unpackfs_conf())
    entry = d["unpack"][0]
    assert entry["source"] == calamares.ARCHISO_SFS
    assert entry["sourcefs"] == "squashfs"
    assert entry["destination"] == ""
    # Proves the f-string actually interpolated the constant into the text.
    assert calamares.ARCHISO_SFS in calamares.unpackfs_conf()


def test_archiso_sfs_path_literal():
    assert calamares.ARCHISO_SFS == "/run/archiso/bootmnt/arch/x86_64/airootfs.sfs"


# --- shellprocess.conf (the "user already exists" + archiso-preset fixes) ----

def test_shellprocess_runs_between_unpackfs_and_its_dependents():
    # The whole point: both fixups run AFTER the target exists (unpackfs) but
    # BEFORE the modules that depend on them -- `users` (account recreation) and
    # `initcpiocfg`/`initcpio` (mkinitcpio -P). Order is load-bearing.
    execs = _settings_exec_list()
    assert "shellprocess" in execs
    assert execs.index("unpackfs") < execs.index("shellprocess") < execs.index("users")
    assert execs.index("shellprocess") < execs.index("initcpiocfg") < execs.index("initcpio")


def test_shellprocess_conf_schema_and_chroot():
    d = yaml.safe_load(calamares.shellprocess_conf())
    # Runs INSIDE the target chroot so it edits the target's databases/config.
    assert d["dontChroot"] is False
    # script is a list of command strings (the shellprocess CommandList form).
    assert isinstance(d["script"], list)
    assert all(isinstance(c, str) for c in d["script"])


def _userdel_commands(script: list) -> list:
    """The two account-removal commands (userdel/groupdel), which are the ones
    prefixed '-' to be non-fatal. The mkinitcpio-reset command is separate."""
    return [c for c in script if "userdel" in c or "groupdel" in c]


def test_shellprocess_deletes_the_live_user_non_fatally():
    d = yaml.safe_load(calamares.shellprocess_conf())
    script = d["script"]
    # It must delete the `main` account (userdel), and the account-removal commands
    # are prefixed "-" so a rootfs lacking the account/group never aborts install.
    assert calamares.LIVE_USER == "main"
    assert f"-userdel -f {calamares.LIVE_USER}" in script
    assert f"-groupdel {calamares.LIVE_USER}" in script
    removal = _userdel_commands(script)
    assert len(removal) == 2
    for cmd in removal:
        assert cmd.startswith("-"), cmd
    # It must NOT remove the home directory (reuseHome relies on /home/main staying).
    joined = "\n".join(removal)
    assert "--remove" not in joined
    assert "userdel -r" not in joined
    assert "-r " not in joined


# --- shellprocess.conf: remove the installer artifacts from the installed system ---

def _installer_cleanup_command(script: list) -> str:
    """The single script command that removes the installer launchers (Desktop + menu) and
    overwrites the autostart on the installed target. Identified by the Desktop launcher."""
    from packages.calamares import calamares_shellprocess as csp
    matches = [c for c in script if csp.INSTALLER_DESKTOP_LAUNCHER in c]
    assert len(matches) == 1, matches
    return matches[0]


def test_shellprocess_removes_installer_from_installed_desktop():
    # The live session ships an installer launcher on the Desktop and (in its OpenBox
    # autostart) a first-run Calamares launch; the OFFLINE install copies /home/main
    # verbatim (reuseHome), so WITHOUT this the installed system would keep the Desktop
    # icon AND re-launch the installer at every login. This is the "installer shouldn't
    # be on the Desktop after install" fix. The command must delete the Desktop launcher
    # from BOTH the reused /home/main and /etc/skel; the auto-launch is removed by
    # overwriting the OpenBox autostart (see the autostart test below).
    from packages.calamares import calamares_shellprocess as csp
    d = yaml.safe_load(calamares.shellprocess_conf())
    cmd = _installer_cleanup_command(d["script"])
    assert f"rm -f {csp.INSTALLER_DESKTOP_LAUNCHER}" in cmd
    assert f"rm -f {csp.INSTALLER_SKEL_LAUNCHER}" in cmd
    # It targets the live user's home Desktop launcher specifically.
    assert csp.INSTALLER_DESKTOP_LAUNCHER == "/home/main/Desktop/azzio-install.desktop"


def test_shellprocess_removes_installer_menu_entry_post_install():
    # The installer must NOT appear ANYWHERE post-installation, so the system-wide
    # application-menu launcher (/usr/share/applications/azzio-install.desktop) is removed
    # too (previously it was left in place). calamares itself is also try_removed by the
    # packages module, so keeping the entry would just leave a dead launcher in the menu.
    from packages.calamares import calamares_shellprocess as csp
    from packages import openbox as desktop
    d = yaml.safe_load(calamares.shellprocess_conf())
    cmd = _installer_cleanup_command(d["script"])
    assert f"rm -f {csp.INSTALLER_MENU_DESKTOP}" in cmd
    # Single source of truth: the path this removes is exactly the one openbox.py ships.
    assert csp.INSTALLER_MENU_DESKTOP == desktop.INSTALL_MENU_DESKTOP_PATH
    assert csp.INSTALLER_MENU_DESKTOP == "/usr/share/applications/azzio-install.desktop"


def test_shellprocess_removes_installer_wrapper_post_install():
    # The privileged Calamares launcher wrapper (/usr/local/bin/azzio-install) makes sense on
    # the LIVE medium (the autostart + both installer launchers exec it), but must NOT survive
    # onto the INSTALLED system: once Calamares has installed Azzio there is nothing left to
    # install, so a leftover azzio-install wrapper is dead weight. The OFFLINE unpackfs install
    # copies the whole live rootfs, so this root-owned file lands on the target and the cleanup
    # step must delete it (post-install requirement: no azzio-install wrapper on the installed
    # system). The LIVE ISO is unchanged -- the wrapper is still shipped there.
    from packages.calamares import calamares_shellprocess as csp
    from packages import openbox as desktop
    d = yaml.safe_load(calamares.shellprocess_conf())
    cmd = _installer_cleanup_command(d["script"])
    assert f"rm -f {csp.INSTALLER_WRAPPER}" in cmd
    # Single source of truth: the path this removes is exactly the one openbox.py ships.
    assert csp.INSTALLER_WRAPPER == desktop.INSTALL_WRAPPER_PATH
    assert csp.INSTALLER_WRAPPER == "/usr/local/bin/azzio-install"
    # The LIVE medium still ships the wrapper (an emit_plan entry writes it to that path):
    # the cleanup only strips it from the TARGET chroot, not from the live ISO.
    plan_dests = {e["dest"] for e in desktop.emit_plan()}
    assert desktop.INSTALL_WRAPPER_PATH in plan_dests


def test_installer_cleanup_command_uses_no_shell_variables():
    # Same no-`$` rule as the other shellprocess commands (Calamares macro-expansion).
    # rm -f / cp -f so an absent path is a no-op and a shipped-file copy never prompts.
    from packages.calamares import calamares_shellprocess as csp
    cmd = csp._installer_cleanup_command()
    assert "$" not in cmd
    assert cmd.startswith("set -e")
    # Every rm uses rm -f and every cp uses cp -f (no interactive/failing bare form).
    for line in cmd.splitlines():
        if line.startswith("rm"):
            assert line.startswith("rm -f "), line
        if line.startswith("cp"):
            assert line.startswith("cp -f "), line


def test_installer_cleanup_chowns_autostart_back_to_the_user():
    # The cleanup `cp`s the installed autostart into $HOME as ROOT, so it lands root:root --
    # a user must own (and be able to edit) their own OpenBox autostart. The shared command
    # must chown it back to the live user's NUMERIC uid:gid (1000:998, per system.py) -- numeric
    # so it needs no `$` (Calamares macro-expander safe) and is correct for the CLI path too,
    # where usermod -l preserves uid/gid. Assert both the parameterized producer AND the
    # Calamares call site chown the home autostart (skel stays root-owned -- it is a template).
    from packages.calamares import calamares_shellprocess as csp
    for home in ("/home/main", "/home/$az_login"):
        cmd = csp.installer_cleanup_command(home)
        assert f"chown 1000:998 {home}/.config/openbox/autostart" in cmd, home
        # The chown must come AFTER the cp that created the file, or it chowns nothing.
        assert cmd.index(f"cp -f {csp.INSTALLED_AUTOSTART_SRC} {home}/.config/openbox/autostart") \
            < cmd.index(f"chown 1000:998 {home}/.config/openbox/autostart"), home
    # The Calamares literal-home call site carries it too, and still uses no `$`.
    calamares_cmd = csp._installer_cleanup_command()
    assert "chown 1000:998 /home/main/.config/openbox/autostart" in calamares_cmd
    assert "$" not in calamares_cmd
    # We do NOT chown the /etc/skel copy -- skel is a system template, root-owned is correct.
    assert "chown 1000:998 /etc/skel" not in calamares_cmd


def test_shellprocess_overwrites_openbox_autostart_so_region_keyboard_and_no_installer():
    # BUG classes fixed on the INSTALLED OpenBox session (the live rootfs is copied
    # verbatim via unpackfs/reuseHome): the live ~/.config/openbox/autostart carries two
    # LIVE-ONLY lines -- a FIXED `setxkbmap us,il` (which would override the region
    # keyboard Calamares wrote to /etc/X11/xorg.conf.d/00-keyboard.conf, so every install
    # would come up US+Hebrew regardless of region) and a first-run Calamares launch
    # (wrong on an installed system). The cleanup step must OVERWRITE the target's
    # autostart (home + skel) with the "installed" variant staged on the ISO, which drops
    # exactly those two lines while keeping wallpaper/xcape/menu-daemon.
    from packages.calamares import calamares_shellprocess as csp
    from packages import openbox as desktop
    d = yaml.safe_load(calamares.shellprocess_conf())
    cmd = _installer_cleanup_command(d["script"])
    src = desktop.INSTALLED_AUTOSTART_STAGING_PATH
    assert f"cp -f {src} {csp.INSTALLED_OPENBOX_AUTOSTART}" in cmd
    assert f"cp -f {src} {csp.INSTALLED_SKEL_OPENBOX_AUTOSTART}" in cmd
    # The paths are the reused-home + skel OpenBox autostart specifically.
    assert csp.INSTALLED_OPENBOX_AUTOSTART == "/home/main/.config/openbox/autostart"
    assert csp.INSTALLED_SKEL_OPENBOX_AUTOSTART == "/etc/skel/.config/openbox/autostart"
    # The staged "installed" autostart really does drop the two live-only lines.
    installed = desktop.openbox_autostart_installed()
    assert "setxkbmap" not in installed
    assert desktop.INSTALL_WRAPPER_PATH not in installed
    # ...while the LIVE autostart (what the target inherits before the overwrite) has both.
    live = desktop.openbox_autostart()
    assert "setxkbmap" in live
    assert desktop.INSTALL_WRAPPER_PATH in live


def test_shellprocess_autostart_source_is_the_staged_shipped_file():
    # Guard against drift: the file the cleanup COPIES FROM must be the exact staging
    # path packages/openbox emits the installed autostart to. If openbox.py's
    # staging dest ever moves, this catches it so the copy keeps sourcing a real file.
    from packages.calamares import calamares_shellprocess as csp
    from packages import openbox as desktop
    assert csp.INSTALLED_AUTOSTART_SRC == desktop.INSTALLED_AUTOSTART_STAGING_PATH
    dests = {e["dest"] for e in desktop.emit_plan()}
    assert desktop.INSTALLED_AUTOSTART_STAGING_PATH in dests


# --- shellprocess.conf: the archiso mkinitcpio fix (kernel + preset) --------

def _mkinitcpio_reset_command(script: list) -> str:
    """The single script command that fixes the target's initramfs setup (the one
    that writes linux.preset). Exactly one such command must exist."""
    matches = [c for c in script if "linux.preset" in c]
    assert len(matches) == 1, matches
    return matches[0]


def test_shellprocess_reinstates_the_kernel_image():
    # mkarchiso empties the rootfs /boot before squashing, so the unpacked target
    # has NO /boot/vmlinuz-linux -- the kernel survives only at
    # /usr/lib/modules/<kver>/vmlinuz. Calamares' initcpio step (`mkinitcpio -p
    # linux`) would fail "'/boot/vmlinuz-linux' must be readable" without this. The
    # command must replicate the linux package's install hook: copy
    # modules/<kver>/vmlinuz -> /boot/vmlinuz-linux. `find ... -exec install` (NOT a
    # glob passed to `install`, which would treat a second kernel as a target dir and
    # abort under set -e) is version-agnostic and recreates /boot with mode 644.
    d = yaml.safe_load(calamares.shellprocess_conf())
    cmd = _mkinitcpio_reset_command(d["script"])
    assert (
        "find /usr/lib/modules -maxdepth 2 -name vmlinuz "
        "-exec install -Dm644 {} /boot/vmlinuz-linux \\;"
    ) in cmd
    # find -exec exits 0 even on no match, so set -e alone would miss a missing
    # kernel; a `test -r` guard re-arms the hard failure the old glob provided.
    assert "test -r /boot/vmlinuz-linux" in cmd


def test_shellprocess_uses_no_shell_variables():
    # LOAD-BEARING: Calamares runs each shellprocess command through a
    # KWordMacroExpander (escape char '$') BEFORE the shell sees it. Any bare
    # `$WORD` that is not a Calamares variable makes the ENTIRE job abort with
    # "Missing variables" -- nothing runs, including the userdel/groupdel. And the
    # only literal-`$` escape ('$$') yields a shell-escaped `\\$` (a literal, not an
    # expansion), so shell variables / `$(...)` are unusable here. Assert NO '$'
    # appears in any emitted script command.
    d = yaml.safe_load(calamares.shellprocess_conf())
    for cmd in d["script"]:
        assert "$" not in cmd, cmd


def test_shellprocess_replaces_archiso_preset_with_stock():
    d = yaml.safe_load(calamares.shellprocess_conf())
    cmd = _mkinitcpio_reset_command(d["script"])
    # Writes the stock `linux` preset to the canonical path...
    assert "/etc/mkinitcpio.d/linux.preset" in cmd
    # ...whose defining feature is the default+fallback PRESETS (NOT archiso).
    assert "PRESETS=('default' 'fallback')" in cmd
    assert "default_image=\"/boot/initramfs-linux.img\"" in cmd
    assert "fallback_image=\"/boot/initramfs-linux-fallback.img\"" in cmd
    # The archiso preset name must be gone from what we write (that name is exactly
    # what makes `mkinitcpio -P` fail on the copied-in live rootfs).
    assert "archiso'" not in cmd  # PRESETS=('archiso') fragment
    # And it removes the archiso conf.d drop-in whose HOOKS would otherwise win.
    assert "rm -f /etc/mkinitcpio.conf.d/archiso.conf" in cmd


def test_shellprocess_mkinitcpio_command_is_fatal_on_failure():
    # The mkinitcpio fixup is deliberately NOT prefixed "-" and uses `set -e`:
    # reinstating the kernel + writing a correct preset is load-bearing (a silent
    # failure would leave /boot empty or the archiso preset in place, and the
    # install would die obscurely later at `initcpio`).
    d = yaml.safe_load(calamares.shellprocess_conf())
    cmd = _mkinitcpio_reset_command(d["script"])
    assert not cmd.startswith("-")
    assert cmd.startswith("set -e")


def test_stock_preset_constant_is_a_valid_default_fallback_preset():
    # The reused constant is the source of truth for the written preset.
    preset = calamares.STOCK_LINUX_PRESET
    assert "PRESETS=('default' 'fallback')" in preset
    assert 'ALL_kver="/boot/vmlinuz-linux"' in preset
    # No archiso-specific keys leak into the installed-system preset.
    assert "archiso" not in preset


# --- shellprocess@desparse (make /boot GRUB-readable) -----------------------

def _desparse_cmd() -> str:
    d = yaml.safe_load(calamares.shellprocess_desparsify_conf())
    # Single block-scalar command in the script list.
    assert len(d["script"]) == 1
    return d["script"][0]


def test_desparse_conf_schema_and_chroot():
    d = yaml.safe_load(calamares.shellprocess_desparsify_conf())
    # Runs INSIDE the target chroot (paths like /boot/vmlinuz-linux are target-abs).
    assert d["dontChroot"] is False
    assert "script" in d


def test_desparse_rewrites_boot_files_hole_free():
    # THE boot fix: cp --sparse=never eliminates the trailing EOF hole that makes
    # GRUB's btrfs driver read the kernel/initramfs short ("premature end of file").
    cmd = _desparse_cmd()
    assert "--sparse=never" in cmd
    # It must touch the kernel AND both initramfs images GRUB loads.
    assert "/boot/vmlinuz-linux" in cmd
    assert "/boot/initramfs-linux.img" in cmd
    assert "/boot/initramfs-linux-fallback.img" in cmd


def test_desparse_kernel_is_unconditional_initramfs_guarded():
    # The kernel always exists by now (reinstated pre-initcpio) -> de-sparsified
    # unconditionally (no `if`/`test` guard). The initramfs images are guarded with
    # `if [ -f ... ]` so a preset emitting only one image never aborts the install.
    cmd = calamares._boot_desparsify_command()
    lines = cmd.splitlines()
    # The kernel cp/mv lines are NOT inside an `if` guard (they sit at top level,
    # right after `set -e`, before the first `if`).
    first_if = next(i for i, l in enumerate(lines) if l.startswith("if "))
    kernel_lines = [l for l in lines[:first_if] if "/boot/vmlinuz-linux" in l]
    assert kernel_lines, "kernel must be de-sparsified before the first guard"
    assert not any(l.strip().startswith(("if ", "test ")) for l in kernel_lines)
    # Both initramfs rewrites ARE guarded with `if [ -f ... ]`.
    for img in ("/boot/initramfs-linux.img", "/boot/initramfs-linux-fallback.img"):
        assert f"if [ -f {img} ]; then" in cmd


def test_desparse_cp_and_mv_are_separate_statements_not_and_chains():
    # REGRESSION GUARD (the exact adversarial finding): `cp ... && mv ...` would let
    # a failed kernel `cp` slip past `set -e` (a command left of `&&` is a "tested"
    # command whose failure is ignored), so the script would exit 0 and Calamares
    # would ship an UNBOOTABLE system. The cp and mv MUST be separate statements.
    cmd = calamares._boot_desparsify_command()
    assert "&&" not in cmd, "cp/mv must not be && -chained (defeats set -e)"


def test_desparse_uses_no_shell_variables():
    # Same Calamares macro-expander constraint as the other shellprocess: a bare
    # `$WORD` aborts the whole job. Assert NO '$' in the emitted command.
    assert "$" not in _desparse_cmd()


def test_desparse_is_fatal_on_failure():
    # Making /boot GRUB-readable is load-bearing: NOT prefixed "-", uses `set -e`.
    cmd = calamares._boot_desparsify_command()
    assert not cmd.startswith("-")
    assert cmd.startswith("set -e")


def test_desparse_actually_aborts_when_kernel_cp_fails(tmp_path):
    # BEHAVIORAL proof of the fatal-on-failure contract (not just a string check):
    # run the emitted command with the kernel source ABSENT but an initramfs image
    # PRESENT. The kernel `cp` must fail and, under set -e, abort the WHOLE script
    # with a non-zero exit -- even though the later (present) initramfs step would
    # succeed. The old `&&`-chained form exited 0 here (the bug).
    import subprocess
    cmd = calamares._boot_desparsify_command()
    # Rebase the absolute /boot/... paths into a sandbox so the test touches no real
    # system files. The kernel source is deliberately NOT created (cp will fail);
    # one initramfs image IS created (its guarded step would otherwise succeed).
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "initramfs-linux.img").write_bytes(b"present")
    # Rebase EVERY /boot occurrence (no trailing slash) into the sandbox so the
    # command -- including its `chattr +C /boot` line -- never touches the host /boot.
    sandboxed = cmd.replace("/boot", str(boot))
    rc = subprocess.run(["bash", "-c", sandboxed], capture_output=True).returncode
    assert rc != 0, "a failed kernel cp must abort the install (set -e), not exit 0"


def test_desparse_actually_removes_trailing_hole_on_disk(tmp_path):
    # THE regression guard the project was missing (this boot bug has now hit TWICE):
    # every other desparse test checks the COMMAND STRING; none proved the command
    # actually yields a hole-free file. Here we reproduce the exact pre-desparse
    # state -- a kernel + initramfs with a TRAILING EOF SPARSE HOLE, as unpackfs'
    # rsync --sparse leaves them -- run the REAL emitted command, and assert the
    # result carries NO trailing hole (allocated blocks cover the whole file). A file
    # with a hole at EOF is precisely what makes GRUB's btrfs driver read the kernel
    # short ("premature end of file /@/boot/vmlinuz-linux"); if a future edit weakens
    # the desparse (drops --sparse=never, re-&&-chains cp/mv, etc.) the file stays
    # sparse and THIS test fails -- catching the regression before an ISO ships.
    import os
    import subprocess

    boot = tmp_path / "boot"
    boot.mkdir()

    def make_sparse(name: str, data_bytes: int, hole_bytes: int) -> None:
        # Write `data_bytes` of real data, then extend the file by `hole_bytes` via
        # truncate -> a genuine sparse hole at EOF (last data extent ends before
        # i_size), the exact shape rsync --sparse produces for a zero-padded bzImage.
        # The hole is 1 MiB+ so the FS reliably leaves it unallocated (a sub-block
        # hole is not portably sparse -- tail allocation/delalloc can back it).
        p = boot / name
        with open(p, "wb") as fh:
            fh.write(b"\xa5" * data_bytes)
            fh.truncate(data_bytes + hole_bytes)

    make_sparse("vmlinuz-linux", 5_000_000, 4 * 1024 * 1024)
    make_sparse("initramfs-linux.img", 3_000_000, 2 * 1024 * 1024)
    make_sparse("initramfs-linux-fallback.img", 7_000_000, 8 * 1024 * 1024)

    # If the test filesystem refuses to make ANY of them sparse (rare -- some tmpfs/
    # overlay setups), there is nothing to de-sparsify and the on-disk assertion is
    # meaningless here; skip rather than assert a false pass/fail.
    if not any(
        os.stat(boot / n).st_blocks * 512 < os.stat(boot / n).st_size
        for n in ("vmlinuz-linux", "initramfs-linux.img", "initramfs-linux-fallback.img")
    ):
        import pytest as _pytest
        _pytest.skip("test filesystem does not create sparse files; on-disk check N/A")

    cmd = calamares._boot_desparsify_command().replace("/boot", str(boot))
    res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    assert res.returncode == 0, f"desparse command failed: {res.stderr}"

    # Every /boot file GRUB reads must now be hole-free: allocated blocks >= size.
    for name in ("vmlinuz-linux", "initramfs-linux.img", "initramfs-linux-fallback.img"):
        st = os.stat(boot / name)
        assert st.st_blocks * 512 >= st.st_size, (
            f"{name} still has a trailing hole after desparse "
            f"(allocated {st.st_blocks * 512} < size {st.st_size}) -- GRUB would "
            f"read it short and fail to boot"
        )
    # No leftover .nosparse temp files (mv must have renamed them into place).
    assert not list(boot.glob("*.nosparse")), "desparse left a .nosparse temp behind"


def test_desparse_marks_boot_nocompress_so_future_kernels_stay_readable():
    # THE actual boot fix (the sparse-hole theory was a MISdiagnosis): the target
    # btrfs is mounted `compress=zstd:1` (mount.conf), so unpackfs writes
    # /boot/vmlinuz-linux as ZSTD-COMPRESSED btrfs extents, and GRUB's btrfs driver
    # cannot decompress zstd -> it reads the kernel short and fails with
    # "premature end of file /@/boot/vmlinuz-linux". Rewriting the file IN PLACE under
    # the same compressed mount (the old `cp --sparse=never`) leaves it compressed --
    # which is why the bug survived that "fix". The command MUST mark /boot with the
    # btrfs no-compress attribute (`chattr +C`) so both the rewritten files AND every
    # kernel a FUTURE `pacman -Syu` writes into /boot are stored uncompressed.
    cmd = calamares._boot_desparsify_command()
    assert "chattr +C /boot" in cmd, (
        "desparse must set the btrfs no-compress attribute on /boot (compress=zstd "
        "makes GRUB read the kernel short); rewriting alone leaves it compressed"
    )
    # The +C must be applied BEFORE the file rewrites, so the fresh temp copies land
    # in a no-compress directory (a copy made before +C would inherit compression).
    # The kernel rewrite is the `cp ... /boot/vmlinuz-linux ...nosparse` line.
    assert cmd.index("chattr +C /boot") < cmd.index("cp --reflink=never --sparse=never -f /boot/vmlinuz-linux"), (
        "chattr +C /boot must precede the kernel rewrite so the rewrite is uncompressed"
    )


@pytest.mark.root
def test_desparse_actually_yields_uncompressed_boot_on_zstd_btrfs(tmp_path):
    # BEHAVIORAL proof against the REAL failure mode, on a real btrfs mounted exactly
    # like the installer mounts the target (compress=zstd:1). Reproduces the state
    # unpackfs leaves -- a compressible kernel written as ZSTD-COMPRESSED extents --
    # runs the REAL emitted command, and asserts every /boot file GRUB reads ends up
    # with NO compressed ("encoded") extents. The old cp-in-place desparse fails this
    # (the file stays `encoded`), catching the exact regression the user hit twice.
    #
    # Needs root + loop mount + btrfs + filefrag; skips cleanly when unavailable so
    # the pure-Python CI still passes.
    import os
    import shutil
    import subprocess

    if os.geteuid() != 0:
        import pytest as _pytest
        _pytest.skip("needs root to loop-mount a btrfs image")
    for tool in ("mkfs.btrfs", "filefrag", "chattr"):
        if shutil.which(tool) is None:
            import pytest as _pytest
            _pytest.skip(f"{tool} not available")

    img = tmp_path / "btrfs.img"
    with open(img, "wb") as fh:
        fh.truncate(400 * 1024 * 1024)
    if subprocess.run(["mkfs.btrfs", "-q", "-f", str(img)]).returncode != 0:
        import pytest as _pytest
        _pytest.skip("mkfs.btrfs failed in this environment")
    mnt = tmp_path / "mnt"
    mnt.mkdir()
    # Mount exactly as the installer mounts the target root: compress=zstd:1.
    if subprocess.run(
        ["mount", "-o", "compress=zstd:1", str(img), str(mnt)]
    ).returncode != 0:
        import pytest as _pytest
        _pytest.skip("cannot loop-mount btrfs here")
    try:
        boot = mnt / "boot"
        boot.mkdir()
        # A highly compressible ~12 MB "kernel" -> btrfs stores it zstd-compressed.
        for name in ("vmlinuz-linux", "initramfs-linux.img", "initramfs-linux-fallback.img"):
            (boot / name).write_bytes(b"\x00" * (12 * 1024 * 1024))
        subprocess.run(["sync"], check=True)

        def is_compressed(p) -> bool:
            out = subprocess.run(
                ["filefrag", "-v", str(p)], capture_output=True, text=True
            ).stdout
            return "encoded" in out

        # Sanity: the pre-fix state IS compressed (else the test proves nothing).
        assert is_compressed(boot / "vmlinuz-linux"), (
            "test setup failed to produce a compressed kernel; cannot prove the fix"
        )

        cmd = calamares._boot_desparsify_command().replace("/boot", str(boot))
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        assert res.returncode == 0, f"desparse command failed: {res.stderr}"

        # Every /boot file GRUB reads must now be UNCOMPRESSED (no `encoded` extents),
        # so GRUB's btrfs driver can read it in full.
        for name in ("vmlinuz-linux", "initramfs-linux.img", "initramfs-linux-fallback.img"):
            assert not is_compressed(boot / name), (
                f"{name} still has zstd-compressed extents after desparse -- GRUB "
                f"cannot decompress them and boots with 'premature end of file'"
            )
    finally:
        subprocess.run(["umount", str(mnt)], capture_output=True)


def test_desparse_preserves_file_contents(tmp_path):
    # De-sparsifying must not change the bytes GRUB/the kernel see: cp --sparse=never
    # rewrites the trailing zeros as real data but the logical content is identical.
    # (A bzImage carries its own length and ignores trailing zeros; an initramfs
    # decoder stops at its own end marker -- so real-zeros vs a hole is equivalent.)
    import hashlib
    import os
    import subprocess

    boot = tmp_path / "boot"
    boot.mkdir()
    body = b"AZ" * 100_000
    for name in ("vmlinuz-linux", "initramfs-linux.img", "initramfs-linux-fallback.img"):
        with open(boot / name, "wb") as fh:
            fh.write(body)
            fh.truncate(len(body) + 777)   # trailing hole
    want = hashlib.sha256(body + b"\x00" * 777).hexdigest()

    cmd = calamares._boot_desparsify_command().replace("/boot", str(boot))
    assert subprocess.run(["bash", "-c", cmd]).returncode == 0
    for name in ("vmlinuz-linux", "initramfs-linux.img", "initramfs-linux-fallback.img"):
        got = hashlib.sha256((boot / name).read_bytes()).hexdigest()
        assert got == want, f"{name} content changed across desparse"


def test_desparse_timeout_is_generous_for_large_initramfs():
    # REGRESSION GUARD: the fallback initramfs is 200+ MB; three sequential cp copies
    # on a slow target disk can exceed a tight timeout, and if Calamares KILLS the
    # step mid-cp the mv leaves a truncated/sparse file -> the exact unbootable state.
    # The timeout must be comfortably large (>= 300 s).
    d = yaml.safe_load(calamares.shellprocess_desparsify_conf())
    assert int(d["timeout"]) >= 300, "desparse timeout too tight for a 200MB+ initramfs cp"


def test_desparse_ends_with_sync():
    # The rewritten /boot files must be flushed to disk before the target is
    # unmounted, so a trailing `sync` is part of the command.
    assert calamares._boot_desparsify_command().rstrip().endswith("sync")


def test_desparse_is_the_LAST_step_that_touches_boot():
    # ORDERING regression guard. The de-sparsify must be the LAST step to touch /boot
    # so it always operates on the final on-disk state and no later step can
    # reintroduce a trailing hole: it must run AFTER `initcpio` (writes the initramfs)
    # and AFTER `grubcfg`/`bootloader`/`packages` (any of which could, now or after a
    # future change, write a /boot file -- e.g. a pacman transaction firing the
    # mkinitcpio hook), immediately before `umount`. This is the invariant that keeps
    # "boot files are hole-free" robust regardless of step order/removal-set changes.
    execs = _settings_exec_list()
    assert "shellprocess@desparse" in execs
    i = execs.index("shellprocess@desparse")
    # after everything that writes /boot:
    for earlier in ("initcpio", "initcpiocfg", "grubcfg", "bootloader", "packages"):
        assert execs.index(earlier) < i, f"{earlier} must run BEFORE desparse"
    # and it must be the final exec step before umount (nothing may touch /boot after).
    assert execs[i + 1] == "umount", "desparse must be the last step before umount"
    assert i == len(execs) - 2, "nothing may run between desparse and umount"


def test_desparse_declared_as_shellprocess_instance():
    # The second instance must be declared so Calamares loads
    # shellprocess-desparse.conf for the `shellprocess@desparse` sequence entry.
    # The filename key is `config` (Calamares Settings.cpp) -- NOT `configuration`;
    # the wrong key silently defaults the instance to shellprocess.conf and the /boot
    # fixup never runs (the boot regression). See
    # test_instances_use_config_key_not_configuration.
    doc = yaml.safe_load(calamares.settings_conf())
    insts = {i["id"]: i for i in doc.get("instances", [])}
    assert "desparse" in insts
    assert insts["desparse"]["module"] == "shellprocess"
    assert insts["desparse"]["config"] == "shellprocess-desparse.conf"


def test_desparse_conf_wired_to_right_path():
    # The emitted file name must match the instance's configuration: reference.
    assert (calamares.emit_map()["modules/shellprocess-desparse.conf"]
            == calamares.shellprocess_desparsify_conf())


# --- END-TO-END boot regression guard ---------------------------------------
#
# THE boot bug shipped TWICE and cost a full debug cycle each time, because every
# piece looked right in isolation:
#   * the desparse command was correct (its own tests passed),
#   * settings.conf declared a `desparse` instance (its own test passed -- but it
#     asserted the WRONG key `configuration:`, so it "passed" while broken),
#   * yet at install time Calamares resolved `shellprocess@desparse` to the DEFAULT
#     shellprocess.conf (mkinitcpio reset) because the per-instance filename key must
#     be `config`, not `configuration`. The /boot fixup never ran and GRUB failed
#     with "premature end of file /@/boot/vmlinuz-linux".
#
# These two tests reproduce the FULL resolution chain the way Calamares does it, so a
# regression in ANY link (wrong instance key, instance pointing at the wrong file,
# the sequence dropping shellprocess@desparse, or the command no longer producing a
# GRUB-readable kernel) fails HERE -- before an ISO ships.


def _resolve_sequence_entry_to_conf(module_at_id: str) -> str:
    """Resolve a `module@id` exec-sequence token to the STEM of the config file
    Calamares would load for it, using the SAME rules as Calamares Settings.cpp:
      * a bare `module` (id == module) loads `<module>.conf`;
      * a `module@id` loads the `config:` filename declared in the matching
        `instances:` entry, and if that entry has no `config` key it SILENTLY
        defaults to `<module>.conf` (the exact trap that broke the boot fix).
    Returns the stem (no `.conf`), so shellprocess@desparse -> "shellprocess-desparse".
    """
    doc = yaml.safe_load(calamares.settings_conf())
    if "@" not in module_at_id:
        return module_at_id
    module, _id = module_at_id.split("@", 1)
    for inst in doc.get("instances", []):
        if inst.get("module") == module and str(inst.get("id")) == _id:
            # Calamares reads m.value("config"); absent -> "<module>.conf".
            cfg = inst.get("config", f"{module}.conf")
            return cfg[:-len(".conf")] if cfg.endswith(".conf") else cfg
    # No instance declared: Calamares would treat it as <module>.conf too.
    return module


def test_desparse_sequence_entry_resolves_to_the_desparse_conf_not_the_default():
    # THE regression the `configuration:` typo caused: shellprocess@desparse must
    # resolve to shellprocess-desparse.conf. With the wrong key it resolved to the
    # DEFAULT "shellprocess" conf (the mkinitcpio reset) and the /boot fixup silently
    # never ran. Assert the exec sequence actually contains the token AND that it
    # resolves to the desparse config -- exactly as Calamares would resolve it.
    doc = yaml.safe_load(calamares.settings_conf())
    exec_seq = next(phase["exec"] for phase in doc["sequence"] if "exec" in phase)
    assert "shellprocess@desparse" in exec_seq, (
        "the /boot fixup step (shellprocess@desparse) is missing from the exec sequence"
    )
    resolved = _resolve_sequence_entry_to_conf("shellprocess@desparse")
    assert resolved == "shellprocess-desparse", (
        f"shellprocess@desparse resolves to {resolved!r}, not 'shellprocess-desparse' "
        f"-- Calamares would run the WRONG commands (likely the mkinitcpio reset) and "
        f"the /boot de-compress/de-sparsify would never run, so the installed system "
        f"fails to boot with 'premature end of file /@/boot/vmlinuz-linux'. The usual "
        f"cause is the instances entry using key `configuration:` instead of `config:`."
    )
    # And the config that token resolves to must be the one carrying the boot fixup
    # (chattr +C /boot + the uncompressed rewrite), not the mkinitcpio reset.
    resolved_conf = calamares.emit_map()[f"modules/{resolved}.conf"]
    assert "chattr +C /boot" in resolved_conf and "vmlinuz-linux" in resolved_conf, (
        "the config shellprocess@desparse resolves to is not the /boot fixup"
    )


@pytest.mark.root
def test_desparse_full_chain_yields_grub_readable_kernel_on_zstd_btrfs(tmp_path):
    # THE definitive end-to-end guard: follow the WHOLE chain Calamares follows --
    # settings.conf sequence -> instance -> config file -> command -- and run the
    # command that shellprocess@desparse actually resolves to on a real btrfs mounted
    # compress=zstd:1 (exactly like the installer mounts the target). Reproduce the
    # pre-fix /boot state (a compressible kernel written as ZSTD-compressed extents,
    # reflinked from the modules tree with a trailing hole), run the RESOLVED command,
    # and assert the kernel ends up GRUB-readable: NO compressed ("encoded") extents
    # AND no trailing hole (allocated bytes >= file size). If the resolution regresses
    # (wrong instance key -> mkinitcpio reset) OR the command regresses, the kernel
    # stays compressed/holey and this fails -- catching the boot bug before ship.
    #
    # Needs root + loop mount + btrfs + filefrag; skips cleanly when unavailable so
    # pure-Python CI still passes.
    import os
    import shutil
    import subprocess

    if os.geteuid() != 0:
        import pytest as _pytest
        _pytest.skip("needs root to loop-mount a btrfs image")
    for tool in ("mkfs.btrfs", "filefrag", "chattr"):
        if shutil.which(tool) is None:
            import pytest as _pytest
            _pytest.skip(f"{tool} not available")

    # 1. Resolve the sequence entry -> config -> command, the Calamares way.
    resolved = _resolve_sequence_entry_to_conf("shellprocess@desparse")
    conf = yaml.safe_load(calamares.emit_map()[f"modules/{resolved}.conf"])
    # shellprocess config: a `script` list; our desparse conf is one block-scalar item.
    script_items = conf["script"]
    command = "\n".join(script_items) if isinstance(script_items, list) else script_items

    # 2. Real btrfs mounted exactly like the installer mounts the target.
    img = tmp_path / "btrfs.img"
    with open(img, "wb") as fh:
        fh.truncate(400 * 1024 * 1024)
    if subprocess.run(["mkfs.btrfs", "-q", "-f", str(img)]).returncode != 0:
        import pytest as _pytest
        _pytest.skip("mkfs.btrfs failed in this environment")
    mnt = tmp_path / "mnt"
    mnt.mkdir()
    if subprocess.run(
        ["mount", "-o", "compress=zstd:1", str(img), str(mnt)]
    ).returncode != 0:
        import pytest as _pytest
        _pytest.skip("cannot loop-mount btrfs here")
    try:
        # 3. Reproduce the pre-fix /boot: a modules-tree kernel with a 512-byte
        #    trailing hole, reflinked into /boot (via install -Dm644), written under
        #    the compressed mount so it lands as ZSTD-compressed extents.
        modules = mnt / "usr/lib/modules/x/"
        modules.mkdir(parents=True)
        boot = mnt / "boot"
        boot.mkdir()
        # Highly compressible body so btrfs really compresses it; sub-block tail hole.
        src = modules / "vmlinuz"
        with open(src, "wb") as fh:
            fh.write(b"\x00" * (12 * 1024 * 1024))
            fh.truncate(12 * 1024 * 1024 + 512)   # 512-byte trailing hole
        subprocess.run(
            ["install", "-Dm644", str(src), str(boot / "vmlinuz-linux")], check=True
        )
        for img_name in ("initramfs-linux.img", "initramfs-linux-fallback.img"):
            with open(boot / img_name, "wb") as fh:
                fh.write(b"\x00" * (8 * 1024 * 1024))
        subprocess.run(["sync"], check=True)

        def is_compressed(p) -> bool:
            out = subprocess.run(
                ["filefrag", "-v", str(p)], capture_output=True, text=True
            ).stdout
            return "encoded" in out

        def has_trailing_hole(p) -> bool:
            st = os.stat(p)
            return st.st_blocks * 512 < st.st_size

        k = boot / "vmlinuz-linux"
        # Sanity: pre-fix the kernel IS compressed (else the test proves nothing).
        assert is_compressed(k), "setup failed to produce a compressed kernel"

        # 4. Run the RESOLVED command, rebased into the sandbox /boot.
        sandboxed = command.replace("/boot", str(boot))
        res = subprocess.run(["bash", "-c", sandboxed], capture_output=True, text=True)
        assert res.returncode == 0, f"resolved desparse command failed: {res.stderr}"

        # 5. The kernel GRUB reads must now be readable: uncompressed AND hole-free.
        assert not is_compressed(k), (
            "kernel still has zstd-compressed extents after the resolved desparse "
            "command -- GRUB cannot decompress zstd ('premature end of file')"
        )
        assert not has_trailing_hole(k), (
            "kernel still has a trailing EOF hole after the resolved desparse command "
            "-- GRUB reads it short ('premature end of file /@/boot/vmlinuz-linux')"
        )
    finally:
        subprocess.run(["umount", str(mnt)], capture_output=True)


# --- users.conf (reuse the surviving /home/main) ----------------------------

def test_users_no_autologin_on_installed_system():
    # After shellprocess removes the account, /home/main (uid 1000) remains on the
    # target; the users module's `useradd -m` recreates `main` and simply reuses that
    # directory (it only WARNS on an existing home, exit 0), so no config key is needed
    # to "reuse" it. (The `reuseHome` GlobalStorage flag Calamares acts on is set by the
    # PARTITION module, never from users.conf -- a top-level `reuseHome` here is dead and
    # the schema rejects it; see test_users_conf_only_uses_schema_keys.)
    #
    # The one policy this file must pin: the live ISO autologins, the INSTALLED system
    # must NOT.
    d = yaml.safe_load(calamares.users_conf())
    assert d["doAutologin"] is False


def test_users_hostname_template_is_literal_azzio():
    # "What is the name of this computer?" defaults to "azzio" and must NOT change
    # as the Full Name / Login fields change. Calamares reads the hostname suggestion
    # from the top-level `hostname` submap's `template`. A LITERAL "azzio" (no
    # ${...} macros) expands to exactly "azzio" for any user input; the paired
    # calamares source patch seeds it as the initial value and freezes it. If a macro
    # ever crept into this template the hostname would go reactive again, so pin it.
    d = yaml.safe_load(calamares.users_conf())
    assert d["hostname"]["template"] == "azzio"
    assert "$" not in d["hostname"]["template"]  # no ${first}/${product}/... macros


# The 3.4.2 users.so contract, transcribed from src/modules/users/users.schema.yaml
# (additionalProperties:false + a `required` list). The users module does NOT validate
# its config against this schema at runtime -- an unknown key is silently ignored and a
# missing required key silently defaults -- so a drift here type-checks in Python, builds,
# boots, and only misbehaves at install time. These two frozen sets are the ONLY place
# that contract is enforced; keep them in sync with the pinned schema on a version bump.
_USERS_SCHEMA_TOPLEVEL_KEYS = {
    "user", "defaultGroups", "autologinGroup", "sudoersGroup",
    "sudoersConfigureWithGroup", "displayAutologin", "doAutologin",
    "setRootPassword", "doReusePassword", "allowWeakPasswords",
    "allowWeakPasswordsDefault", "passwordRequirements", "hostname",
    "allowActiveDirectory", "presets",
}
_USERS_SCHEMA_REQUIRED_KEYS = {"defaultGroups", "autologinGroup", "sudoersGroup"}


def test_users_conf_only_uses_schema_keys():
    # additionalProperties:false -> any top-level key the schema does not list is
    # silently dropped by the users module. Historically users.conf carried
    # `reuseHome` (only ever read from GlobalStorage, set by the PARTITION module --
    # never from this file), a `setHostname` mirror (only the `hostname` submap is
    # read), and a top-level `userShell` (the shell is read from `user:{shell}`), so
    # all three were dead weight the schema rejects. Pin the emitted keys to the
    # schema's allowed set so no ignored key creeps back in.
    d = yaml.safe_load(calamares.users_conf())
    unexpected = set(d) - _USERS_SCHEMA_TOPLEVEL_KEYS
    assert not unexpected, f"users.conf has keys the 3.4.2 schema rejects: {sorted(unexpected)}"


def test_users_conf_has_all_required_keys():
    # The schema's `required:` list. A missing required key defaults silently at
    # runtime (autologinGroup -> "", etc.), so it never errors -- but it means the
    # emitted file no longer matches the contract the module was built against. Assert
    # every required key is present so the config is a faithful, complete users.conf.
    d = yaml.safe_load(calamares.users_conf())
    missing = _USERS_SCHEMA_REQUIRED_KEYS - set(d)
    assert not missing, f"users.conf is missing schema-required keys: {sorted(missing)}"


def test_users_conf_shell_is_under_user_submap():
    # The user shell is read ONLY from the `user:` submap's `shell` (Config.cpp
    # getSubMap("user") -> getString("shell")); a top-level `userShell` is not a
    # schema key and is never read, so the installed account would silently fall back
    # to the useradd default. Pin the shell to the place Calamares actually reads.
    d = yaml.safe_load(calamares.users_conf())
    assert d["user"]["shell"] == "/bin/bash"
    assert "userShell" not in d  # the dead top-level spelling must not return


def test_users_conf_password_is_skippable_and_reuse_for_root_default_checked():
    # Azzio PROMPT: the user password defaults EMPTY and is SKIPPABLE, and the
    # reuse-password checkbox ("Use username password for root password.") defaults
    # CHECKED.
    #
    # 1. NO passwordRequirements block. A `minLength: 1` (or any length check) would
    #    register a check that marks an EMPTY password Invalid, and Config::isReady()
    #    blocks Next on an Invalid password -> the field could not be skipped. With no
    #    checks configured, passwordStatus() returns Valid for any password (incl.
    #    empty), so an empty password never blocks Next. A skipped password is then
    #    locked to "*" by the SetPasswordJob source patch. Assert the block is absent.
    d = yaml.safe_load(calamares.users_conf())
    assert "passwordRequirements" not in d, (
        "users.conf must NOT set passwordRequirements -- a length check would make an "
        "empty (skipped) password Invalid and block Next"
    )
    # 2. doReusePassword: true -> the reuse-for-root checkbox starts CHECKED, so root
    #    mirrors the user password by default (one password box; empty -> root locked).
    assert d["doReusePassword"] is True
    # 3. allowWeakPasswords: false -> the "Require strong passwords." checkbox is not
    #    shown via the config path (the source patch also force-hides it). Since no
    #    checks are configured there is nothing to enforce, so empty still passes.
    assert d["allowWeakPasswords"] is False
    # setRootPassword stays true (root account IS written; it just reuses/locks).
    assert d["setRootPassword"] is True


def test_users_conf_validates_against_shipped_schema_when_present():
    # When the pinned calamares source tree is extracted in the makepkg cache, validate
    # the emitted users.conf against the REAL users.schema.yaml with jsonschema -- the
    # authoritative check. Skipped (not failed) when the build cache is absent, since the
    # extracted source is a build artifact; the transcribed-contract tests above always run.
    jsonschema = pytest.importorskip(
        "jsonschema"
    )  # optional, only needed for this authoritative check
    from conftest import REPO  # noqa: PLC0415

    schema_path = (
        REPO / "cache" / "makepkg" / "calamares" / ".build" / "calamares" / "src"
        / "calamares-3.4.2" / "src" / "modules" / "users" / "users.schema.yaml"
    )
    if not schema_path.exists():
        pytest.skip("calamares source not extracted in makepkg cache; schema unavailable")
    schema = yaml.safe_load(schema_path.read_text())
    cfg = yaml.safe_load(calamares.users_conf())
    errors = sorted(jsonschema.Draft7Validator(schema).iter_errors(cfg), key=lambda e: list(e.path))
    assert not errors, "users.conf fails the shipped schema:\n" + "\n".join(
        f"  [{'/'.join(str(p) for p in e.path) or '(root)'}] {e.message}" for e in errors
    )


# --- locale.conf timezone default -------------------------------------------

def test_locale_conf_defaults_to_asia_jerusalem():
    d = yaml.safe_load(calamares.locale_conf())
    assert d["region"] == "Asia"
    assert d["zone"] == "Jerusalem"


# --- keyboard.conf: no auto-resolve (the Hebrew-preselect fix) --------------

def test_keyboard_conf_enables_region_second_layout():
    # Azzio region-driven keyboard: when the user picks a non-English region on the
    # Location page, the region's native layout is added as a switchable SECOND layout
    # (English "us" stays first/active, Alt+Shift), live in the installer and persisted
    # to the target. This needs guessLayout:true (guessLocaleKeyboardLayout(), which the
    # region-keyboard source patch extends, early-returns when false) AND the opt-in
    # regionSecondLayout:true the patch reads. English no longer resolves to a lone
    # Hebrew layout: it is always force-kept as the primary/active ASCII layout.
    d = yaml.safe_load(calamares.keyboard_conf())
    assert d["guessLayout"] is True
    assert d["regionSecondLayout"] is True


def test_keyboard_conf_uses_plain_xorg_not_locale1():
    # Azzio is Openbox/X11 and setup-locale.sh already wrote
    # /etc/X11/xorg.conf.d/00-keyboard.conf with "us"; managing that file directly
    # (useLocale1 false) is what lets the module read "us" as the current layout.
    d = yaml.safe_load(calamares.keyboard_conf())
    assert d["useLocale1"] is False
    assert d["xOrgConfFileName"] == "/etc/X11/xorg.conf.d/00-keyboard.conf"


def test_keyboard_conf_no_kde_gnome_integration():
    # Openbox/X11 -- no KWin/GNOME keyboard integration to configure.
    d = yaml.safe_load(calamares.keyboard_conf())
    assert d["configure"] == {"kwin": False, "gnome": False}


def test_keyboard_in_show_and_configured():
    # keyboard is a UI page (show) and now carries a real configuration (not defaults).
    assert "keyboard" in _settings_show_list()
    assert calamares.emit_map()["modules/keyboard.conf"] == calamares.keyboard_conf()


# --- grubcfg.conf -----------------------------------------------------------

def test_grubcfg_snake_case_key():
    d = yaml.safe_load(calamares.grubcfg_conf())
    # keep_distributor is snake_case; the camelCase variant is silently ignored,
    # so the GRUB_DISTRIBUTOR string would be dropped.
    assert "keep_distributor" in d
    assert "keepDistributor" not in d
    assert d["keep_distributor"] is True


def test_grubcfg_defaults_and_kernel_params():
    d = yaml.safe_load(calamares.grubcfg_conf())
    assert d["kernel_params"] == ["quiet"]
    # Auto-boot the FIRST menu entry with no wait (the user's "GRUB automatically
    # goes into the first option during boot" request): default 0 == first entry,
    # timeout 0 == boot immediately.
    assert d["defaults"]["GRUB_TIMEOUT"] == 0
    assert d["defaults"]["GRUB_DEFAULT"] == 0


def test_grubcfg_auto_boots_first_entry():
    # Pin the whole auto-boot combination so a partial edit (e.g. dropping the
    # hidden style, or reverting GRUB_DEFAULT to "saved") is caught: first entry,
    # zero wait, no menu shown.
    d = yaml.safe_load(calamares.grubcfg_conf())
    assert d["defaults"]["GRUB_DEFAULT"] == 0
    assert d["defaults"]["GRUB_TIMEOUT"] == 0
    assert d["defaults"]["GRUB_TIMEOUT_STYLE"] == "hidden"
    # "saved" would make GRUB boot the last-saved entry, not the first -- must be gone.
    assert d["defaults"]["GRUB_DEFAULT"] != "saved"


# --- packages.conf ----------------------------------------------------------

def test_packages_conf_uses_try_remove():
    d = yaml.safe_load(calamares.packages_conf())
    ops = d["operations"]
    # try_remove (not remove) so an absent live-only package does not fail install.
    assert ops == [{"try_remove": ["calamares"]}]
    for op in ops:
        assert "remove" not in op


def test_packages_backend_is_pacman_no_network():
    d = yaml.safe_load(calamares.packages_conf())
    assert d["backend"] == "pacman"
    assert d["update_db"] is False
    assert d["update_system"] is False


# --- fstab.conf -------------------------------------------------------------

def test_fstab_only_allowed_keys():
    # Schema is additionalProperties:false with exactly these two keys.
    d = yaml.safe_load(calamares.fstab_conf())
    assert set(d) == {"crypttabOptions", "tmpOptions"}


# --- bootloader.conf --------------------------------------------------------

def test_bootloader_no_schema_rejected_keys():
    # bootloader schema is additionalProperties:false: these derived keys would
    # fail validation and abort the install.
    d = yaml.safe_load(calamares.bootloader_conf())
    assert "kernel" not in d
    assert "img" not in d
    assert "fallback" not in d
    # The ESP key belongs to partition.conf, not here.
    assert "efiSystemPartition" not in d


def test_bootloader_grub_identity():
    d = yaml.safe_load(calamares.bootloader_conf())
    assert d["efiBootLoader"] == "grub"
    assert d["efiBootloaderId"] == "azzio"


# --- finished.conf (Restart-now option on the Finish page) ------------------

def test_finished_offers_restart_now():
    # Without this configuration the Finish page shows only "Done" and cannot reboot into
    # the new system. user-unchecked shows a "Restart now" checkbox, default off.
    d = yaml.safe_load(calamares.finished_conf())
    assert d["restartNowMode"] == "user-unchecked"
    # Reboot command must ignore inhibitors so it actually restarts.
    assert d["restartNowCommand"] == "systemctl -i reboot"


def test_finished_conf_schema_only_valid_keys():
    # finished schema is additionalProperties:false -- an unknown key aborts
    # Calamares at startup. Assert we only use documented keys.
    d = yaml.safe_load(calamares.finished_conf())
    valid = {"restartNowEnabled", "restartNowChecked", "restartNowCommand",
             "restartNowMode", "notifyOnFinished"}
    assert set(d) <= valid
    # finished is in the sequence's show phase, so its configuration is not an orphan.
    assert "finished" in _settings_show_list()


# --- branding.desc ----------------------------------------------------------

def test_branding_style_keys_capitalized():
    d = yaml.safe_load(calamares.branding_desc())
    style = d["style"]
    # Lowercase style keys are silently ignored, so the accent never applies.
    for key in style:
        assert key[0].isupper(), key


def test_branding_images():
    d = yaml.safe_load(calamares.branding_desc())
    images = d["images"]
    assert set(images) == {"productLogo", "productIcon", "productWelcome"}
    # productIcon is the WINDOW ICON: a real PNG shipped INTO the branding dir (so
    # QIcon(imagePath) loads it and OpenBox draws it on the titlebar). It must be the
    # branding-relative filename (no '/'), not a bare theme name -- see calamares.py.
    assert images["productIcon"] == calamares.PRODUCT_ICON_FILE
    assert images["productIcon"] == "productIcon.png"
    assert "/" not in images["productIcon"]
    # productLogo / productWelcome ship no PNG, so they stay empty (Calamares falls
    # back to its default pixmap instead of logging "does not exist").
    assert images["productLogo"] == ""
    assert images["productWelcome"] == ""


def test_branding_component_and_product_strings():
    d = yaml.safe_load(calamares.branding_desc())
    assert d["componentName"] == "azzio"
    assert d["strings"]["productName"] == "Azzio Linux"
    assert d["strings"]["bootloaderEntryName"] == "Azzio"


# --- module identity constants ---------------------------------------------

def test_module_identity_constants():
    assert calamares.BRANDING == "azzio"
    assert calamares.PRODUCT == "Azzio Linux"
    # The branding paths in emit_map interpolate BRANDING.
    m = calamares.emit_map()
    assert f"branding/{calamares.BRANDING}/branding.desc" in m
    assert f"branding/{calamares.BRANDING}/show.qml" in m


# --- every YAML file parses -------------------------------------------------

def test_every_yaml_value_parses():
    # The .qml slide is not YAML; everything else must load without raising, or
    # Calamares would fail to read it at runtime.
    for rel, content in calamares.emit_map().items():
        if rel.endswith(".qml"):
            continue
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict), rel


def test_qml_slide_carries_product_name():
    qml = calamares.branding_show_qml()
    # The product name is rendered across styled Text runs (blue "Az'", white
    # "arch") so the apostrophe must survive in the QML string literals.
    assert "Installing " in qml
    assert "Az'" in qml
    assert "arch" in qml
    assert " Linux" in qml
    assert "goToNextSlide()" in qml


def test_qml_slide_has_no_motivational_copy():
    # The installer must be "get out of my way": a status line only, no marketing.
    qml = calamares.branding_show_qml()
    lowered = qml.lower()
    for banned in ("minimal, fast", "a minimal", "fast arch-based", "welcome to",
                   "enjoy", "powerful", "beautiful", "experience"):
        assert banned not in lowered, f"motivational copy leaked: {banned!r}"
    # Only the neutral status line is allowed as human-facing copy.
    assert "Please wait while the system is being installed." in qml


def test_qml_and_branding_use_minimal_dark_palette():
    # The near-black bg + blue accent + slate muted must match the inspiration.
    qml = calamares.branding_show_qml()
    assert "#030712" in qml          # near-black page background
    assert "#3b82f6" in qml          # blue "Az'" accent
    assert "#64748b" in qml          # slate muted status line
    style = yaml.safe_load(calamares.branding_desc())["style"]
    assert style["SidebarBackground"] == "#070e1b"
    assert style["SidebarTextHighlight"] == "#3b82f6"
    assert style["SidebarTextSelect"] == "#ffffff"


# --- module split: facade re-export + submodule boundaries ------------------
# calamares.py is a thin FACADE: the builders live in config_settings /
# config_storage / config_system / config_branding and are re-exported so the flat
# `calamares.X` surface (which compiler.py and every test above use) is unchanged.
# These tests lock that structure so a future edit that breaks a re-export, moves a
# builder without re-exporting it, or drifts a builder's output fails LOUDLY here.

# Every emitted file -> the facade builder that produces it. Mirrors emit_map(); kept
# explicit so a rename/move that forgets the re-export is caught (AttributeError).
_BUILDER_FOR_FILE = {
    "settings.conf": "settings_conf",
    "modules/partition.conf": "partition_conf",
    "modules/unpackfs.conf": "unpackfs_conf",
    "modules/shellprocess.conf": "shellprocess_conf",
    "modules/shellprocess-desparse.conf": "shellprocess_desparsify_conf",
    "modules/users.conf": "users_conf",
    "modules/packages.conf": "packages_conf",
    "modules/mount.conf": "mount_conf",
    "modules/fstab.conf": "fstab_conf",
    "modules/locale.conf": "locale_conf",
    "modules/keyboard.conf": "keyboard_conf",
    "modules/initcpiocfg.conf": "initcpiocfg_conf",
    "modules/luksbootkeyfile.conf": "luksbootkeyfile_conf",
    "modules/services-systemd.conf": "services_conf",
    "modules/grubcfg.conf": "grubcfg_conf",
    "modules/bootloader.conf": "bootloader_conf",
    "modules/finished.conf": "finished_conf",
    "branding/azzio/branding.desc": "branding_desc",
    "branding/azzio/show.qml": "branding_show_qml",
}


def test_every_builder_reachable_via_facade_and_matches_emit_map():
    # Each builder is callable as calamares.<name>() (re-export works) AND the value
    # emit_map() stores for its file equals calling that builder directly. This is the
    # core guard that the split preserved the flat surface and the assembly point.
    m = calamares.emit_map()
    assert set(_BUILDER_FOR_FILE) == set(m) == EXPECTED_FILES
    for rel, builder_name in _BUILDER_FOR_FILE.items():
        builder = getattr(calamares, builder_name)  # AttributeError if not re-exported
        assert callable(builder), f"calamares.{builder_name} must be callable"
        assert m[rel] == builder(), f"emit_map[{rel!r}] must equal calamares.{builder_name}()"


def test_shared_constants_reexported_from_config_constants():
    # The 4 shared constants are DEFINED in config_constants and re-exported by the
    # facade; callers use calamares.BRANDING etc. Assert both resolve and agree.
    from packages.calamares import config_constants

    for name in ("BRANDING", "PRODUCT", "PRODUCT_ICON_FILE", "ARCHISO_SFS"):
        assert getattr(calamares, name) == getattr(config_constants, name)
    assert calamares.BRANDING == "azzio"


def test_config_submodules_are_independently_importable():
    # Each builder submodule imports and exposes its own builders WITHOUT importing the
    # facade (no circular import), so a future edit can work on one file in isolation.
    from packages.calamares import (
        config_branding,
        config_settings,
        config_storage,
        config_system,
    )

    assert config_settings.settings_conf().startswith("# Calamares master configuration")
    assert "btrfs" in config_storage.partition_conf()
    assert config_storage.unpackfs_conf().count("squashfs") == 1
    assert "doReusePassword: true" in config_system.users_conf()
    assert "systemctl -i reboot" in config_system.finished_conf()
    assert "componentName: azzio" in config_branding.branding_desc()


def test_facade_builders_are_the_submodule_builders():
    # The names on the facade ARE the submodule's functions (re-export, not re-def), so
    # there is exactly one source of truth per builder.
    from packages.calamares import config_storage, config_system

    assert calamares.users_conf is config_system.users_conf
    assert calamares.partition_conf is config_storage.partition_conf
