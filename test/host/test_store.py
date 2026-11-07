"""Host checks for the rolling buffer and its time-range API."""

from __future__ import annotations

import json
import struct
import subprocess
from datetime import datetime, timedelta, timezone

from host.cambot_store.api import Camera
from host.cambot_store.format import IDX, Card
from host.cambot_store.timeparse import parse_bound
from zoneinfo import ZoneInfo

LA = ZoneInfo("America/Los_Angeles")
JPEG_A = b"\xff\xd8" + b"A" * 20 + b"\xff\xd9"
JPEG_B = b"\xff\xd8" + b"B" * 30 + b"\xff\xd9"
TOKEN = "camera-token"


def _aware(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_idx_layout_matches_python_and_c(tmp_path):
    packed = IDX.pack(1000, 2, 3, 4)
    assert len(packed) == 18
    assert packed == bytes(
        [0xE8, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00, 0x00, 0x04, 0x00]
    )
    binary = tmp_path / "test_idx_bin"
    subprocess.check_call(
        ["cc", "-I", "firmware/components/store/include", "-o", str(binary), "test/host/c/test_idx.c", "firmware/components/store/idx.c"]
    )
    subprocess.check_call([str(binary)])
    llm = tmp_path / "test_llm_bin"
    subprocess.check_call(
        [
            "cc",
            "-I",
            "firmware/components/llm/include",
            "-o",
            str(llm),
            "test/host/c/test_llm_body.c",
            "firmware/components/llm/llm_body.c",
            "firmware/components/llm/alert.c",
        ]
    )
    subprocess.check_call([str(llm)])


def test_minute_segment_roundtrip_and_queries(tmp_path):
    card = Card(tmp_path)
    start = _aware("2026-10-29T19:04:10")
    card.add_frame(start, JPEG_A, motion=0.41)
    card.add_frame(start + timedelta(seconds=40), JPEG_B, motion=0.0)
    card.add_frame(_aware("2026-10-29T19:05:01"), JPEG_A, motion=0.2)
    reopened = Card(tmp_path)
    frames = reopened.frames(start, _aware("2026-10-29T19:06:00"))
    assert [frame.jpeg for frame in frames] == [JPEG_A, JPEG_B, JPEG_A]
    assert frames[0].motion == 0.41
    assert frames[0].offset == 0
    assert frames[1].offset == len(JPEG_A)
    seg = tmp_path / "cambot" / "rec" / "2026-10-29" / "19" / "2026-10-29T19-04-00Z.mjpg"
    assert seg.read_bytes() == JPEG_A + JPEG_B
    idx = seg.with_suffix(".idx")
    first = IDX.unpack_from(idx.read_bytes(), 0)
    assert first[1] == 0 and first[2] == len(JPEG_A)
    assert reopened.nearest(_aware("2026-10-29T19:04:31")).jpeg == JPEG_B
    assert reopened.nearest(_aware("2026-10-29T19:04:20")).jpeg == JPEG_A
    assert reopened.frames(_aware("2026-10-29T21:00:00"), _aware("2026-10-29T22:00:00")) == []
    assert reopened.clip(start, start + timedelta(seconds=40)) == JPEG_A + JPEG_B


def test_time_bounds_rfc3339_local_and_relative():
    now = _aware("2026-10-31T20:00:00")
    assert parse_bound("2026-10-31T19:00:00-07:00", now, LA) == _aware("2026-11-01T02:00:00")
    # 31 Oct 2026 is still Pacific Daylight Time (UTC-7).
    assert parse_bound("12:00", now, LA) == _aware("2026-10-31T19:00:00")
    assert parse_bound("-30m", now, LA) == _aware("2026-10-31T19:30:00")


def test_local_and_relative_queries_skip_unsynced_until_repair(tmp_path):
    card = Card(tmp_path)
    now = _aware("2026-10-31T20:00:00")
    unsynced = b"\xff\xd8" + b"C" * 8 + b"\xff\xd9"
    card.add_frame(_aware("2026-10-31T19:40:00"), JPEG_A, synced=True)
    card.add_frame(_aware("2026-10-31T19:00:00"), JPEG_B, synced=True)
    card.add_frame(now, unsynced, synced=False)
    camera = Camera(card, TOKEN, now=now)
    header = {"Authorization": f"Bearer {TOKEN}"}
    status, _kind, body = camera.dispatch("GET", "/api/v1/recordings?from=-30m&to=20:00", header)
    assert status == 200
    assert sum(row["frames"] for row in json.loads(body)) == 1
    assert card.frames(parse_bound("-30m", now, LA), now)[0].jpeg == JPEG_A
    status, _kind, body = camera.dispatch("GET", "/api/v1/frames?from=12:00&to=12:30&every_s=60", header)
    listed = json.loads(body)
    assert len(listed) == 1
    assert all(frame.jpeg != unsynced for frame in card.frames(parse_bound("12:00", now, LA), parse_bound("12:30", now, LA)))
    assert card.repair_unsynced(_aware("2026-10-31T19:10:00")) == 1
    repaired = card.frames(_aware("2026-10-31T19:10:00"), _aware("2026-10-31T19:10:00"))
    assert repaired[0].jpeg == unsynced
    noon = card.frames(parse_bound("12:00", now, LA), parse_bound("12:30", now, LA))
    assert any(frame.jpeg == unsynced for frame in noon)


def test_eviction_keeps_index_then_drops_it_after_keep_days(tmp_path):
    card = Card(tmp_path, capacity=5000, reserve_pct=10, index_keep_days=30)
    hour1 = _aware("2026-10-29T18:10:00")
    hour2 = _aware("2026-10-29T19:10:00")
    blob = b"\xff\xd8" + (b"x" * 1000) + b"\xff\xd9"
    for _ in range(3):
        card.add_frame(hour1, blob)
    card.append_record({"t": "2026-10-29T18:10:00.000Z", "caption": "kept"})
    for _ in range(3):
        card.add_frame(hour2, blob)
    card.maintain(hour2)
    assert not (tmp_path / "cambot" / "rec" / "2026-10-29" / "18").exists()
    assert (tmp_path / "cambot" / "index" / "2026-10-29" / "18.jsonl").exists()
    assert (tmp_path / "cambot" / "rec" / "2026-10-29" / "19").exists()
    card.maintain(hour2 + timedelta(days=31))
    assert not (tmp_path / "cambot" / "index" / "2026-10-29" / "18.jsonl").exists()


def test_api_auth_clip_and_two_minute_recording(tmp_path):
    card = Card(tmp_path)
    start = _aware("2026-10-31T19:00:00")
    jpeg = b"\xff\xd8\xff\xd9"
    for index in range(600):
        card.add_frame(start + timedelta(milliseconds=200 * index), jpeg)
    end = start + timedelta(minutes=2)
    camera = Camera(card, TOKEN, now=end)
    status, _kind, body = camera.dispatch("GET", "/api/v1/ping", {})
    assert status == 200 and json.loads(body)["ok"] is True
    status, _kind, _body = camera.dispatch("GET", "/api/v1/recordings?from=19:00&to=19:02", {})
    assert status == 401
    header = {"Authorization": f"Bearer {TOKEN}"}
    status, _kind, body = camera.dispatch(
        "GET",
        "/api/v1/recordings?from=2026-10-31T19:00:00Z&to=2026-10-31T19:02:00Z",
        header,
    )
    assert status == 200
    assert sum(row["frames"] for row in json.loads(body)) == 600
    status, kind, body = camera.dispatch("GET", "/api/v1/frame/2026-10-31T19:00:30Z.jpg", header)
    assert status == 200 and kind == "image/jpeg" and body == jpeg
    status, _kind, body = camera.dispatch(
        "GET",
        "/api/v1/clip.mjpg?from=2026-10-31T19:00:00Z&to=2026-10-31T19:00:00.200Z",
        header,
    )
    assert body == jpeg + jpeg
    status, kind, body = camera.dispatch(
        "GET",
        "/api/v1/clip.avi?from=2026-10-31T19:00:00Z&to=2026-10-31T19:00:00.000Z",
        header,
    )
    assert status == 200 and body.startswith(b"RIFF") and jpeg in body
    assert struct.calcsize("<4s4sIHH8I4h") == 56


def test_empty_range(tmp_path):
    card = Card(tmp_path)
    camera = Camera(card, TOKEN, now=_aware("2026-10-31T20:00:00"))
    status, _kind, body = camera.dispatch(
        "GET",
        "/api/v1/recordings?from=12:30&to=12:00",
        {"Authorization": f"Bearer {TOKEN}"},
    )
    assert status == 200 and json.loads(body) == []
