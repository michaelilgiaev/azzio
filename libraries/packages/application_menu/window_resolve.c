/* Azzio -- shared window identity -> .desktop id resolver. See window_resolve.h.
 * Moved verbatim out of window_watch.c (only the public names gained an `az_`
 * prefix); the matching order is: WM_CLASS -> Exec basename / id stem -> the PID's
 * /proc exe + cmdline binaries. */
#include "window_resolve.h"

#include <string.h>
#include <stdlib.h>
#include <glib.h>

/* ---- desktop index (window identity -> desktop id) ---------------------- */
struct DesktopIndex {
    GHashTable *by_startup_wmclass;  /* casefolded -> did (both owned) */
    GHashTable *by_exec_bin;
    GHashTable *by_id_stem;
};

/* Basename of the real launched binary from an Exec argv[0], skipping a leading
 * env/wrapper (env FOO=1 kitty). Returns newly-allocated or NULL. */
char *az_exec_binary(char **argv) {
    if (!argv) return NULL;
    int idx = 0;
    while (argv[idx]) {
        char *base = g_path_get_basename(argv[idx]);
        gboolean is_env = (strcmp(base, "env") == 0) || (strchr(argv[idx], '=') != NULL);
        g_free(base);
        if (is_env) { idx++; continue; }
        break;
    }
    if (!argv[idx]) return NULL;
    char *b = g_path_get_basename(argv[idx]);
    if (!b[0]) { g_free(b); return NULL; }
    return b;
}

static void idx_set_default(GHashTable *t, const char *key_cf, const char *did) {
    if (!g_hash_table_contains(t, key_cf))
        g_hash_table_insert(t, g_strdup(key_cf), g_strdup(did));
}

DesktopIndex *az_index_build(GPtrArray *entries) {
    DesktopIndex *ix = g_new0(DesktopIndex, 1);
    ix->by_startup_wmclass = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, g_free);
    ix->by_exec_bin        = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, g_free);
    ix->by_id_stem         = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, g_free);

    for (guint i = 0; i < entries->len; i++) {
        AzAppEntry *e = g_ptr_array_index(entries, i);
        const char *did = e->desktop_id;
        if (e->startup_wmclass && e->startup_wmclass[0]) {
            char *cf = g_utf8_casefold(e->startup_wmclass, -1);
            idx_set_default(ix->by_startup_wmclass, cf, did);
            g_free(cf);
        }
        char *eb = az_exec_binary(e->exec_argv);
        if (eb) {
            char *cf = g_utf8_casefold(eb, -1);
            idx_set_default(ix->by_exec_bin, cf, did);
            g_free(cf);
            g_free(eb);
        }
        char *stem;
        if (g_str_has_suffix(did, ".desktop"))
            stem = g_strndup(did, strlen(did) - 8);
        else
            stem = g_strdup(did);
        char *stem_cf = g_utf8_casefold(stem, -1);
        idx_set_default(ix->by_id_stem, stem_cf, did);
        g_free(stem_cf);
        char *dot = strrchr(stem, '.');
        if (dot) {
            char *last_cf = g_utf8_casefold(dot + 1, -1);
            idx_set_default(ix->by_id_stem, last_cf, did);
            g_free(last_cf);
        }
        g_free(stem);
    }
    return ix;
}

void az_index_free(DesktopIndex *ix) {
    if (!ix) return;
    g_hash_table_destroy(ix->by_startup_wmclass);
    g_hash_table_destroy(ix->by_exec_bin);
    g_hash_table_destroy(ix->by_id_stem);
    g_free(ix);
}

static char *proc_exe_basename(int pid) {
    char *link = g_strdup_printf("/proc/%d/exe", pid);
    char *target = g_file_read_link(link, NULL);
    g_free(link);
    if (!target) return NULL;
    char *base = g_path_get_basename(target);
    g_free(target);
    if (g_str_has_suffix(base, " (deleted)"))
        base[strlen(base) - strlen(" (deleted)")] = '\0';
    if (!base[0]) { g_free(base); return NULL; }
    return base;
}

static GPtrArray *proc_cmdline_bins(int pid) {
    GPtrArray *out = g_ptr_array_new_with_free_func(g_free);
    char *path = g_strdup_printf("/proc/%d/cmdline", pid);
    char *raw = NULL; gsize len = 0;
    if (g_file_get_contents(path, &raw, &len, NULL)) {
        gsize start = 0; int i = 0;
        for (gsize p = 0; p <= len && i < 4; p++) {
            if (p == len || raw[p] == '\0') {
                if (p > start) {
                    char *tok = g_strndup(raw + start, p - start);
                    char *base = g_path_get_basename(tok);
                    if (base[0] && (i == 0 || strchr(tok, '/')))
                        g_ptr_array_add(out, g_strdup(base));
                    g_free(base); g_free(tok);
                    i++;
                }
                start = p + 1;
            }
        }
    }
    g_free(raw); g_free(path);
    return out;
}

const char *az_index_resolve(DesktopIndex *ix, GPtrArray *wm_classes, int pid) {
    for (guint i = 0; i < wm_classes->len; i++) {
        char *cf = g_utf8_casefold(g_ptr_array_index(wm_classes, i), -1);
        const char *hit = g_hash_table_lookup(ix->by_startup_wmclass, cf);
        g_free(cf);
        if (hit) return hit;
    }
    for (guint i = 0; i < wm_classes->len; i++) {
        char *cf = g_utf8_casefold(g_ptr_array_index(wm_classes, i), -1);
        const char *hit = g_hash_table_lookup(ix->by_exec_bin, cf);
        if (!hit) hit = g_hash_table_lookup(ix->by_id_stem, cf);
        g_free(cf);
        if (hit) return hit;
    }
    if (pid > 0) {
        char *base = proc_exe_basename(pid);
        if (base) {
            char *cf = g_utf8_casefold(base, -1);
            const char *hit = g_hash_table_lookup(ix->by_exec_bin, cf);
            if (!hit) hit = g_hash_table_lookup(ix->by_id_stem, cf);
            g_free(cf); g_free(base);
            if (hit) return hit;
        }
        GPtrArray *bins = proc_cmdline_bins(pid);
        const char *hit = NULL;
        for (guint i = 0; i < bins->len && !hit; i++) {
            char *cf = g_utf8_casefold(g_ptr_array_index(bins, i), -1);
            hit = g_hash_table_lookup(ix->by_exec_bin, cf);
            if (!hit) hit = g_hash_table_lookup(ix->by_id_stem, cf);
            g_free(cf);
        }
        g_ptr_array_free(bins, TRUE);
        if (hit) return hit;
    }
    return NULL;
}
