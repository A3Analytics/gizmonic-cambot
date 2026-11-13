# Agent instructions

This repository is **gizmonic-cambot**: the bring-up firmware in `firmware/`,
the printable case in `hardware/`, the FIXR setup path and registrar, and the
design documents. The `cambot` CLI described for milestone M2 does not exist
here yet.

## Which document to follow

- Setting up a physical camera: follow `fixr/SETUP.md` from the top. Section 0
  is the contract. Read the closest host profile next
  (`fixr/profiles/<id>.json` and the matching `.md`; the shipped example is
  `macos-arm64`). The `cambot` commands in that guide ship with firmware
  milestone M2; until then, say so instead of inventing commands.
- Designing or changing the case and mounts: `docs/src/astra-mechanical-spec.md`.
  Dimensions marked TBV are not confirmed. Do not replace them with guesses.
- Printing the case: `hardware/cambot-minimum-petg-kit/README.md` lists the
  plates, parts, PETG settings, hardware, and assembly. Its STL, STEP, and 3MF
  files are a frozen release checked by `manifest.json`. To change a part, edit
  the generator under `source/`, regenerate, and update the exports and
  checksums together.
- Firmware, API, storage format, or repository layout: `docs/src/firmware-repo-plan.md`.
  Section 13 of that plan is the contributor rule set for this repository.

## Setup contract

Copied from `fixr/SETUP.md`. These rules apply whenever an agent sets a camera
up, from this repo or another.

1. Ask the user for the Wi-Fi SSID, the model endpoint, the time zone, and a
   one or two sentence description of what the camera watches. Never guess
   them. The network must be 2.4 GHz, WPA2 or WPA3 Personal.
2. Never print, log, echo, or commit the Wi-Fi password, the model API key, or
   the camera token. Pass them through environment variables, stdin, or a
   hidden prompt. If a secret appears in output, tell the user.
3. Ask before formatting an SD card, erasing flash, or rotating a token.
4. Physical steps belong to the user: plugging cables, pressing BOOT or RST,
   and inserting the card. Say exactly what to do and wait.
5. Run each step's expected check before the next step. On failure, use that
   step's fix table, then the troubleshooting section. Stop after two failed
   attempts at the same step and report what you saw.

## Host profiles and pull requests

Profiles under `fixr/profiles/` are shared install guidance for agents.
After a successful setup, if this host needed different tools, paths, or
workarounds than the closest existing profile, add or update
`profiles/<id>.json` and `profiles/<id>.md` (no secrets) and open a pull
request so other architectures benefit.

## Working in this repository

- Edit the Markdown in `docs/src` and `fixr/SETUP.md`, then rebuild with
  `docs/build.sh`. Commit the sources and the rebuilt HTML and PDF together.
- Do not commit secrets. `secrets.h`, `.env`, and credential files stay out.
- Copy `firmware/main/secrets.h.example` to `secrets.h` and fill every required
  field before building. Set `CAMBOT_TZ` or `TZ` on the host to the same IANA
  zone used in `CAMBOT_TZ` inside `secrets.h`.
