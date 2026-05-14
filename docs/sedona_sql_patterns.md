# Advanced Sedona SQL — Patterns We Adopt

Curated from public Wherobots examples and the Apache Sedona use-cases
(`apache/sedona/docs/usecases/01-mobility-pulse.ipynb`, `03-fire-risk-fusion`,
`04-flood-snapshot`; `wherobots/wherobots-examples`; `wherobots/apache-sedona-book`).
Each snippet is adapted to mitma-sedona column names from
[`src/catmob/schemas.py`](../src/catmob/schemas.py).

This document is the **canonical reference** for the SQL idioms used in
notebooks 01–04 and in `src/catmob/`. Claude Code agents in M2/M3 should
read it before authoring queries.

**Version requirements** — Sedona ≥ 1.9 (`ST_BingTileAt`, proj4 `ST_Transform`),
≥ 1.7 (`dataframe_to_arrow` zero-copy GeoArrow), ≥ 1.6 (`RS_ZonalStats`,
`ST_KNN`), ≥ 1.5 (H3 functions). All within mitma-sedona's floor pin.

> **Note on `ST_Isochrone`**: this is a **WherobotsDB-only** function (cloud
> product, Valhalla-backed under the hood) and is **not** in OSS Apache
> Sedona. We keep the Valhalla container for bike isochrones — that's the
> right call.

---

## 1. H3 grid generation + zonal joins

```sql
-- Build a Catalonia-covering H3 res-8 grid by exploding cell-ids of the
-- distrito polygons (fullCover=true so border cells are not dropped).
WITH cell_ids AS (
    SELECT DISTINCT cell_id
    FROM distritos_catalonia
    LATERAL VIEW EXPLODE(ST_H3CellIDs(geometry, 8, true)) AS cell_id
),
hexes AS (
    SELECT cell_id AS h3_id,
           ST_H3ToGeom(ARRAY(cell_id))[0] AS geometry
    FROM cell_ids
)
SELECT h3_id,
       ST_X(ST_Centroid(geometry)) AS lon_centroid,
       ST_Y(ST_Centroid(geometry)) AS lat_centroid,
       geometry
FROM hexes
WHERE ST_Intersects(geometry,
                    (SELECT ST_Union_Aggr(geometry) FROM catalonia_boundary));
```

**Why:** `ST_H3CellIDs(..., fullCover=true)` is the documented way to
guarantee every part of an irregular boundary (coastline, Pyrenees) is
covered. `false` drops border cells and leaves holes in the gold table.
`LATERAL VIEW EXPLODE` is the canonical Sedona idiom (`apache-sedona-book`
ch6 `grid.sql` and ch8 `nl_hex_grids.py`).

---

## 2. OD line construction from MITMA + distritos

```python
od_df = (
    sedona.read.option("sep", ";").option("encoding", "UTF-8")
        .option("header", "true").schema(MITMA_DAILY_SPARK_SCHEMA)
        .csv("data/bronze/mitma/daily/*.csv.gz")
        .where("substring(origen,1,2) IN ('08','17','25','43')")
)
od_df.createOrReplaceTempView("od")

(sedona.read.format("geojson").load("data/bronze/mitma/zones/distritos.geojson")
        .selectExpr("id", "ST_GeomFromGeoJSON(geometry) AS geom")
        .createOrReplaceTempView("zones"))

flows = sedona.sql("""
    SELECT od.fecha, od.origen, od.destino, od.distancia,
           od.actividad_origen, od.actividad_destino,
           od.viajes, od.viajes_km,
           ST_MakeLine(ST_Centroid(o.geom), ST_Centroid(d.geom)) AS flow_geom
    FROM od
    JOIN zones o ON o.id = od.origen
    JOIN zones d ON d.id = od.destino
    WHERE od.origen <> od.destino
""")
```

**Why:** `ST_MakeLine(ST_Centroid(...), ST_Centroid(...))` is the exact
idiom used in Apache's taxi-zone OD example. Preserves all MITMA enrichment
columns (`renta`, `edad`, `sexo`) for downstream filters; `flow_geom` lands
in GeoArrow for direct Lonboard `ArcLayer` rendering.

---

## 3. Area-weighted disaggregation (distrito → hex)

```sql
-- Disaggregate MITMA daily inflow from distrito polygons to H3 res-8 hex.
WITH overlaps AS (
    SELECT h.h3_id,
           o.id AS distrito_id,
           ST_Area(ST_Intersection(h.geometry, o.geom))
             / NULLIF(ST_Area(o.geom), 0) AS area_weight
    FROM hexes_catalonia h
    JOIN distritos       o ON ST_Intersects(h.geometry, o.geom)
)
SELECT ov.h3_id,
       SUM(ov.area_weight * inflow.viajes_in)   AS mitma_inflow_daily,
       SUM(ov.area_weight * outflow.viajes_out) AS mitma_outflow_daily
FROM overlaps ov
JOIN (SELECT destino AS id, SUM(viajes) AS viajes_in
      FROM od WHERE fecha = '20240306' GROUP BY destino) inflow
  ON inflow.id = ov.distrito_id
JOIN (SELECT origen  AS id, SUM(viajes) AS viajes_out
      FROM od WHERE fecha = '20240306' GROUP BY origen) outflow
  ON outflow.id = ov.distrito_id
GROUP BY ov.h3_id;
```

**Why:** correctness — without the
`ST_Area(intersection) / ST_Area(distrito)` ratio you over-attribute distrito
flows to hexes that only clip a corner of the polygon. `NULLIF` guards
against zero-area distritos (some MITMA distritos are tiny enclaves). This
is the standard dasymetric reallocation, in pure Sedona SQL — no UDFs.

---

## 4. Raster zonal stats (LST / VIIRS / TCD over hex)

```sql
-- Pixel-count-weighted zonal mean for LST, VIIRS, or TCD over each hex.
WITH per_tile AS (
    SELECT /*+ BROADCAST(h) */
           h.h3_id,
           h.geometry,
           RS_ZonalStats(r.rast, h.geometry, 'sum')   AS tile_sum,
           RS_ZonalStats(r.rast, h.geometry, 'count') AS tile_cnt
    FROM hexes_catalonia h
    JOIN landsat_lst_summer r
      ON RS_Intersects(r.rast, h.geometry)
)
SELECT h3_id,
       ROUND(SUM(tile_sum) / NULLIF(SUM(tile_cnt), 0), 2) AS lst_summer_median_c
FROM per_tile
GROUP BY h3_id;
```

**Why:** two perf wins, both from `03-fire-risk-fusion.ipynb`:

1. `RS_Intersects(rast, geom)` *before* `RS_ZonalStats` skips
   non-overlapping `(tile, hex)` pairs at planning time (Sedona pushes it
   into a `RangeJoin`).
2. Aggregating `sum` and `count` per tile then dividing yields a
   *pixel-count-weighted* mean across multi-tile hexes.
   `RS_ZonalStats(..., 'mean')` per tile would average two tiles equally
   even if one contributes 4 pixels and the other 400.

The same template handles VIIRS DNB (`'mean'`) and Copernicus TCD
(`'mean'` returning percentage). For UHI: run twice (urban + rural) and
take the difference inside SQL, or use `RS_MapAlgebra` (see §8e).

> **Caveat (Sedona issue #2409):** `RS_ZonalStats` clipping is not
> automatic; for very small hexes vs huge scenes consider an
> `RS_Tile(rast, 256, 256)` explode upstream.

---

## 5. KNN + isochrone-style point-in-polygon at scale

```python
# Nearest train station per hex centroid (indexed kNN, Sedona ≥ 1.6).
sedona.sql("""
    SELECT h.h3_id,
           ST_DistanceSpheroid(h.centroid, s.geom) / 1000.0 AS station_km
    FROM (SELECT h3_id, ST_Centroid(geometry) AS centroid
          FROM hexes_catalonia) h
    JOIN train_stations s
      ON ST_KNN(h.centroid, s.geom, 1, false)
""").createOrReplaceTempView("nearest_station")

# Hexes covered by ANY Valhalla bike-isochrone (left-join + NULL filter).
sedona.sql("""
    SELECT h.h3_id,
           CASE WHEN MIN(iso.minutes) IS NULL THEN NULL
                ELSE MIN(iso.minutes) END AS train_reach_min
    FROM hexes_catalonia h
    LEFT JOIN bike_isochrones iso
      ON ST_Intersects(iso.polygon, h.geometry)
    GROUP BY h.h3_id
""")
```

**Why:** `ST_KNN(left, right, k, false)` is `O((|L|+|R|)·log|R|)` instead
of the cross-product Cataluña would otherwise produce (50k hexes × ~3M
OSM POIs would be ~150B comparisons). The fourth arg `false` requests
planar nearest (faster); switch to `true` only if geographic-corrected
distance ranking is needed.

The left-join + `MIN(...)` pattern (from `wherobots-examples/Analyzing_Data/Isochrones.ipynb`)
cleanly distinguishes "no station reachable" (`NULL`) from "0 minutes" —
critical for the scoring formula that clips at 25 minutes.

---

## 6. Broadcast + AQE hints for big spatial joins

```python
sedona.conf.set("sedona.join.autoBroadcastJoinThreshold", "100MB")
sedona.conf.set("spark.sql.autoBroadcastJoinThreshold",  "50MB")
sedona.conf.set("sedona.global.indextype", "rtree")
sedona.conf.set("sedona.join.optimizationmode", "all")

# 50k hexes is small enough to broadcast against millions of OD rows.
sedona.sql("""
    SELECT /*+ BROADCAST(h) */
           h.h3_id, COUNT(*) AS od_lines_through
    FROM hexes_catalonia h
    JOIN flows f ON ST_Intersects(h.geometry, f.flow_geom)
    GROUP BY h.h3_id
""")
```

**Why:** the gold table is ~50k hex polygons (~few MB serialised) — fits
the broadcast threshold. The hint forces a `BroadcastIndexJoin` on the
small side, which builds an R-tree per executor on the broadcast hexes.

> **AQE caveat (SEDONA-56):** broadcast spatial joins historically failed
> under Spark AQE; the 1.7+ `RangeJoinExec` rework fixed it but if you see
> a `MatchError` on `Exchange`, set `spark.sql.adaptive.enabled=false` for
> the offending stage.

`apache-sedona-book/chapter10/OptimizingSpatialJoin.ipynb` shows 4–10×
speedups simply from partition sizing — `df.repartition(...)` if shuffle
stages skew.

---

## 7. GeoArrow handoff to Lonboard (zero-copy)

```python
from sedona.spark import dataframe_to_arrow
import lonboard

# Pre-compute endpoints server-side; Lonboard accessors become column lookups.
flows_arrow_ready = sedona.sql("""
    SELECT origen, destino, viajes,
           ST_StartPoint(flow_geom) AS source_pt,
           ST_EndPoint(flow_geom)   AS target_pt
    FROM flows
""")

arrow_table = dataframe_to_arrow(flows_arrow_ready, crs="EPSG:4326")

layer = lonboard.ArcLayer.from_arrow(
    arrow_table,
    get_source_position="source_pt",
    get_target_position="target_pt",
    get_width="viajes / 1000",
)
m = lonboard.Map([layer], basemap_style=lonboard.basemap.CartoBasemap.DarkMatter)
```

**Why:** `dataframe_to_arrow` skips the
`toPandas() → shapely → wkt → Arrow` round-trip the older path forced. On
the 10M-row benchmark in `apache-sedona-book/chapter10/geoarrow_benchmark.py`,
the Arrow path is **~5× faster** and uses **~3× less peak RSS** than the
GeoPandas detour. Pre-computing endpoints server-side keeps Lonboard's
accessors O(1) — at 200k+ MITMA arcs that matters.

---

## 8. Bonus patterns to lift the notebook quality

### (a) `MAX_BY` for peak-hour bucketing in one pass

```sql
SELECT origen,
       SUM(viajes) AS total,
       MAX_BY(periodo, viajes) AS peak_hour,
       CASE WHEN MAX_BY(periodo, viajes) BETWEEN 5  AND 10 THEN 'morning'
            WHEN MAX_BY(periodo, viajes) BETWEEN 11 AND 15 THEN 'midday'
            WHEN MAX_BY(periodo, viajes) BETWEEN 16 AND 19 THEN 'evening'
            ELSE 'nightlife' END AS peak_bucket
FROM mitma_hourly GROUP BY origen
```

Nails Notebook 04 §3 ("rush-hour identification") in 6 lines, no window
function or self-join.

### (b) DBSCAN clusters of underserved hexes

```sql
SELECT cluster_id,
       ST_ConvexHull(ST_Union_Aggr(centroid)) AS poor_zone_hull,
       COUNT(*) AS hex_count,
       AVG(liveability_score) AS avg_score
FROM (
    SELECT h3_id, centroid, liveability_score,
           ST_DBSCAN(centroid, 10000.0, 25, true) AS cluster_id
    FROM gold_hex
    WHERE liveability_score < 30
)
WHERE cluster_id IS NOT NULL
GROUP BY cluster_id;
```

Two SQL passes, no scikit-learn dependency. From `Isochrones.ipynb`.

### (c) GeoParquet 1.1 with bbox metadata for free

```python
gold_df.write.format("geoparquet").mode("overwrite") \
    .save("data/gold/h3_res8_catalonia.parquet")
```

Sedona auto-populates bbox + projjson from geometry SRID — downstream
readers (Lonboard, GeoPandas, DuckDB-spatial) get bbox-pruned reads with
no extra config. Ensure `ST_SetSRID(geom, 4326)` before write
(`04-flood-snapshot.ipynb` §4 fixed this exact bug).

### (d) `ST_BingTileAt` for free coarse aggregation

```sql
-- Zoomed-out heatmap key for deck.gl HeatmapLayer (Sedona ≥ 1.9).
SELECT ST_BingTileAt(lon_centroid, lat_centroid, 9) AS tile_id,
       AVG(liveability_score) AS avg_score
FROM gold_hex
GROUP BY tile_id;
```

~1k tiles for all of Catalonia at zoom 9 — perfect overview before drilling
into res-8 hexes. No `h3-py` dependency.

### (e) `RS_MapAlgebra` for UHI-delta in pure SQL

```sql
SELECT RS_MapAlgebra(
         lst.rast, rural_baseline.rast, 'D',
         'out[0] = rast0[0] - rast1[0];'
       ) AS uhi_delta_rast
FROM landsat_lst_summer lst
JOIN rural_baseline_lst rural_baseline
  ON RS_Intersects(lst.rast, rural_baseline.rast);
```

Server-side raster algebra — one less Python step. From
`04-flood-snapshot.ipynb` §3.

### (f) STAC reader for Landsat / VIIRS pulls

```python
items = sedona.read.format("stac") \
    .option("collection", "landsat-c2-l2") \
    .option("bbox", "0.15,40.50,3.35,42.90") \
    .option("datetime", "2024-06-01/2024-08-31") \
    .option("query", '{"eo:cloud_cover":{"lt":20}}') \
    .load("https://planetarycomputer.microsoft.com/api/stac/v1")
```

Replaces the `io_thermal.query_summer_lst_scenes` stub with one read.
Free retries + partial scene handling. Same pattern works for VIIRS DNB
(collection `viirs-monthly-v22`).

---

## Skeptical caveats

- The **Medium "H3 Spatial Grid Support in Apache Sedona" post (2023)**
  by Mo Sarwat is still useful for fundamentals but does not cover the
  ≥ 1.7 `dataframe_to_arrow` or the ≥ 1.9 `ST_BingTileAt`. Prefer the
  Apache `01-mobility-pulse.ipynb` for current work.
- The **Sedona blog "Should You Use H3 for Geospatial Analytics?" (Sep 2025)**
  argues against H3 for some workloads but explicitly endorses it for
  raster→vector aggregation and visualisation — exactly mitma-sedona's
  use case. Cite this in PLAN.md to pre-empt reviewer skepticism.
- `ST_Isochrone` in Wherobots' `Isochrones.ipynb` is **closed-source
  WherobotsDB**, not OSS Sedona. mitma-sedona's plan to ship Valhalla in
  Docker is the right call.
- `RS_ZonalStats` clipping (Sedona issue #2409, still open) means very
  small hex × very large scene queries can be slow; the per-tile
  `RS_Intersects` filter in §4 is the standard workaround.

## Sources

- [wherobots/wherobots-examples](https://github.com/wherobots/wherobots-examples)
- [wherobots/apache-sedona-book](https://github.com/wherobots/apache-sedona-book)
- [apache/sedona docs/usecases](https://github.com/apache/sedona/tree/master/docs/usecases)
- [Should You Use H3 for Geospatial Analytics? — Apache Sedona blog (Sep 2025)](https://sedona.apache.org/latest/blog/2025/09/05/should-you-use-h3-for-geospatial-analytics-a-deep-dive-with-apache-spark-and-sedona/)
- [Raster Data Analysis with Spatial SQL and Apache Sedona — Wherobots](https://wherobots.com/blog/raster-data-analysis-spatial-sql-wherobots-apache-sedona/)
- [Sedona dataframe_to_arrow API docs](https://sedona.apache.org/latest/api/pydocs/sedona.spark.geoarrow.html)
- [Sedona Optimizer / Query optimization](https://sedona.apache.org/latest/api/sql/Optimizer/)
- [SEDONA-56 — Broadcast join + AQE compatibility](https://issues.apache.org/jira/browse/SEDONA-56)
- [Sedona issue #2409 — RS_ZonalStats raster clipping](https://github.com/apache/sedona/issues/2409)
