# mitma-sedona — Catalonia Liveability × Mobility Planning Doc

**Status:** v2.3 (corrected + enriched index — SHIPPED LIVE) · **Date:** 2026-06-16 · **Author:** Nelson + Claude (atlas + Cowork)
> **Live:** [`explore.html`](https://lunasilvestre.github.io/mitma-sedona/explore.html) — a real Catalonia liveability index over 45,220 H3 res-8 hexes, all 24 feature dimensions real-data-wired, amenity terms as saturating closeness rewards, distances in EPSG:25831 metres. This plan is kept as the project's running narrative; the milestone table (§10) and §15 carry the up-to-date status. Earlier sections are historical design notes — they are not all rewritten to v2.3 phrasing, but the milestones are the source of truth for what shipped.

> **The real question** — *Where in Catalonia could I live well? Within bike-reach of a train station that connects to Barcelona, with climbing gyms and yoga nearby, close to green or sea, away from heavy industry and motorway noise — and now also: clean air, low urban heat, low light pollution, near biodiversity, with health amenities at hand.*

Started as "regional mobility analysis with Sedona". Reframed as a personal liveability search. Expanded to environmental health and biodiversity. Scaled up to full-scale by default. Now (v1.3) we have a clear path to a **working prototype** — see §15.

**Repo layout:** local at `/home/nls/Documents/dev/mitma-sedona`, GitHub target `lunasilvestre/mitma-sedona` (public). Pages live at `https://lunasilvestre.github.io/mitma-sedona/`. CI green on Python 3.11 + 3.12.

---

## 15. Breakthrough — what's actually missing for a working prototype

> **Status (2026-06-16): DONE, and superseded by v2.3.** The working prototype shipped (M6+M7+M8), and the v2 enrichment in [`docs/v2_revision.md`](docs/v2_revision.md) / [`docs/v2_build_playbook.md`](docs/v2_build_playbook.md) is now **executed and live** at [explore.html](https://lunasilvestre.github.io/mitma-sedona/explore.html). The narrative below is the original breakthrough analysis, kept for the record; where it talks about "circular-buffer fallbacks" and "12 of 24 columns", read it as the v1 starting point — v2.3 fixed the scoring (saturating closeness rewards, EPSG:25831 metres) and wired all 24 dimensions to real data. Only Valhalla bike isochrones (M14) remain optional.

We're closer than the open-issues list makes it look. The repo already has:
the data on disk (1.4 GB MITMA + 251 MB OSM PBF + 25 MB MITMA zoning incl. 60 MB GeoJSON), every parser implementation, every schema, the scoring function, the deck.gl HTML exporter, and 44 green tests. The **only** thing missing for "I can show this to a recruiter and click around real Catalonia data" is **a Spark+Sedona runtime that can execute notebook 01 once**.

### Local environment: Miniforge + conda env `sedona` (executed 2026-05-14)

Docker compose builds have failed four times (PEP 668 numpy uninstall, GHCR auth for Valhalla, pyrobuf/setuptools breakage). **Skip the container for v1.** The working local path is **Miniforge3 + a conda env on Python 3.11** — conda-forge supplies the GDAL/PROJ/GEOS/Java/pyrosm stack, pip layers `apache-sedona[spark]` and the viz packages on top. ~3 min to provision, all 44 tests green, PySpark 4.1.1 + Sedona 1.9 + OpenJDK 21 ready for M6 notebook execution. Python 3.11 is forced by `pyrosm` (no py3.12 binaries; pip build is broken upstream); `pyproject.toml` already allows it. Full recipe, version table, and Docker-vs-local guidance: [`docs/local_env_setup.md`](docs/local_env_setup.md). Docker remains the "reproducibility for outside contributors" lane, not the daily-dev path.

### What "working prototype" means concretely

1. `data/gold/h3_res8_catalonia.parquet` exists, ~50k hexes, all 25 columns populated (some from real data, some from sensible fallbacks where a source is deferred).
2. `docs/catalonia_liveability.html` shows real Catalonia hexes coloured by liveability, real MITMA OD arcs from BCN, real climbing/yoga POIs from OSM. Replaces (or sits alongside) `docs/preview_deck.html`.
3. `docs/screenshots/{01..08}.png` — 8 publication-quality charts from notebook 04 (weekday vs weekend, hourly heatmap, per-province, anomaly days, etc.).
4. README updated with embedded screenshots + a "what I learned" paragraph.
5. Top-10 hex ranking printed in notebook 03, with municipality names; the actual answer to the original question.

### Why we ship the prototype with circular-buffer fallbacks (not Valhalla)

Valhalla bike isochrones are a *quality* improvement on the train-reach feature. The fallback — a 5 km Euclidean buffer around each train station, weighted by service frequency — is 80% as informative for 5% of the work. Ship the prototype with circular buffers; document Valhalla as "v2 — replace bike-reach feature with real road-network isochrones." This is a portfolio piece, not a contract; clarity about the simplification beats a missing demo.

### v2 revision plan → [`docs/v2_revision.md`](docs/v2_revision.md)

> **Turn-key execution HOW** → [`docs/v2_build_playbook.md`](docs/v2_build_playbook.md): per-feature acquisition command, exact code change, gold column + weight key, validation check, on-disk/fetch/gated tag, the data-readiness matrix, and the recommended sequential build order (do the EPSG:25831 fix first).

> **UPDATE (v2.3, shipped live):** the "12 of 24 columns / structurally complete but empirically thin" framing below is now **historical** — it describes the v1 dev-scope prototype. The [`docs/v2_revision.md`](docs/v2_revision.md) plan has since been **executed**: all 24 feature dimensions are real-data-wired, the amenity terms are saturating closeness rewards (the v1 flaw where "no amenity" out-scored "amenity but far" is fixed), and every distance is computed in EPSG:25831 metres (the v1 degree-buffer anisotropy is fixed). Only the Valhalla bike isochrones (M14) remain optional/deferred. See `scripts/run_gold_v2.py` + `docs/story_data/manifest.json` for the live coverage.

The v1 dev-scope prototype populated **12 of 24** gold feature columns with real signal (9 real, 3 shortcut: circular-buffer `train_reach_min` and constant-12 GTFS); the other 12 were NULL — schema slot and active scoring weight existed, but notebook 02 never filled them, so the scoring index was structurally complete but empirically thin (each NULL was `weight × 0`). [`docs/v2_revision.md`](docs/v2_revision.md) was the plan — now largely executed — to turn every NULL/shortcut into a source-backed column with no scoring refactor: a per-feature summary table, sections grouped by Mobility/Valhalla, environmental health (air + thermal), nature & biodiversity, amenities & health, and pollution & light — each item wired to its `io_*` module, notebook step, gold column, weight key, and the matching `docs/sedona_sql_patterns.md` idiom. It ends with a value÷effort roadmap (Wave 1 quick wins: OSM green/sea/pharmacy distances, GBIF biodiversity, GTFS frequency, E-PRTR; Wave 2 STAC rasters: TCD, WDPA, air, VIIRS, Landsat LST → UHI; Wave 3: the Valhalla isochrone service) and a dependencies/risks section (EPSG:25831 reprojection first, Valhalla tile build, Planetary Computer STAC, GTFS feed availability, manual-download EEA/WDPA/E-PRTR, the Spark 4.1 × Sedona 1.9 classloader/index constraints).

---

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

## 10. Milestones (revised v2.3 — corrected + enriched index shipped live)

| # | Milestone | Status |
|---|---|---|
| **M0** | Plan + schemas + tests + preview HTML + data catalog | ✅ shipped |
| **M1** | Repo skeleton + GitHub repo created + CI green + Pages live | ✅ shipped (`https://lunasilvestre.github.io/mitma-sedona/`) |
| **M2** | Bronze layer parsers + STAC fetchers (real Sedona path for MITMA + OSM + GTFS, stubs filled for thermal/biodiversity/pollution/health) | ✅ shipped |
| **M3** | h3_utils + scoring + isochrones + viz library code | ✅ shipped |
| **M4** | 4 jupytext notebooks (01-04) | ✅ shipped (not yet executed on real data) |
| **M5** | Push to GitHub + Pages auto-deploy | ✅ shipped |
| **M6** | **Spark stack on atlas — first real Sedona run on dev-scope MITMA** | ✅ shipped (commit `3e5e424`) |
| **M7** | **Real gold parquet (45,220 hexes) from dev-scope** | ✅ shipped — `data/gold/h3_res8_catalonia_v2.parquet` |
| **M8** | **Real visualisations + geo-browser** (`docs/explore.html` + `docs/catalonia_liveability.html`) | ✅ shipped |
| **M9** | **README polish with embedded screenshots + commit + push** | ✅ shipped |
| M10 | GTFS fetcher fix + scripts/fetch_all.sh orchestrator | Prompt β (parallel) |
| M11 | Notebook execution Makefile + jupytext sync hook + nbmake CI | Prompt γ (parallel) |
| M12 | Docker container for outside reproducibility | deferred (v1.1) |
| **M13** | **v2.3 corrected + enriched index — SHIPPED LIVE** (saturating closeness rewards replace the v1 amenity distance-penalty flaw; all distances in EPSG:25831 metres; all 24 feature dimensions real-data-wired) | ✅ shipped — live at [explore.html](https://lunasilvestre.github.io/mitma-sedona/explore.html) |
| M14 | Valhalla bike isochrones (refines the train-reach term over the metric circular buffer) | deferred (only remaining v2 item; `VALHALLA_URL`-gated, off critical path) |
| M15 | Full-scale run (Q1+Q2 2024 daily + all March hourly) | deferred (after dev-scope works) |
| M16 | OB1 / Wiki capture, decision log, blog post | Cowork (later) |

---

## 11. Agent delegation matrix + Claude Code prompts

Cowork keeps the strategic loop and visual review. Claude Code on atlas does the heavy lifting (filesystem persistence, longer tool budgets).

> **Run prompts A & B (and C, D) from `/home/nls/Documents/dev/mitma-sedona/`** — that is the canonical working directory. The previous `catalonia-livability-mobility/` folder was renamed in place, all schemas/tests/preview already live here.

### Prompt α — **WORKING PROTOTYPE on atlas (M6+M7+M8)** ⭐ critical path

```
Repo: /home/nls/Documents/dev/mitma-sedona
You are getting this project to a WORKING PROTOTYPE on real Catalonia data.
Read PLAN.md §0, §15, and the file tree before doing anything; the schemas,
parsers, scoring, viz helpers, and 4 jupytext notebooks are all in place
and 44 contract tests pass. The data is on disk:
  - data/bronze/mitma/daily/2024-03/  → 7 days × ~190 MB CSV.gz
  - data/bronze/mitma/zones/          → SHP + 60 MB GeoJSON in EPSG:4326
  - data/bronze/osm/cataluna-latest.osm.pbf → 251 MB

Goal: end the session with these committed + pushed:
  - data/gold/h3_res8_catalonia.parquet (sample committed via Git LFS or
    a 5k-hex sample if too big)
  - docs/catalonia_liveability.html (real data, replaces preview at /demo)
  - docs/screenshots/{01..08}.png (notebook 04 charts)
  - notebooks/rendered/0{1,2,3,4}.{ipynb,md} via nbconvert
  - README.md updated with embedded screenshots + a "results" section

Steps (skip Docker entirely for v1 per PLAN.md §15):

1. SPARK STACK on atlas (~3 min)
   python3 -m venv .venv-spark
   source .venv-spark/bin/activate
   pip install --upgrade pip wheel
   pip install 'pyspark>=3.5,<5' 'apache-sedona[spark]>=1.9' \
       'pandera[pandas]>=0.20' 'pandas>=2.2' 'numpy>=1.26' 'pyarrow>=17' \
       'geopandas>=1.0' 'shapely>=2.0' 'h3>=4' \
       'lonboard>=0.16' 'pydeck>=0.9' 'matplotlib>=3.8' 'altair>=5.3' \
       'pystac-client>=0.7' 'planetary-computer>=1.0' 'rasterio>=1.3' \
       'xarray>=2024.1' 'rioxarray>=0.15' 'rasterstats>=0.19' \
       'pyrosm>=0.6' 'requests>=2.31' 'pyyaml>=6.0' 'ipywidgets>=8.1' \
       'jupytext>=1.16' 'jupyterlab>=4.2' 'ipykernel>=6.29' 'nbmake>=1.5'
   # Java check — Sedona needs JDK 11+. If missing: sudo apt install -y openjdk-17-jre-headless
   java -version || sudo apt-get install -y openjdk-17-jre-headless
   # Smoke test SedonaContext
   PYTHONPATH=src python -c "from sedona.spark import SedonaContext; \
       cfg = SedonaContext.builder().appName('smoke').getOrCreate(); \
       s = SedonaContext.create(cfg); print('Sedona up:', s.sql('SELECT ST_AsText(ST_Point(1,2))').collect())"

2. NOTEBOOK 01 — bronze ingest on dev scope (~10 min)
   MITMA_SCOPE=dev jupytext --to ipynb notebooks/01_data_ingest.py
   PYTHONPATH=src jupyter nbconvert --to notebook --execute \
       notebooks/01_data_ingest.ipynb \
       --output rendered/01_data_ingest.ipynb \
       --ExecutePreprocessor.timeout=900
   # Expected: data/bronze/mitma_parquet/{daily,hourly}/ + data/bronze/osm/{pois,network}.parquet

3. NOTEBOOK 02 — gold layer (~15 min)
   # Edit the Valhalla section to use the circular-buffer fallback:
   # for each station, generate a Shapely.Point.buffer(0.045)  # ~5km in degrees
   # then convert to a Sedona DataFrame as a synthetic isochrone polygon.
   # Skip the actual Valhalla call — env var VALHALLA_URL is intentionally absent.
   PYTHONPATH=src jupyter nbconvert --to notebook --execute \
       notebooks/02_liveability_layer.ipynb \
       --output rendered/02_liveability_layer.ipynb \
       --ExecutePreprocessor.timeout=1800
   # Expected: data/gold/h3_res8_catalonia.parquet (~50k hexes)

4. NOTEBOOK 03 — visualisations (~10 min)
   PYTHONPATH=src jupyter nbconvert --to notebook --execute \
       notebooks/03_score_and_visualise.ipynb \
       --output rendered/03_score_and_visualise.ipynb \
       --ExecutePreprocessor.timeout=900
   # Expected: docs/catalonia_liveability.html with REAL data

5. NOTEBOOK 04 — descriptives (~10 min)
   PYTHONPATH=src jupyter nbconvert --to notebook --execute \
       notebooks/04_descriptives.ipynb \
       --output rendered/04_descriptives.ipynb \
       --ExecutePreprocessor.timeout=900
   # Expected: docs/screenshots/{01..08}.png

6. SCREENSHOTS of the deck.gl HTML (~5 min) — use chromium-headless
   sudo apt-get install -y chromium
   chromium --headless --disable-gpu --no-sandbox --window-size=1600,900 \
       --screenshot=/tmp/livmap.png file://$(pwd)/docs/catalonia_liveability.html
   cp /tmp/livmap.png docs/screenshots/00_main_map.png

7. README.md — append a "Results" section with embedded screenshots,
   the top-10 hex table from notebook 03 (printed during execution),
   and a "what I learned" paragraph.

8. Commit + push:
   git add -A
   git commit -m "M6+M7+M8: working prototype — real gold layer + viz on dev scope"
   git push

Verify: report back with row counts (mitma_daily, pois, hexes), gold parquet
size on disk, screenshots inventory, and the HEAD commit SHA.

Constraints:
  - DO NOT use Docker. PEP 668 venv on host is faster + reliable.
  - DO NOT chase Valhalla. Circular buffer is fine for v1.
  - If a notebook cell fails, FIX IT (most likely a column-name drift
    or a missing GeoJSON properties path) and continue. Do not skip.
  - Write a NOTES_FROM_PROTOTYPE_RUN.md at repo root capturing any
    surprises so v2 can address them.
```

### Prompt β — GTFS fetcher fix + fetch_all.sh (M10) — *runs in parallel with α*

```
Repo: /home/nls/Documents/dev/mitma-sedona
Read PLAN.md §6 + §11 + scripts/fetch_*.sh before starting.

The GTFS fetcher in scripts/fetch_gtfs.sh fails because transitfeeds.com
URLs have moved. Goal: find the current open-data download URLs for
Renfe Cercanías Barcelona (Rodalies) and Ferrocarrils de la Generalitat
de Catalunya (FGC), update the fetcher, verify a download works.

Steps:
1. Probe candidate URLs:
   - https://api.transitfeeds.com/v1/getLatestFeedVersion?key=...&feed=renfe/505 (needs API key)
   - https://www.renfe.com/content/dam/renfe/es/General/Cercanias/horarios/cercanias_barcelona/google_transit.zip
   - https://transitland.org/feeds/f-sp78~renfe-cercanias-barcelona (S3 link)
   - https://www.fgc.cat/transit/google_transit.zip (or current FGC GTFS path)
   - Mobility Database catalog: https://api.mobilitydatabase.org/v1/feeds (free)
   For each, curl -sIL and report the HTTP code + Content-Type.

2. Update scripts/fetch_gtfs.sh with the working URLs. Add a --check flag
   (HEAD-only) like fetch_mitma.sh has.

3. Run scripts/fetch_gtfs.sh and verify both feeds land with stops.txt
   non-empty.

4. Run a smoke test:
   PYTHONPATH=src python -c "
   from catmob.io_gtfs import load_combined
   from pathlib import Path
   bundle = load_combined(Path('data/bronze/gtfs/rodalies'),
                          Path('data/bronze/gtfs/fgc'))
   print(f'stops: {len(bundle[\"stops\"])}, freq: {len(bundle[\"freq\"])}')
   print(bundle['freq'].sort_values('trips_to_bcn_core', ascending=False).head(10))
   "

5. Write scripts/fetch_all.sh — runs fetch_zoning.sh, fetch_mitma.sh,
   fetch_osm.sh, fetch_air.sh, fetch_gtfs.sh in order; reports total
   bronze size at the end. Idempotent (skips cached files).

6. Commit + push (do not touch any other files; α may be running concurrently).

If a candidate URL needs an API key, document it in NEXT_STEPS.md
under "GTFS — needs Mobility Database API token" and ship the script
with the env-var-driven version.
```

### Prompt γ — Notebook execution Makefile + jupytext sync hook (M11) — *parallel with α*

```
Repo: /home/nls/Documents/dev/mitma-sedona
Read pyproject.toml + notebooks/*.py + .github/workflows/ci.yml.

Goal: make running and re-rendering the 4 notebooks one-command, and
make the .py ↔ .ipynb stay in sync automatically.

Steps:
1. Write a Makefile at repo root with targets:
     make install        → pip install + jupytext config
     make test           → PYTHONPATH=src pytest -q
     make notebooks-dev  → execute all 4 notebooks at MITMA_SCOPE=dev
     make notebooks-full → execute all 4 notebooks at MITMA_SCOPE=full
     make sync           → jupytext --sync notebooks/*.py
     make clean-rendered → rm notebooks/rendered/
     make pages-preview  → python -m http.server -d docs 8000

2. Write .pre-commit-config.yaml with the jupytext --sync hook
   (https://jupytext.readthedocs.io/en/latest/using-pre-commit.html).
   So an .ipynb edit and a .py edit can never drift.

3. Add a new GitHub Actions job in .github/workflows/ci.yml:
     name: notebook-smoke
     runs-on: ubuntu-latest
     steps: ... pip install + nbmake on TINY fixtures (the existing
            tests/fixtures/ MITMA samples) — does NOT need the full bronze.
   Notebook 01 needs to be made resilient: if data/bronze/mitma/daily/ is
   empty, fall back to tests/fixtures/mitma_daily_sample.csv.gz so the
   nbmake CI step succeeds on a fresh clone with no real data.

4. Verify: `make test`, `make sync`, `make notebooks-dev` all pass on a
   freshly-cloned repo (use a /tmp/clone test).

5. Commit + push (do not touch src/catmob/ or notebooks/*.py beyond the
   fixture-fallback in 01).
```

---

### Prompt A — Setup & infra (M1) [historical, kept for reference]

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
