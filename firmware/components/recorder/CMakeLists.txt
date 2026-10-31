idf_component_register(
    SRCS "recorder.c" "recorder_http.c" "witness_clock.c"
    INCLUDE_DIRS "include"
    REQUIRES fatfs sdmmc esp_driver_sdmmc vfs mbedtls esp_http_server json esp32-camera store timebase
)
