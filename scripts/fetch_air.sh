#!/usr/bin/env bash
# fetch_air.sh — XVPCA (Catalonia) + EEA (E1a) air quality station data.
#
# CAMS gridded reanalysis is fetched from inside Python (cdsapi), not here.
#
# Usage:  scripts/fetch_air.sh [--year 2024]

set -euo pipefail

YEAR="2024"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --year) YEAR="$2"; shift 2 ;;
    -h|--help) sed -n '1,10p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DEST="${REPO_ROOT}/data/bronze/air"

mkdir -p "$DEST/xvpca" "$DEST/eea"

# XVPCA — Catalan air quality network, station annual aggregates
xvpca_url="https://analisi.transparenciacatalunya.cat/resource/uy6k-2s8r.csv?\$where=any=${YEAR}&\$limit=10000"
xvpca_dest="$DEST/xvpca/xvpca_${YEAR}.csv"
if [[ ! -f "$xvpca_dest" ]]; then
  echo "fetch XVPCA ${YEAR} → $xvpca_dest"
  curl -sfL "$xvpca_url" -o "${xvpca_dest}.tmp" && mv "${xvpca_dest}.tmp" "$xvpca_dest" || \
    echo "FAIL XVPCA fetch (will retry from notebook)" >&2
else
  echo "skip XVPCA ${YEAR} (cached)"
fi

# EEA E1a — Spain (ES) annual aggregates, NO2 + PM25 + PM10 + O3
# Note: EEA discomap requires per-pollutant per-country form-style URLs;
# the canonical fetcher lives in src/catmob/io_air.py which calls their
# Parquet/JSON download API. This script only fetches the "country list"
# preflight so we have it cached for the notebook.
eea_url="https://discomap.eea.europa.eu/Map/UI/AirQualityE1a/Reports/AnnualReport_AggregatedDataAvailability.json"
eea_dest="$DEST/eea/availability.json"
if [[ ! -f "$eea_dest" ]]; then
  echo "fetch EEA availability → $eea_dest"
  curl -sfL "$eea_url" -o "${eea_dest}.tmp" && mv "${eea_dest}.tmp" "$eea_dest" || \
    echo "FAIL EEA preflight (will retry from notebook)" >&2
else
  echo "skip EEA availability (cached)"
fi

echo ""
echo "Done. Air bronze size:"
du -sh "$DEST" 2>/dev/null || true
