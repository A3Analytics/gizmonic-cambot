"""MCP tools over a fixture card. Tool calls do not open a model connection."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from host.witness_index.images import shape_jpeg
from host.witness_mcp.server import McpServer
from host.witness_store.api import Camera
from host.witness_store.format import Card, iso

TOKEN = "mcp-token"
T0 = datetime(2026, 10, 29, 19, 7, 41, tzinfo=timezone.utc)


def _record(when, caption, watch):
    return {
        "t": iso(when),
        "seg": "2026-10-29T19-07-00Z",
        "frame": 0,
        "motion": 0.41,
        "caption": caption,
        "objects": ["pipette head"],
        "actions": ["tip pickup"],
        "robot_state": "moving",
        "watch": watch,
        "model": "openai/gpt-4.1-mini",
        "verified": False,
    }


def _camera(tmp_path):
    card = Card(tmp_path)
    jpeg = shape_jpeg()
    for offset in range(4):
        card.add_frame(T0 + timedelta(seconds=offset), jpeg)
    card.append_record(_record(T0, "Pipette head lowers onto the tip rack", {"tip pickup": 0.92}))
    card.append_record(_record(T0 + timedelta(days=2), "empty bench", {"person in frame": 0.02}))
    card.live_jpeg = jpeg
    return Camera(card, TOKEN, now=T0 + timedelta(hours=1)), jpeg


def _rpc(server, method, params=None, rpc_id=1, headers=None):
    message = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        message["params"] = params
    headers = headers or {"Authorization": f"Bearer {TOKEN}"}
    return server.handle("POST", "/mcp", headers, json.dumps(message).encode())


def test_initialize_and_tools_list(tmp_path):
    server = McpServer(_camera(tmp_path)[0])
    status, _kind, body = _rpc(server, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
    assert status == 200
    result = json.loads(body)["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"]["name"] == "witness"
    status, _kind, body = _rpc(server, "tools/list")
    names = [tool["name"] for tool in json.loads(body)["result"]["tools"]]
    assert names == [
        "get_status",
        "list_recordings",
        "search_index",
        "inspect_window",
        "make_clip",
        "get_live_frame",
    ]


def test_search_index_hits_caption_and_watch_and_misses_outside_range(tmp_path):
    server = McpServer(_camera(tmp_path)[0])
    window = {"from": "2026-10-29T19:00:00Z", "to": "2026-10-29T20:00:00Z"}
    _status, _kind, body = _rpc(
        server,
        "tools/call",
        {"name": "search_index", "arguments": {**window, "query": "pipette"}},
    )
    found = json.loads(json.loads(body)["result"]["content"][0]["text"])
    assert len(found) == 1
    assert "tip rack" in found[0]["caption"]
    _status, _kind, body = _rpc(
        server,
        "tools/call",
        {"name": "search_index", "arguments": {**window, "query": "tip pickup"}},
    )
    found = json.loads(json.loads(body)["result"]["content"][0]["text"])
    assert len(found) == 1
    later = {"from": "2026-12-01T00:00:00Z", "to": "2026-12-01T23:00:00Z", "query": "pipette"}
    _status, _kind, body = _rpc(server, "tools/call", {"name": "search_index", "arguments": later})
    found = json.loads(json.loads(body)["result"]["content"][0]["text"])
    assert found == []


def test_inspect_window_returns_images_and_does_not_call_out(tmp_path, monkeypatch):
    camera, _jpeg = _camera(tmp_path)
    server = McpServer(camera)

    def boom(*_args, **_kwargs):
        raise AssertionError("model connection")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    _status, _kind, body = _rpc(
        server,
        "tools/call",
        {"name": "inspect_window", "arguments": {"t": iso(T0), "before_s": 1, "after_s": 5, "n": 2}},
    )
    content = json.loads(body)["result"]["content"]
    images = [block for block in content if block["type"] == "image"]
    assert len(images) == 2
    assert images[0]["mimeType"] == "image/jpeg"
    assert json.loads(body)["result"]["isError"] is False


def test_make_clip_url_is_served_by_the_buffer(tmp_path):
    camera, jpeg = _camera(tmp_path)
    server = McpServer(camera)
    _status, _kind, body = _rpc(
        server,
        "tools/call",
        {
            "name": "make_clip",
            "arguments": {"from": "2026-10-29T19:07:41Z", "to": "2026-10-29T19:07:44Z"},
        },
    )
    urls = json.loads(json.loads(body)["result"]["content"][0]["text"])
    status, kind, clip = camera.dispatch("GET", urls["clip_url"], {"Authorization": f"Bearer {TOKEN}"})
    assert status == 200 and kind.startswith("multipart")
    assert clip == jpeg * 4
    status, _kind, avi = camera.dispatch("GET", urls["avi_url"], {"Authorization": f"Bearer {TOKEN}"})
    assert status == 200 and avi.startswith(b"RIFF") and jpeg in avi


def test_mcp_requires_the_bearer_token(tmp_path):
    server = McpServer(_camera(tmp_path)[0])
    status, _kind, _body = server.handle(
        "POST",
        "/mcp",
        {},
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
    )
    assert status == 401
    status, _kind, body = _rpc(server, "tools/list")
    assert status == 200
    assert "search_index" in body.decode()
