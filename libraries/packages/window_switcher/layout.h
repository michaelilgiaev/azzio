/* Azzio window switcher -- the horizontal tile strip (Windows-like overlay body).
 *
 * A centered dark rounded panel holding one tile per window, left to right in the order
 * ordering.c produced. Each tile is a live thumbnail (icon fallback) with the app icon
 * badged in the corner and the window title underneath; the selected tile carries a
 * cyan highlight border. Owns nothing about X input -- switcher.c drives selection.
 */
#ifndef AZ_LAYOUT_H
#define AZ_LAYOUT_H

#include <gtk/gtk.h>
#include <glib.h>

typedef struct AzStrip AzStrip;

/* Build the strip (a horizontal GtkBox) packed into `parent`. */
AzStrip *az_strip_new(GtkWidget *parent);

/* Rebuild tiles from GPtrArray<AzWinIdent*> (thumbnails captured now). Keeps the
 * selection index in range. The array is borrowed for the duration of the call.
 * If the window set is UNCHANGED (same xids, same order) this refreshes thumbnails in
 * place instead of rebuilding -- so the periodic live refresh does not flicker. */
void     az_strip_set_windows(AzStrip *s, GPtrArray *windows);

/* Stream a fresh frame into every existing tile IN PLACE (no widget rebuild). Used by the
 * live-refresh tick for smooth, flicker-free streaming of the window contents. */
void     az_strip_refresh_thumbnails(AzStrip *s);

/* Move the highlight to index i (wrapped into [0,count)). No-op when empty. */
void     az_strip_select(AzStrip *s, int i);
int      az_strip_selected(AzStrip *s);
int      az_strip_count(AzStrip *s);

/* XID of the selected tile's window, or 0 when empty. */
unsigned long az_strip_selected_xid(AzStrip *s);

/* Index of the tile whose window is `xid`, or -1 if no tile matches (including xid == 0).
 * Used to anchor the fresh-open selection on the currently-focused window. */
int      az_strip_index_of_xid(AzStrip *s, unsigned long xid);

void     az_strip_free(AzStrip *s);

#endif /* AZ_LAYOUT_H */
