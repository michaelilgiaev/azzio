"""Minimal OpenBox live-session desktop, authored as configuration-as-Python strings.

OpenBox is the whole desktop. The ISO boots to a graphical OpenBox (X11) live session
WITHOUT a display manager, Manjaro-style:

    getty@tty1 autologins `main`  ->  ~/.bash_profile runs `exec startx` on
    tty1 only  ->  ~/.xinitrc paints the wallpaper (no flash) and execs
    `openbox-session`  ->  OpenBox reads rc.xml (keybinds, no decorations fuss)
    and runs ~/.config/openbox/autostart, which sets the wallpaper, starts the
    Azzio application-menu daemon, arms the Super key (via xcape), applies the
    keyboard layouts, and opens the Calamares installer once.

There is deliberately NO PANEL (the user's "we're not going to have a bottom panel
anymore" decision) and NO desktop right-click menu (the OpenBox root menu was removed
at the user's request; right-clicking the background does nothing). The ONLY shell
surface is the Azzio application menu -- a borderless C/GTK3 launcher centered on
the screen, opened by the Super key. The launcher and power actions all live in that
menu (there is no separate panel).

Everything here is a small builder function returning the CONTENT of one file.
compiler.py emits each to its airootfs destination via emit.write_text/write_exec and
iterates PLAN (below) so the mapping (path + mode) stays declarative. The /home/main
tree is chowned 1000:998 by compiler.py after emit, exactly like the fastfetch/first-boot
payloads.

Design constraints (match archiso/OpenBox/Calamares reality):
  * No emojis, ASCII only.
  * No display manager. `openbox-session` is provided by the `openbox` package; it
    is both the window manager AND the session launcher (it sources
    ~/.config/openbox/{environment,autostart} and reads rc.xml). We ship NO menu.xml
    (the root menu is removed). See libraries/packages/packages.x86_64.
  * Calamares MUST run privileged. The live medium has passwordless-sudo `main`
    and passwordless root, so the launch stays `sudo -E calamares` via the tiny
    /usr/local/bin/azzioinstall wrapper the autostart runs.
  * NO cyan/black flash: ~/.xinitrc sets the X root to the SAME wallpaper image the
    session will show (feh --bg-fill) BEFORE OpenBox starts, and the autostart's own
    `feh --bg-fill` repaints the identical pixels -- so the first and only paint is
    the wallpaper. (OpenBox draws no wallpaper itself; feh owns the root pixmap.)
  * The Super key opens the menu. OpenBox cannot bind a LONE modifier, so `xcape`
    turns a solo Super_L tap into the chord Super_L+Menu, which rc.xml binds to the
    menu launcher (Super still works as a normal modifier for every other bind).
  * The two azzio wallpapers ("years"/"decades") are shipped under
    /usr/share/wallpapers as plain images; "years" is the default feh paints. Baked
    into /etc/skel too so a Calamares-created user inherits the same session.
  * startx-from-tty replaces graphical.target: _link_services needs no
    display-manager .wants symlink or graphical.target (see compiler.py STEPS_NOTE).
"""

from __future__ import annotations

# --- Branding / assets ------------------------------------------------------
# The two wallpapers shipped on the medium. Each is a plain PNG copied under
# /usr/share/wallpapers/<id>/contents/images/<W>x<H>.png (this nested layout is kept so
# the assets and compiler.py emit paths do not have to change; OpenBox/feh only ever
# reads the inner file). Both source images are 1672x941 (see assets/wallpapers/).
WALLPAPERS_SYSTEM_DIR = "/usr/share/wallpapers"
WALLPAPER_IMAGE_RES = "1672x941"          # WxH of the shipped PNGs
WALLPAPER_PACKAGES = [
    {"id": "years", "asset": "wallpapers/years.png"},
    {"id": "decades", "asset": "wallpapers/decades.png"},
]

# The DEFAULT wallpaper painted on the live/installed session -- the "years" image.
# feh needs a real FILE (it cannot take a directory), so this points at the inner png.
WALLPAPER_DEFAULT_ID = "years"
WALLPAPER_IMAGE_FILE = (
    f"{WALLPAPERS_SYSTEM_DIR}/{WALLPAPER_DEFAULT_ID}"
    f"/contents/images/{WALLPAPER_IMAGE_RES}.png"
)
# The asset copied to WALLPAPER_IMAGE_FILE is the same "years" image; compiler.py already
# writes that image, so the default resolves to a file that exists.
WALLPAPER_ASSET = "wallpapers/years.png"

def _feh_wallpaper_line() -> str:
    """A POSIX-sh snippet that paints the wallpaper with feh, honouring the per-user pointer
    `azzio wallpaper` writes: read the pointer file; if it names an existing file use it,
    otherwise fall back to the shipped "years" default. Shared by ~/.xinitrc and the OpenBox
    autostart so the pre-paint (no-flash) and the session repaint choose the SAME image, and
    both follow an `azzio wallpaper` choice. `$HOME` (the SHELL variable) is used so the
    same line works for any user that inherited the config via /etc/skel."""
    # NB: the pointer path is expressed with $HOME so it resolves per-user; the default is
    # the fixed shipped path. Guard on feh existing so a missing tool never breaks startup.
    return (
        f'_azwp="$(cat "$HOME/.config/azzio/wallpaper" 2>/dev/null)"\n'
        f'[ -n "$_azwp" ] && [ -f "$_azwp" ] || _azwp=\'{WALLPAPER_IMAGE_FILE}\'\n'
        f'[ -x /usr/bin/feh ] && feh --no-fehbg --bg-fill "$_azwp"'
    )


def wallpaper_metadata_json(wp_id: str) -> str:
    """Minimal metadata.json shipped alongside each wallpaper image.

    Nothing reads this at runtime; it is kept purely so the two wallpaper directories
    remain self-describing (authorship / license) and so the compiler.py emit layout for
    wallpapers does not have to change.
    Name == Id so any future picker still labels them "years"/"decades"."""
    return (
        "{\n"
        '    "KPlugin": {\n'
        f'        "Id": "{wp_id}",\n'
        f'        "Name": "{wp_id}",\n'
        '        "License": "CC-BY-SA-4.0",\n'
        '        "Authors": [\n'
        '            { "Name": "Azzio", "Email": "" }\n'
        "        ]\n"
        "    }\n"
        "}\n"
    )


# The one privileged launch path shared by the autostart + the OpenBox root menu +
# a menu launcher.
INSTALL_WRAPPER_PATH = "/usr/local/bin/azzioinstall"

# The "instant" ISO auto-install hook. The live autostart normally opens the Calamares GUI
# (azzioinstall --gui); the instant variant instead auto-runs the UNATTENDED installer
# (azzioinstall --instant <sub-flags>) the moment the session comes up. Rather than emit a
# different autostart per variant, the autostart is variant-agnostic and consults THIS
# executable hook at runtime: if it exists and is executable, the autostart runs it (instead
# of the GUI); if it is absent, the GUI installer opens as before. The hook is a tiny shell
# script that `exec`s azzioinstall with the operator's chosen `--instant` sub-flags ALREADY
# baked in and SHELL-QUOTED (the compiler owns the quoting via shlex.quote, so a value with
# spaces -- a password like "correct horse" -- survives intact; letting the autostart re-split
# a flat arg line would corrupt it). The compiler's per-variant overlay
# (compiler._apply_variant) writes this hook ONLY for the instant / instant+sshd variants and
# affirmatively removes it for base / sshd, so the shared autostart Does The Right Thing on each
# ISO. Lives under the same /usr/local/share/azzio dir as the installed-autostart staging file.
INSTANT_INSTALL_HOOK_PATH = "/usr/local/share/azzio/instant-install.sh"

# The scripted (terminal) installer the CLI/SSH path runs, baked into the live ISO under
# the azzio payload dir (/root/azzio) alongside chroot-setup.sh + packages.x86_64 + the
# offline repo it pacstraps from. `azzioinstall --cli` execs it via sudo. It is the SAME
# partition/pacstrap/chroot-setup pipeline as the first-boot installer, authored in
# libraries/installer.installer_sh, so a headless SSH install produces the same system a
# GUI Calamares install would.
INSTALL_CLI_SCRIPT_PATH = "/root/azzio/azzioinstall-cli.sh"

# The Calamares installer window's WM_CLASS. VERIFIED with `xprop WM_CLASS` on the running
# installer in the live VM: BOTH fields are the lowercase "calamares" --
#     WM_CLASS(STRING) = "calamares", "calamares"
#   * res_NAME  = argv[0] basename = "calamares" (our launcher runs `exec sudo -E calamares`).
#   * res_CLASS = "calamares" too. (Calamares sets applicationName to "calamares" here, NOT a
#                 capitalised "Calamares" -- an earlier guess used a capital C and was wrong.)
# OpenBox's <application> matching is CASE-SENSITIVE: name= matches res_name and class= matches
# res_class, so the rule MUST use the exact case of each field. A capital class="Calamares" does
# NOT match the real lowercase res_class, so that rule SILENTLY NO-OPS -- which is exactly why
# the installer was still opening MAXIMIZED (it fell through to the wildcard `<maximized>yes`
# rule) instead of restored-down. Both fields are lowercase, matched precisely below, so the
# installer opens RESTORED-DOWN and CENTERED every time (see the <applications> block):
# Calamares remembers its last window geometry, so on a REOPEN it would otherwise come up
# maximized/wherever it last sat rather than a centered, restored window.
CALAMARES_WM_NAME = "calamares"   # res_name  (argv[0] basename, lowercase)
CALAMARES_WM_CLASS = "calamares"  # res_class (applicationName, lowercase -- VERIFIED via xprop)

# --- Default window geometry (open MAXIMIZED, restore to a wide rectangle) --------------
# The user wants every app to OPEN maximized (not as a small box) and, when "restored down",
# to become a wide "classic terminal" rectangle (wider than tall), never a little square.
# OpenBox does this with a wildcard `<application class="*">` rule (see the <applications>
# block in openbox_rc_xml):
#   * <maximized>yes</maximized> makes the window MAP maximized.
#   * <size> sets the window's NORMAL (un-maximized) size, i.e. the size it snaps to when the
#     user restores it down -- so restore-down lands on this wide rectangle instead of the
#     client's tiny default. <position> centers that restored window.
# The size is a 16:10 landscape rectangle (WIDTH > HEIGHT): decidedly "wider than tall", the
# classic terminal shape. It is an ABSOLUTE OpenBox size (OpenBox <size> takes pixels, not a
# ratio), chosen to sit comfortably inside a 1920x1080 desktop with room to spare; a smaller
# screen just clamps it. A width:height pair (not a magic single number) so the landscape
# intent is explicit and a test can assert width > height.
RESTORED_WINDOW_WIDTH = 1200
RESTORED_WINDOW_HEIGHT = 750       # 1200x750 == 16:10, unmistakably wider than tall

# --- Default session resolution (1920x1080) -----------------------------------------------
# The DE-less OpenBox session has no display manager to pick a mode, so X comes up on the
# output's PREFERRED mode. On the QEMU/virtio-gpu virtual display that preferred mode is a
# quirky 1920x1031 (it reserves a sliver), NOT 1920x1080 -- so a fresh boot landed at 1920x1031
# and the e2e resolution check failed (the reported regression). We therefore ASSERT the
# intended default from the autostart: if the primary output offers 1920x1080 and is not
# already there, switch to it. It is fully guarded (missing xrandr, missing mode, or already
# 1080 => no-op), so real hardware that boots at its own correct mode is untouched, and it lives
# in the SHARED autostart block so both the live and the installed session default to 1080.
DEFAULT_RESOLUTION = "1920x1080"

# The system-wide application-menu launcher for the installer. Present on the LIVE medium
# so the installer can be reopened from the Azzio menu; REMOVED from the installed system
# by the Calamares cleanup step (calamares_shellprocess) so the installer does not appear in
# the menu post-installation (calamares itself is also try_removed). Named here so the PLAN
# entry that ships it and the shellprocess step that deletes it cannot drift.
INSTALL_MENU_DESKTOP_PATH = "/usr/share/applications/azzioinstall.desktop"

# Installer launcher icon. The Azzio icon is standardized as a SCALABLE VECTOR,
# assets/icons/azzio.svg (the "Az'" wordmark on the dark app tile), living under
# assets/icons/ alongside kitty.svg -- the single place icons live, and the same
# vector-master convention kitty follows. compiler.py copies that SVG to the hicolor
# SCALABLE apps dir (the master the icon loader rasterizes) AND rasterizes it to PNGs at
# /usr/share/pixmaps and the hicolor 256x256 apps dir, so the Desktop launcher and the
# application-menu entry (Icon=azzio-installer) resolve it regardless of which path/size
# the icon loader consults. It is ALSO the Calamares window icon (branding.desc
# productIcon, a rasterized PNG QIcon can load), so the OpenBox titlebar shows it -- see
# packages/calamares/calamares.py.
INSTALLER_ICON_ASSET = "icons/azzio.svg"
INSTALLER_ICON_NAME = "azzio-installer"
INSTALLER_ICON_PIXMAP = f"/usr/share/pixmaps/{INSTALLER_ICON_NAME}.png"
INSTALLER_ICON_HICOLOR = (
    f"/usr/share/icons/hicolor/256x256/apps/{INSTALLER_ICON_NAME}.png"
)
# The scalable (SVG) master installed alongside the PNG rasterizations, so the icon loader
# has a vector source at any size (exactly like kitty.svg -> hicolor/scalable/apps).
INSTALLER_ICON_SCALABLE = (
    f"/usr/share/icons/hicolor/scalable/apps/{INSTALLER_ICON_NAME}.svg"
)
# Square px the PNG rasterizations are rendered at (a standard icon size; the source SVG
# is 256x256 so this is 1:1 for the raster fallbacks).
INSTALLER_ICON_PNG_SIZE = 256

# Home directory of the live user; the overlay root for HOME-relative entries.
HOME = "/home/main"
# uid:gid for the live user tree (autologin group gid 998).
HOME_OWNER = (1000, 998)

# The per-user wallpaper POINTER file `azzio wallpaper` writes (packages/azzio/wallpaper.py):
# a one-line file holding the chosen image's absolute path. The session's wallpaper step
# (_feh_wallpaper_line, used by ~/.xinitrc + the OpenBox autostart) reads it and paints that
# image if it exists, else falls back to the "years" default -- so a fresh user gets "years"
# while `azzio wallpaper --decades.png` sticks across a re-login. Under ~/.config
# (XDG_CONFIG_HOME the session exports). Kept in lock-step with the command line interface's _state_file() (a
# test pins the two). The session reads it via the $HOME shell variable (per-user via skel).
WALLPAPER_POINTER_FILE = f"{HOME}/.config/azzio/wallpaper"


# The live file manager sidebar sync helper, launched (with --watch) from the OpenBox autostart so
# additions to the home directory show up in the file manager's shortcuts pane at runtime (PROMPT). Kept
# in lock-step with packages/file_manager/live_sidebar.SYNC_SCRIPT_DEST (a test pins them equal);
# this constant is the single name the autostart refers to it by, held here to avoid importing
# the file_manager package into openbox (mirrors how AZZIO_OSD_SYSTEM_PATH is handled).
FILE_MANAGER_SIDEBAR_SYNC = "/usr/local/lib/azzio/azzio-sidebar-sync"


# --- Application menu wiring (single source of truth in application_menu.py) --
# OUR menu is the whole shell now. It ships as a resident daemon (built once, kept
# hidden) so opening it is instant; the Super key and the OpenBox root menu both run
# the launcher (/usr/local/bin/azzio-application-menu) which signals that daemon.
from packages.application_menu import application_menu as _app_menu  # noqa: E402  (the menu is OUR package)

MENU_LAUNCHER = _app_menu.MENU_LAUNCHER_SYSTEM_PATH
MENU_DAEMON_BIN = _app_menu.MENU_DAEMON_BIN_SYSTEM_PATH

# The Azzio window switcher (alt-tab): OUR replacement for OpenBox's built-in
# NextWindow list -- a horizontal, Windows-like overlay of LIVE window thumbnails. Also a
# resident C/GTK3 daemon (built once, kept hidden); rc.xml binds A-Tab/A-S-Tab to the
# launcher (/usr/local/bin/azzio-window-switcher --next/--prev) which signals it.
from packages.window_switcher import window_switcher as _switcher  # noqa: E402  (the switcher is OUR package)

SWITCHER_LAUNCHER = _switcher.SWITCHER_LAUNCHER_SYSTEM_PATH
SWITCHER_DAEMON_BIN = _switcher.SWITCHER_DAEMON_BIN_SYSTEM_PATH


# --- The compositor (picom) config -----------------------------------------------------
# picom is REQUIRED by the window switcher (it redirects every window to an off-screen pixmap
# so covered/minimized windows still have LIVE content to thumbnail). But picom's PACKAGED
# default config, /etc/xdg/picom.conf, ships two effects the user never asked for and
# explicitly wants gone:
#   * `fading = true`     -- windows FADE IN/OUT when they open and close. This is the
#                            "applications fade in and out" the user reported; verified as the
#                            /etc/xdg/picom.conf default on the live medium (picom v13).
#   * `frame-opacity = 0.9` -- the window FRAME (the OpenBox titlebar) is drawn 90% opaque, so
#                            the wallpaper bleeds through it. That is the "openbox titlebar is
#                            transparent" the user reported -- it is NOT in our OpenBox themerc
#                            (OpenBox themes have no alpha); picom's frame-opacity is the source.
# Rather than depend on (and fight) the packaged /etc/xdg/picom.conf, we ship OUR OWN picom
# config and point the autostart's picom at it with `--config`. It turns BOTH effects off:
# fading disabled (no open/close fade) and every opacity left FULLY OPAQUE (opaque titlebar).
# Everything else is picom's plain default (a bare vsync'd compositor -- exactly what the
# switcher's XComposite capture needs, no eye-candy). Root-owned system path so both the live
# and installed sessions read the same file (the autostart is shared).
PICOM_CONFIG_PATH = "/etc/xdg/azzio-picom.conf"


def picom_conf() -> str:
    """OUR picom compositor config (PICOM_CONFIG_PATH). A minimal, EFFECT-FREE compositor:
    it exists only so the window switcher's XComposite capture has a redirecting compositor.

    It DISABLES the two packaged-default effects the user reported and asked to remove:
      * fading (`fading = false`)   -- no fade-in/out when an application opens or closes.
      * frame opacity (`frame-opacity = 1.0`, plus inactive/active-opacity 1.0) -- the OpenBox
        titlebar/frame is drawn FULLY OPAQUE, so the wallpaper no longer shows through it.
    Passed to picom via `--config` from the OpenBox autostart, so picom does NOT fall back to
    the packaged /etc/xdg/picom.conf (whose `fading = true` / `frame-opacity = 0.9` are the two
    reported bugs). Root-owned; shared by the live and installed autostart."""
    return (
        "# Azzio picom (compositor) config. Generated by packages/openbox (edit the\n"
        "# Python, not this file). picom is REQUIRED by the Azzio window switcher so its\n"
        "# XComposite capture can read LIVE pixels of every window; this config keeps picom a\n"
        "# BARE compositor with NO eye-candy. The packaged /etc/xdg/picom.conf default turns on\n"
        "# window fading and a 0.9 frame opacity (a see-through titlebar) -- BOTH are disabled\n"
        "# here, which is exactly why the autostart runs picom with `--config` pointed at THIS\n"
        "# file instead of letting it fall back to the packaged default.\n"
        "\n"
        "# No fade-in/out when a window opens or closes (the reported 'applications fade in and\n"
        "# out'). Disabling `fading` is the whole fix; the fade-*-step values are left at their\n"
        "# defaults but never used while fading is off.\n"
        "fading = false;\n"
        "\n"
        "# FULLY OPAQUE everywhere (the reported 'openbox titlebar is transparent'). frame-opacity\n"
        "# 1.0 makes the OpenBox titlebar/frame solid (the packaged default 0.9 let the wallpaper\n"
        "# bleed through it); active/inactive-opacity 1.0 keep window contents fully opaque too.\n"
        "frame-opacity = 1.0;\n"
        "inactive-opacity = 1.0;\n"
        "active-opacity = 1.0;\n"
        "inactive-opacity-override = false;\n"
        "\n"
        "# No shadows and no blur -- a plain compositor. (Shadows/blur are pure cosmetics the\n"
        "# switcher does not need; leaving them off keeps the desktop looking exactly as it did\n"
        "# before a compositor was running, just with redirection available for thumbnails.)\n"
        "shadow = false;\n"
        "\n"
        "# Tear-free painting; unredirect a single fullscreen window (e.g. a fullscreen video or\n"
        "# the hypervisor guest) so it bypasses the compositor for full performance.\n"
        "vsync = true;\n"
        "unredir-if-possible = true;\n"
        "backend = \"glx\";\n"
    )


# --- 1. ~/.xinitrc ----------------------------------------------------------
def xinitrc() -> str:
    """Run by `startx` (see ~/.bash_profile). Paints the wallpaper onto the X root
    BEFORE handing the session to OpenBox so nothing flashes, then execs the OpenBox
    X11 session.

    `openbox-session` (from the openbox package) is BOTH the window manager and the
    session bootstrap: it exports the OpenBox environment, runs
    ~/.config/openbox/autostart (wallpaper repaint, menu daemon, xcape, keyboard,
    installer) and reads rc.xml. logind sets XDG_SESSION_TYPE=x11; we export
    XDG_CURRENT_DESKTOP=openbox so XDG-aware tools classify the session correctly.

    The `feh --bg-fill <wallpaper>` line makes the FIRST visible frame the wallpaper
    (feh owns the root pixmap under OpenBox); the autostart repaints the identical
    image, so there is no cyan/black flash. `--no-fehbg` keeps feh from writing a
    ~/.fehbg helper we do not use."""
    return """\
#!/bin/sh
# ~/.xinitrc -- started by `startx` (see ~/.bash_profile). Hands the X session to
# OpenBox. Keep this minimal: per-app launches live in the OpenBox autostart.

# Make sure user-dir XDG paths resolve for anything the session spawns.
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"

# Classify the session for XDG-aware tools (autostart .desktop OnlyShowIn, etc.).
export XDG_CURRENT_DESKTOP=openbox
export DESKTOP_SESSION=openbox

# Load the X resource DB (the GLOBAL SCALE backbone: Xft.dpi + Xcursor.size, derived from the
# single scale in packages/openbox/scale). Every X client reads Xft.dpi as the screen DPI, so this
# is what makes fractional scaling (e.g. 1.35) actually take effect on X11 (GDK_SCALE is
# integer-only). `azzio display scale` rewrites ~/.Xresources and re-runs `xrdb -merge`.
[ -x /usr/bin/xrdb ] && [ -f "$HOME/.Xresources" ] && xrdb -merge "$HOME/.Xresources"

# Paint the wallpaper onto the X root FIRST so the first visible frame is the
# wallpaper, not a solid color. The OpenBox autostart repaints the same image moments
# later (identical pixels -> no visible transition, no flash). feh is shipped in the
# manifest; it owns the root pixmap under OpenBox (OpenBox draws no wallpaper itself).
# The image honours the per-user `azzio wallpaper` pointer, falling back to "years".
""" + _feh_wallpaper_line() + """

# Hand DISPLAY/XAUTHORITY to the systemd USER manager and the D-Bus activation
# environment BEFORE OpenBox starts. Arch's stock startx sources
# /etc/X11/xinit/xinitrc.d/50-systemd-user.sh (which runs exactly these two lines) to do
# this, but OUR ~/.xinitrc replaces the stock xinitrc wholesale and never sources that
# drop-in dir -- so without this the user manager has NO DISPLAY. That silently breaks
# every D-Bus/systemd-ACTIVATED user service that needs X: chiefly xdg-desktop-portal-gtk
# (Type=dbus), which then exits 1 with "cannot open display" the moment an app calls the
# FileChooser portal. Librewolf's "Save image as" routes through that portal (auto mode,
# the portal FRONTEND is up), so with no backend the SAVE DIALOG NEVER APPEARS -- the
# reported "Save image as doesn't work". Seeding the env here lets the backend start on
# demand, so the file picker works. XAUTHORITY is exported by startx; guard the
# dbus-update tool so a missing binary never breaks startup.
systemctl --user import-environment DISPLAY XAUTHORITY
command -v dbus-update-activation-environment >/dev/null 2>&1 && \\
    dbus-update-activation-environment DISPLAY XAUTHORITY

# Replace this shell with the OpenBox X11 session; when OpenBox exits, X exits and
# control returns to the login shell (which, per bash_profile, logs out the tty).
exec openbox-session
"""


# --- 2. /home/main/.bash_profile snippet ------------------------------------
def bash_profile_startx() -> str:
    """Appended to /home/main/.bash_profile. On the FIRST virtual terminal only (and
    only when not already in X) it replaces the login shell with startx, so the
    autologin drops straight into the graphical session. On any other VT or an SSH
    login $DISPLAY is set or $(tty) != /dev/tty1, so the guard is false and you get a
    normal shell -- important for rescue/maintenance use of the ISO.

    KEYBOARD PROTOCOL RESET (the reported "ssh outputs 7;3u / 7;5u junk when I hold
    ctrl/alt" bug). Modern terminals -- kitty in particular, the one Azzio ships and the
    one the operator sshes FROM -- speak the "progressive"/CSI-u keyboard protocol
    (a.k.a. the kitty keyboard protocol / xterm modifyOtherKeys): a modified keypress is
    reported as `ESC [ <codepoint> ; <modifier> u` instead of a legacy control byte.
    When the terminal has that mode enabled and the program on the OTHER end does NOT
    understand it, the escape's `ESC [` prefix is swallowed by the line editor and only
    the tail leaks to the screen as literal text -- `7;3u` (Alt held: mod 1+2) and
    `7;5u` (Ctrl held: mod 1+4). Over ssh that is exactly what happens: the guest's login
    shell is plain bash, whose readline (checked: bash 5.3 / readline 8.3 emit and decode
    NO `ESC[>...u`) cannot parse CSI-u, and the shipped `xterm-kitty` terminfo advertises
    no such capability -- yet the enabled mode rides through ssh with $TERM, so every
    Ctrl-/Alt- chord (ctrl+u, alt+space, arrow-key navigation, backspace) spews the tail.

    The robust, client-agnostic fix is for the guest's interactive shell to tell the
    terminal "I speak LEGACY keys only": push an empty keyboard-protocol flag set
    (`ESC[>0u`, kitty "disable all enhancements") and turn xterm modifyOtherKeys off
    (`ESC[>4;0m`). We do it for every INTERACTIVE shell (an ssh/rescue login is
    interactive; the tty1 autologin `exec startx`s below before any prompt, so this is a
    no-op there) and RE-ASSERT it from PROMPT_COMMAND, so a full-screen TUI that turned
    the protocol back on and then died without restoring it cannot leave the next prompt
    spewing `...u` garbage. This lives in the Azzio-owned .bash_profile (there is no stock
    one) so the fix ships on the guest regardless of whether the operator connects via
    `hypervisor ssh` or a bare `ssh -p <port>`."""
    return """\
# ~/.bash_profile -- Azzio live session bootstrap.
# Source .bashrc for interactive niceties if present.
[[ -f ~/.bashrc ]] && . ~/.bashrc

# LEGACY KEYBOARD for interactive shells (fixes the ssh "7;3u / 7;5u" junk on Ctrl/Alt
# chords): ask the terminal to stop sending kitty/CSI-u + modifyOtherKeys key reports the
# plain-bash line editor cannot decode. ESC[>0u pushes an empty kitty-keyboard flag set
# (disable all progressive enhancements); ESC[>4;0m turns xterm modifyOtherKeys off. Only
# meaningful on a terminal (guarded on interactive + a tty on fd 1), and re-asserted every
# prompt so a TUI that re-enabled the protocol and exited uncleanly cannot leave the shell
# spewing `...u`. No-op on the tty1 autologin: it exec-startx'es below before any prompt.
if [[ $- == *i* ]]; then
    _azzio_legacy_keys() { [[ -t 1 ]] && printf '\\e[>0u\\e[>4;0m'; }
    _azzio_legacy_keys
    case "$PROMPT_COMMAND" in
        *_azzio_legacy_keys*) : ;;                       # already installed (re-sourced shell)
        "") PROMPT_COMMAND=_azzio_legacy_keys ;;
        *) PROMPT_COMMAND="_azzio_legacy_keys;$PROMPT_COMMAND" ;;
    esac
fi

# Auto-start the graphical live session on tty1 login ONLY. On other VTs or over
# SSH this is skipped, leaving a plain login shell for rescue/maintenance.
# We key off the controlling terminal ($(tty) == /dev/tty1) rather than
# $XDG_VTNR: the latter only exists when pam_systemd ran and set it, so on a bare
# agetty autologin it can be empty, making `-eq 1` fail. The tty check is always
# correct for the tty1 autologin and has no such dependency.
if [[ -z $DISPLAY && "$(tty)" == /dev/tty1 ]]; then
    exec startx
fi
"""


# --- 2b. ~/.themes/Azzio/openbox-3/themerc (custom OpenBox theme) ----------
# The window titlebar ("that cyan'ish colored bar") is drawn by the OpenBox THEME, not
# rc.xml. Stock Clearlooks makes a THIN bar: its title height is font(8pt) + a tiny
# padding.height(2) top and bottom, and OpenBox sizes the min/max/close BUTTONS to that
# same label height -- so the whole bar (and its buttons) come out small.
#
# The bar had been grown to ~1.5x stock; the user then found the topbar TOO BIG and asked
# to cut it 25%, drop the two-tone gradient for ONE flat colour, and later to grow it back
# ~10% (the +10% topbar) with WHITE button borders and a touch more space between buttons.
# Most recently: DOUBLE the gap between the min/max/close buttons, highlight a button in the
# signature cyan on hover, and nudge the button icons slightly bigger. So Azzio ships its own
# theme, "Azzio": the stock Clearlooks openbox-3 themerc with the size-driving fields tuned and
# the titlebar/buttons flattened. It is a fresh theme dir (not an edit of the packaged
# Clearlooks) so the airootfs overlay owns it and a package update to openbox cannot revert it:
#   * padding.height 2 -> 5     (top+bottom padding around the label)
#   * padding.width  3 -> 10    (space between the title BUTTONS; DOUBLED from 5 for the 2x gap)
#   * window.handle.width 3->0  (REMOVE the bottom handle -- see below)
#
# SIZE: the dominant half of the height is the title FONT set in rc.xml's <theme>. It was cut
# 25% (pt 12 -> 9), grown back +10% to pt 10, then nudged to pt 11 (TITLE_FONT_SIZE /
# TOPBAR_GROWTH == 1.20) for slightly bigger button icons. Since OpenBox sizes the min/max/close
# buttons (and the glyphs inside them) to the label height, a single font bump grows the whole
# topbar -- bar, buttons, AND button icons -- together, which is how "slightly bigger button
# icons" is achieved (OpenBox has no separate button-glyph-size field). padding.width adds the
# extra horizontal gap between the buttons ("distance between the buttons").
#
# BUTTON GAP (2x): padding.width is DOUBLED (5 -> 10) so the distance between the min/max/close
# buttons is twice as large. titleLayout is NLIMC (icon, Label, iconify, maximize, close): the
# Label is a stretchy spring in the MIDDLE, so it swallows the extra icon<->label space and the
# doubled padding reads almost entirely as a wider gap between the three right-hand buttons. The
# close (exit) button stays rightmost and min/max slide left of it -- "keep exit where it is,
# move the others left".
#
# WHITE BUTTON BORDERS: the min/max/close buttons carry a 1px border via
# window.*.button.*.bg.border.color; it is set to #ffffff (both resting and hover) so the
# buttons have the white outline the user asked for, in both the dark and light themes.
#
# HOVER = SIGNATURE CYAN: the button HOVER fill (window.active.button.hover.bg.color) is the
# signature cyan #06b8fd (the logo/OSD/menu accent), so hovering a min/max/close button lights
# it up in the one signature colour to indicate what is about to be pressed. The white hover
# border frames that cyan highlight.
#
# ONE FLAT COLOUR: the titlebar + its buttons were a splitvertical GRADIENT (top colour ->
# a darker bottom split) -- the "two color split" the user saw. They are now `Flat Solid`,
# filled with the gradient's old BOTTOM colour (title_bg_to_split), so the whole bar is a
# single consistent colour. The buttons take their own bottom split, hover is flat signature
# cyan, pressed was already flat. Flat Solid ignores the Raised bg.highlight/shadow bevel, so
# the bar renders as one true flat colour. Menu/OSD are NOT the topbar and keep their gradients.
#
# THE BOTTOM "THIN WHITE BAR": OpenBox draws a HANDLE -- a full-width strip along the
# BOTTOM edge of every decorated window -- whose height is window.handle.width and whose
# fill is window.*.handle.bg.color (#eaebec, a near-white). Stock Clearlooks makes it a
# few px tall; on Azzio it read as an "unnecessary thin white bar under a window". The
# user asked for it gone, so window.handle.width is set to 0: a zero-height handle draws
# NOTHING (the near-white strip disappears). Resizing is UNAFFECTED -- the rc.xml Bottom /
# Left / Right / corner mouse contexts resize on the window's invisible border edges
# regardless of the visible handle, and keepBorder keeps the 1px frame on maximized
# windows -- so only the cosmetic strip is removed, not the ability to drag-resize.
# Azzio ships TWO OpenBox titlebar themes -- a LIGHT one ("Azzio", the classic
# Clearlooks-cyan look) and a DARK one ("Azzio-Dark", the default). Both are generated
# from openbox_theme_rc(dark) below: identical GEOMETRY (the grown titlebar + 2x button gap +
# white button borders + no bottom handle + flat colour), only the colour palette differs (the
# signature-cyan hover is shared). rc.xml's <theme><name>
# selects one, and `azzio theme --dark|--white` rewrites that name + `openbox
# --reconfigure`s. Dark is the out-of-the-box default (rc.xml ships <name>Azzio-Dark</name>).
OPENBOX_THEME_NAME = "Azzio"            # the LIGHT theme name (classic Clearlooks-cyan)
OPENBOX_THEME_NAME_DARK = "Azzio-Dark"  # the DARK theme name (the default)
OPENBOX_THEME_DIR = f"{HOME}/.themes/{OPENBOX_THEME_NAME}/openbox-3"
OPENBOX_THEME_THEMERC = f"{OPENBOX_THEME_DIR}/themerc"
OPENBOX_THEME_DIR_DARK = f"{HOME}/.themes/{OPENBOX_THEME_NAME_DARK}/openbox-3"
OPENBOX_THEME_THEMERC_DARK = f"{OPENBOX_THEME_DIR_DARK}/themerc"
# The theme OpenBox uses out of the box (dark is the Azzio default). rc.xml names it.
OPENBOX_THEME_DEFAULT = OPENBOX_THEME_NAME_DARK

# The two padding fields (in px) that set the titlebar height, and the resize-handle
# width. The bar was SHRUNK 25% from the earlier ~1.5x size, then grown back via the title
# FONT (see TITLE_FONT_SIZE / TOPBAR_GROWTH), which is what carries the height bump;
# padding.height stays 5 so the height growth rides on the font, not this.
#   * padding.height 5  -- top+bottom label padding; the growth rides on the font, not this.
#   * padding.width  10 -- HORIZONTAL breathing room between the titlebar elements, i.e. the
#     GAP BETWEEN THE min/max/close BUTTONS. DOUBLED 5 -> 10 for the "make the distance between
#     the buttons twice as large" ask (OpenBox has no separate button-gap field; padding.width
#     is the spacing between adjacent title buttons/label/icon, and the stretchy middle label
#     means the doubling shows up as the wider gap between the three right-hand buttons).
# The handle width is 0 to REMOVE the near-white bottom handle bar entirely (the "thin white
# bar under a window" the user asked to drop); resizing is unaffected (the rc.xml edge/corner
# mouse contexts do not depend on the visible handle). Shared by BOTH the light and dark
# themes (only colours differ between them).
OPENBOX_THEME_PADDING_HEIGHT = 5    # unchanged; the topbar growth rides on the title font
OPENBOX_THEME_PADDING_WIDTH = 10    # was 5; DOUBLED so the gap between the title buttons is 2x
OPENBOX_THEME_HANDLE_WIDTH = 0      # stock Clearlooks: 3 -> 0 removes the bottom bar

# The LIGHT palette: the stock Clearlooks colours (unchanged -- this is the classic cyan
# "white theme" look). The DARK palette: a coherent near-black set matching the
# Azzio application menu (bg #0a0f14 / surface #121a21 / text #dee4ea) with the logo
# cyan (#06b8fd) for the active menu item -- the media OSD identity, so the whole shell
# reads dark and cyan.
# openbox_theme_rc(dark) picks one; every field below has a light and a dark value.
_OB_LIGHT = {
    "menu_border": "#aaaaaa",
    "menu_title_bg": "#E6E7E6", "menu_title_text": "#111111",
    "menu_items_bg": "#ffffff", "menu_items_text": "#111111",
    "menu_items_disabled": "#aaaaaa",
    "menu_active_bg": "#97b8e2", "menu_active_bg_split": "#a8c5e9",
    "menu_active_bg_to": "#91b3de", "menu_active_bg_to_split": "#80a7d6",
    "menu_active_border": "#4b6e99", "menu_active_text": "#ffffff",
    "menu_sep": "#aaaaaa",
    "handle_bg": "#eaebec", "grip_bg": "#eaebec",
    "win_border": "#585a5d",
    # active_sep is the FLAT 1px line OpenBox draws at the titlebar's BOTTOM edge (between
    # titlebar and client). The titlebar is a FLAT SOLID fill of title_bg_to_split (#7AA1D1),
    # so the separator is pinned to that same colour and is invisible (same "drop the thin bar
    # under the window" intent as window.handle.width 0).
    # The *_to_split fields are the single flat fill colours used by the titlebar/buttons; the
    # old gradient top/mid stops were dropped when the bar was flattened to one colour.
    "active_sep": "#7AA1D1",
    "title_bg_to_split": "#7AA1D1",
    "active_text": "#ffffff",
    "abtn_bg_to_split": "#769FD0",
    # WHITE button border in the light theme too (same "white outline on the buttons"
    # request); resting + hover both white so the outline never flips colour on hover.
    "abtn_border": "#ffffff", "abtn_image": "#F4F5F6",
    # HOVER fill == the SIGNATURE cyan (#06b8fd), same as the dark theme, so the hover
    # highlight is the one signature colour in both themes (the new prompt). Was #8caede.
    "abtn_hover_bg_to_split": "#06b8fd",
    "abtn_hover_border": "#ffffff", "abtn_hover_image": "#ffffff",
    "abtn_pressed_bg": "#7aa1d2",
    "inactive_sep": "#96999d",
    "ititle_bg_to_split": "#D5D3D1",
    "inactive_text": "#70747d",
    "ibtn_bg_to_split": "#E9E7E6",
    "ibtn_border": "#928F8B", "ibtn_image": "#6D6C6C",
    "osd_border": "#aaaaaa",
    "osd_bg": "#F0EFEE", "osd_bg_split": "#f5f5f4",
    "osd_bg_to": "#EAEBEC", "osd_bg_to_split": "#E7E5E4",
    "osd_bg_border": "#ffffff",
    "osd_label_bg": "#efefef", "osd_label_border": "#9c9e9c", "osd_label_text": "#444",
    "osd_ilabel_text": "#70747d",
    "osd_hi_bg": "#9ebde5", "osd_hi_bg_to": "#749dcf",
    "osd_unhi_bg": "#BABDB6", "osd_unhi_bg_to": "#efefef",
}
_OB_DARK = {
    "menu_border": "#06090c",
    "menu_title_bg": "#121a21", "menu_title_text": "#dee4ea",
    "menu_items_bg": "#0a0f14", "menu_items_text": "#dee4ea",
    "menu_items_disabled": "#5a6b76",
    "menu_active_bg": "#06b8fd", "menu_active_bg_split": "#3fc6fd",
    "menu_active_bg_to": "#04a8e8", "menu_active_bg_to_split": "#0499d6",
    "menu_active_border": "#046a8f", "menu_active_text": "#ffffff",
    "menu_sep": "#20303a",
    "handle_bg": "#0a0f14", "grip_bg": "#0a0f14",
    "win_border": "#05080a",
    # active_sep is the FLAT 1px line OpenBox draws at the titlebar's BOTTOM edge (between
    # titlebar and client). It USED to be #046a8f, a stray bright-cyan bar under a focused,
    # non-maximized window (the reported visual bug). The titlebar is now a FLAT SOLID fill of
    # title_bg_to_split (#0a0f14), so the separator is pinned to that same colour; the line then
    # draws the same colour as the titlebar pixel above it and is invisible (same "drop the thin
    # bar under the window" intent as window.handle.width 0).
    # The *_to_split fields are the single flat fill colours used by the titlebar/buttons; the
    # old gradient top/mid stops were dropped when the bar was flattened to one colour.
    "active_sep": "#0a0f14",
    "title_bg_to_split": "#0a0f14",
    "active_text": "#ffffff",
    "abtn_bg_to_split": "#0a0f14",
    # WHITE button border (the user asked the min/max/close buttons to have a white
    # outline). Both the resting AND hover border are pure white so the outline stays
    # white when the pointer is over a button (a coloured hover border would break the
    # "white border" the user wants); only the button FILL changes on hover.
    "abtn_border": "#ffffff", "abtn_image": "#dee4ea",
    # HOVER fill == the SIGNATURE cyan (#06b8fd, the logo/OSD/menu accent): hovering a
    # min/max/close button lights it up in the one signature colour so it is clear what is
    # about to be pressed (the new prompt). Was #0499d6, a dimmer non-signature cyan.
    "abtn_hover_bg_to_split": "#06b8fd",
    "abtn_hover_border": "#ffffff", "abtn_hover_image": "#ffffff",
    "abtn_pressed_bg": "#0499d6",
    "inactive_sep": "#05080a",
    "ititle_bg_to_split": "#0a1015",
    "inactive_text": "#8b98a3",
    "ibtn_bg_to_split": "#0a1015",
    "ibtn_border": "#05080a", "ibtn_image": "#8b98a3",
    "osd_border": "#06090c",
    "osd_bg": "#0a0f14", "osd_bg_split": "#121a21",
    "osd_bg_to": "#0d141a", "osd_bg_to_split": "#0a1015",
    "osd_bg_border": "#05080a",
    "osd_label_bg": "#121a21", "osd_label_border": "#05080a", "osd_label_text": "#dee4ea",
    "osd_ilabel_text": "#8b98a3",
    "osd_hi_bg": "#06b8fd", "osd_hi_bg_to": "#04a8e8",
    "osd_unhi_bg": "#20303a", "osd_unhi_bg_to": "#121a21",
}


def openbox_theme_rc(dark: bool = True) -> str:
    """One Azzio OpenBox themerc -- the DARK palette (default) when dark=True, else the
    LIGHT (classic Clearlooks-cyan) palette.

    Both share one GEOMETRY -- the titlebar cut 25% then grown (title <font> pt 11 in rc.xml,
    see TITLE_FONT_SIZE/TOPBAR_GROWTH == 1.20: the +10% topbar plus a nudge for slightly bigger
    button icons), with the gap between the min/max/close buttons DOUBLED (padding.width 10) and
    the bottom resize handle REMOVED (window.handle.width 0, so the near-white bottom bar does
    not draw). Because OpenBox sizes the buttons (and their glyphs) to the label, the font grows
    bar, buttons, and icons together. The buttons carry a WHITE 1px border and light up in the
    SIGNATURE cyan (#06b8fd) on hover. The titlebar and its buttons are a SINGLE FLAT colour (the
    old gradient's bottom split), no two-tone split. ONLY the resting colours differ between dark
    and light (see _OB_DARK / _OB_LIGHT; the signature-cyan hover is shared); the light theme
    keeps the Clearlooks-cyan bottom colour.

    Shipped to ~/.themes/<name>/openbox-3/themerc (a user theme search path OpenBox scans
    alongside /usr/share/themes) and mirrored into /etc/skel so the installed user inherits
    both themes. rc.xml names the dark one by default; `azzio theme` swaps between them."""
    c = _OB_DARK if dark else _OB_LIGHT
    variant = "DARK (the default)" if dark else "LIGHT (classic Clearlooks-cyan)"
    return f"""\
# Azzio OpenBox theme -- {variant}. Flat-colour titlebar, WHITE button borders, signature-cyan
# button hover, NO bottom handle. Generated by packages.openbox (edit the Python, not this file).
# Geometry is stock Clearlooks with padding tuned (padding.width 10 DOUBLES the button gap),
# window.handle.width 0 (removes the bottom bar), the buttons given a white border and a
# signature-cyan hover, and the titlebar + buttons one Flat Solid colour; only the resting colour
# palette differs between the dark and light Azzio themes. The title FONT size (the topbar +
# button-icon size lever) is set in rc.xml's <theme>.

# Fonts (halos)
*.font: shadow=n
window.active.label.text.font:shadow=y:shadowtint=30:shadowoffset=1
window.inactive.label.text.font:shadow=y:shadowtint=00:shadowoffset=0
menu.items.font:shadow=y:shadowtint=0:shadowoffset=1

# general stuff -- padding.height/width set for the titlebar (stock was 2 / 3; padding.width
# 10 DOUBLES the gap between the title buttons), handle width set to 0 to REMOVE the near-white
# bottom handle bar (was 3).
border.width: 1
padding.width: {OPENBOX_THEME_PADDING_WIDTH}
padding.height: {OPENBOX_THEME_PADDING_HEIGHT}
window.handle.width: {OPENBOX_THEME_HANDLE_WIDTH}
window.client.padding.width: 0
menu.overlap: 2
*.justify: center

# shadows
*.bg.highlight: 50
*.bg.shadow:    05

window.active.title.bg.highlight: 35
window.active.title.bg.shadow:    05

window.inactive.title.bg.highlight: 30
window.inactive.title.bg.shadow:    05

window.*.grip.bg.highlight: 50
window.*.grip.bg.shadow:    30

window.*.handle.bg.highlight: 50
window.*.handle.bg.shadow:    30

# Menu settings
menu.border.color: {c["menu_border"]}
menu.border.width: 1

menu.title.bg: solid flat
menu.title.bg.color: {c["menu_title_bg"]}
menu.title.text.color: {c["menu_title_text"]}

menu.items.bg: Flat Solid
menu.items.bg.color: {c["menu_items_bg"]}
menu.items.text.color: {c["menu_items_text"]}
menu.items.disabled.text.color: {c["menu_items_disabled"]}

menu.items.active.bg: Flat Gradient splitvertical border

menu.items.active.bg.color: {c["menu_active_bg"]}
menu.items.active.bg.color.splitTo: {c["menu_active_bg_split"]}

menu.items.active.bg.colorTo: {c["menu_active_bg_to"]}
menu.items.active.bg.colorTo.splitTo: {c["menu_active_bg_to_split"]}
menu.items.active.bg.border.color: {c["menu_active_border"]}
menu.items.active.text.color: {c["menu_active_text"]}

menu.separator.width: 1
menu.separator.padding.width: 0
menu.separator.padding.height: 3
menu.separator.color: {c["menu_sep"]}

# handles
window.*.handle.bg: Raised solid
window.*.handle.bg.color: {c["handle_bg"]}

window.*.grip.bg: Raised solid
window.*.grip.bg.color: {c["grip_bg"]}

# Active
window.*.border.color: {c["win_border"]}

window.active.title.separator.color: {c["active_sep"]}

# ONE FLAT COLOUR (was a splitvertical gradient): the titlebar is a solid fill of the
# gradient's old BOTTOM colour (title_bg_to_split), so there is no top/bottom two-tone
# split -- the whole bar is one consistent colour. Flat Solid ignores the Raised
# bg.highlight/shadow bevel above, so it renders truly flat.
*.title.bg: Flat Solid
*.title.bg.color: {c["title_bg_to_split"]}

window.active.label.bg: Parentrelative
window.active.label.text.color: {c["active_text"]}

# Buttons flattened to match the bar: a solid fill of each state's old bottom colour, so
# the min/max/close targets read as the same flat colour as the titlebar (their glyphs,
# image.color, still draw on top).
window.active.button.*.bg: Flat Solid Border

window.active.button.*.bg.color: {c["abtn_bg_to_split"]}

window.active.button.*.bg.border.color: {c["abtn_border"]}
window.active.button.*.image.color: {c["abtn_image"]}

window.active.button.hover.bg: Flat Solid Border
window.active.button.hover.bg.color: {c["abtn_hover_bg_to_split"]}
window.active.button.hover.bg.border.color: {c["abtn_hover_border"]}
window.active.button.hover.image.color: {c["abtn_hover_image"]}

window.active.button.pressed.bg: Flat solid Border
window.active.button.pressed.bg.color: {c["abtn_pressed_bg"]}

# inactive -- flattened to its own bottom colour too, so an unfocused window is the same
# single-colour bar as a focused one (just the dimmer inactive palette).
window.inactive.title.separator.color: {c["inactive_sep"]}

window.inactive.title.bg: Flat Solid
window.inactive.title.bg.color: {c["ititle_bg_to_split"]}

window.inactive.label.bg: Parentrelative
window.inactive.label.text.color: {c["inactive_text"]}

window.inactive.button.*.bg: Flat Solid Border
window.inactive.button.*.bg.color: {c["ibtn_bg_to_split"]}
window.inactive.button.*.bg.border.color: {c["ibtn_border"]}
window.inactive.button.*.image.color: {c["ibtn_image"]}

# osd
osd.border.width: 1
osd.border.color:  {c["osd_border"]}

osd.bg: flat border gradient splitvertical
osd.bg.color: {c["osd_bg"]}
osd.bg.color.splitto: {c["osd_bg_split"]}
osd.bg.colorTo: {c["osd_bg_to"]}
osd.bg.colorTo.splitto: {c["osd_bg_to_split"]}

osd.bg.border.color: {c["osd_bg_border"]}

osd.active.label.bg: parentrelative
osd.active.label.bg.color: {c["osd_label_bg"]}
osd.active.label.bg.border.color: {c["osd_label_border"]}
osd.active.label.text.color: {c["osd_label_text"]}

osd.inactive.label.bg: parentrelative
osd.inactive.label.text.color: {c["osd_ilabel_text"]}

osd.hilight.bg: flat vertical gradient
osd.hilight.bg.color: {c["osd_hi_bg"]}
osd.hilight.bg.colorTo: {c["osd_hi_bg_to"]}
osd.unhilight.bg: flat vertical gradient
osd.unhilight.bg.color: {c["osd_unhi_bg"]}
osd.unhilight.bg.colorTo: {c["osd_unhi_bg_to"]}
"""


def openbox_theme_rc_dark() -> str:
    """PLAN builder for the DARK Azzio OpenBox theme (the default)."""
    return openbox_theme_rc(dark=True)


def openbox_theme_rc_light() -> str:
    """PLAN builder for the LIGHT Azzio OpenBox theme (classic Clearlooks-cyan)."""
    return openbox_theme_rc(dark=False)


# --- 2c. System theme DEFAULT: the freedesktop / GTK dark standard ----------
# Azzio ships DARK as the default, using the EXISTING freedesktop / GTK standard so any
# downloaded app that honours it is configured for free. Three layers, all defaulting dark:
#   * The GTK theme files (gtk-3.0/gtk-4.0 settings.ini + ~/.gtkrc-2.0): Adwaita-dark +
#     gtk-application-prefer-dark-theme=1. GTK2/3/4 apps read these at startup. HOME files
#     (skel-mirrored). These are the DEFAULT; `azzio theme --white` rewrites them to light.
#   * The dconf SYSTEM default for org.gnome.desktop.interface color-scheme='prefer-dark'
#     (the freedesktop "appearance" signal GTK4/libadwaita/portal apps read). Shipped as a
#     /etc/dconf keyfile + profile and compiled by `dconf update` in the customize hook
#     (post-pacstrap, where dconf exists). A per-user `gsettings set` from `azzio theme`
#     OVERRIDES this system default and persists, so a user who picks white keeps white.
# These builders MUST stay byte-for-byte in lock-step with the azzio command line interface's theme.py dark
# output (a test bundles the command line interface and asserts equality) so the shipped default and a later
# `azzio theme --dark` produce identical files.
GTK3_SETTINGS_PATH = f"{HOME}/.config/gtk-3.0/settings.ini"
GTK4_SETTINGS_PATH = f"{HOME}/.config/gtk-4.0/settings.ini"
GTKRC2_PATH = f"{HOME}/.gtkrc-2.0"
# The dconf system-default keyfile + profile + the marker the customize hook greps for.
DCONF_THEME_KEYFILE_PATH = "/etc/dconf/db/local.d/00-azzio-theme"
DCONF_PROFILE_USER_PATH = "/etc/dconf/profile/user"


def gtk3_settings_ini_default() -> str:
    """~/.config/gtk-3.0/settings.ini shipped default (DARK). Matches theme.gtk3_settings_ini(True)."""
    return (
        "# Azzio GTK3 theme. Generated by `azzio theme` (edit via the command, not\n"
        "# this file). gtk-application-prefer-dark-theme is the GTK3 dark switch.\n"
        "[Settings]\n"
        "gtk-theme-name=Adwaita-dark\n"
        "gtk-application-prefer-dark-theme=1\n"
        # The Azzio icon theme (inherits Adwaita) -- our folder/file/toolbar icons win, the rest
        # fall through to Adwaita. Shared by both light and dark (icon themes are not dark/light).
        "gtk-icon-theme-name=Azzio\n"
        # Show icons in menus (PROMPT file manager batch item 6). GTK3's gtk-menu-images defaults to
        # FALSE, which is why the file manager's "Open With" entries render without their app icons; setting
        # it true restores them (the file manager builds those items with xfce_gtk_image_menu_item, which
        # honours this GtkSetting). Harmless/desirable for every other GTK app's menus too.
        "gtk-menu-images=true\n"
    )


def gtk4_settings_ini_default() -> str:
    """~/.config/gtk-4.0/settings.ini shipped default (DARK). Matches theme.gtk4_settings_ini(True)."""
    return (
        "# Azzio GTK4 theme. Generated by `azzio theme`.\n"
        "[Settings]\n"
        "gtk-theme-name=Adwaita-dark\n"
        "gtk-application-prefer-dark-theme=1\n"
        # The Azzio icon theme (inherits Adwaita); see gtk3_settings_ini_default.
        "gtk-icon-theme-name=Azzio\n"
    )


def gtkrc2_default() -> str:
    """~/.gtkrc-2.0 shipped default (DARK). Matches theme.gtkrc2(True)."""
    return (
        "# Azzio GTK2 theme. Generated by `azzio theme`.\n"
        'gtk-theme-name="Adwaita-dark"\n'
        # The Azzio icon theme (inherits Adwaita); see gtk3_settings_ini_default.
        'gtk-icon-theme-name="Azzio"\n'
    )


def dconf_theme_keyfile() -> str:
    """/etc/dconf/db/local.d/00-azzio-theme -- the dconf SYSTEM default that makes the
    freedesktop color-scheme 'prefer-dark' for every user out of the box. A per-user
    `gsettings set` (what `azzio theme` runs) overrides it and persists. Compiled into the
    binary db by `dconf update` in the customize hook (post-pacstrap, dconf present)."""
    return (
        "# Azzio dark theme -- freedesktop color-scheme system default. Compiled by\n"
        "# `dconf update`. A per-user `gsettings set` (azzio theme) overrides this.\n"
        "[org/gnome/desktop/interface]\n"
        "color-scheme='prefer-dark'\n"
        "gtk-theme='Adwaita-dark'\n"
    )


def dconf_profile_user() -> str:
    """/etc/dconf/profile/user -- the dconf profile so the `local` system db (above) backs
    the user db. Without this profile, the system default keyfile is never consulted."""
    return (
        "# Azzio dconf profile: user db on top, the system `local` db (color-scheme\n"
        "# default) beneath it. Generated by packages.openbox.\n"
        "user-db:user\n"
        "system-db:local\n"
    )


# --- 2d. The GLOBAL SCALE, carried by the STANDARD channels (PROMPT Display/scale task) ------
# The single scale source is packages/openbox/scale; here it lands in the standard app-agnostic
# channels so every conformant app obeys it: ~/.Xresources (Xft.dpi + Xcursor.size, loaded by
# `xrdb` in ~/.xinitrc), gtk-xft-dpi in the GTK settings.ini (with the theme, above), and the
# session env (GDK_SCALE + QT_* in the openbox environment). `azzio display scale` rewrites
# these from a chosen factor and re-applies live.
XRESOURCES_PATH = f"{HOME}/.Xresources"


def xresources() -> str:
    """~/.Xresources -- the X resource DB `xrdb` loads at session start. Carries Xft.dpi (the
    screen DPI every X client reads) and Xcursor.size, both DERIVED from the single scale
    (packages/openbox/scale). This is the fractional-scale backbone on X11 (GDK_SCALE is
    integer-only). `azzio display scale` rewrites the two values and re-runs `xrdb -merge`."""
    from . import scale
    return (
        "! Azzio X resources. Generated by packages/openbox (edit the Python, not this\n"
        "! file). Xft.dpi is the GLOBAL SCALE backbone (round(96*scale)) every X client reads;\n"
        "! Xcursor.size scales the cursor with it. `azzio display scale` rewrites these.\n"
        f"Xft.dpi: {scale.xft_dpi()}\n"
        f"Xcursor.size: {scale.xcursor_size()}\n"
    )


# --- 3. ~/.config/openbox/rc.xml --------------------------------------------
# The Super key is armed via xcape: a lone Super_L tap is turned into the chord
# Super_L+Menu (see openbox_autostart), and THIS keybind runs the menu launcher on
# that chord. OpenBox cannot bind a bare modifier itself, so this indirection is how
# "Super opens the menu" works while Super still behaves as a normal modifier for
# every other bind. The Menu keysym is bound both with W- (the xcape chord) and bare
# (belt: some keyboards' physical Menu/Apps key) so either opens the menu.
SUPER_MENU_KEYSYM = "Menu"

# The title font size (points). The OpenBox titlebar font is DPI-BLIND -- OpenBox renders it at a
# fixed pt and does NOT read the X Xft.dpi -- so unlike the GTK/kitty fonts it must be scaled
# EXPLICITLY from the single scale source (packages.openbox.scale). pt(OPENBOX_TITLE_FONT_STOCK) ==
# 9 at the 1.35 default. Set in rc.xml's <theme> <font> blocks below. Deriving it from scale.py
# (not a raw number) keeps ONE source of truth for the scale -- a scale change moves the titlebar
# with everything else.
from . import scale as _scale  # noqa: E402  (single source of truth for the scale)

# TOPBAR GROWTH: the title FONT is the dominant half of the bar height AND the thing OpenBox sizes
# the min/max/close buttons (and their glyphs) to, so a single factor on the font grows the WHOLE
# topbar -- bar height, buttons, and button icons -- together. It carries TWO asks:
#   * "grow the topbar back by ~10%" (the earlier +10% topbar), and
#   * "slightly increase the size of the button icons" (the new prompt) -- OpenBox has no separate
#     button-glyph-size field, so a one-point font nudge is the only native lever for bigger icons;
#     the small extra bar height is the unavoidable ride-along.
# It is applied HERE (not folded into scale.py's stock) so the GLOBAL scale that every OTHER app
# rides stays untouched -- this is a topbar-only cosmetic bump. round(pt(7)=9 * 1.20) == 11 (was
# 10 at 1.10): the +10% topbar plus the icon nudge. A named factor (not a raw 11) keeps the intent
# and the arithmetic explicit for a test.
TOPBAR_GROWTH = 1.20                     # topbar +10% PLUS the button-icon nudge (font -> icons)
TITLE_FONT_SIZE = round(_scale.pt(_scale.OPENBOX_TITLE_FONT_STOCK) * TOPBAR_GROWTH)  # 9 -> 11


def openbox_rc_xml() -> str:
    """OpenBox rc.xml: window-manager behaviour + keybinds for a panel-less session.

    Uses the Azzio theme (flat-colour Clearlooks, grown titlebar with a 2x button gap and
    signature-cyan button hover, see openbox_theme_rc) plus a larger title font, and wires the
    Azzio bits:
      * W-Menu / Menu -> run the application-menu launcher (the Super key, via xcape).
      * A small, sensible keybind set (close window, alt-tab, workspace switch, a
        terminal on W-Return) so the session is usable without a panel.
      * FULL titlebar-button mouse bindings (Iconify/Maximize/Close/Icon/...): OpenBox
        draws the min/max/close buttons from the theme's titleLayout, but they DO NOTHING
        unless rc.xml binds a click action to each button's mouse context -- the previous
        rc.xml bound only the Titlebar context, so the buttons rendered but were dead.
      * FULL window edge/corner RESIZE bindings (Top/Bottom/Left/Right + the four
        corners): same bug shape as the dead buttons -- OpenBox draws a resize border but
        dragging an edge/corner does nothing unless its context is bound. The previous
        rc.xml bound only the Frame's Alt+Right drag; now a plain edge/corner grab resizes
        the window (each side to its edge, corners in both axes), Alt+Right kept too.
      * NO desktop right/middle-click menu: the "Root" mouse context is intentionally
        EMPTY so right-clicking the background does nothing (the OpenBox root menu was
        removed per the user's request; the Super key remains the only way to the menu).
    There is NO dock/panel configuration -- the Azzio menu is the only shell.

    Placed at ~/.config/openbox/rc.xml (and /etc/skel) so the live and installed users
    share it. OpenBox re-reads it on `openbox --reconfigure`."""
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!-- Azzio OpenBox configuration. Panel-less: the Azzio application menu (Super key)
     is the only shell surface, and the desktop right-click menu is disabled. Generated
     by packages.openbox (edit the Python, not this file). -->
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <resistance>
    <strength>10</strength>
    <screen_edge_strength>20</screen_edge_strength>
  </resistance>
  <focus>
    <focusNew>yes</focusNew>
    <followMouse>no</followMouse>
    <focusLast>yes</focusLast>
    <underMouse>no</underMouse>
    <focusDelay>200</focusDelay>
    <raiseOnFocus>no</raiseOnFocus>
  </focus>
  <placement>
    <policy>Smart</policy>
    <center>yes</center>
    <monitor>Primary</monitor>
    <primaryMonitor>1</primaryMonitor>
  </placement>
  <theme>
    <!-- The Azzio theme with a flat-colour grown titlebar (openbox_theme_rc, shipped to
         ~/.themes/{OPENBOX_THEME_NAME} and ~/.themes/{OPENBOX_THEME_NAME_DARK}). DARK is
         the default; `azzio theme` (white / dark) rewrites this name element to
         "{OPENBOX_THEME_NAME}" or "{OPENBOX_THEME_NAME_DARK}". titleLayout NLIMC = icon,
         Label, iconify, maximize, close. The Label is a stretchy spring in the MIDDLE, so the
         iconify/maximize/close buttons pack together on the RIGHT with close (exit) rightmost;
         the doubled padding.width (see openbox_theme_rc) then widens the gap between those three
         while keeping close rightmost and min/max to its left. The leading `N` (icon) is kept
         ONLY to show the window's branding icon (e.g. the Calamares "Az'" tile, productIcon) on
         the left of the bar; it is DELIBERATELY INERT. The Icon mouse context below binds no
         ShowMenu, so clicking it does NOTHING (the user asked that the application-icon popup
         menu not appear at all). Same "the icon must not open a menu" intent as the empty Root
         context. -->
    <name>{OPENBOX_THEME_DEFAULT}</name>
    <titleLayout>NLIMC</titleLayout>
    <keepBorder>yes</keepBorder>
    <animateIconify>yes</animateIconify>
    <!-- Larger title font (the topbar + button-icon size lever, the dominant half of the bar
         height): a taller label makes a taller titlebar, and OpenBox sizes the min/max/close
         buttons (and their glyphs) to the label, so this one bump grows bar, buttons, and
         icons together, which is how the "slightly bigger button icons" ask is met. -->
    <font place="ActiveWindow">
      <name>sans</name>
      <size>{TITLE_FONT_SIZE}</size>
      <weight>bold</weight>
      <slant>normal</slant>
    </font>
    <font place="InactiveWindow">
      <name>sans</name>
      <size>{TITLE_FONT_SIZE}</size>
      <weight>bold</weight>
      <slant>normal</slant>
    </font>
  </theme>
  <!-- EXACTLY ONE desktop. Azzio has a single desktop and no workspace switching: the
       user asked that there not be multiple desktops "to begin with" (they had noticed the
       client-menu's "Send to desktop" listing two live desktops). <number>1</number> means
       OpenBox exposes a single workspace, so the client-menu shows no "Send to desktop"
       submenu and nothing can move a window to a second desktop. The GoToDesktop keybinds
       were removed too (there is nowhere to go). -->
  <desktops>
    <number>1</number>
    <firstdesk>1</firstdesk>
    <names>
      <name>one</name>
    </names>
    <popupTime>0</popupTime>
  </desktops>
  <resize>
    <drawContents>yes</drawContents>
    <popupShow>Nonpixel</popupShow>
  </resize>
  <keyboard>
    <!-- The Super key: xcape emits Super_L+Menu on a lone Super tap; bind that chord
         (and the bare Menu/Apps key) to the Azzio application-menu launcher. -->
    <keybind key="W-{SUPER_MENU_KEYSYM}">
      <action name="Execute">
        <command>{MENU_LAUNCHER}</command>
      </action>
    </keybind>
    <keybind key="{SUPER_MENU_KEYSYM}">
      <action name="Execute">
        <command>{MENU_LAUNCHER}</command>
      </action>
    </keybind>
    <!-- A terminal without the menu (kitty is the primary terminal). -->
    <keybind key="W-Return">
      <action name="Execute">
        <command>kitty</command>
      </action>
    </keybind>
    <!-- Window management basics so the session is usable panel-less. -->
    <keybind key="A-F4">
      <action name="Close"/>
    </keybind>
    <!-- Alt+Tab: the Azzio window switcher (packages/window_switcher), which REPLACES
         OpenBox's built-in vertical icon list. A horizontal, Windows-like overlay of LIVE
         window thumbnails, ordered librewolf/kitty/hypervisor/file_manager/alphabetical. The
         launcher signals the resident daemon (the next flag advances forward, prev
         backward). Releasing Alt (the daemon grabs the seat, so it sees the release)
         commits the selection. -->
    <keybind key="A-Tab">
      <action name="Execute">
        <command>{SWITCHER_LAUNCHER} --next</command>
      </action>
    </keybind>
    <keybind key="A-S-Tab">
      <action name="Execute">
        <command>{SWITCHER_LAUNCHER} --prev</command>
      </action>
    </keybind>
    <keybind key="W-d">
      <action name="ToggleShowDesktop"/>
    </keybind>
    <!-- No C-A-Left/Right GoToDesktop binds: Azzio has a SINGLE desktop (see <desktops>
         above), so there is no second workspace to switch to. -->
    <!-- W-d ToggleShowDesktop still minimises/restores everything on the one desktop. -->
    <!-- FN media keys -> the `azzio` volume/brightness controls (7.5% steps, a centered
         cyan on-screen bar). We bind the X "XF86" media KEYSYMS the keyboard emits, NOT a
         fixed FN+F2/F3, because that physical mapping DIFFERS per machine: on the user's PC
         keyboard FN+F2/F3 emit the AUDIO keysyms (volume), while on their laptop FN+F2/F3 emit
         the BRIGHTNESS keysyms (dim/brighten). Binding the keysyms means each machine's FN keys
         "just work" without us resolving the layout. Brightness is a LAPTOP-ONLY control, so
         `azzio brightness` self-gates: on a PC (no backlight) these brightness binds harmlessly
         do nothing, exactly as intended (a desktop has no screen backlight to dim). -->
    <keybind key="XF86AudioRaiseVolume">
      <action name="Execute"><command>{AZZIO_BIN_PATH} volume up</command></action>
    </keybind>
    <keybind key="XF86AudioLowerVolume">
      <action name="Execute"><command>{AZZIO_BIN_PATH} volume down</command></action>
    </keybind>
    <keybind key="XF86AudioMute">
      <action name="Execute"><command>{AZZIO_BIN_PATH} volume mute</command></action>
    </keybind>
    <keybind key="XF86MonBrightnessUp">
      <action name="Execute"><command>{AZZIO_BIN_PATH} brightness up</command></action>
    </keybind>
    <keybind key="XF86MonBrightnessDown">
      <action name="Execute"><command>{AZZIO_BIN_PATH} brightness down</command></action>
    </keybind>
  </keyboard>
  <mouse>
    <dragThreshold>8</dragThreshold>
    <doubleClickTime>200</doubleClickTime>
    <screenEdgeWarpTime>0</screenEdgeWarpTime>
    <context name="Frame">
      <mousebind button="A-Left" action="Press"><action name="Focus"/><action name="Raise"/></mousebind>
      <mousebind button="A-Left" action="Drag"><action name="Move"/></mousebind>
      <mousebind button="A-Right" action="Drag"><action name="Resize"/></mousebind>
    </context>
    <context name="Titlebar">
      <mousebind button="Left" action="Press"><action name="Focus"/><action name="Raise"/></mousebind>
      <mousebind button="Left" action="Drag"><action name="Move"/></mousebind>
      <mousebind button="Left" action="DoubleClick"><action name="ToggleMaximize"/></mousebind>
    </context>
    <!-- Titlebar BUTTON contexts. OpenBox draws the min/max/close buttons from the
         theme's titleLayout, but a button only DOES something if its mouse context is
         bound here (the old rc.xml bound only Titlebar, so the buttons were dead). These
         are the canonical OpenBox bindings: click iconify/maximize/close to act, and the
         window icon opens the client menu. -->
    <context name="Iconify">
      <mousebind button="Left" action="Press"><action name="Focus"/><action name="Raise"/></mousebind>
      <mousebind button="Left" action="Click"><action name="Iconify"/></mousebind>
    </context>
    <context name="Maximize">
      <mousebind button="Left" action="Press"><action name="Focus"/><action name="Raise"/><action name="Unshade"/></mousebind>
      <mousebind button="Left" action="Click"><action name="ToggleMaximize"/></mousebind>
      <mousebind button="Middle" action="Click"><action name="ToggleMaximize"><direction>vertical</direction></action></mousebind>
      <mousebind button="Right" action="Click"><action name="ToggleMaximize"><direction>horizontal</direction></action></mousebind>
    </context>
    <context name="Close">
      <mousebind button="Left" action="Press"><action name="Focus"/><action name="Raise"/><action name="Unshade"/></mousebind>
      <mousebind button="Left" action="Click"><action name="Close"/></mousebind>
    </context>
    <!-- The window ICON (leftmost titlebar element, the `N` in titleLayout). Its click
         binding is INTENTIONALLY neutered: the user asked that pressing the application icon
         NOT pop up a menu ("i'd prefer it didn't do that in the first place, delete that,
         make sure it doesn't happen"). Stock OpenBox binds this context to ShowMenu
         client-menu (the popup with "Send to desktop"/minimise/maximise/close); we bind ONLY
         Focus+Raise, with NO ShowMenu, so the icon still shows the branding tile but pressing
         it does nothing; the popup can never appear. (Same "the surface exists but opens no
         menu" shape as the empty Root context below.) The min/max/close BUTTONS still work
         (their own contexts, above), so nothing is lost. -->
    <context name="Icon">
      <mousebind button="Left" action="Press"><action name="Focus"/><action name="Raise"/><action name="Unshade"/></mousebind>
      <mousebind button="Right" action="Press"><action name="Focus"/><action name="Raise"/></mousebind>
    </context>
    <!-- Window EDGE + CORNER resize contexts. Same shape of bug as the dead buttons above:
         OpenBox draws a resize border/handle around every decorated window, but dragging an
         edge or corner does NOTHING unless that edge/corner mouse context is bound here. The
         previous rc.xml bound only the Frame's Alt+Right drag, so a plain edge/corner grab
         was dead. These are the canonical OpenBox default bindings: each side drags that one
         edge (Resize with an <edge>), each corner drags freely in both axes (Resize, no
         edge). The Alt+Right whole-window resize on Frame (above) is kept too. The keepBorder
         theme option leaves the 1px border on maximized windows so this stays reachable. -->
    <context name="Top">
      <mousebind button="Left" action="Drag"><action name="Resize"><edge>top</edge></action></mousebind>
    </context>
    <context name="Left">
      <mousebind button="Left" action="Drag"><action name="Resize"><edge>left</edge></action></mousebind>
    </context>
    <context name="Right">
      <mousebind button="Left" action="Drag"><action name="Resize"><edge>right</edge></action></mousebind>
    </context>
    <context name="Bottom">
      <mousebind button="Left" action="Drag"><action name="Resize"><edge>bottom</edge></action></mousebind>
    </context>
    <context name="TRCorner BRCorner TLCorner BLCorner">
      <mousebind button="Left" action="Press"><action name="Focus"/><action name="Raise"/><action name="Unshade"/></mousebind>
      <mousebind button="Left" action="Drag"><action name="Resize"/></mousebind>
    </context>
    <!-- Desktop right/middle click: INTENTIONALLY does nothing. The OpenBox root menu was
         removed per the user's request, so the Root context binds no ShowMenu. -->
    <context name="Root">
    </context>
    <context name="Client">
      <mousebind button="Left" action="Press"><action name="Focus"/><action name="Raise"/></mousebind>
    </context>
  </mouse>
  <applications>
    <!-- DEFAULT for EVERY window (class="*"): open MAXIMIZED, and when restored down snap to a
         wide "classic terminal" rectangle instead of a tiny box. <maximized>yes</maximized>
         maps the window maximized; <size> is its NORMAL (un-maximized) geometry, so a
         restore-down lands on {RESTORED_WINDOW_WIDTH}x{RESTORED_WINDOW_HEIGHT} (16:10, wider
         than tall) rather than the client's small default; <position> centers that restored
         window. force="no" on the position means it only steers the FIRST map, so the user can
         freely move the window afterwards. This rule is listed FIRST so the specific rules
         below (menu, Calamares) can override individual fields (OpenBox merges per-window
         settings from all matching application rules, last matching value wins per field). The
         application menu is override-redirect (unmanaged), so this never affects it; the
         Calamares rule opts OUT of maximize explicitly. -->
    <application class="*">
      <maximized>yes</maximized>
      <size>
        <width>{RESTORED_WINDOW_WIDTH}</width>
        <height>{RESTORED_WINDOW_HEIGHT}</height>
      </size>
      <position force="no">
        <x>center</x>
        <y>center</y>
      </position>
    </application>
    <!-- The Azzio application menu is a borderless override-redirect Tk window; it
         manages its own placement (centered) and needs no OpenBox decorations. -->
    <application name="*azzio*menu*">
      <decor>no</decor>
    </application>
    <!-- The Calamares installer: open RESTORED-DOWN (NOT maximized) and CENTERED, so the live
         session's wallpaper stays visible around it (a deliberate hint that the live desktop can
         still be used while the installer is up). Every OTHER app opens maximized (the wildcard
         rule above), and the user likes that; only the installer opts out. It ALSO re-centres on
         a REOPEN: Calamares saves its last window geometry (Qt session state), so the second
         time it is launched it would come up wherever it last sat, ignoring branding.desc's
         windowPlacement:center (which only steers the FIRST map); a per-app position with
         force="yes" overrides the client's requested position on every map. Matched on BOTH
         WM_CLASS fields with their exact (lowercase) case: res_name AND res_class are both
         "calamares" (VERIFIED via xprop in the VM). OpenBox matching is case-sensitive, so this
         MUST be lowercase. A capital class="Calamares" would not match the real res_class, the
         rule would silently no-op, and the installer would fall through to the wildcard's
         maximized:yes and open full-screen (which was the reported bug). -->
    <application name="{CALAMARES_WM_NAME}" class="{CALAMARES_WM_CLASS}">
      <!-- Opt OUT of the wildcard's <maximized>yes</maximized> above: the installer sizes
           itself (its pages assume its own window size), so it must NOT open maximized. This
           later-matching rule wins on the maximized field; the centered position is kept
           force="yes" so a REOPEN re-centres it (Calamares remembers its last geometry). -->
      <maximized>no</maximized>
      <position force="yes">
        <x>center</x>
        <y>center</y>
      </position>
    </application>
  </applications>
</openbox_config>
"""


# --- 4. (removed) ~/.config/openbox/menu.xml --------------------------------
# The OpenBox ROOT menu (right/middle click on the desktop) was REMOVED per the user's
# request ("remove the right click menu ... disable that menu completely"). rc.xml's
# Root mouse context is now empty (no ShowMenu), so right-clicking the desktop does
# nothing, and no menu.xml is emitted. The Azzio application menu (Super key) remains
# the only shell surface; its launcher, installer, and power actions live there.

# --- 5. ~/.config/openbox/autostart -----------------------------------------
# Keyboard layouts for the LIVE session: US English (default) + Hebrew, Alt+Shift to
# toggle. Applied with setxkbmap in the autostart (a plain, DE-independent xkb config).
# Kept as constants so a test can pin them.
KEYBOARD_LAYOUTS = ["us", "il"]           # xkb codes, us first == default
KEYBOARD_TOGGLE = "grp:alt_shift_toggle"  # Alt+Shift cycles layouts


# The three autostart blocks common to BOTH the live and the installed session:
# wallpaper (feh), the Super key (xcape), and the resident menu daemon. Factored out so
# the live and installed autostarts cannot drift on the parts they share.
def _openbox_autostart_common() -> str:
    return f"""\
# 0. Default resolution: raise an UNDERSIZED primary to {DEFAULT_RESOLUTION}. A DE-less OpenBox
#    session has no display manager to choose a mode, so X comes up on the output's PREFERRED
#    mode -- which on the QEMU/virtio-gpu virtual display is a quirky 1920x1031, SMALLER than
#    {DEFAULT_RESOLUTION} (the resolution regression). We switch to {DEFAULT_RESOLUTION} ONLY WHEN
#    the currently-active mode is SMALLER (in pixel area) than {DEFAULT_RESOLUTION} AND that mode
#    is offered -- so the sub-1080p VM boot is corrected, but a LARGER primary (1440p, 4K,
#    1920x1200, ...) is NEVER downgraded: on those the active area is >= 1920x1080, so the guard
#    is a no-op. Runs SYNCHRONOUSLY and BEFORE the wallpaper so the root is painted at the final
#    geometry. Also a no-op when xrandr is missing, there is no primary, or 1920x1080 isn't listed.
if command -v xrandr >/dev/null 2>&1; then
    # ONE awk pass, SCOPED PER OUTPUT (a non-indented "... connected ..." line starts an output;
    # the indented mode lines under it belong to THAT output until the next connector line). We
    # must NOT parse the active '*' mode globally: on a multi-head layout xrandr lists outputs by
    # connector id, so a small non-primary output can appear BEFORE the primary and its mode would
    # be mistaken for the primary's -- downgrading a large primary. So we record, per output, its
    # active-mode WxH (the '*'-marked indented line) and whether it offers 1920x1080, then at END
    # emit ONLY the chosen output's values: "<name> <activeW> <activeH> <offers1080>". The chosen
    # output is the one flagged `primary`, else the first `connected`.
    set -- $(xrandr --query 2>/dev/null | awk '
        /^[^[:space:]].* connected/ {{
            out=$1; conn[out]=1; order[++n]=out;
            if ($0 ~ / connected primary/) primary=out;
            if (first=="") first=out;
            next;
        }}
        /^[[:space:]]+[0-9]+x[0-9]+/ {{
            if (out=="") next;
            res=$1;
            if ($1=="{DEFAULT_RESOLUTION}") offers[out]=1;
            if ($0 ~ /\\*/) {{ split(res,wh,"x"); aw[out]=wh[1]; ah[out]=wh[2]; }}
        }}
        END {{
            sel=(primary!=""?primary:first);
            if (sel=="") exit 0;
            printf "%s %s %s %s\\n", sel, (aw[sel]==""?0:aw[sel]), (ah[sel]==""?0:ah[sel]), (offers[sel]?1:0);
        }}')
    _az_out="$1"; _az_cw="$2"; _az_ch="$3"; _az_offers="$4"
    # Upgrade ONLY when the PRIMARY's own active mode is a real WxH strictly SMALLER in area than
    # 1920x1080 and the primary offers 1080 -- so a >=1080p primary is never touched, whatever the
    # other heads are doing.
    if [ -n "$_az_out" ] && [ "${{_az_offers:-0}}" = "1" ] \\
        && [ "${{_az_cw:-0}}" -gt 0 ] && [ "${{_az_ch:-0}}" -gt 0 ] \\
        && [ "$(( _az_cw * _az_ch ))" -lt "$(( 1920 * 1080 ))" ]; then
        xrandr --output "$_az_out" --mode {DEFAULT_RESOLUTION} >/dev/null 2>&1 || true
    fi
fi

# 1. Wallpaper: repaint the same image ~/.xinitrc pre-painted (no flash; also covers a
#    re-login where the X root pixmap was reset). feh owns the root pixmap on OpenBox. The
#    image honours the per-user `azzio wallpaper` pointer, falling back to the "years"
#    default -- so an `azzio wallpaper --decades.png` choice survives a re-login.
{_feh_wallpaper_line()} &

# 2. Super key -> application menu. OpenBox cannot bind a lone modifier, so xcape turns
#    a solo Super_L tap into the chord Super_L+Menu, which rc.xml binds to the menu
#    launcher. Super keeps working as a normal modifier for every other bind (xcape
#    suppresses the tap whenever Super is pressed WITH another key). -t 500: a tap fires
#    the instant Super is released; the generous 500ms window means an ordinary, slightly
#    lingering press still counts as a tap instead of being silently dropped -- the old
#    200ms cap made a normal Super press "sometimes do nothing", which felt laggy/buggy.
command -v xcape >/dev/null 2>&1 && \\
    xcape -t 500 -e 'Super_L=Super_L|Menu' &

# 3. Azzio application-menu daemon: build the menu once and keep it hidden so the
#    first Super press is instant (the C/GTK3 daemon, see application_menu/menu.c).
[ -x '{MENU_DAEMON_BIN}' ] && \\
    setsid '{MENU_DAEMON_BIN}' >/dev/null 2>&1 < /dev/null &

# 3a. Compositor (picom): REQUIRED for the alt-tab switcher to read LIVE pixels of every
#     window (even covered/minimized ones) via XComposite. Without it, obscured windows
#     have no backing pixmap and the switcher falls back to their app icon. Started before
#     the switcher daemon so redirection is already in place. Guarded; harmless if absent.
#     `--config {PICOM_CONFIG_PATH}` points picom at OUR config (fading OFF, frames FULLY
#     OPAQUE) instead of the packaged /etc/xdg/picom.conf, whose defaults (window fading + a
#     0.9 frame opacity) are the reported "apps fade in/out" and "transparent titlebar" bugs.
command -v picom >/dev/null 2>&1 && \\
    setsid picom --config {PICOM_CONFIG_PATH} >/dev/null 2>&1 < /dev/null &

# 3b. Azzio window-switcher daemon: build the alt-tab overlay once and keep it hidden so
#     the first Alt+Tab is instant (the C/GTK3 daemon, see packages/window_switcher). Bound
#     to A-Tab/A-S-Tab in rc.xml via the launcher, which signals this daemon.
[ -x '{SWITCHER_DAEMON_BIN}' ] && \\
    setsid '{SWITCHER_DAEMON_BIN}' >/dev/null 2>&1 < /dev/null &

# 4. FN media keys, hold-to-drag: X autorepeat governs how fast HOLDING an FN volume/brightness
#    key repeats (each repeat is one `azzio volume/brightness` step -> the OSD "fast drag").
#    The default ~660ms delay before repeats start feels sluggish when you just want to hold to
#    ramp, so shorten the initial DELAY to 300ms and set a brisk RATE of 25/s. `xset r rate
#    <delay> <rate>`; guarded so a missing xset never breaks the session.
command -v xset >/dev/null 2>&1 && xset r rate 300 25 &

# 5. Media defaults: seed the STARTING levels (50% volume, 100% brightness on a laptop) ONCE.
#    `azzio media-init` keys off a per-user marker, so it applies the defaults on a fresh
#    machine but never clobbers a level the user has since chosen. Silent (no OSD), always rc 0.
[ -x '{AZZIO_BIN_PATH}' ] && '{AZZIO_BIN_PATH}' media-init >/dev/null 2>&1 &

# 6. Live file manager sidebar: keep ~/.config/gtk-3.0/bookmarks in sync with the ACTUAL home
#    contents so anything the user adds to $HOME shows up in the shortcuts pane (PROMPT). The
#    helper regenerates the bookmarks now and then watches the home dir mtime, re-emitting in
#    the required order (dirs -> files -> symlinks -> Trash last), symlinks resolved. Guarded
#    so a missing helper never breaks the session.
[ -x '{FILE_MANAGER_SIDEBAR_SYNC}' ] && \\
    setsid '{FILE_MANAGER_SIDEBAR_SYNC}' --watch >/dev/null 2>&1 < /dev/null &

# 7. SPICE guest agent (the SESSION half): spice-vdagent needs a running X session, so it is
#    started HERE (spice-vdagentd, the system daemon it talks to, is enabled via systemd). On a
#    SPICE guest this is what keeps the guest pointer in sync with the client -- without it,
#    hovering does nothing and left-clicks land at stale coordinates ("dropped"), the reported
#    regression. Guarded (`command -v`) so a missing binary never breaks the session, and it
#    exits at once on a non-SPICE machine (no channel), so it is harmless everywhere.
command -v spice-vdagent >/dev/null 2>&1 && spice-vdagent &"""


def openbox_autostart() -> str:
    """~/.config/openbox/autostart for the LIVE session -- run by openbox-session once
    the WM is up.

    Brings up the full session via autostart, for a panel-less OpenBox desktop: the
    shared wallpaper/xcape/menu-daemon block PLUS two LIVE-ONLY behaviours that must NOT
    survive onto an installed system:
      * setxkbmap us,il grp:alt_shift_toggle: the US + Hebrew layouts (Alt+Shift to
        switch), a plain DE-independent xkb config. Live-only because
        an install picks a region keyboard (written to /etc/X11/xorg.conf.d) that this
        fixed us,il would otherwise override at every login.
      * launch the Calamares installer ONCE (Manjaro-style first-run). Live-only: an
        installed system must not re-open the installer at every login.

    So the Calamares OFFLINE install OVERWRITES this file (home + skel) with
    openbox_autostart_installed() -- which drops exactly those two lines -- via the
    packages/calamares_shellprocess cleanup step. Each line is guarded
    (`command -v` / `[ -x ]`) so a missing tool never aborts the session. Shipped to the
    live home and /etc/skel."""
    layouts = ",".join(KEYBOARD_LAYOUTS)
    return f"""\
#!/bin/sh
# ~/.config/openbox/autostart -- Azzio OpenBox LIVE session startup (panel-less).
# Run by openbox-session after the window manager is up. Keep every line guarded so a
# missing tool never breaks the session. The Calamares install overwrites this with the
# "installed" variant (no fixed keyboard, no installer) -- see calamares_shellprocess.py.

{_openbox_autostart_common()}

# 6. LIVE-ONLY -- keyboard layouts: US English (default) + Hebrew, Alt+Shift to toggle.
#    An install writes a region keyboard to /etc/X11/xorg.conf.d/00-keyboard.conf, so
#    this fixed us,il is stripped from the installed autostart (it would override it).
command -v setxkbmap >/dev/null 2>&1 && \\
    setxkbmap -layout '{layouts}' -option '{KEYBOARD_TOGGLE}' &

# 7. LIVE-ONLY -- the installer, once, a couple seconds in (Manjaro-style first-run).
#    The DEFAULT medium opens the Calamares GUI (`--gui` -- azzioinstall has no default
#    action, so the mode is named explicitly). An "instant" medium instead runs the
#    UNATTENDED installer: if the instant auto-install hook exists and is executable, run
#    IT -- a tiny script the compiler baked that execs `azzioinstall --instant ...` with the
#    operator's chosen identity (correctly quoted), so the box auto-installs with no
#    interaction. The wrapper elevates via passwordless sudo on the live medium. Both
#    branches are stripped from the installed autostart so an installed system never
#    re-opens the installer.
if [ -x '{INSTANT_INSTALL_HOOK_PATH}' ]; then
    # Instant ISO: run the pre-baked hook, which execs `azzioinstall --instant ...` with the
    # operator's build-time identity (correctly quoted). Unattended -- no GUI.
    ( sleep 2; '{INSTANT_INSTALL_HOOK_PATH}' ) &
elif [ -x '{INSTALL_WRAPPER_PATH}' ]; then
    ( sleep 2; '{INSTALL_WRAPPER_PATH}' --gui ) &
fi

# 8. LIVE-ONLY -- first-run SECURITY NOTICE (once). `azzio security-notice` explains that
#    the base desktop ships with password login + ssh OFF, and that enabling either exposes
#    the box over the network. It SELF-GATES: it stays quiet on the ssh variant (ssh was
#    chosen deliberately) and once a real login password is set, and self-silences after the
#    first show. Stripped from the installed autostart (the installed system has a real
#    user password, so the warning does not apply there).
command -v azzio >/dev/null 2>&1 && ( sleep 4; azzio security-notice ) &
"""


def openbox_autostart_installed() -> str:
    """The INSTALLED-system ~/.config/openbox/autostart: the shared wallpaper/xcape/menu-
    daemon block ONLY. The Calamares OFFLINE install overwrites the live autostart (which
    the target inherits verbatim via unpackfs) with THIS content -- dropping the two
    live-only lines (the fixed us,il setxkbmap and the first-run Calamares launch) so the
    installed system uses its chosen region keyboard and never re-opens the installer. It
    is written to BOTH /home/main and /etc/skel by the shellprocess cleanup step. Emitted
    to a staging path on the ISO (installer_autostart.sh) so the shellprocess can `cp` it
    into place inside the target chroot without needing any `$`-expansion."""
    return f"""\
#!/bin/sh
# ~/.config/openbox/autostart -- Azzio OpenBox INSTALLED session startup (panel-less).
# Written by the Calamares install (calamares_shellprocess.py) over the live autostart:
# the shared wallpaper/xcape/menu-daemon block only -- NO fixed us,il keyboard (the
# region keyboard in /etc/X11/xorg.conf.d governs) and NO first-run installer launch.

{_openbox_autostart_common()}
"""


# Where the "installed" autostart is staged on the ISO so the Calamares shellprocess can
# copy it over the target's inherited live autostart (home + skel) inside the chroot.
INSTALLED_AUTOSTART_STAGING_PATH = "/usr/local/share/azzio/openbox-autostart-installed"


def instant_install_hook_sh(azzioinstall_args: list[str]) -> str:
    """The instant ISO's auto-install hook (staged at INSTANT_INSTALL_HOOK_PATH), run by the
    live autostart in place of the Calamares GUI.

    It simply `exec`s the azzioinstall wrapper with the operator's build-time `--instant`
    arguments, each SHELL-QUOTED here (shlex.quote) so a value containing spaces or shell
    metacharacters -- a password like `correct horse`, a full name -- reaches azzioinstall as
    ONE argv word, exactly as typed at build time. The wrapper elevates via passwordless sudo
    on the live medium (run_auto -> run_cli), so the hook itself needs no sudo, mirroring the
    `--gui` autostart line it replaces.

    `azzioinstall_args` MUST already start with `--instant` (the mode selector) followed by any
    resolved sub-flags; the compiler builds the list, this only renders it verbatim + quoted."""
    import shlex
    quoted = " ".join(shlex.quote(a) for a in azzioinstall_args)
    return f"""\
#!/bin/sh
# Azzio INSTANT auto-install hook -- generated by the compiler for the instant/instant+sshd ISO.
# Run once by the live OpenBox autostart (in place of the Calamares GUI) to install onto disk
# unattended with the identity chosen at build time. Removed from the base/sshd ISOs.
exec '{INSTALL_WRAPPER_PATH}' {quoted}
"""


def openbox_environment() -> str:
    """~/.config/openbox/environment -- sourced by openbox-session before autostart.

    A minimal, stable place for session env vars. We re-assert XDG_CURRENT_DESKTOP
    (also set in ~/.xinitrc) so it is correct even if OpenBox is started by some other
    path than our startx, keep the XDG base dirs defined, AND bridge Qt apps onto the
    system theme.

    QT_QPA_PLATFORMTHEME=gtk3 is the SYSTEM-THEME bridge for Qt: with no Qt-side theme
    config and no xdg-desktop-portal on this medium, Qt6 apps like Calamares would
    otherwise render with Qt's stock LIGHT Fusion palette regardless of the freedesktop
    color-scheme. The Qt `gtk3` platform theme plugin (libqgtk3.so, shipped with
    qt6-base) makes those Qt apps read the GTK theme instead -- so they follow the SAME
    Adwaita-dark/Adwaita + prefer-dark signal `azzio theme` sets for GTK, and switch
    dark<->light with the rest of the session. This is what makes Calamares (and any
    downloaded Qt app) obey `azzio theme`."""
    from . import scale
    return f"""\
# ~/.config/openbox/environment -- sourced by openbox-session before autostart.
export XDG_CONFIG_HOME="${{XDG_CONFIG_HOME:-$HOME/.config}}"
export XDG_CACHE_HOME="${{XDG_CACHE_HOME:-$HOME/.cache}}"
export XDG_CURRENT_DESKTOP=openbox
# Bridge Qt apps (Calamares, any downloaded Qt app) onto the system theme: the Qt gtk3
# platform theme makes them follow the GTK theme (Adwaita-dark/Adwaita) that `azzio theme`
# sets, so they honour dark/white like everything else. Without this Qt apps render light
# regardless of the freedesktop color-scheme (no Qt-side theme config or portal here).
export QT_QPA_PLATFORMTHEME=gtk3
# GLOBAL SCALE session env (the integer + Qt parts; the fractional part rides on Xft.dpi /
# gtk-xft-dpi -- see packages/openbox/scale). GDK_SCALE stays 1 (integer-only; 1.35 is fractional).
# GDK_DPI_SCALE is deliberately UNSET (it would be a SECOND font multiplier on top of
# gtk-xft-dpi and double-scale). Qt-over-gtk3 gets an explicit fractional factor (auto-detect
# off so it does not fight it). `azzio display scale` rewrites these values.
export GDK_SCALE={scale.gdk_scale()}
export QT_AUTO_SCREEN_SCALE_FACTOR=0
export QT_ENABLE_HIGHDPI_SCALING=1
export QT_SCALE_FACTOR={scale.qt_scale_factor()}
"""


# --- 6. Menu daemon usage seed (single source of truth in application_menu.py) --
def az_menu_usage_seed_json() -> str:
    """Seed launch-frequency store for OUR menu, fixing the STARTING top of the list on
    a fresh profile (the menu otherwise sorts alphabetically until the user has opened
    things). Content is owned by packages/application_menu/application_menu.py; this module just
    places it under ~/.local/share and mirrors it into /etc/skel. It stays dynamic: the
    daemon re-sorts as apps are opened."""
    return _app_menu.usage_seed_json()


# --- 7. /usr/share/applications/azzioinstall.desktop ----------------------
def install_menu_desktop() -> str:
    """A launcher in the application menu so the installer can be re-opened after it is
    closed, sharing the same privileged wrapper. Lands in /usr/share/applications
    (system-wide), so it is not a per-user file and is picked up by the Azzio menu's
    application scan.

    Exec names `--gui` explicitly: azzioinstall has no default action, so a bare invocation
    would only print help. This entry re-opens the Calamares GUI installer."""
    return """\
[Desktop Entry]
Type=Application
Name=Azzio Linux Installer
GenericName=System Installer
Comment=Install Azzio Linux to disk
Exec=""" + INSTALL_WRAPPER_PATH + """ --gui
Icon=""" + INSTALLER_ICON_NAME + """
Terminal=false
Categories=System;
Keywords=install;calamares;setup;
"""


# --- 7b. ~/Desktop/azzioinstall.desktop (live-session Desktop launcher) ----
def desktop_installer_launcher() -> str:
    """A double-clickable "Azzio Linux Installer" launcher that sits ON the live
    Desktop, so the installer is one obvious icon away even after the autostart window
    is closed. Uses the same privileged wrapper and the "Az'" app icon.

    Ships EXECUTABLE (PLAN mode 0o755 + a profile.py FILE_PERMISSIONS pin) so any file
    manager that honours the exec bit runs it without a "not trusted" prompt -- archiso
    normalizes overlay modes to 0644 in the squashfs unless a path is pinned (the same
    gotcha documented for /usr/local/bin/azzioinstall), so the pin is required.

    Exec names `--gui` explicitly: azzioinstall has no default action, so a bare
    invocation would only print help. Double-clicking this opens the Calamares GUI."""
    return """\
[Desktop Entry]
Type=Application
Name=Azzio Linux Installer
GenericName=System Installer
Comment=Install Azzio Linux to disk
Exec=""" + INSTALL_WRAPPER_PATH + """ --gui
Icon=""" + INSTALLER_ICON_NAME + """
Terminal=false
Categories=System;
Keywords=install;calamares;setup;
"""


# --- 8. /usr/local/bin/azzio (guest-side command line interface) ------------------------------
# The `azzio` guest command line interface is its OWN Python PACKAGE now, libraries/packages/azzio/ (all
# Python -- no shell). It grew a `theme` subcommand (and more to come), so the single module
# was split into small modules (common/country_table/resolver/theme/sshd/command_line_interface). This module
# no longer AUTHORS the command line interface; it (a) asks the package to BUNDLE those modules into one
# self-contained script (bundle.bundle_source()), then (b) injects the country->locale table
# from packages/calamares/locale (the single source of truth) between the AZZIO_CC markers,
# and ships the result to /usr/local/bin/azzio. See paths.AZZIO_COMMAND_LINE_INTERFACE_DIR and packages/azzio/.
AZZIO_BIN_PATH = "/usr/local/bin/azzio"

# The media OSD indicator (the bottom-middle cyan volume/brightness bar) is a COMPILED Xlib
# program now (on_screen_display.c -> azzio-osd), NOT a tkinter script -- it is a separate GUI process that
# `azzio volume/brightness` launches and feeds one JSON line. It is a SINGLE resident window: a
# second launch forwards to the one already up (no flicker) instead of spawning another. It ships
# next to the C terminal user interface binary in the azzio lib dir (built by
# terminal_user_interface_build.build_osd), so the two travel together. Kept in lock-step with
# packages/azzio/media.py OSD_INDICATOR_BIN and terminal_user_interface_build.OSD_BIN_SYSTEM_PATH
# (tests pin them). This constant remains the single name openbox refers to it by.
AZZIO_OSD_SYSTEM_PATH = "/usr/local/lib/azzio/azzio-osd"

# Marker lines (in the bundled source, originally from country_table.py) bracketing the
# generated COUNTRY_TABLE literal.
_AZZIO_CC_START = "# AZZIO_CC_TABLE_START"
_AZZIO_CC_END = "# AZZIO_CC_TABLE_END"


def azzio_command_line_interface() -> str:
    """The `azzio` guest command line interface (Python), BUNDLED from the libraries/packages/azzio/ package
    into one self-contained script and shipped to /usr/local/bin/azzio. The COUNTRY_TABLE
    dict literal between the AZZIO_CC markers is REGENERATED from
    packages/calamares/locale.RESOLVER_COUNTRY_TABLE so the guest resolver's
    country->locale/layout map stays in lock-step with that single source of truth. The
    package already carries a working copy of the table, so it is self-contained/runnable on
    its own; this re-injection just guarantees no drift.

    Subcommands (see packages/azzio/ for the full behavior):
      theme [--dark|--white]  set the system colour theme (dark default); no arg prints it
      --sshd-hypervisor   install host pubkey from ~/Shared/authorized_keys, start sshd
      gpu [--resolve|--list]  detect the GPU and resolve its drivers from the offline repo
      timedate [--resolve]    geolocate by IP (pick a server) and set the timezone
      language [--resolve]    geolocate by IP and set English + the region language
    """
    from packages.calamares.locale import resolver_country_table_py  # noqa: E402 (locale lives with the calamares package)
    from packages.azzio.bundle import bundle_source  # noqa: E402 (the command line interface package's bundler)

    src = bundle_source()
    start = src.index(_AZZIO_CC_START) + len(_AZZIO_CC_START)
    end = src.index(_AZZIO_CC_END)
    generated = (
        "\nCOUNTRY_TABLE: dict[str, tuple[str, str, str, int]] = {\n"
        + resolver_country_table_py()
        + "\n}\n"
    )
    return src[:start] + generated + src[end:]


# --- 8b. /usr/local/lib/azzio/azzio-osd (the media OSD indicator) ---------
# The OSD is a COMPILED C program (on_screen_display.c) now, so there is no text builder here anymore: it is
# built + installed by terminal_user_interface_build.build_osd() (invoked from compiler.py right
# after the terminal UI binary), and pinned executable in profile.FILE_PERMISSIONS. The old
# azzio_osd() text emitter (which shipped the tkinter osd_indicator.py verbatim) is gone.


# --- 9. /usr/local/bin/azzioinstall (privileged Calamares launcher) -------
def install_wrapper_sh() -> str:
    """The single privileged launch path for Calamares, used by both the OpenBox
    autostart and the application-menu / Desktop installer launchers. On the live medium `main` has
    passwordless sudo, so `sudo -E calamares` is the correct, dependency-free way to
    get root for the GUI installer.

    -E preserves the X env (DISPLAY, XAUTHORITY, XDG_*) so the root-owned Calamares Qt
    process can connect to `main`'s X server.

    We deliberately do NOT pass `-c /etc/calamares`. Despite its name, `-c` is a
    testing-only flag that overrides Calamares' *application data* directory, not just
    the configuration tree: once set, Calamares looks for qml/, branding/ and
    settings.conf ONLY under that dir and skips the normal /usr/share/calamares
    fallback. Our QML ships at /usr/share/calamares/qml (there is no /etc/calamares/qml),
    so `-c /etc/calamares` made Calamares die at startup with "FATAL: explicitly
    configured application data directory is missing qml/". With no `-c`, Calamares
    reads /etc/calamares/settings.conf and branding by default (that IS the sysconfdir
    it checks first) and finds QML under /usr/share, so the installer launches.

    SCALE (the "installer is scaled way too much" report): Calamares is a Qt app, and
    the OpenBox session (openbox_environment) exports BOTH desktop scaling channels --
    the high DPI (Xft.dpi = round(96 * GLOBAL_SCALE), which Qt reads because
    QT_ENABLE_HIGHDPI_SCALING=1) AND an explicit QT_SCALE_FACTOR=<scale>. Every OTHER
    app is scaled by exactly ONE channel (GTK/kitty/gedit by the DPI alone -- see
    packages/openbox/scale), but Calamares, inheriting both, scaled by ~scale x scale
    (~1.82x at 1.35) and came up nearly full-screen (measured 1872x1023 on 1920x1080).
    So we pin QT_SCALE_FACTOR=1 for the installer ONLY (via `env` across the sudo
    boundary, since -E would otherwise carry the session's 1.35 into the root process),
    leaving it scaled by the DPI channel alone like everything else (~1.35x -> a
    centered ~1387x758). This is the ONE app that needed the explicit factor dropped;
    the session env is unchanged so the rest of the desktop keeps its Qt factor."""
    return f"""\
#!/bin/sh
# azzioinstall -- the Azzio installer launcher for the live session.
#
# Front-ends over the SAME install (a mode must be chosen explicitly; no default action):
#   * GUI (`-g`/`--gui`): the Calamares graphical installer.
#   * CLI (`-c`/`--cli`): the scripted terminal installer at {INSTALL_CLI_SCRIPT_PATH}.
#     This is what lets a user install Azzio entirely over an SSH session, with no X.
#   * INSTANT (`-a`/`--instant`): the CLI installer with every answer pre-seeded to a
#     default (btrfs, user main, host azzio, tz Asia/Jerusalem, "admin" passwords, DHCP).
#     Each default can be overridden with an --instant sub-flag (see the sub-flags below).
#   Bare `azzioinstall` (or -h/--help) prints help and does nothing else.
#
# `main` has passwordless sudo on the live medium, so neither path needs a polkit agent.
#
# Usage:
#   azzioinstall                  Show this help (no default action).
#   azzioinstall -g|--gui         Force the Calamares graphical installer.
#   azzioinstall -c|--cli         Force the scripted terminal installer (interactive).
#   azzioinstall -a|--instant     Fully-unattended install with defaults (btrfs, admin pw).
#   azzioinstall --cli --disk sdX CLI install onto /dev/sdX, no disk prompt.
#   azzioinstall --instant --hostname=box --username=me --username-password=secret
#                                 Unattended install with overrides (all --instant sub-flags).
#   azzioinstall --instant --final=reboot   Unattended install, then reboot into the system.
#   azzioinstall -h|--help        Show this help.

usage() {{
    cat <<'EOF'
Usage: azzioinstall [ -g | -c | -a [instant-options] | --cli --disk <dev> ] [ -h ]

  (no option), -h, --help
                      Show this help. Running azzioinstall with no option does NOT
                      start an install; pick one of the modes below.

  -g, --gui, --graphical-user-interface
                      Launch the Calamares graphical installer.

  -c, --cli, --command-line-interface
                      Launch the scripted terminal installer, with parameter parity to
                      Calamares: it walks the SAME choices as the Calamares pages -- disk
                      (auto/manual), hostname, username, user password, root password, and
                      timezone -- then installs. Works over SSH (no X). (The full name is
                      cosmetic and is NOT prompted; set AZ_INSTALL_FULLNAME to fill it.)

  -a, --instant, --automatic
                      Fully-unattended install with defaults, no prompts:
                        timezone   Asia/Jerusalem
                        language   English
                        disk       the largest FIXED disk (skips removable/USB), whole
                                   disk, no swap, btrfs, unencrypted
                        user       main   (full name blank)
                        hostname   azzio
                        passwords  "admin" for user and root (shared)
                        network    automatic DHCP
                        final      idle (stay at a shell when done)
                      Erases the target disk without asking.

  Instant-options (ONLY valid together with --instant; each overrides the matching default
  above, and anything omitted keeps its default):
        --disk=<dev>|auto          "auto" = largest fixed disk (default), or a device
                                   name like sda / nvme0n1 to target that disk.
        --hostname=<name>          Installed hostname            (default azzio).
        --username=<name>          Login user name               (default main).
        --username-password=<pw>   The user's password           (default admin).
        --share-username-root-password=True|False
                                   True (default): root reuses the user password.
                                   False: root uses --root-password instead.
        --root-password=<pw>       Root password when not shared (default admin).
        --timezone=<zone>          e.g. Europe/London            (default Asia/Jerusalem).
        --ssh=<pw>                 Enable ssh on the INSTALLED system (NOT the live session):
                                   the installed box brings sshd up at boot for the login
                                   user. <pw> is that user's password too when
                                   --username-password is omitted, so `--ssh=admin` alone
                                   gives an ssh-reachable account. Also valid with --cli.
        --final=idle|reboot|shutdown
                                   What to do after the install finishes:
                                   idle (default) = print "you can reboot now" and return
                                   to a shell; reboot (alias restart) = reboot into the
                                   freshly installed system; shutdown = power the machine
                                   off.
        --final-indication=True|False
                                   False (default): do nothing extra. True: once the install
                                   body completes (just before the --final action), write a
                                   Shared/INSTALL_DONE marker into the live session's
                                   host<->guest shared folder, so the HOST can confirm the
                                   unattended install succeeded -- essential with
                                   --final=shutdown, where self-poweroff and a crash look the
                                   same from outside. No-op if no shared folder is mounted.
      Example:
        azzioinstall --instant --disk=sda --hostname=box --username=me \
          --username-password=secret --share-username-root-password=False \
          --root-password=rootsecret --timezone=Europe/London --final=shutdown \
          --final-indication=True

  --cli --disk <dev>  Use /dev/<dev> (e.g. sda, nvme0n1) as the target instead of asking
                      which disk. Only meaningful with --cli (--instant uses --disk=<dev>).

  --cli --ssh <pw>    --ssh also works with --cli (see the --instant note above): it turns
                      sshd on for the login user on the INSTALLED system. The live terminal
                      session running the installer is unaffected.

The CLI and GUI installers produce the same system (same packages, same chroot setup, a
real user account, a root password, a hostname, and a timezone). All install modes ERASE
the target disk.

Fully unattended over SSH with your OWN values can also be driven by pre-seeding the
environment directly (the --instant sub-flags are the friendly front-end for these), e.g.
  AZ_INSTALL_DISK=sda AZ_INSTALL_HOSTNAME=box AZ_INSTALL_USERNAME=me \
  AZ_INSTALL_PASSWORD=... AZ_INSTALL_ROOT_PASSWORD=... AZ_INSTALL_TIMEZONE=Europe/London \
  azzioinstall --cli
Recognised: AZ_INSTALL_DISK, AZ_INSTALL_HOSTNAME, AZ_INSTALL_USERNAME, AZ_INSTALL_FULLNAME,
AZ_INSTALL_PASSWORD, AZ_INSTALL_ROOT_PASSWORD, AZ_INSTALL_TIMEZONE, AZ_INSTALL_FILESYSTEM
(ext4 default, or btrfs), AZ_INSTALL_CHOICE, AZ_INSTALL_SSH (ssh on the installed system;
the --ssh sub-flag sets it), AZ_INSTALL_FINAL (idle/reboot/shutdown), and
AZ_INSTALL_FINAL_INDICATION (True/False; the --final-indication sub-flag sets it).
Any prompt left un-seeded is asked interactively.
EOF
}}

run_gui() {{
    # XDG_RUNTIME_DIR is unset before elevating: `sudo -E` would otherwise pass main's
    # /run/user/1000 through to the root Qt process, which then logs a "runtime directory
    # is owned by uid 1000, not 0" warning. DISPLAY/XAUTHORITY (the load-bearing X vars)
    # are still preserved by -E, and root can read main's ~/.Xauthority, so Calamares
    # connects to the running X server fine.
    #
    # No `-c /etc/calamares`: that flag overrides the app-data dir and makes Calamares look
    # for qml/ under /etc/calamares (which does not exist), a fatal startup error.
    # Calamares already reads /etc/calamares/settings.conf and branding by default.
    #
    # QT_SCALE_FACTOR=1 for the installer ONLY: the session exports QT_SCALE_FACTOR=<scale>
    # AND a high Xft.dpi (=96*scale), and Qt would apply BOTH -- double-scaling Calamares to
    # ~scale*scale (~1.82x at 1.35), nearly full-screen. Pinning it to 1 leaves it scaled by
    # the DPI channel alone (~1.35x). `env QT_SCALE_FACTOR=1` re-sets it ACROSS the sudo
    # boundary (sudo -E would carry the session's value into root).
    unset XDG_RUNTIME_DIR
    export QT_SCALE_FACTOR=1
    exec sudo -E env QT_SCALE_FACTOR=1 calamares
}}

run_cli() {{
    # The scripted installer needs root and reads its payload from /root/azzio (mode 0750,
    # readable only by root -- so the existence check goes through sudo, not a bare test as
    # `main`). It honours AZ_INSTALL_CHOICE (1=auto largest disk, 2=manual) and AZ_INSTALL_DISK
    # for the disk step, and the AZ_INSTALL_{{HOSTNAME,USERNAME,FULLNAME,PASSWORD,ROOT_PASSWORD,
    # TIMEZONE}} family for the account/hostname/timezone answers, to run without prompts; with
    # none set it prompts interactively (fine over SSH). Each is forwarded ACROSS the sudo
    # boundary explicitly (only when set) so a restrictive sudoers env_reset cannot drop it.
    #
    # QUOTING: each forwarded value is `${{VAR:+"VAR=$VAR"}}` -- the DOUBLE QUOTES sit INSIDE the
    # `:+` alternate so the whole `VAR=value` stays ONE word even when the value contains spaces
    # (a password like "correct horse", a full name like "Ada Lovelace"), while an UNSET var
    # still expands to nothing at all (no stray empty argument). Unquoted, a value with a space
    # word-split into two args and `env` treated the second as the command to exec -- e.g.
    # `--username-password='correct horse'` died with `env: 'horse': No such file or directory`
    # (exit 127) and the installer never ran. The quotes fix that for every field.
    if ! sudo test -r '{INSTALL_CLI_SCRIPT_PATH}'; then
        echo "azzioinstall: CLI installer not found at {INSTALL_CLI_SCRIPT_PATH}" >&2
        exit 1
    fi
    exec sudo -E env \\
        ${{AZ_INSTALL_CHOICE:+"AZ_INSTALL_CHOICE=$AZ_INSTALL_CHOICE"}} \\
        ${{AZ_INSTALL_DISK:+"AZ_INSTALL_DISK=$AZ_INSTALL_DISK"}} \\
        ${{AZ_INSTALL_HOSTNAME:+"AZ_INSTALL_HOSTNAME=$AZ_INSTALL_HOSTNAME"}} \\
        ${{AZ_INSTALL_USERNAME:+"AZ_INSTALL_USERNAME=$AZ_INSTALL_USERNAME"}} \\
        ${{AZ_INSTALL_FULLNAME:+"AZ_INSTALL_FULLNAME=$AZ_INSTALL_FULLNAME"}} \\
        ${{AZ_INSTALL_PASSWORD:+"AZ_INSTALL_PASSWORD=$AZ_INSTALL_PASSWORD"}} \\
        ${{AZ_INSTALL_ROOT_PASSWORD:+"AZ_INSTALL_ROOT_PASSWORD=$AZ_INSTALL_ROOT_PASSWORD"}} \\
        ${{AZ_INSTALL_TIMEZONE:+"AZ_INSTALL_TIMEZONE=$AZ_INSTALL_TIMEZONE"}} \\
        ${{AZ_INSTALL_FILESYSTEM:+"AZ_INSTALL_FILESYSTEM=$AZ_INSTALL_FILESYSTEM"}} \\
        ${{AZ_INSTALL_STAR_PASSWORD:+"AZ_INSTALL_STAR_PASSWORD=$AZ_INSTALL_STAR_PASSWORD"}} \\
        ${{AZ_INSTALL_SSH:+"AZ_INSTALL_SSH=$AZ_INSTALL_SSH"}} \\
        ${{AZ_INSTALL_FINAL:+"AZ_INSTALL_FINAL=$AZ_INSTALL_FINAL"}} \\
        ${{AZ_INSTALL_FINAL_INDICATION:+"AZ_INSTALL_FINAL_INDICATION=$AZ_INSTALL_FINAL_INDICATION"}} \\
        bash '{INSTALL_CLI_SCRIPT_PATH}'
}}

run_auto() {{
    # Fully-unattended install (`-a`/`--instant`/`--automatic`). It is `run_cli` with EVERY answer
    # pre-seeded to a fixed default, so there is ONE install code path -- the scripted
    # installer -- and --instant is simply "the CLI installer, no questions asked". Not gated on a
    # display: it runs on the console and over SSH alike.
    #
    # Each default can be OVERRIDDEN by an --instant sub-flag (parsed below into az_opt_*; an unset
    # az_opt_* means "use the default here"). The `${{az_opt_X:-DEFAULT}}` idiom below is what
    # makes "whatever is omitted assumes default" true: a flag the user did not pass leaves its
    # az_opt_ empty, so the default wins.
    #
    #   disk      --disk: "auto" (default) = largest FIXED disk (AZ_INSTALL_CHOICE=1, skips
    #             removable/USB); any other value (e.g. "sda", "nvme0n1") = that device
    #             (AZ_INSTALL_CHOICE=2 + AZ_INSTALL_DISK). Whole disk, no swap, unencrypted.
    #   filesystem btrfs (AZ_INSTALL_FILESYSTEM=btrfs -- parity with the Calamares GUI's
    #             defaultFileSystemType). Not a sub-flag; --instant is always btrfs.
    #   hostname  --hostname (default "azzio")      user  --username (default "main")
    #   full name always blank (cosmetic GECOS; the scripted installer never prompts for it)
    #   timezone  --timezone (default "Asia/Jerusalem")   language English (fixed locale)
    #   passwords --username-password (default "admin") sets the user password. Root reuses it
    #             when --share-username-root-password is "True" (the default); with "False" the
    #             root password is --root-password (default "admin") instead. So a bare
    #             `azzioinstall --instant` gives user "admin" and root "admin" (shared) -- the
    #             values the spec's flag table lists as defaults.
    #   network   automatic DHCP -- the installed system enables NetworkManager with no static
    #             profile, which IS DHCP, so there is nothing to configure here.
    #   final     --final: what to do once the install finishes -- "idle" (default) returns to a
    #             shell after printing "you can reboot now"; "reboot" (alias "restart") reboots
    #             into the freshly installed system; "shutdown" powers off. Passed through as
    #             AZ_INSTALL_FINAL for the scripted installer's end-of-run step to honour.
    az_disk="${{az_opt_disk:-auto}}"
    if [ "$az_disk" = "auto" ]; then
        export AZ_INSTALL_CHOICE=1
    else
        export AZ_INSTALL_CHOICE=2
        export AZ_INSTALL_DISK="$az_disk"
    fi
    export AZ_INSTALL_HOSTNAME="${{az_opt_hostname:-azzio}}"
    export AZ_INSTALL_USERNAME="${{az_opt_username:-main}}"
    export AZ_INSTALL_FULLNAME=
    export AZ_INSTALL_TIMEZONE="${{az_opt_timezone:-Asia/Jerusalem}}"
    export AZ_INSTALL_FILESYSTEM=btrfs

    # SSH ON THE INSTALLED SYSTEM (`--ssh=<pw>`). Forwarded as AZ_INSTALL_SSH so the scripted
    # installer enables sshd on the INSTALLED box (NOT the live session -- see run_cli). An unset
    # az_opt_ssh leaves AZ_INSTALL_SSH empty and ssh stays off (the default). This is what the
    # instant ISO's baked hook uses (`--ssh=admin`): the live auto-install session has no ssh, but
    # the installed hypervisor does.
    export AZ_INSTALL_SSH="${{az_opt_ssh:-}}"

    # Passwords. --instant uses real passwords ("admin" by default), NOT the '*'/casper convention,
    # so the box is reachable by password out of the box (the operator workflow logs in as the
    # user with this password). AZ_INSTALL_STAR_PASSWORD is deliberately NOT set: the scripted
    # installer's identity step gates its whole password section on that marker, so leaving it
    # unset selects the real-password (chpasswd) path and honours AZ_INSTALL_PASSWORD /
    # AZ_INSTALL_ROOT_PASSWORD non-interactively.
    #
    # When --ssh is given but --username-password is NOT, the ssh password becomes the login
    # user's password too (`${{az_opt_ssh:-admin}}` fallback), so ONE `--ssh=<pw>` yields a
    # working, ssh-reachable account with no separate password flag. An explicit --username-password
    # still wins over both.
    export AZ_INSTALL_PASSWORD="${{az_opt_user_password:-${{az_opt_ssh:-admin}}}}"
    az_share="${{az_opt_share_root_password:-True}}"
    case "$az_share" in
        [tT][rR][uU][eE]) export AZ_INSTALL_ROOT_PASSWORD="$AZ_INSTALL_PASSWORD" ;;  # root == user
        *)                export AZ_INSTALL_ROOT_PASSWORD="${{az_opt_root_password:-admin}}" ;;
    esac

    # Post-install action. Default "idle" keeps today's behaviour (the installer prints
    # "you can reboot now" and returns to a shell). "restart" is normalised to "reboot" so
    # both spellings mean the same thing; "shutdown" powers off. Anything else is rejected
    # early rather than silently ignored (a typo like --final=rebot must not silently idle).
    az_final="${{az_opt_final:-idle}}"
    case "$az_final" in
        [rR][eE][sS][tT][aA][rR][tT]) az_final=reboot ;;
    esac
    case "$az_final" in
        idle|reboot|shutdown) ;;
        *) echo "azzioinstall: --final must be idle, reboot, restart, or shutdown (got '$az_final')" >&2
           usage >&2; exit 2 ;;
    esac
    export AZ_INSTALL_FINAL="$az_final"

    # Final indication. Default "False" keeps today's behaviour (no marker file). "True" makes the
    # scripted installer drop Shared/INSTALL_DONE into the live session's host<->guest shared folder
    # once the install body completes (just before the --final action), so a HOST watching the
    # exported `shared/` dir can conclude the unattended install succeeded -- indispensable for a
    # `--final=shutdown` ISO, where self-poweroff and a mid-install crash look identical from outside.
    # Normalised to exactly "True"/"False" (case-insensitively) so the installer's [tT][rR][uU][eE]
    # match is all it needs; a typo like --final-indication=Tru is rejected here rather than silently
    # treated as False (which would leave the host waiting on a marker that never comes).
    az_final_indication="${{az_opt_final_indication:-False}}"
    case "$az_final_indication" in
        [tT][rR][uU][eE])   az_final_indication=True ;;
        [fF][aA][lL][sS][eE]) az_final_indication=False ;;
        *) echo "azzioinstall: --final-indication must be True or False (got '$az_final_indication')" >&2
           usage >&2; exit 2 ;;
    esac
    export AZ_INSTALL_FINAL_INDICATION="$az_final_indication"
    run_cli
}}

# Parse the command line. A MODE must be chosen explicitly (-g/-c/-a); `mode` stays empty until
# one is set, and a bare invocation (or -h/--help) prints help and exits. The --instant sub-flags
# (--disk/--hostname/--username/--username-password/--share-username-root-password/
# --root-password/--timezone/--final) are collected into az_opt_* here and consumed by run_auto;
# they ONLY apply to --instant (see the post-loop gate). `az_seen_auto_flag` records that at least
# one sub-flag was passed so we can reject them when --instant was not.
mode=
az_seen_auto_flag=
# require_value "$@" : guard for a space-separated flag ("--flag val") -- error out with help
# if the flag has no following value ($# would be 1, just the flag). The caller then does
# `shift; var="$1"` to consume the value itself.
require_value() {{
    if [ "$#" -lt 2 ]; then
        echo "azzioinstall: option '$1' requires a value" >&2; usage >&2; exit 2
    fi
}}
while [ $# -gt 0 ]; do
    case "$1" in
        -g|--gui|--graphical-user-interface) mode=gui ;;
        -c|--cli|--command-line-interface) mode=cli ;;
        -a|--instant|--automatic) mode=auto ;;
        # --disk is dual-purpose: `--cli --disk <dev>` pre-seeds the interactive CLI installer's
        # disk step directly (AZ_INSTALL_CHOICE=2 + AZ_INSTALL_DISK), while `--instant --disk=<dev>`
        # (or "auto") is recorded as an override run_auto resolves. Recording it in az_opt_disk
        # AND (for the --cli combo) exporting the CHOICE/DISK pair keeps both working.
        --disk) require_value "$@"; shift; az_opt_disk="$1"; az_seen_auto_flag=1
                if [ "$az_opt_disk" != "auto" ]; then
                    AZ_INSTALL_CHOICE=2; AZ_INSTALL_DISK="$az_opt_disk"
                    export AZ_INSTALL_CHOICE AZ_INSTALL_DISK
                fi ;;
        --disk=*) az_opt_disk="${{1#--disk=}}"; az_seen_auto_flag=1
                if [ "$az_opt_disk" != "auto" ]; then
                    AZ_INSTALL_CHOICE=2; AZ_INSTALL_DISK="$az_opt_disk"
                    export AZ_INSTALL_CHOICE AZ_INSTALL_DISK
                fi ;;
        --hostname) require_value "$@"; shift; az_opt_hostname="$1"; az_seen_auto_flag=1 ;;
        --hostname=*) az_opt_hostname="${{1#--hostname=}}"; az_seen_auto_flag=1 ;;
        --username) require_value "$@"; shift; az_opt_username="$1"; az_seen_auto_flag=1 ;;
        --username=*) az_opt_username="${{1#--username=}}"; az_seen_auto_flag=1 ;;
        --username-password) require_value "$@"; shift; az_opt_user_password="$1"; az_seen_auto_flag=1 ;;
        --username-password=*) az_opt_user_password="${{1#--username-password=}}"; az_seen_auto_flag=1 ;;
        --share-username-root-password) require_value "$@"; shift; az_opt_share_root_password="$1"; az_seen_auto_flag=1 ;;
        --share-username-root-password=*) az_opt_share_root_password="${{1#--share-username-root-password=}}"; az_seen_auto_flag=1 ;;
        --root-password) require_value "$@"; shift; az_opt_root_password="$1"; az_seen_auto_flag=1 ;;
        --root-password=*) az_opt_root_password="${{1#--root-password=}}"; az_seen_auto_flag=1 ;;
        --timezone) require_value "$@"; shift; az_opt_timezone="$1"; az_seen_auto_flag=1 ;;
        --timezone=*) az_opt_timezone="${{1#--timezone=}}"; az_seen_auto_flag=1 ;;
        # --ssh=<pw>: enable sshd on the INSTALLED system (not the live session), with <pw> as the
        # login password when --username-password is not given. Valid with --instant AND --cli
        # (like --disk), so both a headless instant install and an interactive terminal install can
        # turn ssh on. az_seen_auto_flag is deliberately NOT set here: --ssh is allowed on --cli, so
        # it must not trip the "requires --instant" gate the OTHER sub-flags share.
        --ssh) require_value "$@"; shift; az_opt_ssh="$1" ;;
        --ssh=*) az_opt_ssh="${{1#--ssh=}}" ;;
        --final) require_value "$@"; shift; az_opt_final="$1"; az_seen_auto_flag=1 ;;
        --final=*) az_opt_final="${{1#--final=}}"; az_seen_auto_flag=1 ;;
        --final-indication) require_value "$@"; shift; az_opt_final_indication="$1"; az_seen_auto_flag=1 ;;
        --final-indication=*) az_opt_final_indication="${{1#--final-indication=}}"; az_seen_auto_flag=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "azzioinstall: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

# GATE: the identity/disk sub-flags only make sense with --instant (they seed run_auto's answers).
# The lone exception is `--cli --disk <dev>`, the long-standing combo that pre-seeds the
# interactive installer's disk prompt -- so --disk with --cli is allowed, but the OTHER sub-flags
# (hostname/username/passwords/timezone/final) require --instant. Reject a sub-flag given without
# --instant rather than silently ignoring it (a silent no-op would install with defaults the user
# thought they had overridden).
if [ "$mode" != "auto" ] && [ -n "$az_seen_auto_flag" ]; then
    az_bad=
    [ -n "$az_opt_hostname" ] && az_bad="$az_bad --hostname"
    [ -n "$az_opt_username" ] && az_bad="$az_bad --username"
    [ -n "$az_opt_user_password" ] && az_bad="$az_bad --username-password"
    [ -n "$az_opt_share_root_password" ] && az_bad="$az_bad --share-username-root-password"
    [ -n "$az_opt_root_password" ] && az_bad="$az_bad --root-password"
    [ -n "$az_opt_timezone" ] && az_bad="$az_bad --timezone"
    [ -n "$az_opt_final" ] && az_bad="$az_bad --final"
    [ -n "$az_opt_final_indication" ] && az_bad="$az_bad --final-indication"
    # --disk is allowed alongside --cli; only flag it here if --disk was given without --cli either.
    [ -n "$az_opt_disk" ] && [ "$mode" != "cli" ] && az_bad="$az_bad --disk"
    if [ -n "$az_bad" ]; then
        echo "azzioinstall: these options require --instant:$az_bad" >&2
        usage >&2
        exit 2
    fi
fi

# GATE (--ssh): --ssh enables sshd on the INSTALLED system, so it only means something for an
# install mode -- --instant (headless) or --cli (interactive). It is NOT a run_auto sub-flag (it
# does not set az_seen_auto_flag, so the block above never sees it), and it does NOTHING under
# --gui (Calamares) or a bare invocation, so reject it there rather than silently drop it.
if [ -n "$az_opt_ssh" ] && [ "$mode" != "auto" ] && [ "$mode" != "cli" ]; then
    echo "azzioinstall: --ssh requires --instant or --cli (it enables ssh on the installed system)." >&2
    usage >&2
    exit 2
fi

# --cli reaches run_cli DIRECTLY (not via run_auto), so export AZ_INSTALL_SSH here for the
# interactive path -- run_cli forwards it across the sudo -E env line, but only if it is already
# in the environment. run_auto re-exports it from az_opt_ssh itself, so this is idempotent for
# --instant; empty az_opt_ssh (ssh not requested) leaves it unset and ssh stays off.
[ -n "$az_opt_ssh" ] && export AZ_INSTALL_SSH="$az_opt_ssh"

case "$mode" in
    gui) run_gui ;;
    cli) run_cli ;;
    auto) run_auto ;;
    *) usage; exit 0 ;;    # no mode chosen (bare `azzioinstall`) -> help, no install.
esac
"""


# --- 10. Emit plan ----------------------------------------------------------
# Declarative map so compiler.py can iterate. Each entry: the builder function that
# produces the content, the DESTINATION (absolute, or $HOME-relative for the live
# `main` user), and the file MODE. `owner` records the intended chown so compiler.py knows
# which files fall under the /home/main (uid 1000, gid 998) handback.
#
# HOME-relative paths are given relative to /home/main so the airootfs overlay lands
# them under airootfs/home/main/...; compiler.py chowns that whole tree 1000:998 after
# emit (as it already does for the fastfetch/first-boot payloads). Absolute paths
# (/usr/local/bin/..., /usr/share/...) stay root-owned (0:0) -- do NOT chown them.

# scripts -> 0o755, configs -> 0o644.
_EXEC = 0o755
_CONF = 0o644

# Each PLAN entry is a dict for readability in compiler.py:
#   builder: callable() -> str content
#   dest:    absolute path in the airootfs (already resolved under /home/main for user
#            files, so compiler.py just prefixes the airootfs root)
#   mode:    octal file mode
#   owner:   "home" (chown 1000:998 with the rest of /home/main) or "root"
PLAN = [
    {
        "builder": xinitrc,
        "dest": f"{HOME}/.xinitrc",
        "mode": _EXEC,
        "owner": "home",
    },
    {
        # OpenBox window-manager config: keybinds (Super -> menu via xcape's W-Menu),
        # window management, the doubled-titlebar theme + title font, and the FULL
        # titlebar-button mouse bindings (so min/max/close work). The desktop right-click
        # menu is disabled (empty Root context). No panel/dock config -- the Azzio menu
        # is the only shell. Home-owned; mirrored into /etc/skel. rc.xml is a plain
        # config (0644).
        "builder": openbox_rc_xml,
        "dest": f"{HOME}/.config/openbox/rc.xml",
        "mode": _CONF,
        "owner": "home",
    },
    {
        # The DARK Azzio OpenBox THEME (the default; flat-colour grown titlebar). Ships to
        # ~/.themes/Azzio-Dark/openbox-3/themerc (a user theme search path); rc.xml's
        # <theme> names it "Azzio-Dark" out of the box. Home-owned; mirrored into
        # /etc/skel. Plain data (0o644).
        "builder": openbox_theme_rc_dark,
        "dest": OPENBOX_THEME_THEMERC_DARK,
        "mode": _CONF,
        "owner": "home",
    },
    {
        # The LIGHT Azzio OpenBox THEME (classic Clearlooks-cyan). Ships to
        # ~/.themes/Azzio/openbox-3/themerc so `azzio theme --white` can switch rc.xml's
        # <theme><name> to "Azzio" and have the themerc already present. Home-owned;
        # mirrored into /etc/skel. Plain data (0o644).
        "builder": openbox_theme_rc_light,
        "dest": OPENBOX_THEME_THEMERC,
        "mode": _CONF,
        "owner": "home",
    },
    {
        # System theme DEFAULT (DARK) -- GTK3 theme file. The freedesktop/GTK standard any
        # downloaded GTK3 app reads at startup; `azzio theme --white` rewrites it. Home file.
        "builder": gtk3_settings_ini_default,
        "dest": GTK3_SETTINGS_PATH,
        "mode": _CONF,
        "owner": "home",
    },
    {
        # System theme DEFAULT (DARK) -- GTK4 theme file (same, for GTK4 apps that read it).
        "builder": gtk4_settings_ini_default,
        "dest": GTK4_SETTINGS_PATH,
        "mode": _CONF,
        "owner": "home",
    },
    {
        # System theme DEFAULT (DARK) -- GTK2 theme file (~/.gtkrc-2.0, older GTK2 apps).
        "builder": gtkrc2_default,
        "dest": GTKRC2_PATH,
        "mode": _CONF,
        "owner": "home",
    },
    {
        # System theme DEFAULT (DARK) -- dconf keyfile making color-scheme 'prefer-dark' the
        # system default (compiled by `dconf update` in the customize hook). Root-owned /etc.
        "builder": dconf_theme_keyfile,
        "dest": DCONF_THEME_KEYFILE_PATH,
        "mode": _CONF,
        "owner": "root",
    },
    {
        # The dconf profile so the system `local` db backs the user db. Root-owned /etc.
        "builder": dconf_profile_user,
        "dest": DCONF_PROFILE_USER_PATH,
        "mode": _CONF,
        "owner": "root",
    },
    {
        # OUR picom compositor config (fading OFF, frames FULLY OPAQUE). The autostart runs
        # `picom --config` against this file so picom does NOT use the packaged
        # /etc/xdg/picom.conf, whose window-fading + 0.9 frame-opacity defaults are the
        # reported "apps fade in/out" and "transparent titlebar" bugs. Root-owned /etc file,
        # shared by the live and installed sessions (the autostart is shared).
        "builder": picom_conf,
        "dest": PICOM_CONFIG_PATH,
        "mode": _CONF,
        "owner": "root",
    },
    {
        # ~/.Xresources -- the GLOBAL SCALE backbone (Xft.dpi + Xcursor.size, derived from
        # packages/openbox/scale). Loaded by `xrdb -merge` in ~/.xinitrc. Home file, skel-mirrored.
        "builder": xresources,
        "dest": XRESOURCES_PATH,
        "mode": _CONF,
        "owner": "home",
    },
    {
        # OpenBox session autostart: wallpaper (feh), keyboard layouts (setxkbmap),
        # Super key (xcape), the application-menu daemon, and the first-run installer.
        # Sourced by openbox-session, so it must be EXECUTABLE (0o755). Home-owned;
        # mirrored into /etc/skel. (openbox-session runs it via /bin/sh, but shipping
        # it executable matches the shebang and is harmless.)
        "builder": openbox_autostart,
        "dest": f"{HOME}/.config/openbox/autostart",
        "mode": _EXEC,
        "owner": "home",
    },
    {
        # OpenBox session environment (sourced before autostart): XDG base dirs +
        # XDG_CURRENT_DESKTOP=openbox. Home-owned; mirrored into /etc/skel.
        "builder": openbox_environment,
        "dest": f"{HOME}/.config/openbox/environment",
        "mode": _CONF,
        "owner": "home",
    },
    {
        # The "installed" OpenBox autostart, STAGED on the ISO (root-owned system path,
        # NOT a per-user file). The Calamares OFFLINE install copies it over the target's
        # inherited live autostart (home + skel) so the installed system drops the two
        # live-only lines (fixed us,il keyboard + first-run installer). Executable so the
        # copied-into-place file is runnable by openbox-session.
        "builder": openbox_autostart_installed,
        "dest": INSTALLED_AUTOSTART_STAGING_PATH,
        "mode": _EXEC,
        "owner": "root",
    },
    {
        # Seed OUR menu's launch-frequency store so a fresh profile opens with
        # LibreWolf, kitty, the file manager at the top (it otherwise sorts
        # alphabetically with no history). Home-owned data file (0o644), mirrored into
        # /etc/skel so a Calamares-installed user inherits the same starting order.
        # Fully dynamic afterwards -- the daemon re-sorts as apps are opened.
        "builder": az_menu_usage_seed_json,
        "dest": _app_menu.MENU_USAGE_SEED_SYSTEM_PATH,
        "mode": _CONF,
        "owner": "home",
    },
    {
        "builder": install_menu_desktop,
        "dest": INSTALL_MENU_DESKTOP_PATH,
        "mode": _CONF,
        "owner": "root",
    },
    {
        # The Desktop launcher must be EXECUTABLE (0o755) so a file manager launches it
        # on double-click without an untrusted-.desktop prompt.
        "builder": desktop_installer_launcher,
        "dest": f"{HOME}/Desktop/azzioinstall.desktop",
        "mode": _EXEC,
        "owner": "home",
    },
    {
        "builder": install_wrapper_sh,
        "dest": INSTALL_WRAPPER_PATH,
        "mode": _EXEC,
        "owner": "root",
    },
    {
        "builder": azzio_command_line_interface,
        "dest": AZZIO_BIN_PATH,
        "mode": _EXEC,
        "owner": "root",
    },
    # NOTE: the media OSD indicator (/usr/local/lib/azzio/azzio-osd) is NOT emitted here as a
    # text file anymore -- it is a COMPILED C program (on_screen_display.c). compiler.py builds and installs it
    # via terminal_user_interface_build.build_osd(), exactly like the terminal UI binary. It is
    # still pinned 0755 in FILE_PERMISSIONS so archiso ships it executable.
]

# The .bash_profile snippet is handled separately from PLAN because it is not a
# whole-file replacement conceptually (it is the login bootstrap). compiler.py still
# writes it as the full file content of /home/main/.bash_profile (there is no stock one
# in the airootfs to preserve), mode 0644, owner "home".
BASH_PROFILE_DEST = f"{HOME}/.bash_profile"


def emit_plan() -> list[dict]:
    """Return the PLAN list (builder/dest/mode/owner) plus the .bash_profile entry, so
    compiler.py can iterate a single sequence. Kept as a function (not just the module
    constant) to mirror the builder-function style of the other configuration modules
    and to keep the .bash_profile special-case in one place."""
    return PLAN + [
        {
            "builder": bash_profile_startx,
            "dest": BASH_PROFILE_DEST,
            "mode": _CONF,
            "owner": "home",
        },
    ]
