idf_component_register(
    SRCS "motion.c" "keyframe.c" "gray.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_jpeg
)
