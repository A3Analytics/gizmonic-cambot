"""Turn a description into entailed and contradicted JEV questions.

The description must contain the headings `Entailed:` and `Contradicted:`,
each followed by `- ` bullets. The live check asks a larger model to write
that shape. Unit tests use a canned description so they stay deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DESCRIBE_PROMPT = (
    "Describe the image in two or three sentences. Then list yes/no questions "
    "under these exact headings, with one `- ` bullet per question:\n"
    "Entailed:\n"
    "Contradicted:\n"
    "Entailed questions must be true of this image. Contradicted questions must be false. "
    "Write at least two bullets under each heading."
)

_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class NoulQuestion:
    name: str
    instructions: str
    label: str
    expect: str  # "yes" for entailed, "no" for contradicted


def questions_from_description(text: str) -> list[NoulQuestion]:
    entailed = _bullets(text, "Entailed:")
    contradicted = _bullets(text, "Contradicted:")
    if not entailed or not contradicted:
        raise ValueError("description needs Entailed and Contradicted bullets")
    questions: list[NoulQuestion] = []
    questions.extend(_make(entailed, "yes"))
    questions.extend(_make(contradicted, "no"))
    return questions


def _bullets(text: str, heading: str) -> list[str]:
    start = text.find(heading)
    if start < 0:
        return []
    rest = text[start + len(heading) :]
    for other in ("Entailed:", "Contradicted:"):
        if other == heading:
            continue
        at = rest.find(other)
        if at >= 0:
            rest = rest[:at]
    bullets = []
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            bullet = stripped[2:].strip()
            if bullet:
                bullets.append(bullet)
    return bullets


def _make(bullets: list[str], expect: str) -> list[NoulQuestion]:
    made = []
    for index, bullet in enumerate(bullets):
        slug = _SLUG.sub("_", bullet.lower()).strip("_")[:40] or "q"
        name = f"{expect}_{slug}_{index}"
        made.append(
            NoulQuestion(
                name=name,
                instructions=f"{bullet} Answer about the image `photo`.",
                label=bullet,
                expect=expect,
            )
        )
    return made
