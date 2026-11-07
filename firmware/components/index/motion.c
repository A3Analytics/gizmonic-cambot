#include "cambot_motion.h"

uint16_t cambot_motion_milli(const uint8_t *previous, const uint8_t *current, size_t count) {
    if (!previous || !current || count == 0) {
        return 0;
    }
    uint64_t total = 0;
    for (size_t i = 0; i < count; i++) {
        int diff = (int)current[i] - (int)previous[i];
        if (diff < 0) {
            diff = -diff;
        }
        total += (uint64_t)diff;
    }
    uint64_t denom = (uint64_t)count * 255;
    uint64_t milli = (total * 1000 + denom / 2) / denom;
    if (milli > 1000) {
        milli = 1000;
    }
    return (uint16_t)milli;
}
