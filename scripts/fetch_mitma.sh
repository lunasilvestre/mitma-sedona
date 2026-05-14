#!/usr/bin/env bash
# fetch_mitma.sh — idempotent download of MITMA v2 OD CSV.gz files.
#
# Default --scope full = Q1+Q2 2024 daily + all March 2024 hourly.
# --scope dev          = week 2024-03-04..10 daily + day 2024-03-06 hourly.
#
# All URL construction matches src/catmob/io_mitma.py:build_url so the
# bash and Python paths cannot diverge silently.
#
# Usage:
#   scripts/fetch_mitma.sh                      # default --scope full
#   scripts/fetch_mitma.sh --scope dev          # fast local iteration
#   scripts/fetch_mitma.sh --kind daily --month 2024-03
#   scripts/fetch_mitma.sh --kind hourly --week 2024-03-04
#   scripts/fetch_mitma.sh --check              # HEAD-only smoke check

set -euo pipefail

readonly BASE_URL="https://opendata-movilidad.mitma.es/estudios_basicos/por-distritos/viajes"
readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DEST_ROOT="${REPO_ROOT}/data/bronze/mitma"

SCOPE="full"
KIND=""
MONTH=""
WEEK=""
CHECK_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope)  SCOPE="$2"; shift 2 ;;
    --kind)   KIND="$2"; shift 2 ;;
    --month)  MONTH="$2"; shift 2 ;;
    --week)   WEEK="$2"; shift 2 ;;
    --check)  CHECK_ONLY=1; shift ;;
    -h|--help)
      sed -n '1,20p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Generate the list of (kind, date) pairs based on flags.
generate_targets() {
  local kind="$1" first="$2" last="$3"
  python3 - "$kind" "$first" "$last" <<'PY'
import sys
from datetime import date, timedelta
kind, first, last = sys.argv[1], sys.argv[2], sys.argv[3]
d0 = date.fromisoformat(first); d1 = date.fromisoformat(last)
d = d0
while d <= d1:
    print(f"{kind}\t{d.isoformat()}")
    d += timedelta(days=1)
PY
}

build_url() {
  local kind="$1" date="$2"
  local yyyymm="${date:0:7}"
  local yyyymmdd="${date//-/}"
  if [[ "$kind" == "daily" ]]; then
    echo "${BASE_URL}/ficheros-diarios/${yyyymm}/${yyyymmdd}_Viajes_distritos.csv.gz"
  else
    echo "${BASE_URL}/${yyyymm}/${yyyymmdd}_Viajes_distritos.csv.gz"
  fi
}

determine_targets() {
  local lines=""
  if [[ -n "$KIND" && -n "$MONTH" ]]; then
    # Single explicit month
    local first="${MONTH}-01"
    local last
    last=$(python3 -c "from datetime import date; from calendar import monthrange; y,m=map(int,'${MONTH}'.split('-')); print(date(y,m,monthrange(y,m)[1]).isoformat())")
    lines+="$(generate_targets "$KIND" "$first" "$last")"$'\n'
  elif [[ -n "$KIND" && -n "$WEEK" ]]; then
    # Single explicit week (7 days starting from --week)
    local first="$WEEK"
    local last
    last=$(python3 -c "from datetime import date, timedelta; print((date.fromisoformat('${WEEK}') + timedelta(days=6)).isoformat())")
    lines+="$(generate_targets "$KIND" "$first" "$last")"$'\n'
  elif [[ "$SCOPE" == "dev" ]]; then
    lines+="$(generate_targets daily  2024-03-04 2024-03-10)"$'\n'
    lines+="$(generate_targets hourly 2024-03-06 2024-03-06)"$'\n'
  else  # full
    lines+="$(generate_targets daily  2024-01-01 2024-06-30)"$'\n'
    lines+="$(generate_targets hourly 2024-03-01 2024-03-31)"$'\n'
  fi
  echo -n "$lines"
}

mkdir -p "$DEST_ROOT/daily" "$DEST_ROOT/hourly"

while IFS=$'\t' read -r kind date; do
  [[ -z "$kind" || -z "$date" ]] && continue
  url=$(build_url "$kind" "$date")
  yyyymm="${date:0:7}"
  fname="$(basename "$url")"
  dest="${DEST_ROOT}/${kind}/${yyyymm}/${fname}"

  if [[ "$CHECK_ONLY" == "1" ]]; then
    if curl -sIfL "$url" -o /dev/null; then
      echo "OK   $url"
    else
      echo "MISS $url"
    fi
    continue
  fi

  if [[ -f "$dest" ]]; then
    echo "skip $kind $date (cached at $dest)"
    continue
  fi

  mkdir -p "$(dirname "$dest")"
  echo "fetch $kind $date → $dest"
  if curl -sfL "$url" -o "${dest}.tmp"; then
    mv "${dest}.tmp" "$dest"
  else
    echo "FAIL $url" >&2
    rm -f "${dest}.tmp"
  fi
done < <(determine_targets)

echo ""
echo "Done. Bronze size:"
du -sh "$DEST_ROOT" 2>/dev/null || true
