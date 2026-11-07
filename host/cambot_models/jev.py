"""TypeSafe JEV System One adapter.

JEV is a decision model. A noul question returns the probability of yes
from 0 to 1, which is the watch-list score stored on an index record.
Vision requires model jev-1.14.0 and the TypeSafe-Danger-Zone-Images header.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from host.cambot_models.errors import InvalidJson, ModelCallError
from host.cambot_models.http import Transport, UrllibTransport, install_redactor, post_json
from host.cambot_models.questions import NoulQuestion

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.14.0"
JEV_IMAGES_HEADER = "TypeSafe-Danger-Zone-Images"
MAX_BATCH = 4


@dataclass
class ScoredFrames:
    scores: list[dict[str, float]]
    tokens: int
    model: str = f"typesafe/{JEV_MODEL}"


class JevAdapter:
    provider = "typesafe"

    def __init__(
        self,
        api_key: str,
        model: str = JEV_MODEL,
        base_url: str = JEV_URL,
        transport: Transport | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.transport = transport or UrllibTransport()
        install_redactor(api_key)

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            JEV_IMAGES_HEADER: "true",
        }

    def build_body(self, image_jpeg: bytes, questions: list[NoulQuestion]) -> dict:
        encoded = base64.standard_b64encode(image_jpeg).decode("ascii")
        return {
            "model": self.model,
            "state": {"photo": "img1"},
            "assets": {"img1": {"type": "image", "format": "jpg", "data_base64": encoded}},
            "questions": {
                question.name: {"type": "noul", "instructions": question.instructions}
                for question in questions
            },
        }

    def build_batch(self, frames: list[tuple[str, bytes]], questions: list[NoulQuestion]) -> dict:
        if not frames or len(frames) > MAX_BATCH:
            raise ValueError(f"JEV batch must be 1 to {MAX_BATCH} frames")
        assets = {}
        state = []
        named: dict[str, dict] = {}
        for index, (stamp, image_jpeg) in enumerate(frames):
            key = f"img{index}"
            assets[key] = {
                "type": "image",
                "format": "jpg",
                "data_base64": base64.standard_b64encode(image_jpeg).decode("ascii"),
            }
            state.append({"t": stamp, "photo": key})
            for question in questions:
                instructions = question.instructions.replace("`photo`", f"`{key}`")
                named[f"f{index}__{question.name}"] = {"type": "noul", "instructions": instructions}
        return {"model": self.model, "state": state, "assets": assets, "questions": named}

    def ask(self, image_jpeg: bytes, questions: list[NoulQuestion]) -> dict[str, float]:
        scored = self.ask_frames([("", image_jpeg)], questions)
        return scored.scores[0]

    def ask_frames(self, frames: list[tuple[str, bytes]], questions: list[NoulQuestion]) -> ScoredFrames:
        if len(frames) == 1 and frames[0][0] == "":
            body = self.build_body(frames[0][1], questions)
            payload, tokens = self._post(body)
            answers = _noul_map(payload)
            return ScoredFrames(scores=[_by_label(answers, questions)], tokens=tokens)
        body = self.build_batch(frames, questions)
        payload, tokens = self._post(body)
        answers = _noul_map(payload)
        scores = []
        for index, _frame in enumerate(frames):
            one = {}
            for question in questions:
                key = f"f{index}__{question.name}"
                if key not in answers:
                    raise InvalidJson("jev")
                one[question.label] = answers[key]
            scores.append(one)
        return ScoredFrames(scores=scores, tokens=tokens)

    def _post(self, body: dict) -> tuple[dict, int]:
        last_tokens = 0
        for attempt in (1, 2):
            try:
                payload, last_tokens = post_json(self.transport, self.base_url, self.headers(), body)
                _noul_map(payload)
                return payload, last_tokens
            except InvalidJson:
                if attempt == 2:
                    raise ModelCallError("dropped") from None
            except ModelCallError:
                raise
        raise ModelCallError("dropped")


def _noul_map(payload: dict) -> dict[str, float]:
    answers = payload.get("answers")
    if not isinstance(answers, dict) or not answers:
        raise InvalidJson("jev")
    scores: dict[str, float] = {}
    for name, answer in answers.items():
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            raise InvalidJson("jev")
        value = answer.get("noul")
        if not isinstance(value, (int, float)):
            raise InvalidJson("jev")
        scores[name] = float(value)
    return scores


def _by_label(answers: dict[str, float], questions: list[NoulQuestion]) -> dict[str, float]:
    scored = {}
    for question in questions:
        if question.name not in answers:
            raise InvalidJson("jev")
        scored[question.label] = answers[question.name]
    return scored
