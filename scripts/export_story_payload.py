#!/usr/bin/env python
"""Export per-hex story payloads for docs/story.html.

Run with the sedona env python (has pandas + pyarrow + pyyaml); NO Spark:

    /home/nls/miniforge3/envs/sedona/bin/python scripts/export_story_payload.py

Reads the gold parquet (12 columns; ``liveability_score`` is NOT stored), recomputes
the score for all 4 presets via ``catmob.scoring`` (pure-python arithmetic), and writes
``docs/story_data/{hexes,arcs,pois,manifest}.json``. The H3 geometry is reconstructed
client-side from ``h3_id`` (h3-js), so only the id + values are emitted here.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd  # noqa: E402
from catmob import scoring  # noqa: E402

GOLD = REPO / "data" / "gold" / "h3_res8_catalonia.parquet"
HTML = REPO / "docs" / "catalonia_liveability.html"
OUT = REPO / "docs" / "story_data"
PRESETS = ["default", "nature_first", "quiet_strict", "amenity_first"]

# Columns the story chapters visualize (the populated-enough subset of the 12).
HEX_COLS = [
    "h3_id", "lon_centroid", "lat_centroid",
    "train_reach_min", "climb_min_m", "yoga_min_m", "hospital_min_m",
    "mitma_inflow_daily", "mitma_outflow_daily", "mitma_through_ratio",
    "motorway_within_500m", "industry_density_per_km2",
]


def _extract_payload(html: str) -> dict:
    """Pull the embedded ``const PAYLOAD = {...};`` object out of the deck.gl HTML."""
    start = html.index("const PAYLOAD")
    eq = html.index("=", start) + 1
    nxt = html.index("const {", eq)          # the line right after PAYLOAD in the template
    semi = html.rindex(";", eq, nxt)
    return json.loads(html[eq:semi].strip())


def _round_hex_fields(df: pd.DataFrame) -> None:
    """Round numeric story fields in place to shrink the JSON payload.

    Uses pandas nullable ``Int64`` for integer fields that may contain NaN so
    that missing values stay ``null`` in JSON instead of becoming 0.
    """
    # Coordinates: 5 decimals (~1.1 m at this latitude).
    for c in ("lon_centroid", "lat_centroid"):
        if c in df:
            df[c] = df[c].round(5)

    # Scores: 1 decimal. .round() is NaN-safe and keeps them as floats.
    for p in PRESETS:
        c = f"score_{p}"
        if c in df:
            df[c] = df[c].round(1)

    # Reach/time/distance fields: nearest int, NULLs preserved via nullable Int64.
    for c in ("train_reach_min", "climb_min_m", "yoga_min_m", "hospital_min_m"):
        if c in df:
            df[c] = df[c].round().astype("Int64")

    # MITMA flow counts + industry density: plain ints (no NULLs in source).
    for c in ("mitma_inflow_daily", "mitma_outflow_daily", "industry_density_per_km2"):
        if c in df:
            df[c] = df[c].round().astype("Int64")

    # Through ratio: 3 decimals.
    if "mitma_through_ratio" in df:
        df["mitma_through_ratio"] = df["mitma_through_ratio"].round(3)

    # Motorway proximity: bool.
    if "motorway_within_500m" in df:
        df["motorway_within_500m"] = df["motorway_within_500m"].astype(bool)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(GOLD)
    n = len(df)

    for p in PRESETS:
        df[f"score_{p}"] = scoring.score_dataframe(df, preset=p)["liveability_score"]

    keep = [c for c in HEX_COLS if c in df.columns] + [f"score_{p}" for p in PRESETS]
    hexes = df[keep].copy()
    _round_hex_fields(hexes)
    (OUT / "hexes.json").write_text(hexes.to_json(orient="records"), encoding="utf-8")

    coverage = {c: round(float(df[c].notna().mean()), 4) for c in HEX_COLS if c in df.columns}
    score_stats = {}
    for p in PRESETS:
        d = df[f"score_{p}"].describe()
        score_stats[p] = {k: round(float(d[k]), 2) for k in ["min", "25%", "50%", "75%", "max", "mean", "std"]}

    arcs: list = []
    pois: dict = {}
    init_view = None
    basemap = None
    if HTML.exists():
        try:
            payload = _extract_payload(HTML.read_text(encoding="utf-8"))
            arcs = payload.get("arcs", [])
            pois = payload.get("pois", {})
            init_view = payload.get("initial_view")
            basemap = payload.get("basemap")
        except Exception as exc:  # pragma: no cover - defensive
            print(f"WARN could not extract arcs/pois from {HTML.name}: {exc}", file=sys.stderr)
    (OUT / "arcs.json").write_text(json.dumps(arcs), encoding="utf-8")
    (OUT / "pois.json").write_text(json.dumps(pois), encoding="utf-8")

    manifest = {
        "n_hexes": int(n),
        "coverage": coverage,
        "score_stats": score_stats,
        "presets": PRESETS,
        "arc_count": len(arcs),
        "poi_categories": {k: len(v) for k, v in pois.items()},
        "source_initial_view": init_view,
        "source_basemap": basemap,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"OK n_hexes={n} arcs={len(arcs)} poi_cats={list(pois)} -> {OUT}")
    print("coverage:", coverage)
    print("score_stats.default:", score_stats["default"])


if __name__ == "__main__":
    main()
