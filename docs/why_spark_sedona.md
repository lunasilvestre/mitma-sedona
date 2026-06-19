# Why Spark + Apache Sedona earns its keep

> Status: the **MITMA deep-Spark mobility layers** (rhythm, weekend hotspots,
> typology, geodemographics, OD arcs) are produced by a **real distributed
> Sedona pipeline** — the canonical `src/catmob/pipeline_{silver,gold}.py`
> path, driven through one reusable session (`src/catmob/spark.py:get_sedona`)
> — not by pandas. This note records the operations where Sedona/Spark is
> load-bearing, **reads its numbers straight from the shipped gold**
> (`data/gold/mitma_features/zoning=distritos/` — 46,121 hexes × 29 columns),
> and states the R-tree / `BroadcastIndexJoin` status honestly.

## What is actually load-bearing (and what is NOT)

A claim repeated in earlier drafts — *"a broadcast R-tree spatial join of the
small zonings against billions of OD rows"* — is **wrong on two counts**, and
this note corrects it for good:

1. **The OD rows never participate in a spatial join.** OD is keyed by
   `origen`/`destino` zone ids (strings). It reaches the H3 grid only through a
   **cheap broadcast equi-join on `zone_id`** against a tiny pre-computed
   crosswalk (`pipeline_gold.py` — `zone.join(F.broadcast(xwalk_df), "zone_id")`).
   No geometry is touched at OD scale.

2. **The one spatial join is small and one-time.** It is the **crosswalk build**:
   the **584 Catalonia distrito polygons** (the small side, `BROADCAST`-hinted)
   intersected against the H3 res-8 grid — `ST_Intersects(hex, zone)` plus
   `ST_Intersection`/`ST_Area` in a metric CRS. Hundreds of zones against tens of
   thousands of hexes, computed **once** and persisted to
   `data/silver/zone_h3_xwalk/`. That is the irreducible spatial cost; everything
   downstream is equi-joins and window/cube aggregations.

So the honest one-liner is: **one small dasymetric area-weighted spatial
crosswalk, then a distributed window/cube aggregation over GB-scale OD.**

## The honest before/after

The shipped v2 gold attributed mobility flow to hexes with a **naive centroid
`gpd.sjoin(predicate="within")` join** (`scripts/run_gold_v2.py`): every hex
whose centroid fell inside a distrito received that **whole distrito's** flow —
a step function, not a field.

The canonical pipeline replaces this with an **area-weighted dasymetric
crosswalk** computed in Sedona, giving a genuinely continuous per-hex field.
Numbers read from the shipped gold:

| metric | old (centroid) | new (dasymetric, shipped) |
| --- | --- | --- |
| `mitma_inflow_daily` median | 86,645 | **40.27** |
| distinct inflow values | 569 | **46,121** (one per hex) |

The old median (86,645) was a *whole-distrito total* stamped onto every hex; the
new median (40.27) is a genuine *per-hex share*. This is a **correctness fix**,
not a regression — flagged here and in the PR because the published numbers move.
(The default published liveability score is byte-identical: the new mitma terms
ship at weight 0.)

## The load-bearing Sedona/Spark operations

1. **`ST_H3CellIDs` fullCover grid + `ST_H3ToGeom`** — the H3 res-8 hex polygons
   are materialised from their cell ids inside Sedona
   (`EXPLODE(ST_H3CellIDs(geom_ll, 8, true))` → `ST_H3ToGeom(ARRAY(h3_long))[0]`),
   not pre-baked (`pipeline_silver.py`).

2. **The dasymetric `ST_Intersection` / `ST_Area` crosswalk in EPSG:25831** — the
   irreducible spatial cost. The MITMA source CRS is **EPSG:25830 (ETRS89 UTM
   30N)**; geometry is normalised to lon/lat in the lakehouse, then **both hex and
   distrito polygons are reprojected to EPSG:25831 (UTM 31N, correct for
   Catalonia) with `ST_Transform` before any area math**, guarded by
   `ST_MakeValid` against MITMA self-touching slivers. Then
   `area_weight = ST_Area(ST_Intersection) / NULLIF(ST_Area(zone), 0)`.

   **Closure (read from the shipped `data/silver/zone_h3_xwalk/zoning=distritos`,
   58,656 rows): 100.0 % — all 584 distritos sum their hex `area_weight` to
   exactly 1.0000** (mean, median, min and max of `SUM(area_weight)` per zone all
   equal 1.0). pandas `gpd.sjoin` cannot produce this disaggregation.

3. **Distributed `zone_id` equi-join + window/cube aggregation** — every gold
   theme broadcasts the small crosswalk and equi-joins it onto per-zone flows on
   `zone_id`, then disaggregates by `area_weight`. Spark **Window** functions
   build the per-hex 24-hour rhythm profiles, `max_by(periodo, viajes)` does
   one-pass peak-hour detection, and entropy aggregations run over the
   income × age segment cube. This is the part that needs a distributed engine at
   scale — not because of geometry, but because of the `periodo × distancia ×
   actividad × renta × edad × sexo` cube.

4. **`ST_MakeLine(ST_Centroid(o), ST_Centroid(d))` OD-arc construction** — the OD
   arcs in `arcs.json` are **Sedona-generated** (centroid lines over zone-pair
   flows, endpoints via `ST_X`/`ST_Y` of `ST_Centroid`). The shipped artifact
   holds **250 arcs** (top zone-pair flows), feeding the deck.gl `ArcLayer`
   unchanged.

5. **Spark MLlib `KMeans` typology clustering** — the `mobility_typology` label
   comes from standardising an interpretable per-zone vector (sink/source =
   `log(outflow/inflow)`, intra-zone share, long-trip share, commute vs leisure
   share) with `StandardScaler`, then **`KMeans` (k-means‖)**
   (`pipeline_gold.py`). KMeans is what ships because BisectingKMeans on this
   scaled vector intermittently collapsed to a single cluster. The shipped gold
   carries **5 typologies**: `mixed-balanced` (15,418 hexes), `employment-sink`
   (11,298), `transit-corridor` (6,582), `commuter-dormitory` (6,422),
   `self-contained` (6,401).

6. **`ST_Transform` to EPSG:25831 metric reprojection** — every area weight
   depends on it; doing the area math in a geographic CRS, or assuming 25831 on
   the raw 25830 source geometry, corrupts all weights.

## R-tree / `BroadcastIndexJoin` status — deferred, not proven

The crosswalk's `ST_Intersects` join *would* normally run as a Sedona
`BroadcastIndexJoin` that builds an R-tree on the broadcast (zone) side. **On
this sample it does not, and we do not claim otherwise.** The reusable session
hard-sets `sedona.join.optimizationmode="none"` (`src/catmob/spark.py`):

> On this build (Spark 4.1.1 + `sedona-shaded-4.0`) the env ships two JTS copies
> on different class loaders. The indexed `BroadcastIndexJoin` serialises a JTS
> R-tree via Sedona's `IndexSerde` (shaded loader), which then calls the
> package-private `AbstractSTRtree.getItemBoundables()` on an instance from the
> *other* loader → `IllegalAccessError` during the broadcast spatial-index
> serialize.

With `optimizationmode="none"` Sedona falls back to a **correct non-indexed
range join**; because the spatial side is tiny (584 zones vs ~tens of thousands
of hexes, computed once) this is fast at sample scale and the closure is exact.
**Re-enabling the R-tree (`sedona.join.optimizationmode="all"`) and proving a
`BroadcastIndexJoin` actually runs is an atlas STAGE-0 task** (pin a matched
JTS / shade the serde), not something demonstrated here.

## Scale reality (corrected)

Earlier drafts oversold this as "TBs / tens of billions of rows". The corrected
reality:

- **The working set is GB-scale, not TB.** We read only OD that **touches
  Catalonia** (`origen` *or* `destino` in Catalonia) over a **representative time
  window**, partition-pruned by `zoning × fecha`. The shipped 7-day March-2024
  distritos sample is **27.7 M hourly OD rows** (`od_silver/zoning=distritos`,
  fechas 2024-03-04…10) — hundreds of millions of rows / GB at a full window, not
  the full all-Spain multi-year dump.
- **"OOM" is a single-JVM `local[*]` artifact**, not a property of the data. It
  comes from trying to materialise everything in one JVM; the filtered + windowed
  working set runs fine on **atlas with sized executors**. The pandas shortcut
  was fast *only* because it pre-collapsed the entire segment cube into three
  per-zone scalars before reading — which the rhythm / weekend / typology /
  geodemographic layers cannot do.

## Honest framing of the new layers

These are **relative indices and starting questions, not ground truth.**
`weekend_hotspot_score`, `mobility_typology`, `geodemo_diversity` and the rhythm
shares are *interpretable signals* over a 7-day sample — they tell you *where to
look* (which hexes behave like sinks, corridors or dormitories; where weekend
rhythm diverges from weekday), not a calibrated measurement. Min-support gates
(`support_n`, the <100-device privacy floor) and NA-as-its-own-segment handling
keep the indices honest; cross-zoning ratios are invalid (different expansion
bases) and are never computed.

## Full-scale staging (atlas)

- **STAGE 0 (env hardening):** pin a matched Spark/Sedona pair to kill the
  dual-JTS `IllegalAccessError`, then flip `sedona.join.optimizationmode` back to
  `"all"` and **prove** a `BroadcastIndexJoin` runs on the crosswalk; pre-stage
  the Maven jars so a cold start has no Maven Central dependency.
- **STAGE 1–4:** sized incremental read by `zoning × kind × date-range` tranche →
  bronze; build the `zone_h3_xwalk` table once per zoning → silver; run the five
  gold themes **per zoning** (separate gold partitions — different expansion
  bases, cross-zoning ratios invalid).
- **Guardrails:** never re-expand `viajes` (already population-expanded); honour
  the <100-device privacy floor via `support_n` min-support gates; keep NA
  `edad/sexo/renta` as their own segment; size partitions to executors (a naive
  single-machine all-Spain hourly run will OOM — atlas with proper executors will
  not).

## Keyless / static contract preserved

Every new layer ships as additional JSON under `docs/story_data/`: new scalar
columns merged into `hexes.json`, the heavy 24-float profiles in a lazy
`rhythm.json` sibling (a hover sparkline, keeping `hexes.json` from bloating with
arrays), and the Sedona-built `arcs.json` (250 arcs). No API keys, no build
step — the deck.gl geo-browser reads them statically, exactly as before.
