// run-flowmap-phase1.mjs — headless verification for the FLOWMAP variant Phase 1
// vendoring gate (docs/flows.html + docs/app/flows_fm/main.js).
//
// Asserts:
//   1. flowmap_renders        — the deck canvas draws non-blank flow geometry
//                               (distinct colours + opaque pixels on #deck-canvas),
//                               AND window.__fm.deck holds a FlowmapLayer.
//   2. zero_cdn_requests      — NO request to esm.sh / unpkg / jsdelivr / skypack /
//                               cdn.jsdelivr / any non-same-origin host for
//                               app/renderer code. Keyless basemap tile hosts
//                               (carto, openstreetmap, arcgis, openfreemap) are
//                               EXEMPT.
//   3. single_deck_instance   — exactly one deck.gl instance; no duplicate-deck
//                               console warning ("multiple versions of deck.gl").
//   4. console_clean          — no console errors / page errors.
// Screenshots to tests/ui/proof/_fm/phase1.png (+ .canvas.png).
//
// Reuses the project's server.mjs (static docs/ over a free port) and the
// software-GL chromium launch flags from browser.mjs.

import path from "node:path";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { startServer } from "./lib/server.mjs";
import { launch, captureScreenshots, waitForStableRender } from "./lib/browser.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PROOF_DIR = path.join(HERE, "proof", "_fm");

// Hosts that are LEGITIMATE same-origin-exempt keyless basemap tile/style sources.
// A request to one of these is NOT a CDN violation for our app/renderer code.
const BASEMAP_HOST_RE =
  /(^|\.)(basemaps\.cartocdn\.com|cartocdn\.com|carto\.com|tiles\.openfreemap\.org|openfreemap\.org|server\.arcgisonline\.com|arcgisonline\.com|tile\.openstreetmap\.org|openstreetmap\.org|demotiles\.maplibre\.org)$/i;

// Hosts that, if hit for app/renderer code, are an OUTRIGHT FAILURE of the no-CDN
// guarantee. We flag ANY non-loopback host that is not an allowed basemap host,
// but we name these explicitly in the report for clarity.
const FORBIDDEN_CDN_RE =
  /(esm\.sh|unpkg\.com|jsdelivr\.net|cdn\.jsdelivr|skypack\.dev|cdnjs|jspm\.io|ga\.jspm\.io|esm\.run|deno\.land)/i;

function classifyRequest(urlStr, loopbackHost) {
  let u;
  try { u = new URL(urlStr); } catch { return { kind: "unparseable", urlStr }; }
  if (u.protocol === "data:" || u.protocol === "blob:") return { kind: "inline" };
  const host = u.hostname;
  if (host === "127.0.0.1" || host === "localhost" || host === loopbackHost) {
    return { kind: "same-origin", host, path: u.pathname };
  }
  if (BASEMAP_HOST_RE.test(host)) return { kind: "basemap-exempt", host, urlStr };
  return { kind: "third-party", host, urlStr, forbidden: FORBIDDEN_CDN_RE.test(host) };
}

async function main() {
  await fs.mkdir(PROOF_DIR, { recursive: true });
  const srv = await startServer();
  const loopbackHost = new URL(srv.url).hostname;
  const { browser, close } = await launch();

  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();

  const consoleErrors = [];
  const consoleWarnings = [];
  const pageErrors = [];
  const allRequests = [];
  const failedRequests = [];

  page.on("console", (msg) => {
    const text = msg.text();
    if (msg.type() === "error") consoleErrors.push(text);
    if (msg.type() === "warning") consoleWarnings.push(text);
  });
  page.on("pageerror", (err) => pageErrors.push(String(err && err.stack ? err.stack : err)));
  page.on("request", (req) => allRequests.push(req.url()));
  page.on("requestfailed", (req) => {
    const f = req.failure();
    failedRequests.push({ url: req.url(), failure: f ? f.errorText : "unknown" });
  });

  const url = srv.url.replace(/\/$/, "") + "/flows.html";
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });

  // Wait for the app to boot: window.__fm + a FlowmapLayer in the deck props.
  let bootInfo = { ready: false };
  try {
    await page.waitForFunction(
      () => {
        const fm = window.__fm;
        if (!fm || !fm.deck) return false;
        const layers = (fm.deck.props && fm.deck.props.layers) || [];
        const flat = layers.flat ? layers.flat(Infinity) : layers;
        return flat.some((l) => l && l.id === "od-flowmap");
      },
      { timeout: 45000, polling: 250 }
    );
    bootInfo.ready = true;
  } catch (e) {
    bootInfo.error = String(e);
  }

  // Introspect the live deck/flowmap state.
  bootInfo = {
    ...bootInfo,
    ...(await page.evaluate(() => {
      const out = {};
      const fm = window.__fm;
      out.hasApp = !!fm;
      out.hasDeck = !!(fm && fm.deck);
      // The FlowmapLayer in the deck props.
      const layers = (fm && fm.deck && fm.deck.props && fm.deck.props.layers) || [];
      const flat = layers.flat ? layers.flat(Infinity) : layers;
      const fl = flat.find((l) => l && l.id === "od-flowmap");
      out.hasFlowmapLayer = !!fl;
      out.flowmapLayerClass = fl ? fl.constructor.name : null;
      // Sub-layers the FlowmapLayer composited (proves it actually rendered children).
      try {
        const subs = fl && fl.getSubLayers ? fl.getSubLayers() : [];
        out.subLayerCount = subs.length;
        out.subLayerClasses = subs.map((s) => s.constructor.name);
      } catch (e) { out.subLayerError = String(e); }
      // ONE deck/luma instance proof.
      out.deckVersion = (globalThis.deck && globalThis.deck.VERSION) || null;
      out.lumaVersion = (globalThis.luma && globalThis.luma.VERSION) || null;
      out.lumaHasGeometry = !!(globalThis.luma && typeof globalThis.luma.Geometry === "function");
      out.lumaHasModel = !!(globalThis.luma && typeof globalThis.luma.Model === "function");
      return out;
    })),
  };

  // Let the flow geometry + particle animation draw, then sample the deck canvas.
  await page.waitForTimeout(1200);
  const stable = await sampleDeckCanvas(page);
  await page.waitForTimeout(400);

  // Screenshot proof.
  const shots = await captureFlowmapShots(page, PROOF_DIR, "phase1");

  // ---- Classify every network request ----
  const classified = allRequests.map((u) => classifyRequest(u, loopbackHost));
  const thirdParty = classified.filter((c) => c.kind === "third-party");
  const forbidden = thirdParty.filter((c) => c.forbidden);
  const basemapExempt = classified.filter((c) => c.kind === "basemap-exempt");
  // Any third-party host (forbidden OR not in the basemap allowlist) is a CDN hit
  // for app/renderer code. The basemap allowlist is the ONLY exemption.
  const zeroCdn = thirdParty.length === 0;

  // ---- Duplicate-deck detection ----
  const dupDeckWarning = consoleWarnings
    .concat(consoleErrors)
    .find((t) => /multiple versions of deck\.gl|duplicate.*deck|deck\.gl.*already|two copies/i.test(t));
  const singleDeck =
    !dupDeckWarning && bootInfo.deckVersion != null && bootInfo.lumaVersion != null;

  // ---- Render verdict ----
  const flowmapRenders =
    bootInfo.ready === true &&
    bootInfo.hasFlowmapLayer === true &&
    stable.ok === true &&
    stable.distinctColors >= 8 &&
    stable.opaqueSamples > 0;

  const consoleClean = consoleErrors.length === 0 && pageErrors.length === 0;

  const report = {
    flowmap_renders: flowmapRenders,
    zero_cdn_requests: zeroCdn,
    single_deck_instance: singleDeck,
    console_clean: consoleClean,
    proof_screenshot: shots.fullPagePath,
    proof_canvas: shots.canvasPath,
    detail: {
      boot: bootInfo,
      deckCanvasSample: stable,
      requestTotals: {
        total: classified.length,
        sameOrigin: classified.filter((c) => c.kind === "same-origin").length,
        basemapExempt: basemapExempt.length,
        thirdParty: thirdParty.length,
        forbiddenCdn: forbidden.length,
        inline: classified.filter((c) => c.kind === "inline").length,
      },
      thirdPartyHosts: [...new Set(thirdParty.map((c) => c.host))],
      forbiddenHosts: [...new Set(forbidden.map((c) => c.host))],
      basemapHosts: [...new Set(basemapExempt.map((c) => c.host))],
      consoleErrors,
      consoleWarnings,
      pageErrors,
      failedRequests,
      dupDeckWarning: dupDeckWarning || null,
    },
  };

  console.log(JSON.stringify(report, null, 2));

  await context.close();
  await close();
  await srv.stop();

  // Non-zero exit if any gate failed (so a CI caller can branch).
  const allPass =
    flowmapRenders && zeroCdn && singleDeck && consoleClean;
  process.exitCode = allPass ? 0 : 1;
}

// Sample the deck.gl overlay canvas (#deck-canvas) directly — it is stacked above
// the MapLibre basemap canvas, so reading it isolates the flow geometry. Pumps a
// couple of rAF ticks then polls until the opaque/distinct readback settles.
async function sampleDeckCanvas(page, { stableReads = 2, intervalMs = 140, maxMs = 9000 } = {}) {
  await page.evaluate(
    () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
  );
  const read = () =>
    page.evaluate(() => {
      const src = document.getElementById("deck-canvas");
      if (!src) return { ok: false, reason: "no #deck-canvas" };
      const w = src.width, h = src.height;
      if (!w || !h) return { ok: false, reason: "zero-size canvas", width: w, height: h };
      const tmp = document.createElement("canvas");
      tmp.width = w; tmp.height = h;
      const ctx = tmp.getContext("2d");
      ctx.drawImage(src, 0, 0);
      let data;
      try { data = ctx.getImageData(0, 0, w, h).data; }
      catch (e) { return { ok: false, reason: "getImageData failed: " + e.message }; }
      const colors = new Set();
      let opaque = 0, sampled = 0;
      const stride = 4 * 11;
      for (let i = 0; i < data.length; i += stride) {
        const a = data[i + 3];
        sampled++;
        if (a > 0) opaque++;
        colors.add(`${data[i] >> 3},${data[i + 1] >> 3},${data[i + 2] >> 3}`);
      }
      return { ok: true, distinctColors: colors.size, opaqueSamples: opaque, sampledPixels: sampled, width: w, height: h };
    });
  const deadline = Date.now() + maxMs;
  let prev = null, stable = 0, last = null;
  while (Date.now() < deadline) {
    const s = await read();
    last = s;
    const key = s.ok ? `${s.opaqueSamples}/${s.distinctColors}` : "err";
    if (key === prev) { if (++stable >= stableReads) break; }
    else { stable = 0; prev = key; }
    await page.waitForTimeout(intervalMs);
  }
  return last;
}

// Viewport + #map-clip screenshots (animations disabled, but the flowmap particle
// layer runs a render loop, so we do NOT use fullPage — same rationale as browser.mjs).
async function captureFlowmapShots(page, outDir, name) {
  await fs.mkdir(outDir, { recursive: true });
  const fullPagePath = path.join(outDir, `${name}.png`);
  const canvasPath = path.join(outDir, `${name}.canvas.png`);
  await page.screenshot({ path: fullPagePath, animations: "disabled" });
  const box = await page.locator("#map").boundingBox();
  if (box) {
    await page.screenshot({
      path: canvasPath,
      animations: "disabled",
      clip: { x: Math.max(0, box.x), y: Math.max(0, box.y), width: Math.round(box.width), height: Math.round(box.height) },
    });
  } else {
    await fs.copyFile(fullPagePath, canvasPath);
  }
  return { fullPagePath, canvasPath };
}

main().catch((e) => {
  console.error("FATAL:", e);
  process.exitCode = 2;
});
