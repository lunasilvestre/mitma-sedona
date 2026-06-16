#!/usr/bin/env python
"""EXPLORATORY 10 km-catchment re-run of the gold liveability layer.

Mirrors notebooks/02_liveability_layer.py exactly, but drives every
catchment distance from a single constant CATCHMENT_M = 10000 instead of
the v1 mix of 5 km / 3 km train buffers and an 8 km amenity cap.

NON-DESTRUCTIVE: writes data/gold/h3_res8_catalonia_10km.parquet and does
NOT touch the v1 parquet. Run with the sedona env python:

    /home/nls/miniforge3/envs/sedona/bin/python scripts/run_gold_10km.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from shapely import wkt as _wkt

# --- the single knob -------------------------------------------------------
# v1 used: train reach 5 km (25 min) / 3 km (15 min); amenity sjoin cap 8 km.
# 10 km catchment: outer train buffer -> 10 km, amenity cap -> 10 km.
# Inner "15-min" train buffer kept at its v1 3 km (it is a *time* class, not a
# catchment edge); only the outer reach edge and the NULL-beyond amenity cap
# move out to 10 km.
CATCHMENT_M = 10_000
# Degrees of latitude per metre at ~41.15 N (used for the lon/lat buffers, as
# v1 did: 0.045 deg ~= 5 km, 0.027 deg ~= 3 km).
DEG_PER_M = 0.045 / 5000.0
OUTER_REACH_DEG = CATCHMENT_M * DEG_PER_M   # 10 km -> 0.090 deg
INNER_REACH_DEG = 0.027                      # 3 km, unchanged (15-min class)

SILVER = REPO / "data/silver"
GOLD = REPO / "data/gold"

print(f"=== 10 km gold re-run (CATCHMENT_M={CATCHMENT_M}) ===")

# 1. Zones ------------------------------------------------------------------
print("Loading zones ...")
zones = gpd.read_file(REPO / "data/bronze/mitma/zones/zonificacion_distritos.geojson")
zones = zones.loc[zones.geometry.notna()].copy()
zones["geometry"] = zones.geometry.make_valid()
zones = zones.set_crs("EPSG:4326", allow_override=True)
cat_mask = zones["ID"].astype(str).str[:2].isin(["08", "17", "25", "43"])
cat_zones = zones.loc[cat_mask].copy()
print(f"  {len(zones):,} total zones / {len(cat_zones):,} in Catalonia")

# 2. H3 grid ----------------------------------------------------------------
print("Building H3 res-8 grid ...")
cells: set[int] = set()
for geom in cat_zones.geometry:
    polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    for p in polys:
        if p.is_empty or p.area == 0:
            continue
        lat_lng_poly = h3.LatLngPoly([(lat, lon) for lon, lat in p.exterior.coords])
        cells.update(h3.polygon_to_cells(lat_lng_poly, 8))
cell_list = sorted(cells)
print(f"  {len(cell_list):,} unique hex cells")

hex_records = []
for c in cell_list:
    lat, lon = h3.cell_to_latlng(c)
    hex_records.append({"h3_id": c, "lon_centroid": lon, "lat_centroid": lat})
hex_df = pd.DataFrame(hex_records)
hex_geom = gpd.GeoSeries.from_xy(hex_df["lon_centroid"], hex_df["lat_centroid"], crs="EPSG:4326")
hexes = gpd.GeoDataFrame(hex_df, geometry=hex_geom)

# 3. Train reach (outer buffer -> 10 km) ------------------------------------
print(f"Train reach (inner {INNER_REACH_DEG:.3f} deg / outer {OUTER_REACH_DEG:.3f} deg) ...")
stations = pd.read_parquet(REPO / "data/bronze/osm/stations.parquet")
stations_gdf = gpd.GeoDataFrame(
    stations, geometry=gpd.points_from_xy(stations["lon"], stations["lat"]), crs="EPSG:4326")
union_outer = stations_gdf.buffer(OUTER_REACH_DEG).union_all()
union_inner = stations_gdf.buffer(INNER_REACH_DEG).union_all()
hexes["train_reach_min"] = np.where(
    hexes.geometry.within(union_inner), 15,
    np.where(hexes.geometry.within(union_outer), 25, np.nan),
)
n_reach = int(hexes["train_reach_min"].notna().sum())
print(f"  hexes within outer train reach: {n_reach:,} ({n_reach/len(hexes):.1%})")

# 4. POI distances (cap -> 10 km) -------------------------------------------
print(f"POI distances (climbing, yoga, hospital; cap {CATCHMENT_M} m) ...")
pois = pd.read_parquet(REPO / "data/bronze/osm/pois.parquet")
pois_gdf = gpd.GeoDataFrame(
    pois, geometry=gpd.points_from_xy(pois["lon"], pois["lat"]), crs="EPSG:4326")
hex_m = hexes.to_crs(epsg=25831)
pois_m = pois_gdf.to_crs(epsg=25831)
for cat, label in [("climbing", "climb_min_m"), ("yoga", "yoga_min_m"), ("hospital", "hospital_min_m")]:
    sub = pois_m.loc[pois_m["category"] == cat, "geometry"]
    if sub.empty:
        hexes[label] = np.nan
        print(f"  {cat:>9}: 0 POIs (skipped)")
        continue
    nearest = gpd.sjoin_nearest(
        hex_m, gpd.GeoDataFrame(geometry=sub.reset_index(drop=True), crs=hex_m.crs),
        how="left", distance_col="_dist", max_distance=CATCHMENT_M)
    dist_by_h3 = nearest.groupby("h3_id")["_dist"].min()
    hexes[label] = hexes["h3_id"].map(dist_by_h3)
    n_within = int(hexes[label].notna().sum())
    print(f"  {cat:>9}: {len(sub):>5} POIs / {n_within:,}/{len(hexes):,} hexes hit")

# 5. MITMA disaggregation ---------------------------------------------------
print("MITMA centroid -> distrito ...")
cat_4326 = cat_zones[["ID", "geometry"]].rename(columns={"ID": "id"})
sj = gpd.sjoin(hexes, cat_4326, how="left", predicate="within")
hex_to_zone = sj[["h3_id", "id"]].dropna()
mitma = pd.read_parquet(REPO / "data/bronze/mitma_parquet/daily", columns=["origen", "destino", "viajes"])
inflow = mitma.groupby("destino")["viajes"].sum().rename("viajes_in")
outflow = mitma.groupby("origen")["viajes"].sum().rename("viajes_out")
merged = (hex_to_zone
          .merge(inflow, left_on="id", right_index=True, how="left")
          .merge(outflow, left_on="id", right_index=True, how="left"))
merged["mitma_through_ratio"] = merged["viajes_out"].fillna(0) / merged["viajes_in"].replace(0, np.nan)
m_idx = merged.set_index("h3_id")
hexes["mitma_inflow_daily"] = hexes["h3_id"].map(m_idx["viajes_in"])
hexes["mitma_outflow_daily"] = hexes["h3_id"].map(m_idx["viajes_out"])
hexes["mitma_through_ratio"] = hexes["h3_id"].map(m_idx["mitma_through_ratio"])

# 6. Penalties (motorway 500 m / industry 1 km -- unchanged, not catchment) -
print("Motorway / industry penalties ...")
network = pd.read_parquet(REPO / "data/bronze/osm/network.parquet")
network_gdf = gpd.GeoDataFrame(
    network, geometry=[_wkt.loads(w) for w in network["wkt"]], crs="EPSG:4326")
moto = network_gdf.loc[network_gdf["subtype"].isin(["motorway", "trunk"])].to_crs(epsg=25831)
moto_union = moto.buffer(500).union_all()
hex_m_centroid = hexes.to_crs(epsg=25831)
hexes["motorway_within_500m"] = hex_m_centroid.geometry.within(moto_union)
ind = pois_m.loc[pois_m["category"] == "industry", ["geometry"]].reset_index(drop=True)
if ind.empty:
    hexes["industry_density_per_km2"] = 0
else:
    hex_buf_1km = gpd.GeoDataFrame(
        {"h3_id": hexes["h3_id"].values}, geometry=hex_m_centroid.buffer(1000), crs=hex_m_centroid.crs)
    hits = gpd.sjoin(hex_buf_1km, ind, how="inner", predicate="contains")
    ind_counts = hits.groupby("h3_id").size()
    hexes["industry_density_per_km2"] = hexes["h3_id"].map(ind_counts).fillna(0).astype(int)

# 7. Write ------------------------------------------------------------------
out = hexes.drop(columns="geometry").copy()
def _to_hex_str(c):
    return c if isinstance(c, str) else h3.int_to_str(int(c))
out["h3_id"] = out["h3_id"].apply(_to_hex_str)
out_path = GOLD / "h3_res8_catalonia_10km.parquet"
out.to_parquet(out_path, index=False)
print(f"-> wrote {out_path}  ({len(out):,} hexes, {out_path.stat().st_size/1e6:.2f} MB)")
