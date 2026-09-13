/* Azzio window switcher -- fixed leading order + alphabetical tail. See ordering.h.
 * 1 librewolf, 2 kitty, 3 hypervisor (remote-viewer), 4 the file manager, 5 rest (alpha).
 * Pure: no GTK/X, so it is unit-tested headless. */
#include "ordering.h"
#include <string.h>

static gboolean cls_is(const char *cls, const char *want) {
    return cls && g_ascii_strcasecmp(cls, want) == 0;
}
static gboolean did_is(const char *did, const char *want) {
    /* Match "librewolf" against both a bare id and a "librewolf.desktop" id. */
    if (!did) return FALSE;
    if (g_ascii_strcasecmp(did, want) == 0) return TRUE;
    char *with = g_strconcat(want, ".desktop", NULL);
    gboolean hit = (g_ascii_strcasecmp(did, with) == 0);
    g_free(with);
    return hit;
}

int az_order_rank(const AzWinIdent *w) {
    if (!w) return 5;
    if (did_is(w->desktop_id, "librewolf") || cls_is(w->wm_class, "librewolf"))
        return 1;
    if (did_is(w->desktop_id, "kitty") || cls_is(w->wm_class, "kitty"))
        return 2;
    /* Hypervisor guest viewer: remote-viewer window whose title starts with
     * "hypervisor: " (set in hypervisor/virtual_machine.py), or, failing a title
     * prefix, any remote-viewer window. */
    if ((w->title && g_str_has_prefix(w->title, "hypervisor: ")) ||
        cls_is(w->wm_class, "remote-viewer"))
        return 3;
    if (did_is(w->desktop_id, "thunar") || cls_is(w->wm_class, "Thunar"))
        return 4;
    return 5;
}

int az_order_cmp(gconstpointer a, gconstpointer b) {
    const AzWinIdent *x = *(const AzWinIdent * const *)a;
    const AzWinIdent *y = *(const AzWinIdent * const *)b;
    int rx = az_order_rank(x), ry = az_order_rank(y);
    if (rx != ry) return rx - ry;
    if (rx == 5) {                       /* alphabetical tail by display name */
        const char *nx = x->display_name ? x->display_name : "";
        const char *ny = y->display_name ? y->display_name : "";
        char *cx = g_utf8_casefold(nx, -1), *cy = g_utf8_casefold(ny, -1);
        int c = g_strcmp0(cx, cy);
        g_free(cx); g_free(cy);
        if (c) return c;
    }
    if (x->stack_index != y->stack_index) return x->stack_index - y->stack_index;
    return (x->xid < y->xid) ? -1 : (x->xid > y->xid) ? 1 : 0;
}

void az_order_sort(GPtrArray *windows) {
    if (windows) g_ptr_array_sort(windows, az_order_cmp);
}
