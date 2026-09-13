/* Azzio window switcher -- ordering unit tests. Compiled against the SHIPPING
 * ordering.c (via tests/Makefile). Pure asserts, no framework; non-zero exit on
 * failure. Asserts the exact librewolf/kitty/hypervisor/file manager/alphabetical order
 * the user asked for, plus same-app grouping and the hypervisor title/WM_CLASS match. */
#include "ordering.h"
#include <glib.h>
#include <stdio.h>
#include <string.h>

static int failures = 0;
#define CHECK(cond, msg) do { \
    if (cond) { g_print("  ok   %s\n", msg); } \
    else      { g_print("  FAIL %s\n", msg); failures++; } \
} while (0)

static AzWinIdent *mk(const char *did, const char *cls, const char *title,
                      const char *name, unsigned long xid, int stack) {
    AzWinIdent *w = g_new0(AzWinIdent, 1);
    w->desktop_id   = did   ? g_strdup(did)   : NULL;
    w->wm_class     = cls   ? g_strdup(cls)   : NULL;
    w->title        = title ? g_strdup(title) : NULL;
    w->display_name = name  ? g_strdup(name)  : NULL;
    w->xid = xid; w->stack_index = stack;
    return w;
}

static void test_rank(void) {
    g_print("rank:\n");
    AzWinIdent *lw = mk("librewolf", "librewolf", "X", "LibreWolf", 1, 0);
    AzWinIdent *kt = mk("kitty", "kitty", "X", "kitty", 2, 1);
    AzWinIdent *hv = mk(NULL, "remote-viewer", "hypervisor: win11", "Remote Viewer", 3, 2);
    AzWinIdent *th = mk("thunar", "Thunar", "home", "Thunar", 4, 3);
    AzWinIdent *gp = mk("gimp", "Gimp", "X", "GNU Image Manipulation Program", 5, 4);
    CHECK(az_order_rank(lw) == 1, "librewolf -> 1");
    CHECK(az_order_rank(kt) == 2, "kitty -> 2");
    CHECK(az_order_rank(hv) == 3, "remote-viewer title 'hypervisor:' -> 3");
    CHECK(az_order_rank(th) == 4, "thunar -> 4");
    CHECK(az_order_rank(gp) == 5, "other -> 5");
    g_free(lw); g_free(kt); g_free(hv); g_free(th); g_free(gp);
}

static void test_hypervisor_by_wmclass_only(void) {
    g_print("hypervisor fallback:\n");
    AzWinIdent *hv2 = mk(NULL, "remote-viewer", "Some VM", "Remote Viewer", 9, 9);
    CHECK(az_order_rank(hv2) == 3, "remote-viewer WM_CLASS (no title prefix) -> 3");
    g_free(hv2);
}

static void test_full_sort(void) {
    g_print("full sort:\n");
    GPtrArray *a = g_ptr_array_new_with_free_func(g_free);
    g_ptr_array_add(a, mk("gimp", "Gimp", "x", "GNU Image Manipulation Program", 50, 5));
    g_ptr_array_add(a, mk("thunar", "Thunar", "x", "Thunar", 40, 4));
    g_ptr_array_add(a, mk(NULL, "remote-viewer", "hypervisor: win11", "Remote Viewer", 30, 3));
    g_ptr_array_add(a, mk("kitty", "kitty", "x", "kitty", 20, 1));
    g_ptr_array_add(a, mk("librewolf", "librewolf", "x", "LibreWolf", 10, 0));
    g_ptr_array_add(a, mk("vlc", "vlc", "x", "VLC media player", 60, 6));
    az_order_sort(a);
    /* Expected display names in order (rank-3 hypervisor has no distinct display
     * name in this fixture, so we assert its WM_CLASS for that slot). */
    const char *want[] = { "LibreWolf", "kitty", "remote-viewer", "Thunar",
                           "GNU Image Manipulation Program", "VLC media player" };
    for (guint i = 0; i < a->len; i++) {
        AzWinIdent *w = g_ptr_array_index(a, i);
        const char *got = (az_order_rank(w) == 3) ? w->wm_class : w->display_name;
        CHECK(strcmp(got, want[i]) == 0, want[i]);
    }
    g_ptr_array_free(a, TRUE);
}

static void test_same_app_stable(void) {
    g_print("same-app stable:\n");
    GPtrArray *a = g_ptr_array_new_with_free_func(g_free);
    g_ptr_array_add(a, mk("kitty", "kitty", "b", "kitty", 22, 2));
    g_ptr_array_add(a, mk("kitty", "kitty", "a", "kitty", 21, 1));
    az_order_sort(a);
    AzWinIdent *first = g_ptr_array_index(a, 0);
    CHECK(first->stack_index == 1, "lower stack_index comes first within kitty slot");
    g_ptr_array_free(a, TRUE);
}

int main(void) {
    test_rank();
    test_hypervisor_by_wmclass_only();
    test_full_sort();
    test_same_app_stable();
    if (failures) { g_printerr("%d failure(s)\n", failures); return 1; }
    g_print("all ordering tests passed\n");
    return 0;
}
