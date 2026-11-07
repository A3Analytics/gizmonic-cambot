"""Merge consecutive watch-list hits into events with a start and an end."""

from __future__ import annotations


def merge_events(records: list[dict], threshold: float) -> list[dict]:
    ordered = sorted(records, key=lambda record: record["t"])
    labels: list[str] = []
    for record in ordered:
        for label, score in record.get("watch", {}).items():
            if score >= threshold and label not in labels:
                labels.append(label)
        for action in record.get("actions") or []:
            if action not in labels:
                labels.append(action)
    events = []
    next_id = 1
    for label in labels:
        run: dict | None = None
        for record in ordered:
            watch = record.get("watch") or {}
            hit = watch.get(label, 0) >= threshold or label in (record.get("actions") or [])
            score = watch.get(label, 1.0 if label in (record.get("actions") or []) else 0.0)
            if hit:
                if run is None:
                    run = {
                        "id": str(next_id),
                        "label": label,
                        "start": record["t"],
                        "end": record["t"],
                        "score": score,
                    }
                    next_id += 1
                else:
                    run["end"] = record["t"]
                    run["score"] = max(run["score"], score)
            elif run is not None:
                events.append(run)
                run = None
        if run is not None:
            events.append(run)
    return events
