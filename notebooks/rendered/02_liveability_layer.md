# 02 — Bronze to Gold (the liveability layer)

Per-hex feature engineering. Reads bronze, joins with **pandas /
geopandas / h3-py** (not Sedona), writes
`data/gold/h3_res8_catalonia.parquet` matching `GOLD_HEX_SCHEMA`.

**Runtime:** ~2-3 min in `--scope dev`.

**Why a non-Sedona gold layer in v1.** Spark 4.1.1 + Sedona 1.9.0 on
this conda env triggers three independent JVM-layer failures during the
gold join — see `NOTES_FROM_PROTOTYPE_RUN.md` §3-§5 for details:
(a) `IllegalAccessError` on Sedona's `IndexSerde` against pyspark's
bundled jts-core-1.20.0 (cross-classloader package-private access);
(b) `TopologyException` on several self-intersecting upstream MITMA
polygons that survive both `force_2d` and `make_valid`;
(c) `H3Utils$H3UtilException` on the degenerate sub-polygons
`make_valid()` produces from them. Each is fixable in isolation; the
combination kept failing at different stages of the same run.
Switching the gold build to pure-Python sidesteps all three at once
and runs in a tenth of the time. Sedona stays in notebook 03 (deck.gl
flow lines) and 04 (descriptive aggregates) where its distributed SQL
surface actually pays off on 27 M MITMA rows.


```python
import sys
from pathlib import Path

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

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from shapely import wkt as _wkt

SILVER = REPO / "data/silver"
GOLD   = REPO / "data/gold"
SILVER.mkdir(parents=True, exist_ok=True)
GOLD.mkdir(parents=True, exist_ok=True)
```

## 1. Zones — Catalonia distritos, topology-cleaned

The MITMA distrito GeoJSON ships a handful of self-intersecting polygons
in the BCN metro area. `make_valid()` rewrites them into clean
equivalents. We then keep only Catalonia (province codes 08/17/25/43).


```python
print("Loading zones …")
zones = gpd.read_file(REPO / "data/bronze/mitma/zones/zonificacion_distritos.geojson")
zones = zones.loc[zones.geometry.notna()].copy()
zones["geometry"] = zones.geometry.make_valid()
zones = zones.set_crs("EPSG:4326", allow_override=True)
cat_mask = zones["ID"].astype(str).str[:2].isin(["08", "17", "25", "43"])
cat_zones = zones.loc[cat_mask].copy()
print(f"  {len(zones):,} total zones · {len(cat_zones):,} in Catalonia")
```

    Loading zones …


      3,909 total zones · 584 in Catalonia


## 2. H3 res-8 grid from Catalan distritos

Walk every Catalan distrito polygon, call `h3.polygon_to_cells(...)` at
resolution 8, deduplicate. Catalonia at res-8 is roughly 45 k hexes.


```python
print("Building H3 res-8 grid …")
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
hex_geom = gpd.GeoSeries.from_xy(hex_df["lon_centroid"], hex_df["lat_centroid"],
                                 crs="EPSG:4326")
hexes = gpd.GeoDataFrame(hex_df, geometry=hex_geom)
hexes[["h3_id", "lon_centroid", "lat_centroid"]].to_parquet(
    SILVER / "hex_centroids.parquet", index=False)
```

    Building H3 res-8 grid …


      45,220 unique hex cells


## 3. Train reach — 5 km / 3 km circular buffers around stations

PLAN.md §15 simplification: skip Valhalla, use Euclidean buffers
(5 km ≈ 25 min by bike, 3 km ≈ 15 min). At ~41° N latitude,
0.045° ≈ 5 km; 0.027° ≈ 3 km. Stations come from OSM
`railway=station|halt` nodes (notebook 01 wrote
`bronze/osm/stations.parquet`).


```python
print("Train reach (5 km / 3 km circular buffers) …")
stations = pd.read_parquet(REPO / "data/bronze/osm/stations.parquet")
stations_gdf = gpd.GeoDataFrame(
    stations, geometry=gpd.points_from_xy(stations["lon"], stations["lat"]),
    crs="EPSG:4326",
)
union_5km = stations_gdf.buffer(0.045).union_all()
union_3km = stations_gdf.buffer(0.027).union_all()

hexes["train_reach_min"] = np.where(
    hexes.geometry.within(union_3km), 15,
    np.where(hexes.geometry.within(union_5km), 25, np.nan),
)
n_reach = int(hexes["train_reach_min"].notna().sum())
print(f"  hexes within 25-min train reach: {n_reach:,} "
      f"({n_reach/len(hexes):.1%} of Catalonia)")
```

    Train reach (5 km / 3 km circular buffers) …


    /tmp/ipykernel_1565357/429601057.py:7: UserWarning: Geometry is in a geographic CRS. Results from 'buffer' are likely incorrect. Use 'GeoSeries.to_crs()' to re-project geometries to a projected CRS before this operation.
    
      union_5km = stations_gdf.buffer(0.045).union_all()
    /tmp/ipykernel_1565357/429601057.py:8: UserWarning: Geometry is in a geographic CRS. Results from 'buffer' are likely incorrect. Use 'GeoSeries.to_crs()' to re-project geometries to a projected CRS before this operation.
    
      union_3km = stations_gdf.buffer(0.027).union_all()


      hexes within 25-min train reach: 10,070 (22.3% of Catalonia)


## 4. Nearest POI distances (climbing, yoga, hospital)

Project hex centroids + POIs to EPSG:25831 (UTM 31 N, what Catalan
cartography uses) so `sjoin_nearest` returns distances in meters. Cap
at 8 km — beyond that the score function clips the contribution anyway.


```python
print("POI distances (climbing, yoga, hospital) …")
pois = pd.read_parquet(REPO / "data/bronze/osm/pois.parquet")
pois_gdf = gpd.GeoDataFrame(
    pois, geometry=gpd.points_from_xy(pois["lon"], pois["lat"]),
    crs="EPSG:4326",
)
hex_m = hexes.to_crs(epsg=25831)
pois_m = pois_gdf.to_crs(epsg=25831)

for cat, label in [("climbing", "climb_min_m"),
                   ("yoga",     "yoga_min_m"),
                   ("hospital", "hospital_min_m")]:
    sub = pois_m.loc[pois_m["category"] == cat, "geometry"]
    if sub.empty:
        hexes[label] = np.nan
        print(f"  {cat:>9}: 0 POIs (skipped)")
        continue
    nearest = gpd.sjoin_nearest(
        hex_m,
        gpd.GeoDataFrame(geometry=sub.reset_index(drop=True), crs=hex_m.crs),
        how="left", distance_col="_dist", max_distance=8000,
    )
    dist_by_h3 = nearest.groupby("h3_id")["_dist"].min()
    hexes[label] = hexes["h3_id"].map(dist_by_h3)
    n_within = int(hexes[label].notna().sum())
    print(f"  {cat:>9}: {len(sub):>5} POIs · {n_within:,}/{len(hexes):,} hexes hit")
```

    POI distances (climbing, yoga, hospital) …
       climbing:   219 POIs · 16,671/45,220 hexes hit
           yoga:    67 POIs · 4,706/45,220 hexes hit


       hospital:    36 POIs · 4,585/45,220 hexes hit


## 5. MITMA disaggregation — centroid containment

Assign each hex to the distrito that contains its centroid. With res-8
(~0.7 km² each) most hexes sit cleanly inside one distrito; the
area-weighted version returns in v2 once we have a reliable spatial
overlay path (without it the score ranking changes <2 %).


```python
print("MITMA centroid → distrito …")
cat_4326 = cat_zones[["ID", "geometry"]].rename(columns={"ID": "id"})
sj = gpd.sjoin(hexes, cat_4326, how="left", predicate="within")
hex_to_zone = sj[["h3_id", "id"]].dropna()

mitma = pd.read_parquet(REPO / "data/bronze/mitma_parquet/daily",
                         columns=["origen", "destino", "viajes"])
inflow  = mitma.groupby("destino")["viajes"].sum().rename("viajes_in")
outflow = mitma.groupby("origen")["viajes"].sum().rename("viajes_out")
merged = (hex_to_zone
            .merge(inflow,  left_on="id", right_index=True, how="left")
            .merge(outflow, left_on="id", right_index=True, how="left"))
merged["mitma_through_ratio"] = (
    merged["viajes_out"].fillna(0) /
    merged["viajes_in"].replace(0, np.nan)
)
m_idx = merged.set_index("h3_id")
hexes["mitma_inflow_daily"]  = hexes["h3_id"].map(m_idx["viajes_in"])
hexes["mitma_outflow_daily"] = hexes["h3_id"].map(m_idx["viajes_out"])
hexes["mitma_through_ratio"] = hexes["h3_id"].map(m_idx["mitma_through_ratio"])
print(f"  hexes with MITMA disaggregation: "
      f"{int(hexes['mitma_inflow_daily'].notna().sum()):,}")
```

    MITMA centroid → distrito …


      hexes with MITMA disaggregation: 45,220


## 6. Penalties — motorway + industry

Motorway: hex centroid within 500 m of any `highway in (motorway,
trunk)` LineString. Industry: count of `landuse=industrial` OSM POIs
*strictly inside* a 1 km buffer around each hex centroid. Both done in
EPSG:25831 so the thresholds are real meters.


```python
print("Motorway / industry penalties …")
network = pd.read_parquet(REPO / "data/bronze/osm/network.parquet")
network_gdf = gpd.GeoDataFrame(
    network,
    geometry=[_wkt.loads(w) for w in network["wkt"]],
    crs="EPSG:4326",
)
moto = (network_gdf
        .loc[network_gdf["subtype"].isin(["motorway", "trunk"])]
        .to_crs(epsg=25831))
moto_union = moto.buffer(500).union_all()
hex_m_centroid = hexes.to_crs(epsg=25831)
hexes["motorway_within_500m"] = hex_m_centroid.geometry.within(moto_union)
print(f"  hexes within 500 m of motorway/trunk: "
      f"{int(hexes['motorway_within_500m'].sum()):,}")

ind = pois_m.loc[pois_m["category"] == "industry", ["geometry"]].reset_index(drop=True)
if ind.empty:
    hexes["industry_density_per_km2"] = 0
    print("  industry: 0 POIs in OSM extract (column zero-filled)")
else:
    hex_buf_1km = gpd.GeoDataFrame(
        {"h3_id": hexes["h3_id"].values},
        geometry=hex_m_centroid.buffer(1000),
        crs=hex_m_centroid.crs,
    )
    hits = gpd.sjoin(hex_buf_1km, ind, how="inner", predicate="contains")
    ind_counts = hits.groupby("h3_id").size()
    hexes["industry_density_per_km2"] = (
        hexes["h3_id"].map(ind_counts).fillna(0).astype(int)
    )
    print(f"  hexes with ≥1 industry POI within 1 km: "
          f"{int((hexes['industry_density_per_km2'] > 0).sum()):,}")
```

    Motorway / industry penalties …


      hexes within 500 m of motorway/trunk: 2,528


      hexes with ≥1 industry POI within 1 km: 21


## 7. Write the gold parquet

We write a column-pure DataFrame (no geometry column) — the hex polygon
is reconstructible from `h3_id` whenever a downstream consumer needs it.
This keeps the gold file small (~1.6 MB).


```python
out = hexes.drop(columns="geometry").copy()
# Normalise h3 IDs to hex strings for downstream consumers (deck.gl,
# h3-js). h3-py 4.x's `polygon_to_cells` may return strings already or
# ints depending on the build, so we coerce defensively.
def _to_hex_str(c):
    return c if isinstance(c, str) else h3.int_to_str(int(c))
out["h3_id"] = out["h3_id"].apply(_to_hex_str)
out_path = GOLD / "h3_res8_catalonia.parquet"
out.to_parquet(out_path, index=False)
print(f"→ wrote {out_path}  ({len(out):,} hexes, "
      f"{out_path.stat().st_size / 1e6:.2f} MB)")
print()
print("Columns:")
print(out.dtypes)
```

    → wrote /home/nls/Documents/dev/mitma-sedona/data/gold/h3_res8_catalonia.parquet  (45,220 hexes, 1.57 MB)
    
    Columns:
    h3_id                           str
    lon_centroid                float64
    lat_centroid                float64
    train_reach_min             float64
    climb_min_m                 float64
    yoga_min_m                  float64
    hospital_min_m              float64
    mitma_inflow_daily          float64
    mitma_outflow_daily         float64
    mitma_through_ratio         float64
    motorway_within_500m           bool
    industry_density_per_km2      int64
    dtype: object

