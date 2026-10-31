#pragma once

#include "esp_err.h"
#include "esp_http_server.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#define WITNESS_ROOT "/sdcard/witness"

esp_err_t witness_recorder_start(SemaphoreHandle_t camera_lock);
void witness_http_register(httpd_handle_t server, const char *bearer_token);
bool witness_sd_ready(void);
SemaphoreHandle_t witness_recorder_lock(void);
