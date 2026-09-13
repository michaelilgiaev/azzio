/* Azzio application menu (C port) -- the custom pill scrollbar.
 *
 * A minimal, self-drawn scrollbar, because both the classic Tk scrollbar AND the
 * default GTK scrollbar are too heavy for this borderless launcher. It is:
 *   * ARROW-LESS -- just a slider, no stepper buttons.
 *   * a single ROUNDED (pill) thumb, translucent cyan-grey (AZ_SCROLL_THUMB_WIDTH).
 *   * NO visible track at rest; on hover the thumb brightens and a faint groove
 *     fades in behind it.
 *   * HIDDEN entirely when everything fits (nothing to scroll).
 *
 * It is a GtkDrawingArea that reads/drives a GtkAdjustment (the scrolled window's
 * vertical adjustment), so it composes with a GtkScrolledWindow whose vertical
 * policy is GTK_POLICY_EXTERNAL (scrolls, draws no GTK bar). Draw it in an overlay
 * pinned to the right edge, full height.
 */
#ifndef AZ_SCROLLBAR_H
#define AZ_SCROLLBAR_H

#include <gtk/gtk.h>

typedef struct AzScrollbar AzScrollbar;

/* Create a scrollbar driving `vadj`. The returned GtkWidget (via az_scrollbar_widget)
 * is a fixed-width drawing area to overlay on the right edge. */
AzScrollbar *az_scrollbar_new(GtkAdjustment *vadj);
GtkWidget   *az_scrollbar_widget(AzScrollbar *s);
void         az_scrollbar_free(AzScrollbar *s);

#endif /* AZ_SCROLLBAR_H */
