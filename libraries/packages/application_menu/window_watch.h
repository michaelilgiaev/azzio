/* Azzio application menu (C port) -- system-wide "app opened" detection.
 *
 * Port of winwatch.py. Polls the X11 client list on the GTK main loop and
 * records one launch each time a new top-level application window appears,
 * mapping it back to a .desktop id, into the shared usage store. Talks to the
 * WM via xprop (no extra deps), best-effort and crash-proof.
 */
#ifndef AZ_WINWATCH_H
#define AZ_WINWATCH_H

#include <glib.h>
#include "usage.h"
#include "applications.h"

typedef struct AzWatcher AzWatcher;

/* entries_provider returns a fresh GPtrArray* of AzAppEntry* (caller frees it);
 * used to (re)build the desktop index so newly installed apps become matchable.
 * own_pid is skipped defensively. */
typedef GPtrArray *(*AzEntriesProvider)(gpointer user);

AzWatcher *az_watcher_new(AzUsage *usage, AzEntriesProvider provider,
                          gpointer user, int own_pid);
void az_watcher_start(AzWatcher *w);   /* prime seen-set, begin polling */
void az_watcher_stop(AzWatcher *w);
void az_watcher_refresh_index(AzWatcher *w);
void az_watcher_free(AzWatcher *w);

#endif /* AZ_WINWATCH_H */
