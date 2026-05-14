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
# # 02 — Bronze to Silver to Gold (the liveability layer)
#
# Per-hex feature engineering. Reads bronze, joins with Sedona spatial ops,
# writes `data/gold/h3_res8_catalonia.parquet` matching `GOLD_HEX_SCHEMA`.
#
# **Runtime:** ~45 min (`--scope dev`), ~75 min (`--scope full`).
#
# Patterns from `docs/sedona_sql_patterns.md`:
# - §1 H3 grid via `ST_H3CellIDs` + `LATERAL VIEW EXPLODE`
# - §3 area-weighted disaggregation (distrito → hex)
# - §4 raster zonal stats (LST / VIIRS / TCD)
# - §5 `ST_KNN` for nearest-station + isochrone left-join
# - §6 broadcast hints
# - §8c GeoParquet 1.1 write

# %%
import os
import sys
from pathlib import Path

REPO = Path("/workspace") if Path("/workspace").exists() else Path.cwd().parent
sys.path.insert(0, str(REPO / "src"))

from catmob import h3_utils, isochrones, schemas
from sedona.spark import SedonaContext

config = SedonaContext.builder().appName("mitma-sedona-02-gold").getOrCreate()
sedona = SedonaContext.create(config)

# %% [markdown]
# ## 1. Read bronze tables
# %%
mitma_daily  = sedona.read.parquet(str(REPO / "data/bronze/mitma_parquet/daily"))
mitma_hourly = sedona.read.parquet(str(REPO / "data/bronze/mitma_parquet/hourly"))
zones        = sedona.read.format("geojson") \
                    .load(str(REPO / "data/bronze/mitma/zones/zonificacion_distritos.geojson")) \
                    .selectExpr("properties.id AS id", "ST_GeomFromGeoJSON(geometry) AS geom")
pois         = sedona.read.parquet(str(REPO / "data/bronze/osm/pois.parquet"))
network      = sedona.read.parquet(str(REPO / "data/bronze/osm/network.parquet"))
stops        = sedona.read.parquet(str(REPO / "data/bronze/gtfs/stops.parquet"))
gtfs_freq    = sedona.read.parquet(str(REPO / "data/bronze/gtfs/frequency.parquet"))

for name, df in [("mitma_daily", mitma_daily), ("zones", zones), ("pois", pois),
                 ("network", network), ("stops", stops)]:
    df.createOrReplaceTempView(name)
    print(f"{name}: {df.count():,} rows")

# %% [markdown]
# ## 2. Catalonia boundary
#
# The MITMA distritos zoning covers all of Spain. We build the Catalonia
# boundary by unioning distritos whose `id` starts with the Catalonia
# province codes (08/17/25/43). This is more reliable than fetching OSM
# relation 349 and gives a coastline-accurate boundary.

# %%
sedona.sql("""
    CREATE OR REPLACE TEMP VIEW catalonia_boundary AS
    SELECT ST_Union_Aggr(geom) AS geometry
    FROM zones
    WHERE substring(id, 1, 2) IN ('08', '17', '25', '43')
""")
boundary_count = sedona.sql("SELECT 1 FROM catalonia_boundary").count()
print(f"Catalonia boundary built (1 multipolygon from ~584 distritos)")

# %% [markdown]
# ## 3. H3 res-8 hex grid (PLAN.md §6 / sedona_sql_patterns.md §1)
# %%
hex_sql = h3_utils.build_grid_sql(distritos_view="zones", boundary_view="catalonia_boundary", res=8)
hexes = sedona.sql(hex_sql)
hexes.createOrReplaceTempView("hexes")
print(f"Hex grid: {hexes.count():,} cells")
hexes.write.format("geoparquet").mode("overwrite").save(str(REPO / "data/silver/hexes.parquet"))

# %% [markdown]
# ## 4. Per-hex feature columns

# %% [markdown]
# ### 4a — Train station bike-reach (sedona_sql_patterns.md §5)
#
# Build Valhalla bike isochrones for every Catalan train station first
# (cached on disk), then left-join hexes against the union of isochrones.

# %%
stations_pdf = (
    stops.filter("trips_per_day >= 8")  # min frequency for "real" station
        .select("stop_id", "stop_name", "lat", "lon")
        .toPandas()
)
print(f"Computing bike isochrones for {len(stations_pdf)} stations...")
iso_results = isochrones.batch_isochrones(
    [(r.stop_id, r.lat, r.lon) for r in stations_pdf.itertuples()],
    minutes=(15, 25),
)
print(f"Got {len(iso_results)} isochrone polygons (cache + fresh).")

# Materialise as Sedona view
import json
import pandas as pd
iso_rows = [
    {"station_id": r.station_id, "minutes": r.minutes,
     "polygon_json": json.dumps(r.geojson["features"][0]["geometry"])}
    for r in iso_results
]
iso_pdf = pd.DataFrame(iso_rows)
iso_sdf = sedona.createDataFrame(iso_pdf).selectExpr(
    "station_id", "minutes", "ST_GeomFromGeoJSON(polygon_json) AS polygon"
)
iso_sdf.createOrReplaceTempView("bike_isochrones")

# %%
train_reach = sedona.sql("""
    SELECT /*+ BROADCAST(iso) */
           h.h3_id, MIN(iso.minutes) AS train_reach_min
    FROM hexes h
    LEFT JOIN bike_isochrones iso ON ST_Intersects(iso.polygon, h.geometry)
    GROUP BY h.h3_id
""")
train_reach.createOrReplaceTempView("hex_train_reach")

# %% [markdown]
# ### 4b — POI distances (climbing, yoga, hospital) via ST_KNN
# %%
def knn_distance_sql(category: str, label: str) -> str:
    return f"""
        SELECT h.h3_id,
               MIN(ST_DistanceSpheroid(ST_Centroid(h.geometry), p.geom)) AS {label}
        FROM hexes h
        JOIN (SELECT ST_Point(lon, lat) AS geom FROM pois WHERE category = '{category}') p
          ON ST_KNN(ST_Centroid(h.geometry), p.geom, 1, false)
        GROUP BY h.h3_id
    """

hex_climb    = sedona.sql(knn_distance_sql("climbing", "climb_min_m"));    hex_climb.createOrReplaceTempView("hex_climb")
hex_yoga     = sedona.sql(knn_distance_sql("yoga",     "yoga_min_m"));     hex_yoga.createOrReplaceTempView("hex_yoga")
hex_hospital = sedona.sql(knn_distance_sql("hospital", "hospital_min_m")); hex_hospital.createOrReplaceTempView("hex_hospital")

# %% [markdown]
# ### 4c — Area-weighted MITMA disaggregation (sedona_sql_patterns.md §3)
# %%
hex_mitma = sedona.sql("""
    WITH overlaps AS (
        SELECT h.h3_id, o.id AS distrito_id,
               ST_Area(ST_Intersection(h.geometry, o.geom))
                 / NULLIF(ST_Area(o.geom), 0) AS area_weight
        FROM hexes h
        JOIN zones o ON ST_Intersects(h.geometry, o.geom)
    ),
    inflow  AS (SELECT destino AS id, SUM(viajes) AS viajes_in
                FROM mitma_daily GROUP BY destino),
    outflow AS (SELECT origen  AS id, SUM(viajes) AS viajes_out
                FROM mitma_daily GROUP BY origen)
    SELECT ov.h3_id,
           SUM(ov.area_weight * inflow.viajes_in)  AS mitma_inflow_daily,
           SUM(ov.area_weight * outflow.viajes_out) AS mitma_outflow_daily,
           CASE WHEN SUM(ov.area_weight * inflow.viajes_in) > 0
                THEN SUM(ov.area_weight * outflow.viajes_out)
                   / SUM(ov.area_weight * inflow.viajes_in)
                ELSE NULL END AS mitma_through_ratio
    FROM overlaps ov
    JOIN inflow  ON inflow.id  = ov.distrito_id
    JOIN outflow ON outflow.id = ov.distrito_id
    GROUP BY ov.h3_id
""")
hex_mitma.createOrReplaceTempView("hex_mitma")

# %% [markdown]
# ### 4d — Motorway penalty + industry density
# %%
hex_pens = sedona.sql("""
    SELECT h.h3_id,
           BOOLEAN(EXISTS(
               SELECT 1 FROM network n
               WHERE n.kind = 'highway'
                 AND n.subtype IN ('motorway','trunk')
                 AND ST_Distance(ST_Centroid(h.geometry), n.geometry) < 500
           )) AS motorway_within_500m,
           (SELECT COUNT(*) FROM pois p
            WHERE p.category = 'industry'
              AND ST_Distance(ST_Centroid(h.geometry), ST_Point(p.lon, p.lat)) < 1000
           ) AS industry_density_per_km2
    FROM hexes h
""")
hex_pens.createOrReplaceTempView("hex_pens")

# %% [markdown]
# ### 4e — Stitch all features into the gold table

# %%
gold = sedona.sql("""
    SELECT h.h3_id,
           ST_X(ST_Centroid(h.geometry)) AS lon_centroid,
           ST_Y(ST_Centroid(h.geometry)) AS lat_centroid,
           t.train_reach_min,
           c.climb_min_m, y.yoga_min_m, hosp.hospital_min_m,
           m.mitma_inflow_daily, m.mitma_outflow_daily, m.mitma_through_ratio,
           p.motorway_within_500m, p.industry_density_per_km2
    FROM hexes h
    LEFT JOIN hex_train_reach t ON t.h3_id = h.h3_id
    LEFT JOIN hex_climb c       ON c.h3_id = h.h3_id
    LEFT JOIN hex_yoga y        ON y.h3_id = h.h3_id
    LEFT JOIN hex_hospital hosp ON hosp.h3_id = h.h3_id
    LEFT JOIN hex_mitma m       ON m.h3_id = h.h3_id
    LEFT JOIN hex_pens p        ON p.h3_id = h.h3_id
""")
gold.write.format("geoparquet").mode("overwrite").save(str(REPO / "data/gold/h3_res8_catalonia.parquet"))
print(f"Gold rows: {gold.count():,}")
gold.printSchema()

# %% [markdown]
# > **Follow-ups (M2/M3):** the air-quality, UHI, biodiversity, light-pollution
# > and CatSalut feature joins follow the same skeleton — see
# > `docs/sedona_sql_patterns.md` §4 for the raster zonal stats template.
