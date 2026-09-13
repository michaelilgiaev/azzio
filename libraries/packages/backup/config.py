#!/usr/bin/env python3
"""Opt-in configuration for `backup`'s optional cloud / USB targets.

By DEFAULT `backup` writes its two encrypted archives into HOME and stops there -- USB
and Google Drive upload are DISABLED. This module is the small, user-owned config that
lets the user OPT IN: once ``azzio backup --configure`` (packages/azzio/backup_targets.py)
has registered a USB mount and/or a Google Drive rclone remote, it writes this config,
and `backup` reads it and ALSO copies the freshly built archives to whatever targets are
enabled. Targets absent / disabled -> `backup` behaves exactly as before (local only).

WHERE. The app itself installs root-owned under /usr/local/lib/azzio-backup, which a
normal user cannot write to, so the config lands somewhere the USER owns:
~/.config/azzio-backup/backup.cfg (XDG-style, 0600). This mirrors
packages/passwords/config.py's CONFIG_PATH exactly; the ``azzio`` setup command writes
the SAME path (it lives in a different, root-owned install dir and cannot import this
module, so it repeats the path/keys -- kept in lock-step by this docstring, the way
backup.VAULT_REL tracks passwords/config.DEFAULT_ENCRYPTED).

No secrets live here. The rclone Google-Drive token is stored by rclone in its OWN config
(~/.config/rclone/rclone.conf); we only record the REMOTE NAME to copy to. USB is just a
mount path. So this file is safe at rest even though it is only 0600 for tidiness.
"""

import json
import os

# The config file (XDG). Same shape/location convention as passwords/config.CONFIG_PATH.
CONFIG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "azzio-backup", "backup.cfg")

# Defaults: EVERYTHING off. A missing/empty config therefore means "local archives only",
# which is the required out-of-the-box behaviour. usb_root defaults to the udisks2 mount
# root for the current user (…/run/media/<user>/<label>) but is only consulted when
# usb_enabled is true. gdrive_remote is the rclone remote name (e.g. "gdrive:") consulted
# only when gdrive_enabled is true.
_DEFAULTS = {
    "usb_enabled": False,
    "usb_root": "",
    "gdrive_enabled": False,
    "gdrive_remote": "",
}


def load():
    """Return the config dict, every key filled from the defaults if missing/corrupt.

    A missing file, an unreadable file, or invalid JSON all degrade to the all-disabled
    defaults -- `backup` must never fail to run just because this optional config is
    absent or damaged; it simply does the local-only backup."""
    try:
        with open(CONFIG_PATH) as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in _DEFAULTS})
    return merged


def save(data):
    """Persist ``data`` (only the known keys) to CONFIG_PATH, 0600, creating the dir.
    Returns the path written. Used by the `azzio backup --configure` command."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    payload = {k: data.get(k, _DEFAULTS[k]) for k in _DEFAULTS}
    with open(CONFIG_PATH, "w") as handle:
        json.dump(payload, handle, indent=2)
    os.chmod(CONFIG_PATH, 0o600)
    return CONFIG_PATH


def exists():
    """True if the user has ever run the opt-in setup (the config file is present)."""
    return os.path.exists(CONFIG_PATH)


def any_target_enabled(cfg=None):
    """True if AT LEAST ONE upload/copy target is turned on. `backup` uses this to decide
    whether to do any transfer at all -- False (the default) means local archives only."""
    cfg = load() if cfg is None else cfg
    return bool(cfg.get("usb_enabled")) or bool(cfg.get("gdrive_enabled"))
