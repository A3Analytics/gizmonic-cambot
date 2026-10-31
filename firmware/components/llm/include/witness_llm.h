#pragma once

#include <stddef.h>

/* Wire constants shared with host/witness_models. Host tests read this file. */

#define WITNESS_OPENAI_URL "https://api.openai.com/v1/chat/completions"
#define WITNESS_ANTHROPIC_URL "https://api.anthropic.com/v1/messages"
#define WITNESS_ANTHROPIC_VERSION "2023-06-01"
#define WITNESS_JEV_URL "https://api.typesafe.ai/v1/systemone"
#define WITNESS_JEV_MODEL "jev-1.14.0"
#define WITNESS_JEV_IMAGES_HEADER "TypeSafe-Danger-Zone-Images"

/* Build a JSON request body. Returns the length written, or -1 if it does not fit.
 * b64 is already base64. Prompts are JSON-escaped. The API key is not written. */

int witness_openai_body(char *dst, size_t cap, const char *model, const char *prompt,
                        const char *b64, int max_tokens);
int witness_anthropic_body(char *dst, size_t cap, const char *model, const char *prompt,
                           const char *b64, int max_tokens);
int witness_jev_body(char *dst, size_t cap, const char *model, const char *question_name,
                     const char *instructions, const char *b64);
