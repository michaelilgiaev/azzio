"""application_menu - the Azzio application menu package (OUR menu, the whole shell).

The desktop is OpenBox with no panel, so this centered, cyan-on-dark launcher (search,
launch-frequency ordering, power actions), opened by the Super key, is the only launcher
surface. It is a C / GTK3 program: the sources live directly in this directory (menu.c +
siblings, a Makefile), and application_menu.py is the build wiring that COMPILES them into
the resident daemon binary (azzio-application-menu-daemon), ships the pure-Python launcher
(launcher.py, the bin entry point), and generates the .desktop entry.

Modules:
    application_menu        install paths, the daemon build (build_daemon), emit_plan()
    launcher                the pure-Python bin entry point that drives the daemon

The C sources (menu.c, application_list.c, window_watch.c, scrollbar.c, theme.c,
...) are build inputs, not importable modules; the Makefile builds them into the daemon.
"""
