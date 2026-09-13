/* Azzio window switcher -- live thumbnail capture via XComposite.
 *
 * With a compositor (picom) redirecting every window to an off-screen pixmap, this reads
 * the window's CURRENT contents -- even when covered or minimized -- so the switcher tile
 * streams what the app is actually rendering. Without a compositor there is no backing
 * pixmap for an obscured window and capture returns NULL; the caller then shows the app
 * icon instead.
 */
#ifndef AZ_THUMBNAIL_H
#define AZ_THUMBNAIL_H

#include <gdk-pixbuf/gdk-pixbuf.h>

/* Grab window `xid`'s current contents as a pixbuf no larger than (max_w,max_h),
 * preserving aspect ratio. Returns NULL on any failure (no pixmap yet, not composited,
 * unmapped mid-grab, or the X extension is absent). The returned pixbuf is owned by the
 * caller (g_object_unref when done). */
GdkPixbuf *az_thumbnail_capture(unsigned long xid, int max_w, int max_h);

#endif /* AZ_THUMBNAIL_H */
