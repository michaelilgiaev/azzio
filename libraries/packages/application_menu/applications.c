/* Azzio application menu (C port) -- application discovery + category typing.
 * One-to-one port of the original applications.py. See applications.h. */
#include "applications.h"

#include <string.h>
#include <stdlib.h>
#include <glib.h>

/* --- Category -> human "type" label -------------------------------------- */
/* "Additional" (specific) categories -- the good subtitles. */
static const char *ADDITIONAL_KV[] = {
    "WebBrowser", "Web Browser", "TerminalEmulator", "Terminal",
    "FileManager", "File Manager", "FileTransfer", "File Transfer",
    "TextEditor", "Text Editor", "IDE", "Development Environment",
    "Debugger", "Debugger", "GUIDesigner", "GUI Designer",
    "WebDevelopment", "Web Development", "Documentation", "Documentation",
    "Email", "Email Client", "InstantMessaging", "Instant Messaging",
    "IRCClient", "IRC Client", "Chat", "Chat",
    "VideoConference", "Video Conference", "News", "News Reader",
    "Feed", "Feed Reader", "RemoteAccess", "Remote Access",
    "P2P", "File Sharing", "Dialup", "Dialup",
    "WordProcessor", "Word Processor", "Spreadsheet", "Spreadsheet",
    "Presentation", "Presentation", "Database", "Database",
    "Calendar", "Calendar", "ContactManagement", "Contacts",
    "Publishing", "Publishing", "Finance", "Finance",
    "Photography", "Photography", "Viewer", "Viewer",
    "Scanning", "Scanning", "OCR", "OCR",
    "2DGraphics", "Graphics", "3DGraphics", "3D Graphics",
    "VectorGraphics", "Vector Graphics", "RasterGraphics", "Image Editor",
    "ImageProcessing", "Image Processing", "Player", "Media Player",
    "Recorder", "Recorder", "AudioVideoEditing", "Media Editor",
    "Audio", "Audio", "Video", "Video",
    "Mixer", "Mixer", "Sequencer", "Sequencer",
    "Tuner", "Tuner", "TV", "TV",
    "DiscBurning", "Disc Burning", "Music", "Music",
    "Midi", "MIDI", "TerminalEmulatorConsole", "Console",
    "PackageManager", "Package Manager", "Monitor", "System Monitor",
    "Security", "Security", "Accessibility", "Accessibility",
    "Printing", "Printing", "Filesystem", "Filesystem",
    "HardwareSettings", "Hardware Settings", "DesktopSettings", "Desktop Settings",
    "PackageSettings", "Package Settings", "Screensaver", "Screensaver",
    "Calculator", "Calculator", "Clock", "Clock",
    "TextTools", "Text Tools", "Archiving", "Archive Tool",
    "Compression", "Compression", "Telephony", "Telephony",
    "Dictionary", "Dictionary", "FileTools", "File Tool",
    "Emulator", "Emulator", "Engineering", "Engineering",
    "Astronomy", "Astronomy", "Biology", "Biology",
    "Chemistry", "Chemistry", "Geoscience", "Geoscience",
    "Physics", "Physics", "Math", "Mathematics",
    "Electronics", "Electronics", "Robotics", "Robotics",
    "MedicalSoftware", "Medical", "ArtificialIntelligence", "AI",
    "ComputerScience", "Computer Science", "DataVisualization", "Data Visualization",
    "NumericalAnalysis", "Numerical Analysis", "History", "History",
    "Languages", "Languages", "Literature", "Literature",
    "Geography", "Geography", "ActionGame", "Action Game",
    "AdventureGame", "Adventure Game", "ArcadeGame", "Arcade Game",
    "BoardGame", "Board Game", "BlocksGame", "Puzzle Game",
    "CardGame", "Card Game", "KidsGame", "Kids Game",
    "LogicGame", "Logic Game", "RolePlaying", "Role-Playing Game",
    "Shooter", "Shooter", "Simulation", "Simulation",
    "SportsGame", "Sports Game", "StrategyGame", "Strategy Game",
    "Emulator2", "Emulator",
    NULL
};

/* "Main" categories -- fallback subtitles. */
static const char *MAIN_KV[] = {
    "AudioVideo", "Multimedia", "Audio", "Audio", "Video", "Video",
    "Development", "Development", "Education", "Education", "Game", "Game",
    "Graphics", "Graphics", "Network", "Internet", "Office", "Office",
    "Science", "Science", "Settings", "Settings", "System", "System",
    "Utility", "Utility",
    NULL
};

/* Noise tokens -- never a subtitle. */
static const char *NOISE[] = {
    "Qt", "GTK", "KDE", "GNOME", "Motif", "Java", "Application",
    "ConsoleOnly", "Core", "Documentation", NULL
};

/* Additional-category priority: earlier wins when several are present. */
static const char *ADDITIONAL_PRIORITY[] = {
    "WebBrowser", "TerminalEmulator", "FileManager", "Email",
    "InstantMessaging", "IRCClient", "IDE", "TextEditor", "WordProcessor",
    "Spreadsheet", "Presentation", "Database", "Player", "RasterGraphics",
    "VectorGraphics", "PackageManager", "Monitor", "FileTransfer",
    "RemoteAccess", "Calculator", "Archiving", "Printing", NULL
};

#define GENERIC_TYPE "Application"

static const char *kv_lookup(const char *const *kv, const char *key) {
    for (int i = 0; kv[i] != NULL; i += 2)
        if (strcmp(kv[i], key) == 0)
            return kv[i + 1];
    return NULL;
}

static gboolean in_list(const char *const *list, const char *key) {
    for (int i = 0; list[i] != NULL; i++)
        if (strcmp(list[i], key) == 0)
            return TRUE;
    return FALSE;
}

static int additional_rank(const char *tok) {
    for (int i = 0; ADDITIONAL_PRIORITY[i] != NULL; i++)
        if (strcmp(ADDITIONAL_PRIORITY[i], tok) == 0)
            return i;
    /* Unlisted-but-recognised ranks after the priority list. */
    return (int)(sizeof(ADDITIONAL_PRIORITY) / sizeof(ADDITIONAL_PRIORITY[0]));
}

char *az_category_type(char **categories) {
    /* 1. Most specific: among recognised Additional categories, highest priority. */
    const char *best = NULL;
    int best_rank = 1 << 30;
    for (int i = 0; categories && categories[i]; i++) {
        if (kv_lookup(ADDITIONAL_KV, categories[i]) != NULL) {
            int r = additional_rank(categories[i]);
            if (best == NULL || r < best_rank) {
                best = categories[i];
                best_rank = r;
            }
        }
    }
    if (best != NULL)
        return g_strdup(kv_lookup(ADDITIONAL_KV, best));
    /* 2. Recognised Main category (first in token order). */
    for (int i = 0; categories && categories[i]; i++) {
        const char *label = kv_lookup(MAIN_KV, categories[i]);
        if (label != NULL)
            return g_strdup(label);
    }
    /* 3. First non-noise, non-X- token, else generic. */
    for (int i = 0; categories && categories[i]; i++) {
        const char *t = categories[i];
        if (t[0] != '\0' && !in_list(NOISE, t) &&
            !(t[0] == 'X' && t[1] == '-'))
            return g_strdup(t);
    }
    return g_strdup(GENERIC_TYPE);
}

/* --- Apps hidden from OUR menu (not uninstalled) ------------------------- *
 * INSTALLER FIX: calamares.desktop (stock "Install System", generic icon,
 * Exec=pkexec calamares which is DEAD in this OpenBox session -- no polkit
 * agent) is now HIDDEN, and azzio-install.desktop (Name "Azzio Linux
 * Installer", Icon azzio-installer, passwordless-sudo Exec that works) is NO
 * LONGER hidden, so it shows in the menu and re-opening works. */
static const char *HIDDEN_IDS[] = {
    "azzio-application-menu.desktop",
    "azzio-application-menu-shortcut.desktop",
    "bssh.desktop",
    "bvnc.desktop",
    "avahi-discover.desktop",
    "calamares.desktop",          /* stock installer -- dead pkexec path, hide it */
    "cmake-gui.desktop",          /* "CMake" GUI -- dev tool riding in on cmake, not user-facing */
    "nvidia-settings.desktop",    /* "NVIDIA X Server Settings" -- driver tool, keep out of the menu */
    "kdesystemsettings.desktop",
    "lstopo.desktop",
    "htop.desktop",
    "lftp.desktop",
    "cups.desktop",
    "org.kde.kmenuedit.desktop",
    "assistant.desktop",
    "qdbusviewer.desktop",
    "linguist.desktop",
    "qv4l2.desktop",
    "qvidcap.desktop",
    "designer.desktop",
    "stoken-gui.desktop",
    "stoken-gui-small.desktop",
    "vim.desktop",
    NULL
};

gboolean az_is_hidden_desktop_id(const char *desktop_id) {
    return in_list(HIDDEN_IDS, desktop_id);
}

/* --- live-session detection + installer pin ------------------------------- *
 * On the archiso live medium the distro is not yet installed, so the installer
 * should be the first thing in the menu. archiso's init creates /run/archiso on
 * the live system (and it is absent once installed), which is the canonical, cheap
 * signal. AZZIO_FORCE_LIVE overrides it either way for testing (1/true = live,
 * 0/false = installed). */
gboolean az_is_live_session(void) {
    const char *force = g_getenv("AZZIO_FORCE_LIVE");
    if (force && force[0]) {
        char *low = g_ascii_strdown(force, -1);
        gboolean live = (strcmp(low, "1") == 0) || (strcmp(low, "true") == 0) ||
                        (strcmp(low, "yes") == 0);
        g_free(low);
        return live;
    }
    return g_file_test("/run/archiso", G_FILE_TEST_IS_DIR);
}

/* Move the entry with `desktop_id` to the front of `apps` (stable for the rest).
 * No-op if absent. Returns TRUE if a move happened.
 *
 * Uses g_ptr_array_steal_index, NOT _remove_index: the app arrays are created with a
 * free func (az_app_entry_free), and _remove_index would FREE the entry we are about
 * to re-insert, leaving a dangling pointer the app list then dereferences (crash).
 * _steal_index detaches without freeing, so the entry survives the re-insert. */
gboolean az_apps_pin_first(GPtrArray *apps, const char *desktop_id) {
    if (!apps || !desktop_id) return FALSE;
    for (guint i = 0; i < apps->len; i++) {
        AzAppEntry *e = g_ptr_array_index(apps, i);
        if (strcmp(e->desktop_id, desktop_id) == 0) {
            if (i == 0) return FALSE;
            g_ptr_array_steal_index(apps, i);       /* detach WITHOUT freeing */
            g_ptr_array_insert(apps, 0, e);
            return TRUE;
        }
    }
    return FALSE;
}

/* The .desktop id of the Azzio installer, pinned to the top in a live session. */
const char *az_installer_desktop_id(void) {
    return "azzio-install.desktop";
}

/* --- XDG application dirs (most-specific first) -------------------------- */
static GPtrArray *app_dirs(void) {
    GPtrArray *dirs = g_ptr_array_new_with_free_func(g_free);
    GHashTable *seen = g_hash_table_new(g_str_hash, g_str_equal);

    const char *xdg_data_home = g_getenv("XDG_DATA_HOME");
    char *home_apps;
    if (xdg_data_home && xdg_data_home[0])
        home_apps = g_build_filename(xdg_data_home, "applications", NULL);
    else
        home_apps = g_build_filename(g_get_home_dir(), ".local", "share",
                                     "applications", NULL);
    g_ptr_array_add(dirs, home_apps);
    g_hash_table_add(seen, home_apps);

    const char *xdg_data = g_getenv("XDG_DATA_DIRS");
    if (!xdg_data || !xdg_data[0])
        xdg_data = "/usr/local/share:/usr/share";
    char **bases = g_strsplit(xdg_data, ":", -1);
    for (int i = 0; bases[i]; i++) {
        char *base = g_strstrip(bases[i]);
        if (base[0] == '\0')
            continue;
        char *d = g_build_filename(base, "applications", NULL);
        if (!g_hash_table_contains(seen, d)) {
            g_ptr_array_add(dirs, d);
            g_hash_table_add(seen, d);
        } else {
            g_free(d);
        }
    }
    g_strfreev(bases);
    g_hash_table_destroy(seen);
    return dirs;
}

/* Strip standalone %f %u %U %i %c %k field codes; shell-split the Exec line. */
static char **strip_field_codes(const char *exec_str) {
    int argc = 0;
    char **parts = NULL;
    GError *err = NULL;
    if (!g_shell_parse_argv(exec_str, &argc, &parts, &err)) {
        if (err) g_error_free(err);
        parts = g_strsplit_set(exec_str, " \t", -1);
        argc = (int)g_strv_length(parts);
    }
    GPtrArray *out = g_ptr_array_new();
    for (int i = 0; parts && parts[i]; i++) {
        const char *p = parts[i];
        if (strlen(p) == 2 && p[0] == '%')
            continue;
        g_ptr_array_add(out, g_strdup(p));
    }
    g_ptr_array_add(out, NULL);
    g_strfreev(parts);
    return (char **)g_ptr_array_free(out, FALSE);
}

/* Parse one .desktop file into an AzAppEntry, or NULL if it must not show. */
static AzAppEntry *parse_desktop_file(const char *path) {
    GKeyFile *kf = g_key_file_new();
    GError *err = NULL;
    if (!g_key_file_load_from_file(kf, path, G_KEY_FILE_NONE, &err)) {
        if (err) g_error_free(err);
        g_key_file_free(kf);
        return NULL;
    }
    const char *GROUP = "Desktop Entry";

    /* Type defaults to Application when absent. */
    char *type = g_key_file_get_string(kf, GROUP, "Type", NULL);
    gboolean is_app = (type == NULL) || (strcmp(type, "Application") == 0);
    g_free(type);
    if (!is_app) { g_key_file_free(kf); return NULL; }

    char *nodisplay = g_key_file_get_string(kf, GROUP, "NoDisplay", NULL);
    if (nodisplay) {
        char *low = g_ascii_strdown(nodisplay, -1);
        gboolean hide = (strcmp(low, "true") == 0);
        g_free(low); g_free(nodisplay);
        if (hide) { g_key_file_free(kf); return NULL; }
    }
    char *hidden = g_key_file_get_string(kf, GROUP, "Hidden", NULL);
    if (hidden) {
        char *low = g_ascii_strdown(hidden, -1);
        gboolean hide = (strcmp(low, "true") == 0);
        g_free(low); g_free(hidden);
        if (hide) { g_key_file_free(kf); return NULL; }
    }

    char *name = g_key_file_get_string(kf, GROUP, "Name", NULL);
    char *exec_str = g_key_file_get_string(kf, GROUP, "Exec", NULL);
    if (name) g_strstrip(name);
    if (exec_str) g_strstrip(exec_str);
    if (!name || !name[0] || !exec_str || !exec_str[0]) {
        g_free(name); g_free(exec_str); g_key_file_free(kf);
        return NULL;
    }
    char **argv = strip_field_codes(exec_str);
    if (!argv || !argv[0]) {
        g_free(name); g_free(exec_str); g_key_file_free(kf);
        if (argv) g_strfreev(argv);
        return NULL;
    }

    char *cats_raw = g_key_file_get_string(kf, GROUP, "Categories", NULL);
    char **cats;
    if (cats_raw && cats_raw[0]) {
        /* Split on ';' and drop empties (freedesktop trailing ';'). */
        char **all = g_strsplit(cats_raw, ";", -1);
        GPtrArray *keep = g_ptr_array_new();
        for (int i = 0; all[i]; i++)
            if (all[i][0] != '\0')
                g_ptr_array_add(keep, g_strdup(all[i]));
        g_ptr_array_add(keep, NULL);
        cats = (char **)g_ptr_array_free(keep, FALSE);
        g_strfreev(all);
    } else {
        cats = g_new0(char *, 1);
    }
    g_free(cats_raw);

    AzAppEntry *e = g_new0(AzAppEntry, 1);
    e->name = name;
    e->type_label = az_category_type(cats);
    e->exec_argv = argv;
    char *icon = g_key_file_get_string(kf, GROUP, "Icon", NULL);
    e->icon = icon ? g_strstrip(icon) : g_strdup("");
    char *comment = g_key_file_get_string(kf, GROUP, "Comment", NULL);
    e->comment = comment ? g_strstrip(comment) : g_strdup("");
    e->desktop_id = g_path_get_basename(path);
    char *swc = g_key_file_get_string(kf, GROUP, "StartupWMClass", NULL);
    e->startup_wmclass = swc ? g_strstrip(swc) : g_strdup("");

    g_strfreev(cats);
    g_free(exec_str);
    g_key_file_free(kf);
    return e;
}

void az_app_entry_free(AzAppEntry *e) {
    if (!e) return;
    g_free(e->name);
    g_free(e->type_label);
    g_strfreev(e->exec_argv);
    g_free(e->icon);
    g_free(e->comment);
    g_free(e->desktop_id);
    g_free(e->startup_wmclass);
    g_free(e);
}

static gint cmp_entry_name(gconstpointer a, gconstpointer b) {
    const AzAppEntry *ea = *(const AzAppEntry *const *)a;
    const AzAppEntry *eb = *(const AzAppEntry *const *)b;
    char *ca = g_utf8_casefold(ea->name, -1);
    char *cb = g_utf8_casefold(eb->name, -1);
    gint r = g_strcmp0(ca, cb);
    g_free(ca); g_free(cb);
    return r;
}

/* Sort helper for a GPtrArray of char* (g_ptr_array_sort passes char**). */
static gint cmp_strp(gconstpointer a, gconstpointer b) {
    return strcmp(*(const char *const *)a, *(const char *const *)b);
}

GPtrArray *az_scan_applications(void) {
    GPtrArray *dirs = app_dirs();
    /* desktop_id (owned by entry) -> AzAppEntry*; keep FIRST id seen. */
    GHashTable *by_id = g_hash_table_new(g_str_hash, g_str_equal);
    GPtrArray *entries = g_ptr_array_new_with_free_func(
        (GDestroyNotify)az_app_entry_free);

    for (guint di = 0; di < dirs->len; di++) {
        const char *d = g_ptr_array_index(dirs, di);
        GError *err = NULL;
        GDir *dir = g_dir_open(d, 0, &err);
        if (!dir) { if (err) g_error_free(err); continue; }

        /* Collect + sort filenames (applications.py: sorted(os.listdir(d)) -> byte/
         * codepoint order, NOT casefolded). */
        GPtrArray *names = g_ptr_array_new_with_free_func(g_free);
        const char *fn;
        while ((fn = g_dir_read_name(dir)) != NULL)
            g_ptr_array_add(names, g_strdup(fn));
        g_dir_close(dir);
        g_ptr_array_sort(names, cmp_strp);

        for (guint ni = 0; ni < names->len; ni++) {
            const char *name = g_ptr_array_index(names, ni);
            if (!g_str_has_suffix(name, ".desktop"))
                continue;
            if (g_hash_table_contains(by_id, name))
                continue;                 /* higher-precedence dir won */
            if (az_is_hidden_desktop_id(name))
                continue;                 /* hidden from our menu */
            char *path = g_build_filename(d, name, NULL);
            AzAppEntry *e = parse_desktop_file(path);
            g_free(path);
            if (e != NULL) {
                g_hash_table_add(by_id, e->desktop_id);
                g_ptr_array_add(entries, e);
            }
        }
        g_ptr_array_free(names, TRUE);
    }

    g_hash_table_destroy(by_id);
    g_ptr_array_free(dirs, TRUE);
    g_ptr_array_sort(entries, cmp_entry_name);
    return entries;
}
