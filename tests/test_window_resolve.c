/* Azzio -- unit tests for the shared window->desktop resolver (window_resolve.c),
 * compiled against the SHIPPING applications.c + window_resolve.c. Pure asserts;
 * non-zero exit on any failure. Built + run by tests/Makefile (delegated to by
 * the top-level `make test` and the package Makefiles' `test` target). */
#include "window_resolve.h"
#include "applications.h"
#include <glib.h>
#include <stdio.h>
#include <string.h>

static int failures = 0;
#define CHECK(cond, msg) do { \
    if (cond) { g_print("  ok   %s\n", msg); } \
    else      { g_print("  FAIL %s\n", msg); failures++; } \
} while (0)

/* az_exec_binary skips `env FOO=1` wrappers and returns the real basename. */
static void test_exec_binary(void) {
    g_print("exec_binary:\n");
    char *a1[] = { (char*)"/usr/bin/kitty", NULL };
    char *b1 = az_exec_binary(a1);
    CHECK(b1 && strcmp(b1, "kitty") == 0, "plain path -> kitty");
    g_free(b1);

    char *a2[] = { (char*)"env", (char*)"FOO=1", (char*)"/usr/bin/thunar", NULL };
    char *b2 = az_exec_binary(a2);
    CHECK(b2 && strcmp(b2, "thunar") == 0, "env-wrapped -> thunar");
    g_free(b2);

    CHECK(az_exec_binary(NULL) == NULL, "NULL argv -> NULL");
}

/* A built index resolves a window's WM_CLASS to the owning .desktop id. */
static void test_index_resolve_by_wmclass(void) {
    g_print("index_resolve:\n");
    /* Hand-build a single AzAppEntry for librewolf with StartupWMClass. */
    AzAppEntry *e = g_new0(AzAppEntry, 1);
    e->name = g_strdup("LibreWolf");
    e->desktop_id = g_strdup("librewolf.desktop");
    e->startup_wmclass = g_strdup("librewolf");
    e->exec_argv = g_new0(char *, 2);
    e->exec_argv[0] = g_strdup("/usr/bin/librewolf");

    GPtrArray *entries = g_ptr_array_new_with_free_func(
        (GDestroyNotify)az_app_entry_free);
    g_ptr_array_add(entries, e);

    DesktopIndex *ix = az_index_build(entries);

    GPtrArray *cls = g_ptr_array_new_with_free_func(g_free);
    g_ptr_array_add(cls, g_strdup("librewolf"));   /* casefold-insensitive match */
    const char *did = az_index_resolve(ix, cls, 0);
    CHECK(did && strcmp(did, "librewolf.desktop") == 0,
          "WM_CLASS librewolf -> librewolf.desktop");

    GPtrArray *cls2 = g_ptr_array_new_with_free_func(g_free);
    g_ptr_array_add(cls2, g_strdup("Nonexistent"));
    CHECK(az_index_resolve(ix, cls2, 0) == NULL, "unknown WM_CLASS -> NULL");

    g_ptr_array_free(cls, TRUE);
    g_ptr_array_free(cls2, TRUE);
    az_index_free(ix);
    g_ptr_array_free(entries, TRUE);
}

int main(void) {
    test_exec_binary();
    test_index_resolve_by_wmclass();
    if (failures) { g_printerr("%d failure(s)\n", failures); return 1; }
    g_print("all window_resolve tests passed\n");
    return 0;
}
