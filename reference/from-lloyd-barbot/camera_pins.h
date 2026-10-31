// Camera pin map copied from the working firmware in the lloyd-barbot
// repository (src/main.cpp). Same wiring as CAMERA_MODEL_ESP32S3_EYE.
// PWDN and RESET are not connected.
//
// The microSD slot is not used by that firmware. On this board it is 1-bit
// SDMMC: CLK GPIO39, CMD GPIO38, D0 GPIO40.

#define PWDN_GPIO_NUM -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 15
#define SIOD_GPIO_NUM 4
#define SIOC_GPIO_NUM 5
#define Y2_GPIO_NUM 11
#define Y3_GPIO_NUM 9
#define Y4_GPIO_NUM 8
#define Y5_GPIO_NUM 10
#define Y6_GPIO_NUM 12
#define Y7_GPIO_NUM 18
#define Y8_GPIO_NUM 17
#define Y9_GPIO_NUM 16
#define VSYNC_GPIO_NUM 6
#define HREF_GPIO_NUM 7
#define PCLK_GPIO_NUM 13
