#!/usr/bin/env python3
"""azzio guest command line interface -- `azzio machine` (recognise PC vs Laptop, allow a hard switch).

Azzio treats the machine as one of two TYPES, and it changes what the FN media keys do:

  * Laptop -- has a backlight, so FN controls BOTH volume AND screen brightness. On the
    user's laptop FN+F2/F3 dim/brighten the screen (the OEM's brightness keys), while the
    dedicated volume keys change the volume.
  * PC (desktop) -- has NO integrated backlight to control, so BRIGHTNESS IS NOT AN OPTION;
    only the volume keys do anything. On the user's PC keyboard FN+F2/F3 are the volume
    keys. Asking a desktop to change "screen brightness" is meaningless (the monitor has its
    own buttons), so `azzio brightness` is a no-op there and the Brightness UI never shows.

DETECTION. The type is normally AUTODETECTED and cached nowhere -- it is re-derived each
call from two cheap, root-free signals, most-specific first:

  1. A real backlight under /sys/class/backlight (a non-empty directory) => Laptop. This is
     the ground truth for "can this box control its own screen brightness": if the kernel
     exposes a backlight, brightness control is real, so it is a laptop-class machine.
  2. Otherwise the DMI chassis type (/sys/class/dmi/id/chassis_type, the SMBIOS value):
     laptop/notebook/portable/handheld/tablet/convertible codes => Laptop; everything else
     (desktop/tower/server/mini-pc/...) => PC. `hostnamectl chassis` reads the same value; we
     read the sysfs file directly so there is no dependency and it works headless.

HARD SWITCH. `azzio machine --pc` / `--laptop` FORCES the type, writing a one-line override
to ~/.config/azzio/machine-type; `--auto` deletes the override and returns to autodetection.
When an override is present it WINS over detection (that is the whole point of a hard switch).
The user asked for this switch explicitly even though, on their own hardware, autodetection is
already correct -- so it is here, it persists, and `azzio brightness`/the Brightness UI honour
it (force a desktop to "Laptop" and the brightness controls light up; force a laptop to "PC"
and they go away), which is exactly what a manual override should do.

Runs WITHOUT sudo -- it only reads sysfs and writes the user's own config pointer (like
`azzio theme`/`azzio wallpaper`). Standard library only; bundled into /usr/local/bin/azzio
(see common.py).
"""

from __future__ import annotations

# BUNDLE_START

# The two machine types, as the user-facing words the command line interface + the UI print. Kept as
# constants so the C terminal user interface's machine probe and the tests pin the exact spelling.
MACHINE_PC = "PC"
MACHINE_LAPTOP = "Laptop"

# The kernel backlight directory: a NON-EMPTY listing here means the machine can control its
# own screen brightness, i.e. it is a laptop-class device. This is the strongest signal (it is
# literally "is there a brightness to change"), so it is checked before the DMI chassis type.
_BACKLIGHT_DIR = "/sys/class/backlight"

# The SMBIOS/DMI chassis-type file (an integer code). These codes mean "portable" in the
# SMBIOS spec -- 8 Portable, 9 Laptop, 10 Notebook, 11 Hand Held, 14 Sub Notebook, 30 Tablet,
# 31 Convertible, 32 Detachable. Anything else (3 Desktop, 4 Low Profile Desktop, 6 Mini Tower,
# 7 Tower, 23 Rack Mount Server, ...) is treated as a PC. Same value `hostnamectl chassis`
# reports; we read the file directly for a zero-dependency, headless-safe probe.
_CHASSIS_FILE = "/sys/class/dmi/id/chassis_type"
_LAPTOP_CHASSIS_CODES = {"8", "9", "10", "11", "14", "30", "31", "32"}


def _machine_state_file() -> str:
    """The per-user hard-override pointer: ~/.config/azzio/machine-type. A one-line file
    holding "PC" or "Laptop". Absent => autodetect. Resolved off ~ at runtime so it always
    targets the CURRENT user (no sudo), matching the theme/wallpaper pointer files."""
    return os.path.join(os.path.expanduser("~"), ".config/azzio/machine-type")


def _has_backlight() -> bool:
    """True when the kernel exposes at least one backlight device (=> the machine can control
    its own screen brightness => laptop-class). A missing/empty dir is a clean False."""
    try:
        return any(os.scandir(_BACKLIGHT_DIR))
    except OSError:
        return False


def _chassis_is_laptop() -> bool | None:
    """Decide the type from the DMI chassis code: True (laptop-class), False (PC), or None
    when the file is unreadable/empty (no DMI => caller falls back)."""
    try:
        with open(_CHASSIS_FILE, encoding="utf-8") as fh:
            code = fh.read().strip()
    except OSError:
        return None
    if not code:
        return None
    return code in _LAPTOP_CHASSIS_CODES


def _detect_machine_type() -> str:
    """Autodetect the machine type (ignoring any hard override): a real backlight wins
    (Laptop), else the DMI chassis code decides, else default to PC (the safe assumption --
    a machine with no backlight and no DMI has no brightness to control)."""
    if _has_backlight():
        return MACHINE_LAPTOP
    ch = _chassis_is_laptop()
    if ch is True:
        return MACHINE_LAPTOP
    if ch is False:
        return MACHINE_PC
    return MACHINE_PC


def _read_override() -> str | None:
    """Return the hard-override machine type ("PC"/"Laptop") from the pointer file, or None
    when there is no override (or it is unreadable/garbage). Case-insensitive on the stored
    word so a hand-edited file still resolves."""
    try:
        with open(_machine_state_file(), encoding="utf-8") as fh:
            val = fh.readline().strip().lower()
    except OSError:
        return None
    if val == MACHINE_PC.lower():
        return MACHINE_PC
    if val == MACHINE_LAPTOP.lower():
        return MACHINE_LAPTOP
    return None


def machine_type() -> str:
    """The EFFECTIVE machine type: the hard override if one is set, else autodetection. This
    is the single value the rest of the command line interface (brightness gating, the UI) reads."""
    return _read_override() or _detect_machine_type()


def is_laptop() -> bool:
    """True when the effective machine type is Laptop -- the gate for the brightness controls
    and the Brightness UI (brightness is a laptop-only option per the spec)."""
    return machine_type() == MACHINE_LAPTOP


def _write_override(value: str) -> None:
    """Persist a hard override ("PC"/"Laptop") to the pointer file, creating ~/.config/azzio."""
    path = _machine_state_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(value + "\n")


def _clear_override() -> None:
    """Remove the hard override so the type reverts to autodetection. No-op if none is set."""
    try:
        os.remove(_machine_state_file())
    except OSError:
        pass


def machine_status() -> int:
    """Print the effective machine type, whether it is a hard override or autodetected, and
    (for transparency) what autodetection alone would say. The bare `azzio machine` behaviour."""
    override = _read_override()
    effective = override or _detect_machine_type()
    print(f"Machine type: {effective}")
    if override:
        print(f"  source:     hard override (~/.config/azzio/machine-type)")
        print(f"  detected:   {_detect_machine_type()}")
    else:
        print(f"  source:     autodetected")
    print(f"  brightness: {'available (laptop)' if effective == MACHINE_LAPTOP else 'not an option (PC)'}")
    return 0


def machine_usage() -> None:
    print(
        "Usage: azzio machine [--pc | --laptop | --auto]\n"
        "\n"
        "Show or hard-set the machine type (PC or Laptop). The type decides whether\n"
        "screen-brightness control is offered: laptops have a backlight (brightness on),\n"
        "PCs do not (brightness off -- the FN keys only change the volume).\n"
        "\n"
        "  --pc         Force PC (no brightness control).\n"
        "  --laptop     Force Laptop (brightness control on).\n"
        "  --auto       Forget the override and autodetect (backlight, then DMI chassis).\n"
        "  --help       Show this help.\n"
        "  (no option)  Print the current machine type.\n"
    )


def cmd_machine(args: list[str]) -> int:
    """Dispatch `azzio machine ...`. No option -> print status; --pc/--laptop force the type;
    --auto clears the override; --help prints usage."""
    if not args:
        return machine_status()
    opt = args[0]
    if opt in ("--pc", "-p"):
        _write_override(MACHINE_PC)
        print(f"Machine type forced to {MACHINE_PC}. Brightness control is now off.")
        return 0
    if opt in ("--laptop", "-l"):
        _write_override(MACHINE_LAPTOP)
        print(f"Machine type forced to {MACHINE_LAPTOP}. Brightness control is now on.")
        return 0
    if opt in ("--auto", "-a"):
        _clear_override()
        print(f"Machine type override cleared. Autodetected: {_detect_machine_type()}.")
        return 0
    if opt in ("--help", "-h", "help"):
        machine_usage()
        return 0
    _err(f"azzio machine: unknown option: {opt}")
    machine_usage()
    return 2
