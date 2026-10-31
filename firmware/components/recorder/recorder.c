#include "witness_recorder.h"

#pragma GCC diagnostic ignored "-Wformat-truncation"

#include <dirent.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "esp_vfs_fat.h"
#include "freertos/task.h"
#include "sdmmc_cmd.h"
#include "driver/sdmmc_host.h"
#include "witness_idx.h"
#include "witness_time.h"

static bool sd_ready;
static SemaphoreHandle_t camera_lock;
static FILE *seg_mjpg;
static FILE *seg_idx;
static time_t seg_minute = -1;
static bool seg_unsynced;
static uint8_t *copy_buf;
static size_t copy_cap;

bool witness_sd_ready(void) {
    return sd_ready;
}

SemaphoreHandle_t witness_recorder_lock(void) {
    return camera_lock;
}

static void close_segment(void) {
    if (seg_mjpg) {
        fclose(seg_mjpg);
        seg_mjpg = NULL;
    }
    if (seg_idx) {
        fclose(seg_idx);
        seg_idx = NULL;
    }
    seg_minute = -1;
}

static void delete_hour(const char *day, const char *hour) {
    char path[160];
    snprintf(path, sizeof path, WITNESS_ROOT "/rec/%s/%s", day, hour);
    DIR *dir = opendir(path);
    if (!dir) {
        return;
    }
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        if (entry->d_name[0] == '.') {
            continue;
        }
        char file[192];
        snprintf(file, sizeof file, "%s/%s", path, entry->d_name);
        unlink(file);
    }
    closedir(dir);
    rmdir(path);
    snprintf(path, sizeof path, WITNESS_ROOT "/rec/%s", day);
    rmdir(path);
}

static void evict_oldest(void) {
    uint64_t total = 0, free_bytes = 0;
    if (esp_vfs_fat_info("/sdcard", &total, &free_bytes) != ESP_OK || total == 0) {
        return;
    }
    int guard = 0;
    while (free_bytes * 100 < total * 10 && guard < 8) {
        DIR *days = opendir(WITNESS_ROOT "/rec");
        if (!days) {
            return;
        }
        char oldest_day[16] = "";
        char oldest_hour[8] = "";
        struct dirent *day;
        while ((day = readdir(days)) != NULL) {
            if (day->d_name[0] == '.' || strcmp(day->d_name, "unsynced") == 0) {
                continue;
            }
            char day_path[160];
            snprintf(day_path, sizeof day_path, WITNESS_ROOT "/rec/%s", day->d_name);
            DIR *hours = opendir(day_path);
            if (!hours) {
                continue;
            }
            struct dirent *hour;
            while ((hour = readdir(hours)) != NULL) {
                if (hour->d_name[0] == '.') {
                    continue;
                }
                if (oldest_day[0] == '\0' || strcmp(day->d_name, oldest_day) < 0 ||
                    (strcmp(day->d_name, oldest_day) == 0 && strcmp(hour->d_name, oldest_hour) < 0)) {
                    snprintf(oldest_day, sizeof oldest_day, "%s", day->d_name);
                    snprintf(oldest_hour, sizeof oldest_hour, "%s", hour->d_name);
                }
            }
            closedir(hours);
        }
        closedir(days);
        if (oldest_day[0] == '\0') {
            return;
        }
        printf("storage=evict hour=%s/%s\n", oldest_day, oldest_hour);
        delete_hour(oldest_day, oldest_hour);
        if (esp_vfs_fat_info("/sdcard", &total, &free_bytes) != ESP_OK) {
            return;
        }
        guard++;
    }
}

static bool open_segment(bool synced) {
    close_segment();
    char path[192];
    if (!synced) {
        mkdir(WITNESS_ROOT "/rec/unsynced", 0775);
        snprintf(path, sizeof path, WITNESS_ROOT "/rec/unsynced/unsynced");
        seg_unsynced = true;
        seg_minute = 0;
    } else {
        time_t now = time(NULL);
        time_t minute = now - (now % 60);
        struct tm utc;
        gmtime_r(&minute, &utc);
        char day[16], hour[8], name[40];
        strftime(day, sizeof day, "%Y-%m-%d", &utc);
        strftime(hour, sizeof hour, "%H", &utc);
        strftime(name, sizeof name, "%Y-%m-%dT%H-%M-%SZ", &utc);
        char dir[160];
        snprintf(dir, sizeof dir, WITNESS_ROOT "/rec/%s", day);
        mkdir(WITNESS_ROOT "/rec", 0775);
        mkdir(dir, 0775);
        snprintf(dir, sizeof dir, WITNESS_ROOT "/rec/%s/%s", day, hour);
        mkdir(dir, 0775);
        snprintf(path, sizeof path, "%s/%s", dir, name);
        seg_unsynced = false;
        seg_minute = minute;
        evict_oldest();
    }
    char mjpg[200], idx[200];
    snprintf(mjpg, sizeof mjpg, "%s.mjpg", path);
    snprintf(idx, sizeof idx, "%s.idx", path);
    seg_mjpg = fopen(mjpg, "ab");
    seg_idx = fopen(idx, "ab");
    if (!seg_mjpg || !seg_idx) {
        close_segment();
        return false;
    }
    return true;
}

static void append_jpeg(const uint8_t *jpeg, size_t len) {
    bool synced = witness_time_synced();
    time_t minute = 0;
    if (synced) {
        time_t now = time(NULL);
        minute = now - (now % 60);
    }
    if (!seg_mjpg || seg_unsynced != !synced || (synced && minute != seg_minute)) {
        if (!open_segment(synced)) {
            printf("recording=fail err=open\n");
            return;
        }
    }
    long offset = ftell(seg_mjpg);
    if (offset < 0) {
        return;
    }
    if (fwrite(jpeg, 1, len, seg_mjpg) != len) {
        printf("recording=fail err=write\n");
        return;
    }
    uint8_t raw[WITNESS_IDX_SIZE];
    uint64_t t_ms = synced ? (uint64_t)time(NULL) * 1000 : 0;
    if (witness_idx_encode(raw, sizeof raw, t_ms, (uint32_t)offset, (uint32_t)len, 0) < 0) {
        return;
    }
    fwrite(raw, 1, sizeof raw, seg_idx);
    fflush(seg_mjpg);
    fflush(seg_idx);
}

static void recorder_task(void *arg) {
    (void)arg;
    unsigned frames = 0;
    while (1) {
        if (camera_lock && xSemaphoreTake(camera_lock, pdMS_TO_TICKS(50)) == pdTRUE) {
            camera_fb_t *frame = esp_camera_fb_get();
            size_t len = 0;
            if (frame && frame->len > 1 && frame->buf[0] == 0xff && frame->buf[1] == 0xd8) {
                if (frame->len > copy_cap) {
                    free(copy_buf);
                    copy_buf = heap_caps_malloc(frame->len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                    copy_cap = copy_buf ? frame->len : 0;
                }
                if (copy_buf && frame->len <= copy_cap) {
                    memcpy(copy_buf, frame->buf, frame->len);
                    len = frame->len;
                }
            }
            if (frame) {
                esp_camera_fb_return(frame);
            }
            xSemaphoreGive(camera_lock);
            if (len > 0) {
                append_jpeg(copy_buf, len);
                frames++;
                if (frames == 1 || frames % 300 == 0) {
                    printf("recording=ok frames=%u synced=%s\n", frames, witness_time_synced() ? "yes" : "no");
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

static esp_err_t mount_sd(void) {
    esp_vfs_fat_sdmmc_mount_config_t mount = {
        .format_if_mount_failed = false,
        .max_files = 8,
        .allocation_unit_size = 16 * 1024,
    };
    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.max_freq_khz = SDMMC_FREQ_DEFAULT;
    sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
    slot.width = 1;
    slot.clk = GPIO_NUM_39;
    slot.cmd = GPIO_NUM_38;
    slot.d0 = GPIO_NUM_40;
    slot.flags |= SDMMC_SLOT_FLAG_INTERNAL_PULLUP;
    sdmmc_card_t *card = NULL;
    esp_err_t err = esp_vfs_fat_sdmmc_mount("/sdcard", &host, &slot, &mount, &card);
    if (err != ESP_OK) {
        printf("sd=none err=%d\n", (int)err);
        return err;
    }
    mkdir(WITNESS_ROOT, 0775);
    mkdir(WITNESS_ROOT "/rec", 0775);
    mkdir(WITNESS_ROOT "/index", 0775);
    FILE *meta = fopen(WITNESS_ROOT "/card.json", "w");
    if (meta) {
        fputs("{\"format\":1}\n", meta);
        fclose(meta);
    }
    uint64_t total = 0, free_bytes = 0;
    esp_vfs_fat_info("/sdcard", &total, &free_bytes);
    printf("sd=ok size_gb=%.1f\n", total / 1e9);
    sd_ready = true;
    return ESP_OK;
}

esp_err_t witness_recorder_start(SemaphoreHandle_t lock) {
    camera_lock = lock;
    if (mount_sd() != ESP_OK) {
        return ESP_FAIL;
    }
    if (xTaskCreate(recorder_task, "recorder", 12288, NULL, 4, NULL) != pdPASS) {
        printf("recording=fail err=task\n");
        return ESP_FAIL;
    }
    return ESP_OK;
}
