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
├── tests/                         44 contract tests (pytest -q)
├── data/README.md                 dataset catalog (every source linked)
├── docker/                        Sedona + Valhalla + Jupyter compose stack
├── configs/weights.yaml           liveability scoring weight presets
└── scripts/fetch_*.sh             idempotent data fetchers
```

## Lakehouse architecture

Bronze → Silver → Gold lakehouse, with H3 res-8 hex grid as the
analytical grain. Sedona handles every spatial join. Pandera validates
every bronze write.

- **Bronze** — raw ingested sources (MITMA OD parquet, OSM POIs / network /
  stations) with pandera contract enforcement on write.
- **Silver** — cleaned, conformed, geometry-validated layers.
- **Gold** — the analytical grain: `data/gold/h3_res8_catalonia.parquet`,
  45,220 hexes × 12 features at H3 res-8 (~0.7 km² hexes).

See [PLAN.md §4](../PLAN.md) for the full lakehouse design.

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
