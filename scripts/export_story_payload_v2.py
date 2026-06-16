#!/usr/bin/env python
"""SIDE export of the v2 gold liveability layer for visual preview.

Non-destructive sibling of scripts/export_story_payload.py. Reads the v2 gold
parquet (data/gold/h3_res8_catalonia_v2.parquet) and the v2 catmob.scoring
(saturating closeness REWARDS), recomputes score_<preset> for all 4 presets,
and writes docs/story_data_v2/{hexes,arcs,pois,manifest}.json in the SAME shape
the geo-browser (docs/app/geobrowser-map.js) expects.

Does NOT touch docs/story_data/ or docs/explore.html. Arcs/POIs are pure
geometry inputs (unchanged between v1 and v2) and are copied over from the v1
deck HTML payload so the input layers still render.

Run with the sedona env python:
    /home/nls/miniforge3/envs/sedona/bin/python scripts/export_story_payload_v2.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd  # noqa: E402
from catmob import scoring  # noqa: E402

GOLD = REPO / "data" / "gold" / "h3_res8_catalonia_v2.parquet"
HTML = REPO / "docs" / "catalonia_liveability.html"   # source of arcs/pois (geometry inputs)
OUT = REPO / "docs" / "story_data_v2"
PRESETS = ["default", "nature_first", "quiet_strict", "amenity_first"]

# Columns the geo-browser visualises. v2 adds green_min_m / sea_min_m /
# pharmacy_density_per_km2; v2.1 adds tree_cover_pct / natura2000_within_5km /
# eprtr_facility_min_m / no2_ugm3 / pm25_ugm3. Keeps the metrics the FIELDS
# registry recolours by.
HEX_COLS = [
    "h3_id", "lon_centroid", "lat_centroid",
    "train_reach_min", "climb_min_m", "yoga_min_m", "hospital_min_m",
    "green_min_m", "sea_min_m", "pharmacy_density_per_km2",
    # v2.1 next-wave features
    "tree_cover_pct", "natura2000_within_5km", "eprtr_facility_min_m",
    "no2_ugm3", "pm25_ugm3",
    "mitma_inflow_daily", "mitma_outflow_daily", "mitma_through_ratio",
    "motorway_within_500m", "industry_density_per_km2",
]


def _records_nan_safe(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list[dict] with EVERY NaN/NA/inf converted to None.

    pandas ``DataFrame.to_json`` emits bare ``NaN`` tokens (invalid JSON). We go
    through Python objects instead: pandas null (NaN, NA, NaT) -> None, and any
    residual float inf -> None, so ``json.dumps(..., allow_nan=False)`` succeeds
    and the browser sees ``null`` (its documented "no data" grey state).
    """
    import math

    recs = df.to_dict(orient="records")
    out = []
    for r in recs:
        clean = {}
        for k, v in r.items():
            if v is None:
                clean[k] = None
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                clean[k] = None
            elif v is pd.NA or (not isinstance(v, (list, dict)) and pd.isna(v)):
                clean[k] = None
            else:
                clean[k] = v
        out.append(clean)
    return out


def _scrub(obj):
    """Recursively replace NaN/Infinity floats with None in nested JSON data.

    The arcs/pois payloads are lifted verbatim from the v1 deck.gl HTML, which
    embeds bare ``NaN`` (e.g. a POI with a missing coordinate). Scrub them so
    the emitted .json is valid; the browser reads ``null`` as "no data".
    """
    import math

    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v) for v in obj]
    return obj


def _dump(path: Path, obj) -> None:
    """Serialise with allow_nan=False so a stray NaN/Infinity fails LOUDLY here
    rather than producing an invalid .json the browser silently chokes on.
    Input is scrubbed first (NaN/inf -> null) so valid data never trips it."""
    path.write_text(json.dumps(_scrub(obj), allow_nan=False), encoding="utf-8")


def _extract_payload(html: str) -> dict:
    """Pull the embedded ``const PAYLOAD = {...};`` object out of the deck.gl HTML."""
    start = html.index("const PAYLOAD")
    eq = html.index("=", start) + 1
    nxt = html.index("const {", eq)
    semi = html.rindex(";", eq, nxt)
    return json.loads(html[eq:semi].strip())


def _round_hex_fields(df: pd.DataFrame) -> None:
    for c in ("lon_centroid", "lat_centroid"):
        if c in df:
            df[c] = df[c].round(5)
    for p in PRESETS:
        c = f"score_{p}"
        if c in df:
            df[c] = df[c].round(1)
    # Reach / distance fields: nearest int, NULLs preserved via nullable Int64.
    for c in ("train_reach_min", "climb_min_m", "yoga_min_m", "hospital_min_m",
              "green_min_m", "sea_min_m"):
        if c in df:
            df[c] = df[c].round().astype("Int64")
    for c in ("mitma_inflow_daily", "mitma_outflow_daily", "industry_density_per_km2"):
        if c in df:
            df[c] = df[c].round().astype("Int64")
    if "pharmacy_density_per_km2" in df:
        df["pharmacy_density_per_km2"] = df["pharmacy_density_per_km2"].round(2)
    if "mitma_through_ratio" in df:
        df["mitma_through_ratio"] = df["mitma_through_ratio"].round(3)
    if "motorway_within_500m" in df:
        df["motorway_within_500m"] = df["motorway_within_500m"].astype(bool)
    # v2.1 next-wave fields. eprtr distance -> int metres (NULLs preserved);
    # no2/pm25 -> 1 dp ug/m3; tree_cover -> 1 dp %; natura -> bool.
    for c in ("eprtr_facility_min_m",):
        if c in df:
            df[c] = df[c].round().astype("Int64")
    for c in ("no2_ugm3", "pm25_ugm3", "tree_cover_pct"):
        if c in df:
            df[c] = df[c].round(1)
    if "natura2000_within_5km" in df:
        # nullable boolean -> bool (no NaN in this column; all hexes resolved)
        df["natura2000_within_5km"] = df["natura2000_within_5km"].astype(bool)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(GOLD)
    n = len(df)

    for p in PRESETS:
        df[f"score_{p}"] = scoring.score_dataframe(df, preset=p)["liveability_score"]

    keep = [c for c in HEX_COLS if c in df.columns] + [f"score_{p}" for p in PRESETS]
    hexes = df[keep].copy()
    _round_hex_fields(hexes)
    # NaN-safe: NaN/NA -> None -> JSON null (NO bare NaN tokens).
    _dump(OUT / "hexes.json", _records_nan_safe(hexes))

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
    _dump(OUT / "arcs.json", arcs)
    _dump(OUT / "pois.json", pois)

    manifest = {
        "n_hexes": int(n),
        "coverage": coverage,
        "score_stats": score_stats,
        "presets": PRESETS,
        "arc_count": len(arcs),
        "poi_categories": {k: len(v) for k, v in pois.items()},
        "source_initial_view": init_view,
        "source_basemap": basemap,
        "version": "v2",
    }
    # manifest may carry NaN inside coverage/score_stats if a column is all-null;
    # scrub + allow_nan=False guarantees we never emit a bare NaN token here.
    (OUT / "manifest.json").write_text(
        json.dumps(_scrub(manifest), indent=2, allow_nan=False), encoding="utf-8")

    print(f"OK v2 n_hexes={n} arcs={len(arcs)} poi_cats={list(pois)} -> {OUT}")
    print("coverage:", {k: coverage[k] for k in (
        "train_reach_min", "climb_min_m", "yoga_min_m", "hospital_min_m",
        "green_min_m", "sea_min_m", "pharmacy_density_per_km2") if k in coverage})
    print("score_stats.default:", score_stats["default"])
    print("score_stats.amenity_first:", score_stats["amenity_first"])


if __name__ == "__main__":
    main()
