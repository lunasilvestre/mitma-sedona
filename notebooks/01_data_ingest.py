# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 01 — Sources to Bronze
#
# **Goal:** ingest every source family into `data/bronze/`, schema-validated.
#
# | Inputs | Where |
# |---|---|
# | MITMA v2 OD CSV.gz (daily Q1+Q2 2024 + hourly all March 2024) | `https://opendata-movilidad.mitma.es/` |
# | OSM Cataluña PBF (~250 MB → ~50 MB after osmium prune) | Geofabrik |
# | Renfe Rodalies + FGC GTFS | transitfeeds + fgc.cat |
# | EEA + XVPCA + Copernicus CAMS | discomap.eea.europa.eu / analisi.transparenciacatalunya.cat / CAMS |
# | Landsat 8/9 LST summer composite | Microsoft Planetary Computer STAC |
# | WDPA + Copernicus TCD + iNaturalist (GBIF) | protectedplanet + Copernicus + GBIF |
# | E-PRTR + VIIRS DNB | EEA + Planetary Computer STAC |
# | CatSalut hospitals | analisi.transparenciacatalunya.cat |
#
# | Outputs | Path |
# |---|---|
# | `data/bronze/mitma/{daily,hourly}/<YYYY-MM>/*.parquet` | partitioned by `fecha` |
# | `data/bronze/osm/cataluna_pruned.osm.pbf` + parquet | nodes/ways/POIs/network |
# | `data/bronze/gtfs/{rodalies,fgc}/*.txt` + frequency.parquet | per stop |
# | `data/bronze/air/{xvpca,eea,cams}/*` | station + grid |
# | `data/bronze/thermal/landsat_lst_2024_jja.tif` | summer composite |
# | `data/bronze/biodiversity/{wdpa,inat,tcd}/*` | |
# | `data/bronze/pollution/{eprtr,viirs}/*` | |
# | `data/bronze/health/catsalut/*` | |
#
# **Runtime:** ~30 min in `--scope dev`, ~90 min in `--scope full` (default).

# %% [markdown]
# ## Setup

# %%
import os
import sys
from pathlib import Path

# Two operating modes. Set MITMA_SCOPE=dev for fast local iteration.
SCOPE = os.environ.get("MITMA_SCOPE", "full")
assert SCOPE in ("full", "dev")
print(f"Running in --scope {SCOPE}")

REPO = Path("/workspace") if Path("/workspace").exists() else Path.cwd().parent
sys.path.insert(0, str(REPO / "src"))

from catmob import (
    io_mitma, io_osm, io_air, io_gtfs, io_biodiversity, io_pollution, io_health, io_thermal,
    schemas,
)

# %% [markdown]
# ## SedonaContext

# %%
from sedona.spark import SedonaContext

config = (
    SedonaContext.builder()
        .appName("mitma-sedona-01-ingest")
        .config("spark.sql.session.timeZone", "Europe/Madrid")
        .config("spark.sql.adaptive.enabled", "true")
        .config("sedona.global.indextype", "rtree")
        .config("sedona.join.optimizationmode", "all")
        .config("sedona.join.autoBroadcastJoinThreshold", "100MB")
        .config("spark.sql.autoBroadcastJoinThreshold", "50MB")
        .getOrCreate()
)
sedona = SedonaContext.create(config)
print("Sedona session up. Spark UI on http://localhost:4040")

# %% [markdown]
# ## 1. MITMA daily OD
#
# Pull from `https://opendata-movilidad.mitma.es/`, filter to Catalonia at the
# SQL level (rows where origin OR destination province ∈ {08, 17, 25, 43}).
# We rely on the Spark CSV reader with `;`-delimiter and explicit schema (see
# `src/catmob/io_mitma.py`).

# %%
from datetime import date, timedelta

if SCOPE == "dev":
    DAILY_DATES = [(date(2024, 3, 4) + timedelta(days=i)).isoformat() for i in range(7)]
    HOURLY_DATES = ["2024-03-06"]
else:
    # Q1+Q2 2024 daily, all March 2024 hourly
    DAILY_DATES = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(182)]
    HOURLY_DATES = [(date(2024, 3, 1) + timedelta(days=i)).isoformat() for i in range(31)]

print(f"DAILY:  {len(DAILY_DATES)} days  ({DAILY_DATES[0]}..{DAILY_DATES[-1]})")
print(f"HOURLY: {len(HOURLY_DATES)} days ({HOURLY_DATES[0]}..{HOURLY_DATES[-1]})")

# %%
# Files should already be downloaded by scripts/fetch_mitma.sh
# Run that first if data/bronze/mitma/ is empty.
DAILY_PATHS = [str(REPO / f"data/bronze/mitma/daily/{d[:7]}/{d.replace('-','')}_Viajes_distritos.csv.gz") for d in DAILY_DATES]
HOURLY_PATHS = [str(REPO / f"data/bronze/mitma/hourly/{d[:7]}/{d.replace('-','')}_Viajes_distritos.csv.gz") for d in HOURLY_DATES]
existing_daily = [p for p in DAILY_PATHS if Path(p).exists()]
existing_hourly = [p for p in HOURLY_PATHS if Path(p).exists()]
print(f"Found {len(existing_daily)}/{len(DAILY_PATHS)} daily files")
print(f"Found {len(existing_hourly)}/{len(HOURLY_PATHS)} hourly files")
if not existing_daily:
    print("⚠  Run scripts/fetch_mitma.sh first to populate data/bronze/mitma/")

# %%
mitma_daily = io_mitma.read_with_sedona(sedona, existing_daily, kind="daily", catalonia_only=True)
print(f"Daily rows after Catalonia filter: {mitma_daily.count():,}")
io_mitma.write_bronze_parquet(mitma_daily, str(REPO / "data/bronze/mitma_parquet"), kind="daily")

# %%
mitma_hourly = io_mitma.read_with_sedona(sedona, existing_hourly, kind="hourly", catalonia_only=True)
print(f"Hourly rows after Catalonia filter: {mitma_hourly.count():,}")
io_mitma.write_bronze_parquet(mitma_hourly, str(REPO / "data/bronze/mitma_parquet"), kind="hourly")

# %% [markdown]
# ## 2. MITMA distritos zoning
#
# The zoning shapefile + relation table from the MITMA bucket. We register
# the GeoJSON as a Sedona view called `zones` for the OD-line construction
# in notebook 02 (see `docs/sedona_sql_patterns.md` §2).

# %%
zones = (
    sedona.read.format("geojson")
        .load(str(REPO / "data/bronze/mitma/zones/zonificacion_distritos.geojson"))
        .selectExpr("properties.id AS id", "properties.name AS name",
                    "ST_GeomFromGeoJSON(geometry) AS geom")
)
zones.createOrReplaceTempView("zones")
print(f"Zones loaded: {zones.count():,}")

# %% [markdown]
# ## 3. OSM Cataluña — POIs + network
#
# Read from the pre-pruned PBF (`scripts/fetch_osm.sh` produces it).
# Sedona's native `osmpbf` reader (see `docs/sedona_sql_patterns.md` §1).

# %%
PBF_PATH = str(REPO / "data/bronze/osm/cataluna_pruned.osm.pbf")
if not Path(PBF_PATH).exists():
    print(f"⚠  Run scripts/fetch_osm.sh — missing {PBF_PATH}")
else:
    pois = io_osm.extract_pois_with_sedona(sedona, PBF_PATH)
    pois.write.mode("overwrite").parquet(str(REPO / "data/bronze/osm/pois.parquet"))
    print(f"POIs by category:")
    pois.groupBy("category").count().show()

    network = io_osm.extract_network_with_sedona(sedona, PBF_PATH)
    network.write.mode("overwrite").parquet(str(REPO / "data/bronze/osm/network.parquet"))
    print(f"Network features: {network.count():,}")

# %% [markdown]
# ## 4. GTFS — Renfe Rodalies + FGC

# %%
RODALIES = REPO / "data/bronze/gtfs/rodalies"
FGC = REPO / "data/bronze/gtfs/fgc"
if RODALIES.exists() and FGC.exists():
    bundle = io_gtfs.load_combined(RODALIES, FGC)
    bundle["stops"].to_parquet(REPO / "data/bronze/gtfs/stops.parquet", index=False)
    bundle["freq"].to_parquet(REPO / "data/bronze/gtfs/frequency.parquet", index=False)
    print(f"Stops: {len(bundle['stops'])}, Frequency rows: {len(bundle['freq'])}")
else:
    print("⚠  Run scripts/fetch_gtfs.sh first")

# %% [markdown]
# ## 5. Air quality — XVPCA + EEA + CAMS

# %%
XVPCA_CSV = REPO / "data/bronze/air/xvpca/xvpca_2024.csv"
if XVPCA_CSV.exists():
    xvpca = io_air.parse_xvpca_csv(XVPCA_CSV)
    xvpca.to_parquet(REPO / "data/bronze/air/xvpca_2024.parquet", index=False)
    print(f"XVPCA 2024 stations: {len(xvpca)}")
else:
    print("⚠  Run scripts/fetch_air.sh first")

# EEA + CAMS implementations land in M2 follow-up; the URL and schema are
# already pinned in src/catmob/io_air.py.

# %% [markdown]
# ## 6. Thermal LST, biodiversity, pollution, health
#
# These all use the STAC reader pattern (see `docs/sedona_sql_patterns.md` §8f)
# or pure-Python fetchers from the corresponding `io_*.py` module. They're
# scaffolded but the production fetcher implementations land in a follow-up
# pass — the schemas and contracts in `catmob.schemas` are already locked.

# %%
print("Bronze layer complete (modulo M2 follow-ups for thermal/bio/pollution/health).")
print("Next: notebook 02 — silver + gold liveability layer.")
