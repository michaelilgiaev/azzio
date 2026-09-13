"""Cross-cutting invariants for the build driver.

compiler.STEP_WEIGHTS must stay in lockstep with the number of bar.step() calls in
compiler.run() -- the source itself says "len(STEP_WEIGHTS) - 1 MUST equal the number
of bar.step() calls in run()". If they drift, the progress bar mis-weights and the
final "[ N/N ]" count is wrong. We count the calls from the actual source of run()
so adding/removing a step without updating the weights fails this test.

compiler.cache_is_complete() is the pure cache-first predicate; it reads only
paths.* and one env var, all monkeypatchable.
"""

from __future__ import annotations

import inspect

import compiler
import makepkg
import paths


def _use_fingerprint_dir(monkeypatch, repo, *, stamp_current):
    """Point paths.PKG_FINGERPRINTS at `repo` for the test, and (when stamp_current)
    write the current-recipe fingerprint sidecar for each own package there.

    cache_is_complete() now treats an own package as "present" only if it was built
    from the CURRENT recipe (a recipe-fingerprint match), which is what stops a stale
    calamares -- one built before the networkq patch -- from being reused. A test that
    wants a genuinely up-to-date cache must stamp the matching fingerprints (exactly as
    a real makepkg build does); one simulating an old cache leaves them absent."""
    monkeypatch.setattr(compiler.paths, "PKG_FINGERPRINTS", repo)
    if stamp_current:
        for name, fp in makepkg._current_recipe_fingerprints(full_compile=False).items():
            makepkg._write_recipe_fingerprint(repo, name, fp)


def test_ckbcomp_asset_is_vendored_python_script():
    # Calamares' keyboard preview shells out to `ckbcomp` to render key legends;
    # Arch does not package it, so we vendor it as a companion file of the calamares package
    # (libraries/packages/calamares/ckbcomp.py) -- a Python 3 port of the upstream Perl ckbcomp
    # (byte-identical output, but no Perl in the tree). It must exist and be that Python script
    # (not an empty placeholder).
    src = paths.CKBCOMP_SRC
    assert src.is_file(), "libraries/packages/calamares/ckbcomp.py is missing"
    head = src.read_text(errors="ignore")[:200]
    assert head.startswith("#!/usr/bin/env python3")
    assert "ckbcomp" in head  # the script's own banner names itself


def test_run_installs_ckbcomp_into_usr_bin():
    # run() must plant the vendored ckbcomp at /usr/bin/ckbcomp (executable) so the
    # keyboard preview finds it. Assert the copy_data call is present in run().
    src = inspect.getsource(compiler.run)
    assert 'copy_data("calamares/ckbcomp.py"' in src
    assert 'usr/bin/ckbcomp' in src


def test_run_emits_the_cli_installer_script():
    # The scripted (terminal/SSH) installer -- the CLI half of azzio-install -- must be
    # baked into the ISO under /root/azzio so `azzio-install --cli` can install over SSH.
    # Assert run() writes installer.installer_sh() to azzio-install-cli.sh (executable).
    src = inspect.getsource(compiler.run)
    assert 'installer.installer_sh()' in src
    assert 'azzio-install-cli.sh' in src


def test_emit_calamares_ships_the_window_icon_into_branding():
    # The installer's WINDOW ICON (the "Az'" tile OpenBox draws on the titlebar) is the
    # branding productIcon: a real PNG copied INTO branding/azzio/. Assert _emit_calamares
    # copies the standardized installer icon asset to the branding productIcon file, so the
    # topbar icon exists and matches the launcher icon.
    from packages.calamares import calamares
    from packages import openbox

    src = inspect.getsource(compiler._emit_calamares)
    # The productIcon is rasterized from the standardized SVG master to a real PNG
    # (Calamares' QIcon loads a raster file directly).
    assert "render_svg_png" in src
    assert "INSTALLER_ICON_ASSET" in src
    assert "PRODUCT_ICON_FILE" in src
    # The branding.desc names that same file in productIcon.
    assert calamares.PRODUCT_ICON_FILE == "productIcon.png"
    assert openbox.INSTALLER_ICON_ASSET == "icons/azzio.svg"


# --- power management emission + enablement (Tasks 1 & 2) -------------------

def test_run_calls_emit_power():
    # run() must emit the power-management files (lid/button + PC/laptop idle sleep).
    src = inspect.getsource(compiler.run)
    assert "_emit_power(airootfs)" in src


def test_emit_power_writes_all_four_artifacts(tmp_path):
    # BEHAVIORAL: _emit_power lays down the four root-owned power files under a fresh
    # airootfs -- the static logind drop-in, the sleep-policy script (executable), its
    # service, and the udev rule. These reach the installed system via unpackfs.
    import system

    airootfs = tmp_path / "airootfs"
    compiler._emit_power(airootfs)

    dropin = airootfs / "etc/systemd/logind.conf.d/10-azzio-power.conf"
    script = airootfs / "usr/local/bin/azzio-sleep-policy"
    service = airootfs / "etc/systemd/system/azzio-sleep-policy.service"
    udev = airootfs / "etc/udev/rules.d/99-azzio-sleep-policy.rules"

    assert dropin.read_text() == system.LOGIND_POWER_DROPIN
    assert script.read_text() == system.SLEEP_POLICY_SCRIPT
    assert service.read_text() == system.SLEEP_POLICY_SERVICE
    assert udev.read_text() == system.SLEEP_POLICY_UDEV_RULE
    # The policy script must be executable (a service ExecStart on a non-exec file
    # would fail to run).
    import os
    import stat
    assert os.stat(script).st_mode & stat.S_IXUSR


def test_link_services_enables_sleep_policy(tmp_path):
    # BEHAVIORAL: _link_services must create the multi-user.target.wants symlink that
    # enables azzio-sleep-policy.service on boot (both ISOs + installed system).
    airootfs = tmp_path / "airootfs"
    (airootfs / "etc/systemd/system").mkdir(parents=True)
    compiler._link_services(airootfs)

    link = (airootfs / "etc/systemd/system/multi-user.target.wants"
            / "azzio-sleep-policy.service")
    assert link.is_symlink()
    import os
    assert os.readlink(link) == "/etc/systemd/system/azzio-sleep-policy.service"


def test_link_services_enables_spice_vdagentd(tmp_path):
    # BEHAVIORAL: _link_services must enable spice-vdagentd -- the SPICE guest agent daemon that
    # bridges the com.redhat.spice.0 channel so the session spice-vdagent can sync the guest
    # pointer with the SPICE client. This is the fix for the SPICE-guest pointer regression (no
    # hover / dropped clicks / stuck labels); without the daemon the agent has nothing to talk
    # to. Enabled on both ISOs + (via unpackfs) the installed system.
    import os
    airootfs = tmp_path / "airootfs"
    (airootfs / "etc/systemd/system").mkdir(parents=True)
    compiler._link_services(airootfs)
    link = (airootfs / "etc/systemd/system/multi-user.target.wants"
            / "spice-vdagentd.service")
    assert link.is_symlink()
    assert os.readlink(link) == "/usr/lib/systemd/system/spice-vdagentd.service"


def test_link_services_enables_shared_virtiofs_mount(tmp_path):
    # BEHAVIORAL: _link_services must enable the virtiofs shared-folder mount on BOTH
    # variants (this is the fix for the headed-variant coupling -- the share must
    # appear without the ssh bring-up). A .mount enable-link is a symlink named after
    # the unit, exactly like a .service one.
    import os
    airootfs = tmp_path / "airootfs"
    (airootfs / "etc/systemd/system").mkdir(parents=True)
    compiler._link_services(airootfs)
    link = (airootfs / "etc/systemd/system/multi-user.target.wants"
            / "home-main-Shared.mount")
    assert link.is_symlink()
    assert os.readlink(link) == "/etc/systemd/system/home-main-Shared.mount"


def test_link_services_masks_archiso_networkd_stack(tmp_path):
    # BEHAVIORAL + the crux of the "static IP not applied on the installed system" fix:
    # _link_services must MASK archiso's stock systemd-networkd/systemd-resolved units
    # (symlink -> /dev/null) so NetworkManager is the SOLE network manager. Without this
    # networkd wins the race for the interface, DHCPs it, and NM's static profile stays
    # inactive. Masks live in the airootfs and reach the installed target via unpackfs.
    import os
    airootfs = tmp_path / "airootfs"
    (airootfs / "etc/systemd/system").mkdir(parents=True)
    compiler._link_services(airootfs)
    base = airootfs / "etc/systemd/system"
    # Every unit in the mask list must be a symlink to /dev/null (a real mask).
    assert compiler._ARCHISO_NETWORK_UNITS_TO_MASK  # non-empty contract
    for unit in compiler._ARCHISO_NETWORK_UNITS_TO_MASK:
        link = base / unit
        assert link.is_symlink(), f"{unit} must be masked (symlink)"
        assert os.readlink(link) == "/dev/null", f"{unit} must be masked -> /dev/null"
    # The units we mask must cover BOTH stacks' .service, the wait-online unit, the
    # generator, AND every socket that socket-activates them -- each such socket is
    # WantedBy=sockets.target, so leaving one un-masked lets it start at boot and log a
    # failed activation of the (masked) service. Masking them all keeps the rule "mask
    # anything that can re-pull it" complete. (Verified against the releng systemd unit
    # surface: these are the enabled networkd/resolved sockets.)
    for expected in (
        "systemd-networkd.service",
        "systemd-networkd.socket",
        "systemd-networkd-wait-online.service",
        "systemd-networkd-varlink.socket",
        "systemd-networkd-varlink-metrics.socket",
        "systemd-networkd-resolve-hook.socket",
        "systemd-network-generator.service",
        "systemd-resolved.service",
        "systemd-resolved-varlink.socket",
        "systemd-resolved-monitor.socket",
    ):
        assert expected in compiler._ARCHISO_NETWORK_UNITS_TO_MASK
    # NetworkManager itself must NOT be masked (it is the stack we keep).
    nm_mask = base / "NetworkManager.service"
    assert not (nm_mask.is_symlink() and os.readlink(nm_mask) == "/dev/null")


def test_link_services_enables_nm_wait_online_into_network_online_target(tmp_path):
    # BEHAVIORAL: because _link_services enables NetworkManager via a MANUAL .wants
    # symlink (not `systemctl enable`), NM.service's `Also=NetworkManager-wait-online`
    # is NOT processed, and we mask systemd-networkd-wait-online. So without an explicit
    # enable, nothing feeds network-online.target on the LIVE ISO and locale-setup
    # (Wants=+After=network-online.target) would race an un-configured network.
    # _link_services must enable NM's own wait-online into network-online.target.wants.
    import os
    airootfs = tmp_path / "airootfs"
    (airootfs / "etc/systemd/system").mkdir(parents=True)
    compiler._link_services(airootfs)
    link = (airootfs / "etc/systemd/system/network-online.target.wants"
            / "NetworkManager-wait-online.service")
    assert link.is_symlink(), "NetworkManager-wait-online must be enabled for network-online.target"
    assert os.readlink(link) == "/usr/lib/systemd/system/NetworkManager-wait-online.service"
    # And it must NOT be masked (that would defeat the point).
    assert "NetworkManager-wait-online.service" not in compiler._ARCHISO_NETWORK_UNITS_TO_MASK


def test_link_services_removes_archiso_network_configs(tmp_path):
    # BEHAVIORAL: _link_services must remove releng's /etc/systemd/network/*.network
    # DHCP match files -- with resolved/networkd masked, a lingering match file could
    # still grab an interface if networkd were ever started manually. NetworkManager
    # owns every device instead.
    airootfs = tmp_path / "airootfs"
    netdir = airootfs / "etc/systemd/network"
    netdir.mkdir(parents=True)
    # Stand in for the releng files.
    for name in ("20-ethernet.network", "20-wlan.network", "20-wwan.network"):
        (netdir / name).write_text("[Network]\nDHCP=yes\n")
    # A non-.network file must be left untouched (only match files are removed).
    (netdir / "keep.conf").write_text("x")
    (airootfs / "etc/systemd/system").mkdir(parents=True)
    compiler._link_services(airootfs)
    assert sorted(p.name for p in netdir.glob("*.network")) == []
    assert (netdir / "keep.conf").exists()


def test_link_services_resets_resolv_conf_to_real_file(tmp_path):
    # BEHAVIORAL: _link_services must replace archiso's /etc/resolv.conf -> resolved
    # stub SYMLINK with a real (non-symlink) file. Once resolved is masked the stub
    # symlink dangles (no DNS); NetworkManager (dns=default) refuses to clobber a
    # symlink it does not own, so a REAL file is required for runtime DNS to work.
    import os
    airootfs = tmp_path / "airootfs"
    etc = airootfs / "etc"
    (etc / "systemd/system").mkdir(parents=True)
    # Simulate archiso's inherited stub symlink (dangling target is fine for the test).
    resolv = etc / "resolv.conf"
    os.symlink("/run/systemd/resolve/stub-resolv.conf", resolv)
    assert resolv.is_symlink()
    compiler._link_services(airootfs)
    assert resolv.exists()
    assert not resolv.is_symlink(), "resolv.conf must be a real file, not a symlink"
    # It carries the NetworkManager-managed placeholder content.
    assert resolv.read_text() == compiler.RESOLV_CONF_PLACEHOLDER


def test_run_calls_neutralize_via_link_services():
    # The neutralization must run as part of the always-on unit/policy step so it
    # applies to BOTH ISOs and (via unpackfs) the installed target. Guard that
    # _link_services delegates to the helper (prevents the wiring from silently
    # regressing back to a networkd-vs-NM race).
    src = inspect.getsource(compiler._link_services)
    assert "_neutralize_archiso_network_stack(airootfs)" in src


def test_emit_shared_mount_writes_unit_and_mountpoint(tmp_path):
    # BEHAVIORAL: the emitter writes the virtiofs .mount unit body and creates the
    # /home/main/Shared mountpoint so systemd has somewhere to mount onto.
    import system
    airootfs = tmp_path / "airootfs"
    (airootfs / "etc/systemd/system").mkdir(parents=True)
    (airootfs / "home/main").mkdir(parents=True)
    compiler._emit_shared_mount(airootfs)
    unit = airootfs / "etc/systemd/system/home-main-Shared.mount"
    assert unit.is_file()
    assert unit.read_text() == system.HOME_MAIN_SHARED_MOUNT
    assert (airootfs / "home/main/Shared").is_dir()


def test_run_calls_emit_homedir():
    # run() must create the home-directory layout (folders + convenience symlinks) between
    # the desktop overlay and the app overlay, so _emit_apps's closing chown covers it.
    src = inspect.getsource(compiler.run)
    assert "_emit_homedir(airootfs, home)" in src


def test_emit_homedir_creates_layout_in_home_and_skel(tmp_path):
    # BEHAVIORAL: _emit_homedir lays down the top-level folders, the XDG trash chain and the
    # convenience symlinks in BOTH /home/main and /etc/skel (so a Calamares-created user
    # inherits the identical layout). Symlinks must be relative (valid in every home).
    import os

    from packages.file_manager import home_directory as hd

    airootfs = tmp_path / "airootfs"
    home = airootfs / "home/main"
    home.mkdir(parents=True)
    (airootfs / "etc/skel").mkdir(parents=True)

    compiler._emit_homedir(airootfs, home)

    for root in (home, airootfs / "etc/skel"):
        # 1. Every top-level directory exists and is a real dir.
        for name in hd.DIRECTORIES:
            assert (root / name).is_dir(), f"missing dir {name} under {root}"
        # 2. The XDG trash chain exists (files + info) so Trash does not dangle.
        for rel in hd.TRASH_DIRS:
            assert (root / rel).is_dir(), f"missing trash dir {rel} under {root}"
        # 3. Every convenience symlink exists, is a symlink, and has a RELATIVE target.
        for name, target in hd.LINKS:
            link = root / name
            assert link.is_symlink(), f"{name} is not a symlink under {root}"
            got = os.readlink(link)
            assert got == target, f"{name} -> {got!r}, expected {target!r}"
            assert not os.path.isabs(got), f"{name} target must be relative: {got!r}"
        # 4. The Trash symlink resolves to a real directory (the chain made it non-dangling).
        assert (root / "Trash").resolve().is_dir()
        # 5. The .ssh dir is pre-created (0700) so the SSH symlink resolves too and sshd accepts
        #    it -- shipped by default, no longer only created at the runtime sshd bring-up.
        ssh_dir = root / hd.SSH_DIR
        assert ssh_dir.is_dir(), f"missing {hd.SSH_DIR} under {root}"
        assert (os.stat(ssh_dir).st_mode & 0o777) == hd.SSH_DIR_MODE
        assert (root / "SSH").resolve().is_dir()


def test_brand_boot_menus_writes_all_six_boot_files(tmp_path):
    # BEHAVIORAL: _brand_boot_menus lays the rebranded systemd-boot + syslinux menus
    # over a copied releng tree. Assert the exact six files land with our content.
    import system

    W = tmp_path
    compiler._brand_boot_menus(W)

    e01 = W / "efiboot/loader/entries/01-archiso-linux.conf"
    e02 = W / "efiboot/loader/entries/02-archiso-speech-linux.conf"
    loader = W / "efiboot/loader/loader.conf"
    syssys = W / "syslinux/archiso_sys.cfg"
    syscfg = W / "syslinux/archiso_sys-linux.cfg"
    syshead = W / "syslinux/archiso_head.cfg"

    assert e01.read_text() == system.BOOT_UEFI_LINUX
    assert e02.read_text() == system.BOOT_UEFI_SPEECH
    assert loader.read_text() == system.BOOT_UEFI_LOADER
    assert syssys.read_text() == system.BOOT_BIOS_SYSLINUX_SYS
    assert syscfg.read_text() == system.BOOT_BIOS_SYSLINUX
    assert syshead.read_text() == system.BOOT_BIOS_SYSLINUX_HEAD


def test_brand_boot_menus_deletes_releng_memtest_entry(tmp_path):
    # BEHAVIORAL + the crux of the "skip the EFI options" change: the releng Memtest86+
    # entry copied by _copy_releng must be GONE afterwards, leaving only 01/02. (EFI
    # Shell / firmware are auto entries suppressed by loader.conf, tested in
    # test_configuration_system; here we prove the explicit memtest .conf is removed.)
    W = tmp_path
    entries = W / "efiboot/loader/entries"
    entries.mkdir(parents=True)
    memtest = entries / "03-archiso-memtest86+x64.conf"
    memtest.write_text("title    Memtest86+\n")  # stand in for the releng file

    compiler._brand_boot_menus(W)

    assert not memtest.exists(), "releng Memtest86+ entry must be deleted"
    remaining = sorted(p.name for p in entries.glob("*.conf"))
    assert remaining == ["01-archiso-linux.conf", "02-archiso-speech-linux.conf"]


def test_brand_boot_menus_is_idempotent_without_memtest(tmp_path):
    # The memtest deletion uses missing_ok=True so a future releng that renames/drops
    # the entry (nothing to delete) does not crash the compiler. Running against a tree
    # with no memtest entry must succeed and still write the two Azzio entries.
    W = tmp_path
    compiler._brand_boot_menus(W)  # no pre-existing entries dir at all
    assert (W / "efiboot/loader/entries/01-archiso-linux.conf").exists()
    assert not (W / "efiboot/loader/entries/03-archiso-memtest86+x64.conf").exists()


def test_run_calls_brand_boot_menus():
    # run()'s step 4 must delegate to the helper (guards against the inline block
    # creeping back and diverging from the tested helper).
    src = inspect.getsource(compiler.run)
    assert "_brand_boot_menus(W)" in src


def test_step_weights_match_number_of_steps():
    # run() makes N literal bar.step() calls, but the final one is inside the
    # per-variant finalize loop. The bar is sized for the MAX variant set, so the
    # milestone count uses len(VARIANTS): (N - 1) + len(VARIANTS). STEP_WEIGHTS must
    # have exactly that many real entries (+ the index-0 sentinel). (A given run builds
    # only one variant, but the bar is over-sized to the max and finalize() snaps it.)
    src = inspect.getsource(compiler.run)
    n_calls = src.count("bar.step(")
    executed = (n_calls - 1) + len(compiler.VARIANTS)
    assert len(compiler.STEP_WEIGHTS) - 1 == executed, (
        f"STEP_WEIGHTS has {len(compiler.STEP_WEIGHTS)} entries "
        f"(-> {len(compiler.STEP_WEIGHTS) - 1} steps) but run() executes {executed} "
        f"milestones ({n_calls} literal bar.step() calls, the last once per "
        f"{len(compiler.VARIANTS)} variants)"
    )


def test_step_weights_leading_zero():
    # The first weight is the 0-weight "already at step 0" anchor.
    assert compiler.STEP_WEIGHTS[0] == 0


def test_step_weights_giants_are_last_four():
    # package cache, makepkg, and the TWO mkarchiso passes (one per POSSIBLE ISO variant)
    # -- the four heavy tail weights. The bar is sized for the max; one variant per run.
    assert compiler.STEP_WEIGHTS[-4:] == [250, 120, 270, 270]


def test_cache_complete_false_when_index_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("FORCE_ONLINE", "0")
    monkeypatch.setattr(compiler.paths, "LOCALREPO_INDEX", tmp_path / "nope.db")
    assert compiler.cache_is_complete() is False


def test_cache_complete_force_online_overrides(monkeypatch):
    monkeypatch.setenv("FORCE_ONLINE", "1")
    # Even with everything present, FORCE_ONLINE=1 forces a re-fetch.
    assert compiler.cache_is_complete() is False


def test_cache_complete_true_when_all_present(monkeypatch, tmp_path):
    monkeypatch.setenv("FORCE_ONLINE", "0")
    repo = tmp_path / "repo"
    sync = tmp_path / "db" / "sync"
    repo.mkdir(parents=True)
    sync.mkdir(parents=True)
    idx = repo / "pacstrap-azzio-repo.db"
    idx.write_text("")
    (repo / "somepkg-1.0-1-x86_64.pkg.tar.zst").write_text("")
    # OUR OWN built packages must be present too, else the cache is not complete
    # (they are compiled by the makepkg stage, not downloaded). file_manager is now one of
    # them (built from the vendored source with the symlink-resolve change).
    (repo / "calamares-3.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "librewolf-1.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "file_manager-1-1-x86_64.pkg.tar.zst").write_text("")
    # The own packages count as present only if built from the CURRENT recipe, so a
    # genuinely complete cache also carries their matching recipe fingerprints.
    _use_fingerprint_dir(monkeypatch, repo, stamp_current=True)
    (sync / "core.db").write_text("")
    # Every DOWNLOADED manifest package must also have a file in the repo (the
    # coverage clause). Point the manifest at a tiny file whose one entry is
    # present, so completeness turns only on the structural + own-package markers.
    manifest = tmp_path / "packages.x86_64"
    manifest.write_text("# header\nsomepkg\n")

    monkeypatch.setattr(compiler.paths, "LOCALREPO_INDEX", idx)
    monkeypatch.setattr(compiler.paths, "PKG_REPO", repo)
    monkeypatch.setattr(compiler.paths, "PKG_SYNC_DB", sync)
    monkeypatch.setattr(compiler.paths, "PACKAGES_FILE", manifest)
    monkeypatch.setattr(compiler.downloader.paths, "PACKAGES_FILE", manifest)
    assert compiler.cache_is_complete() is True


def test_cache_complete_false_when_own_recipe_changed(monkeypatch, tmp_path):
    # The networkq regression, at the cache-first predicate: structure, synced DB, and
    # ALL own-package FILES are present, but calamares was built from an OLDER recipe
    # (its fingerprint sidecar no longer matches -- e.g. the networkq patch was added
    # since). This MUST read as incomplete so the run goes ONLINE and rebuilds
    # calamares from the current recipe, instead of shipping the stale binary whose
    # settings.conf lists `networkq` but whose modules dir has no such module.
    monkeypatch.setenv("FORCE_ONLINE", "0")
    repo = tmp_path / "repo"
    sync = tmp_path / "db" / "sync"
    repo.mkdir(parents=True)
    sync.mkdir(parents=True)
    idx = repo / "pacstrap-azzio-repo.db"
    idx.write_text("")
    (repo / "somepkg-1.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "calamares-3.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "librewolf-1.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "file_manager-1-1-x86_64.pkg.tar.zst").write_text("")
    # Stamp CURRENT fingerprints, then corrupt calamares' to simulate its recipe change.
    _use_fingerprint_dir(monkeypatch, repo, stamp_current=True)
    makepkg._write_recipe_fingerprint(repo, "calamares", "old_recipe_before_networkq")
    (sync / "core.db").write_text("")
    manifest = tmp_path / "packages.x86_64"
    manifest.write_text("# header\nsomepkg\n")

    monkeypatch.setattr(compiler.paths, "LOCALREPO_INDEX", idx)
    monkeypatch.setattr(compiler.paths, "PKG_REPO", repo)
    monkeypatch.setattr(compiler.paths, "PKG_SYNC_DB", sync)
    monkeypatch.setattr(compiler.paths, "PACKAGES_FILE", manifest)
    monkeypatch.setattr(compiler.downloader.paths, "PACKAGES_FILE", manifest)
    assert compiler.cache_is_complete() is False


def test_cache_complete_false_when_own_fingerprint_absent(monkeypatch, tmp_path):
    # A cache warmed by an OLDER Azzio (before fingerprints existed): own-package
    # files present but no sidecar at all. "Can't prove it's current" -> incomplete ->
    # rebuild once (after which the sidecar exists and offline reruns are fast again).
    monkeypatch.setenv("FORCE_ONLINE", "0")
    repo = tmp_path / "repo"
    sync = tmp_path / "db" / "sync"
    repo.mkdir(parents=True)
    sync.mkdir(parents=True)
    idx = repo / "pacstrap-azzio-repo.db"
    idx.write_text("")
    (repo / "somepkg-1.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "calamares-3.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "librewolf-1.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "file_manager-1-1-x86_64.pkg.tar.zst").write_text("")
    # Point the fingerprint dir at the (sidecar-free) repo -> no stamps -> stale.
    _use_fingerprint_dir(monkeypatch, repo, stamp_current=False)
    (sync / "core.db").write_text("")
    manifest = tmp_path / "packages.x86_64"
    manifest.write_text("# header\nsomepkg\n")

    monkeypatch.setattr(compiler.paths, "LOCALREPO_INDEX", idx)
    monkeypatch.setattr(compiler.paths, "PKG_REPO", repo)
    monkeypatch.setattr(compiler.paths, "PKG_SYNC_DB", sync)
    monkeypatch.setattr(compiler.paths, "PACKAGES_FILE", manifest)
    monkeypatch.setattr(compiler.downloader.paths, "PACKAGES_FILE", manifest)
    assert compiler.cache_is_complete() is False


def test_cache_complete_false_when_manifest_package_missing(monkeypatch, tmp_path):
    # The 'target not found' guard: structure + synced DB + BOTH own packages are
    # present, but a package named in packages.x86_64 was never downloaded into the
    # offline repo (exactly what happens right after a new package is added to the
    # manifest). This MUST read as incomplete so the build goes ONLINE and fetches
    # the missing package -- otherwise the offline pacstrap aborts with
    # 'error: target not found: <pkg>'. Regression test for the xorg-xset failure.
    monkeypatch.setenv("FORCE_ONLINE", "0")
    repo = tmp_path / "repo"
    sync = tmp_path / "db" / "sync"
    repo.mkdir(parents=True)
    sync.mkdir(parents=True)
    idx = repo / "pacstrap-azzio-repo.db"
    idx.write_text("")
    (repo / "somepkg-1.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "calamares-3.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "librewolf-1.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "file_manager-1-1-x86_64.pkg.tar.zst").write_text("")
    # Everything UPSTREAM of the manifest clause must pass so this test isolates it:
    # current own-package fingerprints present, so the run reaches the coverage check.
    _use_fingerprint_dir(monkeypatch, repo, stamp_current=True)
    (sync / "core.db").write_text("")
    # Manifest names a second package that has NO file in the repo.
    manifest = tmp_path / "packages.x86_64"
    manifest.write_text("# header\nsomepkg\nxorg-xset\n")

    monkeypatch.setattr(compiler.paths, "LOCALREPO_INDEX", idx)
    monkeypatch.setattr(compiler.paths, "PKG_REPO", repo)
    monkeypatch.setattr(compiler.paths, "PKG_SYNC_DB", sync)
    monkeypatch.setattr(compiler.paths, "PACKAGES_FILE", manifest)
    monkeypatch.setattr(compiler.downloader.paths, "PACKAGES_FILE", manifest)
    assert compiler.cache_is_complete() is False


def test_cache_complete_ignores_own_packages_absent_from_repo_files(monkeypatch, tmp_path):
    # The coverage clause must EXCLUDE our own built packages the same way the
    # downloader excludes them from `pacman -Sw`: calamares/librewolf live on no
    # mirror, so their manifest entries must not be counted as "missing downloads"
    # (their presence is already enforced by the own-packages clause via the file
    # they DO get after makepkg). Here calamares is in the manifest AND present as a
    # file, librewolf is in the manifest AND present -- and a plain Arch pkg covers.
    monkeypatch.setenv("FORCE_ONLINE", "0")
    repo = tmp_path / "repo"
    sync = tmp_path / "db" / "sync"
    repo.mkdir(parents=True)
    sync.mkdir(parents=True)
    idx = repo / "pacstrap-azzio-repo.db"
    idx.write_text("")
    (repo / "somepkg-1.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "calamares-3.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "librewolf-1.0-1-x86_64.pkg.tar.zst").write_text("")
    # file_manager is an own-built package too (rebuilt from source): in the manifest AND present.
    (repo / "file_manager-1-1-x86_64.pkg.tar.zst").write_text("")
    # Up-to-date cache -> own packages carry their current recipe fingerprints.
    _use_fingerprint_dir(monkeypatch, repo, stamp_current=True)
    (sync / "core.db").write_text("")
    manifest = tmp_path / "packages.x86_64"
    manifest.write_text("# header\nsomepkg\ncalamares\nlibrewolf\nfile_manager\n")

    monkeypatch.setattr(compiler.paths, "LOCALREPO_INDEX", idx)
    monkeypatch.setattr(compiler.paths, "PKG_REPO", repo)
    monkeypatch.setattr(compiler.paths, "PKG_SYNC_DB", sync)
    monkeypatch.setattr(compiler.paths, "PACKAGES_FILE", manifest)
    monkeypatch.setattr(compiler.downloader.paths, "PACKAGES_FILE", manifest)
    assert compiler.cache_is_complete() is True


def test_cache_complete_false_when_own_packages_absent(monkeypatch, tmp_path):
    # The deadlock guard: 800+ Arch packages, a valid index, and synced DBs are all
    # present, but calamares/librewolf (compiled, never downloaded) are NOT. This
    # MUST read as an incomplete cache so the build goes ONLINE and compiles them --
    # otherwise the offline path is chosen and makepkg refuses offline, hanging the
    # build forever with nothing to downgrade it to online.
    monkeypatch.setenv("FORCE_ONLINE", "0")
    repo = tmp_path / "repo"
    sync = tmp_path / "db" / "sync"
    repo.mkdir(parents=True)
    sync.mkdir(parents=True)
    idx = repo / "pacstrap-azzio-repo.db"
    idx.write_text("")
    (repo / "somepkg-1.0-1-x86_64.pkg.tar.zst").write_text("")
    (sync / "core.db").write_text("")
    # calamares/librewolf deliberately absent.

    monkeypatch.setattr(compiler.paths, "LOCALREPO_INDEX", idx)
    monkeypatch.setattr(compiler.paths, "PKG_REPO", repo)
    monkeypatch.setattr(compiler.paths, "PKG_SYNC_DB", sync)
    assert compiler.cache_is_complete() is False


def test_cache_complete_false_when_only_one_own_package_present(monkeypatch, tmp_path):
    # Half-built (calamares present, librewolf missing) is still incomplete: both
    # own packages are required, so a run that died mid-step-14 re-triggers online.
    monkeypatch.setenv("FORCE_ONLINE", "0")
    repo = tmp_path / "repo"
    sync = tmp_path / "db" / "sync"
    repo.mkdir(parents=True)
    sync.mkdir(parents=True)
    idx = repo / "pacstrap-azzio-repo.db"
    idx.write_text("")
    (repo / "somepkg-1.0-1-x86_64.pkg.tar.zst").write_text("")
    (repo / "calamares-3.0-1-x86_64.pkg.tar.zst").write_text("")
    (sync / "core.db").write_text("")
    # librewolf missing.

    monkeypatch.setattr(compiler.paths, "LOCALREPO_INDEX", idx)
    monkeypatch.setattr(compiler.paths, "PKG_REPO", repo)
    monkeypatch.setattr(compiler.paths, "PKG_SYNC_DB", sync)
    assert compiler.cache_is_complete() is False


def test_cache_complete_false_when_no_synced_db(monkeypatch, tmp_path):
    monkeypatch.setenv("FORCE_ONLINE", "0")
    repo = tmp_path / "repo"
    sync = tmp_path / "db" / "sync"
    repo.mkdir(parents=True)
    sync.mkdir(parents=True)
    idx = repo / "pacstrap-azzio-repo.db"
    idx.write_text("")
    (repo / "somepkg-1.0-1-x86_64.pkg.tar.zst").write_text("")
    # sync dir exists but has NO .db file.

    monkeypatch.setattr(compiler.paths, "LOCALREPO_INDEX", idx)
    monkeypatch.setattr(compiler.paths, "PKG_REPO", repo)
    monkeypatch.setattr(compiler.paths, "PKG_SYNC_DB", sync)
    assert compiler.cache_is_complete() is False


# --- _probe_and_maybe_switch: the mkarchiso pacstrap repo resolution ---------
#
# The build profile's network [core]/[extra] carry SigLevel = Required, but the
# package-cache download step (downloader.py) runs SigLevel=Never and so caches
# package BODIES with NO detached .sig files. If the mkarchiso pacstrap keeps the
# network repos as a package source, pacman must fetch each .pkg.tar.zst.sig from
# the pinned archive host (archive.archlinux.org) to satisfy SigLevel=Required --
# and that single throttled host stalls ("Operation too slow. Less than 1 bytes/
# sec"), aborting the whole transaction. That is exactly the observed compile
# failure (logs/: linux-firmware-marvell-...pkg.tar.zst.sig).
#
# The invariant these tests pin: once the local file:// repo index exists, EVERY
# pinned package is already on disk, so the pacstrap conf must install purely from
# the local repo (SigLevel=Never, no network Include) -- never re-reaching the
# archive host for signatures. Mirror reachability is irrelevant when the cache
# can already serve the build.


def _stub_probe(monkeypatch, *, reachable: bool):
    """Make _probe_and_maybe_switch's helpers side-effect-free for a unit test:
    _sudo() returns [] (no privilege wrapper) and every subprocess.run (the rm -rf
    scratch calls and the mirror -Sy probe) is faked. The probe's returncode is
    driven by ``reachable`` so we exercise both the reachable and unreachable arms
    without touching the network."""
    monkeypatch.setattr(compiler, "_sudo", lambda: [])

    class _Result:
        def __init__(self, rc):
            self.returncode = rc

    def fake_run(argv, **kwargs):
        # The mirror probe is the only `pacman -Sy ...` invocation here.
        if "pacman" in argv and "-Sy" in argv:
            return _Result(0 if reachable else 1)
        return _Result(0)

    monkeypatch.setattr(compiler.subprocess, "run", fake_run)


def _active(conf: str, line: str) -> list[str]:
    """Lines equal to ``line`` once stripped -- i.e. UNCOMMENTED, active directives.
    The profile header/tail carry commented `#Include`/`#[core-testing]` examples, so
    a plain substring test would false-match; an exact stripped-equality is what tells
    an active network repo from a commented example."""
    return [ln for ln in conf.splitlines() if ln.strip() == line]


def _build_profile_conf_with_localrepo(monkeypatch, tmp_path, *, reachable: bool):
    """Drive _probe_and_maybe_switch with a PRESENT local repo index and return the
    pacman.conf it wrote to W."""
    W = tmp_path / "profile"
    W.mkdir()
    localrepo = tmp_path / "repo"
    localrepo.mkdir()
    idx = localrepo / "pacstrap-azzio-repo.db"
    idx.write_text("")
    monkeypatch.setattr(compiler.paths, "LOCALREPO_INDEX", idx)
    _stub_probe(monkeypatch, reachable=reachable)
    conf = compiler.pacman.build_profile_conf(cachedir=str(tmp_path / "pacman-pkg") + "/")
    compiler._probe_and_maybe_switch(W, conf, localrepo, bar=None)
    return (W / "pacman.conf").read_text()


def test_probe_builds_offline_from_local_repo_when_cache_present(monkeypatch, tmp_path):
    # Mirrors reachable, but the local repo index EXISTS: the written conf must be
    # the offline (file://-only) form -- no ACTIVE network Include forcing a .sig fetch.
    written = _build_profile_conf_with_localrepo(monkeypatch, tmp_path, reachable=True)
    assert "[pacstrap-azzio-repo]" in written
    assert _active(written, "Include = /etc/pacman.d/mirrorlist") == []
    assert _active(written, "[core]") == [] and _active(written, "[extra]") == []


def test_probe_offline_when_cache_present_even_if_mirrors_unreachable(monkeypatch, tmp_path):
    # Same offline outcome when mirrors are down -- the cache is authoritative.
    written = _build_profile_conf_with_localrepo(monkeypatch, tmp_path, reachable=False)
    assert "[pacstrap-azzio-repo]" in written
    assert _active(written, "Include = /etc/pacman.d/mirrorlist") == []


def test_probe_goes_online_only_when_no_local_repo(monkeypatch, tmp_path):
    # No local repo cached AND mirrors reachable: the only case that legitimately
    # needs the network -- keep the network repos so pacstrap can fetch from them.
    W = tmp_path / "profile"
    W.mkdir()
    localrepo = tmp_path / "repo"  # deliberately NOT created -> index absent
    monkeypatch.setattr(compiler.paths, "LOCALREPO_INDEX", localrepo / "pacstrap-azzio-repo.db")
    _stub_probe(monkeypatch, reachable=True)
    conf = compiler.pacman.build_profile_conf(cachedir=str(tmp_path / "pacman-pkg") + "/")
    compiler._probe_and_maybe_switch(W, conf, localrepo, bar=None)
    written = (W / "pacman.conf").read_text()
    assert _active(written, "Include = /etc/pacman.d/mirrorlist") != []
    # The local file:// repo MUST still be appended: our own packages (calamares/
    # librewolf/file_manager) live on no mirror and are built into it at step 14, so a cold
    # build would fail to resolve them without this. Guards against a future edit that
    # drops append_local_repo from the online branch (the one thing the network-repo
    # assertion above would not catch).
    assert "[pacstrap-azzio-repo]" in written


# --- build conf is RE-resolved after the local repo is populated ------------
#
# run() resolves the mkarchiso build pacman.conf at step 11 (bar.step "Resolve
# build pacman.conf and mirrors") -- BEFORE step 12 warms the package cache and
# step 13 folds our OWN packages (calamares/librewolf) into cache/pkgs/repo/. On a
# COLD build the local repo index does not exist yet at step 11, so
# _probe_and_maybe_switch cannot take its offline (file://-only) arm; when the host
# is a foreign distro (Manjaro) whose mirrorlist the probe Includes, the probe also
# fails, so the conf is left with NO local repo and multilib commented. mkarchiso's
# pacstrap then reads that stale conf and aborts with "target not found: calamares"
# (local-repo-only) and the four lib32-* multilib targets (logs/compile-full.log).
#
# The fix: run() must RE-resolve the build conf AFTER _refold_own_packages_into_repo
# (the index now exists on disk) and BEFORE the mkarchiso pass, so the tested
# offline-from-local-repo arm actually runs on a cold build. These pin that.


def test_finalize_build_pacman_conf_switches_to_local_repo(monkeypatch, tmp_path):
    # With the local repo index present, the finalize helper must rewrite W/pacman.conf
    # to install purely from the file:// local repo (which by step 13 holds calamares
    # AND every lib32-* package) -- no active network Include left to fail on.
    W = tmp_path / "profile"
    W.mkdir()
    localrepo = tmp_path / "repo"
    localrepo.mkdir()
    idx = localrepo / "pacstrap-azzio-repo.db"
    idx.write_text("")
    monkeypatch.setattr(compiler.paths, "LOCALREPO_INDEX", idx)
    monkeypatch.setattr(compiler.paths, "PKG_REPO", localrepo)
    # Seed a stale step-11 conf that still names the network repos (the else/online arm).
    stale = compiler.pacman.build_profile_conf(cachedir=str(tmp_path / "pacman-pkg") + "/")
    (W / "pacman.conf").write_text(stale)

    compiler._finalize_build_pacman_conf(W)

    written = (W / "pacman.conf").read_text()
    assert "[pacstrap-azzio-repo]" in written
    assert _active(written, "Include = /etc/pacman.d/mirrorlist") == []
    assert _active(written, "[core]") == [] and _active(written, "[extra]") == []


def test_run_reresolves_build_conf_after_repo_is_populated():
    # Source-order invariant: the build pacman.conf must be RE-resolved to the local
    # repo AFTER the own packages are folded in and BEFORE the mkarchiso pass. Guards
    # against reverting to "resolve once at step 11" (the observed compile failure).
    src = inspect.getsource(compiler.run)
    refold = src.index("_refold_own_packages_into_repo(")
    finalize = src.index("_finalize_build_pacman_conf(")
    mkarchiso = src.index("_run_mkarchiso(")
    assert refold < finalize < mkarchiso, (
        "run() must re-resolve the build pacman.conf (_finalize_build_pacman_conf) "
        "after _refold_own_packages_into_repo and before _run_mkarchiso, so mkarchiso "
        "pacstraps against the now-complete local repo (calamares + lib32-*)."
    )


def test_main_wires_use_each_cpu_flag_into_makepkg():
    # PROMPT: --use-each-cpu lifts the hardcoded 75% compile cap to every core. main()
    # must (a) detect the flag from argv and (b) record it via makepkg.set_use_each_cpu
    # BEFORE any build work, so build_jobs() -- read by the compilers AND mkarchiso's
    # thread caps -- sees the decision. Source-level guard (main() itself is IO-heavy).
    src = inspect.getsource(compiler.main)
    assert '"--use-each-cpu" in sys.argv[1:]' in src, (
        "main() must parse the --use-each-cpu flag from argv"
    )
    assert "makepkg.set_use_each_cpu(" in src, (
        "main() must record the --use-each-cpu choice via makepkg.set_use_each_cpu"
    )


def test_compile_sh_documents_use_each_cpu_flag():
    # The operator-facing entrypoint must document the flag in its ARGS header so it is
    # discoverable (it is passed straight through to the Python driver).
    compile_sh = (paths.REPODIR / "compile.sh").read_text()
    assert "--use-each-cpu" in compile_sh, "compile.sh must document --use-each-cpu"
