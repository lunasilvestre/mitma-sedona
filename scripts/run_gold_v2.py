#!/usr/bin/env python
"""v2 gold liveability layer — correctness fix + Wave-1 enrichment.

Builds on scripts/run_gold_10km.py (10 km catchment). Two correctness
upgrades over v1/_10km:

  (0) EPSG:25831 REPROJECTION FIX — train_reach buffers are now metric
      (5000 m / 3000 m in EPSG:25831), not the v1 degree buffers
      (0.045 deg / 0.027 deg) which were ~25% anisotropic at 41 N.
      Every distance/buffer feature runs in EPSG:25831 metres.

  (3) WAVE-1 enrichment, 5 features (all data on disk):
        - train_reach_min  (reproject-fixed, above)
        - green_min_m      data/bronze/nature/green_polys.parquet
        - sea_min_m        data/bronze/nature/coastline.parquet
        - pharmacy_density_per_km2  data/bronze/osm/pois.parquet
        - hospital_min_m   OSM pois UNION CatSalut hospitals.csv (coverage merge)

NON-DESTRUCTIVE: writes data/gold/h3_res8_catalonia_v2.parquet; leaves the
v1 (h3_res8_catalonia.parquet) and _10km parquets untouched.

Run with the sedona env python:
    /home/nls/miniforge3/envs/sedona/bin/python scripts/run_gold_v2.py
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

CATCHMENT_M = 10_000.0           # closeness-reward / amenity-cap edge
TRAIN_OUTER_M = 5_000.0          # 25-min reach (metric; was 0.045 deg)
TRAIN_INNER_M = 3_000.0          # 15-min reach (metric; was 0.027 deg)
RES8_HEX_KM2 = 0.735             # res-8 hex area, for density normalisation

SILVER = REPO / "data/silver"
GOLD = REPO / "data/gold"
SILVER.mkdir(parents=True, exist_ok=True)
GOLD.mkdir(parents=True, exist_ok=True)

print("=== v2 gold re-run (reproj fix + Wave-1 enrichment) ===")

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

# Single metric working copy reused by every distance/buffer feature below.
hex_m = hexes.to_crs(epsg=25831)
hex_m_centroid = hex_m  # centroid points in EPSG:25831 (alias for clarity)

# 3. Train reach (METRIC buffers — EPSG:25831 reproj fix) --------------------
# v1/_10km bug: stations buffered in EPSG:4326 degrees -> ~25% E-W anisotropy
# at 41 N. Fix: reproject stations to 25831, buffer in metres.
print(f"Train reach (metric: inner {TRAIN_INNER_M:.0f} m / outer {TRAIN_OUTER_M:.0f} m) ...")
stations = pd.read_parquet(REPO / "data/bronze/osm/stations.parquet")
stations_gdf = gpd.GeoDataFrame(
    stations, geometry=gpd.points_from_xy(stations["lon"], stations["lat"]), crs="EPSG:4326")
stations_m = stations_gdf.to_crs(epsg=25831)
union_inner = stations_m.buffer(TRAIN_INNER_M).union_all()
union_outer = stations_m.buffer(TRAIN_OUTER_M).union_all()
hexes["train_reach_min"] = np.where(
    hex_m.geometry.within(union_inner), 15,
    np.where(hex_m.geometry.within(union_outer), 25, np.nan),
)
n_reach = int(hexes["train_reach_min"].notna().sum())
print(f"  hexes within outer (5 km) train reach: {n_reach:,} ({n_reach/len(hexes):.1%})")

# 4. POI distances (climbing, yoga; metric, cap 10 km) ----------------------
print(f"POI distances (climbing, yoga; cap {CATCHMENT_M:.0f} m) ...")
pois = pd.read_parquet(REPO / "data/bronze/osm/pois.parquet")
pois_gdf = gpd.GeoDataFrame(
    pois, geometry=gpd.points_from_xy(pois["lon"], pois["lat"]), crs="EPSG:4326")
pois_m = pois_gdf.to_crs(epsg=25831)


def _nearest_min_m(points_m, label, cap=CATCHMENT_M):
    """Nearest-distance (m) from each hex centroid to a point layer, capped."""
    if len(points_m) == 0:
        hexes[label] = np.nan
        print(f"  {label:>28}: 0 features (NaN)")
        return
    nearest = gpd.sjoin_nearest(
        hex_m, gpd.GeoDataFrame(geometry=points_m.reset_index(drop=True), crs=hex_m.crs),
        how="left", distance_col="_dist", max_distance=cap)
    dist_by_h3 = nearest.groupby("h3_id")["_dist"].min()
    hexes[label] = hexes["h3_id"].map(dist_by_h3)
    n = int(hexes[label].notna().sum())
    print(f"  {label:>28}: {len(points_m):>6} feats / {n:,}/{len(hexes):,} hexes hit "
          f"(median {np.nanmedian(hexes[label]):.0f} m)")


_nearest_min_m(pois_m.loc[pois_m["category"] == "climbing", "geometry"], "climb_min_m")
_nearest_min_m(pois_m.loc[pois_m["category"] == "yoga", "geometry"], "yoga_min_m")

# 4b. hospital_min_m — OSM ∪ CatSalut coverage merge ------------------------
print("Hospital distance (OSM pois UNION CatSalut hospitals.csv) ...")
osm_h = pois_m.loc[pois_m["category"] == "hospital", ["geometry"]].reset_index(drop=True)
cs = pd.read_csv(REPO / "data/bronze/health/catsalut/hospitals.csv")
cs_gdf = gpd.GeoDataFrame(
    cs, geometry=gpd.points_from_xy(cs["longitud"], cs["latitud"]), crs="EPSG:4326"
).to_crs(epsg=25831)
hosp = pd.concat(
    [osm_h, cs_gdf[["geometry"]].reset_index(drop=True)], ignore_index=True
)
hosp = gpd.GeoDataFrame(hosp, geometry="geometry", crs=hex_m.crs).drop_duplicates("geometry")
print(f"  OSM {len(osm_h)} + CatSalut {len(cs_gdf)} -> {len(hosp)} unique hospital points")
_nearest_min_m(hosp.geometry, "hospital_min_m")

# 4c. green_min_m — nearest green polygon (metric) --------------------------
print("Green distance (green_polys.parquet) ...")
green = gpd.read_parquet(REPO / "data/bronze/nature/green_polys.parquet")
green = green[green.geom_type.isin(["Polygon", "MultiPolygon"])]   # drop 151 GeometryCollection
green_m = green.to_crs(epsg=25831)[["geometry"]].reset_index(drop=True)
nearest = gpd.sjoin_nearest(hex_m, green_m, how="left", distance_col="_dist", max_distance=CATCHMENT_M)
hexes["green_min_m"] = hexes["h3_id"].map(nearest.groupby("h3_id")["_dist"].min())
n = int(hexes["green_min_m"].notna().sum())
print(f"  green: {len(green_m):,} polys / {n:,}/{len(hexes):,} hexes hit "
      f"(median {np.nanmedian(hexes['green_min_m']):.0f} m)")

# 4d. sea_min_m — nearest coastline (metric, cap 5 km is the bonus edge) -----
print("Sea distance (coastline.parquet) ...")
coast = gpd.read_parquet(REPO / "data/bronze/nature/coastline.parquet").to_crs(epsg=25831)
nearest = gpd.sjoin_nearest(
    hex_m, coast[["geometry"]].reset_index(drop=True),
    how="left", distance_col="_dist", max_distance=5000)
hexes["sea_min_m"] = hexes["h3_id"].map(nearest.groupby("h3_id")["_dist"].min())
n = int(hexes["sea_min_m"].notna().sum())
print(f"  sea: {len(coast):,} lines / {n:,}/{len(hexes):,} hexes within 5 km "
      f"(median {np.nanmedian(hexes['sea_min_m']):.0f} m)")

# 4e. pharmacy_density_per_km2 — count within 1 km / hex area ----------------
print("Pharmacy density (within 1 km buffer / hex area) ...")
pharm = pois_m.loc[pois_m["category"] == "pharmacy", ["geometry"]].reset_index(drop=True)
hex_buf_1km = gpd.GeoDataFrame(
    {"h3_id": hexes["h3_id"].values}, geometry=hex_m_centroid.buffer(1000), crs=hex_m_centroid.crs)
hits = gpd.sjoin(hex_buf_1km, pharm, predicate="contains")
counts = hits.groupby("h3_id").size()
hexes["pharmacy_density_per_km2"] = hexes["h3_id"].map(counts).fillna(0) / RES8_HEX_KM2
n = int((hexes["pharmacy_density_per_km2"] > 0).sum())
print(f"  pharmacy: {len(pharm):,} nodes / {n:,} hexes with >=1 within 1 km "
      f"(max {hexes['pharmacy_density_per_km2'].max():.1f}/km2)")

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

# 6. Penalties (motorway 500 m / industry 1 km — metric, unchanged) ---------
print("Motorway / industry penalties ...")
network = pd.read_parquet(REPO / "data/bronze/osm/network.parquet")
network_gdf = gpd.GeoDataFrame(
    network, geometry=[_wkt.loads(w) for w in network["wkt"]], crs="EPSG:4326")
moto = network_gdf.loc[network_gdf["subtype"].isin(["motorway", "trunk"])].to_crs(epsg=25831)
moto_union = moto.buffer(500).union_all()
hexes["motorway_within_500m"] = hex_m_centroid.geometry.within(moto_union)
ind = pois_m.loc[pois_m["category"] == "industry", ["geometry"]].reset_index(drop=True)
if ind.empty:
    hexes["industry_density_per_km2"] = 0
else:
    hex_buf_ind = gpd.GeoDataFrame(
        {"h3_id": hexes["h3_id"].values}, geometry=hex_m_centroid.buffer(1000), crs=hex_m_centroid.crs)
    hits = gpd.sjoin(hex_buf_ind, ind, how="inner", predicate="contains")
    ind_counts = hits.groupby("h3_id").size()
    hexes["industry_density_per_km2"] = hexes["h3_id"].map(ind_counts).fillna(0).astype(int)
print(f"  motorway hexes: {int(hexes['motorway_within_500m'].sum()):,} / "
      f"industry hexes: {int((hexes['industry_density_per_km2'] > 0).sum()):,}")

# 7. Write ------------------------------------------------------------------
out = hexes.drop(columns="geometry").copy()


def _to_hex_str(c):
    return c if isinstance(c, str) else h3.int_to_str(int(c))


out["h3_id"] = out["h3_id"].apply(_to_hex_str)
out_path = GOLD / "h3_res8_catalonia_v2.parquet"
out.to_parquet(out_path, index=False)
print(f"-> wrote {out_path}  ({len(out):,} hexes, {out_path.stat().st_size/1e6:.2f} MB)")
print("Columns:", list(out.columns))
