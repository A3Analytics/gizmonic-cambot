# Gizmonic Cambot

![Cambot](assets/cambot.png)

Open hardware and firmware for a cheap ESP32-S3 camera that records a rolling
buffer to its microSD card, indexes what happened with a vision model on the
device, and answers questions from a LAN registrar — for example "show me
between 12:00 and 12:30 where the machine picks up a pipette tip."

Watch the build and walkthrough: [Cambot on YouTube](https://www.youtube.com/watch?v=MEkpdgEEsck).

## How to use this repository

Point a coding agent (Cursor, Claude Code, Codex, and similar) at this repo and
tell it to set up the camera. The agent should follow [`fixr/SETUP.md`](fixr/SETUP.md)
from the top — section 0 is the contract — then the closest host profile under
`fixr/profiles/` (start with [`macos-arm64`](fixr/profiles/macos-arm64.md) on
Apple Silicon Macs).

You will need to supply the Wi-Fi SSID, Wi-Fi password, model endpoint (optional
for recording-only), time zone, and a short description of what the camera
watches. Copy `firmware/main/secrets.h.example` to `secrets.h` and fill it as
documented there. Host secrets stay in environment variables or hidden prompts;
they are never committed. Set `CAMBOT_TZ` (or `TZ`) to the same IANA zone for
the registrar.

Until firmware milestone M2, the `cambot` CLI is not shipped. Agents flash the
bring-up image in `firmware/` with ESP-IDF instead of inventing CLI commands.

People can follow the same guide by hand. Agents are the intended path.

## Camera board

The reference board is the **FORIOT ESP32-S3-CAM** with an OV3660 sensor:

| Item | Detail |
|---|---|
| Module | ESP32-S3-WROOM-1 N16R8 (16 MB flash, 8 MB octal PSRAM) |
| Camera | OV3660 on a 24-pin DVP flex |
| Storage | microSD, 1-bit SDMMC |
| USB | Dual USB-C: **TTL** (CH340, flash and logs) and OTG |
| Listing | [Amazon B0GD2BZ9B1](https://www.amazon.com/dp/B0GD2BZ9B1) (often sold as a 3-pack) |

Similar ESP32-S3-CAM layouts from Freenove, Goouuu, and Keyestudio may work; the
firmware pin map today matches this FORIOT board. Use a USB-C **data** cable on
the TTL port at **115200** baud.

## 3D-printed mount

The printable kit in [`hardware/cambot-minimum-petg-kit/`](hardware/cambot-minimum-petg-kit/)
is a PETG drop-in housing, retainer, modular arm rods, and a dual-magnet foot
that sticks to steel. Print the three plate 3MFs (or the individual part files),
assemble with the hardware below, and aim the camera. Full print settings,
assembly steps, and generators are in that kit’s README.

### Bill of materials (one assembled mount)

| Qty | Part | Notes |
|---:|---|---|
| 1 | FORIOT ESP32-S3-CAM (OV3660, N16R8) | Board above |
| 1 | microSD card, 8–128 GB | High-endurance recommended |
| 1 | USB-C data cable | Charge-only cables fail setup |
| 2 | M5 × 25 mm bolts or thumbscrews | End pivots (foot↔arm, arm↔case) |
| 2 | M5 nuts | For the M5 pivots |
| 1 | 6-32 machine screw, ~3/8 in | Arm length lock; form the PETG thread by hand |
| 2 | Rubber-coated mounting magnets, Ø ~22 mm, M4 | Mount **under** the foot, faces to steel |
| 2 | M4 washers, ~12 mm OD × 1 mm | Sit in the foot recesses |
| 2 | M4 screws or nuts | Match magnet stud/thread gender |

Reference magnet: [Radial Magnet 22 mm rubber-coated assembly (SKU 9763)](https://radialmagnet.com/our-magnets/22mm-rubber-coated-outdoor-magnetic-assembly-base-holds-up-to-8-lbs/).
25–30 mm magnets also fit; 32 mm is the maximum without side overhang.

## What’s in the tree

| Path | Role |
|---|---|
| [`fixr/SETUP.md`](fixr/SETUP.md) | Install a camera (person or coding agent) |
| [`fixr/`](fixr/README.md) | Host profiles, secrets env names, LAN registrar |
| [`firmware/`](firmware/) | ESP-IDF bring-up image |
| [`hardware/cambot-minimum-petg-kit/`](hardware/cambot-minimum-petg-kit/README.md) | PETG case, arms, magnet foot |
| [`docs/`](docs/) | White paper, mechanical spec, software plan |

| Document | Read it for |
|---|---|
| [White paper](docs/cambot-whitepaper.pdf) | What it is and how a question becomes an answer |
| [Mechanical spec](docs/cambot-astra-mechanical-spec.pdf) | Current PETG kit and W-Mount contract |
| [Firmware / software plan](docs/cambot-firmware-repo-plan.pdf) | Tree, API, indexer, registrar, remaining milestones |
| [Setup guide](fixr/SETUP.md) | End-to-end install |

HTML copies sit next to the PDFs. Sources are in `docs/src`. `docs/build.sh`
rebuilds them (needs pandoc and Google Chrome).

## Contributing install tips

If your host differs from the shipped profiles (Linux, Windows, another macOS
layout, serial naming, ESP-IDF path), add `fixr/profiles/<id>.json` and
`fixr/profiles/<id>.md` with the useful notes, keep secrets out, and open a
pull request so other agents can reuse them.

## Licenses

| Part | License |
|---|---|
| Software | [Apache-2.0](licenses/Apache-2.0.txt) |
| Hardware / CAD | [CERN-OHL-P-2.0](licenses/CERN-OHL-P-2.0.txt) |
| Documentation | [CC BY 4.0](licenses/CC-BY-4.0.txt) |

See [`LICENSE.md`](LICENSE.md) for the split. The printable kit also has
[`hardware/cambot-minimum-petg-kit/LICENSE.md`](hardware/cambot-minimum-petg-kit/LICENSE.md).
