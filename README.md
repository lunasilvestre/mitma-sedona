# mitma-sedona

[![tests](https://img.shields.io/badge/tests-44%20passing-green.svg)](docs/quickstart.md#tests)
[![python](https://img.shields.io/badge/python-3.11+-blue.svg)](#)
[![sedona](https://img.shields.io/badge/sedona-1.9+-orange.svg)](#)
[![spark](https://img.shields.io/badge/spark-4.1-orange.svg)](#)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[![Catalonia liveability geo-browser — 45,220 H3 res-8 hexes coloured by the
liveability index over Esri satellite imagery, with the dark analysis panel on
the left. Captured from docs/explore.html.](docs/screenshots/geobrowser_hero.png)](docs/explore.html)

> **Where in Catalonia could I live well?** Within bike-reach of a train station that connects to Barcelona, with climbing gyms and yoga nearby, close to green or sea, away from heavy industry and motorway noise — and breathing clean air, away from urban heat and light pollution, near biodiversity, with health amenities at hand.

A multi-criteria **liveability index over Catalonia**, computed at H3 res-8
(~0.7 km² hexes) with **Apache Sedona on Spark** for the spatial-join heavy
lifting. A real personal question, answered with real data engineering — and a
portfolio piece that shows the operational shape of a geospatial scoring
pipeline under open-data constraints.

> **▶ [Explore the interactive geo-browser →](docs/explore.html)** — a dark,
> satellite-backed map of the liveability index: 45,220 H3 res-8 hexes over
> keyless Esri World Imagery, with a preset selector, an analytic-metric
> recolour, a 2.5D extrude toggle, and MITMA OD arcs + OSM amenity inputs.
> Pure static (deck.gl + MapLibre + h3-js, zero build step), served from GitHub
> Pages. Source: [`docs/explore.html`](docs/explore.html) +
> [`docs/app/geobrowser-map.js`](docs/app/geobrowser-map.js).

> **Status — dev-scope prototype (2026-06).** A thin, honest end-to-end slice:
> the Sedona/Spark pipeline runs on **real Catalonia data** (7-day dev window,
> 2024-03-04..10) and emits `data/gold/h3_res8_catalonia.parquet` (45,220 hexes)
> plus the geo-browser. The score is **structurally complete but empirically
> thin** — 12 of 24 feature columns carry real signal today; the rest are wired
> with weights and return as v2 work. Scope boundaries: liveability is a
> *relative index*, not a guarantee; coverage is sparse in places; mobility uses
> buffer approximations, not routed isochrones yet. See
> [`docs/v2_revision.md`](docs/v2_revision.md).

## Why this exists

National "best places to live" rankings are coarse, opinion-weighted, and
city-level. This project answers a *personal* question at hex granularity:
**where could I, specifically, live well in Catalonia** — trading off mobility
to Barcelona, lifestyle amenities, nature, environmental health, and
penalties (industry, motorway noise, urban heat) — with every dimension sourced
from open data and every weight legible in five lines of YAML. The point is
auditability and honesty about coverage, not a glossy verdict.

## Pipeline

```mermaid
flowchart TD
    subgraph sources["Open data sources"]
        MITMA["MITMA v2 OD<br>(daily + hourly)"]
        OSM["OSM Cataluña<br>(POIs · network · stations)"]
        ENV["Environmental<br>(EEA/CAMS air · Landsat LST ·<br>WDPA · iNaturalist · VIIRS · E-PRTR)"]
    end

    MITMA -->|"io_mitma → Sedona ingest"| BRONZE["Bronze<br>(raw parquet, pandera-validated)"]
    OSM -->|"io_osm / io_gtfs"| BRONZE
    ENV -->|"io_air / io_thermal / io_biodiversity / …"| BRONZE

    BRONZE -->|"clean · conform · make_valid"| SILVER["Silver<br>(geometry-validated layers)"]
    SILVER -->|"H3 res-8 explode + spatial joins<br>(Sedona SQL)"| GOLD["Gold<br>h3_res8_catalonia.parquet<br>45,220 hexes × features"]

    GOLD -->|"catmob.scoring.score_hex<br>(configs/weights.yaml, 4 presets)"| SCORE["liveability_score<br>per hex × preset"]
    SCORE --> BROWSER["Geo-browser<br>(docs/explore.html)"]
    SCORE --> DECK["Standalone deck.gl page<br>(catalonia_liveability.html)"]
```

Bronze → Silver → Gold lakehouse, H3 res-8 as the analytical grain, Sedona for
every spatial join. Detail: [`docs/architecture.md`](docs/architecture.md) ·
[`docs/sedona_sql_patterns.md`](docs/sedona_sql_patterns.md).

## The liveability score

A per-hex weighted sum over ~25 features across **6 dimensions**, computed at
H3 res-8. It is a **relative index** — a starting question, not a guarantee.
NULL features (sparse coverage) are kept as a distinct *"none within reach"*
state, never silently rendered as 0.

| Dimension | Sources | Status |
|---|---|:--:|
| Mobility & accessibility | Bike reach from train stations, Renfe + FGC GTFS frequency to BCN | **LIVE** (buffer approx.) / v2 Valhalla isochrones |
| Lifestyle | OSM `sport=climbing`, `sport=yoga` | **LIVE** |
| Mobility "vibe" | MITMA daily OD inflow / outflow, through-flow ratio | **LIVE** |
| Penalties | OSM `landuse=industrial`, E-PRTR registry, motorway proximity | **LIVE** (industry + motorway) / v2 E-PRTR |
| Health amenities | OSM hospitals / pharmacies, CatSalut registry | **LIVE** (hospital) / v2 pharmacy density |
| Nature | OSM parks / forest / coastline, Copernicus tree cover, WDPA / Natura 2000, iNaturalist | v2-PLANNED |
| Environmental health | EEA + XVPCA + CAMS NO₂/PM₂.₅, Landsat LST urban-heat Δ, VIIRS light pollution | v2-PLANNED |

Default weights are balanced across the six dimensions; three presets re-weight
them — **`nature_first`** (green/sea + biodiversity), **`quiet_strict`**
(harder noise/industry penalty), **`amenity_first`** (health + lifestyle). The
scoring function is `catmob.scoring.score_hex`, driven by
[`configs/weights.yaml`](configs/weights.yaml). Because every planned column
already has a weight key (a NULL contributes `0`), the index is structurally
complete and populating v2 columns is pure upside, no scoring refactor. Full
methodology: [`docs/scoring.md`](docs/scoring.md).

## Data sources

All open, all citable. Condensed table below; full catalog + licences in
[`docs/data_sources.md`](docs/data_sources.md) and
[`data/README.md`](data/README.md).

| Source | What | Licence |
|---|---|---|
| MITMA v2 OD distritos (daily + hourly) | mobility flows | MITMA Open Data ≈ CC BY 4.0 |
| OSM Cataluña PBF | POIs + network + boundaries | ODbL |
| Renfe Rodalies + FGC GTFS | train frequencies | open |
| EEA + XVPCA + Copernicus CAMS | air quality | CC BY 2.5 / 4.0 / Copernicus |
| Landsat 8/9 LST (MS Planetary Computer STAC) | urban heat island | open |
| WDPA / Natura 2000 + Copernicus TCD + iNaturalist (GBIF) | biodiversity | CC BY 4.0 / CC BY-NC |
| E-PRTR + VIIRS DNB | non-air pollution | CC BY 2.5 / open |
| CatSalut hospital registry | health amenities | CC BY 4.0 |

**Default data window:** Q1+Q2 2024 daily MITMA + all March 2024 hourly MITMA
(~3.5 GB bronze). `--scope dev` uses the 7-day window (2024-03-04..10) the
prototype run was built on.

## Stack & architecture

| Concern | Choice | Why |
|---|---|---|
| Spatial joins at scale | **Apache Sedona 1.9 on Spark 4.1** | 27 M-row MITMA aggregation + H3 explode in SQL |
| Analytical grain | **H3 res-8** (~0.7 km² hexes) | uniform, hierarchical, join-friendly |
| Lakehouse | **Bronze → Silver → Gold** parquet, pandera contracts on write | provenance + validation |
| Library | `src/catmob/` (schemas, io, scoring, viz) | reusable, testable, notebook-agnostic |
| Visualisation | **deck.gl 9.3 + MapLibre GL 4.7 + h3-js**, keyless, zero build | static GitHub Pages, no backend |

Sedona handles every spatial join; the v1 gold layer at this data size actually
runs faster as plain pandas + geopandas + h3-py (a classloader-mismatch on the
Sedona spatial-index serde — see the retrospective), while bronze + the 27 M-row
MITMA aggregation stay on Sedona where it pays off. Repo layout +
lakehouse design: [`docs/architecture.md`](docs/architecture.md). The eight SQL
idioms used (H3 cell-id explode, dasymetric disaggregation, `RS_ZonalStats`,
`ST_KNN`, `BROADCAST` hints, GeoArrow zero-copy, `MAX_BY` peak-hour, `ST_DBSCAN`):
[`docs/sedona_sql_patterns.md`](docs/sedona_sql_patterns.md).

## Results (dev-scope prototype)

The first end-to-end Sedona run on real Catalonia data (7-day dev window,
~5 min pipeline time) produced:

- **`data/gold/h3_res8_catalonia.parquet`** — **45,220 hexes × 12 features**, 1.6 MB.
- 27.7 M MITMA OD rows ingested, 4,935 OSM POIs, 475 stations, 364,530 highway ways.
- Score distribution across all hexes: min 0 · median 50 · mean 40.7 · max 64.0 · stdev 14.2.

**Honest caveats.** The default-weights top-10 is dominated by small inland
towns in Girona / Lleida that hit the max (64.0) because their negative
penalties don't apply *and* their climbing / yoga / hospital distances are NULL
(beyond the cap) — a relative index will rank thin coverage optimistically.
This is **7 days of March 2024**, mobility uses **buffer approximations** (5 km /
3 km Euclidean circles ≈ 25 / 15 min by bike, not Valhalla isochrones), and the
score is a **relative index, not a guarantee**. Full numbers, the top-10 table,
and the retrospective ("what tripped us / how we fixed it"):
[`docs/results.md`](docs/results.md).

## Repository layout

```
mitma-sedona/
├── README.md                       # this file
├── PLAN.md                         # canonical planning doc + milestones
├── configs/weights.yaml            # liveability scoring weights (4 presets)
├── src/catmob/                     # reusable library
│   ├── schemas.py                  # pandera contracts (GOLD_HEX_SCHEMA, …)
│   ├── io_*.py                     # source readers (mitma, osm, gtfs, air, thermal, …)
│   ├── scoring.py                  # score_hex + score_dataframe
│   └── viz.py                      # deck.gl HTML export
├── notebooks/                      # 01_ingest → 02_liveability → 03_viz + 04_descriptives
├── tests/                          # 44 contract tests (pytest -q)
├── data/                           # bronze / silver / gold (+ data/README.md catalog)
├── docker/                         # Sedona + Valhalla + Jupyter compose stack
├── scripts/fetch_*.sh              # idempotent data fetchers
└── docs/
    ├── explore.html                # the interactive geo-browser (GitHub Pages star)
    ├── app/geobrowser-map.js       # geo-browser logic (+ explore.css)
    ├── catalonia_liveability.html  # classic self-contained deck.gl page (superseded)
    ├── preview_deck.html           # synthetic-data preview, no backend
    ├── story_data/                 # geo-browser data bundle (hexes/arcs/pois/manifest)
    └── *.md                        # the six deeper reference docs (see below)
```

## Quickstart & tests

Two paths — a 5-min tests-only path (no Docker) and the full Docker stack. Full
recipe: [`docs/quickstart.md`](docs/quickstart.md).

```bash
git clone git@github.com:lunasilvestre/mitma-sedona.git && cd mitma-sedona
python3 -m venv .venv && source .venv/bin/activate
pip install 'pandera[pandas]>=0.20' pytest pandas
PYTHONPATH=src pytest -q            # → 44 passed in ~0.2s
xdg-open docs/preview_deck.html     # standalone deck.gl preview (no backend)
```

For the real Sedona/Spark pipeline:
`docker compose -f docker/docker-compose.yml up -d` (JupyterLab on :8888,
Valhalla on :8002). The 44 contract tests cover schema enforcement, MITMA
CSV.gz parsing, OSM POI categorisation, XVPCA air-quality parsing, geo
invariants, and URL builders — CI runs them on every push.

## v2 roadmap

v1 shipped the runtime and a real (if thin) score: **12 of 24 feature columns
carry real signal**, mobility is buffer-approximated, and 12 environmental /
nature / amenity columns are wired but NULL. v2 turns each NULL/shortcut into a
source-backed column (EEA/CAMS air, Landsat/VIIRS STAC, WDPA, iNaturalist,
E-PRTR, Copernicus TCD) plus Valhalla bike isochrones and GTFS frequency, reusing
the documented Sedona patterns — no scoring refactor. The full feature-by-feature
plan with effort/impact estimates: [`docs/v2_revision.md`](docs/v2_revision.md).

## Deeper docs

| Doc | What's in it |
|---|---|
| [docs/quickstart.md](docs/quickstart.md) | Run it in 5 min (tests + preview) or 10 min (full Docker stack) |
| [docs/architecture.md](docs/architecture.md) | Repo layout + Bronze→Silver→Gold lakehouse + Sedona SQL idioms |
| [docs/scoring.md](docs/scoring.md) | The liveability score: 6 dimensions, weights, and the four presets |
| [docs/data_sources.md](docs/data_sources.md) | Every upstream source + licence + the default data window |
| [docs/visualization.md](docs/visualization.md) | The deck.gl / Lonboard stack and the explore.html geo-browser |
| [docs/results.md](docs/results.md) | Prototype artifacts, Top-10, score distribution, and the retrospective |
| [docs/sedona_sql_patterns.md](docs/sedona_sql_patterns.md) | 8 advanced Sedona SQL patterns (H3 explode, dasymetric, `RS_ZonalStats`, …) |
| [docs/v2_revision.md](docs/v2_revision.md) | Path from dev-scope prototype to a complete, defensible index |
| [PLAN.md](PLAN.md) | Canonical planning doc + full milestone breakdown |

## Attribution

- *Datos de movilidad: Ministerio de Transportes y Movilidad Sostenible (MITMS)*
- *© OpenStreetMap contributors, ODbL*
- *© European Environment Agency (EEA)*
- *Generated using Copernicus data and information funded by the European Union — Copernicus Climate Change Service / Atmosphere Monitoring Service*
- *iNaturalist (via GBIF) — CC BY-NC*
- *Generalitat de Catalunya — analisi.transparenciacatalunya.cat*

## Licence

[MIT](LICENSE) — code only. Each upstream dataset retains its own licence; see
the attribution block above and [docs/data_sources.md](docs/data_sources.md).

---

Built by [@lunasilvestre](https://github.com/lunasilvestre) with Claude Code.
