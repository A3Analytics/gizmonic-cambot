#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t witness_llm_drivers_ready(void);

/* Network calls. Keys are not logged. OpenAI goes through espressif/openai.
 * Claude and JEV use the same JSON bodies as the host tests. */

esp_err_t witness_llm_openai(const char *api_key, const char *base_url, const char *model, const uint8_t *jpeg,
                             size_t jpeg_len, const char *prompt, char *out, size_t out_cap);

esp_err_t witness_llm_anthropic(const char *api_key, const char *model, const uint8_t *jpeg, size_t jpeg_len,
                                const char *prompt, char *out, size_t out_cap);

esp_err_t witness_llm_jev(const char *api_key, const uint8_t *jpeg, size_t jpeg_len, const char *question,
                          const char *instructions, char *out, size_t out_cap);
