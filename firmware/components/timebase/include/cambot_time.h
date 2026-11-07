#pragma once

#include <stdbool.h>

/* Start SNTP. iana is logged, not used as a zoneinfo name: newlib on the
 * ESP32 takes a POSIX TZ string. America/Los_Angeles maps to Pacific time. */
void cambot_time_start(const char *iana);

bool cambot_time_synced(void);
