#include "witness_recorder.h"

#pragma GCC diagnostic ignored "-Wformat-truncation"

#include <dirent.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_vfs_fat.h"
#include "freertos/task.h"
#include "sdmmc_cmd.h"
#include "driver/sdmmc_host.h"
#include "witness_gray.h"
#include "witness_idx.h"
#include "witness_keyframe.h"
#include "witness_motion.h"
#include "witness_time.h"

static bool sd_ready;
static SemaphoreHandle_t camera_lock;
static FILE *seg_mjpg;
static FILE *seg_idx;
static time_t seg_minute = -1;
static bool seg_unsynced;
static uint32_t seg_frame;
static char seg_name[40];
static uint8_t *copy_buf;
static size_t copy_cap;
static size_t copy_len;
static SemaphoreHandle_t preview_lock;
static uint8_t *prev_gray;
static uint8_t *curr_gray;
static size_t prev_gray_len;
static bool have_keyframe;
static int64_t last_keyframe_ms;
static bool high_perf;
static unsigned capture_ms = 200;
static unsigned fflush_every = 1;
static unsigned since_fflush;
#define MINUTE_SLOTS 720
static uint16_t minute_frames[MINUTE_SLOTS];
static time_t minute_at[MINUTE_SLOTS];

bool witness_sd_ready(void) {
    return sd_ready;
}

SemaphoreHandle_t witness_recorder_lock(void) {
    return camera_lock;
}

bool witness_recorder_peek_jpeg(uint8_t *dst, size_t cap, size_t *out_len) {
    if (!dst || !out_len || !preview_lock) {
        return false;
    }
    if (xSemaphoreTake(preview_lock, pdMS_TO_TICKS(50)) != pdTRUE) {
        return false;
    }
    bool ok = copy_buf && copy_len > 0 && copy_len <= cap;
    if (ok) {
        memcpy(dst, copy_buf, copy_len);
        *out_len = copy_len;
    }
    xSemaphoreGive(preview_lock);
    return ok;
}

bool witness_perf_enabled(void) {
    return high_perf;
}

unsigned witness_perf_capture_ms(void) {
    return capture_ms;
}

static void apply_perf(bool enabled) {
    high_perf = enabled;
    /* Keep 5 fps. A 100 ms loop made live frame fetches slower and stalled
     * other HTTP handlers when the UI polled hard. */
    capture_ms = 200;
    fflush_every = enabled ? 5 : 1;
    since_fflush = 0;
}

bool witness_perf_set_enabled(bool enabled) {
    if (!sd_ready) {
        apply_perf(enabled);
        return true;
    }
    FILE *file = fopen(WITNESS_ROOT "/high_perf", "w");
    if (!file) {
        return false;
    }
    fputc(enabled ? '1' : '0', file);
    fclose(file);
    apply_perf(enabled);
    printf("perf=%s capture_ms=%u fflush_every=%u\n", enabled ? "on" : "off", capture_ms, fflush_every);
    return true;
}

static void load_perf(void) {
    /* Absent file = install-safe defaults. */
    FILE *file = fopen(WITNESS_ROOT "/high_perf", "r");
    if (!file) {
        apply_perf(false);
        return;
    }
    int ch = fgetc(file);
    fclose(file);
    apply_perf(ch == '1');
    printf("perf=%s capture_ms=%u\n", high_perf ? "on" : "off", capture_ms);
}

static void close_segment(void) {
    if (seg_mjpg) {
        fflush(seg_mjpg);
        fclose(seg_mjpg);
        seg_mjpg = NULL;
    }
    if (seg_idx) {
        fflush(seg_idx);
        fclose(seg_idx);
        seg_idx = NULL;
    }
    seg_minute = -1;
    since_fflush = 0;
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
        snprintf(seg_name, sizeof seg_name, "%s", name);
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
    long idx_size = ftell(seg_idx);
    seg_frame = idx_size > 0 ? (uint32_t)(idx_size / WITNESS_IDX_SIZE) : 0;
    return true;
}

static bool append_jpeg(const uint8_t *jpeg, size_t len, uint16_t motion_milli, uint32_t *frame_out) {
    bool synced = witness_time_synced();
    time_t minute = 0;
    if (synced) {
        time_t now = time(NULL);
        minute = now - (now % 60);
    }
    if (!seg_mjpg || seg_unsynced != !synced || (synced && minute != seg_minute)) {
        if (!open_segment(synced)) {
            printf("recording=fail err=open\n");
            return false;
        }
    }
    long offset = ftell(seg_mjpg);
    if (offset < 0) {
        return false;
    }
    if (fwrite(jpeg, 1, len, seg_mjpg) != len) {
        /* A failed stream stays failed until the minute rolls over; reopen it
         * on the next frame instead. */
        static unsigned write_fails;
        write_fails++;
        printf("recording=fail err=write count=%u internal_free=%u internal_block=%u\n", write_fails,
               (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA),
               (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
        close_segment();
        return false;
    }
    uint8_t raw[WITNESS_IDX_SIZE];
    uint64_t t_ms = synced ? (uint64_t)time(NULL) * 1000 : 0;
    if (witness_idx_encode(raw, sizeof raw, t_ms, (uint32_t)offset, (uint32_t)len, motion_milli) < 0) {
        return false;
    }
    fwrite(raw, 1, sizeof raw, seg_idx);
    since_fflush++;
    if (since_fflush >= fflush_every) {
        fflush(seg_mjpg);
        fflush(seg_idx);
        since_fflush = 0;
    }
    if (frame_out) {
        *frame_out = seg_frame;
    }
    seg_frame++;
    if (synced) {
        time_t minute = time(NULL);
        minute -= minute % 60;
        unsigned slot = (unsigned)((minute / 60) % MINUTE_SLOTS);
        if (minute_at[slot] != minute) {
            minute_at[slot] = minute;
            minute_frames[slot] = 0;
        }
        if (minute_frames[slot] < UINT16_MAX) {
            minute_frames[slot]++;
        }
    }
    return true;
}

int witness_recorder_minutes(time_t start, time_t end, time_t *minutes, uint16_t *frames, int cap) {
    int count = 0;
    time_t minute;
    if (!minutes || !frames || cap <= 0 || end < start) {
        return 0;
    }
    minute = start - (start % 60);
    if (end - minute > (time_t)MINUTE_SLOTS * 60) {
        minute = end - (end % 60) - (time_t)(MINUTE_SLOTS - 1) * 60;
    }
    for (; minute <= end - (end % 60) && count < cap; minute += 60) {
        unsigned slot = (unsigned)((minute / 60) % MINUTE_SLOTS);
        if (minute_at[slot] != minute || minute_frames[slot] == 0) {
            continue;
        }
        minutes[count] = minute;
        frames[count] = minute_frames[slot];
        count++;
    }
    return count;
}

static void recorder_task(void *arg) {
    (void)arg;
    unsigned frames = 0;
    while (1) {
        if (camera_lock && xSemaphoreTake(camera_lock, pdMS_TO_TICKS(50)) == pdTRUE) {
            camera_fb_t *frame = esp_camera_fb_get();
            size_t len = 0;
            if (frame && frame->len > 1 && frame->buf[0] == 0xff && frame->buf[1] == 0xd8) {
                if (preview_lock) {
                    xSemaphoreTake(preview_lock, portMAX_DELAY);
                }
                if (frame->len > copy_cap) {
                    free(copy_buf);
                    copy_buf = heap_caps_malloc(frame->len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                    copy_cap = copy_buf ? frame->len : 0;
                }
                if (copy_buf && frame->len <= copy_cap) {
                    memcpy(copy_buf, frame->buf, frame->len);
                    copy_len = frame->len;
                    len = frame->len;
                }
                if (preview_lock) {
                    xSemaphoreGive(preview_lock);
                }
            }
            if (frame) {
                esp_camera_fb_return(frame);
            }
            xSemaphoreGive(camera_lock);
            if (len > 0) {
                if (!prev_gray) {
                    prev_gray = heap_caps_malloc(48 * 1024, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                    curr_gray = heap_caps_malloc(48 * 1024, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                }
                uint16_t motion = 0;
                size_t gray_len = 0;
                if (prev_gray && curr_gray &&
                    witness_gray_eighth(copy_buf, len, curr_gray, 48 * 1024, &gray_len) == ESP_OK && gray_len > 0) {
                    motion = witness_motion_milli(prev_gray_len == gray_len ? prev_gray : NULL, curr_gray, gray_len);
                    memcpy(prev_gray, curr_gray, gray_len);
                    prev_gray_len = gray_len;
                }
                uint32_t frame_index = seg_frame;
                bool synced = witness_time_synced();
                append_jpeg(copy_buf, len, motion, &frame_index);
                /* Keyframes go to the indexer even when the SD write failed, so the
                 * live score keeps updating. */
                if (synced) {
                    int64_t now_ms = esp_timer_get_time() / 1000;
                    int64_t elapsed = have_keyframe ? now_ms - last_keyframe_ms : 0;
                    if (witness_keyframe_due(have_keyframe, elapsed, motion, false)) {
                        have_keyframe = true;
                        last_keyframe_ms = now_ms;
                        witness_indexer_submit(copy_buf, len, time(NULL), motion, frame_index, seg_name);
                    }
                }
                frames++;
                if (frames == 1 || frames % 300 == 0) {
                    printf("recording=ok frames=%u synced=%s perf=%s internal_free=%u internal_block=%u\n", frames,
                           witness_time_synced() ? "yes" : "no", high_perf ? "on" : "off",
                           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA),
                           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(capture_ms));
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
    preview_lock = xSemaphoreCreateMutex();
    if (mount_sd() != ESP_OK) {
        return ESP_FAIL;
    }
    load_perf();
    witness_indexer_start();
    if (xTaskCreatePinnedToCore(recorder_task, "recorder", 16384, NULL, 4, NULL, 1) != pdPASS &&
        xTaskCreate(recorder_task, "recorder", 16384, NULL, 4, NULL) != pdPASS) {
        printf("recording=fail err=task\n");
        return ESP_FAIL;
    }
    return ESP_OK;
}
