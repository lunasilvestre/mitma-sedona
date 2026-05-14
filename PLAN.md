# mitma-sedona — Catalonia Liveability × Mobility Planning Doc

**Status:** Draft v1.2 (full-scale by default) · **Date:** 2026-05-14 · **Author:** Nelson + Claude (atlas)

> **The real question** — *Where in Catalonia could I live well? Within bike-reach of a train station that connects to Barcelona, with climbing gyms and yoga nearby, close to green or sea, away from heavy industry and motorway noise — and now also: clean air, low urban heat, low light pollution, near biodiversity, with health amenities at hand.*

Started as "regional mobility analysis with Sedona". Reframed as a personal liveability search. Expanded to environmental health and biodiversity. Now scaled up: **using Sedona on a one-month sample defeats the point of Spark.** Default is full-scale; a `--scope dev` switch exists only for fast local iteration.

**Repo layout:** local at `/home/nls/Documents/dev/mitma-sedona`, GitHub target `lunasilvestre/mitma-sedona` (public). All future work lives in this directory; the previous `catalonia-livability-mobility/` was renamed in place.

---

## 0. What's already shipped (M0)

| Deliverable | Path | Status |
|---|---|---|
| Pandera schemas for 13 datasets | `src/catmob/schemas.py` | ✅ all in `SCHEMA_REGISTRY` |
| MITMA CSV.gz parser + URL builder + Catalonia filter | `src/catmob/io_mitma.py` | ✅ |
| OSM POI categoriser + osmium tag spec | `src/catmob/io_osm.py` | ✅ |
| XVPCA air-quality CSV parser | `src/catmob/io_air.py` | ✅ |
| Stubs for thermal/biodiversity/pollution/health loaders | `src/catmob/io_*.py` | ✅ shape contracts only — implemented in M2 |
| **44 contract tests, all green in 0.23 s** | `tests/test_*.py` | ✅ `pytest -q` |
| Test fixtures (gzipped MITMA, GeoJSON zones, XVPCA stations) | `tests/fixtures/` | ✅ |
| Data catalog | `data/README.md` | ✅ |
| **Preliminary deck.gl preview HTML** (synthetic data, all viz layers) | `docs/preview_deck.html` | ✅ open in any browser |
| Notebook 03 storyboard | `notebooks/preview_storyboard.md` | ✅ |
| **Sedona SQL patterns reference** (8 patterns adapted from Wherobots/Apache examples) | `docs/sedona_sql_patterns.md` | ✅ |

Quick repro:

```bash
cd /home/nls/Documents/dev/mitma-sedona
python3 -m venv .venv && source .venv/bin/activate
pip install 'pandera[pandas]>=0.20' pytest pandas
PYTHONPATH=src pytest -q   # → 44 passed in 0.23s
xdg-open docs/preview_deck.html  # or open it in a browser
```

---

## 1. What gets delivered (M5 = portfolio-ready)

| Artifact | Form | Audience |
|---|---|---|
| Public GitHub repo `lunasilvestre/mitma-sedona` | MIT-licensed, README + screenshots | recruiters, peers |
| `docker compose up` reproducible Spark + Sedona env | self-contained, one command | reviewers |
| 4 Jupyter notebooks (markdown-rich) | rendered .ipynb + .md exports | reviewers, future-Nelson |
| Data catalog (`data/README.md`) | navigable tree, every source linked | reviewers |
| Visualisation showcase (Lonboard + pydeck + deck.gl, all flow/arc/hex layers) | three notebook cells + hosted HTML | recruiters who skim |
| Descriptive statistics report (weekday avgs, peak hour, ≥8 charts) | `notebooks/04_descriptives.ipynb` | data-curious reviewers |
| The actual answer for Nelson | top-10 hex ranking + map | Nelson |

---

## 2. Constraints — full liveability score

A hexagon (H3 res 8 ≈ 0.7 km²) earns or loses points across these dimensions. Default weights are **balanced** (per Nelson); `configs/weights.yaml` holds them and the notebook-3 sensitivity cell tests stability across alternatives.

| Dimension | Source | Default weight | Threshold / formula |
|---|---|---|---|
| **Mobility & accessibility** | | | |
| Bike-reach to train station | Valhalla bike isochrones from OSM `railway=station` | + (heavy) | `max(0, 25 − reach_min) × 1.4` |
| Train service frequency to BCN | Renfe Rodalies + FGC GTFS | + (medium) | `trains_to_bcn / 30` |
| **Lifestyle amenities** | | | |
| Climbing gym proximity | OSM `sport=climbing`, `leisure=climbing` | + (medium) | `−min(climb_m, 5000) / 200` |
| Yoga studio proximity | OSM `sport=yoga` (+ name fuzzy) | + (medium) | `−min(yoga_m, 5000) / 250` |
| **Nature** | | | |
| Green proximity | OSM `leisure=park\|garden`, `landuse=forest`, `natural=wood` | + (medium) | `−min(green_m, 4000) / 200` |
| Sea proximity | OSM `natural=coastline` | + (bonus) | `+6 if sea_m < 3000` |
| Tree cover density | Copernicus 10 m TCD | + (small) | `tree_cover_pct × 0.15` |
| Protected area within 5 km | WDPA / Natura 2000 | + (medium) | `+5 if any` |
| Biodiversity observation density | iNaturalist via GBIF | + (small) | log-scaled count per km² |
| **Environmental health** | | | |
| NO₂ annual mean | EEA + XVPCA + CAMS gridded | − (medium) | `−max(0, NO₂ − 20) × 0.5` (WHO 2021) |
| PM₂.₅ annual mean | EEA + XVPCA + CAMS | − (heavy) | `−max(0, PM₂.₅ − 5) × 1.2` |
| Urban Heat Island Δ | Landsat 8/9 LST summer composite | − (medium) | `−max(0, UHI_Δ) × 2` |
| Light pollution (VIIRS DNB) | NOAA monthly composites via STAC | − (small) | `−radiance × 0.05` |
| **Penalties** | | | |
| Industry density | OSM `landuse=industrial` ≤ 1 km | − (heavy) | `−industry_density × 6` |
| E-PRTR facility distance | EEA E-PRTR registry | − (medium) | inverse-weighted by emissions tonnage |
| Motorway noise | OSM `highway=motorway\|trunk` ≤ 500 m | − (heavy) | `−12 if true` |
| **Health amenities** | | | |
| Hospital proximity | OSM `amenity=hospital` + CatSalut | + (small) | `−min(hospital_m, 8000) / 400` |
| Pharmacy density | OSM `amenity=pharmacy` ≤ 1 km | + (small) | log-scaled count |
| **Mobility "vibe check"** | | | |
| Through-flow ratio | MITMA daily OD: `(in+out) / population_proxy` | configurable | disabled by default; turn on for "quiet" bias |

The score is a weighted sum, clipped to `[0, 100]`. Formula in `src/catmob/scoring.py` (M3).

---

## 3. Tech stack — floating pins with floors

Strategy: **install latest stable** at setup time, downgrade only if a known incompatibility surfaces. `uv.lock` freezes whatever resolves so the snapshot is reproducible.

| Component | Floor | Notes |
|---|---|---|
| Apache Sedona | `>=1.9` | latest at install time |
| Apache Spark | `>=3.5,<5.0` | Sedona resolves the matching pyspark |
| Python | `>=3.11,<3.13` | Lonboard floor; bumped only if a dep complains |
| Lonboard | `>=0.16` | latest |
| pydeck | `>=0.9` | latest |
| deck.gl (CDN, JS) | `9.x` | UMD bundle |
| Java | bundled by `apache/sedona` Docker image | host doesn't need JDK |
| H3 | `h3-js >=4` (browser) + Sedona native (Spark) | |
| Valhalla | `gisops/valhalla:latest` | Docker, bike profile |
| pandera | `>=0.20` | pandas + pyspark backends |

**Downgrade fallback playbook**:
1. `pip install` resolves and `pytest -q` is green → ship as-is.
2. Sedona session refuses to start → pin Sedona to last-known-good (`==1.9.0`) + Spark `==3.5.5`.
3. Lonboard fails at GeoArrow handoff → pin Lonboard to 0.16.x.
4. Spark 4.x forces Scala 2.13 mismatch → fall back to Spark 3.5 explicitly.

---

## 4. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Sources (immutable, fetched by scripts/fetch_*.sh)                  │
│  • MITMA v2 OD CSV.gz — full Q1+Q2 2024 daily + all March 2024 hourly│
│  • OSM Cataluña PBF (~250 MB → ~50 MB after osmium prune)            │
│  • Renfe Rodalies + FGC GTFS                                         │
│  • EEA + XVPCA + CAMS air quality                                    │
│  • Landsat 8/9 LST (summer composite, via Planetary Computer STAC)   │
│  • WDPA / Natura 2000 + Copernicus TCD + iNaturalist via GBIF        │
│  • E-PRTR + VIIRS DNB                                                │
│  • CatSalut hospitals                                                │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ schema-validated at write (pandera)
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Bronze — raw → Parquet (data/bronze/<source>/...)                   │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ Sedona spatial joins, OSM POI extraction,
                         │ Valhalla isochrones, GTFS frequency, LST UHI
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Silver — typed, geometry-bearing, partitioned                       │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ H3 res-8 grid + per-hex feature aggregation
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Gold — h3_res8_catalonia.parquet (~50k rows × 25 cols)              │
│  Schema: catmob.schemas.GOLD_HEX_SCHEMA                              │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ Lonboard (mass) / pydeck (interactive) /
                         │ deck.gl (hosted HTML) + descriptive stats
                         ▼
                 ┌───────────────────────┐
                 │ 4 notebooks + HTML demo│
                 └───────────────────────┘
```

---

## 5. Repo skeleton

```
mitma-sedona/
├── README.md                         portfolio landing page (M5)
├── LICENSE                           MIT
├── PLAN.md                           this doc
├── .gitignore                        excludes data/, .venv, .ipynb_checkpoints
├── pyproject.toml                    uv, floor pins
├── docker/
│   ├── docker-compose.yml            sedona + valhalla + jupyter
│   ├── Dockerfile.jupyter            apache/sedona + extras
│   └── valhalla.json                 bike profile
├── data/
│   └── README.md                     dataset catalog (✅ shipped)
├── src/catmob/                       reusable library
│   ├── schemas.py                    pandera schemas (✅)
│   ├── io_mitma.py                   parser + URL builders (✅)
│   ├── io_osm.py                     POI categoriser (✅)
│   ├── io_air.py                     XVPCA parser (✅)
│   ├── io_thermal.py                 STAC LST query (stub)
│   ├── io_biodiversity.py            WDPA + iNat (stub)
│   ├── io_pollution.py               E-PRTR + VIIRS (stub)
│   ├── io_health.py                  CatSalut (stub)
│   ├── io_gtfs.py                    Renfe + FGC GTFS (M2)
│   ├── isochrones.py                 Valhalla client + caching (M3)
│   ├── h3_utils.py                   grid generation + aggregation (M3)
│   ├── scoring.py                    weighted multi-criteria scorer (M3)
│   ├── stats.py                      descriptive stats helpers (M4)
│   └── viz.py                        Lonboard/pydeck/deck.gl helpers (M4)
├── notebooks/
│   ├── preview_storyboard.md         (✅ shipped)
│   ├── 01_data_ingest.ipynb          bronze → silver (M2)
│   ├── 02_liveability_layer.ipynb    silver → gold (M3)
│   ├── 03_score_and_visualise.ipynb  gold → maps + ranking (M4)
│   └── 04_descriptives.ipynb         weekday/peak-hour stats + charts (M4)
├── tests/                            (✅ 44 tests, 0.23 s)
├── docs/
│   ├── preview_deck.html             (✅ preliminary preview)
│   ├── catalonia_liveability.html    final hosted demo (M4)
│   └── screenshots/                  embedded in README
├── configs/
│   └── weights.yaml                  scoring weight presets (M3)
├── scripts/
│   ├── fetch_mitma.sh                idempotent download (M1)
│   ├── fetch_osm.sh                  Geofabrik + osmium pre-filter (M1)
│   ├── fetch_air.sh                  EEA + XVPCA + CAMS (M2)
│   ├── fetch_thermal.sh              STAC LST (M2)
│   ├── fetch_biodiversity.sh         WDPA + iNat + TCD (M2)
│   ├── fetch_pollution.sh            E-PRTR + VIIRS (M2)
│   ├── fetch_health.sh               CatSalut (M2)
│   └── render_notebooks.sh           jupyter nbconvert → md+html (M5)
└── .github/workflows/
    ├── ci.yml                        ruff + pytest on fixtures (M1)
    └── pages.yml                     deploy docs/ to GitHub Pages (M5)
```

---

## 6. Data window — full-scale by default

> Sedona earns its keep on volume, not on a one-month toy. Default = full-scale.

| Window | MITMA daily | MITMA hourly | Bronze size | Use for |
|---|---|---|---|---|
| **`--scope full` (default)** | **Q1+Q2 2024 (181 days)** | **all March 2024 (31 days × 24 h)** | ~3.5 GB | seasonality, weekday averages, rush-hour profile, anomaly detection, score |
| `--scope dev` | week 2024-03-04..10 (7 days) | day 2024-03-06 (24 h) | ~150 MB | fast local iteration, integration tests, demo runs on a laptop |

The CLI flag `--scope` on `scripts/fetch_mitma.sh` chooses; default is `full`. Notebook 04 only makes sense on full-scale data.

Total disk footprint at full scale: **~7 GB**. Atlas has 738 GB free.

---

## 7. Notebook outline

### `01_data_ingest.ipynb` — Sources to Bronze (~30 min dev, ~90 min full)

1. SedonaContext setup; verify Sedona/Spark/Java versions.
2. Pull MITMA daily for the chosen scope.
3. Pull MITMA hourly for the chosen scope.
4. Read OSM PBF with native Sedona reader; project to EPSG:25831.
5. Read GTFS, compute trips_per_day per stop.
6. Pull air quality (XVPCA + EEA + CAMS).
7. Pull thermal LST summer composite via STAC.
8. Pull WDPA + iNat + TCD; pull E-PRTR + VIIRS; pull CatSalut.
9. Schema-validate every bronze table on write.
10. Print row counts + storage savings.

### `02_liveability_layer.ipynb` — Bronze to Silver to Gold (~45 min dev, ~75 min full)

1. Filter MITMA OD to Catalonia (provinces 08/17/25/43).
2. Extract POI layers from OSM.
3. Generate H3 res-8 grid covering Catalonia bbox; clip to boundary.
4. Build Valhalla bike isochrones for every Catalan train station.
5. Per-hex feature joins: train reach, POI distances, green/sea, LST/UHI, NO₂/PM₂.₅, tree cover, Natura 2000, biodiversity density, E-PRTR, VIIRS, hospital reach, pharmacy density.
6. MITMA aggregations to hex (area-weighted disaggregation from distrito).
7. Write `gold/h3_res8_catalonia.parquet`; print distributions per column.

### `03_score_and_visualise.ipynb` — Gold to maps (~20 min)

Per `notebooks/preview_storyboard.md`:
1. Score function with explicit weights from `configs/weights.yaml`.
2. **Lonboard cell** — full MITMA daily OD as ArcLayer over CARTO dark, GeoArrow handoff (mass density).
3. **pydeck cell** — interactive H3HexagonLayer (extruded score) + ScatterplotLayer (POIs) + ArcLayer (top-N flows), with `ipywidgets` toggles.
4. **deck.gl raw HTML** — export `docs/catalonia_liveability.html` (production version of the existing preview).
5. Top-10 ranked table.
6. Sensitivity analysis across 4 weight presets; report Jaccard overlap.
7. Caveats & limitations.

### `04_descriptives.ipynb` — Weekday averages, peak hours, charts (~30 min)

> **Justifies the firepower of the full-scale run.** Without full-scale data this notebook is uninteresting.

1. **Weekday vs weekend daily-flow** — bar chart, per-province + Catalonia total.
2. **Per-weekday hourly profile** — heatmap `weekday × hour`, value = total flow.
3. **Rush-hour identification** — peak-hour share per weekday for top-20 BCN-bound corridors.
4. **Distance-band shifts by weekday** — short trips dominate weekends, longer commute trips dominate weekdays.
5. **Activity-pair matrix** — `casa→trabajo_estudio` weekdays vs `casa→frecuente` weekends.
6. **Per-province comparison** — Barcelona vs Girona vs Tarragona vs Lleida — flow density, average distance, weekday/weekend ratio.
7. **Anomaly day surface** — 3-σ on daily totals; expect to find Spring break + Easter (Mar 28–Apr 1, 2024).
8. **Seasonality across Q1+Q2** — month-on-month flow shifts.
9. **Modal proxy via distance-band** — short → walk/bike; long → train likely.
10. **Charts inventory** — at least 8 publication-quality charts (matplotlib + altair + Lonboard heatmap), all exported as PNG to `docs/screenshots/`.

The charts produced here also embed into the repo README and into `docs/catalonia_liveability.html` as expandable panels.

---

## 8. Visualisation stack — best representation

Per `notebooks/preview_storyboard.md` (full table); summary:

| Use case | Library | Layer | Why |
|---|---|---|---|
| Million-arc raw flow | **Lonboard** | `ArcLayer` (GeoArrow) | Browser-native at scale |
| Aggregated OD lines | Lonboard | `LineLayer` | Cleaner at low zoom |
| Bidirectional OD with magnitude | deck.gl | `ArcLayer` + curvature | source/target colour gradient |
| Multi-flow merged channels | deck.gl-community | `FlowMapLayer` | Best-in-class OD cartography |
| Hex liveability score | deck.gl + pydeck | `H3HexagonLayer` (extruded) | Score = height + colour |
| Constraint toggles | **pydeck** | `Layer` + `ipywidgets` | Python state ↔ JS layers |
| Hosted standalone demo | **deck.gl raw HTML** | layers exported | GitHub Pages friendly |
| POI clusters | deck.gl | `ScatterplotLayer` | Standard, fast |
| Train network | deck.gl | `Scatter` + `PathLayer` | PathLayer when GTFS shapes load |
| Heat / UHI gradient | Lonboard | `BitmapLayer` from LST GeoTIFF | One layer, sub-second |

`docs/preview_deck.html` already exercises 4 of these on synthetic data.

---

## 9. Test strategy

**Already in place (M0):** **44 contract tests**, all green in 0.23 s.

- Schema registry self-tests
- MITMA parser (gzip, UTF-8, semicolon, zone-ID padding, enum enforcement, Catalonia filter)
- OSM POI categoriser (tag rules, fall-throughs, empty inputs)
- XVPCA parser (rename → unified schema, coordinate bounds)
- Geo invariants on fixtures

**Coming in M2/M3:**

- `tests/integration/test_full_ingest.py` — runs `01_data_ingest.ipynb` on a 1-day MITMA + 1 km² OSM clip via `nbmake`.
- `tests/integration/test_sedona_smoke.py` — minimal SedonaContext, read fixture parquet, ST_Within, count.
- `tests/test_scoring.py` — fixture hex with hand-computed inputs, asserted score.
- `tests/test_isochrones.py` — Valhalla mock returning known polygon; cache hit/miss.
- `pytest-benchmark` on `parse_csv_gz` so a perf regression is visible.

---

## 10. Milestones

| # | Milestone | Status |
|---|---|---|
| **M0** | Plan + schemas + tests + preview HTML + data catalog | ✅ shipped |
| M1 | Repo skeleton + Docker stack boots + GitHub repo created | next — assign Prompt A |
| M2 | Bronze layer for all 8 source families, schema-validated | Prompt B |
| M3 | Silver + Gold (50k hexes, all 25 features) | Prompt C |
| M4 | Notebooks 03 + 04 + hosted HTML demo | Prompt D (or Cowork) |
| M5 | README + screenshots + GitHub Pages | Cowork |
| M6 | OB1 capture + vault wiki page + decision log | Cowork |

---

## 11. Agent delegation matrix + Claude Code prompts

Cowork keeps the strategic loop and visual review. Claude Code on atlas does the heavy lifting (filesystem persistence, longer tool budgets).

> **Run prompts A & B (and C, D) from `/home/nls/Documents/dev/mitma-sedona/`** — that is the canonical working directory. The previous `catalonia-livability-mobility/` folder was renamed in place, all schemas/tests/preview already live here.

### Prompt A — Setup & infra (M1)

```
You are setting up a portfolio repo at /home/nls/Documents/dev/mitma-sedona.
Read PLAN.md, src/catmob/, tests/, docs/preview_deck.html, and data/README.md
before doing anything — pandera schemas, parsers, 44 tests, and a deck.gl
preview are already in place.

Goal: M1 — `docker compose up` brings up JupyterLab on Sedona + a Valhalla
server with bike profile, both healthy, and the repo is on GitHub.

Steps:
1. Init git, write a short README.md (point to PLAN.md and to
   docs/preview_deck.html), MIT LICENSE, .gitignore excluding data/,
   .venv, .ipynb_checkpoints, .pytest_cache.
2. Write pyproject.toml using uv with FLOOR pins (>=, not strict ==):
   apache-sedona[spark]>=1.9, lonboard>=0.16, pydeck>=0.9, h3>=4, pyrosm,
   pyarrow>=17, geopandas>=1.0, pandera[pandas]>=0.20, pytest, ruff,
   jupytext, nbmake, ipywidgets, altair, matplotlib. Run `uv lock`.
3. Write docker/docker-compose.yml with services:
   - jupyter: from apache/sedona (latest), mount ./ as /workspace, port 8888
   - valhalla: from gisops/valhalla (latest), bike profile, fed cataluna PBF
   - shared volume for valhalla tiles
4. Write docker/Dockerfile.jupyter that adds the lonboard/pydeck/h3/pyrosm/
   pandera/pytest/ruff/jupytext/nbmake/ipywidgets/altair/matplotlib stack
   on top of apache/sedona. Try latest first; if anything fails to resolve
   apply the fallback floors in PLAN.md §3.
5. Write scripts/fetch_osm.sh — Geofabrik PBF + osmium tag-filter pre-prune
   driven by src/catmob/io_osm.py:OSMIUM_TAG_FILTER (read it, do not retype).
6. Write scripts/fetch_mitma.sh with --scope {full,dev}, default full.
   full = Q1+Q2 2024 daily + all March 2024 hourly.
   dev  = week 2024-03-04..10 daily + day 2024-03-06 hourly.
7. `gh auth status` — already green per Nelson. Create the repo public:
   `gh repo create lunasilvestre/mitma-sedona --public --source=. --push`.
8. `docker compose up -d`, verify both services healthy, commit, push.
9. Re-run `pytest -q` from inside the jupyter container.

Verify with a short report: services healthy, ports listening, first commit
SHA, repo URL, in-container pytest result.
```

### Prompt B — Data ingest pipeline (M2)

```
Repo: /home/nls/Documents/dev/mitma-sedona. PLAN.md is canonical.
Schemas (src/catmob/schemas.py) and stub I/O modules already define every
contract. **READ docs/sedona_sql_patterns.md** before writing any Spark/
Sedona code — it has 8 patterns adapted from Wherobots/Apache examples
(H3 cell-id explode, OD line construction, area-weighted disaggregation,
raster zonal stats, ST_KNN, broadcast+AQE hints, GeoArrow handoff,
MAX_BY peak-hour, DBSCAN clusters, GeoParquet 1.1, ST_BingTileAt,
RS_MapAlgebra, STAC reader). Use these patterns; don't reinvent.

Goal: M2 — notebook 01_data_ingest.ipynb runs green
end-to-end producing data/bronze/ at full scale by default.

Implement, in this order, with tests as you go:

1. Fill in src/catmob/io_mitma.py:read_with_sedona — SedonaContext-bound
   reader with sep=";", encoding="UTF-8", schema-applied, Catalonia filter.
2. Fill in src/catmob/io_osm.py:read_pbf_with_sedona — native osmpbf reader,
   tag-filter to POI / network categories.
3. Fill in src/catmob/io_gtfs.py — load Renfe Rodalies + FGC, compute
   trips_per_day per stop, join to nearest OSM railway=station within 200m.
4. Fill in src/catmob/io_air.py:parse_eea_csv and cams_grid_to_dataframe.
5. Fill in src/catmob/io_thermal.py — Planetary Computer STAC for
   Landsat L2 Coll-2, summer (JJA) composite of ST_B10 over 2024.
6. Fill in src/catmob/io_biodiversity.py — WDPA fetch + iNat via GBIF.
7. Fill in src/catmob/io_pollution.py — E-PRTR + VIIRS DNB.
8. Fill in src/catmob/io_health.py — CatSalut hospital registry.
9. Write notebooks/01_data_ingest.ipynb, default scope = full
   (Q1+Q2 2024 daily + all March 2024 hourly). Add a `--scope dev` switch
   (week 2024-03-04..10 daily + day 2024-03-06 hourly) for fast local iter.
10. Each section ends with row-count assertion + pandera validation.

Verify: pytest -q passes (44 existing + new integration tests), notebook
runs top-to-bottom under nbmake on dev scope, full-scale ingest completes
and writes ~3.5 GB of MITMA bronze + ~3.5 GB other sources, sizes match
PLAN.md §6 ±20%. Commit + push with a meaningful message.
```

### Prompt C — Liveability gold layer (M3)

```
Repo + PLAN.md as before. Schemas already define GOLD_HEX_SCHEMA with every
column. **READ docs/sedona_sql_patterns.md** — the H3 grid generation
(§1), area-weighted disaggregation (§3), raster zonal stats (§4), ST_KNN
nearest-station (§5), broadcast hints (§6), and GeoParquet 1.1 write (§8c)
are the exact patterns this notebook needs.

Goal: M3 — notebook 02_liveability_layer.ipynb green,
gold/h3_res8_catalonia.parquet exists matching the schema exactly.

Implement:
1. src/catmob/h3_utils.py — generate H3 res-8 grid covering Catalonia bbox,
   drop hexes whose centroid falls outside Catalonia boundary polygon
   (from OSM relation 349).
2. src/catmob/isochrones.py — Valhalla client wrapper, function
   bike_isochrone(lat, lon, minutes) returning a Sedona-compatible polygon.
   Cache to silver/isochrones/ keyed by (station_id, minutes).
3. src/catmob/scoring.py — weighted score function on a Pandas row, weights
   from configs/weights.yaml. Implement default, nature_first,
   quiet_strict, amenity_first presets.
4. src/catmob/stats.py — helpers used by notebook 04 (weekday aggregator,
   peak-hour finder, anomaly detector).
5. notebook 02 — runs the joins per PLAN.md §7, computes every gold column,
   writes Parquet, prints distributions + correlation matrix.
6. tests/test_geo_invariants.py — extend with CRS, bounds, no-null-geometry
   checks on every silver/gold table.
7. tests/test_scoring.py — fixture hex with hand-computed inputs.

Verify: pytest passes, gold parquet has expected row count (~50k ± 5%),
no nulls in critical columns, scoring sensitivity is sane.
```

### Prompt D — Visual storytelling + descriptives (M4) — *runnable in Cowork too*

```
Repo + PLAN.md. notebooks/preview_storyboard.md and docs/preview_deck.html
already show the planned look-and-feel. **READ docs/sedona_sql_patterns.md**
for the GeoArrow zero-copy handoff (§7 — pre-compute ST_StartPoint/ST_EndPoint
server-side), MAX_BY peak-hour bucketing (§8a), DBSCAN cluster cartograms
(§8b), and ST_BingTileAt coarse heatmap (§8d). Goal: M4 — notebooks 03 + 04 green,
docs/catalonia_liveability.html exported.

Notebook 03 (per PLAN.md §7):
1. Lonboard ArcLayer cell — all MITMA OD for one representative weekday
   (2024-03-06 Wed), GeoArrow handoff from Sedona.
2. pydeck H3HexagonLayer cell — extruded by score, ScatterplotLayer for
   POIs, ArcLayer for top-30 OD, ipywidgets toggles per constraint.
3. deck.gl raw HTML export — src/catmob/viz.py:export_deck_html. Result
   replaces docs/preview_deck.html as docs/catalonia_liveability.html.
4. Top-10 table + sensitivity Jaccard table.

Notebook 04 (descriptives, NEW in v1.1, see PLAN.md §7 #04):
Implement all 10 sections, ≥8 publication-quality charts saved as PNG to
docs/screenshots/.

Verify: nbconvert both notebooks to .md and .html with cell outputs preserved
(under notebooks/rendered/). Hosted HTML opens cleanly. Take 4–6 hand
screenshots into docs/screenshots/ for the README.
```

---

## 12. Setup needs from Nelson

| # | Need | Status |
|---|---|---|
| 1 | `gh auth status` green on atlas | ✅ confirmed (gho_…, ssh, repo + public_key + read:org + gist) |
| 2 | Basemap | CARTO dark-matter (free, no key) — locked in `docs/preview_deck.html` |
| 3 | Weight preset | Balanced default — locked |
| 4 | Repo name | `mitma-sedona` (under `lunasilvestre`) — locked |
| 5 | Data window | Default = **full-scale** (Q1+Q2 2024 daily + all March hourly); `--scope dev` for fast iteration |
| 6 | Version pinning | Floor-only (`>=`); fallback only if a resolve fails |

---

## 13. Risks & open questions

- **MITMA bucket layout drift** — paths verified by URL builders + tests; M2 prompt has a `curl -I` smoke check before pinning paths.
- **Valhalla isochrones at scale** — a few hundred Catalan stations × 2 isochrone bands, manageable; bike-profile config tuning may need iteration; budget 4 h.
- **Lonboard ↔ Sedona latest path** — confirmed working with 1.7.x; if 1.9 breaks, fall back per PLAN.md §3 playbook.
- **Yoga POI completeness in OSM** — uneven; documented as known limitation; Plan B = one-off Google Places scrape (out of v1).
- **Air quality interpolation uncertainty** — sparse stations + coarse CAMS → ±5 µg/m³; documented in notebook 03 caveats.
- **Landsat LST cloud contamination** — JJA composite mitigates; single-summer baseline is documented as a limitation.
- **iNaturalist observation bias** — cluster around population centres and trails; documented as a known limitation.
- **Full-scale download bandwidth** — ~3.5 GB MITMA gzipped + ~3 GB other sources. Atlas bandwidth + 738 GB free disk → no constraint.

---

## 14. Decision log

- 2026-05-14 — Reframed to personal liveability search.
- 2026-05-14 — H3 res 8 (~0.7 km² hexes) as analytical grain.
- 2026-05-14 — Repo named **`mitma-sedona`** (under `lunasilvestre`); local at `/home/nls/Documents/dev/mitma-sedona`.
- 2026-05-14 — Default data window = **full-scale** (Q1+Q2 2024 daily + all March hourly). `--scope dev` for fast local iteration only. Sedona on a one-month sample defeats the point of Spark.
- 2026-05-14 — **Floor pins** (`>=`) on all dependencies; downgrade fallback playbook documented.
- 2026-05-14 — Added dimensions to scoring: air quality (NO₂/PM₂.₅), urban heat island (Landsat LST), light pollution (VIIRS), biodiversity (WDPA + Natura 2000 + tree cover + iNat), industrial pollution (E-PRTR), health amenities (OSM + CatSalut).
- 2026-05-14 — Added notebook **04 — Descriptive statistics** with weekday averages, peak-hour profiles, anomaly detection, ≥8 charts.
- 2026-05-14 — Valhalla in Docker for bike isochrones (over OSRM/ORS).
- 2026-05-14 — GitHub Pages for the deck.gl hosted demo.
- 2026-05-14 — **Shipped at M0**: pandera schemas (13), MITMA/OSM/XVPCA parsers, 44 contract tests (0.23 s), fixtures, data catalog, deck.gl preview HTML.
- 2026-05-14 — Added `docs/sedona_sql_patterns.md` — 8 patterns adapted from Wherobots `wherobots-examples` + Apache Sedona use-cases, with concrete SQL using mitma-sedona column names. Patterns: H3 cell-ID explode + boundary clip, OD line via `ST_MakeLine(ST_Centroid)`, dasymetric distrito→hex disaggregation, `RS_ZonalStats` per-tile `sum`+`count` for pixel-weighted means, `ST_KNN` indexed nearest-neighbor + isochrone left-join, broadcast+AQE hints, `dataframe_to_arrow` zero-copy GeoArrow → Lonboard, plus 6 bonus patterns (`MAX_BY`, `ST_DBSCAN`, GeoParquet 1.1, `ST_BingTileAt`, `RS_MapAlgebra`, STAC reader). All 4 Claude Code prompts now reference this doc.
