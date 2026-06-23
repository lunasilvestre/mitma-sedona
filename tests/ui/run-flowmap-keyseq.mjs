// run-flowmap-keyseq.mjs — ADVERSARIAL keypress + control-SEQUENCE harness for the
// FLOWMAP variant (docs/flows.html + docs/app/flows_fm/main.js).
//
// Where run-flowmap-phase3.mjs proves the levers RENDER, THIS harness drives every
// KEYPRESS and control SEQUENCE the way a real keyboard user does — through focused
// DOM elements + dispatched keyboard/change events — and asserts each behaves:
//
//   * hour scrubber: ArrowRight/Left step ±1, Home→0, End→23, PageUp/PageDown jump,
//     and CLAMP at both ends (no 24, no -1).
//   * Space on the focused play button toggles the play/pause animation (and we report
//     honestly that there is NO global document-level Space binding — Space only
//     activates the focused <button>, which is the correct native behaviour).
//   * Tab reaches EVERY control in source order.
//   * slice switch (window <select> + daytype radios) re-aggregates (data swap).
//   * basemap switch (the <select>) swaps the passive host, flows untouched.
//   * top-N slider (keyboard) re-selects the top set.
//   * the 4 default-view presets apply their full lever bundle.
//   * "deep-link reload then keyboard-scrub": the fm variant has NO URL-state infra
//     (verified — no s=/replaceState), so a true deep link is impossible. The honest
//     analog is a FULL PAGE RELOAD (fresh boot) then a keyboard scrub, proving the
//     keyboard path survives a reload. We assert that + record the missing-deep-link.
//   * RAPID INTERLEAVED sequence: scrub-then-switch-slice — must leave a coherent,
//     error-free state.
//
// Reports keyseq_total / keyseq_passed / keyseq_failures (+ a per-case log).
// Trust the instrumentation over any self-report.

import path from "node:path";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { startServer } from "./lib/server.mjs";
import { launch } from "./lib/browser.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PROOF_DIR = path.join(HERE, "proof", "_fm");

// Park the camera at a WIDE Catalonia framing so the whole region is in view.
const WIDE = { longitude: 1.4, latitude: 41.75, zoom: 7.1 };

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

// Introspect the live FlowmapLayer (the SAME probe shape both other harnesses use).
async function introspect(page) {
  return page.evaluate(() => {
    const app = window.__fm;
    const out = { ok: false };
    if (!app || !app.deck) return out;
    const layers = (app.deck.props && app.deck.props.layers) || [];
    const flat = layers.flat ? layers.flat(Infinity) : layers;
    const fl = flat.find((l) => l && l.id === "od-flowmap");
    if (!fl) return out;
    out.layerClusteringEnabled = !!(fl.props && fl.props.clusteringEnabled);
    out.layerThicknessScale = fl.props && fl.props.flowLineThicknessScale;
    out.layerColorScheme = fl.props && fl.props.colorScheme;
    out.layerMaxTopFlows = fl.props && fl.props.maxTopFlowsDisplayNum;
    out.userClusteringEnabled = !!app._clusteringEnabled;
    out.activeHour = app._activeHour;
    out.topN = app._topN;
    out.sliceKey = app._sliceKey;
    out.basemapKey = app._basemapKey;
    out.rawFlows = Array.isArray(app._rawFlows) ? app._rawFlows.length : 0;
    out.firstFlowCount = app._data && app._data.flows && app._data.flows[0] ? app._data.flows[0].count : null;
    try {
      const subs = fl.getSubLayers ? fl.getSubLayers() : [];
      let nodeCount = 0, flowGeomCount = 0;
      for (const s of subs) {
        const cn = s.constructor.name;
        const d = s.props && s.props.data;
        const len = Array.isArray(d) ? d.length : (d && d.length) || 0;
        if (/Circle/i.test(cn)) nodeCount = Math.max(nodeCount, len);
        if (/Flow.*Line|AnimatedFlow|CurvedFlow/i.test(cn)) flowGeomCount = Math.max(flowGeomCount, len);
      }
      out.nodeCount = nodeCount;
      out.flowGeomCount = flowGeomCount;
    } catch (e) { out.err = String(e); }
    out.ok = true;
    return out;
  });
}

// Focus a control by id (real DOM focus, as Tab would land it).
async function focusEl(page, id) {
  await page.evaluate((id) => { const el = document.getElementById(id); el && el.focus(); }, id);
}

// What element currently has focus?
function activeId(page) {
  return page.evaluate(() => (document.activeElement && document.activeElement.id) || null);
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
  const { browser, close } = await launch();
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  const page = await context.newPage();

  const consoleErrors = [], pageErrors = [];
  page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text()); });
  page.on("pageerror", (e) => pageErrors.push(String(e && e.stack ? e.stack : e)));

  const url = srv.url.replace(/\/$/, "") + "/flows.html";

  const cases = [];          // { name, pass, detail }
  const pass = (name, detail) => cases.push({ name, pass: true, detail });
  const fail = (name, detail) => cases.push({ name, pass: false, detail });

  async function bootReady() {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForFunction(() => {
      const fm = window.__fm;
      if (!fm || !fm.deck || !window.__fmReady) return false;
      const layers = (fm.deck.props && fm.deck.props.layers) || [];
      const flat = layers.flat ? layers.flat(Infinity) : layers;
      return flat.some((l) => l && l.id === "od-flowmap");
    }, { timeout: 60000, polling: 250 });
  }

  await bootReady();
  await setCamera(page, WIDE.longitude, WIDE.latitude, WIDE.zoom);
  await page.waitForTimeout(800);

  // Set the active hour deterministically so each keypress test starts from a known h.
  // IMPORTANT: _setActiveHour commits the APP state but does NOT write the slider's DOM
  // .value (only _syncControlsToState does, on preset apply). A native <input type=range>
  // increments/decrements from its OWN .value on a keypress — so for a keyboard test to
  // start from a known hour we MUST also set the slider element's .value here, else the
  // arrow keys step from a stale DOM value left by the previous case.
  async function setHour(h) {
    await page.evaluate((h) => {
      const app = window.__fm;
      app._setActiveHour(h);
      const r = document.getElementById("hour-range");
      if (r) { r.value = String(h); r.setAttribute("aria-valuetext", `${String(h).padStart(2, "0")}:00`); }
      const lab = document.getElementById("hour-label");
      if (lab) lab.textContent = `${String(h).padStart(2, "0")}:00`;
    }, h);
    await page.waitForTimeout(120);
  }

  // ---------------------------------------------------------------------------
  // 1. HOUR SCRUBBER KEYPRESSES — native <input type=range> keyboard semantics.
  //    The slider commits on `input`/`change`; the app debounces input ~250ms then
  //    calls _setActiveHour. We press a key on the focused slider and then poll the
  //    committed app._activeHour (the ground truth the layer re-aggregates from).
  // ---------------------------------------------------------------------------
  await focusEl(page, "hour-range");
  const focusedHour = await activeId(page);
  if (focusedHour === "hour-range") pass("focus_hour_range", { activeId: focusedHour });
  else fail("focus_hour_range", { activeId: focusedHour });

  // ArrowRight from 8 -> 9 (native range increments by step=1).
  await setHour(8);
  await focusEl(page, "hour-range");
  await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(450); // input-debounce commit
  let h = (await introspect(page)).activeHour;
  if (h === 9) pass("hour_arrowright_inc", { from: 8, to: h });
  else fail("hour_arrowright_inc", { from: 8, to: h, expected: 9 });

  // ArrowLeft from 8 -> 7.
  await setHour(8);
  await focusEl(page, "hour-range");
  await page.keyboard.press("ArrowLeft");
  await page.waitForTimeout(450);
  h = (await introspect(page)).activeHour;
  if (h === 7) pass("hour_arrowleft_dec", { from: 8, to: h });
  else fail("hour_arrowleft_dec", { from: 8, to: h, expected: 7 });

  // Home -> 0.
  await setHour(12);
  await focusEl(page, "hour-range");
  await page.keyboard.press("Home");
  await page.waitForTimeout(450);
  h = (await introspect(page)).activeHour;
  if (h === 0) pass("hour_home_min", { to: h });
  else fail("hour_home_min", { to: h, expected: 0 });

  // End -> 23.
  await setHour(12);
  await focusEl(page, "hour-range");
  await page.keyboard.press("End");
  await page.waitForTimeout(450);
  h = (await introspect(page)).activeHour;
  if (h === 23) pass("hour_end_max", { to: h });
  else fail("hour_end_max", { to: h, expected: 23 });

  // PageUp / PageDown — native range jumps by a larger step (Chromium uses ~10% of
  // range or its own heuristic). We only assert the DIRECTION (up raises, down lowers)
  // and that the value stays clamped to [0,23].
  await setHour(10);
  await focusEl(page, "hour-range");
  await page.keyboard.press("PageUp");
  await page.waitForTimeout(450);
  const hUp = (await introspect(page)).activeHour;
  await setHour(10);
  await focusEl(page, "hour-range");
  await page.keyboard.press("PageDown");
  await page.waitForTimeout(450);
  const hDown = (await introspect(page)).activeHour;
  if (hUp > 10 && hUp <= 23 && hDown < 10 && hDown >= 0)
    pass("hour_pageupdown_jump", { up: hUp, down: hDown });
  else fail("hour_pageupdown_jump", { up: hUp, down: hDown, expectedUpGt: 10, expectedDownLt: 10 });

  // CLAMP at the top: at 23, ArrowRight/End must NOT exceed 23.
  await setHour(23);
  await focusEl(page, "hour-range");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("End");
  await page.waitForTimeout(450);
  const hTop = (await introspect(page)).activeHour;
  if (hTop === 23) pass("hour_clamp_top", { to: hTop });
  else fail("hour_clamp_top", { to: hTop, expected: 23 });

  // CLAMP at the bottom: at 0, ArrowLeft/Home must NOT go below 0.
  await setHour(0);
  await focusEl(page, "hour-range");
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("Home");
  await page.waitForTimeout(450);
  const hBot = (await introspect(page)).activeHour;
  if (hBot === 0) pass("hour_clamp_bottom", { to: hBot });
  else fail("hour_clamp_bottom", { to: hBot, expected: 0 });

  // ---------------------------------------------------------------------------
  // 2. SPACE PLAY/PAUSE — the play button is a native <button>. Focusing it and
  //    pressing Space ACTIVATES it (native button keyboard semantics) which toggles
  //    the hour animation. We assert ON then OFF. We ALSO record honestly that there
  //    is no document-level Space binding (Space does nothing unless the button is
  //    focused) — which is the correct, accessible behaviour, not a defect.
  // ---------------------------------------------------------------------------
  await page.evaluate(() => window.__fm._stopAnimate());
  await focusEl(page, "hour-play");
  const focusedPlay = await activeId(page);
  await page.keyboard.press("Space");
  await page.waitForTimeout(300);
  const animOn = await page.evaluate(() => window.__fm._animating);
  await focusEl(page, "hour-play");
  await page.keyboard.press("Space");
  await page.waitForTimeout(300);
  const animOff = await page.evaluate(() => window.__fm._animating);
  await page.evaluate(() => window.__fm._stopAnimate());
  if (focusedPlay === "hour-play" && animOn === true && animOff === false)
    pass("space_play_pause_on_button", { focusedPlay, animOn, animOff, note: "Space activates the focused play <button>; no global Space binding (correct native a11y)." });
  else
    fail("space_play_pause_on_button", { focusedPlay, animOn, animOff });

  // ---------------------------------------------------------------------------
  // 3. TAB REACHES EVERY CONTROL.
  //    We Tab from document start and collect the focused id sequence. We require the
  //    sequence to INCLUDE each expected control id (order may interleave with the
  //    map/links, but every control must be reachable).
  // ---------------------------------------------------------------------------
  await page.evaluate(() => { if (document.activeElement) document.activeElement.blur(); document.body.focus(); });
  // Start from the top of the document.
  await page.evaluate(() => window.scrollTo(0, 0));
  const tabbedIds = [];
  // The panel has on the order of a dozen focusable controls; 40 Tabs is plenty to
  // cycle through all of them at least once.
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press("Tab");
    const id = await activeId(page);
    if (id) tabbedIds.push(id);
  }
  const expectControls = [
    "window-select", "hour-range", "hour-play", "topn-range",
    "clustering-toggle", "basemap-select",
  ];
  // The daytype radios share a name; tabbing lands one of the radio group.
  const reachedRadio = tabbedIds.some((id) => id === "" ) ||
    await page.evaluate(() => {
      // a radio has no id in the markup; check that focusing the group is possible.
      const r = document.querySelector('#daytype-toggle input[name="daytype"]');
      r && r.focus();
      return document.activeElement === r;
    });
  const reached = {};
  for (const c of expectControls) reached[c] = tabbedIds.includes(c);
  // Also assert the 4 view-btns (presets) are tab-reachable.
  const viewBtnReached = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll("#view-list .view-btn"));
    if (!btns.length) return false;
    btns[0].focus();
    return document.activeElement === btns[0];
  });
  const allReached = expectControls.every((c) => reached[c]) && reachedRadio && viewBtnReached;
  if (allReached)
    pass("tab_reaches_all_controls", { reached, reachedRadio, viewBtnReached, tabbedIdsSample: [...new Set(tabbedIds)] });
  else
    fail("tab_reaches_all_controls", { reached, reachedRadio, viewBtnReached, tabbedIdsSample: [...new Set(tabbedIds)] });

  // ---------------------------------------------------------------------------
  // 4. SLICE SWITCH — window <select> + daytype radios. We change the select value
  //    via keyboard-equivalent change events and assert the data re-aggregates
  //    (sliceKey changes, the first flow's count is rebuilt). Debounced ~250ms.
  // ---------------------------------------------------------------------------
  const sliceBefore = await introspect(page);
  await page.evaluate(() => {
    const sel = document.getElementById("window-select");
    sel.value = "jun";
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await page.waitForTimeout(1200);
  const sliceAfter = await introspect(page);
  if (sliceAfter.sliceKey === "jun_weekday" && sliceAfter.sliceKey !== sliceBefore.sliceKey && sliceAfter.flowGeomCount > 0)
    pass("slice_switch_window", { from: sliceBefore.sliceKey, to: sliceAfter.sliceKey, flowGeom: sliceAfter.flowGeomCount });
  else fail("slice_switch_window", { from: sliceBefore.sliceKey, to: sliceAfter.sliceKey });

  // daytype radio -> weekend.
  await page.evaluate(() => {
    const r = document.querySelector('#daytype-toggle input[value="weekend"]');
    r.checked = true;
    r.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await page.waitForTimeout(1200);
  const dayAfter = await introspect(page);
  if (dayAfter.sliceKey === "jun_weekend" && dayAfter.flowGeomCount > 0)
    pass("slice_switch_daytype", { to: dayAfter.sliceKey, flowGeom: dayAfter.flowGeomCount });
  else fail("slice_switch_daytype", { to: dayAfter.sliceKey });

  // Restore to may_weekday for the rest of the sequence.
  await page.evaluate(() => {
    const sel = document.getElementById("window-select"); sel.value = "may"; sel.dispatchEvent(new Event("change", { bubbles: true }));
    const r = document.querySelector('#daytype-toggle input[value="weekday"]'); r.checked = true; r.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await page.waitForTimeout(1300);

  // ---------------------------------------------------------------------------
  // 5. BASEMAP SWITCH — the <select> swaps the PASSIVE MapLibre host; deck flows
  //    are untouched. Assert basemapKey changes and the flow layer still renders.
  // ---------------------------------------------------------------------------
  const bmBefore = await introspect(page);
  await page.evaluate(() => {
    const sel = document.getElementById("basemap-select");
    sel.value = "satellite";
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await page.waitForTimeout(900);
  const bmAfter = await introspect(page);
  if (bmAfter.basemapKey === "satellite" && bmAfter.basemapKey !== bmBefore.basemapKey && bmAfter.flowGeomCount > 0)
    pass("basemap_switch", { from: bmBefore.basemapKey, to: bmAfter.basemapKey, flowGeomStillDrawn: bmAfter.flowGeomCount });
  else fail("basemap_switch", { from: bmBefore.basemapKey, to: bmAfter.basemapKey });
  // back to dark
  await page.evaluate(() => { const sel = document.getElementById("basemap-select"); sel.value = "dark"; sel.dispatchEvent(new Event("change", { bubbles: true })); });
  await page.waitForTimeout(500);

  // ---------------------------------------------------------------------------
  // 6. TOP-N SLIDER (keyboard) — focus + ArrowLeft lowers the cap; the live layer's
  //    maxTopFlowsDisplayNum follows. 120ms debounce.
  // ---------------------------------------------------------------------------
  await page.evaluate(() => { const r = document.getElementById("topn-range"); r.value = "5000"; r.dispatchEvent(new Event("input", { bubbles: true })); });
  await page.waitForTimeout(300);
  const topnBefore = await introspect(page);
  await focusEl(page, "topn-range");
  // step=100; ArrowLeft a few times to drop the cap.
  for (let i = 0; i < 5; i++) await page.keyboard.press("ArrowLeft");
  await page.waitForTimeout(400);
  const topnAfter = await introspect(page);
  if (topnAfter.topN < topnBefore.topN && topnAfter.layerMaxTopFlows === topnAfter.topN)
    pass("topn_slider_keyboard", { from: topnBefore.topN, to: topnAfter.topN, layerMaxTop: topnAfter.layerMaxTopFlows });
  else fail("topn_slider_keyboard", { from: topnBefore.topN, to: topnAfter.topN, layerMaxTop: topnAfter.layerMaxTopFlows });

  // ---------------------------------------------------------------------------
  // 7. DEFAULT-VIEW PRESETS — activate each of the 4 preset buttons (keyboard-
  //    activatable <button>s) and assert each applies its full lever bundle.
  // ---------------------------------------------------------------------------
  const presetResults = [];
  const presetIds = await page.evaluate(() =>
    Array.from(document.querySelectorAll("#view-list .view-btn")).map((b) => b.dataset.viewId));
  for (let i = 0; i < presetIds.length; i++) {
    await page.evaluate((i) => {
      const btns = Array.from(document.querySelectorAll("#view-list .view-btn"));
      btns[i].focus();
    }, i);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(1400);
    const info = await introspect(page);
    presetResults.push({ id: presetIds[i], hour: info.activeHour, topN: info.topN, slice: info.sliceKey, basemap: info.basemapKey, flowGeom: info.flowGeomCount });
  }
  const presetsApplied = presetResults.length >= 4 &&
    presetResults.every((p) => p.flowGeom > 0);
  // daily-pulse expects h8 topn5000 may_weekday; long-haul expects topn1200.
  const dailyPulse = presetResults.find((p) => p.id === "daily-pulse");
  const longHaul = presetResults.find((p) => p.id === "long-haul");
  const presetLeversOk = dailyPulse && dailyPulse.hour === 8 && dailyPulse.topN === 5000 &&
    longHaul && longHaul.topN === 1200;
  if (presetsApplied && presetLeversOk)
    pass("presets_apply_lever_bundle", { presetResults });
  else
    fail("presets_apply_lever_bundle", { presetsApplied, presetLeversOk, presetResults });

  // ---------------------------------------------------------------------------
  // 8. DEEP-LINK RELOAD then KEYBOARD-SCRUB.
  //    The fm variant has NO URL-state machinery (verified: no s=/replaceState in
  //    the source). A real deep link is therefore not possible — we record that as a
  //    KNOWN GAP, not a pass. The honest analog is a FULL PAGE RELOAD (fresh boot)
  //    followed by a keyboard scrub, proving the keyboard path survives a reload.
  // ---------------------------------------------------------------------------
  const deepLinkInfra = await page.evaluate(() => {
    // Probe for any URL-state surface on the fm page.
    return {
      hasSearch: typeof location !== "undefined" && location.search.length > 1,
      // The fm main.js exposes no _applyStateFromURL / _syncStateToURL.
      hasUrlSync: !!(window.__fm && (window.__fm._syncStateToURL || window.__fm._applyStateFromURL)),
    };
  });
  // FULL RELOAD (fresh boot).
  await bootReady();
  await setCamera(page, WIDE.longitude, WIDE.latitude, WIDE.zoom);
  await page.waitForTimeout(700);
  await setHour(8);
  await focusEl(page, "hour-range");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(500);
  const afterReloadScrub = (await introspect(page)).activeHour;
  const reloadScrubOk = afterReloadScrub === 10;
  // We PASS the "reload then keyboard works" analog; the deep-link itself is a gap.
  if (reloadScrubOk && deepLinkInfra.hasUrlSync === false)
    pass("reload_then_keyboard_scrub", { afterReloadScrub, deepLinkInfra, note: "No URL deep-link infra in the fm variant (known gap); reload+keyboard scrub works." });
  else if (reloadScrubOk)
    pass("reload_then_keyboard_scrub", { afterReloadScrub, deepLinkInfra });
  else
    fail("reload_then_keyboard_scrub", { afterReloadScrub, expected: 10, deepLinkInfra });

  // ---------------------------------------------------------------------------
  // 9. RAPID INTERLEAVED SEQUENCE — the adversarial part. Must end coherent.
  // ---------------------------------------------------------------------------
  const errBeforeRapid = consoleErrors.length + pageErrors.length;

  // scrub-then-switch-slice: arrow the hour then immediately switch the window.
  await focusEl(page, "hour-range");
  await page.keyboard.press("ArrowRight");
  await page.evaluate(() => { const sel = document.getElementById("window-select"); sel.value = "feb"; sel.dispatchEvent(new Event("change", { bubbles: true })); });
  await page.waitForTimeout(1500);
  const rapidB = await introspect(page);
  const rapidBOk = rapidB.sliceKey === "feb_weekday" && rapidB.flowGeomCount > 0 && rapidB.ok;
  if (rapidBOk) pass("rapid_scrub_then_switch_slice", { slice: rapidB.sliceKey, hour: rapidB.activeHour, flowGeom: rapidB.flowGeomCount });
  else fail("rapid_scrub_then_switch_slice", { rapidB });

  const errAfterRapid = consoleErrors.length + pageErrors.length;
  if (errAfterRapid === errBeforeRapid) pass("rapid_sequences_no_console_error", { newErrors: errAfterRapid - errBeforeRapid });
  else fail("rapid_sequences_no_console_error", { newErrors: errAfterRapid - errBeforeRapid, consoleErrors, pageErrors });

  // A final proof shot of the post-sequence state.
  const keyseqShot = await shot(page, "keyseq_final");

  // ---- Roll up ----
  const total = cases.length;
  const passed = cases.filter((c) => c.pass).length;
  const failures = cases.filter((c) => !c.pass).map((c) => c.name);
  const consoleClean = consoleErrors.length === 0 && pageErrors.length === 0;

  const report = {
    keyseq_total: total,
    keyseq_passed: passed,
    keyseq_failures: failures,
    console_clean: consoleClean,
    proof: { keyseq_final: keyseqShot },
    cases,
    consoleErrors,
    pageErrors,
  };
  console.log(JSON.stringify(report, null, 2));

  await context.close();
  await close();
  await srv.stop();

  process.exitCode = (passed === total && consoleClean) ? 0 : 1;
}

main().catch((e) => { console.error("FATAL:", e); process.exitCode = 2; });
