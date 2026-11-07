#include "cambot_time.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

#include "esp_sntp.h"

static volatile bool synced;

static void on_sync(struct timeval *tv) {
    (void)tv;
    synced = true;
    printf("time=synced\n");
}

void cambot_time_start(const char *iana) {
    const char *zone = "PST8PDT,M3.2.0,M11.1.0";
    if (iana && strcmp(iana, "UTC") == 0) {
        zone = "UTC0";
    }
    setenv("TZ", zone, 1);
    tzset();
    if (synced) {
        return;
    }
    esp_sntp_setoperatingmode(ESP_SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, "pool.ntp.org");
    esp_sntp_set_time_sync_notification_cb(on_sync);
    esp_sntp_init();
    printf("time=start tz=%s\n", iana ? iana : "America/Los_Angeles");
}

bool cambot_time_synced(void) {
    return synced;
}
