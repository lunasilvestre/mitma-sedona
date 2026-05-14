# Notes from the prototype run

**Date:** 2026-05-14 · **Scope:** dev (7 days × 2024-03-04..10) · **Author:** Claude (atlas)

This file captures the *surprises* hit while executing the M6 → M8 plan
(Prompt α from `PLAN.md` §11). Everything that drifted from the original
plan or revealed an undocumented constraint is here so that v2 can either
fix it or design around it.

---

## Environment

### 1. Conda env, not `.venv-spark`

PLAN.md §15 prescribes `python3 -m venv .venv-spark` + `pip install`. On
atlas there was already a **miniforge3 `sedona` env** with Python 3.11,
**Java 21** (bundled by conda-forge `openjdk`), Spark 4.1.1, Sedona
1.9.0, and ~40 other deps. Reusing it saved the ~3 min pip-install +
the entire Java-from-apt detour. The four missing deps
(`jupytext`, `nbconvert`, `rioxarray`, `rasterstats`, `planetary-computer`)
went in via plain `pip install`.

**v2 fix:** update `PLAN.md` §15 + `README.md` "Try it" block to mention
the miniforge3 env as the preferred path; keep `.venv-spark` as the
no-conda fallback.

### 2. Java install via sudo was blocked

Only Java 8 is on the host; Sedona needs ≥ 11. `sudo apt install` wanted
a password (non-interactive shell), and the auto-mode classifier blocked
an unattended curl-from-Adoptium fallback. The conda env's bundled
Java 21 sidestepped both. The next time we ship a Docker/host installer,
the Java step has to be either pre-staged or guided.

---

## Sedona / Spark wiring

### 3. Sedona JARs don't ship with the pip package

`apache-sedona` is Python-only; the Kryo registrator (`SedonaKryoRegistrator`)
lives in the JVM JARs. Without them, the very first `spark.sql("SELECT 1")`
inside `SedonaContext.create` fails with `[FAILED_REGISTER_CLASS_WITH_KRYO]`.
Fix: pin `spark.jars.packages` to

```
org.apache.sedona:sedona-spark-shaded-4.0_2.13:1.9.0
org.datasyslab:geotools-wrapper:1.9.0-33.5
```

at `SedonaContext.builder()`. The notebooks now do this inline; a future
v2 refactor could centralise it in `catmob.sedona_session()`.

### 4. Spark 4.0 vs 4.1 ABI shift

The Sedona 1.9.0 `sedona-spark-shaded-4.0` JAR references
`org.apache.spark.sql.catalyst.expressions.FoldableUnevaluable`, which
Spark 4.1 has renamed/moved. The session starts (noisy stack trace at
init) and the SQL surface we exercise still works (ST_*, ST_H3CellIDs,
ST_Buffer, ST_DWithin, ST_GeomFromGeoJSON, ST_GeomFromWKT, ST_KNN). One
optional Sedona optimizer rule fails to load; we never trigger it.

**v2 fix:** pin pyspark to `==4.0.x` *or* wait for Sedona 1.10+ which
will ship a `sedona-spark-shaded-4.1` artifact.

---

## Data shape drift

### 5. MITMA v2 distritos CSV format changed

`io_mitma._spark_schema_for` and `read_with_sedona` were written against
the older `;`-delimited 12-column "daily" schema. The current public
files at `por-distritos/viajes/ficheros-diarios/<YYYY-MM>/...` are:

- **pipe-delimited** (`|` not `;`)
- carry `periodo` (hour-of-day, `"00".."23"`) in every row — there is no
  separate daily-vs-hourly granularity at distrito level
- add `estudio_origen_posible` and `estudio_destino_posible` boolean-ish
  columns between `actividad_destino` and `residencia`

Total columns: 15 (was 12). `src/catmob/io_mitma.py` Spark schema was
updated; the pandas `parse_csv_gz` path and the pandera schemas are
unchanged (the test fixtures still use the older format, and 44 contract
tests still pass).

### 6. MITMA "hourly" URL returns 404

The published v2 hourly URL at
`https://movilidad-opendata.mitma.es/estudios_basicos/por-distritos/viajes/<YYYY-MM>/<file>`
returns HTTP 404 for the dates we checked (2024-03-06 etc.). The hourly
breakdown is **inside** the "daily" file (column `periodo`). In notebook
01 we write the single read to both `mitma_parquet/daily` *and*
`mitma_parquet/hourly` so downstream code (notebook 04 in particular)
stays untouched.

### 7. GeoJSON property casing

The MITMA zoning GeoJSON uses `properties.ID` (uppercase). The original
notebooks read `properties.id`, which silently returned NULL — every
spatial join was empty. There is also no `properties.name` column;
names live in `data/bronze/mitma/zones/nombres_distritos.csv`.

### 8. Sedona `osmpbf` reader schema in 1.9

Returns `id`, **`kind`** (not `type`), **`location`** struct (not flat
`lon`/`lat`), `tags` map, `refs` array, plus metadata. The Sedona-side
helpers in `io_osm.py` had been written for an earlier (pre-1.7?) schema;
column names updated.

### 9. Sedona `osmpbf` doesn't materialise way geometries

The `kind='way'` rows carry `refs: array<long>` (node IDs) but **no
geometry column** — the user has to JOIN ways → nodes themselves to
reassemble LineStrings. For the highway-network extraction we instead
use **pyrosm** (`OSM(pbf_path).get_network(network_type='driving')`),
write the resulting GeoDataFrame as `bronze/osm/network.parquet` with a
`wkt` text column, and re-hydrate in Sedona via `ST_GeomFromWKT(wkt)`.
This costs ~30-60 s on the 251 MB extract but produces a clean LineString
layer ready for `ST_DWithin` distance joins.

**v2 fix:** either wait for Sedona to add way-geometry assembly to its
PBF reader, or pre-process the PBF with `osmium` → GeoPackage and feed
that to Sedona's GeoPackage reader.

### 10. GTFS feeds not on disk

`data/bronze/gtfs/{rodalies,fgc}/` exist as empty directories. Added
`io_osm.extract_stations_with_sedona` which pulls OSM
`railway=station|halt` nodes as a station table with a synthetic
`trips_per_day = 12`. Notebook 02 reads this when the real GTFS parquet
is absent. The motivation is that the M2 GTFS fetcher is still broken
(see Prompt β in `PLAN.md` §11) — replacing it with a real feed in v2
fills the `trips_per_day` and `trips_to_bcn_core` columns properly.

---

## Modelling shortcuts taken for v1

### 11. Valhalla → circular buffers

Per `PLAN.md` §15: 5 km Euclidean buffer around each station,
implemented as `ST_Buffer(ST_Point(lon,lat), 0.045)` (≈ 5 km at 41°N).
A 15-min band uses `0.027°` (≈ 3 km). Two bands ⇒ a coarse "isochrone"
that `hex_train_reach` collapses with `MIN(minutes)`. The motorway
penalty stays anisotropic-incorrect but that's OK for a first cut.

**v2:** replace with Valhalla bike-profile isochrones (`isochrones.py`
already wraps the REST call).

### 12. Distance thresholds in degrees, not meters

`ST_Distance` on EPSG:4326 inputs returns **degrees**, not meters. The
original `hex_pens` SQL said `ST_Distance(...) < 500` for the motorway
penalty — meaning "within 500 degrees" — which flagged every hex on the
planet as motorway-adjacent. Rewrote to `ST_DWithin(..., 0.005, false)`
(≈ 555 m at 41°N, acceptable overshoot) and `ST_DWithin(..., 0.009, ...)`
(≈ 1 km) for industry density.

**v2 fix:** project hexes + network to EPSG:25831 (UTM 31N, what Catalan
cartography uses) and switch to meter-based `ST_Distance`. The `pyproj`
+ `ST_Transform` path needs the geotools-wrapper PROJ database available,
which the current jar bundle should already include.

### 13. Correlated subqueries → explicit joins

Original `hex_pens` block used `(SELECT COUNT(*) FROM pois p WHERE ...)`
correlated against `hexes`. Spark resolves these but the plan is much
cleaner as two LEFT JOINs (`hex_motorway`, `hex_industry`), each
explicit on `ST_DWithin`. Done.

### 14. Altair PNG export needs `vl-convert-python`

The original notebook 04 saved chart 4 (distance-band share) via
`alt.Chart(...).save(".png")`, which raises without `vl-convert-python`
or `kaleido` installed. Replaced with a matplotlib bar chart.
Also added explicit PNGs for chart 3 (peak-hour corridor distribution)
and chart 7 (daily totals + anomaly markers) so `docs/screenshots/`
gets the full 8 plates.

---

## Spark 4.1 × Sedona 1.9 — the spatial-join saga

These four landmines were all hit in notebook 02 within minutes of each
other; together they cost the most time of any single category of
surprise in this run.

### 15. Spatial-broadcast index serde → `IllegalAccessError` (JTS classloader split)

The first `ST_Intersects` join inside notebook 02 crashed every executor
with

```
java.lang.IllegalAccessError: class org.locationtech.jts.index.strtree.IndexSerde
  tried to access method
  org.locationtech.jts.index.strtree.AbstractSTRtree.getItemBoundables()
```

Root cause: when `spark.jars.packages` (or `--jars`) resolves the Sedona
and `geotools-wrapper` JARs, Spark loads them into a
`MutableURLClassLoader`. JTS is bundled inside `sedona-spark-shaded-4.0`
and is also already on the app classloader (pulled in transitively).
The two JTS copies live in different module loaders, so
`IndexSerde.write` (in the child loader) cannot invoke the
package-private `AbstractSTRtree.getItemBoundables` (in the app loader).

Fixes tried, in order:
1. Pass JARs through `PYSPARK_SUBMIT_ARGS=--jars …` — no change (same
   `MutableURLClassLoader`).
2. Set `spark.driver.extraClassPath` / `spark.executor.extraClassPath` —
   would put JARs on the app loader at JVM launch; *not tried in this
   run*, would be the v2 fix.
3. **Workaround that shipped:** disable the broadcast-spatial-index
   codepath altogether — `sedona.global.index=false`,
   `sedona.join.optimizationmode=none`,
   `sedona.join.autoBroadcastJoinThreshold=-1`,
   `spark.sql.autoBroadcastJoinThreshold=-1`. Sedona then runs spatial
   joins as nested-loop / sort-merge and never serializes an `IndexSerde`.

### 16. `ST_KNN` requires the spatial index — incompatible with the workaround above

With `sedona.global.index=false`, `ST_KNN` itself throws
`UnsupportedOperationException: KNN predicate is not supported`.
Original `hex_climb` / `hex_yoga` / `hex_hospital` SQL used
`ST_KNN(centroid, poi.geom, 1, false)` to find each hex's nearest POI.

Workaround: replace `ST_KNN` with a **bbox-prefiltered LEFT JOIN +
`MIN(ST_DistanceSpheroid(...))`**. Range-equi-join on
`ABS(hex.lon_centroid - poi.lon) < 0.06° AND ABS(hex.lat_centroid -
poi.lat) < 0.06°` keeps only candidate pairs whose lon/lat differ by
less than ~6 km, then take the spheroid distance to the nearest one.
6 km > the 5 km scoring threshold, so we don't lose signal.

Applied the same pattern to every other `ST_Intersects` / `ST_Contains`
/ `ST_DWithin` join in the notebook (train-reach, mitma-disaggregation,
motorway-penalty) — each gets an envelope-based range predicate before
the geometry refinement. Without it, the 50k × 9.3k motorway cross-join
would be 465 M pairs; with it, ~hundreds-of-thousands.

### 17. Plan-lineage explosion → `INTERNAL_ERROR_ATTRIBUTE_NOT_FOUND`

After 6+ `LEFT JOIN`s of derived views that all transitively referenced
the `hexes.geometry` column, the final `gold.write.parquet(...)` call
errored with

```
[INTERNAL_ERROR_ATTRIBUTE_NOT_FOUND]
Could not find geometry#452 in [geom#366]
```

Cause: Spark re-instantiated the H3-explode plan for every reference to
`hexes` (LATERAL VIEW EXPLODE generates fresh attribute IDs each time),
and after enough hops the optimizer couldn't reconcile the geometry
attribute across the children of the final join.

Fix: **persist every per-hex aggregate (`hex_train_reach`, `hex_climb`,
`hex_yoga`, `hex_hospital`, `hex_mitma`, `hex_motorway`, `hex_industry`)
to its own silver parquet, then reload each as a fresh `read.parquet`
DataFrame before the gold compose.** Each JOIN input is now a clean
parquet read with stable attribute IDs.

### 18. MITMA zoning has self-intersecting and degenerate polygons

Two cleaning steps were necessary on `zonificacion_distritos.geojson`:

1. `shapely.geometry.make_valid()` on every distrito polygon — a
   handful of features near BCN are self-intersecting and trip JTS
   `RelateComputer` ("side location conflict") inside `ST_Contains` /
   `ST_Intersects` server-side.
2. **Strip zero-area MultiPolygon sub-parts.** `make_valid()` leaves a
   degenerate sub-polygon (`POLYGON ((x y, x y, x y, x y))` — 4
   identical vertices, zero area) inside one of the distrito
   MultiPolygons; Sedona's `ST_H3CellIDs(geom, 8, true)` then raises
   `H3UtilException: fail to cover the polygon`. Walk each multipolygon
   and drop sub-parts with `area <= 1e-10°` before handing the
   DataFrame to Sedona.

### 19. ST_Union_Aggr of all Catalan distritos fails in JTS `CascadedPolygonUnion`

The original plan unioned the 584 Catalan distritos into a single
MultiPolygon and clipped the H3 grid against it. On Spark 4.1 +
Sedona 1.9 the union itself fails inside JTS (a topology edge case
upstream of the MITMA file). We skip the union entirely — per-distrito
H3 cell explode + DISTINCT gives the same set of cells without needing
a single boundary polygon.

---

## Known limitations / v2 punch list

1. **Train-reach is a 5 km circle, not a bike isochrone.** Replace with
   Valhalla once the docker stack is unblocked.
2. **Train-frequency is a constant 12.** Replace with real GTFS.
3. **Distance thresholds are in degrees.** Reproject to EPSG:25831.
4. **Highway network via pyrosm, not Sedona-native.** Wait for Sedona
   to ship way-geometry assembly or pre-process with osmium.
5. **Air, thermal, biodiversity, light pollution, E-PRTR, hospital
   register columns are NULL** — the M2 follow-up fetchers for those
   sources land in a separate prompt.
6. **Spark 4.1 + Sedona 1.9 emits a noisy `FoldableUnevaluable`
   ClassNotFoundException at session start.** Pin to Spark 4.0.x or wait
   for Sedona 1.10.

---

## What worked first try

- Sedona 1.9 `osmpbf` reader on a 251 MB extract (after fixing column names).
- Sedona H3 functions: `ST_H3CellIDs(geom, 8, true)` + `ST_H3ToGeom(...)`.
- `ST_Buffer` + `ST_DWithin` for the synthetic-isochrone path.
- pyrosm's `get_network(network_type='driving')` for the highway layer.
- The `viz.export_deck_html` helper from `catmob.viz` worked unchanged
  once the upstream gold parquet had real data.
- 44 contract tests still green after the schema edit (test fixtures use
  the older format and the pandas parse path is unchanged).
