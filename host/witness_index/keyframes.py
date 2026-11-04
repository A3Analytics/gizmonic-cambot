"""Keyframe policy: idle every 5 s, active every 3 s, always on a robot mark."""

from __future__ import annotations

from datetime import datetime


def keyframe_due(
    last: datetime | None,
    now: datetime,
    motion: float,
    *,
    mark: bool = False,
    motion_threshold: float = 0.05,
    idle_s: float = 5,
    active_s: float = 3,
) -> bool:
    if mark or last is None:
        return True
    elapsed = (now - last).total_seconds()
    if motion > motion_threshold:
        return elapsed >= active_s
    return elapsed >= idle_s
