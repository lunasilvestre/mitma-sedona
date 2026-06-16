#!/usr/bin/env bash
# fetch_gtfs_v2.sh — Renfe Rodalies + FGC GTFS bundles (v2 draft).
#
# Replaces the dead URLs in scripts/fetch_gtfs.sh:
#   - transitfeeds.com/p/renfe/505/latest/download   → 410/redirect (mirror retired)
#   - fgc.cat/wp-content/uploads/horaris/...          → 404 (path moved)
#
# Verified live 2026-06-16 (HTTP 200, application/zip):
#   Renfe Cercanías/Rodalies  https://ssl.renfe.com/ftransit/Fichero_CER_FOMENTO/fomento_transit.zip  (~14.4 MB, CC BY 4.0, data.renfe.com)
#   FGC                        https://www.fgc.cat/google/google_transit.zip                          (~1.2 MB,  open data, datos.gob.es a09002970)
#
# Idempotent + keyless: re-running skips a feed whose stops.txt already exists.
# Pin a snapshot for reproducibility by committing the unzipped dirs to bronze
# (feeds are refreshed ~quarterly; Renfe's published validity is dated).
#
# IMPORTANT feed-shape facts discovered while verifying (drive the io_gtfs wiring):
#   1. fomento_transit.zip is ALL Spanish Cercanías (Madrid, Valencia, …): 1155
#      stops, only ~204 inside the Catalonia bbox. Non-Catalan stops MUST be
#      dropped (lon 0..4, lat 40.5..42.9) BEFORE GTFS_STOPS_SCHEMA.validate or
#      the strict range checks fail.
#   2. FGC ships calendar_dates.txt ONLY — there is NO calendar.txt. The current
#      io_gtfs.compute_frequency() does pd.read_csv(gd/"calendar.txt") and will
#      raise FileNotFoundError on FGC. The fetch is fine; the parser needs a
#      calendar_dates fallback (see returned wiring notes).
#   3. FGC stops.txt has stop_lat,stop_lon column order swapped vs Rodalies —
#      io_gtfs reads by column NAME so this is harmless, just noted.
#
# Usage:
#   scripts/fetch_gtfs_v2.sh            # fetch both (cached feeds skipped)
#   scripts/fetch_gtfs_v2.sh --force    # re-download even if cached
#   scripts/fetch_gtfs_v2.sh -h

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DEST="${REPO_ROOT}/data/bronze/gtfs"

# Verified-live source URLs (override via env for a pinned mirror).
RODALIES_URL="${RODALIES_GTFS_URL:-https://ssl.renfe.com/ftransit/Fichero_CER_FOMENTO/fomento_transit.zip}"
FGC_URL="${FGC_GTFS_URL:-https://www.fgc.cat/google/google_transit.zip}"

# GTFS tables compute_frequency() needs. calendar.txt is feed-dependent:
# Rodalies has it; FGC does not (calendar_dates.txt only). We only HARD-require
# the always-present trio and report calendar presence so the parser can branch.
readonly REQUIRED_TABLES=(stops.txt trips.txt stop_times.txt)

FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$DEST/rodalies" "$DEST/fgc"

fetch() {
  local label="$1" url="$2" dest="$3"

  if [[ "$FORCE" != "1" && -f "${dest}/stops.txt" ]]; then
    echo "skip $label (cached at ${dest})"
    return 0
  fi

  echo "fetch $label → ${dest}"
  echo "  url: $url"
  local tmpzip
  tmpzip="$(mktemp --suffix=.zip)"
  # -f fail on HTTP error, -S show error, -L follow redirects, retry transient.
  if ! curl -fSL --retry 3 --retry-delay 2 --max-time 180 "$url" -o "$tmpzip"; then
    echo "FAIL  $label download ($url)" >&2
    rm -f "$tmpzip"
    return 1
  fi

  # Validate it is actually a zip before clobbering the cache dir.
  if ! unzip -tq "$tmpzip" >/dev/null 2>&1; then
    echo "FAIL  $label payload is not a valid zip (got $(file -b "$tmpzip" 2>/dev/null || echo unknown))" >&2
    rm -f "$tmpzip"
    return 1
  fi

  unzip -q -o "$tmpzip" -d "$dest"
  rm -f "$tmpzip"

  # Post-fetch table check (hard-fail only on the always-required trio).
  local missing=()
  for t in "${REQUIRED_TABLES[@]}"; do
    [[ -f "${dest}/${t}" ]] || missing+=("$t")
  done
  if (( ${#missing[@]} )); then
    echo "FAIL  $label missing required GTFS tables: ${missing[*]}" >&2
    return 1
  fi

  if [[ -f "${dest}/calendar.txt" ]]; then
    echo "  ok $label: has calendar.txt (compute_frequency weekday path)"
  elif [[ -f "${dest}/calendar_dates.txt" ]]; then
    echo "  ok $label: calendar_dates.txt only (NO calendar.txt) — parser must use the calendar_dates fallback"
  else
    echo "WARN  $label: neither calendar.txt nor calendar_dates.txt — frequency cannot be computed" >&2
  fi

  echo "  ok $label: $(ls "${dest}"/*.txt 2>/dev/null | wc -l) GTFS tables in ${dest}"
}

rc=0
fetch rodalies "$RODALIES_URL" "$DEST/rodalies" || rc=1
fetch fgc      "$FGC_URL"      "$DEST/fgc"      || rc=1

echo ""
echo "GTFS bronze size:"
du -sh "$DEST" 2>/dev/null || true

if (( rc )); then
  echo "" >&2
  echo "One or more feeds failed. Notebook 02 falls back to the synthetic" >&2
  echo "trips_per_day=12 / trips_to_bcn=0 path (io_osm stations) when feeds absent." >&2
fi
exit "$rc"
