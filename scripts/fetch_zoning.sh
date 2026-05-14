#!/usr/bin/env bash
# fetch_zoning.sh — MITMA distritos zoning (shapefile bundle + name/INE tables).
#
# The MITMA bucket layout (verified 2026-05) is:
#   movilidad-opendata.mitma.es/zonificacion/
#     ├── poblacion.csv
#     ├── relacion_ine_zonificacionMitma.csv         ← INE ↔ MITMA mapping
#     └── zonificacion_distritos/
#         ├── nombres_distritos.csv                  ← distrito names
#         ├── poblacion_distritos.csv
#         ├── zonificacion_distritos.{shp,shx,dbf,prj,cpg}    ← polygons
#         └── zonificacion_distritos_centroides.{shp,shx,dbf,prj,cpg}
#
# After fetch we convert the polygon shapefile → GeoJSON so notebooks can
# read it without depending on shapefile readers. Falls back gracefully if
# python3-geopandas isn't available.

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DEST="${REPO_ROOT}/data/bronze/mitma/zones"
readonly BASE="https://movilidad-opendata.mitma.es/zonificacion"

mkdir -p "$DEST"

# 1. INE↔MITMA relation table
RELATION_REL="relacion_ine_zonificacionMitma.csv"
if [[ -f "${DEST}/${RELATION_REL}" ]]; then
  echo "skip ${RELATION_REL} (cached)"
else
  echo "fetch ${RELATION_REL}"
  curl -sfL "${BASE}/${RELATION_REL}" -o "${DEST}/${RELATION_REL}.tmp" \
    && mv "${DEST}/${RELATION_REL}.tmp" "${DEST}/${RELATION_REL}" \
    || { echo "FAIL ${RELATION_REL}" >&2; rm -f "${DEST}/${RELATION_REL}.tmp"; }
fi

# 2. Distrito polygon shapefile bundle (5 sidecar files)
SHP_PREFIX="zonificacion_distritos"
for ext in shp shx dbf prj cpg; do
  fname="${SHP_PREFIX}.${ext}"
  if [[ -f "${DEST}/${fname}" ]]; then
    echo "skip ${fname} (cached)"
    continue
  fi
  url="${BASE}/${SHP_PREFIX}/${fname}"
  echo "fetch ${fname}"
  curl -sfL "${url}" -o "${DEST}/${fname}.tmp" \
    && mv "${DEST}/${fname}.tmp" "${DEST}/${fname}" \
    || { echo "FAIL ${fname}" >&2; rm -f "${DEST}/${fname}.tmp"; }
done

# 3. Distrito names lookup
NAMES_FNAME="nombres_distritos.csv"
if [[ -f "${DEST}/${NAMES_FNAME}" ]]; then
  echo "skip ${NAMES_FNAME} (cached)"
else
  echo "fetch ${NAMES_FNAME}"
  curl -sfL "${BASE}/${SHP_PREFIX}/${NAMES_FNAME}" -o "${DEST}/${NAMES_FNAME}.tmp" \
    && mv "${DEST}/${NAMES_FNAME}.tmp" "${DEST}/${NAMES_FNAME}" \
    || { echo "FAIL ${NAMES_FNAME}" >&2; rm -f "${DEST}/${NAMES_FNAME}.tmp"; }
fi

# 4. SHP → GeoJSON conversion (needs geopandas; falls back to a hint if missing)
GEOJSON="${DEST}/${SHP_PREFIX}.geojson"
if [[ -f "${GEOJSON}" ]]; then
  echo "skip geojson conversion (cached)"
elif [[ -f "${DEST}/${SHP_PREFIX}.shp" ]]; then
  if python3 -c "import geopandas" 2>/dev/null; then
    echo "convert ${SHP_PREFIX}.shp → ${SHP_PREFIX}.geojson"
    python3 - <<PY
import geopandas as gpd
gdf = gpd.read_file("${DEST}/${SHP_PREFIX}.shp")
# Re-project to WGS84 if not already
if gdf.crs is None:
    gdf.set_crs("EPSG:25830", inplace=True)
if gdf.crs.to_epsg() != 4326:
    gdf = gdf.to_crs("EPSG:4326")
gdf.to_file("${GEOJSON}", driver="GeoJSON")
print(f"  wrote {len(gdf)} features in EPSG:4326")
PY
  else
    echo "(skip geojson — install geopandas to enable: pip install geopandas)"
  fi
fi

echo ""
echo "Done. Zoning bronze size:"
du -sh "$DEST" 2>/dev/null || true
ls "$DEST" 2>/dev/null
