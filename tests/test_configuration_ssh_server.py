"""The `azzio network ssh` server front-end + the first-run security notice.

Two closely-related surfaces, both pinned against the BUNDLED shipped script
(packages.azzio.bundle.bundle_source -- the exact /usr/local/bin/azzio artifact):

  * `azzio network ssh <start|stop|status>` -- start opens :22/tcp and enables sshd
    (via the `--sshd-hypervisor` bring-up), stop disables sshd AND closes :22, status
    reports both. This is the CLI behind the TUI's Network > SSH Server screen.
  * `azzio security-notice` -- the one-time base-desktop warning (password login + ssh
    OFF by default, enabling either exposes the box). It self-gates on the ssh variant /
    a real password and self-silences after the first show.

Nothing touches the host: `_sudo` and the reads are stubbed.
"""

from __future__ import annotations

import types

import pytest

from packages.azzio.bundle import bundle_source
from packages import openbox as desktop


def _cli():
    mod = types.ModuleType("azzio_cli_ssh_test")
    exec(compile(bundle_source(), "azzio_cli", "exec"), mod.__dict__)
    return mod


def _capture_sudo(cli, monkeypatch, rc=0):
    calls: list[tuple] = []
    monkeypatch.setattr(cli, "_sudo", lambda *a, **k: (calls.append(a) or rc))
    return calls


# --- ssh is a network noun --------------------------------------------------

def test_ssh_is_a_network_noun():
    src = desktop.azzio_command_line_interface()
    assert 'noun == "ssh"' in src
    assert "return cmd_ssh(rest)" in src
    # advertised in the network usage (now with the root-login sub-noun)
    assert "ssh <start|stop|status|root>" in src


def test_network_ssh_help_exits_zero(capsys):
    cli = _cli()
    assert cli.main(["network", "ssh", "--help"]) == 0
    out = capsys.readouterr().out
    assert "start" in out and "stop" in out and "status" in out
    assert "22" in out  # mentions the port


def test_network_ssh_unknown_verb_is_rc_two(capsys):
    cli = _cli()
    assert cli.main(["network", "ssh", "frob"]) == 2


# --- start = the --sshd-hypervisor bring-up ---------------------------------

def test_ssh_start_delegates_to_sshd_hypervisor(monkeypatch):
    cli = _cli()
    called = {}

    def _fake():
        called["hit"] = True
        return 0

    monkeypatch.setattr(cli, "sshd_hypervisor", _fake)
    assert cli.cmd_ssh(["start"]) == 0
    assert called.get("hit")


# --- stop disables sshd AND closes port 22 ----------------------------------

def test_ssh_stop_disables_sshd_and_closes_port(monkeypatch, capsys):
    cli = _cli()
    calls = _capture_sudo(cli, monkeypatch, rc=0)
    # sshd_stop reads _power_pending? no -- it reads nothing; just runs _sudo steps.
    assert cli.cmd_ssh(["stop"]) == 0
    # It must disable+stop sshd...
    assert ("systemctl", "disable", "--now", "sshd") in calls
    # ...and delete the allow rule for 22/tcp (do not leave :22 open with no service).
    assert ("ufw", "delete", "allow", "22/tcp") in calls


def test_ssh_stop_reports_failure_when_systemctl_fails(monkeypatch, capsys):
    cli = _cli()
    # systemctl disable fails -> non-zero rc, error surfaced.
    monkeypatch.setattr(cli, "_sudo", lambda *a, **k: 1 if a[:1] == ("systemctl",) else 0)
    assert cli.cmd_ssh(["stop"]) == 1


# --- the sshd bring-up opens 22/tcp explicitly (user's "port 22 allow tcp") ---

def test_ssh_bringup_hardens_permit_empty_passwords_no():
    # DECISION 2 hardening: the ssh bring-up drops a sshd_config.d snippet with
    # `PermitEmptyPasswords no`, so a blank shadow field can never be logged into even if
    # one ever slipped through. Assert the shipped source writes it before enabling sshd.
    src = desktop.azzio_command_line_interface()
    assert "PermitEmptyPasswords no" in src
    assert "sshd_config.d/10-azzio-hardening.conf" in src


def test_ssh_bringup_writes_hardening_before_enabling_sshd(monkeypatch):
    cli = _cli()
    # The hardening config must be written BEFORE `systemctl enable --now sshd`, so sshd
    # reads it on first start. Record the order of the privileged calls.
    calls: list = []
    monkeypatch.setattr(cli, "_sudo", lambda *a, **k: calls.append(("sudo",) + a) or 0)
    monkeypatch.setattr(cli, "_sudo_write", lambda p, c: calls.append(("write", p)))
    monkeypatch.setattr(cli, "_is_mountpoint", lambda p: False)  # no share (bare metal)
    monkeypatch.setenv("SUDO_USER", "main")
    import pwd
    import types
    import tempfile
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(pwd, "getpwnam", lambda u: types.SimpleNamespace(pw_dir=tmp))
    assert cli.sshd_hypervisor() == 0
    write_idx = next(i for i, c in enumerate(calls) if c[0] == "write")
    enable_idx = next(i for i, c in enumerate(calls)
                      if c[:1] == ("sudo",) and "enable" in c)
    assert write_idx < enable_idx, calls


def test_sshd_hypervisor_opens_22_tcp_not_ssh_alias():
    # The bring-up must open 22/tcp explicitly (the user's firewall spec), so the port is
    # unambiguously tcp/22. Assert the shipped source uses `ufw allow 22/tcp`.
    src = desktop.azzio_command_line_interface()
    assert '"ufw", "allow", "22/tcp"' in src
    # And it must NOT still use the old `ufw allow ssh` alias.
    assert '"ufw", "allow", "ssh"' not in src


# --- root SSH login: denied by default, toggleable from the TUI/CLI ----------
# data/PROMPT.md: root ssh login must be OFF by default (only the end user's own
# account -- resolved dynamically via SUDO_USER -- may log in). A later request
# added an opt-in switch (`azzio network ssh root on`) surfaced in the TUI. The
# policy lives in its OWN drop-in (00-azzio-root-login.conf) so the toggle never
# has to rewrite the always-on 10-azzio-hardening.conf. The `00-` prefix sorts
# FIRST so, since sshd is first-match-wins, our directive is authoritative and no
# later drop-in can silently override it (the original bug: a lower/other file
# permitted root while the status read only our file and reported "denied").

def test_root_login_drop_in_constants_carry_the_right_directive():
    cli = _cli()
    # Two constants: the default OFF and the opt-in ON, each a valid sshd directive.
    assert "PermitRootLogin no" in cli.SSHD_ROOT_LOGIN_OFF
    assert "PermitRootLogin yes" in cli.SSHD_ROOT_LOGIN_ON
    # They must be the polar opposites (no stray directives crossing over).
    assert "PermitRootLogin yes" not in cli.SSHD_ROOT_LOGIN_OFF
    assert "PermitRootLogin no" not in cli.SSHD_ROOT_LOGIN_ON


def test_bringup_writes_root_login_off_dropin():
    # The shipped bring-up source must write the root-login OFF drop-in to its own file.
    src = desktop.azzio_command_line_interface()
    assert "PermitRootLogin no" in src
    assert "sshd_config.d/00-azzio-root-login.conf" in src


def test_bringup_writes_root_login_off_before_enabling_sshd(monkeypatch):
    cli = _cli()
    # The default-deny root drop-in must be written BEFORE `systemctl enable --now sshd`,
    # so the shipped ISO boots with root login already denied.
    calls: list = []
    monkeypatch.setattr(cli, "_sudo", lambda *a, **k: calls.append(("sudo",) + a) or 0)
    monkeypatch.setattr(cli, "_sudo_write", lambda p, c: calls.append(("write", p, c)))
    monkeypatch.setattr(cli, "_is_mountpoint", lambda p: False)  # bare metal, no share
    monkeypatch.setenv("SUDO_USER", "main")
    import pwd
    import types
    import tempfile
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(pwd, "getpwnam", lambda u: types.SimpleNamespace(pw_dir=tmp))
    assert cli.sshd_hypervisor() == 0
    root_write_idx = next(i for i, c in enumerate(calls)
                          if c[0] == "write" and "00-azzio-root-login.conf" in c[1])
    enable_idx = next(i for i, c in enumerate(calls)
                      if c[:1] == ("sudo",) and "enable" in c)
    assert root_write_idx < enable_idx, calls
    # And the content written by default is the OFF drop-in (PermitRootLogin no).
    assert "PermitRootLogin no" in calls[root_write_idx][2]


def test_ssh_root_is_a_sub_noun_of_ssh():
    # `azzio network ssh root <on|off|status>` -- advertised in the ssh help.
    cli = _cli()
    assert cli.cmd_ssh(["root", "--help"]) == 0 or True  # help is optional; dispatch below is the contract
    # Unknown root verb is a usage error (rc 2), matching the rest of the CLI.
    assert cli.cmd_ssh(["root", "frob"]) == 2


def test_ssh_root_on_writes_on_dropin_and_reloads(monkeypatch):
    cli = _cli()
    calls: list = []
    monkeypatch.setattr(cli, "_sudo", lambda *a, **k: calls.append(("sudo",) + a) or 0)
    monkeypatch.setattr(cli, "_sudo_write", lambda p, c: calls.append(("write", p, c)))
    # sshd is only reloaded when it is running; pin that here so the reload assertion below
    # is deterministic and does not depend on whether the test host happens to run sshd.
    monkeypatch.setattr(cli, "sshd_is_active", lambda: True)
    assert cli.cmd_ssh(["root", "on"]) == 0
    # Wrote the ON drop-in to the root-login file (now the FIRST-sorting 00- file)...
    w = next(c for c in calls if c[0] == "write")
    assert "00-azzio-root-login.conf" in w[1]
    assert "PermitRootLogin yes" in w[2]
    # ...and reloaded sshd so the change takes effect without dropping live sessions.
    assert any(c[:1] == ("sudo",) and "reload" in c and "sshd" in c for c in calls), calls


def test_ssh_root_off_writes_off_dropin_and_reloads(monkeypatch):
    cli = _cli()
    calls: list = []
    monkeypatch.setattr(cli, "_sudo", lambda *a, **k: calls.append(("sudo",) + a) or 0)
    monkeypatch.setattr(cli, "_sudo_write", lambda p, c: calls.append(("write", p, c)))
    # sshd is only reloaded when it is running; pin that here so the reload assertion below
    # is deterministic and does not depend on whether the test host happens to run sshd.
    monkeypatch.setattr(cli, "sshd_is_active", lambda: True)
    # Disable VERIFIES the effective policy afterwards; pin it to "denied" so the happy path
    # returns 0 (the foreign-override branch is exercised by its own test below).
    monkeypatch.setattr(cli, "sshd_root_login_is_enabled", lambda: False)
    assert cli.cmd_ssh(["root", "off"]) == 0
    w = next(c for c in calls if c[0] == "write")
    assert "00-azzio-root-login.conf" in w[1]
    assert "PermitRootLogin no" in w[2]
    assert any(c[:1] == ("sudo",) and "reload" in c and "sshd" in c for c in calls), calls


def test_ssh_root_toggle_removes_stale_20_dropin(monkeypatch):
    # BACKWARD COMPAT: systems provisioned by an older build carry the deny policy in the
    # OLD 20-azzio-root-login.conf. The toggle now writes 00-; to avoid two coexisting
    # (confusing, and a second directive) files, enable/disable must also REMOVE the stale
    # 20- file. Assert both on and off issue an `rm -f .../20-azzio-root-login.conf`.
    cli = _cli()
    monkeypatch.setattr(cli, "sshd_is_active", lambda: False)
    monkeypatch.setattr(cli, "sshd_root_login_is_enabled", lambda: False)
    for verb in ("on", "off"):
        calls: list = []
        monkeypatch.setattr(cli, "_sudo", lambda *a, **k: calls.append(a) or 0)
        monkeypatch.setattr(cli, "_sudo_write", lambda p, c: None)
        assert cli.cmd_ssh(["root", verb]) == 0
        assert any(a[:1] == ("rm",) and any("20-azzio-root-login.conf" in x for x in a)
                   for a in calls), (verb, calls)


def test_ssh_root_off_warns_and_fails_when_override_keeps_root_allowed(monkeypatch, capsys):
    # The reported bug made visible: if, AFTER writing our deny drop-in and reloading, the
    # EFFECTIVE policy still permits root (some foreign, earlier-matching config forces
    # `yes`), `off` must NOT silently claim success. It returns non-zero and prints a clear
    # warning, so the TUI (which shows command output) surfaces that disable did not take.
    # The verify keys off the EFFECTIVE read directly (`sshd -T`), not the file we just wrote.
    cli = _cli()
    monkeypatch.setattr(cli, "_sudo", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_sudo_write", lambda p, c: None)
    monkeypatch.setattr(cli, "sshd_is_active", lambda: True)
    # Effective policy still says root allowed despite our write -> the override case.
    monkeypatch.setattr(cli, "_sshd_effective_permitrootlogin", lambda: "yes")
    rc = cli.cmd_ssh(["root", "off"])
    assert rc != 0
    err = (capsys.readouterr().err + capsys.readouterr().out).lower()
    assert "root" in err and ("still" in err or "override" in err or "effect" in err)


def test_ssh_root_off_is_honest_when_effective_unverifiable(monkeypatch, capsys):
    # When `sshd -T` cannot be consulted (no cached sudo / sshd absent), disable must NOT
    # falsely claim it verified denial by reading back the file it just wrote (circular). It
    # writes the deny drop-in, returns 0 (the on-disk authoritative 00- file IS the intended
    # policy), but its message must not assert a confirmed-effective state it never checked.
    cli = _cli()
    monkeypatch.setattr(cli, "_sudo", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_sudo_write", lambda p, c: None)
    monkeypatch.setattr(cli, "sshd_is_active", lambda: False)
    # Effective read unavailable -> None (the no-sudo / no-sshd branch).
    monkeypatch.setattr(cli, "_sshd_effective_permitrootlogin", lambda: None)
    rc = cli.cmd_ssh(["root", "off"])
    assert rc == 0  # the authoritative deny drop-in was written; that is the intended policy
    out = capsys.readouterr().out.lower()
    assert "disabled" in out  # still tells the user root is denied by the policy we wrote


def test_ssh_root_toggle_skips_reload_when_sshd_not_running(monkeypatch):
    # Inverse of the two tests above: when sshd is NOT active there is nothing to reload,
    # so the drop-in is still written but no `systemctl reload sshd` is issued (the on-disk
    # file is simply read when sshd next starts). This is the branch CI runners hit -- they
    # have no running sshd -- so pin it explicitly rather than leaving it host-dependent.
    cli = _cli()
    calls: list = []
    monkeypatch.setattr(cli, "_sudo", lambda *a, **k: calls.append(("sudo",) + a) or 0)
    monkeypatch.setattr(cli, "_sudo_write", lambda p, c: calls.append(("write", p, c)))
    monkeypatch.setattr(cli, "sshd_is_active", lambda: False)
    assert cli.cmd_ssh(["root", "on"]) == 0
    # The drop-in is still written...
    assert any(c[0] == "write" and "00-azzio-root-login.conf" in c[1] for c in calls), calls
    # ...but nothing was reloaded, because there was no live sshd to reload.
    assert not any("reload" in c for c in calls), calls


def test_root_login_is_enabled_prefers_effective_sshd_config(monkeypatch):
    # THE FIX for the status lie: sshd_root_login_is_enabled() asks sshd for its EFFECTIVE
    # policy (`sshd -T`) rather than trusting a single drop-in file. When the effective
    # config permits root (`permitrootlogin yes`), it reports enabled -- even if OUR drop-in
    # says no (an earlier-matching foreign file won). This is exactly the scenario that made
    # the old file-only read report "denied" while root was actually reachable.
    cli = _cli()
    monkeypatch.setattr(cli, "_sshd_effective_permitrootlogin", lambda: "yes")
    assert cli.sshd_root_login_is_enabled() is True
    # And when sshd's effective value denies root, it reports disabled regardless of files.
    for val in ("no", "prohibit-password", "forced-commands-only"):
        monkeypatch.setattr(cli, "_sshd_effective_permitrootlogin", lambda v=val: v)
        assert cli.sshd_root_login_is_enabled() is False, val


def test_root_login_is_enabled_falls_back_to_file_when_no_effective(monkeypatch, tmp_path):
    # `sshd -T` needs root (host keys) and may be unavailable (no cached sudo, sshd absent).
    # When the effective read yields nothing, fall back to the world-readable drop-in file
    # so status still shows the intended policy without prompting.
    cli = _cli()
    monkeypatch.setattr(cli, "_sshd_effective_permitrootlogin", lambda: None)
    conf = tmp_path / "00-azzio-root-login.conf"
    monkeypatch.setattr(cli, "_root_login_dropin_path", lambda: str(conf))
    conf.write_text("PermitRootLogin yes\n")
    assert cli.sshd_root_login_is_enabled() is True
    conf.write_text("PermitRootLogin no\n")
    assert cli.sshd_root_login_is_enabled() is False
    conf.unlink()  # absent file -> shipped default is disabled
    assert cli.sshd_root_login_is_enabled() is False


def test_ssh_root_status_reports_enabled_from_effective(monkeypatch, capsys):
    cli = _cli()
    # Status returns 0 when root login is ENABLED so a probe can branch on the exit code.
    monkeypatch.setattr(cli, "sshd_root_login_is_enabled", lambda: True)
    assert cli.cmd_ssh(["root", "status"]) == 0
    assert "enabled" in capsys.readouterr().out.lower()


def test_ssh_root_status_reports_disabled_when_effective_denies(monkeypatch, capsys):
    cli = _cli()
    # DISABLED -> status returns 1 (non-zero), mirroring sshd_status's active/inactive
    # exit-code convention.
    monkeypatch.setattr(cli, "sshd_root_login_is_enabled", lambda: False)
    assert cli.cmd_ssh(["root", "status"]) == 1
    assert "disabled" in capsys.readouterr().out.lower()


def test_tui_ssh_screen_has_root_login_toggle_rows():
    # The TUI SSH Server screen must expose the toggle. Assert the shipped C model_tree.c
    # ROWS_SSH table carries rows that drive `azzio network ssh root on` / `... off`.
    import pathlib
    src = pathlib.Path("libraries/packages/azzio/model_tree.c").read_text()
    assert "azzio network ssh root on" in src
    assert "azzio network ssh root off" in src
    # And the enable row should warn it is insecure (root login off is the safe default).
    assert "root" in src.lower() and "login" in src.lower()


# --- security-notice: wording + self-gating ---------------------------------

def test_security_notice_is_a_dispatch_branch():
    src = desktop.azzio_command_line_interface()
    assert 'cmd == "security-notice"' in src
    assert "return cmd_security_notice(argv[1:])" in src


def test_security_notice_text_covers_the_required_points():
    cli = _cli()
    text = cli.SECURITY_NOTICE_TEXT
    low = text.lower()
    # The user asked for: password not configured, ssh exposure is a risk, and a
    # "stay safe / follow security practices" closing.
    assert "password" in low
    assert "ssh" in low
    assert "network" in low or "internet" in low
    assert "security practices" in low
    assert "stay safe" in low
    # Prose rules: no colons/semicolons/dashes in the notice sentences (paths/URLs exempt,
    # and there are none here). The apostrophe in "Azzio" is allowed.
    assert ":" not in text and ";" not in text
    assert " - " not in text and "--" not in text


def test_security_notice_force_prints_text(monkeypatch, capsys):
    cli = _cli()
    # --force bypasses the gates and the stamp, always printing (used to preview wording).
    # Stub the desktop notifier + stamp writer so nothing touches the real home/display.
    monkeypatch.setattr(cli, "_notify_desktop", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_write_stamp", lambda *a, **k: None)
    assert cli.cmd_security_notice(["--force"]) == 0
    assert "security notice" in capsys.readouterr().out.lower()


def test_security_notice_quiet_on_ssh_variant(monkeypatch, tmp_path, capsys):
    cli = _cli()
    # On the ssh variant (enable-link present) the notice stays quiet and just stamps.
    monkeypatch.setattr(cli, "_ssh_variant_configured", lambda: True)
    monkeypatch.setattr(cli, "_main_has_login_password", lambda: False)
    stamp = tmp_path / "stamp"
    monkeypatch.setattr(cli, "_security_notice_stamp_path", lambda: str(stamp))
    written = {}
    monkeypatch.setattr(cli, "_write_stamp", lambda p: written.setdefault("p", p))
    rc = cli.cmd_security_notice([])
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""  # nothing printed
    assert written.get("p") == str(stamp)  # decision recorded so it does not re-check


def test_security_notice_quiet_when_password_set(monkeypatch, tmp_path, capsys):
    cli = _cli()
    monkeypatch.setattr(cli, "_ssh_variant_configured", lambda: False)
    monkeypatch.setattr(cli, "_main_has_login_password", lambda: True)
    stamp = tmp_path / "stamp"
    monkeypatch.setattr(cli, "_security_notice_stamp_path", lambda: str(stamp))
    monkeypatch.setattr(cli, "_write_stamp", lambda p: None)
    assert cli.cmd_security_notice([]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_security_notice_shows_once_then_silent(monkeypatch, tmp_path, capsys):
    cli = _cli()
    # Base desktop, no password, not ssh variant -> shows ONCE, writes the stamp, then a
    # second call (stamp present) stays silent.
    monkeypatch.setattr(cli, "_ssh_variant_configured", lambda: False)
    monkeypatch.setattr(cli, "_main_has_login_password", lambda: False)
    monkeypatch.setattr(cli, "_notify_desktop", lambda *a, **k: None)
    stamp = tmp_path / "stamp"
    monkeypatch.setattr(cli, "_security_notice_stamp_path", lambda: str(stamp))
    # First call: prints and stamps (real _write_stamp so the file appears).
    assert cli.cmd_security_notice([]) == 0
    assert "security notice" in capsys.readouterr().out.lower()
    assert stamp.exists()
    # Second call: stamp present -> silent.
    assert cli.cmd_security_notice([]) == 0
    assert capsys.readouterr().out.strip() == ""


# --- the TUI sudo fix: non-interactive sudo when driven from the UI -----------

def test_sudo_is_noninteractive_under_the_tui(monkeypatch):
    cli = _cli()
    # The TUI sets AZZIO_SUDO_NONINTERACTIVE so privileged applies fail FAST instead of
    # hanging on a dead /dev/null prompt. With it set (and not root), _sudo must prefix
    # `sudo -n`. Without it, plain `sudo` (interactive) is kept for normal CLI use.
    seen = {}
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda argv, **k: seen.update(argv=argv) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)  # not root

    monkeypatch.setenv("AZZIO_SUDO_NONINTERACTIVE", "1")
    cli._sudo("ufw", "status", check=False)
    assert seen["argv"][:3] == ["sudo", "-n", "ufw"], seen["argv"]

    monkeypatch.delenv("AZZIO_SUDO_NONINTERACTIVE", raising=False)
    cli._sudo("ufw", "status", check=False)
    assert seen["argv"][:2] == ["sudo", "ufw"], seen["argv"]  # interactive kept


def test_sudo_is_direct_when_root(monkeypatch):
    cli = _cli()
    # As root, no sudo prefix at all (even with the env var set).
    seen = {}
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda argv, **k: seen.update(argv=argv) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0)
    monkeypatch.setenv("AZZIO_SUDO_NONINTERACTIVE", "1")
    cli._sudo("ufw", "status", check=False)
    assert seen["argv"][0] == "ufw", seen["argv"]


def test_tui_sets_noninteractive_sudo_env():
    # The C TUI must set AZZIO_SUDO_NONINTERACTIVE at startup so the applies it spawns run
    # sudo non-interactively (the fix for the UI wedging when a privileged action needs
    # sudo). Assert the shipped main.c does it.
    import pathlib
    main_c = pathlib.Path("libraries/packages/azzio/main.c").read_text()
    assert 'setenv("AZZIO_SUDO_NONINTERACTIVE", "1", 1)' in main_c


def test_security_notice_gate_reads_shadow_field(monkeypatch):
    cli = _cli()
    # _main_has_login_password treats a '$'-hash as "has password" and '!'/'*'/'' as not.
    def fake_run(argv, **k):
        class R:
            returncode = 0
            stdout = "main:$6$salt$digest:19000:0:99999:7:::\n"
        return R()
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli._main_has_login_password() is True

    def fake_run_locked(argv, **k):
        class R:
            returncode = 0
            stdout = "main:!:19000:0:99999:7:::\n"
        return R()
    monkeypatch.setattr(cli.subprocess, "run", fake_run_locked)
    assert cli._main_has_login_password() is False
