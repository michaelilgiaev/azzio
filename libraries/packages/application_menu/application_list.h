/* Azzio application menu (C port) -- the scrollable application list.
 *
 * Port of applist.py CanvasAppList. Draws the whole list with Cairo on a
 * GtkDrawingArea inside a GtkScrolledWindow: every app is painted directly (icon
 * + name + subtitle + selection outline), filtering only changes which rows are
 * laid out -- no per-row widgets are ever created/destroyed, so it can never
 * flicker (the exact property applist.py was written to guarantee).
 *
 * Public surface used by menu.c mirrors CanvasAppList.
 */
#ifndef AZ_APPLIST_H
#define AZ_APPLIST_H

#include <gtk/gtk.h>
#include "applications.h"
#include "icons.h"

typedef struct AzAppList AzAppList;

/* on_activate is called with the AzAppEntry* the user launched. */
typedef void (*AzActivateFn)(AzAppEntry *entry, gpointer user);

AzAppList *az_applist_new(AzIcons *icons, AzActivateFn on_activate, gpointer user);
GtkWidget *az_applist_widget(AzAppList *l);   /* the scrolled container to pack */
void       az_applist_free(AzAppList *l);

/* Replace the entry set (borrowed pointers; caller keeps ownership and must
 * outlive the list). Returns TRUE iff the visible rows were rebuilt. */
gboolean az_applist_set_entries(AzAppList *l, GPtrArray *entries);
void     az_applist_apply_filter(AzAppList *l, const char *query);
void     az_applist_move_selection(AzAppList *l, int delta);
void     az_applist_activate_selected(AzAppList *l);
void     az_applist_set_selection_enabled(AzAppList *l, gboolean enabled);
void     az_applist_scroll_to_top(AzAppList *l);
int      az_applist_visible_count(AzAppList *l);

#endif /* AZ_APPLIST_H */
