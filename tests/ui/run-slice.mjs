// run-slice.mjs — deterministic, re-runnable UI regression runner for ONE slice.
//
// Reads tests/ui/tests.json, selects every test whose `slice` matches the slice
// name passed on the CLI, and drives each test's steps through the existing
// harness (lib/server.mjs + lib/browser.mjs). For every step it records:
//   - action + human-readable expectation,
//   - a full-page screenshot AND a #map-clip screenshot (proof for a reviewer),
//   - the console errors / page errors seen up to that point,
//   - a PASS/FAIL with the precise reason for any failed assertion.
// A per-slice manifest.json is written to tests/ui/proof/<slice>/manifest.json.
//
// Exit code: 0 iff EVERY step of EVERY test in the slice passed; 1 otherwise.
//
// Design goals (for an EXTERNAL ADVERSARIAL REVIEWER):
//   * Deterministic: one fresh page per test, fixed viewport, software GL, no
//     shared state between tests. Re-running after a rebuild reproduces results.
//   * Self-describing: the manifest records the action, the expected outcome,
//     the screenshot path, the live asserted values, and the verdict — so a
//     reviewer can audit a PASS without trusting the runner blindly.
//   * Independent slices: slices never touch each other's proof dir, so the 8
//     slices can run in parallel processes safely.
//
// Usage (from repo root):
//   node tests/ui/run-slice.mjs <slice-name>
//   node tests/ui/run-slice.mjs presets
//   node tests/ui/run-slice.mjs --list           # list slices + test counts
//   node tests/ui/run-slice.mjs --all            # run every slice sequentially
//
// npm:
//   npm --prefix tests/ui run slice -- presets

import path from "node:path";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { startServer } from "./lib/server.mjs";
import {
  launch,
  openExplore,
  waitForMapRendered,
  waitForDeckResponses,
  waitForStableRender,
  captureScreenshots,
  analyzeCanvas,
} from "./lib/browser.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SPEC_PATH = path.join(HERE, "tests.json");
const PROOF_ROOT = path.join(HERE, "proof");

// ---------------------------------------------------------------------------
// Spec loading + slice selection
// ---------------------------------------------------------------------------
async function loadSpec() {
  const raw = await fs.readFile(SPEC_PATH, "utf8");
  const spec = JSON.parse(raw);
  if (!Array.isArray(spec.tests)) throw new Error("tests.json has no `tests` array");
  return spec;
}

function slicesOf(spec) {
  const m = new Map();
  for (const t of spec.tests) {
    if (!m.has(t.slice)) m.set(t.slice, []);
    m.get(t.slice).push(t.id);
  }
  return m;
}

// ---------------------------------------------------------------------------
// In-page helpers injected as page.evaluate bodies. These read LIVE app state
// from window.gb so assertions are verifiable, not just pixel guesses.
// ---------------------------------------------------------------------------

// Returns a snapshot of every piece of state our assertions care about.
async function readState(page) {
  return page.evaluate(() => {
    const gb = window.gb || {};
    const layers = (gb.deck && gb.deck.props && gb.deck.props.layers) || [];
    const ids = layers.map((l) => l && l.id).filter(Boolean);

    // Count POI points actually drawn (sum of data lengths over pois-* layers).
    let poiVisible = 0;
    for (const l of layers) {
      if (l && typeof l.id === "string" && l.id.indexOf("pois-") === 0) {
        const d = (l.props && l.props.data) || l.data || [];
        poiVisible += Array.isArray(d) ? d.length : 0;
      }
    }

    // pois loaded count (sum of category arrays on gb._pois).
    let poisLoaded = null;
    if (gb._pois && typeof gb._pois === "object") {
      poisLoaded = 0;
      for (const k of Object.keys(gb._pois)) {
        const arr = gb._pois[k];
        if (Array.isArray(arr)) poisLoaded += arr.length;
      }
    }

    // Live ArcLayer props (for the arc-look regression).
    let arc = null;
    const arcLayer = layers.find((l) => l && l.id === "arcs");
    if (arcLayer) {
      const p = arcLayer.props || {};
      const srcColor = Array.isArray(p.getSourceColor) ? p.getSourceColor : null;
      const tgtColor = Array.isArray(p.getTargetColor) ? p.getTargetColor : null;
      // Width spread over the loaded arcs: evaluate getWidth across the dataset.
      let minW = Infinity, maxW = -Infinity;
      const data = (gb._arcs && Array.isArray(gb._arcs)) ? gb._arcs : [];
      const gw = p.getWidth;
      if (typeof gw === "function" && data.length) {
        for (let i = 0; i < data.length; i++) {
          const w = gw(data[i], { index: i, data });
          if (typeof w === "number" && isFinite(w)) {
            if (w < minW) minW = w;
            if (w > maxW) maxW = w;
          }
        }
      } else if (typeof gw === "number") {
        minW = maxW = gw;
      }
      arc = {
        greatCircle: !!p.greatCircle,
        getHeightDefined: p.getHeight !== undefined && p.getHeight !== null,
        srcAlpha: srcColor ? srcColor[3] : null,
        tgtAlpha: tgtColor ? tgtColor[3] : null,
        minWidth: isFinite(minW) ? minW : null,
        maxWidth: isFinite(maxW) ? maxW : null,
        widthSpread: isFinite(minW) && isFinite(maxW) ? maxW - minW : null,
      };
    }

    const vs = gb._viewState || {};
    const dom = (id) => document.getElementById(id);
    return {
      fieldKey: gb._fieldKey,
      preset: gb._preset,
      season: gb._season,
      extrude: !!gb._extrude,
      fillAlpha: gb._fillAlpha,
      basemapKey: gb._basemapKey,
      layersOn: gb._layersOn || {},
      arcsLoaded: Array.isArray(gb._arcs) ? gb._arcs.length : null,
      poisLoaded,
      poiVisible,
      deckLayerIds: ids,
      arc,
      viewZoom: vs.zoom,
      viewPitch: vs.pitch,
      seasonDisabled: dom("season-select") ? dom("season-select").disabled : null,
      methodologyOpen: dom("methodology") ? dom("methodology").open : null,
      metricSelectValue: dom("metric-select") ? dom("metric-select").value : null,
      presetSelectValue: dom("preset-select") ? dom("preset-select").value : null,
      opacityReadout: dom("hex-opacity-val") ? dom("hex-opacity-val").textContent : null,
      extrudeHintOn: dom("extrude-hint")
        ? dom("extrude-hint").classList.contains("is-on")
        : null,
    };
  });
}

// Which lazily-fetched data file(s) does this step trigger on first enable?
// Returning the right basename lets the runner arm a page.waitForResponse
// barrier (browser.mjs DATA_FILES) so we block on the bytes BEFORE waiting for
// the deck to redraw them — closing the "data not yet drawn" race. A cached
// re-enable simply never re-requests and the barrier no-ops (see
// waitForDeckResponses). Conservative: only the first enable in a session
// actually fetches, but arming the wait on every matching step is harmless.
const RHYTHM_FIELDS = new Set([
  "am_peak_share", "pm_peak_share", "midday_share", "night_share", "peak_hour_bucket",
]);
function dataFilesForStep(step) {
  const { action, selector, value } = step;
  if ((action === "check") && selector === "toggle-arcs") return ["arcs.json"];
  if ((action === "check") && selector === "toggle-pois") return ["pois.json"];
  if (action === "select" && selector === "metric-select" && RHYTHM_FIELDS.has(value)) return ["rhythm.json"];
  if (action === "select" && selector === "month-select" && value) return ["seasons.json"];
  if (action === "select" && selector === "season-select" && value) return ["seasons.json"];
  return [];
}

// Apply one control interaction in-page and return when the synchronous part is
// done. Lazy fetches (arcs/pois/seasons/rhythm) are awaited via an explicit
// page.waitForResponse barrier (dataFilesForStep) PLUS the stable-render barrier
// the step loop runs before each screenshot/expect.
async function applyAction(page, step) {
  const { action, selector, value } = step;
  switch (action) {
    case "select":
      await page.evaluate(({ selector, value }) => {
        const el = document.getElementById(selector);
        el.value = value;
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }, { selector, value });
      break;
    case "check":
      await page.evaluate((selector) => {
        const el = document.getElementById(selector);
        if (!el.checked) { el.checked = true; el.dispatchEvent(new Event("change", { bubbles: true })); }
      }, selector);
      break;
    case "uncheck":
      await page.evaluate((selector) => {
        const el = document.getElementById(selector);
        if (el.checked) { el.checked = false; el.dispatchEvent(new Event("change", { bubbles: true })); }
      }, selector);
      break;
    case "range":
      await page.evaluate(({ selector, value }) => {
        const el = document.getElementById(selector);
        el.value = String(value);
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }, { selector, value });
      break;
    case "flyTo":
      await page.evaluate(async (partial) => {
        const gb = window.gb;
        gb._flyTo(partial);
        // _flyTo sets _viewState + deck.initialViewState, but the POI zoom gate
        // is re-evaluated inside _render(), which the app triggers from deck's
        // onViewStateChange -> _scheduleRender() on a REAL camera move. A
        // programmatic _flyTo does not fire onViewStateChange, so we invoke the
        // SAME path the app uses on camera change (_scheduleRender) to re-run the
        // gate deterministically. This is faithful: it is exactly what a user's
        // drag/scroll past z10.5 does.
        if (typeof gb._scheduleRender === "function") gb._scheduleRender();
        else gb._render();
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      }, value);
      break;
    case "goto":
    case "screenshot":
    case "wait":
    case "expect":
      // handled by the step loop, not here
      break;
    default:
      throw new Error(`unknown action: ${action}`);
  }
}

// ---------------------------------------------------------------------------
// Assertion engine. Returns { ok, failures[], asserted{} } for a step's expect.
// Every assertion maps to a single human-readable failure string so the
// manifest tells a reviewer EXACTLY what broke.
// ---------------------------------------------------------------------------
function evalExpect(expect, state, canvas, diag) {
  const failures = [];
  const asserted = {};
  const E = expect || {};

  const num = (v) => (typeof v === "number" ? v : Number(v));

  if (E.field !== undefined) {
    asserted.field = state.fieldKey;
    if (state.fieldKey !== E.field) failures.push(`field: expected ${E.field}, got ${state.fieldKey}`);
  }
  if (E.preset !== undefined) {
    asserted.preset = state.preset;
    if (state.preset !== E.preset) failures.push(`preset: expected ${E.preset}, got ${state.preset}`);
  }
  if (E.season !== undefined) {
    asserted.season = state.season;
    if ((state.season || "") !== E.season) failures.push(`season: expected '${E.season}', got '${state.season}'`);
  }
  if (E.extrude !== undefined) {
    asserted.extrude = state.extrude;
    if (state.extrude !== E.extrude) failures.push(`extrude: expected ${E.extrude}, got ${state.extrude}`);
  }
  if (E.fillAlphaApprox !== undefined) {
    const tol = E.fillAlphaTol != null ? E.fillAlphaTol : 2;
    asserted.fillAlpha = state.fillAlpha;
    if (Math.abs(num(state.fillAlpha) - E.fillAlphaApprox) > tol)
      failures.push(`fillAlpha: expected ${E.fillAlphaApprox}+/-${tol}, got ${state.fillAlpha}`);
  }
  if (E.basemapKey !== undefined) {
    asserted.basemapKey = state.basemapKey;
    if (state.basemapKey !== E.basemapKey) failures.push(`basemapKey: expected ${E.basemapKey}, got ${state.basemapKey}`);
  }
  if (E.layerOn !== undefined) {
    asserted.layersOn = state.layersOn;
    for (const k of Object.keys(E.layerOn)) {
      if (!!state.layersOn[k] !== !!E.layerOn[k])
        failures.push(`layerOn.${k}: expected ${E.layerOn[k]}, got ${!!state.layersOn[k]}`);
    }
  }
  if (E.deckLayerIds !== undefined) {
    asserted.deckLayerIds = state.deckLayerIds;
    for (const id of E.deckLayerIds) {
      if (!state.deckLayerIds.includes(id))
        failures.push(`deckLayerIds: missing '${id}' (have [${state.deckLayerIds.join(", ")}])`);
    }
  }
  if (E.deckLayerAbsent !== undefined) {
    asserted.deckLayerIds = state.deckLayerIds;
    for (const id of E.deckLayerAbsent) {
      if (state.deckLayerIds.includes(id))
        failures.push(`deckLayerAbsent: '${id}' should NOT be present (have [${state.deckLayerIds.join(", ")}])`);
    }
  }
  if (E.arcsLoaded !== undefined) {
    asserted.arcsLoaded = state.arcsLoaded;
    if (state.arcsLoaded !== E.arcsLoaded)
      failures.push(`arcsLoaded: expected ${E.arcsLoaded}, got ${state.arcsLoaded}`);
  }
  if (E.poisLoaded !== undefined) {
    asserted.poisLoaded = state.poisLoaded;
    if (state.poisLoaded !== E.poisLoaded)
      failures.push(`poisLoaded: expected ${E.poisLoaded}, got ${state.poisLoaded}`);
  }
  if (E.poiCountVisible !== undefined) {
    asserted.poiCountVisible = state.poiVisible;
    if (state.poiVisible !== E.poiCountVisible)
      failures.push(`poiCountVisible: expected ${E.poiCountVisible}, got ${state.poiVisible}`);
  }
  if (E.zoomAtLeast !== undefined) {
    asserted.viewZoom = state.viewZoom;
    if (!(num(state.viewZoom) >= E.zoomAtLeast))
      failures.push(`zoomAtLeast: expected >=${E.zoomAtLeast}, got ${state.viewZoom}`);
  }
  if (E.zoomAtMost !== undefined) {
    asserted.viewZoom = state.viewZoom;
    if (!(num(state.viewZoom) <= E.zoomAtMost))
      failures.push(`zoomAtMost: expected <=${E.zoomAtMost}, got ${state.viewZoom}`);
  }
  if (E.pitchApprox !== undefined) {
    asserted.viewPitch = state.viewPitch;
    if (Math.abs(num(state.viewPitch) - E.pitchApprox) > 3)
      failures.push(`pitchApprox: expected ${E.pitchApprox}+/-3, got ${state.viewPitch}`);
  }
  if (E.seasonDisabled !== undefined) {
    asserted.seasonDisabled = state.seasonDisabled;
    if (state.seasonDisabled !== E.seasonDisabled)
      failures.push(`seasonDisabled: expected ${E.seasonDisabled}, got ${state.seasonDisabled}`);
  }
  if (E.methodologyOpen !== undefined) {
    asserted.methodologyOpen = state.methodologyOpen;
    if (state.methodologyOpen !== E.methodologyOpen)
      failures.push(`methodologyOpen: expected ${E.methodologyOpen}, got ${state.methodologyOpen}`);
  }
  if (E.metricSelectValue !== undefined) {
    asserted.metricSelectValue = state.metricSelectValue;
    if (state.metricSelectValue !== E.metricSelectValue)
      failures.push(`metricSelectValue: expected ${E.metricSelectValue}, got ${state.metricSelectValue}`);
  }
  if (E.presetSelectValue !== undefined) {
    asserted.presetSelectValue = state.presetSelectValue;
    if (state.presetSelectValue !== E.presetSelectValue)
      failures.push(`presetSelectValue: expected ${E.presetSelectValue}, got ${state.presetSelectValue}`);
  }
  if (E.opacityReadout !== undefined) {
    asserted.opacityReadout = state.opacityReadout;
    if (state.opacityReadout !== E.opacityReadout)
      failures.push(`opacityReadout: expected ${E.opacityReadout}, got ${state.opacityReadout}`);
  }
  if (E.extrudeHintOn !== undefined) {
    asserted.extrudeHintOn = state.extrudeHintOn;
    if (state.extrudeHintOn !== E.extrudeHintOn)
      failures.push(`extrudeHintOn: expected ${E.extrudeHintOn}, got ${state.extrudeHintOn}`);
  }
  if (E.arcLayerProps !== undefined) {
    asserted.arc = state.arc;
    const a = state.arc;
    const want = E.arcLayerProps;
    if (!a) {
      failures.push("arcLayerProps: no live ArcLayer found in deck (arcs not rendered)");
    } else {
      if (want.greatCircle !== undefined && a.greatCircle !== want.greatCircle)
        failures.push(`arcLayerProps.greatCircle: expected ${want.greatCircle}, got ${a.greatCircle}`);
      if (want.getHeightDefined !== undefined && a.getHeightDefined !== want.getHeightDefined)
        failures.push(`arcLayerProps.getHeightDefined: expected ${want.getHeightDefined}, got ${a.getHeightDefined}`);
      if (want.srcAlpha !== undefined && a.srcAlpha !== want.srcAlpha)
        failures.push(`arcLayerProps.srcAlpha: expected ${want.srcAlpha}, got ${a.srcAlpha}`);
      if (want.tgtAlpha !== undefined && a.tgtAlpha !== want.tgtAlpha)
        failures.push(`arcLayerProps.tgtAlpha: expected ${want.tgtAlpha}, got ${a.tgtAlpha}`);
      if (want.widthSpread !== undefined && !(num(a.widthSpread) >= want.widthSpread))
        failures.push(`arcLayerProps.widthSpread: expected >=${want.widthSpread}px, got ${a.widthSpread} (min ${a.minWidth}, max ${a.maxWidth})`);
    }
  }
  if (E.canvasRendered === true) {
    const floor = E.canvasMinColors != null ? E.canvasMinColors : 12;
    asserted.canvas = { distinctColors: canvas.distinctColors, opaqueSamples: canvas.opaqueSamples };
    const ok = canvas.distinctColors >= floor && (canvas.opaqueSamples || 0) > 0;
    if (!ok)
      failures.push(`canvasRendered: blank/uniform — ${canvas.distinctColors} distinct colors (floor ${floor}), ${canvas.opaqueSamples} opaque`);
  }
  if (E.noConsoleErrors === true) {
    asserted.consoleErrors = diag.consoleErrors.slice();
    asserted.pageErrors = diag.pageErrors.slice();
    if (diag.consoleErrors.length || diag.pageErrors.length)
      failures.push(`noConsoleErrors: ${diag.consoleErrors.length} console error(s), ${diag.pageErrors.length} page error(s)`);
  }

  return { ok: failures.length === 0, failures, asserted };
}

// ---------------------------------------------------------------------------
// Run one test (fresh page) and return its record.
// ---------------------------------------------------------------------------
async function runTest(browser, serverUrl, test, sliceDir) {
  const rec = {
    id: test.id,
    description: test.description,
    pass_criteria: test.pass_criteria,
    pass: true,
    steps: [],
    error: null,
  };

  let opened;
  try {
    // Determine the initial query: a leading `goto` step carries it; otherwise "".
    const firstGoto = test.steps.find((s) => s.action === "goto");
    const query = firstGoto ? (firstGoto.value || "") : "";

    opened = await openExplore(browser, serverUrl, { query });
    const { page, diagnostics } = opened;
    await waitForMapRendered(page);

    let stepIndex = 0;
    for (const step of test.steps) {
      stepIndex += 1;
      const sRec = {
        index: stepIndex,
        action: step.action,
        selector_or_param: step.selector || step.value || null,
        value: step.value !== undefined ? step.value : null,
        expected: describeStep(step),
        screenshot: null,
        canvas_screenshot: null,
        console_errors: [],
        page_errors: [],
        asserted: null,
        status: "PASS",
        failures: [],
      };

      try {
        if (step.action === "goto") {
          // already navigated as the initial load; nothing further to do.
        } else {
          // Arm a *.json response barrier around the action so a lazy fetch
          // (arcs/pois/rhythm/seasons) is on the wire before we wait for the
          // deck to draw it. Closes the data-loaded-but-not-yet-drawn race.
          const files = dataFilesForStep(step);
          await waitForDeckResponses(page, files, () => applyAction(page, step));
        }
        if (step.wait) await page.waitForTimeout(step.wait);

        // Before any PROOF (screenshot or pixel assertion), block until the deck
        // canvas stops changing. The fixed `wait` above is a floor, not a
        // guarantee: deck.gl can hold loaded data ~500ms before drawing it, so a
        // naive screenshot/expect here yields a FALSE NEGATIVE (e.g. zero arc
        // pixels). waitForStableRender polls the ACTUAL drawn pixels until settled.
        if (step.action === "screenshot" || step.action === "expect") {
          await waitForStableRender(page).catch(() => {});
        }

        if (step.action === "screenshot") {
          // captureScreenshots also stabilizes, but we've already settled above;
          // skip the redundant inner wait.
          const shots = await captureScreenshots(page, sliceDir, `${test.id}__${step.value}`, { waitStable: false });
          sRec.screenshot = relProof(shots.fullPagePath);
          sRec.canvas_screenshot = relProof(shots.canvasPath);
        }

        if (step.action === "expect") {
          const state = await readState(page);
          const canvas = await analyzeCanvas(page);
          const ev = evalExpect(step.value, state, canvas, diagnostics);
          sRec.asserted = ev.asserted;
          sRec.failures = ev.failures;
          sRec.status = ev.ok ? "PASS" : "FAIL";
          if (!ev.ok) rec.pass = false;
        }
      } catch (err) {
        sRec.status = "FAIL";
        sRec.failures.push(`step threw: ${String(err && err.message ? err.message : err)}`);
        rec.pass = false;
      }

      // Snapshot the console state seen so far at every step for the reviewer.
      sRec.console_errors = diagnostics.consoleErrors.slice();
      sRec.page_errors = diagnostics.pageErrors.slice();
      rec.steps.push(sRec);
    }
  } catch (err) {
    rec.pass = false;
    rec.error = String(err && err.stack ? err.stack : err);
  } finally {
    if (opened && opened.dispose) await opened.dispose().catch(() => {});
  }
  return rec;
}

function describeStep(step) {
  switch (step.action) {
    case "goto": return `load explore.html?${step.value || ""}`;
    case "select": return `set #${step.selector} = "${step.value}" (change)`;
    case "check": return `tick #${step.selector}`;
    case "uncheck": return `untick #${step.selector}`;
    case "range": return `slide #${step.selector} to ${step.value}`;
    case "flyTo": return `camera flyTo ${JSON.stringify(step.value)}`;
    case "wait": return `wait ${step.value || step.wait}ms`;
    case "screenshot": return `capture screenshot "${step.value}"`;
    case "expect": return `assert ${JSON.stringify(step.value)}`;
    default: return step.action;
  }
}

function relProof(abs) {
  return path.relative(PROOF_ROOT, abs);
}

// ---------------------------------------------------------------------------
// Slice driver
// ---------------------------------------------------------------------------
async function runSlice(sliceName) {
  const spec = await loadSpec();
  const tests = spec.tests.filter((t) => t.slice === sliceName);
  if (!tests.length) {
    const known = [...slicesOf(spec).keys()].sort().join(", ");
    throw new Error(`no tests for slice "${sliceName}". Known slices: ${known}`);
  }

  const sliceDir = path.join(PROOF_ROOT, sliceName);
  // Clean prior proof for this slice ONLY (independence; never touch siblings).
  await fs.rm(sliceDir, { recursive: true, force: true });
  await fs.mkdir(sliceDir, { recursive: true });

  const manifest = {
    slice: sliceName,
    started_at: new Date().toISOString(),
    finished_at: null,
    total: tests.length,
    passed: 0,
    failed: 0,
    gl_renderer: null,
    server_url: null,
    tests: [],
  };

  let server;
  let browser;
  try {
    server = await startServer();
    manifest.server_url = server.url;
    console.error(`[${sliceName}] server up at ${server.url}`);

    const launched = await launch();
    browser = launched.browser;

    // Probe GL once for the manifest header (reviewer wants to know it's software GL).
    {
      const probe = await openExplore(browser, server.url, { query: "" });
      manifest.gl_renderer = probe.gl.renderer;
      await probe.dispose().catch(() => {});
    }

    for (const test of tests) {
      console.error(`[${sliceName}] running ${test.id} …`);
      const rec = await runTest(browser, server.url, test, sliceDir);
      manifest.tests.push(rec);
      if (rec.pass) manifest.passed += 1; else manifest.failed += 1;
      console.error(`[${sliceName}]   ${rec.pass ? "PASS" : "FAIL"} ${test.id}`);
    }
  } finally {
    if (browser) await browser.close().catch(() => {});
    if (server) await server.stop().catch(() => {});
  }

  manifest.finished_at = new Date().toISOString();
  const manifestPath = path.join(sliceDir, "manifest.json");
  await fs.writeFile(manifestPath, JSON.stringify(manifest, null, 2));
  console.error(
    `[${sliceName}] DONE — ${manifest.passed}/${manifest.total} passed. Manifest: ${manifestPath}`
  );
  return manifest;
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------
async function main() {
  const arg = process.argv[2];

  if (!arg || arg === "--help" || arg === "-h") {
    const spec = await loadSpec();
    const slices = slicesOf(spec);
    console.log("Usage: node tests/ui/run-slice.mjs <slice-name> | --list | --all");
    console.log("Slices:");
    for (const [name, ids] of slices) console.log(`  ${name}  (${ids.length} tests)`);
    process.exit(arg ? 0 : 1);
    return;
  }

  if (arg === "--list") {
    const spec = await loadSpec();
    const slices = slicesOf(spec);
    for (const [name, ids] of slices) console.log(`${name}\t${ids.length}\t${ids.join(",")}`);
    process.exit(0);
    return;
  }

  if (arg === "--all") {
    const spec = await loadSpec();
    const slices = [...slicesOf(spec).keys()];
    let anyFail = false;
    for (const s of slices) {
      const m = await runSlice(s);
      if (m.failed > 0) anyFail = true;
    }
    process.exit(anyFail ? 1 : 0);
    return;
  }

  const manifest = await runSlice(arg);
  process.exit(manifest.failed > 0 ? 1 : 0);
}

main().catch((e) => {
  console.error("[run-slice] fatal:", e && e.stack ? e.stack : e);
  process.exit(1);
});
