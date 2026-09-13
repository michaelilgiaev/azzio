# Azzio -- C test runner for the compiled C packages.
#
# The C sources live in libraries/packages/{application_menu,azzio}/ (the bare-`azzio` terminal user interface
# is C inside the azzio package now); this dir is the single home for their tests (the Python
# pytest suite lives here too, run by `bash tests.sh`).
#
#   make -C tests test      headless C unit tests -- the application-menu (test_applications.c
#                           + the shipping applications.c) AND the bare-`azzio` terminal user interface model
#                           (test_terminal_user_interface_model.c + the shipping model.c). No GTK/X/kitty needed.
#                           This is what the top-level `make test` and the package Makefiles'
#                           `test` targets delegate to.
#
# APP_DIR / TERMINAL_USER_INTERFACE_DIR point back at the package sources so the tests can compile the shipping
# translation units and resolve their headers. Nothing here dirties the package trees.
CC      ?= gcc
APP_DIR  = ../libraries/packages/application_menu
TERMINAL_USER_INTERFACE_DIR  = ../libraries/packages/azzio

TEST_BIN    = test_applications
TEST_PKGS   = glib-2.0
TEST_CFLAGS = -O2 -Wall -Wextra -std=c11 -I$(APP_DIR) $(shell pkg-config --cflags $(TEST_PKGS))
TEST_LIBS   = $(shell pkg-config --libs $(TEST_PKGS))

# The terminal user interface model test: pure libc, no pkg-config libs (the model.c under test forks tools at
# RUNTIME for status, but the tested paths -- the tree, the filter, the wallpaper path --
# never do). _POSIX_C_SOURCE matches model.c's own feature-test level.
TERMINAL_USER_INTERFACE_TEST_BIN    = test_terminal_user_interface_model
TERMINAL_USER_INTERFACE_TEST_CFLAGS = -O2 -Wall -Wextra -std=c11 -D_POSIX_C_SOURCE=200809L -I$(TERMINAL_USER_INTERFACE_DIR)

# The ACTION-layer test: exercises the SHIPPING action.c (capture + sudo credential). It forks
# real subprocesses (sh/timeout) but never authenticates sudo (it forces the failing path), so
# it stays deterministic and needs no privileges.
ACT_TEST_BIN    = test_terminal_user_interface_action

$(TEST_BIN): test_applications.c $(APP_DIR)/applications.c $(APP_DIR)/applications.h
	$(CC) $(TEST_CFLAGS) test_applications.c $(APP_DIR)/applications.c -o $@ $(TEST_LIBS)

# model_tree.c's az_screen_find() delegates the "defaultapps." prefix to az_da_screen(), which
# lives in model_default_applications.c -- so that TU MUST be linked in too or the test fails to
# link (undefined reference to az_da_screen). model_display.c holds the az_status_display* probes
# the SCREENS[] table references, so it MUST be linked in as well (else undefined references).
$(TERMINAL_USER_INTERFACE_TEST_BIN): test_terminal_user_interface_model.c $(TERMINAL_USER_INTERFACE_DIR)/model.c $(TERMINAL_USER_INTERFACE_DIR)/model_tree.c $(TERMINAL_USER_INTERFACE_DIR)/model_default_applications.c $(TERMINAL_USER_INTERFACE_DIR)/model_display.c $(TERMINAL_USER_INTERFACE_DIR)/terminal_user_interface.h
	$(CC) $(TERMINAL_USER_INTERFACE_TEST_CFLAGS) test_terminal_user_interface_model.c $(TERMINAL_USER_INTERFACE_DIR)/model.c $(TERMINAL_USER_INTERFACE_DIR)/model_tree.c $(TERMINAL_USER_INTERFACE_DIR)/model_default_applications.c $(TERMINAL_USER_INTERFACE_DIR)/model_display.c -o $@

$(ACT_TEST_BIN): test_terminal_user_interface_action.c $(TERMINAL_USER_INTERFACE_DIR)/action.c $(TERMINAL_USER_INTERFACE_DIR)/action.h
	$(CC) $(TERMINAL_USER_INTERFACE_TEST_CFLAGS) test_terminal_user_interface_action.c $(TERMINAL_USER_INTERFACE_DIR)/action.c -o $@

# The SHARED window->desktop resolver (window_resolve.c), used by BOTH the application-menu
# window watcher and the window-switcher. Linked against applications.c for az_app_entry_free
# and the AzAppEntry the index is built from.
WINDOW_RESOLVE_TEST_BIN = test_window_resolve
$(WINDOW_RESOLVE_TEST_BIN): test_window_resolve.c $(APP_DIR)/window_resolve.c $(APP_DIR)/applications.c $(APP_DIR)/window_resolve.h $(APP_DIR)/applications.h
	$(CC) $(TEST_CFLAGS) test_window_resolve.c $(APP_DIR)/window_resolve.c $(APP_DIR)/applications.c -o $@ $(TEST_LIBS)

# The window-switcher ordering comparator (ordering.c) -- fixed leading order + alphabetical
# tail. Pure (no GTK/X), so it is unit-tested headless like the rest.
SW_DIR = ../libraries/packages/window_switcher
ORDER_TEST_BIN = test_ordering
$(ORDER_TEST_BIN): test_ordering.c $(SW_DIR)/ordering.c $(SW_DIR)/ordering.h
	$(CC) $(TEST_CFLAGS) -I$(SW_DIR) test_ordering.c $(SW_DIR)/ordering.c -o $@ $(TEST_LIBS)

# The window-switcher SELECTION LOGIC (switch_logic.c) -- the pure keyval+state -> direction
# decision and the focus-anchored fresh-open start index. These are the two decisions the two
# reported bugs lived in (shift+tab backward routing; wrong start index), factored out of the
# GTK handler so they are unit-tested headless. switch_logic.c itself pulls NO GDK headers (it
# mirrors the few GDK_KEY_*/GDK_SHIFT_MASK values it needs as local constants, pinned by a
# _Static_assert in switcher.c); the TEST includes <gdk/gdk.h> to drive it with the real GDK
# keysyms, which are header-only, so we add the GTK3 cflags for that header but link glib only.
SW_LOGIC_TEST_BIN = test_switch_logic
SW_LOGIC_CFLAGS   = $(TEST_CFLAGS) -I$(SW_DIR) $(shell pkg-config --cflags gtk+-3.0)
$(SW_LOGIC_TEST_BIN): test_switch_logic.c $(SW_DIR)/switch_logic.c $(SW_DIR)/switch_logic.h
	$(CC) $(SW_LOGIC_CFLAGS) test_switch_logic.c $(SW_DIR)/switch_logic.c -o $@ $(TEST_LIBS)

test: $(TEST_BIN) $(TERMINAL_USER_INTERFACE_TEST_BIN) $(ACT_TEST_BIN) $(WINDOW_RESOLVE_TEST_BIN) $(ORDER_TEST_BIN) $(SW_LOGIC_TEST_BIN)
	./$(TEST_BIN)
	./$(TERMINAL_USER_INTERFACE_TEST_BIN)
	./$(ACT_TEST_BIN)
	./$(WINDOW_RESOLVE_TEST_BIN)
	./$(ORDER_TEST_BIN)
	./$(SW_LOGIC_TEST_BIN)

clean:
	rm -f $(TEST_BIN) $(TERMINAL_USER_INTERFACE_TEST_BIN) $(ACT_TEST_BIN) $(WINDOW_RESOLVE_TEST_BIN) $(ORDER_TEST_BIN) $(SW_LOGIC_TEST_BIN)

.PHONY: test clean
