"""Keyframe policy: every 1.5 s, and always on a robot mark."""

from __future__ import annotations

from datetime import datetime


def keyframe_due(
    last: datetime | None,
    now: datetime,
    motion: float,
    *,
    mark: bool = False,
    motion_threshold: float = 0.05,
    idle_s: float = 1.5,
    active_s: float = 1.5,
) -> bool:
    if mark or last is None:
        return True
    elapsed = (now - last).total_seconds()
    limit = active_s if motion > motion_threshold else idle_s
    return elapsed >= limit
