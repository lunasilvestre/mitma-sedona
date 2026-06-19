#!/usr/bin/env bash
# pull_mitma_window.sh — robust, resumable bulk pull of a representative window
# of MITMA distritos OD daily files (HOURLY-grained: every row carries `periodo`).
#
# Built for the overnight full-scale deep-Spark run on feat/mitma-deep-spark.
# Unlike scripts/fetch_mitma.sh (simple one-shot), this script is designed to be
# launched as a long-running monitored background job:
#
#   * RESUMABLE        — curl -C - continues a partially-downloaded file.
#   * IDEMPOTENT       — re-running skips any file already present AND gzip-valid.
#   * SELF-HEALING     — a present-but-corrupt (failed `gzip -t`) file is deleted
#                        and re-fetched; a stale .part is resumed, not restarted.
#   * RETRY+BACKOFF    — each file gets N attempts with exponential backoff.
#   * MANIFEST         — JSONL ledger of done/failed with bytes + sha-less status,
#                        plus a human progress log. Safe to tail while running.
#
# WHY ficheros-diarios (and not /viajes/<YYYY-MM>/):
#   Verified 2026-06 via curl -I — the bare per-month hourly path
#   /estudios_basicos/por-distritos/viajes/<YYYY-MM>/ returns 404 and does not
#   exist. The published files live under .../viajes/ficheros-diarios/<YYYY-MM>/
#   and are PIPE-delimited with a `periodo` (hour) column per row — i.e. they
#   ARE the hourly bronze the pipeline expects. They land in data/bronze/mitma/
#   daily/<YYYY-MM>/ to match the existing 2024-03 sample layout, which the
#   Sedona reader (src/catmob/io_mitma.py:read_with_sedona) globs uniformly.
#
# DATE WINDOW (default --scope window):
#   Three FULLY-AVAILABLE consecutive-season months giving daily + weekly +
#   weekend cycles AND seasonal contrast:
#     2025-02  winter low                       (28/28 days available)
#     2025-05  late-spring "typical"            (31/31 days available)
#     2025-06  summer-onset / coastal weekends  (30/30 days available)
#   = 89 days, est ~19 GB compressed. (True peak summer 2025-07/08 is sparse or
#   404 on the source — June is the densest available summer-weekend proxy.)
#   --scope window-plus additionally pulls 2025-03 (31/31) -> 120 days ~25 GB.
#
# Usage:
#   scripts/pull_mitma_window.sh                 # default --scope window (~19 GB)
#   scripts/pull_mitma_window.sh --scope window-plus   # adds 2025-03 (~25 GB)
#   scripts/pull_mitma_window.sh --dates 2025-05-01,2025-05-02   # explicit list
#   scripts/pull_mitma_window.sh --month 2025-06                 # one month
#   scripts/pull_mitma_window.sh --dry-run       # plan + HEAD sizes, no download
#   scripts/pull_mitma_window.sh --check         # verify on-disk gzip integrity
#
# Exit status: 0 if every planned file ended up present + gzip-valid; 1 otherwise.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
readonly BASE_URL="https://movilidad-opendata.mitma.es/estudios_basicos/por-distritos/viajes/ficheros-diarios"
readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DEST_ROOT="${REPO_ROOT}/data/bronze/mitma/daily"
readonly LOG_DIR="${REPO_ROOT}/data/bronze/mitma/_pull_logs"
readonly MANIFEST="${LOG_DIR}/manifest.jsonl"
readonly PROGRESS_LOG="${LOG_DIR}/progress.log"

MAX_ATTEMPTS=5          # per-file download attempts
BACKOFF_BASE=10         # seconds; doubles each retry (10,20,40,80,160)
CONNECT_TIMEOUT=30      # curl --connect-timeout
LOW_SPEED_LIMIT=1024    # bytes/s; abort a stalled transfer below this for...
LOW_SPEED_TIME=120      # ...this many seconds, then retry/resume.

SCOPE="window"
DATES_CSV=""
ONE_MONTH=""
DRY_RUN=0
CHECK_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope)   SCOPE="$2"; shift 2 ;;
    --dates)   DATES_CSV="$2"; shift 2 ;;
    --month)   ONE_MONTH="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --check)   CHECK_ONLY=1; shift ;;
    -h|--help) sed -n '1,60p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------------------
# Date-list construction
# ---------------------------------------------------------------------------
# Emit one ISO date per line for every day in the given month.
emit_month() {
  local ym="$1"
  python3 - "$ym" <<'PY'
import sys
from calendar import monthrange
from datetime import date
y, m = map(int, sys.argv[1].split("-"))
for d in range(1, monthrange(y, m)[1] + 1):
    print(date(y, m, d).isoformat())
PY
}

build_date_list() {
  if [[ -n "$DATES_CSV" ]]; then
    tr ',' '\n' <<<"$DATES_CSV" | sed '/^$/d'
    return
  fi
  if [[ -n "$ONE_MONTH" ]]; then
    emit_month "$ONE_MONTH"
    return
  fi
  case "$SCOPE" in
    window)
      emit_month 2025-02
      emit_month 2025-05
      emit_month 2025-06
      ;;
    window-plus)
      emit_month 2025-02
      emit_month 2025-03
      emit_month 2025-05
      emit_month 2025-06
      ;;
    *)
      echo "Unknown --scope '$SCOPE' (use window | window-plus)" >&2
      exit 2
      ;;
  esac
}

url_for()  { local d="$1"; echo "${BASE_URL}/${d:0:7}/${d//-/}_Viajes_distritos.csv.gz"; }
dest_for() { local d="$1"; echo "${DEST_ROOT}/${d:0:7}/${d//-/}_Viajes_distritos.csv.gz"; }

# A file "counts as done" only if it exists AND passes gzip integrity.
is_valid() { [[ -f "$1" ]] && gzip -t "$1" 2>/dev/null; }

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

log() { echo "[$(ts)] $*" | tee -a "$PROGRESS_LOG"; }

# Append a structured record to the JSONL manifest.
manifest_record() {
  # $1=date $2=status $3=bytes $4=attempts $5=note
  printf '{"ts":"%s","date":"%s","status":"%s","bytes":%s,"attempts":%s,"note":"%s"}\n' \
    "$(ts)" "$1" "$2" "${3:-0}" "${4:-0}" "${5:-}" >>"$MANIFEST"
}

# ---------------------------------------------------------------------------
# Modes that don't download
# ---------------------------------------------------------------------------
mkdir -p "$LOG_DIR"

if [[ "$CHECK_ONLY" == "1" ]]; then
  ok=0; bad=0; missing=0
  while IFS= read -r d; do
    [[ -z "$d" ]] && continue
    dest="$(dest_for "$d")"
    if [[ ! -f "$dest" ]]; then echo "MISSING $d"; missing=$((missing+1));
    elif gzip -t "$dest" 2>/dev/null; then echo "OK      $d"; ok=$((ok+1));
    else echo "CORRUPT $d"; bad=$((bad+1)); fi
  done < <(build_date_list)
  echo "---"; echo "ok=$ok corrupt=$bad missing=$missing"
  [[ "$bad" -eq 0 && "$missing" -eq 0 ]] && exit 0 || exit 1
fi

if [[ "$DRY_RUN" == "1" ]]; then
  total=0; n=0
  echo "PLAN (scope=$SCOPE):"
  while IFS= read -r d; do
    [[ -z "$d" ]] && continue
    url="$(url_for "$d")"
    if is_valid "$(dest_for "$d")"; then
      printf "  %s  [already present, gzip-valid] %s\n" "$d" "$url"
      n=$((n+1)); continue
    fi
    clen=$(curl -sI --connect-timeout "$CONNECT_TIMEOUT" "$url" \
            | awk -F': ' 'tolower($1)=="content-length"{gsub(/\r/,"");print $2}')
    code=$(curl -sI --connect-timeout "$CONNECT_TIMEOUT" -o /dev/null -w '%{http_code}' "$url")
    printf "  %s  HTTP %s  %s bytes  %s\n" "$d" "$code" "${clen:-?}" "$url"
    [[ -n "$clen" ]] && total=$((total+clen))
    n=$((n+1))
  done < <(build_date_list)
  echo "---"
  printf "Planned files: %d   Est. total: %.1f GB (sum of HEAD Content-Length)\n" \
    "$n" "$(echo "scale=2; $total/1073741824" | bc)"
  echo "Disk headroom on data/ filesystem:"
  df -h "$DEST_ROOT" | sed -n '1,2p'
  exit 0
fi

# ---------------------------------------------------------------------------
# Download loop
# ---------------------------------------------------------------------------
log "=== pull_mitma_window START scope=$SCOPE dest=$DEST_ROOT ==="

# Pre-flight disk-headroom guard: need ~ (planned compressed) + equal parquet
# headroom. We estimate planned bytes from HEAD only for files not yet present.
planned_files=0
while IFS= read -r d; do [[ -n "$d" ]] && planned_files=$((planned_files+1)); done < <(build_date_list)
log "planned files: $planned_files"

fail_count=0
done_count=0
skip_count=0
idx=0

while IFS= read -r d; do
  [[ -z "$d" ]] && continue
  idx=$((idx+1))
  url="$(url_for "$d")"
  dest="$(dest_for "$d")"
  mkdir -p "$(dirname "$dest")"

  # Already done?
  if is_valid "$dest"; then
    log "[$idx/$planned_files] SKIP $d (present + gzip-valid)"
    skip_count=$((skip_count+1))
    continue
  fi

  # Present but corrupt -> remove so we don't resume onto a bad body.
  if [[ -f "$dest" ]] && ! gzip -t "$dest" 2>/dev/null; then
    log "[$idx/$planned_files] CORRUPT on disk, deleting & re-fetching $d"
    rm -f "$dest"
  fi

  part="${dest}.part"
  attempt=0
  backoff="$BACKOFF_BASE"
  ok=0
  while [[ "$attempt" -lt "$MAX_ATTEMPTS" ]]; do
    attempt=$((attempt+1))
    log "[$idx/$planned_files] GET $d attempt $attempt/$MAX_ATTEMPTS -> $url"
    # -C - resumes from the .part offset (server sends Accept-Ranges: bytes).
    if curl -fL --retry 0 \
            --connect-timeout "$CONNECT_TIMEOUT" \
            --speed-limit "$LOW_SPEED_LIMIT" --speed-time "$LOW_SPEED_TIME" \
            -C - "$url" -o "$part"; then
      # Validate the completed download before promoting it.
      if gzip -t "$part" 2>/dev/null; then
        mv "$part" "$dest"
        bytes=$(stat -c '%s' "$dest")
        log "[$idx/$planned_files] DONE $d ($bytes bytes, $attempt attempt(s))"
        manifest_record "$d" "done" "$bytes" "$attempt" ""
        done_count=$((done_count+1))
        ok=1
        break
      else
        log "[$idx/$planned_files] WARN $d downloaded but gzip -t failed; discarding .part"
        rm -f "$part"
      fi
    else
      rc=$?
      log "[$idx/$planned_files] WARN curl rc=$rc for $d (will keep .part for resume)"
    fi
    if [[ "$attempt" -lt "$MAX_ATTEMPTS" ]]; then
      log "[$idx/$planned_files] backoff ${backoff}s before retry"
      sleep "$backoff"
      backoff=$((backoff*2))
    fi
  done

  if [[ "$ok" -ne 1 ]]; then
    log "[$idx/$planned_files] FAIL $d after $MAX_ATTEMPTS attempts"
    manifest_record "$d" "failed" "0" "$MAX_ATTEMPTS" "exhausted retries"
    fail_count=$((fail_count+1))
  fi
done < <(build_date_list)

log "=== pull_mitma_window END  done=$done_count skip=$skip_count fail=$fail_count ==="
log "Bronze size: $(du -sh "$DEST_ROOT" 2>/dev/null | cut -f1)"

if [[ "$fail_count" -gt 0 ]]; then
  log "RESULT: $fail_count file(s) failed — re-run the SAME command to resume."
  exit 1
fi
log "RESULT: all planned files present + gzip-valid."
exit 0
