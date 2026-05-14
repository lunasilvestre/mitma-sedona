#!/usr/bin/env bash
# fetch_zoning.sh — MITMA distritos zoning shapefile + GeoJSON.

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DEST="${REPO_ROOT}/data/bronze/mitma/zones"
readonly BASE="https://opendata-movilidad.mitma.es/zonificacion"

mkdir -p "$DEST"

for f in zonificacion_distritos.shp zonificacion_distritos.dbf zonificacion_distritos.shx zonificacion_distritos.prj zonificacion_distritos.geojson relaciones_distrito_mitma.csv; do
  if [[ -f "$DEST/$f" ]]; then
    echo "skip $f (cached)"
    continue
  fi
  echo "fetch $f"
  curl -sfL "${BASE}/${f}" -o "${DEST}/${f}.tmp" \
    && mv "${DEST}/${f}.tmp" "${DEST}/${f}" \
    || { echo "FAIL $f" >&2; rm -f "${DEST}/${f}.tmp"; }
done

echo ""
echo "Done. Zoning bronze size:"
du -sh "$DEST" 2>/dev/null || true
