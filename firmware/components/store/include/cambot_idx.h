#pragma once

#include <stddef.h>
#include <stdint.h>

#define CAMBOT_IDX_SIZE 18

int cambot_idx_encode(uint8_t *dst, size_t cap, uint64_t t_ms, uint32_t offset, uint32_t length,
                       uint16_t motion_milli);
