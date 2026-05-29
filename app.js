"use strict";

const PYODIDE_VERSION = "0.26.4";
const PYODIDE_BASE = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const DEBOUNCE_MS = 250;
const MAX_CURVES = 10;

// ColorBrewer Set1 — distinct, accessible defaults; first is red.
const DEFAULT_COLORS = [
  "#e41a1c", "#377eb8", "#4daf4a", "#ff7f00", "#984ea3",
  "#a65628", "#f781bf", "#999999", "#17becf", "#000000",
];

// Real package names + the generic label shown to the user. The label is
// deliberately abstract — the modal phrases load progress in terms of
// what's happening, not which library is responsible.
const PACKAGES = [
  { name: "numpy",      label: "math support" },
  { name: "matplotlib", label: "the plot renderer" },
  { name: "sympy",      label: "the math engine" },
];

const $ = (id) => document.getElementById(id);

let pyodide = null;
let renderPlot = null;          // owned PyProxy of plot_visualizer.render_plot
let pendingTimer = null;
let lastBlobUrl = null;

// ---------------------------------------------------------------------------
// Loader overlay
// ---------------------------------------------------------------------------

function showLoader(libName, sub) {
  $("loader-lib").textContent = libName;
  if (sub !== undefined) $("loader-sub").textContent = sub;
  $("loader").classList.remove("hidden");
}

function hideLoader() {
  $("loader").classList.add("hidden");
}

// ---------------------------------------------------------------------------
// Status / error messaging in the plot pane
// ---------------------------------------------------------------------------

function setError(msg) {
  $("error").textContent = msg || "";
}

function shortenPythonError(message) {
  const lines = message.split("\n").map((l) => l.trimEnd()).filter(Boolean);
  const meaningful = lines.filter((l) => !l.startsWith("  "));
  return meaningful.slice(-3).join("\n") || message;
}

// ---------------------------------------------------------------------------
// Curve rows (dynamic, up to MAX_CURVES)
// ---------------------------------------------------------------------------

function curveRows() {
  return Array.from(document.querySelectorAll(".curve-row"));
}

function updateCurveCount() {
  const n = curveRows().length;
  $("curve-count").textContent = `${n}/${MAX_CURVES}`;
  $("add-curve").disabled = n >= MAX_CURVES || !renderPlot;
  // Leave at least one row.
  for (const btn of document.querySelectorAll(".curve-remove")) {
    btn.disabled = n <= 1;
  }
}

function addCurveRow({ expr = "", color = null } = {}) {
  const rows = curveRows();
  if (rows.length >= MAX_CURVES) return null;

  const frag = $("curve-row-template").content.cloneNode(true);
  const row = frag.querySelector(".curve-row");
  const exprInput = row.querySelector(".curve-expr");
  const colorInput = row.querySelector(".curve-color");
  const removeBtn = row.querySelector(".curve-remove");

  exprInput.value = expr;
  colorInput.value = color || DEFAULT_COLORS[rows.length % DEFAULT_COLORS.length];
  exprInput.disabled = colorInput.disabled = !renderPlot;

  exprInput.addEventListener("input", scheduleRender);
  colorInput.addEventListener("input", scheduleRender);
  colorInput.addEventListener("change", scheduleRender);
  removeBtn.addEventListener("click", () => {
    if (curveRows().length <= 1) return;
    row.remove();
    updateCurveCount();
    scheduleRender();
  });

  $("curves").appendChild(row);
  updateCurveCount();
  return row;
}

function gatherCurves() {
  const out = [];
  for (const row of curveRows()) {
    const expr = row.querySelector(".curve-expr").value.trim();
    if (!expr) continue;
    const color = row.querySelector(".curve-color").value || DEFAULT_COLORS[0];
    out.push([expr, color]);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Borders panel
// ---------------------------------------------------------------------------

function syncBorderEnable() {
  const ready = !!renderPlot;
  const xAuto = $("autoX").checked;
  const yAuto = $("autoY").checked;
  $("xlo").disabled = $("xhi").disabled = xAuto || !ready;
  $("ylo").disabled = $("yhi").disabled = yAuto || !ready;
}

function readBorder(autoId, loId, hiId) {
  if ($(autoId).checked) return null;
  const lo = parseFloat($(loId).value);
  const hi = parseFloat($(hiId).value);
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo >= hi) return null;
  return [lo, hi];
}

function formatBorder(v) {
  if (Math.abs(v) >= 1e4 || (v !== 0 && Math.abs(v) < 1e-3)) return v.toPrecision(4);
  return parseFloat(v.toFixed(3)).toString();
}

// ---------------------------------------------------------------------------
// Image rendering
// ---------------------------------------------------------------------------

function showImage(pngBytes) {
  const blob = new Blob([pngBytes], { type: "image/png" });
  const url = URL.createObjectURL(blob);
  if (lastBlobUrl) URL.revokeObjectURL(lastBlobUrl);
  lastBlobUrl = url;
  $("plot").src = url;
}

function render() {
  if (!renderPlot) return;
  const curves = gatherCurves();
  if (curves.length === 0) {
    setError("Add at least one non-empty expression.");
    return;
  }

  const xBorders = readBorder("autoX", "xlo", "xhi");
  const yBorders = readBorder("autoY", "ylo", "yhi");
  const title = $("title").value;

  let proxy = null;
  try {
    proxy = renderPlot(curves, xBorders, yBorders, title);
    const result = proxy.toJs({ dict_converter: Object.fromEntries });
    proxy.destroy();
    proxy = null;

    showImage(result.png);

    if ($("autoX").checked) {
      $("xlo").value = formatBorder(result.x1);
      $("xhi").value = formatBorder(result.x2);
    }
    if ($("autoY").checked) {
      $("ylo").value = formatBorder(result.y1);
      $("yhi").value = formatBorder(result.y2);
    }

    setError((result.errors && result.errors.length > 0)
      ? result.errors.join("\n")
      : "");
  } catch (e) {
    if (proxy) {
      try { proxy.destroy(); } catch (_) { /* ignore */ }
    }
    setError(shortenPythonError(e.message || String(e)));
  }
}

function scheduleRender() {
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(render, DEBOUNCE_MS);
}

// ---------------------------------------------------------------------------
// Enable / disable all controls
// ---------------------------------------------------------------------------

function setControlsEnabled(enabled) {
  for (const id of ["autoX", "autoY", "title", "xlo", "xhi", "ylo", "yhi"]) {
    $(id).disabled = !enabled;
  }
  for (const inp of document.querySelectorAll(".curve-expr, .curve-color")) {
    inp.disabled = !enabled;
  }
  $("add-curve").disabled = !enabled || curveRows().length >= MAX_CURVES;
  syncBorderEnable();
  updateCurveCount();
}

// ---------------------------------------------------------------------------
// Pyodide boot — sequential package loads so each lib name shows in the modal
// ---------------------------------------------------------------------------

async function boot() {
  if (location.protocol === "file:") {
    hideLoader();
    setError(
      "Page opened via file:// — browsers block fetch() from local files. " +
      "Start an HTTP server in this folder:\n\n" +
      "    python -m http.server 8000\n\n" +
      "and open http://localhost:8000/"
    );
    return;
  }

  let step = "initializing";
  try {
    step = "loading the runtime";
    showLoader("the runtime", "First load is ~10 MB — afterwards it's cached.");
    pyodide = await loadPyodide({ indexURL: PYODIDE_BASE });

    for (const pkg of PACKAGES) {
      step = `loading ${pkg.label}`;
      showLoader(pkg.label, `Fetching ${pkg.label} from the CDN…`);
      await pyodide.loadPackage([pkg.name]);
    }

    step = "loading the visualizer";
    showLoader("the visualizer", "Wiring up the application module…");
    const resp = await fetch("plot_visualizer.py", { cache: "no-cache" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
    pyodide.FS.writeFile("plot_visualizer.py", await resp.text());
    // Owned PyProxy: copy into a top-level global so it survives independently.
    pyodide.runPython("from plot_visualizer import render_plot as _render_plot");
    renderPlot = pyodide.globals.get("_render_plot");

    setControlsEnabled(true);
    hideLoader();
    render();
  } catch (e) {
    hideLoader();
    setError(`Failed during «${step}»:\n${e.message || e}`);
    console.error(`[boot] step="${step}"`, e);
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function bindEvents() {
  $("autoX").addEventListener("change", () => { syncBorderEnable(); scheduleRender(); });
  $("autoY").addEventListener("change", () => { syncBorderEnable(); scheduleRender(); });
  for (const id of ["xlo", "xhi", "ylo", "yhi", "title"]) {
    $(id).addEventListener("input", scheduleRender);
  }
  $("add-curve").addEventListener("click", () => {
    const row = addCurveRow();
    if (row) {
      row.querySelector(".curve-expr").focus();
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  // Start with one curve row pre-populated (the default expression).
  addCurveRow({ expr: "x*sin(3x)" });
  boot();
});
