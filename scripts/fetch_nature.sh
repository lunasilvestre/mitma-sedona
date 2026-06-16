#!/usr/bin/env bash
# fetch_nature.sh — NATURE & BIODIVERSITY cluster source acquisition.
#
# Populates the bronze inputs for these GOLD_HEX_SCHEMA columns:
#   green_min_m              ← OSM parks/gardens/nature_reserve POLYGONS (from PBF, on disk)
#   sea_min_m                ← OSM natural=coastline lines              (from PBF, on disk)
#   tree_cover_pct           ← Copernicus HRL Tree Cover Density 2018  (EEA discomap ImageServer, keyless)
#   natura2000_within_5km    ← EEA Natura 2000 sites (Catalonia)        (EEA bio.discomap MapServer, keyless)
#   biodiversity_obs_density ← iNaturalist via GBIF occurrence API      (keyless)
#
# Design goals: idempotent (skip if output exists), keyless where possible,
# no new system deps beyond what the sedona conda env already has
# (ogr2ogr/GDAL 3.10, curl, python+requests+rasterio).
#
# Usage:
#   scripts/fetch_nature.sh                # fetch everything missing
#   scripts/fetch_nature.sh --force        # re-fetch even if cached
#   scripts/fetch_nature.sh osm tcd        # only the named steps
#       steps: osm | tcd | natura2000 | inat
#
# NOTE (concurrency): this script writes ONLY into data/bronze/nature/ and
# data/bronze/osm/ (new files green_polys.parquet / coastline.parquet). It
# does NOT touch notebooks/, src/catmob/, or data/gold/.

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly OSM_DIR="${REPO_ROOT}/data/bronze/osm"
readonly NAT_DIR="${REPO_ROOT}/data/bronze/nature"
readonly PBF="${OSM_DIR}/cataluna-latest.osm.pbf"

# Catalonia bbox: lon_min lat_min lon_max lat_max  (matches catmob CATALONIA_BBOX)
readonly LON_MIN=0.15 ; readonly LAT_MIN=40.50
readonly LON_MAX=3.35 ; readonly LAT_MAX=42.90

# Python from the sedona conda env (has pyarrow/geopandas/rasterio/requests).
PY="${CATMOB_PY:-/home/nls/miniforge3/envs/sedona/bin/python}"

FORCE=0
STEPS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
    osm|tcd|natura2000|inat) STEPS+=("$1"); shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done
[[ ${#STEPS[@]} -eq 0 ]] && STEPS=(osm tcd natura2000 inat)

mkdir -p "$NAT_DIR"

_have() { [[ -s "$1" && "$FORCE" -eq 0 ]]; }

# ---------------------------------------------------------------------------
# 1. OSM — re-extract from the on-disk PBF (green polygons + coastline).
#
# The v1 bronze (network.parquet) is highway-only and pois.parquet has parks
# as POINT NODES only (377). The PBF has 20,139 park/garden/nature_reserve
# POLYGONS and 2,002 natural=coastline lines — far better signal. osmium is
# NOT installed in this env, so we use ogr2ogr (GDAL OSM driver), which IS.
#
# GDAL note: `leisure`/`landuse`/`natural` are promoted fields on the
# multipolygons layer, but on the `lines` layer `natural` lives inside
# `other_tags` (hstore) — hence the LIKE filter for coastline.
# ---------------------------------------------------------------------------
fetch_osm() {
  if [[ ! -f "$PBF" ]]; then
    echo "[osm] PBF missing at $PBF — run scripts/fetch_osm.sh first" >&2
    exit 3
  fi

  local green_gpkg="${NAT_DIR}/green_polys.gpkg"
  local green_pq="${NAT_DIR}/green_polys.parquet"
  local coast_gpkg="${NAT_DIR}/coastline.gpkg"
  local coast_pq="${NAT_DIR}/coastline.parquet"

  # --- green polygons (parks/gardens/nature_reserve) ---
  if _have "$green_pq"; then
    echo "[osm] skip green polygons (cached at $green_pq)"
  else
    echo "[osm] extracting park/garden/nature_reserve polygons from PBF …"
    rm -f "$green_gpkg"
    ogr2ogr -f GPKG "$green_gpkg" "$PBF" \
      -sql "SELECT osm_id, osm_way_id, name, leisure, landuse, natural AS natural_tag \
            FROM multipolygons \
            WHERE leisure IN ('park','garden','nature_reserve') \
               OR landuse IN ('forest','meadow','recreation_ground') \
               OR natural IN ('wood','grassland','heath')" \
      -nln green -nlt PROMOTE_TO_MULTI \
      -spat "$LON_MIN" "$LAT_MIN" "$LON_MAX" "$LAT_MAX"
    "$PY" - "$green_gpkg" "$green_pq" <<'PY'
import sys, geopandas as gpd
src, dst = sys.argv[1], sys.argv[2]
gdf = gpd.read_file(src)
gdf = gdf[gdf.geometry.notna()].copy()
gdf["geometry"] = gdf.geometry.make_valid()
gdf.to_parquet(dst, index=False)   # GeoParquet (keeps geometry)
print(f"[osm]   wrote {dst}: {len(gdf):,} green polygons")
PY
  fi

  # --- coastline lines ---
  if _have "$coast_pq"; then
    echo "[osm] skip coastline (cached at $coast_pq)"
  else
    echo "[osm] extracting natural=coastline lines from PBF …"
    rm -f "$coast_gpkg"
    ogr2ogr -f GPKG "$coast_gpkg" "$PBF" \
      -sql "SELECT osm_id, name FROM lines \
            WHERE other_tags LIKE '%\"natural\"=>\"coastline\"%'" \
      -nln coastline -nlt PROMOTE_TO_MULTI \
      -spat "$LON_MIN" "$LAT_MIN" "$LON_MAX" "$LAT_MAX"
    "$PY" - "$coast_gpkg" "$coast_pq" <<'PY'
import sys, geopandas as gpd
src, dst = sys.argv[1], sys.argv[2]
gdf = gpd.read_file(src)
gdf = gdf[gdf.geometry.notna()].copy()
gdf.to_parquet(dst, index=False)
print(f"[osm]   wrote {dst}: {len(gdf):,} coastline lines")
PY
  fi
}

# ---------------------------------------------------------------------------
# 2. Tree Cover Density 2018 — Copernicus HRL, 10 m, 0-100 %, nodata=255.
#
# Keyless path: EEA discomap ArcGIS ImageServer `exportImage` (verified
# 2026-06, U8 pixelType, supports format=tiff). Avoids the land.copernicus.eu
# click-through that io_biodiversity's docstring assumes. We export a single
# Catalonia-covering GeoTIFF at ~30 m sampling (5000x4000 px keeps it well
# under the ImageServer's max response size and is ample for res-8 hex means).
#
# Downstream: catmob.io_biodiversity.compute_tree_cover_per_hex(tcd_tif, hexes)
# already uses nodata=255 — this output matches.
# ---------------------------------------------------------------------------
fetch_tcd() {
  local tif="${NAT_DIR}/tcd_2018_catalonia.tif"
  if _have "$tif"; then echo "[tcd] skip (cached at $tif)"; return; fi

  local base="https://image.discomap.eea.europa.eu/arcgis/rest/services/GioLandPublic/HRL_TreeCoverDensity_2018/ImageServer/exportImage"
  # ~30 m: Catalonia ~265 km E-W, ~265 km N-S → 8800 px would be native-ish;
  # 5000x4200 (~55 m) is plenty for res-8 (~660 m) hex zonal means and stays
  # within the ImageServer response cap.
  echo "[tcd] exporting Tree Cover Density 2018 GeoTIFF (EEA ImageServer) …"
  curl -sfL --max-time 300 -o "${tif}.tmp" \
    "${base}?bbox=${LON_MIN},${LAT_MIN},${LON_MAX},${LAT_MAX}&bboxSR=4326&imageSR=4326&size=5000,4200&format=tiff&pixelType=U8&noData=255&interpolation=RSP_NearestNeighbor&f=image"
  # sanity: must be a TIFF rasterio can open, not an ArcGIS JSON error blob.
  "$PY" - "${tif}.tmp" <<'PY'
import sys, rasterio
p = sys.argv[1]
with rasterio.open(p) as ds:
    assert ds.count >= 1 and ds.dtypes[0] == "uint8", f"unexpected raster: {ds.dtypes}"
    print(f"[tcd]   {ds.width}x{ds.height} {ds.dtypes[0]} (CRS {ds.crs})")
PY
  mv "${tif}.tmp" "$tif"
  echo "[tcd]   wrote $tif"
}

# ---------------------------------------------------------------------------
# 3. Natura 2000 — EEA bio.discomap MapServer (keyless), Catalonia subset.
#
# Keyless path: EEA ProtectedSites/Natura2000_Dyna_WM/MapServer layer 0
# ("Query Sites", polygon). Supports geoJSON output + envelope query
# (verified 2026-06: 224 sites intersect the Catalonia bbox). This sidesteps
# the WDPA protectedplanet.net click-through AND the token-gated Protected
# Planet API. Output normalises to PROTECTED_AREA_SCHEMA-ish columns so
# io_biodiversity.filter_wdpa_to_catalonia can be pointed at it (or a thin
# adapter added) — the notebook only needs polygons in EPSG:4326.
# ---------------------------------------------------------------------------
fetch_natura2000() {
  local out="${NAT_DIR}/natura2000_catalonia.parquet"
  local geojson="${NAT_DIR}/natura2000_catalonia.geojson"
  if _have "$out"; then echo "[natura2000] skip (cached at $out)"; return; fi

  local svc="https://bio.discomap.eea.europa.eu/arcgis/rest/services/ProtectedSites/Natura2000_Dyna_WM/MapServer/0/query"
  local env="{\"xmin\":${LON_MIN},\"ymin\":${LAT_MIN},\"xmax\":${LON_MAX},\"ymax\":${LAT_MAX},\"spatialReference\":{\"wkid\":4326}}"

  echo "[natura2000] querying EEA Natura 2000 sites over Catalonia (geoJSON) …"
  # ArcGIS caps records per response (often 1000-2000); Catalonia has ~224 so
  # one page suffices. resultRecordCount + resultOffset kept for safety.
  curl -sfG --max-time 120 -o "${geojson}.tmp" "$svc" \
    --data-urlencode "where=1=1" \
    --data-urlencode "geometry=${env}" \
    --data-urlencode "geometryType=esriGeometryEnvelope" \
    --data-urlencode "inSR=4326" \
    --data-urlencode "outSR=4326" \
    --data-urlencode "spatialRel=esriSpatialRelIntersects" \
    --data-urlencode "outFields=SITECODE,SITENAME,SITETYPE" \
    --data-urlencode "returnGeometry=true" \
    --data-urlencode "f=geoJSON"
  "$PY" - "${geojson}.tmp" "$out" <<'PY'
import sys, geopandas as gpd
src, dst = sys.argv[1], sys.argv[2]
gdf = gpd.read_file(src)
assert len(gdf) > 0, "Natura 2000 query returned 0 features"
gdf = gdf[gdf.geometry.notna()].copy()
if gdf.crs is None: gdf.set_crs(4326, inplace=True)
gdf = gdf.to_crs(4326)
gdf["geometry"] = gdf.geometry.make_valid()
gdf.to_parquet(dst, index=False)
print(f"[natura2000]   wrote {dst}: {len(gdf):,} sites")
PY
  mv "${geojson}.tmp" "$geojson"
}

# ---------------------------------------------------------------------------
# 4. iNaturalist observations via GBIF — keyless occurrence API.
#
# Delegates to the REAL, tested catmob.io_biodiversity.fetch_inat_observations
# (datasetKey 50c9509d-..., Catalonia bbox, research-grade). Verified 2026-06:
# the dataset has 437k records over the bbox for 2018-2024; the fetcher caps
# at max_records (default 50k) and validates against BIODIVERSITY_OBSERVATION_SCHEMA.
# ---------------------------------------------------------------------------
fetch_inat() {
  local out="${NAT_DIR}/inaturalist_catalonia.parquet"
  if _have "$out"; then echo "[inat] skip (cached at $out)"; return; fi
  echo "[inat] fetching iNaturalist research-grade observations via GBIF …"
  CATMOB_OUT="$out" PYTHONPATH="${REPO_ROOT}/src" "$PY" - <<'PY'
import os
from catmob.io_biodiversity import fetch_inat_observations
df = fetch_inat_observations()        # bbox+window+cap defaults; validated
out = os.environ["CATMOB_OUT"]
df.to_parquet(out, index=False)
print(f"[inat]   wrote {out}: {len(df):,} observations")
PY
}

for s in "${STEPS[@]}"; do
  case "$s" in
    osm)        fetch_osm ;;
    tcd)        fetch_tcd ;;
    natura2000) fetch_natura2000 ;;
    inat)       fetch_inat ;;
  esac
done

echo "fetch_nature.sh: done. Outputs in ${NAT_DIR}/ (+ OSM green/coastline)."
