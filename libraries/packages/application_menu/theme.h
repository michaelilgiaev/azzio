/* Azzio application menu (C/GTK3) -- shared palette + geometry constants.
 *
 * The geometry numbers and font sizes are compile-time constants. The COLOURS are
 * RUNTIME-selected so the menu follows the system theme (dark by default, light when the
 * user runs `azzio theme --white`): every colour is an az_color(role) accessor that
 * returns the dark or light hex string depending on the system color-scheme, read ONCE at
 * startup by az_theme_init(). The AZ_*_COLOR names below are kept as macros that expand to
 * that accessor, so every existing call site (widget_bg(w, AZ_BG_COLOR),
 * gdk_rgba_parse(&c, AZ_TEXT_COLOR), the CSS g_strdup_printf, ...) keeps working unchanged
 * -- it still receives a const char* hex string, just the theme-correct one.
 *
 * The menu is a fixed-size borderless window CENTERED on the screen (OpenBox, no panel);
 * menu.c centers it via (screen - size) / 2.
 */
#ifndef AZ_THEME_H
#define AZ_THEME_H

/* The GLOBAL UI SCALE ratio (modifications/scale). AZ_SCALED(x) scales a fixed-PIXEL dimension by
 * it; the point FONTS below are left STOCK and scale via the DPI channel (gtk-xft-dpi, from
 * Xft.dpi) instead -- so they are NOT wrapped in AZ_SCALED (that would double-scale). The
 * checked-in az_scale.h is scale 1.0 (so the C tests compile stock); the ISO build overwrites it
 * with the real ratio, so the shipped menu's geometry derives from the one scale source. */
#include "az_scale.h"

/* --- Geometry (px) -- STOCK (scale-1.0) values, scaled by AZ_SCALED() ------
 * The stock values are the old hand-tuned "looks right at 1.35" numbers divided by 1.35, so
 * AZ_SCALED(stock) at the 1.35 default reproduces them (+-1px rounding) and scale 1.0 is stock. */
#define AZ_DEFAULT_WIDTH   AZ_SCALED(431)   /* menu window width  (was 582 @1.35) */
#define AZ_DEFAULT_HEIGHT  AZ_SCALED(368)   /* menu window height (was 497 @1.35) */

/* How far OFF-screen the daemon parks the (still-mapped) window to hide it.
 * Instant-open key: a mapped window that is merely MOVED costs almost nothing to
 * bring on-screen (no re-expose), whereas a re-map re-exposes the whole tree.
 * menu.c reads this in hide_menu/warmup. */
#define AZ_OFFSCREEN_MARGIN 4000

/* --- Runtime theme colours ----------------------------------------------- */
/* Colour ROLES. az_color(role) returns the dark or light hex string for the role,
 * selected by the system theme az_theme_init() reads at startup. Keep in sync with the
 * palette tables in theme.c (AZ_PALETTE_DARK / AZ_PALETTE_LIGHT). */
typedef enum {
    AZ_C_BG = 0,        /* window background */
    AZ_C_SURFACE,       /* lighter surface (search box, buttons) */
    AZ_C_HOVER,         /* row hover background */
    AZ_C_DIVIDER,       /* subtle separators */
    AZ_C_TEXT,          /* big app names */
    AZ_C_SUBTEXT,       /* muted -- the type subtitle */
    AZ_C_PLACEHOLDER,   /* search placeholder text */
    AZ_C_BORDER,        /* the logo cyan (focus border) */
    AZ_C_SELECT_BORDER, /* outline of selected row / focused button */
    AZ_C_SELECT_FILL,   /* subtle fill inside selected/hovered control */
    AZ_C_SELECT_TEXT,   /* text on a selected row */
    AZ_C_SEL_BG,        /* search-box selection background */
    AZ_C_SEL_FG,        /* search-box selection text */
    AZ_C_SCROLL_THUMB,  /* scrollbar thumb */
    AZ_C_SCROLL_THUMB_HOVER,
    AZ_C_SCROLL_GROOVE,
    AZ_C_COUNT
} AzColorRole;

/* Read the system color-scheme (freedesktop gsettings / GTK settings) and latch whether
 * the menu should render dark. Call ONCE early in main() before any widget is styled.
 * Returns 1 if dark, 0 if light. */
int az_theme_init(void);
/* 1 if the latched theme is dark, else 0 (valid after az_theme_init()). */
int az_theme_is_dark(void);
/* The hex colour string for a role under the latched theme (never NULL). */
const char *az_color(AzColorRole role);

/* The AZ_*_COLOR names as they were, now resolving to the runtime accessor so every
 * existing call site is unchanged. */
#define AZ_BG_COLOR          az_color(AZ_C_BG)
#define AZ_SURFACE_COLOR     az_color(AZ_C_SURFACE)
#define AZ_HOVER_COLOR       az_color(AZ_C_HOVER)
#define AZ_DIVIDER_COLOR     az_color(AZ_C_DIVIDER)
#define AZ_TEXT_COLOR        az_color(AZ_C_TEXT)
#define AZ_SUBTEXT_COLOR     az_color(AZ_C_SUBTEXT)
#define AZ_PLACEHOLDER_COLOR az_color(AZ_C_PLACEHOLDER)
#define AZ_BORDER_COLOR      az_color(AZ_C_BORDER)
#define AZ_SELECT_BORDER     az_color(AZ_C_SELECT_BORDER)
#define AZ_SELECT_FILL       az_color(AZ_C_SELECT_FILL)
#define AZ_SELECT_TEXT       az_color(AZ_C_SELECT_TEXT)
#define AZ_SEL_BG            az_color(AZ_C_SEL_BG)
#define AZ_SEL_FG            az_color(AZ_C_SEL_FG)
#define AZ_SCROLL_THUMB_COLOR  az_color(AZ_C_SCROLL_THUMB)
#define AZ_SCROLL_THUMB_HOVER  az_color(AZ_C_SCROLL_THUMB_HOVER)
#define AZ_SCROLL_GROOVE_COLOR az_color(AZ_C_SCROLL_GROOVE)

/* --- Scrollbar geometry (arrow-less rounded pill) -- STOCK, AZ_SCALED ---
 * The pill and its reserved column are TWICE the old size (the user asked for a
 * bar "twice as big"). Both the visible THUMB_WIDTH and the reserved TRACK_WIDTH
 * are doubled together, so the pill stays centered in its column (on_draw centers
 * it via x0 = (track - thumb) / 2) and the proportions are unchanged, only larger.
 * THUMB_MIN is the pill's minimum VERTICAL length (grab-ability), not a thickness
 * axis, so it is left as-is -- doubling it would change scroll feel, not size. */
#define AZ_SCROLL_THUMB_WIDTH  AZ_SCALED(8)    /* was 4 (6 @1.35); doubled -> 2x bar */
#define AZ_SCROLL_TRACK_WIDTH  AZ_SCALED(18)   /* was 9 (12 @1.35); doubled with the pill */
#define AZ_SCROLL_THUMB_MIN    AZ_SCALED(24)   /* min VERTICAL length (unchanged) */

/* --- Fonts (POINTS) -- TWO scaling paths, because the menu draws text two different ways:
 *   * The app-row NAME/TYPE (application_list.c) and the power labels (power.c) are drawn with
 *     pango_cairo_create_layout(cr), whose PangoContext defaults to 96 DPI and does NOT inherit
 *     gtk-xft-dpi -- so those do NOT scale via the DPI channel and MUST be scaled EXPLICITLY at
 *     build time with AZ_SCALED() (like the DPI-blind OpenBox titlebar). Stock = old/1.35, so
 *     AZ_SCALED(stock) at the 1.35 default reproduces the old 13/10/12.
 *   * The SEARCH box is a real GtkEntry with gtk_widget_override_font (menu.c), which DOES scale
 *     via gtk-xft-dpi -- so it stays STOCK (wrapping it in AZ_SCALED would DOUBLE-scale).
 * (This split -- verified by compiling both paths under a forced gtk-xft-dpi -- is why an earlier
 * "all fonts STOCK" reconciliation made the app rows ~23% too small: the cairo path never saw the
 * DPI.) Stock baselines come from modifications.scale.MENU_FONT_*_STOCK. The AZ_SCALED() results
 * at the 1.35 default are 14/9/12 pt (integer round of stock*1.35) -- i.e. ~the old 13/10/12,
 * within the +-1px rounding the codebase accepts for AZ_SCALED. */
#define AZ_FONT_FAMILY   "Noto Sans"
#define AZ_FONT_APP_NAME AZ_SCALED(10)  /* app-row NAME  (cairo path -> explicit scale); ->14 @1.35 (~13) */
#define AZ_FONT_APP_TYPE AZ_SCALED(8)   /* app-row TYPE  (cairo path -> explicit scale); ->11 @1.35 (~10) */
#define AZ_FONT_SEARCH   10             /* search GtkEntry (DPI path -> STOCK); ~13 @1.35 via DPI */
#define AZ_FONT_POWER    AZ_SCALED(9)   /* power labels  (cairo path -> explicit scale); ->12 @1.35 */

/* Icon sizes (px) -- STOCK, AZ_SCALED. */
#define AZ_ICON_SIZE       AZ_SCALED(33)  /* app-row icon edge;    was 44 @1.35 */
#define AZ_POWER_ICON_SIZE AZ_SCALED(18)  /* power-button icon;    was 24 @1.35 */

/* Row metrics (applist) -- STOCK, AZ_SCALED. */
#define AZ_ROW_H   AZ_SCALED(41)   /* was 56 @1.35 */
#define AZ_ROW_PAD_X AZ_SCALED(6)  /* was 8  @1.35 */
#define AZ_ICON_X  AZ_SCALED(19)   /* was 26 @1.35 */
#define AZ_TEXT_X  AZ_SCALED(58)   /* was 78 @1.35 */
#define AZ_NAME_DY AZ_SCALED(13)   /* was 18 @1.35 */
#define AZ_SUB_DY  AZ_SCALED(28)   /* was 38 @1.35 */

#endif /* AZ_THEME_H */
