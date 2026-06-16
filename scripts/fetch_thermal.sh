#!/usr/bin/env bash
# fetch_thermal.sh — Landsat C2-L2 summer (JJA) Land Surface Temperature composite.
#
# THIS IS A PYTHON/STAC STEP, not a plain curl download. Landsat scenes live on
# Microsoft Planetary Computer (STAC), are accessed as signed COGs, and must be
# median-composited over the JJA window before they are useful. We therefore
# drive the existing src/catmob/io_thermal.py functions from a small inline
# Python block.
#
# Output (idempotent, skip-if-present):
#   data/bronze/thermal/lst_summer_jja_<YEAR>.tif   — JJA median LST, deg C,
#                                                     100 m, EPSG:25831 COG
#
# Access is KEYLESS: planetary_computer.sign_inplace works anonymously (an
# optional PC_SDK_SUBSCRIPTION_KEY only raises rate limits). No credentials
# are committed or required.
#
# Downstream wiring (NOT done here — another agent owns notebooks/ + src/):
#   Sedona reads the COG and runs RS_ZonalStats per H3 hex
#   (docs/sedona_sql_patterns.md §4) to fill lst_summer_median_c; uhi_delta_c
#   is then a pure-SQL rural-baseline subtraction.
#
# Usage:
#   scripts/fetch_thermal.sh                 # default year 2024
#   scripts/fetch_thermal.sh --year 2023
#   scripts/fetch_thermal.sh --max-cloud 30
#   scripts/fetch_thermal.sh --force         # re-composite even if cached

set -euo pipefail

YEAR="2024"
MAX_CLOUD="20"
FORCE="0"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --year) YEAR="$2"; shift 2 ;;
    --max-cloud) MAX_CLOUD="$2"; shift 2 ;;
    --force) FORCE="1"; shift ;;
    -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DEST="${REPO_ROOT}/data/bronze/thermal"
readonly OUT_TIF="${DEST}/lst_summer_jja_${YEAR}.tif"

mkdir -p "$DEST"

# Idempotency: a non-empty cached COG short-circuits the whole STAC pull.
if [[ -f "$OUT_TIF" && -s "$OUT_TIF" && "$FORCE" != "1" ]]; then
  echo "skip LST ${YEAR} (cached at $OUT_TIF)"
  echo "  use --force to re-composite"
  exit 0
fi

# Pick the project python (sedona conda env has pystac-client + planetary-computer
# + rioxarray + rasterio; the system python3 does not). Fall back to PATH python3.
PY="/home/nls/miniforge3/envs/sedona/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi
echo "using python: $PY"

# stackstac is the fast multi-scene mosaicker but is NOT installed in the sedona
# env (verified 2026-06). It is pip-installable and pulls no heavy native deps
# (numpy/dask/rasterio already present). Install on demand into the same env;
# the Python step below also carries a pure-rioxarray fallback if this fails.
if ! "$PY" -c "import stackstac" >/dev/null 2>&1; then
  echo "stackstac missing — installing into the env (one-time) ..."
  "$PY" -m pip install --quiet "stackstac>=0.5" || \
    echo "WARN: stackstac install failed; will use rioxarray per-scene fallback" >&2
fi

echo "compositing JJA ${YEAR} LST (max_cloud<${MAX_CLOUD}%) -> $OUT_TIF"

REPO_ROOT="$REPO_ROOT" YEAR="$YEAR" MAX_CLOUD="$MAX_CLOUD" OUT_TIF="$OUT_TIF" \
"$PY" - <<'PY'
import os
import sys
from pathlib import Path

repo = Path(os.environ["REPO_ROOT"])
sys.path.insert(0, str(repo / "src"))

year = int(os.environ["YEAR"])
max_cloud = int(os.environ["MAX_CLOUD"])
out_tif = Path(os.environ["OUT_TIF"])

from catmob.io_thermal import query_summer_lst_scenes, CATALONIA_BBOX

# 1. STAC query (keyless signing happens inside _open_stac_client()).
items = query_summer_lst_scenes(year=year, max_cloud_pct=max_cloud, bbox=CATALONIA_BBOX)
print(f"  STAC: {len(items)} Landsat 8/9 scenes in JJA {year}")
if not items:
    print("  ERROR: 0 scenes returned — widen --max-cloud or check the year", file=sys.stderr)
    sys.exit(4)

# 2. Median composite via the project function (stackstac path).
#    Falls back to a per-scene rioxarray median if stackstac is unavailable.
try:
    import stackstac  # noqa: F401
    from catmob.io_thermal import composite_lst_summer_median
    composite_lst_summer_median(items, bbox=CATALONIA_BBOX, out_path=out_tif)
except Exception as e:  # stackstac missing OR compose failure -> rioxarray fallback
    print(f"  stackstac path unavailable ({e!r}); using rioxarray per-scene fallback", file=sys.stderr)
    import numpy as np
    import rioxarray  # noqa: F401
    import xarray as xr
    import planetary_computer as pc

    arrs = []
    for it in items:
        href = it["assets"]["lwir11"]["href"]
        href = pc.sign(href)  # keyless SAS signing of the COG asset
        da = rioxarray.open_rasterio(href, masked=True).squeeze("band", drop=True)
        # Landsat C2L2 fill/nodata is 0 -> drop before scaling so it cannot
        # corrupt the per-pixel median (0*scale+offset = 149K = -124 C).
        da = da.where(da != 0)
        # DN -> Kelvin -> Celsius (ST_B10 scale/offset).
        da = da * 0.00341802 + 149.0 - 273.15
        da = da.rio.reproject("EPSG:25831", resolution=100)
        arrs.append(da)
    # Align on a common grid and take the per-pixel median across scenes.
    stacked = xr.concat([a.rename("lst") for a in arrs], dim="scene", join="outer")
    lst_med = stacked.median(dim="scene", skipna=True)
    lst_med = lst_med.rio.write_crs("EPSG:25831")
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    lst_med.rio.to_raster(out_tif, driver="COG", compress="DEFLATE")

print(f"  wrote {out_tif} ({out_tif.stat().st_size/1e6:.2f} MB)")
PY

echo ""
echo "Done. Thermal bronze:"
ls -lh "$OUT_TIF" 2>/dev/null || true
du -sh "$DEST" 2>/dev/null || true
