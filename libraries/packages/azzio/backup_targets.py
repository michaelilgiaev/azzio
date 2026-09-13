#!/usr/bin/env python3
"""azzio guest command line interface -- `azzio backup --configure` (opt in to cloud / USB
backup targets).

By DEFAULT the `backup` command (packages/backup/) writes its two encrypted archives into
HOME and stops -- USB and Google Drive upload are DISABLED. `azzio backup --configure` (short
`-c`) is how the user OPTS IN: it registers a mounted USB device and/or a Google Drive rclone
remote and writes a small, user-owned config that `backup` then reads and, when a target is
enabled, ALSO copies the freshly built archives there. Turn a target off again (or never run
this) and `backup` behaves exactly as before -- local archives only.

NOTE: the actual backup RUN is the SEPARATE top-level `backup` command (/usr/local/bin/backup,
from packages/backup/). `azzio backup` is ONLY the opt-in configurator: a bare `azzio backup`
points the user at the real `backup` command rather than running one.

WHERE THE CONFIG LIVES. ~/.config/azzio-backup/backup.cfg (XDG, 0600). This is the SAME
file packages/backup/config.py reads; that module is the source of truth for the path and
the four keys (usb_enabled / usb_root / gdrive_enabled / gdrive_remote). `azzio` installs
root-owned under /usr/local/bin and cannot import the backup package, so the path + keys are
repeated here and kept in lock-step by this docstring (the same arrangement backup.VAULT_REL
uses to track passwords/config.py). No secrets are written here: rclone keeps the Google
Drive OAuth token in its OWN config (~/.config/rclone/rclone.conf); we only record the remote
NAME to copy to, and for USB just the mount path.

GOOGLE DRIVE uses rclone (the one extra system binary, in packages.x86_64). Setup shells out
to the normal, interactive ``rclone config`` so the user does the Google OAuth login in their
browser exactly as rclone documents it; we then verify the chosen remote answers before
enabling it. USB registration lists the currently mounted removable roots under
/run/media/<user> and lets the user pick one (or type a path); enabling requires it to be
present and writable right now, so a typo can't enable a dead target.

Standard library only; bundled into /usr/local/bin/azzio (see common.py). Runs WITHOUT
sudo -- it only reads mounts and writes the user's own config (like `azzio machine`).
"""

from __future__ import annotations

# BUNDLE_START

import json as _bk_json  # noqa: E402  (json is already imported in common.py; alias keeps this self-evident)

# The opt-in config file + its keys -- MUST match packages/backup/config.py (CONFIG_PATH
# and _DEFAULTS). Repeated here because the bundled `azzio` script cannot import the
# root-owned backup package; kept in sync by the module docstring.
_BACKUP_CFG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "azzio-backup", "backup.cfg")
_BACKUP_CFG_DEFAULTS = {
    "usb_enabled": False,
    "usb_root": "",
    "gdrive_enabled": False,
    "gdrive_remote": "",
}


def _backup_cfg_load() -> dict:
    """Load the backup target config, filling defaults for any missing/corrupt key (mirrors
    packages/backup/config.load so `azzio` and `backup` always agree on the shape)."""
    try:
        with open(_BACKUP_CFG_PATH) as handle:
            data = _bk_json.load(handle)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    merged = dict(_BACKUP_CFG_DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in _BACKUP_CFG_DEFAULTS})
    return merged


def _backup_cfg_save(cfg: dict) -> str:
    """Write only the known keys to the config (0600), creating the dir. Returns the path."""
    os.makedirs(os.path.dirname(_BACKUP_CFG_PATH), exist_ok=True)
    payload = {k: cfg.get(k, _BACKUP_CFG_DEFAULTS[k]) for k in _BACKUP_CFG_DEFAULTS}
    with open(_BACKUP_CFG_PATH, "w") as handle:
        _bk_json.dump(payload, handle, indent=2)
    os.chmod(_BACKUP_CFG_PATH, 0o600)
    return _BACKUP_CFG_PATH


def _backup_usb_candidates() -> list[str]:
    """Currently mounted removable roots for this user: the subdirectories of
    /run/media/<user> (where udisks2 mounts USB drives). Each is a writable candidate the
    user can register. Empty list if nothing is plugged in / mounted."""
    root = os.path.join("/run/media", _current_user())
    try:
        return sorted(os.path.join(root, name) for name in os.listdir(root)
                      if os.path.isdir(os.path.join(root, name)))
    except OSError:
        return []


def _ask(prompt: str, default: str = "") -> str:
    """input() with a default shown; returns the default on empty input or EOF."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def _ask_yes_no(prompt: str, default: bool) -> bool:
    """A y/n prompt; empty / EOF returns ``default``."""
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"{prompt} [{hint}]: ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def _setup_usb(cfg: dict) -> None:
    """Interactively register (or clear) a USB target in ``cfg`` (mutated in place).

    Lists the mounted removable roots; the user picks one by number or types a path.
    Enabling REQUIRES the chosen path to be present + writable now, so a dead target can
    never be enabled. Declining disables the USB target but keeps any remembered path."""
    if not _ask_yes_no("Enable USB backup (copy archives to a USB drive)?",
                       default=bool(cfg.get("usb_enabled"))):
        cfg["usb_enabled"] = False
        print("  USB backup disabled.")
        return

    candidates = _backup_usb_candidates()
    if candidates:
        print("  Mounted removable drives:")
        for i, path in enumerate(candidates, 1):
            print(f"    {i}) {path}")
        choice = _ask("  Pick a number, or type a mount path",
                      default=cfg.get("usb_root") or candidates[0])
    else:
        print("  No mounted removable drives found (plug one in and let it mount, or "
              "type its path).")
        choice = _ask("  USB mount path", default=cfg.get("usb_root"))

    if choice.isdigit() and 1 <= int(choice) <= len(candidates):
        usb_root = candidates[int(choice) - 1]
    else:
        usb_root = os.path.expanduser(choice)

    if not (usb_root and os.path.isdir(usb_root) and os.access(usb_root, os.W_OK)):
        _err(f"  Not a writable mounted directory: {usb_root or '(none)'} -- USB left disabled.")
        cfg["usb_enabled"] = False
        cfg["usb_root"] = usb_root or cfg.get("usb_root", "")
        return
    cfg["usb_enabled"] = True
    cfg["usb_root"] = usb_root
    print(f"  USB backup enabled -> {usb_root}")


def _rclone_remotes() -> list[str]:
    """The configured rclone remote names (each WITH its trailing ':'), or [] if rclone is
    absent or has no remotes yet."""
    if not _have("rclone"):
        return []
    result = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _rclone_remote_ok(remote: str) -> bool:
    """True if ``rclone about <remote>`` succeeds -- i.e. the remote is configured and
    reachable (the OAuth token works). Used to confirm a Drive login before enabling it."""
    result = subprocess.run(["rclone", "about", remote], capture_output=True, text=True)
    return result.returncode == 0


def _setup_gdrive(cfg: dict) -> None:
    """Interactively set up (or clear) the Google Drive target in ``cfg`` (mutated).

    If the user opts in we ensure rclone exists, offer to run the interactive
    ``rclone config`` (the standard Google OAuth browser login), let them choose which
    remote to upload to, then VERIFY it answers before enabling. A remote that does not
    verify leaves Drive disabled (so a half-finished login can't silently enable)."""
    if not _ask_yes_no("Enable Google Drive backup (upload archives via rclone)?",
                       default=bool(cfg.get("gdrive_enabled"))):
        cfg["gdrive_enabled"] = False
        print("  Google Drive backup disabled.")
        return

    if not _have("rclone"):
        _err("  'rclone' is not installed. Install it with: sudo pacman -S rclone")
        cfg["gdrive_enabled"] = False
        return

    if _ask_yes_no("  Run 'rclone config' now to log in to Google Drive?",
                   default=not _rclone_remotes()):
        # Hand the terminal to rclone's own interactive configurator (browser OAuth).
        subprocess.run(["rclone", "config"])

    remotes = _rclone_remotes()
    if not remotes:
        _err("  No rclone remotes configured yet -- Google Drive left disabled. "
             "Re-run after 'rclone config'.")
        cfg["gdrive_enabled"] = False
        return
    print("  Configured rclone remotes:")
    for i, name in enumerate(remotes, 1):
        print(f"    {i}) {name}")
    default_remote = cfg.get("gdrive_remote") or remotes[0]
    choice = _ask("  Pick a number, or type a remote name", default=default_remote)
    if choice.isdigit() and 1 <= int(choice) <= len(remotes):
        remote = remotes[int(choice) - 1]
    else:
        # An rclone remote is "name:" or "name:path"; only a BARE name (no ':' at all)
        # needs a ':' appended. Appending unconditionally on "not endswith(':')" would
        # mangle a "name:path/" form into "name:path/:" (the double-colon bug).
        remote = choice if ":" in choice else choice + ":"

    print(f"  Checking {remote} ...")
    if not _rclone_remote_ok(remote):
        _err(f"  Could not reach {remote} (is it a Drive remote you have logged in to?) "
             "-- Google Drive left disabled.")
        cfg["gdrive_enabled"] = False
        cfg["gdrive_remote"] = remote
        return
    cfg["gdrive_enabled"] = True
    cfg["gdrive_remote"] = remote
    print(f"  Google Drive backup enabled -> {remote}")


def _backup_setup_status(cfg: dict) -> None:
    """Print the resulting opt-in state (both targets), so the user sees exactly what
    `backup` will do now."""
    usb = f"ON -> {cfg['usb_root']}" if cfg.get("usb_enabled") else "off"
    gdrive = f"ON -> {cfg['gdrive_remote']}" if cfg.get("gdrive_enabled") else "off"
    print("\nBackup targets:")
    print(f"  USB          {usb}")
    print(f"  Google Drive {gdrive}")
    if not (cfg.get("usb_enabled") or cfg.get("gdrive_enabled")):
        print("  (both off -- `backup` writes local archives only, the default.)")


def _enable_usb_path(cfg: dict, path: str) -> int:
    """Non-interactive USB enable: register + enable ``path`` as the USB target after the
    SAME validation the interactive flow does (it must be a present, writable directory RIGHT
    NOW, so a typo/dead mount can never enable a target). Mutates ``cfg`` and saves it.

    Returns 0 on success, 2 on a bad path (nothing enabled). This is the surface the bare-`azzio`
    TUI 'Enable USB' row calls -- the interactive picker cannot run inside the alt-screen capture
    overlay, so the TUI prompts for the PATH and hands it here. Mirrors _setup_usb's check."""
    usb_root = os.path.expanduser(path.strip()) if path else ""
    if not (usb_root and os.path.isdir(usb_root) and os.access(usb_root, os.W_OK)):
        _err(f"Not a writable mounted directory: {usb_root or '(none)'} -- USB left disabled.")
        return 2
    cfg["usb_enabled"] = True
    cfg["usb_root"] = usb_root
    _backup_cfg_save(cfg)
    print(f"USB backup enabled -> {usb_root}")
    return 0


def _enable_gdrive_remote(cfg: dict, remote: str) -> int:
    """Non-interactive Google Drive enable: register + enable ``remote`` after the SAME
    verification the interactive flow does (rclone must exist AND ``rclone about <remote>``
    must succeed, so a half-configured / unreachable remote can never enable). Mutates ``cfg``
    and saves it.

    Returns 0 on success, 2 on failure (nothing enabled). A bare NAME (no ':') gets the ':'
    appended, exactly like the interactive picker. This is the surface the TUI 'Enable Google
    Drive' row calls (it prompts for the remote name; ``rclone config`` OAuth is done once in a
    real terminal beforehand). Mirrors _setup_gdrive's verify."""
    remote = (remote or "").strip()
    if not remote:
        _err("No remote name given -- Google Drive left disabled.")
        return 2
    if not _have("rclone"):
        _err("'rclone' is not installed. Install it with: sudo pacman -S rclone")
        return 2
    # An rclone remote is "name:" or "name:path"; only a BARE name (no ':') needs the ':'
    # appended (the same rule the interactive picker uses -- avoids the double-colon bug).
    if ":" not in remote:
        remote = remote + ":"
    if not _rclone_remote_ok(remote):
        _err(f"Could not reach {remote} (is it a Drive remote you have logged in to via "
             "'rclone config'?) -- Google Drive left disabled.")
        return 2
    cfg["gdrive_enabled"] = True
    cfg["gdrive_remote"] = remote
    _backup_cfg_save(cfg)
    print(f"Google Drive backup enabled -> {remote}")
    return 0


def cmd_backup_setup(argv: list[str]) -> int:
    """The opt-in flow behind `azzio backup --configure` / `-c` -- enable / manage
    `backup`'s cloud + USB targets. ``argv`` is what FOLLOWS --configure/-c, so:

      (empty)     interactively configure USB and Google Drive
      --status    just print the current opt-in state (no prompts)
      --disable   turn BOTH targets off (back to local-only)

    (Dispatched by cmd_backup(), which strips the --configure/-c flag first. Kept as its own
    function so the config path/keys stay lock-step with packages/backup/config.py and the
    exec-based unit test can drive it directly.)"""
    if argv and argv[0] in ("-h", "--help", "help"):
        print("Usage: azzio backup --configure|-c [--status|--disable]\n"
              "       azzio backup --configure --enable-usb <PATH>\n"
              "       azzio backup --configure --enable-gdrive <REMOTE>\n\n"
              "Opt in to optional backup targets for the `backup` command. By default\n"
              "`backup` writes local encrypted archives only; enabling a target here makes\n"
              "it ALSO copy those archives to a USB drive and/or Google Drive (rclone).\n\n"
              "  (no option)         interactively enable/disable USB and Google Drive\n"
              "  --status            print the current target state and exit\n"
              "  --disable           turn both targets off (local-only again)\n"
              "  --enable-usb PATH   enable the USB target to a writable mounted PATH\n"
              "  --enable-gdrive REMOTE  enable Google Drive to a verified rclone REMOTE\n\n"
              "The --enable-* forms are non-interactive (the bare-`azzio` TUI uses them);\n"
              "each validates the target before enabling, so a dead path/remote can't enable.\n"
              "The actual backup RUN is the separate `backup` command (not `azzio backup`).\n\n"
              f"Config: {_BACKUP_CFG_PATH} (no secrets; rclone stores its own token).")
        return 0

    cfg = _backup_cfg_load()

    if argv and argv[0] == "--status":
        _backup_setup_status(cfg)
        return 0

    if argv and argv[0] == "--disable":
        cfg["usb_enabled"] = False
        cfg["gdrive_enabled"] = False
        path = _backup_cfg_save(cfg)
        print(f"Both backup targets disabled -- `backup` writes local archives only.\nSaved {path}")
        return 0

    # Non-interactive enable surfaces (what the bare-`azzio` TUI drives: the interactive
    # picker cannot run inside the UI's capture overlay, so the TUI prompts for the value and
    # calls these). Each VALIDATES the path/remote exactly like the interactive flow before
    # enabling, so a typo/dead target can never be enabled.
    if argv and argv[0] == "--enable-usb":
        if len(argv) < 2 or not argv[1]:
            _err("azzio backup --configure --enable-usb <PATH>: a mount PATH is required.")
            return 2
        return _enable_usb_path(cfg, argv[1])

    if argv and argv[0] == "--enable-gdrive":
        if len(argv) < 2 or not argv[1]:
            _err("azzio backup --configure --enable-gdrive <REMOTE>: a remote NAME is required.")
            return 2
        return _enable_gdrive_remote(cfg, argv[1])

    if argv:
        _err(f"azzio backup --configure: unknown option: {argv[0]}")
        return 2

    print("Configure optional backup targets for the `backup` command.")
    print("Both are OFF by default; enable what you want. Nothing here changes the two\n"
          "local archives `backup` always writes.\n")
    _setup_gdrive(cfg)
    print()
    _setup_usb(cfg)
    path = _backup_cfg_save(cfg)
    _backup_setup_status(cfg)
    print(f"\nSaved {path}")
    return 0


def _backup_usage() -> None:
    """The short `azzio backup` usage: it is the opt-in CONFIGURATOR, not the backup run.
    Printed for a bare `azzio backup` (which does NOT run a backup) and for `--help`."""
    print("Usage: azzio backup --configure|-c [--status|--disable]\n\n"
          "`azzio backup` opts in to / manages the OPTIONAL copy targets (USB / Google\n"
          "Drive) for the `backup` command -- it does NOT run a backup itself.\n\n"
          "  --configure, -c            interactively enable/disable USB and Google Drive\n"
          "  --configure --status       print the current target state and exit\n"
          "  --configure --disable      turn both targets off (local-only again)\n\n"
          "To actually CREATE a backup, run the `backup` command (/usr/local/bin/backup).")


def cmd_backup(argv: list[str]) -> int:
    """`azzio backup` -- dispatch the opt-in target configurator.

    This is ONLY the configurator surface; the real backup RUN is the separate top-level
    `backup` command (/usr/local/bin/backup). So:

      azzio backup --configure           (or -c)  -> interactive opt-in (cmd_backup_setup)
      azzio backup --configure --status  (or -c ...) -> print target state
      azzio backup --configure --disable (or -c ...) -> disable both targets
      azzio backup -h|--help                       -> the usage above
      azzio backup            (bare)               -> usage + pointer to `backup`, exit 2
      azzio backup <anything else>                 -> unknown-flag error, exit 2
    """
    if not argv:
        # A bare `azzio backup` does NOT run a backup -- point the user at the real command.
        _backup_usage()
        return 2
    if argv[0] in ("-h", "--help", "help"):
        _backup_usage()
        return 0
    if argv[0] in ("--configure", "-c"):
        # Everything AFTER the flag is the sub-form (--status / --disable / nothing).
        return cmd_backup_setup(argv[1:])
    _err(f"azzio backup: unknown option: {argv[0]} "
         f"(did you mean `azzio backup --configure`?)")
    return 2
