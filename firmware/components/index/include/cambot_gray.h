#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

/* Decode a JPEG at 1/8 scale and store one luma byte per pixel.
 * The host test uses Pillow; this is the on-device approximation of that shrink. */

esp_err_t cambot_gray_eighth(const uint8_t *jpeg, size_t jpeg_len, uint8_t *gray, size_t gray_cap,
                              size_t *gray_len);
