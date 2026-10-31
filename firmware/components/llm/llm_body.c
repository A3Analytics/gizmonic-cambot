#include "witness_llm.h"

#include <stdio.h>
#include <string.h>

static int append(char *dst, size_t cap, size_t *used, const char *text) {
    size_t length = strlen(text);
    if (*used + length >= cap) {
        return -1;
    }
    memcpy(dst + *used, text, length);
    *used += length;
    dst[*used] = '\0';
    return 0;
}

static int append_json(char *dst, size_t cap, size_t *used, const char *text) {
    for (const unsigned char *cursor = (const unsigned char *)text; *cursor; cursor++) {
        char piece[8];
        if (*cursor == '"' || *cursor == '\\') {
            snprintf(piece, sizeof piece, "\\%c", *cursor);
        } else if (*cursor < 0x20) {
            snprintf(piece, sizeof piece, "\\u%04x", *cursor);
        } else {
            piece[0] = (char)*cursor;
            piece[1] = '\0';
        }
        if (append(dst, cap, used, piece) != 0) {
            return -1;
        }
    }
    return 0;
}

int witness_openai_body(char *dst, size_t cap, const char *model, const char *prompt,
                        const char *b64, int max_tokens) {
    size_t used = 0;
    char prefix[128];
    if (cap == 0) {
        return -1;
    }
    dst[0] = '\0';
    snprintf(prefix, sizeof prefix, "{\"model\":\"");
    if (append(dst, cap, &used, prefix) || append_json(dst, cap, &used, model) ||
        append(dst, cap, &used, "\",\"max_tokens\":")) {
        return -1;
    }
    snprintf(prefix, sizeof prefix, "%d", max_tokens);
    if (append(dst, cap, &used, prefix) ||
        append(dst, cap, &used, ",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"") ||
        append_json(dst, cap, &used, prompt) ||
        append(dst, cap, &used, "\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/jpeg;base64,") ||
        append(dst, cap, &used, b64) || append(dst, cap, &used, "\"}}]}]}")) {
        return -1;
    }
    return (int)used;
}

int witness_anthropic_body(char *dst, size_t cap, const char *model, const char *prompt,
                           const char *b64, int max_tokens) {
    size_t used = 0;
    char prefix[64];
    if (cap == 0) {
        return -1;
    }
    dst[0] = '\0';
    if (append(dst, cap, &used, "{\"model\":\"") || append_json(dst, cap, &used, model) ||
        append(dst, cap, &used, "\",\"max_tokens\":")) {
        return -1;
    }
    snprintf(prefix, sizeof prefix, "%d", max_tokens);
    if (append(dst, cap, &used, prefix) ||
        append(dst, cap, &used,
               ",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"image\",\"source\":"
               "{\"type\":\"base64\",\"media_type\":\"image/jpeg\",\"data\":\"") ||
        append(dst, cap, &used, b64) || append(dst, cap, &used, "\"}},{\"type\":\"text\",\"text\":\"") ||
        append_json(dst, cap, &used, prompt) || append(dst, cap, &used, "\"}]}]}")) {
        return -1;
    }
    return (int)used;
}

int witness_jev_body(char *dst, size_t cap, const char *model, const char *question_name,
                     const char *instructions, const char *b64) {
    size_t used = 0;
    if (cap == 0) {
        return -1;
    }
    dst[0] = '\0';
    if (append(dst, cap, &used, "{\"model\":\"") || append_json(dst, cap, &used, model) ||
        append(dst, cap, &used, "\",\"state\":{\"photo\":\"img1\"},\"assets\":{\"img1\":{\"type\":\"image\","
                                "\"format\":\"jpg\",\"data_base64\":\"") ||
        append(dst, cap, &used, b64) || append(dst, cap, &used, "\"}},\"questions\":{\"") ||
        append_json(dst, cap, &used, question_name) ||
        append(dst, cap, &used, "\":{\"type\":\"noul\",\"instructions\":\"") ||
        append_json(dst, cap, &used, instructions) || append(dst, cap, &used, "\"}}}")) {
        return -1;
    }
    return (int)used;
}
