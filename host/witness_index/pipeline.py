"""Index pipeline: motion, keyframes, captions, JEV watch scores, events, budget."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from host.witness_index.budget import Budget
from host.witness_index.events import merge_events
from host.witness_index.keyframes import keyframe_due
from host.witness_index.jsonutil import INDEX_FIELDS
from host.witness_index.motion import motion_score
from host.witness_models.errors import ModelCallError
from host.witness_models.jsonutil import CAPTION_PROMPT
from host.witness_models.questions import NoulQuestion
from host.witness_store.format import Card, iso

BATCH = 4


@dataclass
class _Pending:
    when: datetime
    jpeg: bytes
    motion: float
    seg: str
    frame: int


class Indexer:
    def __init__(
        self,
        card: Card,
        captioner,
        decider,
        questions: list[NoulQuestion],
        budget: Budget | None = None,
        profile: str = "",
        idle_s: float = 60,
        active_s: float = 3,
        motion_threshold: float = 0.05,
        watch_threshold: float = 0.5,
    ):
        self.card = card
        self.captioner = captioner
        self.decider = decider
        self.questions = questions
        self.budget = budget or Budget()
        self.profile = profile
        self.idle_s = idle_s
        self.active_s = active_s
        self.motion_threshold = motion_threshold
        self.watch_threshold = watch_threshold
        self.pending: list[_Pending] = []
        self.previous: list[int] | None = None
        self.last_keyframe: datetime | None = None
        self.paused = False

    def observe(self, when: datetime, jpeg: bytes, mark: bool = False) -> float:
        score, self.previous = motion_score(self.previous, jpeg)
        stored = self.card.add_frame(when, jpeg, motion=score, synced=self.card.clock_synced)
        if keyframe_due(
            self.last_keyframe,
            when,
            score,
            mark=mark,
            motion_threshold=self.motion_threshold,
            idle_s=self.idle_s,
            active_s=self.active_s,
        ):
            self.last_keyframe = when
            self.pending.append(
                _Pending(when=stored.t, jpeg=jpeg, motion=score, seg=stored.seg, frame=stored.index)
            )
            if len(self.pending) >= BATCH:
                self.flush()
        return score

    def flush(self) -> None:
        if not self.pending:
            return
        batch = self.pending[:BATCH]
        self.pending = self.pending[BATCH:]
        if self.budget.paused or not self.budget.allow(len(batch)):
            self.paused = True
            self.pending.clear()
            return
        prompt = CAPTION_PROMPT if not self.profile else self.profile + "\n" + CAPTION_PROMPT
        captions: list[dict | None] = []
        tokens = 0
        for item in batch:
            try:
                parsed, used = self.captioner.caption_json(item.jpeg, prompt)
                captions.append(parsed)
                tokens += used
            except ModelCallError:
                captions.append(None)
        scored = self.decider.ask_frames([(iso(item.when), item.jpeg) for item in batch], self.questions)
        tokens += scored.tokens
        self.budget.note(len(batch), tokens)
        if self.budget.paused:
            self.paused = True
        for item, caption, watch in zip(batch, captions, scored.scores):
            if caption is None and not watch:
                continue
            record = {
                "t": iso(item.when),
                "seg": item.seg,
                "frame": item.frame,
                "motion": round(item.motion, 3),
                "caption": "" if caption is None else caption["caption"],
                "objects": [] if caption is None else caption["objects"],
                "actions": [] if caption is None else caption["actions"],
                "robot_state": "" if caption is None else caption["robot_state"],
                "watch": watch,
                "model": scored.model if caption is None else self._caption_model(),
                "verified": False,
            }
            for field in INDEX_FIELDS:
                if field not in record:
                    raise KeyError(field)
            self.card.append_record(record)
        self.card.write_events(merge_events(self.card.records(), self.watch_threshold))
        if self.pending:
            self.flush()

    def _caption_model(self) -> str:
        model = getattr(self.captioner, "model", "")
        provider = getattr(self.captioner, "provider", "openai")
        if model and "/" in str(model):
            return str(model)
        if model:
            return f"{provider}/{model}"
        return provider
