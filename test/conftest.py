"""Load the OpenAI key from the gitignored file when the environment does not have it.

The value is never printed.
"""

from __future__ import annotations

import os
from pathlib import Path


def pytest_sessionstart(session) -> None:
    if os.environ.get("WITNESS_MODEL_KEY"):
        return
    path = Path(__file__).resolve().parents[1] / "openai_api_key.txt"
    if not path.is_file():
        return
    value = path.read_text(encoding="utf-8").strip()
    if value:
        os.environ["WITNESS_MODEL_KEY"] = value
