# Azzio application menu (C/GTK3 port) build.
# Produces a single resident binary: azzio-application-menu-daemon.
CC      ?= gcc
PKGS     = gtk+-3.0 gdk-x11-3.0 x11 glib-2.0 gdk-pixbuf-2.0 pango pangocairo cairo
# GTK3 colour/font override + gdk_screen_width APIs are deprecated in favour of
# CSS/monitor APIs but still correct here; silence the noise, keep real warnings.
CFLAGS  += -O2 -Wall -Wextra -std=c11 -Wno-deprecated-declarations \
           $(shell pkg-config --cflags $(PKGS))
LDFLAGS += $(shell pkg-config --libs $(PKGS)) -lm

OBJS = menu.o application_list.o applications.o usage.o icons.o actions.o window_watch.o window_resolve.o scrollbar.o power.o theme.o
BIN  = azzio-application-menu-daemon

# The tests now live in the repo-root tests/ dir (their single home). The `test` target
# here just delegates there so `make test` still works from this dir; tests/Makefile
# compiles test_applications.c against this dir's applications.c. This Makefile stays the
# source of truth
# for BUILDING the daemon (build_daemon() copies only these C sources + this Makefile into
# its scratch dir and runs `make $(BIN)`), which is unaffected.
TESTS_DIR = ../../../tests

$(BIN): $(OBJS)
	$(CC) $(OBJS) -o $@ $(LDFLAGS)

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

test:
	$(MAKE) -C $(TESTS_DIR) test

clean:
	rm -f $(OBJS) $(BIN)
	$(MAKE) -C $(TESTS_DIR) clean

.PHONY: clean test
