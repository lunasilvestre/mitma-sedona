# Geo-browser → idiomatic deck.gl refactor — DESIGN (no implementation)

**Date:** 2026-06-20
**Scope:** `docs/app/geobrowser-map.js` (the `GeoBrowser` module) and its wiring in `docs/explore.html`.
**Constraint (hard):** keyless, no-build, static. CDN `<script>` globals only (`deck`, `maplibregl`, `h3`). **No bundler, no React, no vis.gl JSX.** Target = idiomatic **vanilla** deck.gl, not a framework rewrite.
**Gate:** the 82-test headless UI suite (`tests/ui/tests.json`, 8 slices) must stay green at every step — including ARC-002, which is RED today by design and must flip to PASS (never regress to a different failure).

---

## 1. Current architecture (precise, with cites)

### 1.1 Integration model — *separate synced Deck instance* (NOT MapboxOverlay)
The app runs **two stacked canvases**, not deck-as-a-MapLibre-layer:

- MapLibre is built passive: `interactive: false`, no `AttributionControl` default — `geobrowser-map.js:464-488` (`_buildBasemap`). It is a dumb basemap host.
- deck.gl is a **standalone `deck.Deck`** on its own `<canvas id="map-deck">` appended as a sibling inside `#map` — `:527-557` (`_buildDeck`). It owns the camera: `controller: true`, `initialViewState: this._viewState`.
- Camera sync is **one-way, manual**: deck's `onViewStateChange` writes `self._viewState` then calls `self.map.jumpTo({...})` to drag MapLibre along — `:540-553`. CSS stacks the deck canvas above the basemap via `#map #map-deck { z-index: 2 }` — `explore.css:441-450`.
- Basemap swap is `map.setStyle(style, {diff:false})` then re-`jumpTo` the saved camera on `styledata`, and the deck canvas is **never touched** because it is a separate sibling — `:505-524` (`_applyStyle`). This is the explicit design reason the hexes "stay on top by construction" (`:69-74`, `:133-140`).

> **Determination:** integration = **synced dual-canvas**, deck.gl as camera master. `@deck.gl/mapbox` / `MapboxOverlay` is **not** used anywhere.

### 1.2 Layer-management model — imperative full rebuild every change
- State lives on the instance: `_layersOn = {hexes, arcs, pois}` (`:397`), `_fieldKey`, `_preset`, `_season`, `_extrude`, `_fillAlpha`, `_viewState`.
- `_render()` (`:667-684`) **rebuilds the entire layer array from scratch** on every call: it news up a fresh `H3HexagonLayer` (and, if on, ArcLayer×2 + ScatterplotLayer×N), then `this.deck.setProps({ layers })`. Every public setter ends in `_render()`.
- `_scheduleRender()` (`:559-567`) is a hand-rolled rAF debounce, used **only** from `onViewStateChange` for the POI zoom-gate (`:552`).
- `updateTriggers` are present and correct on the hex layer (`:741-744`) and arcs (`:806`, `:814`) — so reactive diffing already half-exists, but it is undercut by the full re-`new` of every layer on every `_render()`. deck still diffs by `id`, so this works, but the module re-allocates accessor closures and constants each render.

### 1.3 FIELDS registry + colour/width scales
- `FIELDS` (`:171-364`) — ~35 entries, each `{column, ramp, label, kind, goodWhen, unit, lowLabel, highLabel}`, plus `pivot` (diverging), `seasonal`/`seasonKey` (15 season-aware fields). `kind ∈ {score, sequential, diverging, presence, boolean, categorical}`.
- **Colour scale (good, data-driven already):**
  - `_domain(field)` (`:604-640`) — prefers `manifest.score_stats[preset].{min,max}` for score columns; else **scans the 45 220 hexes once** for `[min,max]`, cached per `(column[,season])`. Booleans fold into `[0,1]`. **No magic numeric constants here** — domains are computed.
  - `_normalise(field,v,dom)` (`:647-664`) — diverging maps `pivot→0.5` spread by the larger half-range; sequential is `(v-lo)/span`; `goodWhen:'low'` inverts `t` so good=bright; boolean honours polarity. This is the polarity logic the legend must mirror (`:994-1003`).
  - `rampColor(name,t)` (`:151-164`) samples a 6-stop ramp; `RAMPS` (`:28-42`) viridis/magma/RdBu; `CATEGORICAL` (`:47-61`) fixed label→RGB; `categoricalColor` (`:138-148`) with `-\d+` suffix-strip + deterministic hash fallback.
  - Opacity = alpha **byte** appended to every RGB: `[r,g,b,this._fillAlpha]` (`:727,:731`), nulls scaled `NULL_ALPHA*(fillAlpha/FILL_ALPHA)` (`:719`). `NULL_COLOR=[120,128,140]` is a **distinct grey, never ramp-low** (`:65-67`).
- **Width scale (the genuinely hand-rolled / magic-constant part — the arcs):**
  - `_computeArcDomain()` (`:760-772`) scans `arcs.json` for `[logMin,logMax]` over `log10(flow+1)`, with a **hard-coded fallback `5.907`** if empty.
  - `_arcWidth(d)` (`:775-781`) maps that log domain linearly into **`1.5 + t*6.5`** (the "1.5–8 px" gradient). The glow twin adds `+3` px (`:805`). Source/target alpha **200** (`:811-812`), glow alpha **45** (`:803-804`). `greatCircle:true`, `getHeight:0.4` (`:792-796`).
  - This is the scale family with the most magic constants and the one whose earlier regression (raw `log10*1.8` → uniform 10.6–12.4 px stubs) is encoded as the still-red **ARC-002**.

### 1.4 Hex layer type — already `H3HexagonLayer`
`_hexLayer()` (`:686-752`) returns a single **`deck.H3HexagonLayer`** keyed on `getHexagon: d => d.h3_id`. **No** hand-rolled `h3-js` boundary → PolygonLayer path exists. (h3-js is loaded only to satisfy deck's bundled H3 shim — `explore.html:18-27`.) Extrusion: `extruded`, `getElevation` = normalised value, `elevationScale` shrinks with zoom (`:701-703`). A deliberate **no-`transitions`** note (`:745-751`) — animated 45 k-instance buffers overrun SwiftShader; this is a hard headless constraint.

### 1.5 The other subsystems
- **Basemap swap / fallback** — `BASEMAPS` registry (`:96-114`), `_resolveStyle` (`:459-462`), `_applyStyle` (`:505-524`), OSM→Voyager fallback on `map.on('error')` (`:484-500`). Attribution control is **removed + re-added** to change credit (no MapLibre setter) — `:515-523`.
- **Presets** — `setPreset` (`:1027-1031`) sets `_preset`, forces `_fieldKey='score'`, resolves to `score_<preset>` in `_activeField()` (`:570-577`).
- **Season selector** — `_season` ('' = pooled), `setSeason` (`:1052-1062`) lazy-loads `seasons.json` on first non-pooled pick; `_valueFor` (`:589-597`) reads the per-season sidecar value vs the inline column; `_seasonActive` gate (`:580-582`); domain re-scanned per `(column@season)`.
- **Arcs** — lazy-fetched on toggle (`:1107-1110`), `_arcLayer()` returns **`[glow, arcs]`** compound (`:783-818`).
- **POIs (zoom-gate)** — `_poiLayers()` returns `[]` when `zoom < POI_ZOOM_GATE` (10.5) (`:822-849`); one ScatterplotLayer per category; the gate is re-evaluated from `onViewStateChange → _scheduleRender` (`:552`). Plus a **hand-rolled DOM hint** (`_poiHintEl`/`_updatePoiHint`, `:857-892`) injected next to the checkbox.
- **Extrude** — `setExtrude` (`:1071-1080`) flips `_extrude`, `_flyTo`s pitch 45/0 (`:1082-1101`).
- **Legend** — `_emitLegend` (`:953-1014`) computes 6 swatches + swaps numeric ends *and* word labels for `goodWhen:'low'`; categorical branch orders by `manifest.typology_labels`.

### 1.6 What is hand-rolled that deck already provides
| Hand-rolled today | deck.gl already provides |
|---|---|
| `_scheduleRender()` rAF debounce (`:559`) | `setProps` is already batched/scheduled by deck's internal render loop |
| Full `new`-everything in `_render()` each call (`:667`) | reactive layer diffing via stable `id` + `updateTriggers` — re-`new` is unnecessary |
| POI z-gate as `return []` + manual `_scheduleRender` on camera move (`:825,:552`) | `Layer.visible` / per-layer extension; deck re-renders on viewState already |
| Arc width via bespoke `_computeArcDomain`+`_arcWidth` magic `1.5+t*6.5` (`:760-781`) | the same *computed-domain* pattern `_domain()` already uses for hexes — just not shared |
| Manual MapLibre `jumpTo` camera sync (`:540-553`) | `MapboxOverlay` interleaved mode syncs camera automatically (one controller) |

**Crucial nuance:** the colour pipeline is already idiomatic-ish (computed domains, `updateTriggers`). The drift bug the prompt references lived in the **arc width** path, which is the *one* scale that does **not** go through the shared `_domain/_normalise` machinery. The refactor's highest-value move is to **unify scales**, not to rewrite the (already sound) hex colouring.

---

## 2. The idiomatic target

Vanilla deck.gl, same dual-canvas-or-overlay host, but:

### 2.1 Declarative layers + `setProps` + `updateTriggers` (reactive, no manual rebuild)
- Keep `_render()` as the single `setProps({layers})` call, but build layers from **stable, memoised factory functions** whose accessors are defined once (module scope, closing over `self`), not re-allocated per render. deck already diffs by `id`; the win is removing per-render allocation and the bespoke `_scheduleRender` (deck schedules its own draw on `setProps` and on viewState change).
- **Benefit:** fewer moving parts, no rAF bookkeeping, accessors stable so `updateTriggers` is the *only* thing that forces attribute recompute — exactly the deck contract. The no-`transitions` invariant (`:745`) is preserved (we add none).

### 2.2 A single SCALE helper — computed domains, NO magic constants (the core fix)
Introduce one tiny `makeScale(field, {hexes, manifest, season})` that returns `{ color(v), size(v), elevation(v), domain }`, built **entirely from computed `[min,max]`** via the existing `_domain()` logic. Then:
- Hex `getFillColor` calls `scale.color(v)` (unchanged behaviour, now shared).
- **Arc width** calls a `makeLinearScale([logMin,logMax] → [minPx,maxPx])` from the **same** computed-domain family — replacing `_arcWidth`'s inline `1.5+t*6.5`. The px endpoints (`1.5`, `8`) and alpha (`200`) become **named config** (`ARC_WIDTH_PX=[1.5,8]`, `ARC_GLOW_DPX=3`, `ARC_ALPHA=200`) at the top, not buried literals — so presentation can't silently drift when flow magnitude changes (the exact ARC-002 failure mode).
- **Benefit:** one tested scale path for *every* visual channel; the "million-scale flow flattens every arc" class of bug becomes structurally impossible because the domain is always measured, never assumed. ARC-002 flips green and *stays* green across data regens.

### 2.3 `H3HexagonLayer` — already idiomatic, keep as-is
No change of layer type needed (it is *not* a hand-rolled Polygon path). Keep `getHexagon/getFillColor/getElevation` + the existing `updateTriggers`. Only refactor the *accessor bodies* to call the shared scale.

### 2.4 POI gate via deck-native zoom awareness
Replace `_poiLayers()` returning `[]` + `_scheduleRender` with: always include the POI ScatterplotLayers when `_layersOn.pois`, but set **`visible: viewState.zoom >= POI_ZOOM_GATE`** per layer. deck re-renders on viewState natively, so the manual `_scheduleRender` in `onViewStateChange` can go.
- **Behaviour contract:** POI-001/DL-011 assert the `pois-*` layers are **ABSENT** (`deckLayerAbsent`) when gated, and `poiCountVisible===0`. `visible:false` keeps a layer *present* in `deck.props.layers`. **→ This is an OPEN DECISION (§5):** either (a) keep the `return []` gate (zero refactor risk, tests untouched) or (b) move to `visible:` *and* update POI-001/DL-011 to assert absence-of-drawn-points differently. Recommendation: **keep `return []`** — it already satisfies the deck idiom "don't include layers you don't want", costs nothing, and avoids editing locked tests. The DOM hint (`_updatePoiHint`) stays as-is (it is app chrome, not deck).

### 2.5 `MapboxOverlay` interleaving — evaluate, default to keep
`MapboxOverlay` (`@deck.gl/mapbox`, already in the deck.gl bundle global) would collapse the dual-canvas + manual `jumpTo` sync into one controller and one canvas, which is the most "idiomatic" integration.
- **But** it inverts camera ownership (MapLibre becomes interactive master), changes the canvas topology the harness depends on, and `extruded` H3 towers must interleave correctly with the raster basemap. The headless harness samples **only** `#map-deck` (`ui_test_plan.md` §"Harness limitation"); `MapboxOverlay` in *overlaid* mode keeps a separate deck canvas (safe), in *interleaved* mode merges into MapLibre's canvas (**would blind `analyzeCanvas` and break `canvasRendered` across ~70 tests**).
- **→ OPEN DECISION (§5):** the current synced dual-canvas is *already* a clean, test-compatible idiom. Recommendation: **do not adopt `MapboxOverlay`** in this pass — the canvas-topology change is a high-blast-radius rewrite of the exact thing the harness measures, for marginal idiom gain. Revisit only if the harness is taught to sample the basemap canvas.

---

## 3. Migration approaches

### (a) Big-bang rewrite of `geobrowser-map.js`
Rewrite the module clean against the idiomatic target in one PR.
- **+** cleanest end state, no transitional scaffolding.
- **−** 82 tests (esp. the ~70 `canvasRendered`/`deckLayerIds` assertions + the 2 encoded bug regressions) all move at once; a single topology mistake (e.g. accidental `MapboxOverlay` interleave) blanks the suite with no bisect signal. High risk against the "green at every step" gate.

### (b) Incremental strangler — one subsystem at a time, suite between each *(RECOMMENDED)*
Ordered so each step is independently test-guarded:
1. **Scales first.** Extract `makeScale`; route hex colour through it (no behaviour change — guarded by every MET-*/PRESET-*/SEA-* `canvasRendered`). Then route **arc width** through the shared computed-domain scale + named config → **ARC-002 flips to PASS** (the step's own proof). Guard: `arcs-amenities-opacity` slice.
2. **Layer management.** Stabilise accessor factories, drop `_scheduleRender`, rely on deck's native scheduling. Guard: POI-001/002 (gate still fires on camera move), JOURNEY-003 (zoom-gate path), all `deckLayerIds` slices.
3. **Integration (optional, likely deferred).** Only if §5 approves `MapboxOverlay`; otherwise leave the synced dual-canvas. Guard: BM-001..005 (style swaps), EXT-003.
- **+** every step is small, reversible, and has an owning slice that proves it; ARC-002 becomes a *milestone*, not a side effect. **−** brief period where scale code coexists with the old arc path (one commit).

### (c) Hybrid — big-bang the internals, freeze the integration
Rewrite scales + layer management together in one PR, but **explicitly do not touch** the basemap/camera integration.
- **+** faster than (b), still avoids the riskiest change. **−** couples the scale fix to the layer-management refactor, so if `canvasRendered` regresses you can't tell which half did it; loses the clean "ARC-002 flips on the scale commit alone" signal.

**Recommendation: (b) incremental strangler.** It is the only approach where the suite is provably green at every commit, it makes the arc-scale fix a discrete verifiable milestone (ARC-002 PASS), and it defers the one genuinely dangerous change (integration topology) behind a sign-off gate. Matches the "keep the suite green at every step" requirement exactly.

---

## 4. Risks & invariants the refactor MUST preserve (each tied to its guard)

| Invariant | Source | Guarding tests |
|---|---|---|
| **4 presets** → `score_<preset>` recolour; preset forces field='score' | `:1027-1031` | PRESET-001..006, DL-005/012 |
| **~35 metrics**, each `_fieldKey` + dropdown value + non-blank | `FIELDS :171-364` | MET-A-001..015, MET-B-001..020 |
| **Ramps & polarity** unchanged (viridis/magma/RdBu; `goodWhen:'low'` inverts; legend swaps numbers **and** words together) | `:647-664, :994-1003` | every MET-* `canvasRendered`; visual legend (manual) |
| **Diverging pivots** (`through_ratio` 1.0 — raw outflow/inflow ratio centred at 1.0, `weekend_*` 1.0, `summer_minus_winter` 0.0, `uhi` 0.0) | `FIELDS` | MET-A-006/007/008/009, MET-B-007/016 |
| **NULL = distinct grey** `[120,128,140]`, scaled with opacity, never ramp-low/zero | `:65-67, :719-726` | sparse-field tests (MET-B-008/010/019, `canvasMinColors:3`) |
| **Season selector + deltas:** '' pooled reads inline; feb/may/jun lazy-load `seasons.json`; non-seasonal greys the control | `:1052-1062, :589-597` | SEA-001..006, DL-008, JOURNEY-002 |
| **Deep-link params** (`basemap,hexopacity,preset,metric,season,extrude,arcs,pois,methodology`) + clamp + JS-only-key no-op | `explore.html:718-749` | DL-001..012 |
| **Basemap options** (4) swap in place, camera + hex overlay preserved, OSM→Voyager fallback must not throw | `:96-114, :484-524` | BM-001..005, DL-002/012, EXT-003 |
| **Restored arc look:** `greatCircle:true`, `getHeight` defined, alpha **200**, width **~1.5–8 px** visible spread (`widthSpread≥2.0`) | `:783-818` | **ARC-002 (must flip RED→GREEN)**, ARC-001 |
| **POI zoom-gate** at z10.5 (data loads=322, 0 drawn below gate, 322 above) + the hint | `:119, :822-849` | POI-001/002/003, DL-011, JOURNEY-003 |
| **Extrude** pitch→45 on / →0 off, hint toggles, towers track active field | `:1071-1101` | EXT-001..004, DL-009/012, JOURNEY-003 |
| **Opacity** as alpha byte; 0→fillAlpha 0, 100→255, 50→128; live, no re-fetch | `:1144-1148` | OPA-001..003, DL-003/004 |
| **`window.gb` shape** (`_fieldKey,_preset,_season,_extrude,_fillAlpha,_basemapKey,_layersOn,_viewState,_arcs,_pois,deck.props.layers`) | `explore.html:643` | **every** test reads these — the public-ish surface is a hard contract |
| **Layer ids** exactly `hexes`,`arcs`,`arcs-glow`,`pois-<cat>` | `:705,:801,:808,:831` | all `deckLayerIds`/`deckLayerAbsent` assertions |
| **No deck `transitions`** (SwiftShader buffer limit) | `:745-751` | implicit — any add reintroduces `GL_INVALID_OPERATION` headless crash |
| **Published score byte-identical** — display-only; never touch `score_*` columns or `manifest.score_stats` | data | not a UI test; honour by *not* writing data |

**Non-obvious traps:**
- `window.gb._arcs.length === 5000` and `poisLoaded === 322` are **exact** counts — keep lazy-load shapes identical.
- `analyzeCanvas` samples only `#map-deck`; **never** move hex rendering off that canvas (rules out `MapboxOverlay` interleave — §2.5).
- `deckLayerAbsent` semantics mean a gated POI layer must be **truly absent**, not `visible:false` — keep the `return []` gate (§2.4).
- The `arcs-glow` layer is part of the look but ARC-002 reads the `arcs` layer's props; keep both, keep ids.

---

## 5. Validation per step + OPEN DECISIONS

### How each step is validated (slice → subsystem)
| Refactor step | Run these slices | Proof |
|---|---|---|
| Extract `makeScale`, route hex colour | `presets`, `score-fields-A`, `score-fields-B`, `month-window-x-season-fields` | all `canvasRendered`/`field`/`season` stay green (behaviour-neutral) |
| Route **arc width** through shared scale + named config | `arcs-amenities-opacity` | **ARC-002 PASS** (greatCircle/getHeight/alpha 200/widthSpread≥2), ARC-001 still 250 |
| Stabilise accessors, drop `_scheduleRender` | `arcs-amenities-opacity`, `full-user-journeys` | POI-001/002, JOURNEY-003 (gate fires on `flyTo`), no console errors |
| (If approved) integration change | `basemaps-x-extrude`, `deep-links` | BM-001..005, EXT-003, DL-002/012 |
| Full regression after each step | all 8 slices (parallel, per `ui_test_plan.md` §6) | target **82/82** green (was 81/82; ARC-002 was the intended red) |

Run command per `ui_test_plan.md` §7: `node tests/ui/run-slice.mjs <slice>` (or the parallel `for s in … --all` loop). Independent proof dirs ⇒ run all 8 concurrently.

### OPEN DECISIONS needing user sign-off
- **OD-1 — Integration: keep synced dual-canvas vs adopt `MapboxOverlay`.** Recommendation: **keep current** (overlay interleave would merge into MapLibre's canvas and blind `analyzeCanvas`, breaking ~70 `canvasRendered` assertions). Adopt only with a harness change to sample the basemap canvas.
- **OD-2 — POI gate: keep `return []` vs `visible:` prop.** Recommendation: **keep `return []`** — POI-001/DL-011 assert `deckLayerAbsent` (a `visible:false` layer is still *present*), so switching to `visible:` would require editing locked tests for zero idiom benefit.
- **OD-3 — Refactor scope.** Recommendation: **scales + layer-management only** (approach b, steps 1–2); **defer integration** (step 3) behind OD-1. Confirm this is the intended blast radius, or expand to a full module rewrite (approach a) if a clean-slate end state is preferred over the green-at-every-step guarantee.
- **OD-4 — `H3HexagonLayer` vs Polygon.** No decision needed to *change* it (it is already `H3HexagonLayer`); confirm we are **not** expected to switch to a manual `h3-js`→PolygonLayer path (we should not — current is the idiom).
- **OD-5 — Named-config surface for arc presentation.** Confirm promoting the arc literals (`[1.5,8]` px, alpha `200`, glow `+3`) to top-of-file named constants is acceptable as the "no magic constants" remedy (it changes no behaviour, only locality), vs a heavier data-driven `manifest.arc_stats` domain (would require a pipeline/data change — out of scope, and the in-file scan already computes the domain).
