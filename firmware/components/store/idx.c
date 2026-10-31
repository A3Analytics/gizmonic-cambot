#include "witness_idx.h"

#include <string.h>

typedef struct __attribute__((packed)) {
    uint64_t t_ms;
    uint32_t offset;
    uint32_t length;
    uint16_t motion_milli;
} witness_idx_rec;

int witness_idx_encode(uint8_t *dst, size_t cap, uint64_t t_ms, uint32_t offset, uint32_t length,
                       uint16_t motion_milli) {
    witness_idx_rec record;
    if (cap < WITNESS_IDX_SIZE || sizeof(witness_idx_rec) != WITNESS_IDX_SIZE) {
        return -1;
    }
    record.t_ms = t_ms;
    record.offset = offset;
    record.length = length;
    record.motion_milli = motion_milli;
    memcpy(dst, &record, WITNESS_IDX_SIZE);
    return WITNESS_IDX_SIZE;
}

int witness_idx_decode(const uint8_t *src, size_t length, witness_idx_rec *out) {
    if (length < WITNESS_IDX_SIZE || out == NULL) {
        return -1;
    }
    memcpy(out, src, WITNESS_IDX_SIZE);
    return WITNESS_IDX_SIZE;
}
