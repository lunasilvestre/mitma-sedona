#!/usr/bin/env bash
# fetch_gtfs.sh — Renfe Rodalies + FGC GTFS bundles.

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DEST="${REPO_ROOT}/data/bronze/gtfs"

mkdir -p "$DEST/rodalies" "$DEST/fgc"

# Renfe Cercanías Barcelona (Rodalies). transitfeeds is read-only mirror.
RODALIES_URL="https://transitfeeds.com/p/renfe/505/latest/download"
FGC_URL="https://www.fgc.cat/wp-content/uploads/horaris/google_transit.zip"

fetch() {
  local label="$1" url="$2" dest="$3"
  if [[ -f "${dest}/stops.txt" ]]; then
    echo "skip $label (cached)"
    return 0
  fi
  echo "fetch $label → $dest"
  local tmpzip
  tmpzip=$(mktemp --suffix=.zip)
  if curl -sfL "$url" -o "$tmpzip"; then
    unzip -q -o "$tmpzip" -d "$dest"
    rm -f "$tmpzip"
  else
    echo "FAIL $label fetch" >&2
    rm -f "$tmpzip"
    return 1
  fi
}

fetch rodalies "$RODALIES_URL" "$DEST/rodalies" || true
fetch fgc      "$FGC_URL"      "$DEST/fgc"      || true

echo ""
echo "Done. GTFS bronze size:"
du -sh "$DEST" 2>/dev/null || true
