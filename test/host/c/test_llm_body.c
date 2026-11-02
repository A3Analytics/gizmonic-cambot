#include "witness_alert.h"
#include "witness_llm.h"

#include <stdio.h>
#include <string.h>

static int require(int cond, const char *message) {
    if (!cond) {
        fprintf(stderr, "fail %s\n", message);
        return 1;
    }
    return 0;
}

int main(void) {
    char body[1024];
    int length;
    int failed = 0;

    length = witness_openai_body(body, sizeof body, "gpt-4.1-mini", "say \"hi\"", "qqo=", 128);
    failed |= require(length > 0, "openai length");
    failed |= require(strstr(body, "gpt-4.1-mini") != NULL, "openai model");
    failed |= require(strstr(body, "image_url") != NULL, "openai image");
    failed |= require(strstr(body, "data:image/jpeg;base64,qqo=") != NULL, "openai b64");
    failed |= require(strstr(body, "say \\\"hi\\\"") != NULL, "openai escape");

    length = witness_anthropic_body(body, sizeof body, "claude-sonnet-4-5", "look", "qqo=", 64);
    failed |= require(length > 0, "anthropic length");
    failed |= require(strstr(body, "\"media_type\":\"image/jpeg\"") != NULL, "anthropic media");
    failed |= require(strstr(body, "base64") != NULL, "anthropic source");

    length = witness_jev_body(body, sizeof body, WITNESS_JEV_MODEL, "present", "Is it there?", "qqo=");
    failed |= require(length > 0, "jev length");
    failed |= require(strstr(body, WITNESS_JEV_MODEL) != NULL, "jev model");
    failed |= require(strstr(body, "\"type\":\"noul\"") != NULL, "jev noul");
    failed |= require(strstr(body, "img1") != NULL, "jev asset");
    failed |= require(strstr(WITNESS_JEV_IMAGES_HEADER, "TypeSafe-Danger-Zone-Images") != NULL, "header");

    {
        witness_jev_asset assets[] = {{"ex_00", "qqo="}, {"ex_01", "qqo="}};
        witness_jev_question questions[] = {
            {"tip", "Is the tip bent in `photo`? A yes looks like ex_00. A no looks like ex_01.", 1,
             "the pipette tip in `photo`"},
            {"attached", "Is a tip attached in `photo`?", 0, NULL},
        };
        char request[2048];
        int previous = 0;
        length = witness_jev_request(request, sizeof request, WITNESS_JEV_MODEL, "qqo=", assets, 2, questions, 2);
        failed |= require(length > 0, "jev request length");
        failed |= require(strstr(request, "\"ex_00\"") != NULL, "example asset");
        failed |= require(strstr(request, "A yes looks like ex_00") != NULL, "example text");
        failed |= require(strstr(request, "\"type\":\"box\"") != NULL, "box question");
        failed |= require(strstr(request, "tip__box") != NULL, "box name");
        failed |= require(strstr(request, "\"attached\"") != NULL, "second noul");
        failed |= require(request[length - 1] == '}', "request closed");
        {
            int braces = 0;
            int quotes = 0;
            int escaped = 0;
            for (int i = 0; i < length; i++) {
                if (escaped) {
                    escaped = 0;
                    continue;
                }
                if (request[i] == '\\') {
                    escaped = 1;
                    continue;
                }
                if (request[i] == '"') {
                    quotes++;
                    continue;
                }
                if (quotes % 2) {
                    continue;
                }
                if (request[i] == '{') {
                    braces++;
                }
                if (request[i] == '}') {
                    braces--;
                }
            }
            failed |= require(braces == 0, "braces balance");
            failed |= require(quotes % 2 == 0, "quotes balance");
        }
        failed |= require(witness_alert_edge(&previous, 0.91, 1) == 1, "first yes sends");
        failed |= require(previous == 1, "yes latched");
        failed |= require(witness_alert_edge(&previous, 0.8, 1) == 0, "following yes stays quiet");
        failed |= require(witness_alert_edge(&previous, 0.1, 1) == 0, "no clears the latch");
        failed |= require(previous == 0, "latch cleared");
        failed |= require(witness_alert_edge(&previous, 0.7, 1) == 1, "yes after no sends");
    }
    return failed;
}
