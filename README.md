# Witness camera

Open specification for a cheap ESP32-S3 camera that records a rolling buffer to
its microSD card and answers questions about what a robot or machine did, for
example "show me between 12:00 and 12:30 where the machine picks up a pipette
tip."

The case, mounts, and firmware described here are not built yet. This
repository holds the documents that define them.

| Document | Read it for |
|---|---|
| [White paper](docs/witness-whitepaper.pdf) | What it is and how a question becomes an answer |
| [Mechanical spec](docs/witness-astra-mechanical-spec.pdf) | Case, clamp, and magnet mount, written for CAD |
| [Firmware repository plan](docs/witness-firmware-repo-plan.pdf) | On-board software, API, agent, and repo layout |
| [Setup guide](docs/SETUP.md) | Installing a camera, written for a person or a coding agent |

HTML copies of the same documents are next to the PDFs. Sources, diagrams, and
`build.sh` are in `docs/src` and `docs/build.sh`. Rebuilding needs pandoc and
Google Chrome.

`reference/from-lloyd-barbot/` holds the board files these documents were
written from.

## Licenses

Software is intended as Apache-2.0, the case and mounts as CERN-OHL-P-2.0, and
the documentation as CC BY 4.0. Those licenses are proposed in the documents
and are not yet attached as license files.
