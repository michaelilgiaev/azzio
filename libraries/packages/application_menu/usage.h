/* Azzio application menu (C port) -- launch-frequency usage tracking.
 *
 * Port of usage.py. A JSON map desktop_id -> launch count under the XDG data
 * dir, plus the ordering used to sort the app list: most-launched first, ties
 * (all count-0 apps included) A->Z by casefolded display name.
 */
#ifndef AZ_USAGE_H
#define AZ_USAGE_H

#include <glib.h>
#include "applications.h"

typedef struct AzUsage AzUsage;

AzUsage *az_usage_new(void);            /* loads the store (best-effort) */
void     az_usage_free(AzUsage *u);

int  az_usage_count(AzUsage *u, const char *desktop_id);
void az_usage_record(AzUsage *u, const char *desktop_id); /* bump + persist */

/* Sort a GPtrArray of AzAppEntry* in place by frequency order. */
void az_usage_sort_apps(AzUsage *u, GPtrArray *apps);

#endif /* AZ_USAGE_H */
