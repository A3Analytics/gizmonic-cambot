# FIXR

Shared setup notes for putting a Witness camera on a particular computer.
The camera steps themselves stay in [`docs/SETUP.md`](../docs/SETUP.md). FIXR
records the host, what that host already has installed, and what happened when
an agent tried the guide.

Another person adds a profile by copying `profiles/waterbear.json`, filling it
from their own machine, and leaving every secret value out. The file must
match [`schema/host-profile.schema.json`](schema/host-profile.schema.json).

## What a profile may contain

Operating system, CPU, the Python interpreter to use, tool versions, the LAN
address observed that day, and the Wi-Fi name. Passwords and API keys appear
only as the environment variable names in `secrets`.

| Value | Where it lives |
|---|---|
| Wi-Fi password | `WITNESS_WIFI_PASSWORD` |
| Camera access password | `WITNESS_CAMERA_PASSWORD` |
| Model API key | `WITNESS_MODEL_KEY` |

`ask_before_setup` is the list an agent still has to get from the user. Do not
guess a time zone, a scene description, or a model endpoint.

## Registrar

`registrar/serve.py` is a small web app on this computer. When a camera boots
and can reach the LAN, it POSTs its identity to `/api/register`. The page
lists the camera and reloads `GET /api/v1/frame.jpg` from the camera every two
seconds. The registrar uses the TCP source address as the camera's IP, so the
message cannot point the viewer at some other host.

The body is specified in
[`schema/camera-registration.schema.json`](schema/camera-registration.schema.json).
The camera password is sent only from this computer to the camera, as
`Authorization: Bearer`, and only when a browser asks for a frame.

```bash
export WITNESS_CAMERA_PASSWORD    # hidden prompt or your own shell; do not echo it
/opt/homebrew/bin/python3.13 fixr/registrar/serve.py
```

On this machine the page is `http://192.168.68.91:8765/` while that address
stays assigned to `en0`. The process prints the address it detected.

```bash
/opt/homebrew/bin/python3.13 fixr/registrar/serve.py --check
```

Runtime state is written under `fixr/run/`, which is gitignored. If
`fixr/run/secrets.env` exists, the registrar loads it at startup and does not
override variables already set in the environment. Put
`WITNESS_CAMERA_PASSWORD=...` in that file. Do not commit it, and do not paste
the value into chat.

The programming environment in the repository plan is ESP-IDF 5.x in C.
This machine has v5.5.6, which the plan does not pin. `firmware/` is the setup
test. It joins Wi-Fi, POSTs `/api/register`, and serves the live frame. The
Witness firmware itself is a later step. `witness` ships with milestone M2.
Until then, do not invent `witness` commands.

Agents follow [`AGENT.md`](AGENT.md).
