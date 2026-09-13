/* Azzio -- shared window identity -> .desktop id resolver.
 *
 * Lifted out of window_watch.c so BOTH the application-menu window watcher and the
 * window-switcher (packages/window_switcher) resolve a live X window to the .desktop
 * app that owns it with the SAME logic (WM_CLASS, then Exec basename / id stem, then
 * the window's PID via /proc). No behaviour change from the original in-file version;
 * the public functions just gained an `az_` prefix.
 */
#ifndef AZ_WINDOW_RESOLVE_H
#define AZ_WINDOW_RESOLVE_H

#include <glib.h>
#include "applications.h"          /* AzAppEntry, az_app_entry_free */

typedef struct DesktopIndex DesktopIndex;

/* Build an identity index from the scanned .desktop entries (GPtrArray<AzAppEntry*>,
 * borrowed -- the caller keeps ownership; the index copies what it needs). */
DesktopIndex *az_index_build(GPtrArray *entries);
void          az_index_free(DesktopIndex *ix);

/* Resolve a window to its .desktop id, or NULL. `wm_classes` is a GPtrArray<char*> of
 * the window's WM_CLASS strings (instance + class); `pid` is _NET_WM_PID (0 if none).
 * The returned string is owned by the index (valid until az_index_free). */
const char   *az_index_resolve(DesktopIndex *ix, GPtrArray *wm_classes, int pid);

/* Basename of the real launched binary from an Exec argv, skipping a leading
 * env/wrapper (e.g. `env FOO=1 kitty` -> "kitty"). Newly-allocated or NULL. */
char         *az_exec_binary(char **argv);

#endif /* AZ_WINDOW_RESOLVE_H */
