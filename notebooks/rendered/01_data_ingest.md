# 01 — Sources to Bronze

**Goal:** ingest every source family into `data/bronze/`, schema-validated.

| Inputs | Where |
|---|---|
| MITMA v2 OD CSV.gz (daily Q1+Q2 2024 + hourly all March 2024) | `https://movilidad-opendata.mitma.es/` |
| OSM Cataluña PBF (~250 MB → ~50 MB after osmium prune) | Geofabrik |
| Renfe Rodalies + FGC GTFS | transitfeeds + fgc.cat |
| EEA + XVPCA + Copernicus CAMS | discomap.eea.europa.eu / analisi.transparenciacatalunya.cat / CAMS |
| Landsat 8/9 LST summer composite | Microsoft Planetary Computer STAC |
| WDPA + Copernicus TCD + iNaturalist (GBIF) | protectedplanet + Copernicus + GBIF |
| E-PRTR + VIIRS DNB | EEA + Planetary Computer STAC |
| CatSalut hospitals | analisi.transparenciacatalunya.cat |

| Outputs | Path |
|---|---|
| `data/bronze/mitma/{daily,hourly}/<YYYY-MM>/*.parquet` | partitioned by `fecha` |
| `data/bronze/osm/cataluna_pruned.osm.pbf` + parquet | nodes/ways/POIs/network |
| `data/bronze/gtfs/{rodalies,fgc}/*.txt` + frequency.parquet | per stop |
| `data/bronze/air/{xvpca,eea,cams}/*` | station + grid |
| `data/bronze/thermal/landsat_lst_2024_jja.tif` | summer composite |
| `data/bronze/biodiversity/{wdpa,inat,tcd}/*` | |
| `data/bronze/pollution/{eprtr,viirs}/*` | |
| `data/bronze/health/catsalut/*` | |

**Runtime:** ~30 min in `--scope dev`, ~90 min in `--scope full` (default).

## Setup


```python
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
```

    Running in --scope dev
    REPO = /home/nls/Documents/dev/mitma-sedona


## SedonaContext

Spark 4.1.x + Sedona 1.9.0 + Scala 2.13. Maven coordinates are pinned to
the sedona-spark-shaded-4.0 build (Sedona's "4.0" artifact is also Spark
4.1-compatible modulo one optional Catalyst rule that emits a noisy but
non-fatal `FoldableUnevaluable` ClassNotFoundException at session start —
the SQL we exercise here is unaffected).


```python
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
```

    WARNING: Using incubator modules: jdk.incubator.vector


    Warning: Ignoring non-Spark config property: sedona.global.indextype
    Warning: Ignoring non-Spark config property: sedona.join.autoBroadcastJoinThreshold
    Warning: Ignoring non-Spark config property: sedona.join.optimizationmode
    :: loading settings :: url = jar:file:/home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyspark/jars/ivy-2.5.3.jar!/org/apache/ivy/core/settings/ivysettings.xml


    Ivy Default Cache set to: /home/nls/.ivy2.5.2/cache
    The jars for the packages stored in: /home/nls/.ivy2.5.2/jars
    org.apache.sedona#sedona-spark-shaded-4.0_2.13 added as a dependency
    org.datasyslab#geotools-wrapper added as a dependency
    :: resolving dependencies :: org.apache.spark#spark-submit-parent-23590b9d-5934-46b1-91fe-de1b95e866ad;1.0
    	confs: [default]
    	found org.apache.sedona#sedona-spark-shaded-4.0_2.13;1.9.0 in central
    	found org.datasyslab#geotools-wrapper;1.9.0-33.5 in central
    :: resolution report :: resolve 83ms :: artifacts dl 2ms
    	:: modules in use:
    	org.apache.sedona#sedona-spark-shaded-4.0_2.13;1.9.0 from central in [default]
    	org.datasyslab#geotools-wrapper;1.9.0-33.5 from central in [default]
    	---------------------------------------------------------------------
    	|                  |            modules            ||   artifacts   |
    	|       conf       | number| search|dwnlded|evicted|| number|dwnlded|
    	---------------------------------------------------------------------
    	|      default     |   2   |   0   |   0   |   0   ||   2   |   0   |
    	---------------------------------------------------------------------
    :: retrieving :: org.apache.spark#spark-submit-parent-23590b9d-5934-46b1-91fe-de1b95e866ad
    	confs: [default]
    	0 artifacts copied, 2 already retrieved (0kB/3ms)


    26/05/14 23:44:43 WARN NativeCodeLoader: Unable to load native-hadoop library for your platform... using builtin-java classes where applicable
    Using Spark's default log4j profile: org/apache/spark/log4j2-defaults.properties
    Setting default log level to "WARN".
    To adjust logging level use sc.setLogLevel(newLevel). For SparkR, use setLogLevel(newLevel).


    Sedona session up. Spark UI on http://localhost:4040


    26/05/14 23:44:47 WARN Catalog: GEO stats functions are not available due to Spark/DBR compatibility issues.
    java.lang.NoClassDefFoundError: org/apache/spark/sql/catalyst/expressions/FoldableUnevaluable
    	at java.base/java.lang.Class.getDeclaredConstructors0(Native Method)
    	at java.base/java.lang.Class.privateGetDeclaredConstructors(Class.java:3551)
    	at java.base/java.lang.Class.getConstructor0(Class.java:3756)
    	at java.base/java.lang.Class.getConstructor(Class.java:2444)
    	at org.apache.sedona.sql.UDF.AbstractCatalog.function(AbstractCatalog.scala:42)
    	at org.apache.sedona.sql.UDF.Catalog$.geoStatsFunctions(Catalog.scala:384)
    	at org.apache.sedona.sql.UDF.Catalog$.<clinit>(Catalog.scala:373)
    	at org.apache.sedona.spark.SedonaContext$.create(SedonaContext.scala:132)
    	at org.apache.sedona.spark.SedonaContext.create(SedonaContext.scala)
    	at java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
    	at java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke(NativeMethodAccessorImpl.java:75)
    	at java.base/jdk.internal.reflect.DelegatingMethodAccessorImpl.invoke(DelegatingMethodAccessorImpl.java:52)
    	at java.base/java.lang.reflect.Method.invoke(Method.java:580)
    	at py4j.reflection.MethodInvoker.invoke(MethodInvoker.java:244)
    	at py4j.reflection.ReflectionEngine.invoke(ReflectionEngine.java:374)
    	at py4j.Gateway.invoke(Gateway.java:282)
    	at py4j.commands.AbstractCommand.invokeMethod(AbstractCommand.java:132)
    	at py4j.commands.CallCommand.execute(CallCommand.java:79)
    	at py4j.ClientServerConnection.waitForCommands(ClientServerConnection.java:184)
    	at py4j.ClientServerConnection.run(ClientServerConnection.java:108)
    	at java.base/java.lang.Thread.run(Thread.java:1583)
    Caused by: java.lang.ClassNotFoundException: org.apache.spark.sql.catalyst.expressions.FoldableUnevaluable
    	at java.base/java.net.URLClassLoader.findClass(URLClassLoader.java:445)
    	at java.base/java.lang.ClassLoader.loadClass(ClassLoader.java:593)
    	at java.base/java.lang.ClassLoader.loadClass(ClassLoader.java:526)
    	... 21 more
    26/05/14 23:44:47 WARN SimpleFunctionRegistry: The function st_geogfromwkb replaced a previously registered function.
    26/05/14 23:44:47 WARN SimpleFunctionRegistry: The function st_geogfromwkb replaced a previously registered function.
    26/05/14 23:44:47 WARN SimpleFunctionRegistry: The function st_geomfromwkb replaced a previously registered function.
    26/05/14 23:44:47 WARN SimpleFunctionRegistry: The function st_geomfromwkb replaced a previously registered function.
    26/05/14 23:44:47 WARN SimpleFunctionRegistry: The function st_asbinary replaced a previously registered function.
    26/05/14 23:44:47 WARN SimpleFunctionRegistry: The function st_asbinary replaced a previously registered function.
    26/05/14 23:44:47 WARN SimpleFunctionRegistry: The function st_srid replaced a previously registered function.
    26/05/14 23:44:47 WARN SimpleFunctionRegistry: The function st_srid replaced a previously registered function.
    26/05/14 23:44:47 WARN SimpleFunctionRegistry: The function st_setsrid replaced a previously registered function.
    26/05/14 23:44:47 WARN SimpleFunctionRegistry: The function st_setsrid replaced a previously registered function.


## 1. MITMA daily OD

Pull from `https://movilidad-opendata.mitma.es/`, filter to Catalonia at the
SQL level (rows where origin OR destination province ∈ {08, 17, 25, 43}).
We rely on the Spark CSV reader with `;`-delimiter and explicit schema (see
`src/catmob/io_mitma.py`).


```python
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
```

    DAILY:  7 days  (2024-03-04..2024-03-10)
    HOURLY: 1 days (2024-03-06..2024-03-06)



```python
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
```

    Found 7/7 daily files



```python
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
```

    [Stage 3:>                                                          (0 + 7) / 7]

    [Stage 3:========>                                                  (1 + 6) / 7]

    [Stage 3:================>                                          (2 + 5) / 7]

    [Stage 3:=========================>                                 (3 + 4) / 7]

    [Stage 3:=================================>                         (4 + 3) / 7][Stage 3:==========================================>                (5 + 2) / 7]

    [Stage 3:==================================================>        (6 + 1) / 7]

                                                                                    

    OD rows after Catalonia filter: 27,697,944


    [Stage 7:>                                                          (0 + 7) / 7]

    [Stage 7:========>                                                  (1 + 6) / 7][Stage 7:================>                                          (2 + 5) / 7]

    [Stage 7:=========================>                                 (3 + 4) / 7][Stage 7:=================================>                         (4 + 3) / 7]

    [Stage 7:==========================================>                (5 + 2) / 7]                                                                                

    [Stage 8:>                                                          (0 + 7) / 7]

    [58.564s][warning][gc,alloc] Executor task launch worker for task 1.0 in stage 8.0 (TID 25): Retried waiting for GCLocker too often allocating 8388610 words
    [58.570s][warning][gc,alloc] Executor task launch worker for task 1.0 in stage 8.0 (TID 25): Retried waiting for GCLocker too often allocating 8388610 words
    [58.575s][warning][gc,alloc] Executor task launch worker for task 1.0 in stage 8.0 (TID 25): Retried waiting for GCLocker too often allocating 8388610 words


    [Stage 8:========>                                                  (1 + 6) / 7]

    [Stage 8:================>                                          (2 + 5) / 7]

    [Stage 8:=================================>                         (4 + 3) / 7]

    Bronze parquet written to mitma_parquet/{daily,hourly}/ (same data; hourly grain).


    [Stage 8:==================================================>        (6 + 1) / 7]                                                                                

## 2. MITMA distritos zoning

The zoning shapefile + relation table from the MITMA bucket. We register
the GeoJSON as a Sedona view called `zones` for the OD-line construction
in notebook 02 (see `docs/sedona_sql_patterns.md` §2).


```python
# The MITMA zoning GeoJSON has properties.ID (uppercase) and no name property.
# Names live in the companion `nombres_distritos.csv` if needed downstream.
zones = (
    sedona.read.format("geojson")
        .load(str(REPO / "data/bronze/mitma/zones/zonificacion_distritos.geojson"))
        .selectExpr("properties.ID AS id", "ST_GeomFromGeoJSON(geometry) AS geom")
)
zones.createOrReplaceTempView("zones")
print(f"Zones loaded: {zones.count():,}")
```

    Zones loaded: 3,916


## 3. OSM Cataluña — POIs + network

Read from the pre-pruned PBF (`scripts/fetch_osm.sh` produces it).
Sedona's native `osmpbf` reader (see `docs/sedona_sql_patterns.md` §1).


```python
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
```

    [Stage 13:>                                                       (0 + 32) / 32]

    [Stage 13:===>                                                    (2 + 30) / 32]

    [Stage 13:=====>                                                  (3 + 29) / 32][Stage 13:========>                                               (5 + 27) / 32]

    [Stage 13:===============>                                        (9 + 23) / 32][Stage 13:==================>                                    (11 + 21) / 32]

    [Stage 13:====================>                                  (12 + 20) / 32]

    [Stage 13:======================>                                (13 + 19) / 32][Stage 13:=========================>                             (15 + 17) / 32]

    [Stage 13:=============================>                         (17 + 15) / 32]

    [Stage 13:==============================>                        (18 + 14) / 32]

    [Stage 13:===============================================>        (27 + 5) / 32]

                                                                                    

    POIs extracted: 4,935


    POIs by category:


    +--------+-----+
    |category|count|
    +--------+-----+
    |climbing|  219|
    |  clinic|  523|
    | doctors|  491|
    |hospital|   36|
    |industry|    5|
    |    park|  377|
    |pharmacy| 3217|
    |    yoga|   67|
    +--------+-----+
    


    [Stage 21:>                                                       (0 + 32) / 32]

    [Stage 21:=>                                                      (1 + 31) / 32]

    [Stage 21:===>                                                    (2 + 30) / 32]

    [Stage 21:=====>                                                  (3 + 29) / 32][Stage 21:========>                                               (5 + 27) / 32]

    [Stage 21:============>                                           (7 + 25) / 32][Stage 21:=================>                                     (10 + 22) / 32]

    [Stage 21:======================>                                (13 + 19) / 32]

    [Stage 21:========================>                              (14 + 18) / 32][Stage 21:=========================>                             (15 + 17) / 32]

    [Stage 21:===========================>                           (16 + 16) / 32][Stage 21:=============================>                         (17 + 15) / 32]

    [Stage 21:========================================>               (23 + 9) / 32][Stage 21:======================================================> (31 + 1) / 32]

                                                                                    

    [Stage 22:>                                                       (0 + 32) / 32]

    [Stage 22:=>                                                      (1 + 31) / 32]

    [Stage 22:===>                                                    (2 + 30) / 32]

    [Stage 22:=====>                                                  (3 + 29) / 32][Stage 22:=======>                                                (4 + 28) / 32]

    [Stage 22:========>                                               (5 + 27) / 32][Stage 22:===============>                                        (9 + 23) / 32]

    [Stage 22:====================>                                  (12 + 20) / 32][Stage 22:======================>                                (13 + 19) / 32]

    [Stage 22:========================>                              (14 + 18) / 32][Stage 22:===========================>                           (16 + 16) / 32]

    [Stage 22:================================>                      (19 + 13) / 32][Stage 22:======================================================> (31 + 1) / 32]

                                                                                    

    Railway stations / halts: 475
    Extracting highway network with pyrosm (driving preset) — ~1-2 min…


    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(


    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(


    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(


    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(


    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/pyrosm.py:109: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      nodes, ways, relations, node_coordinates = parse_osm_data(


    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(


    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(


    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(


    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(


    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(
    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(


    /home/nls/miniforge3/envs/sedona/lib/python3.11/site-packages/pyrosm/networks.py:37: ChainedAssignmentError: A value is being set on a copy of a DataFrame or Series through chained assignment.
    Such chained assignment never works to update the original DataFrame or Series, because the intermediate object on which we are setting values always behaves as a copy (due to Copy-on-Write).
    
    Try using '.loc[row_indexer, col_indexer] = value' instead, to perform the assignment in a single step.
    
    See the documentation for a more detailed explanation: https://pandas.pydata.org/pandas-docs/stable/user_guide/copy_on_write.html#chained-assignment
      edges, nodes = prepare_geodataframe(


    Network ways: 364,530  (extraction took 132s)


    Wrote network.parquet (WKT column; read back with ST_GeomFromWKT).


## 4. GTFS — Renfe Rodalies + FGC


```python
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
```

    ⚠  No GTFS feeds on disk; falling back to OSM railway=station nodes with a synthetic trips_per_day=12 (see bronze/osm/stations.parquet).


## 5. Air quality — XVPCA + EEA + CAMS


```python
XVPCA_CSV = REPO / "data/bronze/air/xvpca/xvpca_2024.csv"
if XVPCA_CSV.exists():
    xvpca = io_air.parse_xvpca_csv(XVPCA_CSV)
    xvpca.to_parquet(REPO / "data/bronze/air/xvpca_2024.parquet", index=False)
    print(f"XVPCA 2024 stations: {len(xvpca)}")
else:
    print("⚠  Run scripts/fetch_air.sh first")

# EEA + CAMS implementations land in M2 follow-up; the URL and schema are
# already pinned in src/catmob/io_air.py.
```

    ⚠  Run scripts/fetch_air.sh first


## 6. Thermal LST, biodiversity, pollution, health

These all use the STAC reader pattern (see `docs/sedona_sql_patterns.md` §8f)
or pure-Python fetchers from the corresponding `io_*.py` module. They're
scaffolded but the production fetcher implementations land in a follow-up
pass — the schemas and contracts in `catmob.schemas` are already locked.


```python
print("Bronze layer complete (modulo M2 follow-ups for thermal/bio/pollution/health).")
print("Next: notebook 02 — silver + gold liveability layer.")
```

    Bronze layer complete (modulo M2 follow-ups for thermal/bio/pollution/health).
    Next: notebook 02 — silver + gold liveability layer.

