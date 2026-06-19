# Why Spark + Apache Sedona earns its keep

> Status: the **MITMA deep-Spark mobility layers** (rhythm, weekend hotspots,
> typology, geodemographics, OD arcs) are produced by a **real distributed
> Sedona pipeline** — the canonical `src/catmob/pipeline_{silver,gold}.py`
> path, driven through one reusable session (`src/catmob/spark.py:get_sedona`)
> — not by pandas. This note records the operations where Sedona/Spark is
> load-bearing, **reads its numbers straight from the shipped gold**
> (`data/gold/mitma_features/zoning=distritos/` — 46,121 hexes × 35 columns),
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
   comes from standardising an interpretable per-zone vector
   (`TYPOLOGY_FEATURES` = `intra_zone_share`, `leisure_share`, `commute_share`,
   `long_trip_share`) with `StandardScaler`, then **`KMeans` (k-means‖)** with
   `k=5` (`pipeline_gold.py`). KMeans is what ships because BisectingKMeans on
   this scaled vector intermittently collapsed to a single cluster. `sink_source`
   = `log(outflow/inflow)` is deliberately *not* a cluster axis — on this
   daily-distrito sample it spans only ≈ −0.075…0.033, too narrow to support an
   `employment-sink`/`commuter-dormitory` split — so it is carried as a
   descriptive column only. Labels are assigned from the **actual cluster
   centroids** (strongest |z|-score pole, `|z| ≥ 0.5` gate, else `mixed-balanced`).
   The shipped gold's 5 KMeans clusters resolve to **4 distinct, data-driven
   typologies**: `mixed-balanced` (21,846 hexes; two centroids land near the
   origin), `transit-corridor` (11,410; `long_trip` z ≈ +1.14), `commuter-corridor`
   (9,406; `commute` z ≈ +1.34), `self-contained` (3,459; `intra_zone` z ≈ +2.03).
   `leisure-magnet` is a legitimately *unassigned* pole — no cluster has leisure
   as its strongest axis — which is the honest, non-fabricated outcome, not a
   missing label.

6. **`ST_Transform` to EPSG:25831 metric reprojection** — every area weight
   depends on it; doing the area math in a geographic CRS, or assuming 25831 on
   the raw 25830 source geometry, corrupts all weights.

## R-tree / `BroadcastIndexJoin` status — PROVEN (with a parquet caveat)

The crosswalk's `ST_Intersects` join runs as a Sedona `BroadcastIndexJoin` that
builds an R-tree on the broadcast (zone) side. **This is now proven on this
env**, and the precise blocker that previously defeated it is understood.

**Root cause (diagnosed):** on this build (Spark 4.1.1 +
`sedona-spark-shaded-4.0_2.13:1.9.0`) there are **two copies of `jts-core` on two
class loaders** — the shaded Sedona jar bundles JTS *un-relocated* (at
`org.locationtech.jts`) on the `--packages` `MutableURLClassLoader`, while
pyspark *also* ships `jts-core-1.20.0.jar` on the `app` loader. The indexed
`BroadcastIndexJoin` serialises a JTS R-tree via Sedona's `IndexSerde` (shaded
loader), which then calls the *package-private*
`AbstractSTRtree.getItemBoundables()` on an instance from the *other* loader →
`IllegalAccessError` during the broadcast spatial-index serialize.

**Fix (proven):** park pyspark's duplicate `jts-core` jar *before the JVM
launches* so the shaded jar's JTS is the only copy on the classpath
(`catmob.spark._isolate_shaded_jts`, enabled by `get_sedona(enable_rtree=True)`,
which also flips `sedona.join.optimizationmode="all"`; an `atexit` hook restores
the jar). With this, the load-bearing crosswalk join executes with the plan
markers `BroadcastIndexJoin` + `SpatialIndex … RTREE` and produces the same
**exact closure** (`area_weight` sum/zone = 1.0000; 58,656 rows / 584 zones /
46,121 hexes).

Dead ends ruled out: pinning the pyspark jar onto
`spark.{driver,executor}.extraClassPath` keeps two loaders (still fails);
`spark.*.userClassPathFirst=true` breaks Sedona init with an slf4j
`LinkageError`; the *unshaded* `sedona-spark-4.0` artifact additionally surfaces
a `FoldableUnevaluable` `ClassNotFoundException` (the Spark-4.0-vs-4.1 API skew).

**Caveat — why the integrated pipeline still defaults to the range join:**
parking pyspark's `jts-core` to enable the R-tree breaks the **parquet read
codegen** in the same JVM (`org.apache.parquet.schema.PrimitiveStringifier`
fails to initialise). The crosswalk builds its geometry from the **GeoJSON**
zones, so the R-tree path is proven there; but `run_mitma_pipeline.py` and
`build_mitma_layers.py` must *scan the bronze parquet lakehouse*, which is
incompatible with the jts-isolation in one JVM. They therefore default to the
**correct non-indexed range join** (`optimizationmode="none"`) — fast at sample
scale (584-zone broadcast side, computed once) with exact closure — and expose
`--rtree` / `enable_rtree=True` for the geometry-only, parquet-free path. On
atlas the clean resolution is a Spark/Sedona pair whose shaded jar **relocates**
JTS (no app-loader collision), which removes both the `IllegalAccessError` and
the parquet conflict at once.

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
look* (which hexes are self-contained, leisure magnets or transit corridors;
where weekend rhythm diverges from weekday), not a calibrated measurement.
Min-rows gates (`support_n` = the area-weighted **OD-segment row count**, a
coarse density/confidence proxy) and NA-as-its-own-segment handling keep the
indices honest; cross-zoning ratios are invalid (different expansion bases) and
are never computed.

**Risk — the privacy floor is invisible.** `support_n` is *not* the MITMA
<100-device privacy gate. That suppression is applied by MITMA **before**
publication, so the floor cannot be observed or reconstructed in the expanded
open data; `support_n` only counts OD-segment rows behind each aggregate. Named
geodemographic shares (`female_share`, `youth/senior_mobility_share`,
`low_income_inflow_share`) are divided over the **known subset** for each
variable (NA excluded from the denominator) and ship `*_of_all_trips` companions
plus `*_coverage` NA-fractions so the unlabelled mass is explicit.
`geodemo_diversity` is Shannon entropy in **bits** (log base 2).

## Full-scale staging (atlas)

- **STAGE 0 (env hardening):** the R-tree `BroadcastIndexJoin` is already proven
  on this env via `get_sedona(enable_rtree=True)` (parks pyspark's duplicate
  `jts-core`). The remaining atlas hardening is a Spark/Sedona pair whose shaded
  jar **relocates** JTS, so the R-tree and the parquet scan coexist in one JVM
  (no jts-isolation needed); pre-stage the Maven jars so a cold start has no
  Maven Central dependency.
- **STAGE 1–4:** sized incremental read by `zoning × kind × date-range` tranche →
  bronze; build the `zone_h3_xwalk` table once per zoning → silver; run the five
  gold themes **per zoning** (separate gold partitions — different expansion
  bases, cross-zoning ratios invalid).
- **Guardrails:** never re-expand `viajes` (already population-expanded); apply
  `support_n` min-ROWS gates as a density/confidence proxy (the <100-device
  privacy floor is invisible in expanded open data — do not claim to honour it);
  keep NA `edad/sexo/renta` as their own segment but divide named shares over the
  known subset; size partitions to executors (a naive single-machine all-Spain
  hourly run will OOM — atlas with proper executors will not).

## Keyless / static contract preserved

Every new layer ships as additional JSON under `docs/story_data/`: new scalar
columns merged into `hexes.json`, the heavy 24-float profiles in a lazy
`rhythm.json` sibling (a hover sparkline, keeping `hexes.json` from bloating with
arrays), and the Sedona-built `arcs.json` (250 arcs). No API keys, no build
step — the deck.gl geo-browser reads them statically, exactly as before.
