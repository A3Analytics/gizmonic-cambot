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
#define WITNESS_NOULS_JSON WITNESS_ROOT "/nouls.json"
#define WITNESS_NOUL_JPEG_MAX (80 * 1024)

esp_err_t witness_recorder_start(SemaphoreHandle_t camera_lock);
void witness_indexer_start(void);
void witness_indexer_set_keys(const char *openai_key, const char *jev_key);
bool witness_indexer_enabled(void);
bool witness_indexer_set_enabled(bool enabled);
bool witness_jev_enabled(void);
bool witness_jev_set_enabled(bool enabled);
void witness_indexer_submit(const uint8_t *jpeg, size_t len, time_t when, uint16_t motion_milli, uint32_t frame,
                            const char *seg);
void witness_http_register(httpd_handle_t server, const char *bearer_token);
bool witness_sd_ready(void);
SemaphoreHandle_t witness_recorder_lock(void);

/* Copy the newest recorded JPEG for preview. False until the first frame. */
bool witness_recorder_peek_jpeg(uint8_t *dst, size_t cap, size_t *out_len);

/* High-performance capture mode. Boots disabled when the SD flag is absent. */
bool witness_perf_enabled(void);
bool witness_perf_set_enabled(bool enabled);
unsigned witness_perf_capture_ms(void);

/* Copy the newest index record. Returns false when the card has none yet. */
bool witness_index_copy_latest(char *dst, size_t cap);

/* JSON array of the newest index records kept in RAM. Caller frees it. */
char *witness_index_recent_json(const char *query);

/* One short page of the on-card index, oldest first. cursor is empty or
 * "YYYY-MM-DD/HH.jsonl:byte". Caller frees the JSON object. NULL on failure. */
char *witness_index_read_page(const char *cursor, int limit);

/* Minute frame counts kept in RAM since boot. Returns how many minutes were written. */
int witness_recorder_minutes(time_t start, time_t end, time_t *minutes, uint16_t *frames, int cap);
