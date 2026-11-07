#include "cambot_recorder.h"

#pragma GCC diagnostic ignored "-Wformat-truncation"

#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>

#include "cJSON.h"
#include "esp_heap_caps.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"
#include "mbedtls/base64.h"
#include "cambot_alert.h"
#include "cambot_llm.h"
#include "cambot_llm_client.h"

/* One pending keyframe: a newer frame replaces it, so JEV always scores the
 * freshest picture instead of working through a backlog. */
#define PENDING 1
/* About 9 hours of JEV at one frame every 1.6 s. */
#define BUDGET_FRAMES 20000
#define EVENTS_EVERY_S 10
/* Newest records, kept in RAM so a question does not scan the card.
 * Scanning on the HTTP task made the camera unreachable for minutes. */
#define RECENT_MAX 400
#define QUESTION_MAX 8
#define EXAMPLE_MAX 4
#define ALERT_SLOTS 16

typedef struct {
    uint8_t *jpeg;
    size_t len;
    time_t when;
    uint16_t motion_milli;
    uint32_t frame;
    char seg[40];
} keyframe_t;

typedef struct {
    char id[32];
    char polarity[8];
} example_ref_t;

typedef struct {
    char name[48];
    char instructions[240];
    char label[96];
    example_ref_t examples[EXAMPLE_MAX];
    int example_count;
    int box;
    char box_color[16];
    char box_instructions[240];
    int alert;
    int alert_yes;
    char alert_url[300];
} question_t;

typedef struct {
    char name[48];
    int previous_hit;
    int used;
} alert_slot_t;

static QueueHandle_t keyframes;
static SemaphoreHandle_t latest_lock;
static char latest[4096];
static const char *openai_key = "";
static const char *jev_key = "";
static int frames_today;
static time_t budget_day;
static volatile bool index_enabled;
static volatile bool jev_enabled;
static alert_slot_t alert_slots[ALERT_SLOTS];

static const char *CAPTION_PROMPT =
    "General camera setup. Testing basic functionality.\n"
    "Return only a JSON object with keys caption (string), objects (array of strings), "
    "actions (array of strings), and robot_state (string). No markdown.";

void cambot_indexer_set_keys(const char *openai, const char *jev) {
    openai_key = openai ? openai : "";
    jev_key = jev ? jev : "";
}

static void load_enabled(void) {
    /* Always boot with indexing off. The registrar turns it on when needed. */
    index_enabled = false;
}

bool cambot_indexer_enabled(void) {
    return index_enabled;
}

bool cambot_indexer_set_enabled(bool enabled) {
    FILE *file = fopen(CAMBOT_ROOT "/index_enabled", "w");
    if (file) {
        fputc(enabled ? '1' : '0', file);
        fputc('\n', file);
        fclose(file);
    } else {
        /* No card yet: keep the switch live in RAM so the registrar UX works. */
        printf("index=ram_only\n");
    }
    index_enabled = enabled;
    printf("index=%s\n", enabled ? "started" : "stopped");
    return true;
}

static void load_jev_enabled(void) {
    /* Always boot with JEV off. */
    jev_enabled = false;
}

bool cambot_jev_enabled(void) {
    return jev_enabled;
}

bool cambot_jev_set_enabled(bool enabled) {
    FILE *file = fopen(CAMBOT_ROOT "/jev_enabled", "w");
    if (file) {
        fputc(enabled ? '1' : '0', file);
        fputc('\n', file);
        fclose(file);
    } else {
        printf("jev=ram_only\n");
    }
    jev_enabled = enabled;
    printf("jev=%s\n", enabled ? "started" : "stopped");
    return true;
}

static bool either_index(void) {
    return index_enabled || jev_enabled;
}

bool cambot_index_copy_latest(char *dst, size_t cap) {
    if (!dst || cap == 0) {
        return false;
    }
    dst[0] = '\0';
    if (!latest_lock || xSemaphoreTake(latest_lock, pdMS_TO_TICKS(200)) != pdTRUE) {
        return false;
    }
    bool ok = latest[0] != '\0';
    if (ok) {
        snprintf(dst, cap, "%s", latest);
    }
    xSemaphoreGive(latest_lock);
    return ok && dst[0] != '\0';
}

static void remember_latest(const char *json) {
    if (!latest_lock || xSemaphoreTake(latest_lock, pdMS_TO_TICKS(200)) != pdTRUE) {
        return;
    }
    snprintf(latest, sizeof latest, "%s", json);
    xSemaphoreGive(latest_lock);
}

static char *recent_lines[RECENT_MAX];
static int recent_pos;
static int recent_count;
static SemaphoreHandle_t recent_lock;

static void remember_recent(const char *json) {
    char *copy = heap_caps_malloc(strlen(json) + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!copy) {
        copy = malloc(strlen(json) + 1);
    }
    if (!copy) {
        return;
    }
    memcpy(copy, json, strlen(json) + 1);
    if (!recent_lock || xSemaphoreTake(recent_lock, pdMS_TO_TICKS(200)) != pdTRUE) {
        free(copy);
        return;
    }
    free(recent_lines[recent_pos]);
    recent_lines[recent_pos] = copy;
    recent_pos = (recent_pos + 1) % RECENT_MAX;
    if (recent_count < RECENT_MAX) {
        recent_count++;
    }
    xSemaphoreGive(recent_lock);
}

char *cambot_index_recent_json(const char *query) {
    const char **match = malloc(RECENT_MAX * sizeof(char *));
    int found = 0;
    size_t bytes = 3;
    char *out;
    char *cursor;
    if (!match) {
        return NULL;
    }
    if (!recent_lock || xSemaphoreTake(recent_lock, pdMS_TO_TICKS(200)) != pdTRUE) {
        free(match);
        return NULL;
    }
    int start = (recent_pos - recent_count + RECENT_MAX) % RECENT_MAX;
    for (int i = 0; i < recent_count; i++) {
        const char *line = recent_lines[(start + i) % RECENT_MAX];
        if (!line) {
            continue;
        }
        if (query && query[0] && strcasestr(line, query) == NULL) {
            continue;
        }
        match[found++] = line;
        bytes += strlen(line) + 1;
    }
    out = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!out) {
        out = malloc(bytes);
    }
    if (!out) {
        xSemaphoreGive(recent_lock);
        free(match);
        return NULL;
    }
    cursor = out;
    *cursor++ = '[';
    for (int i = 0; i < found; i++) {
        size_t len = strlen(match[i]);
        if (i > 0) {
            *cursor++ = ',';
        }
        memcpy(cursor, match[i], len);
        cursor += len;
    }
    *cursor++ = ']';
    *cursor = '\0';
    xSemaphoreGive(recent_lock);
    free(match);
    return out;
}

/* A page is a few dozen lines. The HTTP task returns between pages so a
 * question can cover the card without holding the live view. */
#define PAGE_LINE_MAX 40
#define PAGE_BYTES (24 * 1024)
#define PAGE_LINE_CAP 16384

typedef struct {
    char rel[40];
} index_file_t;

static int cmp_index_file(const void *left, const void *right) {
    return strcmp(((const index_file_t *)left)->rel, ((const index_file_t *)right)->rel);
}

static int list_index_files(index_file_t **out) {
    *out = NULL;
    DIR *days = opendir(CAMBOT_ROOT "/index");
    if (!days) {
        return 0;
    }
    int count = 0;
    int cap = 0;
    index_file_t *files = NULL;
    struct dirent *day;
    while ((day = readdir(days)) != NULL) {
        if (day->d_name[0] == '.' || strlen(day->d_name) != 10) {
            continue;
        }
        char day_path[160];
        snprintf(day_path, sizeof day_path, CAMBOT_ROOT "/index/%s", day->d_name);
        DIR *hours = opendir(day_path);
        if (!hours) {
            continue;
        }
        struct dirent *hour;
        while ((hour = readdir(hours)) != NULL) {
            size_t name_len = strlen(hour->d_name);
            if (name_len < 7 || strcmp(hour->d_name + name_len - 6, ".jsonl") != 0) {
                continue;
            }
            if (count == cap) {
                cap = cap ? cap * 2 : 16;
                if (cap > 2048) {
                    closedir(hours);
                    closedir(days);
                    free(files);
                    *out = NULL;
                    return -1;
                }
                index_file_t *grown = realloc(files, (size_t)cap * sizeof(index_file_t));
                if (!grown) {
                    closedir(hours);
                    closedir(days);
                    free(files);
                    *out = NULL;
                    return -1;
                }
                files = grown;
            }
            snprintf(files[count].rel, sizeof files[count].rel, "%s/%s", day->d_name, hour->d_name);
            count++;
        }
        closedir(hours);
    }
    closedir(days);
    if (count > 1) {
        qsort(files, (size_t)count, sizeof(index_file_t), cmp_index_file);
    }
    *out = files;
    return count;
}

static bool cursor_path(const char *cursor, char *path, size_t path_cap, long *offset) {
    *offset = 0;
    path[0] = '\0';
    if (!cursor || !cursor[0]) {
        return true;
    }
    if (strstr(cursor, "..") != NULL) {
        return false;
    }
    const char *colon = strrchr(cursor, ':');
    if (!colon || colon == cursor) {
        return false;
    }
    size_t len = (size_t)(colon - cursor);
    if (len == 0 || len >= path_cap || len < 16) {
        return false;
    }
    memcpy(path, cursor, len);
    path[len] = '\0';
    if (path[10] != '/' || strcmp(path + len - 6, ".jsonl") != 0) {
        return false;
    }
    char *end = NULL;
    long value = strtol(colon + 1, &end, 10);
    if (!end || *end != '\0' || value < 0) {
        return false;
    }
    *offset = value;
    return true;
}

char *cambot_index_read_page(const char *cursor, int limit) {
    if (limit < 1) {
        limit = 1;
    }
    if (limit > PAGE_LINE_MAX) {
        limit = PAGE_LINE_MAX;
    }
    index_file_t *files = NULL;
    int count = list_index_files(&files);
    if (count < 0) {
        return NULL;
    }
    char path[40];
    long offset = 0;
    int idx = 0;
    if (!cursor_path(cursor, path, sizeof path, &offset)) {
        free(files);
        return NULL;
    }
    if (path[0]) {
        for (; idx < count; idx++) {
            int cmp = strcmp(files[idx].rel, path);
            if (cmp == 0) {
                break;
            }
            if (cmp > 0) {
                offset = 0;
                break;
            }
        }
    }
    cJSON *root = cJSON_CreateObject();
    cJSON *lines = cJSON_CreateArray();
    char *buf = malloc(PAGE_LINE_CAP);
    if (!root || !lines || !buf) {
        cJSON_Delete(root);
        cJSON_Delete(lines);
        free(buf);
        free(files);
        return NULL;
    }
    int kept = 0;
    size_t bytes = 0;
    char parked[40] = "";
    long parked_at = 0;
    while (idx < count && kept < limit && bytes < PAGE_BYTES) {
        char full[192];
        snprintf(full, sizeof full, CAMBOT_ROOT "/index/%s", files[idx].rel);
        FILE *file = fopen(full, "rb");
        if (!file) {
            idx++;
            offset = 0;
            continue;
        }
        if (offset > 0 && fseek(file, offset, SEEK_SET) != 0) {
            fclose(file);
            idx++;
            offset = 0;
            continue;
        }
        while (kept < limit && bytes < PAGE_BYTES && fgets(buf, PAGE_LINE_CAP, file)) {
            size_t len = strlen(buf);
            int partial = len + 1 >= PAGE_LINE_CAP && buf[len - 1] != '\n';
            if (partial) {
                int ch;
                while ((ch = fgetc(file)) != EOF && ch != '\n') {
                }
                continue;
            }
            while (len > 0 && (buf[len - 1] == '\n' || buf[len - 1] == '\r')) {
                buf[--len] = '\0';
            }
            if (len == 0) {
                continue;
            }
            cJSON_AddItemToArray(lines, cJSON_CreateString(buf));
            kept++;
            bytes += len;
        }
        int eof = feof(file);
        offset = ftell(file);
        fclose(file);
        if (!eof) {
            break;
        }
        /* Remember the end of this file. A finished copy must resume here,
         * not at the first file, or the registrar reads the card forever. */
        snprintf(parked, sizeof parked, "%s", files[idx].rel);
        parked_at = offset < 0 ? 0 : offset;
        idx++;
        offset = 0;
    }
    bool done = idx >= count;
    cJSON_AddItemToObject(root, "lines", lines);
    char next[80];
    next[0] = '\0';
    if (!done) {
        snprintf(next, sizeof next, "%s:%ld", files[idx].rel, offset);
    } else if (parked[0]) {
        snprintf(next, sizeof next, "%s:%ld", parked, parked_at);
    } else if (cursor && cursor[0]) {
        snprintf(next, sizeof next, "%s", cursor);
    }
    cJSON_AddStringToObject(root, "next", next);
    cJSON_AddBoolToObject(root, "done", done);
    char *printed = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    free(buf);
    free(files);
    return printed;
}

static int fits(const char *text, size_t cap) {
    return text && text[0] && strlen(text) < cap;
}

static int plain_id(const char *text) {
    size_t length;
    if (!text || !text[0]) {
        return 0;
    }
    length = strlen(text);
    if (length > 31) {
        return 0;
    }
    for (size_t i = 0; i < length; i++) {
        char ch = text[i];
        if (!((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_' ||
              ch == '-')) {
            return 0;
        }
    }
    return 1;
}

static int hex_color(const char *text) {
    if (!text || text[0] != '#' || strlen(text) != 7) {
        return 0;
    }
    for (int i = 1; i < 7; i++) {
        char ch = text[i];
        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f') || (ch >= 'A' && ch <= 'F'))) {
            return 0;
        }
    }
    return 1;
}

static int fill_question(question_t *question, cJSON *item) {
    const char *name = cJSON_GetStringValue(cJSON_GetObjectItem(item, "name"));
    const char *instructions = cJSON_GetStringValue(cJSON_GetObjectItem(item, "instructions"));
    const char *label = cJSON_GetStringValue(cJSON_GetObjectItem(item, "label"));
    cJSON *examples;
    cJSON *example;
    cJSON *box;
    cJSON *alert;
    const char *url;
    const char *when;
    memset(question, 0, sizeof *question);
    if (!plain_id(name) || !fits(name, sizeof question->name) || !fits(instructions, sizeof question->instructions) ||
        !fits(label, sizeof question->label)) {
        return -1;
    }
    snprintf(question->name, sizeof question->name, "%s", name);
    snprintf(question->instructions, sizeof question->instructions, "%s", instructions);
    snprintf(question->label, sizeof question->label, "%s", label);
    examples = cJSON_GetObjectItem(item, "examples");
    if (cJSON_IsArray(examples)) {
        cJSON_ArrayForEach(example, examples) {
            const char *id;
            const char *polarity;
            if (question->example_count >= EXAMPLE_MAX || !cJSON_IsObject(example)) {
                continue;
            }
            id = cJSON_GetStringValue(cJSON_GetObjectItem(example, "id"));
            polarity = cJSON_GetStringValue(cJSON_GetObjectItem(example, "polarity"));
            if (!plain_id(id) || !fits(id, sizeof question->examples[0].id)) {
                continue;
            }
            if (!polarity || (strcmp(polarity, "yes") != 0 && strcmp(polarity, "no") != 0)) {
                continue;
            }
            snprintf(question->examples[question->example_count].id, sizeof question->examples[0].id, "%s", id);
            snprintf(question->examples[question->example_count].polarity, sizeof question->examples[0].polarity, "%s",
                     polarity);
            question->example_count++;
        }
    }
    box = cJSON_GetObjectItem(item, "box");
    if (cJSON_IsObject(box) && cJSON_IsTrue(cJSON_GetObjectItem(box, "enabled"))) {
        const char *color = cJSON_GetStringValue(cJSON_GetObjectItem(box, "color"));
        const char *box_instructions = cJSON_GetStringValue(cJSON_GetObjectItem(box, "instructions"));
        if (fits(box_instructions, sizeof question->box_instructions)) {
            question->box = 1;
            snprintf(question->box_color, sizeof question->box_color, "%s", hex_color(color) ? color : "#16a34a");
            snprintf(question->box_instructions, sizeof question->box_instructions, "%s", box_instructions);
        }
    }
    alert = cJSON_GetObjectItem(item, "alert");
    url = cJSON_IsObject(alert) ? cJSON_GetStringValue(cJSON_GetObjectItem(alert, "url")) : NULL;
    when = cJSON_IsObject(alert) ? cJSON_GetStringValue(cJSON_GetObjectItem(alert, "when")) : NULL;
    if (url && strncmp(url, "https://", 8) == 0 && fits(url, sizeof question->alert_url)) {
        question->alert = 1;
        question->alert_yes = !when || strcmp(when, "no") != 0;
        snprintf(question->alert_url, sizeof question->alert_url, "%s", url);
    }
    return 0;
}

static int load_questions(question_t *questions, int cap) {
    FILE *file = fopen(CAMBOT_NOULS_JSON, "rb");
    char *raw;
    size_t got;
    cJSON *root;
    int count = 0;
    cJSON *item = NULL;
    if (!file) {
        file = fopen(CAMBOT_ROOT "/watch.json", "rb");
    }
    if (!file) {
        return 0;
    }
    raw = malloc(32768);
    if (!raw) {
        fclose(file);
        return 0;
    }
    got = fread(raw, 1, 32767, file);
    fclose(file);
    raw[got] = '\0';
    root = cJSON_Parse(raw);
    if (!cJSON_IsArray(root)) {
        cJSON_Delete(root);
        free(raw);
        return 0;
    }
    cJSON_ArrayForEach(item, root) {
        if (count >= cap || !cJSON_IsObject(item)) {
            continue;
        }
        if (fill_question(&questions[count], item) == 0) {
            count++;
        }
    }
    cJSON_Delete(root);
    free(raw);
    return count;
}

static bool string_array(cJSON *node, cJSON *dst) {
    if (!cJSON_IsArray(node)) {
        return false;
    }
    cJSON *item = NULL;
    cJSON_ArrayForEach(item, node) {
        if (!cJSON_IsString(item)) {
            return false;
        }
        cJSON_AddItemToArray(dst, cJSON_CreateString(item->valuestring));
    }
    return true;
}

static cJSON *parse_caption(const char *text) {
    const char *start = strchr(text, '{');
    const char *end = strrchr(text, '}');
    if (!start || !end || end < start) {
        return NULL;
    }
    size_t len = (size_t)(end - start + 1);
    char *slice = malloc(len + 1);
    if (!slice) {
        return NULL;
    }
    memcpy(slice, start, len);
    slice[len] = '\0';
    cJSON *root = cJSON_Parse(slice);
    free(slice);
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return NULL;
    }
    cJSON *caption = cJSON_GetObjectItem(root, "caption");
    cJSON *robot = cJSON_GetObjectItem(root, "robot_state");
    if (!cJSON_IsString(caption) || caption->valuestring[0] == '\0' || !cJSON_IsString(robot)) {
        cJSON_Delete(root);
        return NULL;
    }
    cJSON *objects = cJSON_CreateArray();
    cJSON *actions = cJSON_CreateArray();
    if (!string_array(cJSON_GetObjectItem(root, "objects"), objects) ||
        !string_array(cJSON_GetObjectItem(root, "actions"), actions)) {
        cJSON_Delete(objects);
        cJSON_Delete(actions);
        cJSON_Delete(root);
        return NULL;
    }
    cJSON *clean = cJSON_CreateObject();
    cJSON_AddStringToObject(clean, "caption", caption->valuestring);
    cJSON_AddItemToObject(clean, "objects", objects);
    cJSON_AddItemToObject(clean, "actions", actions);
    cJSON_AddStringToObject(clean, "robot_state", robot->valuestring);
    cJSON_Delete(root);
    return clean;
}

static char *b64_alloc(const uint8_t *jpeg, size_t len) {
    size_t cap = 0;
    size_t wrote = 0;
    char *text;
    mbedtls_base64_encode(NULL, 0, &cap, jpeg, len);
    text = heap_caps_malloc(cap + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!text) {
        text = malloc(cap + 1);
    }
    if (!text) {
        return NULL;
    }
    if (mbedtls_base64_encode((unsigned char *)text, cap, &wrote, jpeg, len) != 0) {
        free(text);
        return NULL;
    }
    text[wrote] = '\0';
    return text;
}

static uint8_t *read_capped(const char *path, size_t cap, size_t *out_len) {
    FILE *file = fopen(path, "rb");
    long size;
    uint8_t *buf;
    size_t got;
    if (!file) {
        return NULL;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        return NULL;
    }
    size = ftell(file);
    if (size <= 0 || (size_t)size > cap || fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        return NULL;
    }
    buf = heap_caps_malloc((size_t)size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!buf) {
        buf = malloc((size_t)size);
    }
    if (!buf) {
        fclose(file);
        return NULL;
    }
    got = fread(buf, 1, (size_t)size, file);
    fclose(file);
    if (got != (size_t)size) {
        free(buf);
        return NULL;
    }
    *out_len = got;
    return buf;
}

static int *alert_previous(const char *name) {
    int free_slot = -1;
    for (int i = 0; i < ALERT_SLOTS; i++) {
        if (alert_slots[i].used && strcmp(alert_slots[i].name, name) == 0) {
            return &alert_slots[i].previous_hit;
        }
        if (!alert_slots[i].used && free_slot < 0) {
            free_slot = i;
        }
    }
    if (free_slot < 0) {
        free_slot = 0;
    }
    snprintf(alert_slots[free_slot].name, sizeof alert_slots[free_slot].name, "%s", name);
    alert_slots[free_slot].used = 1;
    alert_slots[free_slot].previous_hit = 0;
    return &alert_slots[free_slot].previous_hit;
}

static void maybe_alert(const question_t *question, double score, const char *iso) {
    int *previous;
    int saved;
    const char *side;
    char text[320];
    cJSON *payload;
    char *printed;
    if (!question->alert || strncmp(question->alert_url, "https://", 8) != 0) {
        return;
    }
    previous = alert_previous(question->name);
    saved = *previous;
    if (!cambot_alert_edge(previous, score, question->alert_yes)) {
        return;
    }
    side = question->alert_yes ? "yes" : "no";
    snprintf(text, sizeof text, "Gizmonic Cambot: %s is %s (%.2f) at %s", question->label, side, score, iso);
    payload = cJSON_CreateObject();
    if (!payload) {
        *previous = saved;
        return;
    }
    cJSON_AddStringToObject(payload, "text", text);
    printed = cJSON_PrintUnformatted(payload);
    cJSON_Delete(payload);
    if (!printed) {
        *previous = saved;
        return;
    }
    if (cambot_llm_alert(question->alert_url, printed) == ESP_OK) {
        printf("alert=sent name=%s\n", question->name);
    } else {
        *previous = saved;
        printf("alert=dropped name=%s\n", question->name);
    }
    free(printed);
}

static int box_edges(cJSON *answer, double *top, double *bottom, double *left, double *right) {
    cJSON *box = cJSON_GetObjectItem(answer, "box");
    cJSON *top_item = cJSON_GetObjectItem(box, "top");
    cJSON *bottom_item = cJSON_GetObjectItem(box, "bottom");
    cJSON *left_item = cJSON_GetObjectItem(box, "left");
    cJSON *right_item = cJSON_GetObjectItem(box, "right");
    if (!cJSON_IsNumber(top_item) || !cJSON_IsNumber(bottom_item) || !cJSON_IsNumber(left_item) ||
        !cJSON_IsNumber(right_item)) {
        return 0;
    }
    *top = top_item->valuedouble;
    *bottom = bottom_item->valuedouble;
    *left = left_item->valuedouble;
    *right = right_item->valuedouble;
    if (*top < 0 || *bottom > 1 || *left < 0 || *right > 1 || !(*top < *bottom) || !(*left < *right)) {
        return 0;
    }
    return 1;
}

static bool ask_jev(const uint8_t *jpeg, size_t len, const question_t *questions, int count, cJSON *watch,
                    cJSON *boxes, const char *iso) {
    cambot_jev_asset assets[QUESTION_MAX * EXAMPLE_MAX];
    char asset_keys[QUESTION_MAX * EXAMPLE_MAX][16];
    cambot_jev_question outgoing[QUESTION_MAX];
    char instructions[QUESTION_MAX][640];
    char *photo = NULL;
    char *body = NULL;
    char *response = NULL;
    int asset_count = 0;
    int body_cap;
    int wrote;
    bool any = false;
    cJSON *root = NULL;
    if (!jev_key[0] || count <= 0) {
        return false;
    }
    memset(assets, 0, sizeof assets);
    memset(outgoing, 0, sizeof outgoing);
    photo = b64_alloc(jpeg, len);
    if (!photo) {
        return false;
    }
    body_cap = (int)strlen(photo) + 1024;
    for (int i = 0; i < count; i++) {
        int used = snprintf(instructions[i], sizeof instructions[i], "%s", questions[i].instructions);
        outgoing[i].name = questions[i].name;
        outgoing[i].instructions = instructions[i];
        outgoing[i].box = questions[i].box;
        outgoing[i].box_instructions = questions[i].box_instructions;
        body_cap += (int)strlen(questions[i].instructions) + (int)strlen(questions[i].box_instructions) + 256;
        for (int e = 0; e < questions[i].example_count && asset_count < QUESTION_MAX * EXAMPLE_MAX; e++) {
            char path[192];
            uint8_t *jpeg_bytes;
            size_t jpeg_len = 0;
            const char *side;
            int more;
            snprintf(path, sizeof path, CAMBOT_ROOT "/nouls/%s/%s.jpg", questions[i].name, questions[i].examples[e].id);
            jpeg_bytes = read_capped(path, CAMBOT_NOUL_JPEG_MAX, &jpeg_len);
            if (!jpeg_bytes) {
                continue;
            }
            assets[asset_count].b64 = b64_alloc(jpeg_bytes, jpeg_len);
            free(jpeg_bytes);
            if (!assets[asset_count].b64) {
                continue;
            }
            snprintf(asset_keys[asset_count], sizeof asset_keys[asset_count], "ex_%02d", asset_count);
            assets[asset_count].key = asset_keys[asset_count];
            side = strcmp(questions[i].examples[e].polarity, "no") == 0 ? "no" : "yes";
            more = snprintf(instructions[i] + used, sizeof instructions[i] - (size_t)used, " A %s looks like %s.", side,
                            asset_keys[asset_count]);
            if (more > 0 && (size_t)more < sizeof instructions[i] - (size_t)used) {
                used += more;
            }
            body_cap += (int)strlen(assets[asset_count].b64) + 64;
            asset_count++;
        }
    }
    body = heap_caps_malloc((size_t)body_cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!body) {
        body = malloc((size_t)body_cap);
    }
    response = heap_caps_malloc(8192, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!response) {
        response = malloc(8192);
    }
    if (!body || !response) {
        printf("jev=fail err=no_mem\n");
        goto done;
    }
    wrote = cambot_jev_request(body, (size_t)body_cap, CAMBOT_JEV_MODEL, photo, assets, asset_count, outgoing, count);
    if (wrote < 0 || cambot_llm_jev_post(jev_key, body, response, 8192) != ESP_OK) {
        goto done;
    }
    root = cJSON_Parse(response);
    {
        cJSON *answers = cJSON_GetObjectItem(root, "answers");
        for (int i = 0; i < count; i++) {
            char box_name[64];
            cJSON *answer = cJSON_GetObjectItem(answers, questions[i].name);
            cJSON *noul = cJSON_GetObjectItem(answer, "noul");
            double score;
            double top, bottom, left, right;
            cJSON *box_answer;
            cJSON *drawn;
            if (!cJSON_IsNumber(noul)) {
                continue;
            }
            score = noul->valuedouble;
            cJSON_AddNumberToObject(watch, questions[i].label, score);
            any = true;
            maybe_alert(&questions[i], score, iso);
            if (!questions[i].box || score < 0.5) {
                continue;
            }
            snprintf(box_name, sizeof box_name, "%s__box", questions[i].name);
            box_answer = cJSON_GetObjectItem(answers, box_name);
            if (!box_edges(box_answer, &top, &bottom, &left, &right)) {
                continue;
            }
            drawn = cJSON_CreateObject();
            if (!drawn) {
                continue;
            }
            cJSON_AddStringToObject(drawn, "label", questions[i].label);
            cJSON_AddStringToObject(drawn, "color", questions[i].box_color);
            cJSON_AddNumberToObject(drawn, "top", top);
            cJSON_AddNumberToObject(drawn, "bottom", bottom);
            cJSON_AddNumberToObject(drawn, "left", left);
            cJSON_AddNumberToObject(drawn, "right", right);
            cJSON_AddItemToArray(boxes, drawn);
        }
    }
done:
    cJSON_Delete(root);
    free(response);
    free(body);
    free(photo);
    for (int i = 0; i < asset_count; i++) {
        free((void *)assets[i].b64);
    }
    return any;
}

static void stamp(time_t when, char *iso, size_t iso_cap, char *day, size_t day_cap, char *hour, size_t hour_cap) {
    struct tm utc;
    gmtime_r(&when, &utc);
    strftime(iso, iso_cap, "%Y-%m-%dT%H:%M:%S.000Z", &utc);
    strftime(day, day_cap, "%Y-%m-%d", &utc);
    strftime(hour, hour_cap, "%H", &utc);
}

static bool append_line(const char *path, const char *line) {
    FILE *file = fopen(path, "ab");
    if (!file) {
        return false;
    }
    fputs(line, file);
    fputc('\n', file);
    fclose(file);
    return true;
}

static cJSON *read_day_records(const char *day) {
    cJSON *rows = cJSON_CreateArray();
    char dirpath[160];
    snprintf(dirpath, sizeof dirpath, CAMBOT_ROOT "/index/%s", day);
    for (int hour = 0; hour < 24 && cJSON_GetArraySize(rows) < 256; hour++) {
        char path[192];
        snprintf(path, sizeof path, "%s/%02d.jsonl", dirpath, hour);
        FILE *file = fopen(path, "rb");
        if (!file) {
            continue;
        }
        char line[4096];
        while (fgets(line, sizeof line, file) && cJSON_GetArraySize(rows) < 256) {
            cJSON *row = cJSON_Parse(line);
            if (cJSON_IsObject(row)) {
                cJSON_AddItemToArray(rows, row);
            } else {
                cJSON_Delete(row);
            }
        }
        fclose(file);
    }
    return rows;
}

static bool label_listed(cJSON *labels, const char *label) {
    cJSON *item = NULL;
    cJSON_ArrayForEach(item, labels) {
        if (cJSON_IsString(item) && strcmp(item->valuestring, label) == 0) {
            return true;
        }
    }
    return false;
}

static bool action_hit(cJSON *actions, const char *label) {
    cJSON *item = NULL;
    cJSON_ArrayForEach(item, actions) {
        if (cJSON_IsString(item) && strcmp(item->valuestring, label) == 0) {
            return true;
        }
    }
    return false;
}

static void rewrite_events(const char *day) {
    cJSON *rows = read_day_records(day);
    cJSON *labels = cJSON_CreateArray();
    cJSON *row = NULL;
    cJSON_ArrayForEach(row, rows) {
        cJSON *watch = cJSON_GetObjectItem(row, "watch");
        cJSON *score = NULL;
        cJSON_ArrayForEach(score, watch) {
            if (score->string && cJSON_IsNumber(score) && score->valuedouble >= 0.5 &&
                !label_listed(labels, score->string)) {
                cJSON_AddItemToArray(labels, cJSON_CreateString(score->string));
            }
        }
        cJSON *action = NULL;
        cJSON_ArrayForEach(action, cJSON_GetObjectItem(row, "actions")) {
            if (cJSON_IsString(action) && action->valuestring[0] && !label_listed(labels, action->valuestring)) {
                cJSON_AddItemToArray(labels, cJSON_CreateString(action->valuestring));
            }
        }
    }
    cJSON *events = cJSON_CreateArray();
    int next_id = 1;
    cJSON *label = NULL;
    cJSON_ArrayForEach(label, labels) {
        cJSON *run = NULL;
        cJSON_ArrayForEach(row, rows) {
            cJSON *watch = cJSON_GetObjectItem(row, "watch");
            cJSON *score_item = cJSON_GetObjectItem(watch, label->valuestring);
            bool watch_hit = cJSON_IsNumber(score_item) && score_item->valuedouble >= 0.5;
            bool act = action_hit(cJSON_GetObjectItem(row, "actions"), label->valuestring);
            if (watch_hit || act) {
                double score = watch_hit ? score_item->valuedouble : 1.0;
                const char *when = cJSON_GetStringValue(cJSON_GetObjectItem(row, "t"));
                if (!when) {
                    continue;
                }
                if (!run) {
                    run = cJSON_CreateObject();
                    char id[16];
                    snprintf(id, sizeof id, "%d", next_id++);
                    cJSON_AddStringToObject(run, "id", id);
                    cJSON_AddStringToObject(run, "label", label->valuestring);
                    cJSON_AddStringToObject(run, "start", when);
                    cJSON_AddStringToObject(run, "end", when);
                    cJSON_AddNumberToObject(run, "score", score);
                } else {
                    cJSON_ReplaceItemInObject(run, "end", cJSON_CreateString(when));
                    cJSON *prev = cJSON_GetObjectItem(run, "score");
                    if (cJSON_IsNumber(prev) && score > prev->valuedouble) {
                        cJSON_ReplaceItemInObject(run, "score", cJSON_CreateNumber(score));
                    }
                }
            } else if (run) {
                cJSON_AddItemToArray(events, run);
                run = NULL;
            }
        }
        if (run) {
            cJSON_AddItemToArray(events, run);
        }
    }
    mkdir(CAMBOT_ROOT "/events", 0775);
    char path[160];
    snprintf(path, sizeof path, CAMBOT_ROOT "/events/%s.jsonl", day);
    FILE *file = fopen(path, "wb");
    if (file) {
        cJSON *event = NULL;
        cJSON_ArrayForEach(event, events) {
            char *printed = cJSON_PrintUnformatted(event);
            if (printed) {
                fputs(printed, file);
                fputc('\n', file);
                free(printed);
            }
        }
        fclose(file);
    }
    cJSON_Delete(events);
    cJSON_Delete(labels);
    cJSON_Delete(rows);
}

static bool budget_allows(int count, time_t when) {
    time_t day = when - (when % 86400);
    if (day != budget_day) {
        budget_day = day;
        frames_today = 0;
    }
    return frames_today + count <= BUDGET_FRAMES;
}

static void write_record(const keyframe_t *item, cJSON *caption, cJSON *watch, cJSON *boxes, bool have_watch) {
    char iso[40], day[16], hour[8];
    stamp(item->when, iso, sizeof iso, day, sizeof day, hour, sizeof hour);
    cJSON *record = cJSON_CreateObject();
    cJSON_AddStringToObject(record, "t", iso);
    cJSON_AddStringToObject(record, "seg", item->seg);
    cJSON_AddNumberToObject(record, "frame", item->frame);
    cJSON_AddNumberToObject(record, "motion", (double)item->motion_milli / 1000.0);
    if (caption) {
        cJSON_AddStringToObject(record, "caption", cJSON_GetStringValue(cJSON_GetObjectItem(caption, "caption")));
        cJSON_AddItemToObject(record, "objects", cJSON_DetachItemFromObject(caption, "objects"));
        cJSON_AddItemToObject(record, "actions", cJSON_DetachItemFromObject(caption, "actions"));
        cJSON_AddStringToObject(record, "robot_state",
                                cJSON_GetStringValue(cJSON_GetObjectItem(caption, "robot_state")));
        cJSON_AddStringToObject(record, "model", "openai/gpt-4.1-mini");
    } else {
        cJSON_AddStringToObject(record, "caption", "");
        cJSON_AddArrayToObject(record, "objects");
        cJSON_AddArrayToObject(record, "actions");
        cJSON_AddStringToObject(record, "robot_state", "");
        cJSON_AddStringToObject(record, "model", "typesafe/" CAMBOT_JEV_MODEL);
    }
    cJSON_AddItemToObject(record, "watch", watch);
    if (boxes && cJSON_GetArraySize(boxes) > 0) {
        cJSON_AddItemToObject(record, "boxes", boxes);
    } else {
        cJSON_Delete(boxes);
    }
    cJSON_AddBoolToObject(record, "verified", false);
    char *printed = cJSON_PrintUnformatted(record);
    if (!printed) {
        cJSON_Delete(record);
        return;
    }
    char dir[160];
    snprintf(dir, sizeof dir, CAMBOT_ROOT "/index/%s", day);
    mkdir(CAMBOT_ROOT "/index", 0775);
    mkdir(dir, 0775);
    char path[192];
    snprintf(path, sizeof path, "%s/%s.jsonl", dir, hour);
    append_line(path, printed);
    if (strlen(printed) < sizeof latest) {
        remember_latest(printed);
    }
    remember_recent(printed);
    printf("index=ok t=%s motion_milli=%u model=%s\n", iso, (unsigned)item->motion_milli,
           caption ? "openai" : "jev");
    free(printed);
    cJSON_Delete(record);
    /* Rebuilding the day's events reads every record on the card; doing it per
     * keyframe slowed JEV updates and starved internal RAM. */
    static time_t events_at;
    static char events_day[16];
    if ((caption || have_watch) &&
        (item->when - events_at >= EVENTS_EVERY_S || strcmp(day, events_day) != 0)) {
        rewrite_events(day);
        events_at = item->when;
        snprintf(events_day, sizeof events_day, "%s", day);
    }
}

static void discard_batch(keyframe_t *batch, int count) {
    for (int i = 0; i < count; i++) {
        free(batch[i].jpeg);
        batch[i].jpeg = NULL;
    }
}

static void flush_batch(keyframe_t *batch, int count) {
    if (count <= 0) {
        return;
    }
    if (!either_index()) {
        discard_batch(batch, count);
        return;
    }
    if (!budget_allows(count, batch[0].when)) {
        static time_t paused_logged;
        if (batch[0].when - paused_logged >= 60) {
            printf("index=paused frames_today=%d\n", frames_today);
            paused_logged = batch[0].when;
        }
        for (int i = 0; i < count; i++) {
            free(batch[i].jpeg);
            batch[i].jpeg = NULL;
        }
        return;
    }
    question_t questions[QUESTION_MAX];
    int question_count = load_questions(questions, QUESTION_MAX);
    for (int i = 0; i < count; i++) {
        if (!either_index()) {
            discard_batch(batch + i, count - i);
            return;
        }
        cJSON *caption = NULL;
        if (index_enabled && openai_key[0]) {
            char response[3072];
            for (int attempt = 0; attempt < 4 && !caption && index_enabled; attempt++) {
                if (attempt > 0) {
                    vTaskDelay(pdMS_TO_TICKS(3000));
                }
                if (!index_enabled) {
                    break;
                }
                if (cambot_llm_openai(openai_key, NULL, "gpt-4.1-mini", batch[i].jpeg, batch[i].len, CAPTION_PROMPT,
                                       response, sizeof response) == ESP_OK) {
                    caption = parse_caption(response);
                }
            }
        }
        if (!either_index()) {
            cJSON_Delete(caption);
            discard_batch(batch + i, count - i);
            return;
        }
        char iso[40], day[16], hour[8];
        cJSON *watch = cJSON_CreateObject();
        cJSON *boxes = cJSON_CreateArray();
        bool have_watch = false;
        stamp(batch[i].when, iso, sizeof iso, day, sizeof day, hour, sizeof hour);
        if (jev_enabled && watch && boxes) {
            have_watch = ask_jev(batch[i].jpeg, batch[i].len, questions, question_count, watch, boxes, iso);
        }
        if (caption || have_watch) {
            write_record(&batch[i], caption, watch, boxes, have_watch);
            frames_today++;
        } else {
            printf("index=dropped\n");
            cJSON_Delete(watch);
            cJSON_Delete(boxes);
        }
        cJSON_Delete(caption);
        free(batch[i].jpeg);
        batch[i].jpeg = NULL;
    }
}

static void indexer_task(void *arg) {
    (void)arg;
    while (1) {
        keyframe_t item;
        if (xQueueReceive(keyframes, &item, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (!either_index()) {
            free(item.jpeg);
            continue;
        }
        /* One JEV call per frame. Waiting for a batch of 4 only delayed the score. */
        flush_batch(&item, 1);
    }
}

void cambot_indexer_submit(const uint8_t *jpeg, size_t len, time_t when, uint16_t motion_milli, uint32_t frame,
                            const char *seg) {
    if (!either_index() || !keyframes || !jpeg || len == 0 || !seg) {
        return;
    }
    uint8_t *copy = heap_caps_malloc(len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!copy) {
        return;
    }
    memcpy(copy, jpeg, len);
    keyframe_t item = {
        .jpeg = copy,
        .len = len,
        .when = when,
        .motion_milli = motion_milli,
        .frame = frame,
    };
    snprintf(item.seg, sizeof item.seg, "%s", seg);
    if (xQueueSend(keyframes, &item, 0) != pdTRUE) {
        keyframe_t stale;
        if (xQueueReceive(keyframes, &stale, 0) == pdTRUE) {
            free(stale.jpeg);
        }
        if (xQueueSend(keyframes, &item, 0) != pdTRUE) {
            free(copy);
        }
    }
}

static void *json_malloc(size_t size) {
    void *ptr = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    return ptr ? ptr : malloc(size);
}

void cambot_indexer_start(void) {
    /* cJSON trees are thousands of small nodes; in internal RAM they left too
     * little contiguous DMA memory for SD writes and Wi-Fi. */
    cJSON_Hooks hooks = {.malloc_fn = json_malloc, .free_fn = free};
    cJSON_InitHooks(&hooks);
    load_enabled();
    load_jev_enabled();
    printf("index=%s jev=%s\n", index_enabled ? "started" : "stopped", jev_enabled ? "started" : "stopped");
    latest_lock = xSemaphoreCreateMutex();
    recent_lock = xSemaphoreCreateMutex();
    keyframes = xQueueCreate(PENDING, sizeof(keyframe_t));
    if (!keyframes) {
        printf("index=fail err=queue\n");
        return;
    }
    if (xTaskCreatePinnedToCore(indexer_task, "indexer", 49152, NULL, 3, NULL, 0) != pdPASS) {
        xTaskCreate(indexer_task, "indexer", 49152, NULL, 3, NULL);
    }
}
