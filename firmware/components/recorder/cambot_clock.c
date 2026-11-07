#include "cambot_clock.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int64_t ymd_to_unix(int year, int month, int day, int hour, int minute, int second) {
    year -= month <= 2;
    const int era = (year >= 0 ? year : year - 399) / 400;
    const unsigned yoe = (unsigned)(year - era * 400);
    const unsigned doy = (153 * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1;
    const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    const int64_t days = (int64_t)era * 146097 + (int64_t)doe - 719468;
    return days * 86400 + hour * 3600 + minute * 60 + second;
}

static bool parse_rfc3339(const char *text, time_t *out) {
    int year, month, day, hour, minute, second;
    if (sscanf(text, "%d-%d-%dT%d:%d:%d", &year, &month, &day, &hour, &minute, &second) != 6) {
        return false;
    }
    const char *zone = strchr(text, 'T');
    if (!zone) {
        return false;
    }
    zone = strpbrk(zone + 1, "Z+-");
    int offset = 0;
    if (zone && *zone != 'Z') {
        int zh = 0, zm = 0;
        if (sscanf(zone + 1, "%d:%d", &zh, &zm) < 1) {
            return false;
        }
        offset = (zh * 60 + zm) * 60;
        if (*zone == '+') {
            offset = -offset;
        }
    }
    *out = (time_t)(ymd_to_unix(year, month, day, hour, minute, second) + offset);
    return true;
}

bool cambot_parse_bound(const char *text, time_t now, time_t *out) {
    if (!text || !out) {
        return false;
    }
    while (*text == ' ') {
        text++;
    }
    if (text[0] == '-' && isdigit((unsigned char)text[1])) {
        char unit = 0;
        int count = 0;
        if (sscanf(text, "-%d%c", &count, &unit) < 2) {
            return false;
        }
        int scale = 1;
        if (unit == 'm') {
            scale = 60;
        } else if (unit == 'h') {
            scale = 3600;
        } else if (unit == 'd') {
            scale = 86400;
        } else if (unit != 's') {
            return false;
        }
        *out = now - (time_t)count * scale;
        return true;
    }
    if (isdigit((unsigned char)text[0]) && strchr(text, 'T') == NULL && strchr(text, ':') != NULL) {
        int hour = 0, minute = 0, second = 0;
        if (sscanf(text, "%d:%d:%d", &hour, &minute, &second) < 2) {
            return false;
        }
        struct tm local;
        localtime_r(&now, &local);
        local.tm_hour = hour;
        local.tm_min = minute;
        local.tm_sec = second;
        local.tm_isdst = -1;
        *out = mktime(&local);
        return *out != (time_t)-1;
    }
    return parse_rfc3339(text, out);
}
