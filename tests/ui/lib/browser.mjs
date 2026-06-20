// browser.mjs — headless-chromium helper for the deck.gl geo-browser.
//
// deck.gl renders the H3 hexes to a WebGL <canvas>. A headless container has no
// GPU, so we force ANGLE's software backend (SwiftShader). Without these flags
// the WebGL context silently fails and the canvas stays blank.
//
// Public API:
//   launch()                       -> { browser, close }
//   openExplore(browser, baseUrl, { query })
//                                  -> { page, diagnostics, gl, dispose }
//   waitForMapRendered(page)       -> resolves once gb + deck + hex data exist
//   waitForDeckResponses(page, fn) -> arms a *.json response barrier around an action
//   waitForStableRender(page)      -> resolves once the deck canvas stops changing
//   captureScreenshots(page, dir, name)
//                                  -> { fullPagePath, canvasPath }
//   analyzeCanvas(page)            -> { rendered, reason, distinctColors, ... }
//   sampleDeckOverlay(page)        -> low-level opaque/distinct readback of #map-deck
//   assertDeckIsolated(page)       -> proves analyzeCanvas reads the DECK, not basemap
//
// All ES modules. Software-GL flags are centralized in SOFTWARE_GL_FLAGS so the
// fallback puppeteer path (puppeteer-core) could reuse them verbatim.

import { chromium } from "playwright";
import fs from "node:fs/promises";
import path from "node:path";

// Software WebGL via ANGLE + SwiftShader. `--enable-unsafe-swiftshader` is
// required on recent Chromium (the safe path is gated behind an allowlist).
export const SOFTWARE_GL_FLAGS = [
  "--use-gl=angle",
  "--use-angle=swiftshader",
  "--enable-unsafe-swiftshader",
  "--ignore-gpu-blocklist",
  "--enable-webgl",
  "--disable-gpu-sandbox",
  // Container-friendliness (no /dev/shm pressure, no setuid sandbox needs).
  "--no-sandbox",
  "--disable-dev-shm-usage",
];

/** Launch a headless chromium with software WebGL. */
export async function launch({ headless = true } = {}) {
  const browser = await chromium.launch({
    headless,
    args: SOFTWARE_GL_FLAGS,
  });
  return { browser, close: () => browser.close() };
}

/**
 * Open /explore.html and attach diagnostic collectors.
 * @returns page + a live `diagnostics` object the caller can read after waits.
 */
export async function openExplore(browser, baseUrl, { query = "" } = {}) {
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();

  const diagnostics = {
    consoleMessages: [],   // { type, text, location }
    consoleErrors: [],     // text of type === 'error'
    pageErrors: [],        // uncaught exceptions (stringified)
    failedRequests: [],    // { url, failure, method }
  };

  page.on("console", (msg) => {
    const rec = { type: msg.type(), text: msg.text(), location: msg.location() };
    diagnostics.consoleMessages.push(rec);
    if (msg.type() === "error") diagnostics.consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => {
    diagnostics.pageErrors.push(String(err && err.stack ? err.stack : err));
  });
  page.on("requestfailed", (req) => {
    const f = req.failure();
    diagnostics.failedRequests.push({
      url: req.url(),
      method: req.method(),
      failure: f ? f.errorText : "unknown",
    });
  });

  const url = baseUrl.replace(/\/$/, "") + "/explore.html" + (query ? "?" + query : "");
  // hexes.json is ~67MB; "load" can be slow, so we navigate with a generous
  // timeout and then wait on explicit app readiness signals below.
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });

  const gl = await probeWebGL(page);

  const dispose = async () => { await context.close(); };
  return { page, diagnostics, gl, dispose };
}

/** Confirm a real WebGL context + report the (software) renderer string. */
export async function probeWebGL(page) {
  return page.evaluate(() => {
    const c = document.createElement("canvas");
    const gl = c.getContext("webgl2") || c.getContext("webgl");
    if (!gl) return { ok: false, renderer: null, version: null };
    let renderer = null;
    try {
      const ext = gl.getExtension("WEBGL_debug_renderer_info");
      renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    } catch { renderer = gl.getParameter(gl.RENDERER); }
    return { ok: true, renderer, version: gl.getParameter(gl.VERSION) };
  });
}

/**
 * Wait until the map has actually rendered:
 *   1. the deck canvas (#map-deck) is in the DOM and sized,
 *   2. window.gb (the GeoBrowser instance) exists with its deck + hex data,
 *   3. network is idle (CDN libs + tiles + 67MB hexes.json settled),
 *   4. a short settle delay so deck.gl finishes its first GPU draw.
 */
export async function waitForMapRendered(page, { settleMs = 1500 } = {}) {
  // The explore page exposes window.gb and appends a <canvas id="map-deck">.
  await page.waitForSelector("#map-deck", { state: "attached", timeout: 60000 });

  await page.waitForFunction(
    () => {
      const gb = window.gb;
      if (!gb || !gb.deck) return false;
      const hexCount = Array.isArray(gb._hexes) ? gb._hexes.length : 0;
      const cv = document.getElementById("map-deck");
      const sized = cv && cv.width > 0 && cv.height > 0;
      return hexCount > 0 && sized;
    },
    { timeout: 90000, polling: 250 }
  );

  // Let tiles + the deck draw quiesce.
  await page.waitForLoadState("networkidle", { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(settleMs);
}

// Data files a step may lazily fetch on its first enable. We arm a
// page.waitForResponse barrier on these so the harness blocks until the bytes
// arrive AND we can then wait for the deck to actually redraw them. Matching is
// by basename so a step can name e.g. "arcs.json" regardless of dataBase.
export const DATA_FILES = [
  "arcs.json",
  "pois.json",
  "rhythm.json",
  "seasons.json",
  "hexes.json",
  "manifest.json",
];

/**
 * Run `action()` (which triggers a control interaction) while waiting for the
 * named data-file network responses it is expected to load. Returns once the
 * action has run AND every expected response has resolved (200..399). A file
 * already in the HTTP cache resolves immediately; one that never loads simply
 * times out the inner wait and is ignored (the stable-render barrier is the
 * real proof) — so this never hangs a step that legitimately re-uses cached
 * data. Pass an empty `files` array for actions that fetch nothing.
 *
 * @param {import('playwright').Page} page
 * @param {string[]} files       basenames from DATA_FILES this step loads
 * @param {() => Promise<any>} action
 */
export async function waitForDeckResponses(page, files, action, { timeout = 30000 } = {}) {
  const wanted = (files || []).filter((f) => DATA_FILES.includes(f));
  const waiters = wanted.map((f) =>
    page
      .waitForResponse(
        (resp) => {
          try {
            const u = new URL(resp.url());
            return u.pathname.endsWith("/" + f) || u.pathname.endsWith(f);
          } catch {
            return false;
          }
        },
        { timeout }
      )
      // A cached re-enable never re-requests; don't fail the step for that.
      .catch(() => null)
  );
  const result = await action();
  await Promise.all(waiters);
  return result;
}

/**
 * Low-level readback of the deck.gl overlay canvas (#map-deck) ONLY. deck.gl
 * renders to its own <canvas> stacked above the MapLibre basemap canvas, so
 * drawing #map-deck onto a 2D scratch canvas isolates the deck overlay — the
 * satellite/basemap lives on a different canvas and never bleeds in. Returns
 * opaque-sample and distinct-colour counts plus an alpha histogram so callers
 * can both detect a blank overlay AND prove the isolation.
 */
export async function sampleDeckOverlay(page, { sampleStep = 11 } = {}) {
  return page.evaluate(({ sampleStep }) => {
    const src = document.getElementById("map-deck");
    if (!src) return { ok: false, reason: "no #map-deck canvas" };
    const w = src.width, h = src.height;
    if (!w || !h) return { ok: false, reason: "canvas has zero size", width: w, height: h };
    const tmp = document.createElement("canvas");
    tmp.width = w; tmp.height = h;
    const ctx = tmp.getContext("2d");
    ctx.drawImage(src, 0, 0);
    let data;
    try {
      data = ctx.getImageData(0, 0, w, h).data;
    } catch (e) {
      return { ok: false, reason: "getImageData failed: " + e.message };
    }
    const colors = new Set();
    let opaque = 0, sampled = 0, alphaZero = 0;
    const stride = 4 * sampleStep;
    for (let i = 0; i < data.length; i += stride) {
      const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
      sampled++;
      if (a > 0) opaque++; else alphaZero++;
      colors.add(`${r >> 3},${g >> 3},${b >> 3}`);
    }
    return {
      ok: true,
      distinctColors: colors.size,
      opaqueSamples: opaque,
      alphaZeroSamples: alphaZero,
      sampledPixels: sampled,
      width: w,
      height: h,
    };
  }, { sampleStep });
}

/**
 * "Wait until stable" barrier — the fix for async render timing (false negatives
 * on arcs/pois). After a layer toggle the DATA can be loaded (gb._arcs filled,
 * the ArcLayer already in deck.props.layers) a FULL ~500ms before deck.gl has
 * actually drawn those primitives to the GL canvas. Asserting / screenshotting
 * in that window proves "zero arc pixels" even though the live render is correct.
 *
 * We pump a couple of requestAnimationFrame ticks (so a queued deck draw runs),
 * then poll the deck-overlay opaque-pixel readback until it is unchanged across
 * `stableReads` consecutive samples (the canvas has settled), then a short final
 * settle. Polling the ACTUAL drawn pixels — not a self-reported flag — is the
 * ground truth an adversarial reviewer can trust.
 */
export async function waitForStableRender(
  page,
  { stableReads = 2, intervalMs = 120, maxMs = 8000, settleMs = 150 } = {}
) {
  // Let any queued deck.gl draw run before we start sampling.
  await page.evaluate(
    () =>
      new Promise((r) =>
        requestAnimationFrame(() => requestAnimationFrame(r))
      )
  );

  const deadline = Date.now() + maxMs;
  let prevKey = null;
  let stable = 0;
  let last = null;
  while (Date.now() < deadline) {
    const s = await sampleDeckOverlay(page);
    last = s;
    const key = s.ok ? `${s.opaqueSamples}/${s.distinctColors}` : "err";
    if (key === prevKey) {
      stable += 1;
      if (stable >= stableReads) break;
    } else {
      stable = 0;
      prevKey = key;
    }
    await page.waitForTimeout(intervalMs);
  }
  // Final settle so the screenshot grabs a fully-composited frame.
  await page.waitForTimeout(settleMs);
  return last;
}

/**
 * Self-check that analyzeCanvas isolates the DECK overlay and is NOT reading the
 * basemap. Turns the deck layers OFF (empty deck), samples the overlay (must go
 * transparent: opaqueSamples ~0), then restores the layers and samples again
 * (overlay opaque again). If "deck off" still scored as opaque, the probe would
 * be counting the satellite basemap bleeding through — the exact failure the
 * contrarian review flagged. Returns the before/empty/after readings + verdict.
 * Non-destructive: it restores the prior layer state before returning.
 */
export async function assertDeckIsolated(page, { emptyOpaqueMax = 8 } = {}) {
  const before = await sampleDeckOverlay(page);
  // Snapshot + clear deck layers, render, sample, restore.
  await page.evaluate(() => {
    const gb = window.gb;
    window.__uiPrevLayersOn = JSON.parse(JSON.stringify(gb._layersOn || {}));
    gb._layersOn = { hexes: false, arcs: false, pois: false };
    gb._render();
  });
  await waitForStableRender(page, { settleMs: 120 });
  const empty = await sampleDeckOverlay(page);
  await page.evaluate(() => {
    const gb = window.gb;
    if (window.__uiPrevLayersOn) gb._layersOn = window.__uiPrevLayersOn;
    delete window.__uiPrevLayersOn;
    gb._render();
  });
  await waitForStableRender(page, { settleMs: 120 });
  const after = await sampleDeckOverlay(page);

  const isolated = empty.ok && empty.opaqueSamples <= emptyOpaqueMax;
  return {
    isolated,
    reason: isolated
      ? "deck overlay reads transparent when layers are off (basemap does NOT bleed in)"
      : `deck-off overlay still opaque (${empty.opaqueSamples} > ${emptyOpaqueMax}) — probe may be reading the basemap`,
    before: before.opaqueSamples,
    emptyOpaque: empty.opaqueSamples,
    after: after.opaqueSamples,
  };
}

/**
 * Map-canvas-only + viewport screenshots. Returns absolute paths.
 *
 * IMPORTANT (3D/extrude fix): we do NOT use `fullPage: true`. With extrude on
 * (pitch>0) MapLibre+deck run a continuous render loop, so the page never
 * reaches the "stable" state Playwright's full-page capture waits for and the
 * call times out (~30s). A viewport capture + `animations: "disabled"` grabs the
 * same 1600x1000 frame reliably in ~250ms. We also wait for the deck draw to be
 * stable first so the capture is never a mid-draw frame.
 */
export async function captureScreenshots(page, outDir, name, { waitStable = true } = {}) {
  await fs.mkdir(outDir, { recursive: true });
  const fullPagePath = path.join(outDir, `${name}.png`);
  const canvasPath = path.join(outDir, `${name}.canvas.png`);

  if (waitStable) {
    await waitForStableRender(page).catch(() => {});
  }

  // Viewport capture (NOT fullPage — fullPage hangs whenever pitch>0). The map
  // fills the viewport, so this is the same proof the reviewer wants, minus the
  // 30s font/stability barrier that full-page imposes.
  await page.screenshot({ path: fullPagePath, animations: "disabled" });

  // Map-canvas-only: clip to the #map container (holds both the MapLibre and
  // deck canvases, stacked). element.screenshot on #map-deck alone can come
  // back transparent for a GPU canvas, so we clip the container box instead.
  const box = await page.locator("#map").boundingBox();
  if (box) {
    await page.screenshot({
      path: canvasPath,
      animations: "disabled",
      clip: {
        x: Math.max(0, box.x),
        y: Math.max(0, box.y),
        width: Math.round(box.width),
        height: Math.round(box.height),
      },
    });
  } else {
    // Fallback: copy the viewport shot so the path always exists.
    await fs.copyFile(fullPagePath, canvasPath);
  }
  return { fullPagePath, canvasPath };
}

/**
 * Decide whether the DECK overlay actually rendered, by sampling the deck.gl
 * canvas (#map-deck) ONLY — see sampleDeckOverlay for why that isolates the deck
 * from the MapLibre basemap. A blank/uniform/transparent overlay means the deck
 * draw produced nothing (WebGL draw failed, or the data layer is genuinely
 * absent). `opaqueSamples > 0` is the load-bearing signal: because the readback
 * is the deck canvas alone, the satellite basemap can never inflate it — when
 * the deck overlay is empty (opacity 0 / layer off) opaqueSamples is ~0, NOT the
 * full sample count. We additionally require a minimum number of distinct colors
 * to reject a single-colour fill that is not real data.
 */
export async function analyzeCanvas(page, { minDistinct = 12, sampleStep = 11 } = {}) {
  const s = await sampleDeckOverlay(page, { sampleStep });
  if (!s.ok) {
    return { rendered: false, reason: s.reason, distinctColors: 0, opaqueSamples: 0 };
  }
  const distinct = s.distinctColors;
  const opaque = s.opaqueSamples;
  const rendered = distinct >= minDistinct && opaque > 0;
  return {
    rendered,
    reason: rendered ? "ok" : `only ${distinct} distinct colors / ${opaque} opaque samples`,
    distinctColors: distinct,
    opaqueSamples: opaque,
    alphaZeroSamples: s.alphaZeroSamples,
    sampledPixels: s.sampledPixels,
    width: s.width,
    height: s.height,
  };
}
