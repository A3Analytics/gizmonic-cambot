#pragma once

#include <stddef.h>
#include <stdint.h>

/* Mean absolute difference of two grayscale buffers, in thousandths (0..1000).
 * A missing previous buffer scores 0, matching the host indexer. */

uint16_t witness_motion_milli(const uint8_t *previous, const uint8_t *current, size_t count);
