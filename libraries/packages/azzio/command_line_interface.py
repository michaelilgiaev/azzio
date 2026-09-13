#!/usr/bin/env python3
"""azzio guest command line interface -- top-level dispatch (usage + main).

This is the LAST module bundled into /usr/local/bin/azzio. It ties the pieces together:
the `theme` positional subcommand (theme.cmd_theme), the `timedate`/`language` positional geolocators (resolver.*) and the `gpu` command (gpu.py),
and `--sshd-hypervisor` (sshd.sshd_hypervisor). See common.py for the bundle
mechanism; every name referenced below is defined in an earlier bundled module.
"""

from __future__ import annotations

# BUNDLE_START


def _resolve_server_choice(args: list[str]) -> str | None:
    """Pull an optional `--server N` (N in 1-5) out of a --resolve arg list, so the
    server can be chosen non-interactively (the C terminal UI passes the number the user
    typed into its in-UI prompt, because its captured/`/dev/null`-stdin overlay cannot
    host the interactive picker). Returns the digit string, or None for the interactive
    shuffle+prompt path. `--server` with no/blank value falls back to interactive."""
    for i, a in enumerate(args):
        if a == "--server":
            return args[i + 1] if i + 1 < len(args) else None
        if a.startswith("--server="):
            return a.split("=", 1)[1] or None
    return None


def cmd_timedate(args: list[str]) -> int:
    """`azzio timedate [--resolve [--server N]]`. --resolve geolocates (user picks 1 of 5
    shuffled servers, or a fixed server via --server N) and sets the system timezone; no arg
    prints the current zone. The ONLY timedate path that touches the network -- everything
    else is static/user-chosen."""
    opt = args[0] if args else ""
    if opt in ("--help", "-h", "help"):
        print("Usage: azzio timedate [--resolve [--server N]]\n\n"
              "  --resolve    Geolocate by IP (pick a server) and set the timezone.\n"
              "  --server N   With --resolve: use server N (1-5) without prompting.\n"
              "  (no option)  Print the current system timezone.")
        return 0
    if opt == "--resolve":
        result = resolve_via_server(_resolve_server_choice(args))
        if result is None:
            return 1
        country, tz = result
        print(f"Resolved: country={country} timezone={tz}")
        return apply_timezone(tz)
    if opt == "":
        # Current zone: prefer timedatectl; fall back to the /etc/localtime symlink target.
        if _have("timedatectl"):
            subprocess.run(["timedatectl", "show", "-p", "Timezone", "--value"], check=False)
        else:
            try:
                print(os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[-1])
            except OSError:
                print("unknown")
        return 0
    _err(f"azzio timedate: unknown option: {opt}")
    return 2


def cmd_language(args: list[str]) -> int:
    """`azzio language [--resolve [--server N]]`. --resolve geolocates (user picks 1 of 5
    shuffled servers, or a fixed server via --server N) and sets English + the region's
    language/keyboard as a switchable second layout (English only for English-speaking
    countries); no arg prints the current LANG + layout."""
    opt = args[0] if args else ""
    if opt in ("--help", "-h", "help"):
        print("Usage: azzio language [--resolve [--server N]]\n\n"
              "  --resolve    Geolocate by IP (pick a server) and set English + the region "
              "language.\n"
              "  --server N   With --resolve: use server N (1-5) without prompting.\n"
              "  (no option)  Print the current language and keyboard layout.")
        return 0
    if opt == "--resolve":
        result = resolve_via_server(_resolve_server_choice(args))
        if result is None:
            return 1
        country, _tz = result
        print(f"Resolved: country={country}")
        return apply_language(country)
    if opt == "":
        print("LANG=" + os.environ.get("LANG", "en_US.UTF-8"))
        if _have("setxkbmap"):
            subprocess.run(["setxkbmap", "-query"], check=False)
        return 0
    _err(f"azzio language: unknown option: {opt}")
    return 2


def usage() -> None:
    print(
        "Usage: azzio [command]\n"
        "\n"
        "With no command, azzio opens a simple full-screen UI to configure the\n"
        "Theme, Wallpaper, and Network (arrow keys to move, Enter to select, / to\n"
        "search, Esc to go back).\n"
        "\n"
        "Commands:\n"
        "  theme [--dark|--white]  Set the system colour theme (dark is the default);\n"
        "                          no option prints the current theme. See "
        "`azzio theme --help`\n"
        "  wallpaper [--years.png|--decades.png]  Set the desktop wallpaper; no option\n"
        "                          prints the current one. See `azzio wallpaper --help`\n"
        "  network <wifi|wired|bluetooth|airplane|firewall|ip|status>  Everything\n"
        "                          network related; no option prints an overview. See "
        "`azzio network --help`\n"
        "  volume <up|down|mute|get>  Change the volume in 7.5% steps (centered cyan bar).\n"
        "                          See `azzio volume --help`\n"
        "  brightness <up|down|get>  Change screen brightness in 7.5% steps (laptops only).\n"
        "                          See `azzio brightness --help`\n"
        "  machine [--pc|--laptop|--auto]  Show / hard-set the machine type (PC or Laptop);\n"
        "                          no option prints it. See `azzio machine --help`\n"
        "  default-applications <list|get|set|...>  List / change the XDG default apps (which\n"
        "                          app opens which file type). See "
        "`azzio default-applications --help`\n"
        "  display <info|scale|resolution|...>  Configure the display (xrandr) and the global\n"
        "                          UI scale. See `azzio display --help`\n"
        "  backup --configure|-c [--status|--disable]  Opt in to USB / Google Drive copy\n"
        "                          targets for the backup command (off by default). See "
        "`azzio backup --help`\n"
        "  power <shutdown|restart|sleep|lock>  Power/session control with optional "
        "timers\n"
        "                          (--in D, --at T, --status, --cancel). Also as "
        "`azzio shutdown` etc.\n"
        "  --sshd-hypervisor    Install host pubkey from ~/Shared/authorized_keys "
        "and start sshd\n"
        "  mkazzioiso --ssh=\"<PASSWORD>\" [--out DIR]  Build the azzio-sshd ISO FROM the\n"
        "                          RUNNING system (captures packages installed while live);\n"
        "                          --ssh sets the `main` login password. See "
        "`azzio mkazzioiso --help`\n"
        "  gpu [--resolve|--list]  Detect the GPU and resolve its drivers from the baked-in\n"
        "                          offline repo (developer drivers included). See "
        "`azzio gpu --help`\n"
        "  timedate [--resolve]  Geolocate by IP (pick a server) and set the timezone. See\n"
        "                          `azzio timedate --help`\n"
        "  language [--resolve]  Geolocate by IP (pick a server) and set English + the region\n"
        "                          language. See `azzio language --help`"
    )


def usage_err() -> None:
    """Same as usage() but on stderr (for the unknown-command path)."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        usage()
    sys.stderr.write(buf.getvalue())


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ""

    if cmd == "theme":
        return cmd_theme(argv[1:])
    if cmd == "wallpaper":
        return cmd_wallpaper(argv[1:])
    if cmd == "network":
        return cmd_network(argv[1:])
    if cmd == "volume":
        return cmd_volume(argv[1:])
    if cmd == "brightness":
        return cmd_brightness(argv[1:])
    if cmd == "media-init":
        return cmd_media_init(argv[1:])
    if cmd == "machine":
        return cmd_machine(argv[1:])
    if cmd == "default-applications":
        return cmd_default_applications(argv[1:])
    if cmd == "display":
        return cmd_display(argv[1:])
    if cmd == "backup":
        return cmd_backup(argv[1:])
    if cmd == "gpu":
        return cmd_gpu(argv[1:])
    if cmd == "timedate":
        return cmd_timedate(argv[1:])
    if cmd == "language":
        return cmd_language(argv[1:])
    if cmd == "power":
        return cmd_power(argv[1:])
    # The power verbs are ALSO exposed at the top level for convenience (azzio shutdown,
    # azzio restart, azzio sleep, azzio lock), dispatching to the same handlers.
    if cmd == "shutdown":
        return _power_verb("shutdown", argv[1:])
    # `reboot` is an alias for `restart` (see cmd_power) -- both run the same restart action.
    if cmd in ("restart", "reboot"):
        return _power_verb("restart", argv[1:])
    if cmd == "sleep":
        return _power_verb("sleep", argv[1:])
    if cmd == "lock":
        return cmd_lock(argv[1:])
    if cmd == "--sshd-hypervisor":
        return sshd_hypervisor()
    if cmd == "security-notice":
        return cmd_security_notice(argv[1:])
    if cmd == "mkazzioiso":
        return cmd_mkazzioiso(argv[1:])
    if cmd in ("-h", "--help", "help"):
        usage()
        return 0
    if cmd == "":
        # Bare `azzio` (no arguments) opens the full-screen terminal user interface (Theme / Wallpaper /
        # Network). run_terminal_user_interface degrades to a pointer message when there is no terminal, so this
        # is safe even when stdin/stdout are not a tty.
        return run_terminal_user_interface(argv)
    _err(f"azzio: unknown command: {cmd}")
    usage_err()
    return 2


if __name__ == "__main__":
    sys.exit(main())
