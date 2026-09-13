/* Azzio application menu (C port) -- launch-frequency usage tracking.
 * Port of usage.py. Uses GLib's JSON-free approach: the store is a tiny flat
 * {"id":N,...} object; we parse/emit it by hand (no json-glib dependency) in the
 * SAME compact form usage.py writes (json.dump separators=(",",":")) so the two
 * are byte-compatible and can read each other's files. */
#include "usage.h"

#include <string.h>
#include <stdio.h>
#include <unistd.h>
#include <glib.h>
#include <glib/gstdio.h>

struct AzUsage {
    char       *path;
    GHashTable *counts;   /* char* desktop_id -> int (via GINT_TO_POINTER) */
    AzUsage    *sort_ctx; /* used by the sort comparator (self) */
};

static char *store_path(void) {
    const char *override = g_getenv("AZZIO_USAGE_FILE");
    if (override && override[0])
        return g_strdup(override);
    const char *data_home = g_getenv("XDG_DATA_HOME");
    if (data_home && data_home[0])
        return g_build_filename(data_home, "azzio-application-menu",
                                "usage.json", NULL);
    return g_build_filename(g_get_home_dir(), ".local", "share",
                            "azzio-application-menu", "usage.json", NULL);
}

/* Minimal, defensive parser for a flat JSON object of string->integer.
 * Mirrors usage.py._load: coerce sane ints, keep only n>0, ignore anything odd.
 * Not a general JSON parser -- the store is only ever this shape. */
static void load_counts(AzUsage *u) {
    char *text = NULL;
    gsize len = 0;
    if (!g_file_get_contents(u->path, &text, &len, NULL))
        return;                 /* missing -> {} */

    const char *p = text;
    while (*p) {
        /* find a key opening quote */
        while (*p && *p != '"') p++;
        if (!*p) break;
        p++;                    /* past opening quote */
        GString *key = g_string_new(NULL);
        while (*p && *p != '"') {
            if (*p == '\\' && p[1]) { g_string_append_c(key, p[1]); p += 2; }
            else g_string_append_c(key, *p++);
        }
        if (*p == '"') p++;     /* past closing quote */
        while (*p && *p != ':') p++;
        if (*p == ':') p++;
        while (*p == ' ' || *p == '\t') p++;
        /* parse integer value */
        char *endp = NULL;
        long n = strtol(p, &endp, 10);
        if (endp != p && n > 0 && key->len > 0)
            g_hash_table_insert(u->counts, g_strdup(key->str),
                                GINT_TO_POINTER((int)n));
        g_string_free(key, TRUE);
        p = (endp != p) ? endp : p + 1;
    }
    g_free(text);
}

AzUsage *az_usage_new(void) {
    AzUsage *u = g_new0(AzUsage, 1);
    u->path = store_path();
    u->counts = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, NULL);
    load_counts(u);
    return u;
}

void az_usage_free(AzUsage *u) {
    if (!u) return;
    g_hash_table_destroy(u->counts);
    g_free(u->path);
    g_free(u);
}

int az_usage_count(AzUsage *u, const char *desktop_id) {
    gpointer v;
    if (g_hash_table_lookup_extended(u->counts, desktop_id, NULL, &v))
        return GPOINTER_TO_INT(v);
    return 0;
}

/* Write the whole map atomically (temp file + rename), compact JSON. */
static void save_counts(AzUsage *u) {
    char *dir = g_path_get_dirname(u->path);
    g_mkdir_with_parents(dir, 0755);

    GString *out = g_string_new("{");
    GHashTableIter it;
    gpointer k, v;
    g_hash_table_iter_init(&it, u->counts);
    gboolean first = TRUE;
    while (g_hash_table_iter_next(&it, &k, &v)) {
        if (!first) g_string_append_c(out, ',');
        first = FALSE;
        /* keys here are plain .desktop ids -- no chars needing JSON escaping,
         * but escape quotes/backslashes defensively to stay valid JSON. */
        g_string_append_c(out, '"');
        for (const char *c = (const char *)k; *c; c++) {
            if (*c == '"' || *c == '\\') g_string_append_c(out, '\\');
            g_string_append_c(out, *c);
        }
        g_string_append_printf(out, "\":%d", GPOINTER_TO_INT(v));
    }
    g_string_append_c(out, '}');

    char *tmp = g_strdup_printf("%s.tmp-%d", u->path, (int)getpid());
    if (g_file_set_contents(tmp, out->str, out->len, NULL))
        g_rename(tmp, u->path);
    g_unlink(tmp);              /* harmless no-op if rename consumed it */
    g_free(tmp);
    g_string_free(out, TRUE);
    g_free(dir);
}

void az_usage_record(AzUsage *u, const char *desktop_id) {
    if (!desktop_id || !desktop_id[0]) return;
    int n = az_usage_count(u, desktop_id) + 1;
    g_hash_table_insert(u->counts, g_strdup(desktop_id), GINT_TO_POINTER(n));
    save_counts(u);
}

/* order_key: (-count, casefold(name)). Python sorts ascending, so bigger count
 * first; ties by casefolded name. */
static gint cmp_freq(gconstpointer a, gconstpointer b, gpointer user) {
    AzUsage *u = (AzUsage *)user;
    const AzAppEntry *ea = *(const AzAppEntry *const *)a;
    const AzAppEntry *eb = *(const AzAppEntry *const *)b;
    int ca = az_usage_count(u, ea->desktop_id);
    int cb = az_usage_count(u, eb->desktop_id);
    if (ca != cb)
        return cb - ca;        /* higher count first */
    char *na = g_utf8_casefold(ea->name, -1);
    char *nb = g_utf8_casefold(eb->name, -1);
    gint r = g_strcmp0(na, nb);
    g_free(na); g_free(nb);
    return r;
}

void az_usage_sort_apps(AzUsage *u, GPtrArray *apps) {
    g_ptr_array_sort_with_data(apps, cmp_freq, u);
}
