#!/usr/bin/env python3
"""Optional cloud (Google Drive via rclone) + USB copy targets for `backup`.

After `backup` has built its two local archives it calls copy_archives_to_targets():
for each ENABLED target (see config.py) the freshly built ``*.tar.gz.gpg`` files are
ALSO placed there. Nothing here runs unless the user opted in via ``azzio backup --configure``
-- with the default (all-disabled) config this module is never even reached.

This is a deliberately SMALL, standard-library-only distillation of the cloud/USB
machinery in the repo-root prototype data/backup.py (which is far larger: live progress
UI, space policy, GitHub/QEMU prep, parallel pipelines). We keep only the three pieces
the PROMPT calls out and that matter for correctness:

  * GDRIVE_RCLONE_FLAGS -- the exact resumable-upload tuning from the prototype: chunked
    (``--drive-chunk-size``) so a big file needs few round-trips, and CRUCIALLY
    ``--retries 1`` + ``--low-level-retries`` so a transient hiccup RESUMES the same
    upload session at the failed chunk instead of throwing away progress and re-sending
    (and re-counting) the whole multi-GB archive. rclone is the ONE new system binary
    (named in packages.x86_64); everything else here is stdlib subprocess/shutil.

  * USB mount detection -- before copying to a USB target we confirm the configured root
    is actually a present, writable directory (an unplugged drive is skipped with a
    warning, never an error -- a missing USB must not fail the whole backup).

  * previous-backup rotation -- the existing archives already sitting at a target root are
    moved into ``previous_backups/`` before the new ones are written, so the last good
    generation is retained rather than overwritten in place.

Every rclone/USB failure is REPORTED but non-fatal: the local archives are the primary
deliverable and always remain, so the user can just re-run. copy_archives_to_targets()
returns True only if every enabled target fully succeeded.
"""

import os
import shutil
import subprocess
import sys

# Flat-package bootstrap: make this module's own dir importable before the sibling
# ``import user_interface`` below, so it resolves whether targets.py is reached via an entry
# script (backup.py, which already does this) or imported directly by the test suite. Mirrors
# the bootstrap in archive.py; a no-op when the dir is already on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import user_interface  # noqa: E402  (after the sys.path bootstrap above)

# The two archive basenames rotation recognises at a target root (kept in sync with
# backup.HOME_ARCHIVE_NAME / PASSWORDS_ARCHIVE_NAME). Only these fixed names are rotated,
# so nothing else a user keeps on the USB stick is touched.
_ROTATED_NAMES = ("backup.tar.gz.gpg", "passwords.tar.gz.gpg")
_PREVIOUS_DIRNAME = "previous_backups"

# rclone Google-Drive upload tuning -- copied verbatim (in intent) from data/backup.py's
# GDRIVE_RCLONE_FLAGS. Bigger chunks = fewer round-trips on a fast link; --retries 1 plus
# a generous --low-level-retries means one transient error RESUMES the same upload at the
# failed chunk (no whole-file restart, no double-counting) rather than re-sending
# gigabytes. If the single top-level attempt still fails, the local archive is kept and
# the user just re-runs -- far better than burning 3x the bandwidth on duplicates.
GDRIVE_RCLONE_FLAGS = [
    "--drive-chunk-size", "256M",
    "--transfers", "4",
    "--checkers", "8",
    "--buffer-size", "64M",
    "--retries", "1",
    "--low-level-retries", "20",
]


def _have_rclone():
    """True when the ``rclone`` binary is on PATH (the one new system dependency)."""
    return shutil.which("rclone") is not None


def _normalise_remote(remote):
    """Return an rclone remote usable as a directory PREFIX to join a basename onto.

    An rclone remote is ``name:`` (its root) or ``name:path`` (a subdir). A bare name with
    no ``:`` at all gets one appended (``gdrive`` -> ``gdrive:``). Then we ensure the value
    ends in ``:`` or ``/`` so that ``remote + basename`` addresses a file AT that root/dir
    (``gdrive:`` -> ``gdrive:file``; ``drive:sub`` -> ``drive:sub/file``). Idempotent, so a
    value that already ends correctly (``gdrive:``) is unchanged -- this is the fix for the
    earlier double-colon bug where ``drive:/path/`` wrongly became ``drive:/path/:``."""
    if ":" not in remote:
        remote += ":"
    if not remote.endswith((":", "/")):
        remote += "/"
    return remote


def usb_target_ready(usb_root):
    """True if ``usb_root`` is a present, writable directory we can copy into.

    This is the mount detection: a configured USB root that is not currently mounted
    (drive unplugged) simply does not exist as a writable dir, so we return False and the
    caller SKIPS the USB copy with a warning rather than failing the backup."""
    return bool(usb_root) and os.path.isdir(usb_root) and os.access(usb_root, os.W_OK)


def _rotate_local(dest_root):
    """Move any existing archive files at ``dest_root`` into ``dest_root/previous_backups``
    before new ones are written, so the previous generation is retained (USB path). Best
    effort: a move that fails is reported but does not abort the copy."""
    previous = os.path.join(dest_root, _PREVIOUS_DIRNAME)
    for name in _ROTATED_NAMES:
        src = os.path.join(dest_root, name)
        if os.path.isfile(src):
            os.makedirs(previous, exist_ok=True)
            try:
                shutil.move(src, os.path.join(previous, name))
            except OSError as error:
                user_interface.warn(f"USB: could not rotate {name} into {_PREVIOUS_DIRNAME}/ ({error})")


def copy_to_usb(archives, usb_root):
    """Copy each archive in ``archives`` to the USB root, after rotating the previous
    generation aside. Returns True on full success. A not-mounted / unwritable USB root is
    a skipped WARNING (returns False but never raises), because a missing stick must not
    fail the whole backup."""
    if not usb_target_ready(usb_root):
        user_interface.warn(f"USB target not mounted/writable at {usb_root or '(unset)'}; skipping USB copy.")
        return False
    _rotate_local(usb_root)
    ok = True
    for archive in archives:
        base = os.path.basename(archive)
        dest = os.path.join(usb_root, base)
        try:
            shutil.copy2(archive, dest)
            user_interface.result_line(f"USB: {base}", dest, None)
        except OSError as error:
            ok = False
            user_interface.warn(f"USB: copy of {base} failed ({error}).")
    return ok


def _rclone(args):
    """Run ``rclone`` with ``args`` appended, returning the CompletedProcess (output
    captured). A tiny wrapper so every call is uniform and testable."""
    return subprocess.run(["rclone", *args], capture_output=True, text=True)


def _gdrive_rotate(remote):
    """Move the current archives at the Drive remote root into ``previous_backups/`` before
    uploading the new ones (mirrors data/backup.py's gdrive_prepare). Best effort: rclone
    errors are ignored here -- a rotation that could not run must not block the upload."""
    for name in _ROTATED_NAMES:
        _rclone(["moveto", f"{remote}{name}", f"{remote}{_PREVIOUS_DIRNAME}/{name}"])


def _gdrive_remote_has(remote, basename, expected_size):
    """True if ``basename`` exists at the Drive remote root at ``expected_size`` bytes.
    Used to CONFIRM an upload actually landed -- rclone's exit code alone can't be
    trusted (a transient error can leave nothing behind). Mirrors the prototype's
    size-verify. Any rclone/parse failure -> False (treated as "not confirmed")."""
    result = _rclone(["lsf", "--files-only", "--format", "sp", remote])
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        line = line.strip()
        if ";" not in line:
            continue
        size_str, name = line.split(";", 1)
        if name == basename:
            try:
                return int(size_str) == expected_size
            except ValueError:
                return False
    return False


def copy_to_gdrive(archives, remote):
    """Upload each archive to the Google Drive ``remote`` (e.g. "gdrive:") with the
    resumable flags, after rotating the previous generation aside, and CONFIRM each upload
    by size. Returns True on full success. rclone missing / unreachable / an unconfirmed
    upload is a WARNING (returns False), never a raise -- the local archives remain."""
    if not remote:
        user_interface.warn("Google Drive remote is not set; skipping Drive upload.")
        return False
    if not _have_rclone():
        user_interface.warn("'rclone' not found; skipping Drive upload. Install it with: "
                "sudo pacman -S rclone")
        return False
    remote = _normalise_remote(remote)

    _gdrive_rotate(remote)
    ok = True
    for archive in archives:
        base = os.path.basename(archive)
        size = os.path.getsize(archive) if os.path.exists(archive) else 0
        result = _rclone(["copy", archive, remote, *GDRIVE_RCLONE_FLAGS])
        if result.returncode == 0 and _gdrive_remote_has(remote, base, size):
            user_interface.result_line(f"GDrive: {base}", f"{remote}{base}", "verified")
        else:
            ok = False
            detail = (f"rclone exit {result.returncode}" if result.returncode != 0
                      else "not confirmed on Drive")
            user_interface.warn(f"GDrive: upload of {base} failed ({detail}); kept the local copy.")
    return ok


def copy_archives_to_targets(archives, cfg):
    """Copy the freshly built ``archives`` to every ENABLED target in ``cfg``.

    ``archives`` is the list of local ``*.tar.gz.gpg`` paths `backup` just wrote;
    ``cfg`` is packages.backup.config.load(). Google Drive is done first, then USB (so the
    off-site copy is attempted before the local stick). Returns True if every enabled
    target fully succeeded, False if any target had a problem (each already warned). If NO
    target is enabled this is a no-op returning True -- but `backup` gates on
    config.any_target_enabled() before calling, so that case normally prints nothing."""
    if not archives:
        return True
    all_ok = True
    if cfg.get("gdrive_enabled"):
        print("Uploading to Google Drive ...")
        all_ok = copy_to_gdrive(archives, cfg.get("gdrive_remote", "")) and all_ok
    if cfg.get("usb_enabled"):
        print("Copying to USB ...")
        all_ok = copy_to_usb(archives, cfg.get("usb_root", "")) and all_ok
    return all_ok
