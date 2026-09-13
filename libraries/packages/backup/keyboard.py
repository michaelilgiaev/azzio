"""Live keyboard layout + Caps Lock readout, shown at the master-password prompt.

Same method as ~/.local/share/backup_system/backup/keyboard.py: read the XKB
state via libX11 (one call gives both the active layout group and the locked
modifiers). Degrades to "unknown"/None when X cannot be reached (Wayland, a bare
tty, no libX11) so it never breaks the prompt.
"""

import ctypes
import ctypes.util
import subprocess


class _XkbStateRec(ctypes.Structure):
    _fields_ = [
        ("group", ctypes.c_uint8),
        ("locked_group", ctypes.c_uint8),
        ("base_group", ctypes.c_uint16),
        ("latched_group", ctypes.c_uint16),
        ("mods", ctypes.c_uint8),
        ("base_mods", ctypes.c_uint8),
        ("latched_mods", ctypes.c_uint8),
        ("locked_mods", ctypes.c_uint8),
        ("compat_state", ctypes.c_uint8),
        ("grab_mods", ctypes.c_uint8),
        ("compat_grab_mods", ctypes.c_uint8),
        ("lookup_mods", ctypes.c_uint8),
        ("compat_lookup_mods", ctypes.c_uint8),
        ("ptr_buttons", ctypes.c_uint16),
    ]


def _read_xkb_state():
    """Live XKB state (active group + locked modifiers) via libX11, or None."""
    try:
        libx11 = ctypes.CDLL(ctypes.util.find_library("X11"))
        libx11.XOpenDisplay.restype = ctypes.c_void_p
        libx11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        libx11.XkbGetState.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
        libx11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        display = libx11.XOpenDisplay(None)
        if not display:
            return None
        state = _XkbStateRec()
        libx11.XkbGetState(display, 0x0100, ctypes.byref(state))  # XkbUseCoreKbd
        libx11.XCloseDisplay(display)
        return state
    except Exception:
        return None


def get_active_keyboard_layout():
    try:
        output = subprocess.check_output(["setxkbmap", "-query"], text=True)
        layouts = []
        for line in output.splitlines():
            if line.startswith("layout:"):
                layouts = [l.strip() for l in line.split(":")[1].split(",")]
                break
        if not layouts:
            return "unknown"
        if len(layouts) == 1:
            return layouts[0]
        state = _read_xkb_state()
        if state is None:
            return layouts[0]
        idx = state.group
        return layouts[idx] if idx < len(layouts) else layouts[0]
    except Exception:
        return "unknown"


def get_caps_lock_state():
    """'on'/'off' for Caps Lock (X Lock modifier, LockMask 0x02), or None."""
    state = _read_xkb_state()
    if state is None:
        return None
    return "on" if (state.locked_mods & 0x02) else "off"


def keyboard_status_line():
    """One line: layout, and Caps Lock when it can be read (ON is shouted)."""
    layout = get_active_keyboard_layout()
    caps = get_caps_lock_state()
    if caps == "on":
        return "Keyboard: %s   Caps Lock: ON" % layout
    if caps == "off":
        return "Keyboard: %s   Caps Lock: off" % layout
    return "Keyboard: %s" % layout
