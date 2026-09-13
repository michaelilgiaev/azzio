"""The `sshd` build variant: the opt-in ISO named azzio-headed-ssh-<ver>-
x86_64.iso, identical to the base `azzio-headed` one but with ssh ENABLED (it
auto-runs `azzio --sshd-hypervisor` at boot) and `main` carrying the operator's
build-time --ssh password.

Each run builds exactly ONE ISO: the base/headed ISO by default, or the ssh ISO
INDIVIDUALLY (on its own, NOT alongside the base ISO) via `--ssh="<PASSWORD>"` (no
default password is ever shipped -- see data/PROMPT.md DECISION 2). A bare/blank `--ssh`
is a HARD ERROR, not a silent base-only build (see check_ssh_flag). The flow is
compile.sh -> compiler.py -> compiler.run(), which loops over the RUNTIME-selected
variant (compiler._variants_for: base without --ssh, sshd with a password -- one, never
both) applying that variant's tiny differences via compiler._apply_variant and running
its single mkarchiso pass.

The observable per-variant effects, checked here as pure data/emit (no mkarchiso):

  1. profiledef's iso_name flips azzio-headed -> azzio-headed-ssh, so mkarchiso
     writes the azzio-headed-ssh-*.iso filename.
  2. _apply_variant emits + enables sshd-hypervisor-setup.service (a systemd oneshot that
     runs `azzio --sshd-hypervisor`) ONLY for the sshd variant; the base ISO gets NEITHER
     the unit nor its enable link, so there it stays ssh-disabled.
  3. the sshd variant's /etc/shadow carries the operator's real hash for `main`; the base
     ISO ships LOCKED accounts (no password login).

A drift in any of these silently ships the wrong ISO name, an ssh ISO that does NOT
actually start sshd on boot, or a base ISO that unexpectedly does.
"""

from __future__ import annotations

import inspect
import os
import re
import sys

import pytest

import compiler
import profile
import system
from packages import openbox


# --- VARIANTS is the canonical MAX set; the sshd ISO is OPT-IN ----------------

def test_variants_include_base_and_sshd():
    # VARIANTS is the canonical MAXIMUM set a build can produce. Which single variant ACTUALLY
    # builds is decided at runtime by _variants_for(): base without --ssh, sshd (individually)
    # with it (below). The set also carries the instant / instant+sshd variants (see
    # test_instant_variant.py), but base and sshd must always be present.
    assert compiler.VARIANTS == ("base", "sshd", "instant", "instant+sshd")
    assert "base" in compiler.VARIANTS and "sshd" in compiler.VARIANTS


# --- Method A: the --ssh=<PASSWORD> build-time flag --------------------------

def test_parse_ssh_flag_absent_is_none():
    # No flag -> no sshd ISO. An empty/missing string "demands a string or it doesn't
    # work": the flag must be present AND non-empty to opt in.
    assert compiler.parse_ssh_flag([]) is None
    assert compiler.parse_ssh_flag(["--full-compile"]) is None


def test_parse_ssh_flag_empty_value_is_none():
    # `--ssh=` with a blank value yields None from the VALUE parser (there is no
    # password). Whether that is a hard error is decided in main() via ssh_flag_present:
    # a PRESENT-but-blank flag is an error, an ABSENT flag builds base-only. parse_ssh_flag
    # only reports the value; it never conflates the two.
    assert compiler.parse_ssh_flag(["--ssh="]) is None


# --- ssh_flag_present: three-state detection (absent vs present-but-blank) ----

def test_ssh_flag_present_detects_bare_and_valued_forms():
    # The flag is "present" whether written bare (`--ssh`), empty (`--ssh=`), quoted-empty
    # (`--ssh=""` arrives as `--ssh=`) or with a value (`--ssh=pw`). This is what lets
    # main() distinguish "operator asked for ssh but forgot the password" (hard stop) from
    # "operator never mentioned ssh" (base-only, fine).
    assert compiler.ssh_flag_present(["--ssh"]) is True
    assert compiler.ssh_flag_present(["--ssh="]) is True
    assert compiler.ssh_flag_present(["--ssh=pw"]) is True
    assert compiler.ssh_flag_present(["--full-compile", "--ssh"]) is True


def test_ssh_flag_absent_is_not_present():
    assert compiler.ssh_flag_present([]) is False
    assert compiler.ssh_flag_present(["--full-compile"]) is False
    # A DIFFERENT flag that merely starts with the letters must not match.
    assert compiler.ssh_flag_present(["--sshfoo"]) is False


def test_ssh_flag_present_bare_flag_has_no_value():
    # A bare `--ssh` (no '=') is "present" but carries no value -> main() must hard-stop.
    assert compiler.ssh_flag_present(["--ssh"]) is True
    assert compiler.parse_ssh_flag(["--ssh"]) is None


def test_parse_ssh_flag_returns_password():
    assert compiler.parse_ssh_flag(["--ssh=hunter2"]) == "hunter2"
    # Order-independent, and coexists with other flags.
    assert compiler.parse_ssh_flag(["--full-compile", "--ssh=s3cret"]) == "s3cret"


def test_parse_ssh_flag_preserves_equals_in_password():
    # split("=", 1): a password containing '=' must NOT be truncated (the CLI precedent
    # in command_line_interface.py uses the same rule).
    assert compiler.parse_ssh_flag(["--ssh=a=b=c"]) == "a=b=c"


def test_ssh_password_hash_produces_sha512_crypt():
    # The supplied password is stored as a proper sha-512 crypt hash ($6$...), never
    # plaintext, never blank. openssl passwd -6 emits $6$ for sha-512.
    import shutil
    import subprocess
    if not shutil.which("openssl"):
        pytest.skip("openssl not available on this host")
    password = "correct horse battery staple"
    h = compiler.ssh_password_hash(password)
    assert h.startswith("$6$")
    # A real crypt hash has three '$'-delimited parts: $6$salt$digest.
    assert h.count("$") >= 3
    # Round-trip: re-hashing the same password with the SAME salt reproduces the hash
    # (proves it verifies). Python's crypt module is gone as of 3.13, so verify via
    # openssl with the salt extracted from the hash.
    salt = h.split("$")[2]
    again = subprocess.run(
        ["openssl", "passwd", "-6", "-salt", salt, "-stdin"],
        input=password, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert again == h


def test_ssh_password_hash_rejects_blank():
    # An empty password can never be hashed into the image (belt-and-suspenders on top
    # of parse_ssh_flag returning None for "").
    with pytest.raises(ValueError):
        compiler.ssh_password_hash("")


# --- the --ssh hard stop: present-but-blank must ABORT, not silently base-only ---

def test_ssh_flag_error_none_when_absent():
    # No --ssh at all: base-only build, no error. check_ssh_flag returns None so main()
    # proceeds normally.
    assert compiler.check_ssh_flag([]) is None
    assert compiler.check_ssh_flag(["--full-compile"]) is None


def test_ssh_flag_error_none_when_valued():
    # --ssh=pw: a real password, no error (the ssh ISO will build).
    assert compiler.check_ssh_flag(["--ssh=hunter2"]) is None


def test_ssh_flag_error_message_when_present_but_blank():
    # The reported bug: `--ssh` (bare) and `--ssh=` built base-only with NO explanation.
    # Now they must yield an explanatory error string (which main() prints to stderr and
    # exits non-zero on) instead of silently proceeding.
    for argv in (["--ssh"], ["--ssh="], ["--full-compile", "--ssh"]):
        msg = compiler.check_ssh_flag(argv)
        assert msg, f"blank --ssh must produce an error message: {argv!r}"
        assert "--ssh" in msg
        # It must EXPLAIN that a password is required (the user's complaint was the lack
        # of an explanation), and tell them the correct form.
        assert "password" in msg.lower()
        assert '--ssh="' in msg  # points at the correct --ssh="<PASSWORD>" form


def test_main_exits_nonzero_on_blank_ssh(monkeypatch, capsys):
    # End-to-end: main() must ABORT (non-zero) before doing any build work when --ssh is
    # present but blank. We stub the heavy build entry points so a regression that lets it
    # fall through would try to build and fail differently -- but the guard should return
    # first. run() is stubbed to raise so we can PROVE it was never reached.
    monkeypatch.setattr(sys, "argv", ["compiler", "--ssh"])

    def _boom(*a, **k):
        raise AssertionError("run() must NOT be reached when --ssh is blank")

    monkeypatch.setattr(compiler, "run", _boom)
    rc = compiler.main()
    assert rc != 0
    err = capsys.readouterr().err
    assert "--ssh" in err and "password" in err.lower()


# --- run() selects variants at runtime from the ssh hash ---------------------

def test_variants_for_base_only_without_ssh():
    # No ssh hash -> ONLY the base ISO is built (the sshd ISO is opt-in).
    assert compiler._variants_for(None) == ("base",)


def test_variants_for_is_sshd_only_with_hash():
    # A real hash -> the ssh ISO ONLY (built INDIVIDUALLY). --ssh outputs the SSH-type
    # medium on its own; it does NOT also build the base ISO. The base ISO is what you
    # get WITHOUT --ssh, so an --ssh run and a plain run each produce exactly one ISO.
    assert compiler._variants_for("$6$salt$digest") == ("sshd",)


def test_variants_for_never_builds_base_alongside_ssh():
    # Pin the "individually" contract explicitly: when --ssh opts in, the base variant
    # must NOT be in the built set (that would be the old base+ssh pairing).
    assert "base" not in compiler._variants_for("$6$salt$digest")


def test_run_threads_ssh_hash_into_variant_selection():
    # run() must build the runtime-selected variants (not the static VARIANTS tuple)
    # and thread the ssh hash into the shadow it writes.
    src = inspect.getsource(compiler.run)
    assert "_variants_for(" in src, "run() must pick variants at runtime from the ssh hash"
    assert "shadow_for(" in src, "run() must write the variant's shadow via system.shadow_for"


def test_run_signature_takes_ssh_password_hash():
    # The build-time password (already hashed) is plumbed into run() as a keyword arg.
    params = inspect.signature(compiler.run).parameters
    assert "ssh_password_hash" in params


def test_apply_variant_sshd_requires_a_hash(tmp_path):
    # Emitting the sshd variant without a password hash is a programming error: the
    # sshd ISO must NEVER be built with the base (locked) shadow -- that would ship an
    # sshd nobody can log into, or worse, hide that the credential was dropped.
    W = tmp_path / "profile"
    airootfs = W / "airootfs"
    with pytest.raises(ValueError):
        compiler._apply_variant(W, airootfs, "sshd", ssh_password_hash=None)


def test_apply_variant_sshd_writes_hashed_shadow(tmp_path):
    # The sshd variant's airootfs /etc/shadow must carry the real hash for `main`
    # (never blank, never the base locked value).
    W = tmp_path / "profile"
    airootfs = W / "airootfs"
    fake_hash = "$6$abcdefghijklmnop$" + "x" * 86
    compiler._apply_variant(W, airootfs, "sshd", ssh_password_hash=fake_hash)
    shadow = (airootfs / "etc/shadow").read_text()
    main_line = next(l for l in shadow.splitlines() if l.startswith("main:"))
    assert main_line.split(":")[1] == fake_hash
    # root stays locked in the sshd variant too.
    root_line = next(l for l in shadow.splitlines() if l.startswith("root:"))
    assert root_line.split(":")[1] in ("!", "*")


def test_apply_variant_base_writes_locked_shadow(tmp_path):
    # The base variant must (re)write the LOCKED shadow -- so even if a prior sshd pass
    # left a hashed shadow in the shared airootfs, the base ISO ships locked accounts.
    W = tmp_path / "profile"
    airootfs = W / "airootfs"
    fake_hash = "$6$abcdefghijklmnop$" + "x" * 86
    compiler._apply_variant(W, airootfs, "sshd", ssh_password_hash=fake_hash)  # leaves hashed shadow
    compiler._apply_variant(W, airootfs, "base", ssh_password_hash=fake_hash)  # base pass must relock
    shadow = (airootfs / "etc/shadow").read_text()
    for line in shadow.splitlines():
        assert line.split(":")[1] in ("!", "*"), f"base shadow must be locked: {line}"


# --- profiledef iso_name per variant ----------------------------------------

def _iso_name(pd: str) -> str:
    m = re.search(r'iso_name="([^"]+)"', pd)
    assert m, "profiledef has no iso_name"
    return m.group(1)


def test_iso_name_for_maps_variants():
    # The base/default ISO is the "headed" product line; the ssh flavour is
    # "headed-ssh" (the "headless" line slots in as azzio-headless without touching
    # the base/sshd variant keys). See profile.ISO_NAMES.
    assert profile.iso_name_for("base") == "azzio-headed"
    assert profile.iso_name_for("sshd") == "azzio-headed-ssh"
    # An unknown variant must fall back to the base name, never crash the build.
    assert profile.iso_name_for("nonsense") == "azzio-headed"
    assert profile.iso_name_for() == "azzio-headed"


def test_profiledef_base_is_azzio_headed():
    assert _iso_name(profile.profiledef_sh("base")) == "azzio-headed"
    # Default (no arg) is the base ISO.
    assert _iso_name(profile.profiledef_sh()) == "azzio-headed"


def test_profiledef_sshd_is_azzio_headed_ssh():
    # This is what makes mkarchiso name the artifact azzio-headed-ssh-<ver>-x86_64.iso.
    assert _iso_name(profile.profiledef_sh("sshd")) == "azzio-headed-ssh"


def test_only_iso_name_differs_between_variants():
    # The variants must be otherwise byte-identical: same bootmodes, permissions,
    # squashfs options, everything. Normalizing the one iso_name line makes the rest
    # comparable, proving the variant changes ONLY the name (packages/behaviour
    # parity is what "basically like the normal one" requires).
    base = profile.profiledef_sh("base")
    sshd = profile.profiledef_sh("sshd")
    norm = lambda s: s.replace('iso_name="azzio-headed-ssh"', 'iso_name="azzio-headed"')
    assert norm(sshd) == base


# --- the auto-setup systemd unit --------------------------------------------

def test_sshd_service_runs_the_cli_subcommand():
    svc = system.SSHD_HYPERVISOR_SETUP_SERVICE
    # It must invoke exactly the documented subcommand -- this IS "on by default".
    assert "ExecStart=/usr/local/bin/azzio --sshd-hypervisor" in svc


def test_sshd_service_targets_main_via_sudo_user():
    # Run as root with SUDO_USER=main: the azzio command line interface keys off ${SUDO_USER:-...} and
    # refuses a bare-root target, so this is what makes the pubkey land in
    # /home/main/.ssh (the account sshd accepts) without needing a PAM session.
    svc = system.SSHD_HYPERVISOR_SETUP_SERVICE
    assert "Environment=SUDO_USER=main" in svc
    assert "Type=oneshot" in svc


def test_sshd_service_ordering_is_sane():
    svc = system.SSHD_HYPERVISOR_SETUP_SERVICE
    # After pkgs-setup (whose `ufw enable` default-denies incoming) so our
    # `ufw allow ssh` wins and :22 is reachable.
    assert "After=pkgs-setup.service" in svc
    assert "WantedBy=multi-user.target" in svc
    # MUST NOT order after the target that pulls it in (anti-pattern / cycle risk).
    assert "After=multi-user.target" not in svc


def test_sshd_service_guarded_on_cli_presence():
    # ConditionPathExists keeps it from failing loudly if the azzio command line interface is absent.
    assert "ConditionPathExists=/usr/local/bin/azzio" in system.SSHD_HYPERVISOR_SETUP_SERVICE


# --- base airootfs bakes in `PermitRootLogin no` (survives a Calamares install) --
# The reported bug: a Calamares-installed box was reachable as ROOT over ssh. The base
# ISO locks all shadow fields, but a Calamares install SETS a real root password
# (config_system.py), and sshd's default is `prohibit-password` -- so without an
# explicit policy root is reachable. The runtime `--sshd-hypervisor` bring-up writes the
# deny drop-in, but that NEVER runs on a base-ISO Calamares install. So the deny must be
# BAKED into the airootfs, where the offline unpackfs copies it to the installed target.

def test_base_airootfs_bakes_root_login_denied(tmp_path):
    airootfs = tmp_path / "airootfs"
    compiler._provision_sshd_hardening(airootfs)
    dropin = airootfs / system.SSHD_ROOT_LOGIN_DROPIN_PATH
    assert dropin.is_file(), "base airootfs must bake in the root-login drop-in"
    body = dropin.read_text()
    assert "PermitRootLogin no" in body
    assert "PermitRootLogin yes" not in body


def test_provision_sshd_hardening_uses_the_system_constant(tmp_path):
    # The baked file body must be exactly the system.py source of truth (no drift).
    airootfs = tmp_path / "airootfs"
    compiler._provision_sshd_hardening(airootfs)
    dropin = airootfs / system.SSHD_ROOT_LOGIN_DROPIN_PATH
    assert dropin.read_text() == system.SSHD_ROOT_LOGIN_OFF


def test_run_provisions_sshd_hardening_for_the_base_airootfs():
    # run() must actually CALL the hardening provisioner as part of building the airootfs,
    # or the baked drop-in would never make it onto the ISO.
    src = inspect.getsource(compiler.run)
    assert "_provision_sshd_hardening(" in src


# --- base headed ISO ships ssh DISABLED everywhere ------------------------------

def test_link_services_never_enables_stock_sshd():
    # The default headed ISO must ship with ssh OFF. _link_services enables the curated daemon
    # set (NetworkManager/CUPS/spice + the azzio oneshots) -- it must NEVER enable the
    # stock sshd.service or ssh.socket, or the base ISO would listen on :22 with a LOCKED
    # account (or, worse on an installed system, expose ssh unexpectedly).
    src = inspect.getsource(compiler._link_services)
    assert "sshd.service" not in src
    assert "ssh.socket" not in src
    assert "sshd.socket" not in src


def test_base_airootfs_enables_no_ssh_unit(tmp_path):
    # After the FULL base link + variant apply, the base airootfs must contain NO ssh
    # enable-link of any kind under multi-user.target.wants.
    root = tmp_path / "root"
    compiler._link_services(root)
    compiler._apply_variant(root.parent, root, "base", ssh_password_hash=None)
    wants = root / "etc/systemd/system/multi-user.target.wants"
    if wants.is_dir():
        names = [p.name for p in wants.iterdir()]
        assert not any("ssh" in n for n in names), f"base must enable no ssh unit: {names}"


# --- releng inherits an ENABLED sshd.service; _copy_releng MUST strip it ------
# The stock archiso `releng` profile ships
# airootfs/etc/systemd/system/multi-user.target.wants/sshd.service (upstream enables sshd on
# the official Arch ISO). _copy_releng copies releng wholesale, so WITHOUT an explicit strip
# that enable-link survives and the BASE headed ISO boots with sshd active on :22 -- exactly the
# reported bug (`systemctl status sshd` -> enabled; active). These tests pin the strip.

def _needs_releng():
    from pathlib import Path
    return pytest.mark.skipif(
        not Path("/usr/share/archiso/configs/releng").is_dir(),
        reason="archiso releng profile not installed on this host",
    )


@_needs_releng()
def test_copy_releng_strips_inherited_sshd_want(tmp_path):
    # After _copy_releng, the base profile must NOT carry the releng-inherited sshd.service
    # enable-link -- so the default headed ISO ships sshd DISABLED.
    W = tmp_path / "profile"
    compiler._copy_releng(W)
    want = W / "airootfs/etc/systemd/system/multi-user.target.wants/sshd.service"
    assert not want.is_symlink() and not want.exists(), (
        "releng's sshd.service want must be stripped so the base ISO ships sshd disabled"
    )


@_needs_releng()
def test_copy_releng_leaves_other_wants_intact(tmp_path):
    # The strip is surgical: it removes ONLY the sshd want, not the rest of the releng
    # multi-user.target.wants tree (the directory itself and unrelated links stay).
    W = tmp_path / "profile"
    compiler._copy_releng(W)
    wants = W / "airootfs/etc/systemd/system/multi-user.target.wants"
    assert wants.is_dir(), "the wants directory itself must survive the strip"
    names = {p.name for p in wants.iterdir()}
    # A representative non-ssh releng want is still present (releng ships pacman-init).
    assert "pacman-init.service" in names, f"unrelated wants must remain: {sorted(names)}"


# --- firewall parity: base = no ports; ssh = 22/tcp --------------------------

def test_base_firewall_opens_no_ports():
    # The base headed ISO's live firewall baseline (installer.setup_pkgs_sh): incoming DENY,
    # outgoing ALLOW, and NO service ports opened. It must not `ufw allow` anything but the
    # explicit off-box deny of the timedate port.
    import installer
    sh = installer.setup_pkgs_sh()
    assert "ufw default deny incoming" in sh
    assert "ufw default allow outgoing" in sh
    # No port is OPENED in the base baseline (the only allow-style verbs would be `allow`).
    assert "ufw allow" not in sh


def test_ssh_variant_opens_22_tcp_via_sshd_bringup():
    # The ssh headed ISO opens :22/tcp -- via the sshd bring-up (sshd.py), on top of the same
    # deny-incoming base. Assert the bring-up path opens 22/tcp (the user's "port 22 allow
    # tcp"). The bring-up lives in the guest CLI; check its shipped source.
    from packages import openbox as desktop
    src = desktop.azzio_command_line_interface()
    assert '"ufw", "allow", "22/tcp"' in src


# --- always-on links: identical for both variants ---------------------------

def _link_dest(airootfs):
    return (airootfs / "etc/systemd/system/multi-user.target.wants"
            / "sshd-hypervisor-setup.service")


def test_link_services_never_creates_the_sshd_link(tmp_path):
    # _link_services now only enables the variant-INDEPENDENT daemons; the sshd
    # enable-link is applied per-variant by _apply_variant, never here.
    root = tmp_path / "root"
    compiler._link_services(root)
    assert not _link_dest(root).is_symlink()
    # The three always-on daemon links ARE created (sanity that the helper ran).
    always = root / "etc/systemd/system/multi-user.target.wants/NetworkManager.service"
    assert always.is_symlink()


# --- compiler._apply_variant: emit + enable only for the sshd variant ----------

def _svc_dest(airootfs):
    return airootfs / "etc/systemd/system/sshd-hypervisor-setup.service"


def test_apply_variant_sshd_emits_and_enables_service(tmp_path):
    W = tmp_path / "profile"
    airootfs = W / "airootfs"
    compiler._apply_variant(W, airootfs, "sshd", ssh_password_hash="$6$s$" + "x" * 86)
    # The unit file is written...
    svc = _svc_dest(airootfs)
    assert svc.is_file()
    assert "azzio --sshd-hypervisor" in svc.read_text()
    # ...and enabled via a multi-user.target.wants symlink to it.
    link = _link_dest(airootfs)
    assert link.is_symlink()
    assert os.readlink(link) == "/etc/systemd/system/sshd-hypervisor-setup.service"
    # profiledef at the profile root carries the sshd iso_name.
    assert _iso_name((W / "profiledef.sh").read_text()) == "azzio-headed-ssh"


def test_apply_variant_base_has_no_sshd_service_or_link(tmp_path):
    W = tmp_path / "profile"
    airootfs = W / "airootfs"
    compiler._apply_variant(W, airootfs, "base", ssh_password_hash=None)
    assert not _svc_dest(airootfs).exists()
    assert not _link_dest(airootfs).is_symlink()
    assert _iso_name((W / "profiledef.sh").read_text()) == "azzio-headed"


def test_apply_variant_base_after_sshd_removes_the_leftover(tmp_path):
    # The finalize loop reuses ONE shared airootfs across passes. If sshd were built
    # before base, base's pass MUST strip the sshd unit + enable link the sshd pass
    # left behind -- otherwise the base ISO would silently auto-start sshd too. Assert
    # _apply_variant("base") affirmatively removes both even when they pre-exist.
    W = tmp_path / "profile"
    airootfs = W / "airootfs"
    hh = "$6$s$" + "x" * 86
    compiler._apply_variant(W, airootfs, "sshd", ssh_password_hash=hh)  # leave the sshd artifacts
    assert _svc_dest(airootfs).is_file()
    assert _link_dest(airootfs).is_symlink()
    compiler._apply_variant(W, airootfs, "base", ssh_password_hash=hh)  # base pass must clean them up
    assert not _svc_dest(airootfs).exists()
    assert not _link_dest(airootfs).is_symlink()


def test_run_signature_has_no_variant_param():
    # run() takes NO `variant` argument: the single variant it builds is chosen at
    # runtime from the --ssh hash (via _variants_for), not passed in by the caller. A
    # stray `variant` param would let a caller override that selection out of band.
    import inspect
    params = inspect.signature(compiler.run).parameters
    assert "variant" not in params


def test_run_calls_mkarchiso_once_per_variant():
    # run() must invoke _run_mkarchiso once per selected variant (one ISO per run) and
    # append each returned ISO. Assert the finalize loop iterates the runtime selection
    # and calls _run_mkarchiso inside it.
    src = inspect.getsource(compiler.run)
    # Iterates the RUNTIME-selected variant (base without --ssh; sshd individually with
    # it), not the static VARIANTS tuple.
    assert "for variant in " in src
    assert "_variants_for(" in src
    assert "_run_mkarchiso(" in src
    assert "_apply_variant(" in src


def test_mkarchiso_pass_resets_work_dir_before_running():
    # THE two-variant integration hazard: mkarchiso guards every build step with a
    # `_run_once` sentinel file under work/ (work/base.<fn>, work/iso.<fn>) and refuses
    # to delete a pre-existing work dir. If the second (sshd) pass reused the first
    # pass's work/, mkarchiso would skip airootfs/squashfs/ISO-write as "already done"
    # and NEVER write azzio-sshd-*.iso. So each pass MUST wipe work/ before invoking
    # mkarchiso. Assert the reset (rm -rf of the work dir) happens in _run_mkarchiso
    # BEFORE the mkarchiso subprocess is spawned.
    import inspect
    src = inspect.getsource(compiler._run_mkarchiso)
    # A work-dir wipe must be present...
    assert 'rm", "-rf"' in src and 'W / "work"' in src, \
        "_run_mkarchiso must rm -rf the work dir so each variant is a fresh mkarchiso pass"
    # ...and it must come BEFORE the mkarchiso invocation (else the sentinels from a
    # prior pass are still present when mkarchiso decides what to skip).
    reset_at = src.index('"work"')
    mkarchiso_at = src.index('"mkarchiso"')
    assert reset_at < mkarchiso_at, "work/ must be reset BEFORE mkarchiso runs"


def test_iso_selection_glob_distinguishes_base_from_sshd():
    # output/ can hold BOTH azzio-headed-*.iso and azzio-headed-ssh-*.iso. The base
    # pass must never pick up the ssh ISO. mkarchiso names artifacts <iso_name>-<YYYY.MM.DD>-
    # <arch>.iso, so anchoring the glob with a digit after "{iso_name}-" separates
    # them ("azzio-headed-2026..." matches base; "azzio-headed-ssh-..." does not,
    # since 's' is not a digit). Emulate the exact glob _run_mkarchiso uses.
    import fnmatch
    both = ["azzio-headed-2026.07.31-x86_64.iso",
            "azzio-headed-ssh-2026.07.31-x86_64.iso"]
    base_hits = [f for f in both if fnmatch.fnmatch(f, "azzio-headed-[0-9]*.iso")]
    sshd_hits = [f for f in both if fnmatch.fnmatch(f, "azzio-headed-ssh-[0-9]*.iso")]
    assert base_hits == ["azzio-headed-2026.07.31-x86_64.iso"]
    assert sshd_hits == ["azzio-headed-ssh-2026.07.31-x86_64.iso"]
    # And the source really uses the digit-anchored glob (not a bare "-*.iso").
    import inspect
    src = inspect.getsource(compiler._run_mkarchiso)
    assert '{iso_name}-[0-9]*.iso' in src


# =============================================================================
# The `instant` build variant: the opt-in AUTO-INSTALLING ISO (azzio-headed-instant),
# and its combination with --ssh (azzio-headed-instant-ssh -- "instant" BEFORE "ssh").
#
# These live here, beside the sshd-variant tests, because they exercise the SAME variant/
# flag machinery (VARIANTS, _variants_for, _apply_variant, profile.iso_name_for) one product
# tier over: `--instant` makes the built medium's LIVE SESSION auto-run `azzioinstall
# --instant <sub-flags>` at boot -- the fully-scripted installer, already implemented in
# packages/openbox -- instead of opening the Calamares GUI. Unlike --ssh, --instant carries
# NO value (its presence is the opt-in) and a bare `--instant` is VALID. It STACKS with --ssh,
# except the two password-setting sub-flags (--username-password / --root-password) are rejected
# then (the --ssh password already governs those). Checked as pure data/emit (no mkarchiso).
# =============================================================================


# --- VARIANTS carries the instant variants -----------------------------------

def test_variants_include_instant_and_combined():
    # The canonical MAX set gains the two instant variants alongside base/sshd. Only one is ever
    # selected per run (_variants_for), but all four size the progress bar's mkarchiso weights.
    assert compiler.VARIANTS == ("base", "sshd", "instant", "instant+sshd")


# --- --instant is a BOOLEAN opt-in (no value; presence == build instant ISO) --

def test_instant_flag_present_detects_forms():
    # Any `--instant` / `--instant=...` token counts (--instant carries no value, so even a
    # stray `--instant=x` still means "build the instant ISO").
    assert compiler.instant_flag_present(["--instant"]) is True
    assert compiler.instant_flag_present(["--instant=1"]) is True
    assert compiler.instant_flag_present(["--full-compile", "--instant"]) is True


def test_instant_flag_absent_is_not_present():
    assert compiler.instant_flag_present([]) is False
    assert compiler.instant_flag_present(["--full-compile"]) is False
    assert compiler.instant_flag_present(["--instantfoo"]) is False  # near-miss is NOT the flag


# --- sub-flag parsing (mirrors azzioinstall's own --instant options) ----------

def test_parse_instant_subflag_absent_is_none():
    assert compiler.parse_instant_subflag(["--instant"], "--username") is None
    assert compiler.parse_instant_subflag([], "--hostname") is None


def test_parse_instant_subflag_returns_value():
    argv = ["--instant", "--username=alice", "--hostname=box"]
    assert compiler.parse_instant_subflag(argv, "--username") == "alice"
    assert compiler.parse_instant_subflag(argv, "--hostname") == "box"


def test_parse_instant_subflag_empty_value_is_empty_string_not_none():
    # An EXPLICIT blank (`--username=`) must be distinguishable from "not given" so
    # check_instant_flag can reject it. Empty string, not None.
    assert compiler.parse_instant_subflag(["--instant", "--username="], "--username") == ""


def test_parse_instant_subflag_preserves_equals_in_value():
    # split on the FIRST '=' only -- a password may itself contain '='.
    assert compiler.parse_instant_subflag(
        ["--instant", "--username-password=a=b=c"], "--username-password") == "a=b=c"


def test_instant_subflags_present_lists_in_canonical_order():
    # Reported in INSTANT_SUBFLAGS order regardless of argv order.
    argv = ["--instant", "--final=idle", "--username=me", "--disk=sda"]
    assert compiler.instant_subflags_present(argv) == ["--disk", "--username", "--final"]


def test_all_azzioinstall_instant_subflags_are_mirrored():
    # Every --instant option azzioinstall accepts must be forwardable by the compiler, else an
    # operator could not reach it at build time. Pins the 1:1 mirror.
    assert set(compiler.INSTANT_SUBFLAGS) == {
        "--disk", "--hostname", "--username", "--username-password",
        "--share-username-root-password", "--root-password", "--timezone", "--final",
    }


# --- resolving the azzioinstall command line ---------------------------------

def test_instant_azzioinstall_args_bare():
    assert compiler.instant_azzioinstall_args(["--instant"]) == ["--instant"]


def test_instant_azzioinstall_args_forwards_present_subflags_in_order():
    argv = ["--instant", "--final=reboot", "--username=me", "--hostname=box"]
    assert compiler.instant_azzioinstall_args(argv) == [
        "--instant", "--hostname=box", "--username=me", "--final=reboot",
    ]


def test_instant_azzioinstall_args_omits_absent_subflags():
    args = compiler.instant_azzioinstall_args(["--instant", "--username=me"])
    assert args == ["--instant", "--username=me"]
    assert not any(a.startswith("--hostname") for a in args)


def test_instant_azzioinstall_args_drops_non_subflags():
    # --ssh / --full-compile are NOT azzioinstall sub-flags and must not leak into the command.
    args = compiler.instant_azzioinstall_args(
        ["--instant", "--ssh=pw", "--full-compile", "--hostname=box"])
    assert args == ["--instant", "--hostname=box"]


# --- check_instant_flag: validation matrix -----------------------------------

def test_check_instant_absent_is_none():
    assert compiler.check_instant_flag([]) is None
    assert compiler.check_instant_flag(["--full-compile"]) is None


def test_check_instant_bare_is_ok():
    # Unlike --ssh, a bare --instant is VALID (defaults apply). No error.
    assert compiler.check_instant_flag(["--instant"]) is None


def test_check_instant_valid_subflags_ok():
    assert compiler.check_instant_flag(
        ["--instant", "--username=me", "--hostname=box", "--final=reboot"]) is None


def test_check_instant_subflag_without_instant_is_error():
    # A sub-flag with no --instant would silently do nothing -> hard error naming the flag.
    msg = compiler.check_instant_flag(["--username=me"])
    assert msg
    assert "--instant" in msg
    assert "--username" in msg


def test_check_instant_blank_subflag_value_is_error():
    for name in ("--username", "--hostname", "--timezone", "--disk"):
        msg = compiler.check_instant_flag(["--instant", f"{name}="])
        assert msg, f"blank {name} must error"
        assert name in msg


@pytest.mark.parametrize("bad", ["restrat", "reboot-now", "", "off"])
def test_check_instant_bad_final_is_error(bad):
    msg = compiler.check_instant_flag(["--instant", f"--final={bad}"])
    assert msg, f"--final={bad!r} must error"
    assert "--final" in msg


@pytest.mark.parametrize("good", ["idle", "reboot", "restart", "shutdown"])
def test_check_instant_good_final_ok(good):
    assert compiler.check_instant_flag(["--instant", f"--final={good}"]) is None


# --- the ONE cross-flag restriction: --ssh forbids setting a password ---

@pytest.mark.parametrize("clashing", [
    "--username-password=secret",
    "--root-password=secret",
])
def test_check_instant_ssh_plus_identity_is_hard_error(clashing):
    # With --ssh, the medium's user / root credential come from the --ssh password, so re-setting
    # either password via --instant is contradictory -> hard error (the spec's rule).
    msg = compiler.check_instant_flag(["--instant", "--ssh=pw", clashing])
    assert msg, f"--ssh + {clashing} must be a hard error"
    assert "--ssh" in msg
    assert clashing.split("=")[0] in msg   # names the offending flag


@pytest.mark.parametrize("allowed", [
    "--username=alice",                     # picking WHICH user is allowed (sets no password)
    "--share-username-root-password=False",  # a policy toggle, sets no password itself
    "--hostname=box",
    "--timezone=Europe/London",
    "--disk=sda",
    "--final=reboot",
])
def test_check_instant_ssh_plus_nonidentity_is_ok(allowed):
    assert compiler.check_instant_flag(["--instant", "--ssh=pw", allowed]) is None


def test_check_instant_ssh_plus_bare_instant_ok():
    # --instant + --ssh with no password overrides is a valid combo (azzio-headed-instant-ssh).
    assert compiler.check_instant_flag(["--instant", "--ssh=pw"]) is None


def test_identity_conflict_set_is_exactly_the_two_password_flags():
    # Pin the spec: ONLY the two password-setting flags clash with --ssh. --username (which user)
    # and --share-username-root-password (a policy toggle) set no password and are NOT in the set.
    assert set(compiler.INSTANT_IDENTITY_SUBFLAGS_CONFLICTING_WITH_SSH) == {
        "--username-password", "--root-password",
    }
    for allowed in ("--username", "--share-username-root-password"):
        assert allowed not in compiler.INSTANT_IDENTITY_SUBFLAGS_CONFLICTING_WITH_SSH


# --- _variants_for: instant selects the right single variant ------------------

def test_variants_for_instant_only():
    assert compiler._variants_for(None, instant=True) == ("instant",)


def test_variants_for_instant_plus_ssh_is_combined():
    assert compiler._variants_for("$6$salt$digest", instant=True) == ("instant+sshd",)


def test_variants_for_ssh_only_unchanged_by_instant_kwarg():
    assert compiler._variants_for("$6$salt$digest", instant=False) == ("sshd",)


def test_variants_for_base_when_neither():
    assert compiler._variants_for(None, instant=False) == ("base",)
    assert compiler._variants_for(None) == ("base",)  # default instant kwarg is False


def test_variant_predicates():
    assert compiler._variant_is_instant("instant") is True
    assert compiler._variant_is_instant("instant+sshd") is True
    assert compiler._variant_is_instant("base") is False
    assert compiler._variant_is_instant("sshd") is False
    assert compiler._variant_is_sshd("sshd") is True
    assert compiler._variant_is_sshd("instant+sshd") is True
    assert compiler._variant_is_sshd("base") is False
    assert compiler._variant_is_sshd("instant") is False


# --- ISO NAMING: instant appears, and appears BEFORE ssh ----------------------

def test_iso_name_for_maps_instant_variants():
    assert profile.iso_name_for("instant") == "azzio-headed-instant"
    assert profile.iso_name_for("instant+sshd") == "azzio-headed-instant-ssh"


def test_instant_appears_before_ssh_in_combined_name():
    # The spec: "instant" must come BEFORE "ssh" -- azzio-headed-instant-ssh, NEVER
    # azzio-headed-ssh-instant.
    name = profile.iso_name_for("instant+sshd")
    assert name == "azzio-headed-instant-ssh"
    assert name.index("instant") < name.index("ssh")
    assert "ssh-instant" not in name


def test_profiledef_instant_iso_names():
    assert _iso_name(profile.profiledef_sh("instant")) == "azzio-headed-instant"
    assert _iso_name(profile.profiledef_sh("instant+sshd")) == "azzio-headed-instant-ssh"


def test_only_iso_name_differs_for_instant_variant():
    # The instant profiledef is byte-identical to base EXCEPT its iso_name -- the auto-install
    # difference lives in the airootfs hook (_apply_variant), not in profiledef.
    base = profile.profiledef_sh("base")
    inst = profile.profiledef_sh("instant")
    norm = inst.replace('iso_name="azzio-headed-instant"', 'iso_name="azzio-headed"')
    assert norm == base


def test_instant_variant_globs_are_disjoint():
    # The output-separation glob "{iso_name}-[0-9]*.iso" must keep the variants apart: the char
    # right after each shorter name is a LETTER (not a digit), so no glob matches a longer name.
    import fnmatch
    all_names = [
        "azzio-headed-2026.07.31-x86_64.iso",
        "azzio-headed-ssh-2026.07.31-x86_64.iso",
        "azzio-headed-instant-2026.07.31-x86_64.iso",
        "azzio-headed-instant-ssh-2026.07.31-x86_64.iso",
    ]
    def hits(iso_name):
        return [f for f in all_names if fnmatch.fnmatch(f, f"{iso_name}-[0-9]*.iso")]
    assert hits("azzio-headed") == ["azzio-headed-2026.07.31-x86_64.iso"]
    assert hits("azzio-headed-instant") == ["azzio-headed-instant-2026.07.31-x86_64.iso"]
    assert hits("azzio-headed-instant-ssh") == ["azzio-headed-instant-ssh-2026.07.31-x86_64.iso"]


# --- the auto-install HOOK builder (openbox) ----------------------------------

def test_hook_execs_azzioinstall_with_args():
    hook = openbox.instant_install_hook_sh(["--instant", "--username=me"])
    assert hook.startswith("#!/bin/sh")
    assert f"exec '{openbox.INSTALL_WRAPPER_PATH}'" in hook
    assert "--instant" in hook and "--username=me" in hook


def test_hook_shell_quotes_values_with_spaces():
    # A password with a space must reach azzioinstall as ONE argv word -- the builder shell-quotes
    # each arg (shlex.quote), so `correct horse` is single-quoted, not split.
    hook = openbox.instant_install_hook_sh(["--instant", "--username-password=correct horse"])
    assert "'--username-password=correct horse'" in hook
    exec_lines = [l for l in hook.splitlines() if l.startswith("exec ")]
    assert len(exec_lines) == 1   # single exec line, no newline injection


def test_hook_quotes_shell_metacharacters():
    hook = openbox.instant_install_hook_sh(["--instant", "--username-password=a;rm -rf /"])
    # shlex.quote wraps the whole a;rm... in single quotes -> inert.
    assert "'--username-password=a;rm -rf /'" in hook
    assert "; rm -rf /" not in hook.replace("'--username-password=a;rm -rf /'", "")


# --- the LIVE autostart wires the hook (and falls back to the GUI) ------------

def test_live_autostart_prefers_hook_then_gui():
    a = openbox.openbox_autostart()
    assert openbox.INSTANT_INSTALL_HOOK_PATH in a
    # The hook is tested in an `if` that gates the `elif ... --gui` fallback, so on an instant ISO
    # (hook present) the GUI never runs. Assert the CONTROL FLOW, not raw substring order (the
    # header comment mentions --gui too): the `if [ -x '<hook>' ]` guard must precede the
    # `elif [ -x '<wrapper>' ]` ... `--gui` runner line.
    if_line = next(l for l in a.splitlines()
                   if l.lstrip().startswith("if [ -x") and openbox.INSTANT_INSTALL_HOOK_PATH in l)
    gui_run_line = next(l for l in a.splitlines() if "--gui" in l and "sleep 2" in l)
    elif_line = next(l for l in a.splitlines() if l.lstrip().startswith("elif [ -x"))
    assert a.index(if_line) < a.index(elif_line) < a.index(gui_run_line)
    assert "sleep 2" in a   # both launches are still guarded/backgrounded


def test_installed_autostart_has_neither_hook_nor_installer():
    # The INSTALLED system must never auto-install or re-open the installer.
    inst = openbox.openbox_autostart_installed()
    assert openbox.INSTANT_INSTALL_HOOK_PATH not in inst
    assert "azzioinstall" not in inst
    assert "--gui" not in inst


# --- _apply_variant: the per-variant airootfs overlay (reuses _iso_name /
#     _svc_dest / _link_dest defined above for the sshd tests) ----------------

def _hook_dest(airootfs):
    return airootfs / openbox.INSTANT_INSTALL_HOOK_PATH.lstrip("/")


def _instant_tree(tmp_path):
    W = tmp_path / "profile"
    airootfs = W / "airootfs"
    airootfs.mkdir(parents=True)
    return W, airootfs


def _main_shadow_field(airootfs):
    shadow = (airootfs / "etc/shadow").read_text()
    return next(l.split(":")[1] for l in shadow.splitlines() if l.startswith("main:"))


def test_apply_variant_instant_plants_executable_hook(tmp_path):
    W, airootfs = _instant_tree(tmp_path)
    compiler._apply_variant(W, airootfs, "instant",
                            instant_azzioinstall_args=["--instant", "--username=me"])
    hook = _hook_dest(airootfs)
    assert hook.is_file()
    assert os.access(hook, os.X_OK), "the hook must be executable (the autostart runs it)"
    body = hook.read_text()
    assert "azzioinstall" in body and "--username=me" in body
    # instant-only ISO: NO sshd service/link, shadow stays LOCKED, name is azzio-headed-instant.
    assert not _svc_dest(airootfs).exists()
    assert not _link_dest(airootfs).is_symlink()
    assert _main_shadow_field(airootfs) in ("!", "*")
    assert _iso_name((W / "profiledef.sh").read_text()) == "azzio-headed-instant"


def test_apply_variant_instant_plus_sshd_has_both(tmp_path):
    W, airootfs = _instant_tree(tmp_path)
    fake_hash = "$6$salt$" + "x" * 80
    compiler._apply_variant(W, airootfs, "instant+sshd",
                            ssh_password_hash=fake_hash,
                            instant_azzioinstall_args=["--instant", "--hostname=box"])
    assert _hook_dest(airootfs).is_file()          # instant hook present...
    assert _svc_dest(airootfs).is_file()           # ...AND the sshd service + link + hashed shadow.
    assert _link_dest(airootfs).is_symlink()
    assert _main_shadow_field(airootfs) == fake_hash
    assert _iso_name((W / "profiledef.sh").read_text()) == "azzio-headed-instant-ssh"


def test_apply_variant_base_has_no_hook(tmp_path):
    W, airootfs = _instant_tree(tmp_path)
    compiler._apply_variant(W, airootfs, "base")
    assert not _hook_dest(airootfs).exists()


def test_apply_variant_sshd_has_no_hook(tmp_path):
    W, airootfs = _instant_tree(tmp_path)
    compiler._apply_variant(W, airootfs, "sshd", ssh_password_hash="$6$s$" + "y" * 80)
    assert not _hook_dest(airootfs).exists()
    assert _svc_dest(airootfs).is_file()   # sshd still gets its service


def test_apply_variant_removes_stale_hook_from_prior_instant_pass(tmp_path):
    # The airootfs is shared across passes; a base/sshd pass after an instant pass MUST strip the
    # hook, else the base/sshd ISO would silently auto-install.
    W, airootfs = _instant_tree(tmp_path)
    compiler._apply_variant(W, airootfs, "instant", instant_azzioinstall_args=["--instant"])
    assert _hook_dest(airootfs).is_file()
    compiler._apply_variant(W, airootfs, "base")
    assert not _hook_dest(airootfs).exists()


def test_apply_variant_instant_without_args_raises(tmp_path):
    # Refuse to build an instant ISO with no hook -- it would boot to the GUI, silently ignoring
    # --instant.
    W, airootfs = _instant_tree(tmp_path)
    with pytest.raises(ValueError):
        compiler._apply_variant(W, airootfs, "instant", instant_azzioinstall_args=None)


def test_apply_variant_instant_plus_sshd_without_hash_raises(tmp_path):
    W, airootfs = _instant_tree(tmp_path)
    with pytest.raises(ValueError):
        compiler._apply_variant(W, airootfs, "instant+sshd",
                                instant_azzioinstall_args=["--instant"])


# --- main(): end-to-end guards + wiring --------------------------------------

def test_main_exits_nonzero_on_subflag_without_instant(monkeypatch, capsys):
    # An --instant sub-flag without --instant aborts BEFORE any build work.
    monkeypatch.setattr(sys, "argv", ["compiler", "--username=me"])

    def _boom(*a, **k):
        raise AssertionError("run() must NOT be reached on an invalid --instant combination")

    monkeypatch.setattr(compiler, "run", _boom)
    rc = compiler.main()
    assert rc != 0
    err = capsys.readouterr().err
    assert "--instant" in err and "--username" in err


def test_main_exits_nonzero_on_ssh_plus_password(monkeypatch, capsys):
    # --ssh clashes with a PASSWORD-setting sub-flag (--username-password / --root-password).
    monkeypatch.setattr(
        sys, "argv", ["compiler", "--instant", "--ssh=pw", "--username-password=secret"])

    def _boom(*a, **k):
        raise AssertionError("run() must NOT be reached when --ssh clashes with a password flag")

    monkeypatch.setattr(compiler, "run", _boom)
    rc = compiler.main()
    assert rc != 0
    err = capsys.readouterr().err
    assert "--ssh" in err and "--username-password" in err


def test_main_exits_nonzero_on_bad_final(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["compiler", "--instant", "--final=nope"])
    monkeypatch.setattr(compiler, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unreached")))
    rc = compiler.main()
    assert rc != 0
    assert "--final" in capsys.readouterr().err


def test_main_threads_instant_args_into_run(monkeypatch):
    # A VALID --instant run must reach run() with instant_azzioinstall_args resolved from the
    # sub-flags. Stub everything heavy and capture the kwargs run() receives.
    monkeypatch.setattr(sys, "argv",
                        ["compiler", "--instant", "--username=me", "--final=reboot"])
    captured = {}

    def _fake_run(*a, **k):
        captured.update(k)
        return []   # no ISOs; main() reports "0 ISOs" and returns 0

    monkeypatch.setattr(compiler, "run", _fake_run)
    monkeypatch.setattr(compiler.logstream, "install", lambda *a, **k: None)
    monkeypatch.setattr(compiler, "cache_is_complete", lambda: True)
    monkeypatch.setattr(compiler, "_stale_cache_notice", lambda *a, **k: None)
    monkeypatch.setattr(compiler.makepkg, "set_use_each_cpu", lambda *a, **k: None)
    monkeypatch.setattr(compiler.makepkg, "build_jobs", lambda: 4)
    monkeypatch.setattr(compiler.makepkg, "_cpu_count", lambda: 8)

    class _Bar:
        subfrac = 0
        total_steps = 18
        def __init__(self, *a, **k): pass
        def init(self): pass
        def finalize(self): pass
        def cleanup(self): pass
    monkeypatch.setattr(compiler, "ProgressBar", _Bar)

    class _Own:
        def __init__(self, *a, **k): pass
        def reclaim_full(self): pass
        def start_continuous(self): pass
        def stop_continuous(self): pass
    monkeypatch.setattr(compiler, "Ownership", _Own)

    class _Keep:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def stop(self): pass
    monkeypatch.setattr(compiler, "SudoKeepalive", _Keep)
    monkeypatch.setattr(compiler, "_sudo", lambda: [])
    monkeypatch.setattr(compiler.signal, "signal", lambda *a, **k: None)

    rc = compiler.main()
    assert rc == 0
    assert captured.get("instant_azzioinstall_args") == [
        "--instant", "--username=me", "--final=reboot"]
    assert captured.get("ssh_password_hash") is None   # ssh not supplied -> no hash threaded
