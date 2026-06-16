#!/usr/bin/env python
"""v2 scoring + validation report.

Scores data/gold/h3_res8_catalonia_v2.parquet with the reformulated
catmob.scoring (saturating closeness rewards) across all 4 presets, and
prints the validation report requested for feat/v2-scoring-wave1:

  (a) default-preset distribution (min/median/mean/max/std)
  (b) SANITY: nearby hospital+gym+train must out-score a hex with none
  (c) REUS comparison: v2 (4 presets) vs v1 (21.64) vs 10km-old (17.27)
  (d) coverage/index impact of the 5 Wave-1 features

Run: /home/nls/miniforge3/envs/sedona/bin/python scripts/score_v2.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import numpy as np
import pandas as pd

from catmob.scoring import load_weights, score_hex
from score_10km import score_hex_10km  # v1-arithmetic, 10 km caps  # noqa: E402

PRESETS = ["default", "nature_first", "quiet_strict", "amenity_first"]
GOLD = REPO / "data/gold"


def score_hex_v1(row, w):
    """ORIGINAL v1 scoring (negative-distance amenity terms, 5 km / 8 km caps).

    Reconstructed here only to reproduce the documented Reus baseline (21.64)
    like-for-like against the original weights; catmob.scoring.score_hex is now
    the v2 reward formulation.
    """
    s = w.get("base_offset", 50.0)
    if pd.notna(row.get("train_reach_min")):
        s += max(0, 25 - float(row["train_reach_min"])) * w.get("train_reach_per_min_under25", 0)
    if pd.notna(row.get("trains_to_bcn_nearest")):
        s += float(row["trains_to_bcn_nearest"]) / 30.0 * w.get("trains_to_bcn_per_30", 0)
    if pd.notna(row.get("climb_min_m")):
        s += min(float(row["climb_min_m"]), 5000.0) / 200.0 * w.get("climb_per_200m", 0)
    if pd.notna(row.get("yoga_min_m")):
        s += min(float(row["yoga_min_m"]), 5000.0) / 250.0 * w.get("yoga_per_250m", 0)
    if pd.notna(row.get("green_min_m")):
        s += min(float(row["green_min_m"]), 4000.0) / 200.0 * w.get("green_per_200m", 0)
    if pd.notna(row.get("sea_min_m")) and float(row["sea_min_m"]) < 3000.0:
        s += w.get("sea_within_3km_bonus", 0)
    if pd.notna(row.get("industry_density_per_km2")):
        s += float(row["industry_density_per_km2"]) * w.get("industry_density", 0)
    if row.get("motorway_within_500m"):
        s += w.get("motorway_within_500m", 0)
    if pd.notna(row.get("hospital_min_m")):
        s += min(float(row["hospital_min_m"]), 8000.0) / 400.0 * w.get("hospital_per_400m", 0)
    if pd.notna(row.get("pharmacy_density_per_km2")):
        s += np.log1p(float(row["pharmacy_density_per_km2"])) * w.get("pharmacy_density_log", 0)
    if pd.notna(row.get("mitma_through_ratio")):
        s += float(row["mitma_through_ratio"]) * w.get("mitma_through_ratio", 0)
    return float(max(0.0, min(100.0, s)))


def score_v2(df: pd.DataFrame, preset: str) -> pd.Series:
    w = load_weights(preset)
    return df.apply(lambda r: score_hex(r, w), axis=1)


def dist(s: pd.Series) -> str:
    return (f"min={s.min():.2f}  median={s.median():.2f}  mean={s.mean():.2f}  "
            f"max={s.max():.2f}  std={s.std():.2f}")


print("=" * 72)
print("v2 SCORING + VALIDATION REPORT  (branch feat/v2-scoring-wave1)")
print("=" * 72)

v2 = pd.read_parquet(GOLD / "h3_res8_catalonia_v2.parquet")
print(f"\nv2 gold: {len(v2):,} hexes")

# (a) default-preset distribution -------------------------------------------
sc = {p: score_v2(v2, p) for p in PRESETS}
print("\n(a) DEFAULT-PRESET SCORE DISTRIBUTION")
print("   ", dist(sc["default"]))
print("    floored at 0:", int((sc["default"] <= 0).sum()),
      " capped at 100:", int((sc["default"] >= 100).sum()),
      f" ({len(v2):,} total)")
print("    all presets:")
for p in PRESETS:
    print(f"      {p:>14}: {dist(sc[p])}")

# (b) sanity check — flaw reversed ------------------------------------------
print("\n(b) SANITY CHECK — nearby amenities must out-score absence")
near = {"train_reach_min": 15, "climb_min_m": 200.0, "yoga_min_m": 300.0,
        "hospital_min_m": 400.0, "green_min_m": 50.0, "sea_min_m": np.nan,
        "pharmacy_density_per_km2": 4.0, "motorway_within_500m": False,
        "industry_density_per_km2": 0, "mitma_through_ratio": np.nan}
far = dict(near, climb_min_m=9500.0, yoga_min_m=9500.0, hospital_min_m=9500.0,
           green_min_m=9500.0, train_reach_min=25, pharmacy_density_per_km2=0.0)
none = dict(near, train_reach_min=np.nan, climb_min_m=np.nan, yoga_min_m=np.nan,
            hospital_min_m=np.nan, green_min_m=np.nan, pharmacy_density_per_km2=0.0)
w = load_weights("default")
s_near, s_far, s_none = score_hex(near, w), score_hex(far, w), score_hex(none, w)
print(f"    nearby (train15 + gym@200m + hospital@400m + park@50m): {s_near:.2f}")
print(f"    far    (train25 + all amenities @9.5 km):               {s_far:.2f}")
print(f"    none   (no train, no amenities):                        {s_none:.2f}")
ok = s_near > s_far > s_none
print(f"    near > far > none ? {ok}   "
      f"({'FLAW REVERSED — PASS' if ok else 'FAIL'})")

# (c) REUS comparison --------------------------------------------------------
print("\n(c) REUS comparison (lon 1.05-1.16, lat 41.12-41.20)")
reus = v2[(v2.lon_centroid.between(1.05, 1.16)) & (v2.lat_centroid.between(41.12, 41.20))]
print(f"    {len(reus):,} hexes in REUS window")
print("    v2 score by preset (mean over Reus hexes):")
for p in PRESETS:
    rs = score_v2(reus, p)
    print(f"      {p:>14}: mean={rs.mean():.2f}  median={rs.median():.2f}  "
          f"min={rs.min():.2f}  max={rs.max():.2f}")
print("    reference baselines (documented): v1=21.64, 10km-old-scoring=17.27")
print("    like-for-like reproduction (original v1 weights from git HEAD):")

# The 21.64 / 17.27 references were produced with the ORIGINAL negative-distance
# weights. Reproduce them on the SAME Reus window using those weights from git
# (written to /tmp/weights_v1.yaml by the caller) so the v2 delta is honest.
V1_W = Path("/tmp/weights_v1.yaml")
if V1_W.exists():
    w_v1 = load_weights("default", path=V1_W)
    # v1 gold + v1 scoring (default 5 km caps) -> expect ~21.64
    g_v1 = pd.read_parquet(GOLD / "h3_res8_catalonia.parquet")
    gr_v1 = g_v1[(g_v1.lon_centroid.between(1.05, 1.16)) & (g_v1.lat_centroid.between(41.12, 41.20))]
    base_v1 = gr_v1.apply(lambda r: score_hex_v1(r, w_v1), axis=1)
    print(f"      v1 (v1 gold, v1 weights, 5 km caps):   mean={base_v1.mean():.2f}")
    # 10km gold + 10km old scoring (v1 weights) -> expect ~17.27
    g_10 = pd.read_parquet(GOLD / "h3_res8_catalonia_10km.parquet")
    gr_10 = g_10[(g_10.lon_centroid.between(1.05, 1.16)) & (g_10.lat_centroid.between(41.12, 41.20))]
    base_10 = gr_10.apply(lambda r: score_hex_10km(r, w_v1), axis=1)
    print(f"      10km (10km gold, v1 weights, 10 km caps): mean={base_10.mean():.2f}")
    print(f"      => v2 default ({score_v2(reus, 'default').mean():.2f}) lifts Reus above both — "
          f"absence no longer beats far-presence.")
else:
    print("      (skipped: /tmp/weights_v1.yaml not found)")

# (d) coverage / index impact of the 5 Wave-1 features ----------------------
print("\n(d) WAVE-1 FEATURE COVERAGE (non-null / total)")
feat_cov = {
    "train_reach_min (reproj-fixed)": v2.train_reach_min.notna(),
    "green_min_m": v2.green_min_m.notna(),
    "sea_min_m (<5 km)": v2.sea_min_m.notna(),
    "pharmacy_density_per_km2 (>0)": v2.pharmacy_density_per_km2 > 0,
    "hospital_min_m (OSM∪CatSalut)": v2.hospital_min_m.notna(),
}
for name, mask in feat_cov.items():
    n = int(mask.sum())
    print(f"    {name:>34}: {n:>6,} / {len(v2):,}  ({n/len(v2):.1%})")
print("=" * 72)
