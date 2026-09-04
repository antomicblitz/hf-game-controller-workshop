"""Single-page 2D layout and 3D review editor frontend.

The page is intentionally inline: the workshop server serves one small page,
while the manifest remains the source of truth for the case-local layout.
"""

from __future__ import annotations

MINIMAL_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>HF Game Controller — Case Editor</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; }
    body { margin: 0; padding: 1rem; background: #f5f5f5; color: #172033; }
    h1 { margin: 0 0 .75rem; font-size: 1.2rem; }
     main { display: grid; grid-template-columns: minmax(0, 1fr) 340px;
            gap: 1rem; align-items: start; height: calc(100vh - 4rem);
            min-height: 0; }
     #stage { position: relative; width: 100%; aspect-ratio: 13 / 9;
              min-height: 420px; max-height: calc(100vh - 4rem);
              background: #fff; border-radius: 8px; overflow: hidden; }
    #viewer, #layout-svg, #annotation-overlay { position: absolute; inset: 0;
             width: 100%; height: 100%; }
     #viewer { visibility: hidden; opacity: 0; pointer-events: none;
                background: #fff; }
     #layout-svg { display: block; }
    #annotation-overlay { z-index: 4; pointer-events: none; }
    #annotation-overlay.interactive { pointer-events: auto; cursor: crosshair; }
    #annotation-overlay.tool-text { cursor: text; }
     #stage.view-3d #viewer { visibility: visible; opacity: 1; pointer-events: auto; }
     #stage.view-3d #layout-svg { visibility: hidden; pointer-events: none; }
     #shell-view-toggle, #comparison-toggle { position: absolute; right: 8px; z-index: 5;
       display: none; border-radius: 5px; overflow: hidden;
       box-shadow: 0 1px 4px rgba(15, 23, 42, .24); }
     #shell-view-toggle { top: 8px; }
     #comparison-toggle { top: 46px; }
     #stage.view-3d #shell-view-toggle { display: flex; }
     #stage.view-3d #comparison-toggle:not([hidden]) { display: flex; }
     #shell-view-toggle button, #comparison-toggle button {
       padding: .4rem .65rem; border: 0; background: #fff;
       color: #172033; cursor: pointer; font: 500 11px/1.4 system-ui, sans-serif; }
     #shell-view-toggle button + button, #comparison-toggle button + button {
       border-left: 1px solid #94a3b8; }
     #shell-view-toggle button[aria-pressed="true"],
     #comparison-toggle button[aria-pressed="true"] { background: #1d4ed8; color: #fff; }
     .view-label { position: absolute; top: 8px; left: 8px; z-index: 5;
      background: rgba(20, 20, 20, .72); color: #fff; padding: 4px 10px;
      border-radius: 4px; font: 500 11px/1.4 system-ui, sans-serif;
      pointer-events: none; }
    #generation-progress { position: absolute; inset: 0; z-index: 8; display: grid;
      place-content: center; justify-items: center; gap: .7rem; text-align: center;
      background: rgba(15, 23, 42, .74); color: #fff; padding: 1rem; }
    #generation-progress[hidden] { display: none; }
    .generation-spinner { width: 2.25rem; height: 2.25rem; border-radius: 50%;
      border: .3rem solid rgba(255, 255, 255, .3); border-top-color: #fff;
      animation: generation-spin .8s linear infinite; }
    #generation-progress strong, #generation-progress span { display: block; }
    #generation-progress span { margin-top: .25rem; font-size: .85rem; color: #dbeafe; }
    #stage[aria-busy="true"] #viewer, #stage[aria-busy="true"] #layout-svg {
      opacity: .55; }
    @keyframes generation-spin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .generation-spinner { animation: none; } }
     #layout-svg { shape-rendering: geometricPrecision; }
     .case-outline { fill: #d6dde8; stroke: #26364f; stroke-width: 1.2; }
     .case-outline.selected { stroke: #2563eb; stroke-width: 2; }
     .layout-shell-envelope { fill: #94a3b8; fill-opacity: .18; stroke: #1e293b;
       stroke-width: .9; }
     .layout-bottom-shell { fill: #64748b; fill-opacity: .6; stroke: #334155;
       stroke-width: 1; }
     .layout-top-shell { fill: url(#top-shell-fill); fill-opacity: .28; stroke: #2563eb;
       stroke-width: 1.1; filter: url(#shell-shadow); pointer-events: none; }
     .layout-shell-highlight { fill: none; stroke: #bfdbfe; stroke-width: .7;
       stroke-dasharray: 3 1.5; pointer-events: stroke; }
      .layout-pbs-button { fill: #f97316; stroke: #9a3412; stroke-width: .7;
       cursor: grab; }
      .layout-pbs-button.selected { fill: #22c55e; stroke: #166534; stroke-width: 1.2; }
     .layout-button-hit-target { fill: transparent; stroke: none; pointer-events: all; }
     .layout-button-cap { fill: #fb923c; stroke: #7c2d12; stroke-width: .45; }
     .layout-button-ring { fill: none; stroke: #fed7aa; stroke-width: .8; }
      .layout-pbs-button:active { cursor: grabbing; }
     .layout-keepout { fill: #93c5fd; fill-opacity: .16; stroke: #60a5fa;
       stroke-width: .5; stroke-dasharray: 2 1; }
     .layout-feather { fill: #2563eb; fill-opacity: .9; stroke: #172554; stroke-width: .7; }
     .layout-feather-chip { fill: #0f172a; stroke: #64748b; stroke-width: .35; }
     .layout-feather-header { fill: #475569; }
      .layout-feather-hole { fill: #fff; stroke: #1e40af; stroke-width: .35; }
      .layout-breadboard { fill: #f8fafc; stroke: #64748b; stroke-width: .65; }
      .layout-breadboard-field { fill: #fff; stroke: #cbd5e1; stroke-width: .35; }
       .layout-breadboard-hole { fill: #64748b; opacity: .72; }
       .layout-breadboard-contact { stroke: #fff; stroke-width: .35; }
       .layout-breadboard-contact.signal { fill: #dc2626; }
       .layout-breadboard-contact.ground { fill: #111827; }
      .layout-breadboard-rail { fill: none; stroke-width: .55; }
      .layout-breadboard-rail.positive { stroke: #dc2626; }
      .layout-breadboard-rail.negative { stroke: #2563eb; }
     .layout-usb { fill: #1e293b; stroke: #020617; stroke-width: .5; }
     .layout-usb-inner { fill: #cbd5e1; stroke: #475569; stroke-width: .35; }
      .layout-pbs-button { fill: #f97316; stroke: #9a3412; stroke-width: .7; }
      .layout-action-button { fill: #2563eb; stroke: #1e3a8a; stroke-width: .7; }
     .layout-connector { fill: #f59e0b; stroke: #92400e; stroke-width: .6; }
     .layout-fastener { fill: #cbd5e1; stroke: #334155; stroke-width: .45; }
     .layout-heat-shrink { fill: #7c3aed; fill-opacity: .7; stroke: #4c1d95; stroke-width: .5; }
     .layout-strain-relief { fill: #475569; stroke: #1e293b; stroke-width: .6; }
     .layout-wire { fill: none; stroke-width: 1.05; stroke-linejoin: round;
       stroke-linecap: round; opacity: .72; }
     .layout-wire.signal { stroke: #dc2626; }
     .layout-wire.ground { stroke: #111827; }
      .layout-route-label { font: 2.4px system-ui, sans-serif; fill: #172033;
        paint-order: stroke; stroke: #fff; stroke-width: .8; pointer-events: none; }
     .layout-12-header, .layout-16-header { fill: #94a3b8; stroke: #1e293b; stroke-width: .35; }
     .layout-12-header { fill: #a7f3d0; }
     .layout-16-header { fill: #fde68a; }
     .layout-unknown { fill: #fef3c7; stroke: #a16207; stroke-width: .7;
       stroke-dasharray: 2 1; }
     .layout-unknown-mark { stroke: #a16207; stroke-width: .6; fill: none; }
     .layout-label { font: 2.6px system-ui, sans-serif; fill: #172033;
       pointer-events: none; user-select: none; }
    .layout-locked { cursor: pointer; }
     #sidebar { background: #fff; padding: 1rem; border-radius: 8px;
                min-height: 0; max-height: calc(100vh - 4rem); overflow: auto; }
    #mode-toggle { display: flex; gap: .25rem; margin-bottom: .75rem; }
     #mode-toggle button, #toolbar button, .chip, #reset-design, #send { font: inherit; }
    #mode-toggle button { flex: 1; padding: .55rem .4rem; border: 1px solid #94a3b8;
      background: #fff; cursor: pointer; }
    #mode-toggle button:first-child { border-radius: 6px 0 0 6px; }
    #mode-toggle button:last-child { border-radius: 0 6px 6px 0; }
    #mode-toggle button[aria-pressed="true"] { background: #1d4ed8; color: #fff;
      border-color: #1d4ed8; }
    #toolbar { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: 1rem; }
    #toolbar button { padding: .45rem .65rem; cursor: pointer; }
    #toolbar button.active { background: #2563eb; color: #fff; }
    #elements { list-style: none; padding: 0; margin: 0 0 1rem; }
    #elements li { padding: .45rem .5rem; border-radius: 4px; cursor: pointer;
      user-select: none; }
    #elements li.movable { cursor: grab; }
    #elements li.active { background: #fef3c7; }
    #elements li.locked { color: #475569; }
     .quick-actions { display: flex; flex-wrap: wrap; gap: .4rem; margin: 0 0 .5rem; }
     #layer-controls { display: grid; gap: .35rem; margin: 0 0 .5rem; }
     .layer-toggle { display: flex; align-items: center; gap: .4rem; font-size: .8rem;
       color: #334155; }
     .layer-swatch { width: .75rem; height: .75rem; border-radius: 2px; border: 1px solid #64748b; }
     .layer-swatch.bottom { background: #64748b; }
     .layer-swatch.internals { background: #2563eb; }
     .layer-swatch.wiring { background: #dc2626; }
     .layer-swatch.top-shell { background: #93c5fd; opacity: .55; }
     .layer-swatch.top-controls { background: #f97316; }
      .legend { margin: .4rem 0 .8rem; padding: .45rem .6rem; border: 1px solid #cbd5e1;
        border-radius: 5px; font-size: .75rem; color: #475569; }
      #prototype-downloads { display: grid; gap: .4rem; margin: 0 0 1rem; }
      #prototype-downloads a { display: block; padding: .45rem .6rem; border: 1px solid #93c5fd;
        border-radius: 5px; color: #1d4ed8; background: #eff6ff; font-size: .8rem; }
      #prototype-downloads a[aria-disabled="true"] { color: #64748b; background: #f8fafc;
        border-color: #cbd5e1; pointer-events: none; }
      #prototype-stl-note { margin: -.5rem 0 .5rem; color: #64748b; font-size: .72rem; }
     .demo-badge { display: inline-block; margin: 0 0 .8rem; padding: .25rem .5rem;
       border: 1px solid #f59e0b; border-radius: 999px; background: #fffbeb;
       color: #92400e; font-size: .72rem; font-weight: 700; letter-spacing: .02em; }
     .chip { padding: .4rem .8rem; background: #f1f5f9; color: #0f172a;
       border: 1px solid #cbd5e1; border-radius: 999px; cursor: pointer; }
     .chip.active { background: #16a34a; color: #fff; border-color: #15803d; }
     #reset-design { margin: 0 0 .8rem; padding: .45rem .7rem; border: 1px solid #f59e0b;
       border-radius: 6px; background: #fffbeb; color: #92400e; cursor: pointer; }
    .prompt-bar { display: flex; gap: .5rem; margin: 0 0 .5rem; }
    #prompt-input { flex: 1; min-width: 0; padding: .5rem .6rem;
      border: 1px solid #cbd5e1; border-radius: 6px; }
    #send { padding: .5rem 1rem; background: #16a34a; color: #fff; border: 0;
      border-radius: 6px; cursor: pointer; font-weight: 600; }
    pre#status { background: #f0f0f0; padding: .5rem; font-size: .75rem;
      overflow: auto; max-height: 30vh; white-space: pre-wrap; }
    #change-summary { margin: 0 0 .5rem; padding: .55rem .65rem; border-radius: 6px;
      border: 1px solid #86efac; background: #f0fdf4; color: #166534; font-size: .8rem; }
    #change-summary[data-visible-change="false"] { border-color: #fcd34d;
      background: #fffbeb; color: #92400e; }
    #change-summary[hidden] { display: none; }
    .marker-num { font: bold 14px sans-serif; fill: #fff; paint-order: stroke;
      stroke: #000; stroke-width: 3px; stroke-linejoin: round; }
    .marker-line { stroke: #ef4444; stroke-width: 3; fill: none; }
    .marker-circle { stroke: #2563eb; stroke-width: 3; fill: none; }
    .marker-text { font: 13px sans-serif; fill: #111; paint-order: stroke;
      stroke: #fff; stroke-width: 3px; }
     @media (max-width: 800px) {
       main { grid-template-columns: 1fr; height: auto; min-height: 0; }
       #stage { min-height: 0; max-height: none; }
       #sidebar { max-height: none; overflow: visible; }
     }
     @media (any-pointer: coarse) {
       #mode-toggle button, #shell-view-toggle button, #comparison-toggle button,
       #toolbar button, .chip, #reset-design, #send, #prompt-input,
       .layer-toggle, #elements li { min-width: 44px; min-height: 44px; }
       #prompt-input { font-size: 16px; }
       .layer-toggle { box-sizing: border-box; width: 100%; padding: .25rem 0; }
       .layer-toggle input { width: 24px; height: 24px; margin: 0; flex: 0 0 24px; }
       #layout-svg, #annotation-overlay { touch-action: none; user-select: none;
         -webkit-user-select: none; -webkit-touch-callout: none; }
     }
     @media (max-width: 800px) and (any-pointer: coarse) {
       main { gap: 1.25rem; }
       #sidebar { padding: 1rem; }
       #mode-toggle, #toolbar, .quick-actions { gap: .6rem; }
       #elements { margin-bottom: 1.25rem; }
       .prompt-bar { gap: .6rem; }
     }
  </style>
  <script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.5.0/model-viewer.min.js"></script>
</head>
<body>
  <h1>HF Game Controller — Case Editor</h1>
  <main>
     <div id="stage" class="view-2d">
       <model-viewer id="viewer" alt="controller case" loading="eager"
                     interaction-prompt="none" environment-image="neutral"></model-viewer>
       <div id="shell-view-toggle" role="group" aria-label="3D shell appearance">
         <button type="button" data-shell-view="exterior" aria-pressed="true">Exterior</button>
         <button type="button" data-shell-view="xray" aria-pressed="false">X-ray</button>
       </div>
       <div id="comparison-toggle" role="group" aria-label="Compare generated geometry" hidden>
         <button type="button" data-comparison-view="before" aria-pressed="false">Before</button>
         <button type="button" data-comparison-view="after" aria-pressed="true">After</button>
       </div>
       <svg id="layout-svg" role="img" aria-label="Case-local 2D layout"
           xmlns="http://www.w3.org/2000/svg"></svg>
      <svg id="annotation-overlay" aria-label="Screen-space annotations"
           xmlns="http://www.w3.org/2000/svg"></svg>
      <div class="view-label" id="view-label">2D Layout · case-local millimetres</div>
      <div id="generation-progress" role="status" aria-live="polite" hidden>
        <div class="generation-spinner" aria-hidden="true"></div>
        <div><strong id="generation-progress-title">Generating case…</strong>
          <span id="generation-progress-detail">Waiting for model, validation, and render · 0s</span></div>
      </div>
    </div>
     <aside id="sidebar">
       <div id="mode-toggle" role="group" aria-label="Editor view mode">
        <button type="button" data-view-mode="2d" aria-pressed="true">2D Layout</button>
         <button type="button" data-view-mode="3d" aria-pressed="false">3D Review</button>
       </div>
         <div class="demo-badge" id="demo-badge">APPROVED · EXACT-SIX ASSEMBLY</div>
       <div id="toolbar" aria-label="Annotation tools">
        <button type="button" data-tool="select" class="active">Select</button>
        <button type="button" data-tool="arrow">Arrow</button>
        <button type="button" data-tool="circle">Circle</button>
        <button type="button" data-tool="text">Text</button>
        <button type="button" data-tool="delete" id="delete-btn">Delete</button>
        <button type="button" data-tool="undo" id="undo-btn">Undo</button>
         <button type="button" data-tool="clear">Clear</button>
       </div>
       <h2>Layers</h2>
       <div id="layer-controls" aria-label="2D layer visibility"></div>
        <div class="legend" aria-label="2D preview legend">
         <strong>Legend</strong><br>
           X-ray shell · translucent blue &nbsp; Wiring · six Dupont cables with alligator clips<br>
          PBS controls are orange; GUUZI action is blue. Dashed marks are keep-outs.
         </div>
       <h2>Download shell STLs</h2>
       <p id="prototype-stl-note">Downloads are validated and bound to the displayed revision.</p>
       <div id="prototype-downloads" aria-label="Validated shell STL downloads">
         <a id="download-top" aria-disabled="true">Top shell STL unavailable</a>
         <a id="download-bottom" aria-disabled="true">Bottom shell STL unavailable</a>
       </div>
        <h2>Elements</h2>
      <ul id="elements"></ul>
      <h3>Quick changes</h3>
       <div class="quick-actions">
         <button class="chip" data-chip="snes_curve" type="button">SNES curve</button>
          <button class="chip" data-chip="n64_lobes" type="button">N64 lobes</button>
         <button class="chip" data-chip="angular_shell" type="button">Angular shell</button>
       </div>
       <button id="reset-design" type="button">Reset design</button>
       <h3>Or describe your own change</h3>
       <p class="legend">Move controls only by dragging them in 2D Layout. Prompts change the case
         shape and preserve every CONTROLS x/y position.</p>
        <div class="prompt-bar">
         <input id="prompt-input" type="text" placeholder="e.g. round the corners, but keep the control layout">
         <button id="send" type="button">Send →</button>
       </div>
       <div id="change-summary" role="status" aria-live="polite" hidden></div>
      <pre id="status" role="status" aria-live="polite">Loading…</pre>
    </aside>
  </main>
<script>
'use strict';

const CASE_PATH = "examples/6-button-gamepad/case.py";
const MOVE_STORAGE_KEY = `hf-il3-editor-moves:${CASE_PATH}`;
const STAGE = document.getElementById("stage");
const VIEWER = document.getElementById("viewer");
const LAYOUT = document.getElementById("layout-svg");
const OVERLAY = document.getElementById("annotation-overlay");
const STATUS = document.getElementById("status");
const PROMPT_INPUT = document.getElementById("prompt-input");
const SEND = document.getElementById("send");
const RESET_DESIGN = document.getElementById("reset-design");
const GENERATION_PROGRESS = document.getElementById("generation-progress");
const GENERATION_PROGRESS_TITLE = document.getElementById("generation-progress-title");
const GENERATION_PROGRESS_DETAIL = document.getElementById("generation-progress-detail");
const CHANGE_SUMMARY = document.getElementById("change-summary");
const COMPARISON_TOGGLE = document.getElementById("comparison-toggle");
let manifest = null;
let editorRevision = null;
let revisionMoves = [];
let glbB64 = null;
let previousGlbB64 = null;
let snapshotB64 = null;
let viewMode = "2d";
let shellView = "exterior";
let comparisonView = "after";
let activeShellMaterial = null;
let shellXrayColor = null;
let activeTool = "select";
let activeElement = null;
let deleteMode = false;
let nextAnnotationId = 1;
const annotations = [];
const moves = [];
const undoStack = [];
let selectedChip = null;
let arrowStart = null;
let layoutDrag = null;
let moveValidationPending = false;
let moveActionPending = false;
let generationTimer = null;
let generationElapsedSeconds = 0;
let viewerObjectUrl = null;
let activeViewerLoad = null;
const layerVisibility = {
  bottom: true,
  internals: true,
  wiring: true,
  "top-shell": true,
  "top-controls": true,
};
const LAYER_LABELS = {
  bottom: "Bottom shell + mounts",
  internals: "Boards + connectors",
  wiring: "Wiring + protected routes",
  "top-shell": "Top shell (x-ray)",
  "top-controls": "Top controls",
};

async function init() {
  try {
    const snapshot = await fetch("/snapshot", { method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ overlay: "<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        part_path: CASE_PATH, width: 800, height: 600 }) }).then(jsonResponse);
    if (!snapshot.manifest || !snapshot.manifest.assembly ||
        JSON.stringify(snapshot.manifest.assembly) !== JSON.stringify(snapshot.assembly)) {
      throw new Error("Snapshot response is missing its revision-bound assembly manifest.");
    }
    loadModel(snapshot.manifest, snapshot.glb_b64, snapshot.snapshot_b64);
    resetEditorState(false);
    const restoredMoves = loadStoredMoves();
    if (restoredMoves.length) {
      moves.push(...restoredMoves);
      STATUS.textContent = `Validating ${moves.length} saved move(s)…`;
      setMoveValidationPending(true);
      let validation;
      try {
        validation = await validateMovesOnServer(moves);
      } finally {
        setMoveValidationPending(false);
      }
      if (validation.valid === true) {
        applyValidatedMoveModel(validation);
        STATUS.textContent = `Restored ${moves.length} move(s); 2D and 3D refreshed.`;
      } else {
        moves.length = 0;
        persistMoves();
      }
    }
    if (!VIEWER.loaded) {
      STATUS.textContent = `2D ready · 3D loading… (${assemblyItemCount()} assembly items)`;
    }
  } catch (error) {
    STATUS.textContent = error instanceof Error ? error.message : "Sandbox unavailable.";
  }
}

async function jsonResponse(response) {
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 503 && body.status === "sandbox_unavailable") {
      throw new Error("Sandbox unavailable: CAD execution is disabled on this host.");
    }
    throw new Error(body.error || `Request failed (${response.status})`);
  }
  return body;
}

function decodeBase64Bytes(encoded) {
  const binary = window.atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

function beginViewerLoad(encodedGlb) {
  if (activeViewerLoad) {
    activeViewerLoad.cleanup();
    URL.revokeObjectURL(activeViewerLoad.url);
    activeViewerLoad = null;
  }
  const previousUrl = viewerObjectUrl;
  const replacingLoadedModel = Boolean(VIEWER.src && VIEWER.loaded);
  let observedUnloaded = !replacingLoadedModel;
  const blob = new Blob([decodeBase64Bytes(encodedGlb)], { type: "model/gltf-binary" });
  const nextUrl = URL.createObjectURL(blob);
  const record = { url: nextUrl, cleanup: () => {} };
  let reconcileTimer = null;
  const cleanup = () => {
    VIEWER.removeEventListener("load", onLoad);
    VIEWER.removeEventListener("error", onError);
    if (reconcileTimer !== null) window.clearInterval(reconcileTimer);
    reconcileTimer = null;
  };
  const onLoad = () => {
    cleanup();
    if (activeViewerLoad !== record) return;
    activeViewerLoad = null;
    viewerObjectUrl = nextUrl;
    if (previousUrl && previousUrl !== nextUrl) URL.revokeObjectURL(previousUrl);
    applyShellAppearance();
    VIEWER.classList.add("model-ready");
    STATUS.textContent = `Loaded ${assemblyItemCount()} assembly items. ${
      viewMode === "2d" ? "2D ready; switch to 3D Review when needed." :
      "Select to orbit and zoom the assembled GLB."}`;
  };
  const onError = () => {
    cleanup();
    if (activeViewerLoad !== record) return;
    activeViewerLoad = null;
    URL.revokeObjectURL(nextUrl);
    STATUS.textContent = "3D load failed; 2D Layout remains available.";
  };
  record.cleanup = cleanup;
  activeViewerLoad = record;
  VIEWER.classList.remove("model-ready");
  // Register both listeners before assigning src: model-viewer may resolve a
  // Blob URL synchronously once its custom element is already initialized.
  VIEWER.addEventListener("load", onLoad, { once: true });
  VIEWER.addEventListener("error", onError, { once: true });
  VIEWER.src = nextUrl;
  // Reconcile model-viewer builds that update `loaded` without dispatching a
  // DOM load event. This has no timeout or failure shortcut; the normal event
  // remains the primary path and the loop stops on load, error, or replacement.
  const reconcileLoadedState = () => {
    if (activeViewerLoad !== record) return;
    if (!VIEWER.loaded) { observedUnloaded = true; return; }
    if (observedUnloaded) onLoad();
  };
  reconcileTimer = window.setInterval(reconcileLoadedState, 100);
  window.queueMicrotask(reconcileLoadedState);
}

async function loadModel(newManifest, newGlbB64, newSnapshotB64) {
  manifest = newManifest;
  editorRevision = typeof newManifest.editor_revision === "string" ? newManifest.editor_revision : null;
  revisionMoves = [];
  glbB64 = newGlbB64;
  snapshotB64 = newSnapshotB64;
  renderLayerControls();
  renderElementList();
  renderLayout();
  setViewMode(viewMode);
  updatePrototypeDownloads();
  beginViewerLoad(glbB64);
}

function resetEditorState(clearMoves = true) {
  annotations.length = 0;
  if (clearMoves) {
    moves.length = 0;
    persistMoves();
  }
  undoStack.length = 0;
  nextAnnotationId = 1;
  arrowStart = null;
  activeElement = null;
  selectedChip = null;
  PROMPT_INPUT.value = "";
  document.querySelectorAll(".chip.active").forEach(b => b.classList.remove("active"));
  renderElementList();
  renderLayout();
  renderAnnotations();
}

function setViewMode(mode) {
  const nextMode = mode === "3d" ? "3d" : "2d";
  const modeChanged = nextMode !== viewMode;
  viewMode = nextMode;
  STAGE.classList.toggle("view-3d", viewMode === "3d");
  updateViewLabel();
  document.querySelectorAll("#mode-toggle button").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.viewMode === viewMode));
  });
  // model-viewer receives input only in 3D Select mode; annotation tools own the overlay.
  VIEWER.cameraControls = viewMode === "3d" && activeTool === "select";
  if (viewMode === "3d" && modeChanged) {
    // Start close enough to read the assembled parts while retaining a
    // useful three-quarter view. model-viewer treats the percentage as a
    // multiple of its automatically fitted camera distance.
    if (previousGlbB64) VIEWER.setAttribute("camera-orbit", "0deg 30deg 85%");
    else VIEWER.cameraOrbit = "35deg 65deg 80%";
  }
  setTool(activeTool);
  renderLayout();
  renderAnnotations();
}

document.querySelectorAll("#mode-toggle button").forEach(button => {
  button.addEventListener("click", () => setViewMode(button.dataset.viewMode));
});

function updateViewLabel() {
  const comparisonLabel = previousGlbB64
    ? ` · ${comparisonView === "before" ? "Before" : "After"}` : "";
  document.getElementById("view-label").textContent = viewMode === "2d"
    ? "2D Layout · case-local millimetres"
    : `3D Review${comparisonLabel} · ${shellView === "xray" ? "X-ray" : "Exterior"} shell · orbit and zoom in Select`;
}

function applyShellAppearance() {
  const shellMaterial = VIEWER.model?.getMaterialByName("case_shell");
  if (!shellMaterial) return;
  if (shellMaterial !== activeShellMaterial) {
    activeShellMaterial = shellMaterial;
    shellXrayColor = [...shellMaterial.pbrMetallicRoughness.baseColorFactor];
  }
  if (!shellXrayColor) return;
  shellMaterial.pbrMetallicRoughness.setBaseColorFactor([
    ...shellXrayColor.slice(0, 3), shellView === "xray" ? shellXrayColor[3] : 1,
  ]);
  shellMaterial.setAlphaMode(shellView === "xray" ? "BLEND" : "OPAQUE");
}

function setShellView(mode) {
  shellView = mode === "xray" ? "xray" : "exterior";
  document.querySelectorAll("#shell-view-toggle button").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.shellView === shellView));
  });
  applyShellAppearance();
  updateViewLabel();
}

document.querySelectorAll("#shell-view-toggle button").forEach(button => {
  button.addEventListener("click", () => setShellView(button.dataset.shellView));
});

function syncComparisonControls() {
  COMPARISON_TOGGLE.hidden = previousGlbB64 === null;
  document.querySelectorAll("#comparison-toggle button").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.comparisonView === comparisonView));
  });
  updateViewLabel();
}

function setComparisonView(mode) {
  if (mode === "before" && previousGlbB64 === null) return;
  comparisonView = mode === "before" ? "before" : "after";
  syncComparisonControls();
  const selectedGlb = comparisonView === "before" ? previousGlbB64 : glbB64;
  if (selectedGlb) beginViewerLoad(selectedGlb);
}

document.querySelectorAll("#comparison-toggle button").forEach(button => {
  button.addEventListener("click", () => setComparisonView(button.dataset.comparisonView));
});

function setTool(tool) {
  activeTool = tool;
  const annotationTool = ["arrow", "circle", "text"].includes(tool);
  document.querySelectorAll("#toolbar button").forEach(button =>
    button.classList.toggle("active", button.dataset.tool === tool));
  OVERLAY.classList.toggle("interactive", annotationTool || deleteMode);
  OVERLAY.classList.toggle("tool-text", tool === "text");
  VIEWER.cameraControls = viewMode === "3d" && tool === "select" && !deleteMode;
  if (viewMode === "2d") LAYOUT.style.pointerEvents = annotationTool ? "none" : "auto";
}

document.querySelectorAll("#toolbar button").forEach(button => {
  button.addEventListener("click", async () => {
    const tool = button.dataset.tool;
    if (tool === "clear") {
      if (moveActionsBlocked()) return;
      if (window.confirm(`Clear ${annotations.length} annotation(s) and ${moves.length} move(s)?`)) {
        setMoveActionPending(true);
        try {
          annotations.length = 0;
          moves.length = 0;
          persistMoves();
          renderAnnotations();
          await refreshMoveModels();
          STATUS.textContent = "Cleared all annotations and moves.";
        } finally {
          setMoveActionPending(false);
        }
      }
      setTool("select");
    } else if (tool === "undo") {
      if (!moveActionsBlocked()) undo();
    } else if (tool === "delete") {
      deleteMode = !deleteMode;
      button.classList.toggle("active", deleteMode);
      setTool(deleteMode ? "delete" : "select");
      STATUS.textContent = deleteMode ? "Delete mode: click an annotation." : "Delete mode off.";
    } else {
      deleteMode = false;
      setTool(tool);
    }
  });
});

window.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key === "z" && !event.shiftKey) {
    event.preventDefault();
    undo();
  } else if (event.key === "Escape") {
    deleteMode = false;
    arrowStart = null;
    setTool("select");
    STATUS.textContent = "Selection cleared.";
  }
});

function elementById(id) {
  return sceneNodes().find(node => node.id === id);
}

function scene() {
  return manifest && manifest.assembly;
}

function sceneNodes() {
  const current = scene();
  return current && Array.isArray(current.nodes) ? current.nodes : [];
}

function sceneRoutes() {
  const current = scene();
  return current && Array.isArray(current.routes) ? current.routes : [];
}

function updatePrototypeDownloads() {
  const downloads = [
    ["top", document.getElementById("download-top")],
    ["bottom", document.getElementById("download-bottom")],
  ];
  const moveQuery = revisionMoves.length
    ? `&moves=${encodeURIComponent(JSON.stringify(revisionMoves))}`
    : "";
  for (const [part, link] of downloads) {
    if (!link) continue;
    const label = `${part[0].toUpperCase()}${part.slice(1)}`;
    if (!editorRevision) {
      link.removeAttribute("href");
      link.setAttribute("aria-disabled", "true");
      link.textContent = `${label} shell STL unavailable`;
      continue;
    }
    link.href = `/stl/${part}?case_path=${encodeURIComponent(CASE_PATH)}&revision=${encodeURIComponent(editorRevision)}${moveQuery}`;
    link.download = `case-${part}.stl`;
    link.removeAttribute("aria-disabled");
    link.textContent = `Download case-${part}.stl`;
  }
  const note = document.getElementById("prototype-stl-note");
  if (note) {
    note.textContent = editorRevision
      ? `Validated for displayed revision ${editorRevision.slice(0, 12)}…`
      : "Clear or submit moves to restore validated downloads.";
  }
}

function invalidatePrototypeDownloads() {
  editorRevision = null;
  revisionMoves = [];
  updatePrototypeDownloads();
}

function assemblyItemCount() {
  return sceneNodes().length + sceneRoutes().length;
}

function nodePosition(node) {
  return node.transform.position_mm;
}

function nodeDimensions(node, keepOut = false) {
  const dimensions = keepOut ? node.keep_out_dimensions : node.physical_dimensions;
  return dimensions || { x_mm: 1, y_mm: 1, z_mm: 1 };
}

function nodeVisual(node) {
  return node.visual || {};
}

function nodeRotation(node) {
  const rotation = node.transform.rotation_deg || [0, 0, 0];
  return Number(rotation[2] || 0);
}

function isMovable(node) {
  return node.mobility === "constrained_xy";
}

function nodeLayer(node) {
  if (node.id === "case.bottom") return "bottom";
  if (node.id === "case.top") return "top-shell";
  if (node.id === "case.shell") return "bottom";
  if (node.kind === "pbs33b_button" || node.kind === "guuzi_action_button") return "top-controls";
  if (node.kind === "wire") return "wiring";
  return "internals";
}

function nodeSortKey(node) {
  const layerOrder = { bottom: 10, internals: 20, wiring: 25, "top-shell": 30,
    "top-controls": 40 };
  return [layerOrder[nodeLayer(node)] || 20, Number(node.layer || 0),
    Number(node.z_order || 0), node.id];
}

function compareNodeOrder(left, right) {
  const a = nodeSortKey(left);
  const b = nodeSortKey(right);
  for (let index = 0; index < a.length; index++) {
    if (a[index] < b[index]) return -1;
    if (a[index] > b[index]) return 1;
  }
  return 0;
}

function caseDimensions() {
  const current = scene();
  return current && current.case_dimensions;
}

function renderElementList() {
  const list = document.getElementById("elements");
  list.innerHTML = "";
  for (const element of sceneNodes()) {
    const row = document.createElement("li");
    const movable = isMovable(element);
    row.className = movable ? "movable" : "locked";
    row.dataset.id = element.id;
    row.textContent = `${element.id} (${element.kind}) · ${movable ? "movable" : element.mobility}`;
    row.addEventListener("click", () => selectElement(element));
    list.appendChild(row);
  }
  updateSelectedRow();
}

function selectElement(element) {
  activeElement = element;
  updateSelectedRow();
  renderLayout();
  STATUS.textContent = `${element.id}: ${isMovable(element) ?
    "movable in 2D Layout" : "fixed; available for selection and annotation"}.`;
}

function updateSelectedRow() {
  document.querySelectorAll("#elements li").forEach(row =>
    row.classList.toggle("active", row.dataset.id === (activeElement && activeElement.id)));
}

function bodyElement() {
  return elementById("case.shell");
}

function visual(element) {
  return nodeVisual(element);
}

function renderLayerControls() {
  const controls = document.getElementById("layer-controls");
  controls.innerHTML = Object.entries(LAYER_LABELS).map(([key, label]) =>
    `<label class="layer-toggle"><input type="checkbox" data-layer="${key}" ${layerVisibility[key] ? "checked" : ""}>` +
    `<span class="layer-swatch ${key}"></span>${label}</label>`).join("");
  controls.querySelectorAll("input[data-layer]").forEach(input => input.addEventListener("change", () => {
    layerVisibility[input.dataset.layer] = input.checked;
    renderLayout();
  }));
}

function scenePoint(point, height) {
  return [Number(point[0]), height - Number(point[1])];
}

function nodeGroup(node, bodyHeight, content) {
  const [x, y] = scenePoint(nodePosition(node), bodyHeight);
  const selected = activeElement === node ? " selected" : "";
  const movable = isMovable(node) ? " layout-movable" : " layout-locked";
  return `<g data-element-id="${escapeXml(node.id)}" class="${movable}" aria-label="${escapeXml(node.id)}"` +
    ` data-layer-key="${nodeLayer(node)}" data-layer="${Number(node.layer || 0)}"` +
    ` data-z-order="${Number(node.z_order || 0)}" transform="translate(${x} ${y}) rotate(${-nodeRotation(node)})">` +
    `${content(selected)}</g>`;
}

function renderPbsButton(node) {
  const dimensions = nodeDimensions(node);
  const diameter = Number(nodeVisual(node).diameter_mm || dimensions.x_mm);
  const selected = activeElement === node;
  const ring = diameter * .62;
  return nodeGroup(node, caseDimensions().y_mm, selected =>
    `<circle class="layout-button-hit-target" cx="0" cy="0" r="${diameter / 2 + 4}"/>` +
    `<circle class="layout-pbs-button${selected}" cx="0" cy="0" r="${diameter / 2}"/>` +
    `<circle class="layout-button-ring" cx="0" cy="0" r="${ring / 2}"/>` +
    `<circle class="layout-button-cap" cx="0" cy="0" r="${diameter * .34}"/>` +
    `<line x1="${-diameter * .18}" y1="0" x2="${diameter * .18}" y2="0" stroke="#fff7ed" stroke-width=".55"/>` +
     `<text class="layout-label" x="0" y="${diameter * .08}" text-anchor="middle">${escapeXml(node.metadata && node.metadata.control_role || node.id)}</text>`);
}

function renderActionButton(node) {
  const dimensions = nodeDimensions(node);
  const width = Number(dimensions.x_mm);
  const selected = activeElement === node;
  return nodeGroup(node, caseDimensions().y_mm, selected =>
    `<rect class="layout-button-hit-target" x="${-width / 2 - 4}" y="${-width / 2 - 4}" width="${width + 8}" height="${width + 8}" rx="3"/>` +
    `<rect class="layout-action-button${selected}" x="${-width / 2}" y="${-width / 2}" width="${width}" height="${width}" rx="2"/>` +
    `<circle cx="0" cy="0" r="${width * .28}" fill="#dbeafe" stroke="#1e3a8a" stroke-width=".5"/>` +
    `<text class="layout-label" x="0" y="1" text-anchor="middle">${escapeXml(node.metadata && node.metadata.control_role || node.id)}</text>`);
}

function renderFeather(node) {
  const v = nodeVisual(node);
  const physical = nodeDimensions(node);
  const keepOut = nodeDimensions(node, true);
  const boardWidth = Number(v.width_mm || physical.x_mm);
  const boardHeight = Number(v.height_mm || physical.y_mm);
  const keepWidth = Number(keepOut.x_mm || boardWidth);
  const keepHeight = Number(keepOut.y_mm || boardHeight);
  return nodeGroup(node, caseDimensions().y_mm, selected => {
    const pieces = [`<rect class="layout-keepout" x="${-keepWidth / 2}" y="${-keepHeight / 2}" width="${keepWidth}" height="${keepHeight}" rx="2"/>`,
      `<rect class="layout-feather${selected}" x="${-boardWidth / 2}" y="${-boardHeight / 2}" width="${boardWidth}" height="${boardHeight}" rx="2"/>`,
      `<rect class="layout-feather-chip" x="${-boardWidth * .18}" y="${-boardHeight * .12}" width="${boardWidth * .36}" height="${boardHeight * .24}" rx="1"/>`];
    for (const hole of (v.mounting_holes_mm || [])) {
      pieces.push(`<circle class="layout-feather-hole" cx="${Number(hole[0]) - boardWidth / 2}" cy="${Number(hole[1]) - boardHeight / 2}" r="1.2"/>`);
    }
    for (const header of (v.headers || [])) {
       // Header coordinates are native Feather-frame coordinates;
      // nodeGroup applies the scene rotation exactly once.
      const hx = Number(header.x_mm || 0);
      const hy = Number(header.y_mm || 0);
      const count = Number(header.count || 0);
      const spacing = Number(header.pitch_mm || 2.54);
      const headerClass = count === 12 ? "layout-12-header" : "layout-16-header";
      for (let index = 0; index < count; index++) {
        const pinX = header.axis === "y" ? hx : hx + index * spacing;
        const pinY = header.axis === "y" ? hy + index * spacing : hy;
        pieces.push(`<rect class="${headerClass}" x="${pinX}" y="${pinY}" width="1.1" height="1.1"/>`);
      }
      pieces.push(`<text class="layout-label" x="${hx}" y="${hy - 1.2}" data-header-id="${escapeXml(header.id || "header")}">${count}</text>`);
    }
    pieces.unshift(`<title>Feather · ${escapeXml(node.metadata && node.metadata.orientation || "oriented")}</title>`);
    return pieces.join("");
  });
}

function renderBreadboard(node) {
  const v = nodeVisual(node);
  const dimensions = nodeDimensions(node);
  const width = Number(v.width_mm || dimensions.x_mm);
  const height = Number(v.height_mm || dimensions.y_mm);
  const columns = Number(v.grid_columns || 30);
  const rows = Number(v.grid_rows || 10);
  return nodeGroup(node, caseDimensions().y_mm, selected => {
    const pieces = [
      `<title>SKU 100058 installed backbone · operator-reported preview only</title>`,
      `<rect class="layout-breadboard${selected}" x="${-width / 2}" y="${-height / 2}" width="${width}" height="${height}" rx="1.5"/>`,
      `<rect class="layout-breadboard-field" x="${-width * .38}" y="${-height * .31}" width="${width * .76}" height="${height * .62}" rx="1"/>`,
      `<line class="layout-breadboard-rail positive" x1="${-width * .44}" y1="${-height * .41}" x2="${width * .44}" y2="${-height * .41}"/>`,
      `<line class="layout-breadboard-rail negative" x1="${-width * .44}" y1="${height * .41}" x2="${width * .44}" y2="${height * .41}"/>`,
    ];
    for (let column = 0; column < columns; column++) {
      const x = -width * .35 + column * width * .7 / Math.max(columns - 1, 1);
      for (let row = 0; row < rows; row++) {
        const gap = row >= rows / 2 ? height * .06 : 0;
        const y = -height * .25 + row * height * .5 / Math.max(rows - 1, 1) + gap;
        pieces.push(`<circle class="layout-breadboard-hole" cx="${x}" cy="${y}" r=".32"/>`);
      }
    }
    for (const port of node.ports || []) {
      if (!String(port.name || "").startsWith("signal_") && !String(port.name || "").startsWith("gnd_")) continue;
      const position = port.position_mm || [0, 0, 0];
      const contactClass = String(port.signal || "").toUpperCase() === "GND" ? "ground" : "signal";
      pieces.push(`<circle class="layout-breadboard-contact ${contactClass}" data-port-name="${escapeXml(port.name)}" cx="${Number(position[0])}" cy="${-Number(position[1])}" r=".75"/>`);
    }
    pieces.push(`<text class="layout-label" x="0" y="${height * .47}" text-anchor="middle">SKU 100058 · PREVIEW</text>`);
    return pieces.join("");
  });
}

function renderNode(node) {
  const v = nodeVisual(node);
  const dimensions = nodeDimensions(node);
  const width = Number(v.width_mm || dimensions.x_mm);
  const height = Number(v.height_mm || dimensions.y_mm);
  const selected = activeElement === node ? " selected" : "";
  if (node.id === "case.shell") {
    return nodeGroup(node, caseDimensions().y_mm, () => `<rect class="layout-shell-envelope${selected}" x="${-width / 2}" y="${-height / 2}" width="${width}" height="${height}" rx="${Number(v.corner_radius_mm || 4)}"/>`);
  }
  if (node.id === "case.bottom") {
    return nodeGroup(node, caseDimensions().y_mm, () => `<rect class="layout-bottom-shell${selected}" x="${-width / 2}" y="${-height / 2}" width="${width}" height="${height}" rx="${Number(v.corner_radius_mm || 4)}"/>`);
  }
  if (node.id === "case.top") {
    const radius = Number(v.corner_radius_mm || 4);
    return nodeGroup(node, caseDimensions().y_mm, () => `<rect class="layout-top-shell${selected}" x="${-width / 2}" y="${-height / 2}" width="${width}" height="${height}" rx="${radius}"/><rect class="layout-shell-highlight" x="${-width / 2 + 2}" y="${-height / 2 + 2}" width="${width - 4}" height="${height - 4}" rx="${Math.max(radius - 2, 0)}"/>`);
  }
   if (node.kind === "pbs33b_button") return renderPbsButton(node);
   if (node.kind === "guuzi_action_button") return renderActionButton(node);
  if (node.kind === "half_size_breadboard") return renderBreadboard(node);
  if (node.kind === "feather_board") return renderFeather(node);
  if (node.kind === "pid2830_header_row") {
    const pinCount = Number(node.metadata && node.metadata.pin_count || 0);
    const headerWidth = Number(dimensions.x_mm);
    return nodeGroup(node, caseDimensions().y_mm, () => `<title>${pinCount}-pin stacking header</title><rect class="${pinCount === 12 ? "layout-12-header" : "layout-16-header"}" x="${-headerWidth / 2}" y="${-dimensions.y_mm / 2}" width="${headerWidth}" height="${dimensions.y_mm}" rx=".5"/>`);
  }
  if (node.kind === "micro_usb_connector" || node.kind === "rear_usb_opening") {
    const slotWidth = Number(v.width_mm || width);
    const slotHeight = Number(v.height_mm || height);
    return nodeGroup(node, caseDimensions().y_mm, () => `<rect class="layout-usb${selected}" x="${-slotWidth / 2}" y="${-slotHeight / 2}" width="${slotWidth}" height="${slotHeight}" rx="${Number(v.corner_radius_mm || 2)}"/><rect class="layout-usb-inner" x="${-slotWidth * .32}" y="${-slotHeight * .18}" width="${slotWidth * .64}" height="${slotHeight * .36}" rx=".8"/><text class="layout-label" x="0" y="1" text-anchor="middle" fill="white">USB</text>`);
  }
  if (node.kind === "m2_5_fastener") {
    return nodeGroup(node, caseDimensions().y_mm, () => `<polygon class="layout-fastener${selected}" points="0,-2.5 2.2,-1.25 2.2,1.25 0,2.5 -2.2,1.25 -2.2,-1.25"/><circle cx="0" cy="0" r=".75" fill="#475569"/>`);
  }
  if (node.kind === "heat_shrink") {
    return nodeGroup(node, caseDimensions().y_mm, () => `<rect class="layout-heat-shrink${selected}" x="${-width / 2}" y="${-height / 2}" width="${width}" height="${height}" rx="1"/><line x1="${-width / 2}" y1="0" x2="${width / 2}" y2="0" stroke="#ddd6fe" stroke-width=".5"/>`);
  }
  if (node.kind === "strain_relief") {
    return nodeGroup(node, caseDimensions().y_mm, () => `<rect class="layout-strain-relief${selected}" x="${-width / 2}" y="${-height / 2}" width="${width}" height="${height}" rx="2"/><path d="M${-width / 4},${-height / 2} v${height} M0,${-height / 2} v${height} M${width / 4},${-height / 2} v${height}" stroke="#cbd5e1" stroke-width=".35"/>`);
  }
  return nodeGroup(node, caseDimensions().y_mm, () => `<rect class="layout-unknown${selected}" x="${-width / 2}" y="${-height / 2}" width="${width}" height="${height}" rx="1.5"/><path class="layout-unknown-mark" d="M${-width * .22},${-height * .22} L${width * .22},${height * .22} M${width * .22},${-height * .22} L${-width * .22},${height * .22}"/><text class="layout-label" x="0" y="1" text-anchor="middle">${escapeXml(node.id)}</text>`);
}

function resolvePortPosition(reference) {
  const separator = String(reference || "").lastIndexOf(".");
  if (separator < 1) return null;
  const node = elementById(reference.slice(0, separator));
  const portName = reference.slice(separator + 1);
  const port = node && (node.ports || []).find(candidate => candidate.name === portName);
  if (!port) return null;
  const local = port.position_mm || [0, 0, 0];
  const angle = nodeRotation(node) * Math.PI / 180;
  return [nodePosition(node)[0] + Number(local[0]) * Math.cos(angle) - Number(local[1]) * Math.sin(angle),
    nodePosition(node)[1] + Number(local[0]) * Math.sin(angle) + Number(local[1]) * Math.cos(angle),
    nodePosition(node)[2] + Number(local[2])];
}

function attachedRoutePoints(route) {
  const points = (route.waypoints_mm || []).map(point => [...point]);
  const source = resolvePortPosition(route.source);
  const target = resolvePortPosition(route.target);
  if (source && points.length) points[0] = source;
  if (target && points.length) points[points.length - 1] = target;
  return points;
}

function routeCableMetadata(route) {
  const cable = route.cable || route;
  const cableKind = cable.cable_kind || cable.kind;
  const terminationKind = cable.termination_kind || cable.termination;
  if (cableKind && terminationKind) {
    return { cable_kind: cableKind, termination_kind: terminationKind };
  }
  if (String(route.source || "").startsWith("control.") &&
      String(route.target || "").startsWith("electronics.breadboard.signal_")) {
    return { cable_kind: "Dupont", termination_kind: "alligator clips" };
  }
  return null;
}

function renderRoute(route, bodyHeight) {
  const points = attachedRoutePoints(route).map(point => scenePoint(point, bodyHeight));
  const signal = String(route.signal || "").toUpperCase();
  const routeClass = signal === "GND" ? "ground" : "signal";
  const cable = routeCableMetadata(route);
  const cableAttributes = cable
    ? ` data-cable-kind="${escapeXml(cable.cable_kind)}" data-termination-kind="${escapeXml(cable.termination_kind)}"`
    : "";
  const cableLabel = cable ? ` · ${cable.cable_kind} · ${cable.termination_kind}` : "";
  const title = `${signal} · ${route.id} · ${route.source} → ${route.target}${cableLabel}`;
  return `<g data-route-id="${escapeXml(route.id)}" data-route-layer="wiring" aria-label="${escapeXml(route.id)}"${cableAttributes}>` +
    `<title>${escapeXml(title)}</title>` +
    `<polyline class="layout-wire ${routeClass}" data-signal="${escapeXml(signal)}"${cableAttributes} points="${points.map(point => point.join(",")).join(" ")}"/>` +
    `<text class="layout-route-label" x="${points[0][0]}" y="${points[0][1]}">${escapeXml(signal)}</text>` +
    `</g>`;
}

function compareRouteOrder(left, right) {
  return left.id.localeCompare(right.id);
}

function renderLayout() {
  const dimensions = caseDimensions();
  if (!dimensions) return;
  const width = Number(dimensions.x_mm);
  const height = Number(dimensions.y_mm);
  document.getElementById("stage").style.aspectRatio = `${width} / ${height}`;
  LAYOUT.setAttribute("viewBox", `0 0 ${width} ${height}`);
  LAYOUT.setAttribute("preserveAspectRatio", "xMidYMid meet");
  const parts = [`<defs><linearGradient id="top-shell-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#dbeafe"/><stop offset="1" stop-color="#60a5fa"/></linearGradient><filter id="shell-shadow" x="-10%" y="-10%" width="120%" height="120%"><feDropShadow dx="0" dy="1" stdDeviation=".7" flood-color="#1e3a8a" flood-opacity=".28"/></filter></defs>`];
  const nodes = sceneNodes().slice().sort(compareNodeOrder);
  const baseNodes = nodes.filter(node => ["bottom", "internals"].includes(nodeLayer(node)));
  const upperNodes = nodes.filter(node => ["top-shell", "top-controls"].includes(nodeLayer(node)));
  for (const node of baseNodes) {
    if (layerVisibility[nodeLayer(node)]) parts.push(renderNode(node));
  }
  for (const route of sceneRoutes().slice().sort(compareRouteOrder)) {
    if (layerVisibility.wiring) parts.push(renderRoute(route, height));
  }
  for (const node of upperNodes) {
    if (layerVisibility[nodeLayer(node)]) parts.push(renderNode(node));
  }
  LAYOUT.innerHTML = parts.join("");
}

// Case-local XY <-> screen helpers.  The same uniform scale is used for both
// directions, including the letterbox margin when the stage is not square.
function buildToScreen(position) {
  const rect = LAYOUT.getBoundingClientRect();
  const dimensions = caseDimensions();
  const L = Number(dimensions.x_mm);
  const W = Number(dimensions.y_mm);
  const scale = Math.min(rect.width / L, rect.height / W);
  return [(rect.width - L * scale) / 2 + position[0] * scale,
    (rect.height + W * scale) / 2 - position[1] * scale];
}

function screenToBuild(screenX, screenY) {
  const rect = LAYOUT.getBoundingClientRect();
  const dimensions = caseDimensions();
  const L = Number(dimensions.x_mm);
  const W = Number(dimensions.y_mm);
  const scale = Math.min(rect.width / L, rect.height / W);
  return [(screenX - (rect.width - L * scale) / 2) / scale,
    ((rect.height + W * scale) / 2 - screenY) / scale];
}

function clampMovablePosition(node, position) {
  const dimensions = caseDimensions();
  const footprint = nodeDimensions(node, true) || nodeDimensions(node);
  const halfX = Math.abs(Number(footprint.x_mm || 0)) / 2;
  const halfY = Math.abs(Number(footprint.y_mm || 0)) / 2;
  return [Math.max(halfX, Math.min(Number(dimensions.x_mm) - halfX, position[0])),
    Math.max(halfY, Math.min(Number(dimensions.y_mm) - halfY, position[1]))];
}

function loadStoredMoves() {
  try {
    const stored = JSON.parse(window.localStorage.getItem(MOVE_STORAGE_KEY) || "null");
    if (!stored || stored.editor_revision !== manifest.editor_revision ||
        !Array.isArray(stored.moves)) {
      window.localStorage.removeItem(MOVE_STORAGE_KEY);
      return [];
    }
    return stored.moves;
  } catch (_error) {
    return [];
  }
}

function persistMoves() {
  try {
    if (!moves.length) {
      window.localStorage.removeItem(MOVE_STORAGE_KEY);
      return;
    }
    window.localStorage.setItem(
      MOVE_STORAGE_KEY,
      JSON.stringify({ editor_revision: manifest.editor_revision, moves }),
    );
  } catch (_error) {
    // The editor still works for this page when browser storage is unavailable.
  }
}

function updateMoveActionControls() {
  const disabled = moveValidationPending || moveActionPending;
  document.querySelectorAll("#send, #reset-design, #undo-btn, [data-tool=\"clear\"]").forEach(control => {
    control.disabled = disabled;
    control.setAttribute("aria-disabled", String(disabled));
  });
}

function setMoveValidationPending(pending) {
  moveValidationPending = pending;
  updateMoveActionControls();
}

function setMoveActionPending(pending) {
  moveActionPending = pending;
  updateMoveActionControls();
}

function moveActionsBlocked() {
  return moveValidationPending || moveActionPending;
}

async function validateMovesOnServer(candidateMoves) {
  try {
    const cumulativeMoves = candidateMoves.map(candidate => ({
      element_id: candidate.element_id,
      new_position: candidate.new_position,
      expected_position: candidate.expected_position,
    }));
    const response = await fetch("/validate-move", { method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ case_path: CASE_PATH, moves: cumulativeMoves }) });
    const body = await response.json();
    if (!response.ok || body.valid !== true) return body;
    return body;
  } catch (error) {
    return { valid: false, violations: [{ message: "Move validation unavailable: " + error }] };
  }
}

async function validateMoveOnServer(move) {
  return validateMovesOnServer(moves.concat([move]));
}

function applyValidatedMoveModel(validation, elementId = null) {
  if (validation.scene) {
    manifest.assembly = validation.scene;
    if (typeof validation.editor_revision === "string" && Array.isArray(validation.revision_moves)) {
      editorRevision = validation.editor_revision;
      revisionMoves = validation.revision_moves;
      updatePrototypeDownloads();
    } else {
      invalidatePrototypeDownloads();
    }
    activeElement = elementId ? elementById(elementId) : null;
    renderElementList();
  }
  renderLayout();
  if (validation.glb_b64) {
    previousGlbB64 = null;
    comparisonView = "after";
    syncComparisonControls();
    glbB64 = validation.glb_b64;
    beginViewerLoad(glbB64);
  }
}

async function refreshMoveModels() {
  if (moveValidationPending) return false;
  setMoveValidationPending(true);
  try {
    const validation = await validateMovesOnServer(moves);
    if (validation.valid !== true) return false;
    applyValidatedMoveModel(validation);
    return true;
  } finally {
    setMoveValidationPending(false);
  }
}

function releaseLayoutPointerCapture(pointerId) {
  try {
    if (LAYOUT.hasPointerCapture(pointerId)) LAYOUT.releasePointerCapture(pointerId);
  } catch (_error) {
    // Pointer capture may already have been released by the browser.
  }
}

function revertLayoutDrag(drag) {
  const position = nodePosition(drag.element);
  position[0] = drag.oldPosition[0];
  position[1] = drag.oldPosition[1];
  renderLayout();
}

function cancelLayoutDrag(pointerId) {
  if (!layoutDrag || layoutDrag.pointerId !== pointerId) {
    releaseLayoutPointerCapture(pointerId);
    return;
  }
  const drag = layoutDrag;
  layoutDrag = null;
  revertLayoutDrag(drag);
  releaseLayoutPointerCapture(pointerId);
}

LAYOUT.addEventListener("pointerdown", event => {
  if (viewMode !== "2d" || activeTool !== "select" || moveActionsBlocked()) return;
  const group = event.target.closest("[data-element-id]");
  const element = group && elementById(group.dataset.elementId);
  if (!element) return;
  selectElement(element);
  if (!isMovable(element)) return;
  event.preventDefault();
  layoutDrag = { element, startX: event.clientX, startY: event.clientY,
    oldPosition: nodePosition(element).slice(0, 2), pointerId: event.pointerId };
  try {
    LAYOUT.setPointerCapture(event.pointerId);
  } catch (_error) {
    layoutDrag = null;
  }
});

LAYOUT.addEventListener("pointermove", event => {
  if (!layoutDrag || event.pointerId !== layoutDrag.pointerId) return;
  const start = buildToScreen(layoutDrag.oldPosition);
  const next = clampMovablePosition(layoutDrag.element, screenToBuild(start[0] + event.clientX - layoutDrag.startX,
    start[1] + event.clientY - layoutDrag.startY));
  nodePosition(layoutDrag.element)[0] = Math.round(next[0] * 10) / 10;
  nodePosition(layoutDrag.element)[1] = Math.round(next[1] * 10) / 10;
  renderLayout();
});

LAYOUT.addEventListener("pointerup", async event => {
  if (!layoutDrag || event.pointerId !== layoutDrag.pointerId) return;
  const drag = layoutDrag;
  const newPosition = [nodePosition(drag.element)[0], nodePosition(drag.element)[1]];
  layoutDrag = null;
  releaseLayoutPointerCapture(event.pointerId);
  if (moveActionsBlocked()) {
    revertLayoutDrag(drag);
    return;
  }
  if (newPosition[0] !== drag.oldPosition[0] || newPosition[1] !== drag.oldPosition[1]) {
    const moveRecord = { element_id: drag.element.id, new_position: newPosition,
      expected_position: drag.oldPosition };
    setMoveValidationPending(true);
    try {
      const validation = await validateMoveOnServer(moveRecord);
      if (validation.valid !== true) {
        nodePosition(drag.element)[0] = drag.oldPosition[0];
        nodePosition(drag.element)[1] = drag.oldPosition[1];
        renderLayout();
        const messages = (validation.violations || []).map(violation => violation.message).join("; ");
        STATUS.textContent = `Move rejected: ${messages || "canonical scene rule violation"}`;
        return;
      }
      moves.push(moveRecord);
      persistMoves();
      applyValidatedMoveModel(validation, moveRecord.element_id);
      pushUndo({ undo: async () => {
        const currentElement = elementById(moveRecord.element_id);
        if (currentElement) {
          nodePosition(currentElement)[0] = drag.oldPosition[0];
          nodePosition(currentElement)[1] = drag.oldPosition[1];
        }
        const index = moves.lastIndexOf(moveRecord);
        if (index >= 0) moves.splice(index, 1);
        persistMoves();
        await refreshMoveModels();
      }});
      STATUS.textContent = `Moved ${drag.element.id} to (${newPosition[0]}, ${newPosition[1]}).`;
    } finally {
      setMoveValidationPending(false);
    }
   }
});

LAYOUT.addEventListener("pointercancel", event => cancelLayoutDrag(event.pointerId));
LAYOUT.addEventListener("lostpointercapture", event => cancelLayoutDrag(event.pointerId));

function annotationCoords(event) {
  const rect = OVERLAY.getBoundingClientRect();
  return [Math.round((event.clientX - rect.left) * 800 / rect.width),
    Math.round((event.clientY - rect.top) * 600 / rect.height)];
}

OVERLAY.addEventListener("click", event => {
  const [x, y] = annotationCoords(event);
  if (deleteMode) {
    const hit = annotations.find(annotation => annotation.view_mode === viewMode &&
      Math.hypot((annotation.anchor || annotation.center || [0, 0])[0] - x,
        (annotation.anchor || annotation.center || [0, 0])[1] - y) < 24);
    if (hit) annotations.splice(annotations.indexOf(hit), 1);
    renderAnnotations();
    return;
  }
  if (activeTool === "arrow") {
    if (!arrowStart) { arrowStart = [x, y]; return; }
    annotations.push({ id: nextAnnotationId++, kind: "arrow", start: arrowStart,
      end: [x, y], element_id: activeElement ? activeElement.id : null, view_mode: viewMode });
    arrowStart = null;
  } else if (activeTool === "circle") {
    annotations.push({ id: nextAnnotationId++, kind: "circle", center: [x, y], radius: 30,
      element_id: activeElement ? activeElement.id : null, view_mode: viewMode });
  } else if (activeTool === "text") {
    const text = window.prompt("Annotation text:") || "";
    if (text) annotations.push({ id: nextAnnotationId++, kind: "text", anchor: [x, y],
      text, element_id: activeElement ? activeElement.id : null, view_mode: viewMode });
  }
  renderAnnotations();
});

function renderAnnotations() {
  const parts = [`<defs><marker id="arrowhead" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#ef4444"/></marker></defs>`];
  for (const annotation of annotations) {
    if (annotation.view_mode !== viewMode) continue;
    if (annotation.kind === "arrow") {
      parts.push(`<line class="marker-line" x1="${annotation.start[0]}" y1="${annotation.start[1]}" x2="${annotation.end[0]}" y2="${annotation.end[1]}" marker-end="url(#arrowhead)"/>`);
      parts.push(`<text class="marker-num" x="${annotation.end[0] + 8}" y="${annotation.end[1] + 4}">${annotation.id}</text>`);
    } else if (annotation.kind === "circle") {
      parts.push(`<circle class="marker-circle" cx="${annotation.center[0]}" cy="${annotation.center[1]}" r="${annotation.radius}"/>`);
      parts.push(`<text class="marker-num" x="${annotation.center[0]}" y="${annotation.center[1] + 5}" text-anchor="middle">${annotation.id}</text>`);
    } else if (annotation.kind === "text") {
      parts.push(`<text class="marker-text" x="${annotation.anchor[0]}" y="${annotation.anchor[1]}">${escapeXml(annotation.text)}</text>`);
      parts.push(`<text class="marker-num" x="${annotation.anchor[0] - 14}" y="${annotation.anchor[1] + 4}">${annotation.id}</text>`);
    }
  }
  OVERLAY.setAttribute("viewBox", "0 0 800 600");
  OVERLAY.innerHTML = parts.join("");
}

function pushUndo(action) {
  undoStack.push(action);
  if (undoStack.length > 50) undoStack.shift();
}

async function undo() {
  if (moveActionsBlocked()) return;
  const action = undoStack.pop();
  if (!action) { STATUS.textContent = "Nothing to undo."; return; }
  setMoveActionPending(true);
  try {
    await action.undo();
    STATUS.textContent = `Undid. ${undoStack.length} action(s) on the stack.`;
  } finally {
    setMoveActionPending(false);
  }
}

function escapeXml(value) {
  return String(value).replace(/[<>&'\"]/g, character => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "'": "&apos;", "\"": "&quot;" }[character]));
}

function nextPaint() {
  return new Promise(resolve => window.setTimeout(resolve, 0));
}

function updateGenerationElapsed() {
  GENERATION_PROGRESS_DETAIL.textContent =
    `Server pipeline: model → validation → 3D render · ${generationElapsedSeconds}s elapsed`;
}

function beginGenerationProgress(promptText) {
  generationElapsedSeconds = 0;
  GENERATION_PROGRESS_TITLE.textContent = promptText ? "Applying requested case change…" : "Building case…";
  updateGenerationElapsed();
  GENERATION_PROGRESS.hidden = false;
  CHANGE_SUMMARY.hidden = true;
  STAGE.setAttribute("aria-busy", "true");
  SEND.textContent = "Working…";
  generationTimer = window.setInterval(() => {
    generationElapsedSeconds += 1;
    updateGenerationElapsed();
  }, 1000);
}

function endGenerationProgress() {
  if (generationTimer !== null) window.clearInterval(generationTimer);
  generationTimer = null;
  GENERATION_PROGRESS.hidden = true;
  STAGE.removeAttribute("aria-busy");
  SEND.textContent = "Send →";
}

function caseCornerRadius(candidateManifest) {
  const scene = candidateManifest && candidateManifest.assembly;
  const shell = scene && (scene.nodes || []).find(node => node.id === "case.shell");
  return shell && shell.visual ? Number(shell.visual.corner_radius_mm) : null;
}

function caseDimensionLabel(candidateManifest) {
  const dimensions = candidateManifest && candidateManifest.case_dimensions;
  if (!dimensions) return null;
  return `${Number(dimensions.x_mm)} × ${Number(dimensions.y_mm)} × ${Number(dimensions.z_mm)} mm`;
}

function describeVisibleChanges(previousManifest, nextManifest, previousGlbB64, nextGlbB64) {
  const changes = [];
  const previousRadius = caseCornerRadius(previousManifest);
  const nextRadius = caseCornerRadius(nextManifest);
  if (Number.isFinite(previousRadius) && Number.isFinite(nextRadius) && previousRadius !== nextRadius) {
    changes.push(`2D corner radius ${previousRadius} → ${nextRadius} mm`);
  }
  const previousDimensions = caseDimensionLabel(previousManifest);
  const nextDimensions = caseDimensionLabel(nextManifest);
  if (previousDimensions && nextDimensions && previousDimensions !== nextDimensions) {
    changes.push(`2D case dimensions ${previousDimensions} → ${nextDimensions}`);
  }
  if (previousGlbB64 !== nextGlbB64) {
    changes.push("3D geometry changed; compare Before and After in 3D Review");
  }
  return changes.length ? `Visible changes: ${changes.join(" · ")}` :
    "No visible geometry change was produced; the accepted candidate matches the current 2D and 3D views.";
}

function showChangeSummary(changeSummary) {
  CHANGE_SUMMARY.textContent = changeSummary;
  CHANGE_SUMMARY.dataset.visibleChange = String(!changeSummary.startsWith("No visible geometry change"));
  CHANGE_SUMMARY.hidden = false;
}

const CHIPS = {
  snes_curve: "Use the snes_inspired exterior profile. Keep the fixed case frame and preserve every CONTROLS x/y position; protected routes are immovable.",
  n64_lobes: "Use the n64_inspired exterior profile. Keep the fixed case frame and preserve every CONTROLS x/y position; protected routes are immovable.",
  angular_shell: "Use a custom exterior profile with roundness_mm to 6. Keep the fixed case frame and preserve every CONTROLS x/y position; protected routes are immovable.",
};
document.querySelectorAll(".chip").forEach(button => button.addEventListener("click", () => {
  const name = button.dataset.chip;
  document.querySelectorAll(".chip.active").forEach(chip => chip.classList.remove("active"));
  selectedChip = selectedChip === name ? null : name;
  if (selectedChip) { button.classList.add("active"); PROMPT_INPUT.value = CHIPS[name]; }
  else PROMPT_INPUT.value = "";
}));
PROMPT_INPUT.addEventListener("input", () => {
  if (selectedChip && PROMPT_INPUT.value !== CHIPS[selectedChip]) {
    selectedChip = null;
    document.querySelectorAll(".chip.active").forEach(button => button.classList.remove("active"));
  }
});

RESET_DESIGN.addEventListener("click", async () => {
  if (moveActionsBlocked()) return;
  if (!window.confirm("Reset exterior and control positions to workshop defaults?")) return;
  setMoveActionPending(true);
  RESET_DESIGN.textContent = "Resetting…";
  STATUS.textContent = "Restoring the default rounded case and control layout…";
  try {
    const response = await fetch("/reset", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({case_path: CASE_PATH, editor_revision: manifest.editor_revision}),
    });
    const body = await jsonResponse(response);
    const previousGlb = glbB64;
    previousGlbB64 = previousGlb !== body.glb_b64 ? previousGlb : null;
    comparisonView = "after";
    syncComparisonControls();
    if (previousGlbB64 && viewMode === "3d") {
      VIEWER.setAttribute("camera-orbit", "0deg 30deg 85%");
    }
    await loadModel(body.manifest, body.glb_b64, body.snapshot_b64);
    resetEditorState();
    showChangeSummary("Reset to the default rounded case and control layout.");
    STATUS.textContent = "Default design restored; 2D and 3D refreshed.";
  } catch (error) {
    STATUS.textContent = `Reset failed: ${error instanceof Error ? error.message : error}`;
  } finally {
    RESET_DESIGN.textContent = "Reset design";
    setMoveActionPending(false);
  }
});

document.getElementById("send").addEventListener("click", async () => {
  if (moveActionsBlocked() || layoutDrag) return;
  setMoveActionPending(true);
  try {
    const promptText = (selectedChip ? CHIPS[selectedChip] : PROMPT_INPUT.value).trim();
    const feedbackMoves = moves.map(move => ({ element_id: move.element_id, new_position: move.new_position,
     expected_position: move.expected_position }));
    const feedback = {
      moves: feedbackMoves,
      // Keep the established annotation payload shape; view_mode is additive.
      annotations: annotations.map(annotation => ({
        id: annotation.id,
        kind: annotation.kind,
        element_id: annotation.element_id || null,
        anchor: annotation.anchor || annotation.center || annotation.start || null,
        end: annotation.end || null,
        text: annotation.text || null,
        view_mode: annotation.view_mode,
      })),
      selected_element_id: activeElement ? activeElement.id : null,
      notes: [`${annotations.length} annotation(s), ${moves.length} move(s) sent from ${viewMode}`],
    };
    if (promptText) { feedback.prompt = promptText; feedback.notes.unshift(`Prompt: ${promptText}`); }
    STATUS.textContent = promptText ? `Regenerating: "${promptText.slice(0, 60)}…"` : "Submitting…";
    beginGenerationProgress(promptText);
    await nextPaint();
    const response = await fetch("/submit", { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ case_path: CASE_PATH, snapshot_b64: snapshotB64, feedback }) });
    const body = await response.json();
    if (body.status === "ok") {
      const changeSummary = describeVisibleChanges(manifest, body.manifest, glbB64, body.glb_b64);
      const previousGlb = glbB64;
      previousGlbB64 = previousGlb !== body.glb_b64 ? previousGlb : null;
      comparisonView = "after";
      syncComparisonControls();
      if (previousGlbB64 && viewMode === "3d") {
        VIEWER.setAttribute("camera-orbit", "0deg 30deg 85%");
      }
      await loadModel(body.manifest, body.glb_b64, body.snapshot_b64);
      resetEditorState();
      showChangeSummary(changeSummary);
      STATUS.textContent = `Regenerated (${body.iterations || 1} iteration${body.iterations === 1 ? "" : "s"}); 2D ready · 3D loading…`;
    } else {
      const violations = (body.violations || []).filter(v => v.severity === "error").map(v => v.message).join("; ");
      STATUS.textContent = `Regeneration failed: ${body.error || "(no detail)"}${violations ? `\nViolations: ${violations}` : ""}`;
    }
  } catch (error) {
    STATUS.textContent = `Regeneration failed: ${error instanceof Error ? error.message : error}`;
  } finally {
    endGenerationProgress();
    setMoveActionPending(false);
  }
});

init().catch(error => { STATUS.textContent = "Init error: " + error; });
</script>
</body>
</html>
"""
