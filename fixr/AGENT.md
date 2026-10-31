# FIXR agent instructions

Read [`docs/SETUP.md`](../docs/SETUP.md) section 0 first. This file is the
host-specific record for the computer in
[`profiles/waterbear.json`](profiles/waterbear.json). Other computers get their
own profile beside that file. The schema is
[`schema/host-profile.schema.json`](schema/host-profile.schema.json).

## This computer

| Fact | Value |
|---|---|
| Machine | Waterbear, Apple M4 Max, 16 cores, macOS 26.6.2 (25G83), arm64, zsh |
| `python3` on PATH | 3.9.6 at `/Library/Developer/CommandLineTools/usr/bin/python3` |
| Interpreter to use | `/opt/homebrew/bin/python3.13` (3.13.7) |
| pipx | 1.7.1, no `witness-cli` |
| PlatformIO | Core 6.1.18 (`pio`) |
| ffmpeg | 8.1.1 |
| esptool | not on PATH |
| `witness` | not installed |
| LAN | `en0`, `192.168.68.91` on 2026-10-31 |
| Wi-Fi name | Figaro. Confirm it is 2.4 GHz. |
| Registrar | port 8765 |

`python3` on PATH fails the setup guide, which requires 3.10 or newer. Call
Homebrew Python 3.13 by its full path.

## Secrets

Never print, log, echo, or commit the Wi-Fi password, the model API key, or
the camera password. The profile stores environment variable names only.

If a secret shows up in command output or in the chat, tell the user and do
not copy it into a file.

The user sets these in their own shell, or types them into a hidden prompt:

```bash
export WITNESS_WIFI_PASSWORD
export WITNESS_CAMERA_PASSWORD
export WITNESS_MODEL_KEY
```

## Ask before provisioning

Do not guess these:

- IANA time zone
- One or two sentences describing what the camera watches
- Model base URL, model name, and API key. Recording works without a model.

Also ask before formatting an SD card, erasing flash, or rotating a token.

## Setup answers for this camera

| Fact | Value |
|---|---|
| Time zone | `America/Los_Angeles` (Pacific) |
| Scene | General camera setup. Testing basic functionality. |
| Model | `gpt-4.1-mini` at `https://api.openai.com/v1` |
| API key | Read `openai_api_key.txt` into `WITNESS_MODEL_KEY`. The file is gitignored. Never print it. |

`gpt-4.1-mini` is a small model that accepts images. OpenAI plans to remove it on 14 January 2027 and names `gpt-6-luna` as the replacement.

## Programming environment

The repository plan (`docs/src/firmware-repo-plan.md`) says the firmware is an **ESP-IDF 5.x** project written in **C**. It is not MicroPython. The Lloyd prototype used Arduino; Witness does not. PlatformIO is an optional entry point in the plan, and it is not the toolchain installed here.

The plan does not pin an ESP-IDF release, an installer, or a program to flash at setup. This machine uses **ESP-IDF v5.5.6** (the current 5.x bugfix; 6.x exists and was not installed) at `~/esp/esp-idf`. `install.sh` picked Python 3.9.6. cmake 4.4.5 and ninja 1.13.2 came from Homebrew. esptool.py 4.12.0 lives in the IDF virtualenv.

The setup image is `firmware/`. It is the setup test, not the Witness component tree. On boot it prints `boot=HELLO witness`, takes one OV3660 JPEG, joins Wi-Fi, serves `GET /api/v1/frame.jpg` on port 80, and POSTs `/api/register` on this computer every 15 seconds. Pins come from `reference/from-lloyd-barbot/camera_pins.h`.

Write `firmware/main/secrets.h` from `secrets.h.example` before building. Put the SSID, the Wi-Fi password, the camera password, and `http://<this-computer>:8765/api/register` in that file. The same camera password goes in `fixr/run/secrets.env` as `WITNESS_CAMERA_PASSWORD`, which the registrar loads at startup. Both files are gitignored. Never print them.

On 2026-10-31 this path worked. The camera is `witness-0d98` at `192.168.68.51`. The first two Wi-Fi attempts logged `reason=2` and `reason=204`, then it joined Figaro with WPA3-SAE and printed `register=ok`. The registrar fetched a JPEG. The page is `http://192.168.68.91:8765/`.

```bash
. "$HOME/esp/esp-idf/export.sh"
cd firmware
idf.py build
idf.py -p /dev/cu.usbserial-XXXX -b 115200 flash monitor
```

Use 115200. `idf.py` suggests 460800, and this board's CH340 drops at that speed.

**Expect** on the TTL log:

```text
boot=HELLO witness fw=setup board=foriot_s3cam id=witness-XXXX
chip=esp32s3 cores=2 rev=... flash_mb=16 psram_mb=8
camera=ok pid=0x3660 bytes=... width=320 height=240
wifi=ip=...
register=ok id=witness-XXXX
```

Then `http://<this-computer>:8765/` lists the camera and refreshes its frame. Restart the registrar after `fixr/run/secrets.env` exists, or the frame request returns 503.

`psram_mb=0` means the octal-PSRAM settings did not take. `camera=fail` means the flex or the module needs to be reseated. Flashing erases whatever is on the board now.

## Programming cable

Plug a USB-C **data** cable into the port labeled **TTL**. That port is the
CH340 serial link, VID `1a86`, and it is the one used for flashing and logs.
The baud rate on this board is **115200**.

The OTG port is the ESP32's native USB, VID `303a`. Opening it resets the
chip. Image capture over USB in the Lloyd reference script uses OTG for that
reason; programming does not.

Physical work belongs to the user. Say what to plug in, then wait.

With the board unplugged, the microSD card goes in the slot on the back,
contacts facing the board, until it clicks.

## What already happened

1. **Python.** The default `python3` is 3.9.6. The setup guide wants 3.10 or
   newer for the `witness` CLI. ESP-IDF 5.5.6 accepts 3.9 and its installer
   used 3.9.6. FIXR's registrar still runs on Homebrew Python 3.13.7.
2. **`witness` CLI.** Not installed. Milestone M2 is not in this repository.
   The bring-up image in `firmware/` did build.
3. **Serial port, flash, Wi-Fi, registrar.** On 2026-10-31 the TTL port was
   `/dev/cu.usbserial-11230`. The setup test flashed at 115200, joined Figaro,
   and registered as `witness-0d98`. The registrar listed it and served a JPEG.
   The port name can change on replug. A first `wifi=disconnected` is not a
   failure if `register=ok` follows.
4. **Registrar.** `fixr/registrar/serve.py --check` passed on 2026-10-31:
   a local client registered, the page loaded, and the frame request returned
   503 because `WITNESS_CAMERA_PASSWORD` was unset. A camera on the LAN cannot
   call the registrar until firmware exists that sends the POST. The check
   command is:

```bash
/opt/homebrew/bin/python3.13 fixr/registrar/serve.py --check
```

Start it for a real camera with:

```bash
/opt/homebrew/bin/python3.13 fixr/registrar/serve.py
```

The process prints `camera_password=set` or `camera_password=missing`. It
does not print the password. The registration URL it prints includes this
computer's current LAN address. Give that URL to the camera's firmware when
firmware can store it. Detect the address again at setup time; do not trust
the IP written in the profile if `en0` has changed.

## Registration message

```http
POST /api/register
Content-Type: application/json

{"id":"witness-ab12","fw":"0.0.0","board":"foriot_s3cam"}
```

The registrar stores the source IP of that connection. Each camera has its
own access password. This host reads one password from
`WITNESS_CAMERA_PASSWORD` and sends it only when proxying
`GET http://<camera-ip>/api/v1/frame.jpg`.

## After the board is connected

Follow `docs/SETUP.md` from S3. Expect one port with kind `ttl`. Stop after
two failed attempts at the same step and report what you saw. Update
`install_notes` in the profile with `ok`, `failed`, `skipped`, or `untested`,
and rebuild nothing unless you also changed a document under `docs/`.
