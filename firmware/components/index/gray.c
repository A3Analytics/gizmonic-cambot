#include "witness_gray.h"

#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "jpeg_decoder.h"

static uint8_t *rgb_buf;
static size_t rgb_cap;

static uint8_t luma(uint8_t red, uint8_t green, uint8_t blue) {
    return (uint8_t)((77 * red + 150 * green + 29 * blue) >> 8);
}

esp_err_t witness_gray_eighth(const uint8_t *jpeg, size_t jpeg_len, uint8_t *gray, size_t gray_cap,
                              size_t *gray_len) {
    if (!jpeg || jpeg_len < 2 || !gray || !gray_len) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_jpeg_image_cfg_t cfg = {
        .indata = (uint8_t *)jpeg,
        .indata_size = (uint32_t)jpeg_len,
        .out_format = JPEG_IMAGE_FORMAT_RGB888,
        .out_scale = JPEG_IMAGE_SCALE_1_8,
    };
    esp_jpeg_image_output_t info = {0};
    if (esp_jpeg_get_image_info(&cfg, &info) != ESP_OK || info.output_len < 3) {
        return ESP_FAIL;
    }
    if (info.output_len > rgb_cap) {
        free(rgb_buf);
        rgb_buf = heap_caps_malloc(info.output_len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        rgb_cap = rgb_buf ? info.output_len : 0;
    }
    if (!rgb_buf) {
        return ESP_ERR_NO_MEM;
    }
    cfg.outbuf = rgb_buf;
    cfg.outbuf_size = (uint32_t)rgb_cap;
    esp_jpeg_image_output_t decoded = {0};
    if (esp_jpeg_decode(&cfg, &decoded) != ESP_OK || decoded.output_len < 3) {
        return ESP_FAIL;
    }
    size_t pixels = decoded.output_len / 3;
    if (pixels > gray_cap) {
        return ESP_ERR_NO_MEM;
    }
    for (size_t i = 0; i < pixels; i++) {
        const uint8_t *pix = rgb_buf + i * 3;
        gray[i] = luma(pix[0], pix[1], pix[2]);
    }
    *gray_len = pixels;
    return ESP_OK;
}
