/* Setup test. Joins Wi-Fi, serves the live frame, and registers with the
 * computer running fixr/registrar/serve.py. Credentials are in secrets.h,
 * which is not committed. Freeze this file once that path works.
 */

#include <stdio.h>
#include <string.h>

#include "esp_camera.h"
#include "esp_chip_info.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_flash.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_http_server.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "secrets.h"
#include "witness_llm_client.h"
#include "witness_recorder.h"
#include "witness_time.h"

#define WIFI_CONNECTED_BIT BIT0

/* Pins from reference/from-lloyd-barbot/camera_pins.h.
 * Y2..Y9 are DVP D0..D7. Same wiring as CAMERA_MODEL_ESP32S3_EYE. */
static const camera_config_t camera_cfg = {
    .pin_pwdn = -1,
    .pin_reset = -1,
    .pin_xclk = 15,
    .pin_sccb_sda = 4,
    .pin_sccb_scl = 5,
    .pin_d7 = 16,
    .pin_d6 = 17,
    .pin_d5 = 18,
    .pin_d4 = 12,
    .pin_d3 = 10,
    .pin_d2 = 8,
    .pin_d1 = 9,
    .pin_d0 = 11,
    .pin_vsync = 6,
    .pin_href = 7,
    .pin_pclk = 13,
    .xclk_freq_hz = 20000000,
    .ledc_timer = LEDC_TIMER_0,
    .ledc_channel = LEDC_CHANNEL_0,
    .pixel_format = PIXFORMAT_JPEG,
    .frame_size = FRAMESIZE_QVGA,
    .jpeg_quality = 12,
    .fb_count = 2,
    .fb_location = CAMERA_FB_IN_PSRAM,
    .grab_mode = CAMERA_GRAB_LATEST,
};

static EventGroupHandle_t wifi_events;
static SemaphoreHandle_t camera_lock;
static char camera_id[20];

static void make_id(void) {
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(camera_id, sizeof camera_id, "witness-%02x%02x", mac[4], mac[5]);
}

static void on_wifi(void *arg, esp_event_base_t base, int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *event = data;
        xEventGroupClearBits(wifi_events, WIFI_CONNECTED_BIT);
        printf("wifi=disconnected reason=%d\n", event->reason);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = data;
        printf("wifi=ip=" IPSTR "\n", IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(wifi_events, WIFI_CONNECTED_BIT);
    }
}

static void start_wifi(void) {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &on_wifi, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &on_wifi, NULL));

    wifi_config_t wifi = {0};
    strncpy((char *)wifi.sta.ssid, WITNESS_WIFI_SSID, sizeof wifi.sta.ssid - 1);
    strncpy((char *)wifi.sta.password, WITNESS_WIFI_PASSWORD, sizeof wifi.sta.password - 1);
    wifi.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wifi.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
    wifi.sta.pmf_cfg.capable = true;
    wifi.sta.pmf_cfg.required = false;

    printf("wifi=connecting ssid=%s\n", WITNESS_WIFI_SSID);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi));
    ESP_ERROR_CHECK(esp_wifi_start());
}

static void register_once(void) {
    char body[128];
    snprintf(body, sizeof body, "{\"id\":\"%s\",\"fw\":\"setup\",\"board\":\"foriot_s3cam\"}", camera_id);

    esp_http_client_config_t cfg = {
        .url = WITNESS_REGISTRAR_URL,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 4000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) {
        printf("register=fail err=init\n");
        return;
    }
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, body, (int)strlen(body));
    esp_err_t err = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    if (err == ESP_OK && status == 200) {
        printf("register=ok id=%s\n", camera_id);
    } else {
        printf("register=fail err=%d status=%d\n", (int)err, status);
    }
}

static void register_task(void *arg) {
    witness_time_start("America/Los_Angeles");
    while (1) {
        xEventGroupWaitBits(wifi_events, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
        register_once();
        vTaskDelay(pdMS_TO_TICKS(15000));
    }
}

static bool authorized(httpd_req_t *req) {
    char expected[96];
    int n = snprintf(expected, sizeof expected, "Bearer %s", WITNESS_CAMERA_PASSWORD);
    if (n < 0 || n >= (int)sizeof expected) {
        return false;
    }
    size_t len = httpd_req_get_hdr_value_len(req, "Authorization");
    if (len != (size_t)n || len >= sizeof expected) {
        return false;
    }
    char got[96];
    if (httpd_req_get_hdr_value_str(req, "Authorization", got, sizeof got) != ESP_OK) {
        return false;
    }
    return memcmp(got, expected, (size_t)n) == 0;
}

static esp_err_t ping_get(httpd_req_t *req) {
    char body[96];
    snprintf(body, sizeof body, "{\"ok\":true,\"id\":\"%s\",\"fw\":\"setup\"}", camera_id);
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, body);
}

static esp_err_t frame_get(httpd_req_t *req) {
    if (!authorized(req)) {
        httpd_resp_set_status(req, "401 Unauthorized");
        return httpd_resp_sendstr(req, "unauthorized\n");
    }

    xSemaphoreTake(camera_lock, portMAX_DELAY);
    camera_fb_t *frame = NULL;
    for (int i = 0; i < 5; i++) {
        camera_fb_t *got = esp_camera_fb_get();
        if (!got) {
            continue;
        }
        int jpeg = got->len > 1 && got->buf[0] == 0xff && got->buf[1] == 0xd8;
        if (jpeg) {
            if (frame) {
                esp_camera_fb_return(frame);
            }
            frame = got;
            break;
        }
        esp_camera_fb_return(got);
        vTaskDelay(pdMS_TO_TICKS(30));
    }
    if (!frame) {
        xSemaphoreGive(camera_lock);
        httpd_resp_set_status(req, "503 Service Unavailable");
        return httpd_resp_sendstr(req, "camera=fail err=no_frame\n");
    }
    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    esp_err_t err = httpd_resp_send(req, (const char *)frame->buf, (ssize_t)frame->len);
    esp_camera_fb_return(frame);
    xSemaphoreGive(camera_lock);
    return err;
}

static void start_http(void) {
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.server_port = 80;
    cfg.stack_size = 12288;
    cfg.max_uri_handlers = 24;
    cfg.uri_match_fn = httpd_uri_match_wildcard;
    cfg.lru_purge_enable = true;
    httpd_handle_t server = NULL;
    ESP_ERROR_CHECK(httpd_start(&server, &cfg));
    httpd_uri_t ping = {.uri = "/api/v1/ping", .method = HTTP_GET, .handler = ping_get};
    httpd_uri_t frame = {.uri = "/api/v1/frame.jpg", .method = HTTP_GET, .handler = frame_get};
    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &ping));
    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &frame));
    witness_http_register(server, WITNESS_CAMERA_PASSWORD);
    printf("httpd=ok port=80\n");
}

static void log_camera(void) {
    camera_fb_t *frame = NULL;
    for (int i = 0; i < 8; i++) {
        if (frame) {
            esp_camera_fb_return(frame);
        }
        frame = esp_camera_fb_get();
        if (!frame) {
            printf("camera=fail err=no_frame\n");
            return;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    sensor_t *sensor = esp_camera_sensor_get();
    unsigned pid = sensor ? sensor->id.PID : 0;
    printf("camera=ok pid=0x%04x bytes=%u width=%u height=%u\n", pid, (unsigned)frame->len,
           (unsigned)frame->width, (unsigned)frame->height);
    esp_camera_fb_return(frame);
}

void app_main(void) {
    esp_chip_info_t chip;
    uint32_t flash_bytes = 0;
    size_t psram_bytes = heap_caps_get_total_size(MALLOC_CAP_SPIRAM);

    esp_chip_info(&chip);
    esp_flash_get_size(NULL, &flash_bytes);
    make_id();
    printf("boot=HELLO witness fw=setup board=foriot_s3cam id=%s\n", camera_id);
    printf("chip=esp32s3 cores=%d rev=%d flash_mb=%u psram_mb=%u\n", chip.cores, chip.revision,
           (unsigned)(flash_bytes / (1024 * 1024)), (unsigned)(psram_bytes / (1024 * 1024)));

    esp_err_t err = esp_camera_init(&camera_cfg);
    if (err != ESP_OK) {
        printf("camera=fail err=%d\n", (int)err);
        return;
    }
    camera_lock = xSemaphoreCreateMutex();
    log_camera();

    wifi_events = xEventGroupCreate();
    start_wifi();
    start_http();
#ifndef WITNESS_MODEL_KEY
#define WITNESS_MODEL_KEY ""
#endif
#ifndef WITNESS_ANTHROPIC_KEY
#define WITNESS_ANTHROPIC_KEY ""
#endif
#ifndef WITNESS_JEV_KEY
#define WITNESS_JEV_KEY ""
#endif
    witness_indexer_set_keys(WITNESS_MODEL_KEY, WITNESS_JEV_KEY);
    witness_recorder_start(camera_lock);
    witness_llm_drivers_ready();
    printf("llm openai=%s anthropic=%s jev=%s\n", WITNESS_MODEL_KEY[0] ? "set" : "missing",
           WITNESS_ANTHROPIC_KEY[0] ? "set" : "missing", WITNESS_JEV_KEY[0] ? "set" : "missing");
    xTaskCreate(register_task, "register", 8192, NULL, 5, NULL);
}
