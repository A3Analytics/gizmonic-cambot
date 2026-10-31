#include "witness_recorder.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "cJSON.h"
#include "esp_heap_caps.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "witness_llm.h"
#include "witness_llm_client.h"

#define BATCH 4
#define BUDGET_FRAMES 2000
#define QUESTION_MAX 8

typedef struct {
    uint8_t *jpeg;
    size_t len;
    time_t when;
    uint16_t motion_milli;
    uint32_t frame;
    char seg[40];
} keyframe_t;

typedef struct {
    char name[48];
    char instructions[240];
    char label[96];
} question_t;

static QueueHandle_t keyframes;
static SemaphoreHandle_t latest_lock;
static char latest[2048];
static const char *openai_key = "";
static const char *jev_key = "";
static int frames_today;
static time_t budget_day;

static const char *CAPTION_PROMPT =
    "General camera setup. Testing basic functionality.\n"
    "Return only a JSON object with keys caption (string), objects (array of strings), "
    "actions (array of strings), and robot_state (string). No markdown.";

void witness_indexer_set_keys(const char *openai, const char *jev) {
    openai_key = openai ? openai : "";
    jev_key = jev ? jev : "";
}

bool witness_index_copy_latest(char *dst, size_t cap) {
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

static int load_questions(question_t *questions, int cap) {
    FILE *file = fopen(WITNESS_ROOT "/watch.json", "rb");
    if (!file) {
        return 0;
    }
    char raw[4096];
    size_t got = fread(raw, 1, sizeof raw - 1, file);
    fclose(file);
    raw[got] = '\0';
    cJSON *root = cJSON_Parse(raw);
    if (!cJSON_IsArray(root)) {
        cJSON_Delete(root);
        return 0;
    }
    int count = 0;
    cJSON *item = NULL;
    cJSON_ArrayForEach(item, root) {
        if (count >= cap || !cJSON_IsObject(item)) {
            continue;
        }
        const char *name = cJSON_GetStringValue(cJSON_GetObjectItem(item, "name"));
        const char *instructions = cJSON_GetStringValue(cJSON_GetObjectItem(item, "instructions"));
        const char *label = cJSON_GetStringValue(cJSON_GetObjectItem(item, "label"));
        if (!name || !instructions || !label || !name[0] || !instructions[0] || !label[0]) {
            continue;
        }
        snprintf(questions[count].name, sizeof questions[count].name, "%s", name);
        snprintf(questions[count].instructions, sizeof questions[count].instructions, "%s", instructions);
        snprintf(questions[count].label, sizeof questions[count].label, "%s", label);
        count++;
    }
    cJSON_Delete(root);
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

static bool ask_jev(const uint8_t *jpeg, size_t len, const question_t *questions, int count, cJSON *watch) {
    if (!jev_key[0] || count <= 0) {
        return false;
    }
    bool any = false;
    char response[2048];
    for (int i = 0; i < count; i++) {
        if (witness_llm_jev(jev_key, jpeg, len, questions[i].name, questions[i].instructions, response,
                            sizeof response) != ESP_OK) {
            continue;
        }
        cJSON *root = cJSON_Parse(response);
        cJSON *answers = cJSON_GetObjectItem(root, "answers");
        cJSON *answer = cJSON_GetObjectItem(answers, questions[i].name);
        cJSON *noul = cJSON_GetObjectItem(answer, "noul");
        if (cJSON_IsNumber(noul)) {
            cJSON_AddNumberToObject(watch, questions[i].label, noul->valuedouble);
            any = true;
        }
        cJSON_Delete(root);
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
    snprintf(dirpath, sizeof dirpath, WITNESS_ROOT "/index/%s", day);
    for (int hour = 0; hour < 24 && cJSON_GetArraySize(rows) < 256; hour++) {
        char path[192];
        snprintf(path, sizeof path, "%s/%02d.jsonl", dirpath, hour);
        FILE *file = fopen(path, "rb");
        if (!file) {
            continue;
        }
        char line[2048];
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
    mkdir(WITNESS_ROOT "/events", 0775);
    char path[160];
    snprintf(path, sizeof path, WITNESS_ROOT "/events/%s.jsonl", day);
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

static void write_record(const keyframe_t *item, cJSON *caption, cJSON *watch, bool have_watch) {
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
        cJSON_AddStringToObject(record, "model", "typesafe/" WITNESS_JEV_MODEL);
    }
    cJSON_AddItemToObject(record, "watch", watch);
    cJSON_AddBoolToObject(record, "verified", false);
    char *printed = cJSON_PrintUnformatted(record);
    if (!printed) {
        cJSON_Delete(record);
        return;
    }
    char dir[160];
    snprintf(dir, sizeof dir, WITNESS_ROOT "/index/%s", day);
    mkdir(WITNESS_ROOT "/index", 0775);
    mkdir(dir, 0775);
    char path[192];
    snprintf(path, sizeof path, "%s/%s.jsonl", dir, hour);
    if (append_line(path, printed)) {
        remember_latest(printed);
    }
    printf("index=ok t=%s motion_milli=%u model=%s\n", iso, (unsigned)item->motion_milli,
           caption ? "openai" : "jev");
    free(printed);
    cJSON_Delete(record);
    if (caption || have_watch) {
        rewrite_events(day);
    }
    (void)have_watch;
}

static void flush_batch(keyframe_t *batch, int count) {
    if (count <= 0) {
        return;
    }
    if (!budget_allows(count, batch[0].when)) {
        printf("index=paused frames_today=%d\n", frames_today);
        for (int i = 0; i < count; i++) {
            free(batch[i].jpeg);
            batch[i].jpeg = NULL;
        }
        return;
    }
    question_t questions[QUESTION_MAX];
    int question_count = load_questions(questions, QUESTION_MAX);
    for (int i = 0; i < count; i++) {
        cJSON *caption = NULL;
        if (openai_key[0]) {
            char response[3072];
            for (int attempt = 0; attempt < 4 && !caption; attempt++) {
                if (attempt > 0) {
                    vTaskDelay(pdMS_TO_TICKS(3000));
                }
                if (witness_llm_openai(openai_key, NULL, "gpt-4.1-mini", batch[i].jpeg, batch[i].len, CAPTION_PROMPT,
                                       response, sizeof response) == ESP_OK) {
                    caption = parse_caption(response);
                }
            }
        }
        cJSON *watch = cJSON_CreateObject();
        bool have_watch = ask_jev(batch[i].jpeg, batch[i].len, questions, question_count, watch);
        if (caption || have_watch) {
            write_record(&batch[i], caption, watch, have_watch);
            frames_today++;
        } else {
            printf("index=dropped\n");
            cJSON_Delete(watch);
        }
        cJSON_Delete(caption);
        free(batch[i].jpeg);
        batch[i].jpeg = NULL;
    }
}

static void indexer_task(void *arg) {
    (void)arg;
    keyframe_t batch[BATCH];
    int count = 0;
    while (1) {
        keyframe_t item;
        if (xQueueReceive(keyframes, &item, pdMS_TO_TICKS(count > 0 ? 1000 : 1000)) == pdTRUE) {
            batch[count++] = item;
            if (count >= BATCH) {
                flush_batch(batch, count);
                count = 0;
            }
        } else if (count > 0) {
            flush_batch(batch, count);
            count = 0;
        }
    }
}

void witness_indexer_submit(const uint8_t *jpeg, size_t len, time_t when, uint16_t motion_milli, uint32_t frame,
                            const char *seg) {
    if (!keyframes || !jpeg || len == 0 || !seg) {
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
        free(copy);
    }
}

void witness_indexer_start(void) {
    latest_lock = xSemaphoreCreateMutex();
    keyframes = xQueueCreate(BATCH, sizeof(keyframe_t));
    if (!keyframes) {
        printf("index=fail err=queue\n");
        return;
    }
    if (xTaskCreatePinnedToCore(indexer_task, "indexer", 32768, NULL, 3, NULL, 0) != pdPASS) {
        xTaskCreate(indexer_task, "indexer", 32768, NULL, 3, NULL);
    }
}
