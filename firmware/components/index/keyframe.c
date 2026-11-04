#include "witness_keyframe.h"

bool witness_keyframe_due(bool have_last, int64_t elapsed_ms, uint16_t motion_milli, bool mark) {
    if (mark || !have_last) {
        return true;
    }
    if (motion_milli > 50) {
        return elapsed_ms >= 3000;
    }
    /* Quiet scenes still need a fresh JEV score; 60s left the UI looking stuck. */
    return elapsed_ms >= 5000;
}
