#include "cambot_llm.h"
#include "cambot_llm_client.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "mbedtls/base64.h"

static const char *TAG = "cambot.llm";

static esp_err_t post_json(const char *url, const char *header_name, const char *header_value,
                           const char *extra_name, const char *extra_value, const char *body, char *out,
                           size_t out_cap, int *status) {
    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 60000,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) {
        return ESP_FAIL;
    }
    esp_http_client_set_header(client, "Content-Type", "application/json");
    if (header_name && header_value) {
        esp_http_client_set_header(client, header_name, header_value);
    }
    if (extra_name && extra_value) {
        esp_http_client_set_header(client, extra_name, extra_value);
    }
    int body_len = (int)strlen(body);
    esp_err_t err = esp_http_client_open(client, body_len);
    if (err == ESP_OK) {
        if (esp_http_client_write(client, body, body_len) < 0) {
            err = ESP_FAIL;
        }
    }
    int status_code = 0;
    if (err == ESP_OK) {
        int64_t length = esp_http_client_fetch_headers(client);
        status_code = esp_http_client_get_status_code(client);
        if (length < 0 && status_code <= 0) {
            err = ESP_FAIL;
        } else {
            int total = 0;
            while (out && total < (int)out_cap - 1) {
                int got = esp_http_client_read(client, out + total, (int)out_cap - 1 - total);
                if (got <= 0) {
                    break;
                }
                total += got;
            }
            if (out && out_cap > 0) {
                if (total < 0) {
                    total = 0;
                }
                if (total >= (int)out_cap) {
                    total = (int)out_cap - 1;
                }
                out[total] = '\0';
            }
        }
        esp_http_client_close(client);
    }
    esp_http_client_cleanup(client);
    *status = status_code;
    ESP_LOGI(TAG, "model_http status=%d url=%s", status_code, url);
    return err;
}

static char *b64_alloc(const uint8_t *jpeg, size_t len, size_t *out_len) {
    size_t cap = 0;
    mbedtls_base64_encode(NULL, 0, &cap, jpeg, len);
    char *text = malloc(cap + 1);
    if (!text) {
        return NULL;
    }
    size_t wrote = 0;
    if (mbedtls_base64_encode((unsigned char *)text, cap, &wrote, jpeg, len) != 0) {
        free(text);
        return NULL;
    }
    text[wrote] = '\0';
    *out_len = wrote;
    return text;
}

esp_err_t cambot_llm_anthropic(const char *api_key, const char *model, const uint8_t *jpeg, size_t jpeg_len,
                                const char *prompt, char *out, size_t out_cap) {
    size_t b64_len = 0;
    char *b64 = b64_alloc(jpeg, jpeg_len, &b64_len);
    if (!b64) {
        return ESP_ERR_NO_MEM;
    }
    size_t cap = b64_len + strlen(prompt) + 512;
    char *body = malloc(cap);
    if (!body) {
        free(b64);
        return ESP_ERR_NO_MEM;
    }
    int wrote = cambot_anthropic_body(body, cap, model, prompt, b64, 1024);
    free(b64);
    if (wrote < 0) {
        free(body);
        return ESP_ERR_NO_MEM;
    }
    int status = 0;
    esp_err_t err = post_json(CAMBOT_ANTHROPIC_URL, "x-api-key", api_key, "anthropic-version",
                              CAMBOT_ANTHROPIC_VERSION, body, out, out_cap, &status);
    free(body);
    if (err != ESP_OK) {
        return err;
    }
    if (status == 401 || status == 403) {
        ESP_LOGW(TAG, "model=auth provider=anthropic");
        return ESP_FAIL;
    }
    if (status == 429) {
        ESP_LOGW(TAG, "model=rate_limited provider=anthropic");
        return ESP_FAIL;
    }
    if (status >= 400) {
        ESP_LOGW(TAG, "model=dropped provider=anthropic status=%d", status);
        return ESP_FAIL;
    }
    return ESP_OK;
}

esp_err_t cambot_llm_jev(const char *api_key, const uint8_t *jpeg, size_t jpeg_len, const char *question,
                          const char *instructions, char *out, size_t out_cap) {
    size_t b64_len = 0;
    char *b64 = b64_alloc(jpeg, jpeg_len, &b64_len);
    if (!b64) {
        return ESP_ERR_NO_MEM;
    }
    size_t cap = b64_len + strlen(instructions) + 512;
    char *body = malloc(cap);
    if (!body) {
        free(b64);
        return ESP_ERR_NO_MEM;
    }
    char *auth = malloc(strlen(api_key) + 8);
    if (!auth) {
        free(body);
        free(b64);
        return ESP_ERR_NO_MEM;
    }
    snprintf(auth, strlen(api_key) + 8, "Bearer %s", api_key);
    int wrote = cambot_jev_body(body, cap, CAMBOT_JEV_MODEL, question, instructions, b64);
    free(b64);
    if (wrote < 0) {
        free(body);
        free(auth);
        return ESP_ERR_NO_MEM;
    }
    int status = 0;
    esp_err_t err = post_json(CAMBOT_JEV_URL, "Authorization", auth, CAMBOT_JEV_IMAGES_HEADER, "true", body, out,
                              out_cap, &status);
    memset(auth, 0, strlen(api_key) + 8);
    free(auth);
    free(body);
    if (err != ESP_OK) {
        return err;
    }
    if (status == 401 || status == 403) {
        ESP_LOGW(TAG, "model=auth provider=jev");
        return ESP_FAIL;
    }
    if (status >= 400) {
        ESP_LOGW(TAG, "model=dropped provider=jev status=%d", status);
        printf("jev=fail status=%d\n", status);
        return ESP_FAIL;
    }
    return ESP_OK;
}

/* One TLS connection to JEV, kept open between keyframes. A fresh handshake
 * costs about 1.8 s on the camera; a reused connection skips it. */
static esp_http_client_handle_t jev_client;
static SemaphoreHandle_t jev_lock;

static void jev_drop(void) {
    if (jev_client) {
        esp_http_client_close(jev_client);
        esp_http_client_cleanup(jev_client);
        jev_client = NULL;
    }
}

static esp_err_t jev_exchange(const char *auth, const char *body, char *out, size_t out_cap, int *status,
                              bool *reused) {
    *reused = jev_client != NULL;
    if (!jev_client) {
        esp_http_client_config_t cfg = {
            .url = CAMBOT_JEV_URL,
            .method = HTTP_METHOD_POST,
            .timeout_ms = 20000,
            .crt_bundle_attach = esp_crt_bundle_attach,
            .keep_alive_enable = true,
        };
        jev_client = esp_http_client_init(&cfg);
        if (!jev_client) {
            return ESP_FAIL;
        }
        esp_http_client_set_header(jev_client, "Content-Type", "application/json");
        esp_http_client_set_header(jev_client, CAMBOT_JEV_IMAGES_HEADER, "true");
    }
    esp_http_client_set_header(jev_client, "Authorization", auth);
    int body_len = (int)strlen(body);
    int status_code = 0;
    int total = 0;
    esp_err_t err = esp_http_client_open(jev_client, body_len);
    if (err == ESP_OK && esp_http_client_write(jev_client, body, body_len) != body_len) {
        err = ESP_FAIL;
    }
    if (err == ESP_OK) {
        int64_t length = esp_http_client_fetch_headers(jev_client);
        status_code = esp_http_client_get_status_code(jev_client);
        if (status_code <= 0 || (length < 0 && status_code < 200)) {
            err = ESP_FAIL;
        } else {
            while (out && total < (int)out_cap - 1) {
                int got = esp_http_client_read(jev_client, out + total, (int)out_cap - 1 - total);
                if (got <= 0) {
                    break;
                }
                total += got;
            }
            if (!esp_http_client_is_complete_data_received(jev_client)) {
                esp_http_client_flush_response(jev_client, NULL);
            }
        }
    }
    if (out && out_cap > 0) {
        out[total] = '\0';
    }
    if (err != ESP_OK || !esp_http_client_is_complete_data_received(jev_client)) {
        jev_drop();
    }
    *status = status_code;
    ESP_LOGI(TAG, "model_http status=%d url=%s reused=%d", status_code, CAMBOT_JEV_URL, *reused ? 1 : 0);
    return err;
}

esp_err_t cambot_llm_jev_post(const char *api_key, const char *body, char *out, size_t out_cap) {
    char *auth;
    int status = 0;
    esp_err_t err;
    bool reused = false;
    if (!api_key || !api_key[0] || !body || !body[0]) {
        return ESP_ERR_INVALID_ARG;
    }
    auth = malloc(strlen(api_key) + 8);
    if (!auth) {
        return ESP_ERR_NO_MEM;
    }
    snprintf(auth, strlen(api_key) + 8, "Bearer %s", api_key);
    if (!jev_lock) {
        jev_lock = xSemaphoreCreateMutex();
    }
    if (!jev_lock || xSemaphoreTake(jev_lock, pdMS_TO_TICKS(30000)) != pdTRUE) {
        memset(auth, 0, strlen(api_key) + 8);
        free(auth);
        return ESP_ERR_TIMEOUT;
    }
    err = jev_exchange(auth, body, out, out_cap, &status, &reused);
    if (err != ESP_OK && reused) {
        /* The server closed the idle connection; one retry on a fresh one. */
        err = jev_exchange(auth, body, out, out_cap, &status, &reused);
    }
    xSemaphoreGive(jev_lock);
    memset(auth, 0, strlen(api_key) + 8);
    free(auth);
    if (err != ESP_OK) {
        printf("jev=fail err=%s\n", esp_err_to_name(err));
    }
    if (err != ESP_OK) {
        return err;
    }
    if (status == 401 || status == 403) {
        ESP_LOGW(TAG, "model=auth provider=jev");
        return ESP_FAIL;
    }
    if (status >= 400) {
        ESP_LOGW(TAG, "model=dropped provider=jev status=%d", status);
        printf("jev=fail status=%d\n", status);
        return ESP_FAIL;
    }
    return ESP_OK;
}

esp_err_t cambot_llm_alert(const char *url, const char *json_body) {
    esp_http_client_config_t cfg;
    esp_http_client_handle_t client;
    int body_len;
    int status_code = 0;
    esp_err_t err;
    if (!url || strncmp(url, "https://", 8) != 0 || !json_body || !json_body[0]) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(&cfg, 0, sizeof cfg);
    cfg.url = url;
    cfg.method = HTTP_METHOD_POST;
    cfg.timeout_ms = 5000;
    cfg.crt_bundle_attach = esp_crt_bundle_attach;
    client = esp_http_client_init(&cfg);
    if (!client) {
        return ESP_FAIL;
    }
    esp_http_client_set_header(client, "Content-Type", "application/json");
    body_len = (int)strlen(json_body);
    err = esp_http_client_open(client, body_len);
    if (err == ESP_OK && esp_http_client_write(client, json_body, body_len) < 0) {
        err = ESP_FAIL;
    }
    if (err == ESP_OK) {
        esp_http_client_fetch_headers(client);
        status_code = esp_http_client_get_status_code(client);
        esp_http_client_close(client);
    }
    esp_http_client_cleanup(client);
    ESP_LOGI(TAG, "alert_http status=%d", status_code);
    if (err != ESP_OK) {
        return err;
    }
    if (status_code < 200 || status_code >= 300) {
        return ESP_FAIL;
    }
    return ESP_OK;
}
