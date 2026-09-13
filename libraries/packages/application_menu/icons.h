/* Azzio application menu (C port) -- icon resolution to GdkPixbuf.
 *
 * Port of icons.py. Given an Icon= value it finds the best matching icon on
 * disk across the theme chain (breeze-dark -> breeze -> Adwaita -> hicolor),
 * rasterises SVGs to a cached PNG via rsvg-convert (the system has NO SVG
 * gdk-pixbuf loader, so we shell out exactly like the Python), and returns a
 * GdkPixbuf at the target size. Always returns something usable (a flat
 * placeholder if all lookup fails). Loaded pixbufs are cached per resolver.
 */
#ifndef AZ_ICONS_H
#define AZ_ICONS_H

#include <gtk/gtk.h>

typedef struct AzIcons AzIcons;

AzIcons   *az_icons_new(int size);
void       az_icons_free(AzIcons *r);
/* Returns a borrowed GdkPixbuf* owned by the resolver (do NOT unref); ref it if
 * you keep it past the resolver's life. Never NULL. */
GdkPixbuf *az_icons_load(AzIcons *r, const char *icon);

#endif /* AZ_ICONS_H */
