/* Azzio window switcher -- enumerate managed windows and resolve their identity.
 *
 * Reuses the application-menu machinery: xprop for _NET_CLIENT_LIST + per-window
 * WM_CLASS/PID/type/name, az_scan_applications() for the .desktop set, and the shared
 * window_resolve.c index (WM_CLASS/PID -> .desktop id). Produces a list already sorted by
 * ordering.c so the switcher just renders it left to right.
 */
#ifndef AZ_WINDOWS_H
#define AZ_WINDOWS_H

#include <glib.h>
#include "ordering.h"        /* AzWinIdent */

/* Enumerate managed top-level windows (skipping DOCK/DESKTOP/TOOLTIP/... and our own
 * pid), resolve each to an AzWinIdent (display_name + icon_name filled from the matched
 * .desktop, title from _NET_WM_NAME), and return a GPtrArray<AzWinIdent*> already sorted
 * by az_order_sort(). Caller frees with az_windows_free(). */
GPtrArray *az_windows_list(void);
void       az_windows_free(GPtrArray *windows);

#endif /* AZ_WINDOWS_H */
