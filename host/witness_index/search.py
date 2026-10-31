"""Keyword and watch-list search over index records."""

from __future__ import annotations

from datetime import datetime

from host.witness_store.format import parse_iso


def search_records(
    records: list[dict],
    start: datetime,
    end: datetime,
    query: str,
    k: int = 20,
    watch_threshold: float = 0.5,
) -> list[dict]:
    needle = query.strip().lower()
    found = []
    for record in records:
        when = parse_iso(record["t"])
        if when < start or when > end:
            continue
        if needle and not _matches(record, needle, watch_threshold):
            continue
        found.append(record)
    found.sort(key=lambda record: record["t"])
    if k >= 0:
        return found[:k]
    return found


def _matches(record: dict, needle: str, watch_threshold: float) -> bool:
    caption = str(record.get("caption", "")).lower()
    if needle in caption:
        return True
    for key in ("objects", "actions"):
        for item in record.get(key) or []:
            if needle in str(item).lower():
                return True
    for label, score in (record.get("watch") or {}).items():
        if needle in label.lower() and score >= watch_threshold:
            return True
    return False
