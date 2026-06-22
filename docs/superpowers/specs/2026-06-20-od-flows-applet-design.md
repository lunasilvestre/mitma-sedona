# OD-Flows Applet — v1 DESIGN SPEC

Date: 2026-06-20
Status: DESIGN ONLY — no implementation. Scope, contracts, and phases for sign-off.
Author: lead architect
Supersedes: nothing (greenfield sibling applet). Reads:
- Research brief — `docs/research/2026-06-20-fullset-flowmap-duckdb-deckgl.md`
- Storytelling design — `docs/superpowers/specs/2026-06-15-mitma-sedona-storytelling-geobrowser-design.md`
- Deck refactor design — `docs/superpowers/specs/2026-06-20-geobrowser-deckgl-refactor-design.md`

---

## 0. LOCKED v1 DECISIONS (user signed off — do not relitigate in v1)

1. **Precomputed per-(season × day-type) top-N OD lever-slices ONLY.** DuckDB-WASM
   power-mode is DEFERRED (escape hatch, not v1).
2. **Lean STOCK deck.gl `ArcLayer` + `DataFilterExtension`.** Flowmap.gl is DEFERRED.
   No custom GLSL, no shader hooks.
3. **Build on the live MapLibre 4.7 flat shell**, but as a SEPARATE page
   `docs/explore_flows.html`. The globe (Cesium) comes later.
4. **VENDOR the resolved deck.gl / luma ESM into the repo** under `docs/vendor/`.
   No runtime `esm.sh` / `unpkg` CDN for the renderer at page load.
5. **`hexes.json` (69 MB, over the 50 MB warning) → LFS/sidecar split is a PARALLEL
   cleanup**, tracked here but not on this applet's critical path. The OD store is a
   SEPARATE sibling so `hexes.json` stays byte-identical.

### Hard constraints (carried from the project, binding)
- **Keyless. No build step. Fully static on GitHub Pages.** Import-map / `<script>` only;
  no bundler, no React, no transpile.
- **Same-origin only.** Pages serves HTTP **range** (`206` + `accept-ranges: bytes`);
  Releases do **NOT** send CORS → all OD data lives under `docs/`, never a Release asset.
- **Stack pins:** deck.gl **9.3**, MapLibre **4.7**, h3-js **4.1** (h3-js global MUST be
  defined before deck.gl evaluates — load-order is load-bearing).
- **Anti-hairball is alpha-over (deck.gl default `blend:true`, src-alpha / one-minus-src-alpha),
  NEVER additive.** Density = more corridors + flow-driven width + threshold + top-N cap.
- **Width must stay data-driven** (domain scanned/seeded at load), guarded by the harness —
  the OB1 magnitude-coupling regression must not recur.

### Grid honesty correction (carried into the contract)
The prompt assumed "4 seasons × 2 day-types = 8 slices". **Only 3 months are on disk**
(Feb/May/Jun = winter / spring / summer-onset; Mar/Apr/Jul–Jan absent). The honest grid is
**3 month-windows × 2 day-types = 6 slices**, consistent with the existing
`manifest.seasonal.note` ("three calendar month-windows, NOT a climate average"). The
contract is authored for **6 slices, N-extensible to 8** if a 4th window is ever ingested —
the index drives the picker, so adding a slice is a data change, not a code change.

---

## 1. EXECUTIVE SUMMARY (~200 words)

A new static sibling applet, `docs/explore_flows.html` + `docs/app/flows/*.js`, renders
Catalonia origin–destination corridors as deck.gl arcs over the same keyless MapLibre 4.7
satellite/dark shell the live geo-browser uses — but in its own page, its own vendored ESM
deck.gl 9.3 context, and its own data store, so `hexes.json` stays byte-identical and the
existing UMD map is untouched. Data is a small grid of **precomputed per-(month-window ×
day-type) top-N slices** (6 slices, N=8000), each a `.bin` of Float32 geometry+rank+flow plus
a lazy `hours.bin` sidecar carrying the 24-float hourly profile, indexed by one tiny
`flows_index.json`. The renderer is a **stock `ArcLayer` fed via binary `data.attributes`**
(zero-copy typed arrays) with **`DataFilterExtension(filterSize:1, countItems:true)`** as the
single lever primitive: hour is scrubbed by re-evaluating `getWidth`/`getFilterValue` via
`updateTriggers` (one tiny attribute re-upload, sub-ms over 10k arcs — verified mechanism,
NOT 24 resident slices, NOT a pure uniform); volume threshold and top-N are live uniform
`filterRange` slides at zero re-upload cost. A **vitality semaphore** reads the GPU-side
`onFilteredItemsChange` count as a live "N of M corridors" governor — green/amber/red with a
top-N auto-cap that raises the threshold uniform until the on-screen set is safe, pre-empting
the Barcelona hairball. Levers, default views, and an lz-string deep-link complete v1; a new
`flows` harness slice proves it.

---

## 2. DATA CONTRACT

### 2.1 Where it lives
A new sibling subtree, isolated from `hexes.json`:

```
docs/story_data/flows/
  flows_index.json                      # tiny human-readable index (drives the picker)
  geom/<window>_<daytype>.bin           # 6 files — geometry + rank + flow (eager per active slice)
  hours/<window>_<daytype>.hours.bin    # 6 files — 24-float hourly profile (LAZY: only on scrub/animate)
```

- `<window>` ∈ `{feb, may, jun}` (matches existing export keys; relabel as "month-window", not "season").
- `<daytype>` ∈ `{weekday, weekend}` (from `is_weekend`).
- Naming mirrors the existing lazy-sidecar idiom (`rhythm.json`, `seasons.json`): the cheap
  geometry loads instantly; the heavy 24h array defers until the hour scrubber/animation engages.
- The new files are **never** routed through `export_mitma_layers.py`'s `hexes.json` merge.
  `arcs.json` (the existing 5k single-window arc file) is left in place and untouched.

### 2.2 Per-arc record (logical schema)
Each arc carries exactly these fields. Sizes assume `N = 8000` arcs/slice (recommendation
from the slice-size estimate; the `.bin` can hold more arcs than are ever drawn at once — the
governor keeps the on-screen set small).

| field            | type    | unit / meaning                                  | wire location |
| ---------------- | ------- | ----------------------------------------------- | ------------- |
| `source_lon`     | f32     | origin centroid lon (EPSG:4326, round 6)        | geom.bin      |
| `source_lat`     | f32     | origin centroid lat                             | geom.bin      |
| `target_lon`     | f32     | dest centroid lon                               | geom.bin      |
| `target_lat`     | f32     | dest centroid lat                               | geom.bin      |
| `flow`           | f32     | **trips/day** for the slice (windowed sum ÷ this slice's own day-count) | geom.bin |
| `rank`           | f32     | 0-based dense rank within the slice (0 = strongest); reserved for a future rank filter | geom.bin |
| `flow_by_hour[24]` | f32×24 | trips/day at each hour 0..23 (Σ ≈ `flow`)      | hours.bin     |

**Distance band** (`distancia` ∈ {`0.5-2`,`2-10`,`10-50`,`>50`}) is an **OPEN DECISION**
(§9, D2): v1 default is **omit** (a single dominant band per pair is lossy to pick, and a 4th
filter dim is not needed for v1). If kept, it is a per-arc `uint8` category column appended to
`geom.bin`, wired to `DataFilterExtension` `categorySize:1` — but that is reserved, not v1.

### 2.3 Wire format (the `.bin` files)
**Why size-3 positions, not a 6-float interleaved record.** deck.gl 9.3's `ArcLayer`
registers `instanceSourcePositions` / `instanceTargetPositions` as **`size:3`** attributes.
Supplying a `size:2` (lon,lat) view — interleaved or not — leaves the per-instance `z`
unfilled, and the layer silently reads garbage / renders every arc at `[0,0]`. v1 therefore
emits **explicit xyz (z=0) positions** and keeps the per-attribute buffers SEPARATE rather
than packing one 6-float interleaved record. Separate contiguous buffers (each its own
`Float32Array`, `size` matching the accessor, no `offset`/`stride` arithmetic) are the
simplest correct layout and map 1:1 onto the binary `data.attributes` accessors (§4.2).

`geom/<window>_<daytype>.bin` is one file containing **four concatenated sub-arrays** in this
fixed order (each a flat `Float32Array`, arc order = descending flow, index `i` aligns across
all of them and across `hours.bin`):

| sub-array | layout                          | floats/arc | accessor              |
| --------- | ------------------------------- | ---------- | --------------------- |
| `srcPos`  | `[ src_lon, src_lat, 0 ] × N`   | 3          | `getSourcePosition`   |
| `dstPos`  | `[ dst_lon, dst_lat, 0 ] × N`   | 3          | `getTargetPosition`   |
| `flow`    | `[ flow ] × N`                  | 1          | width / filter source |
| `rank`    | `[ rank ] × N`                  | 1          | reserved (rank filter)|

```
geom.bin = concat(  srcPos[3N],  dstPos[3N],  flow[N],  rank[N]  )
                    └ z prefilled 0 ┘
```
- Per arc = **3 + 3 + 1 + 1 = 8 Float32 = 32 bytes**.
- N=8000 → **256 KB/slice**, 6 slices → **~1.5 MB total**. (No 24h array here — that is the
  dominant cost and is deferred to `hours.bin`.)
- Loaded with `new Float32Array(await (await fetch(url)).arrayBuffer())`, then sub-arrayed by
  the fixed offsets above (`buf.subarray(0,3N)`, `buf.subarray(3N,6N)`, `buf.subarray(6N,7N)`,
  `buf.subarray(7N,8N)`) and handed straight to the `size:3` / `size:1` binary-attribute
  accessors (§4.2). Zero parse cost, zero offset/stride math. Splitting into four `.bin` files
  instead of one concatenated file is an equivalent, equally-simple alternative; v1 keeps one
  file per slice to match the lazy-sidecar idiom and minimize request count.

**`hours/<window>_<daytype>.hours.bin`** — one record = **24 Float32 = 96 bytes**, flat in the
SAME arc order as `geom.bin` (index `i` aligns across both files):
```
[ h0, h1, ... h23 ]   × N
```
- N=8000 → **768 KB/slice**, 6 slices → **~4.5 MB total**. Fetched **only** when the scrubber or
  time-animation is first engaged for the active slice (lazy, cached per slice thereafter).

**Total store ≈ 6.0 MB for 6 slices** (geom ~1.5 MB always + hours ~4.5 MB on demand).
Comfortably under the 50 MB warning; nowhere near the `hexes.json` LFS problem; ~3× smaller than compact JSON with
zero parse cost. Arrow is byte-equivalent to `.bin` but adds a dependency for no win at this
fixed schema — skip it.

### 2.4 The index — `flows_index.json`
The only human-readable file; drives the picker and pre-seeds the width log-domain so the
client never scans bytes to find min/max:
```json
{
  "version": 1,
  "unit": "trips_per_day",
  "n_per_slice": 8000,
  "hours": 24,
  "grid": { "windows": ["feb","may","jun"], "daytypes": ["weekday","weekend"] },
  "window_labels": { "feb": "Feb 2025 (winter)", "may": "May 2025 (spring)", "jun": "Jun 2025 (summer-onset)" },
  "global_flow_log_domain": [<logMin>, <logMax>],   // log10(flow+1) across ALL slices -> stable width across slices
  "slices": [
    {
      "key": "feb_weekday",
      "window": "feb", "daytype": "weekday", "is_weekend": false,
      "n": 8000,
      "day_count": 20,                 // distinct fecha partitions in THIS (month,is_weekend) bucket
      "flow_min": 12.4, "flow_max": 90233.7,
      "flow_log_domain": [1.13, 4.96], // per-slice, for an optional per-slice width rescale
      "geom_url": "geom/feb_weekday.bin",
      "geom_bytes": 256000,
      "hours_url": "hours/feb_weekday.hours.bin",
      "hours_bytes": 768000,
      "includes_am_zones": true        // see D3
    }
    /* ... 5 more ... */
  ],
  "notes": "month-window comparison, NOT a climate average; 3 months on disk (Feb/May/Jun)."
}
```
- **`day_count` per slice is mandatory and per-bucket.** Weekends are ~2/7 of days; dividing a
  weekend slice by the full month day-count understates weekend flow ~2.5×. Each slice divides
  its windowed sum by its OWN distinct-day count (the §3 export computes this from the `fecha`
  partitions that fall in that `(month, is_weekend)` bucket).
- **`global_flow_log_domain`** is the default width domain so corridor width is comparable
  ACROSS slices (the lesson: width is data-driven but must not silently re-scale when the user
  switches slice). `flow_log_domain` per slice is offered for an optional "rescale per slice"
  toggle (OPEN DECISION D4).

### 2.5 Centroid anchoring (geometry identity across slices)
Every slice's endpoints are anchored at the SAME distrito-centroid map
(`zonificacion_distritos.geojson`, 3,909 features, `geopandas .centroid`), so an OD pair has
byte-identical endpoints in every slice it appears in — geometry is stable when the user
switches window/day-type. Pairs whose zone is absent from the geojson are dropped (matches the
existing Spark inner join and `_build_arcs`).

---

## 3. PRECOMPUTE EXPORT PLAN

### 3.1 New standalone script — `scripts/export_od_flows.py`
A **fork**, not an extension, of `_build_arcs` (lines 331–399 of
`scripts/export_mitma_layers.py`). It writes ONLY the flow slice files and **must not touch
`hexes.json`, `manifest.json`, the score, or any existing sidecar.** Run with
`/home/nls/miniforge3/envs/sedona/bin/python` (needs pandas + geopandas).

Reused machinery (import or copy from `export_mitma_layers.py`):
- `SILVER_OD` (L66), `ZONES_GEOJSON` (L71) constants.
- `_silver_fechas()` (L196–210) → the `fecha=YYYYMMDD` partition list.
- the centroid block (L379–386): `gpd.read_file(ZONES_GEOJSON)[["ID","geometry"]]` →
  `cmap = {ID: (lon, lat)}`. ONE cmap anchors every slice.
- `_build_arcs`'s stream-one-partition-at-a-time, drop-intra-zone, group-and-concat loop
  (L363–370) — the memory-bounded pattern over the ~390M-row window.
- the `round(_,6)` / `round(_,2)` conventions; `_scrub`/`_records_nan_safe` only for the JSON index.

### 3.2 Algorithm
1. **Enumerate + bucket partitions.** For each `fecha=YYYYMMDD`, derive `month ∈ {feb,may,jun}`
   from the date and `is_weekend` from the partition's `is_weekend` column (or `weekday`/calendar).
   Group the partition paths into the 6 `(month, is_weekend)` buckets. Record each bucket's
   distinct **`day_count`**.
2. **Aggregate per slice with hourly breakdown.** For each bucket, stream its partitions reading
   `["origen","destino","periodo","viajes"]`. Drop intra-zone (`origen != destino`). Accumulate a
   running aggregate keyed by `(origen,destino)` that holds both the total `viajes` and a 24-slot
   `viajes_by_hour` vector (pivot `viajes` over `periodo` 0..23). Incremental concat+regroup as in
   `_build_arcs`, extended with the hour pivot (the `_load_rhythm_map` 24-float precedent, L270–292,
   is the template).
3. **Per-day normalization.** Divide the windowed sum AND each hour slot by that slice's own
   `day_count` → **trips/day** and **trips/day per hour**. (Σ hours ≈ flow, modulo rounding.)
4. **Top-N + rank.** `top = agg.nlargest(N, "flow")` with **N=8000**. Assign dense `rank`
   (0..N-1, 0 = strongest).
5. **Anchor endpoints** at `cmap`; drop pairs whose zone is missing (inner-join parity).
6. **Emit.** Write `geom/<window>_<daytype>.bin` as four concatenated `np.float32` sub-arrays
   in fixed order — `srcPos[(lon,lat,0)×N]`, `dstPos[(lon,lat,0)×N]`, `flow[N]`, `rank[N]`
   (the `z` column is a prefilled-0 array so `ArcLayer`'s `size:3` position accessors get xyz;
   §2.3) — then `hours/<window>_<daytype>.hours.bin` (`np.float32` flat `[h0..h23] × N`, same
   arc order), and accumulate the slice entry for `flows_index.json`. Build each sub-array with
   `np.column_stack([lon, lat, np.zeros(N)])` (positions) / `flow.astype(np.float32)` and
   concatenate `.ravel()`'d views so the on-disk offsets are exactly `[0, 3N, 6N, 7N, 8N)`.
7. **Index finalize.** Compute `global_flow_log_domain` = `log10(flow+1)` min/max across ALL
   slices; write `flows_index.json`.

### 3.3 Determinism + parity
- **Tie-break** `nlargest` deterministically (sort by `flow` desc, then `(origen,destino)`) so
  re-runs are byte-stable and the harness can diff.
- **`_AM` super-zones** (D3): default v1 keeps them (consistent with current `arcs.json`); the
  index flags `includes_am_zones` so a future filter can drop them.
- **PROJ warning** ("Open of .../share/proj failed") is cosmetic — the geojson is already CRS84,
  centroids return correct lon/lat. Do not treat it as a failure.
- A tiny pytest (`tests/test_od_flows_export.py`, §7) asserts file sizes, `N`, the geom/hours
  index alignment, `Σ hours ≈ flow`, and that `hexes.json` mtime/bytes are unchanged.

---

## 4. APPLET STRUCTURE + DECK.GL WIRING

### 4.1 Files + vendoring
```
docs/explore_flows.html            # new page; left panel (levers + semaphore) + right deck/MapLibre map
docs/app/flows/flows-app.js        # orchestrator (the FlowsApp class; window.fb for the harness)
docs/app/flows/flows-data.js       # fetch + Float32Array slicing of geom/hours .bin; index load
docs/app/flows/flows-layers.js     # ArcLayer (binary attributes) + DataFilterExtension wiring
docs/app/flows/flows-semaphore.js  # the vitality governor (count -> green/amber/red + auto-cap)
docs/app/flows/flows-deeplink.js   # lz-string encode/decode of lever state
docs/app/flows.css                 # panel chrome (reuse explore.css tokens; dark UI)
docs/vendor/deck/9.3/             # VENDORED per-subpath ESM (NOT the aggregate deck.gl entry):
  core/...                          #   @deck.gl/core
  layers/...                        #   @deck.gl/layers   (ArcLayer)
  extensions/...                    #   @deck.gl/extensions (DataFilterExtension)
  mapbox/...                        #   @deck.gl/mapbox   (only if MapboxOverlay is ever needed)
docs/vendor/luma/9.x/...           # VENDORED @luma.gl/* (transitive dep of @deck.gl/core)
docs/vendor/mathgl/...             # VENDORED @math.gl/* (transitive)
docs/vendor/loaders/...            # VENDORED @loaders.gl/* (transitive)
docs/vendor/gl-matrix/...          # VENDORED gl-matrix (transitive)
docs/vendor/h3-js/4.1/h3-js.umd.js # vendored h3-js (UMD; loaded first as a classic script)
docs/vendor/lz-string/lz-string.min.js
docs/vendor/maplibre/4.7/...       # OPEN (D5): vendor MapLibre too, or keep the existing unpkg pin
```

**Vendoring plan (no build step) — vendor the SUBPATH packages, NOT the aggregate entry:**

> ⚠️ **Do NOT vendor `deck.gl@9.3.2/dist/index.js`.** The aggregate `deck.gl` entry
> `export ... from '@deck.gl/react'`, which pulls **React** — a hard violation of the
> keyless/no-build/**no-React** lock — AND it does **not** export `MapboxOverlay` or
> `DataFilterExtension` (those live in `@deck.gl/mapbox` / `@deck.gl/extensions`, not the
> aggregate). The earlier draft's claim that the aggregate entry re-exports extensions+mapbox
> was wrong. We vendor the individual `@deck.gl/*` subpath packages directly instead.

1. **Resolve the subpath ESM packages, not the aggregate.** Vendor exactly these `@deck.gl/*`
   subpaths (each its own ESM module entry), which together give `ArcLayer` +
   `DataFilterExtension` and nothing React-shaped:
   - `@deck.gl/core` — the `Deck` class, `ArcLayer`'s base, the controller.
   - `@deck.gl/layers` — `ArcLayer`.
   - `@deck.gl/extensions` — `DataFilterExtension`.
   - `@deck.gl/mapbox` — `MapboxOverlay` (vendor ONLY if an overlay path is ever revived; the
     v1 camera model uses `deck.Deck({controller:true})`, §4.1.2, so `@deck.gl/mapbox` is
     **not required for v1** — `Deck` lives in `@deck.gl/core`).
2. **Resolve the FULL transitive graph by hand.** The `@deck.gl/*` ESM modules `import` their
   deps as **bare specifiers**: `@luma.gl/core`, `@luma.gl/engine`, `@luma.gl/shadertools`,
   `@luma.gl/webgl`, `@luma.gl/constants`; `@math.gl/core`, `@math.gl/web-mercator`,
   `@math.gl/polygon`, `@math.gl/sun`; `@loaders.gl/core`, `@loaders.gl/images`,
   `@loaders.gl/schema`; and `gl-matrix`. Every one of these (and anything THEY import) must be
   fetched, copied under `docs/vendor/`, and given its own import-map entry. There is no single
   bundle — this is a hand-authored, fully-resolved module graph. **This is the HIGH-RISK part
   of Phase 1.**
3. **Hand-authored import-map page.** `explore_flows.html` carries one
   `<script type="importmap">` with **one entry per bare specifier** — every `@deck.gl/*`,
   `@luma.gl/*`, `@math.gl/*`, `@loaders.gl/*`, and `gl-matrix` import resolved to its
   same-origin vendored path under `docs/vendor/` — then `<script type="module"
   src="app/flows/flows-app.js">`. A specifier that is left unmapped will fall through to a
   network resolution attempt: **NO `esm.sh`/`unpkg`/any third-party CDN may appear in the
   resolved graph at runtime.** (See the Phase-1 zero-CDN assertion below.)
4. **h3-js load order preserved.** Even though this page does not use H3HexagonLayer, deck.gl's
   bundled H3 shim caches `globalThis.h3` at eval time. Load vendored `h3-js.umd.js` as a
   **classic `<script>` BEFORE** the module script so the global exists when deck evaluates.
   (Defensive: mirrors the live shell's documented load-order trap.)
5. **MapLibre 4.7** is the passive basemap host driven by the `deck.Deck` controller
   (§4.1.2 — NOT `MapboxOverlay`); whether MapLibre itself is vendored or kept on its existing
   unpkg pin is D5. Basemaps registry (satellite default, CARTO dark/positron,
   OpenFreeMap+Voyager fallback) is copied from the live shell to match the
   dark-UI-over-satellite preference.

**Phase-1 zero-CDN harness assertion (binding).** The vendoring-spike smoke must assert that
loading `explore_flows.html` makes **ZERO network requests to `esm.sh`, `unpkg.com`, `cdn.jsdelivr.net`, or any third-party CDN** for app/renderer code — e.g. fail the run if any
request URL outside the page's own origin (basemap tile/style hosts excepted, since those are
keyless-remote by design) is observed during load + first draw. This is what proves the
import-map actually resolved the whole `@deck.gl/* + @luma.gl/* + @math.gl/* + @loaders.gl/* +
gl-matrix` graph to same-origin vendored files rather than silently falling through to a CDN.

### 4.1.2 Map + overlay model
MapLibre is a **passive basemap host** (`interactive:false`, `attributionControl:false`),
identical to `geobrowser-map.js` L479–503. **deck.gl owns the camera via a standalone
`deck.Deck({ controller:true })` instance** on its OWN sibling canvas (z-index above the
MapLibre container), and syncs the passive map with `onViewStateChange → map.jumpTo(...)`.
This is the PROVEN pattern from `geobrowser-map.js` `_buildDeck` (~L549) — the live shell
runs exactly this and the harness already exercises it. Do **NOT** use `MapboxOverlay`: with
MapLibre `interactive:false` an overlay-driven map is frozen (no pan/zoom), because the
overlay relies on MapLibre's own (disabled) input handlers to drive the camera. The
`deck.Deck` controller owns input directly, so the map stays fully pannable while MapLibre
stays passive.
```js
const deckInstance = new deck.Deck({
  canvas: deckCanvas,            // a sibling <canvas> above the MapLibre container
  width: '100%', height: '100%',
  initialViewState: state.viewState,
  controller: true,             // deck owns pan/zoom/pitch/bearing
  onViewStateChange: ({ viewState }) => {
    state.viewState = viewState;
    map.jumpTo({                // MapLibre follows; it never drives
      center: [viewState.longitude, viewState.latitude],
      zoom: viewState.zoom, bearing: viewState.bearing, pitch: viewState.pitch
    });
  },
  getTooltip: (info) => tooltip(info),
  layers: []
});
// re-render on any lever change (no MapboxOverlay; set props on the Deck instance):
deckInstance.setProps({ layers: buildLayers(state) });
```
The deck canvas is a sibling ABOVE MapLibre (separate canvas, not interleaved), so basemap
`setStyle` swaps never disturb arcs — same invariant the live shell relies on (`_applyStyle`
L520–539). MapLibre's `interactive:false` means it only ever follows `jumpTo`; deck.gl is the
sole camera authority. This keeps the OD applet camera identical to the live geo-browser and
makes the existing harness camera assertions directly reusable.

### 4.2 The ArcLayer (binary attributes, zero-copy)
One stock `deck.ArcLayer` fed binary `data.attributes` (the verified zero-copy recipe). Keys are
**accessor names**; positions are **`size:3`** to match how ArcLayer registers
`instanceSourcePositions` / `instanceTargetPositions` in deck.gl 9.3 (a `size:2` view would
leave `z` unset and render arcs at `[0,0]`). The four geom sub-arrays (§2.3) are sub-viewed
from the one `.bin` and passed as SEPARATE contiguous buffers — no `offset`/`stride`:
```js
const buf  = new Float32Array(geomBuffer);   // srcPos[3N] | dstPos[3N] | flow[N] | rank[N]
const N    = buf.length / 8;
const src  = buf.subarray(0,      3 * N);     // [lon,lat,0] x N
const dst  = buf.subarray(3 * N,  6 * N);     // [lon,lat,0] x N
const flow = buf.subarray(6 * N,  7 * N);     // [flow] x N   (reserved direct width source)
// rank = buf.subarray(7*N, 8*N) — reserved for a future rank filter (not wired in v1)
const arcLayer = new deck.ArcLayer({
  id: 'od-arcs',
  data: {
    length: N,
    attributes: {
      // separate same-origin buffers; size matches the accessor, z is prefilled 0
      getSourcePosition: { value: src, size: 3 },
      getTargetPosition: { value: dst, size: 3 },
      getWidth:          { value: widthsF32 /* derived from flow_by_hour[activeHour] */, size: 1 }
    }
  },
  // sidecar JS arrays read by {index} (hours buffer + rank):
  getFilterValue: (_, { index }) => hours[index * 24 + activeHour],
  getSourceColor: [0, 220, 255, ARC_ALPHA],   // cyan
  getTargetColor: [255, 0, 200, ARC_ALPHA],   // magenta
  greatCircle: true, getHeight: 0.4, widthUnits: 'pixels',
  pickable: true,
  extensions: [filterExt],
  filterRange: [threshold, Infinity],
  filterEnabled: true,
  updateTriggers: { getWidth: activeHour, getFilterValue: activeHour },
  onFilteredItemsChange: ({ count }) => semaphore.update(count, N),
  // deck.gl DEFAULT alpha-over blending — DO NOT set additive parameters.
});
```
Notes that are load-bearing:
- **Positions are Float32, `size:3` (xyz, z prefilled 0).** ArcLayer 9.3 registers
  `instanceSourcePositions`/`instanceTargetPositions` as `size:3`; a `size:2` interleaved view
  silently renders every arc at `[0,0]` (this was the blocking bug). The two position buffers
  are separate (no interleave), so each maps 1:1 onto its accessor with no offset/stride math.
  Supplying Float32 keeps the lean 32-bit path — keep `coordinateSystem` default lng/lat so
  `use64bitPositions()` never triggers the doubled fp64 attribute path.
- **Glow twin (optional, v1.1):** the live shell renders a wide low-alpha glow under a crisp
  arc. v1 may keep a single arc layer to keep the governor count clean (one primitive →
  exactly one `onFilteredItemsChange`). If a glow twin is added, the **governor reads the crisp
  layer only**; the glow is `pickable:false` and carries no `countItems`.

### 4.3 Hour scrubber — VERIFIED stock mechanism
Hour is NOT 24 resident slices and NOT a pure GPU uniform (stock ArcLayer has no accessor that
re-indexes a 24-wide attribute by a uniform). The verified idiomatic path is a per-hour CPU
re-eval driven by `updateTriggers`:
- **(A) Width re-weight (primary visual):** on scrub, recompute `getWidth` from
  `flow_by_hour[index*24 + activeHour]` and bump `updateTriggers.getWidth = activeHour`. deck.gl
  re-evaluates `getWidth` over ~8–10k arcs on the CPU (sub-ms) and re-uploads ONLY the 1-float
  `instanceWidths` (~40 KB). In practice v1 precomputes `widthsF32` for the active hour in JS and
  hands it as a binary attribute, then re-derives it on scrub — either path re-uploads one tiny
  attribute, no geometry.
- **(B) Filter/threshold (feeds the semaphore + cap):** `getFilterValue = hours[index*24 +
  activeHour]` with `updateTriggers.getFilterValue = activeHour`; `filterRange = [threshold, ∞]`.
  The per-arc VALUE re-evals on scrub; the THRESHOLD is a live uniform.
- **Cost:** one accessor pass over ~10k arcs + one tiny attribute re-upload per scrub. NOT the
  24× memory/eval blowup of precomputed sub-slices; no custom GLSL → inside the stock lock.

### 4.4 Threshold + top-N via DataFilterExtension
```js
const filterExt = new deck.DataFilterExtension({ filterSize: 1, countItems: true });
```
- `filterSize:1` (single flow-at-active-hour threshold). `fp64:false` (flow + hour are small
  32-bit-safe integers; fp64 costs an extra attribute slot and risks the ~16-slot budget).
- `filterRange[0] = threshold` is a **live uniform** — slide it at zero re-upload cost. This is
  the volume threshold lever AND the auto-cap actuator (§5).
- `filterSoftRange` (optional) fades arcs in/out near the threshold so slider moves don't pop.
- **Attribute-slot budget:** ArcLayer uses positions(2)+colors(2)+widths+heights+tilts;
  DataFilter `filterSize:1` adds 1 — well within ~16. The other 3 numeric + 1 category dims are
  RESERVED for future season/daytype/rank/distance filters (do not wire in v1).

### 4.5 Alpha-over + data-driven width
- **Blending:** deck.gl default (`blend:true`, src-alpha / one-minus-src-alpha). NEVER additive.
  Per-arc alpha is low (`ARC_ALPHA` ≈ 150–180); density reads via more corridors + width +
  threshold + cap, not pixel accumulation. This is the locked anti-hairball decision.
- **Width:** map `log10(flowAtHour + 1)` linearly into ~1.5–8 px (mirrors `_arcWidth`
  L797–804), seeded from `flows_index.global_flow_log_domain` so width is comparable across
  slices and never silently re-scales on slice switch. `updateTriggers.getWidth` carries
  `activeHour` (and the log domain). A harness assertion re-asserts a visible thin→thick spread
  (the OB1 magnitude-coupling regression gate).

---

## 5. VITALITY SEMAPHORE (the governor)

A live "**N of M corridors**" governor that keeps the on-screen arc set inside a safe budget,
pre-empting the Barcelona hairball. It is the single consumer of the GPU-side filter count.

### 5.1 Inputs
- **Primary — visible-arc count `N`:** from `DataFilterExtension.countItems:true` →
  `onFilteredItemsChange({count})`. `M` = total arcs in the active slice (`index.n`). This is
  the only source of truth for the governor; **do not count on the CPU.**
- **Secondary (advisory, best-effort):** `performance.memory.usedJSHeapSize` where available
  (Chromium only; guarded), and a rolling FPS estimate from `requestAnimationFrame` deltas.
  These shade the readout and can tighten the cap on weak devices, but never override the count.

### 5.2 Thresholds (defaults; tunable in `flows-semaphore.js`)
Let `CAP` = the top-N auto-cap target (default 2500 simultaneously-drawn corridors — the count
at which alpha-over over Barcelona stays legible at Catalonia-wide zoom).

| state | condition                         | meaning + action                                   |
| ----- | --------------------------------- | -------------------------------------------------- |
| green | `N ≤ CAP`                         | healthy; render as-is                              |
| amber | `CAP < N ≤ 1.6×CAP`               | busy; show warning, offer "tighten" (raise threshold) |
| red   | `N > 1.6×CAP` OR FPS < ~24 sustained | hairball risk; **auto-cap engages** (see 5.3)     |

(FPS/memory only *promote* toward red on weak devices; they never demote red→green.)

### 5.3 Top-N auto-cap behaviour
The cap is enforced by **raising the threshold uniform**, never by mutating geometry:
- When `N > CAP`, increment `filterRange[0]` (a pure uniform change, **no attribute re-upload**)
  and let the next `onFilteredItemsChange` report the new `N`; iterate (bisect on the threshold
  between the current value and the slice's `flow_max`) until `N ≤ CAP`. Typically 2–4 uniform
  nudges settle.
- The user's manual threshold slider sets a FLOOR; auto-cap only raises above it. A small
  "auto-capped at flow ≥ X trips/day" note shows when the governor is holding the floor.
- Because the cap is a uniform slide, it is frame-cheap and reversible (zoom in / pick a quieter
  slice → `N` drops → threshold relaxes back toward the user floor).

### 5.4 Pre-empting heavy combos
- **Debounce the readback.** `countItems` triggers a GPU→CPU sync; recompute on scrub/slider
  **settle** (≈120 ms debounce), not on every intermediate drag pixel, to avoid pipeline stalls.
- **Predict before draw.** On a *slice switch*, the index already carries `n` and `flow_max`; the
  governor can seed an initial threshold from the slice's flow distribution so the first frame of
  a known-heavy slice (e.g. `may_weekday`, dense BCN commute) opens already near the cap rather
  than drawing the full hairball then clawing back.
- **Readout.** A compact chip: `🟢 1,840 of 8,000 corridors` / `🟡 3,100 of 8,000 — tighten?` /
  `🔴 auto-capped → 2,480 of 8,000 (flow ≥ 410/day)`. The chip is also a live caption for the
  current hour + slice.

---

## 6. DEFAULT VIEWS + LEVERS + DEEP-LINK

### 6.1 Four default views (curated entry states)
1. **Catalonia-wide commute (default load):** `may_weekday`, hour = 08, threshold seeded to
   green, satellite basemap, Catalonia-wide camera. The canonical AM-peak corridor map.
2. **Barcelona hairball-tamed:** `may_weekday`, hour = 08, camera over BCN metro, governor in
   amber→auto-capped — the showcase that the cap keeps it legible.
3. **Weekend leisure:** `jun_weekend`, hour = 12, dark basemap — coastal/leisure corridors stand
   out against commute.
4. **Night network:** `may_weekday`, hour = 23, dark basemap, low threshold — the sparse
   late-night skeleton.

Each view = a preset lever bundle → also a shareable deep-link.

### 6.2 Levers (left panel controls)
| lever            | control            | wiring                                                            |
| ---------------- | ------------------ | ---------------------------------------------------------------- |
| Month-window     | `<select>` 3 opts  | structural → fetch new slice (geom eager, hours lazy), re-seed governor |
| Day-type         | `<select>` 2 opts  | structural → fetch new slice                                     |
| Hour scrubber    | `<input range>` 0–23 | `updateTriggers` re-eval of getWidth/getFilterValue (§4.3); debounced |
| Hour animate     | play/pause button  | rAF loop stepping the scrubber; pauses readback-heavy count to settle frames |
| Volume threshold | `<input range>`    | `filterRange[0]` uniform (live); sets the auto-cap floor          |
| Top-N cap target | `<input range>`    | sets `CAP` for the governor (advanced; default hidden behind a details disclosure) |
| Basemap          | `<select>`         | satellite / dark / light / osm (copied from live shell)          |

Structural changes (window/day-type) are the only ones that re-fetch; everything else is a
uniform slide or a one-attribute re-eval.

### 6.3 Deep-link state (lz-string)
A compact, URL-safe state string via vendored `lz-string`
(`compressToEncodedURIComponent` / `decompressFromEncodedURIComponent`), carried as `?s=`:
```json
{
  "v": 1,
  "w": "may", "d": "weekday",        // window, daytype
  "h": 8,                            // active hour 0..23
  "t": 410,                          // threshold floor (trips/day)
  "cap": 2500,                       // top-N cap target
  "bm": "satellite",                 // basemap key
  "cam": [2.17, 41.39, 8.2, 0, 0]    // lon, lat, zoom, pitch, bearing
}
```
- Encode on every settled lever change (debounced), `history.replaceState` so back-button isn't
  spammed. Decode on load; missing keys fall back to default view #1.
- The four default views are emittable as canned `?s=` links (a "copy link" button per view).
- **Harness reuse:** the runner navigates `explore_flows.html?s=<...>` exactly like the existing
  `goto` step navigates `explore.html?<query>` — the deep-link IS the test fixture.

---

## 7. TESTING — new harness slice

Extend the existing `tests/ui` harness (no new framework). The flows applet exposes
**`window.fb`** (the `FlowsApp` instance) the way `explore.html` exposes `window.gb`, so the
same `readState`/`evalExpect` pattern applies.

### 7.1 Runner additions
- Teach `run-slice.mjs` (or a sibling `run-flows-slice.mjs`) to open `explore_flows.html` and to
  read `window.fb`. Add `dataFilesForStep` entries for the flow `.bin` URLs
  (`geom/*.bin` on slice select; `hours/*.hours.bin` on first scrub/animate) so the response
  barrier blocks on the bytes before asserting a draw — same race-closing idiom as `arcs.json`.
- New `_expect_grammar` keys (live-state assertions, so an adversarial reviewer trusts PASS
  without eyeballing pixels):
  - `sliceKey` → `fb._sliceKey === "<window>_<daytype>"`
  - `activeHour` → `fb._activeHour === h`
  - `arcCountTotal` (M) and `visibleCount` (N) → from `fb._semaphore` / loaded geom length
  - `semaphoreState` → `"green"|"amber"|"red"`
  - `thresholdAtLeast` → `fb._filterRange[0] >= x` (proves auto-cap raised it)
  - `deckLayerIds` includes `od-arcs`
  - `blendModeAlphaOver` → asserts the ArcLayer params are deck default (NOT additive) — the
    locked anti-hairball gate
  - `widthSpread` ≥ 2.0 px over the loaded arcs — the magnitude-coupling regression gate
  - `noConsoleErrors`, `canvasRendered` (reuse existing)

### 7.2 `tests/ui/tests.json` — new slice `od-flows`
Representative tests (each = goto deep-link → optional lever steps → screenshot → expect):
1. **FLOW-001 default load** — `?s=` for view #1: `sliceKey=may_weekday`, `activeHour=8`,
   `od-arcs` present, `semaphoreState=green`, `blendModeAlphaOver`, `canvasRendered`, no errors.
2. **FLOW-002 hour scrub** — scrub to hour 23: `activeHour=23`, width re-evaluated (widthSpread
   holds), `visibleCount` changes, no geometry re-fetch (geom URL requested once), no errors.
3. **FLOW-003 slice switch** — select `jun_weekend`: new geom fetched, `sliceKey` updates,
   endpoints byte-stable for a known shared pair (geometry-identity check), no errors.
4. **FLOW-004 threshold slide** — raise threshold: `visibleCount` drops, `filterRange[0]` rises,
   zero attribute re-upload (assert via a render-count probe), no errors.
5. **FLOW-005 hairball governor** — view #2 over BCN, low threshold: `semaphoreState` ∈
   {amber,red}, auto-cap engages → `thresholdAtLeast` raised → `visibleCount ≤ CAP`, no errors.
6. **FLOW-006 lazy hours sidecar** — confirm `hours.bin` is NOT fetched until first scrub, THEN
   is fetched exactly once and cached, no errors.
7. **FLOW-007 deep-link round-trip** — encode current state, reload with the emitted `?s=`,
   assert identical `sliceKey/activeHour/threshold/basemap/cam`.

### 7.3 Python export test — `tests/test_od_flows_export.py`
Asserts (on a small fixture or a dry-run flag): geom `.bin` length `= N*8*4` bytes (3+3+1+1
floats/arc — two `size:3` position blocks + flow + rank); hours `.bin` `= N*24*4`; geom/hours
arc-order alignment; that each position block's every-third float (the `z` column) is exactly
0; `Σ flow_by_hour ≈ flow` within rounding; per-slice
`day_count` matches the bucket; and that running the export leaves `hexes.json` byte-identical
(mtime + sha guard).

---

## 8. BUILD PHASES (ordered, each independently shippable + harness-verifiable)

(Returned also as `build_phases`.)

1. **Vendoring spike — HIGH-RISK (full transitive-graph resolution + hand-authored
   import-map).** Resolve + commit the per-subpath `@deck.gl/core`, `@deck.gl/layers`,
   `@deck.gl/extensions` (and `@deck.gl/mapbox` only if ever needed) ESM **plus their entire
   transitive `@luma.gl/* + @math.gl/* + @loaders.gl/* + gl-matrix` graph** under
   `docs/vendor/`, with one import-map entry per bare specifier (§4.1). Do NOT vendor the
   aggregate `deck.gl` entry (pulls React; lacks MapboxOverlay/DataFilterExtension). A 1-file
   `explore_flows.html` that import-maps the vendored modules, instantiates MapLibre 4.7 as the
   passive `interactive:false` host + a `deck.Deck({controller:true, onViewStateChange→
   map.jumpTo})` camera (§4.1.2, the proven `geobrowser-map.js` pattern), and draws a hardcoded
   3-arc `ArcLayer`. Ships when the dual-context / shader-error / unresolved-specifier failure
   modes are ruled out and the map draws AND is pannable. Harness: a trivial `od-flows` smoke
   (map non-blank, pannable, `od-arcs` present, no console errors) **+ the binding zero-CDN
   assertion (no request to esm.sh / unpkg / any third-party CDN for app/renderer code during
   load + first draw).**
2. **Precompute export** — `scripts/export_od_flows.py` writes the 6 geom + 6 hours `.bin` and
   `flows_index.json`; `tests/test_od_flows_export.py` green; `hexes.json` proven untouched.
3. **Binary-attribute render** — load one real slice from `.bin` via binary `data.attributes`,
   data-driven width seeded from the index domain, alpha-over. Harness: FLOW-001 + widthSpread +
   blend gate.
4. **Hour scrubber** — lazy `hours.bin`, `updateTriggers` re-eval (A)+(B), debounced. Harness:
   FLOW-002, FLOW-006.
5. **Threshold + DataFilterExtension** — live `filterRange` uniform, soft-range fade. Harness:
   FLOW-004.
6. **Vitality semaphore** — `countItems`+`onFilteredItemsChange`, green/amber/red, top-N
   auto-cap, "N of M" chip. Harness: FLOW-005.
7. **Levers + slice switching** — month-window / day-type selects, basemap, geometry-identity on
   switch, governor re-seed. Harness: FLOW-003.
8. **Deep-link + default views** — lz-string `?s=` encode/decode, four canned views, copy-link.
   Harness: FLOW-007.
9. **Polish + a11y + caption honesty** — "month-window, not climate" caption, attribution,
   keyboard scrubber, mobile panel. Optional glow twin (governor reads crisp layer only).
10. **(PARALLEL, off critical path) `hexes.json` LFS/sidecar split** — independent cleanup;
    must leave `hexes.json` byte-identical for `explore.html` until the swap lands.

---

## 9. OPEN DECISIONS (need user sign-off)

(Returned also as `open_decisions`.)

- **D1 — Grid size: 6 vs 8 slices.** Disk has only Feb/May/Jun → honest grid is 3×2=6. Confirm
  v1 ships 6 (relabel "month-window"), or whether a 4th window / full year is being ingested
  before sizing. Recommendation: ship 6, index-extensible.
- **D2 — Distance band.** Omit (v1 default, leaner) or carry as a `categorySize:1` filter dim?
  Recommendation: omit in v1; reserve the slot.
- **D3 — `_AM` super-zones.** Keep (consistent with current `arcs.json`; coarse super-zone hops)
  or filter out for cleaner distrito-to-distrito corridors? Recommendation: keep + flag in index.
- **D4 — Width domain scope.** Global-across-slices (comparable, recommended) vs per-slice
  rescale toggle vs both. Recommendation: global default + optional per-slice toggle.
- **D5 — Vendor MapLibre too?** The lock vendors the deck.gl/luma *renderer*; MapLibre is the
  basemap host. Vendor MapLibre 4.7 for full no-CDN, or keep its existing unpkg pin (lighter
  diff)? Recommendation: vendor it too for a clean "no runtime CDN for app code" story; basemap
  TILE/style fetches remain remote regardless (keyless).
- **D6 — N (top-N per slice) = 8000?** The estimate recommends 8000 (~6.0 MB total, headroom for
  the governor to draw from). Confirm, or pick 5000 (smaller) / 10000 (richer tail).
- **D7 — git-lfs is NOT installed** in this environment (`git lfs version` → not found). The
  hexes.json split (D-parallel) needs either git-lfs installed or the sidecar-not-LFS route.
  Confirm the preferred path before Phase 10. (Does not block Phases 1–9.)
- **D8 — `window.fb` global name + harness file split.** Confirm exposing `window.fb` and adding
  `run-flows-slice.mjs` vs folding flows tests into the existing `run-slice.mjs`.
```
