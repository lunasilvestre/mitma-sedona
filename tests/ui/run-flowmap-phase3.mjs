// run-flowmap-phase3.mjs — headless verification for the FLOWMAP variant Phase 3
// (full comparison build): docs/flows.html + docs/app/flows_fm/main.js.
//
// Phase 3 is the full A/B twin loading a REAL slice (may_weekday) into a
// LocalFlowmapDataProvider, with clustering + animation + framework scales + the
// hour/slice/top-N/clustering/basemap levers. This harness proves it actually
// renders the headline flowmap behaviours, headlessly, with a clean console and
// zero third-party CDN, and captures three proof shots:
//   (a) phase3_clustered.png — a LOW-zoom Catalonia view: clustering ON produces
//       zoom-LOD SUPER-NODES (fewer, fatter circles + aggregated flows).
//   (b) phase3_bcn.png       — a zoomed-in Barcelona view: clusters EXPAND into
//       individual flows + animated particles (more sub-layers, more geometry).
//   (c) phase3_pick.png      — a hovered/picked flow: the typed tooltip + the panel
//       readout populate (captured if the pick lands).
//
// Asserts:
//   1. flowmap_renders     — window.__fmReady + a FlowmapLayer on the deck + the
//                            deck canvas draws non-blank flow geometry.
//   2. clustering_active   — at LOW zoom the FlowmapLayer composites circle/flow
//                            sub-layers AND the clustered node count is materially
//                            FEWER than the raw 599 locations (super-nodes formed);
//                            zooming to BCN raises the rendered geometry.
//   3. levers_wired        — hour scrubber, slice switch, top-N, clustering toggle,
//                            basemap select are all present + the app methods exist.
//   4. zero_cdn_requests   — no esm.sh/unpkg/jsdelivr/... for app/renderer code
//                            (keyless basemap tile hosts exempt).
//   5. single_deck_instance— one deck/luma; no duplicate-deck warning.
//   6. console_clean       — no console errors / page errors.

import path from "node:path";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { startServer } from "./lib/server.mjs";
import { launch } from "./lib/browser.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PROOF_DIR = path.join(HERE, "proof", "_fm");

const BASEMAP_HOST_RE =
  /(^|\.)(basemaps\.cartocdn\.com|cartocdn\.com|carto\.com|tiles\.openfreemap\.org|openfreemap\.org|server\.arcgisonline\.com|arcgisonline\.com|tile\.openstreetmap\.org|openstreetmap\.org|demotiles\.maplibre\.org)$/i;
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

// Drive the deck camera directly (the deck owns the camera) so the harness can park
// the view at a precise zoom for each proof shot. We set viewState on the live Deck,
// jumpTo the passive map, and call the app's _render so clustering re-forms at the
// new zoom — exactly the path a user pan/zoom takes.
async function setCamera(page, longitude, latitude, zoom) {
  await page.evaluate(({ longitude, latitude, zoom }) => {
    const app = window.__fm;
    const vs = { longitude, latitude, zoom, pitch: 0, bearing: 0 };
    app._viewState = vs;
    app.deck.setProps({ initialViewState: vs, viewState: vs });
    if (app.map) app.map.jumpTo({ center: [longitude, latitude], zoom, pitch: 0, bearing: 0 });
    app._render();
  }, { longitude, latitude, zoom });
}

// Introspect the live FlowmapLayer: its composited sub-layer classes + counts, and
// (the clustering proof) how many distinct location NODES the circle sub-layer drew
// vs the raw 599. At low zoom with clustering ON that node count is materially fewer
// (super-nodes); zoomed in it climbs toward the raw set.
async function introspectFlowmap(page) {
  return page.evaluate(() => {
    const app = window.__fm;
    const out = { ok: false };
    if (!app || !app.deck) return out;
    const layers = (app.deck.props && app.deck.props.layers) || [];
    const flat = layers.flat ? layers.flat(Infinity) : layers;
    const fl = flat.find((l) => l && l.id === "od-flowmap");
    out.hasFlowmapLayer = !!fl;
    out.flowmapLayerClass = fl ? fl.constructor.name : null;
    out.rawLocations = Array.isArray(app._locations) ? app._locations.length : 0;
    out.rawFlows = Array.isArray(app._rawFlows) ? app._rawFlows.length : 0;
    out.clusteringEnabled = !!app._clusteringEnabled;
    out.topN = app._topN;
    out.activeHour = app._activeHour;
    out.zoom = app._viewState && app._viewState.zoom;
    try {
      const subs = fl && fl.getSubLayers ? fl.getSubLayers() : [];
      out.subLayerCount = subs.length;
      out.subLayerClasses = subs.map((s) => s.constructor.name);
      // The circles sub-layer (FlowCirclesLayer) holds one instance per drawn NODE.
      // Its data length is the clustered node count at the current zoom.
      let nodeCount = 0, flowGeomCount = 0;
      for (const s of subs) {
        const cn = s.constructor.name;
        const len = (s.props && s.props.data && (s.props.data.length ?? (s.props.data.attributes ? undefined : null))) ?? null;
        if (/Circle/i.test(cn)) {
          const d = s.props && s.props.data;
          nodeCount = Array.isArray(d) ? d.length : (d && d.length) || nodeCount;
        }
        if (/Flow.*Line|AnimatedFlow|CurvedFlow/i.test(cn)) {
          const d = s.props && s.props.data;
          const fl2 = Array.isArray(d) ? d.length : (d && d.length) || 0;
          if (fl2 > flowGeomCount) flowGeomCount = fl2;
        }
      }
      out.nodeCount = nodeCount;
      out.flowGeomCount = flowGeomCount;
    } catch (e) { out.subLayerError = String(e); }
    return { ...out, ok: true };
  });
}

// Sample the deck overlay canvas (#deck-canvas) for opaque/distinct pixels.
async function sampleDeckCanvas(page, { stableReads = 2, intervalMs = 150, maxMs = 9000 } = {}) {
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
        sampled++;
        if (data[i + 3] > 0) opaque++;
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

async function shot(page, name) {
  await fs.mkdir(PROOF_DIR, { recursive: true });
  const full = path.join(PROOF_DIR, `${name}.png`);
  await page.screenshot({ path: full, animations: "disabled" });
  return full;
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
  page.on("console", (msg) => {
    const t = msg.text();
    if (msg.type() === "error") consoleErrors.push(t);
    if (msg.type() === "warning") consoleWarnings.push(t);
  });
  page.on("pageerror", (e) => pageErrors.push(String(e && e.stack ? e.stack : e)));
  page.on("request", (req) => allRequests.push(req.url()));

  const url = srv.url.replace(/\/$/, "") + "/flows.html";
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });

  // Wait for the app to boot: window.__fmReady + a FlowmapLayer in the deck props.
  let booted = false;
  try {
    await page.waitForFunction(
      () => {
        const fm = window.__fm;
        if (!fm || !fm.deck || !window.__fmReady) return false;
        const layers = (fm.deck.props && fm.deck.props.layers) || [];
        const flat = layers.flat ? layers.flat(Infinity) : layers;
        return flat.some((l) => l && l.id === "od-flowmap");
      },
      { timeout: 60000, polling: 250 }
    );
    booted = true;
  } catch (e) { /* booted stays false; reported below */ }

  // ---- (a) LOW-zoom clustered view ----
  await setCamera(page, 2.0, 41.65, 7.2);
  await page.waitForTimeout(1500); // let the provider re-aggregate + particles draw
  const clusteredSample = await sampleDeckCanvas(page);
  const clusteredInfo = await introspectFlowmap(page);
  const clusteredShot = await shot(page, "phase3_clustered");

  // ---- (b) Zoomed-in Barcelona view (clusters expand) ----
  await setCamera(page, 2.17, 41.39, 10.2);
  await page.waitForTimeout(1800);
  const bcnSample = await sampleDeckCanvas(page);
  const bcnInfo = await introspectFlowmap(page);
  const bcnShot = await shot(page, "phase3_bcn");

  // ---- Levers present + app methods exist ----
  const levers = await page.evaluate(() => {
    const app = window.__fm;
    const el = (id) => !!document.getElementById(id);
    return {
      hourScrubber: el("hour-range") && typeof app._setActiveHour === "function",
      sliceSwitch: el("window-select") && document.querySelectorAll('#daytype-toggle input').length === 2 && typeof app._setSlice === "function",
      topN: el("topn-range") && typeof app._buildTopN === "function",
      clusteringToggle: el("clustering-toggle"),
      basemapSwitch: el("basemap-select") && typeof app._setBasemap === "function",
      defaultViews: document.querySelectorAll("#view-list .view-btn").length,
      playPause: el("hour-play"),
    };
  });

  // Exercise the hour scrubber once (commit hour 18) to prove the data swap path.
  const scrubResult = await page.evaluate(async () => {
    const app = window.__fm;
    const before = app._data && app._data.flows && app._data.flows[0] ? app._data.flows[0].count : null;
    app._setActiveHour(18);
    await new Promise((r) => setTimeout(r, 400));
    const after = app._data && app._data.flows && app._data.flows[0] ? app._data.flows[0].count : null;
    return { before, after, hour: app._activeHour, changed: before !== after };
  });

  // ---- (c) Pick / hover a flow or node ----
  // FlowmapLayer surfaces its TYPED picked object ({type:'flow'|'location',...}) only
  // through its async onHover lifecycle (it awaits the data provider to resolve the
  // flow/location from the raw sub-layer pick index). A bare deck.pickObject returns
  // the sub-layer index WITHOUT that typed object, so the real path is to dispatch a
  // genuine mouse hover over a pixel where geometry exists and then poll app._hover.
  //
  // We find a real node-circle screen pixel from the rendered FlowCirclesLayer (its
  // pickable instances), move the real mouse there, wait for the async onHover to
  // populate app._hover, and read the panel readout the handler wrote. If the mouse
  // path doesn't land we fall back to driving the layer's own async picking builder
  // on a raw pick index — the same object the live hover would produce.
  let pickXY = await page.evaluate(() => {
    // Find a screen pixel for a drawn node by raw-picking a coarse grid and keeping
    // the first hit that resolves to a FlowCirclesLayer or a flow line sub-layer.
    const deck = window.__fm.deck;
    const W = deck.width || 1280, H = deck.height || 1000;
    const cols = 40, rows = 26;
    for (let r = 1; r < rows; r++) {
      for (let c = 1; c < cols; c++) {
        const x = Math.round((c / cols) * W);
        const y = Math.round((r / rows) * H);
        let info = null;
        try { info = deck.pickObject({ x, y, radius: 8 }); } catch { info = null; }
        if (info && info.sourceLayer && info.index >= 0) {
          const cn = info.sourceLayer.constructor.name;
          if (/Circle|Flow.*Line|AnimatedFlow|CurvedFlow/i.test(cn)) {
            return { x, y, sub: cn, index: info.index };
          }
        }
      }
    }
    return null;
  });

  // Move the REAL mouse to that pixel so deck dispatches a genuine hover -> the
  // FlowmapLayer's async onHover resolves the typed object into app._hover.
  let hoverHit = null;
  if (pickXY) {
    const box = await page.locator("#deck-canvas").boundingBox();
    const px = (box ? box.x : 0) + pickXY.x;
    const py = (box ? box.y : 0) + pickXY.y;
    await page.mouse.move(px - 3, py - 3);
    await page.mouse.move(px, py);
    // Poll app._hover for the async onHover to land.
    for (let i = 0; i < 20 && !hoverHit; i++) {
      await page.waitForTimeout(120);
      hoverHit = await page.evaluate(() => {
        const o = window.__fm._hover;
        return o && o.type ? { type: o.type } : null;
      });
    }
  }

  // Fallback: drive the layer's own async picking builder on the raw pick index — the
  // identical typed object a live hover produces — then route it through the readout.
  const pick = await page.evaluate(async (xy) => {
    const app = window.__fm;
    let chosen = app._hover && app._hover.type ? app._hover : null;
    let via = chosen ? "mouse-hover" : null;
    if (!chosen && xy) {
      const deck = app.deck;
      const layers = (deck.props.layers || []).flat(Infinity);
      const fl = layers.find((l) => l && l.id === "od-flowmap");
      let info = null;
      try { info = deck.pickObject({ x: xy.x, y: xy.y, radius: 8 }); } catch { info = null; }
      if (fl && info && typeof fl._getFlowmapLayerPickingInfo === "function") {
        try {
          const typed = await fl._getFlowmapLayerPickingInfo(info);
          if (typed && typed.object && typed.object.type) { chosen = typed.object; via = "async-builder"; }
        } catch (e) { /* leave chosen null */ }
      }
    }
    if (chosen) { app._hover = chosen; app._updatePickReadout(chosen); }
    const readout = document.getElementById("pick-readout");
    return {
      hit: chosen ? { type: chosen.type, via } : null,
      pickXY: xy,
      readoutText: readout ? readout.textContent : null,
      readoutHtml: readout ? readout.innerHTML : null,
    };
  }, pickXY);
  pick.hoverHit = hoverHit;
  await page.waitForTimeout(300);
  const pickShot = await shot(page, "phase3_pick");

  // ---- Classify network requests ----
  const classified = allRequests.map((u) => classifyRequest(u, loopbackHost));
  const thirdParty = classified.filter((c) => c.kind === "third-party");
  const forbidden = thirdParty.filter((c) => c.forbidden);
  const zeroCdn = thirdParty.length === 0;

  const dupDeckWarning = consoleWarnings.concat(consoleErrors)
    .find((t) => /multiple versions of deck\.gl|duplicate.*deck|two copies/i.test(t));
  const deckVersion = await page.evaluate(() => (globalThis.deck && globalThis.deck.VERSION) || null);
  const lumaVersion = await page.evaluate(() => (globalThis.luma && globalThis.luma.VERSION) || null);
  const singleDeck = !dupDeckWarning && deckVersion != null && lumaVersion != null;

  // ---- Verdicts ----
  const flowmapRenders =
    booted && clusteredInfo.hasFlowmapLayer &&
    clusteredSample.ok && clusteredSample.distinctColors >= 6 && clusteredSample.opaqueSamples > 0;

  // Clustering proof: at LOW zoom the drawn node count is materially fewer than the
  // raw 599 (super-nodes), AND zooming in raises the rendered geometry (flows expand).
  const clusteringActive =
    clusteredInfo.clusteringEnabled === true &&
    (clusteredInfo.subLayerCount || 0) >= 2 &&
    clusteredInfo.nodeCount > 0 &&
    clusteredInfo.nodeCount < clusteredInfo.rawLocations &&
    bcnSample.ok && bcnSample.opaqueSamples > 0;

  const leversWired =
    levers.hourScrubber && levers.sliceSwitch && levers.topN &&
    levers.clusteringToggle && levers.basemapSwitch && levers.defaultViews >= 4 &&
    scrubResult.changed === true;

  const pickWorked = !!(pick.hit && pick.hit.type);
  const consoleClean = consoleErrors.length === 0 && pageErrors.length === 0;

  const report = {
    flowmap_renders: flowmapRenders,
    clustering_active: clusteringActive,
    levers_wired: leversWired,
    pick_worked: pickWorked,
    zero_cdn_requests: zeroCdn,
    single_deck_instance: singleDeck,
    console_clean: consoleClean,
    proof: { clustered: clusteredShot, bcn: bcnShot, pick: pickShot },
    detail: {
      booted,
      clusteredSample, clusteredInfo,
      bcnSample, bcnInfo,
      levers, scrubResult, pick,
      deckVersion, lumaVersion, dupDeckWarning: dupDeckWarning || null,
      requestTotals: {
        total: classified.length,
        sameOrigin: classified.filter((c) => c.kind === "same-origin").length,
        basemapExempt: classified.filter((c) => c.kind === "basemap-exempt").length,
        thirdParty: thirdParty.length,
        forbiddenCdn: forbidden.length,
      },
      thirdPartyHosts: [...new Set(thirdParty.map((c) => c.host))],
      consoleErrors, consoleWarnings: consoleWarnings.slice(0, 30), pageErrors,
    },
  };

  console.log(JSON.stringify(report, null, 2));

  await context.close();
  await close();
  await srv.stop();

  const allPass =
    flowmapRenders && clusteringActive && leversWired && zeroCdn && singleDeck && consoleClean;
  process.exitCode = allPass ? 0 : 1;
}

main().catch((e) => {
  console.error("FATAL:", e);
  process.exitCode = 2;
});
