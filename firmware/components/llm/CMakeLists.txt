idf_component_register(
    SRCS "alert.c" "llm_body.c" "llm_http.c" "llm_openai.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_http_client esp-tls mbedtls json openai
)
