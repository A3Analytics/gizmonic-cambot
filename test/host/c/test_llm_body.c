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
    return failed;
}
