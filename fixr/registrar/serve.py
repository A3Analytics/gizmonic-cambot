#!/usr/bin/env python3
"""LAN registrar for a Witness camera.

The camera POSTs /api/register when it joins the network. This page lists
each camera and refreshes its live frame. A query box on this computer reads
the local index copy in fixr/run/index.sqlite, then asks gpt-4.1-mini to answer
from that history. The camera password and model key are never printed or stored.

  /opt/homebrew/bin/python3.13 fixr/registrar/serve.py
  /opt/homebrew/bin/python3.13 fixr/registrar/serve.py --check
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from PIL import Image

if __package__:
    from fixr.registrar.index_store import IndexStore, summarize_lines
else:
    from index_store import IndexStore, summarize_lines

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
MODEL_NAME = "gpt-4.1-mini"
INDEX_LIMIT = 400
RUN = ROOT / "run"
REGISTRY = RUN / "cameras.json"
INDEX_DB = RUN / "index.sqlite"
INDEX_STORE: IndexStore | None = None
PROFILE = ROOT / "profiles" / "waterbear.json"
HOST_SCHEMA = ROOT / "schema" / "host-profile.schema.json"
FRAME_PATH = "/api/v1/frame.jpg"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
EXAMPLE_RE = re.compile(
    r"^/api/cameras/(?P<camera>[A-Za-z0-9][A-Za-z0-9_-]{0,31})/nouls/"
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9_-]{0,31})/examples/"
    r"(?P<example>[A-Za-z0-9][A-Za-z0-9_-]{0,31})$"
)
EXAMPLE_MAX_BYTES = 80 * 1024
UPLOAD_MAX_BYTES = 8 * 1024 * 1024
PORT = 8765
STALE_AFTER_S = 120
DEV_IDLE_S = 5 * 60
DEV_MODE_PATH = RUN / "dev_mode.json"
HIGH_PERF_PATH = RUN / "high_perf.json"
POLL_MS_DEFAULT = 2000
POLL_MS_HIGH_PERF = 500
SWITCH_SYNC_MS = 30_000


def camera_is_stale(cam: dict, now: datetime | None = None) -> bool:
    """True when the camera has not registered recently enough to trust."""
    if not isinstance(cam, dict):
        return True
    stamp = cam.get("last_seen")
    if not isinstance(stamp, str) or not stamp:
        return True
    try:
        seen = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return True
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current - seen).total_seconds() > STALE_AFTER_S


def load_dev_mode(path: Path = DEV_MODE_PATH) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("enabled")) if isinstance(payload, dict) else False


def save_dev_mode(enabled: bool, path: Path = DEV_MODE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabled": bool(enabled)}, indent=2) + "\n")


def load_high_perf(path: Path = HIGH_PERF_PATH) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("enabled")) if isinstance(payload, dict) else False


def save_high_perf(enabled: bool, path: Path = HIGH_PERF_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabled": bool(enabled)}, indent=2) + "\n")


def set_cameras_perf(registry: "Registry", enabled: bool, poster=None) -> int:
    """Best-effort: push high-performance mode to every registered camera."""
    send = poster or post_authed
    updated = 0
    for camera in registry.list():
        ip = camera.get("ip")
        if not isinstance(ip, str) or not ip:
            continue
        try:
            send(ip, "/api/v1/perf", {"enabled": bool(enabled)})
            updated += 1
        except Exception:
            continue
    return updated


def idle_due(last_activity: datetime | None, now: datetime | None = None, idle_s: int = DEV_IDLE_S) -> bool:
    if last_activity is None:
        return False
    current = now or datetime.now(timezone.utc)
    if last_activity.tzinfo is None:
        last_activity = last_activity.replace(tzinfo=timezone.utc)
    return (current - last_activity).total_seconds() >= idle_s


def disable_camera_indexing(ip: str, poster=None) -> None:
    """Best-effort: turn GPT and JEV indexing off on one camera."""
    send = poster or post_authed
    for path in ("/api/v1/indexing", "/api/v1/jev"):
        try:
            send(ip, path, {"enabled": False})
        except Exception:
            continue


def disable_all_indexing(registry: "Registry", poster=None) -> int:
    stopped = 0
    for camera in registry.list():
        ip = camera.get("ip")
        if not isinstance(ip, str) or not ip:
            continue
        disable_camera_indexing(ip, poster=poster)
        stopped += 1
    return stopped


class HighPerfState:
    """Persisted High performance flag for the registrar UI poll rate."""

    def __init__(self, path: Path = HIGH_PERF_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.enabled = load_high_perf(path)

    def set_enabled(self, enabled: bool) -> bool:
        with self._lock:
            self.enabled = bool(enabled)
            save_high_perf(self.enabled, self.path)
            return self.enabled

    def snapshot(self) -> bool:
        with self._lock:
            return self.enabled


class DevState:
    """Persisted Dev mode flag plus last UI activity for the idle watchdog."""

    def __init__(self, path: Path = DEV_MODE_PATH, now: datetime | None = None):
        self.path = path
        self._lock = threading.Lock()
        self.enabled = load_dev_mode(path)
        self.last_activity = now or datetime.now(timezone.utc)

    def set_enabled(self, enabled: bool) -> bool:
        with self._lock:
            self.enabled = bool(enabled)
            save_dev_mode(self.enabled, self.path)
            self.last_activity = datetime.now(timezone.utc)
            return self.enabled

    def touch(self, when: datetime | None = None) -> None:
        with self._lock:
            self.last_activity = when or datetime.now(timezone.utc)

    def snapshot(self) -> tuple[bool, datetime]:
        with self._lock:
            return self.enabled, self.last_activity


def maybe_idle_stop(registry: "Registry", state: DevState, poster=None, now: datetime | None = None) -> bool:
    """If Dev mode is on and idle, turn indexing off on every camera."""
    enabled, last = state.snapshot()
    if not enabled or not idle_due(last, now=now):
        return False
    disable_all_indexing(registry, poster=poster)
    state.touch(now)
    return True


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Witness registrar</title>
<style>
  body { font: 16px/1.4 ui-sans-serif, system-ui, sans-serif; margin: 1rem 1.25rem 2rem; color: #1c1917; background: #fafaf9; }
  h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
  p.note { color: #57534e; margin-top: 0; }
  .card { background: white; border: 1px solid #e7e5e4; border-radius: 8px; padding: 1rem; margin: 1rem 0; max-width: 52rem; }
  #list .card { max-width: 56rem; }
  img { max-width: 100%; background: #e7e5e4; }
  .meta { color: #57534e; font-size: 0.9rem; }
  .err { color: #9f1239; }
  form.query { display: flex; gap: 0.5rem; margin-top: 0.75rem; }
  form.query input, select, form.query button { font: inherit; padding: 0.4rem 0.55rem; }
  form.query input { flex: 1; }
  #transcript { max-height: 18rem; overflow: auto; }
  #transcript p { margin: 0.4rem 0; }
  .switches { display: flex; flex-direction: column; gap: 0.35rem; margin: 0.6rem 0; }
  .noul { border-top: 1px solid #e7e5e4; margin-top: 0.9rem; padding-top: 0.8rem; }
  .noul label, .stack { display: block; margin: 0.35rem 0; }
  .noul input[type="text"], .noul textarea, .noul select, .noul input[type="url"] { width: 100%; box-sizing: border-box; font: inherit; padding: 0.35rem; }
  .noul textarea { min-height: 4.2rem; }
  .row { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; margin: 0.4rem 0; }
  .example-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; margin: 0.6rem 0; }
  .example-side { border: 1px solid #e7e5e4; border-radius: 8px; padding: 0.6rem; }
  .example-side h3 { font-size: 0.95rem; margin: 0 0 0.35rem; }
  .example-side.yes { border-color: #86efac; }
  .example-side.no { border-color: #fda4af; }
  .example-side p { margin: 0 0 0.4rem; }
  .examples { display: flex; gap: 0.6rem; flex-wrap: wrap; margin: 0.4rem 0; }
  .examples figure { margin: 0; width: 5.5rem; }
  .examples img { width: 5.5rem; height: 5.5rem; object-fit: cover; }
  .examples figcaption { font-size: 0.8rem; color: #57534e; }
  @media (max-width: 640px) { .example-pair { grid-template-columns: 1fr; } }
  button.small { font: inherit; padding: 0.25rem 0.45rem; }
  .frame { position: relative; display: inline-block; max-width: 100%; }
  .frame img { display: block; max-width: 100%; width: min(100%, 640px); }
  .frame.paused { outline: 2px solid #a8a29e; outline-offset: 2px; }
  .frame-controls { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 0.45rem 0 0; }
  .boxes { position: absolute; left: 0; top: 0; right: 0; bottom: 0; pointer-events: none; }
  .box { position: absolute; border: 3px solid #16a34a; box-sizing: border-box; }
  .watches { display: flex; flex-direction: column; gap: 0.55rem; margin: 0.75rem 0 0.35rem; }
  .watch-score { display: flex; align-items: baseline; gap: 0.75rem; flex-wrap: wrap;
    padding: 0.65rem 0.85rem; border-radius: 10px; border: 2px solid transparent; }
  .watch-score.yes { background: #dcfce7; border-color: #16a34a; color: #14532d; }
  .watch-score.no { background: #ffe4e6; border-color: #e11d48; color: #881337; }
  .watch-label { font-size: 1.15rem; font-weight: 650; text-transform: capitalize; }
  .watch-value { font-size: 2rem; font-weight: 750; font-variant-numeric: tabular-nums; line-height: 1; }
  .watch-state { font-size: 1.35rem; font-weight: 750; letter-spacing: 0.04em; }
  .watch-time { font-size: 0.85rem; opacity: 0.75; margin-left: auto; }
  .noul-charts { display: grid; gap: 0.75rem; margin-top: 0.85rem; }
  .noul-chart { border: 1px solid #e7e5e4; border-radius: 8px; padding: 0.55rem 0.65rem 0.35rem; background: #fff; }
  .noul-chart h3 { margin: 0 0 0.35rem; font-size: 0.95rem; }
  .noul-chart svg { width: 100%; height: 120px; display: block; }
  .noul-chart .axis { fill: #78716c; font-size: 10px; }
  .card.stale { opacity: 0.72; border-style: dashed; }
  .card.stale .frame { filter: grayscale(0.4); }
  #switch-status { min-height: 1.2em; }
  #switch-status.err { color: #9f1239; }
</style>
</head>
<body>
<h1>Witness registrar</h1>
<p class="note" id="hint">Waiting for a camera to POST /api/register.</p>
<div id="list"></div>
<section class="card" id="ask">
  <h2>Ask a camera</h2>
  <p class="meta">This computer reads the on-camera index over MCP, including JEV watch scores, then answers with gpt-4.1-mini.</p>
  <label class="meta">Camera <select id="camera"></select></label>
  <div class="switches">
    <label class="meta" for="indexing"><input type="checkbox" id="indexing"> GPT indexing</label>
    <label class="meta" for="jev"><input type="checkbox" id="jev"> JEV indexing</label>
    <label class="meta" for="dev-mode"><input type="checkbox" id="dev-mode"> Dev mode</label>
    <label class="meta" for="high-perf"><input type="checkbox" id="high-perf"> High performance</label>
    <p class="meta" id="switch-status"></p>
    <p class="meta">JEV indexing runs on the camera. The camera calls TypeSafe. This computer does not.</p>
    <p class="meta">Dev mode turns GPT and JEV indexing off after 5 minutes without interaction. Restarts also start with indexing off.</p>
    <p class="meta">High performance refreshes the live view every 0.5s. Capture stays at 5 fps so recording and the frame API stay reliable. Leave it off during install.</p>
  </div>
  <div id="transcript"></div>
  <form class="query" id="query-form">
    <input id="question" autocomplete="off" placeholder="How many times was a pipette tip seen in the last 2 minutes?">
    <button type="submit">Ask</button>
  </form>
</section>
<section class="card" id="nouls">
  <h2>JEV nouls</h2>
  <p class="meta">Yes/no questions for the camera. Example photos, box colors, and an optional Slack webhook are stored on the camera. This page does not call TypeSafe.</p>
  <p class="meta">For a pipette demo, add one noul for a tip on the pipettor and one for a bent tip. Each noul has its own Yes examples and No examples. The box is drawn on the live view when the camera answers yes.</p>
  <div id="noul-list"></div>
  <div class="row">
    <button type="button" id="noul-add" class="small">Add noul</button>
    <button type="button" id="noul-save" class="small">Save to camera</button>
  </div>
  <p class="meta" id="noul-status"></p>
</section>
<script>
const transcript = [];
let indexingBusy = false;
let jevBusy = false;
let noulDraft = [];
let noulCamera = null;
const liveCards = new Map();
const pausedCameras = new Set();
const DEV_IDLE_MS = 5 * 60 * 1000;
const POLL_MS_DEFAULT = 2000;
const POLL_MS_HIGH_PERF = 500;
const SWITCH_SYNC_MS = 30000;
let lastUserActivity = Date.now();
let idleWatchdog = null;
let activityPing = null;
let loadTimer = null;
let loadBusy = false;
let highPerfOn = false;
let lastSwitchSync = 0;

function cameraId() {
  return document.getElementById("camera").value;
}

function noteActivity() {
  lastUserActivity = Date.now();
  if (activityPing) return;
  activityPing = setTimeout(() => {
    activityPing = null;
    fetch("/api/activity", {method: "POST"}).catch(() => {});
  }, 2000);
}

async function setCameraSwitch(kind, enabled) {
  const camera = cameraId();
  if (!camera) return false;
  const res = await fetch("/api/cameras/" + encodeURIComponent(camera) + "/" + kind, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({enabled}),
  });
  const data = await res.json().catch(() => ({}));
  return res.ok ? !!data.enabled : null;
}

async function stopIndexingForIdle() {
  setSwitchStatus("Dev mode: indexing stopped after 5 minutes idle.", false);
  await fetch("/api/dev-idle", {method: "POST"}).catch(() => {});
  document.getElementById("indexing").checked = false;
  document.getElementById("jev").checked = false;
}

function armIdleWatchdog() {
  if (idleWatchdog) clearInterval(idleWatchdog);
  idleWatchdog = setInterval(() => {
    if (!document.getElementById("dev-mode").checked) return;
    if (Date.now() - lastUserActivity < DEV_IDLE_MS) return;
    stopIndexingForIdle();
    noteActivity();
  }, 15000);
}

function armLoadTimer() {
  if (loadTimer) clearInterval(loadTimer);
  const ms = highPerfOn ? POLL_MS_HIGH_PERF : POLL_MS_DEFAULT;
  loadTimer = setInterval(load, ms);
}

async function syncDevMode() {
  const box = document.getElementById("dev-mode");
  const res = await fetch("/api/dev-mode");
  if (!res.ok) return;
  const data = await res.json().catch(() => null);
  if (!data) return;
  box.checked = !!data.enabled;
  armIdleWatchdog();
}

async function syncHighPerf() {
  const box = document.getElementById("high-perf");
  const res = await fetch("/api/high-perf");
  if (!res.ok) return;
  const data = await res.json().catch(() => null);
  if (!data) return;
  highPerfOn = !!data.enabled;
  box.checked = highPerfOn;
  armLoadTimer();
}

document.getElementById("dev-mode").addEventListener("change", async (event) => {
  noteActivity();
  const enabled = event.target.checked;
  const res = await fetch("/api/dev-mode", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({enabled}),
  });
  const data = await res.json().catch(() => ({}));
  event.target.checked = res.ok ? !!data.enabled : !enabled;
  setSwitchStatus(
    event.target.checked
      ? "Dev mode on. Indexing turns off after 5 minutes idle."
      : "Dev mode off.",
    false,
  );
  armIdleWatchdog();
});

document.getElementById("high-perf").addEventListener("change", async (event) => {
  noteActivity();
  const enabled = event.target.checked;
  const res = await fetch("/api/high-perf", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({enabled}),
  });
  const data = await res.json().catch(() => ({}));
  highPerfOn = res.ok ? !!data.enabled : !enabled;
  event.target.checked = highPerfOn;
  setSwitchStatus(
    highPerfOn
      ? "High performance on. Live view every 0.5s."
      : "High performance off. Live view every 2s.",
    false,
  );
  armLoadTimer();
});

["click", "keydown", "change"].forEach((name) => {
  document.addEventListener(name, (event) => {
    if (event.target && event.target.id === "dev-mode") return;
    noteActivity();
  }, true);
});

function drawBox(box) {
  const el = document.createElement("div");
  el.className = "box";
  const top = Number(box.top);
  const left = Number(box.left);
  const right = Number(box.right);
  const bottom = Number(box.bottom);
  if (![top, left, right, bottom].every((value) => Number.isFinite(value))) return el;
  el.style.top = (top * 100) + "%";
  el.style.left = (left * 100) + "%";
  el.style.width = ((right - left) * 100) + "%";
  el.style.height = ((bottom - top) * 100) + "%";
  el.style.borderColor = /^#[0-9a-fA-F]{6}$/.test(box.color || "") ? box.color : "#16a34a";
  if (box.label) el.title = box.label;
  return el;
}

function setSwitchStatus(text, isError) {
  const status = document.getElementById("switch-status");
  status.textContent = text || "";
  status.classList.toggle("err", !!isError);
}

function cameraStale(cam) {
  if (!cam || !cam.last_seen) return true;
  const ms = Date.parse(cam.last_seen);
  if (!Number.isFinite(ms)) return true;
  return (Date.now() - ms) > 120000;
}

async function syncIndexing(camera) {
  const box = document.getElementById("indexing");
  if (!camera) {
    box.checked = false;
    box.disabled = true;
    return;
  }
  box.disabled = false;
  if (indexingBusy) return;
  const res = await fetch("/api/cameras/" + encodeURIComponent(camera) + "/indexing");
  if (!res.ok) {
    setSwitchStatus("Could not read GPT indexing from " + camera + ".", true);
    return;
  }
  const data = await res.json().catch(() => null);
  if (!data || indexingBusy) return;
  box.checked = !!data.enabled;
  setSwitchStatus("", false);
}

document.getElementById("indexing").addEventListener("change", async (event) => {
  const camera = cameraId();
  const box = event.target;
  if (!camera) {
    box.checked = false;
    return;
  }
  indexingBusy = true;
  const enabled = box.checked;
  setSwitchStatus(enabled ? "Turning GPT indexing on…" : "Turning GPT indexing off…", false);
  try {
    const res = await fetch("/api/cameras/" + encodeURIComponent(camera) + "/indexing", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({enabled}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      box.checked = !enabled;
      setSwitchStatus((data.error || "GPT indexing change failed") + " (" + camera + ").", true);
    } else {
      box.checked = !!data.enabled;
      setSwitchStatus("GPT indexing is " + (data.enabled ? "on" : "off") + ".", false);
    }
  } catch (err) {
    box.checked = !enabled;
    setSwitchStatus("GPT indexing change failed (" + camera + ").", true);
  }
  indexingBusy = false;
});

async function syncJev(camera) {
  const box = document.getElementById("jev");
  if (!camera) {
    box.checked = false;
    box.disabled = true;
    return;
  }
  box.disabled = false;
  if (jevBusy) return;
  const res = await fetch("/api/cameras/" + encodeURIComponent(camera) + "/jev");
  if (!res.ok) {
    setSwitchStatus("Could not read JEV indexing from " + camera + ".", true);
    return;
  }
  const data = await res.json().catch(() => null);
  if (!data || jevBusy) return;
  box.checked = !!data.enabled;
  setSwitchStatus("", false);
}

document.getElementById("jev").addEventListener("change", async (event) => {
  const camera = cameraId();
  const box = event.target;
  if (!camera) {
    box.checked = false;
    return;
  }
  jevBusy = true;
  const enabled = box.checked;
  setSwitchStatus(enabled ? "Turning JEV indexing on…" : "Turning JEV indexing off…", false);
  try {
    const res = await fetch("/api/cameras/" + encodeURIComponent(camera) + "/jev", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({enabled}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      box.checked = !enabled;
      setSwitchStatus((data.error || "JEV indexing change failed") + " (" + camera + ").", true);
    } else {
      box.checked = !!data.enabled;
      setSwitchStatus("JEV indexing is " + (data.enabled ? "on" : "off") + ".", false);
    }
  } catch (err) {
    box.checked = !enabled;
    setSwitchStatus("JEV indexing change failed (" + camera + ").", true);
  }
  jevBusy = false;
});

function slug(text) {
  const made = text.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 32);
  return made && /^[a-z0-9]/.test(made) ? made : "noul";
}

function blankNoul() {
  return {
    name: "",
    label: "",
    instructions: "",
    examples: [],
    box: {enabled: false, color: "#16a34a", instructions: ""},
    alert: {when: "yes", url: ""},
  };
}

function normalizeNoul(item) {
  const box = item.box || {};
  const alert = item.alert || {};
  return {
    name: item.name || "",
    label: item.label || "",
    instructions: item.instructions || "",
    examples: Array.isArray(item.examples) ? item.examples.filter((example) => example && example.id).map((example) => ({
      id: String(example.id),
      polarity: example.polarity === "no" ? "no" : "yes",
    })) : [],
    box: {
      enabled: !!box.enabled,
      color: /^#[0-9a-fA-F]{6}$/.test(box.color || "") ? box.color : "#16a34a",
      instructions: box.instructions || "",
    },
    alert: {when: alert.when === "no" ? "no" : "yes", url: alert.url || ""},
  };
}

function setNoulStatus(text) {
  document.getElementById("noul-status").textContent = text;
}

function collectNouls() {
  document.querySelectorAll("[data-noul]").forEach((node) => {
    const item = noulDraft[Number(node.dataset.noul)];
    if (!item) return;
    item.label = node.querySelector(".noul-label").value.trim();
    item.name = node.querySelector(".noul-name").value.trim();
    item.instructions = node.querySelector(".noul-instructions").value.trim();
    item.box.enabled = node.querySelector(".noul-box").checked;
    item.box.color = node.querySelector(".noul-color").value;
    item.box.instructions = node.querySelector(".noul-box-instructions").value.trim();
    item.alert.when = node.querySelector(".noul-when").value;
    item.alert.url = node.querySelector(".noul-url").value.trim();
  });
}

function field(labelText, control) {
  const label = document.createElement("label");
  label.className = "stack";
  label.append(document.createTextNode(labelText), control);
  return label;
}

function renderNouls() {
  const list = document.getElementById("noul-list");
  const camera = cameraId();
  list.replaceChildren(...noulDraft.map((item, index) => {
    const block = document.createElement("div");
    block.className = "noul";
    block.dataset.noul = String(index);
    const label = document.createElement("input");
    label.className = "noul-label";
    label.value = item.label;
    label.placeholder = "pipette tip bent";
    const name = document.createElement("input");
    name.className = "noul-name";
    name.value = item.name;
    name.placeholder = "tip_bent";
    const instructions = document.createElement("textarea");
    instructions.className = "noul-instructions";
    instructions.value = item.instructions;
    instructions.placeholder = "Is the pipette tip bent in `photo`?";
    const examplePair = document.createElement("div");
    examplePair.className = "example-pair";
    examplePair.append(
      exampleSide(index, "yes", item, camera),
      exampleSide(index, "no", item, camera),
    );
    const boxOn = document.createElement("input");
    boxOn.type = "checkbox";
    boxOn.className = "noul-box";
    boxOn.checked = !!item.box.enabled;
    const color = document.createElement("input");
    color.type = "color";
    color.className = "noul-color";
    color.value = item.box.color;
    const boxInstructions = document.createElement("input");
    boxInstructions.className = "noul-box-instructions";
    boxInstructions.value = item.box.instructions;
    boxInstructions.placeholder = "the pipette tip in `photo`";
    const when = document.createElement("select");
    when.className = "noul-when";
    ["yes", "no"].forEach((side) => {
      const option = document.createElement("option");
      option.value = side;
      option.textContent = "Alert on " + side;
      if (item.alert.when === side) option.selected = true;
      when.append(option);
    });
    const url = document.createElement("input");
    url.type = "url";
    url.className = "noul-url";
    url.value = item.alert.url;
    url.placeholder = "https://hooks.slack.com/services/...";
    url.autocomplete = "off";
    const removeNoul = document.createElement("button");
    removeNoul.type = "button";
    removeNoul.className = "small";
    removeNoul.textContent = "Remove noul";
    removeNoul.addEventListener("click", () => {
      collectNouls();
      noulDraft.splice(index, 1);
      renderNouls();
    });
    const boxRow = document.createElement("div");
    boxRow.className = "row";
    const boxLabel = document.createElement("label");
    boxLabel.className = "meta";
    boxLabel.append(boxOn, document.createTextNode(" Bounding box"));
    boxRow.append(boxLabel, color);
    block.append(
      field("Label", label),
      field("Name", name),
      field("Yes/no question", instructions),
      examplePair,
      boxRow,
      field("What to outline", boxInstructions),
      field("Slack webhook, optional", url),
      when,
      removeNoul,
    );
    return block;
  }));
}

function exampleSide(index, polarity, item, camera) {
  const side = document.createElement("div");
  side.className = "example-side " + polarity;
  const heading = document.createElement("h3");
  heading.textContent = polarity === "yes" ? "Yes examples" : "No examples";
  const note = document.createElement("p");
  note.className = "meta";
  note.textContent = polarity === "yes" ? "A photo where the answer is yes." : "A photo where the answer is no.";
  const figures = document.createElement("div");
  figures.className = "examples";
  item.examples.forEach((example, exampleIndex) => {
    if (example.polarity !== polarity) return;
    const figure = document.createElement("figure");
    const img = document.createElement("img");
    img.alt = polarity + " example";
    if (camera && item.name && example.id) {
      img.src = "/api/cameras/" + encodeURIComponent(camera) + "/nouls/" + encodeURIComponent(item.name) + "/examples/" + encodeURIComponent(example.id);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "small";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      collectNouls();
      noulDraft[index].examples.splice(exampleIndex, 1);
      renderNouls();
    });
    figure.append(img, remove);
    figures.append(figure);
  });
  const file = document.createElement("input");
  file.type = "file";
  file.accept = "image/*";
  const upload = document.createElement("button");
  upload.type = "button";
  upload.className = "small";
  upload.textContent = polarity === "yes" ? "Upload yes" : "Upload no";
  upload.addEventListener("click", () => uploadExample(index, file, polarity));
  side.append(heading, note, figures, file, upload);
  return side;
}

function nextExampleId(examples, polarity) {
  const used = new Set(examples.map((example) => example.id));
  let number = 1;
  let id = polarity + String(number);
  while (used.has(id)) {
    number += 1;
    id = polarity + String(number);
  }
  return id;
}

async function uploadExample(index, file, polarity) {
  collectNouls();
  const camera = cameraId();
  const item = noulDraft[index];
  if (!camera || !item) return;
  if (!item.name) item.name = slug(item.label);
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/.test(item.name)) {
    setNoulStatus("Give the noul a short name before uploading an example.");
    renderNouls();
    return;
  }
  if (!file.files || !file.files[0]) {
    setNoulStatus("Choose an example image first.");
    return;
  }
  if (item.examples.length >= 4) {
    setNoulStatus("A noul can have 4 examples.");
    return;
  }
  const id = nextExampleId(item.examples, polarity === "no" ? "no" : "yes");
  setNoulStatus("Uploading example to the camera.");
  try {
    const res = await fetch("/api/cameras/" + encodeURIComponent(camera) + "/nouls/" + encodeURIComponent(item.name) + "/examples/" + encodeURIComponent(id), {
      method: "PUT",
      headers: {"Content-Type": file.files[0].type || "application/octet-stream"},
      body: file.files[0],
    });
    if (!res.ok) {
      const text = (await res.text().catch(() => "")).trim();
      if (res.status === 404 || res.status === 502) {
        setNoulStatus("Example upload failed. The camera firmware does not have the noul routes yet. Flash the jev-nouls build.");
      } else if (text && !text.includes("https://")) {
        setNoulStatus("Example upload failed: " + text.slice(0, 160));
      } else {
        setNoulStatus("Example upload failed.");
      }
      return;
    }
    item.examples.push({id, polarity: polarity === "no" ? "no" : "yes"});
    setNoulStatus("Example stored on the camera. Save the noul to keep it in the question.");
    renderNouls();
  } catch (err) {
    setNoulStatus("Example upload failed. The registrar could not reach the camera.");
  }
}

async function saveNouls() {
  const camera = cameraId();
  if (!camera) return;
  collectNouls();
  const payload = noulDraft.map((item) => ({
    name: item.name || slug(item.label),
    label: item.label,
    instructions: item.instructions,
    examples: item.examples.map((example) => ({id: example.id, polarity: example.polarity})),
    box: {
      enabled: !!item.box.enabled,
      color: item.box.color,
      instructions: item.box.instructions,
    },
    alert: {when: item.alert.when === "no" ? "no" : "yes", url: item.alert.url},
  }));
  setNoulStatus("Saving nouls to the camera.");
  try {
    const res = await fetch("/api/cameras/" + encodeURIComponent(camera) + "/nouls", {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const text = await res.text();
      setNoulStatus(text.includes("https://") ? "Could not save nouls." : (text.trim().slice(0, 200) || "Could not save nouls."));
      return;
    }
    noulCamera = null;
    await loadNouls(camera);
    setNoulStatus("Saved " + noulDraft.length + " noul" + (noulDraft.length === 1 ? "" : "s") + " on the camera.");
  } catch (err) {
    setNoulStatus("Could not save nouls.");
  }
}

async function loadNouls(camera) {
  if (camera === noulCamera) return;
  noulCamera = camera;
  if (!camera) {
    noulDraft = [];
    renderNouls();
    return;
  }
  try {
    const res = await fetch("/api/cameras/" + encodeURIComponent(camera) + "/nouls");
    if (!res.ok) {
      noulDraft = [];
      renderNouls();
      return;
    }
    const data = await res.json().catch(() => []);
    noulDraft = Array.isArray(data) ? data.map(normalizeNoul) : [];
  } catch (err) {
    noulDraft = [];
  }
  renderNouls();
}

document.getElementById("noul-add").addEventListener("click", () => {
  collectNouls();
  noulDraft.push(blankNoul());
  renderNouls();
});

document.getElementById("noul-save").addEventListener("click", () => {
  saveNouls();
});

document.getElementById("camera").addEventListener("change", () => {
  const camera = cameraId();
  syncIndexing(camera);
  syncJev(camera);
  noulCamera = null;
  loadNouls(camera);
});

function syncCameras(cameras) {
  const select = document.getElementById("camera");
  const current = select.value;
  const ordered = [...cameras].sort((a, b) => Number(cameraStale(a)) - Number(cameraStale(b)));
  select.replaceChildren(...ordered.map((cam) => {
    const option = document.createElement("option");
    option.value = cam.id;
    option.textContent = cam.id + (cameraStale(cam) ? " (stale)" : "");
    return option;
  }));
  const live = ordered.find((cam) => !cameraStale(cam));
  if (ordered.some((cam) => cam.id === current) && !(cameraStale(ordered.find((cam) => cam.id === current)) && live)) {
    select.value = current;
  } else if (live) {
    select.value = live.id;
  }
}

function renderTranscript() {
  const box = document.getElementById("transcript");
  box.replaceChildren(...transcript.map((line) => {
    const row = document.createElement("p");
    row.textContent = line.who + ": " + line.text;
    return row;
  }));
  box.scrollTop = box.scrollHeight;
}

document.getElementById("query-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const camera = document.getElementById("camera").value;
  const input = document.getElementById("question");
  const text = input.value.trim();
  if (!camera || !text) return;
  input.value = "";
  transcript.push({who: "You", text});
  renderTranscript();
  const button = event.target.querySelector("button");
  button.disabled = true;
  try {
    const res = await fetch("/api/cameras/" + encodeURIComponent(camera) + "/query", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text}),
    });
    const data = await res.json().catch(() => ({}));
    transcript.push({who: camera, text: res.ok ? (data.answer || "") : (data.error || "query failed")});
  } catch (err) {
    transcript.push({who: camera, text: "query failed"});
  }
  button.disabled = false;
  renderTranscript();
});

function frameUrl(camera, stamp) {
  return "/api/cameras/" + encodeURIComponent(camera) + "/frame.jpg?t=" + stamp;
}

function setPaused(camera, paused) {
  if (paused) pausedCameras.add(camera);
  else pausedCameras.delete(camera);
  const card = liveCards.get(camera);
  if (!card) return;
  card.frame.classList.toggle("paused", paused);
  card.pauseBtn.textContent = paused ? "Resume" : "Pause";
}

function downloadFrame(camera) {
  const card = liveCards.get(camera);
  if (!card || !card.frameBlob) {
    if (card) card.err.textContent = "no frame to download yet";
    return;
  }
  const url = URL.createObjectURL(card.frameBlob);
  const link = document.createElement("a");
  link.href = url;
  link.download = camera + "-" + new Date().toISOString().replace(/[:.]/g, "-") + ".jpg";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function showFrame(card, blob) {
  if (card.objectUrl) URL.revokeObjectURL(card.objectUrl);
  card.frameBlob = blob;
  card.objectUrl = URL.createObjectURL(blob);
  card.img.src = card.objectUrl;
}

const CHART_WINDOW_MS = 10 * 60 * 1000;

function ensureCard(cam) {
  let card = liveCards.get(cam.id);
  if (card) return card;
  const section = document.createElement("section");
  section.className = "card";
  section.dataset.camera = cam.id;
  const title = document.createElement("h2");
  title.textContent = cam.id;
  const meta = document.createElement("p");
  meta.className = "meta";
  const frame = document.createElement("div");
  frame.className = "frame";
  const img = document.createElement("img");
  img.alt = "Live view from " + cam.id;
  const boxes = document.createElement("div");
  boxes.className = "boxes";
  frame.append(img, boxes);
  const controls = document.createElement("div");
  controls.className = "frame-controls";
  const pauseBtn = document.createElement("button");
  pauseBtn.type = "button";
  pauseBtn.className = "small";
  pauseBtn.textContent = "Pause";
  pauseBtn.addEventListener("click", () => setPaused(cam.id, !pausedCameras.has(cam.id)));
  const downloadBtn = document.createElement("button");
  downloadBtn.type = "button";
  downloadBtn.className = "small";
  downloadBtn.textContent = "Download";
  downloadBtn.addEventListener("click", () => downloadFrame(cam.id));
  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.className = "small";
  removeBtn.textContent = "Remove";
  removeBtn.addEventListener("click", () => removeCamera(cam.id));
  controls.append(pauseBtn, downloadBtn, removeBtn);
  const watches = document.createElement("div");
  watches.className = "watches";
  const caption = document.createElement("p");
  caption.className = "meta";
  const charts = document.createElement("div");
  charts.className = "noul-charts";
  const err = document.createElement("p");
  err.className = "err";
  section.append(title, meta, frame, controls, watches, caption, charts, err);
  card = {
    section, meta, frame, img, boxes, watches, caption, charts, err, pauseBtn, removeBtn,
    loadToken: 0, frameBlob: null, objectUrl: null,
    series: new Map(), historyLoaded: false, lastWatchT: "",
  };
  liveCards.set(cam.id, card);
  loadWatchHistory(card, cam.id);
  return card;
}

function parseStamp(text) {
  if (!text) return NaN;
  const ms = Date.parse(text);
  return Number.isFinite(ms) ? ms : NaN;
}

function trimSeries(card) {
  const cutoff = Date.now() - CHART_WINDOW_MS;
  for (const [label, points] of card.series) {
    const kept = points.filter((point) => point.t >= cutoff);
    if (kept.length) card.series.set(label, kept);
    else card.series.delete(label);
  }
}

function rememberWatch(card, row) {
  if (!row || !row.t || !row.watch || typeof row.watch !== "object") return;
  const t = parseStamp(row.t);
  if (!Number.isFinite(t)) return;
  Object.keys(row.watch).forEach((label) => {
    const score = Number(row.watch[label]);
    if (!Number.isFinite(score)) return;
    const points = card.series.get(label) || [];
    const last = points[points.length - 1];
    if (last && last.t === t) {
      last.v = score;
    } else if (!last || last.t < t) {
      points.push({t, v: score});
    }
    card.series.set(label, points);
  });
  trimSeries(card);
}

function drawCharts(card) {
  const labels = [...card.series.keys()].sort();
  card.charts.replaceChildren(...labels.map((label) => {
    const points = card.series.get(label) || [];
    const wrap = document.createElement("div");
    wrap.className = "noul-chart";
    const heading = document.createElement("h3");
    heading.textContent = label;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 640 120");
    svg.setAttribute("preserveAspectRatio", "none");
    const width = 640, height = 120, pad = 18;
    const now = Date.now();
    const minT = now - CHART_WINDOW_MS;
    const xAt = (t) => pad + ((t - minT) / CHART_WINDOW_MS) * (width - pad * 2);
    const yAt = (v) => (height - pad) - Math.max(0, Math.min(1, v)) * (height - pad * 2);
    const axis = document.createElementNS("http://www.w3.org/2000/svg", "line");
    axis.setAttribute("x1", String(pad));
    axis.setAttribute("x2", String(width - pad));
    axis.setAttribute("y1", String(yAt(0.5)));
    axis.setAttribute("y2", String(yAt(0.5)));
    axis.setAttribute("stroke", "#d6d3d1");
    axis.setAttribute("stroke-dasharray", "4 4");
    svg.append(axis);
    if (points.length) {
      const path = points.map((point, index) => {
        const x = xAt(Math.max(minT, point.t));
        const y = yAt(point.v);
        return (index ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
      }).join(" ");
      const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
      line.setAttribute("d", path);
      line.setAttribute("fill", "none");
      line.setAttribute("stroke", "#0f766e");
      line.setAttribute("stroke-width", "2.5");
      svg.append(line);
      const last = points[points.length - 1];
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", String(xAt(Math.max(minT, last.t))));
      dot.setAttribute("cy", String(yAt(last.v)));
      dot.setAttribute("r", "4");
      dot.setAttribute("fill", last.v >= 0.5 ? "#16a34a" : "#e11d48");
      svg.append(dot);
    }
    const left = document.createElementNS("http://www.w3.org/2000/svg", "text");
    left.setAttribute("class", "axis");
    left.setAttribute("x", String(pad));
    left.setAttribute("y", "14");
    left.textContent = "10m ago";
    const right = document.createElementNS("http://www.w3.org/2000/svg", "text");
    right.setAttribute("class", "axis");
    right.setAttribute("x", String(width - pad));
    right.setAttribute("y", "14");
    right.setAttribute("text-anchor", "end");
    right.textContent = "now";
    svg.append(left, right);
    wrap.append(heading, svg);
    return wrap;
  }));
}

function renderWatch(card, row) {
  const watch = row && row.watch && typeof row.watch === "object" ? row.watch : null;
  const labels = watch ? Object.keys(watch) : [];
  // Keep the last YES/NO strip when a caption arrives without watch scores.
  if (labels.length) {
    card.watches.replaceChildren(...labels.map((label) => {
      const score = Number(watch[label]);
      const yes = score >= 0.5;
      const el = document.createElement("div");
      el.className = "watch-score " + (yes ? "yes" : "no");
      const name = document.createElement("span");
      name.className = "watch-label";
      name.textContent = label;
      const value = document.createElement("span");
      value.className = "watch-value";
      value.textContent = Number.isFinite(score) ? score.toFixed(2) : "—";
      const state = document.createElement("span");
      state.className = "watch-state";
      state.textContent = yes ? "YES" : "NO";
      const when = document.createElement("span");
      when.className = "watch-time";
      when.textContent = row.t || "";
      el.append(name, value, state, when);
      return el;
    }));
  }
  if (row && row.caption) {
    const objects = Array.isArray(row.objects) && row.objects.length ? " (" + row.objects.join(", ") + ")" : "";
    card.caption.textContent = (row.t || "") + " — " + row.caption + objects;
  }
  rememberWatch(card, row);
  drawCharts(card);
}

function loadWatchHistory(card, camera) {
  if (card.historyLoaded) return;
  card.historyLoaded = true;
  fetch("/api/cameras/" + encodeURIComponent(camera) + "/index")
    .then((res) => res.status === 200 ? res.json() : null)
    .then((data) => {
      const rows = data && Array.isArray(data.records) ? data.records : [];
      rows.forEach((row) => rememberWatch(card, row));
      drawCharts(card);
    })
    .catch(() => {});
}

function updateCaption(card, camera) {
  if (pausedCameras.has(camera)) return;
  fetch("/api/cameras/" + encodeURIComponent(camera) + "/caption")
    .then((res) => res.status === 200 ? res.json() : null)
    .then((row) => {
      if (!row || pausedCameras.has(camera)) return;
      if (row.t && row.t === card.lastWatchT) return;
      card.lastWatchT = row.t || "";
      renderWatch(card, row);
      card.boxes.replaceChildren(...(Array.isArray(row.boxes) ? row.boxes : []).map(drawBox));
    })
    .catch(() => {});
}

async function refreshFrame(card, camera, stamp) {
  if (pausedCameras.has(camera)) return;
  const token = ++card.loadToken;
  try {
    const res = await fetch(frameUrl(camera, stamp));
    if (!res.ok) return;
    const blob = await res.blob();
    if (token !== card.loadToken || pausedCameras.has(camera)) return;
    const url = URL.createObjectURL(blob);
    const next = new Image();
    next.onload = () => {
      URL.revokeObjectURL(url);
      if (token !== card.loadToken || pausedCameras.has(camera)) return;
      showFrame(card, blob);
    };
    next.onerror = () => URL.revokeObjectURL(url);
    next.src = url;
  } catch (err) {}
}

async function removeCamera(camera) {
  if (!camera) return;
  const res = await fetch("/api/cameras/" + encodeURIComponent(camera), {method: "DELETE"});
  if (!res.ok) {
    setSwitchStatus("Could not remove " + camera + ".", true);
    return;
  }
  const card = liveCards.get(camera);
  if (card && card.objectUrl) URL.revokeObjectURL(card.objectUrl);
  liveCards.delete(camera);
  pausedCameras.delete(camera);
  if (cameraId() === camera) {
    document.getElementById("camera").value = "";
  }
  setSwitchStatus("Removed " + camera + ".", false);
  await load();
}

async function load() {
  if (loadBusy) return;
  loadBusy = true;
  try {
  const res = await fetch("/api/cameras");
  const data = await res.json();
  const hint = document.getElementById("hint");
  const list = document.getElementById("list");
  if (!data.cameras.length) {
    hint.textContent = "Waiting for a camera to POST /api/register.";
    syncCameras([]);
    syncIndexing("");
    syncJev("");
    loadNouls("");
    for (const card of liveCards.values()) {
      if (card.objectUrl) URL.revokeObjectURL(card.objectUrl);
    }
    liveCards.clear();
    pausedCameras.clear();
    list.replaceChildren();
    return;
  }
  const liveCount = data.cameras.filter((cam) => !cameraStale(cam)).length;
  hint.textContent = liveCount + " live camera" + (liveCount === 1 ? "" : "s")
    + " · " + data.cameras.length + " registered.";
  syncCameras(data.cameras);
  const now = Date.now();
  if (now - lastSwitchSync >= SWITCH_SYNC_MS) {
    lastSwitchSync = now;
    syncIndexing(cameraId());
    syncJev(cameraId());
    loadNouls(cameraId());
  }
  const stamp = now;
  const seen = new Set();
  const jobs = [];
  const cards = data.cameras.map((cam) => {
    seen.add(cam.id);
    const card = ensureCard(cam);
    const stale = cameraStale(cam);
    card.section.classList.toggle("stale", stale);
    card.meta.textContent = cam.ip + " · last seen " + cam.last_seen
      + (cam.fw ? " · fw " + cam.fw : "")
      + (stale ? " · stale" : "");
    card.err.textContent = cam.last_view_error || (stale ? "Camera has not registered recently. Remove it if it is gone." : "");
    card.frame.classList.toggle("paused", pausedCameras.has(cam.id));
    card.pauseBtn.textContent = pausedCameras.has(cam.id) ? "Resume" : "Pause";
    if (!stale) {
      jobs.push(refreshFrame(card, cam.id, stamp));
      updateCaption(card, cam.id);
    }
    return card.section;
  });
  for (const id of [...liveCards.keys()]) {
    if (!seen.has(id)) {
      const gone = liveCards.get(id);
      if (gone && gone.objectUrl) URL.revokeObjectURL(gone.objectUrl);
      liveCards.delete(id);
      pausedCameras.delete(id);
    }
  }
  const same =
    list.children.length === cards.length &&
    cards.every((section, index) => list.children[index] === section);
  if (!same) list.replaceChildren(...cards);
  if (jobs.length) await Promise.all(jobs);
  } catch (err) {
  } finally {
    loadBusy = false;
  }
}
load();
syncDevMode();
syncHighPerf();
armLoadTimer();
</script>
</body>
</html>
"""


def load_local_env() -> None:
    """Load fixr/run/secrets.env. Existing environment variables win. Values are never printed."""
    path = RUN / "secrets.env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lan_ip() -> str:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def acceptable_ip(value: str) -> bool:
    try:
        addr = ip_address(value)
    except ValueError:
        return False
    return addr.version == 4 and (addr.is_private or addr.is_loopback)


class Registry:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.cameras: dict[str, dict] = {}
        if path.exists():
            loaded = json.loads(path.read_text())
            self.cameras = {item["id"]: item for item in loaded.get("cameras", [])}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"cameras": list(self.cameras.values())}
        self.path.write_text(json.dumps(payload, indent=2) + "\n")

    def upsert(self, camera_id: str, ip: str, fw: str | None, board: str | None) -> dict:
        with self._lock:
            current = self.cameras.get(camera_id, {})
            current.update(
                {
                    "id": camera_id,
                    "ip": ip,
                    "fw": fw or current.get("fw") or "",
                    "board": board or current.get("board") or "",
                    "last_seen": utc_now(),
                    "last_view_error": current.get("last_view_error", ""),
                }
            )
            self.cameras[camera_id] = current
            self.save()
            return dict(current)

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self.cameras.values()]

    def get(self, camera_id: str) -> dict | None:
        with self._lock:
            item = self.cameras.get(camera_id)
            return dict(item) if item else None

    def note_view(self, camera_id: str, error: str) -> None:
        with self._lock:
            item = self.cameras.get(camera_id)
            if not item:
                return
            item["last_view_error"] = error
            if not error:
                item["last_view_ok"] = utc_now()
            self.save()

    def remove(self, camera_id: str) -> bool:
        with self._lock:
            if camera_id not in self.cameras:
                return False
            del self.cameras[camera_id]
            self.save()
            return True


def fetch_authed(ip: str, path: str, limit: int) -> tuple[int, bytes, str]:
    password = os.environ.get("WITNESS_CAMERA_PASSWORD", "")
    if not password:
        return 503, b"WITNESS_CAMERA_PASSWORD is not set\n", "text/plain"
    req = urllib.request.Request(f"http://{ip}{path}")
    req.add_header("Authorization", "Bearer " + password)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read(limit)
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
            return resp.status, body, ctype
    except urllib.error.HTTPError as exc:
        return 502, f"camera returned HTTP {exc.code}\n".encode(), "text/plain"
    except Exception as exc:
        return 502, f"camera unreachable: {exc.__class__.__name__}\n".encode(), "text/plain"


def fetch_frame(ip: str) -> tuple[int, bytes, str]:
    return fetch_authed(ip, FRAME_PATH, 2_000_000)


def shrink_jpeg(data: bytes) -> bytes:
    # TypeSafe image limits: each side 280..1568, area 78400..2458624
    # (https://docs.typesafe.ai/images). thumbnail() only shrinks.
    image = Image.open(BytesIO(data))
    image = image.convert("RGB")
    width, height = image.size
    min_side = 280
    max_side = 1568
    if width < min_side or height < min_side:
        scale = max(min_side / width, min_side / height)
        image = image.resize((max(min_side, round(width * scale)), max(min_side, round(height * scale))))
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side))
    raw = b""
    quality = 85
    while quality >= 40:
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=quality)
        raw = buf.getvalue()
        if len(raw) <= EXAMPLE_MAX_BYTES:
            return raw
        quality -= 15
    raise ValueError("example image is still over 80KB")


def put_authed(ip: str, path: str, data: bytes, content_type: str, limit: int) -> tuple[int, bytes, str]:
    password = os.environ.get("WITNESS_CAMERA_PASSWORD", "")
    if not password:
        return 503, b"WITNESS_CAMERA_PASSWORD is not set\n", "text/plain"
    req = urllib.request.Request(f"http://{ip}{path}", data=data, method="PUT")
    req.add_header("Authorization", "Bearer " + password)
    req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read(limit), resp.headers.get("Content-Type", "application/octet-stream")
    except urllib.error.HTTPError as exc:
        detail = exc.read(400)
        if b"https://" in detail:
            detail = b"camera rejected the noul\n"
        return exc.code, detail or f"camera returned HTTP {exc.code}\n".encode(), "text/plain"
    except Exception as exc:
        return 502, f"camera unreachable: {exc.__class__.__name__}\n".encode(), "text/plain"


def post_authed(ip: str, path: str, payload: dict) -> tuple[int, bytes, str]:
    password = os.environ.get("WITNESS_CAMERA_PASSWORD", "")
    if not password:
        return 503, b"WITNESS_CAMERA_PASSWORD is not set\n", "text/plain"
    req = urllib.request.Request(f"http://{ip}{path}", data=json.dumps(payload).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + password)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, resp.read(8192), resp.headers.get("Content-Type", "application/json")
    except urllib.error.HTTPError as exc:
        return 502, f"camera returned HTTP {exc.code}\n".encode(), "text/plain"
    except Exception as exc:
        return 502, f"camera unreachable: {exc.__class__.__name__}\n".encode(), "text/plain"


class QueryError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def model_key() -> str:
    env = os.environ.get("WITNESS_MODEL_KEY", "").strip()
    if env:
        return env
    path = REPO / "openai_api_key.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def mcp_call(ip: str, name: str, arguments: dict) -> str:
    password = os.environ.get("WITNESS_CAMERA_PASSWORD", "")
    if not password:
        raise QueryError(503, "WITNESS_CAMERA_PASSWORD is not set")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    req = urllib.request.Request(
        f"http://{ip}/mcp",
        data=json.dumps(payload).encode(),
        method="POST",
    )
    req.add_header("Authorization", "Bearer " + password)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            message = json.loads(resp.read(1_500_000))
    except urllib.error.HTTPError as exc:
        raise QueryError(502, f"camera returned HTTP {exc.code}") from exc
    except Exception as exc:
        raise QueryError(502, f"camera unreachable: {exc.__class__.__name__}") from exc
    result = message.get("result") if isinstance(message, dict) else None
    if not isinstance(result, dict):
        raise QueryError(502, "camera returned an unexpected MCP response")
    if result.get("isError"):
        texts = _mcp_text(result)
        raise QueryError(502, texts or "camera tool failed")
    return _mcp_text(result)


def _mcp_text(result: dict) -> str:
    parts = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


def index_records(text: str) -> tuple[list[dict], bool]:
    try:
        parsed = json.loads(text) if text else []
    except json.JSONDecodeError as exc:
        raise QueryError(502, "camera index was not valid JSON") from exc
    if not isinstance(parsed, list):
        raise QueryError(502, "camera index was not a list")
    rows = []
    for row in parsed:
        if not isinstance(row, dict) or not row.get("t"):
            continue
        watch = row.get("watch") if isinstance(row.get("watch"), dict) else {}
        yes = [
            label
            for label, score in watch.items()
            if isinstance(score, (int, float)) and float(score) >= 0.5
        ]
        rows.append(
            {
                "t": row.get("t"),
                "caption": row.get("caption") or "",
                "objects": row.get("objects") or [],
                "actions": row.get("actions") or [],
                "motion": row.get("motion"),
                "watch": watch,
                "yes": yes,
            }
        )
    rows.sort(key=lambda row: str(row["t"]))
    truncated = len(rows) > INDEX_LIMIT
    if truncated:
        rows = rows[-INDEX_LIMIT:]
    return rows, truncated


def answer_question(question: str, records: list[dict], recordings: str, truncated: bool) -> str:
    key = model_key()
    if not key:
        raise QueryError(503, "WITNESS_MODEL_KEY is not set")
    now = datetime.now(LOCAL_TZ).isoformat(timespec="seconds")
    note = ""
    if truncated:
        note = f"Only the newest {INDEX_LIMIT} keyframes are included.\n"
    prompt = (
        "You answer questions about a single Witness camera using only the keyframe index below.\n"
        "Each record is one keyframe. The camera writes one on the first frame, then about every "
        "1.5 seconds.\n"
        "Record times are UTC. Answer in America/Los_Angeles local time, and say how long ago that was "
        f"relative to the current time, {now}.\n"
        "GPT captions may be empty when GPT indexing is off. That does not mean nothing was seen.\n"
        "The watch field is the primary evidence for noul questions: each key is a label, each value is "
        "a yes-probability from 0 to 1 from on-camera JEV. A value of 0.5 or higher means YES for that "
        "label in that keyframe. The yes array lists those labels explicitly.\n"
        "Match the user's words to watch labels loosely (for example pipette tip, tip present, tip).\n"
        "When asked how many times something was seen, count keyframes where that label is in yes "
        "(or watch >= 0.5). When asked when it was last seen, use the newest such keyframe.\n"
        "When asked how long something was in frame, the duration is only the span from the earliest "
        "yes keyframe to the latest yes keyframe. The next keyframe that is not yes is an upper bound "
        "on when it left, not extra time it was visible.\n"
        "Only say the index has no evidence if no watch label and no caption/object/action matches.\n"
        "Do not invent events. Answer in a few plain sentences. Do not use markdown.\n\n"
        f"{note}Question: {question}\n\n"
        f"Index records:\n{json.dumps(records)}\n\n"
        f"Recording minutes:\n{recordings[:4000]}"
    )
    payload = {
        "model": MODEL_NAME,
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        method="POST",
    )
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read(1_000_000))
    except urllib.error.HTTPError as exc:
        raise QueryError(502, f"model returned HTTP {exc.code}") from exc
    except Exception as exc:
        raise QueryError(502, f"model unreachable: {exc.__class__.__name__}") from exc
    try:
        text = body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError, TypeError) as exc:
        raise QueryError(502, "model returned no answer") from exc
    if not text:
        raise QueryError(502, "model returned no answer")
    return text


def answer_from_history(question: str, summary: dict, recordings: str) -> str:
    """Ask the model about the registrar's copy of the index, not the camera."""
    key = model_key()
    if not key:
        raise QueryError(503, "WITNESS_MODEL_KEY is not set")
    now = datetime.now(LOCAL_TZ).isoformat(timespec="seconds")
    prompt = (
        "You answer questions about a single Witness camera using only the history below.\n"
        "The history is the camera's keyframe index, copied onto this computer. It can cover "
        "several days. It is not a live card scan.\n"
        "Each keyframe is about 1.5 seconds apart. Times in the history are UTC. Answer in "
        f"America/Los_Angeles local time, and say how long ago that was relative to {now}.\n"
        "labels maps a watch label to its evidence. yes_frames is how many keyframes scored "
        "0.5 or higher. episodes is how many separate sightings that was, split when the label "
        "was absent for more than 5 seconds. latest_episodes lists the newest of those sightings. "
        "by_hour counts yes keyframes per UTC hour.\n"
        "recent lists the newest keyframes with their yes labels and any caption.\n"
        "Match the user's words to watch labels loosely (for example pipette tip, tip present, tip).\n"
        "When asked how many times something was seen, use episodes for separate sightings and "
        "yes_frames when they ask how many keyframes. When asked when it was last seen, use last.\n"
        "When asked how long something was in frame, use the span of the matching episode. "
        "Do not add the gap after it left.\n"
        "Only say the index has no evidence if no label, recent caption, or hour count matches.\n"
        "If complete is false, the copy has not reached the newest keyframe yet. Answer from "
        "what is present. Do not treat a time after the history as time you have already seen.\n"
        "Do not invent events. Answer in a few plain sentences. Do not use markdown.\n\n"
        f"Question: {question}\n\n"
        f"History:\n{json.dumps(summary)}\n\n"
        f"Recording minutes:\n{recordings[:4000]}"
    )
    payload = {
        "model": MODEL_NAME,
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        method="POST",
    )
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read(1_000_000))
    except urllib.error.HTTPError as exc:
        raise QueryError(502, f"model returned HTTP {exc.code}") from exc
    except Exception as exc:
        raise QueryError(502, f"model unreachable: {exc.__class__.__name__}") from exc
    try:
        text = body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError, TypeError) as exc:
        raise QueryError(502, "model returned no answer") from exc
    if not text:
        raise QueryError(502, "model returned no answer")
    return text


def copy_latest(ip: str, camera_id: str, store: IndexStore) -> int:
    status, body, _ctype = fetch_authed(ip, "/api/v1/index/latest", 8192)
    if status != 200 or not body:
        return 0
    return store.insert_lines(camera_id, [body])


def copy_recent(ip: str, camera_id: str, store: IndexStore) -> int:
    """Copy the camera's in-memory recent index. This does not read the card."""
    return store.insert_lines(camera_id, _index_lines(mcp_call(ip, "search_index", {"query": ""})))


def backfill_step(ip: str, camera_id: str, store: IndexStore) -> tuple[int, bool, int]:
    """Read one short page of the on-card index into the local copy."""
    cursor, _done = store.cursor(camera_id)
    text = mcp_call(ip, "read_index_page", {"cursor": cursor, "limit": 40})
    try:
        page = json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise QueryError(502, "index page was not valid JSON") from exc
    if not isinstance(page, dict):
        raise QueryError(502, "index page was not an object")
    lines = page.get("lines") if isinstance(page.get("lines"), list) else []
    added = store.insert_lines(camera_id, [line for line in lines if isinstance(line, str)])
    done = bool(page.get("done"))
    next_cursor = str(page.get("next") or "")
    if done and not next_cursor:
        next_cursor = cursor
    store.set_cursor(camera_id, next_cursor, done)
    return added, done, len(lines)


def _index_lines(text: str) -> list[str]:
    try:
        parsed = json.loads(text) if text else []
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [json.dumps(row) for row in parsed if isinstance(row, dict) and row.get("t")]


def start_index_sync(registry: Registry, store: IndexStore) -> threading.Event:
    """Copy new index rows in the background. Questions never start this work."""
    stop = threading.Event()
    state = {"recent_at": 0.0, "backfill_at": 0.0, "pages": 0, "latest_warned": False, "idle": False}

    def loop() -> None:
        while not stop.wait(1.0):
            now = time.time()
            cameras = [item for item in registry.list() if item.get("ip") and item.get("id")]
            for camera in cameras:
                if not store.backfill_done(camera["id"]):
                    continue
                try:
                    copy_latest(camera["ip"], camera["id"], store)
                    state["latest_warned"] = False
                except Exception as exc:
                    if not state["latest_warned"]:
                        print(
                            f"index_sync=latest_fail camera={camera['id']} err={type(exc).__name__}",
                            flush=True,
                        )
                        state["latest_warned"] = True
            if now >= state["recent_at"]:
                state["recent_at"] = now + 10
                for camera in cameras:
                    if not store.backfill_done(camera["id"]):
                        continue
                    try:
                        added = copy_recent(camera["ip"], camera["id"], store)
                        if added:
                            print(f"index_sync=recent camera={camera['id']} added={added}", flush=True)
                    except Exception as exc:
                        print(
                            f"index_sync=recent_fail camera={camera['id']} err={type(exc).__name__}",
                            flush=True,
                        )
            if now < state["backfill_at"] or not cameras:
                continue
            camera = cameras[0]
            try:
                added, done, nlines = backfill_step(camera["ip"], camera["id"], store)
            except Exception as exc:
                print(
                    f"index_backfill=fail camera={camera['id']} err={type(exc).__name__}",
                    flush=True,
                )
                state["backfill_at"] = now + 60
                continue
            state["pages"] += 1
            if nlines == 0 and done:
                state["backfill_at"] = now + 30
            else:
                state["backfill_at"] = now + 1
            if done and not state["idle"]:
                print(f"index_backfill=done camera={camera['id']} records={store.count(camera['id'])}", flush=True)
                state["idle"] = True
            elif not done:
                state["idle"] = False
                if added and state["pages"] % 25 == 0:
                    print(
                        f"index_backfill camera={camera['id']} added={added} records={store.count(camera['id'])}",
                        flush=True,
                    )

    threading.Thread(target=loop, name="index-sync", daemon=True).start()
    return stop


def load_index(ip: str) -> tuple[list[dict], bool]:
    """Pull the camera's recent index rows for Ask and charts.

    One search. The camera keeps these rows in RAM; a second pass, or a pass
    over the card, held its only HTTP task and the live view went offline.
    """
    try:
        rows, truncated = index_records(mcp_call(ip, "search_index", {"query": ""}))
    except QueryError:
        return [], False
    if len(rows) > INDEX_LIMIT:
        rows = rows[-INDEX_LIMIT:]
        truncated = True
    return rows, truncated


def run_query(ip: str, question: str, camera_id: str | None = None, store: IndexStore | None = None) -> str:
    """Answer from the local copy as soon as it has rows.

    Until the first page lands, use the camera's memory index. The copy is a
    prefix of the card, so a question during the copy covers everything copied
    so far and does not itself read the card.
    """
    if camera_id and store is not None:
        summary = summarize_lines(store.lines(camera_id))
        if summary["keyframes"]:
            summary["complete"] = store.backfill_done(camera_id)
            try:
                recordings = mcp_call(ip, "list_recordings", {"from": "-30m", "to": "-0s"})
            except QueryError:
                recordings = ""
            return answer_from_history(question, summary, recordings)
    records, truncated = load_index(ip)
    try:
        recordings = mcp_call(ip, "list_recordings", {"from": "-30m", "to": "-0s"})
    except QueryError:
        recordings = ""
    return answer_question(question, records, recordings, truncated)


def make_handler(
    registry: Registry,
    dev_state: DevState | None = None,
    high_perf_state: HighPerfState | None = None,
):
    state = dev_state or DevState()
    perf_state = high_perf_state or HighPerfState()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def handle_error(self) -> None:
            exc = sys.exception()
            if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                return
            super().handle_error()

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _dev_payload(self) -> dict:
            enabled, last = state.snapshot()
            return {
                "enabled": enabled,
                "idle_s": DEV_IDLE_S,
                "last_activity": last.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                return
            if path == "/api/dev-mode":
                self._json(200, self._dev_payload())
                return
            if path == "/api/high-perf":
                self._json(
                    200,
                    {
                        "enabled": perf_state.snapshot(),
                        "poll_ms": POLL_MS_HIGH_PERF if perf_state.snapshot() else POLL_MS_DEFAULT,
                    },
                )
                return
            if path == "/api/cameras":
                self._json(200, {"cameras": registry.list()})
                return
            prefix = "/api/cameras/"
            suffix = "/frame.jpg"
            if path.startswith(prefix) and path.endswith(suffix):
                camera_id = path[len(prefix) : -len(suffix)]
                camera = registry.get(camera_id)
                if camera is None:
                    self._send(404, b"unknown camera\n", "text/plain")
                    return
                status, body, ctype = fetch_frame(camera["ip"])
                if status == 200:
                    registry.note_view(camera_id, "")
                else:
                    registry.note_view(camera_id, body.decode().strip())
                self._send(status, body, ctype)
                return
            if path.startswith(prefix) and path.endswith("/index"):
                camera_id = path[len(prefix) : -len("/index")]
                camera = registry.get(camera_id)
                if camera is None:
                    self._send(404, b"unknown camera\n", "text/plain")
                    return
                try:
                    records, truncated = load_index(camera["ip"])
                except QueryError as exc:
                    self._json(exc.status, {"error": str(exc)})
                    return
                self._json(200, {"records": records, "truncated": truncated})
                return
            caption_suffix = "/caption"
            if path.startswith(prefix) and path.endswith(caption_suffix):
                camera_id = path[len(prefix) : -len(caption_suffix)]
                camera = registry.get(camera_id)
                if camera is None:
                    self._send(404, b"unknown camera\n", "text/plain")
                    return
                status, body, ctype = fetch_authed(camera["ip"], "/api/v1/index/latest", 8192)
                if status == 204:
                    self._send(204, b"", "application/json")
                    return
                self._send(status, body, ctype if status == 200 else "text/plain")
                return
            if path.startswith(prefix) and path.endswith("/indexing"):
                self._indexing_get(path[len(prefix) : -len("/indexing")])
                return
            if path.startswith(prefix) and path.endswith("/perf"):
                self._perf_get(path[len(prefix) : -len("/perf")])
                return
            if path.startswith(prefix) and path.endswith("/jev"):
                self._jev_get(path[len(prefix) : -len("/jev")])
                return
            example = EXAMPLE_RE.match(path)
            if example:
                self._example_get(example.group("camera"), example.group("name"), example.group("example"))
                return
            if path.startswith(prefix) and path.endswith("/nouls"):
                self._nouls_get(path[len(prefix) : -len("/nouls")])
                return
            self._send(404, b"not found\n", "text/plain")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/dev-mode":
                self._dev_mode_post()
                return
            if path == "/api/high-perf":
                self._high_perf_post()
                return
            if path == "/api/activity":
                state.touch()
                self._json(200, {"ok": True})
                return
            if path == "/api/dev-idle":
                stopped = disable_all_indexing(registry)
                state.touch()
                self._json(200, {"ok": True, "stopped": stopped})
                return
            prefix = "/api/cameras/"
            if path.startswith(prefix) and path.endswith("/query"):
                state.touch()
                self._query(path[len(prefix) : -len("/query")])
                return
            if path.startswith(prefix) and path.endswith("/indexing"):
                state.touch()
                self._indexing_post(path[len(prefix) : -len("/indexing")])
                return
            if path.startswith(prefix) and path.endswith("/perf"):
                state.touch()
                self._perf_post(path[len(prefix) : -len("/perf")])
                return
            if path.startswith(prefix) and path.endswith("/jev"):
                state.touch()
                self._jev_post(path[len(prefix) : -len("/jev")])
                return
            if path != "/api/register":
                self._send(404, b"not found\n", "text/plain")
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                self._json(400, {"ok": False, "error": "body must be 1 to 4096 bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._json(400, {"ok": False, "error": "invalid json"})
                return
            if not isinstance(body, dict):
                self._json(400, {"ok": False, "error": "json object required"})
                return
            camera_id = body.get("id", "")
            if not isinstance(camera_id, str) or not ID_RE.match(camera_id):
                self._json(400, {"ok": False, "error": "invalid id"})
                return
            peer = self.client_address[0]
            if not acceptable_ip(peer):
                self._json(403, {"ok": False, "error": "peer address is not a local address"})
                return
            stated = body.get("ip")
            if stated is not None and stated != peer:
                self._json(400, {"ok": False, "error": "ip does not match the connection"})
                return
            fw = body.get("fw") or ""
            board = body.get("board") or ""
            if not isinstance(fw, str) or not isinstance(board, str):
                self._json(400, {"ok": False, "error": "fw and board must be strings"})
                return
            saved = registry.upsert(camera_id, peer, fw[:32], board[:64])
            self._json(200, {"ok": True, "id": saved["id"], "ip": saved["ip"]})

        def _dev_mode_post(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > 256:
                self._json(400, {"error": "body must be 1 to 256 bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            enabled = body.get("enabled") if isinstance(body, dict) else None
            if not isinstance(enabled, bool):
                self._json(400, {"error": "enabled must be a boolean"})
                return
            state.set_enabled(enabled)
            payload = self._dev_payload()
            self._json(200, payload)

        def _high_perf_post(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > 256:
                self._json(400, {"error": "body must be 1 to 256 bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            enabled = body.get("enabled") if isinstance(body, dict) else None
            if not isinstance(enabled, bool):
                self._json(400, {"error": "enabled must be a boolean"})
                return
            perf_state.set_enabled(enabled)
            updated = set_cameras_perf(registry, enabled)
            self._json(
                200,
                {
                    "enabled": perf_state.snapshot(),
                    "poll_ms": POLL_MS_HIGH_PERF if enabled else POLL_MS_DEFAULT,
                    "cameras_updated": updated,
                },
            )

        def _indexing_get(self, camera_id: str) -> None:
            camera = registry.get(camera_id)
            if camera is None:
                self._json(404, {"error": "unknown camera"})
                return
            status, body, ctype = fetch_authed(camera["ip"], "/api/v1/indexing", 8192)
            self._send(status, body, ctype if status == 200 else "text/plain")

        def _indexing_post(self, camera_id: str) -> None:
            camera = registry.get(camera_id)
            if camera is None:
                self._json(404, {"error": "unknown camera"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > 256:
                self._json(400, {"error": "body must be 1 to 256 bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            enabled = body.get("enabled") if isinstance(body, dict) else None
            if not isinstance(enabled, bool):
                self._json(400, {"error": "enabled must be a boolean"})
                return
            status, raw, ctype = post_authed(camera["ip"], "/api/v1/indexing", {"enabled": enabled})
            self._send_switch(status, raw, ctype, enabled)

        def _perf_get(self, camera_id: str) -> None:
            camera = registry.get(camera_id)
            if camera is None:
                self._json(404, {"error": "unknown camera"})
                return
            status, body, ctype = fetch_authed(camera["ip"], "/api/v1/perf", 8192)
            self._send(status, body, ctype if status == 200 else "text/plain")

        def _perf_post(self, camera_id: str) -> None:
            camera = registry.get(camera_id)
            if camera is None:
                self._json(404, {"error": "unknown camera"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > 256:
                self._json(400, {"error": "body must be 1 to 256 bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            enabled = body.get("enabled") if isinstance(body, dict) else None
            if not isinstance(enabled, bool):
                self._json(400, {"error": "enabled must be a boolean"})
                return
            status, raw, ctype = post_authed(camera["ip"], "/api/v1/perf", {"enabled": enabled})
            self._send(status, raw, ctype if status == 200 else "text/plain")

        def _jev_get(self, camera_id: str) -> None:
            camera = registry.get(camera_id)
            if camera is None:
                self._json(404, {"error": "unknown camera"})
                return
            status, body, ctype = fetch_authed(camera["ip"], "/api/v1/jev", 8192)
            self._send(status, body, ctype if status == 200 else "text/plain")

        def _jev_post(self, camera_id: str) -> None:
            camera = registry.get(camera_id)
            if camera is None:
                self._json(404, {"error": "unknown camera"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > 256:
                self._json(400, {"error": "body must be 1 to 256 bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            enabled = body.get("enabled") if isinstance(body, dict) else None
            if not isinstance(enabled, bool):
                self._json(400, {"error": "enabled must be a boolean"})
                return
            status, raw, ctype = post_authed(camera["ip"], "/api/v1/jev", {"enabled": enabled})
            self._send_switch(status, raw, ctype, enabled)

        def _send_switch(self, status: int, raw: bytes, ctype: str, enabled: bool) -> None:
            if status == 200 and ctype.startswith("application/json"):
                self._send(status, raw, ctype)
                return
            detail = raw.decode("utf-8", "replace").strip()[:200] or f"camera returned HTTP {status}"
            self._json(status if status >= 400 else 502, {"error": detail, "enabled": False})

        def _nouls_get(self, camera_id: str) -> None:
            camera = registry.get(camera_id)
            if camera is None:
                self._json(404, {"error": "unknown camera"})
                return
            status, body, ctype = fetch_authed(camera["ip"], "/api/v1/nouls", 65536)
            self._send(status, body, ctype if status == 200 else "text/plain")

        def _example_get(self, camera_id: str, name: str, example: str) -> None:
            camera = registry.get(camera_id)
            if camera is None:
                self._send(404, b"unknown camera\n", "text/plain")
                return
            status, body, ctype = fetch_authed(
                camera["ip"], f"/api/v1/nouls/{name}/examples/{example}", EXAMPLE_MAX_BYTES + 1024
            )
            self._send(status, body, ctype if status == 200 else "text/plain")

        def do_PUT(self) -> None:
            path = urlparse(self.path).path
            example = EXAMPLE_RE.match(path)
            if example:
                self._example_put(example.group("camera"), example.group("name"), example.group("example"))
                return
            prefix = "/api/cameras/"
            if path.startswith(prefix) and path.endswith("/nouls"):
                self._nouls_put(path[len(prefix) : -len("/nouls")])
                return
            self._send(404, b"not found\n", "text/plain")

        def do_DELETE(self) -> None:
            path = urlparse(self.path).path
            prefix = "/api/cameras/"
            if path.startswith(prefix) and path.count("/") == 3:
                camera_id = path[len(prefix) :]
                if not ID_RE.match(camera_id):
                    self._json(400, {"error": "bad camera id"})
                    return
                if not registry.remove(camera_id):
                    self._json(404, {"error": "unknown camera"})
                    return
                self._json(200, {"ok": True, "id": camera_id})
                return
            self._send(404, b"not found\n", "text/plain")

        def _nouls_put(self, camera_id: str) -> None:
            camera = registry.get(camera_id)
            if camera is None:
                self._json(404, {"error": "unknown camera"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > 65536:
                self._json(400, {"error": "body must be 1 to 65536 bytes"})
                return
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            if not isinstance(parsed, list):
                self._json(400, {"error": "json array required"})
                return
            status, body, ctype = put_authed(camera["ip"], "/api/v1/nouls", raw, "application/json", 4096)
            self._send(status, body, ctype if status == 200 else "text/plain")

        def _example_put(self, camera_id: str, name: str, example: str) -> None:
            camera = registry.get(camera_id)
            if camera is None:
                self._send(404, b"unknown camera\n", "text/plain")
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > UPLOAD_MAX_BYTES:
                self._send(400, b"example image is too large\n", "text/plain")
                return
            raw = self.rfile.read(length)
            try:
                jpeg = shrink_jpeg(raw)
            except Exception:
                self._send(400, b"example image could not be prepared\n", "text/plain")
                return
            status, body, ctype = put_authed(
                camera["ip"],
                f"/api/v1/nouls/{name}/examples/{example}",
                jpeg,
                "image/jpeg",
                4096,
            )
            self._send(status, body, ctype if status == 200 else "text/plain")

        def _query(self, camera_id: str) -> None:
            camera = registry.get(camera_id)
            if camera is None:
                self._json(404, {"error": "unknown camera"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > 8192:
                self._json(400, {"error": "body must be 1 to 8192 bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            text = body.get("text") if isinstance(body, dict) else None
            if not isinstance(text, str) or not text.strip() or len(text) > 500:
                self._json(400, {"error": "text must be 1 to 500 characters"})
                return
            try:
                answer = run_query(camera["ip"], text.strip(), camera_id, INDEX_STORE)
            except QueryError as exc:
                self._json(exc.status, {"error": exc.message})
                return
            self._json(200, {"answer": answer})

    return Handler


def start_idle_watchdog(registry: Registry, state: DevState, interval_s: float = 15.0) -> threading.Event:
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval_s):
            if maybe_idle_stop(registry, state):
                print("dev_mode=idle indexing=off", flush=True)

    threading.Thread(target=loop, name="dev-idle-watchdog", daemon=True).start()
    return stop


def serve(
    host: str,
    port: int,
    registry: Registry,
    dev_state: DevState | None = None,
    high_perf_state: HighPerfState | None = None,
) -> ThreadingHTTPServer:
    state = dev_state or DevState()
    perf = high_perf_state or HighPerfState()
    stopped = disable_all_indexing(registry)
    print(f"indexing=off cameras={stopped}", flush=True)
    print(f"dev_mode={'on' if state.enabled else 'off'} idle_s={DEV_IDLE_S}", flush=True)
    print(
        f"high_perf={'on' if perf.enabled else 'off'} poll_ms="
        f"{POLL_MS_HIGH_PERF if perf.enabled else POLL_MS_DEFAULT}",
        flush=True,
    )
    global INDEX_STORE
    INDEX_STORE = IndexStore(INDEX_DB)
    print(f"index_db={INDEX_DB}", flush=True)
    start_index_sync(registry, INDEX_STORE)
    if perf.enabled:
        set_cameras_perf(registry, True)
    start_idle_watchdog(registry, state)
    httpd = ThreadingHTTPServer((host, port), make_handler(registry, state, perf))
    httpd.serve_forever()
    return httpd


def check_profile() -> None:
    profile = json.loads(PROFILE.read_text())
    schema = json.loads(HOST_SCHEMA.read_text())
    missing = [key for key in schema["required"] if key not in profile]
    if missing:
        raise SystemExit(f"profile missing {missing}")
    if profile["python"]["use"]["meets_guide"] is not True:
        raise SystemExit("profile python.use must meet the 3.10 guide")
    if profile["secrets"]["wifi_password_env"] != "WITNESS_WIFI_PASSWORD":
        raise SystemExit("wifi password must be an env var name")
    blob = json.dumps(profile).lower()
    for banned in ("password", "token", "api_key"):
        if f'"{banned}"' in blob:
            raise SystemExit(f"profile contains a secret field: {banned}")


def run_check() -> int:
    check_profile()
    registry = Registry(RUN / "cameras-check.json")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(registry))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/register",
            data=json.dumps({"id": "witness-test", "fw": "0.0.0", "board": "foriot_s3cam"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            registered = json.loads(resp.read())
        if registered.get("ip") != "127.0.0.1" or registered.get("ok") is not True:
            raise SystemExit(f"register failed: {registered}")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/cameras", timeout=3) as resp:
            listing = json.loads(resp.read())
        if listing["cameras"][0]["id"] != "witness-test":
            raise SystemExit("camera missing from list")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as resp:
            page = resp.read().decode()
        if (
            "Witness registrar" not in page
            or "/caption" not in page
            or "/index" not in page
            or "Ask a camera" not in page
            or "GPT indexing" not in page
            or "JEV indexing" not in page
            or "JEV nouls" not in page
            or "runs on the camera" not in page
            or "watch-score" not in page
            or "noul-charts" not in page
            or "JEV watch scores" not in page
            or "switch-status" not in page
            or "removeCamera" not in page
            or 'textContent = "Remove"' not in page
            or "High performance" not in page
            or 'id="high-perf"' not in page
            or "POLL_MS_HIGH_PERF" not in page
        ):
            raise SystemExit("page missing title")
        if camera_is_stale({"last_seen": "2000-01-01T00:00:00Z"}) is not True:
            raise SystemExit("stale detection failed")
        if camera_is_stale({"last_seen": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}) is not False:
            raise SystemExit("fresh camera marked stale")
        delete_missing = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/cameras/no-such",
            method="DELETE",
        )
        try:
            urllib.request.urlopen(delete_missing, timeout=3)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise SystemExit(f"unexpected missing-camera delete status {exc.code}")
        else:
            raise SystemExit("unknown camera delete should be 404")
        delete_ok = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/cameras/witness-test",
            method="DELETE",
        )
        with urllib.request.urlopen(delete_ok, timeout=3) as resp:
            deleted = json.loads(resp.read())
        if deleted.get("ok") is not True:
            raise SystemExit(f"delete failed: {deleted}")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/cameras", timeout=3) as resp:
            after = json.loads(resp.read())
        if after.get("cameras"):
            raise SystemExit("camera remained after delete")
        # Re-register for the rest of the checks.
        with urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{port}/api/register",
                data=json.dumps({"id": "witness-test", "fw": "0.0.0", "board": "foriot_s3cam"}).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=3,
        ) as resp:
            json.loads(resp.read())
        sample_rows, _truncated = index_records(
            json.dumps(
                [
                    {
                        "t": "2026-11-03T16:00:00.000Z",
                        "caption": "",
                        "watch": {"pipette tip": 0.91},
                    },
                    {
                        "t": "2026-11-03T16:00:05.000Z",
                        "caption": "",
                        "watch": {"pipette tip": 0.08},
                    },
                ]
            )
        )
        if sample_rows[0]["yes"] != ["pipette tip"] or sample_rows[1]["yes"] != []:
            raise SystemExit("watch yes enrichment failed")
        index_missing = f"http://127.0.0.1:{port}/api/cameras/no-such/index"
        try:
            urllib.request.urlopen(index_missing, timeout=3)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise SystemExit(f"unexpected missing-camera index status {exc.code}")
        else:
            raise SystemExit("unknown camera index should be 404")
        query_missing = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/cameras/no-such/query",
            data=json.dumps({"text": "was a hand in frame?"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(query_missing, timeout=3)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise SystemExit(f"unexpected missing-camera query status {exc.code}")
        else:
            raise SystemExit("unknown camera query should be 404")
        caption_missing = f"http://127.0.0.1:{port}/api/cameras/no-such/caption"
        try:
            urllib.request.urlopen(caption_missing, timeout=3)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise SystemExit(f"unexpected missing-camera caption status {exc.code}")
        else:
            raise SystemExit("unknown camera caption should be 404")
        frame_url = f"http://127.0.0.1:{port}/api/cameras/witness-test/frame.jpg"
        caption_url = f"http://127.0.0.1:{port}/api/cameras/witness-test/caption"
        for url, label in ((frame_url, "frame"), (caption_url, "caption")):
            try:
                urllib.request.urlopen(url, timeout=3)
            except urllib.error.HTTPError as exc:
                if exc.code not in (502, 503):
                    raise SystemExit(f"unexpected {label} status {exc.code}")
            else:
                raise SystemExit(f"{label} fetch should fail without a camera")
        query_url = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/cameras/witness-test/query",
            data=json.dumps({"text": "was a hand in frame?"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(query_url, timeout=45) as resp:
                payload = json.loads(resp.read())
            # Unreachable camera usually 502/503. With a model key, Ask may still
            # answer from an empty index after the MCP fetch fails soft.
            if not isinstance(payload, dict) or not (payload.get("answer") or payload.get("error")):
                raise SystemExit(f"unexpected query payload: {payload}")
        except urllib.error.HTTPError as exc:
            if exc.code not in (502, 503):
                raise SystemExit(f"unexpected query status {exc.code}")
        indexing_missing = f"http://127.0.0.1:{port}/api/cameras/no-such/indexing"
        try:
            urllib.request.urlopen(indexing_missing, timeout=3)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise SystemExit(f"unexpected missing-camera indexing status {exc.code}")
        else:
            raise SystemExit("unknown camera indexing should be 404")
        indexing_url = f"http://127.0.0.1:{port}/api/cameras/witness-test/indexing"
        try:
            urllib.request.urlopen(indexing_url, timeout=3)
        except urllib.error.HTTPError as exc:
            if exc.code not in (502, 503):
                raise SystemExit(f"unexpected indexing status {exc.code}")
        else:
            raise SystemExit("indexing fetch should fail without a camera")
        for missing in (
            f"http://127.0.0.1:{port}/api/cameras/no-such/jev",
            f"http://127.0.0.1:{port}/api/cameras/no-such/nouls",
            f"http://127.0.0.1:{port}/api/cameras/no-such/nouls/tip/examples/yes1",
        ):
            try:
                urllib.request.urlopen(missing, timeout=3)
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise SystemExit(f"unexpected missing-camera status {exc.code} for {missing}")
            else:
                raise SystemExit(f"unknown camera should be 404 for {missing}")
        jev_url = f"http://127.0.0.1:{port}/api/cameras/witness-test/jev"
        nouls_url = f"http://127.0.0.1:{port}/api/cameras/witness-test/nouls"
        for url, label in ((jev_url, "jev"), (nouls_url, "nouls")):
            try:
                urllib.request.urlopen(url, timeout=3)
            except urllib.error.HTTPError as exc:
                if exc.code not in (502, 503):
                    raise SystemExit(f"unexpected {label} status {exc.code}")
            else:
                raise SystemExit(f"{label} fetch should fail without a camera")
    finally:
        httpd.shutdown()
        check_file = RUN / "cameras-check.json"
        if check_file.exists():
            check_file.unlink()
    print("registrar=ok profile=ok")
    return 0


def main() -> int:
    if "--check" in sys.argv:
        return run_check()
    load_local_env()
    registry = Registry(REGISTRY)
    host = "0.0.0.0"
    ip = lan_ip()
    password_state = "set" if os.environ.get("WITNESS_CAMERA_PASSWORD") else "missing"
    model_state = "set" if model_key() else "missing"
    print(f"registrar=http://{ip}:{PORT}/", flush=True)
    print(f"register=POST http://{ip}:{PORT}/api/register", flush=True)
    print(f"camera_password={password_state}", flush=True)
    print(f"model_key={model_state}", flush=True)
    print(f"frame={FRAME_PATH}", flush=True)
    serve(host, PORT, registry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
