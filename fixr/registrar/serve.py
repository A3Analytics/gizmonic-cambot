#!/usr/bin/env python3
"""LAN registrar for a Witness camera.

The camera POSTs /api/register when it joins the network. This page lists
each camera and refreshes its live frame. A query box on this computer calls
the camera MCP for the on-card index, then asks gpt-4.1-mini to answer from
those captions. The camera password and model key are never printed or stored.

  /opt/homebrew/bin/python3.13 fixr/registrar/serve.py
  /opt/homebrew/bin/python3.13 fixr/registrar/serve.py --check
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
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

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
MODEL_NAME = "gpt-4.1-mini"
INDEX_LIMIT = 400
RUN = ROOT / "run"
REGISTRY = RUN / "cameras.json"
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

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Witness registrar</title>
<style>
  body { font: 16px/1.4 ui-sans-serif, system-ui, sans-serif; margin: 2rem; color: #1c1917; background: #fafaf9; }
  h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
  p.note { color: #57534e; margin-top: 0; }
  .card { background: white; border: 1px solid #e7e5e4; border-radius: 8px; padding: 1rem; margin: 1rem 0; max-width: 52rem; }
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
  .frame img { display: block; max-width: 100%; }
  .frame.paused { outline: 2px solid #a8a29e; outline-offset: 2px; }
  .frame-controls { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 0.45rem 0 0; }
  .boxes { position: absolute; left: 0; top: 0; right: 0; bottom: 0; pointer-events: none; }
  .box { position: absolute; border: 3px solid #16a34a; box-sizing: border-box; }
</style>
</head>
<body>
<h1>Witness registrar</h1>
<p class="note" id="hint">Waiting for a camera to POST /api/register.</p>
<section class="card" id="ask">
  <h2>Ask a camera</h2>
  <p class="meta">This computer reads the camera index over MCP, then answers from those captions.</p>
  <label class="meta">Camera <select id="camera"></select></label>
  <div class="switches">
    <label class="meta" for="indexing"><input type="checkbox" id="indexing"> GPT indexing</label>
    <label class="meta" for="jev"><input type="checkbox" id="jev"> JEV indexing</label>
    <p class="meta">JEV indexing runs on the camera. The camera calls TypeSafe. This computer does not.</p>
  </div>
  <div id="transcript"></div>
  <form class="query" id="query-form">
    <input id="question" autocomplete="off" placeholder="How long ago was a hand in the frame, and for how long?">
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
<div id="list"></div>
<script>
const transcript = [];
let indexingBusy = false;
let jevBusy = false;
let noulDraft = [];
let noulCamera = null;
const liveCards = new Map();
const pausedCameras = new Set();

function cameraId() {
  return document.getElementById("camera").value;
}

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
  if (!res.ok) return;
  const data = await res.json().catch(() => null);
  if (!data || indexingBusy) return;
  box.checked = !!data.enabled;
}

document.getElementById("indexing").addEventListener("change", async (event) => {
  const camera = document.getElementById("camera").value;
  const box = event.target;
  if (!camera) {
    box.checked = false;
    return;
  }
  indexingBusy = true;
  const enabled = box.checked;
  try {
    const res = await fetch("/api/cameras/" + encodeURIComponent(camera) + "/indexing", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({enabled}),
    });
    const data = await res.json().catch(() => ({}));
    box.checked = res.ok ? !!data.enabled : !enabled;
  } catch (err) {
    box.checked = !enabled;
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
  if (!res.ok) return;
  const data = await res.json().catch(() => null);
  if (!data || jevBusy) return;
  box.checked = !!data.enabled;
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
  try {
    const res = await fetch("/api/cameras/" + encodeURIComponent(camera) + "/jev", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({enabled}),
    });
    const data = await res.json().catch(() => ({}));
    box.checked = res.ok ? !!data.enabled : !enabled;
  } catch (err) {
    box.checked = !enabled;
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
      setNoulStatus("Example upload failed.");
      return;
    }
    item.examples.push({id, polarity: polarity === "no" ? "no" : "yes"});
    setNoulStatus("Example stored on the camera. Save the noul to keep it in the question.");
    renderNouls();
  } catch (err) {
    setNoulStatus("Example upload failed.");
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
  select.replaceChildren(...cameras.map((cam) => {
    const option = document.createElement("option");
    option.value = cam.id;
    option.textContent = cam.id;
    return option;
  }));
  if (cameras.some((cam) => cam.id === current)) select.value = current;
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
  controls.append(pauseBtn, downloadBtn);
  const caption = document.createElement("p");
  caption.className = "meta";
  const err = document.createElement("p");
  err.className = "err";
  section.append(title, meta, frame, controls, caption, err);
  card = {
    section, meta, frame, img, boxes, caption, err, pauseBtn,
    loadToken: 0, frameBlob: null, objectUrl: null,
  };
  liveCards.set(cam.id, card);
  return card;
}

function updateCaption(card, camera) {
  if (pausedCameras.has(camera)) return;
  fetch("/api/cameras/" + encodeURIComponent(camera) + "/caption")
    .then((res) => res.status === 200 ? res.json() : null)
    .then((row) => {
      if (!row || pausedCameras.has(camera)) return;
      if (row.caption) {
        const objects = Array.isArray(row.objects) && row.objects.length ? " (" + row.objects.join(", ") + ")" : "";
        card.caption.textContent = (row.t || "") + " — " + row.caption + objects;
      } else if (row.t && row.watch && typeof row.watch === "object") {
        const parts = Object.keys(row.watch).map((label) => label + " " + Number(row.watch[label]).toFixed(2));
        if (parts.length) card.caption.textContent = row.t + " — " + parts.join(", ");
      }
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

async function load() {
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
  hint.textContent = data.cameras.length + " camera" + (data.cameras.length === 1 ? "" : "s") + " on the LAN.";
  syncCameras(data.cameras);
  syncIndexing(cameraId());
  syncJev(cameraId());
  loadNouls(cameraId());
  const stamp = Date.now();
  const seen = new Set();
  const cards = data.cameras.map((cam) => {
    seen.add(cam.id);
    const card = ensureCard(cam);
    card.meta.textContent = cam.ip + " · last seen " + cam.last_seen + (cam.fw ? " · fw " + cam.fw : "");
    card.err.textContent = cam.last_view_error || "";
    card.frame.classList.toggle("paused", pausedCameras.has(cam.id));
    card.pauseBtn.textContent = pausedCameras.has(cam.id) ? "Resume" : "Pause";
    refreshFrame(card, cam.id, stamp);
    updateCaption(card, cam.id);
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
}
load();
setInterval(load, 2000);
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
    image = Image.open(BytesIO(data))
    image = image.convert("RGB")
    image.thumbnail((320, 320))
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
        rows.append(
            {
                "t": row.get("t"),
                "caption": row.get("caption") or "",
                "objects": row.get("objects") or [],
                "actions": row.get("actions") or [],
                "motion": row.get("motion"),
                "watch": row.get("watch") if isinstance(row.get("watch"), dict) else {},
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
        "Each record is one keyframe. The camera writes one on the first frame, then every 3 seconds "
        "while the scene is moving, and about once a minute when it is still.\n"
        "Record times are UTC. Answer in America/Los_Angeles local time, and say how long ago that was "
        f"relative to the current time, {now}.\n"
        "When asked how long something was in frame, the duration is only the span from the earliest "
        "keyframe that shows it to the latest keyframe that shows it. The next keyframe that does not "
        "show it is an upper bound on when it left, not extra time it was visible.\n"
        "The watch field holds on-camera JEV yes-probabilities from 0 to 1. "
        "A value of 0.5 or higher means yes. Use those scores the same way as captions "
        "when deciding when something was present or absent.\n"
        "If the index does not mention it, say so. Do not invent events.\n"
        "Answer in a few plain sentences. Do not use markdown.\n\n"
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


def run_query(ip: str, question: str) -> str:
    records, truncated = index_records(mcp_call(ip, "search_index", {"query": ""}))
    try:
        recordings = mcp_call(ip, "list_recordings", {"from": "-6h", "to": "-0s"})
    except QueryError:
        recordings = ""
    return answer_question(question, records, recordings, truncated)


def make_handler(registry: Registry):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
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
            prefix = "/api/cameras/"
            if path.startswith(prefix) and path.endswith("/query"):
                self._query(path[len(prefix) : -len("/query")])
                return
            if path.startswith(prefix) and path.endswith("/indexing"):
                self._indexing_post(path[len(prefix) : -len("/indexing")])
                return
            if path.startswith(prefix) and path.endswith("/jev"):
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
            self._send(status, raw, ctype if status == 200 else "text/plain")

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
                answer = run_query(camera["ip"], text.strip())
            except QueryError as exc:
                self._json(exc.status, {"error": exc.message})
                return
            self._json(200, {"answer": answer})

    return Handler


def serve(host: str, port: int, registry: Registry) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(registry))
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
            or "Ask a camera" not in page
            or "GPT indexing" not in page
            or "JEV indexing" not in page
            or "JEV nouls" not in page
            or "runs on the camera" not in page
        ):
            raise SystemExit("page missing title")
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
            urllib.request.urlopen(query_url, timeout=3)
        except urllib.error.HTTPError as exc:
            if exc.code not in (502, 503):
                raise SystemExit(f"unexpected query status {exc.code}")
        else:
            raise SystemExit("query should fail without a camera")
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
