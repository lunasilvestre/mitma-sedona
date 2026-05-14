# NEXT_STEPS — pick up where Cowork left off

> **Last touched:** 2026-05-14, by Claude in Cowork unattended block. Local
> repo has 1 commit (`6427ae6`); GitHub repo not yet created (gh token in
> the DC shell was expired). SSH to github.com handshake is green.

## Right now (1 minute)

```bash
cd /home/nls/Documents/dev/mitma-sedona

# Refresh gh auth (or skip if your interactive shell already has a valid token)
gh auth status                            # if invalid:
gh auth refresh -h github.com -s repo,workflow,public_key,read:org

# Create + push in one command (remote 'origin' is already set to ssh)
gh repo create lunasilvestre/mitma-sedona --public --source=. --push

# Or if you prefer the web UI: create empty repo on github.com, then:
git push -u origin main
```

## Verify the build (3 minutes)

```bash
# Quick: just the unit tests (44, no Spark needed)
python3 -m venv .venv && source .venv/bin/activate
pip install 'pandera[pandas]>=0.20' pytest pandas pyyaml numpy h3 requests
PYTHONPATH=src pytest -q   # → 44 passed in 0.23s

# Full: bring up the Docker stack
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml logs jupyter | grep token  # → http://localhost:8888?token=mitma
docker compose -f docker/docker-compose.yml ps  # both services healthy
```

## Then (M2 ingest — 30 min dev / ~2 h full)

```bash
# Pull the data — default --scope full = Q1+Q2 2024 daily + all March hourly
scripts/fetch_zoning.sh        # MITMA distritos shapefile
scripts/fetch_mitma.sh         # ~3.5 GB if --scope full
scripts/fetch_osm.sh           # ~250 MB → ~50 MB after osmium prune
scripts/fetch_gtfs.sh          # ~10 MB
scripts/fetch_air.sh --year 2024

# Open notebook 01 inside Jupyter (jupytext auto-syncs .py ↔ .ipynb)
# http://localhost:8888 → notebooks/01_data_ingest.py → Run All
```

## What's already shipped

See `PLAN.md` §0 ("What's already shipped") and the commit message of
`6427ae6` for the full list. Highlights:

- **44 contract tests** green in 0.23 s (`PYTHONPATH=src pytest -q`)
- **Pandera schemas** for 13 datasets — `src/catmob/schemas.py`
- **Real Sedona implementations** of MITMA + OSM readers with the
  Wherobots-aligned SQL idioms (semicolon CSV with explicit schema,
  native osmpbf reader, POI category SQL CASE, area-weighted disaggregation,
  ST_KNN nearest-station, GeoArrow zero-copy handoff to Lonboard)
- **8 advanced Sedona SQL patterns** documented at `docs/sedona_sql_patterns.md`
- **4 jupytext notebooks**: 01 ingest, 02 gold layer, 03 viz, 04 descriptives
- **Docker stack**: Sedona Jupyter + Valhalla bike profile with healthcheck
- **5 fetch scripts**: idempotent, respect `--scope dev|full`
- **Preliminary deck.gl preview** at `docs/preview_deck.html` (synthetic data,
  full layer toggles + sliders + tooltips — opens in any browser)
- **GitHub Actions**: CI (pytest + ruff) + Pages (auto-deploy `docs/`)
- **MIT licence + README + .gitignore + pyproject.toml + weights.yaml**
- **PLAN.md v1.2** — canonical, full-scale by default, floor pins, 4 Claude Code prompts

## Still to wire (M2/M3 follow-ups)

The contracts are locked, the helpers are skeleton-ready. M2 prompt B in
`PLAN.md` §11 is self-contained for any of these:

- `io_thermal.py` — Planetary Computer STAC for Landsat L2 ST_B10
- `io_biodiversity.py` — WDPA fetch + iNaturalist via GBIF API
- `io_pollution.py` — E-PRTR registry + VIIRS DNB monthly composites
- `io_health.py` — CatSalut hospital registry CSV
- `io_air.py:parse_eea_csv` and `cams_grid_to_dataframe` — implementations
- The thermal/biodiversity/pollution joins in notebook 02 (the skeleton is
  there; follow the raster zonal stats pattern from `docs/sedona_sql_patterns.md` §4)

## Useful one-liners

```bash
# Render notebooks to executed .ipynb + .md
jupytext --to ipynb notebooks/*.py
jupyter nbconvert --to markdown --execute notebooks/01_data_ingest.ipynb

# Export deck.gl HTML from existing gold parquet
PYTHONPATH=src python3 -c "
import pandas as pd
from catmob import scoring, viz
df = pd.read_parquet('data/gold/h3_res8_catalonia.parquet')
df = scoring.score_dataframe(df, preset='default')
viz.export_deck_html('docs/catalonia_liveability.html', df, title='Catalonia Liveability — final')
"

# Tail Spark UI
open http://localhost:4040  # while jupyter container is up
```

## Open setup needs (for context)

| # | Item | Status |
|---|---|---|
| 1 | gh auth (CLI token) | ⚠ expired in DC shell — refresh before push |
| 2 | ssh keys for github.com | ✅ confirmed (`Hi lunasilvestre!` handshake) |
| 3 | Docker daemon | ✅ on atlas |
| 4 | Disk | ✅ 738 GB free |
| 5 | Java for Spark | ✅ bundled in `apache/sedona` Docker image |
| 6 | MapTiler/Stadia key | ⚪ optional (CARTO works without) |
