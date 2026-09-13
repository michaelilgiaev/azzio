"""configuration_schema.py - the typed hypervisor.cfg schema, coercion, and validation.

Part 2 turned hypervisor.cfg from "bools + strings" into a real typed config:
each key coerces to a Python type and is validated. This module is the SINGLE
SOURCE OF TRUTH for that. Two callers share it:

  * configuration.HypervisorCfg.from_dir -- parses the file into typed values.
  * configuration_watcher -- on every live save, re-runs coerce_all(); an empty error
    list means "valid -> apply", a non-empty one means "invalid -> revert".

Everything here is PURE (str in, typed value or error string out): no I/O, no
process launch. That is what makes the live-reload accept/revert decision
unit-testable without a running VM.

Type model (per key):
  bool                : share_host_gpu, ssh, fullscreen,
                        ask_before_quitting_hypervisor
  int (positive)      : ram, cpus, ssh_guest_to_host_port_forward
  disk-size string    : disk_size            (e.g. 200G, 1024M, 2T)
  audio enum          : audio                (on | off)
  network             : network              (user | none | <interface name>)
  false-or-path       : shared               (false | <working dir> | /path)
  list-of-paths       : usb                  ("" | /dev/... [more /dev/...])
"""

from __future__ import annotations

import re

# --- primitive coercers: each returns (ok, value, error) --------------------
# value is meaningful only when ok is True.


def _as_str(raw):
    """Every value from a parsed cfg file is a str; but a caller could hand
    coerce_all an already-typed dict. Reject non-str defensively so a coercer
    never raises AttributeError on `.strip()`."""
    return raw if isinstance(raw, str) else None


def _coerce_bool(raw):
    s = _as_str(raw)
    if s is None:
        return False, None, "must be true or false"
    low = s.strip().lower()
    if low == "true":
        return True, True, ""
    if low == "false":
        return True, False, ""
    return False, None, "must be true or false"


def _coerce_int(raw):
    s = _as_str(raw)
    if s is not None:
        s = s.strip()
    # ASCII-only: str.isdigit() is also True for superscripts/other-script digits
    # (e.g. "²", Arabic-Indic) that int() then fails to parse -- guard with
    # isascii() so those are rejected cleanly instead of raising ValueError.
    if not (s and s.isascii() and s.isdigit()):   # rejects None, "", "-4", "3.5", "²"
        return False, None, "must be a positive whole number"
    n = int(s)
    if n < 1:                                       # rejects "0"
        return False, None, "must be >= 1"
    return True, n, ""


_MAX_PORT = 65535


def _coerce_port(raw):
    """A TCP port: a positive int in 1..65535. Bounding it here stops a huge
    value from later blowing up socket.bind (OverflowError) on the run path."""
    ok, val, err = _coerce_int(raw)
    if not ok:
        return False, None, err
    if val > _MAX_PORT:
        return False, None, f"must be a port in 1..{_MAX_PORT}"
    return True, val, ""


# ASCII digits only (\d is unicode-aware and would wrongly accept e.g. "٢٠٠G").
_DISK_SIZE_RE = re.compile(r"^[0-9]+[MGT]$")


def _coerce_disk_size(raw):
    s = _as_str(raw)
    if s is not None and _DISK_SIZE_RE.match(s.strip()):
        return True, s.strip(), ""
    return False, None, "must be a size like 200G, 1024M or 2T"


def _coerce_audio(raw):
    s = _as_str(raw)
    if s is None:
        return False, None, "must be on or off"
    low = s.strip().lower()
    if low in ("on", "off"):
        return True, low, ""
    return False, None, "must be on or off"


# A plausible Linux network-interface name: letters, digits, and . _ - @ (no
# whitespace, no slash). Covers eno1, enp5s0, wlan0, br0, vlan.10, bond0.
_IFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")


def _coerce_network(raw):
    s = _as_str(raw)
    if s is None:
        return False, None, "must be user, none, or a host interface name"
    s = s.strip()
    if s in ("user", "none"):
        return True, s, ""
    if _IFACE_RE.match(s):
        return True, s, ""
    return False, None, "must be user, none, or a host interface name (see 'ip -br addr')"


def _coerce_shared(raw):
    """false -> off; true/'' -> enabled at the working dir; else a host path."""
    s = _as_str(raw)
    if s is None:
        return False, None, "must be false, empty (working dir), or an absolute path"
    s = s.strip()
    low = s.lower()
    if low == "false":
        return True, False, ""
    if low == "true" or s == "":
        return True, True, ""          # True == "the working dir" sentinel
    if s.startswith("/"):
        return True, s, ""
    return False, None, "must be false, empty (working dir), or an absolute path"


def _coerce_usb(raw):
    """'' -> []; otherwise whitespace/comma-separated ABSOLUTE device paths.

    Legacy tolerance: pre-redesign cfgs wrote a boolean (`usb = false`/`true`);
    both map to [] (no device passthrough) so an old hypervisor.cfg still loads.
    """
    s = _as_str(raw)
    if s is None:
        return False, None, "must be empty or absolute device path(s)"
    s = s.strip()
    if s == "" or s.lower() in ("false", "true"):
        return True, [], ""
    tokens = [t for t in re.split(r"[,\s]+", s) if t]
    for t in tokens:
        if not t.startswith("/"):
            return False, None, (
                f"USB entry '{t}' must be an absolute device path "
                "(e.g. /dev/bus/usb/003/004; see 'lsusb' and "
                "'lsblk -o NAME,TRAN,MOUNTPOINT')"
            )
    return True, tokens, ""


# --- the schema: key -> coercer ---------------------------------------------
# Order here is also the ORDER KEYS ARE WRITTEN in the generated file.
SCHEMA = {
    "share_host_gpu":                 _coerce_bool,
    "network":                        _coerce_network,
    "shared":                         _coerce_shared,
    "ssh":                            _coerce_bool,
    "ssh_guest_to_host_port_forward": _coerce_port,
    "usb":                            _coerce_usb,
    "fullscreen":                     _coerce_bool,
    "ask_before_quitting_hypervisor": _coerce_bool,
    "ram":                            _coerce_int,
    "cpus":                           _coerce_int,
    "disk_size":                      _coerce_disk_size,
    "audio":                          _coerce_audio,
}

KEYS = tuple(SCHEMA)


def coerce_one(key: str, raw: str):
    """Coerce a single key's raw string. (ok, value, error). Unknown key -> ok
    with value None (caller ignores it)."""
    fn = SCHEMA.get(key)
    if fn is None:
        return True, None, ""
    return fn(raw)


def coerce_all(raw: dict) -> tuple[dict, list[str]]:
    """Coerce a whole KEY=VALUE(str) map to typed values.

    Returns (values, errors). Unknown keys are ignored (never errors). Each bad
    value contributes one 'key: reason' string to errors; a non-empty errors
    list is the signal to REVERT a live edit. Keys absent from raw are simply
    absent from values (the caller layers these over the defaults).
    """
    values: dict = {}
    errors: list[str] = []
    for key, rawval in raw.items():
        fn = SCHEMA.get(key)
        if fn is None:
            continue
        ok, val, err = fn(rawval)
        if ok:
            values[key] = val
        else:
            errors.append(f"{key}: {err} (got '{rawval}')")
    return values, errors
