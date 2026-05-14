# mitma-sedona

[![tests](https://img.shields.io/badge/tests-44%20passing-green.svg)](#tests)
[![python](https://img.shields.io/badge/python-3.11+-blue.svg)](#)
[![sedona](https://img.shields.io/badge/sedona-1.9+-orange.svg)](#)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **Where in Catalonia could I live well?** Within bike-reach of a train station that connects to Barcelona, with climbing gyms and yoga nearby, close to green or sea, away from heavy industry and motorway noise — and breathing clean air, away from urban heat and light pollution, near biodiversity, with health amenities at hand.

A multi-criteria liveability index over Catalonia, computed at H3 res-8
(~0.7 km² hexes). Built with **Apache Sedona on Spark** for the
spatial-join heavy lifting, **MITMA v2** mobility flows for the
"is-this-area-actually-connected" signal, **OpenStreetMap** for POIs and
network, **EEA + XVPCA + Copernicus** for environmental health, and
**Lonboard / pydeck / deck.gl** for the visualisations.

This is also a portfolio piece: a real personal question, answered with
real data engineering.

## Try it (5 min, no Docker)

```bash
git clone git@github.com:lunasilvestre/mitma-sedona.git
cd mitma-sedona
python3 -m venv .venv && source .venv/bin/activate
pip install 'pandera[pandas]>=0.20' pytest pandas
PYTHONPATH=src pytest -q   # → 44 passed in 0.23s
xdg-open docs/preview_deck.html  # standalone deck.gl preview
```

## Try it (10 min, full Docker stack)

```bash
git clone git@github.com:lunasilvestre/mitma-sedona.git
cd mitma-sedona
docker compose -f docker/docker-compose.yml up -d
# → JupyterLab on http://localhost:8888 (token printed in logs)
# → Valhalla on   http://localhost:8002
```

## What's inside

```
mitma-sedona/
├── PLAN.md                        canonical planning doc
├── docs/
│   ├── preview_deck.html          standalone deck.gl preview (open in browser)
│   └── sedona_sql_patterns.md     8 advanced SQL patterns from Wherobots/Apache
├── src/catmob/                    reusable library (schemas, io, scoring, viz)
├── notebooks/                     01_ingest → 02_liveability → 03_viz + 04_descriptives
├── tests/                         44 contract tests (pytest -q)
├── data/README.md                 dataset catalog (every source linked)
├── docker/                        Sedona + Valhalla + Jupyter compose stack
├── configs/weights.yaml           liveability scoring weight presets
└── scripts/fetch_*.sh             idempotent data fetchers
```

## The liveability score

Per-hex weighted sum over ~25 features in 6 dimensions:

| Dimension | Sources |
|---|---|
| Mobility & accessibility | Valhalla bike isochrones from train stations, Renfe + FGC GTFS frequency to BCN |
| Lifestyle | OSM `sport=climbing`, `sport=yoga` |
| Nature | OSM parks/forest/coastline, Copernicus tree cover, WDPA / Natura 2000, iNaturalist density |
| Environmental health | EEA + XVPCA + CAMS NO₂/PM₂.₅, Landsat 8/9 LST UHI Δ, VIIRS DNB light pollution |
| Penalties | OSM `landuse=industrial`, E-PRTR registry, motorway proximity |
| Health amenities | OSM hospitals/pharmacies, CatSalut registry |
| Mobility "vibe" | MITMA daily OD inflow/outflow, through-flow ratio |

Full constraint table: [PLAN.md §2](PLAN.md). Default weights are
balanced; presets (`nature_first`, `quiet_strict`, `amenity_first`) and
sensitivity analysis live in `configs/weights.yaml` and notebook 03.

## Visualisation stack

| Layer | Library | Why |
|---|---|---|
| Million-arc raw flow | **Lonboard** (GeoArrow handoff from Sedona) | browser-native at scale |
| Interactive constraint toggles | **pydeck** + ipywidgets | python state ↔ js layers |
| Hosted 3D demo | **deck.gl** raw HTML | zero install for viewers |
| H3 score (extruded) | `H3HexagonLayer` | score = height + colour |
| MITMA OD | `ArcLayer` (curvature + colour gradient) | direction encoded |
| POIs | `ScatterplotLayer` | standard, fast |
| Aggregated flows | `LineLayer` | cleaner at low zoom |

Open `docs/preview_deck.html` in any browser for the synthetic-data
preview; `docs/catalonia_liveability.html` (built by notebook 03) is the
production version.

## Architecture

Bronze → Silver → Gold lakehouse, with H3 res-8 hex grid as the
analytical grain. Sedona handles every spatial join. Pandera validates
every bronze write. See [PLAN.md §4](PLAN.md) and
[docs/sedona_sql_patterns.md](docs/sedona_sql_patterns.md) for the
specific SQL idioms (H3 cell-id explode, area-weighted disaggregation,
raster zonal stats, ST_KNN, broadcast hints, GeoArrow handoff).

## Data

| Source | What | Licence |
|---|---|---|
| MITMA v2 OD distritos (daily + hourly) | mobility flows | MITMA Open Data ≈ CC BY 4.0 |
| OSM Cataluña PBF | POIs + network + boundaries | ODbL |
| Renfe Rodalies + FGC GTFS | train frequencies | open |
| EEA + XVPCA + Copernicus CAMS | air quality | CC BY 2.5 / CC BY 4.0 / Copernicus |
| Landsat 8/9 LST (Microsoft Planetary Computer STAC) | urban heat island | open |
| WDPA / Natura 2000 + Copernicus TCD + iNaturalist (GBIF) | biodiversity | CC BY 4.0 / CC BY-NC |
| E-PRTR + VIIRS DNB | non-air pollution | CC BY 2.5 / open |
| CatSalut hospital registry | health amenities | CC BY 4.0 |

Full catalog: [data/README.md](data/README.md). Default data window =
**Q1+Q2 2024 daily MITMA + all March 2024 hourly MITMA** (full-scale,
~3.5 GB MITMA bronze). `--scope dev` for fast local iteration.

## Tests

```bash
PYTHONPATH=src pytest -q       # → 44 passed in 0.23s
PYTHONPATH=src pytest -v       # full output
```

44 contract tests covering schema enforcement, MITMA CSV.gz parsing
(gzip + UTF-8 + semicolon + zone-ID padding), OSM POI categorisation,
XVPCA air-quality parsing, geo invariants, and URL builders. CI runs
them on every push.

## Status

| Milestone | Status |
|---|---|
| M0 — Plan + schemas + tests + preview HTML | ✅ |
| M1 — Repo bootstrap + Docker stack + GitHub | in progress |
| M2 — Bronze layer for all 8 source families | next |
| M3 — Silver + Gold (50k hexes, 25 features) | |
| M4 — Notebooks 03 + 04 + hosted HTML demo | |
| M5 — README + screenshots + GitHub Pages | |

See [PLAN.md](PLAN.md) for the full milestone breakdown and Claude Code
prompts for each lane.

## Attribution

- *Datos de movilidad: Ministerio de Transportes y Movilidad Sostenible (MITMS)*
- *© OpenStreetMap contributors, ODbL*
- *© European Environment Agency (EEA)*
- *Generated using Copernicus data and information funded by the European Union — Copernicus Climate Change Service / Atmosphere Monitoring Service*
- *iNaturalist (via GBIF) — CC BY-NC*
- *Generalitat de Catalunya — analisi.transparenciacatalunya.cat*

## Licence

[MIT](LICENSE) — code only. Each upstream dataset retains its own
licence; see the attribution block above.

---

Built by [@lunasilvestre](https://github.com/lunasilvestre) on atlas
with Cowork + Claude Code.
