# Azzio window switcher (C/GTK3) build. Produces azzio-window-switcher-daemon.
# Reuses four application-menu translation units (the shared window resolver, the app
# scan, the icon resolver, the theme palette) by compiling them from ../application_menu
# with -I so we neither fork nor copy them. build_daemon() (window_switcher.py) stages
# BOTH package dirs into its scratch tree before running `make`.
CC      ?= gcc
APP_DIR  = ../application_menu
PKGS     = gtk+-3.0 gdk-x11-3.0 x11 xcomposite xrender xdamage glib-2.0 \
           gdk-pixbuf-2.0 pango pangocairo cairo cairo-xlib
CFLAGS  += -O2 -Wall -Wextra -std=c11 -Wno-deprecated-declarations -I$(APP_DIR) \
           $(shell pkg-config --cflags $(PKGS))
LDFLAGS += $(shell pkg-config --libs $(PKGS)) -lm

OBJS = switcher.o windows.o thumbnail.o layout.o ordering.o switch_logic.o \
       window_resolve.o applications.o icons.o theme.o
BIN  = azzio-window-switcher-daemon
TESTS_DIR = ../../../tests

$(BIN): $(OBJS)
	$(CC) $(OBJS) -o $@ $(LDFLAGS)

# Our own sources.
%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

# Reused application-menu sources (compiled here without copying them into this dir).
window_resolve.o: $(APP_DIR)/window_resolve.c
	$(CC) $(CFLAGS) -c $< -o $@
applications.o: $(APP_DIR)/applications.c
	$(CC) $(CFLAGS) -c $< -o $@
icons.o: $(APP_DIR)/icons.c
	$(CC) $(CFLAGS) -c $< -o $@
theme.o: $(APP_DIR)/theme.c
	$(CC) $(CFLAGS) -c $< -o $@

# `make test` delegates to the repo-root tests dir (the single home for the C tests),
# same as the application-menu Makefile.
test:
	$(MAKE) -C $(TESTS_DIR) test

clean:
	rm -f $(OBJS) $(BIN)

.PHONY: clean test
