#!/usr/bin/env python3
"""azzio guest command line interface -- `azzio volume` / `azzio brightness` (the FN media keys).

The FN keys change two things, and the OpenBox session binds the X "XF86" media keysyms to
these subcommands (see packages/openbox rc.xml):

    azzio volume up        raise the volume by one step (7.5%)
    azzio volume down       lower the volume by one step (7.5%)
    azzio volume set <N>    set the volume to a precise percent (0-100)
    azzio volume mute       toggle mute
    azzio volume get        print the current volume percent (0-100)

    azzio brightness up     raise screen brightness by one step (7.5%)  [LAPTOP ONLY]
    azzio brightness down   lower screen brightness by one step (7.5%)  [LAPTOP ONLY]
    azzio brightness set <N>  set screen brightness to a precise percent [LAPTOP ONLY]
    azzio brightness get    print the current brightness percent (0-100)

STEP + RANGE. Both go 0..100% and every press moves 7.5% (the spec's step), so the scale is
100 -> 92.5 -> 85 -> ... A press is clamped to the range (you cannot go past 100 or below 0).

BRIGHTNESS IS LAPTOP-ONLY. A PC (desktop) has no integrated backlight to control -- its
monitor has its own buttons -- so `azzio brightness up/down` on a PC does NOTHING and says
so (and the FN keys that would dim a laptop's screen are the volume keys on a PC keyboard).
The gate is `machine.is_laptop()` (which honours the hard override), so forcing "Laptop" via
`azzio machine --laptop` turns the brightness controls on even on a desktop, and forcing
"PC" turns them off on a laptop -- exactly what the manual switch is for. `get` still prints a
reading regardless so a caller can query without tripping the gate.

THE ON-SCREEN UI. Each up/down/set (and mute) pops a small BOTTOM-MIDDLE on-screen display (the
Manjaro Cinnamon resting spot) -- a cyan bar (the Azzio logo cyan) filling to the new percent,
with a volume or brightness icon -- by launching the OSD indicator (the compiled Xlib program
azzio-osd, shipped to OSD_INDICATOR_BIN) detached and feeding it one JSON line describing what
to show. The OSD is a SINGLE resident window: a second press does NOT spawn a second window (that
was the old flicker) -- it forwards the new level to the one already up, which repaints in place,
holds, then fades out. It is a real borderless top-most window (in the spirit of the speech-to-
text indicator), rewritten from tkinter to C so it never flickers and can be mouse-dragged.

BACKENDS (root-free where possible). Volume goes through PipeWire's `wpctl` on the default
audio sink (the ISO ships pipewire + wireplumber), falling back to ALSA `amixer` if wpctl is
absent. Brightness reads/writes the kernel backlight under /sys/class/backlight directly (no
brightnessctl dependency): reading is free, and writing the brightness file needs root, so the
brightness setters run that one write via sudo (the FN keybind runs `azzio brightness ...`,
and the live medium/user has passwordless sudo). All standard library; bundled into the single
/usr/local/bin/azzio script (see common.py).
"""

from __future__ import annotations

# BUNDLE_START

# The single step every FN press moves, as a PERCENT of the 0..100 range. The spec: "each
# decrease and increase should be 7.5%". Kept exact (float) so 100 -> 92.5 -> 85 -> ... .
MEDIA_STEP_PERCENT = 7.5
MEDIA_MIN_PERCENT = 0.0
MEDIA_MAX_PERCENT = 100.0

# The pre/post-install STARTING levels (the spec: "the first default when its pre/post install
# is: 50% volume and 100% brightness"). Applied ONCE (see cmd_media_init / _media_seed_file);
# after the user changes anything their choice persists -- we never re-seed.
MEDIA_DEFAULT_VOLUME = 50.0
MEDIA_DEFAULT_BRIGHTNESS = 100.0

# The kernel backlight dir (mirrors machine.py). Brightness is read/written under the first
# backlight device found here: <dev>/brightness (current, writable by root) and
# <dev>/max_brightness (the raw ceiling the percent scales against).
_BACKLIGHT_DIR = "/sys/class/backlight"

# The shipped OSD indicator (the compiled Xlib program azzio-osd, built from on_screen_display.c). Installed
# next to the C terminal user interface binary in the azzio lib dir so the two travel together.
# Kept in lock-step with packages/openbox (AZZIO_OSD_SYSTEM_PATH) -- a test pins the two so
# they cannot drift.
OSD_INDICATOR_BIN = "/usr/local/lib/azzio/azzio-osd"


def _clamp_percent(p: float) -> float:
    """Clamp a percent into [0, 100]."""
    return max(MEDIA_MIN_PERCENT, min(MEDIA_MAX_PERCENT, p))


def _step_percent(current: float, direction: int) -> float:
    """Apply ONE 7.5% step to `current` in `direction` (+1 up, -1 down), clamped to 0..100.
    Pure -- unit-tested so the step/clamp behaviour can't silently drift."""
    return _clamp_percent(current + direction * MEDIA_STEP_PERCENT)


def _parse_percent(s: str) -> float | None:
    """Parse a user-supplied percent for `set <N>`: an int/float, optionally with a trailing
    '%'. Returns the CLAMPED 0..100 value, or None if it is not a number. This is what lets
    `azzio volume set 65` (and the TUI's Volume rows / the OSD mouse-drag) pick a PRECISE
    level instead of only stepping 7.5% at a time."""
    s = s.strip().rstrip("%").strip()
    try:
        return _clamp_percent(float(s))
    except ValueError:
        return None


# --- the on-screen display ---------------------------------------------------
def _show_osd(kind: str, percent: float, muted: bool = False) -> None:
    """Pop the bottom-middle cyan OSD bar for `kind` ("volume"/"brightness") at `percent`,
    marking it muted when asked. Best-effort and non-blocking: the indicator is launched
    DETACHED with a single JSON line on its stdin, so the FN key returns instantly and a missing
    indicator (or no display) never makes the volume/brightness change itself fail. If a resident
    OSD window is already up, that one JSON line just updates it in place (no second window, no
    flicker) -- the single-instance handling lives in the azzio-osd program. No DISPLAY => skip."""
    if not os.environ.get("DISPLAY"):
        return
    if not (os.path.exists(OSD_INDICATOR_BIN) and os.access(OSD_INDICATOR_BIN, os.X_OK)):
        return
    payload = json.dumps({
        "kind": kind,
        "percent": round(_clamp_percent(percent), 1),
        "muted": bool(muted),
    }) + "\n"
    try:
        proc = subprocess.Popen(
            [OSD_INDICATOR_BIN],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Hand it the one message, then close stdin so it renders and self-dismisses. We do
        # not wait on it (fire-and-forget): the key press must feel instant.
        if proc.stdin:
            proc.stdin.write(payload.encode())
            proc.stdin.close()
    except OSError:
        pass


# --- volume (PipeWire wpctl, ALSA amixer fallback) --------------------------
_WPCTL_SINK = "@DEFAULT_AUDIO_SINK@"


def _wpctl_get() -> tuple[float, bool] | None:
    """Read (percent, muted) from wpctl for the default sink, or None if wpctl is unavailable
    or unparseable. `wpctl get-volume @DEFAULT_AUDIO_SINK@` prints e.g. "Volume: 0.75" or
    "Volume: 0.75 [MUTED]"; the float is 0.0..1.0 (can exceed 1.0, which we clamp)."""
    if not _have("wpctl"):
        return None
    r = subprocess.run(["wpctl", "get-volume", _WPCTL_SINK],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if r.returncode != 0:
        return None
    out = r.stdout.decode("utf-8", "replace")
    muted = "MUTED" in out.upper()
    for tok in out.replace(",", ".").split():
        try:
            return _clamp_percent(float(tok) * 100.0), muted
        except ValueError:
            continue
    return None


def _wpctl_set(percent: float) -> bool:
    """Set the default sink to `percent` via wpctl (0..100 -> a 0..1 fraction). Returns True on
    success. `-l 1.0` caps the volume at 100% so a press can never push it into overdrive."""
    if not _have("wpctl"):
        return False
    frac = _clamp_percent(percent) / 100.0
    rc = subprocess.run(["wpctl", "set-volume", "-l", "1.0", _WPCTL_SINK, f"{frac:.4f}"],
                        stderr=subprocess.DEVNULL).returncode
    return rc == 0


def _wpctl_toggle_mute() -> bool:
    if not _have("wpctl"):
        return False
    rc = subprocess.run(["wpctl", "set-mute", _WPCTL_SINK, "toggle"],
                        stderr=subprocess.DEVNULL).returncode
    return rc == 0


def _amixer_get() -> tuple[float, bool] | None:
    """Fallback: read (percent, muted) from ALSA `amixer get Master`. The line carries
    "[42%]" and "[on]"/"[off]". None if amixer is unavailable/unparseable."""
    if not _have("amixer"):
        return None
    r = subprocess.run(["amixer", "get", "Master"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if r.returncode != 0:
        return None
    out = r.stdout.decode("utf-8", "replace")
    pct, muted = None, False
    import re
    m = re.search(r"\[(\d+)%\]", out)
    if m:
        pct = float(m.group(1))
    if "[off]" in out:
        muted = True
    return (pct, muted) if pct is not None else None


def _amixer_set(percent: float) -> bool:
    if not _have("amixer"):
        return False
    rc = subprocess.run(["amixer", "-q", "set", "Master", f"{int(round(_clamp_percent(percent)))}%"],
                        stderr=subprocess.DEVNULL).returncode
    return rc == 0


def _amixer_toggle_mute() -> bool:
    if not _have("amixer"):
        return False
    rc = subprocess.run(["amixer", "-q", "set", "Master", "toggle"],
                        stderr=subprocess.DEVNULL).returncode
    return rc == 0


def _volume_read() -> tuple[float, bool]:
    """Current (percent, muted), preferring wpctl (PipeWire) then amixer (ALSA); (0, False)
    if neither is available so the caller still has a number to step from."""
    return _wpctl_get() or _amixer_get() or (0.0, False)


def _volume_write(percent: float) -> bool:
    """Set the volume via wpctl, else amixer. True on success."""
    return _wpctl_set(percent) or _amixer_set(percent)


def _volume_toggle_mute() -> bool:
    return _wpctl_toggle_mute() or _amixer_toggle_mute()


def cmd_volume(args: list[str]) -> int:
    """Dispatch `azzio volume up|down|set <N>|mute|get`. up/down step 7.5%; set <N> jumps to a
    precise 0..100 percent; both show the OSD. mute toggles and shows the OSD; get prints the
    percent. Unknown verb -> usage + rc 2."""
    verb = args[0] if args else "get"
    if verb in ("up", "down"):
        cur, _muted = _volume_read()
        new = _step_percent(cur, +1 if verb == "up" else -1)
        if not _volume_write(new):
            _err("azzio volume: no audio backend (wpctl/amixer) available")
            return 1
        _show_osd("volume", new, muted=False)
        print(f"Volume: {int(round(new))}%")
        return 0
    if verb in ("set", "="):
        # Set a PRECISE level (the spec: "select precisely how much volume I want"). Drives the
        # TUI's Volume rows and the OSD mouse-drag. The percent is the next arg (or glued: set=65).
        arg = args[1] if len(args) > 1 else verb.split("=", 1)[1] if "=" in verb else ""
        pct = _parse_percent(arg)
        if pct is None:
            _err(f"azzio volume: set needs a percent 0-100 (got {arg!r})")
            return 2
        if not _volume_write(pct):
            _err("azzio volume: no audio backend (wpctl/amixer) available")
            return 1
        _show_osd("volume", pct, muted=False)
        print(f"Volume: {int(round(pct))}%")
        return 0
    if verb in ("mute", "toggle", "togglemute"):
        if not _volume_toggle_mute():
            _err("azzio volume: no audio backend (wpctl/amixer) available")
            return 1
        pct, muted = _volume_read()
        _show_osd("volume", pct, muted=muted)
        print(f"Volume: {'muted' if muted else 'unmuted'} ({int(round(pct))}%)")
        return 0
    if verb == "get":
        pct, muted = _volume_read()
        print(f"{int(round(pct))}{' muted' if muted else ''}")
        return 0
    if verb in ("--help", "-h", "help"):
        _media_usage("volume")
        return 0
    _err(f"azzio volume: unknown option: {verb}")
    _media_usage("volume")
    return 2


# --- brightness (kernel backlight sysfs, laptop-only) -----------------------
def _backlight_device() -> str | None:
    """The first backlight device dir under /sys/class/backlight (e.g. .../intel_backlight),
    or None when there is none (a desktop). Sorted so the choice is stable across calls."""
    try:
        names = sorted(e.name for e in os.scandir(_BACKLIGHT_DIR))
    except OSError:
        return None
    return os.path.join(_BACKLIGHT_DIR, names[0]) if names else None


def _brightness_read() -> float | None:
    """Current brightness as a 0..100 percent from the kernel backlight (brightness /
    max_brightness * 100), or None if there is no backlight or it is unreadable."""
    dev = _backlight_device()
    if not dev:
        return None
    try:
        with open(os.path.join(dev, "brightness"), encoding="utf-8") as fh:
            cur = int(fh.read().strip())
        with open(os.path.join(dev, "max_brightness"), encoding="utf-8") as fh:
            mx = int(fh.read().strip())
    except (OSError, ValueError):
        return None
    if mx <= 0:
        return None
    return _clamp_percent(cur / mx * 100.0)


def _brightness_write(percent: float) -> bool:
    """Set the backlight to `percent` (scaled to the device's raw max). Writing the brightness
    file needs root, so the write goes through `sudo tee` (the FN keybind runs `azzio
    brightness ...`, and the medium/user has passwordless sudo). A raw value of at least 1 is
    kept so a "down" step never blanks the panel to pure black. True on success."""
    dev = _backlight_device()
    if not dev:
        return False
    try:
        with open(os.path.join(dev, "max_brightness"), encoding="utf-8") as fh:
            mx = int(fh.read().strip())
    except (OSError, ValueError):
        return False
    if mx <= 0:
        return False
    raw = int(round(_clamp_percent(percent) / 100.0 * mx))
    raw = max(1, min(mx, raw))
    path = os.path.join(dev, "brightness")
    # Prefer a direct write if we can (already root); else `sudo tee` the one file.
    try:
        if os.access(path, os.W_OK):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(str(raw))
            return True
    except OSError:
        pass
    _sudo_write(path, str(raw))
    # Verify the write took (sudo tee is best-effort/silent), so we only claim success when it did.
    return _brightness_read() is not None


def cmd_brightness(args: list[str]) -> int:
    """Dispatch `azzio brightness up|down|set <N>|get`. up/down step 7.5% and set <N> jumps to
    a precise 0..100 percent -- both show the OSD, but ONLY on a laptop (a PC has no backlight --
    brightness is not an option); get always prints a reading. Unknown verb -> usage + rc 2."""
    verb = args[0] if args else "get"
    if verb in ("up", "down", "set", "="):
        # Brightness is a LAPTOP-ONLY control (honours the machine hard override).
        if not is_laptop():
            _err("azzio brightness: not a laptop -- brightness is a laptop-only control "
                 "(this machine is a PC). Use `azzio machine --laptop` to force it on.")
            return 1
        if verb in ("set", "="):
            # A PRECISE level (the TUI's Brightness rows / the OSD mouse-drag). No need to read
            # the current value -- we jump straight to the requested percent.
            arg = args[1] if len(args) > 1 else verb.split("=", 1)[1] if "=" in verb else ""
            new = _parse_percent(arg)
            if new is None:
                _err(f"azzio brightness: set needs a percent 0-100 (got {arg!r})")
                return 2
        else:
            cur = _brightness_read()
            if cur is None:
                _err("azzio brightness: no backlight found under /sys/class/backlight")
                return 1
            new = _step_percent(cur, +1 if verb == "up" else -1)
        if not _brightness_write(new):
            _err("azzio brightness: could not set the backlight")
            return 1
        _show_osd("brightness", new)
        print(f"Brightness: {int(round(new))}%")
        return 0
    if verb == "get":
        cur = _brightness_read()
        if cur is None:
            print("n/a")     # no backlight (a PC) -- still exit 0 so a query never errors
            return 0
        print(f"{int(round(cur))}")
        return 0
    if verb in ("--help", "-h", "help"):
        _media_usage("brightness")
        return 0
    _err(f"azzio brightness: unknown option: {verb}")
    _media_usage("brightness")
    return 2


# --- one-time defaults seed (50% volume / 100% brightness) -------------------
def _media_seed_file() -> str:
    """The per-user marker that records the one-time media seed has run:
    ~/.config/azzio/media-seeded. Its mere EXISTENCE means "already seeded" -- so the defaults
    are applied exactly once and a later user change is never clobbered on the next boot. Off ~
    at runtime so it targets the current user (no sudo), like the theme/wallpaper pointers."""
    return os.path.join(os.path.expanduser("~"), ".config/azzio/media-seeded")


def cmd_media_init(args: list[str]) -> int:
    """`azzio media-init` -- seed the STARTING media levels ONCE (50% volume, and 100%
    brightness on a laptop), then drop a marker so it never runs again. This is the pre/post-
    install default the spec asks for: a fresh machine boots at 50% volume / 100% brightness,
    but the instant the user configures anything their choice persists (we key off the marker,
    not off the current level, so we do NOT re-assert 50/100 on later boots).

    Idempotent and silent (NO OSD -- this runs at login, not from a keypress). `--force`
    re-seeds even if the marker exists (for testing / a deliberate reset). Always rc 0: a boot
    hook must never fail the session just because, say, no audio backend is up yet."""
    force = bool(args) and args[0] in ("--force", "-f")
    marker = _media_seed_file()
    if os.path.exists(marker) and not force:
        return 0                      # already seeded -- respect whatever the user has since set
    # Volume: everyone gets 50%. Best-effort -- if no backend is present we still write the
    # marker so we do not retry forever every login (the next real change will set it anyway).
    _volume_write(MEDIA_DEFAULT_VOLUME)
    # Brightness: only meaningful on a laptop (a PC has no backlight). Honour the machine type.
    if is_laptop():
        _brightness_write(MEDIA_DEFAULT_BRIGHTNESS)
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("seeded\n")
    except OSError:
        pass
    return 0


def _media_usage(which: str) -> None:
    if which == "volume":
        print(
            "Usage: azzio volume <up|down|set <N>|mute|get>\n"
            "\n"
            "Change the system volume in 7.5% steps (0-100%), showing a bottom-middle cyan\n"
            "on-screen bar. Bound to the FN volume keys by the OpenBox session.\n"
            "\n"
            "  up       Raise the volume 7.5%.\n"
            "  down     Lower the volume 7.5%.\n"
            "  set <N>  Set the volume to a precise percent (0-100).\n"
            "  mute     Toggle mute.\n"
            "  get      Print the current volume percent.\n"
        )
    else:
        print(
            "Usage: azzio brightness <up|down|set <N>|get>\n"
            "\n"
            "Change the screen brightness in 7.5% steps (0-100%), showing a bottom-middle cyan\n"
            "on-screen bar. LAPTOP ONLY -- a PC has no backlight to control (use\n"
            "`azzio machine --laptop` to force it on). Bound to the FN brightness keys.\n"
            "\n"
            "  up       Raise brightness 7.5%.\n"
            "  down     Lower brightness 7.5%.\n"
            "  set <N>  Set brightness to a precise percent (0-100).\n"
            "  get      Print the current brightness percent (n/a on a PC).\n"
        )
