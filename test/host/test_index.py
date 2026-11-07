"""Motion, keyframes, JEV watch scores, captions, events, and the daily budget."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

from host.cambot_index.budget import Budget
from host.cambot_index.events import merge_events
from host.cambot_index.images import shape_jpeg
from host.cambot_index.jsonutil import INDEX_FIELDS
from host.cambot_index.keyframes import keyframe_due
from host.cambot_index.motion import motion_score
from host.cambot_index.pipeline import Indexer
from host.cambot_models.errors import ModelCallError
from host.cambot_models.jev import ScoredFrames
from host.cambot_models.questions import NoulQuestion
from host.cambot_store.api import Camera
from host.cambot_store.format import Card, iso

QUESTION = NoulQuestion("tip_pickup", "Is a tip being picked up in `photo`?", "tip pickup", "yes")
T0 = datetime(2026, 10, 29, 19, 7, 41, tzinfo=timezone.utc)


class FakeCaption:
    provider = "openai"
    model = "gpt-4.1-mini"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def caption_json(self, image, prompt):
        self.calls += 1
        if self.fail:
            raise ModelCallError("dropped")
        return (
            {
                "caption": "Pipette head lowers onto tip rack column 3",
                "objects": ["pipette head", "tip rack"],
                "actions": ["tip pickup"],
                "robot_state": "moving",
            },
            7,
        )


class FakeDecider:
    def __init__(self, score: float = 0.92):
        self.score = score
        self.calls = 0
        self.sizes = []
        self.model = "typesafe/jev-1.14.0"

    def ask_frames(self, frames, questions):
        self.calls += 1
        self.sizes.append(len(frames))
        scores = [{question.label: self.score for question in questions} for _frame in frames]
        return ScoredFrames(scores=scores, tokens=3, model=self.model)


def _motion_milli(previous, current):
    if previous is None or len(previous) != len(current) or not current:
        return 0
    total = sum(abs(left - right) for left, right in zip(previous, current))
    denom = len(current) * 255
    milli = (total * 1000 + denom // 2) // denom
    return min(1000, milli)


def test_motion_milli_and_keyframe_match_c(tmp_path):
    same = [0, 1, 2, 3, 4, 5, 6, 7]
    moved = [255] * 8
    assert _motion_milli(None, same) == 0
    assert _motion_milli(same, same) == 0
    assert _motion_milli(same, moved) == 986
    binary = tmp_path / "test_motion_bin"
    subprocess.check_call(
        [
            "cc",
            "-I",
            "firmware/components/index/include",
            "-o",
            str(binary),
            "test/host/c/test_motion.c",
            "firmware/components/index/motion.c",
            "firmware/components/index/keyframe.c",
        ]
    )
    subprocess.check_call([str(binary)])


def test_motion_is_quiet_on_identical_frames_and_high_when_the_shape_moves():
    still = shape_jpeg(True)
    blank = shape_jpeg(False)
    _score, previous = motion_score(None, still)
    again, _previous = motion_score(previous, still)
    moved, _previous = motion_score(previous, blank)
    assert again < 0.01
    assert moved > 0.05


def test_keyframe_policy():
    start = T0
    assert keyframe_due(None, start, 0.0) is True
    assert keyframe_due(start, start + timedelta(seconds=1), 0.9) is False
    assert keyframe_due(start, start + timedelta(seconds=1.5), 0.9) is True
    assert keyframe_due(start, start + timedelta(seconds=1), 0.0) is False
    assert keyframe_due(start, start + timedelta(seconds=1.5), 0.0) is True
    assert keyframe_due(start, start, 0.0, mark=True) is True


def test_index_record_and_four_frame_watch_batch(tmp_path):
    card = Card(tmp_path)
    caption = FakeCaption()
    decider = FakeDecider()
    indexer = Indexer(card, caption, decider, [QUESTION])
    for offset in range(4):
        indexer.observe(T0 + timedelta(seconds=offset), shape_jpeg(offset % 2 == 0), mark=True)
    assert decider.sizes == [4]
    records = card.records()
    assert len(records) == 4
    for field in INDEX_FIELDS:
        assert field in records[0]
    assert records[0]["caption"].startswith("Pipette")
    assert records[0]["objects"] == ["pipette head", "tip rack"]
    assert records[0]["watch"]["tip pickup"] == 0.92
    assert records[0]["model"] == "openai/gpt-4.1-mini"
    assert records[3]["watch"]["tip pickup"] == 0.92
    assert records[0]["seg"] == "2026-10-29T19-07-00Z"
    events = card.events()
    assert len(events) >= 1
    tip = next(event for event in events if event["label"] == "tip pickup")
    assert tip["start"] == iso(T0)
    assert tip["end"] == iso(T0 + timedelta(seconds=3))


def test_events_split_when_a_hit_drops_below_threshold():
    def row(second, score):
        when = T0 + timedelta(seconds=second)
        return {"t": iso(when), "watch": {"tip pickup": score}, "actions": []}

    events = merge_events([row(0, 0.9), row(3, 0.9), row(6, 0.1), row(9, 0.95)], 0.5)
    tips = [event for event in events if event["label"] == "tip pickup"]
    assert len(tips) == 2
    assert tips[0]["start"] == iso(T0) and tips[0]["end"] == iso(T0 + timedelta(seconds=3))
    assert tips[1]["start"] == tips[1]["end"] == iso(T0 + timedelta(seconds=9))


def test_budget_pauses_indexing_and_keeps_recording(tmp_path):
    card = Card(tmp_path)
    caption = FakeCaption()
    decider = FakeDecider()
    budget = Budget(frames_per_day=1, tokens_per_day=100)
    indexer = Indexer(card, caption, decider, [QUESTION], budget=budget)
    indexer.observe(T0, shape_jpeg(), mark=True)
    indexer.flush()
    indexer.observe(T0 + timedelta(seconds=5), shape_jpeg(False), mark=True)
    indexer.flush()
    assert caption.calls == 1
    assert decider.calls == 1
    assert indexer.paused is True
    assert card.recording is True
    start = T0 - timedelta(seconds=1)
    end = T0 + timedelta(seconds=10)
    assert len(card.frames(start, end)) == 2
    camera = Camera(card, "tok", now=end)
    camera.budget_paused = indexer.paused
    camera.frames_used = budget.frames_used
    camera.frames_per_day = budget.frames_per_day
    _status, _kind, body = camera.dispatch("GET", "/api/v1/status", {"Authorization": "Bearer tok"})
    reported = json.loads(body)
    assert reported["recording"] is True
    assert reported["budget"]["paused"] is True


def test_dropped_caption_still_keeps_the_watch_score(tmp_path):
    card = Card(tmp_path)
    indexer = Indexer(card, FakeCaption(fail=True), FakeDecider(), [QUESTION])
    indexer.observe(T0, shape_jpeg(), mark=True)
    indexer.flush()
    record = card.records()[0]
    assert record["caption"] == ""
    assert record["watch"]["tip pickup"] == 0.92
    assert record["model"] == "typesafe/jev-1.14.0"
