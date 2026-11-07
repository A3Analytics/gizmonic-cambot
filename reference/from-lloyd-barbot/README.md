# Reference files from lloyd-barbot

These are the files the Gizmonic Cambot documents were written from. The rest of that
project (motors, Pico controller, chassis, captures) stayed there.

| File | Why it is here |
|---|---|
| `platformio.ini` | Board profile for the FORIOT ESP32-S3-CAM: N16R8, octal PSRAM, 16 MB flash, TTL port at 115200 |
| `camera_pins.h` | Camera GPIO map taken from the working firmware |
| `scripts/capture.py` | Host-side JPEG capture over the board's USB ports, including which port is which |
| `include/secrets.h.example` | Wi-Fi credential header. The real `secrets.h` was not copied |

The board in those files is the FORIOT ESP32-S3-CAM, ESP32-S3-WROOM-1 N16R8, OV3660, dual USB-C (Amazon B0GD2BZ9B1).
