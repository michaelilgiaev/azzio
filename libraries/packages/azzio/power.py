#!/usr/bin/env python3
"""azzio guest command line interface -- `azzio power` (shutdown / restart / sleep / lock, with timers).

Plain wrappers over the confusing raw session/power tools, matching the rest of the azzio
CLI (base command vs azzio wrapper). Four verbs:

  shutdown  -> systemctl poweroff
  restart   -> systemctl reboot
  sleep     -> systemctl suspend
  lock      -> lock the screen (loginctl lock-session, with an xdg/light-locker fallback)

The first three take TIMER flags so an action can be scheduled, inspected, and cancelled:

  --in <DURATION>   run after a delay (e.g. 30m, 1h, 90s, or a bare number = minutes)
  --at <HH:MM>      run at a wall-clock time today (or tomorrow if already past)
  --status          show whether this action has a pending timer (and when)
  --cancel          cancel this action's pending timer

TIMER MECHANISM. All three scheduled actions use a TRANSIENT systemd timer created with
`systemd-run --on-active=/--on-calendar=`, named `azzio-<action>` (so
`azzio-shutdown.timer` etc.). One mechanism covers poweroff, reboot AND suspend uniformly
(plain `shutdown(8)` cannot schedule a suspend), and `--status`/`--cancel` just inspect and
stop that transient unit. No separate on-disk state file to drift: the timer IS the state.

lock has no timer (locking on a delay is niche); it always locks now.

Everything below lands in the single bundled /usr/local/bin/azzio namespace, so it calls
the shared helpers (_err, _have) by bare name and uses os/subprocess from the bundle header.
See common.py / bundle.py.
"""

from __future__ import annotations

# BUNDLE_START

# The transient systemd units are named azzio-<action>{,.timer}. Keeping a single prefix
# means --status/--cancel can find them without any bookkeeping file.
_POWER_UNIT_PREFIX = "azzio-"

# The scheduled actions (lock is excluded -- it has no timer) mapped to the systemctl verb
# the timer ultimately runs.
_POWER_ACTIONS = {
    "shutdown": "poweroff",
    "restart": "reboot",
    "sleep": "suspend",
}


def _power_unit(action: str) -> str:
    """The transient unit BASENAME for an action (systemd-run appends .service/.timer)."""
    return f"{_POWER_UNIT_PREFIX}{action}"


def _human_secs(secs: int) -> str:
    """Render a whole-seconds delay as a compact human string: `45s`, `2m30s`, `1h5m`,
    `1d2h`. Only the two largest non-zero units are shown (enough for a confirmation line),
    so a sub-minute schedule reports `6s` instead of the old `0 min`. Pure/string-testable."""
    if secs < 60:
        return f"{secs}s"
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if secs >= size:
            parts.append(f"{secs // size}{unit}")
            secs %= size
    return "".join(parts[:2])


_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _round_half_up(x: float) -> int | None:
    """Round a NON-NEGATIVE float to the nearest int, halves going UP (2.5 -> 3). Python's
    built-in round() uses banker's rounding (2.5 -> 2), which is surprising for a duration a
    user typed; half-up matches the intuition that "2.5s" is at least 2.5 so it becomes 3.

    Returns None for a non-finite input (inf/nan). A silly-but-all-digit magnitude like a
    400-digit number overflows float()*60 to inf, and `int(inf + 0.5)` would raise
    OverflowError -- so we reject it here and the caller treats it as an invalid duration
    (rc 2) instead of surfacing a traceback. `x - x` is 0.0 only for a FINITE x (inf-inf and
    nan-nan are nan), so it is a dependency-free finite test."""
    if x - x != 0:
        return None
    return int(x + 0.5)


def _num_ok(s: str) -> bool:
    """True if `s` is a plain, ASCII, NON-NEGATIVE decimal number (`5`, `0.1`, `30.5`).

    Guards the float() the callers do: str.isdigit()/isdecimal() accept non-ASCII digit
    characters ("²", "①") that float() then rejects, and we also want to reject signs,
    exponents, and malformed forms like "1.2.3", ".", or "1." here rather than let float()
    accept some of them. So: at most one dot, at least one ASCII digit, digits/one-dot only."""
    if not s or not s.isascii():
        return False
    if s.count(".") > 1:
        return False
    if not any(c.isdigit() for c in s):        # ".", "" -> no actual digit
        return False
    if s.startswith(".") or s.endswith("."):   # ".5" / "1." are rejected (require D.D form)
        return False
    return all(c.isdigit() or c == "." for c in s)


def parse_duration(token: str) -> int | None:
    """Parse a human duration into whole SECONDS, or None if it is not a valid duration.

    Accepts, case-insensitively:
      * a unit form -- `90s`, `30m`, `2h`, `1d`, a combo like `1h30m`, and now DECIMAL
        magnitudes with a unit (`1.5h`, `0.5m`, `2.5s`); fractional results round to the
        nearest whole second.
      * a BARE number -- to match the `shutdown +N` convention users expect, this is MINUTES
        (`30` == 30 minutes). Bare DECIMALS work too (`0.1` == 0.1 min == 6s), so `--in 0.1`
        gives fine sub-minute control without needing to know the `s` suffix.
    Zero (in any form) and negatives are rejected -- a timer must be in the future. Pure
    (string in, int out) so it is trivially unit-testable."""
    token = token.strip().lower()
    if not token:
        return None
    # Bare number (no unit letters) -> MINUTES. Accepts an integer or a decimal; _num_ok
    # rejects non-ASCII "digits", signs, and malformed decimals so float() below is safe.
    if _num_ok(token):
        secs = _round_half_up(float(token) * 60)
        return secs if secs and secs > 0 else None
    # Unit form: a sequence of <number><unit> chunks (e.g. 1h30m, 1.5h, 90s). Accumulate the
    # magnitude for each unit; the magnitude may be a decimal. A stray char, a unit with no
    # number, or trailing digits with no unit are all invalid.
    total = 0.0
    num = ""
    for ch in token:
        if ch.isascii() and (ch.isdigit() or ch == "."):
            num += ch
        elif ch in _DURATION_UNITS and num:
            if not _num_ok(num):        # e.g. "1.h" / "1.2.3h" -> reject cleanly
                return None
            total += float(num) * _DURATION_UNITS[ch]
            num = ""
        else:
            return None  # stray char, or a unit with no preceding number
    if num:  # trailing digits with no unit are invalid in the unit form
        return None
    secs = _round_half_up(total)
    return secs if secs and secs > 0 else None


def _valid_hhmm(token: str) -> str | None:
    """Validate an HH:MM (or HH:MM:SS) wall-clock token; return it normalised as a full
    HH:MM:SS OnCalendar time-of-day (zero-padded) or None. Used for --at: the SS field is
    optional (defaults to :00) so a user can schedule to the second (`--at 03:07:42`)."""
    parts = token.split(":")
    # isascii() guards the int() below: str.isdigit() accepts non-ASCII digit characters
    # (e.g. "²") that int() then rejects with ValueError, so a bare isdigit() would crash.
    if len(parts) not in (2, 3) or not all(p.isascii() and p.isdigit() for p in parts):
        return None
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) == 3 else 0
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
        return None
    return f"{h:02d}:{m:02d}:{s:02d}"


def _bad_at_message(action: str, value: str) -> str:
    """Error string for a rejected --at value. The reported confusion is people typing a bare
    number/duration into --at (e.g. `--at 10`, `--at 0.1`) expecting a delay -- so when the
    value would have been a VALID --in duration, redirect them to --in instead of only saying
    "use HH:MM". A genuinely malformed clock time (`25:99`, `ab:cd`) keeps the HH:MM hint."""
    if parse_duration(value) is not None:
        return (f"azzio power {action}: --at takes a CLOCK TIME like 23:30, not a duration. "
                f"For a delay from now, use `--in {value}`.")
    return (f"azzio power {action}: invalid time '{value}' "
            "(use a 24-hour HH:MM or HH:MM:SS, e.g. 23:30).")


def _schedule_power(action: str, when_args: list[str]) -> int:
    """Create the transient timer for a scheduled action from its --in/--at args.

    Builds a `systemd-run --unit=azzio-<action> --on-active=<secs> / --on-calendar=<HH:MM>
    systemctl <verb>` invocation. A pre-existing timer for the same action is cleared first
    (--cancel semantics) so re-scheduling replaces rather than stacks."""
    verb = _POWER_ACTIONS[action]
    on_active_secs: int | None = None
    on_calendar: str | None = None
    i = 0
    while i < len(when_args):
        a = when_args[i]
        if a == "--in":
            if i + 1 >= len(when_args):
                _err(f"azzio power {action}: --in needs a duration (e.g. 30m, 1h, 90s).")
                return 2
            on_active_secs = parse_duration(when_args[i + 1])
            if on_active_secs is None:
                _err(f"azzio power {action}: invalid duration '{when_args[i + 1]}' "
                     "(use e.g. 30m, 1h, 90s, or a plain number of minutes).")
                return 2
            i += 2
            continue
        if a.startswith("--in="):
            on_active_secs = parse_duration(a.split("=", 1)[1])
            if on_active_secs is None:
                _err(f"azzio power {action}: invalid duration in '{a}'.")
                return 2
            i += 1
            continue
        if a == "--at":
            if i + 1 >= len(when_args):
                _err(f"azzio power {action}: --at needs a time (HH:MM).")
                return 2
            val = when_args[i + 1]
            on_calendar = _valid_hhmm(val)
            if on_calendar is None:
                _err(_bad_at_message(action, val))
                return 2
            i += 2
            continue
        if a.startswith("--at="):
            val = a.split("=", 1)[1]
            on_calendar = _valid_hhmm(val)
            if on_calendar is None:
                _err(_bad_at_message(action, val))
                return 2
            i += 1
            continue
        _err(f"azzio power {action}: unknown option '{a}'.")
        return 2

    if on_active_secs is None and on_calendar is None:
        _err(f"azzio power {action}: --in <DURATION> or --at <HH:MM> is required to "
             "schedule.")
        return 2

    if not _have("systemd-run"):
        _err("azzio power: systemd-run not found (systemd is required to schedule).")
        return 1

    # Replace any existing timer for this action so scheduling is idempotent.
    _cancel_power(action, quiet=True)

    unit = _power_unit(action)
    cmd = ["systemd-run", f"--unit={unit}"]
    if on_active_secs is not None:
        cmd.append(f"--on-active={on_active_secs}s")
        human = f"in {_human_secs(on_active_secs)}"
    else:
        # _valid_hhmm already normalised on_calendar to a full HH:MM:SS time-of-day.
        cmd.append(f"--on-calendar=*-*-* {on_calendar}")
        human = f"at {on_calendar}"
    cmd += ["systemctl", verb]
    rc = _sudo(*cmd, check=False)
    if rc != 0:
        _err(f"azzio power {action}: could not schedule the timer.")
        return 1
    print(f"Scheduled {action} ({verb}) {human}. Cancel with "
          f"`azzio power {action} --cancel`.")
    return 0


def _cancel_power(action: str, quiet: bool = False) -> int:
    """Stop this action's transient timer (and its pending service), if any. Best-effort;
    returns 0 whether or not a timer existed (cancelling nothing is not an error).

    IMPORTANT (the reported "Unit ... not loaded" noise): we only issue the stop/reset-failed
    when a timer is ACTUALLY pending (_power_pending is non-empty). When nothing is scheduled
    -- which is the common case both for a bare `--cancel` and for the idempotent pre-clear
    every schedule runs -- we touch NO unit, so systemctl never prints its not-loaded lines.
    The stop that DOES run is `quiet` (stderr -> /dev/null) as a belt-and-braces guard against
    a half-torn-down transient unit. timer + service are stopped/reset in ONE systemctl call
    each, halving the privileged invocations (and any sudo re-prompts)."""
    unit = _power_unit(action)
    had = bool(_power_pending(action))
    if had:
        _sudo("systemctl", "stop", f"{unit}.timer", f"{unit}.service",
              check=False, quiet=True)
        # A transient unit lingers as 'failed'/'dead'; reset so a later --status is clean.
        _sudo("systemctl", "reset-failed", f"{unit}.timer", f"{unit}.service",
              check=False, quiet=True)
    if not quiet:
        print(f"{'Cancelled pending ' + action if had else 'No pending ' + action}.")
    return 0


def _power_pending(action: str) -> str:
    """The next-elapse description of this action's pending timer, or '' if none. Reads
    `systemctl list-timers` for the action's unit (a read; no root needed)."""
    unit = _power_unit(action)
    r = subprocess.run(
        ["systemctl", "list-timers", "--all", "--no-legend", f"{unit}.timer"],
        capture_output=True, text=True, check=False,
    )
    for line in r.stdout.splitlines():
        if f"{unit}.timer" in line:
            return line.strip()
    return ""


def _status_power(action: str) -> int:
    """Print whether this action has a pending timer (and its schedule). Returns 0 when a
    timer is pending, 1 when none -- so a probe/TUI can branch on the exit code."""
    pending = _power_pending(action)
    if pending:
        print(f"{action}: scheduled -- {pending}")
        return 0
    print(f"{action}: no timer scheduled")
    return 1


def _do_power_now(action: str) -> int:
    """Run the action immediately via systemctl (poweroff/reboot/suspend)."""
    verb = _POWER_ACTIONS[action]
    if not _have("systemctl"):
        _err("azzio power: systemctl not found (systemd is required).")
        return 1
    rc = _sudo("systemctl", verb, check=False)
    if rc != 0:
        _err(f"azzio power {action}: `systemctl {verb}` failed.")
        return 1
    return 0


def _power_verb(action: str, args: list[str]) -> int:
    """Shared handler for shutdown/restart/sleep: dispatch the timer flags, else act now.

      --status  -> show pending timer
      --cancel  -> cancel pending timer
      --in/--at -> schedule
      (none)    -> do it now
    """
    if args and args[0] in ("-h", "--help", "help"):
        verb = _POWER_ACTIONS[action]
        print(f"Usage: azzio power {action} [--in <DURATION> | --at <TIME> | "
              "--status | --cancel]\n"
              "\n"
              f"  (no option)      {action.capitalize()} now (systemctl {verb}).\n"
              "  --in <DURATION>  Schedule after a DELAY from now.\n"
              "  --at <TIME>      Schedule at a CLOCK TIME of day (tomorrow if already past).\n"
              "  --status         Show any pending timer for this action.\n"
              "  --cancel         Cancel the pending timer for this action.\n"
              "\n"
              "--in <DURATION> -- a delay from now; pick whichever reads best, combine units:\n"
              "  10s        10 seconds        90s / 30s   seconds\n"
              "  5m / 1.5m  minutes (decimals OK: 1.5m = 90s)\n"
              "  2h / 1d    hours / days      1h30m       combined\n"
              "  10         a BARE number is MINUTES (10 = 10m); 0.1 = 6s\n"
              "--at <TIME> -- a wall-clock time of day, HH:MM or HH:MM:SS (24-hour):\n"
              "  23:30      half past eleven at night     06:05:42   to the second\n"
              "  (a bare number is NOT a time here -- use --in for a delay)\n"
              "\n"
              f"Examples:\n"
              f"  azzio power {action} --in 30s     {action} 30 seconds from now\n"
              f"  azzio power {action} --in 10      {action} 10 minutes from now\n"
              f"  azzio power {action} --at 02:00   {action} at 2 AM\n"
              f"  azzio power {action} --status     is one scheduled?\n"
              f"  azzio power {action} --cancel     call it off")
        return 0
    if "--status" in args:
        return _status_power(action)
    if "--cancel" in args:
        return _cancel_power(action)
    if any(a == "--in" or a.startswith("--in=") or a == "--at" or a.startswith("--at=")
           for a in args):
        return _schedule_power(action, args)
    if args:
        _err(f"azzio power {action}: unknown option '{args[0]}' "
             "(try --in, --at, --status, --cancel).")
        return 2
    return _do_power_now(action)


def cmd_lock(args: list[str]) -> int:
    """`azzio lock` -- lock the screen NOW. Tries loginctl lock-session first (the
    session-manager way, works under any DE), then a couple of common lockers as a
    fallback. No timer (locking on a delay is not useful)."""
    if args and args[0] in ("-h", "--help", "help"):
        print("Usage: azzio lock\n\n  Lock the screen now.")
        return 0
    # loginctl lock-session: asks the session manager to lock; the running locker (if any)
    # picks it up. No root needed for one's own session.
    if _have("loginctl"):
        if subprocess.run(["loginctl", "lock-session"], check=False).returncode == 0:
            print("Screen locked.")
            return 0
    for locker in (["light-locker-command", "-l"], ["xdg-screensaver", "lock"],
                   ["betterlockscreen", "-l"], ["i3lock"]):
        if _have(locker[0]):
            if subprocess.run(locker, check=False).returncode == 0:
                print("Screen locked.")
                return 0
    _err("azzio lock: no screen locker available (tried loginctl, light-locker, "
         "xdg-screensaver, betterlockscreen, i3lock).")
    return 1


def cmd_power(args: list[str]) -> int:
    """`azzio power <shutdown|restart|sleep|lock> [timer flags]` -- the grouped entry
    point. The individual verbs are also exposed at the top level (azzio shutdown, etc.)
    by command_line_interface.main, which calls the same handlers."""
    if not args or args[0] in ("-h", "--help", "help"):
        print("Usage: azzio power <shutdown|restart|reboot|sleep|lock> [options]\n"
              "\n"
              "  shutdown [--in D|--at T|--status|--cancel]  Power off (optionally timed).\n"
              "  restart  [--in D|--at T|--status|--cancel]  Reboot (optionally timed).\n"
              "  reboot   [--in D|--at T|--status|--cancel]  Same as restart (alias).\n"
              "  sleep    [--in D|--at T|--status|--cancel]  Suspend (optionally timed).\n"
              "  lock                                        Lock the screen now.\n"
              "\n"
              "Two ways to time it (shutdown/restart/reboot/sleep):\n"
              "  --in D   a DELAY from now      -- 10s, 90s, 5m, 1.5m, 2h, 1d, 1h30m; a\n"
              "                                    bare number is MINUTES (10 = 10m, 0.1 = 6s).\n"
              "  --at T   a CLOCK TIME of day   -- HH:MM or HH:MM:SS (e.g. 23:30, 06:05:42);\n"
              "                                    today, or tomorrow if that time already passed.\n"
              "  --status  show if/when one is scheduled.    --cancel  call it off.\n"
              "\n"
              "Tip: `--in` counts forward from now; `--at` is a wall-clock time. So `--in 10`\n"
              "is 10 minutes from now, while `--at 22:00` is 10 PM. `--at` will NOT take a\n"
              "bare number -- use `--in` for a delay.\n"
              "\n"
              "See `azzio power <verb> --help` for per-verb examples. The verbs are also\n"
              "available directly: `azzio shutdown --in 30s`, `azzio reboot`, `azzio lock`.")
        return 0
    verb = args[0]
    rest = args[1:]
    if verb == "shutdown":
        return _power_verb("shutdown", rest)
    # `reboot` is an alias for `restart` -- both map to the same action (systemctl reboot,
    # azzio-restart timer unit), so --status/--cancel of either see the same timer.
    if verb in ("restart", "reboot"):
        return _power_verb("restart", rest)
    if verb == "sleep":
        return _power_verb("sleep", rest)
    if verb == "lock":
        return cmd_lock(rest)
    _err(f"azzio power: unknown command: {verb} "
         "(use shutdown|restart|reboot|sleep|lock).")
    return 2
