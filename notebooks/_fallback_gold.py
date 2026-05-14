"""Fallback gold-layer builder — pure pandas / geopandas / h3-py.

If notebook 02 (Sedona path) keeps tripping on JTS-internal access or
topology issues, run this instead. It produces an equivalent
`data/gold/h3_res8_catalonia.parquet`, slower but completely without
Sedona's spatial-join machinery.

Usage:  PYTHONPATH=src python notebooks/_fallback_gold.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from shapely.geometry import Point, Polygon

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


def _hex_to_polygon(cell: int) -> Polygon:
    boundary = h3.cell_to_boundary(h3.int_to_str(cell))
    return Polygon([(lon, lat) for lat, lon in boundary])


def main() -> None:
    silver = REPO / "data/silver"
    gold = REPO / "data/gold"
    silver.mkdir(parents=True, exist_ok=True)
    gold.mkdir(parents=True, exist_ok=True)

    # ── 1. Zones (Catalonia only, validated) ──────────────────────────────
    print("Loading zones …")
    zones = gpd.read_file(REPO / "data/bronze/mitma/zones/zonificacion_distritos.geojson")
    zones = zones.loc[zones.geometry.notna()].copy()
    zones["geometry"] = zones.geometry.make_valid()
    zones = zones.set_crs("EPSG:4326", allow_override=True)
    cat_mask = zones["ID"].astype(str).str[:2].isin(["08", "17", "25", "43"])
    cat_zones = zones.loc[cat_mask].copy()
    print(f"  {len(zones):,} total zones · {len(cat_zones):,} in Catalonia")

    # ── 2. H3 cell grid from Catalonia polygons ───────────────────────────
    print("Building H3 res-8 grid …")
    cells: set[int] = set()
    for geom in cat_zones.geometry:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for p in polys:
            lat_lng_poly = h3.LatLngPoly(
                [(lat, lon) for lon, lat in p.exterior.coords]
            )
            cells.update(int(c, 16) if isinstance(c, str) else c
                         for c in h3.polygon_to_cells(lat_lng_poly, 8))
    cell_list = sorted(cells)
    print(f"  {len(cell_list):,} hex cells")

    hex_records = []
    for c in cell_list:
        cstr = h3.int_to_str(c)
        lat, lon = h3.cell_to_latlng(cstr)
        hex_records.append({"h3_id": cstr, "lon_centroid": lon, "lat_centroid": lat})
    hex_df = pd.DataFrame(hex_records)
    hex_geom = gpd.GeoSeries.from_xy(hex_df["lon_centroid"], hex_df["lat_centroid"],
                                     crs="EPSG:4326")
    hexes = gpd.GeoDataFrame(hex_df, geometry=hex_geom)
    print(f"  hex centroids dataframe ready")

    # ── 3. Train reach (5 km buffer around OSM railway stations) ──────────
    print("Train reach (5 km circular buffer) …")
    stations = pd.read_parquet(REPO / "data/bronze/osm/stations.parquet")
    stations_gdf = gpd.GeoDataFrame(
        stations, geometry=gpd.points_from_xy(stations["lon"], stations["lat"]),
        crs="EPSG:4326",
    )
    stations_gdf["buf_5km"] = stations_gdf.buffer(0.045)
    union_5km = stations_gdf["buf_5km"].union_all()
    stations_gdf["buf_3km"] = stations_gdf.buffer(0.027)
    union_3km = stations_gdf["buf_3km"].union_all()

    hexes["train_reach_min"] = np.where(
        hexes.geometry.within(union_3km), 15,
        np.where(hexes.geometry.within(union_5km), 25, np.nan),
    )
    print(f"  hexes covered (≤25 min): {int(hexes['train_reach_min'].notna().sum()):,}")

    # ── 4. Nearest POI distances ──────────────────────────────────────────
    print("POI distances (climbing, yoga, hospital) …")
    pois = pd.read_parquet(REPO / "data/bronze/osm/pois.parquet")
    pois_gdf = gpd.GeoDataFrame(
        pois, geometry=gpd.points_from_xy(pois["lon"], pois["lat"]),
        crs="EPSG:4326",
    )
    # Project to UTM 31N (meters) for distance calculations
    hex_m = hexes.to_crs(epsg=25831)
    pois_m = pois_gdf.to_crs(epsg=25831)

    for cat, label in [("climbing", "climb_min_m"),
                       ("yoga", "yoga_min_m"),
                       ("hospital", "hospital_min_m")]:
        sub = pois_m.loc[pois_m["category"] == cat, "geometry"]
        if sub.empty:
            hexes[label] = np.nan
            print(f"  {cat:>9}: 0 POIs (skipped)")
            continue
        # sjoin_nearest with max_distance to cap to 8 km
        nearest = gpd.sjoin_nearest(
            hex_m, gpd.GeoDataFrame(geometry=sub.reset_index(drop=True), crs=hex_m.crs),
            how="left", distance_col="_dist", max_distance=8000,
        )
        dist_by_h3 = nearest.groupby("h3_id")["_dist"].min()
        hexes[label] = hexes["h3_id"].map(dist_by_h3)
        n_within = int(hexes[label].notna().sum())
        print(f"  {cat:>9}: {len(sub):>5} POIs, {n_within:,}/{len(hexes):,} hexes hit")

    # ── 5. MITMA disaggregation — centroid containment ────────────────────
    print("MITMA centroid → distrito …")
    cat_zones_4326 = cat_zones[["ID", "geometry"]].rename(columns={"ID": "id"})
    sj = gpd.sjoin(hexes, cat_zones_4326, how="left", predicate="within")
    hex_to_zone = sj[["h3_id", "id"]].dropna()
    mitma = pd.read_parquet(REPO / "data/bronze/mitma_parquet/daily",
                            columns=["origen", "destino", "viajes"])
    inflow = mitma.groupby("destino")["viajes"].sum().rename("viajes_in")
    outflow = mitma.groupby("origen")["viajes"].sum().rename("viajes_out")
    merged = hex_to_zone.merge(inflow, left_on="id", right_index=True, how="left")
    merged = merged.merge(outflow, left_on="id", right_index=True, how="left")
    merged["mitma_through_ratio"] = (
        merged["viajes_out"].fillna(0) / merged["viajes_in"].replace(0, np.nan)
    )
    hexes["mitma_inflow_daily"] = hexes["h3_id"].map(merged.set_index("h3_id")["viajes_in"])
    hexes["mitma_outflow_daily"] = hexes["h3_id"].map(merged.set_index("h3_id")["viajes_out"])
    hexes["mitma_through_ratio"] = hexes["h3_id"].map(merged.set_index("h3_id")["mitma_through_ratio"])
    print(f"  hexes with MITMA disaggregation: {int(hexes['mitma_inflow_daily'].notna().sum()):,}")

    # ── 6. Motorway proximity + industry density (UTM, meters) ────────────
    print("Motorway / industry penalties …")
    network = pd.read_parquet(REPO / "data/bronze/osm/network.parquet")
    if "wkt" in network.columns:
        from shapely import wkt as _wkt
        network_gdf = gpd.GeoDataFrame(
            network, geometry=[_wkt.loads(w) for w in network["wkt"]],
            crs="EPSG:4326",
        )
    else:
        network_gdf = gpd.GeoDataFrame(network, crs="EPSG:4326")

    moto = network_gdf.loc[network_gdf["subtype"].isin(["motorway", "trunk"])].to_crs(epsg=25831)
    moto_buf = moto.buffer(500).union_all()
    moto_buf_gdf = gpd.GeoSeries([moto_buf], crs="EPSG:25831")
    hex_m_centroid = hexes.to_crs(epsg=25831)
    hexes["motorway_within_500m"] = hex_m_centroid.geometry.within(moto_buf)
    print(f"  hexes within 500 m of motorway/trunk: {int(hexes['motorway_within_500m'].sum()):,}")

    ind = pois_m.loc[pois_m["category"] == "industry"]
    hex_buf_1km = hex_m_centroid.buffer(1000)
    counts = gpd.sjoin(
        gpd.GeoDataFrame(geometry=hex_buf_1km, data={"h3_id": hexes["h3_id"].values},
                         crs=hex_m_centroid.crs),
        ind, predicate="contains", how="left",
    )
    counts = counts.groupby("h3_id").size().rename("industry_density_per_km2")
    hexes["industry_density_per_km2"] = hexes["h3_id"].map(counts).fillna(0).astype(int)
    print(f"  hexes with industry POI ≤1 km: "
          f"{int((hexes['industry_density_per_km2'] > 0).sum()):,}")

    # ── 7. Drop geometry, write parquet ───────────────────────────────────
    out = hexes.drop(columns="geometry")
    out_path = gold / "h3_res8_catalonia.parquet"
    out.to_parquet(out_path, index=False)
    print(f"\n→ wrote {out_path}  ({len(out):,} hexes, "
          f"{out_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
