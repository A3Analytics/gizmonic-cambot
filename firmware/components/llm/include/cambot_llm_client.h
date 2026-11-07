#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t cambot_llm_drivers_ready(void);

/* Network calls. Keys are not logged. OpenAI goes through espressif/openai.
 * Claude and JEV use the same JSON bodies as the host tests. */

esp_err_t cambot_llm_openai(const char *api_key, const char *base_url, const char *model, const uint8_t *jpeg,
                             size_t jpeg_len, const char *prompt, char *out, size_t out_cap);

esp_err_t cambot_llm_anthropic(const char *api_key, const char *model, const uint8_t *jpeg, size_t jpeg_len,
                                const char *prompt, char *out, size_t out_cap);

esp_err_t cambot_llm_jev(const char *api_key, const uint8_t *jpeg, size_t jpeg_len, const char *question,
                          const char *instructions, char *out, size_t out_cap);

/* body is a finished System One JSON document. The key is not logged. */
esp_err_t cambot_llm_jev_post(const char *api_key, const char *body, char *out, size_t out_cap);

/* Slack-compatible {"text":"..."} POST. The URL is not logged. Timeout is short. */
esp_err_t cambot_llm_alert(const char *url, const char *json_body);
