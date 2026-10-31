#include "witness_llm.h"
#include "witness_llm_client.h"

#include <stdio.h>
#include <string.h>

#include "OpenAI.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "mbedtls/base64.h"

static const char *TAG = "witness.llm";

esp_err_t witness_llm_drivers_ready(void) {
    return ESP_OK;
}

esp_err_t witness_llm_openai(const char *api_key, const char *base_url, const char *model, const uint8_t *jpeg,
                             size_t jpeg_len, const char *prompt, char *out, size_t out_cap) {
    if (!api_key || api_key[0] == '\0' || !out || out_cap < 2) {
        return ESP_ERR_INVALID_ARG;
    }
    size_t b64_cap = 0;
    mbedtls_base64_encode(NULL, 0, &b64_cap, jpeg, jpeg_len);
    const char *prefix = "data:image/jpeg;base64,";
    size_t url_cap = strlen(prefix) + b64_cap + 1;
    char *url = heap_caps_malloc(url_cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!url) {
        url = malloc(url_cap);
    }
    if (!url) {
        return ESP_ERR_NO_MEM;
    }
    memcpy(url, prefix, strlen(prefix));
    size_t wrote = 0;
    int b64_err = mbedtls_base64_encode((unsigned char *)url + strlen(prefix), b64_cap, &wrote, jpeg, jpeg_len);
    if (b64_err != 0) {
        free(url);
        return ESP_FAIL;
    }
    url[strlen(prefix) + wrote] = '\0';

    OpenAI_t *client = OpenAICreate(api_key);
    if (!client) {
        free(url);
        ESP_LOGW(TAG, "model=fail provider=openai err=create");
        return ESP_FAIL;
    }
    if (base_url && base_url[0] != '\0') {
        OpenAIChangeBaseURL(client, base_url);
    }
    OpenAI_ChatCompletion_t *chat = client->chatCreate(client);
    if (!chat) {
        OpenAIDelete(client);
        free(url);
        return ESP_FAIL;
    }
    chat->setModel(chat, model ? model : "gpt-4.1-mini");
    chat->setMaxTokens(chat, 1024);
    chat->setSystem(chat, prompt);
    OpenAI_StringResponse_t *response = chat->multiModalMessage(chat, "image_url", url, false);
    free(url);
    esp_err_t result = ESP_FAIL;
    if (response) {
        const char *error = response->getError(response);
        const char *text = response->getData(response, 0);
        if ((error == NULL || error[0] == '\0') && text && text[0] != '\0') {
            snprintf(out, out_cap, "%s", text);
            result = ESP_OK;
            ESP_LOGI(TAG, "model=ok provider=openai");
        } else {
            ESP_LOGW(TAG, "model=dropped provider=openai");
        }
        response->deleteResponse(response);
    } else {
        ESP_LOGW(TAG, "model=fail provider=openai");
    }
    client->chatDelete(chat);
    OpenAIDelete(client);
    return result;
}
