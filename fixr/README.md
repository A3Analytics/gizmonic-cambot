# FIXR

Setup lives here. [`SETUP.md`](SETUP.md) is the shared guide for a person or a
coding agent.

An agent reads `SETUP.md` first, then the closest host profile for the computer
it is on. Profiles are JSON + notes under `profiles/`. The shipped example is
[`profiles/macos-arm64.json`](profiles/macos-arm64.json) with notes in
[`profiles/macos-arm64.md`](profiles/macos-arm64.md).

If the guide fails on another computer, or you have useful tips for a different
architecture, add two files and open a pull request:

- `profiles/<id>.json`, matching [`schema/host-profile.schema.json`](schema/host-profile.schema.json)
- `profiles/<id>.md`, the setup notes for that system

Leave every secret value out of both files.

## What a profile may contain

Operating system, CPU, the Python interpreter to use, tool versions, the LAN
address observed that day, and optional Wi-Fi notes. Passwords and API keys
appear only as the environment variable names in `secrets`.

| Value | Where it lives |
|---|---|
| Wi-Fi password | `CAMBOT_WIFI_PASSWORD` |
| Camera access password | `CAMBOT_CAMERA_PASSWORD` |
| Model API key | `CAMBOT_MODEL_KEY` |
| Host time zone | `CAMBOT_TZ` or `TZ` (IANA), or `setup.time_zone` in the profile |

`ask_before_setup` is the list an agent still has to get from the user. Do not
guess a time zone, a scene description, or a model endpoint.

## Registrar

`registrar/serve.py` is a small web app on this computer. When a camera boots
and can reach the LAN, it POSTs its identity to `/api/register`. The page
lists the camera and reloads `GET /api/v1/frame.jpg` from the camera every two
seconds by default. The **High performance** switch on the page drops that to
0.5 s and leaves capture at 5 fps; leave it off during install. The registrar
uses the TCP source address as the camera's IP, so
the message cannot point the viewer at some other host.

The body is specified in
[`schema/camera-registration.schema.json`](schema/camera-registration.schema.json).
The camera password is sent only from this computer to the camera, as
`Authorization: Bearer`, and only when a browser asks for a frame.

```bash
export CAMBOT_CAMERA_PASSWORD    # hidden prompt or your own shell; do not echo it
export CAMBOT_TZ=America/Los_Angeles
python3 fixr/registrar/serve.py
```

The process prints the LAN address and `tz=...` it resolved. Use the Python
interpreter from your host profile when the system `python3` is too old.

```bash
python3 fixr/registrar/serve.py --check
```

`--check` validates a host profile. By default it uses the first
`profiles/*.json` file; set `CAMBOT_HOST_PROFILE` to a path or profile id to
pick another.

To check that JEV scores keep reaching the page, run the watch script with
the registrar up. It turns JEV on, polls the way the page does, prints every
new score and the longest gap, then turns JEV and GPT indexing off.

```bash
.venv/bin/python test/hil/jev_watch.py --seconds 60 --poll 0.5 \
  --registrar http://127.0.0.1:8765 --serial /dev/cu.usbserial-XXXX
```

Runtime state is written under `fixr/run/`, which is gitignored. If
`fixr/run/secrets.env` exists, the registrar loads it at startup and does not
override variables already set in the environment. Put
`CAMBOT_CAMERA_PASSWORD=...` in that file. Do not commit it, and do not paste
the value into chat.

Firmware credentials are compiled from `firmware/main/secrets.h`. Copy
`secrets.h.example` to `secrets.h` and fill every required field before
building.

The programming environment is ESP-IDF 5.x in C. `firmware/` is the Cambot
bring-up image: it records to the SD card, indexes with GPT/JEV when enabled,
joins Wi-Fi, POSTs `/api/register`, and serves REST/MCP. The `cambot` CLI still
ships with milestone M2. Until then, do not invent `cambot` commands; follow
`SETUP.md` and flash with ESP-IDF.
