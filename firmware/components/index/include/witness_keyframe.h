#pragma once

#include <stdbool.h>
#include <stdint.h>

/* First frame, a robot mark, every 3 s above 0.05 motion, otherwise every 60 s. */

bool witness_keyframe_due(bool have_last, int64_t elapsed_ms, uint16_t motion_milli, bool mark);
