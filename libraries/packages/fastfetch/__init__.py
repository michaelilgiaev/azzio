"""fastfetch configuration + the azzio "Az'" ASCII logo.

fastfetch ships in the ISO's package list; this module provides the two files
that make `fastfetch` render the azzio brand instead of the stock Arch logo:

  logo_txt()     the "Az'" ASCII art, read verbatim from the repo asset
                 assets/ascii/azzio_fastfetch.ansi. That file is ALREADY
                 colored with the azzio cyan->blue gradient (real ANSI truecolor
                 escapes baked in per row), so the configuration below uses
                 `type: "file-raw"`, which prints the file byte-for-byte with no
                 post-processing. (The older `type: "file"` + $N-placeholder path
                 was dropped: it required a separate uncolored art plus a color
                 map, and the hand-rolled art it used was a crude reverse-engineer
                 of the logo. The .ansi asset is the good hand-tuned art.)
  config_jsonc() ~/.config/fastfetch/config.jsonc pointing fastfetch at the logo
                 file by ABSOLUTE path.

Both land in the user's ~/.config/fastfetch/ on the live ISO and, via the
installer copy path, on the installed system.
"""

from __future__ import annotations

import paths

# The "Az'" logo lives as a real asset in the repo (git-tracked, survives
# `git clean -Xdf`): a rounded capital A with a hollow counter and a lowercase z,
# 40 cols x 18 rows, pre-colored with the cyan->blue gradient sampled from
# assets/azzio_logo.png. We read it verbatim rather than embedding a copy here so
# the art has a single source of truth (the .ansi file you can open and eyeball).
_LOGO_ASSET = paths.ASSETSDIR / "ascii" / "azzio_fastfetch.ansi"

# The bare filename the art is written to inside ~/.config/fastfetch/. The configuration's
# `source` is the absolute path to this; compiler.py / the installer write the file
# there. Kept as a constant so the two stay in lockstep.
LOGO_FILENAME = "azzio.ansi"
LOGO_PATH = f"/home/main/.config/fastfetch/{LOGO_FILENAME}"


def logo_txt() -> str:
    """The pre-colored Az' art, verbatim from the repo asset."""
    return _LOGO_ASSET.read_text(encoding="utf-8")


def config_jsonc() -> str:
    """fastfetch configuration: print the pre-colored Az' art verbatim.

    `source` MUST be an absolute path. fastfetch resolves a RELATIVE source
    against the CURRENT WORKING DIRECTORY -- not the configuration dir -- so a bare
    filename only works if you happen to run fastfetch from ~/.config/fastfetch/.
    Run it from ~ (the normal case) and fastfetch fails to read the file and
    SILENTLY falls back to the auto-detected Arch logo -- exactly the bug that
    showed the stock Arch logo. We hard-code the absolute path (rather than "~/...")
    to avoid any ambiguity about tilde expansion in the source field: the live ISO
    user and the installed user are both `main` with home /home/main, so the
    literal path is correct in both.

    `type: "file-raw"` prints the file byte-for-byte -- no $N replacement, no
    re-coloring -- because the .ansi art already carries its own truecolor escapes.

    The `os` module is expanded from the bare "os" string into an object with an
    explicit `format`: "Azzio {arch}". By default fastfetch renders the os line from
    os-release NAME + architecture, and NAME is "Azzio Linux" (system.py, the real
    distro identity used by the BIOS menu, specs, etc.), so the default would print
    "Azzio Linux x86_64" -- redundant with the kernel line below. We hard-code
    "Azzio" here (the ONLY place the display is trimmed; os-release NAME is left
    untouched) and keep the live `{arch}` placeholder so it still reports the real
    architecture. `key: "OS"` pins the label so the row stays "OS: Azzio x86_64".
    """
    return f"""\
{{
    "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
    "logo": {{
        "type": "file-raw",
        "source": "{LOGO_PATH}",
        "padding": {{
            "top": 1,
            "left": 2
        }}
    }},
    "modules": [
        "title",
        "separator",
        {{
            "type": "os",
            "key": "OS",
            "format": "Azzio {{arch}}"
        }},
        "host",
        "kernel",
        "uptime",
        "packages",
        "shell",
        "de",
        "wm",
        "terminal",
        "cpu",
        "gpu",
        "memory",
        "disk",
        "break",
        "colors"
    ]
}}
"""
