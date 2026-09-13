"""window_switcher - the Azzio alt-tab window switcher (OUR switcher, Windows-style).

The desktop is OpenBox with no panel; OpenBox's built-in NextWindow switcher is a
vertical icon-only list the user disliked. This package replaces it with a horizontal,
Windows-like overlay: one tile per window (left to right, in a fixed order -- librewolf,
kitty, the hypervisor display, the file manager, then alphabetical), each tile a LIVE thumbnail of
what the app is rendering (via XComposite, fed by picom), the app icon badged in the
corner, the selected tile highlighted. Alt+Tab advances; releasing Alt commits.

Like the application menu, it is a C / GTK3 resident daemon: the sources live directly in
this directory (switcher.c + siblings, a Makefile), and window_switcher.py is the build
wiring that COMPILES them into azzio-window-switcher-daemon, ships the pure-Python
launcher (launcher.py, the bin entry point), and returns the emit plan. It REUSES four
application-menu translation units (window_resolve/applications/icons/theme) as build inputs.

Modules:
    window_switcher   install paths, the daemon build (build_daemon), emit_plan()
    launcher          the pure-Python bin entry point that drives the daemon (--next/--prev)

The C sources (switcher.c, windows.c, thumbnail.c, layout.c, ordering.c, ...) are build
inputs, not importable modules; the Makefile builds them into the daemon.
"""
