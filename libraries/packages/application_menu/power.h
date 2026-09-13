/* Azzio application menu (C port) -- bottom power/session button.
 *
 * A session icon beside a label, drawn on a GtkDrawingArea, that highlights on hover
 * and can render a cyan keyboard-focus
 * OUTLINE (the menu's TAB navigation drives it). The four buttons are gridded into
 * four EQUAL columns by the caller; each button centres its icon+label group within
 * its own cell.
 *
 * The returned widget carries two pieces of object data the menu reads directly:
 *   "focused" (gboolean via GINT) -- set by the menu to drive the focus outline;
 *   "action"  (AzPowerAction)     -- the session action, fired by Enter on focus.
 */
#ifndef AZ_POWER_H
#define AZ_POWER_H

#include <gtk/gtk.h>
#include "icons.h"

typedef void (*AzPowerAction)(void);          /* az_suspend / az_lock_session / ... */
typedef void (*AzPowerBeforeFn)(gpointer);    /* run before the action (hide the menu) */

/* Build one power button. `before(before_user)` runs first on click (to hide the
 * menu), then `action()`. `icons` resolves the session icon at POWER_ICON_SIZE. */
GtkWidget *az_power_button_new(AzIcons *icons, const char *icon_name,
                               const char *label, AzPowerAction action,
                               AzPowerBeforeFn before, gpointer before_user);

/* Force-clear the hover state (repaints at rest). The menu calls this on hide so a
 * button the pointer was over when the menu closed does not re-open still lit -- the
 * off-screen hide never delivers the leave-notify that would normally clear it. */
void az_power_button_clear_hover(GtkWidget *btn);

/* Give every button a reference to the whole row (borrowed array of `n` widgets) so hover
 * can (a) stay exclusive to one button, (b) take over the TAB-focus highlight while the
 * mouse is over the row, and (c) LEAVE THE HIGHLIGHT on the last-hovered button once the
 * pointer leaves, instead of snapping back to the TAB position. Call once after all buttons
 * are created and gridded. */
void az_power_row_set_siblings(GtkWidget **btns, int n);

/* Drop the "last hovered" sticky highlight so the keyboard TAB/arrow focus shows again.
 * The menu calls this when TAB or an arrow key moves power-row focus, so an explicit key
 * press cleanly takes the highlight back from a stale mouse-hover after-image. `any_btn`
 * may be any button in the row (they share the row array). */
void az_power_row_clear_sticky(GtkWidget *any_btn);

#endif /* AZ_POWER_H */
