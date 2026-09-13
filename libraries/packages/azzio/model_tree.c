/* Azzio bare-`azzio` terminal user interface (C) -- the screen TREE (data + accessors).
 *
 * Split out of model.c (which grew past the per-file size budget): model.c keeps the shared
 * infrastructure (az_capture, the probe cache, `have`, the network/theme/wallpaper/volume/
 * brightness/machine status probes, the Default Applications + Display probes, the filter and
 * the row-command/base helpers), and THIS file holds the whole navigable tree as static data --
 * the ROWS_* tables and the SCREENS[] array -- plus az_screens/az_screen_find/az_screen_count.
 *
 * The tree references the probe function pointers and the AzRow/AzScreen/AzActKind/AzPreviewKind
 * types by name; all of them are declared in terminal_user_interface.h, so this TU only needs
 * that header. Keeping the data here (and the logic in model.c) keeps both files well under the
 * size limit and makes the screen tree easy to read as one contiguous table.
 */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1

#include "terminal_user_interface.h"

#include <string.h>

/* AZ_DA_DIRS_LINE (the .desktop directory disclosure line) is declared in
 * terminal_user_interface.h -- it is shared with model_default_applications.c (the runtime Default
 * Applications screens) and used by the category-list screen subtitle below. */

/* AZ_WALLPAPERS_DIR / AZ_WALLPAPER_RES are used by the wallpaper rows' base commands below.
 * They are defined in model.c too (for az_wallpaper_image); kept in lock-step with wallpaper.py
 * (a test pins the strings). Redefined here for the row base-command string literals. */
#ifndef AZ_WALLPAPERS_DIR
#define AZ_WALLPAPERS_DIR "/usr/share/wallpapers"
#endif
#ifndef AZ_WALLPAPER_RES
#define AZ_WALLPAPER_RES  "1672x941"
#endif

/* --- the screen tree -------------------------------------------------------- */
/* Actions are shell command lines run through the installed `azzio` command line interface. main.c runs
 * them INSIDE the UI (output captured, shown in the results overlay), then shows a result. */

/* All rows use DESIGNATED initializers: any field not named is zero (NULL / AZ_PV_NONE /
 * needs_root==0 / show_output==0), so adding a field never forces touching every row and the
 * intent of each row is self-documenting. `.needs_root = 1` marks an apply that first secures a
 * sudo credential; `.show_output = 1` shows its captured output in the overlay. */

/* Network is FIRST (it is what a fresh machine needs first). The main rows keep their live
 * status -- it is a genuine at-a-glance summary of the sub-screen (e.g. "firewall active"),
 * NOT a redundant echo, and the main screen has no "Current:" line of its own. */
static const AzRow ROWS_MAIN[] = {
    {.label="Network",      .kind=AZ_ACT_SCREEN, .target="network",    .status=az_status_network},
    {.label="Theme",        .kind=AZ_ACT_SCREEN, .target="theme",      .status=az_status_theme},
    {.label="Wallpaper",    .kind=AZ_ACT_SCREEN, .target="wallpaper",  .status=az_status_wallpaper},
    {.label="Volume",       .kind=AZ_ACT_SCREEN, .target="volume",     .status=az_status_volume},
    {.label="Brightness",   .kind=AZ_ACT_SCREEN, .target="brightness", .status=az_status_brightness},
    {.label="Default Applications", .kind=AZ_ACT_SCREEN, .target="defaultapps"},
    {.label="Display",      .kind=AZ_ACT_SCREEN, .target="display",    .status=az_status_display},
    {.label="GPU",          .kind=AZ_ACT_SCREEN, .target="gpu",        .status=az_status_gpu},
    {.label="Machine Type", .kind=AZ_ACT_SCREEN, .target="machine",    .status=az_status_machine},
    {.label="Time & Date",  .kind=AZ_ACT_SCREEN, .target="timedate",   .status=az_status_timedate},
    {.label="Language",     .kind=AZ_ACT_SCREEN, .target="language",   .status=az_status_language},
    /* Hypervisor: the per-directory VM runner's GLOBAL install defaults (its status summarises
     * ram/cpus/disk/net). Sits just before the opt-in Backup entry. */
    {.label="Hypervisor",   .kind=AZ_ACT_SCREEN, .target="hypervisor", .status=az_status_hypervisor},
    /* Power: shutdown / restart / sleep / lock, with optional timers -- a completionist
     * home in the UI for the session/power controls (the spec asks for these options here). */
    {.label="Power",        .kind=AZ_ACT_SCREEN, .target="power",      .status=az_status_power},
    /* Backup is LAST -- an opt-in reached once, after the day-to-day settings above. */
    {.label="Backup",       .kind=AZ_ACT_SCREEN, .target="backup",     .status=az_status_backup},
};

/* Theme / Wallpaper rows carry NO per-row status: the live state is shown ONCE as the
 * "Current:" line at the top of the screen (the screen's `current` probe), so echoing
 * "white"/"years" after each option would just be noise -- exactly what the spec calls out.
 * Applying a theme/wallpaper needs no sudo (it configures the user session), so needs_root
 * stays 0; the apply still runs inside the UI (captured), so no command line interface text flashes over it. */
static const AzRow ROWS_THEME[] = {
    {.label="Dark",  .kind=AZ_ACT_APPLY, .target="azzio theme --dark",
     .base="gsettings set org.gnome.desktop.interface color-scheme prefer-dark",
     .preview=AZ_PV_THEME, .preview_arg="dark"},
    {.label="White", .kind=AZ_ACT_APPLY, .target="azzio theme --white",
     .base="gsettings set org.gnome.desktop.interface color-scheme prefer-light",
     .preview=AZ_PV_THEME, .preview_arg="white"},
};

static const AzRow ROWS_WALLPAPER[] = {
    {.label="Years",   .kind=AZ_ACT_APPLY, .target="azzio wallpaper --years.png",
     .base="feh --no-fehbg --bg-fill " AZ_WALLPAPERS_DIR "/years/contents/images/" AZ_WALLPAPER_RES ".png",
     .preview=AZ_PV_WALLPAPER, .preview_arg="years"},
    {.label="Decades", .kind=AZ_ACT_APPLY, .target="azzio wallpaper --decades.png",
     .base="feh --no-fehbg --bg-fill " AZ_WALLPAPERS_DIR "/decades/contents/images/" AZ_WALLPAPER_RES ".png",
     .preview=AZ_PV_WALLPAPER, .preview_arg="decades"},
};

static const AzRow ROWS_NETWORK[] = {
    {.label="Wifi",          .kind=AZ_ACT_SCREEN, .target="network.wifi",      .status=az_status_wifi},
    {.label="Wired",         .kind=AZ_ACT_SCREEN, .target="network.wired",     .status=az_status_wired},
    /* IP Address: static (manual IPv4/subnet/gateway/DNS) vs dynamic (DHCP) on an interface --
     * the live control-surface twin of the Calamares installer "Network" page. Its own screen
     * (network.ip) shows the active address and sets a static/dynamic address via `azzio
     * network ip`. */
    {.label="IP Address",    .kind=AZ_ACT_SCREEN, .target="network.ip",        .status=az_status_ip},
    {.label="Bluetooth",     .kind=AZ_ACT_SCREEN, .target="network.bluetooth", .status=az_status_bluetooth},
    {.label="Airplane mode", .kind=AZ_ACT_SCREEN, .target="network.airplane",  .status=az_status_airplane},
    {.label="Firewall",      .kind=AZ_ACT_SCREEN, .target="network.firewall",  .status=az_status_firewall},
    {.label="SSH Server",    .kind=AZ_ACT_SCREEN, .target="network.ssh",       .status=az_status_ssh},
};

/* The sub-screen action rows carry NO per-row .status -- the live state is shown ONCE as the
 * screen's "Current:" line (its .current probe), exactly like Theme/Wallpaper. This is the fix
 * for the repeated "radio enabled" spam: every row on a screen was echoing the same probe. */
/* Every network apply runs privileged tools (nmcli/rfkill/systemctl/ufw), so needs_root=1:
 * the UI secures a sudo credential (masked, in-UI, cached) before running it, and runs it
 * captured -- no black screen, no scrollback. The list/scan verbs set show_output=1 so their
 * table lands in the results overlay; the toggles just show a one-line result. */
static const AzRow ROWS_WIFI[] = {
    {.label="Turn wifi on",         .kind=AZ_ACT_APPLY, .target="azzio network wifi on",   .needs_root=1,
     .base="sudo nmcli radio wifi on"},
    {.label="Turn wifi off",        .kind=AZ_ACT_APPLY, .target="azzio network wifi off",  .needs_root=1,
     .base="sudo nmcli radio wifi off"},
    {.label="Scan / list networks", .kind=AZ_ACT_APPLY, .target="azzio network wifi list", .needs_root=1, .show_output=1,
     .base="nmcli -f IN-USE,SSID,SIGNAL,SECURITY device wifi list"},
    {.label="Disconnect",           .kind=AZ_ACT_APPLY, .target="azzio network wifi disconnect", .needs_root=1,
     .base="sudo nmcli device disconnect <iface>"},
};

static const AzRow ROWS_WIRED[] = {
    {.label="Turn wired on",  .kind=AZ_ACT_APPLY, .target="azzio network wired on",  .needs_root=1,
     .base="sudo nmcli device connect <iface>"},
    {.label="Turn wired off", .kind=AZ_ACT_APPLY, .target="azzio network wired off", .needs_root=1,
     .base="sudo nmcli device disconnect <iface>"},
};

static const AzRow ROWS_BLUETOOTH[] = {
    {.label="Turn bluetooth on",   .kind=AZ_ACT_APPLY, .target="azzio network bluetooth on",  .needs_root=1,
     .base="sudo systemctl enable --now bluetooth"},
    {.label="Turn bluetooth off",  .kind=AZ_ACT_APPLY, .target="azzio network bluetooth off", .needs_root=1,
     .base="sudo systemctl disable --now bluetooth"},
    {.label="Scan / list devices", .kind=AZ_ACT_APPLY, .target="azzio network bluetooth scan", .needs_root=1, .show_output=1,
     .base="bluetoothctl devices"},
};

static const AzRow ROWS_AIRPLANE[] = {
    {.label="Turn airplane mode on",  .kind=AZ_ACT_APPLY, .target="azzio network airplane on", .needs_root=1,
     .base="sudo nmcli networking off"},
    {.label="Turn airplane mode off", .kind=AZ_ACT_APPLY, .target="azzio network airplane off", .needs_root=1,
     .base="sudo nmcli networking on"},
};

/* IP Address: the live twin of the Calamares installer "Network" page (static IPv4 vs DHCP),
 * so a fresh machine can pin ipv4/subnet/gateway/DNS from the terminal user interface too.
 * "Show" is a plain read (no root). The two setters are AZ_ACT_PROMPT rows: the UI collects
 * a free-text argument line and appends it to the wrapper (mirroring the firewall port prompts
 * and the Backup enable rows), so no dropping to a shell. They wrap `azzio network ip
 * static|dynamic`, which edits the device's NetworkManager connection (needs_root=1). The
 * static row takes the whole "<iface> <addr/prefix> <gateway> [dns...]" line; dynamic takes
 * just "<iface>". The subnet mask is expressed as the CIDR prefix (e.g. /24), exactly as
 * `azzio network ip static` documents. */
static const AzRow ROWS_IP[] = {
    {.label="Show IP configuration", .kind=AZ_ACT_APPLY, .target="azzio network ip show", .show_output=1,
     .base="nmcli -f DEVICE,TYPE,STATE,CONNECTION device status"},
    {.label="Set static IPv4 (type: iface addr/prefix gateway [dns...])",
     .kind=AZ_ACT_PROMPT, .target="azzio network ip static", .needs_root=1, .show_output=1,
     .prompt="iface addr/prefix gateway [dns...]:",
     .base="sudo nmcli connection modify <conn> ipv4.method manual ipv4.addresses ... && sudo nmcli connection up <conn>"},
    {.label="Set dynamic / DHCP (type: iface)",
     .kind=AZ_ACT_PROMPT, .target="azzio network ip dynamic", .needs_root=1, .show_output=1,
     .prompt="interface:",
     .base="sudo nmcli connection modify <conn> ipv4.method auto && sudo nmcli connection up <conn>"},
};

/* Firewall: enable/disable, LIST the port rules right here in the overlay (show_output=1),
 * and open/close/delete a port by TYPING its number (AZ_ACT_PORT prompts, then appends the
 * port to the command). This is the in-UI firewall config the spec asks for -- no dropping
 * to a shell, no guessing the command line interface. */
static const AzRow ROWS_FIREWALL[] = {
    {.label="Enable firewall",   .kind=AZ_ACT_APPLY, .target="azzio network firewall enable",  .needs_root=1,
     .base="sudo ufw --force enable"},
    {.label="Disable firewall",  .kind=AZ_ACT_APPLY, .target="azzio network firewall disable", .needs_root=1,
     .base="sudo ufw disable"},
    {.label="List ports",        .kind=AZ_ACT_APPLY, .target="azzio network firewall port list", .needs_root=1, .show_output=1,
     .base="sudo ufw status numbered"},
    {.label="Open a port",       .kind=AZ_ACT_PORT,  .target="azzio network firewall port open",   .needs_root=1, .show_output=1,
     .base="sudo ufw allow"},
    {.label="Close a port",      .kind=AZ_ACT_PORT,  .target="azzio network firewall port close",  .needs_root=1, .show_output=1,
     .base="sudo ufw deny"},
    {.label="Delete a port rule", .kind=AZ_ACT_PORT, .target="azzio network firewall port delete", .needs_root=1, .show_output=1,
     .base="sudo ufw delete allow"},
    /* DEFAULT-policy control (the "general incoming and outgoing rule configuration" the spec
     * asks the UI to display AND control). The screen's "Current:" line shows the live default
     * (az_status_firewall_policy); these rows SET it, wrapping `azzio network firewall default
     * <in> <out>`. The Azzio baseline is deny incoming + allow outgoing; the two "reset"
     * rows restore it, and the openers/closers flip one side. */
    {.label="Show default policy", .kind=AZ_ACT_APPLY, .target="azzio network firewall status", .needs_root=1, .show_output=1,
     .base="sudo ufw status verbose"},
    {.label="Default: deny incoming, allow outgoing (recommended)", .kind=AZ_ACT_APPLY,
     .target="azzio network firewall default deny allow", .needs_root=1,
     .base="sudo ufw default deny incoming && sudo ufw default allow outgoing"},
    {.label="Allow incoming (open -- not recommended)", .kind=AZ_ACT_APPLY,
     .target="azzio network firewall default allow allow", .needs_root=1,
     .base="sudo ufw default allow incoming"},
    {.label="Deny outgoing (lock down)", .kind=AZ_ACT_APPLY,
     .target="azzio network firewall default deny deny", .needs_root=1,
     .base="sudo ufw default deny outgoing"},
};

/* SSH Server: start/stop the ssh server and manage the firewall for it, from ONE screen (the
 * spec: an "SSH Server" option under Network with everything -- start/stop, firewall notice,
 * a button for `azzio --sshd-hypervisor`, brief explanations, base-vs-wrapper commands). The
 * default desktop ships ssh OFF; this is where a user turns it on/off deliberately. "Start"
 * runs the full bring-up (opens :22/tcp then enables sshd); the firewall row opens the port on
 * its own; "Status" shows sshd + the port. Every action is privileged (needs_root=1). */
static const AzRow ROWS_SSH[] = {
    {.label="Start ssh server (open :22 + enable sshd)", .kind=AZ_ACT_APPLY,
     .target="azzio network ssh start", .needs_root=1, .show_output=1,
     .base="sudo ufw allow 22/tcp && sudo systemctl enable --now sshd"},
    {.label="Stop ssh server (disable sshd + close :22)", .kind=AZ_ACT_APPLY,
     .target="azzio network ssh stop", .needs_root=1, .show_output=1,
     .base="sudo systemctl disable --now sshd && sudo ufw delete allow 22/tcp"},
    {.label="Status (sshd + firewall :22)", .kind=AZ_ACT_APPLY,
     .target="azzio network ssh status", .needs_root=1, .show_output=1,
     .base="systemctl is-active sshd; sudo ufw status"},
    /* Root SSH login: DENIED by default (only the end user's own account may log in).
     * These two rows flip the 00-azzio-root-login.conf drop-in (named `00-` so it sorts
     * FIRST and, with sshd's first-match-wins, authoritatively wins over any other drop-in)
     * and reload sshd. Enabling root login widens exposure, so it is labelled INSECURE;
     * "off" restores the default. */
    {.label="Enable root SSH login (INSECURE)", .kind=AZ_ACT_APPLY,
     .target="azzio network ssh root on", .needs_root=1, .show_output=1,
     .base="echo 'PermitRootLogin yes' | sudo tee /etc/ssh/sshd_config.d/00-azzio-root-login.conf && sudo systemctl reload sshd"},
    {.label="Disable root SSH login (default)", .kind=AZ_ACT_APPLY,
     .target="azzio network ssh root off", .needs_root=1, .show_output=1,
     .base="echo 'PermitRootLogin no' | sudo tee /etc/ssh/sshd_config.d/00-azzio-root-login.conf && sudo systemctl reload sshd"},
    {.label="Echo root login status (allowed or denied)", .kind=AZ_ACT_APPLY,
     .target="azzio network ssh root status", .needs_root=1, .show_output=1,
     .base="sudo sshd -T | grep -i PermitRootLogin"},
    {.label="Set up for hypervisor (install host key + start sshd)", .kind=AZ_ACT_APPLY,
     .target="azzio --sshd-hypervisor", .needs_root=1, .show_output=1,
     .base="sudo azzio --sshd-hypervisor"},
    {.label="Open :22 in firewall only", .kind=AZ_ACT_APPLY,
     .target="azzio network firewall port open 22/tcp", .needs_root=1, .show_output=1,
     .base="sudo ufw allow 22/tcp"},
    {.label="Close :22 in firewall only", .kind=AZ_ACT_APPLY,
     .target="azzio network firewall port delete 22/tcp", .needs_root=1, .show_output=1,
     .base="sudo ufw delete allow 22/tcp"},
};

/* Power: shutdown / restart / sleep / lock, plus scheduled timers. The immediate power
 * actions call `systemctl poweroff|reboot|suspend` under the hood (needs_root=1 so the UI
 * secures a sudo credential first); lock needs no root. The "in ..." rows are AZ_ACT_PROMPT
 * rows: the UI collects a duration (e.g. 30m) and appends it to `azzio power <verb> --in`.
 * Cancel/status wrap the same. The "Current:" line shows whether a timer is pending
 * (az_status_power). This is the completionist power menu the spec asks for. */
static const AzRow ROWS_POWER[] = {
    {.label="Shut down now", .kind=AZ_ACT_APPLY, .target="azzio power shutdown", .needs_root=1,
     .base="sudo systemctl poweroff"},
    {.label="Restart now", .kind=AZ_ACT_APPLY, .target="azzio power restart", .needs_root=1,
     .base="sudo systemctl reboot"},
    {.label="Sleep (suspend) now", .kind=AZ_ACT_APPLY, .target="azzio power sleep", .needs_root=1,
     .base="sudo systemctl suspend"},
    {.label="Lock screen", .kind=AZ_ACT_APPLY, .target="azzio power lock",
     .base="loginctl lock-session"},
    {.label="Schedule shutdown in...", .kind=AZ_ACT_PROMPT, .target="azzio power shutdown --in",
     .prompt="Delay (e.g. 30m, 1h, 90s, or a number of minutes):", .needs_root=1, .show_output=1,
     .base="sudo shutdown -h +<minutes>  (via a systemd-run timer)"},
    {.label="Schedule restart in...", .kind=AZ_ACT_PROMPT, .target="azzio power restart --in",
     .prompt="Delay (e.g. 30m, 1h, 90s, or a number of minutes):", .needs_root=1, .show_output=1,
     .base="sudo shutdown -r +<minutes>  (via a systemd-run timer)"},
    {.label="Timer status", .kind=AZ_ACT_APPLY, .target="azzio power shutdown --status", .show_output=1,
     .base="systemctl list-timers azzio-shutdown.timer"},
    {.label="Cancel scheduled shutdown", .kind=AZ_ACT_APPLY, .target="azzio power shutdown --cancel", .needs_root=1,
     .base="sudo systemctl stop azzio-shutdown.timer"},
    {.label="Cancel scheduled restart", .kind=AZ_ACT_APPLY, .target="azzio power restart --cancel", .needs_root=1,
     .base="sudo systemctl stop azzio-restart.timer"},
};

/* Machine Type: show what Azzio recognises (PC or Laptop) via the "Current:" line, and let
 * the user HARD-SWITCH it -- Force PC / Force Laptop / Autodetect. The switch decides whether
 * the brightness controls are offered (a PC has no backlight), so forcing "Laptop" turns them
 * on even on a desktop. These write the user's own config pointer (no sudo), so needs_root
 * stays 0; each runs captured inside the UI and shows its one-line result. */
static const AzRow ROWS_MACHINE[] = {
    /* Machine type is a pure config-pointer write (~/.config/azzio/machine-type) -- there is
     * no system tool behind it, so the "base command" is the equivalent file write / removal. */
    {.label="Force PC",   .kind=AZ_ACT_APPLY, .target="azzio machine --pc",
     .base="printf 'PC\\n' > ~/.config/azzio/machine-type"},
    {.label="Force Laptop", .kind=AZ_ACT_APPLY, .target="azzio machine --laptop",
     .base="printf 'Laptop\\n' > ~/.config/azzio/machine-type"},
    {.label="Autodetect", .kind=AZ_ACT_APPLY, .target="azzio machine --auto",
     .base="rm -f ~/.config/azzio/machine-type"},
};

/* GPU: detect the machine's GPU and resolve its drivers from the baked-in offline repo. The
 * ISO ships a generic stack (mesa) that works everywhere; --resolve installs the RIGHT vendor +
 * developer drivers. Resolve installs packages (pacman), so needs_root=1; the status/list rows
 * just read, so they only show_output. Outdated drivers are `pacman -Syu`, not this screen. */
static const AzRow ROWS_GPU[] = {
    {.label="Detect & resolve GPU drivers", .kind=AZ_ACT_APPLY, .target="azzio gpu --resolve",
     .needs_root=1, .show_output=1, .base="sudo pacman -Sy --needed <vendor+dev drivers>"},
    {.label="Show detected GPU / drivers",  .kind=AZ_ACT_APPLY, .target="azzio gpu",
     .show_output=1, .base="lspci | grep -i vga"},
    {.label="List driver map",              .kind=AZ_ACT_APPLY, .target="azzio gpu --list",
     .show_output=1, .base="azzio gpu --list"},
};

/* Time & Date: resolve the timezone by IP geolocation. --resolve normally asks the user to PICK
 * one of 5 servers on stdin -- but the UI runs commands with output CAPTURED and stdin from
 * /dev/null, so that interactive prompt can never be answered here. Instead this is an
 * AZ_ACT_PROMPT row: the UI collects the server number in its own in-field prompt and appends it,
 * running "azzio timedate --resolve --server <N>" (the non-interactive resolver path, which uses
 * the FIXED server order the prompt lists). apply_timezone self-escalates (timedatectl / sudo
 * relink), so needs_root stays 0; show the output. */
static const AzRow ROWS_TIMEDATE[] = {
    {.label="Resolve timezone (pick a server)", .kind=AZ_ACT_PROMPT,
     .target="azzio timedate --resolve --server",
     .prompt="Server 1-5 (1 ipapi.co  2 ipquery.io  3 ip-api.com  4 ipinfo.io  5 ipwho.is):",
     .show_output=1, .base="timedatectl set-timezone <geolocated-zone>"},
};

/* Language: resolve the language/keyboard by IP geolocation. English stays the UI language;
 * a non-English country adds its layout as a switchable second (Alt+Shift). Same in-UI server
 * pick as Time & Date (the interactive stdin prompt can't run in the capture overlay), so this is
 * an AZ_ACT_PROMPT row running "azzio language --resolve --server <N>". apply_language
 * self-escalates (sudo writes locale.conf/vconsole), so needs_root=0. */
static const AzRow ROWS_LANGUAGE[] = {
    {.label="Resolve language (pick a server)", .kind=AZ_ACT_PROMPT,
     .target="azzio language --resolve --server",
     .prompt="Server 1-5 (1 ipapi.co  2 ipquery.io  3 ip-api.com  4 ipinfo.io  5 ipwho.is):",
     .show_output=1, .base="localectl set-locale / setxkbmap <region>"},
};

/* Volume: the "Current:" line shows the live level (az_status_volume); the rows set a PRECISE
 * level via `azzio volume set <N>` (the same subcommand the OSD mouse-drag uses) plus the two
 * 7.5% steps and mute. Each pops the bottom-middle cyan OSD bar. No sudo (PipeWire/ALSA run in
 * the user session), so needs_root stays 0; each runs captured in the UI and shows its result. */
static const AzRow ROWS_VOLUME[] = {
    {.label="Mute / unmute",   .kind=AZ_ACT_APPLY, .target="azzio volume mute",
     .base="wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"},
    {.label="Louder (+7.5%)",  .kind=AZ_ACT_APPLY, .target="azzio volume up",
     .base="wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 7.5%+"},
    {.label="Quieter (-7.5%)", .kind=AZ_ACT_APPLY, .target="azzio volume down",
     .base="wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 7.5%-"},
    {.label="Set to 0%",       .kind=AZ_ACT_APPLY, .target="azzio volume set 0",
     .base="wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 0%"},
    {.label="Set to 25%",      .kind=AZ_ACT_APPLY, .target="azzio volume set 25",
     .base="wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 25%"},
    {.label="Set to 50%",      .kind=AZ_ACT_APPLY, .target="azzio volume set 50",
     .base="wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 50%"},
    {.label="Set to 75%",      .kind=AZ_ACT_APPLY, .target="azzio volume set 75",
     .base="wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 75%"},
    {.label="Set to 100%",     .kind=AZ_ACT_APPLY, .target="azzio volume set 100",
     .base="wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 100%"},
};

/* Brightness: LAPTOP-ONLY (a PC has no backlight). The "Current:" line reads "not on a PC" on a
 * desktop; the set/step rows still run `azzio brightness ...`, which SELF-GATES (it refuses and
 * says so on a PC), so selecting one on a desktop is harmless and explains itself. Force the type
 * on the Machine Type screen to light this up on a desktop. No sudo needed for the UI wrapper. */
/* Brightness has NO brightnessctl on this build: azzio scales percent -> the raw kernel value
 * (percent/100 * max_brightness) and writes it to the backlight device's brightness file under
 * /sys/class/backlight via sudo tee. The base commands mirror that exactly, scaling inline so
 * they are copy-pasteable on any laptop (the glob picks the single backlight device, e.g.
 * intel_backlight). */
static const AzRow ROWS_BRIGHTNESS[] = {
    {.label="Brighter (+7.5%)", .kind=AZ_ACT_APPLY, .target="azzio brightness up",
     .base="sudo sh -c 'b=/sys/class/backlight/*; echo $(( $(cat $b/brightness) + 8*$(cat $b/max_brightness)/100 )) > $b/brightness'"},
    {.label="Dimmer (-7.5%)",   .kind=AZ_ACT_APPLY, .target="azzio brightness down",
     .base="sudo sh -c 'b=/sys/class/backlight/*; echo $(( $(cat $b/brightness) - 8*$(cat $b/max_brightness)/100 )) > $b/brightness'"},
    {.label="Set to 25%",       .kind=AZ_ACT_APPLY, .target="azzio brightness set 25",
     .base="sudo sh -c 'b=/sys/class/backlight/*; echo $(( 25*$(cat $b/max_brightness)/100 )) > $b/brightness'"},
    {.label="Set to 50%",       .kind=AZ_ACT_APPLY, .target="azzio brightness set 50",
     .base="sudo sh -c 'b=/sys/class/backlight/*; echo $(( 50*$(cat $b/max_brightness)/100 )) > $b/brightness'"},
    {.label="Set to 75%",       .kind=AZ_ACT_APPLY, .target="azzio brightness set 75",
     .base="sudo sh -c 'b=/sys/class/backlight/*; echo $(( 75*$(cat $b/max_brightness)/100 )) > $b/brightness'"},
    {.label="Set to 100%",      .kind=AZ_ACT_APPLY, .target="azzio brightness set 100",
     .base="sudo sh -c 'b=/sys/class/backlight/*; cat $b/max_brightness > $b/brightness'"},
};

/* --- Backup screen ----------------------------------------------------------
 * A "Backup" entry on ROWS_MAIN opens this screen. It drives the SAME opt-in flow
 * `azzio backup --configure` exposes, STREAMLINED for a new user and OFF BY DEFAULT: the two
 * LOCAL encrypted archives `backup` writes always happen; this screen only opts IN to ALSO
 * copying them to a USB drive and/or Google Drive. The screen's "Current:" line (az_status_backup)
 * shows the live target state ("off (local only)" by default). Every row runs `azzio backup
 * --configure ...` -- none needs sudo (the configurator writes the user's own config, like
 * `azzio machine`), so needs_root stays 0; each runs captured inside the UI and shows its output.
 *
 * The --status/--disable rows are non-interactive APPLIES. The two ENABLE flows are interactive
 * in the terminal (y/n prompts, `rclone config`), which cannot run cleanly in the alt-screen
 * capture overlay -- so the TUI uses the NON-interactive enable surface added to backup_targets.py
 * (`--enable-usb <PATH>` / `--enable-gdrive <REMOTE>`, each validating the target exactly like the
 * interactive flow before enabling) via an AZ_ACT_PROMPT row that asks for the path/remote first.
 * The base command is the REAL copy the enabled target then performs (cp / rclone copy), so the
 * "Base Command:" line teaches what actually happens under the hood. */
static const AzRow ROWS_BACKUP[] = {
    {.label="Show backup targets",  .kind=AZ_ACT_APPLY, .target="azzio backup --configure --status",
     .show_output=1, .base="cat ~/.config/azzio-backup/backup.cfg"},
    {.label="Turn all targets off", .kind=AZ_ACT_APPLY, .target="azzio backup --configure --disable",
     .show_output=1, .base="rm -f ~/.config/azzio-backup/backup.cfg"},
    /* Enable USB: prompt for the mount PATH, then `... --enable-usb <PATH>` (validates it is a
     * writable mounted dir before enabling). The base is the copy `backup` then does to that path. */
    {.label="Enable USB backup",    .kind=AZ_ACT_PROMPT, .target="azzio backup --configure --enable-usb",
     .prompt="USB mount path:", .show_output=1, .base="cp ~/backup.tar.gz.gpg"},
    /* Enable Google Drive: prompt for the rclone REMOTE, then `... --enable-gdrive <REMOTE>`
     * (verifies `rclone about <remote>` before enabling; run `rclone config` once in a real
     * terminal first for the Google login). The base is the rclone copy `backup` then does. */
    {.label="Enable Google Drive backup", .kind=AZ_ACT_PROMPT, .target="azzio backup --configure --enable-gdrive",
     .prompt="rclone remote name (e.g. gdrive):", .show_output=1, .base="rclone copy ~/backup.tar.gz.gpg"},
};

/* --- Hypervisor screen ------------------------------------------------------
 * A "Hypervisor" entry on ROWS_MAIN opens this screen. `hypervisor` is a PER-DIRECTORY VM runner
 * (the directory you run it in IS the VM); this screen edits the GLOBAL DEFAULTS every NEW
 * `hypervisor install` starts from -- NOT any one VM. It drives the non-interactive `hypervisor
 * --configure` surface (command_line_interface.py): --status prints the effective defaults,
 * --reset drops them back to the built-ins, and --set KEY VALUE validates + saves one default into
 * ~/.config/azzio-hypervisor/defaults.cfg. A directory's own hypervisor.cfg still WINS for that VM.
 *
 * The --status/--reset rows are APPLIES; the --set rows are AZ_ACT_PROMPT (prompt for the value,
 * then append it to the target -- exactly like the Backup enable rows). We expose the five keys a
 * user tunes most (ram/cpus/disk_size/network/audio); the rest stay editable per-VM in
 * hypervisor.cfg and via `hypervisor --configure --set` on the command line. None needs sudo (the
 * defaults file is the user's own), so needs_root stays 0; each runs captured inside the UI. The
 * base command teaches the underlying edit (writing the key into the user's defaults.cfg). */
static const AzRow ROWS_HYPERVISOR[] = {
    {.label="Show defaults",              .kind=AZ_ACT_APPLY, .target="hypervisor --configure --status",
     .show_output=1, .base="cat ~/.config/azzio-hypervisor/defaults.cfg"},
    {.label="Reset to built-in defaults", .kind=AZ_ACT_APPLY, .target="hypervisor --configure --reset",
     .show_output=1, .base="rm -f ~/.config/azzio-hypervisor/defaults.cfg"},
    /* The PROMPT rows' base teaches the underlying `KEY = value` line written into the defaults
     * file; az_row_base() appends the "<value>" placeholder, so the base ends at "KEY =" (the
     * defaults path is disclosed in the screen subtitle, not repeated on every row). */
    {.label="Set default RAM (MiB)",      .kind=AZ_ACT_PROMPT, .target="hypervisor --configure --set ram",
     .prompt="RAM in MiB (e.g. 16384):",        .show_output=1, .base="ram ="},
    {.label="Set default CPUs",           .kind=AZ_ACT_PROMPT, .target="hypervisor --configure --set cpus",
     .prompt="vCPU count (e.g. 16):",           .show_output=1, .base="cpus ="},
    {.label="Set default disk size",      .kind=AZ_ACT_PROMPT, .target="hypervisor --configure --set disk_size",
     .prompt="disk size (e.g. 200G):",          .show_output=1, .base="disk_size ="},
    {.label="Set default network",        .kind=AZ_ACT_PROMPT, .target="hypervisor --configure --set network",
     .prompt="network (user | none | iface):",  .show_output=1, .base="network ="},
    {.label="Set default audio",          .kind=AZ_ACT_PROMPT, .target="hypervisor --configure --set audio",
     .prompt="audio (on | off):",               .show_output=1, .base="audio ="},
};

/* --- Default Applications screens -------------------------------------------
 * A "Default Applications" entry on ROWS_MAIN opens the `defaultapps` screen, which lists the
 * 14 categories (Web/HTML/Music/.../Terminal). Each category row's status shows the handler it
 * currently resolves to, and descends into a per-category screen whose rows CHANGE the default
 * by running `azzio default-applications set <key> <id>` (the same apply-and-capture flow the
 * other screens use). The category set, keys, labels, candidate handlers and the base commands
 * are all pinned to packages/azzio/default_applications.py by a test, so C and Python cannot
 * drift. Applying a default writes the user's own mimeapps.list / exo helper -- no sudo. */

/* Per-category candidate rows are BUILT AT RUNTIME (az_da_screen, in model_default_applications.c), not
 * stored as static tables: the offered handlers RESOLVE LIVE against what is installed, so e.g.
 * installing Firefox makes firefox.desktop appear under Web/HTML/PDF and removing it drops it --
 * the user's "the list should resolve itself" requirement. Each generated row's label is the bare
 * .desktop id (e.g. "librewolf.desktop") -- the old pretty "Set to <Name>" labels are gone -- its
 * target is `azzio default-applications set <key> <id.desktop>` and its base is the underlying
 * `xdg-mime default ...` (or exo helper) line, for the teaching line + `x` copy. The category
 * set/keys/mimes/curated-seed mirror packages/azzio/default_applications.py (CATEGORIES /
 * CANDIDATES / CATEGORY_KEYS) via the AZ_DA_CATS table (model_default_applications.c), pinned by a test. */

/* The AzDaCat descriptor table (AZ_DA_CATS: key + full MIME list + curated seed) that drives the
 * runtime candidate resolution lives in model_default_applications.c, alongside az_da_screen() which uses
 * it. The category-LIST screen below is static (the per-category screens are built at runtime). */

/* The category list (the `defaultapps` screen). Each row shows the live handler and descends
 * into its per-category screen. This is exactly the category set the PROMPT lists for the TUI
 * (Web, HTML, Music, Video, Photos, Word, Spreadsheet, PDF, Source Code, File Manager, Plain
 * Text, Calculator, Terminal) -- "Mail" is deliberately absent (no mail client is shipped, so
 * default_applications leaves it empty and the TUI does not surface it). */
static const AzRow ROWS_DEFAULTAPPS[] = {
    {.label="Web",          .kind=AZ_ACT_SCREEN, .target="defaultapps.web",          .status=az_status_da_web},
    {.label="HTML",         .kind=AZ_ACT_SCREEN, .target="defaultapps.html",         .status=az_status_da_html},
    {.label="Music",        .kind=AZ_ACT_SCREEN, .target="defaultapps.music",        .status=az_status_da_music},
    {.label="Video",        .kind=AZ_ACT_SCREEN, .target="defaultapps.video",        .status=az_status_da_video},
    {.label="Photos",       .kind=AZ_ACT_SCREEN, .target="defaultapps.photos",       .status=az_status_da_photos},
    {.label="Word",         .kind=AZ_ACT_SCREEN, .target="defaultapps.word",         .status=az_status_da_word},
    {.label="Spreadsheet",  .kind=AZ_ACT_SCREEN, .target="defaultapps.spreadsheet",  .status=az_status_da_spreadsheet},
    {.label="PDF",          .kind=AZ_ACT_SCREEN, .target="defaultapps.pdf",          .status=az_status_da_pdf},
    {.label="Source Code",  .kind=AZ_ACT_SCREEN, .target="defaultapps.source-code",  .status=az_status_da_source_code},
    {.label="File Manager", .kind=AZ_ACT_SCREEN, .target="defaultapps.file-manager", .status=az_status_da_file_manager},
    {.label="Plain Text",   .kind=AZ_ACT_SCREEN, .target="defaultapps.plain-text",   .status=az_status_da_plain_text},
    {.label="Calculator",   .kind=AZ_ACT_SCREEN, .target="defaultapps.calculator",   .status=az_status_da_calculator},
    {.label="Terminal",     .kind=AZ_ACT_SCREEN, .target="defaultapps.terminal",     .status=az_status_da_terminal},
};

/* --- Display screens --------------------------------------------------------
 * A "Display" entry on ROWS_MAIN opens the `display` screen: cinnamon-settings-display parity
 * for this X11/OpenBox setup (resolution/refresh/orientation/primary/on-off/mirror via xrandr)
 * PLUS the GLOBAL SCALE chooser (the single source of truth for UI scaling). Each row runs an
 * `azzio display ...` apply; the screens' Current: lines read live state. The scale chooser is
 * the firm requirement; the xrandr rows reflect/act on the real output (defaulting to the
 * primary on a single-head VM). No sudo -- xrandr + the X resource DB are per-session. */

/* GLOBAL SCALE chooser: one row per SCALE_OPTIONS value (modifications/scale, pinned by a test).
 * Each runs `azzio display scale <factor>` -- rewrites ~/.Xresources' Xft.dpi and re-applies
 * live so the change propagates (new windows immediately; a re-login everywhere). */
static const AzRow ROWS_DISPLAY_SCALE[] = {
    {.label="100% (1.00)",  .kind=AZ_ACT_APPLY, .target="azzio display scale 1.00",
     .base="printf 'Xft.dpi: 96\\n'  > ~/.Xresources && xrdb -merge ~/.Xresources"},
    {.label="125% (1.25)",  .kind=AZ_ACT_APPLY, .target="azzio display scale 1.25",
     .base="printf 'Xft.dpi: 120\\n' > ~/.Xresources && xrdb -merge ~/.Xresources"},
    {.label="135% (1.35)",  .kind=AZ_ACT_APPLY, .target="azzio display scale 1.35",
     .base="printf 'Xft.dpi: 130\\n' > ~/.Xresources && xrdb -merge ~/.Xresources"},
    {.label="150% (1.50)",  .kind=AZ_ACT_APPLY, .target="azzio display scale 1.50",
     .base="printf 'Xft.dpi: 144\\n' > ~/.Xresources && xrdb -merge ~/.Xresources"},
    {.label="175% (1.75)",  .kind=AZ_ACT_APPLY, .target="azzio display scale 1.75",
     .base="printf 'Xft.dpi: 168\\n' > ~/.Xresources && xrdb -merge ~/.Xresources"},
    {.label="200% (2.00)",  .kind=AZ_ACT_APPLY, .target="azzio display scale 2.00",
     .base="printf 'Xft.dpi: 192\\n' > ~/.Xresources && xrdb -merge ~/.Xresources"},
};

/* Resolution: show the available modes (a captured `xrandr` list) + a couple of common presets.
 * The presets no-op with an xrandr error if the output lacks that mode, which the overlay shows. */
static const AzRow ROWS_DISPLAY_RESOLUTION[] = {
    {.label="List available modes", .kind=AZ_ACT_APPLY, .target="azzio display info", .show_output=1,
     .base="xrandr --query"},
    {.label="Set 1920x1080", .kind=AZ_ACT_APPLY, .target="azzio display resolution 1920x1080", .show_output=1,
     .base="xrandr --output <primary> --mode 1920x1080"},
    {.label="Set 1680x1050", .kind=AZ_ACT_APPLY, .target="azzio display resolution 1680x1050", .show_output=1,
     .base="xrandr --output <primary> --mode 1680x1050"},
    {.label="Set 1280x720",  .kind=AZ_ACT_APPLY, .target="azzio display resolution 1280x720", .show_output=1,
     .base="xrandr --output <primary> --mode 1280x720"},
};

/* Refresh rate: list the modes (rates are shown per resolution in the xrandr table) + presets. */
static const AzRow ROWS_DISPLAY_REFRESH[] = {
    {.label="List modes / rates", .kind=AZ_ACT_APPLY, .target="azzio display info", .show_output=1,
     .base="xrandr --query"},
    {.label="Set 60 Hz", .kind=AZ_ACT_APPLY, .target="azzio display refresh 60", .show_output=1,
     .base="xrandr --output <primary> --rate 60"},
    {.label="Set 75 Hz", .kind=AZ_ACT_APPLY, .target="azzio display refresh 75", .show_output=1,
     .base="xrandr --output <primary> --rate 75"},
};

/* Orientation / rotation. */
static const AzRow ROWS_DISPLAY_ORIENTATION[] = {
    {.label="Normal",   .kind=AZ_ACT_APPLY, .target="azzio display rotate normal", .show_output=1,
     .base="xrandr --output <primary> --rotate normal"},
    {.label="Left (90 CCW)",  .kind=AZ_ACT_APPLY, .target="azzio display rotate left", .show_output=1,
     .base="xrandr --output <primary> --rotate left"},
    {.label="Right (90 CW)",  .kind=AZ_ACT_APPLY, .target="azzio display rotate right", .show_output=1,
     .base="xrandr --output <primary> --rotate right"},
    {.label="Inverted (180)", .kind=AZ_ACT_APPLY, .target="azzio display rotate inverted", .show_output=1,
     .base="xrandr --output <primary> --rotate inverted"},
};

/* Monitors: primary select, enable/disable, mirror vs extend. The list/info is here too. */
static const AzRow ROWS_DISPLAY_MONITORS[] = {
    {.label="Show monitors (xrandr)", .kind=AZ_ACT_APPLY, .target="azzio display info", .show_output=1,
     .base="xrandr --query"},
    {.label="Mirror displays",  .kind=AZ_ACT_APPLY, .target="azzio display mirror on", .show_output=1,
     .base="xrandr --output <o> --same-as <primary>"},
    {.label="Extend displays",  .kind=AZ_ACT_APPLY, .target="azzio display mirror off", .show_output=1,
     .base="xrandr --output <o> --right-of <primary>"},
};

/* The Display screen: the scale chooser + the xrandr feature screens. Every row shows its OWN
 * current value inline (.status) -- the user asked for the standalone top "Current: scale 1.35x"
 * line to be removed and the current value put on each line instead, so the display screen has
 * NO .current (see SCREENS[]) and each row carries an inline probe. */
static const AzRow ROWS_DISPLAY[] = {
    {.label="Global Scale", .kind=AZ_ACT_SCREEN, .target="display.scale",       .status=az_status_display_scale},
    {.label="Resolution",   .kind=AZ_ACT_SCREEN, .target="display.resolution",  .status=az_status_display_resolution},
    {.label="Refresh Rate", .kind=AZ_ACT_SCREEN, .target="display.refresh",     .status=az_status_display_refresh},
    {.label="Orientation",  .kind=AZ_ACT_SCREEN, .target="display.orientation", .status=az_status_display_orientation},
    {.label="Monitors",     .kind=AZ_ACT_SCREEN, .target="display.monitors",    .status=az_status_display_monitors},
};

#define AZN(a) (int)(sizeof(a) / sizeof((a)[0]))

/* Only Theme and Wallpaper set `.current` (the top "Current:" line); every other screen
 * leaves it NULL. The main screen's subtitle is empty (the spec removed the "Move with the
 * arrow keys..." line -- the nav hints at the bottom already say how to move). Designated
 * initializers throughout, so the NULL terminator is simply an empty pair of braces. */
static const AzScreen SCREENS[] = {
    {.id="main",      .title="Azzio Settings", .subtitle="",
     .rows=ROWS_MAIN, .nrows=AZN(ROWS_MAIN)},
    /* Subtitles now say WHAT tool each screen drives and WHAT it does (the spec: the top label
     * should explain the wrapped commands), not a bare tagline. The Theme one keeps the pinned
     * "Kitty does not follow the system theme" phrase. */
    {.id="theme",     .title="Theme",
     .subtitle="Wraps gsettings color-scheme (prefer-dark/prefer-light) to switch dark/white. "
               "Kitty does not follow the system theme.",
     .current=az_status_theme,     .rows=ROWS_THEME,     .nrows=AZN(ROWS_THEME)},
    /* Wallpaper subtitle is the DIRECTORY PATH -- coloured cyan (subtitle_accent) and placed
     * tight above the "Current:" line, per the spec. It keeps the /usr/share/wallpapers path. */
    {.id="wallpaper", .title="Wallpaper",
     .subtitle="Wallpapers directory: " AZ_WALLPAPERS_DIR "/", .subtitle_accent=1,
     .current=az_status_wallpaper, .rows=ROWS_WALLPAPER, .nrows=AZN(ROWS_WALLPAPER)},
    {.id="network",   .title="Network",
     .subtitle="A front-end over nmcli, rfkill, bluetoothctl and ufw -- wifi, wired, "
               "bluetooth, airplane and the firewall.",
     .rows=ROWS_NETWORK, .nrows=AZN(ROWS_NETWORK)},
    /* Each network sub-screen shows its live state ONCE via .current (the "Current:" line at
     * the top), so the rows below stay label-only -- no repeated status echo. */
    {.id="network.wifi",      .title="Wifi",
     .subtitle="Wraps nmcli radio wifi (on/off) and nmcli device wifi (list/disconnect).",
     .current=az_status_wifi,      .rows=ROWS_WIFI,      .nrows=AZN(ROWS_WIFI)},
    {.id="network.wired",     .title="Wired",
     .subtitle="Wraps nmcli device connect/disconnect on the ethernet interface.",
     .current=az_status_wired,     .rows=ROWS_WIRED,     .nrows=AZN(ROWS_WIRED)},
    {.id="network.ip",        .title="IP Address",
     .subtitle="Static (manual IPv4 + subnet prefix + gateway + DNS) vs dynamic (DHCP) on an "
               "interface -- the live twin of the installer's Network page. Wraps `azzio "
               "network ip`, which edits the device's NetworkManager connection. Active "
               "address shown above; type `azzio network ip show` for the full list.",
     .current=az_status_ip,        .rows=ROWS_IP,        .nrows=AZN(ROWS_IP)},
    {.id="network.bluetooth", .title="Bluetooth",
     .subtitle="Wraps systemctl (enable/disable bluetooth) + rfkill; bluetoothctl to scan. "
               "Off by default.",
     .current=az_status_bluetooth, .rows=ROWS_BLUETOOTH, .nrows=AZN(ROWS_BLUETOOTH)},
    {.id="network.airplane",  .title="Airplane mode",
     .subtitle="Wraps nmcli networking off/on (plus rfkill) -- one switch that really drops "
               "the internet.",
     .current=az_status_airplane,  .rows=ROWS_AIRPLANE,  .nrows=AZN(ROWS_AIRPLANE)},
    {.id="network.firewall",  .title="Firewall",
     .subtitle="Wraps ufw: enable/disable, the incoming/outgoing DEFAULT policy, status "
               "numbered, and allow/deny/delete a port. Default policy shown above.",
     .current=az_status_firewall_policy, .rows=ROWS_FIREWALL, .nrows=AZN(ROWS_FIREWALL)},
    {.id="network.ssh",       .title="SSH Server",
     .subtitle="Start/stop the ssh server (sshd) and open/close port 22/tcp. Off by default. "
               "Exposing ssh to an untrusted network lets anyone who can reach this machine "
               "try to log in -- use a strong password or key auth and only open :22 when "
               "you need remote access. Wraps `azzio --sshd-hypervisor` + ufw + systemctl.",
     .current=az_status_ssh,       .rows=ROWS_SSH,       .nrows=AZN(ROWS_SSH)},
    /* Volume: the "Current:" line shows the live level; the rows set a precise level (or step /
     * mute), each popping the bottom-middle cyan OSD bar. */
    {.id="volume",    .title="Volume",
     .subtitle="Wraps wpctl set-volume / set-mute on @DEFAULT_AUDIO_SINK@ (PipeWire). "
               "Drag the on-screen bar for any value.",
     .current=az_status_volume,    .rows=ROWS_VOLUME,    .nrows=AZN(ROWS_VOLUME)},
    /* Brightness: LAPTOP-ONLY. The "Current:" line reads the level on a laptop, or "not on a PC"
     * on a desktop (where the rows self-gate). Force Laptop on Machine Type to enable it. */
    {.id="brightness", .title="Brightness",
     .subtitle="Writes the scaled value to /sys/class/backlight/*/brightness (sudo tee). "
               "Laptops only -- a PC has no backlight.",
     .current=az_status_brightness, .rows=ROWS_BRIGHTNESS, .nrows=AZN(ROWS_BRIGHTNESS)},
    /* Machine Type: the "Current:" line shows what Azzio recognises (PC / Laptop); the rows
     * hard-switch it. Brightness is a laptop-only control, so this is where a desktop can be
     * forced to "Laptop" to light the brightness UI up (or a laptop forced to "PC"). */
    {.id="machine",   .title="Machine Type",
     .subtitle="Writes ~/.config/azzio/machine-type (PC/Laptop) or removes it to autodetect. "
               "Laptops get screen-brightness control; PCs do not.",
     .current=az_status_machine,   .rows=ROWS_MACHINE,   .nrows=AZN(ROWS_MACHINE)},
    {.id="gpu",       .title="GPU",
     .subtitle="Detects the PCI GPU and resolves its drivers from the baked-in offline repo "
               "(vendor + developer drivers). Generic mesa already works everywhere; this adds "
               "the right vendor stack. Outdated versions: `sudo pacman -Syu`.",
     .current=az_status_gpu,       .rows=ROWS_GPU,       .nrows=AZN(ROWS_GPU)},
    {.id="timedate",  .title="Time & Date",
     .subtitle="Geolocates by IP -- you PICK one of 5 shuffled servers in the terminal -- and "
               "sets the system timezone. The only time/date path that uses the network; "
               "otherwise the zone is static/user-chosen.",
     .current=az_status_timedate,  .rows=ROWS_TIMEDATE,  .nrows=AZN(ROWS_TIMEDATE)},
    {.id="language",  .title="Language",
     .subtitle="Geolocates by IP -- you PICK one of 5 shuffled servers in the terminal -- and "
               "sets English plus (for a non-English country) the region language + keyboard as "
               "a switchable second layout (Alt+Shift). English stays the UI language.",
     .current=az_status_language,  .rows=ROWS_LANGUAGE,  .nrows=AZN(ROWS_LANGUAGE)},
    /* Hypervisor: the GLOBAL defaults every NEW `hypervisor install` starts from. The subtitle
     * makes clear these are DEFAULTS for new VMs and that a directory's own hypervisor.cfg still
     * wins; the "Current:" line (az_status_hypervisor) summarises ram/cpus/disk/net. */
    {.id="hypervisor", .title="Hypervisor",
     .subtitle="Defaults for NEW VMs the `hypervisor` command creates (each directory is its own "
               "VM). Changing a default here affects future `hypervisor install`s; a directory's "
               "own hypervisor.cfg still wins for that VM.",
     .current=az_status_hypervisor, .rows=ROWS_HYPERVISOR, .nrows=AZN(ROWS_HYPERVISOR)},
    /* Backup: OFF BY DEFAULT. The subtitle explains the feature -- the two local archives always
     * happen; this only opts in to a USB / Google Drive COPY. The "Current:" line shows the live
     * target state (az_status_backup: "off (local only)" by default). */
    {.id="backup",    .title="Backup",
     .subtitle="Off by default. `backup` always writes two local encrypted archives; this opts "
               "IN to ALSO copying them to a USB drive and/or Google Drive (rclone). Enabling a "
               "target validates it first; nothing here changes the local archives.",
     .current=az_status_backup,    .rows=ROWS_BACKUP,    .nrows=AZN(ROWS_BACKUP)},
    {.id="power",     .title="Power",
     .subtitle="Shut down, restart, sleep (suspend) or lock -- now, or on a timer. Wraps "
               "systemctl poweroff/reboot/suspend and loginctl lock-session; scheduled actions "
               "use a systemd-run timer (status/cancel from here). Current timer shown above.",
     .current=az_status_power,     .rows=ROWS_POWER,     .nrows=AZN(ROWS_POWER)},
    /* Default Applications: the category LIST is static (below); the 13 per-category screens
     * (defaultapps.web, ...) are BUILT AT RUNTIME by az_screen_find -> az_da_screen, so their
     * candidate rows resolve live against the installed .desktop files and each discloses WHERE
     * the .desktop files live (like the Wallpaper screen discloses its directory). They are NOT
     * listed here. The list screen's subtitle names the .desktop directories too. */
    {.id="defaultapps", .title="Default Applications",
     .subtitle="Which app opens which file type (the XDG mimeapps defaults). Pick a category to "
               "change its handler. To add or override an app, drop its .desktop into "
               AZ_DA_DIRS_LINE ". Options resolve automatically from what is installed.",
     .rows=ROWS_DEFAULTAPPS, .nrows=AZN(ROWS_DEFAULTAPPS)},
    /* Display: cinnamon-settings-display parity (xrandr) + the GLOBAL SCALE chooser. NO
     * .current here -- the top "Current: scale 1.35x" line was removed at the user's request;
     * each ROWS_DISPLAY row shows its own current value inline via .status instead. */
    {.id="display",   .title="Display",
     .subtitle="Resolution, refresh, orientation, monitors (xrandr) and the global UI scale.",
     .rows=ROWS_DISPLAY,   .nrows=AZN(ROWS_DISPLAY)},
    {.id="display.scale", .title="Global Scale",
     .subtitle="The ONE UI scale every app obeys (Xft.dpi + Xcursor.size, re-applied live via "
               "xrdb). The file manager and DPI-aware apps rescale at once; others on next launch.",
     .current=az_status_display_scale, .rows=ROWS_DISPLAY_SCALE, .nrows=AZN(ROWS_DISPLAY_SCALE)},
    {.id="display.resolution", .title="Resolution",
     .subtitle="Wraps xrandr --output --mode. List the modes, then pick one (or type "
               "`azzio display resolution <WxH>`).",
     .rows=ROWS_DISPLAY_RESOLUTION, .nrows=AZN(ROWS_DISPLAY_RESOLUTION)},
    {.id="display.refresh", .title="Refresh Rate",
     .subtitle="Wraps xrandr --output --rate. The rates per resolution are in the mode list.",
     .rows=ROWS_DISPLAY_REFRESH, .nrows=AZN(ROWS_DISPLAY_REFRESH)},
    {.id="display.orientation", .title="Orientation",
     .subtitle="Wraps xrandr --output --rotate (normal / left / right / inverted).",
     .rows=ROWS_DISPLAY_ORIENTATION, .nrows=AZN(ROWS_DISPLAY_ORIENTATION)},
    {.id="display.monitors", .title="Monitors",
     .subtitle="Wraps xrandr: show outputs, mirror (same-as) or extend (right-of). Primary / "
               "on / off per output are `azzio display primary|on|off <output>`.",
     .rows=ROWS_DISPLAY_MONITORS, .nrows=AZN(ROWS_DISPLAY_MONITORS)},
    { 0 },
};

const AzScreen *az_screens(void) { return SCREENS; }

int az_screen_count(void)
{
    int c = 0;
    while (SCREENS[c].id) c++;
    return c;
}

const AzScreen *az_screen_find(const char *id)
{
    if (!id) return NULL;
    /* Runtime-built Default Applications category screens (candidate rows resolve live). */
    if (strncmp(id, "defaultapps.", 12) == 0) {
        const AzScreen *dyn = az_da_screen(id);
        if (dyn) return dyn;
    }
    for (int i = 0; SCREENS[i].id; i++)
        if (strcmp(SCREENS[i].id, id) == 0) return &SCREENS[i];
    return NULL;
}
