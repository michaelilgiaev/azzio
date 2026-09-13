"""The `azzio power` command -- shutdown / restart / sleep / lock, with timers.

These pin, against the BUNDLED shipped script (packages.azzio.bundle.bundle_source -- the
exact /usr/local/bin/azzio artifact):

  * that `power` (and the top-level `shutdown`/`restart`/`sleep`/`lock` convenience verbs)
    are real dispatch branches in main();
  * the duration parser (30m/1h/90s/N-minutes) and HH:MM validation;
  * that immediate actions call `systemctl poweroff|reboot|suspend`, scheduling builds a
    `systemd-run --unit=azzio-<action> --on-active=/--on-calendar=` timer, --status reads
    `systemctl list-timers`, and --cancel stops the transient unit -- all WITHOUT touching
    the host (`_sudo` and the reads are stubbed);
  * lock tries `loginctl lock-session` first;
  * the guard rails: unknown verb -> rc 2, bad duration/time -> rc 2, scheduling with no
    --in/--at -> rc 2.

The command line interface is exercised via its bundle executed in one namespace, so tests
drive the real functions exactly as shipped.
"""

from __future__ import annotations

import types

import pytest

from packages.azzio.bundle import bundle_source
from packages import openbox as desktop


def _cli():
    """Exec the bundled azzio CLI in a fresh module namespace (as shipped)."""
    mod = types.ModuleType("azzio_cli_power_test")
    exec(compile(bundle_source(), "azzio_cli", "exec"), mod.__dict__)
    return mod


def _capture_sudo(cli, monkeypatch, rc=0):
    calls: list[tuple] = []
    monkeypatch.setattr(cli, "_sudo", lambda *a, **k: (calls.append(a) or rc))
    return calls


def _have(cli, monkeypatch, have=True):
    monkeypatch.setattr(cli, "_have", lambda prog: have)


# --- dispatch wiring --------------------------------------------------------

def test_power_is_a_dispatch_branch_in_main():
    # Assert against the SHIPPED bundle source (as the network test does) -- the exec'd
    # namespace has no source file for inspect.getsource.
    src = desktop.azzio_command_line_interface()
    assert 'cmd == "power"' in src
    assert "return cmd_power(argv[1:])" in src
    # The convenience top-level verbs exist too. restart/reboot share one branch
    # (cmd in ("restart", "reboot")), so assert each verb name appears rather than a
    # specific `cmd == "<verb>"` spelling.
    for verb in ("shutdown", "restart", "reboot", "sleep", "lock"):
        assert f'"{verb}"' in src


def test_power_help_exits_zero(capsys):
    cli = _cli()
    assert cli.cmd_power(["--help"]) == 0
    out = capsys.readouterr().out
    assert "shutdown" in out and "restart" in out and "sleep" in out and "lock" in out


def test_power_unknown_verb_is_rc_two(capsys):
    cli = _cli()
    assert cli.cmd_power(["frobnicate"]) == 2


# --- duration parsing -------------------------------------------------------

@pytest.mark.parametrize("token,secs", [
    ("90s", 90), ("30m", 1800), ("2h", 7200), ("1d", 86400),
    ("1h30m", 5400), ("5", 300),  # bare number = minutes
    # Sub-minute / fractional precision (the reported "give me seconds and easy control"):
    ("10s", 10), ("45s", 45),
    ("0.1", 6),        # bare DECIMAL = minutes -> 0.1 min = 6s (so `--in 0.1` works)
    ("0.5", 30),       # 0.5 min = 30s
    ("1.5m", 90),      # decimal WITH a unit -> 1.5 min = 90s
    ("1.5h", 5400),    # 1.5 h = 5400s (decimals with units are now accepted)
    ("0.5h", 1800),    # 0.5 h = 1800s
    ("2.5s", 3),       # fractional seconds round to the nearest whole second (2.5 -> 3)
    ("0.25m", 15),     # 0.25 min = 15s
])
def test_parse_duration_valid(token, secs):
    cli = _cli()
    assert cli.parse_duration(token) == secs


@pytest.mark.parametrize("token", [
    "", "0", "-5", "x", "10x", "1h30", "h",
    "0", "0.0", "0s", "0m",   # zero (in any form) is never a valid future delay
    "-0.5", "1.2.3", ".", "1.h", ".5h",  # malformed decimals
    # Forms raw float() would MIS-accept -- the parser must reject them, not let a Python
    # float quirk through: underscores (1_000 -> float 1000!), exponents, hex, a sign.
    "1_000", "1e3", "0x10", "+5", "5.", ".5", "1..5", "1.5.h", "1.5hh", "5m5",
    "0.001",  # 0.001 min = 0.06s rounds to 0s -> rejected (a timer must be in the future)
])
def test_parse_duration_invalid(token):
    cli = _cli()
    assert cli.parse_duration(token) is None


@pytest.mark.parametrize("token", [
    "9" * 400,          # bare number so huge float(token) overflows to inf
    "9" * 400 + "h",    # same in the unit form
    "1" * 320,
])
def test_parse_duration_huge_magnitude_returns_none_not_crash(token):
    # A digit string long enough to overflow float() must be rejected cleanly (None), NOT
    # crash with OverflowError from int(inf + 0.5) -- a user typing a silly number should get
    # the normal "invalid duration" path (rc 2), never a Python traceback.
    cli = _cli()
    assert cli.parse_duration(token) is None


def test_schedule_huge_duration_is_rc_two_not_crash(monkeypatch):
    # End-to-end through the real dispatch: an overflowing --in must surface as rc 2, not a
    # traceback out of _round_half_up.
    cli = _cli()
    monkeypatch.setattr(cli, "_have", lambda prog: True)
    assert cli.cmd_power(["shutdown", "--in", "9" * 400]) == 2


@pytest.mark.parametrize("token", ["²", "①", "²h", "②③"])
def test_parse_duration_non_ascii_digits_dont_crash(token):
    # str.isdigit() accepts non-ASCII "digit" chars (superscript "²", circled "①")
    # that int() then rejects with ValueError. parse_duration must treat them as INVALID
    # (return None), never raise an uncaught exception that surfaces as a traceback.
    cli = _cli()
    assert cli.parse_duration(token) is None


def test_schedule_non_ascii_time_is_rc_two_not_crash(monkeypatch):
    # Same guard on the --at HH:MM validator: a non-ASCII "digit" must be rejected cleanly
    # (rc 2), not crash mid-schedule.
    cli = _cli()
    monkeypatch.setattr(cli, "_have", lambda prog: True)
    assert cli.cmd_power(["shutdown", "--at", "²:²"]) == 2


# --- immediate actions call systemctl ---------------------------------------

@pytest.mark.parametrize("verb,systemctl_verb", [
    ("shutdown", "poweroff"), ("restart", "reboot"), ("sleep", "suspend"),
])
def test_power_now_calls_systemctl(verb, systemctl_verb, monkeypatch):
    cli = _cli()
    _have(cli, monkeypatch, True)
    calls = _capture_sudo(cli, monkeypatch)
    assert cli.cmd_power([verb]) == 0
    assert (calls[-1] == ("systemctl", systemctl_verb)), calls


def test_reboot_is_an_alias_for_restart(monkeypatch):
    # `azzio power reboot` and `azzio reboot` must behave exactly like restart:
    # immediate `systemctl reboot`, same as the `restart` verb.
    cli = _cli()
    _have(cli, monkeypatch, True)
    calls = _capture_sudo(cli, monkeypatch)
    assert cli.cmd_power(["reboot"]) == 0
    assert calls[-1] == ("systemctl", "reboot"), calls


def test_reboot_alias_wired_in_main_and_help():
    # The top-level `reboot` verb is a real dispatch branch (shared with restart), and both
    # help screens mention it.
    src = desktop.azzio_command_line_interface()
    assert '"reboot"' in src and 'cmd in ("restart", "reboot")' in src
    cli = _cli()
    for helped in (cli.cmd_power(["--help"]), cli._power_verb("restart", ["--help"])):
        assert helped == 0
    # cmd_power help lists reboot alongside restart.
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.cmd_power(["--help"])
    assert "reboot" in buf.getvalue()


def test_reboot_alias_schedules_the_restart_unit(monkeypatch):
    # A timed reboot uses the SAME transient unit as restart (azzio-restart), so --status /
    # --cancel of either verb see the same timer.
    cli = _cli()
    _have(cli, monkeypatch, True)
    monkeypatch.setattr(cli, "_power_pending", lambda action: "")
    calls = _capture_sudo(cli, monkeypatch)
    assert cli.cmd_power(["reboot", "--in", "5m"]) == 0
    run = [c for c in calls if c and c[0] == "systemd-run"]
    assert run and "--unit=azzio-restart" in run[0], run


# --- scheduling builds a systemd-run timer ----------------------------------

def test_schedule_in_builds_on_active_timer(monkeypatch):
    cli = _cli()
    _have(cli, monkeypatch, True)
    # _cancel_power runs first (idempotent replace); we only care the systemd-run call is
    # correct, so let reads report "no pending" and record every _sudo argv.
    monkeypatch.setattr(cli, "_power_pending", lambda action: "")
    calls = _capture_sudo(cli, monkeypatch)
    assert cli.cmd_power(["shutdown", "--in", "30m"]) == 0
    run = [c for c in calls if c and c[0] == "systemd-run"]
    assert run, f"expected a systemd-run call, got {calls}"
    argv = run[0]
    assert "--unit=azzio-shutdown" in argv
    assert "--on-active=1800s" in argv
    assert argv[-2:] == ("systemctl", "poweroff")


def test_schedule_at_builds_on_calendar_timer(monkeypatch):
    cli = _cli()
    _have(cli, monkeypatch, True)
    monkeypatch.setattr(cli, "_power_pending", lambda action: "")
    calls = _capture_sudo(cli, monkeypatch)
    assert cli.cmd_power(["restart", "--at", "3:07"]) == 0
    run = [c for c in calls if c and c[0] == "systemd-run"]
    assert run
    argv = run[0]
    assert "--unit=azzio-restart" in argv
    # HH:MM is zero-padded and expanded to a full OnCalendar spec.
    assert any(a == "--on-calendar=*-*-* 03:07:00" for a in argv), argv
    assert argv[-2:] == ("systemctl", "reboot")


def test_schedule_requires_in_or_at(capsys):
    cli = _cli()
    # A stray option that is neither --in/--at/--status/--cancel is rejected.
    assert cli.cmd_power(["shutdown", "--soon"]) == 2


def test_schedule_bad_duration_is_rc_two(monkeypatch, capsys):
    cli = _cli()
    _have(cli, monkeypatch, True)
    assert cli.cmd_power(["shutdown", "--in", "banana"]) == 2


def test_schedule_bad_time_is_rc_two(monkeypatch, capsys):
    cli = _cli()
    _have(cli, monkeypatch, True)
    assert cli.cmd_power(["sleep", "--at", "25:99"]) == 2


def test_at_bare_number_redirects_to_in(monkeypatch, capsys):
    # The reported confusion: `--at 10` / `--at 0.1` -- a bare number is NOT a clock time.
    # It must still be rc 2, but the error should point the user at --in (which DOES take a
    # duration) rather than only saying "use HH:MM".
    cli = _cli()
    _have(cli, monkeypatch, True)
    assert cli.cmd_power(["shutdown", "--at", "10"]) == 2
    err = capsys.readouterr().err
    assert "--in" in err and "10" in err, err


def test_at_bare_decimal_redirects_to_in(monkeypatch, capsys):
    cli = _cli()
    _have(cli, monkeypatch, True)
    assert cli.cmd_power(["sleep", "--at=0.1"]) == 2
    err = capsys.readouterr().err
    assert "--in" in err, err


def test_at_malformed_time_keeps_hhmm_hint(monkeypatch, capsys):
    # A genuinely malformed clock time (not a bare number) still gets the HH:MM guidance and
    # does NOT falsely suggest --in.
    cli = _cli()
    _have(cli, monkeypatch, True)
    assert cli.cmd_power(["restart", "--at", "25:99"]) == 2
    err = capsys.readouterr().err
    assert "HH:MM" in err, err


def test_power_help_contrasts_in_and_at(capsys):
    # Help must make the --in (delay from now) vs --at (wall-clock time) distinction explicit,
    # since conflating them is the reported confusion.
    cli = _cli()
    assert cli.cmd_power(["--help"]) == 0
    out = capsys.readouterr().out.lower()
    assert "from now" in out  # --in is described as counting from now
    assert "clock time" in out or "wall-clock" in out  # --at is a time of day


# --- status + cancel --------------------------------------------------------

def test_status_reads_list_timers(monkeypatch, capsys):
    cli = _cli()
    seen = {}

    def fake_run(argv, **k):
        seen["argv"] = argv
        class R:  # noqa: N801 -- tiny stand-in for CompletedProcess
            returncode = 0
            stdout = ("Mon 2026-08-27 03:07:00 UTC 1h left n/a n/a "
                      "azzio-shutdown.timer azzio-shutdown.service\n")
        return R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    rc = cli.cmd_power(["shutdown", "--status"])
    assert rc == 0
    assert "list-timers" in seen["argv"]
    assert "scheduled" in capsys.readouterr().out


def test_status_reports_none_when_no_timer(monkeypatch, capsys):
    cli = _cli()
    monkeypatch.setattr(cli, "_power_pending", lambda action: "")
    rc = cli.cmd_power(["restart", "--status"])
    assert rc == 1
    assert "no timer" in capsys.readouterr().out.lower()


def test_cancel_stops_the_transient_unit(monkeypatch, capsys):
    cli = _cli()
    monkeypatch.setattr(cli, "_power_pending", lambda action: "something pending")
    calls = _capture_sudo(cli, monkeypatch)
    assert cli.cmd_power(["shutdown", "--cancel"]) == 0
    stops = [c for c in calls if c[:2] == ("systemctl", "stop")]
    assert any("azzio-shutdown.timer" in c for c in stops), calls


# --- noise suppression: no `Unit ... not loaded` wall when nothing is pending ---
# The reported bug: every schedule (which first does an idempotent cancel) and every
# --cancel-with-nothing-pending spewed "Failed to stop azzio-shutdown.timer: Unit not
# loaded" x4 to the terminal. The fix gates the stop/reset-failed on _power_pending: when
# no timer exists, we touch NO unit at all, so systemctl never prints the not-loaded lines.

def test_cancel_when_nothing_pending_touches_no_unit(monkeypatch, capsys):
    cli = _cli()
    monkeypatch.setattr(cli, "_power_pending", lambda action: "")  # nothing scheduled
    calls = _capture_sudo(cli, monkeypatch)
    assert cli.cmd_power(["shutdown", "--cancel"]) == 0
    # With nothing pending, NO systemctl stop/reset-failed is issued (that is what used to
    # print the "Unit ... not loaded" noise). The user gets a clean "no pending" message.
    assert not [c for c in calls if c[:2] == ("systemctl", "stop")], calls
    assert not [c for c in calls if c[:2] == ("systemctl", "reset-failed")], calls
    assert "no pending" in capsys.readouterr().out.lower()


def test_schedule_when_nothing_pending_does_not_pre_stop(monkeypatch, capsys):
    # Scheduling replaces any existing timer, but if there is none it must NOT issue the
    # stop/reset-failed calls (whose stderr was the reported noise) before systemd-run.
    cli = _cli()
    _have(cli, monkeypatch, True)
    monkeypatch.setattr(cli, "_power_pending", lambda action: "")
    calls = _capture_sudo(cli, monkeypatch)
    assert cli.cmd_power(["shutdown", "--in", "5m"]) == 0
    assert not [c for c in calls if c[:2] == ("systemctl", "stop")], calls
    assert not [c for c in calls if c[:2] == ("systemctl", "reset-failed")], calls
    # ...but the actual schedule still happened.
    assert [c for c in calls if c and c[0] == "systemd-run"], calls


def test_schedule_at_accepts_seconds(monkeypatch):
    # --at now also accepts HH:MM:SS for second-precise wall-clock scheduling.
    cli = _cli()
    _have(cli, monkeypatch, True)
    monkeypatch.setattr(cli, "_power_pending", lambda action: "")
    calls = _capture_sudo(cli, monkeypatch)
    assert cli.cmd_power(["shutdown", "--at", "03:07:42"]) == 0
    run = [c for c in calls if c and c[0] == "systemd-run"]
    assert run and any(a == "--on-calendar=*-*-* 03:07:42" for a in run[0]), run


# --- lock -------------------------------------------------------------------

def test_lock_prefers_loginctl(monkeypatch, capsys):
    cli = _cli()
    _have(cli, monkeypatch, True)
    seen = {}

    def fake_run(argv, **k):
        seen.setdefault("first", argv)
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli.cmd_lock([]) == 0
    assert seen["first"][:2] == ["loginctl", "lock-session"]


def test_lock_no_locker_is_rc_one(monkeypatch, capsys):
    cli = _cli()
    _have(cli, monkeypatch, False)  # nothing available
    assert cli.cmd_lock([]) == 1
