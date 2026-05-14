# Tests — Data Loading Stage

This package guards the **data-loading contract**. Every bronze-layer
ingest path passes through a pandera schema (defined in
`src/catmob/schemas.py`); the tests here assert that the schemas are
self-consistent, the fixtures match the schemas, and the parsers preserve
the critical invariants we depend on downstream.

The suite is intentionally **fast** (sub-second) and **dependency-light**
(no Spark, no network). Heavier integration tests live under
`tests/integration/` (added in M2) and are gated behind `RUN_INTEGRATION=1`.

## Running

```bash
# from repo root, with a fresh uv venv
uv sync --extra test
uv run pytest -q

# specific suite
uv run pytest tests/test_io_mitma.py -v

# with coverage
uv run pytest --cov=catmob --cov-report=term-missing
```

## Layout

```
tests/
├── conftest.py                  shared fixtures (paths to sample files)
├── fixtures/
│   ├── mitma_daily_sample.csv(.gz)
│   ├── mitma_hourly_sample.csv(.gz)
│   ├── mitma_zones_sample.geojson
│   └── xvpca_stations_sample.csv
├── test_schemas.py              schema registry self-tests
├── test_io_mitma.py             MITMA CSV.gz parser
├── test_io_osm.py               OSM POI categoriser
├── test_io_air.py               XVPCA station CSV parser
└── test_geo_invariants.py       coordinate / id invariants on fixtures
```

## What each suite covers

| Suite | Asserts |
|---|---|
| `test_schemas.py` | every dataset has a registered schema; gold schema covers every dimension referenced by the score function; hourly extends daily |
| `test_io_mitma.py` | gzip + UTF-8 + `;`-delimited parse; zone-IDs stay strings; distance/actividad enums enforced; Catalonia filter keeps cross-border; URL builders match documented bucket paths byte-for-byte |
| `test_io_osm.py` | POI categoriser maps tags→category correctly, drops unmatched POIs, handles empty/malformed tags; the `osmium tags-filter` spec covers every key downstream code depends on |
| `test_io_air.py` | XVPCA CSV → unified schema rename works; operator marked; coordinates inside Catalonia bbox; non-negative pollutant values |
| `test_geo_invariants.py` | zone IDs unique; geometries inside Catalonia bbox; zone-ID prefixes match `provincia` field |

## Why this matters before M2

These tests **encode the contract** the M2 implementation has to satisfy.
When Claude Code (Prompt B in `PLAN.md` §11) implements the actual
fetchers, the tests already exist and a green run is the definition of
done. The schemas double as ingest-time guards — every bronze write
passes through the same validators, so a stale-source surprise crashes
loudly during ingest rather than silently corrupting gold.

## Adding new tests

When a new dataset enters the catalog:

1. Add its schema to `src/catmob/schemas.py` and register it in
   `SCHEMA_REGISTRY`.
2. Add a tiny CSV/GeoJSON fixture under `tests/fixtures/` (≤2 KB).
3. Add `tests/test_io_<dataset>.py` exercising at least: happy-path parse,
   one invalid input, schema enforcement on a critical column.
4. Add the dataset to `data/README.md`'s catalog table.
5. Add a row to `test_schemas.test_gold_hex_columns_cover_all_dimensions`
   if the new dataset contributes a gold-layer column.

## Future hooks (M2+)

- `tests/integration/test_full_ingest.py` — runs `01_data_ingest.ipynb`
  on a 1-day MITMA + 1 km² OSM clip via `nbmake`.
- `tests/integration/test_sedona_smoke.py` — minimal SedonaContext start,
  read fixture parquet, ST_Within, count.
- `pytest-benchmark` on `parse_csv_gz` so a perf regression is visible.
