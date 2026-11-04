#include "witness_recorder.h"

#pragma GCC diagnostic ignored "-Wformat-truncation"

#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <stdbool.h>
#include <sys/stat.h>

#include "cJSON.h"
#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "mbedtls/base64.h"
#include "witness_clock.h"
#include "witness_time.h"

static const char *bearer;

static bool authorized(httpd_req_t *req) {
    if (!bearer || bearer[0] == '\0') {
        return false;
    }
    char expected[192];
    int length = snprintf(expected, sizeof expected, "Bearer %s", bearer);
    if (length < 0 || length >= (int)sizeof expected) {
        return false;
    }
    size_t got_len = httpd_req_get_hdr_value_len(req, "Authorization");
    if (got_len != (size_t)length || got_len >= sizeof expected) {
        memset(expected, 0, sizeof expected);
        return false;
    }
    char got[192];
    bool ok = httpd_req_get_hdr_value_str(req, "Authorization", got, sizeof got) == ESP_OK &&
              memcmp(got, expected, (size_t)length) == 0;
    memset(expected, 0, sizeof expected);
    memset(got, 0, sizeof got);
    return ok;
}

static void deny(httpd_req_t *req) {
    httpd_resp_set_status(req, "401 Unauthorized");
    httpd_resp_sendstr(req, "unauthorized\n");
}

static int cmp_path(const void *left, const void *right) {
    return strcmp(*(char *const *)left, *(char *const *)right);
}

#define SEARCH_INDEX_MAX 200

static char **list_index_files(int *count) {
    *count = 0;
    int cap = 0;
    char **paths = NULL;
    DIR *days = opendir(WITNESS_ROOT "/index");
    if (!days) {
        return NULL;
    }
    struct dirent *day;
    while ((day = readdir(days)) != NULL) {
        if (day->d_name[0] == '.') {
            continue;
        }
        char day_path[192];
        snprintf(day_path, sizeof day_path, WITNESS_ROOT "/index/%s", day->d_name);
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
            if (*count == cap) {
                cap = cap ? cap * 2 : 8;
                char **grown = realloc(paths, (size_t)cap * sizeof(char *));
                if (!grown) {
                    closedir(hours);
                    closedir(days);
                    goto done;
                }
                paths = grown;
            }
            char full[224];
            snprintf(full, sizeof full, "%s/%s", day_path, hour->d_name);
            paths[(*count)++] = strdup(full);
        }
        closedir(hours);
    }
    closedir(days);
done:
    if (paths && *count > 1) {
        qsort(paths, (size_t)*count, sizeof(char *), cmp_path);
    }
    return paths;
}

static char **list_idx(int *count) {
    *count = 0;
    int cap = 0;
    char **paths = NULL;
    DIR *days = opendir(WITNESS_ROOT "/rec");
    if (!days) {
        return NULL;
    }
    struct dirent *day;
    while ((day = readdir(days)) != NULL) {
        if (day->d_name[0] == '.') {
            continue;
        }
        char day_path[160];
        snprintf(day_path, sizeof day_path, WITNESS_ROOT "/rec/%s", day->d_name);
        DIR *hours = opendir(day_path);
        if (!hours) {
            char idx[192];
            snprintf(idx, sizeof idx, "%s/unsynced.idx", day_path);
            FILE *probe = fopen(idx, "rb");
            if (probe) {
                fclose(probe);
                if (*count == cap) {
                    cap = cap ? cap * 2 : 8;
                    char **grown = realloc(paths, (size_t)cap * sizeof(char *));
                    if (!grown) {
                        break;
                    }
                    paths = grown;
                }
                paths[(*count)++] = strdup(idx);
            }
            continue;
        }
        struct dirent *hour;
        while ((hour = readdir(hours)) != NULL) {
            if (hour->d_name[0] == '.') {
                continue;
            }
            char hour_path[192];
            snprintf(hour_path, sizeof hour_path, "%s/%s", day_path, hour->d_name);
            DIR *files = opendir(hour_path);
            if (!files) {
                continue;
            }
            struct dirent *file;
            while ((file = readdir(files)) != NULL) {
                size_t name_len = strlen(file->d_name);
                if (name_len < 5 || strcmp(file->d_name + name_len - 4, ".idx") != 0) {
                    continue;
                }
                if (*count == cap) {
                    cap = cap ? cap * 2 : 8;
                    char **grown = realloc(paths, (size_t)cap * sizeof(char *));
                    if (!grown) {
                        closedir(files);
                        closedir(hours);
                        closedir(days);
                        goto done;
                    }
                    paths = grown;
                }
                char full[224];
                snprintf(full, sizeof full, "%s/%s", hour_path, file->d_name);
                paths[(*count)++] = strdup(full);
            }
            closedir(files);
        }
        closedir(hours);
    }
    closedir(days);
done:
    if (paths && *count > 1) {
        qsort(paths, (size_t)*count, sizeof(char *), cmp_path);
    }
    return paths;
}

static void free_paths(char **paths, int count) {
    for (int i = 0; i < count; i++) {
        free(paths[i]);
    }
    free(paths);
}

typedef void (*frame_fn)(const char *mjpg, uint64_t t_ms, uint32_t offset, uint32_t length, void *ctx);

static void each_synced_frame(frame_fn fn, void *ctx) {
    int count = 0;
    char **paths = list_idx(&count);
    for (int i = 0; i < count; i++) {
        FILE *idx = fopen(paths[i], "rb");
        if (!idx) {
            continue;
        }
        char mjpg[224];
        size_t len = strlen(paths[i]);
        if (len < 5 || len >= sizeof mjpg) {
            fclose(idx);
            continue;
        }
        memcpy(mjpg, paths[i], len - 4);
        memcpy(mjpg + len - 4, ".mjpg", 6);
        uint8_t raw[18];
        while (fread(raw, 1, sizeof raw, idx) == sizeof raw) {
            uint64_t t_ms;
            uint32_t offset, length;
            memcpy(&t_ms, raw, 8);
            memcpy(&offset, raw + 8, 4);
            memcpy(&length, raw + 12, 4);
            if (t_ms == 0) {
                continue;
            }
            fn(mjpg, t_ms, offset, length, ctx);
        }
        fclose(idx);
    }
    free_paths(paths, count);
}

static bool query_range(httpd_req_t *req, time_t *start, time_t *end) {
    char query[256];
    char from[80];
    char to[80];
    if (httpd_req_get_url_query_str(req, query, sizeof query) != ESP_OK) {
        return false;
    }
    if (httpd_query_key_value(query, "from", from, sizeof from) != ESP_OK ||
        httpd_query_key_value(query, "to", to, sizeof to) != ESP_OK) {
        return false;
    }
    time_t now = time(NULL);
    return witness_parse_bound(from, now, start) && witness_parse_bound(to, now, end);
}

static void count_minute(const char *mjpg, uint64_t t_ms, uint32_t offset, uint32_t length, void *ctx) {
    (void)mjpg;
    (void)offset;
    (void)length;
    cJSON *object = ctx;
    time_t sec = (time_t)(t_ms / 1000);
    struct tm utc;
    gmtime_r(&sec, &utc);
    char key[40];
    strftime(key, sizeof key, "%Y-%m-%dT%H:%M:00.000Z", &utc);
    cJSON *item = cJSON_GetObjectItem(object, key);
    if (!item) {
        cJSON_AddNumberToObject(object, key, 1);
    } else {
        item->valuedouble += 1;
        item->valueint += 1;
    }
}

static char *recordings_json(time_t start, time_t end) {
    cJSON *counts = cJSON_CreateObject();
    cJSON *rows = cJSON_CreateArray();
    if (!counts || !rows) {
        cJSON_Delete(counts);
        cJSON_Delete(rows);
        return NULL;
    }
    each_synced_frame(count_minute, counts);
    cJSON *item = NULL;
    cJSON_ArrayForEach(item, counts) {
        time_t stamp;
        if (!item->string || !witness_parse_bound(item->string, time(NULL), &stamp)) {
            continue;
        }
        if (stamp < start || stamp > end) {
            continue;
        }
        cJSON *row = cJSON_CreateObject();
        cJSON_AddStringToObject(row, "minute", item->string);
        cJSON_AddNumberToObject(row, "frames", item->valuedouble);
        cJSON_AddItemToArray(rows, row);
    }
    char *printed = cJSON_PrintUnformatted(rows);
    cJSON_Delete(rows);
    cJSON_Delete(counts);
    return printed;
}

static esp_err_t recordings_get(httpd_req_t *req) {
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    time_t start, end;
    if (!query_range(req, &start, &end) || end < start) {
        httpd_resp_set_type(req, "application/json");
        return httpd_resp_sendstr(req, "[]");
    }
    char *printed = recordings_json(start, end);
    httpd_resp_set_type(req, "application/json");
    esp_err_t err = httpd_resp_sendstr(req, printed ? printed : "[]");
    free(printed);
    return err;
}

struct nearest_ctx {
    time_t target;
    int64_t gap;
    char mjpg[224];
    uint32_t offset;
    uint32_t length;
    bool found;
};

static void consider_nearest(const char *mjpg, uint64_t t_ms, uint32_t offset, uint32_t length, void *ctx) {
    struct nearest_ctx *best = ctx;
    int64_t gap = (int64_t)(t_ms / 1000) - (int64_t)best->target;
    if (gap < 0) {
        gap = -gap;
    }
    if (!best->found || gap < best->gap) {
        best->found = true;
        best->gap = gap;
        best->offset = offset;
        best->length = length;
        snprintf(best->mjpg, sizeof best->mjpg, "%s", mjpg);
    }
}

static int hex_nibble(char ch) {
    if (ch >= '0' && ch <= '9') {
        return ch - '0';
    }
    if (ch >= 'a' && ch <= 'f') {
        return ch - 'a' + 10;
    }
    if (ch >= 'A' && ch <= 'F') {
        return ch - 'A' + 10;
    }
    return -1;
}

static void url_decode(char *text) {
    char *write = text;
    for (char *read = text; *read; read++) {
        if (*read == '%' && hex_nibble(read[1]) >= 0 && hex_nibble(read[2]) >= 0) {
            *write++ = (char)((hex_nibble(read[1]) << 4) | hex_nibble(read[2]));
            read += 2;
            continue;
        }
        *write++ = *read;
    }
    *write = '\0';
}

static esp_err_t frame_at_get(httpd_req_t *req) {
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    const char *prefix = "/api/v1/frame/";
    if (strncmp(req->uri, prefix, strlen(prefix)) != 0) {
        httpd_resp_send_404(req);
        return ESP_OK;
    }
    char stamp[80];
    snprintf(stamp, sizeof stamp, "%s", req->uri + strlen(prefix));
    char *dot = strstr(stamp, ".jpg");
    if (dot) {
        *dot = '\0';
    }
    url_decode(stamp);
    time_t when;
    if (!witness_parse_bound(stamp, time(NULL), &when)) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad time");
        return ESP_OK;
    }
    struct nearest_ctx best = {.target = when};
    each_synced_frame(consider_nearest, &best);
    if (!best.found || best.length == 0 || best.length > 200000) {
        httpd_resp_send_404(req);
        return ESP_OK;
    }
    FILE *file = fopen(best.mjpg, "rb");
    if (!file) {
        httpd_resp_send_404(req);
        return ESP_OK;
    }
    uint8_t *jpeg = malloc(best.length);
    if (!jpeg || fseek(file, (long)best.offset, SEEK_SET) != 0 || fread(jpeg, 1, best.length, file) != best.length) {
        free(jpeg);
        fclose(file);
        httpd_resp_send_500(req);
        return ESP_OK;
    }
    fclose(file);
    httpd_resp_set_type(req, "image/jpeg");
    esp_err_t err = httpd_resp_send(req, (const char *)jpeg, (ssize_t)best.length);
    free(jpeg);
    return err;
}

struct clip_ctx {
    httpd_req_t *req;
    time_t start;
    time_t end;
    esp_err_t err;
};

static void send_clip_frame(const char *mjpg, uint64_t t_ms, uint32_t offset, uint32_t length, void *ctx) {
    struct clip_ctx *clip = ctx;
    time_t sec = (time_t)(t_ms / 1000);
    if (clip->err != ESP_OK || sec < clip->start || sec > clip->end || length == 0 || length > 200000) {
        return;
    }
    FILE *file = fopen(mjpg, "rb");
    if (!file) {
        return;
    }
    uint8_t *jpeg = malloc(length);
    if (jpeg && fseek(file, (long)offset, SEEK_SET) == 0 && fread(jpeg, 1, length, file) == length) {
        clip->err = httpd_resp_send_chunk(clip->req, (const char *)jpeg, (ssize_t)length);
    }
    free(jpeg);
    fclose(file);
}

static esp_err_t clip_get(httpd_req_t *req) {
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    time_t start, end;
    if (!query_range(req, &start, &end)) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad range");
        return ESP_OK;
    }
    httpd_resp_set_type(req, "multipart/x-mixed-replace");
    struct clip_ctx clip = {.req = req, .start = start, .end = end, .err = ESP_OK};
    each_synced_frame(send_clip_frame, &clip);
    return httpd_resp_send_chunk(req, NULL, 0);
}

static esp_err_t status_get(httpd_req_t *req) {
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    char body[192];
    snprintf(body, sizeof body,
             "{\"time_synced\":%s,\"recording\":%s,\"sd\":\"%s\"}",
             witness_time_synced() ? "true" : "false", witness_sd_ready() ? "true" : "false",
             witness_sd_ready() ? "ok" : "none");
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, body);
}

static char *read_body(httpd_req_t *req) {
    if (req->content_len <= 0 || req->content_len > 8192) {
        return NULL;
    }
    char *body = malloc((size_t)req->content_len + 1);
    if (!body) {
        return NULL;
    }
    int got = 0;
    while (got < req->content_len) {
        int n = httpd_req_recv(req, body + got, (size_t)req->content_len - (size_t)got);
        if (n <= 0) {
            free(body);
            return NULL;
        }
        got += n;
    }
    body[got] = '\0';
    return body;
}

static cJSON *tool_result(cJSON *content, bool error) {
    cJSON *result = cJSON_CreateObject();
    cJSON_AddItemToObject(result, "content", content);
    cJSON_AddBoolToObject(result, "isError", error);
    return result;
}

static cJSON *text_content(const char *text) {
    cJSON *block = cJSON_CreateObject();
    cJSON_AddStringToObject(block, "type", "text");
    cJSON_AddStringToObject(block, "text", text);
    cJSON *array = cJSON_CreateArray();
    cJSON_AddItemToArray(array, block);
    return array;
}

static const char *TOOLS =
    "[{\"name\":\"get_status\"},{\"name\":\"list_recordings\"},{\"name\":\"search_index\"},"
    "{\"name\":\"inspect_window\"},{\"name\":\"make_clip\"},{\"name\":\"get_live_frame\"}]";

static cJSON *call_tool(const char *name, cJSON *arguments) {
    if (strcmp(name, "get_status") == 0) {
        char body[192];
        snprintf(body, sizeof body, "{\"time_synced\":%s,\"recording\":%s,\"sd\":\"%s\"}",
                 witness_time_synced() ? "true" : "false", witness_sd_ready() ? "true" : "false",
                 witness_sd_ready() ? "ok" : "none");
        return tool_result(text_content(body), false);
    }
    if (strcmp(name, "make_clip") == 0) {
        const char *from = cJSON_GetStringValue(cJSON_GetObjectItem(arguments, "from"));
        const char *to = cJSON_GetStringValue(cJSON_GetObjectItem(arguments, "to"));
        if (!from || !to) {
            return tool_result(text_content("invalid params"), true);
        }
        char body[256];
        snprintf(body, sizeof body, "{\"clip_url\":\"/api/v1/clip.mjpg?from=%s&to=%s\"}", from, to);
        return tool_result(text_content(body), false);
    }
    if (strcmp(name, "list_recordings") == 0) {
        const char *from = cJSON_GetStringValue(cJSON_GetObjectItem(arguments, "from"));
        const char *to = cJSON_GetStringValue(cJSON_GetObjectItem(arguments, "to"));
        time_t start, end, now = time(NULL);
        if (!from || !to || !witness_parse_bound(from, now, &start) || !witness_parse_bound(to, now, &end)) {
            return tool_result(text_content("[]"), true);
        }
        char *printed = recordings_json(start, end);
        cJSON *result = tool_result(text_content(printed ? printed : "[]"), false);
        free(printed);
        return result;
    }
    if (strcmp(name, "search_index") == 0) {
        const char *query = cJSON_GetStringValue(cJSON_GetObjectItem(arguments, "query"));
        cJSON *rows = cJSON_CreateArray();
        char *line = malloc(4096);
        int file_count = 0;
        char **files = list_index_files(&file_count);
        /* Keep only the newest SEARCH_INDEX_MAX matches so old GPT captions cannot
         * exhaust heap before today's JEV watch rows are reached. */
        for (int i = 0; line && files && i < file_count; i++) {
            FILE *file = fopen(files[i], "r");
            if (!file) {
                continue;
            }
            while (fgets(line, 4096, file)) {
                if (query && query[0] && strcasestr(line, query) == NULL) {
                    continue;
                }
                cJSON *row = cJSON_Parse(line);
                if (!row) {
                    continue;
                }
                cJSON_AddItemToArray(rows, row);
                while (cJSON_GetArraySize(rows) > SEARCH_INDEX_MAX) {
                    cJSON *drop = cJSON_DetachItemFromArray(rows, 0);
                    cJSON_Delete(drop);
                }
            }
            fclose(file);
        }
        for (int i = 0; files && i < file_count; i++) {
            free(files[i]);
        }
        free(files);
        free(line);
        char *printed = cJSON_PrintUnformatted(rows);
        cJSON_Delete(rows);
        cJSON *result = tool_result(text_content(printed ? printed : "[]"), false);
        free(printed);
        return result;
    }
    if (strcmp(name, "get_live_frame") == 0 || strcmp(name, "inspect_window") == 0) {
        SemaphoreHandle_t lock = witness_recorder_lock();
        if (!lock || xSemaphoreTake(lock, pdMS_TO_TICKS(200)) != pdTRUE) {
            return tool_result(text_content("camera busy"), true);
        }
        camera_fb_t *frame = esp_camera_fb_get();
        xSemaphoreGive(lock);
        if (!frame) {
            return tool_result(text_content("no frame"), true);
        }
        size_t cap = 0;
        mbedtls_base64_encode(NULL, 0, &cap, frame->buf, frame->len);
        unsigned char *encoded = malloc(cap + 1);
        cJSON *content = cJSON_CreateArray();
        if (encoded && mbedtls_base64_encode(encoded, cap, &cap, frame->buf, frame->len) == 0) {
            encoded[cap] = '\0';
            cJSON *block = cJSON_CreateObject();
            cJSON_AddStringToObject(block, "type", "image");
            cJSON_AddStringToObject(block, "mimeType", "image/jpeg");
            cJSON_AddStringToObject(block, "data", (char *)encoded);
            cJSON_AddItemToArray(content, block);
        }
        free(encoded);
        esp_camera_fb_return(frame);
        return tool_result(content, false);
    }
    return tool_result(text_content("unknown tool"), true);
}

static esp_err_t mcp_post(httpd_req_t *req) {
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    char *raw = read_body(req);
    cJSON *message = raw ? cJSON_Parse(raw) : NULL;
    free(raw);
    if (!message) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "parse error");
        return ESP_OK;
    }
    const char *method = cJSON_GetStringValue(cJSON_GetObjectItem(message, "method"));
    cJSON *rpc_id = cJSON_GetObjectItem(message, "id");
    if (method && strncmp(method, "notifications/", 14) == 0) {
        cJSON_Delete(message);
        httpd_resp_set_status(req, "202 Accepted");
        return httpd_resp_send(req, "", 0);
    }
    cJSON *response = cJSON_CreateObject();
    cJSON_AddStringToObject(response, "jsonrpc", "2.0");
    if (rpc_id) {
        cJSON_AddItemToObject(response, "id", cJSON_Duplicate(rpc_id, true));
    }
    if (method && strcmp(method, "initialize") == 0) {
        cJSON *result = cJSON_CreateObject();
        cJSON_AddStringToObject(result, "protocolVersion", "2025-03-26");
        cJSON *caps = cJSON_CreateObject();
        cJSON_AddItemToObject(caps, "tools", cJSON_CreateObject());
        cJSON_AddItemToObject(result, "capabilities", caps);
        cJSON *info = cJSON_CreateObject();
        cJSON_AddStringToObject(info, "name", "witness");
        cJSON_AddStringToObject(info, "version", "0.1.0");
        cJSON_AddItemToObject(result, "serverInfo", info);
        cJSON_AddItemToObject(response, "result", result);
    } else if (method && strcmp(method, "tools/list") == 0) {
        cJSON *result = cJSON_CreateObject();
        cJSON *tools = cJSON_Parse(TOOLS);
        cJSON_AddItemToObject(result, "tools", tools ? tools : cJSON_CreateArray());
        cJSON_AddItemToObject(response, "result", result);
    } else if (method && strcmp(method, "tools/call") == 0) {
        cJSON *params = cJSON_GetObjectItem(message, "params");
        const char *name = cJSON_GetStringValue(cJSON_GetObjectItem(params, "name"));
        cJSON *arguments = cJSON_GetObjectItem(params, "arguments");
        cJSON_AddItemToObject(response, "result", call_tool(name ? name : "", arguments));
    } else {
        cJSON *error = cJSON_CreateObject();
        cJSON_AddNumberToObject(error, "code", -32601);
        cJSON_AddStringToObject(error, "message", "method not found");
        cJSON_AddItemToObject(response, "error", error);
    }
    char *printed = cJSON_PrintUnformatted(response);
    httpd_resp_set_type(req, "application/json");
    esp_err_t err = httpd_resp_sendstr(req, printed ? printed : "{}");
    free(printed);
    cJSON_Delete(response);
    cJSON_Delete(message);
    return err;
}

static esp_err_t indexing_get(httpd_req_t *req) {
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    char body[32];
    snprintf(body, sizeof body, "{\"enabled\":%s}", witness_indexer_enabled() ? "true" : "false");
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, body);
}

static esp_err_t indexing_post(httpd_req_t *req) {
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    char *raw = read_body(req);
    cJSON *body = raw ? cJSON_Parse(raw) : NULL;
    free(raw);
    cJSON *flag = body ? cJSON_GetObjectItem(body, "enabled") : NULL;
    if (!cJSON_IsBool(flag)) {
        cJSON_Delete(body);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "enabled must be a boolean");
        return ESP_OK;
    }
    if (!witness_indexer_set_enabled(cJSON_IsTrue(flag))) {
        cJSON_Delete(body);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "could not store indexing state");
        return ESP_OK;
    }
    cJSON_Delete(body);
    char reply[32];
    snprintf(reply, sizeof reply, "{\"enabled\":%s}", witness_indexer_enabled() ? "true" : "false");
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, reply);
}

static esp_err_t latest_get(httpd_req_t *req) {
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    char *body = malloc(4096);
    esp_err_t err;
    if (!body) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "could not read index");
        return ESP_OK;
    }
    if (!witness_index_copy_latest(body, 4096)) {
        free(body);
        httpd_resp_set_status(req, "204 No Content");
        return httpd_resp_send(req, "", 0);
    }
    httpd_resp_set_type(req, "application/json");
    err = httpd_resp_sendstr(req, body);
    free(body);
    return err;
}

static esp_err_t jev_get(httpd_req_t *req) {
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    char body[32];
    snprintf(body, sizeof body, "{\"enabled\":%s}", witness_jev_enabled() ? "true" : "false");
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, body);
}

static esp_err_t jev_post(httpd_req_t *req) {
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    char *raw = read_body(req);
    cJSON *body = raw ? cJSON_Parse(raw) : NULL;
    free(raw);
    cJSON *flag = body ? cJSON_GetObjectItem(body, "enabled") : NULL;
    if (!cJSON_IsBool(flag)) {
        cJSON_Delete(body);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "enabled must be a boolean");
        return ESP_OK;
    }
    if (!witness_jev_set_enabled(cJSON_IsTrue(flag))) {
        cJSON_Delete(body);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "could not store jev state");
        return ESP_OK;
    }
    cJSON_Delete(body);
    char reply[32];
    snprintf(reply, sizeof reply, "{\"enabled\":%s}", witness_jev_enabled() ? "true" : "false");
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, reply);
}

static char *read_body_limit(httpd_req_t *req, size_t limit) {
    char *body;
    int got = 0;
    if (req->content_len <= 0 || (size_t)req->content_len > limit) {
        return NULL;
    }
    body = heap_caps_malloc((size_t)req->content_len + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!body) {
        body = malloc((size_t)req->content_len + 1);
    }
    if (!body) {
        return NULL;
    }
    while (got < req->content_len) {
        int n = httpd_req_recv(req, body + got, (size_t)req->content_len - (size_t)got);
        if (n <= 0) {
            free(body);
            return NULL;
        }
        got += n;
    }
    body[got] = '\0';
    return body;
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

static int short_text(const char *text, size_t cap) {
    return text && text[0] && strlen(text) < cap;
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

static int https_url(const char *url) {
    size_t length;
    if (!url || strncmp(url, "https://", 8) != 0) {
        return 0;
    }
    length = strlen(url);
    if (length < 12 || length > 299) {
        return 0;
    }
    for (size_t i = 0; i < length; i++) {
        unsigned char ch = (unsigned char)url[i];
        if (ch <= ' ' || ch == '"' || ch == '\\') {
            return 0;
        }
    }
    return 1;
}

static int names_include(cJSON *array, const char *name) {
    cJSON *item = NULL;
    cJSON_ArrayForEach(item, array) {
        const char *have = cJSON_GetStringValue(cJSON_GetObjectItem(item, "name"));
        if (have && strcmp(have, name) == 0) {
            return 1;
        }
    }
    return 0;
}

static const char *nouls_clean(cJSON *root, cJSON **out) {
    cJSON *clean;
    cJSON *item = NULL;
    *out = NULL;
    if (!cJSON_IsArray(root)) {
        return "json array required";
    }
    if (cJSON_GetArraySize(root) > 8) {
        return "too many nouls";
    }
    clean = cJSON_CreateArray();
    if (!clean) {
        return "could not store nouls";
    }
    cJSON_ArrayForEach(item, root) {
        const char *name;
        const char *label;
        const char *instructions;
        cJSON *copy;
        cJSON *examples;
        cJSON *example = NULL;
        cJSON *copied_examples;
        cJSON *box;
        cJSON *alert;
        const char *url;
        if (!cJSON_IsObject(item)) {
            cJSON_Delete(clean);
            return "each noul must be an object";
        }
        name = cJSON_GetStringValue(cJSON_GetObjectItem(item, "name"));
        label = cJSON_GetStringValue(cJSON_GetObjectItem(item, "label"));
        instructions = cJSON_GetStringValue(cJSON_GetObjectItem(item, "instructions"));
        if (!plain_id(name) || !short_text(label, 96) || !short_text(instructions, 240)) {
            cJSON_Delete(clean);
            return "noul name, label, and instructions are required";
        }
        if (names_include(clean, name)) {
            cJSON_Delete(clean);
            return "noul names must be unique";
        }
        copy = cJSON_CreateObject();
        copied_examples = cJSON_CreateArray();
        cJSON_AddStringToObject(copy, "name", name);
        cJSON_AddStringToObject(copy, "label", label);
        cJSON_AddStringToObject(copy, "instructions", instructions);
        cJSON_AddItemToObject(copy, "examples", copied_examples);
        examples = cJSON_GetObjectItem(item, "examples");
        if (examples && !cJSON_IsArray(examples)) {
            cJSON_Delete(copy);
            cJSON_Delete(clean);
            return "examples must be a list";
        }
        if (cJSON_IsArray(examples) && cJSON_GetArraySize(examples) > 4) {
            cJSON_Delete(copy);
            cJSON_Delete(clean);
            return "too many examples";
        }
        cJSON_ArrayForEach(example, examples) {
            const char *id = cJSON_GetStringValue(cJSON_GetObjectItem(example, "id"));
            const char *polarity = cJSON_GetStringValue(cJSON_GetObjectItem(example, "polarity"));
            cJSON *one;
            if (!plain_id(id)) {
                cJSON_Delete(copy);
                cJSON_Delete(clean);
                return "example id is invalid";
            }
            if (!polarity || (strcmp(polarity, "yes") != 0 && strcmp(polarity, "no") != 0)) {
                cJSON_Delete(copy);
                cJSON_Delete(clean);
                return "example polarity must be yes or no";
            }
            one = cJSON_CreateObject();
            cJSON_AddStringToObject(one, "id", id);
            cJSON_AddStringToObject(one, "polarity", polarity);
            cJSON_AddItemToArray(copied_examples, one);
        }
        box = cJSON_GetObjectItem(item, "box");
        if (cJSON_IsObject(box)) {
            cJSON *enabled = cJSON_GetObjectItem(box, "enabled");
            const char *color = cJSON_GetStringValue(cJSON_GetObjectItem(box, "color"));
            const char *box_instructions = cJSON_GetStringValue(cJSON_GetObjectItem(box, "instructions"));
            cJSON *copied = cJSON_CreateObject();
            int on = cJSON_IsTrue(enabled);
            if (box_instructions && strlen(box_instructions) >= 240) {
                cJSON_Delete(copied);
                cJSON_Delete(copy);
                cJSON_Delete(clean);
                return "box instructions are too long";
            }
            if (on && !short_text(box_instructions, 240)) {
                cJSON_Delete(copied);
                cJSON_Delete(copy);
                cJSON_Delete(clean);
                return "box instructions are required";
            }
            cJSON_AddBoolToObject(copied, "enabled", on);
            cJSON_AddStringToObject(copied, "color", hex_color(color) ? color : "#16a34a");
            cJSON_AddStringToObject(copied, "instructions", box_instructions ? box_instructions : "");
            cJSON_AddItemToObject(copy, "box", copied);
        }
        alert = cJSON_GetObjectItem(item, "alert");
        url = cJSON_IsObject(alert) ? cJSON_GetStringValue(cJSON_GetObjectItem(alert, "url")) : NULL;
        if (url && url[0]) {
            const char *when = cJSON_GetStringValue(cJSON_GetObjectItem(alert, "when"));
            cJSON *copied;
            if (!https_url(url)) {
                cJSON_Delete(copy);
                cJSON_Delete(clean);
                return "alert url must be https";
            }
            if (when && strcmp(when, "yes") != 0 && strcmp(when, "no") != 0) {
                cJSON_Delete(copy);
                cJSON_Delete(clean);
                return "alert when must be yes or no";
            }
            copied = cJSON_CreateObject();
            cJSON_AddStringToObject(copied, "when", when && strcmp(when, "no") == 0 ? "no" : "yes");
            cJSON_AddStringToObject(copied, "url", url);
            cJSON_AddItemToObject(copy, "alert", copied);
        }
        cJSON_AddItemToArray(clean, copy);
    }
    *out = clean;
    return NULL;
}

static esp_err_t nouls_get(httpd_req_t *req) {
    FILE *file;
    char *raw;
    size_t got;
    esp_err_t err;
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    file = fopen(WITNESS_NOULS_JSON, "rb");
    if (!file) {
        httpd_resp_set_type(req, "application/json");
        return httpd_resp_sendstr(req, "[]");
    }
    raw = malloc(32769);
    if (!raw) {
        fclose(file);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "could not read nouls");
        return ESP_OK;
    }
    got = fread(raw, 1, 32768, file);
    fclose(file);
    raw[got] = '\0';
    httpd_resp_set_type(req, "application/json");
    err = httpd_resp_sendstr(req, raw);
    free(raw);
    return err;
}

static esp_err_t nouls_put(httpd_req_t *req) {
    char *raw;
    cJSON *body;
    cJSON *clean = NULL;
    const char *error;
    char *printed;
    FILE *file;
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    raw = read_body_limit(req, 32768);
    body = raw ? cJSON_Parse(raw) : NULL;
    free(raw);
    error = body ? nouls_clean(body, &clean) : "invalid json";
    cJSON_Delete(body);
    if (error) {
        cJSON_Delete(clean);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, error);
        return ESP_OK;
    }
    printed = cJSON_PrintUnformatted(clean);
    cJSON_Delete(clean);
    if (!printed) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "could not store nouls");
        return ESP_OK;
    }
    mkdir(WITNESS_ROOT, 0775);
    file = fopen(WITNESS_NOULS_JSON ".tmp", "wb");
    if (!file || fputs(printed, file) < 0) {
        if (file) {
            fclose(file);
        }
        free(printed);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "could not store nouls");
        return ESP_OK;
    }
    fclose(file);
    free(printed);
    remove(WITNESS_NOULS_JSON);
    if (rename(WITNESS_NOULS_JSON ".tmp", WITNESS_NOULS_JSON) != 0) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "could not store nouls");
        return ESP_OK;
    }
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, "{\"ok\":true}");
}

static int example_ids(const httpd_req_t *req, char *name, size_t name_cap, char *id, size_t id_cap) {
    char path[160];
    const char *query = strchr(req->uri, '?');
    size_t length = query ? (size_t)(query - req->uri) : strlen(req->uri);
    const char *prefix = "/api/v1/nouls/";
    const char *rest;
    const char *mark;
    const char *example;
    size_t name_len;
    if (length == 0 || length >= sizeof path || strncmp(req->uri, prefix, strlen(prefix)) != 0) {
        return -1;
    }
    memcpy(path, req->uri, length);
    path[length] = '\0';
    rest = path + strlen(prefix);
    mark = strstr(rest, "/examples/");
    if (!mark || mark == rest) {
        return -1;
    }
    name_len = (size_t)(mark - rest);
    if (name_len == 0 || name_len >= name_cap) {
        return -1;
    }
    memcpy(name, rest, name_len);
    name[name_len] = '\0';
    example = mark + strlen("/examples/");
    if (!example[0] || strchr(example, '/') || strlen(example) >= id_cap) {
        return -1;
    }
    snprintf(id, id_cap, "%s", example);
    if (!plain_id(name) || !plain_id(id)) {
        return -1;
    }
    return 0;
}

static void example_path(const char *name, const char *id, char *path, size_t cap) {
    snprintf(path, cap, WITNESS_ROOT "/nouls/%s/%s.jpg", name, id);
}

static esp_err_t noul_example_get(httpd_req_t *req) {
    char name[32];
    char id[32];
    char path[160];
    FILE *file;
    long size;
    char *body;
    esp_err_t err;
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    if (example_ids(req, name, sizeof name, id, sizeof id) != 0) {
        httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "not found");
        return ESP_OK;
    }
    example_path(name, id, path, sizeof path);
    file = fopen(path, "rb");
    if (!file) {
        httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "not found");
        return ESP_OK;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "could not read example");
        return ESP_OK;
    }
    size = ftell(file);
    if (size <= 0 || (size_t)size > WITNESS_NOUL_JPEG_MAX || fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        httpd_resp_send_err(req, HTTPD_404_NOT_FOUND, "not found");
        return ESP_OK;
    }
    body = heap_caps_malloc((size_t)size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!body) {
        body = malloc((size_t)size);
    }
    if (!body || fread(body, 1, (size_t)size, file) != (size_t)size) {
        free(body);
        fclose(file);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "could not read example");
        return ESP_OK;
    }
    fclose(file);
    httpd_resp_set_type(req, "image/jpeg");
    err = httpd_resp_send(req, body, (ssize_t)size);
    free(body);
    return err;
}

static esp_err_t noul_example_put(httpd_req_t *req) {
    char name[32];
    char id[32];
    char dir[128];
    char path[160];
    char *body;
    FILE *file;
    size_t len;
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    if (example_ids(req, name, sizeof name, id, sizeof id) != 0) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "example path is invalid");
        return ESP_OK;
    }
    body = read_body_limit(req, WITNESS_NOUL_JPEG_MAX);
    len = body ? (size_t)req->content_len : 0;
    if (!body || len < 3 || (unsigned char)body[0] != 0xff || (unsigned char)body[1] != 0xd8) {
        free(body);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "example must be a JPEG under 80KB");
        return ESP_OK;
    }
    mkdir(WITNESS_ROOT, 0775);
    mkdir(WITNESS_ROOT "/nouls", 0775);
    snprintf(dir, sizeof dir, WITNESS_ROOT "/nouls/%s", name);
    mkdir(dir, 0775);
    example_path(name, id, path, sizeof path);
    file = fopen(path, "wb");
    if (!file || fwrite(body, 1, len, file) != len) {
        if (file) {
            fclose(file);
        }
        free(body);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "could not store example");
        return ESP_OK;
    }
    fclose(file);
    free(body);
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, "{\"ok\":true}");
}

void witness_http_register(httpd_handle_t server, const char *token) {
    bearer = token;
    httpd_uri_t routes[] = {
        {.uri = "/api/v1/status", .method = HTTP_GET, .handler = status_get},
        {.uri = "/api/v1/recordings", .method = HTTP_GET, .handler = recordings_get},
        {.uri = "/api/v1/frame/*", .method = HTTP_GET, .handler = frame_at_get},
        {.uri = "/api/v1/clip.mjpg", .method = HTTP_GET, .handler = clip_get},
        {.uri = "/api/v1/index/latest", .method = HTTP_GET, .handler = latest_get},
        {.uri = "/api/v1/indexing", .method = HTTP_GET, .handler = indexing_get},
        {.uri = "/api/v1/indexing", .method = HTTP_POST, .handler = indexing_post},
        {.uri = "/api/v1/jev", .method = HTTP_GET, .handler = jev_get},
        {.uri = "/api/v1/jev", .method = HTTP_POST, .handler = jev_post},
        {.uri = "/api/v1/nouls", .method = HTTP_GET, .handler = nouls_get},
        {.uri = "/api/v1/nouls", .method = HTTP_PUT, .handler = nouls_put},
        {.uri = "/api/v1/nouls/*", .method = HTTP_GET, .handler = noul_example_get},
        {.uri = "/api/v1/nouls/*", .method = HTTP_PUT, .handler = noul_example_put},
        {.uri = "/mcp", .method = HTTP_POST, .handler = mcp_post},
    };
    for (size_t i = 0; i < sizeof routes / sizeof routes[0]; i++) {
        httpd_register_uri_handler(server, &routes[i]);
    }
    printf("httpd=routes recordings,clip,mcp\n");
}
