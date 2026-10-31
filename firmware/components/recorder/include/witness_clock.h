#pragma once

#include <stdbool.h>
#include <time.h>

bool witness_parse_bound(const char *text, time_t now, time_t *out);
