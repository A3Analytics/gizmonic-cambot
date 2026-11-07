#pragma once

#include <stddef.h>

/* Wire constants shared with host/cambot_models. Host tests read this file. */

#define CAMBOT_OPENAI_URL "https://api.openai.com/v1/chat/completions"
#define CAMBOT_ANTHROPIC_URL "https://api.anthropic.com/v1/messages"
#define CAMBOT_ANTHROPIC_VERSION "2023-06-01"
#define CAMBOT_JEV_URL "https://api.typesafe.ai/v1/systemone"
#define CAMBOT_JEV_MODEL "jev-1.14.0"
#define CAMBOT_JEV_IMAGES_HEADER "TypeSafe-Danger-Zone-Images"

/* Build a JSON request body. Returns the length written, or -1 if it does not fit.
 * b64 is already base64. Prompts are JSON-escaped. The API key is not written. */

typedef struct {
    const char *key;
    const char *b64;
} cambot_jev_asset;

typedef struct {
    const char *name;
    const char *instructions;
    int box;
    const char *box_instructions;
} cambot_jev_question;

int cambot_openai_body(char *dst, size_t cap, const char *model, const char *prompt,
                        const char *b64, int max_tokens);
int cambot_anthropic_body(char *dst, size_t cap, const char *model, const char *prompt,
                           const char *b64, int max_tokens);
int cambot_jev_body(char *dst, size_t cap, const char *model, const char *question_name,
                     const char *instructions, const char *b64);

/* One System One request: the keyframe is asset img1, plus example assets and
 * a noul per question. box adds a "{name}__box" question when non-zero. */
int cambot_jev_request(char *dst, size_t cap, const char *model, const char *photo_b64,
                        const cambot_jev_asset *assets, int asset_count,
                        const cambot_jev_question *questions, int question_count);
