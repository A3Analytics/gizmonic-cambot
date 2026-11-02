#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>

#include "esp_err.h"
#include "esp_http_server.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#define WITNESS_ROOT "/sdcard/witness"

esp_err_t witness_recorder_start(SemaphoreHandle_t camera_lock);
void witness_indexer_start(void);
void witness_indexer_set_keys(const char *openai_key, const char *jev_key);
bool witness_indexer_enabled(void);
bool witness_indexer_set_enabled(bool enabled);
void witness_indexer_submit(const uint8_t *jpeg, size_t len, time_t when, uint16_t motion_milli, uint32_t frame,
                            const char *seg);
void witness_http_register(httpd_handle_t server, const char *bearer_token);
bool witness_sd_ready(void);
SemaphoreHandle_t witness_recorder_lock(void);

/* Copy the newest index record. Returns false when the card has none yet. */
bool witness_index_copy_latest(char *dst, size_t cap);
