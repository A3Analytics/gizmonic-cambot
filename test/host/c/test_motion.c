#include "cambot_keyframe.h"
#include "cambot_motion.h"

#include <stdio.h>

int main(void) {
    const uint8_t same[8] = {0, 1, 2, 3, 4, 5, 6, 7};
    const uint8_t moved[8] = {255, 255, 255, 255, 255, 255, 255, 255};
    if (cambot_motion_milli(NULL, same, 8) != 0) {
        return 1;
    }
    if (cambot_motion_milli(same, same, 8) != 0) {
        return 1;
    }
    if (cambot_motion_milli(same, moved, 8) != 986) {
        fprintf(stderr, "motion %u\n", cambot_motion_milli(same, moved, 8));
        return 2;
    }
    if (!cambot_keyframe_due(false, 0, 0, false)) {
        return 3;
    }
    if (cambot_keyframe_due(true, 1000, 900, false)) {
        return 4;
    }
    if (!cambot_keyframe_due(true, 1500, 900, false)) {
        return 5;
    }
    if (cambot_keyframe_due(true, 1000, 0, false)) {
        return 6;
    }
    if (!cambot_keyframe_due(true, 1500, 0, false)) {
        return 7;
    }
    if (!cambot_keyframe_due(true, 0, 0, true)) {
        return 8;
    }
    return 0;
}
