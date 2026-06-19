#!/usr/bin/env bash
# run_full_scale.sh — full-scale MITMA deep-Spark pipeline over the 2025 window.
#
# Runs the CANONICAL DEV-#1 chain end-to-end on the 89-day Catalonia window that
# pull_mitma_window.sh downloaded as raw CSV.gz under data/bronze/mitma/daily/:
#
#     2025-02 (28d) + 2025-05 (31d) + 2025-06 (30d)  =  89 days, ~20 GB
#     fecha window  20250201 .. 20250630   (the 2024-03 7d sample is OUT)
#
# CHAIN (the exact scripts/flags the runners support for a window this size):
#
#   STAGE 1  scripts/run_mitma_pipeline.py  (CSV-glob ingest path)
#     One JVM does ALL of: ingest the 3-month CSV brace-glob into the partitioned
#     bronze PARQUET lakehouse (data/bronze/mitma_lakehouse, mode=overwrite) ->
#     build the dasymetric silver crosswalk (EPSG:25831 area math) + od_silver ->
#     compute the 6 gold themes -> write data/gold/mitma_features/zoning=distritos/.
#     The Spark CSV reader takes a single brace glob {2025-02,2025-05,2025-06};
#     --fecha-start/--fecha-end additionally bound the in-frame window defensively.
#     --regression runs the byte-identity gate vs the shipped
#     data/gold/h3_res8_catalonia_v2.parquet and the runner prints the per-zone
#     area_weight sum/zone closure (expect ~1.0).
#     NON-INDEXED RangeJoin path: we DO NOT pass --rtree — its jts isolation
#     breaks the parquet read codegen in the same JVM on this Spark-4.1.1/
#     sedona-1.9.0 pair (per the runner's own --help).
#
#   STAGE 2  scripts/export_mitma_layers.py  (keyless story export)
#     Reads the DEV-#1 gold (preferred source) and (re)writes the keyless static
#     docs/story_data/{hexes,rhythm,arcs,manifest}.json — the shippable layer.
#
# DURABILITY / OBSERVABILITY:
#   * set -euo pipefail; every stage is timed and tee'd to
#     data/gold/_run_logs/full_scale_<ts>.log (also a stable -> latest.log symlink).
#   * INCREMENTAL commits: gold JSON sidecars committed after Stage 1, the
#     docs/story_data payload committed after Stage 2 (per-stage durability).
#   * Final marker: data/gold/_run_logs/DONE  (success)  or
#     data/gold/_run_logs/FAILED  (first line = the stage that failed).
#     The orchestrator chains validation on the DONE marker.
#   * IDEMPOTENT / re-runnable: all writes are mode=overwrite; re-running the
#     whole script re-ingests cleanly and overwrites gold + story_data. Stale
#     DONE/FAILED markers from a prior run are cleared at startup.
#
# Launch (as-is, foreground or as a monitored background job):
#     bash scripts/run_full_scale.sh
#
# Exit status: 0 on full success (DONE written); non-zero on first failure
# (FAILED written with the failing stage). Safe to tail the log while running.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PY="/home/nls/miniforge3/envs/sedona/bin/python"
readonly BRANCH="feat/mitma-deep-spark"

# Analytic window = the three 2025 months (the 2024-03 7d sample is excluded).
readonly FECHA_START="20250201"
readonly FECHA_END="20250630"
# Spark/Hadoop GlobPattern brace-alternation over the three month dirs (89 files).
readonly SAMPLE_GLOB="${REPO_ROOT}/data/bronze/mitma/daily/{2025-02,2025-05,2025-06}/*_Viajes_distritos.csv.gz"

readonly ZONING="distritos"
# Driver heap: machine has 62 GB RAM -> 42g is ~68% (local[*], single JVM).
readonly DRIVER_MEMORY="42g"

# Spark spills shuffle/sort to spark.local.dir (default java.io.tmpdir = /tmp,
# a 32 GB tmpfs on this box). At 89-day scale the spill overflowed it
# ("No space left on device") while the 674 GB nvme sat unused. Redirect Spark's
# scratch to the big disk. SPARK_LOCAL_DIRS is honoured by Spark in local mode.
export SPARK_LOCAL_DIRS="${SPARK_LOCAL_DIRS:-${REPO_ROOT}/data/_spark_local}"
mkdir -p "${SPARK_LOCAL_DIRS}"

readonly LOG_DIR="${REPO_ROOT}/data/gold/_run_logs"
readonly TS="$(date -u +"%Y%m%dT%H%M%SZ")"
readonly LOG="${LOG_DIR}/full_scale_${TS}.log"
readonly DONE_MARKER="${LOG_DIR}/DONE"
readonly FAILED_MARKER="${LOG_DIR}/FAILED"

readonly GOLD_DIR="${REPO_ROOT}/data/gold/mitma_features/zoning=${ZONING}"
readonly STORY_DIR="${REPO_ROOT}/docs/story_data"

mkdir -p "$LOG_DIR"
# Fresh markers each run (idempotent re-launch).
rm -f "$DONE_MARKER" "$FAILED_MARKER"
ln -sfn "$(basename "$LOG")" "${LOG_DIR}/latest.log"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ts()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }

# Write FAILED marker (stage on line 1) and abort. Trapped on any ERR/signal.
CURRENT_STAGE="startup"
fail() {
  local rc=$?
  {
    echo "${CURRENT_STAGE}"
    echo "exit_code=${rc}"
    echo "ts=$(ts)"
    echo "log=${LOG}"
  } > "$FAILED_MARKER"
  log "!!! FAILED at stage='${CURRENT_STAGE}' (exit ${rc}). Marker: ${FAILED_MARKER}"
  exit "$rc"
}
trap fail ERR
trap 'CURRENT_STAGE="${CURRENT_STAGE} (interrupted)"; fail' INT TERM

# Run a stage command, timed, with all output tee'd to the log.
run_stage() {
  local name="$1"; shift
  CURRENT_STAGE="$name"
  log "=== STAGE START: ${name} ==="
  local t0 t1
  t0="$(date +%s)"
  # Pipe stdout+stderr through tee; PIPESTATUS[0] is the python exit code so a
  # stage failure (not tee) trips set -e / the ERR trap.
  set +e
  "$@" 2>&1 | tee -a "$LOG"
  local rc="${PIPESTATUS[0]}"
  set -e
  t1="$(date +%s)"
  if [[ "$rc" -ne 0 ]]; then
    log "=== STAGE FAILED: ${name}  (exit ${rc}, $((t1 - t0))s) ==="
    return "$rc"
  fi
  log "=== STAGE OK: ${name}  ($((t1 - t0))s) ==="
}

# Commit a set of paths to BRANCH if anything changed. Force-adds gitignored
# gold JSON sidecars (data/gold/ is gitignored by design) but NEVER large parquet
# — only the small JSON deliverables. docs/story_data/* is tracked normally.
commit_paths() {
  local msg="$1"; shift
  cd "$REPO_ROOT"
  # Ensure we are on the expected branch (don't silently commit elsewhere).
  local cur
  cur="$(git rev-parse --abbrev-ref HEAD)"
  if [[ "$cur" != "$BRANCH" ]]; then
    log "  [git] on '${cur}', expected '${BRANCH}' — checking out ${BRANCH}"
    git checkout "$BRANCH"
  fi
  local added=0 p
  for p in "$@"; do
    if [[ -e "$p" ]]; then
      # -f so the gitignored gold JSON sidecars can be tracked; tracked
      # docs/story_data files add normally either way.
      git add -f "$p" && added=1
    fi
  done
  if [[ "$added" -eq 1 ]] && ! git diff --cached --quiet; then
    git commit -m "$msg" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>" \
      | tee -a "$LOG"
    log "  [git] committed: ${msg}"
  else
    log "  [git] nothing to commit for: ${msg}"
  fi
}

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
log "=== run_full_scale START  ts=${TS}  branch=${BRANCH} ==="
log "repo=${REPO_ROOT}"
log "python=${PY}"
log "window: fecha ${FECHA_START}..${FECHA_END} (2025-02 + 2025-05 + 2025-06, 89 days)"
log "glob:   ${SAMPLE_GLOB}"
log "driver_memory=${DRIVER_MEMORY}  (machine RAM: $(free -g | awk '/^Mem:/{print $2}') GB)"

CURRENT_STAGE="preflight"
[[ -x "$PY" ]] || { log "env python not found/executable: ${PY}"; false; }
n_csv="$(ls -1 "${REPO_ROOT}"/data/bronze/mitma/daily/2025-02/*_Viajes_distritos.csv.gz \
                "${REPO_ROOT}"/data/bronze/mitma/daily/2025-05/*_Viajes_distritos.csv.gz \
                "${REPO_ROOT}"/data/bronze/mitma/daily/2025-06/*_Viajes_distritos.csv.gz 2>/dev/null | wc -l)"
log "preflight: ${n_csv} CSV.gz files match the 2025 window (expect 89)"
[[ "$n_csv" -ge 1 ]] || { log "no CSV.gz files found for the 2025 window"; false; }
log "preflight: disk headroom on data/ -> $(df -h "${REPO_ROOT}/data" | awk 'NR==2{print $4" free of "$2}')"

# ---------------------------------------------------------------------------
# STAGE 1 — ingest CSV window -> bronze lakehouse -> silver -> gold
# ---------------------------------------------------------------------------
# CSV-glob path (no --from-bronze): the runner writes the partitioned bronze
# parquet lakehouse AND runs silver+gold in one JVM. --fecha-start/--end bound the
# in-frame window; --regression adds the byte-identity gate; the runner prints the
# per-zone area_weight sum closure. NO --rtree (parquet-safe RangeJoin).
run_stage "stage1_pipeline_ingest_silver_gold" \
  "$PY" "${REPO_ROOT}/scripts/run_mitma_pipeline.py" \
    --zoning "$ZONING" \
    --sample-glob "$SAMPLE_GLOB" \
    --fecha-start "$FECHA_START" \
    --fecha-end "$FECHA_END" \
    --driver-memory "$DRIVER_MEMORY" \
    --regression

# Verify the gold parquet landed before declaring the stage durable.
CURRENT_STAGE="stage1_verify"
[[ -f "${GOLD_DIR}/h3_mitma_features.parquet" ]] || {
  log "expected gold features parquet missing: ${GOLD_DIR}/h3_mitma_features.parquet"; false; }
log "stage1 produced: $(ls -1 "${GOLD_DIR}" 2>/dev/null | tr '\n' ' ')"

# Incremental commit: small gold JSON sidecars (force-add; data/gold is gitignored,
# parquet stays out of git — it is a rebuildable lake artifact).
commit_paths "gold(full-scale 2025 window): typology + arcs sidecars [stage1]" \
  "${GOLD_DIR}/typology_centroids.json" \
  "${GOLD_DIR}/arcs.json" \
  "${GOLD_DIR}/arcs_weekend.json"

# ---------------------------------------------------------------------------
# STAGE 2 — export keyless story_data JSON
# ---------------------------------------------------------------------------
run_stage "stage2_export_story_data" \
  "$PY" "${REPO_ROOT}/scripts/export_mitma_layers.py"

CURRENT_STAGE="stage2_verify"
for f in hexes.json rhythm.json arcs.json manifest.json; do
  [[ -f "${STORY_DIR}/${f}" ]] || { log "expected story_data file missing: ${STORY_DIR}/${f}"; false; }
done
log "stage2 produced: $(ls -1 "${STORY_DIR}"/*.json 2>/dev/null | xargs -n1 basename | tr '\n' ' ')"

# Incremental commit: the keyless story_data deliverable (tracked in git).
commit_paths "story_data(full-scale 2025 window): regenerated hexes/rhythm/arcs/manifest [stage2]" \
  "${STORY_DIR}/hexes.json" \
  "${STORY_DIR}/rhythm.json" \
  "${STORY_DIR}/arcs.json" \
  "${STORY_DIR}/manifest.json"

# ---------------------------------------------------------------------------
# DONE
# ---------------------------------------------------------------------------
CURRENT_STAGE="finalize"
{
  echo "ts=$(ts)"
  echo "window=${FECHA_START}..${FECHA_END}"
  echo "driver_memory=${DRIVER_MEMORY}"
  echo "log=${LOG}"
  echo "head=$(git -C "$REPO_ROOT" rev-parse HEAD)"
} > "$DONE_MARKER"
trap - ERR INT TERM
log "=== run_full_scale DONE. Marker: ${DONE_MARKER} ==="
log "    gold:       ${GOLD_DIR}"
log "    story_data: ${STORY_DIR}"
log "    HEAD:       $(git -C "$REPO_ROOT" rev-parse --short HEAD) on ${BRANCH}"
exit 0
