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
# | MITMA v2 OD CSV.gz (daily Q1+Q2 2024 + hourly all March 2024) | `https://movilidad-opendata.mitma.es/` |
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
SCOPE = os.environ.get("MITMA_SCOPE", "dev")
assert SCOPE in ("full", "dev")
print(f"Running in --scope {SCOPE}")

# Locate the repo root robustly: from notebooks/, from rendered/, or from /workspace.
_here = Path.cwd()
if (_here / "PLAN.md").exists():
    REPO = _here
elif (_here.parent / "PLAN.md").exists():
    REPO = _here.parent
elif Path("/workspace/PLAN.md").exists():
    REPO = Path("/workspace")
else:
    REPO = Path("/home/nls/Documents/dev/mitma-sedona")
sys.path.insert(0, str(REPO / "src"))
print(f"REPO = {REPO}")

from catmob import (
    io_mitma, io_osm, io_air, io_gtfs, io_biodiversity, io_pollution, io_health, io_thermal,
    schemas,
)

# %% [markdown]
# ## SedonaContext
#
# Spark 4.1.x + Sedona 1.9.0 + Scala 2.13. Maven coordinates are pinned to
# the sedona-spark-shaded-4.0 build (Sedona's "4.0" artifact is also Spark
# 4.1-compatible modulo one optional Catalyst rule that emits a noisy but
# non-fatal `FoldableUnevaluable` ClassNotFoundException at session start —
# the SQL we exercise here is unaffected).

# %%
from sedona.spark import SedonaContext

SEDONA_PACKAGES = (
    "org.apache.sedona:sedona-spark-shaded-4.0_2.13:1.9.0,"
    "org.datasyslab:geotools-wrapper:1.9.0-33.5"
)

config = (
    SedonaContext.builder()
        .appName("mitma-sedona-01-ingest")
        .config("spark.jars.packages", SEDONA_PACKAGES)
        .config("spark.sql.session.timeZone", "Europe/Madrid")
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.shuffle.partitions", "16")
        .config("spark.driver.memory", "6g")
        .config("sedona.global.indextype", "rtree")
        .config("sedona.join.optimizationmode", "all")
        .config("sedona.join.autoBroadcastJoinThreshold", "100MB")
        .config("spark.sql.autoBroadcastJoinThreshold", "50MB")
        .getOrCreate()
)
sedona = SedonaContext.create(config)
sedona.sparkContext.setLogLevel("ERROR")
print("Sedona session up. Spark UI on http://localhost:4040")

# %% [markdown]
# ## 1. MITMA daily OD
#
# Pull from `https://movilidad-opendata.mitma.es/`, filter to Catalonia at the
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
DAILY_PATHS = [
    str(REPO / f"data/bronze/mitma/daily/{d[:7]}/{d.replace('-','')}_Viajes_distritos.csv.gz")
    for d in DAILY_DATES
]
existing_daily = [p for p in DAILY_PATHS if Path(p).exists()]
print(f"Found {len(existing_daily)}/{len(DAILY_PATHS)} daily files")
if not existing_daily:
    raise RuntimeError(
        "No MITMA daily files on disk — run scripts/fetch_mitma.sh first."
    )

# %%
# v2 MITMA distritos CSVs are *already hour-level* (every row carries
# `periodo`), so a single read populates both the `daily` and `hourly`
# bronze parquet trees. Downstream notebooks aggregate over `periodo` when
# they want daily totals and group by `(fecha, periodo, ...)` when they
# want hourly totals.
mitma_od = io_mitma.read_with_sedona(sedona, existing_daily, kind="daily", catalonia_only=True)
mitma_od = mitma_od.cache()
mitma_total = mitma_od.count()
print(f"OD rows after Catalonia filter: {mitma_total:,}")

io_mitma.write_bronze_parquet(mitma_od, str(REPO / "data/bronze/mitma_parquet"), kind="daily")
io_mitma.write_bronze_parquet(mitma_od, str(REPO / "data/bronze/mitma_parquet"), kind="hourly")
print("Bronze parquet written to mitma_parquet/{daily,hourly}/ (same data; hourly grain).")

# %% [markdown]
# ## 2. MITMA distritos zoning
#
# The zoning shapefile + relation table from the MITMA bucket. We register
# the GeoJSON as a Sedona view called `zones` for the OD-line construction
# in notebook 02 (see `docs/sedona_sql_patterns.md` §2).

# %%
# The MITMA zoning GeoJSON has properties.ID (uppercase) and no name property.
# Names live in the companion `nombres_distritos.csv` if needed downstream.
zones = (
    sedona.read.format("geojson")
        .load(str(REPO / "data/bronze/mitma/zones/zonificacion_distritos.geojson"))
        .selectExpr("properties.ID AS id", "ST_GeomFromGeoJSON(geometry) AS geom")
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
    # Fall back to the unpruned regional extract if the pre-pruned file is
    # absent — scripts/fetch_osm.sh pre-prunes, but for the prototype run we
    # accept the full Cataluña extract too.
    alt = REPO / "data/bronze/osm/cataluna-latest.osm.pbf"
    if alt.exists():
        PBF_PATH = str(alt)
        print(f"Using unpruned PBF: {PBF_PATH}")
    else:
        raise RuntimeError(f"OSM PBF not found at {PBF_PATH} or {alt}.")

# --- POIs from OSM nodes via Sedona's native osmpbf reader. ---
pois = io_osm.extract_pois_with_sedona(sedona, PBF_PATH).cache()
poi_total = pois.count()
print(f"POIs extracted: {poi_total:,}")
pois.write.mode("overwrite").parquet(str(REPO / "data/bronze/osm/pois.parquet"))
print("POIs by category:")
pois.groupBy("category").count().orderBy("category").show()

# --- Railway stations (GTFS fallback for v1: synthetic trips_per_day=12). ---
stations = io_osm.extract_stations_with_sedona(sedona, PBF_PATH)
stations.write.mode("overwrite").parquet(str(REPO / "data/bronze/osm/stations.parquet"))
print(f"Railway stations / halts: {stations.count():,}")

# --- Highway network via pyrosm. Sedona's native PBF reader returns nodes
#     and refs but not ready-made way geometries; pyrosm hands us LineStrings
#     directly which is what the motorway-penalty join needs. We persist as
#     a Sedona-compatible parquet of WKT geometry (read back as ST_GeomFromText).
import time as _t
_t0 = _t.time()
print("Extracting highway network with pyrosm (driving preset) — ~1-2 min…")
network_gdf = io_osm.extract_network_pyrosm(PBF_PATH, network_type="driving")
print(f"Network ways: {len(network_gdf):,}  (extraction took {_t.time()-_t0:.0f}s)")
import pandas as _pd
net_pdf = _pd.DataFrame({
    "osm_id":  network_gdf["osm_id"].astype("int64").values,
    "kind":    network_gdf["kind"].astype(str).values,
    "subtype": network_gdf["subtype"].astype(str).values,
    "wkt":     network_gdf.geometry.to_wkt().values,
})
net_pdf.to_parquet(REPO / "data/bronze/osm/network.parquet", index=False)
print("Wrote network.parquet (WKT column; read back with ST_GeomFromWKT).")

# %% [markdown]
# ## 4. GTFS — Renfe Rodalies + FGC

# %%
RODALIES = REPO / "data/bronze/gtfs/rodalies"
FGC = REPO / "data/bronze/gtfs/fgc"
_have_gtfs = (
    RODALIES.exists() and (RODALIES / "stops.txt").exists()
    and FGC.exists() and (FGC / "stops.txt").exists()
)
if _have_gtfs:
    bundle = io_gtfs.load_combined(RODALIES, FGC)
    bundle["stops"].to_parquet(REPO / "data/bronze/gtfs/stops.parquet", index=False)
    bundle["freq"].to_parquet(REPO / "data/bronze/gtfs/frequency.parquet", index=False)
    print(f"Stops: {len(bundle['stops'])}, Frequency rows: {len(bundle['freq'])}")
else:
    print(
        "⚠  No GTFS feeds on disk; falling back to OSM railway=station nodes "
        "with a synthetic trips_per_day=12 (see bronze/osm/stations.parquet)."
    )

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
