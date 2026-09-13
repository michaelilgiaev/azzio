/* Azzio -- headless C unit tests for the bare-`azzio` terminal user interface MODEL.
 *
 * The UI's menu tree + the search filter + the wallpaper path are pure data/logic in
 * model.c (no terminal), so we exercise them directly here -- the C counterpart of the old
 * Python build_menu()/filter_items tests. No X, no kitty, no ncurses.
 *
 * We deliberately test az_row_matches only with queries that hit the LABEL (or the empty
 * query), both of which short-circuit BEFORE the live status probe -- so the tests never
 * fork nmcli/ufw and stay deterministic on any host. tests/Makefile compiles this against
 * the shipping model.c.
 */
#include "terminal_user_interface.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int failures = 0;
#define CHECK(cond) do { \
    if (!(cond)) { printf("FAIL: %s (line %d)\n", #cond, __LINE__); failures++; } \
} while (0)

/* The top-level subsystems, in order (Network FIRST per the spec): Network, Theme, Wallpaper,
 * then the media controls Volume + Brightness (the follow-up spec added these -- they were
 * missing from the UI), then Machine Type (the PC/Laptop screen), then Hypervisor (the
 * per-directory VM runner's global install defaults), then Backup (the opt-in backup targets,
 * LAST), and nothing else. */
static void test_top_level_is_network_theme_wallpaper(void)
{
    const AzScreen *m = az_screen_find("main");
    CHECK(m != NULL);
    CHECK(m->nrows == 14);
    CHECK(strcmp(m->rows[0].label, "Network") == 0);   /* Network is the first option */
    CHECK(strcmp(m->rows[1].label, "Theme") == 0);
    CHECK(strcmp(m->rows[2].label, "Wallpaper") == 0);
    CHECK(strcmp(m->rows[3].label, "Volume") == 0);
    CHECK(strcmp(m->rows[4].label, "Brightness") == 0);
    CHECK(strcmp(m->rows[5].label, "Default Applications") == 0);
    CHECK(strcmp(m->rows[6].label, "Display") == 0);
    CHECK(strcmp(m->rows[7].label, "GPU") == 0);
    CHECK(strcmp(m->rows[8].label, "Machine Type") == 0);
    CHECK(strcmp(m->rows[9].label, "Time & Date") == 0);
    CHECK(strcmp(m->rows[10].label, "Language") == 0);
    CHECK(strcmp(m->rows[11].label, "Hypervisor") == 0); /* per-directory VM defaults */
    CHECK(strcmp(m->rows[12].label, "Power") == 0);      /* shutdown/restart/sleep/lock + timers */
    CHECK(strcmp(m->rows[13].label, "Backup") == 0);    /* opt-in backup entry, still last */
    /* the entry title is the (re)named "Azzio Settings" */
    CHECK(strcmp(m->title, "Azzio Settings") == 0);
}

/* Exactly the subsystems + the network sub-screens + the volume/brightness + the machine screen
 * are reachable -- no extras. */
static void test_screen_set_is_exactly_expected(void)
{
    const char *want[] = {
        "main", "theme", "wallpaper", "network",
        "network.wifi", "network.wired", "network.ip", "network.bluetooth",
        "network.airplane", "network.firewall", "network.ssh",
        "volume", "brightness", "machine", "timedate", "language", "hypervisor",
        "power", "backup",
        /* Default Applications: the category list + one screen per category (Mail excluded --
         * no mail client shipped, so the TUI does not surface it). */
        "defaultapps",
        "defaultapps.web", "defaultapps.html", "defaultapps.music", "defaultapps.video",
        "defaultapps.photos", "defaultapps.word", "defaultapps.spreadsheet", "defaultapps.pdf",
        "defaultapps.source-code", "defaultapps.file-manager", "defaultapps.plain-text",
        "defaultapps.calculator", "defaultapps.terminal",
        /* Display: the screen + the scale chooser + the xrandr feature screens. */
        "display", "display.scale", "display.resolution", "display.refresh",
        "display.orientation", "display.monitors", "gpu",
    };
    /* az_screen_count() counts only the STATIC SCREENS[] table. The 13 per-category
     * "defaultapps.<key>" screens are built at RUNTIME (az_da_screen), so they are findable but
     * NOT part of the static count: the static count is the want[] size minus those 13. */
    const int DYNAMIC_DEFAULTAPPS_SCREENS = 13;
    int n = az_screen_count();
    CHECK(n == (int)(sizeof want / sizeof want[0]) - DYNAMIC_DEFAULTAPPS_SCREENS);
    /* every listed screen -- static AND runtime-built -- must resolve via az_screen_find. */
    for (size_t i = 0; i < sizeof want / sizeof want[0]; i++)
        CHECK(az_screen_find(want[i]) != NULL);
    CHECK(az_screen_find("nonesuch") == NULL);
}

/* Volume + Brightness screens (the follow-up spec: "I don't see Volume and Brightness settings,
 * that should be there"). Volume shows the live level via a screen-level Current: probe and its
 * rows set a PRECISE level (or step/mute) via `azzio volume ...`. Brightness is the same but
 * LAPTOP-ONLY (its Current: probe reports PC vs Laptop). Neither needs sudo (the user session
 * owns audio/backlight), and neither echoes a per-row status (Current: shows it once). */
static void test_volume_and_brightness_screens(void)
{
    const AzScreen *v = az_screen_find("volume");
    CHECK(v != NULL);
    CHECK(strcmp(v->title, "Volume") == 0);
    CHECK(v->current == az_status_volume);          /* live level shown once, up top */
    CHECK(v->nrows >= 5);
    int has_mute = 0, has_set50 = 0, has_set100 = 0;
    for (int i = 0; i < v->nrows; i++) {
        CHECK(v->rows[i].kind == AZ_ACT_APPLY);
        CHECK(v->rows[i].needs_root == 0);          /* PipeWire/ALSA run in the user session */
        CHECK(v->rows[i].status == NULL);           /* no per-row echo (Current: shows it) */
        if (strcmp(v->rows[i].target, "azzio volume mute") == 0) has_mute = 1;
        if (strcmp(v->rows[i].target, "azzio volume set 50") == 0) has_set50 = 1;
        if (strcmp(v->rows[i].target, "azzio volume set 100") == 0) has_set100 = 1;
    }
    CHECK(has_mute == 1);
    CHECK(has_set50 == 1);
    CHECK(has_set100 == 1);

    const AzScreen *b = az_screen_find("brightness");
    CHECK(b != NULL);
    CHECK(strcmp(b->title, "Brightness") == 0);
    CHECK(b->current == az_status_brightness);      /* PC vs Laptop / the level, shown once */
    CHECK(b->nrows >= 4);
    int has_bset100 = 0;
    for (int i = 0; i < b->nrows; i++) {
        CHECK(b->rows[i].kind == AZ_ACT_APPLY);
        CHECK(b->rows[i].needs_root == 0);
        CHECK(b->rows[i].status == NULL);
        if (strcmp(b->rows[i].target, "azzio brightness set 100") == 0) has_bset100 = 1;
    }
    CHECK(has_bset100 == 1);

    /* the main-menu rows that descend here carry the live level as their at-a-glance summary */
    const AzScreen *main_s = az_screen_find("main");
    CHECK(main_s->rows[3].status == az_status_volume);
    CHECK(strcmp(main_s->rows[3].target, "volume") == 0);
    CHECK(main_s->rows[4].status == az_status_brightness);
    CHECK(strcmp(main_s->rows[4].target, "brightness") == 0);
}

/* The Machine Type screen: it shows the recognised type ONCE via a screen-level `.current`
 * probe (PC/Laptop), and its rows HARD-SWITCH the type -- Force PC / Force Laptop / Autodetect
 * -- each an apply that runs `azzio machine ...` (no sudo: it writes the user's own pointer).
 * This backs the spec's "add Machine Type ... display what it recognizes ... allow a hard
 * switch." */
static void test_machine_type_screen(void)
{
    const AzScreen *m = az_screen_find("machine");
    CHECK(m != NULL);
    CHECK(strcmp(m->title, "Machine Type") == 0);
    /* the recognised type is shown once, up top (a screen-level Current: probe) */
    CHECK(m->current == az_status_machine);
    /* three hard-switch rows: force PC, force Laptop, autodetect */
    CHECK(m->nrows == 3);
    CHECK(strcmp(m->rows[0].target, "azzio machine --pc") == 0);
    CHECK(strcmp(m->rows[1].target, "azzio machine --laptop") == 0);
    CHECK(strcmp(m->rows[2].target, "azzio machine --auto") == 0);
    for (int i = 0; i < m->nrows; i++) {
        CHECK(m->rows[i].kind == AZ_ACT_APPLY);
        CHECK(m->rows[i].needs_root == 0);       /* writes the user's own config, no sudo */
        CHECK(m->rows[i].status == NULL);        /* no per-row echo (Current: shows it once) */
    }
    /* the main-menu row that descends here carries the machine status as its at-a-glance summary
     * (Machine Type is now the EIGHTH row, after Volume, Brightness, Default Applications AND
     * Display were added before it) */
    const AzScreen *main_s = az_screen_find("main");
    CHECK(main_s->rows[8].status == az_status_machine);
    CHECK(main_s->rows[8].kind == AZ_ACT_SCREEN);
    CHECK(strcmp(main_s->rows[8].target, "machine") == 0);
}

/* The Backup screen (step six): a "Backup" entry on ROWS_MAIN opens a screen that drives the
 * SAME opt-in flow `azzio backup --configure` exposes, streamlined + OFF BY DEFAULT. Its
 * "Current:" line is az_status_backup ("off (local only)" by default). The four rows: two
 * non-interactive APPLIES (--status / --disable) and two AZ_ACT_PROMPT enable rows (USB / Google
 * Drive) that prompt for the path/remote and run the non-interactive --enable-* surface. Every
 * row is captured in-UI (show_output) and carries a Base Command + Azzio Wrapper hint; none needs
 * sudo (the configurator writes the user's own config). */
static void test_backup_screen(void)
{
    /* the ROWS_MAIN entry that opens it -- LAST row (index 13 now, after Power), with the
     * target-summary status */
    const AzScreen *main_s = az_screen_find("main");
    CHECK(strcmp(main_s->rows[13].label, "Backup") == 0);
    CHECK(main_s->rows[13].kind == AZ_ACT_SCREEN);
    CHECK(strcmp(main_s->rows[13].target, "backup") == 0);
    CHECK(main_s->rows[13].status == az_status_backup);

    const AzScreen *b = az_screen_find("backup");
    CHECK(b != NULL);
    CHECK(strcmp(b->title, "Backup") == 0);
    /* the screen shows the live target state ONCE via a screen-level Current: probe */
    CHECK(b->current == az_status_backup);
    /* the subtitle EXPLAINS the feature: off by default; local archives always happen; this only
     * opts in to a USB / Google Drive copy. */
    CHECK(strstr(b->subtitle, "Off by default") != NULL);
    CHECK(strstr(b->subtitle, "local") != NULL);
    CHECK(strstr(b->subtitle, "USB") != NULL && strstr(b->subtitle, "Google Drive") != NULL);
    CHECK(b->nrows == 4);

    int has_status = 0, has_disable = 0, has_enable_usb = 0, has_enable_gdrive = 0;
    for (int i = 0; i < b->nrows; i++) {
        const AzRow *r = &b->rows[i];
        CHECK(r->needs_root == 0);        /* the configurator writes the user's own config, no sudo */
        CHECK(r->show_output == 1);       /* every row shows its captured result in the overlay */
        /* every row carries BOTH hint lines: an azzio wrapper AND a base command (no bare row) */
        CHECK(az_row_command(r) != NULL);
        CHECK(az_row_base(r) != NULL);
        if (r->kind == AZ_ACT_APPLY &&
            strcmp(r->target, "azzio backup --configure --status") == 0) {
            has_status = 1;
        }
        if (r->kind == AZ_ACT_APPLY &&
            strcmp(r->target, "azzio backup --configure --disable") == 0) {
            has_disable = 1;
        }
        if (r->kind == AZ_ACT_PROMPT &&
            strcmp(r->target, "azzio backup --configure --enable-usb") == 0) {
            has_enable_usb = 1;
            CHECK(r->prompt != NULL);                          /* asks for the mount path */
            CHECK(strstr(az_row_command(r), "<value>") != NULL); /* wrapper has the placeholder */
            CHECK(strstr(az_row_base(r), "<value>") != NULL);    /* base too (cp ... <value>) */
            CHECK(strstr(az_row_base(r), "cp ") != NULL);        /* the real copy backup does */
        }
        if (r->kind == AZ_ACT_PROMPT &&
            strcmp(r->target, "azzio backup --configure --enable-gdrive") == 0) {
            has_enable_gdrive = 1;
            CHECK(r->prompt != NULL);                          /* asks for the remote name */
            CHECK(strstr(az_row_command(r), "<value>") != NULL);
            CHECK(strstr(az_row_base(r), "rclone copy") != NULL); /* the real rclone copy */
        }
    }
    CHECK(has_status == 1);
    CHECK(has_disable == 1);
    CHECK(has_enable_usb == 1);
    CHECK(has_enable_gdrive == 1);
}

/* The Hypervisor screen: a "Hypervisor" entry on ROWS_MAIN (just before Backup) opens a screen
 * that manages the GLOBAL defaults every NEW `hypervisor install` starts from -- it drives the
 * non-interactive `hypervisor --configure` surface (--status / --reset / --set KEY VALUE). Its
 * "Current:" line is az_status_hypervisor (a short ram/cpus/disk/net summary). The rows: a --status
 * APPLY, a --reset APPLY, and several AZ_ACT_PROMPT --set rows (ram/cpus/disk_size/network/audio)
 * that prompt for the value and append it. Every row is captured in-UI and carries a Base + Wrapper
 * hint; none needs sudo (the defaults file is the user's own ~/.config/azzio-hypervisor). */
static void test_hypervisor_screen(void)
{
    /* the ROWS_MAIN entry that opens it -- row 11, just before Backup, with the summary status */
    const AzScreen *main_s = az_screen_find("main");
    CHECK(strcmp(main_s->rows[11].label, "Hypervisor") == 0);
    CHECK(main_s->rows[11].kind == AZ_ACT_SCREEN);
    CHECK(strcmp(main_s->rows[11].target, "hypervisor") == 0);
    CHECK(main_s->rows[11].status == az_status_hypervisor);

    const AzScreen *h = az_screen_find("hypervisor");
    CHECK(h != NULL);
    CHECK(strcmp(h->title, "Hypervisor") == 0);
    CHECK(h->current == az_status_hypervisor);
    /* the subtitle EXPLAINS that these are defaults for NEW VMs and a dir's own cfg still wins */
    CHECK(strstr(h->subtitle, "default") != NULL);
    CHECK(strstr(h->subtitle, "hypervisor.cfg") != NULL);
    CHECK(h->nrows >= 4);

    int has_status = 0, has_reset = 0, has_set_ram = 0, has_set_network = 0;
    for (int i = 0; i < h->nrows; i++) {
        const AzRow *r = &h->rows[i];
        CHECK(r->needs_root == 0);        /* the user's own config file, no sudo */
        /* every row carries BOTH hint lines: an azzio wrapper AND a base command (no bare row) */
        CHECK(az_row_command(r) != NULL);
        CHECK(az_row_base(r) != NULL);
        if (r->kind == AZ_ACT_APPLY &&
            strcmp(r->target, "hypervisor --configure --status") == 0) {
            has_status = 1;
            CHECK(r->show_output == 1);   /* the status dump lands in the overlay */
        }
        if (r->kind == AZ_ACT_APPLY &&
            strcmp(r->target, "hypervisor --configure --reset") == 0) {
            has_reset = 1;
        }
        if (r->kind == AZ_ACT_PROMPT &&
            strcmp(r->target, "hypervisor --configure --set ram") == 0) {
            has_set_ram = 1;
            CHECK(r->prompt != NULL);                              /* asks for the value */
            CHECK(strstr(az_row_command(r), "<value>") != NULL);   /* wrapper placeholder */
            CHECK(strstr(az_row_base(r), "<value>") != NULL);      /* base placeholder too */
        }
        if (r->kind == AZ_ACT_PROMPT &&
            strcmp(r->target, "hypervisor --configure --set network") == 0) {
            has_set_network = 1;
            CHECK(r->prompt != NULL);
        }
    }
    CHECK(has_status == 1);
    CHECK(has_reset == 1);
    CHECK(has_set_ram == 1);
    CHECK(has_set_network == 1);
}

/* Default Applications: a category list + one screen per category, each letting the user CHANGE
 * that category's default via an `azzio default-applications set ...` apply. The category set,
 * keys and the current-handler probes are the TUI half of the default_applications.py source;
 * a Python test pins the labels/keys against that source so C and Python cannot drift. */
/* Write a stub .desktop (declaring the given MimeType) into <dir>. Used by the fixture so the
 * per-category candidate resolution (az_da_screen scans the XDG dirs for installed .desktop
 * files) is DETERMINISTIC on any host -- it must not depend on what happens to be installed on
 * the machine running the tests. */
static void write_desktop(const char *dir, const char *id, const char *mimetype)
{
    char path[1024];
    snprintf(path, sizeof path, "%s/%s", dir, id);
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "[Desktop Entry]\nType=Application\nName=%s\n", id);
    if (mimetype && mimetype[0]) fprintf(f, "MimeType=%s;\n", mimetype);
    fclose(f);
}

/* Point XDG_DATA_HOME/XDG_DATA_DIRS at a private temp dir seeded with EXACTLY the .desktop files
 * the assertions below expect (the shipped curated seeds for Web/Photos/... plus firefox as the
 * MIME-discovered extra), so the live resolver produces a known, host-independent candidate set.
 * Returns the applications dir path (into `out`). */
static void seed_desktop_fixture(char *out, size_t n)
{
    char tmpl[] = "/tmp/azdatestXXXXXX";
    char *base = mkdtemp(tmpl);
    if (!base) { out[0] = '\0'; return; }
    char apps[1024];
    snprintf(apps, sizeof apps, "%s/applications", base);
    mkdir(apps, 0755);
    /* the curated seeds the category tests assert (each with the category's representative MIME
     * so it would ALSO satisfy the MIME-discovery path). */
    write_desktop(apps, "librewolf.desktop", "x-scheme-handler/http;text/html;application/pdf");
    write_desktop(apps, "xviewer.desktop", "image/png;image/jpeg");
    write_desktop(apps, "gimp.desktop", "image/png;image/jpeg");
    write_desktop(apps, "feh.desktop", "image/png;image/jpeg");
    write_desktop(apps, "org.gnome.gedit.desktop", "text/plain;text/html");
    write_desktop(apps, "vlc.desktop", "audio/mpeg;video/mp4");
    write_desktop(apps, "kitty.desktop", "");
    /* a MIME-discovered extra that is NOT in any curated seed: firefox declares the http scheme,
     * so it must surface under Web (the self-resolving behaviour) purely by its MimeType. */
    write_desktop(apps, "firefox.desktop", "x-scheme-handler/http;text/html");
    setenv("XDG_DATA_HOME", base, 1);
    setenv("XDG_DATA_DIRS", base, 1);   /* only our fixture -> deterministic candidate set */
    snprintf(out, n, "%s", apps);
}

static void test_default_applications_screens(void)
{
    char apps[1024];
    seed_desktop_fixture(apps, sizeof apps);

    /* the ROWS_MAIN entry that opens it */
    const AzScreen *main_s = az_screen_find("main");
    CHECK(strcmp(main_s->rows[5].label, "Default Applications") == 0);
    CHECK(main_s->rows[5].kind == AZ_ACT_SCREEN);
    CHECK(strcmp(main_s->rows[5].target, "defaultapps") == 0);

    /* the category list screen: 13 categories (Mail excluded), each a SCREEN row with a live
     * current-handler status. */
    const AzScreen *da = az_screen_find("defaultapps");
    CHECK(da != NULL);
    CHECK(strcmp(da->title, "Default Applications") == 0);
    CHECK(da->nrows == 13);
    const char *cats[] = {
        "Web", "HTML", "Music", "Video", "Photos", "Word", "Spreadsheet", "PDF",
        "Source Code", "File Manager", "Plain Text", "Calculator", "Terminal",
    };
    for (int i = 0; i < da->nrows; i++) {
        CHECK(da->rows[i].kind == AZ_ACT_SCREEN);
        CHECK(da->rows[i].status != NULL);          /* shows the live handler at a glance */
        CHECK(strcmp(da->rows[i].label, cats[i]) == 0);
    }
    /* Mail is NOT a category screen (no mail client shipped). */
    CHECK(az_screen_find("defaultapps.mail") == NULL);

    /* each category screen shows the current handler up top and CHANGES it via an apply that
     * runs `azzio default-applications set ...` -- no sudo (writes the user's own config). */
    const AzScreen *web = az_screen_find("defaultapps.web");
    CHECK(web != NULL);
    /* the per-category screen discloses the .desktop drop-in dir TERSELY (user request): just
     * ".desktop directory: <dir>/" (trailing slash), rendered in the accent -- NOT the old wordy
     * "To add or override an app, drop its .desktop into ... (the list below resolves ...)". */
    CHECK(strstr(web->subtitle, ".desktop directory: ~/.local/share/applications/") != NULL);
    CHECK(strstr(web->subtitle, "drop its .desktop into") == NULL);
    CHECK(web->subtitle_accent == 1);
    CHECK(web->current == az_status_da_web);
    CHECK(web->nrows >= 1);
    CHECK(web->rows[0].kind == AZ_ACT_APPLY);
    CHECK(web->rows[0].needs_root == 0);
    CHECK(strncmp(web->rows[0].target, "azzio default-applications set web ",
                  strlen("azzio default-applications set web ")) == 0);
    /* SELF-RESOLVING (the load-bearing behaviour): the curated seed (librewolf) comes FIRST, and
     * an app that is NOT curated but declares the category's MIME (firefox: x-scheme-handler/http)
     * SURFACES purely from being installed -- exactly "install Firefox and it appears; remove it
     * and it disappears" -- WITHOUT firefox being hard-listed. The fixture seeds both. */
    CHECK(strstr(web->rows[0].target, "librewolf.desktop") != NULL);   /* curated seed first */
    int web_has_firefox = 0, web_has_librewolf = 0;
    for (int i = 0; i < web->nrows; i++) {
        if (strstr(web->rows[i].target, "firefox.desktop")) web_has_firefox = 1;
        if (strstr(web->rows[i].target, "librewolf.desktop")) web_has_librewolf = 1;
    }
    CHECK(web_has_librewolf == 1);
    CHECK(web_has_firefox == 1);   /* MIME-discovered, proving live resolution */

    /* a multi-candidate category (Photos: xviewer + gimp + feh) really offers all choices. */
    const AzScreen *ph = az_screen_find("defaultapps.photos");
    CHECK(ph != NULL);
    CHECK(ph->current == az_status_da_photos);
    CHECK(ph->nrows == 3);
    int has_xviewer = 0, has_gimp = 0, has_feh = 0;
    for (int i = 0; i < ph->nrows; i++) {
        if (strstr(ph->rows[i].target, "xviewer.desktop")) has_xviewer = 1;
        if (strstr(ph->rows[i].target, "gimp.desktop")) has_gimp = 1;
        if (strstr(ph->rows[i].target, "feh.desktop")) has_feh = 1;
    }
    CHECK(has_xviewer == 1);
    CHECK(has_gimp == 1);
    CHECK(has_feh == 1);
}

/* Display: cinnamon-settings-display parity (xrandr) + the GLOBAL SCALE chooser. The scale
 * chooser is the firm requirement; its rows set the ONE scale via `azzio display scale`. */
static void test_display_screens(void)
{
    /* the ROWS_MAIN entry */
    const AzScreen *main_s = az_screen_find("main");
    CHECK(strcmp(main_s->rows[6].label, "Display") == 0);
    CHECK(main_s->rows[6].kind == AZ_ACT_SCREEN);
    CHECK(strcmp(main_s->rows[6].target, "display") == 0);
    CHECK(main_s->rows[6].status == az_status_display);

    const AzScreen *d = az_screen_find("display");
    CHECK(d != NULL);
    CHECK(strcmp(d->title, "Display") == 0);
    /* the top "Current: scale 1.35x" line was REMOVED at the user's request: the display screen
     * has NO .current, and each row shows its OWN current value inline via .status instead. */
    CHECK(d->current == NULL);
    /* the feature set: Global Scale + resolution/refresh/orientation/monitors, EACH with an
     * inline current-value probe (.status). */
    int has_scale = 0, has_res = 0, has_refresh = 0, has_orient = 0, has_mon = 0;
    for (int i = 0; i < d->nrows; i++) {
        CHECK(d->rows[i].status != NULL);   /* every display row shows its current value inline */
        if (strcmp(d->rows[i].target, "display.scale") == 0)
            { has_scale = 1; CHECK(d->rows[i].status == az_status_display_scale); }
        if (strcmp(d->rows[i].target, "display.resolution") == 0)
            { has_res = 1; CHECK(d->rows[i].status == az_status_display_resolution); }
        if (strcmp(d->rows[i].target, "display.refresh") == 0)
            { has_refresh = 1; CHECK(d->rows[i].status == az_status_display_refresh); }
        if (strcmp(d->rows[i].target, "display.orientation") == 0)
            { has_orient = 1; CHECK(d->rows[i].status == az_status_display_orientation); }
        if (strcmp(d->rows[i].target, "display.monitors") == 0)
            { has_mon = 1; CHECK(d->rows[i].status == az_status_display_monitors); }
    }
    CHECK(has_scale && has_res && has_refresh && has_orient && has_mon);

    /* the GLOBAL SCALE chooser: offers the scale options, each an apply, none needing sudo. */
    const AzScreen *sc = az_screen_find("display.scale");
    CHECK(sc != NULL);
    CHECK(sc->current == az_status_display_scale);
    CHECK(sc->nrows == 6);                       /* 1.00 .. 2.00 */
    int has_135 = 0, has_100 = 0, has_200 = 0;
    for (int i = 0; i < sc->nrows; i++) {
        CHECK(sc->rows[i].kind == AZ_ACT_APPLY);
        CHECK(sc->rows[i].needs_root == 0);      /* the X resource DB is per-session, no sudo */
        if (strcmp(sc->rows[i].target, "azzio display scale 1.35") == 0) has_135 = 1;
        if (strcmp(sc->rows[i].target, "azzio display scale 1.00") == 0) has_100 = 1;
        if (strcmp(sc->rows[i].target, "azzio display scale 2.00") == 0) has_200 = 1;
    }
    CHECK(has_135 && has_100 && has_200);

    /* orientation offers the four rotations. */
    const AzScreen *ori = az_screen_find("display.orientation");
    CHECK(ori != NULL);
    int has_normal = 0, has_left = 0, has_right = 0, has_inv = 0;
    for (int i = 0; i < ori->nrows; i++) {
        if (strstr(ori->rows[i].target, "rotate normal")) has_normal = 1;
        if (strstr(ori->rows[i].target, "rotate left")) has_left = 1;
        if (strstr(ori->rows[i].target, "rotate right")) has_right = 1;
        if (strstr(ori->rows[i].target, "rotate inverted")) has_inv = 1;
    }
    CHECK(has_normal && has_left && has_right && has_inv);
}

/* Network rows all DESCEND into a real child screen (they are navigation, not applies). */
static void test_network_rows_descend(void)
{
    const AzScreen *net = az_screen_find("network");
    CHECK(net != NULL);
    for (int i = 0; i < net->nrows; i++) {
        CHECK(net->rows[i].kind == AZ_ACT_SCREEN);
        CHECK(az_screen_find(net->rows[i].target) != NULL);
    }
}

/* IP Address screen: the live twin of the Calamares installer "Network" page (static IPv4 vs
 * DHCP). It hangs off the Network parent, shows the active address ONCE via az_status_ip, has a
 * plain "Show" read (no root) and two AZ_ACT_PROMPT setters wrapping `azzio network ip
 * static|dynamic` (needs_root; they edit the NM connection). The setters teach a "<value>"
 * placeholder in BOTH the wrapper and the base command, like the firewall port / backup enable
 * rows. This backs the user's "the terminal UI must also have these [network] settings". */
static void test_ip_address_screen(void)
{
    /* Reachable from the Network parent, and the parent row carries the live status. */
    const AzScreen *net = az_screen_find("network");
    CHECK(net != NULL);
    int parent_has_ip = 0;
    for (int i = 0; i < net->nrows; i++)
        if (strcmp(net->rows[i].target, "network.ip") == 0) {
            parent_has_ip = 1;
            CHECK(net->rows[i].kind == AZ_ACT_SCREEN);
            CHECK(strcmp(net->rows[i].label, "IP Address") == 0);
            CHECK(net->rows[i].status == az_status_ip);   /* at-a-glance summary on the parent */
        }
    CHECK(parent_has_ip == 1);

    const AzScreen *ip = az_screen_find("network.ip");
    CHECK(ip != NULL);
    CHECK(strcmp(ip->title, "IP Address") == 0);
    CHECK(ip->current == az_status_ip);                   /* active address shown once, up top */
    CHECK(ip->nrows >= 3);

    int has_show = 0, has_static = 0, has_dynamic = 0;
    for (int i = 0; i < ip->nrows; i++) {
        const AzRow *r = &ip->rows[i];
        CHECK(r->status == NULL);                         /* no per-row echo (Current: shows it) */
        if (strcmp(r->target, "azzio network ip show") == 0) {
            has_show = 1;
            CHECK(r->kind == AZ_ACT_APPLY);
            CHECK(r->needs_root == 0);                    /* a read: no sudo */
            CHECK(r->show_output == 1);                   /* the table lands in the overlay */
        }
        if (strcmp(r->target, "azzio network ip static") == 0) {
            has_static = 1;
            CHECK(r->kind == AZ_ACT_PROMPT);              /* type the iface/addr/gw/dns line */
            CHECK(r->needs_root == 1);                    /* edits the NM connection */
            CHECK(r->prompt != NULL);                     /* PROMPT rows carry their own label */
            CHECK(strstr(az_row_command(r), "<value>") != NULL);  /* wrapper placeholder */
            CHECK(strstr(az_row_base(r), "<value>") != NULL);     /* base placeholder too */
            CHECK(strstr(az_row_base(r), "nmcli") != NULL);       /* the real tool it wraps */
        }
        if (strcmp(r->target, "azzio network ip dynamic") == 0) {
            has_dynamic = 1;
            CHECK(r->kind == AZ_ACT_PROMPT);
            CHECK(r->needs_root == 1);
            CHECK(r->prompt != NULL);
            CHECK(strstr(az_row_command(r), "<value>") != NULL);
            CHECK(strstr(az_row_base(r), "ipv4.method auto") != NULL);  /* DHCP switch */
        }
    }
    CHECK(has_show == 1);
    CHECK(has_static == 1);
    CHECK(has_dynamic == 1);
}

/* Theme rows are APPLIES that run the tested `azzio theme` subcommand. */
static void test_theme_rows_are_applies(void)
{
    const AzScreen *t = az_screen_find("theme");
    CHECK(t != NULL);
    CHECK(t->nrows == 2);
    CHECK(strcmp(t->rows[0].label, "Dark") == 0);
    CHECK(strcmp(t->rows[1].label, "White") == 0);
    CHECK(t->rows[0].kind == AZ_ACT_APPLY);
    CHECK(strcmp(t->rows[0].target, "azzio theme --dark") == 0);
    CHECK(strcmp(t->rows[1].target, "azzio theme --white") == 0);
    /* both request the theme preview */
    CHECK(t->rows[0].preview == AZ_PV_THEME);
    CHECK(t->rows[1].preview == AZ_PV_THEME);
    /* theme needs no sudo (it configures the user session) -> needs_root stays 0 */
    CHECK(t->rows[0].needs_root == 0);
    CHECK(t->rows[1].needs_root == 0);
}

/* Every apply teaches its bash command (az_row_command); a plain sub-screen row teaches
 * nothing. This backs the "show the bash command that invokes the setting" requirement. */
static void test_row_command(void)
{
    const AzScreen *t = az_screen_find("theme");
    CHECK(strcmp(az_row_command(&t->rows[0]), "azzio theme --dark") == 0);
    /* a SCREEN row (Network parent) has no command to type */
    const AzScreen *m = az_screen_find("main");
    CHECK(az_row_command(&m->rows[0]) == NULL);
    /* a PORT row's command carries the "<port>" placeholder the user would type */
    const AzScreen *fw = az_screen_find("network.firewall");
    int found_port = 0;
    for (int i = 0; i < fw->nrows; i++) {
        if (fw->rows[i].kind == AZ_ACT_PORT) {
            found_port = 1;
            CHECK(strstr(az_row_command(&fw->rows[i]), "<port>") != NULL);
        }
    }
    CHECK(found_port == 1);
}

/* PROMPT: every apply/port row now teaches its UNDERLYING base command too (az_row_base) --
 * the "Base Command: $ ..." line, which `x` copies -- alongside the azzio wrapper (`c`). A
 * SCREEN row teaches neither. A PORT row's base carries the same "<port>" placeholder the
 * wrapper does. These are the exact commands wired in the model, verified end-to-end. */
static void test_row_base_command(void)
{
    /* Theme: the base is the gsettings call, the wrapper is the azzio one. */
    const AzScreen *t = az_screen_find("theme");
    CHECK(strcmp(az_row_base(&t->rows[0]),
                 "gsettings set org.gnome.desktop.interface color-scheme prefer-dark") == 0);
    CHECK(strcmp(az_row_command(&t->rows[0]), "azzio theme --dark") == 0);

    /* Airplane on: the PROMPT's worked example -- base nmcli, wrapper azzio. */
    const AzScreen *air = az_screen_find("network.airplane");
    CHECK(strcmp(air->rows[0].label, "Turn airplane mode on") == 0);
    CHECK(strcmp(az_row_base(&air->rows[0]), "sudo nmcli networking off") == 0);
    CHECK(strcmp(az_row_command(&air->rows[0]), "azzio network airplane on") == 0);

    /* Wallpaper base is the feh line ending in the real image path. */
    const AzScreen *w = az_screen_find("wallpaper");
    CHECK(strstr(az_row_base(&w->rows[0]), "feh --no-fehbg --bg-fill") != NULL);
    CHECK(strstr(az_row_base(&w->rows[0]),
                 "/usr/share/wallpapers/years/contents/images/1672x941.png") != NULL);

    /* A SCREEN row (main > Network) teaches NO command and NO base. */
    const AzScreen *m = az_screen_find("main");
    CHECK(az_row_command(&m->rows[0]) == NULL);
    CHECK(az_row_base(&m->rows[0]) == NULL);

    /* A PORT row's base AND wrapper both carry the "<port>" the user would type. */
    const AzScreen *fw = az_screen_find("network.firewall");
    for (int i = 0; i < fw->nrows; i++) {
        if (fw->rows[i].kind == AZ_ACT_PORT) {
            CHECK(strstr(az_row_base(&fw->rows[i]), "<port>") != NULL);
            CHECK(strstr(az_row_base(&fw->rows[i]), "ufw") != NULL);
            CHECK(strstr(az_row_command(&fw->rows[i]), "<port>") != NULL);
        }
    }
    /* Every APPLY/PORT row that has a wrapper also declares a base (no half-filled rows). */
    const char *screens[] = {"theme", "wallpaper", "volume", "brightness", "machine",
                             "network.wifi", "network.wired", "network.bluetooth",
                             "network.airplane", "network.firewall"};
    for (size_t s = 0; s < sizeof screens / sizeof screens[0]; s++) {
        const AzScreen *sc = az_screen_find(screens[s]);
        CHECK(sc != NULL);
        for (int i = 0; i < sc->nrows; i++)
            if (sc->rows[i].kind == AZ_ACT_APPLY || sc->rows[i].kind == AZ_ACT_PORT)
                CHECK(az_row_base(&sc->rows[i]) != NULL);
    }
}

/* PROMPT: the Wallpaper subtitle became the directory PATH ("Wallpapers directory: .../") and is
 * flagged to render in the accent (cyan) tight above "Current:". Other screens' subtitles stay
 * default (subtitle_accent == 0) and now EXPLAIN the wrapped tools. */
static void test_subtitles_explain_and_wallpaper_is_accented(void)
{
    const AzScreen *w = az_screen_find("wallpaper");
    CHECK(strstr(w->subtitle, "Wallpapers directory:") != NULL);
    CHECK(strstr(w->subtitle, "/usr/share/wallpapers/") != NULL);   /* trailing slash, per spec */
    CHECK(w->subtitle_accent == 1);                                  /* cyan + tight */

    /* The explanatory subtitles name the tool they wrap; they are NOT accented. */
    const AzScreen *fw = az_screen_find("network.firewall");
    CHECK(strstr(fw->subtitle, "ufw") != NULL);
    CHECK(fw->subtitle_accent == 0);
    const AzScreen *air = az_screen_find("network.airplane");
    CHECK(strstr(air->subtitle, "nmcli") != NULL);
    const AzScreen *vol = az_screen_find("volume");
    CHECK(strstr(vol->subtitle, "wpctl") != NULL);
    /* Theme keeps the pinned kitty-exemption phrase AND now names gsettings. */
    const AzScreen *th = az_screen_find("theme");
    CHECK(strstr(th->subtitle, "gsettings") != NULL);
    CHECK(strstr(th->subtitle, "Kitty does not follow the system theme") != NULL);
}

/* The Firewall screen can LIST ports (show_output) and OPEN/CLOSE/DELETE a port by typing its
 * number (AZ_ACT_PORT) -- the in-UI firewall config the spec asks for. Every firewall apply
 * needs root, so needs_root is set (the UI secures a credential first, no black screen). */
static void test_firewall_lists_and_configures_ports(void)
{
    const AzScreen *fw = az_screen_find("network.firewall");
    CHECK(fw != NULL);
    int has_list = 0, n_port = 0;
    for (int i = 0; i < fw->nrows; i++) {
        CHECK(fw->rows[i].needs_root == 1);        /* all firewall applies secure sudo first */
        if (fw->rows[i].kind == AZ_ACT_APPLY &&
            strcmp(fw->rows[i].target, "azzio network firewall port list") == 0) {
            has_list = 1;
            CHECK(fw->rows[i].show_output == 1);    /* the listing renders in the overlay */
        }
        if (fw->rows[i].kind == AZ_ACT_PORT) {
            n_port++;
            CHECK(fw->rows[i].show_output == 1);
        }
    }
    CHECK(has_list == 1);
    CHECK(n_port == 3);                             /* open / close / delete */
}

/* The "Current:" line comes from the SCREEN, not a per-row status: Theme and Wallpaper set
 * a `.current` probe and their rows carry NO status (so no "white"/"years" echoes trail each
 * option), while other screens have no `.current` line at all. */
static void test_current_is_screen_level_not_per_row(void)
{
    const AzScreen *t = az_screen_find("theme");
    const AzScreen *w = az_screen_find("wallpaper");
    const AzScreen *m = az_screen_find("main");
    CHECK(t->current != NULL);
    CHECK(w->current != NULL);
    CHECK(m->current == NULL);              /* main has no "Current:" line */
    /* Theme/Wallpaper rows are label-only (no trailing status echo). */
    for (int i = 0; i < t->nrows; i++) CHECK(t->rows[i].status == NULL);
    for (int i = 0; i < w->nrows; i++) CHECK(w->rows[i].status == NULL);
    /* main's rows DO keep a status (the at-a-glance sub-screen summary). */
    CHECK(m->rows[0].status != NULL);
}

/* THE ANTI-SPAM CONTRACT. Every network sub-screen (Wifi/Wired/Bluetooth/Airplane/Firewall)
 * shows its live state EXACTLY ONCE via a screen-level `.current` probe -- and its action rows
 * carry NO per-row .status. This is the fix for "radio enabled" being echoed on all four Wifi
 * rows: the state now appears only in the "Current:" line, never after each option. */
static void test_network_subscreens_have_current_and_no_row_spam(void)
{
    const char *subs[] = {
        "network.wifi", "network.wired", "network.ip", "network.bluetooth",
        "network.airplane", "network.firewall", "network.ssh",
    };
    for (size_t i = 0; i < sizeof subs / sizeof subs[0]; i++) {
        const AzScreen *s = az_screen_find(subs[i]);
        CHECK(s != NULL);
        CHECK(s->current != NULL);                       /* state shown ONCE, up top */
        for (int r = 0; r < s->nrows; r++)
            CHECK(s->rows[r].status == NULL);            /* no per-row echo (no spam) */
    }
    /* The Network PARENT screen keeps one distinct status per row (a genuine at-a-glance
     * summary of each sub-screen -- not a repeated label), so those DO have a status. */
    const AzScreen *net = az_screen_find("network");
    for (int r = 0; r < net->nrows; r++) CHECK(net->rows[r].status != NULL);
}

/* The SSH Server screen (Network > SSH Server -- the spec's streamlined ssh entry). It must
 * resolve, show sshd state via a screen-level Current: probe, and carry rows to START/STOP the
 * server, run the hypervisor bring-up (`azzio --sshd-hypervisor`), and open/close :22 -- each
 * privileged (needs_root) and teaching a base command + the azzio wrapper. */
static void test_ssh_server_screen(void)
{
    const AzScreen *s = az_screen_find("network.ssh");
    CHECK(s != NULL);
    CHECK(strcmp(s->title, "SSH Server") == 0);
    CHECK(s->current == az_status_ssh);            /* sshd active/inactive shown once, up top */
    /* the subtitle explains the security implication (the spec: brief explanation + notice) */
    CHECK(strstr(s->subtitle, "ssh") != NULL || strstr(s->subtitle, "SSH") != NULL);
    CHECK(strstr(s->subtitle, "22") != NULL);       /* mentions the port */
    int has_start = 0, has_stop = 0, has_hyper = 0, has_open = 0;
    int has_root_on = 0, has_root_off = 0;
    for (int i = 0; i < s->nrows; i++) {
        CHECK(s->rows[i].kind == AZ_ACT_APPLY);
        CHECK(s->rows[i].needs_root == 1);          /* every ssh action secures sudo first */
        CHECK(az_row_command(&s->rows[i]) != NULL); /* teaches the azzio wrapper */
        CHECK(az_row_base(&s->rows[i]) != NULL);    /* AND the base command */
        if (strcmp(s->rows[i].target, "azzio network ssh start") == 0) has_start = 1;
        if (strcmp(s->rows[i].target, "azzio network ssh stop") == 0) has_stop = 1;
        if (strcmp(s->rows[i].target, "azzio --sshd-hypervisor") == 0) has_hyper = 1;
        if (strcmp(s->rows[i].target, "azzio network firewall port open 22/tcp") == 0) has_open = 1;
        /* root SSH login toggle (off by default) -- both directions must be present */
        if (strcmp(s->rows[i].target, "azzio network ssh root on") == 0) has_root_on = 1;
        if (strcmp(s->rows[i].target, "azzio network ssh root off") == 0) has_root_off = 1;
    }
    CHECK(has_start == 1);
    CHECK(has_stop == 1);
    CHECK(has_hyper == 1);                           /* the "button" for azzio --sshd-hypervisor */
    CHECK(has_open == 1);
    CHECK(has_root_on == 1);                         /* Enable root SSH login (INSECURE) */
    CHECK(has_root_off == 1);                        /* Disable root SSH login (default) */
    /* root-login state read: point the getter at a temp drop-in via AZ_ROOT_LOGIN_DROPIN
     * (the test override) so this is host-INDEPENDENT -- it must not depend on whatever
     * /etc/ssh/sshd_config.d the build host happens to carry. Absent file -> the shipped
     * default "denied"; an explicit `yes` -> "allowed"; `no` -> "denied". Also proves the
     * root half of the status line is a pure file read (no fork, no root). */
    char tdir[] = "/tmp/az_root_login_test.XXXXXX";
    CHECK(mkdtemp(tdir) != NULL);
    char tf[300];
    snprintf(tf, sizeof tf, "%s/00-azzio-root-login.conf", tdir);
    setenv("AZ_ROOT_LOGIN_DROPIN", tf, 1);
    remove(tf);                                        /* absent -> shipped default */
    CHECK(strcmp(az_root_login_state(), "denied") == 0);
    { FILE *rf = fopen(tf, "w"); CHECK(rf != NULL);
      fputs("PermitRootLogin yes\n", rf); fclose(rf); }
    CHECK(strcmp(az_root_login_state(), "allowed") == 0);   /* regression: reads the file, not a guess */
    { FILE *rf = fopen(tf, "w"); CHECK(rf != NULL);
      fputs("PermitRootLogin no\n", rf); fclose(rf); }
    CHECK(strcmp(az_root_login_state(), "denied") == 0);
    remove(tf);
    rmdir(tdir);
    unsetenv("AZ_ROOT_LOGIN_DROPIN");
    /* and the combined Current: line embeds the root-login state so the user sees it. */
    char sb[128];
    az_status_ssh(sb, sizeof sb);
    CHECK(strstr(sb, "root login") != NULL);
    /* the Network parent has an "SSH Server" row that descends here, with the sshd status */
    const AzScreen *net = az_screen_find("network");
    int found = 0;
    for (int i = 0; i < net->nrows; i++)
        if (strcmp(net->rows[i].label, "SSH Server") == 0) {
            found = 1;
            CHECK(net->rows[i].kind == AZ_ACT_SCREEN);
            CHECK(strcmp(net->rows[i].target, "network.ssh") == 0);
            CHECK(net->rows[i].status == az_status_ssh);
        }
    CHECK(found == 1);
}

/* The Power screen (shutdown / restart / sleep / lock, with timers). It resolves, shows any
 * pending timer via a Current: probe, and carries the immediate actions (systemctl-backed,
 * needs_root except lock) plus AZ_ACT_PROMPT timer rows and status/cancel. */
static void test_power_screen(void)
{
    const AzScreen *p = az_screen_find("power");
    CHECK(p != NULL);
    CHECK(strcmp(p->title, "Power") == 0);
    CHECK(p->current == az_status_power);
    int has_shutdown = 0, has_restart = 0, has_sleep = 0, has_lock = 0, has_prompt = 0, has_cancel = 0;
    for (int i = 0; i < p->nrows; i++) {
        CHECK(az_row_command(&p->rows[i]) != NULL);   /* teaches the azzio wrapper */
        CHECK(az_row_base(&p->rows[i]) != NULL);      /* AND the base command */
        if (strcmp(p->rows[i].target, "azzio power shutdown") == 0) {
            has_shutdown = 1; CHECK(p->rows[i].needs_root == 1);
        }
        if (strcmp(p->rows[i].target, "azzio power restart") == 0) has_restart = 1;
        if (strcmp(p->rows[i].target, "azzio power sleep") == 0) has_sleep = 1;
        if (strcmp(p->rows[i].target, "azzio power lock") == 0) {
            has_lock = 1; CHECK(p->rows[i].needs_root == 0);   /* locking needs no root */
        }
        if (p->rows[i].kind == AZ_ACT_PROMPT) {
            has_prompt = 1;
            CHECK(p->rows[i].prompt != NULL);          /* asks for a duration */
        }
        if (strcmp(p->rows[i].target, "azzio power shutdown --cancel") == 0) has_cancel = 1;
    }
    CHECK(has_shutdown == 1);
    CHECK(has_restart == 1);
    CHECK(has_sleep == 1);
    CHECK(has_lock == 1);
    CHECK(has_prompt == 1);                            /* at least one scheduled-timer row */
    CHECK(has_cancel == 1);
    /* the main menu has a Power row that descends here */
    const AzScreen *m = az_screen_find("main");
    int found = 0;
    for (int i = 0; i < m->nrows; i++)
        if (strcmp(m->rows[i].label, "Power") == 0) {
            found = 1;
            CHECK(strcmp(m->rows[i].target, "power") == 0);
            CHECK(m->rows[i].status == az_status_power);
        }
    CHECK(found == 1);
}

/* The firewall DEFAULT-policy control (the "general incoming and outgoing rule configuration"
 * the spec asks the UI to display AND control). The screen's Current: probe is the policy
 * summary, and rows wrap `azzio network firewall default <in> <out>`. */
static void test_firewall_default_policy_control(void)
{
    const AzScreen *fw = az_screen_find("network.firewall");
    CHECK(fw != NULL);
    /* Current: line now surfaces the default incoming/outgoing policy. */
    CHECK(fw->current == az_status_firewall_policy);
    int has_recommended = 0, has_allow_in = 0;
    for (int i = 0; i < fw->nrows; i++) {
        if (strcmp(fw->rows[i].target, "azzio network firewall default deny allow") == 0) {
            has_recommended = 1;
            CHECK(fw->rows[i].needs_root == 1);
        }
        if (strcmp(fw->rows[i].target, "azzio network firewall default allow allow") == 0)
            has_allow_in = 1;
    }
    CHECK(has_recommended == 1);   /* deny incoming + allow outgoing (the Azzio baseline) */
    CHECK(has_allow_in == 1);      /* open incoming (advanced) */
}

/* Wallpaper rows request the image preview and carry the right ids. */
static void test_wallpaper_rows_preview(void)
{
    const AzScreen *w = az_screen_find("wallpaper");
    CHECK(w != NULL);
    CHECK(w->nrows == 2);
    CHECK(w->rows[0].preview == AZ_PV_WALLPAPER);
    CHECK(strcmp(w->rows[0].preview_arg, "years") == 0);
    CHECK(strcmp(w->rows[1].preview_arg, "decades") == 0);
    /* the screen names the wallpaper directory (the spec) */
    CHECK(strstr(w->subtitle, "/usr/share/wallpapers") != NULL);
}

/* GPU / Time & Date / Language: the three new screens resolve, carry the right `azzio`
 * subcommand targets, and their main-menu rows descend into them. */
static void test_resolve_screens(void)
{
    const AzScreen *g = az_screen_find("gpu");
    const AzScreen *t = az_screen_find("timedate");
    const AzScreen *l = az_screen_find("language");
    CHECK(g != NULL); CHECK(t != NULL); CHECK(l != NULL);
    CHECK(strcmp(g->title, "GPU") == 0);
    CHECK(strcmp(g->rows[0].target, "azzio gpu --resolve") == 0);
    CHECK(g->rows[0].kind == AZ_ACT_APPLY);         /* GPU resolve is non-interactive (just installs) */
    CHECK(g->rows[0].needs_root == 1);              /* resolve installs packages (pacman) */
    CHECK(g->rows[0].show_output == 1);
    /* Time & Date / Language resolve pick 1 of 5 servers INTERACTIVELY. The capture overlay
     * feeds /dev/null to stdin, so the resolver cannot prompt there -- instead these are
     * AZ_ACT_PROMPT rows: the UI collects the server number in-field and appends it, running
     * "azzio <sub> --resolve --server <N>" (the non-interactive resolver path). The prompt
     * label lists the fixed server order so the typed number is unambiguous. */
    CHECK(t->rows[0].kind == AZ_ACT_PROMPT);
    CHECK(strcmp(t->rows[0].target, "azzio timedate --resolve --server") == 0);
    CHECK(t->rows[0].prompt != NULL && strstr(t->rows[0].prompt, "ipapi.co") != NULL);
    CHECK(t->rows[0].show_output == 1);
    CHECK(l->rows[0].kind == AZ_ACT_PROMPT);
    CHECK(strcmp(l->rows[0].target, "azzio language --resolve --server") == 0);
    CHECK(l->rows[0].prompt != NULL && strstr(l->rows[0].prompt, "ipapi.co") != NULL);
    CHECK(l->rows[0].show_output == 1);
    CHECK(g->current == az_status_gpu);
    CHECK(t->current == az_status_timedate);
    CHECK(l->current == az_status_language);
    /* main-menu rows descend into the new screens (indices per Step 2) */
    const AzScreen *m = az_screen_find("main");
    CHECK(strcmp(m->rows[7].target, "gpu") == 0);
    CHECK(m->rows[7].status == az_status_gpu);
    CHECK(strcmp(m->rows[9].target, "timedate") == 0);
    CHECK(strcmp(m->rows[10].target, "language") == 0);
}

/* The search filter: empty query matches all; a label substring matches; a miss doesn't.
 * All three cases short-circuit before the live status probe. */
static void test_row_matches(void)
{
    const AzScreen *net = az_screen_find("network");
    /* Look the Firewall row up by LABEL, not a fixed index, so inserting a new network
     * sub-screen (e.g. "IP Address") never silently shifts this test onto the wrong row. */
    const AzRow *fw = NULL;
    for (int i = 0; i < net->nrows; i++)
        if (strcmp(net->rows[i].label, "Firewall") == 0) fw = &net->rows[i];
    CHECK(fw != NULL);
    CHECK(strcmp(fw->label, "Firewall") == 0);
    CHECK(az_row_matches(fw, "") == 1);        /* empty -> all */
    CHECK(az_row_matches(fw, NULL) == 1);
    CHECK(az_row_matches(fw, "fire") == 1);    /* case-insensitive label substring */
    CHECK(az_row_matches(fw, "FIRE") == 1);
    CHECK(az_row_matches(fw, "wall") == 1);
    CHECK(az_row_matches(fw, "zzz") == 0);     /* a miss */
    /* Wifi row must NOT match "fire" */
    CHECK(az_row_matches(&net->rows[0], "fire") == 0);
}

/* The wallpaper image path mirrors wallpaper.py's on-disk layout. */
static void test_wallpaper_image_path(void)
{
    char buf[512];
    az_wallpaper_image("years", buf, sizeof buf);
    CHECK(strcmp(buf, "/usr/share/wallpapers/years/contents/images/1672x941.png") == 0);
    az_wallpaper_image("decades", buf, sizeof buf);
    CHECK(strstr(buf, "/decades/contents/images/1672x941.png") != NULL);
}

int main(void)
{
    test_top_level_is_network_theme_wallpaper();
    test_screen_set_is_exactly_expected();
    test_volume_and_brightness_screens();
    test_machine_type_screen();
    test_resolve_screens();
    test_backup_screen();
    test_hypervisor_screen();
    test_default_applications_screens();
    test_display_screens();
    test_network_rows_descend();
    test_ip_address_screen();
    test_theme_rows_are_applies();
    test_row_command();
    test_row_base_command();
    test_subtitles_explain_and_wallpaper_is_accented();
    test_firewall_lists_and_configures_ports();
    test_firewall_default_policy_control();
    test_ssh_server_screen();
    test_power_screen();
    test_current_is_screen_level_not_per_row();
    test_network_subscreens_have_current_and_no_row_spam();
    test_wallpaper_rows_preview();
    test_row_matches();
    test_wallpaper_image_path();

    if (failures == 0) {
        printf("test_terminal_user_interface_model: all checks passed\n");
        return 0;
    }
    printf("test_terminal_user_interface_model: %d check(s) FAILED\n", failures);
    return 1;
}
