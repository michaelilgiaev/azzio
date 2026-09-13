"""~/.config/user-dirs.dirs -- the XDG user-dirs pointer for the file manager (PROMPT: delete ~/Templates).

This is a SUBMODULE of the file_manager package (packages/file_manager/templates.py). It USED to
ship a "Create Document" template set in ~/Templates plus a user-dirs.dirs that pointed
XDG_TEMPLATES_DIR at that dir. The user asked for ~/Templates to be deleted entirely ("Delete that
'Templates/' directory, idk what it's created in the first place"), so the template FILE set is
gone and this module now ships ONLY the XDG user-dirs pointer, with XDG_TEMPLATES_DIR back at the
stock "$HOME/" default (no Templates dir any more -- an empty "Create Document" submenu, which is
fine; misc-show-about-templates=false in settings hides the leftover placeholder modal).

The module NAME and public surface (USER_DIRS_PATH, user_dirs_dirs(), emit_plan()) are kept so
file_manager/__init__.py (`from . import templates`, re-exports USER_DIRS_PATH, folds emit_plan())
and the reclassification test still import it unchanged.

WHY STILL SHIP user-dirs.dirs AT ALL. If we ship nothing, xdg-user-dirs-update regenerates the
stock file on first login (which points XDG_TEMPLATES_DIR at "$HOME/Templates" and would recreate
that dir the moment the file manager touches it). Shipping our own file -- with the
`# written by xdg-user-dirs-update` banner so the updater treats it as its own and preserves it --
pins XDG_TEMPLATES_DIR="$HOME/" (no Templates dir) and keeps the other XDG dirs mapped to the
Azzio home layout (home_directory.DIRECTORIES) so xdg-aware apps land in the right folders.

WHERE IT GOES. A HOME file (owner "home", skel-mirrored) -- user-dirs.dirs belongs to the user.
file_manager.emit_plan() folds this module's emit_plan() into the combined file-manager plan that
compiler._emit_apps writes alongside every other package's.
"""

from __future__ import annotations

# The live user's home (matches openbox.HOME / the airootfs /home/main tree).
HOME = "/home/main"

# ~/.config/user-dirs.dirs -- the XDG user-dirs pointer. XDG_TEMPLATES_DIR="$HOME/" (stock default,
# no Templates dir); the rest mirror the Azzio home layout (home_directory.DIRECTORIES) so
# xdg-aware apps land in the right folders.
USER_DIRS_PATH = f"{HOME}/.config/user-dirs.dirs"


def user_dirs_dirs() -> str:
    """Return ~/.config/user-dirs.dirs. Maps the XDG dirs to the Azzio home layout; keeps
    XDG_TEMPLATES_DIR at the stock "$HOME/" (there is no ~/Templates any more -- the user asked for
    it to be deleted). The `# written by xdg-user-dirs-update` banner is kept so
    xdg-user-dirs-update treats it as its own file and preserves these values (it only rewrites
    missing lines) -- otherwise it would regenerate a stock file that points XDG_TEMPLATES_DIR at
    "$HOME/Templates" and recreate that dir."""
    return (
        "# This file is written by xdg-user-dirs-update\n"
        "# Azzio ships it (packages/file_manager/templates) to map the XDG dirs to the Azzio home\n"
        "# layout and to keep XDG_TEMPLATES_DIR at the stock $HOME/ (there is no ~/Templates dir).\n"
        '# Format is XDG_xxx_DIR="$HOME/yyy".\n'
        'XDG_DESKTOP_DIR="$HOME/Desktop"\n'
        'XDG_DOWNLOAD_DIR="$HOME/Downloads"\n'
        'XDG_TEMPLATES_DIR="$HOME/"\n'
        'XDG_PUBLICSHARE_DIR="$HOME/"\n'
        'XDG_DOCUMENTS_DIR="$HOME/Documents"\n'
        'XDG_MUSIC_DIR="$HOME/Music"\n'
        'XDG_PICTURES_DIR="$HOME/Pictures"\n'
        'XDG_VIDEOS_DIR="$HOME/Videos"\n'
    )


_CONF = 0o644


def emit_plan() -> list[dict]:
    """Return the emit plan for user-dirs.dirs -- a single HOME file (owner "home", skel-mirrored).
    file_manager.emit_plan() folds this into the combined file-manager plan that
    compiler._emit_apps writes. (There is no ~/Templates template set any more -- the user asked for
    the Templates dir to be deleted, so only the XDG pointer ships.)"""
    return [
        {
            "builder": user_dirs_dirs,
            "dest": USER_DIRS_PATH,
            "mode": _CONF,
            "owner": "home",
        },
    ]
