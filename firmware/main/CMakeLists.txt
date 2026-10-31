if(NOT EXISTS "${COMPONENT_DIR}/secrets.h")
    message(FATAL_ERROR
        "firmware/main/secrets.h is missing. Copy secrets.h.example and fill "
        "SSID, passwords, and this computer's registrar URL. Do not commit secrets.h.")
endif()

idf_component_register(
    SRCS "bringup.c"
    INCLUDE_DIRS "."
    REQUIRES esp_wifi esp_event esp_netif nvs_flash esp_http_server esp_http_client spi_flash
)
