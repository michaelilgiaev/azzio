#!/usr/bin/env python3
"""azzio guest command line interface -- `azzio security-notice` (first-run security warning).

The default DESKTOP live session ships with password login DISABLED (locked /etc/shadow)
and NO ssh server running. That is safe as shipped, but the moment a user sets a login
password or starts ssh, the machine can be logged into over the network -- and a live
session that is port-forwarded or sitting in a DMZ is then exposed. So on the FIRST launch
of the base desktop session we show a one-time notice explaining the risk and asking the
user to follow good security practices before exposing the box.

It is invoked from the shared OpenBox autostart. The autostart is inherited by BOTH the
base desktop and the ssh variant (and, before Calamares overwrites it, staged for the
installed system too), so the DECISION about whether to actually show anything lives HERE,
in the command -- matching the codebase's "guard in the tool" style:

  * If ssh is DELIBERATELY configured on this medium (the sshd-hypervisor auto-setup unit
    is enabled -- i.e. this is the azzio-headed-ssh variant) the operator already chose
    ssh + a password at build time, so we stay QUIET.
  * If `main` already has a real (unlocked) login password, the "password not configured"
    warning does not apply, so we stay QUIET.
  * Otherwise we show the notice ONCE (a stamp file under ~/.config/azzio), and never
    again for that user.

Everything below lands in the single bundled /usr/local/bin/azzio namespace, so it calls
the shared helpers (_have) by bare name and uses os/subprocess from the bundle header.
See common.py / bundle.py.
"""

from __future__ import annotations

# BUNDLE_START

# The one-time stamp: once written, the notice never shows again for this user.
_SECURITY_NOTICE_STAMP = "~/.config/azzio/security-notice-shown"

# The enable-link the ssh variant ships; its presence means ssh was deliberately turned on
# at build time, so the "you have not configured security" notice does not apply.
_SSHD_ENABLE_LINK = (
    "/etc/systemd/system/multi-user.target.wants/sshd-hypervisor-setup.service"
)

# The worded notice. Prose per the user's writing rules (no dashes/colons/semicolons in the
# sentences). Kept as a module constant so a test can assert the exact wording ships.
SECURITY_NOTICE_TEXT = (
    "Azzio security notice\n"
    "\n"
    "This live session ships with password login disabled and no ssh server running, so "
    "it cannot be logged into over the network as it is.\n"
    "\n"
    "If you set a login password or start the ssh server, anyone who can reach this "
    "machine over the network may try to log in. That matters most when the machine is "
    "port forwarded, sitting in a DMZ, or on a network you do not trust.\n"
    "\n"
    "Please make sure you are following good security practices before you expose this "
    "machine to the internet. Use a strong password or key based ssh, keep the firewall "
    "on, and only open ports you actually need. Stay safe."
)


def _security_notice_stamp_path() -> str:
    return os.path.expanduser(_SECURITY_NOTICE_STAMP)


def _ssh_variant_configured() -> bool:
    """True if this medium is the ssh variant (the sshd auto-setup enable-link exists).
    On that variant the operator chose ssh + a password at build time, so the notice is
    not shown."""
    return os.path.islink(_SSHD_ENABLE_LINK) or os.path.exists(_SSHD_ENABLE_LINK)


def _main_has_login_password() -> bool:
    """True if the `main` account has a real (unlocked, non-empty) password hash in
    /etc/shadow. A locked field (`!`/`*`) or an empty field means no password login is
    possible, so the warning still applies. Reads shadow via `sudo -n` (passwordless on
    the live medium); if it cannot be read we assume NO password (show the notice) rather
    than silently suppressing a security warning."""
    r = subprocess.run(["sudo", "-n", "getent", "shadow", "main"],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0 or not r.stdout:
        return False
    field = r.stdout.split(":", 2)[1] if r.stdout.count(":") >= 1 else ""
    # A crypt hash starts with '$'. '!'/'*'/'' are locked/empty -> no password login.
    return field.startswith("$")


def _notify_desktop(summary: str, body: str) -> None:
    """Best-effort desktop notification (only if a notifier and a display are present).
    Never raises -- the printed text is the real channel; this is a nicety on top."""
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return
    if not _have("notify-send"):
        return
    subprocess.run(["notify-send", "-u", "critical", "-i", "security-high",
                    summary, body], check=False)


def cmd_security_notice(args: list[str]) -> int:
    """`azzio security-notice [--force] [--reset]`.

    Show the first-run security notice ONCE, self-gating on ssh being deliberately
    configured / a real password being set. --force shows it regardless of the stamp/gates
    (used to preview the exact wording); --reset clears the stamp so it will show again on
    the next plain invocation."""
    force = "--force" in args
    reset = "--reset" in args
    if args and args[0] in ("-h", "--help", "help"):
        print("Usage: azzio security-notice [--force] [--reset]\n"
              "\n"
              "  (no option)  Show the first-run security notice once (self-silences).\n"
              "  --force      Show it now regardless of the one-time stamp or gates.\n"
              "  --reset      Clear the stamp so it shows again next time.")
        return 0

    stamp = _security_notice_stamp_path()
    if reset:
        try:
            os.remove(stamp)
        except OSError:
            pass
        if not force:
            print("Security notice will be shown again on next launch.")
            return 0

    if not force:
        # Already shown for this user?
        if os.path.exists(stamp):
            return 0
        # Deliberately-configured ssh variant, or a real password is set -> stay quiet, but
        # still record that we made the decision so we do not re-check every login.
        if _ssh_variant_configured() or _main_has_login_password():
            _write_stamp(stamp)
            return 0

    print(SECURITY_NOTICE_TEXT)
    _notify_desktop("Azzio security notice",
                    "Password login and ssh are off by default. Follow good security "
                    "practices before exposing this machine to the internet.")
    if not force:
        _write_stamp(stamp)
    return 0


def _write_stamp(stamp: str) -> None:
    """Record that the notice has been handled for this user (best-effort)."""
    try:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w", encoding="utf-8") as f:
            f.write("shown\n")
    except OSError:
        pass
