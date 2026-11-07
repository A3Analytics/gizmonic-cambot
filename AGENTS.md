# Agent instructions

This repository is the Gizmonic Cambot specification, the bring-up firmware in
`firmware/`, and the printable case in `hardware/`. The `cambot` command
described in the documents does not exist here yet.

## Which document to follow

- Setting up a physical camera: follow `fixr/SETUP.md` from the top. Section 0
  is the contract. Read the profile for this computer next
  (`fixr/profiles/waterbear.json` and `fixr/profiles/waterbear.md` on this
  machine). The `cambot` commands in that guide ship with firmware
  milestone M2; until then, say so instead of inventing commands.
- Designing or changing the case and mounts: `docs/src/astra-mechanical-spec.md`.
  Dimensions marked TBV are not confirmed. Do not replace them with guesses.
- Printing the case: `hardware/cambot-minimum-petg-kit/README.md` lists the
  plates, parts, PETG settings, hardware, and assembly. Its STL, STEP, and 3MF
  files are a frozen release checked by `manifest.json`. To change a part, edit
  the generator under `source/`, regenerate, and update the exports and
  checksums together.
- Firmware, API, storage format, or repository layout: `docs/src/firmware-repo-plan.md`.
  Section 13 of that plan is the contributor rule set for the future firmware
  repository.

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

## Working in this repository

- Edit the Markdown in `docs/src` and `fixr/SETUP.md`, then rebuild with
  `docs/build.sh`. Commit the sources and the rebuilt HTML and PDF together.
- Do not commit secrets. `secrets.h`, `.env`, and credential files stay out.
- `reference/from-lloyd-barbot/` is a copy of the board bring-up that the
  documents were written from. Change the documents when the design changes;
  do not silently "fix" that reference to match.
