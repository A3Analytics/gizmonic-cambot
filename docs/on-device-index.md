# On-device indexing

The host indexer in `host/cambot_index/pipeline.py` is the contract. This branch moves that loop onto the ESP32-S3 and shows the latest keyframe description in the registrar.

The registrar chat is a separate branch, `registrar-chat`. Both branches edit `fixr/registrar/serve.py`. Keep the caption display to one proxied field so the two can merge.

## What the camera does today

The recorder on core-unpinned task `recorder` grabs a QVGA JPEG, copies it to PSRAM, and appends it to `/sdcard/cambot/rec/…`. The 18-byte frame index stores motion as 0. Nothing writes `/sdcard/cambot/index/` or `/sdcard/cambot/events/`.

`cambot_llm_openai`, `cambot_llm_anthropic`, and `cambot_llm_jev` can POST one JPEG and receive a body. Boot only prints whether each key was compiled in. GPT captions and JEV nouls are separate switches. JEV indexing runs on the camera: one System One request per keyframe, with the live frame, any example images, every noul, and an optional box question. The registrar stores that configuration and draws the results. It does not call TypeSafe.

`jev_api_key.txt` and `openai_api_key.txt` stay gitignored. Copy them into `firmware/main/secrets.h` as `CAMBOT_JEV_KEY` and `CAMBOT_MODEL_KEY` when building this branch. Never print them.

## Both cores

The ESP32-S3 has two cores. Boot already reports `cores=2`. `xTaskCreate` leaves the core to the scheduler. Wi-Fi runs on core 0.

Pin the work like this:

- Core 1: the recorder. Grab, copy, append to the card, release the camera mutex. Frame timing stays off the Wi-Fi core.
- Core 0: a new `indexer` task, next to the network stack. It scores motion, decides keyframes, calls the models, and appends JSONL.

A single JPEG is not split across the two cores. Motion on a 320×240 frame is cheap next to the HTTPS wait. The useful split is capture versus indexing, so a caption that takes seconds does not stall the grab. The indexer must use its own JPEG copy and must not hold the camera mutex across the network call.

Put the model JSON body in PSRAM once a request holds more than one frame, or once capture moves above QVGA. One QVGA body fits in internal RAM.

## Loop to port

Match `Indexer` in `host/cambot_index/pipeline.py`.

1. Motion: grayscale, shrink to one eighth, mean absolute change, 0 to 1. Store thousandths in the existing motion field instead of 0.
2. Keyframe: first frame, then every 3 seconds while motion stays above 0.05, otherwise every 60 seconds. Robot marks come later.
3. Batch up to four keyframes. When GPT indexing is on, caption each with `gpt-4.1-mini` into caption, objects, actions, and robot state. When JEV indexing is on, send that keyframe to JEV `jev-1.14.0` with the nouls stored on the card. Either switch can be on alone.
4. Append one JSON line per keyframe under `/sdcard/cambot/index/YYYY-MM-DD/HH.jsonl`, with the same fields as the host record.
5. Merge events into `/sdcard/cambot/events/YYYY-MM-DD.jsonl`.
6. The daily budget pauses the indexer only. Recording continues.
7. A dropped caption still keeps a JEV score when one came back.

The nouls are a file on the card, `nouls.json`, plus example JPEGs under `nouls/<name>/`. The camera does not invent them. The scene profile is “General camera setup. Testing basic functionality.” An empty list means a JEV call is skipped and `watch` is `{}`. A missing `nouls.json` still reads the older `watch.json` list of name, instructions, and label.

Each noul is a yes/no question. Up to four example images can be attached, each marked yes or no, and the camera names them in the instructions (`A yes looks like ex_00`). An optional box question is sent in the same request. The answer is four edges as fractions of the frame. A box is always a rectangle, so the camera keeps it on the index line only when that noul’s yes-probability is at least 0.5, and stores the color chosen in the registrar. An optional alert is an `https` webhook. The camera POSTs `{"text":"..."}` when the answer crosses into the chosen yes or no, then waits until the answer leaves that state. The webhook URL is not printed.

`GET/POST /api/v1/indexing` is GPT indexing. `GET/POST /api/v1/jev` is JEV indexing. `GET/PUT /api/v1/nouls` stores the questions. Example JPEGs are `PUT` and `GET` at `/api/v1/nouls/<name>/examples/<id>` and are refused above 80KB.

## Registrar

Add `GET /api/v1/index/latest` on the camera. It returns the newest index record as JSON, with bearer auth, and no image body.

The registrar proxies that for each registered camera, the same way it proxies `frame.jpg`, using `CAMBOT_CAMERA_PASSWORD` and never printing it. The card under the live frame shows the caption, the time, and the objects. An empty index shows nothing in that slot. Boxes from the latest index line are drawn on the live image in the color stored with the noul.

The registrar has a JEV nouls section. It uploads example images, after shrinking them, and saves the questions onto the camera. The JEV checkbox says the camera calls TypeSafe. Ask keeps `watch` scores in the prompt it sends to gpt-4.1-mini, so a question about when a noul was yes or no can be answered with GPT indexing off. Webhook URLs are not written to the registrar log.

## Tests

- C motion and keyframe checks compiled with `cc`, same style as `test/host/c/test_idx.c`, against the Python results for one still pair and one changed pair.
- A fake HTTP transport is not available on device. The JSON builders stay covered by `test/host/c/test_llm_body.c`.
- On the board: after a short recording, `GET /api/v1/index/latest` returns a caption, the JSONL line is on the card, and the registrar page shows that sentence. Flash at 115200 on the TTL port. The card and this board can be reflashed without asking.

## Order

1. Motion and keyframes on the device, still with motion written into the binary index.
2. One OpenAI caption appended to the card.
3. JEV scores when JEV indexing is on and the noul file is non-empty.
4. Registrar shows the latest caption.

Out of this branch: the registrar chat, `POST /api/v1/ask`, live MJPEG, provisioning, and the `cambot` CLI.
