# UI Regression Test Plan — `docs/explore.html` (deck.gl geo-browser)

> Authoritative design doc for the headless-browser UI regression suite. Written
> for an **external adversarial reviewer**: every control and every meaningful
> sequence of controls is exercised the way a real user clicks through the app,
> and every step records **screenshot + live-state + console proof** so a PASS
> can be audited without trusting the runner blindly.

- **Target under test:** `docs/explore.html` + `docs/app/geobrowser-map.js`
- **Harness:** `tests/ui/lib/server.mjs` (static server over `docs/`), `tests/ui/lib/browser.mjs` (headless Chromium, software WebGL via ANGLE/SwiftShader)
- **Spec (machine-readable):** [`tests/ui/tests.json`](../tests/ui/tests.json)
- **Runner:** [`tests/ui/run-slice.mjs`](../tests/ui/run-slice.mjs)
- **Proof output:** `tests/ui/proof/<slice>/` — screenshots + `manifest.json`
- **Totals:** 82 tests across 8 independent slices.

---

## 1. Philosophy

1. **Drive the real controls, not the internals.** Tests dispatch the same DOM
   events a user generates (`change` on selects/checkboxes, `input` on the
   range, the `?param=` deep-link path on load). They never call private setters
   to *establish* state — only to *read* it for assertions.
2. **Assert live application state, not just pixels.** Each `expect` reads
   `window.gb` (`_fieldKey`, `_preset`, `_season`, `_extrude`, `_fillAlpha`,
   `_basemapKey`, `_layersOn`, `_viewState`, `_arcs`, `_pois`, and
   `deck.props.layers`) plus the relevant DOM (disabled flags, readouts,
   `<details>.open`). A reviewer can therefore confirm *why* a step passed.
3. **Screenshot every step anyway.** Pixels are the human-auditable backstop:
   each step writes a full-page PNG and a `#map`-clipped PNG. Bug regressions
   that are about *look* (the arcs) are judged on the live layer props **and**
   the screenshot.
4. **Console is a first-class signal.** Every step records the console-error and
   page-error tally seen so far. `noConsoleErrors` fails a step on any
   `console.error` or uncaught exception.
5. **Deterministic + independent.** One fresh browser context per test (fixed
   1600×1000 viewport, deviceScaleFactor 1, software GL). Slices never share a
   proof directory, so the 8 slices run in parallel processes without
   interference. Re-running after a rebuild reproduces the verdicts.
6. **Regressions flip green on fix.** The two known bugs are encoded as live
   assertions calibrated to the *fixed* state. While the bug exists the test
   FAILS (and the slice exits non-zero); when the fix lands the same test PASSES
   with no edit. That is the contract with the reviewer.

### Harness limitation the reviewer must know
`analyzeCanvas()` samples **only** the deck.gl `#map-deck` canvas, not the
MapLibre basemap canvas stacked beneath it. So `canvasRendered` proves the
*deck overlay* drew (hexes/arcs/POIs). For states where the deck is
intentionally empty/transparent — **hex opacity 0** and **hexes toggled off** —
the satellite still shows on its own canvas, so those steps assert deck *state*
(`fillAlpha`, `deckLayerAbsent`) and rely on the **full-page screenshot** as the
"basemap reads through" proof, instead of `canvasRendered`. Two genuinely sparse
fields (`industry_density_per_km2`, `mitma_through_ratio`) lower the distinct-color
floor to 3 because the real blank-detector is `opaqueSamples > 0`.

---

## 2. Control inventory covered (every control gets ≥1 owning test)

| Control | Selector | Owning tests |
|---|---|---|
| Liveability hexes | `#toggle-hexes` | JOURNEY-005 (off→on), and implicitly every test (default on) |
| Basemap | `#basemap-select` | BM-001..005, EXT-003, DL-002, DL-012, JOURNEY-001/004 |
| Hex opacity | `#hex-opacity` + `#hex-opacity-val` | OPA-001/002/003, DL-003/004, POI-003, JOURNEY-001/004 |
| Score preset | `#preset-select` | PRESET-001..006, DL-005, DL-012, JOURNEY-001/003/004 |
| Recolour metric | `#metric-select` (~35 opts) | MET-A-001..015, MET-B-001..020, DL-006/007, EXT-004, SEA-*, JOURNEY-001/002/004 |
| Month-window | `#season-select` | SEA-001..006, DL-008, JOURNEY-002 |
| 2.5D extrude | `#toggle-extrude` | EXT-001..004, DL-009, DL-012, JOURNEY-003 |
| MITMA OD arcs | `#toggle-arcs` | ARC-001/002, POI-003, DL-010, JOURNEY-002/004 |
| OSM amenities | `#toggle-pois` | POI-001/002/003, DL-011, JOURNEY-003 |
| Methodology details | `#methodology` | DL-001, DL-012 |

Every dropdown **option** reachable from the UI is exercised: 4 basemaps, 4
presets, all 35 metric-select options (the v3 optgroup + the top-level group),
and all 4 month-windows. JS-only `FIELDS` keys that are **not** in the dropdown
(`green_min_m`, `yoga_min_m`, …) are covered by DL-007, which proves the
deep-link guard rejects them (they only reach via `?metric=` if also a dropdown
option — DL-007 documents that they do **not**).

---

## 3. The enumerated matrix

### 3.1 Single-control tests
- **Presets (4):** PRESET-001..004 — each preset → `score_<preset>` recolour.
- **Metrics (35):** MET-A-001..015 (v3 mobility/rhythm/typology group + 2 of the
  diverging Jun−Feb fields) and MET-B-001..020 (the remaining v3 demographic
  shares + all top-level environmental/transport fields). Each asserts
  `_fieldKey`, the dropdown value, non-blank deck, no console errors. Seasonal
  fields additionally assert `season-select` becomes enabled.
- **Basemaps (4):** BM-001..004 — each style swaps in place; hexes stay on top;
  camera preserved. BM-004 tolerates the OSM→Voyager fallback (must not throw).
- **Opacity (2 endpoints):** OPA-001 (0% → `fillAlpha` 0), OPA-002 (100% → 255).
- **Extrude (1):** EXT-001 — pitch→~45, hint visible, towers render.
- **Month-window (per window):** SEA-003 (feb), SEA-004 (jun); SEA-005 covers may.
- **Methodology:** DL-001.
- **Hexes toggle:** JOURNEY-005.

### 3.2 Multi-control SEQUENCES and ORDERING-sensitive cases
- **PRESET-005** — preset cycle `nature_first→amenity_first→default`: idempotent,
  order-independent.
- **PRESET-006** — **metric THEN preset**: choosing a preset must snap
  `metric-select` back to `score` and `_fieldKey` to `score` (regression guard
  on the explore.html wiring at lines 647–651 / 669–679).
- **MET-A-015** — **metric THEN back to `score`**: restores score view and
  re-greys season.
- **BM-005** — cycle all four basemaps and back: the deck hex overlay must
  survive every in-place style swap (no lost layer / blank).
- **EXT-002** — extrude ON→OFF restores pitch ~0 and hides hint.
- **EXT-003 / EXT-004** — extrude × dark basemap, extrude × metric (tower height
  tracks the chosen field).
- **OPA-003** — opacity 100→0→50 round-trip; final `fillAlpha` ~128.
- **SEA-005** — month-window feb→jun→may→pooled; pooled restores the inline column.
- **SEA-006** — **seasonal field + feb window THEN a non-seasonal metric**: season
  must grey out again (stale-state guard).
- **POI-003** — arcs + amenities + opacity + zoom-in all at once (simultaneous
  lazy layers).

### 3.3 Deep-link param cases (every documented `?param=`)
`DL-001` methodology · `DL-002` basemap · `DL-003` hexopacity · **`DL-004`
hexopacity clamp >100** · `DL-005` preset · `DL-006` metric (dropdown option) ·
**`DL-007` metric JS-only key → no-op guard** · `DL-008` metric+season combo ·
`DL-009` extrude · `DL-010` arcs · `DL-011` pois (gated) · **`DL-012`
kitchen-sink** (basemap+hexopacity+preset+extrude+methodology together).

### 3.4 Realistic full user journeys
`JOURNEY-001` relocation scout · `JOURNEY-002` mobility analyst (seasonal compare
+ arcs) · `JOURNEY-003` amenity hunter (the zoom-gate path end to end) ·
`JOURNEY-004` shared deep-link restore + continue clicking · `JOURNEY-005`
hexes off→on.

---

## 4. Known-bug regression cases (the heart of the suite)

### 4.1 `bug_osm_amenities` — zoom-gate feedback gap (NOT a data/render bug)
Two paired tests prove the root cause exactly:

- **POI-001 (gate behaves as designed):** toggle amenities ON at the default
  Catalonia-wide zoom (7.6, below the z10.5 gate). Asserts `poisLoaded === 322`
  (data fetched: climbing 219 + yoga 67 + hospital 36) **and**
  `poiCountVisible === 0` **and** the `pois-*` scatter layers are **absent** from
  the deck. This captures the UX gap: checkbox on, data present, nothing drawn.
- **POI-002 (proves load + render — zooms IN past z10.5):** with amenities ON,
  fly the camera to Barcelona at **zoom 11.2**. Asserts all three
  `pois-climbing/pois-yoga/pois-hospital` layers are now present and
  `poiCountVisible === 322`, with the deck non-blank. **This proves the data
  path, `getPosition`, and colors are all correct** — the only gate was zoom.
  The runner's `flyTo` action invokes the same `_scheduleRender()` path the app
  fires from `onViewStateChange` on a real camera move, so the gate is
  re-evaluated faithfully.

Both PASS on current code (the gate is working as designed). Proof screenshots:
`POI-001…poi-gated-zoomed-out.png` (empty over the wide view) vs
`POI-002…poi-zoomed-in-rendered.png` (322 dots over Barcelona).

### 4.2 `bug_arcs` — degraded arc look (a real, currently-failing regression)
- **ARC-001 (load/render):** arcs ON lazy-fetches `arcs.json` (250), ArcLayer
  enters the deck, non-blank. **PASSES** today.
- **ARC-002 (arc LOOK regression):** asserts the **live ArcLayer props** against
  the *fixed/prototype* look:
  - `greatCircle === true` (tall curved great-circle bridges) — today `false` ❌
  - `getHeight` defined — present today (it was the omission of `greatCircle`
    plus the width calibration that flattened them) ✓
  - source/target alpha `=== 200` — today `190` ❌
  - `widthSpread >= 2.0px` — i.e. a visible thin→thick gradient over the 250
    arcs. Today the width formula `max(1, log10(flow+1)*1.8)` on million-scale
    flows (807k–7.87M) evaluates to **10.6–12.4 px for every arc** → spread only
    **1.78 px** (uniform fat stubs) ❌

  **ARC-002 FAILS on today's code, and that failure IS the documented proof of
  `bug_arcs`.** The runner records the exact measured props in the manifest
  (`greatCircle:false, srcAlpha:190, widthSpread:1.78, min 10.6 / max 12.4`) and
  the screenshot `ARC-002…arc-look.png` shows the flat uniform tangle. When the
  fix lands (`greatCircle:true`, alpha 200, width normalised to ~1.5–8 px),
  ARC-002 flips to PASS with **no spec edit**.

> Consequence for CI: the `arcs-amenities-opacity` slice exits **non-zero** while
> `bug_arcs` is unfixed (7/8). This is intentional — the suite stays red exactly
> until the documented bug is repaired.

---

## 5. Proof recorded per step

For each step the per-slice `manifest.json` records:

| Field | Meaning |
|---|---|
| `action` / `selector_or_param` / `value` | the interaction performed |
| `expected` | human-readable description of the step |
| `screenshot` / `canvas_screenshot` | relative paths under `proof/<slice>/` |
| `asserted` | the **live values** read from `window.gb` / DOM / canvas |
| `console_errors` / `page_errors` | tally seen up to this step |
| `failures[]` | precise per-assertion failure strings (empty ⇒ pass) |
| `status` | `PASS` / `FAIL` |

The manifest header records `gl_renderer` (proving software GL), `server_url`,
start/finish timestamps, and `passed/failed/total`.

**Pass criteria (per test):** every step's `status` is `PASS`. A test fails if
any assertion fails, any step throws, or the page emits a console/page error
where `noConsoleErrors` is asserted. **Slice pass:** all its tests pass → exit 0;
otherwise exit 1.

---

## 6. The 8 parallel-safe slices

Each slice is an independent process writing only to `proof/<slice>/`. They can
all run concurrently.

| # | Slice | Tests | Focus |
|---|---|---|---|
| 1 | `presets` | 7 | Baseline load + 4 presets + preset/metric ordering guards |
| 2 | `score-fields-A` | 15 | v3 mobility/rhythm/typology metrics + season-enable + score round-trip |
| 3 | `score-fields-B` | 20 | demographic shares + all top-level environmental/transport metrics |
| 4 | `basemaps-x-extrude` | 9 | 4 basemaps, full cycle, extrude on/off, extrude×basemap, extrude×metric |
| 5 | `arcs-amenities-opacity` | 8 | **both known-bug regressions** + opacity endpoints + simultaneous lazy layers |
| 6 | `month-window-x-season-fields` | 6 | season enable/grey logic + feb/may/jun windows + stale-state guard |
| 7 | `deep-links` | 12 | every `?param=`, clamp + JS-only-key edge cases, kitchen-sink combo |
| 8 | `full-user-journeys` | 5 | realistic end-to-end click-throughs incl. the zoom-gate journey |

---

## 7. How to run

First-time browser bootstrap (once per machine):
```bash
scripts/ui_test.sh --install        # npm install + playwright chromium
```

Run one slice (writes `tests/ui/proof/<slice>/manifest.json`, exits non-zero on
any failure):
```bash
node tests/ui/run-slice.mjs presets
# or: npm --prefix tests/ui run slice -- presets
```

List slices / run everything:
```bash
node tests/ui/run-slice.mjs --list
node tests/ui/run-slice.mjs --all
```

Run all 8 in parallel (independent proof dirs ⇒ safe):
```bash
for s in presets score-fields-A score-fields-B basemaps-x-extrude \
         arcs-amenities-opacity month-window-x-season-fields deep-links \
         full-user-journeys; do
  node tests/ui/run-slice.mjs "$s" & 
done; wait
```

Re-run after each rebuild / UI update — the suite is deterministic and the
manifests + screenshots are the regression evidence.

---

## 8. Current baseline result (this codebase)

| Slice | Result |
|---|---|
| presets | 7/7 ✅ |
| score-fields-A | 15/15 ✅ |
| score-fields-B | 20/20 ✅ |
| basemaps-x-extrude | 9/9 ✅ |
| month-window-x-season-fields | 6/6 ✅ |
| deep-links | 12/12 ✅ |
| full-user-journeys | 5/5 ✅ |
| arcs-amenities-opacity | 7/8 — **ARC-002 fails by design** (documents `bug_arcs`; flips to PASS on fix) |

**81/82 green; the single red is the intentional arc-look regression.** WebGL
renderer at runtime: `ANGLE … SwiftShader` (confirmed software GL, no GPU).
