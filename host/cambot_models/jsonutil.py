"""Shared JSON schema for a caption record."""

from __future__ import annotations

import json
import re

from host.cambot_models.errors import InvalidJson

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_caption_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = _FENCE.sub("", cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise InvalidJson("caption") from error
    if not isinstance(data, dict):
        raise InvalidJson("caption")
    caption = data.get("caption")
    if not isinstance(caption, str) or not caption.strip():
        raise InvalidJson("caption")
    for key in ("objects", "actions"):
        values = data.get(key)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise InvalidJson("caption")
    if not isinstance(data.get("robot_state"), str):
        raise InvalidJson("caption")
    return {
        "caption": caption.strip(),
        "objects": list(data["objects"]),
        "actions": list(data["actions"]),
        "robot_state": data["robot_state"],
    }


CAPTION_PROMPT = (
    "Return only a JSON object with keys caption (string), objects (array of strings), "
    "actions (array of strings), and robot_state (string). No markdown."
)
