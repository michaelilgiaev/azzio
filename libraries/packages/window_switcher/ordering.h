/* Azzio window switcher -- fixed leading order + alphabetical tail.
 *
 * The user's spec: alt-tab shows one tile per window, left to right, ordered
 *   1 librewolf, 2 kitty, 3 the hypervisor display, 4 the file manager,
 *   5 everything else, alphabetical.
 * This module is the pure comparator that realizes that order. No GTK/X, so it is
 * unit-tested headless (tests/test_ordering.c).
 */
#ifndef AZ_ORDERING_H
#define AZ_ORDERING_H

#include <glib.h>

/* A window's identity, filled by windows.c and consumed here + by layout.c. */
typedef struct {
    char *desktop_id;   /* resolved .desktop id, e.g. "librewolf" (may be NULL) */
    char *wm_class;     /* primary WM_CLASS, e.g. "remote-viewer" (may be NULL) */
    char *title;        /* window title, e.g. "hypervisor: win11" (may be NULL) */
    char *display_name; /* human name for the alphabetical tail (may be NULL) */
    char *icon_name;    /* Icon= from the .desktop, for layout.c's badge/fallback (may be NULL) */
    unsigned long xid;  /* X window id, stable tiebreak within a slot */
    int stack_index;    /* position in _NET_CLIENT_LIST, stable tiebreak */
} AzWinIdent;

/* Leading rank: 1 librewolf, 2 kitty, 3 hypervisor, 4 the file manager, 5 everything else. */
int az_order_rank(const AzWinIdent *w);

/* g_ptr_array_sort comparator over AzWinIdent*: rank asc, then (rank 5) display_name
 * casefold asc, then stack_index asc, then xid asc. */
int az_order_cmp(gconstpointer a, gconstpointer b);

/* Sort in place: GPtrArray<AzWinIdent*>. */
void az_order_sort(GPtrArray *windows);

#endif /* AZ_ORDERING_H */
