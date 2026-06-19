#!/usr/bin/env python
"""Export the NEW MITMA deep-Spark mobility layers to docs/story_data/*.json.

Mirrors scripts/export_story_payload_v2.py (same NaN-safe / allow_nan=False
scrub, same rounding discipline, same keyless-static output dir) but for the
mobility analytic layers produced by scripts/build_mitma_layers.py:

  * MERGES the new per-hex scalar columns into docs/story_data/hexes.json
    (am/pm/midday/night peak shares, peak_hour_bucket, weekend_weekday_ratio,
    leisure/commute shares, weekend_hotspot_score, mobility_typology, the
    geodemographic shares + geodemo_diversity, intra_zone_share, support_n) and
    RECOMPUTES the dasymetric mitma_inflow/outflow/through_ratio columns.
  * ships docs/story_data/rhythm.json  — h3_id -> [24 floats] (lazy sibling).
  * REPLACES docs/story_data/arcs.json  — Sedona-built, identical shape.
  * updates docs/story_data/manifest.json coverage + mobility stats + the
    typology legend.

KEYLESS / STATIC: only additional JSON columns + sibling files; no app change
beyond the FIELDS registry + explore.html selector wiring (DEV-#2 W10).

Run with the sedona env python (only needs pandas)::
    /home/nls/miniforge3/envs/sedona/bin/python scripts/export_mitma_layers.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import pandas as pd  # noqa: E402

GOLD = REPO / "data" / "gold"
OUT = REPO / "docs" / "story_data"

# Two gold sources, in preference order. (1) DEV-#1's canonical lakehouse gold
# from scripts/run_mitma_pipeline.py; (2) the DEV-#2 dev-bridge gold from
# scripts/build_mitma_layers.py (used when DEV-#1's pipeline hasn't run yet).
# Column names are identical by design (both follow the architecture); this
# export is the single integration point that reads whichever is on disk.
DEV1 = GOLD / "mitma_features" / "zoning=distritos"
SOURCES = {
    "dev1": {
        "features": DEV1 / "h3_mitma_features.parquet",
        "rhythm_long": DEV1 / "rhythm_long.parquet",   # (h3_id, periodo, share) long-form
        "arcs": DEV1 / "arcs.json",                     # {source:[lon,lat],target:[...],value}
    },
    "dev2_bridge": {
        "features": GOLD / "mitma_mobility_gold.parquet",
        "rhythm_wide": GOLD / "mitma_rhythm.parquet",   # h3_id + '0'..'23' columns
        "arcs": GOLD / "mitma_arcs_gold.parquet",        # flat source_lon/.../flow
    },
}

# The new scalar mobility columns merged into hexes.json (alongside v2 columns).
# Recomputed dasymetric flow columns OVERWRITE the naive-centroid v2 values.
MOBILITY_SCALAR_COLS = [
    # recomputed (dasymetric) — replace the naive-centroid v2 values in-place
    "mitma_inflow_daily", "mitma_outflow_daily", "mitma_through_ratio",
    # rhythm scalars
    "am_peak_share", "midday_share", "pm_peak_share", "night_share", "peak_hour_bucket",
    # weekend
    "weekend_weekday_ratio", "leisure_share", "commute_share", "weekend_hotspot_score",
    # typology + self-containment
    "mobility_typology", "intra_zone_share",
    # geodemographic — honest KNOWN-subset shares + transparency companions
    "low_income_inflow_share", "youth_mobility_share", "senior_mobility_share",
    "female_share", "geodemo_diversity",
    "female_of_all_trips", "youth_of_all_trips", "senior_of_all_trips",
    "low_income_of_all_trips",
    "sexo_coverage", "edad_coverage", "renta_coverage",
    # density / confidence proxy (OD-segment row count — NOT the privacy gate)
    "support_n",
]

# Fixed label->meaning order for the typology legend (manifest + browser). Must
# stay in sync with CATEGORICAL.mobility_typology in docs/app/geobrowser-map.js.
# Data-driven labels: the sink/source pair was dropped (sink_source range too
# narrow at daily distrito resolution to support it); commuter-corridor (high
# work/study pull) replaces it.
TYPOLOGY_LABELS = [
    "commuter-corridor", "leisure-magnet", "transit-corridor",
    "self-contained", "mixed-balanced",
]


def _scrub(obj):
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v) for v in obj]
    return obj


def _records_nan_safe(df: pd.DataFrame) -> list[dict]:
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


def _dump(path: Path, obj, *, indent=None) -> None:
    path.write_text(json.dumps(_scrub(obj), allow_nan=False, indent=indent), encoding="utf-8")


def _round_mobility(df: pd.DataFrame) -> None:
    """Round mobility scalars for a compact, honest hexes.json."""
    for c in ("mitma_inflow_daily", "mitma_outflow_daily", "support_n"):
        if c in df:
            df[c] = df[c].round().astype("Int64")
    for c in ("mitma_through_ratio", "weekend_weekday_ratio", "weekend_hotspot_score",
              "geodemo_diversity"):
        if c in df:
            df[c] = df[c].round(3)
    for c in ("am_peak_share", "midday_share", "pm_peak_share", "night_share",
              "leisure_share", "commute_share", "intra_zone_share",
              "low_income_inflow_share", "youth_mobility_share",
              "senior_mobility_share", "female_share",
              "female_of_all_trips", "youth_of_all_trips", "senior_of_all_trips",
              "low_income_of_all_trips",
              "sexo_coverage", "edad_coverage", "renta_coverage"):
        if c in df:
            df[c] = df[c].round(3)


def _resolve_source() -> tuple[str, dict]:
    """Pick the gold source: DEV-#1 canonical lakehouse, else DEV-#2 dev-bridge."""
    if SOURCES["dev1"]["features"].exists():
        return "dev1", SOURCES["dev1"]
    if SOURCES["dev2_bridge"]["features"].exists():
        return "dev2_bridge", SOURCES["dev2_bridge"]
    sys.exit(
        "no gold features parquet found — run scripts/run_mitma_pipeline.py "
        "(DEV-#1) or scripts/build_mitma_layers.py (DEV-#2 dev-bridge) first"
    )


def _load_rhythm_map(src_name: str, src: dict) -> dict:
    """h3_id -> [24 floats], from either the long-form (DEV-#1) or wide (DEV-#2)."""
    rmap: dict[str, list] = {}
    if src_name == "dev1" and src["rhythm_long"].exists():
        rl = pd.read_parquet(src["rhythm_long"])  # (h3_id, periodo, share)
        for h3, grp in rl.groupby("h3_id"):
            vec = [0.0] * 24
            for _, r in grp.iterrows():
                p = int(r["periodo"])
                if 0 <= p < 24 and pd.notna(r["share"]):
                    vec[p] = round(float(r["share"]), 4)
            rmap[h3] = vec
        return rmap
    wide = src.get("rhythm_wide")
    if wide and wide.exists():
        rw = pd.read_parquet(wide)
        cols = [str(i) for i in range(24)]
        for _, row in rw.iterrows():
            rmap[row["h3_id"]] = [
                round(float(row[c]), 4) if (c in rw.columns and pd.notna(row[c])) else 0.0
                for c in cols
            ]
    return rmap


def _load_arcs(src: dict) -> list[dict]:
    """Normalise either arcs shape to the deck.gl ArcLayer flat shape.

    DEV-#1 emits arcs.json as {source:[lon,lat], target:[lon,lat], value};
    DEV-#2's parquet is already flat source_lon/.../flow. Both -> the flat
    {source_lon, source_lat, target_lon, target_lat, flow} the ArcLayer reads.
    """
    p = src["arcs"]
    if not p.exists():
        return []
    out: list[dict] = []
    if p.suffix == ".json":
        raw = json.loads(p.read_text(encoding="utf-8"))
        for a in raw:
            if "source_lon" in a:  # already flat
                s_lon, s_lat = a["source_lon"], a["source_lat"]
                t_lon, t_lat = a["target_lon"], a["target_lat"]
                flow = a.get("flow", a.get("value"))
            else:                  # nested {source:[lon,lat], target:[...], value}
                s_lon, s_lat = a["source"][0], a["source"][1]
                t_lon, t_lat = a["target"][0], a["target"][1]
                flow = a.get("value", a.get("flow"))
            out.append({
                "source_lon": round(float(s_lon), 6), "source_lat": round(float(s_lat), 6),
                "target_lon": round(float(t_lon), 6), "target_lat": round(float(t_lat), 6),
                "flow": round(float(flow), 2),
            })
    else:  # parquet (DEV-#2 dev-bridge)
        df = pd.read_parquet(p)
        for c in ("source_lon", "source_lat", "target_lon", "target_lat"):
            df[c] = df[c].round(6)
        df["flow"] = df["flow"].round(2)
        out = df[["source_lon", "source_lat", "target_lon", "target_lat", "flow"]].to_dict(orient="records")
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src_name, src = _resolve_source()
    print(f"gold source: {src_name}  ({src['features']})")

    mob = pd.read_parquet(src["features"])
    keep = ["h3_id"] + [c for c in MOBILITY_SCALAR_COLS if c in mob.columns]
    mob = mob[keep].copy()
    _round_mobility(mob)

    # --- merge into existing hexes.json (preserve all v2 columns) -----------
    hexes_path = OUT / "hexes.json"
    hexes = json.loads(hexes_path.read_text(encoding="utf-8"))
    hx_df = pd.DataFrame(hexes)
    # Drop EVERY mobility column we're about to (re)write — both the dasymetric
    # flow columns and every other mobility scalar — so a re-export overwrites
    # cleanly instead of colliding into pandas _x/_y suffixes. Only h3_id is the
    # join key; all v2 (non-mobility) columns are preserved.
    drop_cols = [c for c in mob.columns if c != "h3_id" and c in hx_df.columns]
    if drop_cols:
        hx_df = hx_df.drop(columns=drop_cols)
    merged = hx_df.merge(mob, on="h3_id", how="left")
    _dump(hexes_path, _records_nan_safe(merged))
    print(f"OK hexes.json: {len(merged):,} hexes, +{len(mob.columns) - 1} mobility cols")

    # --- rhythm sibling: h3_id -> [24 floats] (lazy-loaded, NOT in hexes.json) -
    rmap = _load_rhythm_map(src_name, src)
    _dump(OUT / "rhythm.json", rmap)
    print(f"OK rhythm.json: {len(rmap):,} hex 24h profiles")

    # --- arcs (Sedona-built, normalised to the deck.gl ArcLayer flat shape) ----
    arcs = _load_arcs(src)
    _dump(OUT / "arcs.json", arcs)
    print(f"OK arcs.json: {len(arcs):,} Sedona-built OD arcs")

    # --- manifest: extend coverage + mobility stats + typology legend --------
    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cov = manifest.get("coverage", {})
    for c in MOBILITY_SCALAR_COLS:
        if c in merged.columns:
            cov[c] = round(float(merged[c].notna().mean()), 4)
    manifest["coverage"] = cov

    # mobility_stats: a small summary block for the browser headline.
    mstats = {}
    for c in ("mitma_inflow_daily", "weekend_weekday_ratio", "weekend_hotspot_score",
              "leisure_share", "geodemo_diversity", "night_share"):
        if c in merged.columns:
            d = merged[c].astype(float).describe()
            mstats[c] = {k: round(float(d[k]), 3) for k in ("min", "50%", "max", "mean")}
    manifest["mobility_stats"] = mstats

    # Typology: count EVERY label actually present (incl. mixed-balanced and any
    # cluster-suffix variants the pipeline emits), known labels in fixed order
    # first. The browser legend mirrors this ordering.
    if "mobility_typology" in merged:
        counts = merged["mobility_typology"].value_counts(dropna=True).to_dict()
        extra = [l for l in counts if l not in TYPOLOGY_LABELS]
        present = [l for l in TYPOLOGY_LABELS if l in counts] + sorted(extra)
        manifest["typology_counts"] = {l: int(counts[l]) for l in present}
    else:
        present = []
        manifest["typology_counts"] = {}
    manifest["typology_labels"] = TYPOLOGY_LABELS
    manifest["typology_present"] = present
    manifest["arc_count"] = len(arcs)
    manifest["mobility_source"] = src_name
    manifest["mobility_window"] = "7 days, March 2024 (2024-03-04..10), distritos zoning"
    manifest["mobility_method"] = "dasymetric zone->H3 crosswalk (Sedona ST_Intersection, EPSG:25831)"
    manifest["version"] = "v3-mobility"

    manifest_path.write_text(
        json.dumps(_scrub(manifest), indent=2, allow_nan=False), encoding="utf-8")
    print(f"OK manifest.json: typology_present={present}")
    print("   typology_counts:", manifest["typology_counts"])
    print("   mobility_stats.weekend_hotspot_score:", mstats.get("weekend_hotspot_score"))


if __name__ == "__main__":
    main()
