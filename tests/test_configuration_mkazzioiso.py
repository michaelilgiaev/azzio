"""packages.azzio.mkazzioiso -- Method B: the in-distro `azzio mkazzioiso` command.

Method A (compile.sh --ssh=...) rebuilds the SSH variant from the recipe. Method B ships
INSIDE the distribution and produces the SSH variant FROM THE RUNNING SYSTEM -- so packages
the user installed while live are captured. Same `--ssh="<PASSWORD>"` option, same security
posture: the password becomes `main`'s login credential, hashed sha-512 into the emitted
ISO's /etc/shadow; root stays locked; no default password is ever shipped.

HOW it captures the live state (mkarchiso is a pacstrap+overlay tool -- it cannot squash a
raw rootfs, so Method B works WITH it, not around it):
  * it copies the on-system archiso releng profile skeleton (syslinux/, efiboot/, grub/,
    pacman.conf, packages.x86_64, airootfs skeleton) so the profile mkarchiso validates is
    COMPLETE -- without this, mkarchiso aborts on missing required files;
  * it REGENERATES packages.x86_64 from `pacman -Qqe` (the currently-installed explicit
    packages), so anything installed while live is pacstrapped into the ISO -- THIS is the
    real "current state" capture (mkarchiso rebuilds a clean, bootable rootfs from it);
  * it overlays a CURATED, boot-safe slice into the profile's airootfs/: the user trees
    (/home, /usr/local, /opt, /etc/skel), the safe config dir /etc/calamares, and ONLY the
    exact azzio /etc FILES (passwd/group/gshadow, hostname, the azzio sudoers drop-ins, and
    the azzio systemd UNIT FILES + logind/udev drop-ins). It DELIBERATELY does NOT overlay
    whole /etc (boot-critical mkinitcpio.conf/fstab/machine-id must stay archiso's) NOR whole
    /etc/systemd/system (its enable-symlink forest would bake the HOST's enabled daemons --
    stock sshd, cloud-init, VM agents -- into the live ISO). Instead it re-creates the recipe's
    CURATED enable-links itself (ENABLE_LINKS, mirroring compiler._link_services);
  * it overlays the sshd variant's shadow (main hashed, root locked) + the sshd-hypervisor
    auto-setup service, and names the ISO azzio-sshd.

These are pure unit tests: they exercise argument parsing, the shadow builder, the overlay
allowlists (and that they exclude boot-critical /etc + the wholesale enable-symlink forest),
the curated enable-links, the exclude list, the packages.x86_64 regeneration, the profile
assembly plan, and the mkarchiso argv -- WITHOUT copying a real rootfs or running mkarchiso.
They also assert the command bundles into the guest CLI + is dispatched + advertised.
"""

from __future__ import annotations

import ast
import os
import re

import pytest

from packages.azzio import mkazzioiso as mk


# --- --ssh parsing: the variant's credential is REQUIRED ---------------------

def test_parse_ssh_absent_is_none():
    assert mk.parse_ssh_arg([]) is None
    assert mk.parse_ssh_arg(["--full"]) is None


def test_parse_ssh_empty_is_none():
    # `--ssh=` with a blank value -> None ("demands a string or it doesn't work").
    assert mk.parse_ssh_arg(["--ssh="]) is None


def test_parse_ssh_returns_password_and_keeps_equals():
    assert mk.parse_ssh_arg(["--ssh=hunter2"]) == "hunter2"
    # split("=", 1): a password containing '=' is not truncated.
    assert mk.parse_ssh_arg(["--ssh=a=b=c"]) == "a=b=c"


# --- shadow builder: main hashed, root locked --------------------------------

def test_shadow_for_locks_root_and_hashes_main():
    fake = "$6$salt$" + "d" * 86
    txt = mk.shadow_for(fake)
    rows = {l.split(":")[0]: l.split(":") for l in txt.splitlines()}
    assert rows["main"][1] == fake            # main carries the operator hash
    assert rows["root"][1] in ("!", "*")      # root stays locked
    for line in txt.splitlines():
        assert len(line.split(":")) == 9, line  # valid /etc/shadow rows


def test_shadow_for_rejects_non_hash():
    for bad in ("", "admin", "password"):
        with pytest.raises(ValueError):
            mk.shadow_for(bad)


# --- overlay allowlists: curated, boot-safe (NEVER whole /etc or the .wants forest) --

def test_overlay_sources_carry_user_data_and_skel_only():
    # User data + local software + the desktop skeleton -- boot-safe, no system config.
    src = set(mk.OVERLAY_SOURCES)
    for p in ("/home", "/usr/local", "/opt", "/etc/skel"):
        assert p in src, f"{p} should be overlaid into the ISO airootfs"
    # Whole /etc and whole /etc/systemd/system must NEVER be TREE-overlaid.
    assert "/etc" not in src
    assert "/etc/systemd/system" not in src


def test_overlay_never_tree_copies_whole_etc_or_systemd_system():
    # CRITICAL (boot + security): overlaying whole /etc clobbers releng's archiso boot
    # config (live /etc/mkinitcpio.conf has no `archiso` HOOKS -> unbootable initramfs;
    # /etc/fstab has host UUIDs). Overlaying whole /etc/systemd/system inherits the HOST's
    # enable-symlink forest (stock sshd, cloud-init, VM agents) into the live ISO. Neither
    # may appear as a wholesale tree/dir source.
    for coll in (mk.OVERLAY_SOURCES, mk.OVERLAY_ETC_DIRS):
        assert "/etc" not in coll
        assert "/etc/systemd/system" not in coll


def test_overlay_etc_files_are_exact_azzio_files_only():
    # We overlay ONLY exact azzio /etc FILES the recipe emits -- users/branding/sudoers +
    # the azzio UNIT FILES (not their enable symlinks). Pin the key members.
    files = set(mk.OVERLAY_ETC_FILES)
    for p in ("/etc/passwd", "/etc/group", "/etc/gshadow", "/etc/hostname",
              "/etc/sudoers.d/00-main", "/etc/systemd/system/pkgs-setup.service",
              "/etc/systemd/system/azzio-timedate.service",
              "/etc/systemd/system/getty@tty1.service.d/autologin.conf"):
        assert p in files, f"{p} (an azzio customization file) should be overlaid"
    # Boot-critical / host-specific files and the SHADOW must NOT be in the file overlay
    # (shadow is written separately with the hash; the live shadow must never leak).
    for bad in ("/etc/mkinitcpio.conf", "/etc/fstab", "/etc/machine-id",
                "/etc/crypttab", "/etc/shadow"):
        assert bad not in files, f"{bad} must NOT be overlaid"
    # Every overlaid /etc file is a FILE path, not a bare directory like /etc/sudoers.d.
    assert "/etc/sudoers.d" not in files
    assert "/etc/systemd/system" not in files


def test_shadow_is_not_in_any_overlay_source():
    # Defence in depth: the live /etc/shadow (which may hold real user hashes on an
    # installed system) must not ride in via ANY overlay list -- only shadow_for(hash)
    # writes the ISO's shadow.
    for coll in (mk.OVERLAY_SOURCES, mk.OVERLAY_ETC_DIRS, mk.OVERLAY_ETC_FILES):
        assert "/etc/shadow" not in coll
        assert "/etc" not in coll  # a bare /etc would sweep in shadow


def test_enable_links_mirror_the_recipe_curated_set():
    # The ISO's enabled daemons are AUTHORED here (mirroring compiler._link_services), NOT
    # inherited from the host. Pin the exact curated set: NetworkManager, CUPS,
    # spice-vdagentd, and the azzio oneshots -- and assert nothing host-specific/dangerous
    # (sshd, cloud-init, a display-manager) is in it.
    names = {name for _t, name in mk.ENABLE_LINKS}
    assert names == {
        "NetworkManager.service", "org.cups.cupsd.service", "spice-vdagentd.service",
        "locale-setup.service", "pkgs-setup.service",
        "azzio-sleep-policy.service", "azzio-timedate.service",
        # The virtiofs shared-folder auto-mount -- enabled on BOTH variants so the
        # host ./Shared folder appears at /home/main/Shared regardless of --ssh
        # (the fix for the headed-variant coupling). It is a .mount unit, not a
        # .service, but the enable-link mechanism is unit-type agnostic.
        "home-main-Shared.mount",
    }
    # The stock sshd is NOT auto-enabled here -- the sshd variant's controlled auto-setup is
    # the sshd-hypervisor-setup.service added by _overlay_sshd_variant, not stock sshd.
    for bad in ("sshd.service", "cloud-init.service", "gdm.service", "sddm.service",
                "systemd-homed.service", "bluetooth.service"):
        assert bad not in names, f"{bad} must NOT be baked into the live ISO's enabled set"


# --- rsync excludes: never ship volatile / pseudo / self ---------------------

def test_rsync_excludes_pseudo_and_volatile_filesystems():
    ex = set(mk.RSYNC_EXCLUDES)
    # Volatile / privacy-sensitive trees under the overlaid roots.
    for p in ("*/.cache/*", "/root/.cache/*", "/tmp/*", "/var/tmp/*"):
        assert p in ex, f"{p} should be excluded"
    # The mounted virtiofs `Shared` folder (host keys) must not ship.
    assert "*/Shared/*" in ex


def test_excludes_for_adds_the_runtime_workdir():
    # excludes_for(work) returns the static excludes PLUS the concrete work dir, so an
    # overlay rsync never copies the profile it is currently assembling.
    ex = mk.excludes_for("/var/tmp/mkazzioiso.work")
    assert "/var/tmp/mkazzioiso.work/*" in ex
    # and it still carries the static ones.
    assert "*/.cache/*" in ex


# --- SECURITY: the /home overlay must never ship the operator's secrets ------

def test_secret_excludes_cover_the_critical_credential_stores():
    # Overlaying whole /home would otherwise bake SSH private keys, the GPG private
    # keyring, shell history, saved passwords, cloud tokens, and browser credential
    # stores into the DISTRIBUTED ISO -- readable by the autologin `main` user. Pin the
    # critical patterns so a future edit can never silently drop them.
    ex = set(mk.RSYNC_EXCLUDES)
    for pat in ("*/.ssh/*", "*/.gnupg/*", "*/.bash_history",
                "*/.local/share/keyrings/*", "*/.config/rclone/*", "*/.netrc",
                "*/.git-credentials", "*/.password-store/*", "*/.mozilla/*",
                "*/.aws/*", "*/.pki/*"):
        assert pat in ex, f"{pat} must be excluded from the /home overlay (secret leak)"


def test_secret_excludes_cover_azzio_native_stores():
    # azzio ships its OWN secret stores: the `passwords` manager keeps ~/Vault/passwords.txt
    # (cleartext, can persist after a hard kill) + ~/Vault/passwords.txt.gpg, and `backup`
    # writes ~/backup.tar.gz.gpg + ~/passwords.tar.gz.gpg to HOME. These are shipped by this
    # distro (~/Vault is in /etc/skel), so they are squarely in scope and MUST be excluded.
    import fnmatch
    ex = mk.RSYNC_EXCLUDES
    for leaky in ("/home/main/Vault/passwords.txt",
                  "/home/main/Vault/passwords.txt.gpg",
                  "/home/main/backup.tar.gz.gpg",
                  "/home/main/passwords.tar.gz.gpg"):
        assert any(fnmatch.fnmatch(leaky, pat) for pat in ex), \
            f"{leaky} (an azzio-native secret store) would LEAK -- add an exclude"


def test_secret_paths_actually_match_the_exclude_patterns():
    # Belt-and-suspenders: prove the patterns really match the concrete secret files rsync
    # would see (fnmatch mirrors rsync's shell-style matching for these anchored globs).
    import fnmatch
    leaky = [
        "/home/main/.ssh/id_ed25519",
        "/home/main/.ssh/authorized_keys",
        "/home/main/.gnupg/private-keys-v1.d/ABC.key",
        "/home/main/.bash_history",
        "/home/main/.local/share/keyrings/login.keyring",
        "/home/main/.config/rclone/rclone.conf",
        "/home/main/.mozilla/firefox/abc.default/logins.json",
    ]
    for path in leaky:
        assert any(fnmatch.fnmatch(path, pat) for pat in mk.RSYNC_EXCLUDES), \
            f"{path} would LEAK -- no exclude pattern matches it"
    # And ordinary user data is NOT excluded (the feature still captures it).
    for keep in ("/home/main/Documents/notes.txt", "/home/main/.config/openbox/rc.xml",
                 "/home/main/Desktop/thing.desktop"):
        assert not any(fnmatch.fnmatch(keep, pat) for pat in mk.RSYNC_EXCLUDES), \
            f"{keep} should be captured, not excluded"


# --- the on-system releng skeleton the profile is built from -----------------

def test_releng_profile_path_is_the_archiso_default():
    # mkarchiso REQUIRES a complete profile (boot dirs, pacman.conf, packages.x86_64).
    # Method B seeds it from the archiso-installed releng profile, exactly like the
    # recipe build's _copy_releng.
    assert mk.RELENG_PROFILE == "/usr/share/archiso/configs/releng"


# --- packages.x86_64 regenerated from the CURRENT install --------------------

def test_current_packages_command_lists_native_explicit_packages():
    # The captured package set comes from `pacman -Qqen` (explicitly-installed, NATIVE
    # packages -- i.e. available in a configured repo). This is the "ship the current
    # state" feature that distinguishes Method B from Method A. It is deliberately NATIVE
    # (-n): foreign/AUR packages (`-Qqem`) are not in any repo, so pacstrap inside
    # mkarchiso could not install them and would ABORT -- filtering to native keeps the
    # emitted build sound. (Documented limitation: AUR packages are not re-shipped.)
    assert mk.CURRENT_PACKAGES_CMD == ["pacman", "-Qqen"]


# --- profile emission: sshd variant name + auto-setup service ----------------

def test_profiledef_names_the_sshd_iso():
    pd = mk.profiledef_sh()
    # Method B builds the ssh flavour of the headed line -> azzio-headed-ssh (matches
    # the recipe's profile.ISO_NAME_SSHD).
    assert 'iso_name="azzio-headed-ssh"' in pd
    assert pd.startswith("#!/usr/bin/env bash")


def test_guest_bootstrap_zstd_threads_are_capped_not_all_cores():
    # The guest `azzio mkazzioiso` tool runs mkarchiso from inside the live system, so
    # its bootstrap-tarball zstd must ALSO leave headroom instead of grabbing every core
    # with -T0 -- otherwise building the ssh ISO from a running Azzio freezes the box.
    pd = mk.profiledef_sh(threads=20)
    assert "'-T0'" not in pd
    assert "'-T20'" in pd


def test_guest_bootstrap_zstd_defaults_to_a_positive_capped_count():
    # With no explicit count it computes an affinity-aware, headroom-leaving default
    # on its own (the guest tool is standalone and cannot import the build-host cap),
    # and that default is a concrete positive integer -- never -T0.
    pd = mk.profiledef_sh()
    assert "'-T0'" not in pd
    assert re.search(r"'-T([1-9]\d*)'", pd), pd


def test_guest_squashfs_processors_capped_in_tool_options():
    # Same mkarchiso reality as the recipe build: the -processors cap for mksquashfs
    # must ride on airootfs_image_tool_options (mkarchiso ignores MKSQUASHFS_OPTIONS),
    # so a guest-side ISO build does not pin every core during squashfs compression.
    pd = mk.profiledef_sh(threads=20)
    tool_line = [l for l in pd.splitlines() if "airootfs_image_tool_options=" in l][0]
    assert "'-comp' 'zstd'" in tool_line
    assert "'-processors' '20'" in tool_line


def test_guest_squashfs_processors_defaults_to_positive_count():
    pd = mk.profiledef_sh()
    tool_line = [l for l in pd.splitlines() if "airootfs_image_tool_options=" in l][0]
    assert re.search(r"'-processors' '([1-9]\d*)'", tool_line), tool_line


def test_sshd_service_matches_the_baked_variant():
    # The auto-setup unit Method B writes must run the same subcommand the baked sshd
    # ISO does, so a live-generated ISO behaves identically.
    svc = mk.SSHD_HYPERVISOR_SETUP_SERVICE
    assert "ExecStart=/usr/local/bin/azzio --sshd-hypervisor" in svc
    assert "Environment=SUDO_USER=main" in svc
    assert "WantedBy=multi-user.target" in svc


def test_shared_mount_unit_is_virtiofs_at_home_main_shared():
    # The virtiofs auto-mount unit baked into EVERY variant. Its name must encode the
    # mount path (systemd requirement: home-main-Shared.mount <-> /home/main/Shared),
    # it mounts the "shared" virtiofs tag (an opaque identifier -- stays lowercase even
    # though the dir is "Shared"), and it is WantedBy multi-user.target so it comes up
    # on boot on the headed variant too (no ssh service involved).
    unit = mk.HOME_MAIN_SHARED_MOUNT
    assert "[Mount]" in unit
    assert "What=shared" in unit               # the virtiofs mount tag (opaque, lowercase)
    assert "Where=/home/main/Shared" in unit
    assert "Type=virtiofs" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "9p" not in unit
    # It must be overlaid as a real unit file AND enabled.
    assert "/etc/systemd/system/home-main-Shared.mount" in mk.OVERLAY_ETC_FILES


def test_shared_mount_unit_bodies_match_across_build_paths():
    # Method B's inline copy must be byte-identical to the recipe's system.py body, so a
    # live-generated ISO mounts the share exactly like a recipe-built one.
    import system
    assert mk.HOME_MAIN_SHARED_MOUNT == system.HOME_MAIN_SHARED_MOUNT


# --- mkarchiso argv -----------------------------------------------------------

def test_mkarchiso_argv_targets_the_profile_and_output():
    argv = mk.mkarchiso_argv(profile_dir="/w/profile", work_dir="/w/work",
                             out_dir="/w/out")
    assert argv[0] == "mkarchiso"
    # -w work, -o out, and the profile dir as the final positional.
    assert "-w" in argv and "/w/work" in argv
    assert "-o" in argv and "/w/out" in argv
    assert argv[-1] == "/w/profile"


# --- functional: enable-links + sshd overlay operate on a real airootfs ------

def _local_sudo(monkeypatch):
    """Inject unprivileged local stand-ins for the bundled _sudo/_sudo_write helpers so the
    overlay functions can be exercised on tmp paths without real root."""
    import subprocess as _sp

    def _sudo(*args, check=True):
        return _sp.run(list(args), check=check).returncode

    def _sudo_write(path, content):
        with open(path, "w") as f:
            f.write(content)

    monkeypatch.setattr(mk, "_sudo", _sudo, raising=False)
    monkeypatch.setattr(mk, "_sudo_write", _sudo_write, raising=False)


def test_enable_curated_services_creates_only_the_curated_links(monkeypatch, tmp_path):
    _local_sudo(monkeypatch)
    airootfs = str(tmp_path / "airootfs")
    mk._enable_curated_services(airootfs)
    wants = tmp_path / "airootfs/etc/systemd/system/multi-user.target.wants"
    created = {p.name: os.readlink(p) for p in wants.iterdir()}
    # Exactly the curated set, each a symlink to its ENABLE_LINKS target.
    assert set(created) == {name for _t, name in mk.ENABLE_LINKS}
    assert created["NetworkManager.service"] == "/usr/lib/systemd/system/NetworkManager.service"
    # No host-inherited enable-symlinks (e.g. stock sshd) appear.
    assert "sshd.service" not in created


def test_overlay_sshd_variant_writes_hashed_shadow_and_enables_the_auto_setup(monkeypatch, tmp_path):
    _local_sudo(monkeypatch)
    profile = str(tmp_path / "profile")
    os.makedirs(os.path.join(profile, "airootfs/etc"), exist_ok=True)
    fake_hash = "$6$salt$" + "z" * 86
    mk._overlay_sshd_variant(profile, fake_hash)
    # profiledef names the ssh ISO (headed line).
    assert 'iso_name="azzio-headed-ssh"' in (tmp_path / "profile/profiledef.sh").read_text()
    # shadow: main hashed, root locked (the sole writer of the ISO shadow).
    shadow = (tmp_path / "profile/airootfs/etc/shadow").read_text()
    rows = {l.split(":")[0]: l.split(":")[1] for l in shadow.splitlines()}
    assert rows["main"] == fake_hash and rows["root"] in ("!", "*")
    # the sshd-hypervisor auto-setup service is emitted AND enabled.
    sysd = tmp_path / "profile/airootfs/etc/systemd/system"
    assert (sysd / "sshd-hypervisor-setup.service").is_file()
    link = sysd / "multi-user.target.wants/sshd-hypervisor-setup.service"
    assert link.is_symlink()
    assert os.readlink(link) == "/etc/systemd/system/sshd-hypervisor-setup.service"


# --- bundling + dispatch + usage ---------------------------------------------

def _bundle() -> str:
    from packages.azzio.bundle import bundle_source
    return bundle_source()


def test_mkazzioiso_is_bundled_and_dispatched():
    src = _bundle()
    ast.parse(src)  # the whole bundle stays valid Python with the new module in it
    # The command function is present, main() dispatches `mkazzioiso`, usage advertises it.
    assert "def cmd_mkazzioiso(" in src
    assert 'cmd == "mkazzioiso"' in src
    assert "mkazzioiso" in src
    # It parses --ssh and refuses to run without it (the credential is required).
    assert "parse_ssh_arg(" in src


def test_mkazzioiso_module_is_before_command_line_interface_in_order():
    from packages.azzio.bundle import MODULE_ORDER
    assert "mkazzioiso.py" in MODULE_ORDER
    assert MODULE_ORDER.index("mkazzioiso.py") < MODULE_ORDER.index("command_line_interface.py")


def test_cmd_mkazzioiso_errors_when_releng_profile_absent(monkeypatch, tmp_path, capsys):
    # mkarchiso needs a complete profile seeded from the on-system releng skeleton. If
    # that skeleton is missing (archiso not installed / trimmed), fail LOUDLY before any
    # build work rather than hand mkarchiso an incomplete profile it would abort on.
    import sys as _sys
    monkeypatch.setattr(mk, "_err", lambda m: print(m, file=_sys.stderr), raising=False)
    monkeypatch.setattr(mk, "_have", lambda p: True, raising=False)
    monkeypatch.setattr(mk, "_sudo", lambda *a, **k: 0, raising=False)
    monkeypatch.setattr(mk, "_sudo_write", lambda *a, **k: None, raising=False)
    # openssl hashing must succeed so we reach the releng check.
    monkeypatch.setattr(mk, "_hash_password", lambda p: "$6$s$" + "x" * 86, raising=False)
    # Point RELENG_PROFILE at a nonexistent path.
    monkeypatch.setattr(mk, "RELENG_PROFILE", str(tmp_path / "nope"), raising=False)
    rc = mk.cmd_mkazzioiso(["--ssh=secret"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "releng" in err.lower() or "archiso" in err.lower()


def test_cmd_mkazzioiso_without_ssh_is_an_error(monkeypatch, capsys):
    # No --ssh -> non-zero, and it must NOT attempt a build (the credential is required;
    # Method B only produces the SSH variant). The module calls the bundled common
    # helpers by bare name; standalone-imported, `_err` is absent, so inject a stderr
    # shim (matching the bundled _err) exactly as the backup-target tests do.
    import sys as _sys
    monkeypatch.setattr(mk, "_err", lambda m: print(m, file=_sys.stderr), raising=False)
    # Guard: if the credential check somehow falls through, these would explode rather
    # than silently shelling out -- so the test proves the early return really happens.
    monkeypatch.setattr(mk, "_have", lambda p: True, raising=False)
    monkeypatch.setattr(mk, "_sudo", lambda *a, **k: 0, raising=False)
    rc = mk.cmd_mkazzioiso([])
    assert rc != 0
    err = capsys.readouterr().err
    assert "--ssh" in err
