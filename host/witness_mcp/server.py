"""MCP Streamable HTTP: one JSON-RPC POST at /mcp. Stateless, no model calls."""

from __future__ import annotations

import json

from host.witness_mcp import tools
from host.witness_store.api import Camera, _authorized

PROTOCOL = "2025-03-26"

TOOL_DEFS = [
    {
        "name": "get_status",
        "description": "Time, sync, storage, budget, and recording state.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_recordings",
        "description": "Minutes that contain recorded frames.",
        "inputSchema": {
            "type": "object",
            "properties": {"from": {"type": "string"}, "to": {"type": "string"}},
            "required": ["from", "to"],
        },
    },
    {
        "name": "search_index",
        "description": "Keyword and watch-list search over the index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from": {"type": "string"},
                "to": {"type": "string"},
                "query": {"type": "string"},
                "k": {"type": "integer"},
            },
            "required": ["from", "to", "query"],
        },
    },
    {
        "name": "inspect_window",
        "description": "Return frames around a time. Does not call a model.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "t": {"type": "string"},
                "before_s": {"type": "number"},
                "after_s": {"type": "number"},
                "n": {"type": "integer"},
            },
            "required": ["t"],
        },
    },
    {
        "name": "make_clip",
        "description": "URLs for a recorded window.",
        "inputSchema": {
            "type": "object",
            "properties": {"from": {"type": "string"}, "to": {"type": "string"}},
            "required": ["from", "to"],
        },
    },
    {
        "name": "get_live_frame",
        "description": "The current view, as an image.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class McpServer:
    def __init__(self, camera: Camera):
        self.camera = camera

    def handle(self, method: str, path: str, headers: dict[str, str], body: bytes = b"") -> tuple[int, str, bytes]:
        if path.split("?", 1)[0] != "/mcp" or method != "POST":
            return 404, "text/plain", b"not found\n"
        if not _authorized(headers.get("Authorization"), self.camera.token):
            return 401, "text/plain", b"unauthorized\n"
        try:
            message = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return 400, "application/json", _rpc_error(None, -32700, "parse error")
        if not isinstance(message, dict):
            return 400, "application/json", _rpc_error(None, -32600, "invalid request")
        rpc_method = message.get("method")
        rpc_id = message.get("id")
        if isinstance(rpc_method, str) and rpc_method.startswith("notifications/"):
            return 202, "application/json", b""
        if rpc_method == "initialize":
            result = {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "witness", "version": "0.1.0"},
            }
            return 200, "application/json", _rpc_result(rpc_id, result)
        if rpc_method == "tools/list":
            return 200, "application/json", _rpc_result(rpc_id, {"tools": TOOL_DEFS})
        if rpc_method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                content, is_error = _call(self.camera, name, arguments)
            except KeyError:
                return 200, "application/json", _rpc_error(rpc_id, -32602, "invalid params")
            except Exception:
                content = [{"type": "text", "text": "tool failed"}]
                is_error = True
            result = {"content": content, "isError": is_error}
            return 200, "application/json", _rpc_result(rpc_id, result)
        return 200, "application/json", _rpc_error(rpc_id, -32601, "method not found")


def _call(camera: Camera, name: str, arguments: dict) -> tuple[list[dict], bool]:
    if name == "get_status":
        return [{"type": "text", "text": json.dumps(tools.get_status(camera))}], False
    if name == "list_recordings":
        rows = tools.list_recordings(camera, arguments["from"], arguments["to"])
        return [{"type": "text", "text": json.dumps(rows)}], False
    if name == "search_index":
        rows = tools.search_index(
            camera,
            arguments["from"],
            arguments["to"],
            arguments.get("query", ""),
            int(arguments.get("k", 20)),
        )
        return [{"type": "text", "text": json.dumps(rows)}], False
    if name == "inspect_window":
        content = tools.inspect_window(
            camera,
            arguments["t"],
            float(arguments.get("before_s", 2)),
            float(arguments.get("after_s", 2)),
            int(arguments.get("n", 4)),
        )
        return content, False
    if name == "make_clip":
        urls = tools.make_clip(camera, arguments["from"], arguments["to"])
        return [{"type": "text", "text": json.dumps(urls)}], False
    if name == "get_live_frame":
        return tools.get_live_frame(camera), False
    return [{"type": "text", "text": "unknown tool"}], True


def _rpc_result(rpc_id, result: dict) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": result}).encode()


def _rpc_error(rpc_id, code: int, message: str) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}).encode()
