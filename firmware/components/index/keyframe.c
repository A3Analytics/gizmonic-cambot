#include "witness_keyframe.h"

bool witness_keyframe_due(bool have_last, int64_t elapsed_ms, uint16_t motion_milli, bool mark) {
    (void)motion_milli;
    if (mark || !have_last) {
        return true;
    }
    return elapsed_ms >= 1500;
}
