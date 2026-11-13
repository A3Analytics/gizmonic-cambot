# macOS arm64 setup notes

Read [`fixr/SETUP.md`](../SETUP.md) section 0 first. This file is the
contributed record for the computer in
[`macos-arm64.json`](macos-arm64.json).

**Contributing profiles.** If your install differs (Linux, Windows, another
macOS version, different Python layout, ESP-IDF path, serial device naming),
add `profiles/<id>.json` and `profiles/<id>.md` with useful tips, leave every
secret out, and open a pull request so other agents can reuse them.

The schema is
[`host-profile.schema.json`](../schema/host-profile.schema.json).

## This computer (example)

| Fact | Value |
|---|---|
| Machine | macOS on Apple Silicon (arm64), zsh |
| `python3` on PATH | Often still 3.9.x from Command Line Tools |
| Interpreter to use | Homebrew `/opt/homebrew/bin/python3.13` when PATH Python is too old |
| pipx | Present; `cambot-cli` not installed until M2 |
| PlatformIO | Optional; not required for the ESP-IDF path |
| ffmpeg | Homebrew |
| esptool | Inside the ESP-IDF / Espressif Python env, not always on PATH |
| `cambot` | Not installed (milestone M2) |
| LAN | Wi-Fi interface `en0`; detect the current IPv4 at setup time |
| Wi-Fi name | Ask the user; must be 2.4 GHz WPA2 or WPA3 Personal |
| Registrar | port 8765 |
| Time zone | Set `CAMBOT_TZ` or `TZ` to an IANA name (for example `America/Los_Angeles`) |

`python3` on PATH may fail the setup guide’s 3.10 requirement. Prefer the
Homebrew interpreter by full path when that happens.

## Secrets

Never print, log, echo, or commit the Wi-Fi password, the model API key, or
the camera password. The profile stores environment variable names only.

If a secret shows up in command output or in the chat, tell the user and do
not copy it into a file.

The user sets these in their own shell, or types them into a hidden prompt:

```bash
export CAMBOT_WIFI_PASSWORD
export CAMBOT_CAMERA_PASSWORD
export CAMBOT_MODEL_KEY
export CAMBOT_TZ=America/Los_Angeles   # or another IANA zone
```

## Ask before provisioning

Do not guess these:

- Wi-Fi SSID (2.4 GHz) and password
- IANA time zone
- One or two sentences describing what the camera watches
- Model base URL, model name, and API key. Recording works without a model.

Also ask before formatting an SD card, erasing flash, or rotating a token.

## Programming environment

The repository plan (`docs/src/firmware-repo-plan.md`) says the firmware is an
**ESP-IDF 5.x** project written in **C**. It is not MicroPython. PlatformIO is
optional and unused in the current path.

This example used **ESP-IDF v5.5.6** at `~/esp/esp-idf`. `install.sh` can pick
an older PATH Python; cmake and ninja may need Homebrew. esptool.py lives in
the IDF virtualenv.

The setup image is `firmware/`. On boot it prints `boot=HELLO cambot`, takes
one OV3660 JPEG, joins Wi-Fi, serves `GET /api/v1/frame.jpg` on port 80, and
POSTs `/api/register` on this computer every 15 seconds. Camera pins are the
FORIOT map in `firmware/main/bringup.c`.

Copy `firmware/main/secrets.h.example` to `firmware/main/secrets.h` and fill
every required field before building (see the comments in that file). Put the
same camera password in `fixr/run/secrets.env` as `CAMBOT_CAMERA_PASSWORD`.
Both files are gitignored. Never print them.

```bash
. "$HOME/esp/esp-idf/export.sh"
cd firmware
idf.py build
idf.py -p /dev/cu.usbserial-XXXX -b 115200 flash monitor
```

Use **115200**. `idf.py` may suggest 460800; this board’s CH340 drops at that
speed.

**Expect** on the TTL log:

```text
boot=HELLO cambot fw=setup board=foriot_s3cam id=cambot-XXXX
chip=esp32s3 cores=2 rev=... flash_mb=16 psram_mb=8
camera=ok pid=0x3660 bytes=... width=320 height=240
wifi=ip=...
register=ok id=cambot-XXXX
```

Then `http://<this-computer>:8765/` lists the camera and refreshes its frame.
Restart the registrar after `fixr/run/secrets.env` exists, or the frame
request returns 503.

`psram_mb=0` means the octal-PSRAM settings did not take. `camera=fail` means
the flex or the module needs to be reseated. Flashing erases whatever is on
the board now.

## Programming cable

Plug a USB-C **data** cable into the port labeled **TTL**. That port is the
CH340 serial link, VID `1a86`, and it is the one used for flashing and logs.
The baud rate on this board is **115200**.

The OTG port is the ESP32’s native USB, VID `303a`. Opening it resets the
chip. Programming and serial logs use the TTL port only.

Physical work belongs to the user. Say what to plug in, then wait.

With the board unplugged, the microSD card goes in the slot on the back,
contacts facing the board, until it clicks.

## Tips that mattered on this architecture

1. **Python.** Default `python3` can be 3.9.x. Use Homebrew 3.13 for FIXR.
2. **`cambot` CLI.** Not shipped until M2; flash with ESP-IDF instead.
3. **Serial.** TTL device names look like `/dev/cu.usbserial-XXXX` and change
   on replug. A first `wifi=disconnected` is not a failure if `register=ok`
   follows.
4. **Registrar check.**

```bash
/opt/homebrew/bin/python3.13 fixr/registrar/serve.py --check
```

Start it for a real camera with:

```bash
export CAMBOT_TZ=America/Los_Angeles
/opt/homebrew/bin/python3.13 fixr/registrar/serve.py
```

The process prints `camera_password=set` or `camera_password=missing`. It
does not print the password. The registration URL it prints includes this
computer’s current LAN address. Give that URL to `CAMBOT_REGISTRAR_URL` in
`secrets.h`. Detect the address again at setup time; do not trust a stale IP
in a profile.

## Registration message

```http
POST /api/register
Content-Type: application/json

{"id":"cambot-ab12","fw":"0.0.0","board":"foriot_s3cam"}
```

The registrar stores the source IP of that connection. Each camera has its
own access password. This host reads one password from
`CAMBOT_CAMERA_PASSWORD` and sends it only when proxying
`GET http://<camera-ip>/api/v1/frame.jpg`.

## After the board is connected

Follow `fixr/SETUP.md` from S3. Expect one port with kind `ttl`. Stop after
two failed attempts at the same step and report what you saw. Update
`install_notes` in the profile with `ok`, `failed`, `skipped`, or `untested`.
If your notes would help another architecture, open a pull request.

After S6 verify succeeds, an agent may turn on **High performance** in the
registrar UI for a snappier live view. If frame fetch or recording checks fail,
turn it off and stop. Do not enable it during install steps S1–S6.
