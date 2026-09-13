/* Azzio window switcher -- enumerate managed windows + resolve identity. See windows.h.
 *
 * The xprop helpers (run_xprop, client_list, the WM_CLASS/PID/type parser) mirror
 * application_menu/window_watch.c so both agree on what "a window" is; here we also read
 * _NET_WM_NAME (the title, needed for the hypervisor match) and map the resolved
 * .desktop id back to its AzAppEntry for the display name + icon. */
#include "windows.h"

#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <glib.h>

#include "window_resolve.h"      /* az_index_build / az_index_resolve / az_index_free */
#include "applications.h"     /* az_scan_applications, AzAppEntry */

/* Same skip set as the application-menu watcher: never offer these as "windows". */
static const char *SKIP_TYPES[] = {
    "_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK",
    "_NET_WM_WINDOW_TYPE_TOOLBAR", "_NET_WM_WINDOW_TYPE_MENU",
    "_NET_WM_WINDOW_TYPE_UTILITY", "_NET_WM_WINDOW_TYPE_SPLASH",
    "_NET_WM_WINDOW_TYPE_DROPDOWN_MENU", "_NET_WM_WINDOW_TYPE_POPUP_MENU",
    "_NET_WM_WINDOW_TYPE_TOOLTIP", "_NET_WM_WINDOW_TYPE_NOTIFICATION",
    "_NET_WM_WINDOW_TYPE_COMBO", "_NET_WM_WINDOW_TYPE_DND", NULL
};

/* ---- xprop helpers (mirror window_watch.c) ------------------------------ */
static char *run_xprop(char **args) {
    char *out = NULL;
    int status = 0;
    if (g_spawn_sync(NULL, args, NULL,
                     G_SPAWN_SEARCH_PATH | G_SPAWN_STDERR_TO_DEV_NULL,
                     NULL, NULL, &out, NULL, &status, NULL)) {
        return out ? out : g_strdup("");
    }
    g_free(out);
    return g_strdup("");
}

/* _NET_CLIENT_LIST as an ordered GPtrArray<char*> of window-id strings (0x...). The
 * order is the stacking/age order the WM maintains -- used as stack_index. */
static GPtrArray *client_list(void) {
    GPtrArray *ids = g_ptr_array_new_with_free_func(g_free);
    char *args[] = { "xprop", "-root", "_NET_CLIENT_LIST", NULL };
    char *out = run_xprop(args);
    char *hash = strchr(out, '#');
    if (hash) {
        char **parts = g_strsplit(hash + 1, ",", -1);
        for (int i = 0; parts[i]; i++) {
            char *w = g_strstrip(g_strdup(parts[i]));
            if (w[0]) g_ptr_array_add(ids, w);
            else g_free(w);
        }
        g_strfreev(parts);
    }
    g_free(out);
    return ids;
}

/* Strip surrounding quotes/space from an xprop value chunk. Returns newly-allocated. */
static char *unquote(const char *chunk) {
    char *c = g_strstrip(g_strdup(chunk));
    char *s = c;
    if (*s == '"') s++;
    char *end = s + strlen(s);
    while (end > s && (end[-1] == '"' || g_ascii_isspace(end[-1]))) end--;
    char *r = g_strndup(s, (gsize)(end - s));
    g_free(c);
    return r;
}

/* Parse the multi-property xprop dump for one window. Fills wm_classes/types (owned
 * char*), *pid, and *title (newly-allocated or NULL). */
static void parse_props(const char *out, GPtrArray *wm_classes, int *pid,
                        GPtrArray *types, char **title) {
    char **lines = g_strsplit(out, "\n", -1);
    for (int i = 0; lines[i]; i++) {
        const char *ln = lines[i];
        if (g_str_has_prefix(ln, "WM_CLASS")) {
            const char *eq = strchr(ln, '=');
            if (eq) {
                char **chunks = g_strsplit(eq + 1, ",", -1);
                for (int k = 0; chunks[k]; k++) {
                    char *s = unquote(chunks[k]);
                    if (s[0]) g_ptr_array_add(wm_classes, s);
                    else g_free(s);
                }
                g_strfreev(chunks);
            }
        } else if (g_str_has_prefix(ln, "_NET_WM_PID")) {
            const char *eq = strchr(ln, '=');
            if (eq) *pid = atoi(eq + 1);
        } else if (g_str_has_prefix(ln, "_NET_WM_WINDOW_TYPE")) {
            const char *eq = strchr(ln, '=');
            if (eq) {
                char **ts = g_strsplit(eq + 1, ",", -1);
                for (int k = 0; ts[k]; k++) {
                    char *t = g_strstrip(g_strdup(ts[k]));
                    if (t[0]) g_ptr_array_add(types, t);
                    else g_free(t);
                }
                g_strfreev(ts);
            }
        } else if (!*title &&
                   (g_str_has_prefix(ln, "_NET_WM_NAME") ||
                    g_str_has_prefix(ln, "WM_NAME"))) {
            /* Prefer _NET_WM_NAME (UTF-8); fall back to WM_NAME. Only take the first. */
            const char *eq = strchr(ln, '=');
            if (eq) {
                char *t = unquote(eq + 1);
                if (t[0]) *title = t; else g_free(t);
            }
        }
    }
    g_strfreev(lines);
}

static gboolean all_skippable(GPtrArray *types) {
    if (types->len == 0) return FALSE;
    for (guint i = 0; i < types->len; i++) {
        const char *t = g_ptr_array_index(types, i);
        gboolean skip = FALSE;
        for (int k = 0; SKIP_TYPES[k]; k++)
            if (strcmp(t, SKIP_TYPES[k]) == 0) { skip = TRUE; break; }
        if (!skip) return FALSE;
    }
    return TRUE;
}

/* Parse "0x1234" (or "1234") -> unsigned long. */
static unsigned long parse_xid(const char *s) {
    return (unsigned long)g_ascii_strtoull(s, NULL, 0);
}

static void ident_free(gpointer p) {
    AzWinIdent *w = p;
    if (!w) return;
    g_free(w->desktop_id);
    g_free(w->wm_class);
    g_free(w->title);
    g_free(w->display_name);
    g_free(w->icon_name);
    g_free(w);
}

GPtrArray *az_windows_list(void) {
    GPtrArray *out = g_ptr_array_new_with_free_func(ident_free);

    /* One application scan -> the resolver index + a desktop_id -> entry map (for the
     * display name + icon of whatever the window resolves to). */
    GPtrArray *entries = az_scan_applications();
    DesktopIndex *ix = az_index_build(entries);
    GHashTable *by_id = g_hash_table_new(g_str_hash, g_str_equal);  /* borrowed */
    for (guint i = 0; i < entries->len; i++) {
        AzAppEntry *e = g_ptr_array_index(entries, i);
        if (e->desktop_id) g_hash_table_insert(by_id, e->desktop_id, e);
    }

    int own_pid = (int)getpid();
    GPtrArray *ids = client_list();
    for (guint i = 0; i < ids->len; i++) {
        const char *win = g_ptr_array_index(ids, i);
        char *args[] = { "xprop", "-id", (char *)win, "WM_CLASS", "_NET_WM_PID",
                         "_NET_WM_WINDOW_TYPE", "_NET_WM_NAME", "WM_NAME", NULL };
        char *dump = run_xprop(args);

        GPtrArray *wm_classes = g_ptr_array_new_with_free_func(g_free);
        GPtrArray *types = g_ptr_array_new_with_free_func(g_free);
        int pid = 0; char *title = NULL;
        parse_props(dump, wm_classes, &pid, types, &title);
        g_free(dump);

        gboolean drop = all_skippable(types) || (pid > 0 && pid == own_pid);
        if (!drop) {
            const char *did = az_index_resolve(ix, wm_classes, pid);
            AzAppEntry *e = (did && *did) ? g_hash_table_lookup(by_id, did) : NULL;

            AzWinIdent *id = g_new0(AzWinIdent, 1);
            id->xid = parse_xid(win);
            id->stack_index = (int)i;
            id->desktop_id = did ? g_strdup(did) : NULL;
            id->wm_class = (wm_classes->len > 0)
                         ? g_strdup(g_ptr_array_index(wm_classes, 0)) : NULL;
            id->title = title; title = NULL;   /* transfer ownership */
            /* Display name: the .desktop Name, else the WM_CLASS, else "window". */
            if (e && e->name && e->name[0]) id->display_name = g_strdup(e->name);
            else if (id->wm_class)          id->display_name = g_strdup(id->wm_class);
            else                            id->display_name = g_strdup("window");
            if (e && e->icon && e->icon[0]) id->icon_name = g_strdup(e->icon);
            g_ptr_array_add(out, id);
        }
        g_free(title);
        g_ptr_array_free(wm_classes, TRUE);
        g_ptr_array_free(types, TRUE);
    }

    g_ptr_array_free(ids, TRUE);
    g_hash_table_destroy(by_id);
    az_index_free(ix);
    g_ptr_array_free(entries, TRUE);

    az_order_sort(out);
    return out;
}

void az_windows_free(GPtrArray *windows) {
    if (windows) g_ptr_array_free(windows, TRUE);
}
