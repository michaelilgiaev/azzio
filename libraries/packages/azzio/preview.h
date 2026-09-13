/* Azzio bare-`azzio` terminal user interface (C) -- the hovered-row PREVIEW pane.
 *
 * Two kinds of preview, both REAL images placed with kitty's `kitten icat --place` graphics
 * into the rectangle the renderer reserves (this is kitty; it can do it):
 *
 *   WALLPAPER -- the actual wallpaper image for the hovered choice. The current wallpaper is
 *                named at the top of the screen by the renderer; hovering a choice previews IT.
 *
 *   THEME     -- two shipped SCREENSHOTS side by side: LibreWolf on the timedate home page and
 *                the file manager, in the dark or white variant matching the hovered
 *                choice, so the user sees the theme before applying it. The images ship from
 *                assets/previews/ to AZ_PREVIEW_DIR and are used unmodified (kitty scales them
 *                at draw time). No caption text under them. (Kitty itself is exempt from the
 *                system theme -- the Theme screen's subtitle says so.)
 */
#ifndef AZ_PREVIEW_H
#define AZ_PREVIEW_H

#include "render.h"

/* Place the preview for the hovered row of `ui` into `rect` (as returned by az_render).
 * Safe to call with rect->valid==0 (does nothing). Both preview kinds shell out to
 * `kitten icat` to place real images. Returns 1 if an image is on screen for this frame (so
 * the caller knows the graphics may need clearing later), else 0. Re-placing is memoised:
 * an unchanged preview does not re-fork kitten. */
int az_preview_draw(const AzUI *ui, const AzRect *rect);

/* Clear any kitty graphics currently on screen (called before a frame that had an image).
 * No-op-safe. */
void az_preview_clear(void);

#endif /* AZ_PREVIEW_H */
