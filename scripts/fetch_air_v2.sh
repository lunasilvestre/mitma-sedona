#!/usr/bin/env bash
# fetch_air_v2.sh — AIR QUALITY cluster (no2_ugm3, pm25_ugm3) for v2.
#
# VERIFIED 2026-06 against live endpoints. Supersedes scripts/fetch_air.sh,
# which pointed at the retired discomap E1a service and the wrong XVPCA
# resource id (uy6k-2s8r). DRAFT — review before replacing fetch_air.sh.
#
# Three sources, in priority order for the gold columns:
#   1. XVPCA  (Catalan network, ~140 active stations) — densest, primary.
#   2. EEA    (E1a verified, Spain) — fills XVPCA gaps + cross-check.
#   3. CAMS   (0.1deg ~10km gridded reanalysis) — stationless-area fallback,
#             and the PREFERRED field for pm25 zonal-mean (continuous surface).
#
# Keyless where possible:
#   - XVPCA: Socrata SODA API, keyless (app token only raises rate limits).
#   - EEA:   Azure download API, keyless POST.
#   - CAMS:  requires a free ADS account + ~/.cdsapirc. GATED: only runs when
#            CAMS_FETCH=1 and ~/.cdsapirc exists. Pipeline must not block on it.
#
# Usage:  scripts/fetch_air_v2.sh [--year 2024]
#         CAMS_FETCH=1 scripts/fetch_air_v2.sh --year 2024   # also pull CAMS

set -euo pipefail

YEAR="2024"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --year) YEAR="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DEST="${REPO_ROOT}/data/bronze/air"
mkdir -p "$DEST/xvpca" "$DEST/eea" "$DEST/cams"

# --- Catalonia bbox (lon/lat, EPSG:4326) — matches schemas.py Check ranges ---
readonly BBOX_W="0.0" BBOX_S="40.5" BBOX_E="3.4" BBOX_N="42.9"

# ---------------------------------------------------------------------------
# 1. XVPCA — hourly measurements, resource tasf-thgu (VERIFIED current id).
#    We pull the full year for NO2 + PM2.5 only (server-side $where filter),
#    selecting just the columns we need. Annual-mean aggregation happens in
#    Python (io_air.parse_xvpca_csv must be updated to the tasf-thgu shape:
#    one row per station/day/pollutant with hourly cols h01..h24).
#    Fields verified live: codi_eoi, nom_estacio, data, contaminant, unitats,
#    latitud, longitud, municipi, h01..h24. contaminant in ('NO2','PM2.5');
#    unitats 'µg/m3'.
# ---------------------------------------------------------------------------
xvpca_base="https://analisi.transparenciacatalunya.cat/resource/tasf-thgu.csv"
xvpca_dest="$DEST/xvpca/xvpca_hourly_${YEAR}.csv"
# SoQL: filter to the year and the two pollutants; cap generous; CSV out.
# date_extract_y(data) keeps it robust to the floating-timestamp format.
xvpca_where="date_extract_y(data)=${YEAR} AND contaminant in ('NO2','PM2.5')"
xvpca_select="codi_eoi,nom_estacio,data,contaminant,unitats,latitud,longitud,municipi,h01,h02,h03,h04,h05,h06,h07,h08,h09,h10,h11,h12,h13,h14,h15,h16,h17,h18,h19,h20,h21,h22,h23,h24"
if [[ ! -f "$xvpca_dest" ]]; then
  echo "fetch XVPCA ${YEAR} (NO2+PM2.5 hourly) -> $xvpca_dest"
  curl -sfG "$xvpca_base" \
    --data-urlencode "\$select=${xvpca_select}" \
    --data-urlencode "\$where=${xvpca_where}" \
    --data-urlencode "\$limit=2000000" \
    -o "${xvpca_dest}.tmp" \
    && mv "${xvpca_dest}.tmp" "$xvpca_dest" \
    || { echo "FAIL XVPCA fetch (non-fatal; notebook can retry)" >&2; rm -f "${xvpca_dest}.tmp"; }
else
  echo "skip XVPCA ${YEAR} (cached: $xvpca_dest)"
fi

# ---------------------------------------------------------------------------
# 2. EEA — Air Quality Download Service (Azure), E1a verified (dataset=1).
#    VERIFIED: discomap is retired; current API base:
#      https://eeadmz1-downloads-api-appservice.azurewebsites.net
#    POST /ParquetFile returns a zip of per-sampling-point parquet files.
#    Body: ParquetDownloadDataDTO { countries[], cities[], pollutants[],
#      dataset(int, 1=E1a verified), source, dateTimeStart, dateTimeEnd,
#      aggregationType, email, compress }.
#    pollutants take CEIP/EEA vocabulary URIs, not bare 'NO2'. We request the
#    two we need by their vocabulary codes (NO2=8, PM2.5=6001 in the EEA
#    pollutant vocabulary; URI form below — confirm against /Pollutant if the
#    server rejects). Spain = 'ES'. Keyless.
# ---------------------------------------------------------------------------
eea_api="https://eeadmz1-downloads-api-appservice.azurewebsites.net"
eea_zip="$DEST/eea/eea_es_e1a_${YEAR}.zip"
eea_no2="http://dd.eionet.europa.eu/vocabulary/aq/pollutant/8"
eea_pm25="http://dd.eionet.europa.eu/vocabulary/aq/pollutant/6001"
if [[ ! -f "$eea_zip" ]]; then
  echo "fetch EEA E1a Spain ${YEAR} (NO2+PM2.5) -> $eea_zip"
  read -r -d '' eea_body <<JSON || true
{
  "countries": ["ES"],
  "cities": [],
  "pollutants": ["${eea_no2}", "${eea_pm25}"],
  "dataset": 1,
  "source": "API",
  "dateTimeStart": "${YEAR}-01-01T00:00:00Z",
  "dateTimeEnd": "${YEAR}-12-31T23:59:59Z",
  "aggregationType": "annual",
  "compress": true
}
JSON
  curl -sfL -X POST "${eea_api}/ParquetFile" \
    -H "Content-Type: application/json" \
    -H "Accept: application/zip" \
    -d "$eea_body" \
    -o "${eea_zip}.tmp" \
    && mv "${eea_zip}.tmp" "$eea_zip" \
    || { echo "FAIL EEA fetch (non-fatal; XVPCA covers Catalonia)" >&2; rm -f "${eea_zip}.tmp"; }
  # Unzip the per-SPO parquet files next to the zip (idempotent).
  if [[ -f "$eea_zip" ]]; then
    ( cd "$DEST/eea" && unzip -oq "$(basename "$eea_zip")" -d "e1a_${YEAR}" ) \
      || echo "WARN could not unzip EEA bundle" >&2
  fi
else
  echo "skip EEA E1a ${YEAR} (cached: $eea_zip)"
fi

# Station metadata (coords/type) — needed to place EEA SPOs in space.
eea_meta="$DEST/eea/stations_metadata.csv"
if [[ ! -f "$eea_meta" ]]; then
  echo "fetch EEA station metadata -> $eea_meta"
  curl -sfL "https://discomap.eea.europa.eu/App/AQViewer/download?fqn=Airquality_Dissem.b2g.measurements&f=csv" \
    -o "${eea_meta}.tmp" 2>/dev/null \
    && mv "${eea_meta}.tmp" "$eea_meta" \
    || { echo "WARN EEA metadata fetch failed; use /List endpoint from Python" >&2; rm -f "${eea_meta}.tmp"; }
fi

# ---------------------------------------------------------------------------
# 3. CAMS — European air quality reanalyses, 0.1deg ensemble (GATED).
#    VERIFIED dataset id: 'cams-europe-air-quality-reanalyses' (ADS).
#    Requires a free ADS account + ~/.cdsapirc. Interim reanalysis for year Y
#    lands ~May Y+1; validated ~Nov Y+2. We request the full year (12 months)
#    of surface NO2 + PM2.5 as a zip of NetCDF, then crop to Catalonia in
#    Python (io_air.cams_grid_to_dataframe). Annual mean is computed downstream.
#    Skipped unless CAMS_FETCH=1 AND ~/.cdsapirc present — never blocks.
# ---------------------------------------------------------------------------
cams_zip="$DEST/cams/cams_no2_pm25_${YEAR}.zip"
if [[ "${CAMS_FETCH:-0}" == "1" ]]; then
  if [[ ! -f "$HOME/.cdsapirc" ]]; then
    echo "skip CAMS: ~/.cdsapirc not found (register at ads.atmosphere.copernicus.eu)" >&2
  elif [[ -f "$cams_zip" ]]; then
    echo "skip CAMS ${YEAR} (cached: $cams_zip)"
  else
    echo "fetch CAMS ${YEAR} via cdsapi -> $cams_zip"
    PY="${PYTHON:-/home/nls/miniforge3/envs/sedona/bin/python}"
    "$PY" - "$YEAR" "$cams_zip" <<'PYEOF' || echo "FAIL CAMS fetch (non-fatal)" >&2
import sys
year, out = sys.argv[1], sys.argv[2]
try:
    import cdsapi
except ImportError:
    sys.exit("cdsapi not installed in env; pip install cdsapi")
# Interim reanalysis is the only one guaranteed present for recent years.
cdsapi.Client().retrieve(
    "cams-europe-air-quality-reanalyses",
    {
        "variable": ["nitrogen_dioxide", "particulate_matter_2.5um"],
        "model": "ensemble",
        "type": ["interim_reanalysis"],
        "level": ["0"],            # 0 m = surface
        "year": [year],
        "month": [f"{m:02d}" for m in range(1, 13)],
        "data_format": "netcdf_zip",
    },
    out,
)
PYEOF
  fi
else
  echo "skip CAMS (set CAMS_FETCH=1 and have ~/.cdsapirc to enable)"
fi

echo ""
echo "Done. Air bronze tree:"
du -sh "$DEST"/* 2>/dev/null || true
echo ""
echo "Catalonia bbox used for downstream crop: ${BBOX_W},${BBOX_S},${BBOX_E},${BBOX_N}"
