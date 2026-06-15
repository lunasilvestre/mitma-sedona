# Results — working prototype (dev scope, 2024-03-04..10)

The first end-to-end Sedona run on real Catalonia data, plus the retrospective
on what tripped us and how we fixed it.

[← back to README](../README.md)

> **Dev-scope prototype.** These numbers come from the dev data window (7 days),
> with sparse amenity coverage and a liveability score that is a *relative
> index*, not a guarantee. See the retrospective below for the caveats.

## Prototype artifacts

The first end-to-end run took **~5 minutes of pipeline time** (notebook 01
ingest + the pandas/geopandas gold layer + notebook 03 visualisation) and
produced:

| Artifact | Count / size | Notes |
|---|---|---|
| `data/bronze/mitma_parquet/{daily,hourly}/` | 27,697,944 OD rows × 252 MB | 7 days × 24 hours, Catalonia-touching |
| `data/bronze/osm/pois.parquet` | 4,935 POIs | climbing 219 · yoga 67 · hospital 36 · pharmacy 411 · industry 5 · park/clinic/doctors |
| `data/bronze/osm/network.parquet` | 364,530 highway ways | from pyrosm — Sedona's native PBF reader doesn't yet build way geometries |
| `data/bronze/osm/stations.parquet` | 475 stations / halts | OSM `railway=station\|halt`, GTFS fallback |
| **`data/gold/h3_res8_catalonia.parquet`** | **45,220 hexes × 12 features** | 1.6 MB on disk |
| `catalonia_liveability.html` | 3.5 MB | self-contained deck.gl page, opens in any browser |
| `screenshots/00_main_map.png` | 1600 × 900 | OD-arc layer over Catalonia |

## Top 10 hexes by liveability score (default weights)

The default-weights ranking is dominated by hexes inside 15-min bike
reach of a train station (lots of small inland towns in Girona / Lleida
get the maximum 64.0 because none of the negative penalties apply and
the climbing / yoga / hospital distances are NULL — beyond the 8 km
cap). Real ranking will shake out once Valhalla isochrones, GTFS
frequency, air-quality rasters, and population disaggregation land in v2.

| h3_id | score | train reach (min) | motorway ≤500 m | industry ≤1 km |
|---|---:|---:|:-:|---:|
| 8839441941fffff | 64.0 | 15 | ✗ | 0 |
| 8839441943fffff | 64.0 | 15 | ✗ | 0 |
| 8839441945fffff | 64.0 | 15 | ✗ | 0 |
| 8839441947fffff | 64.0 | 15 | ✗ | 0 |
| 8839441949fffff | 64.0 | 15 | ✗ | 0 |
| 883944194bfffff | 64.0 | 15 | ✗ | 0 |
| 883944194dfffff | 64.0 | 15 | ✗ | 0 |
| 8839441b01fffff | 64.0 | 15 | ✗ | 0 |
| 8839441b03fffff | 64.0 | 15 | ✗ | 0 |
| 8839441b05fffff | 64.0 | 15 | ✗ | 0 |

## Score distribution

Across all 45,220 hexes: min 0 · 25th pct 28.9 · median 50 · 75th pct 50 ·
max 64 · mean 40.7 · stdev 14.2.

## Map

[![Catalonia liveability — final score](screenshots/00_main_map.png)](catalonia_liveability.html)

OD arcs from notebook 03's deck.gl HTML demo over the 2024-03-06 (Wed)
MITMA flows — pink (source) → cyan (target) arcs concentrate on the
Barcelona metro region with secondary clusters around Girona,
Tarragona, and the Lleida corridor.

For the interactive version of these results, see the geo-browser at
[explore.html](explore.html) (described in [visualization.md](visualization.md)).

## What I learned (v1.3 retrospective)

1. **MITMA's v2 distritos schema has drifted**: the file is pipe-delimited
   now, every row carries `periodo` (hour-of-day), and there are two
   extra `estudio_*_posible` columns. The pandera fixtures in the test
   suite still use the older `;`-delimited 12-column form, so the 44
   contract tests stayed green even after the Spark schema was rewritten.
2. **The Sedona pip package doesn't ship Java JARs.** Pinning
   `spark.jars.packages` to `sedona-spark-shaded-4.0_2.13:1.9.0` +
   `geotools-wrapper:1.9.0-33.5` got us a working `SedonaContext` on
   Spark 4.1 in ~10 s on the first call (Maven download), under a second
   thereafter.
3. **Catalonia's distrito GeoJSON has a few self-intersecting polygons
   and a couple of degenerate sub-multipolygons near Barcelona** that JTS
   refuses (TopologyException, side-location conflict at 2.059, 41.383).
   `geopandas.geometry.make_valid()` + a `geometry.area > 0` filter
   handles both — the H3 explode is well-behaved once they're cleaned.
4. **The v1 gold layer runs faster as plain pandas + geopandas + h3-py
   than via Sedona** at this data size (45 k hexes × 5 k POIs × 364 k
   highway ways), because the Sedona + Spark 4.1 spatial-index serde
   tripped a classloader-mismatch IllegalAccessError on every spatial
   join. The bronze + visualisation steps stay on Sedona (where it pays
   off on the 27 M-row MITMA daily aggregation). See
   [NOTES_FROM_PROTOTYPE_RUN.md](../NOTES_FROM_PROTOTYPE_RUN.md) §3 / §5
   for the gory details.
5. **The deck.gl HTML demo "just works" in any browser** (the
   `catalonia_liveability.html` artifact is fully self-contained,
   3.5 MB including all hex + arc + POI data). Headless screenshots
   require `--use-gl=angle --use-angle=swiftshader`; the default
   `--disable-gpu` flag in Chromium kills WebGL.

---

See also: [scoring.md](scoring.md) · [data_sources.md](data_sources.md) ·
[visualization.md](visualization.md)
