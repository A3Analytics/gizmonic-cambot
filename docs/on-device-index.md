# On-device indexing

The host indexer in `host/cambot_index/pipeline.py` is the contract. That loop
now runs on the ESP32-S3. The registrar shows the latest keyframe description,
edits nouls, and answers questions from a local SQLite copy of the index.

## What the camera does today

The recorder grabs VGA JPEGs (~5 fps), copies each to PSRAM, and appends it
under `/sdcard/cambot/rec/…` with motion in the binary frame index. An
`indexer` task scores motion, picks keyframes, optionally calls GPT and/or
JEV, and appends JSONL under `/sdcard/cambot/index/` plus events under
`events/`.

`cambot_llm_openai`, `cambot_llm_anthropic`, and `cambot_llm_jev` POST one JPEG
and receive a body. GPT captions and JEV nouls are separate switches. JEV
indexing runs on the camera: one System One request per keyframe, with the
live frame, any example images, every noul, and an optional box question. The
registrar stores that configuration and draws the results. It does not call
TypeSafe.

`jev_api_key.txt` and `openai_api_key.txt` stay gitignored. Copy them into
`firmware/main/secrets.h` as `CAMBOT_JEV_KEY` and `CAMBOT_MODEL_KEY` when
building. Never print them.

## Both cores

The ESP32-S3 has two cores. Boot already reports `cores=2`. Prefer:

- Core 1: the recorder. Grab, copy, append to the card, release the camera
  mutex.
- Core 0: the indexer, next to the network stack. Motion, keyframes, model
  calls, JSONL.

The indexer must use its own JPEG copy and must not hold the camera mutex
across the network call.

## Loop

Match `Indexer` in `host/cambot_index/pipeline.py`.

1. Motion: grayscale, shrink, mean absolute change, 0 to 1, stored as
   thousandths in the frame index.
2. Keyframe: first frame, then every ~3 seconds while motion stays above
   threshold, otherwise every ~60 seconds. Robot marks come later.
3. When GPT indexing is on, caption with `gpt-4.1-mini`. When JEV indexing is
   on, score nouls with TypeSafe. Either switch can be on alone.
4. Append one JSON line per keyframe under
   `/sdcard/cambot/index/YYYY-MM-DD/HH.jsonl`.
5. Merge events into `/sdcard/cambot/events/YYYY-MM-DD.jsonl`.
6. The daily budget pauses the indexer only. Recording continues.
7. Keep a RAM recent window for MCP `search_index` and registrar sync;
   `read_index_page` reads deeper history from the card.

Nouls live in `nouls.json` plus example JPEGs under `nouls/<name>/`. An empty
list skips the JEV call. A missing `nouls.json` still reads the older
`watch.json` shape.

Each noul is a yes/no question. Up to four example images can be attached.
An optional box question returns edges as frame fractions; the camera keeps a
box when that noul’s yes-probability is at least 0.5. An optional alert is an
`https` webhook POSTing `{"text":"..."}` on edge into yes or no. Webhook URLs
are not printed.

`GET/POST /api/v1/indexing` is GPT indexing. `GET/POST /api/v1/jev` is JEV
indexing. `GET/PUT /api/v1/nouls` stores the questions. Examples are
`PUT`/`GET` at `/api/v1/nouls/<name>/examples/<id>` and are refused above 80KB.

## Registrar

`GET /api/v1/index/latest` returns the newest index record as JSON with bearer
auth.

The registrar proxies that for each registered camera, the same way it proxies
`frame.jpg`, using `CAMBOT_CAMERA_PASSWORD` and never printing it. The card
under the live frame shows the caption, the time, and the objects. Boxes from
the latest index line are drawn on the live image. Index history is copied
into `fixr/run/index.sqlite` so ask can cover days without stalling the
camera. Ask uses gpt-4.1-mini on this computer, including JEV watch scores in
the prompt.

The registrar also owns the noul editor, GPT/JEV checkboxes, High performance
mode, and Dev mode (idle indexing off).

## Still out of scope here

On-board `POST /api/v1/ask`, robot marks, SoftAP / Improv provisioning, and the
`cambot` CLI. Those remain later milestones in the software plan.
