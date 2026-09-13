/* Azzio application menu (C port) -- icon resolution to GdkPixbuf.
 * One-to-one port of icons.py. See icons.h. */
#include "icons.h"

#include <string.h>
#include <glib.h>
#include <glib/gstdio.h>

static const char *ICON_ROOTS[] = {
    "/usr/local/share/icons", "/usr/share/icons", NULL
};
static const char *PIXMAPS[] = {
    "/usr/local/share/pixmaps", "/usr/share/pixmaps", NULL
};
static const char *DEFAULT_THEME_CHAIN[] = {
    "breeze-dark", "breeze", "Adwaita", "hicolor", NULL
};
/* apps first, actions second (flat session glyphs win over coloured ones). */
static const char *CONTEXTS[] = {
    "apps", "actions", "preferences", "categories", "devices",
    "places", "status", "mimetypes", "apps/preferences", NULL
};
static const char *GENERIC_NAMES[] = {
    "application-x-executable", "application-default-icon", NULL
};

struct AzIcons {
    int          size;
    GPtrArray   *themes;       /* char*, configured-first chain */
    GHashTable  *cache;        /* char* key -> GdkPixbuf* (owned) */
    char        *cache_dir;
};

/* Best-effort read of [Icons] Theme= from ~/.config/kdeglobals. */
static char *read_configured_theme(void) {
    char *path = g_build_filename(g_get_home_dir(), ".config", "kdeglobals", NULL);
    char *text = NULL;
    char *result = NULL;
    if (g_file_get_contents(path, &text, NULL, NULL)) {
        char **lines = g_strsplit(text, "\n", -1);
        gboolean in_icons = FALSE;
        for (int i = 0; lines[i]; i++) {
            char *s = g_strstrip(g_strdup(lines[i]));
            if (s[0] == '[' && s[strlen(s) - 1] == ']')
                in_icons = (strcmp(s, "[Icons]") == 0);
            else if (in_icons && g_str_has_prefix(s, "Theme=")) {
                char *v = g_strstrip(g_strdup(s + 6));
                if (v[0]) result = g_strdup(v);
                g_free(v);
                g_free(s);
                break;
            }
            g_free(s);
        }
        g_strfreev(lines);
    }
    g_free(text);
    return result;
}

static GPtrArray *theme_chain(void) {
    GPtrArray *chain = g_ptr_array_new_with_free_func(g_free);
    char *configured = read_configured_theme();
    if (configured)
        g_ptr_array_add(chain, configured);
    for (int i = 0; DEFAULT_THEME_CHAIN[i]; i++) {
        gboolean have = FALSE;
        for (guint j = 0; j < chain->len; j++)
            if (strcmp(g_ptr_array_index(chain, j), DEFAULT_THEME_CHAIN[i]) == 0)
                { have = TRUE; break; }
        if (!have)
            g_ptr_array_add(chain, g_strdup(DEFAULT_THEME_CHAIN[i]));
    }
    return chain;
}

AzIcons *az_icons_new(int size) {
    AzIcons *r = g_new0(AzIcons, 1);
    r->size = size;
    r->themes = theme_chain();
    r->cache = g_hash_table_new_full(g_str_hash, g_str_equal, g_free,
                                     (GDestroyNotify)g_object_unref);
    const char *cache_home = g_getenv("XDG_CACHE_HOME");
    if (cache_home && cache_home[0])
        r->cache_dir = g_build_filename(cache_home, "azzio-application-menu",
                                        "icons", NULL);
    else
        r->cache_dir = g_build_filename(g_get_home_dir(), ".cache",
                                        "azzio-application-menu", "icons", NULL);
    g_mkdir_with_parents(r->cache_dir, 0755);
    return r;
}

void az_icons_free(AzIcons *r) {
    if (!r) return;
    g_ptr_array_free(r->themes, TRUE);
    g_hash_table_destroy(r->cache);
    g_free(r->cache_dir);
    g_free(r);
}

static gboolean has_ext(const char *name, const char *const *exts) {
    for (int i = 0; exts[i]; i++)
        if (g_str_has_suffix(name, exts[i]))
            return TRUE;
    return FALSE;
}
/* Size ranking: >= target first (closest), then remaining by proximity. */
static const int NUMERIC_SIZES[] = {
    512, 256, 192, 128, 96, 64, 48, 44, 40, 36, 32, 24, 22, 16
};
typedef struct { int size; int target; } SizeRank;
static gint cmp_size_rank(gconstpointer a, gconstpointer b, gpointer u) {
    int target = GPOINTER_TO_INT(u);
    int sa = *(const int *)a, sb = *(const int *)b;
    int ra0 = (sa >= target) ? 0 : 1, rb0 = (sb >= target) ? 0 : 1;
    if (ra0 != rb0) return ra0 - rb0;
    int da = (sa >= target) ? sa - target : target - sa;
    int db = (sb >= target) ? sb - target : target - sb;
    return da - db;
}

/* Append ordered candidate file paths for icon name `base`. */
static void themed_candidates(AzIcons *r, const char *base, GPtrArray *out) {
    int n = (int)(sizeof(NUMERIC_SIZES) / sizeof(NUMERIC_SIZES[0]));
    int *sizes = g_new(int, n);
    memcpy(sizes, NUMERIC_SIZES, sizeof(NUMERIC_SIZES));
    g_qsort_with_data(sizes, n, sizeof(int), cmp_size_rank,
                      GINT_TO_POINTER(r->size));

    for (guint ti = 0; ti < r->themes->len; ti++) {
        const char *theme = g_ptr_array_index(r->themes, ti);
        for (int ri = 0; ICON_ROOTS[ri]; ri++) {
            char *theme_dir = g_build_filename(ICON_ROOTS[ri], theme, NULL);
            if (!g_file_test(theme_dir, G_FILE_TEST_IS_DIR)) {
                g_free(theme_dir);
                continue;
            }
            for (int ci = 0; CONTEXTS[ci]; ci++) {
                const char *context = CONTEXTS[ci];
                for (int si = 0; si < n; si++) {
                    int s = sizes[si];
                    char *subs[3] = {
                        g_strdup_printf("%s/%d", context, s),
                        g_strdup_printf("%dx%d/%s", s, s, context),
                        g_strdup_printf("%s/%dx%d", context, s, s),
                    };
                    for (int k = 0; k < 3; k++) {
                        char *d = g_build_filename(theme_dir, subs[k], NULL);
                        g_ptr_array_add(out, g_strdup_printf("%s/%s.png", d, base));
                        g_ptr_array_add(out, g_strdup_printf("%s/%s.svg", d, base));
                        g_free(d);
                        g_free(subs[k]);
                    }
                }
                /* scalable dirs for this context */
                char *sc1 = g_build_filename(theme_dir, context, "scalable", NULL);
                char *sc2 = g_build_filename(theme_dir, "scalable", context, NULL);
                g_ptr_array_add(out, g_strdup_printf("%s/%s.svg", sc1, base));
                g_ptr_array_add(out, g_strdup_printf("%s/%s.svg", sc2, base));
                g_free(sc1); g_free(sc2);
            }
            g_free(theme_dir);
        }
    }
    g_free(sizes);
}

/* Locate the best on-disk file for an Icon= value, or NULL. */
static char *find_source(AzIcons *r, const char *icon) {
    if (!icon || !icon[0])
        return NULL;
    /* Absolute/explicit path. */
    if (g_path_is_absolute(icon) && g_file_test(icon, G_FILE_TEST_IS_REGULAR))
        return g_strdup(icon);

    /* Name may already include an extension -> strip for lookup base. */
    char *base;
    if (has_ext(icon, (const char *[]){ ".png", ".svg", ".svgz", NULL })) {
        char *dot = strrchr(icon, '.');
        base = g_strndup(icon, dot - icon);
    } else {
        base = g_strdup(icon);
    }

    GPtrArray *cands = g_ptr_array_new_with_free_func(g_free);
    themed_candidates(r, base, cands);
    char *found = NULL;
    for (guint i = 0; i < cands->len; i++) {
        const char *c = g_ptr_array_index(cands, i);
        if (g_file_test(c, G_FILE_TEST_IS_REGULAR)) {
            found = g_strdup(c);
            break;
        }
    }
    g_ptr_array_free(cands, TRUE);

    if (!found) {
        /* Pixmaps (flat, no size) as a last resort. */
        const char *exts[] = { ".png", ".svg", ".svgz", NULL };
        for (int pi = 0; PIXMAPS[pi] && !found; pi++)
            for (int ei = 0; exts[ei] && !found; ei++) {
                char *p = g_strdup_printf("%s/%s%s", PIXMAPS[pi], base, exts[ei]);
                if (g_file_test(p, G_FILE_TEST_IS_REGULAR))
                    found = g_strdup(p);
                g_free(p);
            }
    }
    g_free(base);
    return found;
}

/* Rasterise an SVG to a cached PNG at the target size (rsvg-convert). */
static char *rasterise(AzIcons *r, const char *svg_path) {
    GStatBuf st;
    if (g_stat(svg_path, &st) != 0)
        return NULL;
    char *sig = g_strdup_printf("%s:%ld:%d", svg_path,
                                (long)st.st_mtime, r->size);
    char *digest_full = g_compute_checksum_for_string(G_CHECKSUM_SHA1, sig, -1);
    char digest[17];
    g_strlcpy(digest, digest_full, sizeof(digest));  /* first 16 hex chars */
    g_free(digest_full);
    g_free(sig);

    char *out_png = g_strdup_printf("%s/%s-%d.png", r->cache_dir, digest, r->size);
    GStatBuf ost;
    if (g_stat(out_png, &ost) == 0 && ost.st_size > 0)
        return out_png;

    char *size_s = g_strdup_printf("%d", r->size);
    char *argv[] = { "rsvg-convert", "-w", size_s, "-h", size_s,
                     "-o", out_png, (char *)svg_path, NULL };
    int status = 0;
    gboolean ok = g_spawn_sync(NULL, argv, NULL,
                               G_SPAWN_SEARCH_PATH | G_SPAWN_STDOUT_TO_DEV_NULL |
                               G_SPAWN_STDERR_TO_DEV_NULL,
                               NULL, NULL, NULL, NULL, &status, NULL);
    g_free(size_s);
    if (ok && g_spawn_check_wait_status(status, NULL) &&
        g_stat(out_png, &ost) == 0 && ost.st_size > 0)
        return out_png;
    g_free(out_png);
    return NULL;
}

/* Find (or rasterise) a PNG on disk for this icon value, or NULL. */
static char *resolve_to_png(AzIcons *r, const char *icon) {
    char *src = find_source(r, icon);
    if (!src) {
        gboolean is_generic = FALSE;
        for (int i = 0; GENERIC_NAMES[i]; i++)
            if (icon && strcmp(icon, GENERIC_NAMES[i]) == 0) is_generic = TRUE;
        if (!is_generic)
            for (int i = 0; GENERIC_NAMES[i] && !src; i++)
                src = find_source(r, GENERIC_NAMES[i]);
    }
    if (!src)
        return NULL;
    char *lower = g_ascii_strdown(src, -1);
    char *png = NULL;
    if (g_str_has_suffix(lower, ".png"))
        png = g_strdup(src);
    else if (g_str_has_suffix(lower, ".svg") || g_str_has_suffix(lower, ".svgz"))
        png = rasterise(r, src);
    g_free(lower);
    g_free(src);
    return png;
}

static GdkPixbuf *placeholder(int size) {
    GdkPixbuf *p = gdk_pixbuf_new(GDK_COLORSPACE_RGB, TRUE, 8, size, size);
    /* Muted grey placeholder surface #4d5359, fully opaque. */
    gdk_pixbuf_fill(p, 0x4d5359ffu);
    return p;
}

GdkPixbuf *az_icons_load(AzIcons *r, const char *icon) {
    const char *key = (icon && icon[0]) ? icon : "<none>";
    GdkPixbuf *cached = g_hash_table_lookup(r->cache, key);
    if (cached)
        return cached;

    char *png = resolve_to_png(r, icon ? icon : "");
    GdkPixbuf *img = NULL;
    if (png) {
        GError *err = NULL;
        /* Scale to the target square, preserving aspect like the Python's
         * subsample-toward-target intent but crisper (bilinear). */
        img = gdk_pixbuf_new_from_file_at_size(png, r->size, r->size, &err);
        if (err) g_error_free(err);
        g_free(png);
    }
    if (!img)
        img = placeholder(r->size);
    g_hash_table_insert(r->cache, g_strdup(key), img);
    return img;
}
