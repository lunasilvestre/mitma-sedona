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

# === v2.1 NEXT-WAVE ENRICHMENT (tree cover / Natura 2000 / E-PRTR / air) ====
# All distance/zonal math runs in EPSG:25831 metres; closeness_reward for
# benefits, saturating penalties for harms, NULL = neutral (scoring.py).
#
# Build H3 cell-BOUNDARY POLYGONS once (NOT the centroids in
# silver/hex_centroids.parquet). Reused for: tree_cover_pct (rasterstats zonal
# mean, honor nodata=255) and natura2000_within_5km (reproject -> union ->
# buffer(5000).intersects). cell_to_boundary returns (lat, lon) pairs.
print("Building H3 cell-boundary polygons (for zonal/buffer features) ...")
from shapely.geometry import Polygon  # noqa: E402

# hexes["h3_id"] holds int cell IDs at this point (string conversion happens
# only at write time in section 7); h3.cell_to_boundary accepts the int form.
hex_poly_geom = [
    Polygon([(lon, lat) for lat, lon in h3.cell_to_boundary(c)])
    for c in hexes["h3_id"]
]
hex_polys = gpd.GeoDataFrame(
    {"h3_id": hexes["h3_id"].values}, geometry=hex_poly_geom, crs="EPSG:4326")
hex_polys_m = hex_polys.to_crs(epsg=25831)

# 4f. tree_cover_pct — zonal mean of the EEA TCD raster per H3 cell ----------
# Raster: data/bronze/treecover/tcd_2018_catalonia.tif (EPSG:4326, U8 0-100,
# nodata=255 ~ sea/outside). Positive nature reward (weight tree_cover_pct).
print("Tree cover (zonal mean per H3 cell, nodata=255) ...")
from rasterstats import zonal_stats  # noqa: E402

TCD_RASTER = REPO / "data/bronze/treecover/tcd_2018_catalonia.tif"
# rasterstats needs the polygons in the raster CRS (EPSG:4326).
tc_stats = zonal_stats(hex_polys, str(TCD_RASTER), stats=["mean"], nodata=255, all_touched=False)
hexes["tree_cover_pct"] = [s["mean"] for s in tc_stats]
n_tc = int(hexes["tree_cover_pct"].notna().sum())
print(f"  tree_cover: {n_tc:,}/{len(hexes):,} hexes with a value "
      f"(median {np.nanmedian(hexes['tree_cover_pct']):.1f}%, "
      f"max {np.nanmax(hexes['tree_cover_pct']):.1f}%)")

# 4g. natura2000_within_5km — hex within 5 km of a Natura 2000 site ----------
# data/bronze/natura2000/natura2000_catalonia.parquet (231 sites, WGS84,
# EEA cols SITECODE/SITENAME/SITETYPE — NOT WDPA; no filter_wdpa helper).
# Boolean positive nature bonus (weight natura2000_within_5km).
# LAND-CLIP: the raw set includes huge MARINE SPAs (e.g. ES0000512 Delta de
# l'Ebre marine, 9017 km2) that inflate the union to ~97% of Catalonia. Clip to
# the Catalonia land union (cat_zones) so the flag means "near a TERRESTRIAL
# protected area" (clipped union 9762 km2 ~ 30% of land, the real figure).
print("Natura 2000 within 5 km (land-clipped; buffer(5000).intersects union) ...")
land_union = cat_zones.to_crs(epsg=25831).geometry.union_all()
n2k_raw = gpd.read_parquet(REPO / "data/bronze/natura2000/natura2000_catalonia.parquet").to_crs(epsg=25831)
n2k_clipped = n2k_raw.geometry.intersection(land_union)
n2k_clipped = n2k_clipped[~n2k_clipped.is_empty]
n2k_union = n2k_clipped.union_all()
print(f"  natura land-clipped union: {n2k_union.area/1e6:.0f} km2 "
      f"({n2k_union.area/land_union.area:.1%} of land; raw {n2k_raw.geometry.union_all().area/1e6:.0f} km2 incl. marine)")
# Buffer each hex polygon by 5 km and test intersection with the protected union.
hexes["natura2000_within_5km"] = hex_polys_m.geometry.buffer(5000).intersects(n2k_union).values
n_n2k = int(hexes["natura2000_within_5km"].sum())
print(f"  natura2000: {len(n2k_raw):,} sites / {n_n2k:,}/{len(hexes):,} hexes within 5 km "
      f"({n_n2k/len(hexes):.1%})")

# 4h. eprtr_facility_min_m — nearest E-PRTR facility distance (PENALTY) ------
# data/bronze/pollution/eprtr/spain.csv -> parse_eprtr_facilities (clips to
# Catalonia bbox, renames Longitude/Latitude). Multi-pollutant rows -> dedup to
# one point per facility_id. Nearest distance, cap 50 km. closer = worse
# (scoring: (1/dist)*eprtr_inverse_dist, negative weight).
print("E-PRTR nearest-facility distance (cap 50 km; PENALTY) ...")
from catmob.io_pollution import parse_eprtr_facilities  # noqa: E402

EPRTR_CAP_M = 50_000.0
fac = parse_eprtr_facilities(REPO / "data/bronze/pollution/eprtr/spain.csv")
fac = fac.drop_duplicates("facility_id").reset_index(drop=True)  # one point per facility
fac_gdf = gpd.GeoDataFrame(
    fac, geometry=gpd.points_from_xy(fac["lon"], fac["lat"]), crs="EPSG:4326"
).to_crs(epsg=25831)
nearest = gpd.sjoin_nearest(
    hex_m, fac_gdf[["geometry"]].reset_index(drop=True),
    how="left", distance_col="_dist", max_distance=EPRTR_CAP_M)
hexes["eprtr_facility_min_m"] = hexes["h3_id"].map(nearest.groupby("h3_id")["_dist"].min())
n_ep = int(hexes["eprtr_facility_min_m"].notna().sum())
print(f"  eprtr: {len(fac_gdf):,} facilities / {n_ep:,}/{len(hexes):,} hexes within 50 km "
      f"(median {np.nanmedian(hexes['eprtr_facility_min_m']):.0f} m)")

# 4i. no2_ugm3 (+ pm25_ugm3) — XVPCA nearest-station annual mean (PENALTY) ----
# data/bronze/air/xvpca/xvpca_hourly_2024.csv is HOURLY long-form (h01..h24,
# codi_eoi/latitud/longitud). Melt -> per-station+contaminant annual mean, then
# nearest-station join (NO2 steep-gradient/traffic). Penalty above WHO 2021
# annual thresholds applied in scoring.py (NO2 20, PM2.5 5 ug/m3).
print("Air quality (XVPCA hourly -> annual station mean -> nearest join) ...")
AIR_JOIN_CAP_M = 15_000.0  # nearest-station catchment (dense ~140-station net)
air = pd.read_csv(REPO / "data/bronze/air/xvpca/xvpca_hourly_2024.csv")
hour_cols = [c for c in air.columns if c.lower().startswith("h") and c[1:].isdigit()]
# Long-melt the 24 hourly readings, then station+contaminant annual mean.
air_long = air.melt(
    id_vars=["codi_eoi", "latitud", "longitud", "contaminant"],
    value_vars=hour_cols, value_name="val").dropna(subset=["val"])
air_long["val"] = pd.to_numeric(air_long["val"], errors="coerce")
station_mean = (air_long.dropna(subset=["val"])
                .groupby(["codi_eoi", "latitud", "longitud", "contaminant"])["val"]
                .mean().reset_index())


def _join_pollutant(contaminant: str, out_col: str) -> None:
    sub = station_mean[station_mean["contaminant"] == contaminant].copy()
    if sub.empty:
        hexes[out_col] = np.nan
        print(f"  {out_col:>10}: 0 stations (NaN)")
        return
    st = gpd.GeoDataFrame(
        sub, geometry=gpd.points_from_xy(sub["longitud"], sub["latitud"]), crs="EPSG:4326"
    ).to_crs(epsg=25831)[["val", "geometry"]].reset_index(drop=True)
    nn = gpd.sjoin_nearest(hex_m, st, how="left", distance_col="_d", max_distance=AIR_JOIN_CAP_M)
    # one station per hex (nearest); first() after the groupby min-distance order
    nn = nn.sort_values("_d").drop_duplicates("h3_id")
    hexes[out_col] = hexes["h3_id"].map(nn.set_index("h3_id")["val"])
    n = int(hexes[out_col].notna().sum())
    print(f"  {out_col:>10}: {len(st):,} stations / {n:,}/{len(hexes):,} hexes "
          f"(median {np.nanmedian(hexes[out_col]):.1f} ug/m3)")


_join_pollutant("NO2", "no2_ugm3")
_join_pollutant("PM2.5", "pm25_ugm3")

# === v2.3 NEXT-WAVE ENRICHMENT (biodiversity / VIIRS / LST→UHI / GTFS) ======
# scoring.py already carries the weight terms for all 4; no formula change. The
# H3 cell-boundary POLYGONS (hex_polys / hex_polys_m) built above are reused for
# the two raster-zonal layers (VIIRS in 4326, LST in 25831).


def _to_hex_str(c):
    """int H3 cell id -> hex string (idempotent if already a str)."""
    return c if isinstance(c, str) else h3.int_to_str(int(c))

# 4j. biodiversity_obs_density — GBIF/iNaturalist obs count per H3 / hex area --
# data/bronze/biodiversity/gbif_occurrences.parquet (50k research-grade recs,
# lon/lat). h3 v4 latlng_to_cell per record -> count -> density. NATURE reward
# (scoring: log1p(density)*biodiversity_obs_log; log1p absorbs citizen-science
# clustering bias). Divisor is the precise res-8 hex area (km²).
print("Biodiversity obs density (GBIF iNaturalist count / hex area) ...")
RES8_HEX_KM2_EXACT = 0.737327
obs = pd.read_parquet(REPO / "data/bronze/biodiversity/gbif_occurrences.parquet")
obs["h3_id"] = [
    h3.latlng_to_cell(la, lo, 8) for la, lo in zip(obs["lat"].to_numpy(), obs["lon"].to_numpy())
]
bio_counts = obs.groupby("h3_id").size()
hexes["biodiversity_obs_density"] = (
    hexes["h3_id"].map(bio_counts).fillna(0) / RES8_HEX_KM2_EXACT
)
n_bio = int((hexes["biodiversity_obs_density"] > 0).sum())
print(f"  biodiversity: {len(obs):,} obs / {n_bio:,}/{len(hexes):,} hexes with >=1 obs "
      f"(max {hexes['biodiversity_obs_density'].max():.1f}/km2)")
pd.DataFrame({
    "h3_id": hexes["h3_id"].apply(_to_hex_str),
    "biodiversity_obs_density": hexes["biodiversity_obs_density"].values,
}).to_parquet(SILVER / "biodiversity_per_hex.parquet", index=False)

# 4k. viirs_radiance — zonal mean of the VIIRS NTL COG per H3 cell (PENALTY) --
# data/bronze/pollution/viirs/viirs_ntl_2024_catalonia.tif (COG EPSG:4326,
# float32, fill=-3.402823e38). io_pollution.compute_viirs_radiance_per_hex does
# rasterstats zonal mean (all_touched, clamp max(0,v)) over the 4326 hex polys —
# raster + polys both 4326, NO reprojection. Light-pollution PENALTY tiebreaker.
print("VIIRS radiance (zonal mean per H3 cell, EPSG:4326, no reproject) ...")
from catmob.io_pollution import VIIRS_CATALONIA_TIF, compute_viirs_radiance_per_hex  # noqa: E402

viirs = compute_viirs_radiance_per_hex(REPO / VIIRS_CATALONIA_TIF, hex_polys)
hexes["viirs_radiance"] = hexes["h3_id"].map(viirs.set_index("h3_id")["viirs_radiance"])
n_v = int(hexes["viirs_radiance"].notna().sum())
print(f"  viirs: {n_v:,}/{len(hexes):,} hexes with a value "
      f"(median {np.nanmedian(hexes['viirs_radiance']):.2f}, max {np.nanmax(hexes['viirs_radiance']):.1f})")
pd.DataFrame({
    "h3_id": hexes["h3_id"].apply(_to_hex_str),
    "viirs_radiance": hexes["viirs_radiance"].values,
}).to_parquet(SILVER / "viirs_per_hex.parquet", index=False)

# 4l. lst_summer_median_c -> uhi_delta_c — zonal mean LST per H3 cell ----------
# data/bronze/thermal/lst_summer_jja_2024.tif (COG EPSG:25831, °C, fill=NaN,
# composite already built). io_thermal.lst_zonal_mean_per_hex with nodata=NaN +
# the 25831 hex POLYGONS. uhi_delta_c = lst - rural baseline (global 0.25 qtile).
# UHI PENALTY in scoring (max(0,uhi)*uhi_per_degree).
print("LST summer median -> UHI delta (zonal mean per H3 cell, EPSG:25831) ...")
from catmob.io_thermal import lst_zonal_mean_per_hex  # noqa: E402

LST_RASTER = REPO / "data/bronze/thermal/lst_summer_jja_2024.tif"
lst_per_hex = lst_zonal_mean_per_hex(LST_RASTER, hex_polys_m)  # 25831 polys
hexes["lst_summer_median_c"] = hexes["h3_id"].map(lst_per_hex.set_index("h3_id")["lst_summer_median_c"])
hexes["uhi_delta_c"] = hexes["h3_id"].map(lst_per_hex.set_index("h3_id")["uhi_delta_c"])
n_lst = int(hexes["lst_summer_median_c"].notna().sum())
print(f"  lst: {n_lst:,}/{len(hexes):,} hexes "
      f"(median {np.nanmedian(hexes['lst_summer_median_c']):.1f} C, "
      f"uhi median {np.nanmedian(hexes['uhi_delta_c']):.1f} C, "
      f"uhi max {np.nanmax(hexes['uhi_delta_c']):.1f} C)")
pd.DataFrame({
    "h3_id": hexes["h3_id"].apply(_to_hex_str),
    "lst_summer_median_c": hexes["lst_summer_median_c"].values,
    "uhi_delta_c": hexes["uhi_delta_c"].values,
}).to_parquet(SILVER / "lst_per_hex.parquet", index=False)

# 4m. trains_per_day_nearest + trains_to_bcn_nearest — GTFS nearest-stop join --
# data/bronze/gtfs/{rodalies,fgc}/. io_gtfs.load_combined (3 fixes: col-strip,
# national-bbox prefilter, FGC calendar_dates fallback) -> stops + per-stop freq.
# sjoin_nearest hex centroid -> nearest stop, map trips_per_day (fillna 12) and
# trips_to_bcn_core (fillna 0). Both feed trains_to_bcn_per_30 weight.
print("GTFS frequency (load_combined -> nearest-stop trips/day + trips->BCN) ...")
from catmob import io_gtfs  # noqa: E402

g = io_gtfs.load_combined(REPO / "data/bronze/gtfs/rodalies", REPO / "data/bronze/gtfs/fgc")
stops_m = gpd.GeoDataFrame(
    g["stops"], geometry=gpd.points_from_xy(g["stops"]["lon"], g["stops"]["lat"]),
    crs="EPSG:4326").to_crs(epsg=25831)[["stop_id", "geometry"]].reset_index(drop=True)
nn_stop = gpd.sjoin_nearest(hex_m, stops_m, how="left", distance_col="_d")
nn_stop = nn_stop.sort_values("_d").drop_duplicates("h3_id")
nearest_stop_id = hexes["h3_id"].map(nn_stop.set_index("h3_id")["stop_id"])
freq_idx = g["freq"].set_index("stop_id")
hexes["trains_per_day_nearest"] = (
    nearest_stop_id.map(freq_idx["trips_per_day"]).fillna(12).astype(int)
)
hexes["trains_to_bcn_nearest"] = (
    nearest_stop_id.map(freq_idx["trips_to_bcn_core"]).fillna(0).astype(int)
)
print(f"  gtfs: {len(g['stops']):,} stops / {len(g['freq']):,} with freq; "
      f"trains_per_day median {int(hexes['trains_per_day_nearest'].median())}, "
      f"trains_to_bcn median {int(hexes['trains_to_bcn_nearest'].median())}, "
      f"max {int(hexes['trains_to_bcn_nearest'].max())}")
pd.DataFrame({
    "h3_id": hexes["h3_id"].apply(_to_hex_str),
    "trains_per_day_nearest": hexes["trains_per_day_nearest"].values,
    "trains_to_bcn_nearest": hexes["trains_to_bcn_nearest"].values,
}).to_parquet(SILVER / "gtfs_frequency.parquet", index=False)

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
out["h3_id"] = out["h3_id"].apply(_to_hex_str)
out_path = GOLD / "h3_res8_catalonia_v2.parquet"
out.to_parquet(out_path, index=False)
print(f"-> wrote {out_path}  ({len(out):,} hexes, {out_path.stat().st_size/1e6:.2f} MB)")
print("Columns:", list(out.columns))
