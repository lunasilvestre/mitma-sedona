# Architecture

How the repo is laid out and how data flows through the lakehouse.

[← back to README](../README.md)

## What's inside

```
mitma-sedona/
├── PLAN.md                        canonical planning doc
├── docs/
│   ├── preview_deck.html          standalone deck.gl preview (open in browser)
│   ├── explore.html               interactive liveability geo-browser
│   └── sedona_sql_patterns.md     8 advanced SQL patterns from Wherobots/Apache
├── src/catmob/                    reusable library (schemas, io, scoring, viz)
├── notebooks/                     01_ingest → 02_liveability → 03_viz + 04_descriptives
├── tests/                         47 contract tests (pytest -q)
├── data/README.md                 dataset catalog (every source linked)
├── docker/                        Sedona + Valhalla + Jupyter compose stack
├── configs/weights.yaml           liveability scoring weight presets
└── scripts/fetch_*.sh             idempotent data fetchers
```

## Lakehouse architecture

Bronze → Silver → Gold lakehouse, with H3 res-8 hex grid as the
analytical grain. Sedona handles the spatial work — but note that the
heavy mobility step is **not** a giant spatial join: the OD rows reach the
H3 grid through a cheap broadcast **equi**-join on `zone_id`, and the only
spatial join is the **one-time zone→hex dasymetric crosswalk** (584 distrito
polygons vs the H3 grid; see
[why_spark_sedona.md](why_spark_sedona.md)). Pandera validates every bronze
write.

- **Bronze** — raw ingested sources (MITMA OD parquet, OSM POIs / network /
  stations, GTFS, air-quality stations, Landsat LST, Natura 2000, E-PRTR,
  VIIRS) with pandera contract enforcement on write.
- **Silver** — cleaned, conformed, geometry-validated layers.
- **Gold** — the analytical grain: `data/gold/h3_res8_catalonia_v2.parquet`,
  **45,220 hexes × 26 columns** at H3 res-8 (~0.7 km² hexes), carrying the real
  feature columns (mobility, amenity-proximity, nature, environmental-health and
  penalty terms) that drive the `liveability_score`. Distance/buffer features are
  computed in EPSG:25831 metres. The exact column contract is `GOLD_HEX_SCHEMA`
  in `src/catmob/schemas.py`; `scripts/run_gold_v2.py` assembles the grid.
  The additive **deep-Spark mobility gold**
  (`data/gold/mitma_features/zoning=distritos/h3_mitma_features.parquet` —
  46,121 hexes × 37 columns, plus `seasonal_long.parquet`) is a separate Sedona
  output that feeds the geo-browser's mobility/rhythm/typology/season layers; it
  ships at weight 0, so the published score is unchanged. See
  [why_spark_sedona.md](why_spark_sedona.md).

See [PLAN.md §4](../PLAN.md) for the full lakehouse design.

## I/O modules

Each upstream source has a loader in `src/catmob/io_*.py` that ends with a
pandera `SCHEMA.validate(df)`. As of v2.3 these produce **real** gold columns,
not placeholders:

- `io_mitma` — OD flows → `mitma_inflow_daily`, `mitma_outflow_daily`,
  `mitma_through_ratio`.
- `io_gtfs` — Rodalies + FGC feeds → `trains_per_day_nearest` (now ~91 distinct
  values, not a constant 12) and `trains_to_bcn_nearest`.
- `io_osm` — POIs + network → climb/yoga/hospital proximity, pharmacy density,
  `motorway_within_500m`.
- `io_air` / `io_pollution` — XVPCA stations + E-PRTR + VIIRS →
  `no2_ugm3`, `pm25_ugm3`, `eprtr_facility_min_m`, `viirs_radiance`.
- `io_thermal` — Landsat 8/9 LST → `lst_summer_median_c`, `uhi_delta_c`.
- `io_biodiversity` / `io_health` — Natura 2000 + tree-cover + health POIs →
  `natura2000_within_5km`, `tree_cover_pct`, `hospital_min_m`.

- `io_biodiversity` also lands the GBIF/iNaturalist species feed →
  `biodiversity_obs_density` (observations per km², 100% hex coverage, ~6.2k
  hexes non-zero).

All v2.3 score columns now carry real signal; coverage varies by layer (the
manifest records per-column fractions). The additive deep-Spark mobility layers
(rhythm, weekend hotspots, KMeans typology, geodemographics, the month/season
dimension) are built by the canonical `pipeline_{silver,gold}.py` Sedona path
from the full-scale 89-day-2025 OD scan — see
[why_spark_sedona.md](why_spark_sedona.md).

## Sedona SQL idioms

The specific SQL patterns used for the spatial-join heavy lifting live in
[sedona_sql_patterns.md](sedona_sql_patterns.md): H3 cell-id explode,
area-weighted (dasymetric) disaggregation, raster zonal stats (`RS_ZonalStats`),
`ST_KNN`, `BROADCAST` hints, GeoArrow zero-copy handoff, `MAX_BY` peak-hour, and
`ST_DBSCAN`.

## Local environment

The development environment runs on Miniforge + conda (which sidesteps the
PEP 668 numpy block). The full recipe is in
[local_env_setup.md](local_env_setup.md). For the containerised path, see the
Docker stack in [quickstart.md](quickstart.md).

---

See also: [scoring.md](scoring.md) · [visualization.md](visualization.md) ·
[data_sources.md](data_sources.md)
