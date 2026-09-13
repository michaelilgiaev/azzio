#!/usr/bin/env python3
"""azzio guest command line interface -- `azzio wallpaper` (pick the desktop wallpaper: years / decades).

Azzio ships TWO wallpapers on the medium, both under the standard system wallpaper dir
/usr/share/wallpapers (each as .../<id>/contents/images/<W>x<H>.png -- the layout
packages/openbox emits and compiler.py fills). The OpenBox session paints one of them
onto the X root with feh (from ~/.xinitrc and the OpenBox autostart); "years" is the
shipped default.

`azzio wallpaper` lets the end user switch between the two AND makes the choice stick:

    azzio wallpaper --years.png     set the "years" wallpaper
    azzio wallpaper --decades.png   set the "decades" wallpaper
    azzio wallpaper --help          show usage
    azzio wallpaper                 print the current wallpaper

HOW IT APPLIES + PERSISTS. Two halves, mirroring how `azzio theme` both writes config and
applies live:

  * PERSIST: the chosen image's absolute path is written to ~/.config/azzio/wallpaper (a
    one-line pointer file). The OpenBox session's wallpaper step (~/.xinitrc and
    ~/.config/openbox/autostart, both from packages/openbox) reads THIS file if it
    exists and paints that image, falling back to the shipped "years" default when it is
    absent -- so the choice survives a re-login, while a fresh user still gets "years".
  * LIVE: if a session is up (DISPLAY set) and feh is installed, repaint the root pixmap
    now with `feh --no-fehbg --bg-fill <image>` -- the SAME command the autostart uses --
    so the wallpaper changes immediately, no re-login.

Runs WITHOUT sudo: it only writes the user's own config pointer and repaints the user's
own X root (exactly like `azzio theme`). Standard library only (this module is bundled
into the single /usr/local/bin/azzio script; see common.py). The wallpaper paths here are
kept in lock-step with packages/openbox (a test pins them) so the command line interface and the shipped
images can never disagree.
"""

from __future__ import annotations

# BUNDLE_START

# The two wallpapers shipped under the standard system wallpaper dir. Kept in lock-step
# with packages/openbox (WALLPAPERS_SYSTEM_DIR / WALLPAPER_IMAGE_RES / WALLPAPER_PACKAGES);
# a test (test_configuration_wallpaper) pins these against those constants so the command line interface and the
# emitted images cannot drift. feh needs a real FILE (not a dir), so each id resolves to the
# inner .../<id>/contents/images/<W>x<H>.png the compiler writes.
WALLPAPERS_SYSTEM_DIR = "/usr/share/wallpapers"
WALLPAPER_IMAGE_RES = "1672x941"          # WxH of the shipped PNGs (matches openbox)
WALLPAPER_IDS = ("years", "decades")      # the two shipped wallpapers; years is the default
WALLPAPER_DEFAULT_ID = "years"


def _wallpaper_image(wp_id: str) -> str:
    """Absolute path to the inner PNG for a wallpaper id (the file feh paints)."""
    return (f"{WALLPAPERS_SYSTEM_DIR}/{wp_id}"
            f"/contents/images/{WALLPAPER_IMAGE_RES}.png")


# The per-user pointer file the OpenBox session reads to decide which wallpaper to paint.
# Under the user's config dir (XDG_CONFIG_HOME defaults to ~/.config, which the OpenBox
# session exports). A one-line file holding the chosen image's absolute path. Resolved off ~
# at runtime so it always targets the CURRENT user's session (no sudo).
def _state_file() -> str:
    return os.path.join(os.path.expanduser("~"), ".config/azzio/wallpaper")


# The option each wallpaper is selected by on the command line. The spec spells them with a
# ".png" suffix (`azzio wallpaper --years.png`), so accept exactly those, mapping to the id.
_OPTION_TO_ID = {f"--{wp_id}.png": wp_id for wp_id in WALLPAPER_IDS}


def _apply_live(image: str) -> None:
    """Repaint the X root with the chosen image NOW, if a session is up and feh exists.
    Uses the SAME `feh --no-fehbg --bg-fill` the OpenBox autostart/xinitrc use, so the live
    change matches what a re-login would paint. Silent no-op with no DISPLAY (headless)."""
    if os.environ.get("DISPLAY") and _have("feh"):
        subprocess.run(["feh", "--no-fehbg", "--bg-fill", image],
                       stderr=subprocess.DEVNULL, check=False)


def _persist(image: str) -> None:
    """Write the chosen image path to the per-user pointer file (creating ~/.config/azzio).
    The session's wallpaper step reads this on the next login; a plain one-line path."""
    path = _state_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(image + "\n")


def _current_image() -> str:
    """The currently-selected wallpaper image path: the pointer file's contents if present
    and non-empty, else the shipped default ("years"). Matches what the session would paint."""
    try:
        val = open(_state_file(), encoding="utf-8").read().strip()
        if val:
            return val
    except OSError:
        pass
    return _wallpaper_image(WALLPAPER_DEFAULT_ID)


def _current_id() -> str:
    """The current wallpaper as an id ("years"/"decades") when the path is one of ours,
    else "custom" (a hand-set pointer). Purely for the status print."""
    cur = _current_image()
    for wp_id in WALLPAPER_IDS:
        if cur == _wallpaper_image(wp_id):
            return wp_id
    return "custom"


def apply_wallpaper(wp_id: str) -> int:
    """Set the wallpaper to `wp_id`: persist the pointer AND apply it live. The image must
    exist on disk (it is shipped by the build); if it is missing we still persist the
    pointer but warn, since a live session without the file cannot paint it."""
    image = _wallpaper_image(wp_id)
    if not os.path.exists(image):
        _err(f"azzio wallpaper: image not found: {image} "
             "(is this an Azzio system with the wallpapers installed?)")
        # Persist anyway so a later-installed image is honoured, but report the miss.
        _persist(image)
        return 1
    _persist(image)
    _apply_live(image)
    print(f"Wallpaper set to {wp_id}.")
    return 0


def wallpaper_status() -> int:
    """Print the current wallpaper (the bare `azzio wallpaper` behaviour)."""
    print(f"Wallpaper: {_current_id()}")
    print(f"  image: {_current_image()}")
    return 0


def wallpaper_usage() -> None:
    print(
        "Usage: azzio wallpaper [--years.png | --decades.png]\n"
        "\n"
        "Set the desktop wallpaper. Azzio ships two wallpapers under\n"
        "/usr/share/wallpapers; this picks between them, applies it to the running\n"
        "session immediately, and remembers it for the next login.\n"
        "\n"
        "  --years.png     The 'years' wallpaper (the default).\n"
        "  --decades.png   The 'decades' wallpaper.\n"
        "  --help          Show this help.\n"
        "  (no option)     Print the current wallpaper.\n"
    )


def cmd_wallpaper(args: list[str]) -> int:
    """Dispatch `azzio wallpaper ...`. No option -> print status; --years.png /
    --decades.png set the wallpaper; --help/-h prints usage."""
    if not args:
        return wallpaper_status()
    opt = args[0]
    if opt in _OPTION_TO_ID:
        return apply_wallpaper(_OPTION_TO_ID[opt])
    if opt in ("--help", "-h", "help"):
        wallpaper_usage()
        return 0
    _err(f"azzio wallpaper: unknown option: {opt}")
    wallpaper_usage()
    return 2
