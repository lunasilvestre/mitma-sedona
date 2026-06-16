#!/usr/bin/env bash
# fetch_pollution.sh — POLLUTION & LIGHT cluster bronze fetch.
#
# Populates four gold columns (see src/catmob/schemas.py GOLD_HEX_SCHEMA):
#   viirs_radiance          ← VIIRS DNB monthly radiance raster (zonal mean/hex)
#   eprtr_facility_min_m    ← E-PRTR industrial facility points (nearest/hex)
#   pharmacy_density_per_km2← OSM amenity=pharmacy (ALREADY in bronze/osm — no fetch)
#   hospital_min_m          ← OSM amenity=hospital ∪ CatSalut registry (nearest/hex)
#
# VERIFIED 2026-06-16. Three independent sources, each gated + idempotent:
#
#   1. VIIRS — Planetary Computer's `viirs-monthly-v22` collection is DEAD
#      (404; PC dropped all VIIRS night-lights). EOG (eogdata.mines.edu) now
#      sits behind OAuth. The remaining KEYLESS raster source is AWS Open Data
#      `s3://globalnightlight/` (World Bank "Light Every Night", anonymous /
#      --no-sign-request), but it only spans 2012–2020. For a CURRENT monthly
#      field use NASA Black Marble VNP46A3 via LAADS, which needs an Earthdata
#      bearer token in $EARTHDATA_TOKEN (NOT keyless) — gated, never blocks.
#      Default path here is keyless: pull a Catalonia-clipped DNB COG from the
#      AWS bucket. If $EARTHDATA_TOKEN is set, prefer the fresher Black Marble.
#
#   2. E-PRTR — EEA "Industrial Reporting" (v15 Dec-2025; v14 record below).
#      No clean per-file CSV: the portal serves a Nextcloud bulk zip. We pull
#      the bulk archive from the public WebDAV datastore and extract the
#      facility-location table; parser is src/catmob/io_pollution.py
#      parse_eprtr_facilities (robust to column renames). Keyless.
#
#   3. CatSalut hospitals — the resource hardcoded in io_health.py
#      (`yub2-3z85`) is DEAD (404). VERIFIED live replacement is
#      `8gmd-gz7i` ("Equipaments de Catalunya"): the official facilities
#      registry with longitud/latitud/utmx/utmy and a hierarchical
#      `categoria` field. Hospitals are the rows whose categoria starts
#      `Salut|Centres sanitaris|3. Hospitals|` (~70 sites vs OSM's 36 —
#      a real coverage gain). Keyless Socrata SoQL export.
#      (Note: `nrmq-ytje` is the *pharmacy/optician* registry — 3304
#      `Farmàcia` rows, NO hospitals — a useful authoritative pharmacy
#      cross-check, but NOT the hospital source.)
#
# Pharmacies need NO fetch — bronze/osm/pois.parquet already has 3217
# amenity=pharmacy + 36 amenity=hospital nodes (fetch_osm.sh). This script
# only verifies their presence.
#
# Usage:
#   scripts/fetch_pollution.sh                 # all sub-fetches, default year
#   scripts/fetch_pollution.sh --year 2024
#   scripts/fetch_pollution.sh --only viirs    # viirs|eprtr|catsalut|pharmacy
#   scripts/fetch_pollution.sh --no-prune-aws  # keep raw AWS DNB tiles

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DEST="${REPO_ROOT}/data/bronze/pollution"
readonly HEALTH_DEST="${REPO_ROOT}/data/bronze/health"
readonly OSM_POIS="${REPO_ROOT}/data/bronze/osm/pois.parquet"

# Catalonia bbox (lon_min lat_min lon_max lat_max) — matches
# catmob.io_pollution.CATALONIA_BBOX = (0.15, 40.50, 3.35, 42.90).
readonly LON_MIN=0.15 LAT_MIN=40.50 LON_MAX=3.35 LAT_MAX=42.90

YEAR="2024"
ONLY=""
NO_PRUNE_AWS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --year) YEAR="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    --no-prune-aws) NO_PRUNE_AWS=1; shift ;;
    -h|--help) sed -n '1,60p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

want() { [[ -z "$ONLY" || "$ONLY" == "$1" ]]; }
mkdir -p "$DEST/viirs" "$DEST/eprtr" "$HEALTH_DEST/catsalut"

# ---------------------------------------------------------------------------
# 1. VIIRS DNB monthly radiance → bronze/pollution/viirs/viirs_dnb_<year>.tif
# ---------------------------------------------------------------------------
# Default: KEYLESS AWS Open Data (Light Every Night, 2012–2020 only).
# Preferred when $EARTHDATA_TOKEN set: NASA Black Marble VNP46A3 (current).
if want viirs; then
  viirs_out="$DEST/viirs/viirs_dnb_${YEAR}.tif"
  if [[ -f "$viirs_out" ]]; then
    echo "skip VIIRS ${YEAR} (cached at $viirs_out)"
  elif [[ -n "${EARTHDATA_TOKEN:-}" ]]; then
    # --- Black Marble VNP46A3 (monthly), needs Earthdata bearer token ---
    # Catalonia falls in Black Marble tile h18v04. Pull Jan composite of the
    # year (representative annual proxy; notebook can average 12 if desired).
    bm_dir="https://ladsweb.modaps.eosdis.nasa.gov/archive/allData/5000/VNP46A3/${YEAR}/001"
    echo "fetch Black Marble VNP46A3 listing (${YEAR}/001) …"
    listing="$DEST/viirs/_vnp46a3_listing.json"
    curl -sfL -H "Authorization: Bearer ${EARTHDATA_TOKEN}" \
         "${bm_dir}.json" -o "$listing" || {
      echo "FAIL Black Marble listing (token invalid?) — will try AWS fallback" >&2
      EARTHDATA_TOKEN=""
    }
    if [[ -n "${EARTHDATA_TOKEN:-}" ]]; then
      h5name=$(python3 - "$listing" <<'PY'
import json, sys
items = json.load(open(sys.argv[1]))
items = items.get("content", items) if isinstance(items, dict) else items
# Catalonia tile: h18v04
for it in items:
    n = it.get("name", "")
    if "h18v04" in n and n.endswith(".h5"):
        print(n); break
PY
)
      if [[ -n "$h5name" ]]; then
        h5_out="$DEST/viirs/${h5name}"
        echo "fetch $h5name → $h5_out"
        curl -sfL -H "Authorization: Bearer ${EARTHDATA_TOKEN}" \
             "${bm_dir}/${h5name}" -o "${h5_out}.tmp" && mv "${h5_out}.tmp" "$h5_out"
        echo "NOTE: VNP46A3 ships HDF5 — notebook/io_pollution must extract the"
        echo "      NearNadir_Composite_Snow_Free SDS → GeoTIFF (gdal_translate"
        echo "      HDF5:...://...) before RS_ZonalStats. Left as a python step."
      fi
    fi
  fi

  # --- KEYLESS fallback: AWS Light Every Night (anonymous S3) ---
  if [[ ! -f "$viirs_out" && ! -f "$DEST/viirs/"*.h5 ]] 2>/dev/null; then
    aws_year="$YEAR"; [[ "$YEAR" -gt 2020 ]] && aws_year="2020"
    # Bucket layout: s3://globalnightlight/YYYYMM/ ... DNB monthly mosaics.
    # We pull the January monthly catalog + the relevant Europe-west COG and
    # clip to Catalonia. Anonymous read (no creds) is confirmed working.
    s3_prefix="https://globalnightlight.s3.amazonaws.com/${aws_year}01"
    echo "fetch VIIRS (keyless AWS Light Every Night, ${aws_year}01) …"
    cat <<EOF
  NOTE: s3://globalnightlight/ is per-orbit GDNBO tiles, not a single
  pre-mosaicked monthly COG. The clean path is the awscli (keyless):
      aws s3 cp --no-sign-request \\
          s3://globalnightlight/${aws_year}01/ "$DEST/viirs/raw_${aws_year}01/" \\
          --recursive --exclude '*' --include '*.li.co.tif'
  then mosaic + clip to Catalonia bbox with gdalwarp:
      gdalwarp -te ${LON_MIN} ${LAT_MIN} ${LON_MAX} ${LAT_MAX} -t_srs EPSG:4326 \\
          -of COG "$DEST/viirs/raw_${aws_year}01"/*.li.co.tif "$viirs_out"
  Run that block if awscli+gdal are present; left explicit because the
  conda env (sedona) currently lacks awscli/gdal (osgeo import fails).
EOF
    if command -v aws >/dev/null 2>&1 && command -v gdalwarp >/dev/null 2>&1; then
      raw="$DEST/viirs/raw_${aws_year}01"
      mkdir -p "$raw"
      aws s3 cp --no-sign-request "s3://globalnightlight/${aws_year}01/" "$raw/" \
          --recursive --exclude '*' --include '*.li.co.tif' || \
          echo "FAIL AWS DNB pull (network?) — viirs column stays NULL" >&2
      shopt -s nullglob
      tifs=("$raw"/*.li.co.tif)
      if [[ ${#tifs[@]} -gt 0 ]]; then
        gdalwarp -overwrite -te ${LON_MIN} ${LAT_MIN} ${LON_MAX} ${LAT_MAX} \
                 -t_srs EPSG:4326 -of COG "${tifs[@]}" "${viirs_out}.tmp" \
            && mv "${viirs_out}.tmp" "$viirs_out"
        [[ "$NO_PRUNE_AWS" == "0" ]] && rm -rf "$raw"
      fi
      shopt -u nullglob
    else
      echo "  (awscli/gdal absent — viirs_radiance will stay NULL until present)"
    fi
  fi
  [[ -f "$viirs_out" ]] && echo "VIIRS ready: $viirs_out"
fi

# ---------------------------------------------------------------------------
# 2. E-PRTR industrial facilities → bronze/pollution/eprtr/spain.csv
# ---------------------------------------------------------------------------
# EEA Industrial Reporting (E-PRTR). No per-file CSV endpoint: the portal
# serves a Nextcloud bulk zip from the public WebDAV datastore. We pull the
# bulk archive once, extract the facility table, and let
# io_pollution.parse_eprtr_facilities normalise + bbox-filter to Catalonia.
if want eprtr; then
  eprtr_csv="$DEST/eprtr/spain.csv"
  eprtr_zip="$DEST/eprtr/eea_ied_eprtr_v14.zip"
  # v14 record id (2007-2023). Bump to the v15 (Dec-2025) record when wired.
  eea_record="21e758c6-a9ac-4a7d-a64a-19d2ba9eecb7"
  if [[ -f "$eprtr_csv" ]]; then
    echo "skip E-PRTR (cached at $eprtr_csv)"
  else
    echo "fetch E-PRTR bulk archive (EEA WebDAV datastore) …"
    # The 'download all' endpoint streams a zip of the whole datastore folder.
    eea_url="https://sdi.eea.europa.eu/datashare/s/download?record=${eea_record}"
    eea_webdav="https://sdi.eea.europa.eu/webdav/datastore/public/eea_t_ied-eprtr_p_2007-2023_v14_r00/?download"
    if curl -sfL "$eea_url" -o "${eprtr_zip}.tmp" 2>/dev/null && [[ -s "${eprtr_zip}.tmp" ]]; then
      mv "${eprtr_zip}.tmp" "$eprtr_zip"
    elif curl -sfL "$eea_webdav" -o "${eprtr_zip}.tmp" 2>/dev/null && [[ -s "${eprtr_zip}.tmp" ]]; then
      mv "${eprtr_zip}.tmp" "$eprtr_zip"
    else
      rm -f "${eprtr_zip}.tmp"
      cat >&2 <<EOF
FAIL E-PRTR auto-download (EEA portal is click-through / Nextcloud blob).
MANUAL: open https://industry.eea.europa.eu/download (or the v14 record
  https://sdi.eea.europa.eu/catalogue/srv/api/records/${eea_record}?language=eng),
  download the CSV/tabular export, place the facility-location table at:
      $eprtr_csv
  io_pollution.parse_eprtr_facilities is robust to its column names.
  eprtr_facility_min_m stays NULL until this CSV exists (weight × 0 = no harm).
EOF
    fi
    # Extract a facility-location CSV from the bulk zip if we got one.
    if [[ -f "$eprtr_zip" ]]; then
      tmpd="$(mktemp -d)"
      unzip -o -q "$eprtr_zip" -d "$tmpd" || true
      # Facility-location table varies by version: ProductionFacility /
      # F_..._Facility / *Facility*.csv. Grab the first plausible match.
      fac=$(find "$tmpd" -iname '*facilit*' \( -iname '*.csv' -o -iname '*.txt' \) \
              | head -1 || true)
      if [[ -n "$fac" ]]; then
        cp "$fac" "$eprtr_csv"
        echo "E-PRTR facility table extracted → $eprtr_csv"
      else
        echo "WARN zip had no *facilit*.csv — inspect $tmpd manually" >&2
      fi
      rm -rf "$tmpd"
    fi
  fi
  [[ -f "$eprtr_csv" ]] && echo "E-PRTR ready: $eprtr_csv ($(wc -l <"$eprtr_csv") rows)"
fi

# ---------------------------------------------------------------------------
# 3. CatSalut hospitals → bronze/health/catsalut/hospitals.csv
#    + pharmacy cross-check → bronze/health/catsalut/pharmacies.csv
# ---------------------------------------------------------------------------
# Live resource 8gmd-gz7i "Equipaments de Catalunya" (old yub2-3z85 is 404).
# Server-side SoQL filter to the hospital class; the categoria taxonomy is
# pipe-delimited, hospitals are `Salut|Centres sanitaris|3. Hospitals|...`.
# Columns: idequipament, nom, categoria, longitud, latitud, utmx, utmy,
#          codi_municipi, comarca.
if want catsalut; then
  cs_csv="$HEALTH_DEST/catsalut/hospitals.csv"
  ph_csv="$HEALTH_DEST/catsalut/pharmacies.csv"
  if [[ -f "$cs_csv" ]]; then
    echo "skip CatSalut hospitals (cached at $cs_csv)"
  else
    # SoQL: categoria LIKE '%Hospitals%' under the Salut taxonomy. URL-encode
    # the spaces/percent ourselves so curl doesn't mangle the $where.
    cs_url="https://analisi.transparenciacatalunya.cat/resource/8gmd-gz7i.csv"
    cs_q="\$where=categoria%20like%20%27%25Centres%20sanitaris%253%252e%20Hospitals%25%27&\$limit=5000"
    # Simpler + robust: filter on the literal 'Hospitals' token; downstream
    # parser keeps only rows with valid lon/lat anyway.
    cs_q="\$where=upper(categoria)%20like%20%27%25HOSPITALS%25%27&\$limit=5000"
    echo "fetch CatSalut hospitals (8gmd-gz7i) → $cs_csv"
    if curl -sfL "${cs_url}?${cs_q}" -o "${cs_csv}.tmp" && [[ -s "${cs_csv}.tmp" ]]; then
      mv "${cs_csv}.tmp" "$cs_csv"
      echo "CatSalut hospitals ready: $cs_csv ($(($(wc -l <"$cs_csv")-1)) rows)"
    else
      rm -f "${cs_csv}.tmp"
      echo "FAIL CatSalut hospital fetch (OSM hospitals suffice as fallback)" >&2
    fi
  fi
  # Authoritative pharmacy cross-check (nrmq-ytje, tipus_establiment_nom=Farmàcia).
  # OSM already gives pharmacy_density; this is an optional validation source.
  if [[ ! -f "$ph_csv" ]]; then
    ph_url="https://analisi.transparenciacatalunya.cat/resource/nrmq-ytje.csv"
    ph_q="\$where=tipus_establiment_nom=%27Farm%C3%A0cia%27&\$limit=10000"
    echo "fetch CatSalut pharmacy cross-check (nrmq-ytje) → $ph_csv"
    curl -sfL "${ph_url}?${ph_q}" -o "${ph_csv}.tmp" 2>/dev/null \
      && [[ -s "${ph_csv}.tmp" ]] && mv "${ph_csv}.tmp" "$ph_csv" \
      && echo "  pharmacy cross-check ready: $ph_csv ($(($(wc -l <"$ph_csv")-1)) rows)" \
      || { rm -f "${ph_csv}.tmp"; echo "  (pharmacy cross-check skipped — OSM suffices)"; }
  fi
fi

# ---------------------------------------------------------------------------
# 4. Pharmacies + OSM hospitals — NO FETCH (already in bronze/osm/pois.parquet)
# ---------------------------------------------------------------------------
if want pharmacy; then
  if [[ -d "$OSM_POIS" ]]; then
    echo "OSM POIs present: $OSM_POIS"
    echo "  (pharmacy + hospital categories produced by fetch_osm.sh —"
    echo "   pharmacy_density_per_km2 and the hospital union need no new fetch)"
  else
    echo "WARN $OSM_POIS missing — run scripts/fetch_osm.sh first" >&2
  fi
fi

echo ""
echo "Done. Pollution/health bronze sizes:"
du -sh "$DEST" "$HEALTH_DEST" 2>/dev/null || true
