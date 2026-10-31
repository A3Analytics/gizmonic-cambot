#include "witness_recorder.h"

#pragma GCC diagnostic ignored "-Wformat-truncation"

#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <stdbool.h>

#include "cJSON.h"
#include "esp_camera.h"
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
        DIR *days = opendir(WITNESS_ROOT "/index");
        if (days) {
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
                    char file_path[224];
                    snprintf(file_path, sizeof file_path, "%s/%s", day_path, hour->d_name);
                    FILE *file = fopen(file_path, "r");
                    if (!file) {
                        continue;
                    }
                    char line[512];
                    while (fgets(line, sizeof line, file)) {
                        if (query && query[0] && strcasestr(line, query) == NULL) {
                            continue;
                        }
                        cJSON *row = cJSON_Parse(line);
                        if (row) {
                            cJSON_AddItemToArray(rows, row);
                        }
                    }
                    fclose(file);
                }
                closedir(hours);
            }
            closedir(days);
        }
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

static esp_err_t latest_get(httpd_req_t *req) {
    if (!authorized(req)) {
        deny(req);
        return ESP_OK;
    }
    char body[2048];
    if (!witness_index_copy_latest(body, sizeof body)) {
        httpd_resp_set_status(req, "204 No Content");
        return httpd_resp_send(req, "", 0);
    }
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, body);
}

void witness_http_register(httpd_handle_t server, const char *token) {
    bearer = token;
    httpd_uri_t routes[] = {
        {.uri = "/api/v1/status", .method = HTTP_GET, .handler = status_get},
        {.uri = "/api/v1/recordings", .method = HTTP_GET, .handler = recordings_get},
        {.uri = "/api/v1/frame/*", .method = HTTP_GET, .handler = frame_at_get},
        {.uri = "/api/v1/clip.mjpg", .method = HTTP_GET, .handler = clip_get},
        {.uri = "/api/v1/index/latest", .method = HTTP_GET, .handler = latest_get},
        {.uri = "/mcp", .method = HTTP_POST, .handler = mcp_post},
    };
    for (size_t i = 0; i < sizeof routes / sizeof routes[0]; i++) {
        httpd_register_uri_handler(server, &routes[i]);
    }
    printf("httpd=routes recordings,clip,mcp\n");
}
