#include "cambot_idx.h"

#include <string.h>

typedef struct __attribute__((packed)) {
    uint64_t t_ms;
    uint32_t offset;
    uint32_t length;
    uint16_t motion_milli;
} cambot_idx_rec;

int cambot_idx_encode(uint8_t *dst, size_t cap, uint64_t t_ms, uint32_t offset, uint32_t length,
                       uint16_t motion_milli) {
    cambot_idx_rec record;
    if (cap < CAMBOT_IDX_SIZE || sizeof(cambot_idx_rec) != CAMBOT_IDX_SIZE) {
        return -1;
    }
    record.t_ms = t_ms;
    record.offset = offset;
    record.length = length;
    record.motion_milli = motion_milli;
    memcpy(dst, &record, CAMBOT_IDX_SIZE);
    return CAMBOT_IDX_SIZE;
}

int cambot_idx_decode(const uint8_t *src, size_t length, cambot_idx_rec *out) {
    if (length < CAMBOT_IDX_SIZE || out == NULL) {
        return -1;
    }
    memcpy(out, src, CAMBOT_IDX_SIZE);
    return CAMBOT_IDX_SIZE;
}
