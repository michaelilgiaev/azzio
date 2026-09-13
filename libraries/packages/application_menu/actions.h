/* Azzio application menu (C port) -- side-effect actions (launch + power).
 * Port of actions.py. Fire-and-forget; never blocks or crashes the menu. */
#ifndef AZ_ACTIONS_H
#define AZ_ACTIONS_H

#include <glib.h>

void az_launch(char **argv);   /* start an app detached (setsid) */
void az_suspend(void);
void az_lock_session(void);
void az_reboot(void);
void az_poweroff(void);

#endif /* AZ_ACTIONS_H */
