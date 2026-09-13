"""packages.openbox -- the OpenBox live-session configuration-as-Python payloads.

Why these tests matter: compiler.py never inspects the CONTENT of these builders;
it blindly iterates PLAN/emit_plan() and calls emit.write_text/write_exec with
the (dest, mode) each entry declares, then chowns the /home/main subtree to the
live user only for entries marked owner "home". So the declarative PLAN table IS
the contract -- a wrong mode makes a script non-executable (the ISO's `[ -x ]`
guards then silently skip it) or a configuration world-writable; a wrong owner chowns a
root-owned wrapper to uid 1000 (or leaves a home dotfile root-owned so the live
user cannot read it). None of that raises in Python; it only shows up as a dead
live session. These tests pin the mode/owner/dest table, prove emit_plan() does
not mutate the module-level PLAN (compiler.py may call it more than once), lock the
OpenBox session contract (xinitrc execs openbox-session, no cyan flash, wallpaper
pre-painted; rc.xml binds the Super key to the menu; autostart arms xcape + the
keyboard + the menu daemon + the installer), and the privileged wrapper's
`unset XDG_RUNTIME_DIR` before `exec sudo`.

KDE Plasma was REMOVED from Azzio and replaced by a panel-less OpenBox desktop;
every Plasma-specific builder/constant (panel/appletsrc, kdeglobals, kwinrc,
powerdevil, kscreenlocker, kickoff, ...) is gone, so the tests that pinned them are
gone too. The Azzio application menu (opened by the Super key) is the only shell
surface now; the OpenBox desktop right-click root menu was removed at the user's
request, and the titlebar uses the Azzio theme (Clearlooks with a doubled bar).
"""

from __future__ import annotations

import importlib.util

import pytest

from packages import openbox as desktop


def _load_azzio_command_line_interface():
    """Load the `azzio` guest command line interface as a single module namespace.

    The command line interface is now a PACKAGE (libraries/packages/azzio/) that is BUNDLED into one
    self-contained script for shipping (packages.azzio.bundle.bundle_source). We exec that
    bundle text in a fresh namespace -- exactly the artifact the compiler ships, before the
    country-table re-injection -- so tests exercise the real functions (main/
    resolve_via_server/apply_*/cmd_theme/...) and data (COUNTRY_TABLE/RESOLVER_SERVERS) in
    the single namespace they run in on the guest, and no import-cache aliasing masks drift.

    Returned as a types.ModuleType so monkeypatch.setattr / attribute access work like a
    normal module."""
    import types
    from packages.azzio.bundle import bundle_source

    mod = types.ModuleType("azzio_guest_command_line_interface")
    exec(compile(bundle_source(), "azzio_guest_command_line_interface", "exec"), mod.__dict__)
    return mod


# --- PLAN mode/owner/dest table --------------------------------------------

def test_plan_has_exactly_nineteen_entries():
    # compiler.py iterates PLAN; a dropped/extra entry silently un-emits a file. The
    # panel-less OpenBox session ships exactly nineteen files via PLAN:
    #   1. ~/.xinitrc                               (startx -> openbox-session)
    #   2. ~/.config/openbox/rc.xml                 (keybinds, theme, titlebar-button binds)
    #   3. ~/.themes/Azzio-Dark/openbox-3/themerc  (DARK Azzio theme -- the default)
    #   4. ~/.themes/Azzio/openbox-3/themerc       (LIGHT Azzio theme -- for `theme --white`)
    #   5. ~/.config/gtk-3.0/settings.ini           (GTK3 dark theme default)
    #   6. ~/.config/gtk-4.0/settings.ini           (GTK4 dark theme default)
    #   7. ~/.gtkrc-2.0                              (GTK2 dark theme default)
    #   8. /etc/dconf/db/local.d/00-azzio-theme     (dconf color-scheme=prefer-dark default)
    #   9. /etc/dconf/profile/user                   (dconf profile so the system db backs user)
    #  10. /etc/xdg/azzio-picom.conf                (OUR picom config: fading OFF, opaque frames)
    #  11. ~/.Xresources                             (GLOBAL SCALE backbone: Xft.dpi + Xcursor.size)
    #  12. ~/.config/openbox/autostart              (feh, setxkbmap, xcape, menu daemon, installer)
    #  13. ~/.config/openbox/environment            (XDG_CURRENT_DESKTOP + scale env)
    #  14. /usr/local/share/azzio/openbox-autostart-installed (staged "installed" autostart)
    #  15. ~/.local/share menu usage seed            (default menu ordering)
    #  16. /usr/share/applications/azzio-install.desktop (menu re-open entry, system)
    #  17. ~/Desktop/azzio-install.desktop          (double-clickable installer launcher)
    #  18. /usr/local/bin/azzio-install             (privileged Calamares wrapper)
    #  19. /usr/local/bin/azzio                      (guest-side command line interface)
    # (entries 3-9 are the system theme: dark is the default; `azzio theme` toggles it; entry
    # 10 is the compositor config that kills the picom fade + transparent-titlebar defaults;
    # entry 11 is the GLOBAL SCALE, PROMPT Display/scale task.)
    # NOTE: the media OSD (/usr/local/lib/azzio/azzio-osd) is NO LONGER a PLAN entry -- it is a
    # COMPILED C binary now (on_screen_display.c), built + installed by terminal_user_interface_build.build_osd()
    # like the terminal UI binary, so it is not emitted as a text file here.
    # The .bash_profile snippet is appended by emit_plan(), NOT part of PLAN.
    assert len(desktop.PLAN) == 19


def test_plan_entries_have_the_four_declared_keys():
    for entry in desktop.PLAN:
        assert set(entry) == {"builder", "dest", "mode", "owner"}


def test_plan_modes_are_only_exec_or_conf():
    # Every mode must be one of the two declared octals (0o755 script / 0o644 conf);
    # anything else means a hand-typed literal drifted.
    for entry in desktop.PLAN:
        assert entry["mode"] in (0o755, 0o644), entry["dest"]


def test_plan_owners_are_only_home_or_root():
    for entry in desktop.PLAN:
        assert entry["owner"] in ("home", "root"), entry["dest"]


def test_exec_and_conf_octal_values():
    # Guard the module-level octal constants directly: a script must be 0o755 and
    # a configuration 0o644, or the ISO's [ -x ] guards skip scripts / configs go writable.
    assert desktop._EXEC == 0o755
    assert desktop._CONF == 0o644


def test_scripts_are_exec_configs_are_conf():
    # Shell scripts / executables -> 0o755; plain config/data (OpenBox XML configs, the
    # system-wide .desktop) -> 0o644. The OpenBox `autostart` is a shell script sourced
    # by openbox-session, so it is EXECUTABLE; rc.xml/menu.xml/environment are data.
    by_builder = {e["builder"].__name__: e for e in desktop.PLAN}
    assert by_builder["xinitrc"]["mode"] == 0o755
    assert by_builder["openbox_autostart"]["mode"] == 0o755
    assert by_builder["openbox_rc_xml"]["mode"] == 0o644
    assert by_builder["openbox_theme_rc_dark"]["mode"] == 0o644
    assert by_builder["openbox_theme_rc_light"]["mode"] == 0o644
    assert by_builder["openbox_environment"]["mode"] == 0o644
    assert by_builder["install_menu_desktop"]["mode"] == 0o644
    # The Desktop launcher is the exception among data files: it must be EXECUTABLE so a
    # file manager runs it on double-click without the untrusted-.desktop prompt.
    assert by_builder["desktop_installer_launcher"]["mode"] == 0o755


def test_install_wrapper_entry_is_root_owned_exec():
    # The privileged launcher lives in /usr/local/bin and must stay root-owned
    # (0:0) and executable; chowning it to the live user would let uid 1000 rewrite
    # the thing that runs `sudo -E calamares`.
    entry = next(e for e in desktop.PLAN if e["dest"] == desktop.INSTALL_WRAPPER_PATH)
    assert entry["mode"] == 0o755
    assert entry["owner"] == "root"
    assert entry["builder"] is desktop.install_wrapper_sh


def test_install_wrapper_has_gui_cli_auto_and_help():
    # azzio-install is a dispatcher over three explicit modes -- GUI (Calamares), the
    # scripted CLI installer, and the fully-unattended --auto -- plus a --help. There is NO
    # default action: a bare invocation shows help (see below). Assert all surfaces exist.
    w = desktop.install_wrapper_sh()
    assert "--help" in w and "Usage: azzio-install" in w
    assert "--gui" in w and "--cli" in w and "--auto" in w
    # GUI path still launches Calamares the same way.
    assert "calamares" in w
    # CLI path runs the scripted installer baked under /root/azzio.
    assert desktop.INSTALL_CLI_SCRIPT_PATH in w


def test_install_wrapper_has_no_default_action_and_shows_help_on_no_args():
    # PROMPT.md: `azzio-install` (no option) and `--help`/`-h` must ECHO the help text,
    # NOT start an install. The wrapper therefore has NO display-detection default anymore;
    # a mode must be named explicitly (-g/-c/-a). Assert the dispatch defaults to `usage`
    # (the `*)` arm) and that the old auto-detect branch is gone.
    w = desktop.install_wrapper_sh()
    assert "run_cli" in w and "run_gui" in w and "run_auto" in w
    # The bare-invocation dispatch prints help and exits 0 (no install).
    assert "*) usage; exit 0 ;;" in w
    # The removed behaviour: no DISPLAY/WAYLAND_DISPLAY auto-detect default.
    assert 'if [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]; then' not in w


def test_install_wrapper_mode_flag_aliases():
    # PROMPT.md requires short + long aliases for each mode:
    #   -g / --gui / --graphical-user-interface
    #   -c / --cli / --command-line-interface
    #   -a / --auto / --automatic
    w = desktop.install_wrapper_sh()
    assert "-g|--gui|--graphical-user-interface) mode=gui" in w
    assert "-c|--cli|--command-line-interface) mode=cli" in w
    assert "-a|--auto|--automatic) mode=auto" in w
    # -h / --help still print help.
    assert "-h|--help) usage; exit 0 ;;" in w


def test_install_wrapper_disk_preseeds_the_installer():
    # --disk pre-seeds the scripted installer's disk selection (AZ_INSTALL_CHOICE=2 +
    # AZ_INSTALL_DISK) so a --cli install can target a named device without the disk prompt.
    w = desktop.install_wrapper_sh()
    assert "AZ_INSTALL_DISK=" in w                    # --disk <dev>
    assert "--disk" in w


def test_install_wrapper_auto_is_fully_unattended_with_fixed_defaults():
    # PROMPT.md: `--auto` applies defaults with no prompts (each overridable by a sub-flag, see
    # test_install_wrapper_auto_sub_flags_*). run_auto resolves every answer -- via the
    # `${az_opt_*:-DEFAULT}` idiom so an omitted flag keeps its default -- then delegates to the
    # single scripted-installer path (run_cli). Assert the DEFAULTS: largest disk, hostname
    # azzio, user main, blank full name, Asia/Jerusalem, btrfs, and REAL "admin" passwords for
    # user and root (the spec's flag table lists admin as the password default; --auto no longer
    # uses the '*' casper convention). DHCP is the installed default, so nothing to assert there.
    w = desktop.install_wrapper_sh()
    assert "run_auto()" in w
    assert "export AZ_INSTALL_CHOICE=1" in w              # largest fixed disk (skips USB)
    assert 'export AZ_INSTALL_HOSTNAME="${az_opt_hostname:-azzio}"' in w
    assert 'export AZ_INSTALL_USERNAME="${az_opt_username:-main}"' in w
    assert "export AZ_INSTALL_FULLNAME=" in w             # blank -> skipped (never prompted)
    assert 'export AZ_INSTALL_TIMEZONE="${az_opt_timezone:-Asia/Jerusalem}"' in w
    assert "export AZ_INSTALL_FILESYSTEM=btrfs" in w      # parity with the Calamares GUI
    # Real passwords, defaulting to admin; NO star/casper knob under --auto anymore.
    assert 'export AZ_INSTALL_PASSWORD="${az_opt_user_password:-admin}"' in w
    assert "export AZ_INSTALL_STAR_PASSWORD=1" not in w
    # run_auto funnels into the single install path.
    assert "run_cli" in w
    # run_cli still forwards the filesystem knob across the sudo boundary.
    assert "AZ_INSTALL_FILESYSTEM=$AZ_INSTALL_FILESYSTEM" in w


def test_install_wrapper_auto_password_defaults_admin_shared():
    # Default password policy for a bare `azzio-install --auto`: user "admin", and root SHARES
    # it (--share-username-root-password default True). Assert the share default and both arms:
    # True -> root == user password; anything else -> root == --root-password (default admin).
    w = desktop.install_wrapper_sh()
    assert 'az_share="${az_opt_share_root_password:-True}"' in w        # default shared
    assert 'export AZ_INSTALL_ROOT_PASSWORD="$AZ_INSTALL_PASSWORD"' in w  # True arm: reuse user pw
    assert 'export AZ_INSTALL_ROOT_PASSWORD="${az_opt_root_password:-admin}"' in w  # False arm


def test_install_wrapper_auto_sub_flags_are_parsed():
    # The seven --auto sub-flags must each be parsed in BOTH the `--flag value` and `--flag=value`
    # forms into an az_opt_* the resolver consumes. Assert every flag token appears.
    w = desktop.install_wrapper_sh()
    for flag, var in (
        ("--hostname", "az_opt_hostname"),
        ("--username", "az_opt_username"),
        ("--username-password", "az_opt_user_password"),
        ("--share-username-root-password", "az_opt_share_root_password"),
        ("--root-password", "az_opt_root_password"),
        ("--timezone", "az_opt_timezone"),
        ("--disk", "az_opt_disk"),
    ):
        assert f"{flag})" in w, f"missing space-form parse for {flag}"
        assert f"{flag}=*)" in w, f"missing =-form parse for {flag}"
        assert var in w, f"flag {flag} not captured into {var}"


def test_install_wrapper_auto_sub_flags_require_auto():
    # SPEC: "if --auto was not passed in then these flags shouldn't work." The wrapper must
    # REJECT a sub-flag given without --auto (exit non-zero + help), not silently ignore it.
    # Assert the gate exists and names the requirement.
    w = desktop.install_wrapper_sh()
    assert 'if [ "$mode" != "auto" ] && [ -n "$az_seen_auto_flag" ]; then' in w
    assert "require --auto" in w
    # --disk stays allowed with --cli (the long-standing combo), so the gate excludes that case.
    assert '[ "$mode" != "cli" ]' in w


def test_install_wrapper_auto_behaviour_end_to_end(tmp_path):
    # Behavioural: drive the REAL wrapper (run_cli/run_gui execs replaced by a probe that dumps
    # the resolved AZ_INSTALL_* env) through several flag combinations and assert the resolved
    # environment. This is the actual contract -- parsing + run_auto resolution + the gate --
    # exercised as bash, not just string-matched.
    import re
    import subprocess

    w = desktop.install_wrapper_sh()
    probe = w.replace(
        "    exec sudo -E env \\",
        '    { echo "CHOICE=${AZ_INSTALL_CHOICE-} DISK=${AZ_INSTALL_DISK-} '
        'HOST=${AZ_INSTALL_HOSTNAME-} USER=${AZ_INSTALL_USERNAME-} '
        'FULL=[${AZ_INSTALL_FULLNAME-}] TZ=${AZ_INSTALL_TIMEZONE-} FS=${AZ_INSTALL_FILESYSTEM-} '
        'PW=${AZ_INSTALL_PASSWORD-} ROOTPW=${AZ_INSTALL_ROOT_PASSWORD-} '
        'STAR=${AZ_INSTALL_STAR_PASSWORD-}"; exit 0; }\n    exec true \\',
        1,
    ).replace("if ! sudo test -r", "if ! true && ! sudo test -r")
    path = tmp_path / "probe.sh"
    path.write_text(probe)

    def run(args):
        return subprocess.run(["bash", str(path), *args], capture_output=True,
                              text=True, timeout=20)

    def f(out, key):
        m = re.search(rf"\b{key}=(\[[^\]]*\]|\S*)", out)
        return m.group(1) if m else None

    # bare --auto -> admin/admin shared, largest disk, azzio/main, no star.
    r = run(["--auto"])
    assert r.returncode == 0, r.stderr
    assert f(r.stdout, "CHOICE") == "1"
    assert f(r.stdout, "HOST") == "azzio" and f(r.stdout, "USER") == "main"
    assert f(r.stdout, "PW") == "admin" and f(r.stdout, "ROOTPW") == "admin"
    assert f(r.stdout, "STAR") == "" and f(r.stdout, "FULL") == "[]"

    # full overrides (both = and space forms exercised elsewhere; = here).
    r = run(["--auto", "--disk=sda", "--hostname=box", "--username=me",
             "--username-password=secret", "--share-username-root-password=False",
             "--root-password=rootsecret", "--timezone=Europe/London"])
    assert r.returncode == 0, r.stderr
    assert f(r.stdout, "CHOICE") == "2" and f(r.stdout, "DISK") == "sda"
    assert f(r.stdout, "HOST") == "box" and f(r.stdout, "USER") == "me"
    assert f(r.stdout, "PW") == "secret" and f(r.stdout, "ROOTPW") == "rootsecret"
    assert f(r.stdout, "TZ") == "Europe/London"

    # share True -> root reuses user password.
    r = run(["--auto", "--username-password=hunter2", "--share-username-root-password=True"])
    assert f(r.stdout, "PW") == "hunter2" and f(r.stdout, "ROOTPW") == "hunter2"

    # --disk=auto keeps largest.
    r = run(["--auto", "--disk=auto"])
    assert f(r.stdout, "CHOICE") == "1" and f(r.stdout, "DISK") == ""

    # gate: sub-flag without --auto exits 2.
    r = run(["--hostname=x"])
    assert r.returncode == 2 and "require --auto" in r.stderr

    # --cli --disk still works (the allowed combo).
    r = run(["--cli", "--disk", "sdb"])
    assert r.returncode == 0, r.stderr
    assert f(r.stdout, "CHOICE") == "2" and f(r.stdout, "DISK") == "sdb"

    # missing value errors.
    r = run(["--auto", "--hostname"])
    assert r.returncode == 2 and "requires a value" in r.stderr


def test_install_wrapper_forwards_whitespace_values_intact(tmp_path):
    # REGRESSION (adversary-found): run_cli forwards each AZ_INSTALL_* across `sudo -E env` with
    # `${VAR:+"VAR=$VAR"}`. The double quotes INSIDE the :+ are load-bearing -- without them a
    # value with a space word-splits and `env` treats the tail as the command to exec, so
    # `--auto --username-password='correct horse'` died with `env: 'horse': No such file or
    # directory` (exit 127) and the installer never ran. This drives the REAL forwarding (sudo
    # dropped, env kept, the target replaced by a probe that dumps the AZ_* it actually received)
    # and asserts a whitespace password/hostname arrive as ONE value in the child environment.
    import subprocess

    w = desktop.install_wrapper_sh()
    probe = w.replace("    exec sudo -E env \\", "    exec env \\", 1)
    probe = probe.replace("if ! sudo test -r", "if ! true && ! sudo test -r")
    probe = probe.replace(
        f"        bash '{desktop.INSTALL_CLI_SCRIPT_PATH}'",
        "        bash -c 'for v in PASSWORD ROOT_PASSWORD HOSTNAME USERNAME; do "
        "eval \"val=\\${AZ_INSTALL_$v-<UNSET>}\"; echo \"AZ_INSTALL_$v=[$val]\"; done'")
    path = tmp_path / "probe.sh"
    path.write_text(probe)

    # A user password with a space, shared to root by default.
    r = subprocess.run(
        ["bash", str(path), "--auto", "--username-password=correct horse"],
        capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, f"whitespace password broke env forwarding: rc={r.returncode} {r.stderr}"
    assert "AZ_INSTALL_PASSWORD=[correct horse]" in r.stdout, r.stdout
    assert "AZ_INSTALL_ROOT_PASSWORD=[correct horse]" in r.stdout, r.stdout

    # An independent root password with a space (share=False).
    r = subprocess.run(
        ["bash", str(path), "--auto", "--share-username-root-password=False",
         "--root-password=my root pw", "--username-password=user pw"],
        capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    assert "AZ_INSTALL_PASSWORD=[user pw]" in r.stdout, r.stdout
    assert "AZ_INSTALL_ROOT_PASSWORD=[my root pw]" in r.stdout, r.stdout


def test_install_wrapper_forwarding_omits_unset_vars(tmp_path):
    # The other half of the quoting contract: an UNSET AZ_INSTALL_* must expand to NOTHING (no
    # stray empty `env` argument), so a plain `--cli` run forwards none of them. Assert the child
    # sees them all unset (proving `${VAR:+"..."}` still drops cleanly when the var is unset).
    import subprocess

    w = desktop.install_wrapper_sh()
    probe = w.replace("    exec sudo -E env \\", "    exec env \\", 1)
    probe = probe.replace("if ! sudo test -r", "if ! true && ! sudo test -r")
    probe = probe.replace(
        f"        bash '{desktop.INSTALL_CLI_SCRIPT_PATH}'",
        "        bash -c 'for v in CHOICE DISK HOSTNAME USERNAME PASSWORD STAR_PASSWORD; do "
        "eval \"val=\\${AZ_INSTALL_$v-<UNSET>}\"; echo \"AZ_INSTALL_$v=[$val]\"; done'")
    path = tmp_path / "probe.sh"
    path.write_text(probe)
    r = subprocess.run(["bash", str(path), "--cli"], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    for v in ("CHOICE", "DISK", "HOSTNAME", "USERNAME", "PASSWORD", "STAR_PASSWORD"):
        assert f"AZ_INSTALL_{v}=[<UNSET>]" in r.stdout, f"{v} leaked a value: {r.stdout}"


def test_install_wrapper_forwards_identity_env_across_sudo():
    # The CLI installer now also prompts for hostname/user/passwords/timezone (Calamares
    # Users+Location parity). For an unattended SSH install those are pre-seeded via the
    # AZ_INSTALL_* family, which run_cli must forward ACROSS the sudo boundary (env_reset in
    # sudoers would otherwise drop them). Assert each identity var is explicitly forwarded.
    w = desktop.install_wrapper_sh()
    for var in ("AZ_INSTALL_HOSTNAME", "AZ_INSTALL_USERNAME", "AZ_INSTALL_FULLNAME",
                "AZ_INSTALL_PASSWORD", "AZ_INSTALL_ROOT_PASSWORD", "AZ_INSTALL_TIMEZONE"):
        assert f"{var}=${var}" in w, f"run_cli must forward {var} across sudo"
    # The help documents the unattended env pre-seed so an SSH user can discover it.
    assert "AZ_INSTALL_TIMEZONE" in w and "unattended" in w.lower()


def test_install_wrapper_is_valid_sh():
    # It ships as /bin/sh and is exec'd directly; a syntax error would break the installer
    # launch entirely. Syntax-check with `sh -n`.
    import subprocess
    import tempfile
    import os
    w = desktop.install_wrapper_sh()
    assert w.startswith("#!/bin/sh\n")
    fd, path = tempfile.mkstemp(suffix=".sh", dir="/tmp")
    try:
        os.write(fd, w.encode())
        os.close(fd)
        r = subprocess.run(["sh", "-n", path], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    finally:
        os.remove(path)


def test_openbox_rc_xml_entry_is_home_owned_conf():
    # OpenBox's rc.xml is a plain config (0o644) and must be handed to the live user
    # (home-owned; mirrored into /etc/skel) or the session cannot read its keybinds.
    entry = next(
        e for e in desktop.PLAN
        if e["dest"] == f"{desktop.HOME}/.config/openbox/rc.xml"
    )
    assert entry["mode"] == 0o644
    assert entry["owner"] == "home"
    assert entry["builder"] is desktop.openbox_rc_xml


def test_root_owned_dests_are_wrapper_cli_menu_entry_installed_autostart_dconf_and_picom():
    # Exactly seven PLAN entries are root-owned: the azzio command line interface (/usr/local/bin),
    # the installer wrapper (/usr/local/bin), the system-wide installer menu .desktop
    # (/usr/share/applications), the STAGED "installed" OpenBox autostart the Calamares install
    # copies onto the target, the TWO dconf system-theme files (the color-scheme=prefer-dark
    # keyfile + the dconf profile) under /etc, and OUR picom compositor config under /etc/xdg
    # (fading OFF, opaque frames -- shared by the live + installed autostart). Everything else is a
    # /home/main dotfile handed to the live user (uid 1000, gid 998). The media OSD is root-owned
    # too but is installed by the C build (build_osd), not as a PLAN text entry -- so it is not in
    # this set.
    root_dests = [e["dest"] for e in desktop.PLAN if e["owner"] == "root"]
    assert set(root_dests) == {
        desktop.INSTALL_WRAPPER_PATH,
        desktop.AZZIO_BIN_PATH,
        "/usr/share/applications/azzio-install.desktop",
        desktop.INSTALLED_AUTOSTART_STAGING_PATH,
        desktop.DCONF_THEME_KEYFILE_PATH,
        desktop.DCONF_PROFILE_USER_PATH,
        desktop.PICOM_CONFIG_PATH,
    }


def test_desktop_launcher_is_on_the_desktop_executable_and_home_owned():
    # The live-session "Azzio Linux Installer" launcher must land in ~/Desktop, be
    # executable (0o755, so a file manager trusts it), and be handed to the live user.
    entry = next(
        e for e in desktop.PLAN
        if e["dest"] == f"{desktop.HOME}/Desktop/azzio-install.desktop"
    )
    assert entry["builder"] is desktop.desktop_installer_launcher
    assert entry["mode"] == 0o755
    assert entry["owner"] == "home"


def test_desktop_launcher_content_names_installer_and_wrapper_and_icon():
    body = desktop.desktop_installer_launcher()
    assert "[Desktop Entry]" in body
    assert "Name=Azzio Linux Installer" in body
    # Exec names `--gui` explicitly (azzio-install has no default action now, so a bare
    # invocation would only print help -- the launcher must ask for the GUI installer).
    assert f"Exec={desktop.INSTALL_WRAPPER_PATH} --gui" in body
    assert f"Icon={desktop.INSTALLER_ICON_NAME}" in body
    assert "Type=Application" in body


def test_installer_launchers_all_use_the_azzio_icon():
    # Both installer launchers (the Desktop one and the application-menu one) must
    # reference the "Az'" installer icon (not the old generic system-software-install).
    for body in (
        desktop.desktop_installer_launcher(),
        desktop.install_menu_desktop(),
    ):
        assert f"Icon={desktop.INSTALLER_ICON_NAME}" in body
        assert "system-software-install" not in body
        assert "Name=Azzio Linux Installer" in body


def test_installer_launchers_all_invoke_gui_mode():
    # Neither .desktop launcher may rely on a default action -- azzio-install has none now.
    # Both the Desktop launcher and the application-menu entry must Exec `--gui` so a
    # double-click / menu-open starts the Calamares GUI (not the help text).
    for body in (
        desktop.desktop_installer_launcher(),
        desktop.install_menu_desktop(),
    ):
        assert f"Exec={desktop.INSTALL_WRAPPER_PATH} --gui" in body


def test_installer_icon_paths_are_standard_system_locations():
    # The icon is installed to /usr/share/pixmaps and hicolor 256x256 apps (rasterized
    # PNGs) plus the hicolor SCALABLE apps dir (the SVG master) so the basename Icon=
    # resolves at any size; all must be absolute system paths.
    assert desktop.INSTALLER_ICON_PIXMAP == "/usr/share/pixmaps/azzio-installer.png"
    assert desktop.INSTALLER_ICON_HICOLOR == (
        "/usr/share/icons/hicolor/256x256/apps/azzio-installer.png"
    )
    assert desktop.INSTALLER_ICON_SCALABLE == (
        "/usr/share/icons/hicolor/scalable/apps/azzio-installer.svg"
    )
    # The icon is standardized as a scalable vector master (azzio.svg), like kitty.svg.
    assert desktop.INSTALLER_ICON_ASSET == "icons/azzio.svg"
    assert desktop.INSTALLER_ICON_PNG_SIZE == 256


def test_home_owned_dests_live_under_home():
    for entry in desktop.PLAN:
        if entry["owner"] == "home":
            assert entry["dest"].startswith(desktop.HOME + "/"), entry["dest"]


def test_home_owner_gid_is_autologin_group():
    # The chown after emit uses (1000, 998); 998 is the autologin group gid that
    # libraries/system.py assigns. A drift here would chown the live tree to a
    # nonexistent gid.
    assert desktop.HOME_OWNER == (1000, 998)
    assert desktop.HOME == "/home/main"


# --- emit_plan(): PLAN + bash_profile, without mutating PLAN ----------------

def test_emit_plan_length_is_nineteen_plus_bash_profile():
    # 19 PLAN entries + the appended .bash_profile snippet = 20. emit_plan() is the
    # single sequence compiler.py iterates. (19 = 18 + the new /etc/xdg/azzio-picom.conf
    # compositor config that disables the picom fade + transparent-titlebar defaults.)
    assert len(desktop.emit_plan()) == 20


def test_emit_plan_prefix_is_plan():
    # The first entries are exactly PLAN (same dict objects), the bash_profile is
    # appended last.
    assert desktop.emit_plan()[:len(desktop.PLAN)] == desktop.PLAN


def test_emit_plan_last_entry_is_bash_profile():
    last = desktop.emit_plan()[-1]
    assert last["builder"] is desktop.bash_profile_startx
    assert last["dest"] == desktop.BASH_PROFILE_DEST
    assert last["mode"] == 0o644
    assert last["owner"] == "home"


def test_bash_profile_dest_is_home_bash_profile():
    assert desktop.BASH_PROFILE_DEST == f"{desktop.HOME}/.bash_profile"


def test_emit_plan_does_not_mutate_module_plan():
    # compiler.py may call emit_plan() more than once; it must not grow PLAN each call
    # (PLAN + [x] builds a new list, so the constant stays fixed).
    before = len(desktop.PLAN)
    desktop.emit_plan()
    desktop.emit_plan()
    assert len(desktop.PLAN) == before == 19


# --- xinitrc: OpenBox X11 session, no flash ---------------------------------

def test_xinitrc_execs_openbox_session():
    # startx hands the session to `openbox-session`, which is BOTH the window manager
    # AND the session bootstrap (it sources environment/autostart and reads rc.xml).
    assert "exec openbox-session" in desktop.xinitrc()


def test_xinitrc_exports_openbox_desktop_classification():
    # logind sets XDG_SESSION_TYPE=x11; we export XDG_CURRENT_DESKTOP/DESKTOP_SESSION
    # so XDG-aware tools (autostart OnlyShowIn, etc.) classify the session as openbox.
    out = desktop.xinitrc()
    assert "export XDG_CURRENT_DESKTOP=openbox" in out
    assert "export DESKTOP_SESSION=openbox" in out


def test_xinitrc_has_no_cyan_solid_flash():
    # THE regression this fixes: the old session did `xsetroot -solid <cyan>`,
    # flashing a solid color before the desktop painted. The new xinitrc must NOT
    # set any solid color; it paints the wallpaper instead (see below).
    out = desktop.xinitrc()
    assert "xsetroot -solid" not in out
    assert "#06b8fd" not in out


def test_xinitrc_prepaints_wallpaper_before_exec():
    # No-flash contract: feh paints the SAME wallpaper onto the X root BEFORE the
    # exec that starts OpenBox, so the first visible frame is the wallpaper and the
    # autostart's own feh repaint is invisible (identical pixels). feh needs the
    # actual image FILE (it cannot take a directory). The image is chosen by the
    # per-user `azzio wallpaper` pointer, defaulting to the shipped "years" image.
    out = desktop.xinitrc()
    # Reads the pointer and falls back to the WALLPAPER_IMAGE_FILE default, then paints it.
    assert 'cat "$HOME/.config/azzio/wallpaper"' in out
    assert "|| _azwp='" + desktop.WALLPAPER_IMAGE_FILE + "'" in out
    assert 'feh --no-fehbg --bg-fill "$_azwp"' in out
    feh_idx = out.index("feh --no-fehbg --bg-fill")
    exec_idx = out.index("exec openbox-session")
    assert feh_idx < exec_idx


def test_xinitrc_seeds_systemd_user_env_before_exec():
    # ROOT CAUSE of the reported "Save image as doesn't work" in Librewolf: our ~/.xinitrc
    # replaces Arch's stock xinitrc wholesale and never sources
    # /etc/X11/xinit/xinitrc.d/50-systemd-user.sh, so the systemd USER manager (and the
    # D-Bus activation environment) had NO DISPLAY. xdg-desktop-portal-gtk (Type=dbus,
    # D-Bus-activated) then exited 1 with "cannot open display" whenever an app invoked the
    # FileChooser portal -- and Librewolf routes Save-image-as through that portal, so the
    # save dialog never mapped. The xinitrc must import DISPLAY (and XAUTHORITY) into BOTH
    # the systemd user manager and the dbus activation env, BEFORE exec-ing OpenBox so the
    # portal backend can start on demand.
    out = desktop.xinitrc()
    assert "systemctl --user import-environment DISPLAY XAUTHORITY" in out
    assert "dbus-update-activation-environment DISPLAY XAUTHORITY" in out
    # The dbus-update tool is guarded so a missing binary never breaks session startup.
    assert "command -v dbus-update-activation-environment" in out
    # Must run BEFORE the session starts, else the activated portal backend is spawned
    # by the still-DISPLAY-less manager and the seeding is too late to help it.
    import_idx = out.index("systemctl --user import-environment")
    exec_idx = out.index("exec openbox-session")
    assert import_idx < exec_idx


# --- SPICE guest agent: the pointer-regression fix ---------------------------

def test_spice_vdagent_started_in_both_autostarts():
    # ROOT CAUSE of the reported pointer regression: the VM is a SPICE guest (the
    # com.redhat.spice.0 channel is present) but spice-vdagent was absent, so the guest pointer
    # drifted out of sync with the SPICE client -- no hover highlight, dropped left-clicks, stale
    # labels. The fix starts spice-vdagent from the session autostart (the SESSION half; the
    # spice-vdagentd daemon is enabled via systemd). It must run on BOTH the live and the
    # installed session (both inherit the shared common block). Guarded so it is harmless off
    # SPICE (it exits with no channel).
    for au in (desktop.openbox_autostart(), desktop.openbox_autostart_installed()):
        assert "spice-vdagent" in au
        assert "command -v spice-vdagent" in au   # guarded, never breaks the session


# --- Compositor (picom): kill the fade + transparent-titlebar packaged defaults ----------

def test_picom_config_disables_fading_and_frame_opacity():
    # The reported bugs -- "applications fade in and out" and "the openbox titlebar is
    # transparent" -- are BOTH the packaged /etc/xdg/picom.conf defaults (fading = true,
    # frame-opacity = 0.9), NOT anything in our OpenBox themerc. OUR picom config must turn
    # both OFF: no window fading, and every opacity fully opaque (a solid titlebar).
    conf = desktop.picom_conf()
    assert "fading = false;" in conf
    assert "frame-opacity = 1.0;" in conf
    assert "inactive-opacity = 1.0;" in conf
    assert "active-opacity = 1.0;" in conf
    # Nothing may re-enable fading (a stray `fading = true;` would resurrect the bug).
    assert "fading = true;" not in conf


def test_autostart_runs_picom_with_our_config():
    # picom must be launched with `--config <our file>` so it does NOT fall back to the
    # packaged /etc/xdg/picom.conf (whose fade + 0.9 frame-opacity are the reported bugs).
    # It runs in BOTH the live and installed session (the shared common block) and stays
    # guarded so a machine without picom is unaffected.
    for au in (desktop.openbox_autostart(), desktop.openbox_autostart_installed()):
        assert f"picom --config {desktop.PICOM_CONFIG_PATH}" in au
        assert "command -v picom" in au   # guarded, never breaks the session


def test_picom_config_is_root_owned_conf_under_xdg():
    # The compositor config is a system file both sessions read (the autostart is shared), so
    # it is root-owned and a plain 0o644 config under /etc/xdg (picom's system config dir).
    assert desktop.PICOM_CONFIG_PATH == "/etc/xdg/azzio-picom.conf"
    entry = next(e for e in desktop.PLAN if e["dest"] == desktop.PICOM_CONFIG_PATH)
    assert entry["owner"] == "root"
    assert entry["mode"] == desktop._CONF


def test_autostart_defaults_resolution_to_1920x1080():
    # REGRESSION: a DE-less OpenBox session has no display manager to choose a mode, so X came
    # up on the virtio-gpu PREFERRED mode (a quirky 1920x1031), not 1920x1080 -- the e2e
    # resolution check failed. The shared autostart block RAISES an undersized primary to
    # 1920x1080, so BOTH the live and installed sessions default to 1080. It must run BEFORE the
    # wallpaper (feh) line so the root is painted at the final geometry.
    for au in (desktop.openbox_autostart(), desktop.openbox_autostart_installed()):
        assert desktop.DEFAULT_RESOLUTION == "1920x1080"
        assert f"--mode {desktop.DEFAULT_RESOLUTION}" in au
        assert "command -v xrandr" in au          # guarded, never breaks the session
        # ordering: the resolution block precedes the feh wallpaper repaint.
        assert au.index("--mode 1920x1080") < au.index("feh")
        # CRITICAL: it must NEVER downgrade a LARGER display. The switch is gated on the ACTIVE
        # mode's pixel AREA being strictly smaller than 1920x1080 -- so a 1440p/4K/1920x1200
        # primary (whose area is >= 1920*1080) is left alone. Assert that area guard is present.
        assert "1920 * 1080" in au
        assert "-lt" in au                          # active-area < 1080p is the switch condition


def test_autostart_resolution_never_downgrades_larger_display(tmp_path):
    # Drive the emitted resolution block through /bin/sh against a STUB xrandr for the display
    # shapes that matter, proving the guard raises an undersized primary but never shrinks a
    # larger one (the exact failure mode an area-blind "1080 offered && not active" guard has).
    import subprocess, os, stat
    au = desktop.openbox_autostart()
    block = "#!/bin/sh\n" + au[au.index("# 0. Default resolution"):au.index("# 1. Wallpaper")]
    blockfile = tmp_path / "resblock.sh"
    blockfile.write_text(block)
    stubdir = tmp_path / "stub"
    stubdir.mkdir()
    switchlog = tmp_path / "switch.log"

    def run(xrandr_query: str) -> bool:
        """Return True iff the block issued an `xrandr --output ... --mode` switch."""
        (stubdir / "xrandr").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--query" ]; then cat <<\'XR\'\n' + xrandr_query + "\nXR\n  exit 0\nfi\n"
            f'echo "SWITCH: $*" >> "{switchlog}"\nexit 0\n')
        (stubdir / "xrandr").chmod(0o755)
        switchlog.write_text("")
        env = dict(os.environ, PATH=f"{stubdir}:{os.environ['PATH']}")
        subprocess.run(["sh", str(blockfile)], env=env, check=False,
                       capture_output=True, timeout=20)
        return switchlog.read_text().strip() != ""

    # VM regression: active 1920x1031 (smaller area than 1080p), 1080 offered -> UPGRADE.
    assert run("Virtual-1 connected primary 1920x1031+0+0\n"
               "   1920x1031     75.00*+\n   1920x1080     60.00\n   1280x720      60.00") is True
    # 4K primary active, 1080 merely offered -> MUST NOT downgrade.
    assert run("DP-1 connected primary 3840x2160+0+0\n"
               "   3840x2160     60.00*+\n   1920x1080     60.00") is False
    # 1440p primary active -> MUST NOT downgrade.
    assert run("DP-2 connected primary 2560x1440+0+0\n"
               "   2560x1440     59.95*+\n   1920x1080     60.00") is False
    # already at 1080 -> no-op.
    assert run("Virtual-1 connected primary 1920x1080+0+0\n"
               "   1920x1080     60.00*+\n   1280x720      60.00") is False
    # undersized laptop panel (1600x900) with 1080 available -> UPGRADE.
    assert run("eDP-1 connected primary 1600x900+0+0\n"
               "   1600x900      60.00*+\n   1920x1080     60.00") is True
    # MULTI-HEAD, the order-dependent trap: a SMALL non-primary output is listed BEFORE the large
    # primary. The active-mode parse MUST be scoped to the primary, so the 1440p/4K primary is NOT
    # downgraded just because a 1366x768/1600x900 secondary appears first in xrandr's enumeration.
    assert run("HDMI-1 connected 1366x768+2560+0\n   1366x768      59.79*+\n   1920x1080     60.00\n"
               "DP-1 connected primary 2560x1440+0+0\n   2560x1440     59.95*+\n   1920x1080     60.00") is False
    assert run("HDMI-1 connected 1600x900+0+0\n   1600x900      60.00*+\n   1920x1080     60.00\n"
               "DP-1 connected primary 3840x2160+0+0\n   3840x2160     60.00*+\n   1920x1080     60.00") is False
    # MULTI-HEAD where the PRIMARY itself is the undersized one -> it (and only it) is upgraded.
    assert run("Virtual-1 connected primary 1920x1031+0+0\n   1920x1031     75.00*+\n   1920x1080     60.00\n"
               "HDMI-1 connected 2560x1440+1920+0\n   2560x1440     59.95*+") is True
    # no `primary` flag: fall back to the first connected output (undersized) -> upgrade it.
    assert run("eDP-1 connected 1600x900+0+0\n   1600x900      60.00*+\n   1920x1080     60.00") is True
    # ultrawide primary (huge area) -> never downgraded.
    assert run("DP-1 connected primary 3440x1440+0+0\n   3440x1440     59.97*+\n   1920x1080     60.00") is False


def test_spice_vdagent_in_manifest():
    # The SPICE agent package must be shipped (the releng baseline has open-vm-tools /
    # qemu-guest-agent / virtualbox-guest-utils but NOT spice-vdagent).
    import paths
    manifest = paths.PACKAGES_FILE.read_text(encoding="utf-8")
    pkgs = [ln.strip() for ln in manifest.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    assert "spice-vdagent" in pkgs


# --- OpenBox rc.xml: Super -> menu, no root menu, borderless menu window -----

def test_rc_xml_binds_super_and_menu_to_the_launcher():
    # The Super key opens the Azzio menu. OpenBox cannot bind a lone modifier, so
    # xcape turns a solo Super_L tap into Super_L+Menu; rc.xml binds THAT chord
    # (W-Menu) and the bare Menu/Apps key to the menu launcher so either opens it.
    out = desktop.openbox_rc_xml()
    assert desktop.SUPER_MENU_KEYSYM == "Menu"
    assert '<keybind key="W-Menu">' in out
    assert '<keybind key="Menu">' in out
    # Both keybinds run the single application-menu launcher.
    assert desktop.MENU_LAUNCHER == desktop._app_menu.MENU_LAUNCHER_SYSTEM_PATH
    assert desktop.MENU_LAUNCHER == "/usr/local/bin/azzio-application-menu"
    assert f"<command>{desktop.MENU_LAUNCHER}</command>" in out


def test_rc_xml_binds_alt_tab_to_switcher_not_nextwindow():
    # Alt+Tab / Alt+Shift+Tab run the Azzio window switcher (a horizontal, Windows-like
    # LIVE-thumbnail overlay), NOT OpenBox's built-in vertical NextWindow list. The
    # launcher takes a direction (--next forward, --prev backward).
    out = desktop.openbox_rc_xml()
    assert '<keybind key="A-Tab">' in out
    assert '<keybind key="A-S-Tab">' in out
    assert desktop.SWITCHER_LAUNCHER == "/usr/local/bin/azzio-window-switcher"
    assert f"<command>{desktop.SWITCHER_LAUNCHER} --next</command>" in out
    assert f"<command>{desktop.SWITCHER_LAUNCHER} --prev</command>" in out
    # The old built-in switcher actions are gone.
    assert "NextWindow" not in out
    assert "PreviousWindow" not in out


def test_autostart_starts_picom_and_switcher_daemon():
    # The compositor (picom) and the switcher daemon are started warm from the SHARED
    # autostart block, so they land on BOTH the live and installed sessions. picom is
    # required for the switcher's live thumbnails (XComposite backing pixmaps).
    for au in (desktop.openbox_autostart(), desktop.openbox_autostart_installed()):
        assert "picom" in au
        assert desktop.SWITCHER_DAEMON_BIN in au


def _root_context_block(rc_xml: str) -> str:
    # Return just the <context name="Root">...</context> block from rc.xml, so a test can
    # assert on the desktop-click bindings in isolation (the window-icon context is
    # neutered separately -- see test_rc_xml_window_icon_opens_no_menu).
    start = rc_xml.index('<context name="Root">')
    end = rc_xml.index("</context>", start)
    return rc_xml[start:end]


def test_rc_xml_root_menu_is_disabled():
    # THE regression this fixes: the user asked to "remove the right click menu ...
    # disable that menu completely". rc.xml must STILL declare a "Root" context (so the
    # element tree is explicit) but that context must open NO menu -- no ShowMenu action
    # and no root-menu reference inside it -- and rc.xml must NOT point OpenBox at any
    # menu.xml (there is none). Right/middle-clicking the desktop then does nothing. (The
    # titlebar window-icon popup is disabled too, but by a separate change -- see
    # test_rc_xml_window_icon_opens_no_menu.)
    out = desktop.openbox_rc_xml()
    root_block = _root_context_block(out)
    assert 'name="ShowMenu"' not in root_block
    assert "root-menu" not in root_block
    # No <menu><file>menu.xml</file> block anywhere, and no root-menu declared.
    assert "<menu>root-menu</menu>" not in out
    assert "<file>menu.xml</file>" not in out
    assert "menu.xml" not in out


def test_rc_xml_menu_window_is_undecorated():
    # The Azzio application menu is a borderless override-redirect Tk window; rc.xml
    # must match it (`*azzio*menu*`) and give it NO OpenBox decorations, so no
    # titlebar/border wraps the launcher.
    out = desktop.openbox_rc_xml()
    assert '<application name="*azzio*menu*">' in out
    assert "<decor>no</decor>" in out


def test_rc_xml_opens_calamares_restored_and_centered():
    # The Calamares installer must open RESTORED-DOWN (NOT maximized) and CENTERED every time,
    # including on a REOPEN, so the live wallpaper stays visible around it. Every other app
    # opens maximized (the wildcard rule); the installer opts out with <maximized>no</maximized>
    # and a per-application <position force="yes"> that overrides Calamares' remembered geometry.
    import xml.etree.ElementTree as ET

    root = ET.fromstring(desktop.openbox_rc_xml())
    ns = {"ob": "http://openbox.org/3.4/rc"}
    app = None
    for a in root.findall(".//ob:applications/ob:application", ns):
        # Match on the CLASS field (res_class, lowercase "calamares"). name= is also present
        # (res_name, likewise lowercase "calamares").
        if a.get("class") == desktop.CALAMARES_WM_CLASS:
            app = a
            break
    assert app is not None, "no <application class='calamares'> rule in rc.xml"
    # Both WM_CLASS fields matched, each with its exact (lowercase) case -- OpenBox matching is
    # case-sensitive, so a capitalised class= would not match the real res_class and the rule
    # would silently no-op (letting the installer open maximized, the bug this guards against).
    assert app.get("name") == desktop.CALAMARES_WM_NAME
    assert app.get("class") == desktop.CALAMARES_WM_CLASS
    # LOAD-BEARING for the new prompt: the installer must OPT OUT of the wildcard's
    # <maximized>yes</maximized>, otherwise it opens full-screen (no visible background).
    maximized = app.find("ob:maximized", ns)
    assert maximized is not None and maximized.text == "no", \
        "Calamares rule must set <maximized>no</maximized> (restored-down, background visible)"
    pos = app.find("ob:position", ns)
    assert pos is not None and pos.get("force") == "yes", "position must be force='yes'"
    assert pos.find("ob:x", ns).text == "center"
    assert pos.find("ob:y", ns).text == "center"


def test_calamares_wm_class_constants_match_verified_derivation():
    # The installer window's WM_CLASS was VERIFIED with `xprop WM_CLASS` on the running
    # installer in the live VM -- BOTH fields are the lowercase "calamares":
    #     WM_CLASS(STRING) = "calamares", "calamares"
    #   res_name  = argv[0] basename = "calamares" (our wrapper runs `sudo -E calamares`)
    #   res_class = applicationName() = "calamares" (NOT a capitalised "Calamares" -- an
    #               earlier guess used a capital C, which made the OpenBox rule silently no-op
    #               because matching is case-sensitive, so the installer opened maximized).
    # Pinned so a drift is caught here, not on the guest.
    assert desktop.CALAMARES_WM_NAME == "calamares"
    assert desktop.CALAMARES_WM_CLASS == "calamares"


def test_rc_xml_is_wellformed_xml():
    # Parse the FULL document, COMMENTS INCLUDED. OpenBox parses rc.xml with libxml2,
    # which is NOT lenient about comment content: a "--" (double hyphen) inside a
    # <!-- --> comment is a FATAL XML error, and OpenBox pops a blocking "Openbox Syntax
    # Error" dialog and falls back to stock keybinds (no Super -> menu). A previous version
    # of this test stripped comments before parsing "so the prose can contain --", which
    # let exactly that bug ship (a "opens cleanly -- an <iconic>..." comment). Parsing the
    # raw document is the whole point: it validates what OpenBox actually reads, so an
    # illegal double hyphen (or any malformed comment/tag) fails HERE, not on the guest.
    import xml.etree.ElementTree as ET

    ET.fromstring(desktop.openbox_rc_xml())


# --- OpenBox root menu removed: no menu.xml builder at all ------------------

def test_openbox_menu_xml_builder_is_gone():
    # The OpenBox root menu was removed at the user's request; the builder that produced
    # menu.xml must no longer exist (nothing should be able to re-emit it).
    assert not hasattr(desktop, "openbox_menu_xml")


def test_no_plan_entry_emits_a_menu_xml():
    # Belt: no PLAN destination is a menu.xml, so the file is never written to the
    # airootfs (the empty Root context in rc.xml is the whole "no right-click menu" fix).
    assert not any(e["dest"].endswith("menu.xml") for e in desktop.PLAN)


# --- Titlebar buttons: min/max/close mouse contexts actually bound -----------

def test_rc_xml_binds_titlebar_button_contexts():
    # THE regression this fixes: OpenBox draws the min/max/close buttons from the theme's
    # titleLayout, but a button DOES NOTHING unless its mouse context is bound in rc.xml.
    # The old rc.xml bound only "Titlebar", so the buttons rendered but were dead. rc.xml
    # must now bind the Iconify/Maximize/Close contexts to their click actions.
    out = desktop.openbox_rc_xml()
    assert '<context name="Iconify">' in out
    assert '<context name="Maximize">' in out
    assert '<context name="Close">' in out
    # Each button's click must fire its action.
    assert '<action name="Iconify"/>' in out
    assert '<action name="ToggleMaximize"/>' in out
    assert '<action name="Close"/>' in out


def _icon_context_block(rc_xml: str) -> str:
    # Return just the <context name="Icon">...</context> block from rc.xml, so a test can
    # assert on the titlebar-icon click bindings in isolation.
    start = rc_xml.index('<context name="Icon">')
    end = rc_xml.index("</context>", start)
    return rc_xml[start:end]


def test_rc_xml_window_icon_opens_no_menu():
    # THE regression this fixes: pressing the application ICON on the left of the OpenBox
    # titlebar used to pop up the built-in client-menu ("Send to desktop"/minimise/maximise/
    # close). The user asked that it "didn't do that in the first place, delete that, make
    # sure it doesn't happen". So the Icon mouse context must STILL exist (the icon is kept
    # to show the window's branding tile) but bind NO ShowMenu -- clicking it does nothing.
    # The icon (`N`) stays in titleLayout so the branding tile still renders; only the click
    # menu is removed.
    out = desktop.openbox_rc_xml()
    assert '<context name="Icon">' in out          # the icon context is still declared
    assert "NLIMC" in out                          # the icon (`N`) still shows in the bar
    icon_block = _icon_context_block(out)
    # No menu opens from the icon: no ShowMenu action and no client-menu reference inside it.
    assert 'name="ShowMenu"' not in icon_block
    assert "client-menu" not in icon_block
    # Belt: NO context or keybind anywhere opens the client-menu, so the popup has no trigger
    # path at all (the titlebar/keyboard never bind ShowMenu client-menu either).
    assert "<menu>client-menu</menu>" not in out
    assert 'name="ShowMenu"' not in out


# --- Exactly one desktop: no multiple workspaces -----------------------------

def test_rc_xml_declares_exactly_one_desktop():
    # THE regression this fixes: OpenBox shipped with <number>2</number>, so there were TWO
    # live desktops (the client-menu even offered "Send to desktop" between them). The user
    # asked that there not be multiple desktops "to begin with -- there is simply one desktop
    # and that's that". rc.xml must declare EXACTLY ONE desktop: <number>1</number> and a
    # single <name>. Parsed via ElementTree so the assertion is on the real element values.
    import xml.etree.ElementTree as ET

    root = ET.fromstring(desktop.openbox_rc_xml())
    ns = {"ob": "http://openbox.org/3.4/rc"}
    number = root.find(".//ob:desktops/ob:number", ns)
    assert number is not None and number.text == "1", "OpenBox must expose a single desktop"
    names = root.findall(".//ob:desktops/ob:names/ob:name", ns)
    assert len(names) == 1, f"exactly one desktop name expected, got {[n.text for n in names]}"
    # The old two-desktop config must be gone (no stray second workspace name).
    assert "<number>2</number>" not in desktop.openbox_rc_xml()
    assert "<name>two</name>" not in desktop.openbox_rc_xml()


def test_rc_xml_has_no_go_to_desktop_keybinds():
    # With a SINGLE desktop there is nowhere to switch, so the workspace-switch keybinds
    # (the old C-A-Left/Right GoToDesktop binds, and any SendToDesktop) must be removed --
    # leaving them would bind keys to no-ops and imply multiple desktops still exist.
    out = desktop.openbox_rc_xml()
    assert 'name="GoToDesktop"' not in out
    assert 'name="SendToDesktop"' not in out
    assert 'key="C-A-Left"' not in out
    assert 'key="C-A-Right"' not in out


# --- Window resize: edge + corner mouse contexts actually bound --------------

def test_rc_xml_binds_window_edge_and_corner_resize():
    # THE regression this fixes: OpenBox draws a resize border/handle around every
    # decorated window, but dragging an edge or corner does NOTHING unless that context is
    # bound (same shape as the dead titlebar buttons). The old rc.xml bound only the
    # Frame's Alt+Right drag, so a plain edge/corner grab was dead. rc.xml must now bind
    # the four side contexts (each Resize-ing its own edge) and the four corners (Resize in
    # both axes) -- the canonical OpenBox defaults.
    out = desktop.openbox_rc_xml()
    # Each side edge is its own context, resizing that one edge.
    assert '<context name="Top">' in out
    assert '<context name="Bottom">' in out
    assert '<context name="Left">' in out
    assert '<context name="Right">' in out
    assert "<action name=\"Resize\"><edge>top</edge></action>" in out
    assert "<action name=\"Resize\"><edge>bottom</edge></action>" in out
    assert "<action name=\"Resize\"><edge>left</edge></action>" in out
    assert "<action name=\"Resize\"><edge>right</edge></action>" in out
    # All four corners share one context that resizes freely (Resize with no <edge>).
    assert '<context name="TRCorner BRCorner TLCorner BLCorner">' in out
    # A drag-to-Resize with no edge (the corner case) is present.
    assert '<mousebind button="Left" action="Drag"><action name="Resize"/></mousebind>' in out


def test_rc_xml_keeps_alt_right_drag_resize_on_frame():
    # The existing whole-window resize (Alt + Right-drag anywhere on the frame) must be
    # KEPT alongside the new edge/corner grabs -- it is the fallback that always works even
    # on a borderless/edge-less window.
    out = desktop.openbox_rc_xml()
    frame_start = out.index('<context name="Frame">')
    frame_end = out.index("</context>", frame_start)
    frame_block = out[frame_start:frame_end]
    assert '<mousebind button="A-Right" action="Drag"><action name="Resize"/></mousebind>' in frame_block


# --- Titlebar: Azzio theme, 25% smaller, single flat colour ----------------

def test_rc_xml_uses_the_azzio_dark_theme_by_default():
    # rc.xml must name the Azzio DARK theme by default (dark is the Azzio default), not
    # stock Clearlooks. `azzio theme --white` rewrites this <name> to the light "Azzio".
    out = desktop.openbox_rc_xml()
    assert desktop.OPENBOX_THEME_NAME == "Azzio"
    assert desktop.OPENBOX_THEME_NAME_DARK == "Azzio-Dark"
    assert desktop.OPENBOX_THEME_DEFAULT == "Azzio-Dark"
    assert f"<name>{desktop.OPENBOX_THEME_DEFAULT}</name>" in out
    assert "<name>Clearlooks</name>" not in out


def test_rc_xml_sets_the_titlebar_font():
    # The dominant half of the bar height is the title font, and OpenBox sizes the min/max/
    # close BUTTONS and their GLYPHS to the label -- so the title font is the single native
    # lever for button-icon size. The bar was cut 25% (to 9pt), grown +10% (the "+10% topbar"
    # ask) to 10pt, and now nudged one more point to 11pt for the "slightly increase the size
    # of the button icons" ask (the new prompt). OpenBox has no separate button-glyph-size
    # field, so the icons grow via this font; the tiny extra bar height is the unavoidable
    # ride-along. Set for both the active and inactive window title.
    out = desktop.openbox_rc_xml()
    assert desktop.TITLE_FONT_SIZE == 11
    # The growth is an explicit factor on the scale-derived base (round(pt(7)=9 * 1.20) == 11),
    # not a corruption of the global scale; pin the factor and the arithmetic.
    assert desktop.TOPBAR_GROWTH == 1.20
    assert desktop.TITLE_FONT_SIZE == round(desktop._scale.pt(
        desktop._scale.OPENBOX_TITLE_FONT_STOCK) * desktop.TOPBAR_GROWTH)
    assert desktop.TITLE_FONT_SIZE > 10   # strictly larger than the previous 10pt bar
    assert f"<size>{desktop.TITLE_FONT_SIZE}</size>" in out
    assert '<font place="ActiveWindow">' in out
    assert '<font place="InactiveWindow">' in out


def test_theme_rc_titlebar_padding():
    # The size-driving padding fields. padding.height stays 5 (the +10% topbar rides on the
    # title font, not the padding, so height stays ~10% not more). padding.width was DOUBLED
    # 5 -> 10 to make the distance between the min/max/close buttons "twice as large" (the new
    # prompt). OpenBox has no separate per-button gap field -- padding.width is the spacing
    # between EVERY adjacent title element -- but because the stretchy label spring in the
    # middle (titleLayout NLIMC: icon, Label, iconify, maximize, close) swallows the extra
    # icon<->label space, the doubled padding reads almost entirely as a wider gap between the
    # three right-hand buttons, with the close (exit) button staying rightmost and min/max
    # sliding left of it -- exactly the requested "keep exit where it is, move the others left".
    out = desktop.openbox_theme_rc()
    assert desktop.OPENBOX_THEME_PADDING_HEIGHT == 5
    assert desktop.OPENBOX_THEME_PADDING_WIDTH == 10
    assert f"padding.height: {desktop.OPENBOX_THEME_PADDING_HEIGHT}" in out
    assert f"padding.width: {desktop.OPENBOX_THEME_PADDING_WIDTH}" in out
    # The button gap is TWICE the previous value (5 -> 10): the "twice as large" ask.
    assert desktop.OPENBOX_THEME_PADDING_WIDTH == 2 * 5
    # Both strictly larger than the stock Clearlooks originals (2 / 3), so the bar keeps
    # breathing room above stock; the width is wider than the previous 5 (the button-gap bump).
    assert desktop.OPENBOX_THEME_PADDING_HEIGHT > 2
    assert desktop.OPENBOX_THEME_PADDING_WIDTH > 5


def test_theme_rc_removes_the_bottom_handle_bar():
    # THE regression this fixes: OpenBox draws a HANDLE -- a full-width near-white
    # (#eaebec) strip along the BOTTOM edge of every decorated window, sized by
    # window.handle.width. The user asked for that "thin white bar under a window" gone,
    # so window.handle.width must be 0 (a zero-height handle draws nothing). A drift back
    # to a positive width regrows the bar.
    out = desktop.openbox_theme_rc()
    assert desktop.OPENBOX_THEME_HANDLE_WIDTH == 0
    assert "window.handle.width: 0" in out


def test_theme_rc_active_separator_blends_into_the_titlebar():
    # THE regression this fixes: OpenBox draws a FLAT 1px line at the titlebar's BOTTOM edge
    # (between titlebar and client) via window.active.title.separator.color. The dark theme
    # once used the accent value, which rendered as a stray bright-CYAN bar under a focused,
    # non-maximized window (the reported visual bug).
    #
    # The titlebar is now a FLAT SOLID fill of the bottom colour (title_bg_to_split), so the
    # separator must match THAT single colour -- dark = #0a0f14, light = #7AA1D1 -- to draw
    # the same colour as the bar pixel above it (zero contrast, no line).
    dark = desktop.openbox_theme_rc(dark=True)
    light = desktop.openbox_theme_rc(dark=False)
    assert "window.active.title.separator.color: #0a0f14" in dark
    assert "window.active.title.separator.color: #7AA1D1" in light
    # The separator matches the titlebar's flat fill colour in each.
    for rc, bottom in ((dark, "#0a0f14"), (light, "#7AA1D1")):
        assert f"*.title.bg.color: {bottom}" in rc
    # The accent (logo cyan) must NOT come back as the active separator.
    assert "window.active.title.separator.color: #06b8fd" not in dark
    assert "window.active.title.separator.color: #4e76a8" not in light


def test_light_theme_keeps_the_clearlooks_cyan_titlebar_colour():
    # The LIGHT ("Azzio") theme keeps its familiar "cyan'ish" Clearlooks look, but now as a
    # SINGLE FLAT colour: the titlebar is the Clearlooks gradient's BOTTOM split (#7AA1D1),
    # not the old top #8CB0DC (which is gone entirely with the gradient). The DARK theme
    # (default) uses the near-black surface instead -- so the Clearlooks cyan must NOT be dark.
    light = desktop.openbox_theme_rc(dark=False)
    dark = desktop.openbox_theme_rc(dark=True)
    assert "*.title.bg.color: #7AA1D1" in light
    assert "#7AA1D1" not in dark
    # The dark theme uses the Azzio near-black surface palette (matching the application menu).
    assert "*.title.bg.color: #0a0f14" in dark
    # Both keep the shared geometry (padding.height 5 + no bottom handle).
    for out in (light, dark):
        assert "padding.height: 5" in out
        assert "window.handle.width: 0" in out


def test_theme_rc_titlebar_and_buttons_are_one_flat_colour():
    # The user asked to drop the "two color split" and use ONE colour (the bottom of the old
    # gradient) for the whole topbar. The titlebar and its buttons must be `Flat Solid` (no
    # splitvertical gradient), so there is no top/bottom two-tone. Assert the flat textures and
    # that no gradient/split key survives on the titlebar or button backgrounds.
    for dark in (True, False):
        out = desktop.openbox_theme_rc(dark=dark)
        # Titlebar + buttons are flat solid.
        assert "*.title.bg: Flat Solid" in out
        assert "window.active.button.*.bg: Flat Solid Border" in out
        assert "window.inactive.title.bg: Flat Solid" in out
        assert "window.inactive.button.*.bg: Flat Solid Border" in out
        assert "window.active.button.hover.bg: Flat Solid Border" in out
        # No gradient/split remnants on the flattened surfaces (they would reintroduce the
        # two-tone the user asked to remove).
        assert "*.title.bg: Raised Gradient" not in out
        assert "*.title.bg.colorTo" not in out
        assert "*.title.bg.color.splitTo" not in out
        assert "window.active.button.*.bg.colorTo" not in out
        assert "window.inactive.title.bg.colorTo" not in out


def test_theme_rc_buttons_have_a_white_border():
    # The user asked the min/max/close buttons to have a WHITE outline. The button border is
    # window.active.button.*.bg.border.color; both the resting AND hover border must be white
    # so the outline stays white on hover (only the button FILL changes on hover). Assert it
    # in the DARK (default) and LIGHT themes; the inactive buttons keep their dim palette.
    for dark in (True, False):
        out = desktop.openbox_theme_rc(dark=dark)
        assert "window.active.button.*.bg.border.color: #ffffff" in out
        assert "window.active.button.hover.bg.border.color: #ffffff" in out
    # The old dark resting border (#05080a, invisible on the near-black bar) must be gone.
    dark_out = desktop.openbox_theme_rc(dark=True)
    assert "window.active.button.*.bg.border.color: #05080a" not in dark_out


def test_theme_rc_buttons_hover_the_signature_cyan():
    # THE new prompt: "when mouse hovers over a button, highlight it with that signature
    # cyan'ish color to help indicate what is about to be pressed." The button HOVER fill
    # (window.active.button.hover.bg.color) must be the Azzio SIGNATURE cyan -- the logo
    # cyan #06b8fd used for the active menu item, the focus border, and the media OSD accent
    # -- so hovering any min/max/close button lights it up in the one signature colour. This
    # holds in BOTH the dark (default) and light themes so the highlight is identical.
    SIGNATURE_CYAN = "#06b8fd"
    for dark in (True, False):
        out = desktop.openbox_theme_rc(dark=dark)
        assert f"window.active.button.hover.bg.color: {SIGNATURE_CYAN}" in out
    # The previous, dimmer hover fills (dark #0499d6, light #8caede) must be gone -- they were
    # NOT the signature cyan, so a drift back to them would fail the "signature cyan" ask.
    dark_out = desktop.openbox_theme_rc(dark=True)
    light_out = desktop.openbox_theme_rc(dark=False)
    assert "window.active.button.hover.bg.color: #0499d6" not in dark_out
    assert "window.active.button.hover.bg.color: #8caede" not in light_out
    # The hover border stays WHITE (the earlier request); only the FILL is the signature cyan,
    # so the white outline still frames the cyan highlight.
    for out in (dark_out, light_out):
        assert "window.active.button.hover.bg.border.color: #ffffff" in out


def test_theme_rc_dests_are_user_theme_search_paths():
    # Both themes ship to ~/.themes/<name>/openbox-3/themerc -- a user theme search path
    # OpenBox scans alongside /usr/share/themes -- so naming one in rc.xml resolves it.
    assert desktop.OPENBOX_THEME_DIR == f"{desktop.HOME}/.themes/Azzio/openbox-3"
    assert desktop.OPENBOX_THEME_THEMERC == (
        f"{desktop.HOME}/.themes/Azzio/openbox-3/themerc"
    )
    assert desktop.OPENBOX_THEME_DIR_DARK == f"{desktop.HOME}/.themes/Azzio-Dark/openbox-3"
    assert desktop.OPENBOX_THEME_THEMERC_DARK == (
        f"{desktop.HOME}/.themes/Azzio-Dark/openbox-3/themerc"
    )


def test_theme_rc_entries_are_home_owned_conf():
    # Both the dark (default) and light themerc PLAN entries are home-owned 0o644 data.
    dark_entry = next(
        e for e in desktop.PLAN if e["dest"] == desktop.OPENBOX_THEME_THEMERC_DARK
    )
    assert dark_entry["builder"] is desktop.openbox_theme_rc_dark
    assert dark_entry["mode"] == 0o644 and dark_entry["owner"] == "home"
    light_entry = next(
        e for e in desktop.PLAN if e["dest"] == desktop.OPENBOX_THEME_THEMERC
    )
    assert light_entry["builder"] is desktop.openbox_theme_rc_light
    assert light_entry["mode"] == 0o644 and light_entry["owner"] == "home"


# --- OpenBox autostart: wallpaper, keyboard, xcape, menu daemon, installer ---

def test_autostart_repaints_wallpaper_with_feh():
    # The autostart repaints the SAME image ~/.xinitrc pre-painted (no flash; also
    # covers a re-login where the root pixmap was reset). feh owns the root pixmap.
    # The image follows the per-user `azzio wallpaper` pointer (default "years"), and
    # feh is backgrounded (&) so the autostart continues.
    out = desktop.openbox_autostart()
    assert 'cat "$HOME/.config/azzio/wallpaper"' in out
    assert "|| _azwp='" + desktop.WALLPAPER_IMAGE_FILE + "'" in out
    assert 'feh --no-fehbg --bg-fill "$_azwp" &' in out


def test_autostart_applies_us_and_hebrew_layouts_with_alt_shift():
    # setxkbmap sets US English (default) + Hebrew, Alt+Shift to toggle -- a plain,
    # DE-independent xkb config. Constants are pinned so a test catches a layout/toggle
    # drift.
    assert desktop.KEYBOARD_LAYOUTS == ["us", "il"]
    assert desktop.KEYBOARD_TOGGLE == "grp:alt_shift_toggle"
    out = desktop.openbox_autostart()
    assert "setxkbmap -layout 'us,il' -option 'grp:alt_shift_toggle'" in out


def test_autostart_arms_super_key_via_xcape():
    # OpenBox cannot bind a lone modifier, so xcape turns a solo Super_L tap into the
    # chord Super_L+Menu that rc.xml binds to the menu. -t 500: a tap fires on release;
    # the generous window keeps a normal (slightly lingering) Super press registering as
    # a tap instead of being silently dropped -- the old 200ms cap felt laggy/buggy. A
    # Super pressed WITH another key is still a plain modifier (xcape suppresses the tap).
    out = desktop.openbox_autostart()
    assert "xcape -t 500 -e 'Super_L=Super_L|Menu'" in out


def test_autostart_starts_the_application_menu_daemon():
    # The application-menu daemon is started (detached) so the menu is pre-built and
    # hidden -- the first Super press is then instant. It runs the INSTALLED daemon
    # BINARY (the menu is a compiled C/GTK3 program now; single source of truth in
    # application_menu.py) directly -- no `python3` interpreter in front of it.
    out = desktop.openbox_autostart()
    assert desktop.MENU_DAEMON_BIN == desktop._app_menu.MENU_DAEMON_BIN_SYSTEM_PATH
    assert f"setsid '{desktop.MENU_DAEMON_BIN}'" in out
    assert "python3" not in out                       # the daemon is a native binary now


def test_autostart_launches_the_installer_once():
    # The Calamares installer auto-opens ONCE, a couple seconds in (Manjaro-style first-run),
    # via the privileged wrapper -- the same wrapper the menu/Desktop launchers use. It must
    # name `--gui` explicitly now: azzio-install has NO default action (a bare invocation
    # only prints help), so the first-run autostart must ask for GUI mode by name.
    out = desktop.openbox_autostart()
    assert f"( sleep 2; '{desktop.INSTALL_WRAPPER_PATH}' --gui )" in out


def test_live_autostart_shows_the_security_notice():
    # The base desktop's LIVE session runs `azzio security-notice` once (it self-gates on
    # the ssh variant / a real password and self-silences after the first show). It is a
    # LIVE-only line -- the installed autostart must NOT carry it (the installed system has
    # a real user password, so the "password not configured" warning does not apply).
    live = desktop.openbox_autostart()
    assert "azzio security-notice" in live


def test_installed_autostart_has_no_security_notice_or_installer():
    # The installed autostart drops BOTH live-only lines: the first-run installer AND the
    # security notice. (It is the shared common block only.)
    installed = desktop.openbox_autostart_installed()
    assert "azzio security-notice" not in installed
    assert desktop.INSTALL_WRAPPER_PATH not in installed


def test_autostart_is_sh_script():
    # openbox-session runs it via /bin/sh; it ships executable, so a shebang is required.
    assert desktop.openbox_autostart().startswith("#!/bin/sh\n")


# --- OpenBox environment: classify the session as openbox -------------------

def test_environment_exports_openbox_desktop():
    # ~/.config/openbox/environment is sourced by openbox-session before autostart; it
    # re-asserts XDG_CURRENT_DESKTOP=openbox so the classification is correct even if
    # OpenBox is started by some path other than our startx.
    assert "export XDG_CURRENT_DESKTOP=openbox" in desktop.openbox_environment()


def test_environment_bridges_qt_apps_onto_the_gtk_system_theme():
    # QT_QPA_PLATFORMTHEME=gtk3 is the system-theme bridge for Qt apps (Calamares, any
    # downloaded Qt app): with no Qt-side theme config or portal they render LIGHT
    # regardless of the freedesktop color-scheme, so the Qt gtk3 platform theme makes them
    # follow the GTK theme (Adwaita-dark/Adwaita) `azzio theme` sets -- i.e. Qt apps obey
    # the system dark/white toggle too. Regression guard: dropping this un-themes Qt apps.
    assert "export QT_QPA_PLATFORMTHEME=gtk3" in desktop.openbox_environment()


# --- OpenBox config files land under home, correct modes --------------------

def test_openbox_config_files_are_home_owned_with_correct_modes():
    # The OpenBox files under ~/.config/openbox (plus the theme's themerc under
    # ~/.themes) are handed to the live user (home-owned; mirrored into /etc/skel).
    # rc.xml/environment/themerc are plain data (0o644); autostart is a sourced shell
    # script and must be EXECUTABLE (0o755). (menu.xml is gone -- root menu removed.)
    by_dest = {e["dest"]: e for e in desktop.PLAN}
    expected = {
        f"{desktop.HOME}/.config/openbox/rc.xml": (desktop.openbox_rc_xml, 0o644),
        desktop.OPENBOX_THEME_THEMERC_DARK: (desktop.openbox_theme_rc_dark, 0o644),
        desktop.OPENBOX_THEME_THEMERC: (desktop.openbox_theme_rc_light, 0o644),
        f"{desktop.HOME}/.config/openbox/environment": (desktop.openbox_environment, 0o644),
        f"{desktop.HOME}/.config/openbox/autostart": (desktop.openbox_autostart, 0o755),
    }
    for dest, (builder, mode) in expected.items():
        entry = by_dest[dest]
        assert entry["builder"] is builder, dest
        assert entry["mode"] == mode, dest
        assert entry["owner"] == "home", dest


# --- Menu usage seed lands under home ---------------------------------------

def test_menu_usage_seed_entry_is_home_owned_conf():
    # OUR menu's launch-frequency seed lands under ~/.local/share, is plain data
    # (0o644), and is handed to the live user (mirrored to /etc/skel). Content is owned
    # by application_menu.py; openbox.py just places it.
    entry = next(
        e for e in desktop.PLAN
        if e["dest"] == desktop._app_menu.MENU_USAGE_SEED_SYSTEM_PATH
    )
    assert entry["builder"] is desktop.az_menu_usage_seed_json
    assert entry["mode"] == 0o644
    assert entry["owner"] == "home"


def test_menu_usage_seed_content_comes_from_application_menu():
    # Single source of truth: the seed content is application_menu.usage_seed_json().
    assert desktop.az_menu_usage_seed_json() == desktop._app_menu.usage_seed_json()


# --- Autostart + menu launchers open the installer via the wrapper ----------

def test_menu_launcher_execs_the_wrapper():
    # The application-menu installer entry (re-open after close) shares the same wrapper.
    out = desktop.install_menu_desktop()
    assert out.splitlines()[0] == "[Desktop Entry]"
    assert "Exec=" + desktop.INSTALL_WRAPPER_PATH in out
    assert "Categories=System;" in out


def test_install_menu_desktop_is_system_owned_conf():
    # The application-menu installer .desktop lands system-wide in
    # /usr/share/applications (one file for all users), root-owned, plain data (0o644).
    entry = next(
        e for e in desktop.PLAN
        if e["dest"] == "/usr/share/applications/azzio-install.desktop"
    )
    assert entry["builder"] is desktop.install_menu_desktop
    assert entry["mode"] == 0o644
    assert entry["owner"] == "root"


# --- Privileged wrapper: unset before exec ----------------------------------

def test_install_wrapper_unsets_runtime_dir_before_exec():
    # sudo -E would otherwise pass main's /run/user/1000 to root; the unset must
    # come strictly before the exec that elevates.
    out = desktop.install_wrapper_sh()
    unset_idx = out.index("unset XDG_RUNTIME_DIR")
    exec_idx = out.index("exec sudo -E env QT_SCALE_FACTOR=1 calamares")
    assert unset_idx < exec_idx


def test_install_wrapper_exec_line_present():
    # The exact privileged launch: sudo -E (preserve X env) with QT_SCALE_FACTOR=1
    # re-set across the sudo boundary (see the scale test below). NO `-c /etc/calamares`:
    # that overrides the app-data dir and makes Calamares look for qml/ under
    # /etc/calamares (absent) -> fatal startup error. Calamares reads
    # /etc/calamares/settings.conf and branding by default without it.
    assert "exec sudo -E env QT_SCALE_FACTOR=1 calamares\n" in desktop.install_wrapper_sh()


def test_install_wrapper_pins_qt_scale_factor_one():
    # The installer scale fix: Calamares (Qt) inherits BOTH the session's high Xft.dpi
    # (=96*scale) AND QT_SCALE_FACTOR=<scale>, so it double-scaled (~1.82x at 1.35) and
    # came up nearly full-screen. The wrapper pins QT_SCALE_FACTOR=1 so it scales by the
    # DPI channel alone (like every other app). Because `sudo -E` would carry the
    # session's value into the root process, the factor must also be re-set ACROSS the
    # sudo boundary with `env QT_SCALE_FACTOR=1` on the exec line -- exporting it in the
    # unprivileged shell alone is not enough.
    out = desktop.install_wrapper_sh()
    # The GUI exec is now inside run_gui(); find the calamares exec line (indented).
    exec_line = next(ln.strip() for ln in out.splitlines()
                     if ln.strip().startswith("exec ") and "calamares" in ln)
    assert "env QT_SCALE_FACTOR=1 calamares" in exec_line
    # And the session's fractional QT_SCALE_FACTOR must NOT survive to the exec: no
    # `QT_SCALE_FACTOR=<fraction>` is passed through (only the pinned integer 1).
    assert "QT_SCALE_FACTOR=1.35" not in out


def test_install_wrapper_does_not_override_appdata_dir():
    # Regression: `-c /etc/calamares` is a testing-only app-data override that
    # broke QML resolution (no /etc/calamares/qml) and crashed the installer.
    # Check the actual command line (the `exec` line), not the explanatory
    # comments, which mention the flag on purpose.
    exec_line = next(
        ln.strip() for ln in desktop.install_wrapper_sh().splitlines()
        if ln.strip().startswith("exec ") and "calamares" in ln
    )
    assert "-c" not in exec_line
    assert exec_line == "exec sudo -E env QT_SCALE_FACTOR=1 calamares"


def test_install_wrapper_is_sh_script():
    assert desktop.install_wrapper_sh().startswith("#!/bin/sh\n")


# --- azzio --sshd-hypervisor guest command line interface (now pure Python) -------------------
# The `azzio` guest command line interface is a single Python module (libraries/packages/azzio);
# desktop.azzio_command_line_interface() ships it to /usr/local/bin/azzio with the country table
# re-injected from packages/calamares/locale. These tests assert on that emitted Python.

def test_azzio_cli_is_a_python_program():
    # It is Python now (no shell), so it must carry the python shebang and NOT be a
    # /bin/sh script. This is the whole point of the de-shelling.
    out = desktop.azzio_command_line_interface()
    assert out.startswith("#!/usr/bin/env python3")
    assert "#!/bin/sh" not in out


def test_azzio_subcommand_is_sshd_hypervisor():
    # The guest command line interface subcommand is --sshd-hypervisor (the binary stays `azzio`).
    # Assert the branch + usage line exist and no bare `--sshd` token survives.
    out = desktop.azzio_command_line_interface()
    assert 'cmd == "--sshd-hypervisor"' in out
    assert "--sshd-hypervisor    Install host pubkey" in out
    import re

    assert not re.search(r"--sshd(?!-hypervisor)", out)


def test_azzio_sshd_installs_pubkey_and_starts_sshd():
    # The --sshd-hypervisor path must stage the host pubkey into the target user's
    # ~/.ssh/authorized_keys, (re)generate host keys, and enable+start sshd.
    out = desktop.azzio_command_line_interface()
    assert '"authorized_keys"' in out                  # installed into ssh_dir
    assert '"ssh-keygen", "-A"' in out
    assert '"systemctl", "enable", "--now", "sshd"' in out


def test_azzio_sshd_targets_sudo_invoking_user_not_root_home():
    # The documented invocation is `sudo azzio --sshd-hypervisor`, under which $HOME=/root
    # and $USER=root. The command line interface must resolve the REAL user via SUDO_USER (fallback to the
    # current user) and look the home up in the passwd db, never off $HOME/os.environ.
    out = desktop.azzio_command_line_interface()
    assert 'os.environ.get("SUDO_USER")' in out
    assert "pwd.getpwnam(target_user).pw_dir" in out
    # It must NOT read HOME from the environment to place the login key.
    assert 'os.environ.get("HOME")' not in out
    assert 'environ["HOME"]' not in out


def test_azzio_sshd_chowns_key_to_target_user():
    # Under sudo the ~/.ssh tree is created as root; a root-owned authorized_keys
    # trips sshd StrictModes and is ignored. The install must hand ownership to the
    # target user (install -o/-g target_user for BOTH the dir and the key file).
    out = desktop.azzio_command_line_interface()
    assert '"install", "-d", "-m", "700", "-o", target_user, "-g", target_user' in out
    assert '"install", "-m", "600", "-o", target_user, "-g", target_user' in out


def test_azzio_sshd_refuses_bare_root_target():
    # If the resolved target is root (no SUDO_USER, invoked as root), there is no home
    # pubkey login for root here, so the command line interface must bail rather than stage a useless key.
    out = desktop.azzio_command_line_interface()
    assert 'if target_user == "root":' in out


def test_azzio_sshd_opens_firewall_before_starting_sshd():
    # setup-pkgs.sh sets 'ufw default deny incoming', so without an explicit allow
    # the forwarded host->guest :22 is dropped even though sshd listens. The allow must
    # come BEFORE sshd starts so the port is reachable the instant it listens. The rule is
    # 22/tcp explicitly (the user's "port 22 configured to allow tcp").
    out = desktop.azzio_command_line_interface()
    allow_idx = out.index('"ufw", "allow", "22/tcp"')
    start_idx = out.index('"systemctl", "enable", "--now", "sshd"')
    assert allow_idx < start_idx


def test_azzio_sshd_enables_sshd_even_without_the_9p_share(monkeypatch):
    # The INSTALLED ssh desktop is bare metal: there is NO 9p `shared` folder. The bring-up
    # must SKIP the host-pubkey install (best effort, hypervisor-only) and STILL enable+
    # start sshd (password login via the --ssh password baked into /etc/shadow). A
    # regression that made pubkey install fatal would leave the installed ssh desktop with
    # sshd OFF -- exactly what the user does not want.
    import types
    azcli = _load_azzio_command_line_interface()
    calls: list[tuple] = []
    monkeypatch.setattr(azcli, "_sudo", lambda *a, **k: calls.append(a) or 0)
    # No share: the mount attempt fails, so the pubkey step bows out gracefully.
    monkeypatch.setattr(azcli, "_is_mountpoint", lambda p: False)
    monkeypatch.setenv("SUDO_USER", "main")
    import pwd
    tmp = pytest.importorskip("tempfile").mkdtemp()
    monkeypatch.setattr(pwd, "getpwnam", lambda u: types.SimpleNamespace(pw_dir=tmp))
    rc = azcli.sshd_hypervisor()
    assert rc == 0
    # sshd was enabled+started and :22/tcp opened despite no pubkey being installed.
    assert ("systemctl", "enable", "--now", "sshd") in calls
    assert ("ufw", "allow", "22/tcp") in calls
    # No authorized_keys install was attempted (no share/key present).
    assert not any(a[:1] == ("install",) for a in calls), calls


def test_azzio_sshd_is_fail_fast_no_false_success(monkeypatch):
    # Behavioral regression guard (the old shell ran `set -e`): if a privileged step
    # FAILS, the command line interface must bail with that step's exit code and NEVER print the
    # "sshd enabled and started" success line. A `check=False` port that always
    # returns 0 would silently report success on a dead sshd -- exactly the bug this
    # pins. Drive the real sshd_hypervisor() with systemctl stubbed to fail (rc 5).
    import types

    azcli = _load_azzio_command_line_interface()
    printed: list[str] = []
    monkeypatch.setattr(azcli, "print",
                        lambda *a, **k: printed.append(" ".join(map(str, a)))
                        if k.get("file") is None else None, raising=False)
    monkeypatch.setattr(azcli, "_is_mountpoint", lambda p: True)
    monkeypatch.setattr(azcli, "_sudo",
                        lambda *a, **k: 5 if a[:1] == ("systemctl",) else 0)

    tmp = pytest.importorskip("tempfile").mkdtemp()
    import os
    os.makedirs(os.path.join(tmp, "Shared"), exist_ok=True)
    open(os.path.join(tmp, "Shared", "authorized_keys"), "w").write("ssh-ed25519 X\n")
    monkeypatch.setenv("SUDO_USER", "main")
    import pwd
    monkeypatch.setattr(pwd, "getpwnam",
                        lambda u: types.SimpleNamespace(pw_dir=tmp))

    rc = azcli.sshd_hypervisor()
    assert rc == 5, f"must propagate the failing step's exit code, got {rc}"
    assert not any("sshd enabled and started" in p for p in printed), printed


# --- azzio --resolve-* guest command line interface (IP geolocation, user-chosen server) ------

def test_azzio_resolve_subcommands_present_in_case_and_usage():
    # The resolvers are now POSITIONAL subcommands (azzio timedate/language --resolve).
    out = desktop.azzio_command_line_interface()
    for sub in ("timedate", "language", "gpu"):
        assert f'cmd == "{sub}"' in out              # dispatch branch
        assert (sub + " ") in out                    # usage mentions it
    # The old flag surface is gone.
    for old in ("--resolve-date-time", "--resolve-language", "--resolve-region"):
        assert old not in out


def test_azzio_resolve_offers_five_shuffled_servers():
    # The user must be presented FIVE servers, shuffled, including the two called out
    # in issue #46 (ipapi.co, ipquery.io). The prompt says 1-5. Assert against the
    # actual RESOLVER_SERVERS list the emitted command line interface defines.
    azcli = _load_azzio_command_line_interface()

    out = desktop.azzio_command_line_interface()
    labels = [s[0] for s in azcli.RESOLVER_SERVERS]
    assert "ipapi.co" in labels
    assert "ipquery.io" in labels
    assert len(azcli.RESOLVER_SERVERS) == 5, labels
    assert "random.shuffle(servers)" in out          # shuffled before display
    assert "(1-5)" in out


def test_azzio_resolve_uses_stdlib_not_curl_or_jq():
    # The Python command line interface parses JSON with the standard library (urllib + json), so it does
    # NOT shell out to curl or jq (the old shell command line interface's dependencies). This is a
    # regression guard that the de-shelling did not smuggle those back in.
    out = desktop.azzio_command_line_interface()
    assert "urllib.request" in out
    assert "json.loads" in out
    assert "command -v curl" not in out
    assert "command -v jq" not in out


def test_azzio_resolve_language_english_first_with_alt_shift():
    # The applied keyboard must put English ("us") FIRST/active and the region layout
    # SECOND, switched with Alt+Shift -- never the region layout alone.
    out = desktop.azzio_command_line_interface()
    assert 'xkb_layout = f"us,{layout}"' in out
    assert "grp:alt_shift_toggle" in out
    # English-speaking regions get a lone "us" layout (English only).
    assert 'xkb_layout = "us"' in out


def test_azzio_resolve_language_keeps_lang_english():
    # Matching the installer: the display language stays English (LANG=en_US) and only
    # the region FORMAT locale (LC_*) follows the country. The LC_* keys are written in
    # a loop over the format categories.
    out = desktop.azzio_command_line_interface()
    assert "LANG=en_US.UTF-8" in out
    assert '"LC_NUMERIC", "LC_TIME", "LC_MONETARY", "LC_PAPER", "LC_MEASUREMENT"' in out


def test_resolve_region_flag_removed():
    # --resolve-region (do timezone AND language in one query) was intentionally dropped when
    # the resolvers became positional (azzio timedate/language --resolve). A user runs both
    # commands, picking a server each time. Pin that the flag is gone.
    azcli = _load_azzio_command_line_interface()
    assert azcli.main(["--resolve-region"]) == 2


def test_azzio_timedate_resolve_sets_timezone_only():
    azcli = _load_azzio_command_line_interface()
    calls = []
    orig_resolve = azcli.resolve_via_server
    orig_tz = azcli.apply_timezone
    orig_lang = azcli.apply_language
    try:
        azcli.resolve_via_server = lambda choice=None: ("SV", "America/El_Salvador")
        azcli.apply_timezone = lambda tz: calls.append(("tz", tz)) or 0
        azcli.apply_language = lambda cc: calls.append(("lang", cc)) or 0
        rc = azcli.main(["timedate", "--resolve"])
    finally:
        azcli.resolve_via_server = orig_resolve
        azcli.apply_timezone = orig_tz
        azcli.apply_language = orig_lang
    assert rc == 0
    assert ("tz", "America/El_Salvador") in calls
    assert not any(k == "lang" for k, _ in calls)


# --- ipwho.is timezone-shape regression (issue: dict timezone) --------------

def test_ipwho_is_timezone_path_digs_the_id_field():
    # ipwho.is returns `timezone` as a nested OBJECT ({"id": "Asia/Jerusalem", ...}),
    # not a bare string like the other four servers. The dotted path MUST be
    # "timezone.id" so _dig extracts the IANA zone; a bare "timezone" path yields the
    # whole dict and apply_timezone then fails with "unknown timezone {...}".
    azcli = _load_azzio_command_line_interface()
    by_label = {s[0]: s for s in azcli.RESOLVER_SERVERS}
    assert by_label["ipwho.is"][3] == "timezone.id", by_label["ipwho.is"]
    # The other servers return a flat timezone string, so their path stays bare.
    for label in ("ipquery.io", "ip-api.com", "ipinfo.io"):
        assert by_label[label][3].split(".")[-1] == "timezone", by_label[label]


def test_resolve_via_server_flattens_ipwho_is_dict_timezone(monkeypatch):
    # End-to-end guard on the real resolve_via_server: given ipwho.is's dict-timezone
    # payload, it must return the FLAT string "Asia/Jerusalem", never the dict. Pin the
    # server (choice=5 -> ipwho.is in the fixed order) and stub the HTTP fetch.
    azcli = _load_azzio_command_line_interface()
    payload = {
        "country_code": "IL",
        "timezone": {"id": "Asia/Jerusalem", "abbr": "IDT", "is_dst": True,
                     "offset": 10800, "utc": "+03:00"},
    }

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return _json.dumps(payload).encode("utf-8")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    ipwho_index = [s[0] for s in azcli.RESOLVER_SERVERS].index("ipwho.is") + 1
    result = azcli.resolve_via_server(choice=str(ipwho_index))
    assert result == ("IL", "Asia/Jerusalem"), result


# --- non-interactive server pick (needed for the TUI capture overlay) -------

def test_resolve_via_server_choice_arg_skips_the_stdin_prompt(monkeypatch):
    # The C terminal UI runs the resolver with stdin from /dev/null and its output
    # captured, so an interactive "Server number:" prompt can never be answered there.
    # resolve_via_server(choice="N") must select the Nth server WITHOUT reading stdin
    # and WITHOUT shuffling (fixed order == RESOLVER_SERVERS), so the TUI can pass the
    # number the user typed into the in-UI prompt.
    azcli = _load_azzio_command_line_interface()

    def _boom():
        raise AssertionError("resolve_via_server must not read stdin when choice is given")

    monkeypatch.setattr("builtins.input", _boom)
    queried = {}

    class _Resp:
        def __init__(self, label): self.label = label
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return _json.dumps({"country_code": "US", "timezone": "America/New_York"}).encode()

    import urllib.request

    def _fake_urlopen(url, *a, **k):
        # Record which server URL was hit so we can prove choice maps to fixed order.
        queried["url"] = url
        return _Resp("x")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    # choice="1" must always hit RESOLVER_SERVERS[0]'s url (fixed, unshuffled).
    azcli.resolve_via_server(choice="1")
    assert queried["url"] == azcli.RESOLVER_SERVERS[0][1]


def test_resolve_via_server_rejects_out_of_range_choice(monkeypatch):
    azcli = _load_azzio_command_line_interface()
    monkeypatch.setattr("builtins.input",
                        lambda *a: (_ for _ in ()).throw(AssertionError("no stdin")))
    assert azcli.resolve_via_server(choice="9") is None
    assert azcli.resolve_via_server(choice="0") is None
    assert azcli.resolve_via_server(choice="x") is None


def test_timedate_resolve_passes_server_flag_through(monkeypatch):
    # `azzio timedate --resolve --server 3` must forward "3" to resolve_via_server as the
    # non-interactive choice (this is exactly what the TUI row runs).
    azcli = _load_azzio_command_line_interface()
    seen = {}
    monkeypatch.setattr(azcli, "resolve_via_server",
                        lambda choice=None: seen.__setitem__("choice", choice)
                        or ("US", "America/New_York"))
    monkeypatch.setattr(azcli, "apply_timezone", lambda tz: 0)
    rc = azcli.main(["timedate", "--resolve", "--server", "3"])
    assert rc == 0
    assert seen["choice"] == "3"


def test_language_resolve_passes_server_flag_through(monkeypatch):
    azcli = _load_azzio_command_line_interface()
    seen = {}
    monkeypatch.setattr(azcli, "resolve_via_server",
                        lambda choice=None: seen.__setitem__("choice", choice)
                        or ("US", "America/New_York"))
    monkeypatch.setattr(azcli, "apply_language", lambda cc: 0)
    rc = azcli.main(["language", "--resolve", "--server", "2"])
    assert rc == 0
    assert seen["choice"] == "2"


def test_azzio_resolve_embeds_country_table_from_locale():
    # The country->layout table is the single source of truth in packages/calamares/locale;
    # the emitted command line interface's COUNTRY_TABLE must equal exactly that data. Build the emitted
    # module and compare its table to locale.RESOLVER_COUNTRY_TABLE.
    from packages.calamares import locale
    azcli = _load_azzio_command_line_interface()

    expected = {cc: (loc, lay, km, 1 if en else 0)
                for cc, (loc, lay, km, en) in locale.RESOLVER_COUNTRY_TABLE.items()}
    # The emitted command line interface re-injects the table; the on-disk command_line_interface.py already carries it, so
    # asserting the imported module's table matches locale is the strongest check.
    assert azcli.COUNTRY_TABLE == expected
    # And the emitted text really contains the regenerated literal (not a stale copy).
    out = desktop.azzio_command_line_interface()
    assert "'IL': ('he_IL.UTF-8', 'il', 'il', 0)" in out
    assert "'SV': ('es_SV.UTF-8', 'latam', 'la-latin1', 0)" in out
    assert "'US': ('en_US.UTF-8', 'us', 'us', 1)" in out       # English-speaking flag
    assert "'SA': ('ar_SA.UTF-8', 'ara', 'us', 0)" in out


def test_azzio_cli_is_valid_python_and_in_sync():
    # The whole emitted command line interface (source file with the re-injected table) must be valid
    # Python, and importing/executing it must reproduce the in-sync COUNTRY_TABLE.
    import ast

    from packages.calamares import locale

    out = desktop.azzio_command_line_interface()
    # No f-string/template artefacts leaked from the injection.
    assert "AZZIO_CC_TABLE_START" in out and "AZZIO_CC_TABLE_END" in out
    ast.parse(out)
    ns: dict = {}
    exec(compile(out, "azzio_command_line_interface", "exec"), ns)
    expected = {cc: (loc, lay, km, 1 if en else 0)
                for cc, (loc, lay, km, en) in locale.RESOLVER_COUNTRY_TABLE.items()}
    assert ns["COUNTRY_TABLE"] == expected


# --- bash_profile tty1 guard ------------------------------------------------

def test_bash_profile_guard_keys_off_tty():
    # The autostart guard keys off the controlling terminal (/dev/tty1), NOT
    # $XDG_VTNR, because on a bare agetty autologin XDG_VTNR can be empty.
    out = desktop.bash_profile_startx()
    assert '[[ -z $DISPLAY && "$(tty)" == /dev/tty1 ]]' in out
    assert "exec startx" in out


def test_bash_profile_guard_line_does_not_reference_xdg_vtnr():
    # SOURCE TRUTH: the returned content DOES mention $XDG_VTNR -- but only in an
    # explanatory comment. The actual `if` guard line must not reference it (that
    # was the whole point of keying off tty). Assert the guard line specifically,
    # not the whole string.
    out = desktop.bash_profile_startx()
    guard_lines = [
        line for line in out.splitlines() if line.strip().startswith("if [[")
    ]
    assert guard_lines, "no guard line found"
    for line in guard_lines:
        assert "XDG_VTNR" not in line


def test_bash_profile_sources_bashrc():
    assert "[[ -f ~/.bashrc ]] && . ~/.bashrc" in desktop.bash_profile_startx()


# --- bash_profile: legacy-keyboard reset (the ssh "7;3u / 7;5u" junk fix) ----

def test_bash_profile_resets_kitty_keyboard_protocol_for_interactive_shells():
    # THE reported ssh bug: holding Ctrl/Alt over ssh spews `7;3u` / `7;5u` because the
    # terminal (kitty) keeps its progressive/CSI-u keyboard protocol enabled across ssh,
    # but the guest's plain bash cannot decode `ESC[<code>;<mod>u`, so the leaked tail
    # prints. The interactive login shell must ask the terminal for LEGACY keys: push an
    # empty kitty-keyboard flag set (ESC[>0u) and turn xterm modifyOtherKeys off (ESC[>4;0m).
    out = desktop.bash_profile_startx()
    # Gated on an interactive shell (ssh/rescue login is interactive; tty1 exec-startx's
    # before any prompt so this is a no-op there).
    assert "if [[ $- == *i* ]]; then" in out
    # The exact disable sequences, emitted only when fd 1 is a terminal.
    assert r"printf '\e[>0u\e[>4;0m'" in out
    assert "[[ -t 1 ]]" in out
    # Re-asserted every prompt so a TUI that re-enabled the protocol and died uncleanly
    # cannot leave the next prompt spewing `...u`.
    assert "_azzio_legacy_keys" in out
    assert "PROMPT_COMMAND" in out


def test_bash_profile_legacy_keys_emitted_on_interactive_login(tmp_path):
    # Behavioural: an INTERACTIVE bash sourcing the profile with a pty on stdout must emit
    # the legacy-keyboard reset bytes (ESC[>0u ESC[>4;0m). Driven through a real pty so the
    # `[[ -t 1 ]]` guard is satisfied, exactly like an ssh session. A regression that dropped
    # the reset (or mis-gated it) would emit nothing here, re-exposing the `7;3u` junk.
    import os
    import pty

    (tmp_path / "bp.sh").write_text(desktop.bash_profile_startx())
    script = f"source {tmp_path / 'bp.sh'}; exit 0"

    out = bytearray()
    pid, fd = pty.fork()
    if pid == 0:  # child: an interactive shell (no rc noise) that sources the profile
        os.execvp("bash", ["bash", "--norc", "--noprofile", "-ic", script])
    else:
        try:
            while True:
                try:
                    data = os.read(fd, 1024)
                except OSError:
                    break
                if not data:
                    break
                out += data
        finally:
            os.waitpid(pid, 0)
    assert b"\x1b[>0u\x1b[>4;0m" in bytes(out), repr(bytes(out)[:200])


def test_bash_profile_legacy_keys_not_emitted_when_non_interactive(tmp_path):
    # The reset must NOT fire for a NON-interactive shell (e.g. `hypervisor ssh -- cmd`,
    # or any piped/scripted invocation): emitting escape bytes there would corrupt captured
    # command output. Sourcing the profile in `bash -c` (non-interactive, stdout a pipe)
    # must produce ZERO escape bytes -- only the command's own output.
    import subprocess

    (tmp_path / "bp.sh").write_text(desktop.bash_profile_startx())
    r = subprocess.run(
        ["bash", "-c", f"source {tmp_path / 'bp.sh'}; printf DONE"],
        capture_output=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == b"DONE", repr(r.stdout)


# --- Branding / wrapper / wallpaper constants -------------------------------

def test_install_wrapper_path_value():
    assert desktop.INSTALL_WRAPPER_PATH == "/usr/local/bin/azzio-install"


def test_wallpaper_image_file_is_the_inner_years_png():
    # feh needs a real FILE (it cannot take a directory), so WALLPAPER_IMAGE_FILE points
    # at the "years" package's inner PNG. The KPackage-style layout is kept only so the
    # compiler.py emit paths do not change (nothing reads the package metadata at runtime).
    assert desktop.WALLPAPERS_SYSTEM_DIR == "/usr/share/wallpapers"
    assert desktop.WALLPAPER_DEFAULT_ID == "years"
    assert desktop.WALLPAPER_IMAGE_RES == "1672x941"
    assert desktop.WALLPAPER_IMAGE_FILE == (
        "/usr/share/wallpapers/years/contents/images/1672x941.png"
    )
    assert desktop.WALLPAPER_ASSET == "wallpapers/years.png"
    # The default must be one of the shipped wallpaper packages.
    assert desktop.WALLPAPER_DEFAULT_ID in [p["id"] for p in desktop.WALLPAPER_PACKAGES]


def test_wallpaper_image_file_used_in_xinitrc_and_autostart():
    # feh paints the same FILE in both the xinitrc pre-paint and the autostart repaint;
    # neither may drift, or the two paints differ and the no-flash guarantee breaks.
    assert desktop.WALLPAPER_IMAGE_FILE in desktop.xinitrc()
    assert desktop.WALLPAPER_IMAGE_FILE in desktop.openbox_autostart()


# --- Every builder returns non-empty content --------------------------------

def test_all_builders_return_nonempty_str():
    # Catches an import-time f-string ValueError or an accidental None return: each
    # builder in the plan (plus bash_profile) must yield a non-empty string.
    for entry in desktop.emit_plan():
        content = entry["builder"]()
        assert isinstance(content, str)
        assert content.strip(), entry["dest"]


# --- wallpaper packages (years + decades) -----------------------------------

import json as _json


def test_two_wallpaper_packages_named_years_and_decades():
    ids = [p["id"] for p in desktop.WALLPAPER_PACKAGES]
    assert ids == ["years", "decades"]


def test_wallpaper_metadata_json_is_valid_and_named():
    # The KPackage engine is gone, so nothing reads this at runtime; it is kept only so
    # each wallpaper dir stays self-describing. It must still be valid JSON with Id/Name.
    for wp_id in ("years", "decades"):
        meta = _json.loads(desktop.wallpaper_metadata_json(wp_id))
        assert meta["KPlugin"]["Id"] == wp_id
        assert meta["KPlugin"]["Name"] == wp_id      # grid label
        assert meta["KPlugin"]["Authors"]            # non-empty


def test_wallpaper_package_assets_exist():
    import paths
    for pkg in desktop.WALLPAPER_PACKAGES:
        assert (paths.ASSETSDIR / pkg["asset"]).exists(), pkg["asset"]
