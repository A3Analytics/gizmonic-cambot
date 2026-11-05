"""Local copy of a camera's keyframe index.

Questions read this file instead of asking the camera to scan its card. The
camera still records the index; this is a copy on the registrar computer.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

EPISODE_GAP_S = 5.0
RECENT_KEYFRAMES = 40


def _normalize(line: str | bytes) -> tuple[str, str] | None:
    if isinstance(line, bytes):
        line = line.decode()
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict):
        return None
    stamp = row.get("t")
    if not isinstance(stamp, str) or not stamp:
        return None
    return stamp, json.dumps(row, separators=(",", ":"), sort_keys=True)


def parse_stamp(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class IndexStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=4000")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                camera_id TEXT NOT NULL,
                t TEXT NOT NULL,
                line TEXT NOT NULL,
                PRIMARY KEY (camera_id, t)
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS backfill (
                camera_id TEXT PRIMARY KEY,
                cursor TEXT NOT NULL,
                done INTEGER NOT NULL
            )
            """
        )
        self._db.commit()

    def insert_lines(self, camera_id: str, lines: list[str | bytes]) -> int:
        added = 0
        with self._lock:
            for line in lines:
                normalized = _normalize(line)
                if normalized is None:
                    continue
                stamp, stored = normalized
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO records(camera_id, t, line) VALUES (?, ?, ?)",
                    (camera_id, stamp, stored),
                )
                added += cur.rowcount
            self._db.commit()
        return added

    def count(self, camera_id: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM records WHERE camera_id = ?",
                (camera_id,),
            ).fetchone()
        return int(row[0] if row else 0)

    def lines(self, camera_id: str) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT line FROM records WHERE camera_id = ? ORDER BY t",
                (camera_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def cursor(self, camera_id: str) -> tuple[str, bool]:
        with self._lock:
            row = self._db.execute(
                "SELECT cursor, done FROM backfill WHERE camera_id = ?",
                (camera_id,),
            ).fetchone()
        if row is None:
            return "", False
        return str(row[0]), bool(row[1])

    def set_cursor(self, camera_id: str, cursor: str, done: bool) -> None:
        with self._lock:
            self._db.execute(
                """
                INSERT INTO backfill(camera_id, cursor, done) VALUES (?, ?, ?)
                ON CONFLICT(camera_id) DO UPDATE SET cursor = excluded.cursor, done = excluded.done
                """,
                (camera_id, cursor, 1 if done else 0),
            )
            self._db.commit()

    def backfill_done(self, camera_id: str) -> bool:
        return self.cursor(camera_id)[1]


def summarize_lines(lines: list[str]) -> dict:
    """Fold keyframes into per-label episodes. The model never sees every line."""
    rows = []
    for line in lines:
        normalized = _normalize(line)
        if normalized is None:
            continue
        row = json.loads(normalized[1])
        when = parse_stamp(normalized[0])
        if when is None:
            continue
        watch = row.get("watch") if isinstance(row.get("watch"), dict) else {}
        yes = sorted(
            label
            for label, score in watch.items()
            if isinstance(label, str) and isinstance(score, (int, float)) and float(score) >= 0.5
        )
        rows.append(
            {
                "t": when,
                "yes": yes,
                "caption": row.get("caption") if isinstance(row.get("caption"), str) else "",
                "watch": watch,
            }
        )
    rows.sort(key=lambda item: item["t"])
    by_label: dict[str, list[datetime]] = {}
    for row in rows:
        for label in row["yes"]:
            by_label.setdefault(label, []).append(row["t"])
    labels = {}
    for label, times in sorted(by_label.items()):
        spans = []
        start = prev = times[0]
        frames = 1
        for when in times[1:]:
            if (when - prev).total_seconds() <= EPISODE_GAP_S:
                prev = when
                frames += 1
                continue
            spans.append({"start": _iso(start), "end": _iso(prev), "frames": frames})
            start = prev = when
            frames = 1
        spans.append({"start": _iso(start), "end": _iso(prev), "frames": frames})
        by_hour: dict[str, int] = {}
        for when in times:
            hour = when.strftime("%Y-%m-%dT%H:00:00.000Z")
            by_hour[hour] = by_hour.get(hour, 0) + 1
        labels[label] = {
            "yes_frames": len(times),
            "episodes": len(spans),
            "first": _iso(times[0]),
            "last": _iso(times[-1]),
            "latest_episodes": spans[-8:],
            "by_hour": [{"hour": hour, "yes_frames": count} for hour, count in sorted(by_hour.items())],
        }
    recent = [
        {"t": _iso(row["t"]), "yes": row["yes"], "caption": row["caption"]}
        for row in rows[-RECENT_KEYFRAMES:]
    ]
    return {
        "keyframes": len(rows),
        "from": _iso(rows[0]["t"]) if rows else "",
        "to": _iso(rows[-1]["t"]) if rows else "",
        "labels": labels,
        "recent": recent,
    }
