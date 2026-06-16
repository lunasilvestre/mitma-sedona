#!/usr/bin/env python
"""10 km-aware liveability scoring (EXPLORATORY).

Identical arithmetic to catmob.scoring.score_hex, except the three
distance-capped amenity terms cap at CATCHMENT_M (10 km) instead of the v1
5 km (climb/yoga) and 8 km (hospital). The per-unit weight semantics are
unchanged: climb/yoga are still scored per 200 m / 250 m, hospital per 400 m;
only the clip ceiling moves so the now-populated 8-10 km hexes are not
re-clipped back to the old cap.

Weights still come from configs/weights.yaml via catmob.scoring.load_weights.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import numpy as np
import pandas as pd
from catmob.scoring import load_weights  # noqa: E402

CATCHMENT_M = 10_000.0


def score_hex_10km(row: Mapping[str, float], weights: Mapping[str, float]) -> float:
    w = weights
    s = w.get("base_offset", 50.0)

    # Mobility & accessibility
    if pd.notna(row.get("train_reach_min")):
        s += max(0, 25 - float(row["train_reach_min"])) * w.get("train_reach_per_min_under25", 0)
    if pd.notna(row.get("trains_to_bcn_nearest")):
        s += float(row["trains_to_bcn_nearest"]) / 30.0 * w.get("trains_to_bcn_per_30", 0)

    # Lifestyle amenities -- cap lifted 5000 -> CATCHMENT_M
    if pd.notna(row.get("climb_min_m")):
        s += min(float(row["climb_min_m"]), CATCHMENT_M) / 200.0 * w.get("climb_per_200m", 0)
    if pd.notna(row.get("yoga_min_m")):
        s += min(float(row["yoga_min_m"]), CATCHMENT_M) / 250.0 * w.get("yoga_per_250m", 0)

    # Nature (features absent from v1 gold; kept for parity)
    if pd.notna(row.get("green_min_m")):
        s += min(float(row["green_min_m"]), 4000.0) / 200.0 * w.get("green_per_200m", 0)
    if pd.notna(row.get("sea_min_m")) and float(row["sea_min_m"]) < 3000.0:
        s += w.get("sea_within_3km_bonus", 0)
    if pd.notna(row.get("tree_cover_pct")):
        s += float(row["tree_cover_pct"]) * w.get("tree_cover_pct", 0)
    if row.get("natura2000_within_5km"):
        s += w.get("natura2000_within_5km", 0)
    if pd.notna(row.get("biodiversity_obs_density")):
        s += np.log1p(float(row["biodiversity_obs_density"])) * w.get("biodiversity_obs_log", 0)

    # Environmental health
    if pd.notna(row.get("no2_ugm3")):
        s += max(0.0, float(row["no2_ugm3"]) - 20.0) * w.get("no2_above_who_per_ugm3", 0)
    if pd.notna(row.get("pm25_ugm3")):
        s += max(0.0, float(row["pm25_ugm3"]) - 5.0) * w.get("pm25_above_who_per_ugm3", 0)
    if pd.notna(row.get("uhi_delta_c")):
        s += max(0.0, float(row["uhi_delta_c"])) * w.get("uhi_per_degree", 0)
    if pd.notna(row.get("viirs_radiance")):
        s += float(row["viirs_radiance"]) * w.get("viirs_radiance", 0)

    # Penalties
    if pd.notna(row.get("industry_density_per_km2")):
        s += float(row["industry_density_per_km2"]) * w.get("industry_density", 0)
    if pd.notna(row.get("eprtr_facility_min_m")) and float(row["eprtr_facility_min_m"]) > 0:
        s += (1.0 / float(row["eprtr_facility_min_m"])) * w.get("eprtr_inverse_dist", 0)
    if row.get("motorway_within_500m"):
        s += w.get("motorway_within_500m", 0)

    # Health amenities -- cap lifted 8000 -> CATCHMENT_M
    if pd.notna(row.get("hospital_min_m")):
        s += min(float(row["hospital_min_m"]), CATCHMENT_M) / 400.0 * w.get("hospital_per_400m", 0)
    if pd.notna(row.get("pharmacy_density_per_km2")):
        s += np.log1p(float(row["pharmacy_density_per_km2"])) * w.get("pharmacy_density_log", 0)

    # Mobility "vibe check"
    if pd.notna(row.get("mitma_through_ratio")):
        s += float(row["mitma_through_ratio"]) * w.get("mitma_through_ratio", 0)

    return float(max(0.0, min(100.0, s)))


def score_df(df: pd.DataFrame, preset: str = "default") -> pd.Series:
    w = load_weights(preset)
    return df.apply(lambda r: score_hex_10km(r, w), axis=1)
