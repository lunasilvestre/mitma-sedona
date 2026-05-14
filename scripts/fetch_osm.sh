#!/usr/bin/env bash
# fetch_osm.sh — Geofabrik PBF download + osmium tag-filter pre-prune.
#
# Pre-pruning the PBF to ~50 MB before Sedona ingest is a 5x speedup.
# The tag-filter spec is read from src/catmob/io_osm.py:OSMIUM_TAG_FILTER
# so the bash script and Python source cannot drift silently.
#
# Usage:
#   scripts/fetch_osm.sh           # default cataluna
#   scripts/fetch_osm.sh --bbox catalonia
#   scripts/fetch_osm.sh --no-prune  # keep raw PBF too

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DEST="${REPO_ROOT}/data/bronze/osm"
readonly URL="https://download.geofabrik.de/europe/spain/cataluna-latest.osm.pbf"

NO_PRUNE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bbox) shift 2 ;;  # accepted for symmetry; only catalonia for now
    --no-prune) NO_PRUNE=1; shift ;;
    -h|--help) sed -n '1,15p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$DEST"
cd "$DEST"

raw="cataluna-latest.osm.pbf"
pruned="cataluna_pruned.osm.pbf"

if [[ ! -f "$raw" ]]; then
  echo "fetching $URL → $DEST/$raw"
  curl -sfL "$URL" -o "${raw}.tmp" && mv "${raw}.tmp" "$raw"
else
  echo "skip raw PBF (cached at $DEST/$raw)"
fi

if [[ "$NO_PRUNE" == "1" ]]; then
  echo "skip prune (--no-prune)"
  exit 0
fi

# Generate osmium filter spec from the Python source-of-truth.
spec=$(python3 - <<'PY'
import sys
sys.path.insert(0, "src")
from catmob.io_osm import OSMIUM_TAG_FILTER
print(" ".join(OSMIUM_TAG_FILTER))
PY
)

if [[ -z "$spec" ]]; then
  echo "FAIL: could not load OSMIUM_TAG_FILTER from catmob.io_osm" >&2
  exit 3
fi

echo "pruning with osmium tag-filter (spec from src/catmob/io_osm.py)..."
echo "  spec: $spec"
osmium tags-filter "$raw" $spec -o "${pruned}.tmp" --overwrite
mv "${pruned}.tmp" "$pruned"

echo ""
echo "Done. Sizes:"
ls -lh "$raw" "$pruned"
