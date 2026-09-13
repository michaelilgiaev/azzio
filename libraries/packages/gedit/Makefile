# Azzio gedit notepad-mode plugin -- build the libpeas C plugin .so.
#
# A libpeas/gedit plugin is a shared object loaded by gedit at runtime; it is built
# against the gedit + GTK3 + libpeas dev stack via pkg-config (the same style as the
# application-menu Makefile). compiler.py copies these sources into a scratch dir and runs
# `make libgedit-modifications.so`, then installs the result into /usr/lib/gedit/plugins/.
#
# Build host requirements (NOT shipped in the ISO -- host build-time only): gcc, pkgconf,
# and the gedit dev headers (`gedit` pkg-config module, which pulls in gtk+-3.0 and
# libpeas-1.0). The live/installed system carries only the compiled .so plus the gedit +
# GTK3 + libpeas RUNTIME libraries (already in the manifest).

PLUGIN_SO = libgedit-modifications.so

# gedit.pc transitively provides gtk+-3.0, libpeas-1.0, gio, tepl, etc. -- exactly the
# headers/libs the plugin needs. gmodule-2.0 is added explicitly for G_MODULE_EXPORT.
PKGS = gedit gmodule-2.0

CFLAGS ?= -O2 -Wall
CFLAGS += -fPIC $(shell pkg-config --cflags $(PKGS))
LDFLAGS += -shared
LDLIBS += $(shell pkg-config --libs $(PKGS))

$(PLUGIN_SO): gedit_modifications.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LDLIBS)

clean:
	rm -f $(PLUGIN_SO)

.PHONY: clean
