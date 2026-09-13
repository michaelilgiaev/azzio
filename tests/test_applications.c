/* Azzio application menu (C port) -- unit tests for the hidden-id / installer
 * swap and app scanning. Lives in the repo-root tests/ dir (the single home for the
 * suite) and is built with `make -C tests test`, which compiles this against the
 * SHIPPING applications.c from libraries/packages/application_menu/ (resolved via the tests
 * Makefile's -I). The top-level `make test` delegates here. Pure asserts, no
 * framework -- exits non-zero on first failure.
 *
 * The key contract (the installer fix): calamares.desktop is HIDDEN (its stock
 * "Install System" entry runs a dead `pkexec calamares` with no polkit agent),
 * and azzio-install.desktop ("Azzio Linux Installer", passwordless-sudo Exec)
 * is SHOWN so the menu launches/re-opens it. This mirrors what the C daemon ships
 * and must stay swapped relative to the old Python behaviour.
 */
#include "applications.h"
#include <glib.h>
#include <stdio.h>
#include <string.h>

static int failures = 0;

#define CHECK(cond, msg) do { \
    if (cond) { g_print("  ok   %s\n", msg); } \
    else      { g_print("  FAIL %s\n", msg); failures++; } \
} while (0)

/* --- the installer swap (the fix) ---------------------------------------- */
static void test_installer_swap(void) {
    g_print("installer swap:\n");
    CHECK(az_is_hidden_desktop_id("calamares.desktop") == TRUE,
          "calamares.desktop is HIDDEN");
    CHECK(az_is_hidden_desktop_id("azzio-install.desktop") == FALSE,
          "azzio-install.desktop is SHOWN");
}

/* --- other denylist ids stay hidden -------------------------------------- */
static void test_denylist(void) {
    g_print("denylist:\n");
    const char *hidden[] = {
        "azzio-application-menu.desktop",
        "azzio-application-menu-shortcut.desktop",
        "bssh.desktop", "bvnc.desktop", "avahi-discover.desktop",
        "kdesystemsettings.desktop", "lstopo.desktop", "htop.desktop",
        "lftp.desktop", "cups.desktop", "org.kde.kmenuedit.desktop",
        "assistant.desktop", "qdbusviewer.desktop", "linguist.desktop",
        "qv4l2.desktop", "qvidcap.desktop", "designer.desktop",
        "vim.desktop",
        /* CMake ("CMake") and nvidia-settings ("NVIDIA X Server Settings") are dev/driver
         * tools that ride in on other packages; they must not clutter the user menu. */
        "cmake-gui.desktop", "nvidia-settings.desktop",
        NULL,
    };
    for (int i = 0; hidden[i]; i++)
        CHECK(az_is_hidden_desktop_id(hidden[i]) == TRUE, hidden[i]);
    /* The real System Settings must NOT be hidden (only KDE's duplicate is). */
    CHECK(az_is_hidden_desktop_id("systemsettings.desktop") == FALSE,
          "systemsettings.desktop is SHOWN");
    /* A normal app is never hidden. */
    CHECK(az_is_hidden_desktop_id("kitty.desktop") == FALSE,
          "kitty.desktop is SHOWN");
}

/* --- category typing (a couple of spot checks) --------------------------- */
static void test_category_type(void) {
    g_print("category type:\n");
    char *web[]  = { "Network", "WebBrowser", NULL };
    char *t1 = az_category_type(web);
    CHECK(t1 && strcmp(t1, "Web Browser") == 0, "WebBrowser -> 'Web Browser'");
    g_free(t1);

    char *term[] = { "System", "TerminalEmulator", NULL };
    char *t2 = az_category_type(term);
    CHECK(t2 && strcmp(t2, "Terminal") == 0, "TerminalEmulator -> 'Terminal'");
    g_free(t2);

    char *empty[] = { NULL };
    char *t3 = az_category_type(empty);
    CHECK(t3 != NULL && t3[0] != '\0', "empty categories -> non-empty fallback");
    g_free(t3);
}

/* --- helpers for the pin / live-session tests ---------------------------- */
/* A minimal heap AzAppEntry with just a desktop_id set (every field owned, so
 * az_app_entry_free / the array free func can clean it up like a real one). */
static AzAppEntry *mk_entry(const char *desktop_id) {
    AzAppEntry *e = g_new0(AzAppEntry, 1);
    e->name            = g_strdup(desktop_id);
    e->type_label      = g_strdup("");
    e->exec_argv       = g_new0(char *, 1);
    e->icon            = g_strdup("");
    e->comment         = g_strdup("");
    e->desktop_id      = g_strdup(desktop_id);
    e->startup_wmclass = g_strdup("");
    return e;
}

static const char *id_at(GPtrArray *a, guint i) {
    return ((AzAppEntry *)g_ptr_array_index(a, i))->desktop_id;
}

/* --- installer pin (the live-session ordering) --------------------------- */
static void test_pin_first(void) {
    g_print("installer pin:\n");
    /* Array with the SAME free func the real scan uses, so a buggy pin that frees
     * the moved entry (the g_ptr_array_remove_index trap) would corrupt/crash here. */
    GPtrArray *a = g_ptr_array_new_with_free_func((GDestroyNotify)az_app_entry_free);
    g_ptr_array_add(a, mk_entry("aaa.desktop"));
    g_ptr_array_add(a, mk_entry("bbb.desktop"));
    g_ptr_array_add(a, mk_entry(az_installer_desktop_id()));   /* index 2 */
    g_ptr_array_add(a, mk_entry("zzz.desktop"));

    gboolean moved = az_apps_pin_first(a, az_installer_desktop_id());
    CHECK(moved == TRUE, "pin reports it moved the installer");
    CHECK(a->len == 4, "pin keeps the entry count (no free/leak of the moved item)");
    CHECK(g_strcmp0(id_at(a, 0), az_installer_desktop_id()) == 0,
          "installer is now first");
    /* Order of the rest is preserved. */
    CHECK(g_strcmp0(id_at(a, 1), "aaa.desktop") == 0, "aaa still 2nd");
    CHECK(g_strcmp0(id_at(a, 2), "bbb.desktop") == 0, "bbb still 3rd");
    CHECK(g_strcmp0(id_at(a, 3), "zzz.desktop") == 0, "zzz still last");
    /* The moved entry is still a VALID object (would crash/garbage if freed). */
    CHECK(((AzAppEntry *)g_ptr_array_index(a, 0))->name != NULL,
          "moved entry survives (not freed)");

    /* Already-first -> no-op, returns FALSE. */
    CHECK(az_apps_pin_first(a, az_installer_desktop_id()) == FALSE,
          "pin is a no-op when already first");
    /* Absent id -> no-op, returns FALSE, nothing disturbed. */
    CHECK(az_apps_pin_first(a, "nope.desktop") == FALSE,
          "pin is a no-op for an absent id");
    CHECK(a->len == 4 && g_strcmp0(id_at(a, 0), az_installer_desktop_id()) == 0,
          "array unchanged after no-op pins");
    g_ptr_array_free(a, TRUE);

    /* Empty / NULL inputs must not crash. */
    CHECK(az_apps_pin_first(NULL, az_installer_desktop_id()) == FALSE,
          "pin handles NULL array");
    GPtrArray *empty = g_ptr_array_new_with_free_func((GDestroyNotify)az_app_entry_free);
    CHECK(az_apps_pin_first(empty, az_installer_desktop_id()) == FALSE,
          "pin handles empty array");
    g_ptr_array_free(empty, TRUE);
}

/* --- live-session detection (via the test override) ---------------------- */
static void test_live_session(void) {
    g_print("live session:\n");
    g_setenv("AZZIO_FORCE_LIVE", "1", TRUE);
    CHECK(az_is_live_session() == TRUE, "AZZIO_FORCE_LIVE=1 -> live");
    g_setenv("AZZIO_FORCE_LIVE", "true", TRUE);
    CHECK(az_is_live_session() == TRUE, "AZZIO_FORCE_LIVE=true -> live");
    g_setenv("AZZIO_FORCE_LIVE", "0", TRUE);
    CHECK(az_is_live_session() == FALSE, "AZZIO_FORCE_LIVE=0 -> installed");
    g_setenv("AZZIO_FORCE_LIVE", "false", TRUE);
    CHECK(az_is_live_session() == FALSE, "AZZIO_FORCE_LIVE=false -> installed");
    g_unsetenv("AZZIO_FORCE_LIVE");
    /* installer id is the expected basename */
    CHECK(g_strcmp0(az_installer_desktop_id(), "azzio-install.desktop") == 0,
          "installer desktop id is azzio-install.desktop");
}

int main(void) {
    test_installer_swap();
    test_denylist();
    test_category_type();
    test_pin_first();
    test_live_session();
    if (failures) {
        g_printerr("\n%d test(s) FAILED\n", failures);
        return 1;
    }
    g_print("\nall tests passed\n");
    return 0;
}
