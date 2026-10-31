#pragma once

#include <stdbool.h>

/* Start SNTP. iana is logged, not used as a zoneinfo name: newlib on the
 * ESP32 takes a POSIX TZ string. America/Los_Angeles maps to Pacific time. */
void witness_time_start(const char *iana);

bool witness_time_synced(void);
