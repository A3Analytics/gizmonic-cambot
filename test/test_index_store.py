"""The question index lives in SQLite. A question must not scan the camera."""

import json
from pathlib import Path

from fixr.registrar.index_store import IndexStore, summarize_lines
from fixr.registrar import serve


def _line(stamp: str, score: float, label: str = "pipette tip") -> str:
    return json.dumps({"t": stamp, "caption": "", "watch": {label: score}})


def test_insert_keeps_one_row_per_timestamp(tmp_path: Path):
    store = IndexStore(tmp_path / "index.sqlite")
    line = _line("2026-11-05T18:00:00.000Z", 0.9)
    assert store.insert_lines("cam", [line, line]) == 1
    assert store.count("cam") == 1
    assert store.insert_lines("cam", [_line("2026-11-05T18:00:02.000Z", 0.1)]) == 1
    assert store.count("cam") == 2


def test_summary_splits_episodes_on_a_five_second_gap():
    lines = [
        _line("2026-11-05T18:00:00.000Z", 0.9),
        _line("2026-11-05T18:00:02.000Z", 0.8),
        _line("2026-11-05T18:00:04.000Z", 0.2),
        _line("2026-11-05T18:00:20.000Z", 0.95),
    ]
    summary = summarize_lines(lines)
    label = summary["labels"]["pipette tip"]
    assert summary["keyframes"] == 4
    assert label["yes_frames"] == 3
    assert label["episodes"] == 2
    assert label["latest_episodes"][0]["frames"] == 2
    assert label["latest_episodes"][1]["frames"] == 1
    assert summary["recent"][-1]["yes"] == ["pipette tip"]


def test_query_uses_sqlite_after_backfill_and_does_not_search_the_camera(tmp_path, monkeypatch):
    store = IndexStore(tmp_path / "index.sqlite")
    store.insert_lines("cam", [_line("2026-11-05T18:00:00.000Z", 0.9)])
    store.set_cursor("cam", "", True)
    calls = []
    seen = {}

    def fake_mcp(ip, name, arguments):
        calls.append(name)
        return "[]"

    def fake_answer(question, summary, recordings):
        seen["complete"] = summary["complete"]
        return "from history"

    monkeypatch.setattr(serve, "mcp_call", fake_mcp)
    monkeypatch.setattr(serve, "answer_from_history", fake_answer)
    assert serve.run_query("10.0.0.8", "was the tip seen?", "cam", store) == "from history"
    assert calls == ["list_recordings"]
    assert seen["complete"] is True


def test_query_uses_a_partial_copy_without_searching_the_camera(tmp_path, monkeypatch):
    store = IndexStore(tmp_path / "index.sqlite")
    store.insert_lines("cam", [_line("2026-11-05T18:00:00.000Z", 0.9)])
    calls = []
    seen = {}

    def fake_mcp(ip, name, arguments):
        calls.append(name)
        return "[]"

    def fake_answer(question, summary, recordings):
        seen["complete"] = summary["complete"]
        return "from history"

    monkeypatch.setattr(serve, "mcp_call", fake_mcp)
    monkeypatch.setattr(serve, "answer_from_history", fake_answer)
    assert serve.run_query("10.0.0.8", "was the tip seen?", "cam", store) == "from history"
    assert calls == ["list_recordings"]
    assert seen["complete"] is False


def test_backfill_step_stores_a_page_and_its_cursor(tmp_path, monkeypatch):
    store = IndexStore(tmp_path / "index.sqlite")
    page = {
        "lines": [_line("2026-11-04T12:00:00.000Z", 0.7)],
        "next": "2026-11-04/12.jsonl:80",
        "done": False,
    }

    monkeypatch.setattr(serve, "mcp_call", lambda *args, **kwargs: json.dumps(page))
    added, done, nlines = serve.backfill_step("10.0.0.8", "cam", store)
    assert (added, done, nlines) == (1, False, 1)
    assert store.cursor("cam") == ("2026-11-04/12.jsonl:80", False)
    assert store.count("cam") == 1


def test_finished_page_does_not_restart_at_the_first_file(tmp_path, monkeypatch):
    store = IndexStore(tmp_path / "index.sqlite")
    store.set_cursor("cam", "2026-11-05/18.jsonl:400", False)
    page = {"lines": [], "next": "", "done": True}
    monkeypatch.setattr(serve, "mcp_call", lambda *args, **kwargs: json.dumps(page))
    added, done, nlines = serve.backfill_step("10.0.0.8", "cam", store)
    assert (added, done, nlines) == (0, True, 0)
    assert store.cursor("cam") == ("2026-11-05/18.jsonl:400", True)
