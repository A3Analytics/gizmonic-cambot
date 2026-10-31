"""On-card layout for the rolling JPEG buffer.

A segment is one UTC minute of concatenated JPEG frames plus a packed index.
Each index record is little-endian uint64 timestamp (ms), uint32 offset,
uint32 length, uint16 motion in thousandths (0.41 -> 410). The record is
18 bytes, with no padding.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

IDX = struct.Struct("<QIIH")
IDX_SIZE = IDX.size  # 18

WITNESS_DIR = "witness"


def utc(when: datetime) -> datetime:
    if when.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return when.astimezone(timezone.utc)


def segment_start(when: datetime) -> datetime:
    when = utc(when)
    return when.replace(second=0, microsecond=0)


def segment_id(when: datetime) -> str:
    return segment_start(when).strftime("%Y-%m-%dT%H-%M-%SZ")


def iso(when: datetime) -> str:
    return utc(when).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def motion_to_milli(motion: float) -> int:
    return max(0, min(1000, int(round(motion * 1000))))


def milli_to_motion(milli: int) -> float:
    return milli / 1000.0


@dataclass
class Frame:
    t: datetime
    jpeg: bytes
    motion: float
    seg: str
    index: int
    offset: int
    synced: bool


@dataclass
class Minute:
    start: datetime
    frames: int


class Card:
    """A witness card directory. Capacity 0 disables the software quota."""

    def __init__(
        self,
        root: Path,
        camera_id: str = "witness-test",
        capacity: int = 0,
        reserve_pct: float = 10,
        index_keep_days: int = 30,
        clock_synced: bool = True,
    ):
        self.root = Path(root)
        self.camera_id = camera_id
        self.capacity = capacity
        self.reserve_pct = reserve_pct
        self.index_keep_days = index_keep_days
        self.clock_synced = clock_synced
        self.recording = True
        self.live_jpeg: bytes | None = None
        self.base.mkdir(parents=True, exist_ok=True)
        card_path = self.base / "card.json"
        if not card_path.exists():
            card_path.write_text(
                json.dumps({"id": camera_id, "format": 1}) + "\n",
                encoding="utf-8",
            )

    @property
    def base(self) -> Path:
        return self.root / WITNESS_DIR

    def add_frame(self, when: datetime, jpeg: bytes, motion: float = 0.0, synced: bool = True) -> Frame:
        if not jpeg:
            raise ValueError("empty frame")
        if synced:
            start = segment_start(when)
            seg = segment_id(when)
            day, hour = start.strftime("%Y-%m-%d"), start.strftime("%H")
            directory = self.base / "rec" / day / hour
            t_ms = int(utc(when).timestamp() * 1000)
        else:
            seg = "unsynced"
            directory = self.base / "rec" / "unsynced"
            t_ms = 0
            start = utc(when)
        directory.mkdir(parents=True, exist_ok=True)
        mjpg = directory / f"{seg}.mjpg"
        idx = directory / f"{seg}.idx"
        offset = mjpg.stat().st_size if mjpg.exists() else 0
        frame_index = (idx.stat().st_size // IDX_SIZE) if idx.exists() else 0
        with mjpg.open("ab") as handle:
            handle.write(jpeg)
        with idx.open("ab") as handle:
            handle.write(IDX.pack(t_ms, offset, len(jpeg), motion_to_milli(motion)))
        self.recording = True
        return Frame(
            t=utc(when) if synced else datetime.fromtimestamp(0, timezone.utc),
            jpeg=jpeg,
            motion=motion,
            seg=seg if synced else segment_id(when),
            index=frame_index,
            offset=offset,
            synced=synced,
        )

    def repair_unsynced(self, start: datetime, step: timedelta = timedelta(seconds=1)) -> int:
        directory = self.base / "rec" / "unsynced"
        mjpg_path = directory / "unsynced.mjpg"
        idx_path = directory / "unsynced.idx"
        if not mjpg_path.exists() or not idx_path.exists():
            return 0
        blob = mjpg_path.read_bytes()
        records = _read_idx(idx_path)
        when = utc(start)
        count = 0
        for record in records:
            frame = blob[record[1] : record[1] + record[2]]
            self.add_frame(when, frame, motion=milli_to_motion(record[3]), synced=True)
            when += step
            count += 1
        mjpg_path.unlink()
        idx_path.unlink()
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()
        return count

    def frames(self, start: datetime, end: datetime) -> list[Frame]:
        start_u, end_u = utc(start), utc(end)
        if end_u < start_u:
            return []
        found = [frame for frame in self._iter_frames() if frame.synced and start_u <= frame.t <= end_u]
        found.sort(key=lambda frame: frame.t)
        return found

    def nearest(self, when: datetime) -> Frame | None:
        target = utc(when)
        best: Frame | None = None
        best_gap = None
        for frame in self._iter_frames():
            if not frame.synced:
                continue
            gap = abs((frame.t - target).total_seconds())
            if best_gap is None or gap < best_gap:
                best = frame
                best_gap = gap
        return best

    def recordings(self, start: datetime, end: datetime) -> list[Minute]:
        counts: dict[datetime, int] = {}
        for frame in self.frames(start, end):
            minute = segment_start(frame.t)
            counts[minute] = counts.get(minute, 0) + 1
        return [Minute(start=key, frames=counts[key]) for key in sorted(counts)]

    def sample(self, start: datetime, end: datetime, every_s: float) -> list[Frame]:
        chosen: list[Frame] = []
        next_at: datetime | None = None
        for frame in self.frames(start, end):
            if next_at is None or frame.t >= next_at:
                chosen.append(frame)
                next_at = frame.t + timedelta(seconds=every_s)
        return chosen

    def clip(self, start: datetime, end: datetime) -> bytes:
        return b"".join(frame.jpeg for frame in self.frames(start, end))

    def append_record(self, record: dict) -> None:
        when = parse_iso(record["t"])
        day, hour = when.strftime("%Y-%m-%d"), when.strftime("%H")
        directory = self.base / "index" / day
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{hour}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    def records(self) -> list[dict]:
        found = []
        index_root = self.base / "index"
        if not index_root.exists():
            return found
        for path in sorted(index_root.glob("*/*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    found.append(json.loads(line))
        return found

    def write_events(self, events: list[dict]) -> None:
        by_day: dict[str, list[dict]] = {}
        for event in events:
            day = parse_iso(event["start"]).strftime("%Y-%m-%d")
            by_day.setdefault(day, []).append(event)
        events_root = self.base / "events"
        events_root.mkdir(parents=True, exist_ok=True)
        for day, rows in by_day.items():
            path = events_root / f"{day}.jsonl"
            path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")

    def events(self, start: datetime | None = None, end: datetime | None = None, since_id: str | None = None) -> list[dict]:
        events_root = self.base / "events"
        if not events_root.exists():
            return []
        rows = []
        for path in sorted(events_root.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        if since_id is not None:
            rows = [row for row in rows if str(row.get("id", "")) > since_id]
        if start is not None and end is not None:
            start_u, end_u = utc(start), utc(end)
            kept = []
            for row in rows:
                ev_start = parse_iso(row["start"])
                ev_end = parse_iso(row["end"])
                if ev_end >= start_u and ev_start <= end_u:
                    kept.append(row)
            rows = kept
        return rows

    def used_bytes(self) -> int:
        total = 0
        for path in self.base.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total

    def maintain(self, now: datetime) -> None:
        """Evict oldest video hours under the reserve, then drop expired index files."""
        if self.capacity > 0:
            floor = self.capacity * (self.reserve_pct / 100.0)
            while self.capacity - self.used_bytes() < floor:
                oldest = self._oldest_video_hour()
                if oldest is None:
                    break
                self._delete_tree(oldest)
        self._prune_index(utc(now))

    def _oldest_video_hour(self) -> Path | None:
        rec = self.base / "rec"
        if not rec.exists():
            return None
        hours = []
        for day in sorted(path for path in rec.iterdir() if path.is_dir() and path.name != "unsynced"):
            for hour in sorted(path for path in day.iterdir() if path.is_dir()):
                hours.append(hour)
        return hours[0] if hours else None

    def _prune_index(self, now: datetime) -> None:
        index_root = self.base / "index"
        if not index_root.exists():
            return
        cutoff = now - timedelta(days=self.index_keep_days)
        for day in list(index_root.iterdir()):
            if not day.is_dir():
                continue
            for path in list(day.glob("*.jsonl")):
                hour = path.stem
                video = self.base / "rec" / day.name / hour
                try:
                    hour_end = datetime.strptime(f"{day.name}T{hour}", "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
                    hour_end += timedelta(hours=1)
                except ValueError:
                    continue
                if not video.exists() and hour_end < cutoff:
                    path.unlink()
            if day.exists() and not any(day.iterdir()):
                day.rmdir()

    def _iter_frames(self) -> list[Frame]:
        rec = self.base / "rec"
        if not rec.exists():
            return []
        frames: list[Frame] = []
        for idx_path in sorted(rec.glob("**/*.idx")):
            synced = "unsynced" not in idx_path.parts
            mjpg_path = idx_path.with_suffix(".mjpg")
            if not mjpg_path.exists():
                continue
            blob = mjpg_path.read_bytes()
            seg = idx_path.stem
            for index, (t_ms, offset, length, milli) in enumerate(_read_idx(idx_path)):
                if t_ms == 0 or not synced:
                    when = datetime.fromtimestamp(0, timezone.utc)
                    frame_synced = False
                else:
                    when = datetime.fromtimestamp(t_ms / 1000, timezone.utc)
                    frame_synced = True
                frames.append(
                    Frame(
                        t=when,
                        jpeg=blob[offset : offset + length],
                        motion=milli_to_motion(milli),
                        seg=seg,
                        index=index,
                        offset=offset,
                        synced=frame_synced,
                    )
                )
        return frames

    def _delete_tree(self, directory: Path) -> None:
        for path in directory.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(directory.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        if directory.exists():
            directory.rmdir()


def _read_idx(path: Path) -> list[tuple[int, int, int, int]]:
    data = path.read_bytes()
    records = []
    for offset in range(0, len(data) - (len(data) % IDX_SIZE), IDX_SIZE):
        records.append(IDX.unpack_from(data, offset))
    return records
