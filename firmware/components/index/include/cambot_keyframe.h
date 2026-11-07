#pragma once

#include <stdbool.h>
#include <stdint.h>

/* First frame, a robot mark, otherwise every 1.5 s. */

bool cambot_keyframe_due(bool have_last, int64_t elapsed_ms, uint16_t motion_milli, bool mark);
