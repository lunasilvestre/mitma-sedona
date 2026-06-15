"""Multi-criteria liveability scoring.

Loads the weight vector from ``configs/weights.yaml`` (4 presets shipped:
``default``, ``nature_first``, ``quiet_strict``, ``amenity_first``) and
applies it to a Pandas DataFrame mirroring ``GOLD_HEX_SCHEMA``.

The score is computed in pure Python so it's easy to step through and
unit-test; for production rendering we ship the same arithmetic via a
PySpark UDF (``score_udf``) so it can be applied in a Sedona pipeline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEIGHTS_PATH = REPO_ROOT / "configs" / "weights.yaml"


def load_weights(preset: str = "default", path: Path | str | None = None) -> dict[str, float]:
    """Load a weight vector by preset name."""
    p = Path(path) if path else DEFAULT_WEIGHTS_PATH
    with p.open() as fh:
        cfg = yaml.safe_load(fh)
    if preset not in cfg:
        raise KeyError(f"preset {preset!r} not in {list(cfg)}")
    # Resolve inheritance from default for non-default presets.
    base = dict(cfg["default"])
    if preset != "default":
        base.update(cfg[preset])
    return base


# story:scoring:score_hex
def score_hex(row: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Compute a single hex's liveability score from its features.

    The score is a weighted sum, clipped to ``[0, 100]``. Missing values
    contribute zero to that term (they don't crash; documented limitation).
    """
    w = weights
    s = w.get("base_offset", 50.0)

    # Mobility & accessibility
    if pd.notna(row.get("train_reach_min")):
        s += max(0, 25 - float(row["train_reach_min"])) * w.get("train_reach_per_min_under25", 0)
    if pd.notna(row.get("trains_to_bcn_nearest")):
        s += float(row["trains_to_bcn_nearest"]) / 30.0 * w.get("trains_to_bcn_per_30", 0)

    # Lifestyle amenities (weights are negative coefficients on distance/200m)
    if pd.notna(row.get("climb_min_m")):
        s += min(float(row["climb_min_m"]), 5000.0) / 200.0 * w.get("climb_per_200m", 0)
    if pd.notna(row.get("yoga_min_m")):
        s += min(float(row["yoga_min_m"]), 5000.0) / 250.0 * w.get("yoga_per_250m", 0)

    # Nature
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

    # Health amenities
    if pd.notna(row.get("hospital_min_m")):
        s += min(float(row["hospital_min_m"]), 8000.0) / 400.0 * w.get("hospital_per_400m", 0)
    if pd.notna(row.get("pharmacy_density_per_km2")):
        s += np.log1p(float(row["pharmacy_density_per_km2"])) * w.get("pharmacy_density_log", 0)

    # Mobility "vibe check"
    if pd.notna(row.get("mitma_through_ratio")):
        s += float(row["mitma_through_ratio"]) * w.get("mitma_through_ratio", 0)

    return float(max(0.0, min(100.0, s)))
# story:end


def score_dataframe(
    df: pd.DataFrame, *, preset: str = "default", weights: Mapping[str, float] | None = None
) -> pd.DataFrame:
    """Add a ``liveability_score`` column to a Pandas DataFrame in place-safe way."""
    w = dict(weights) if weights is not None else load_weights(preset)
    out = df.copy()
    out["liveability_score"] = out.apply(lambda r: score_hex(r, w), axis=1)
    return out


def sensitivity_top10(df: pd.DataFrame, presets: list[str] | None = None, k: int = 10) -> pd.DataFrame:
    """Return per-preset top-k h3_ids and a Jaccard overlap matrix vs default."""
    presets = presets or ["default", "nature_first", "quiet_strict", "amenity_first"]
    tops: dict[str, set[str]] = {}
    for p in presets:
        scored = score_dataframe(df, preset=p)
        tops[p] = set(scored.nlargest(k, "liveability_score")["h3_id"].tolist())
    matrix = []
    for a in presets:
        row = {"preset": a}
        for b in presets:
            inter = len(tops[a] & tops[b])
            union = len(tops[a] | tops[b])
            row[b] = round(inter / union, 3) if union else 0.0
        matrix.append(row)
    return pd.DataFrame(matrix).set_index("preset")
