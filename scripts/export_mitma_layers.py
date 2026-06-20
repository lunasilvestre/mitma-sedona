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
        "seasonal_long": DEV1 / "seasonal_long.parquet",  # h3_id + <metric>_<feb|may|jun> wide
    },
    "dev2_bridge": {
        "features": GOLD / "mitma_mobility_gold.parquet",
        "rhythm_wide": GOLD / "mitma_rhythm.parquet",   # h3_id + '0'..'23' columns
        "arcs": GOLD / "mitma_arcs_gold.parquet",        # flat source_lon/.../flow
    },
}

# Silver lakehouse — the dated od_silver partitions whose fecha=YYYYMMDD dirs are
# the ground-truth provenance of the analytic window. The gold features parquet is
# aggregated and carries NO fecha column, so the manifest window is DERIVED from
# these partition dirs (min/max fecha + distinct day count) rather than hardcoded,
# so it can't drift away from the data actually shipped. Defined as scripts/
# run_full_scale.sh's canonical window: 2025-02 + 2025-05 + 2025-06 = 89 days,
# fecha 20250201..20250630.
SILVER_OD = REPO / "data" / "silver" / "od_silver"

# Zone polygons (EPSG:4326 / CRS84 lon-lat, ``ID`` property) — the geometry source
# for arc endpoints. Zone centroids anchor each OD corridor's source/target, exactly
# as the Spark od_arcs builder does via ST_Centroid(geom_ll).
ZONES_GEOJSON = REPO / "data" / "bronze" / "mitma" / "zones" / "zonificacion_distritos.geojson"

# OD arc layer: how many of the strongest inter-zone corridors to ship. The 5,000
# top corridors reproduce the original story-layer density (commit 77bc004); the
# deep-Spark re-export had silently capped this at 250. The browser's data-driven
# width domain (_computeArcDomain/_arcWidth) adapts to whatever flow magnitude the
# resulting set carries — no magic width constant rides on this number.
ARC_TOP_N = 5000

# Fallback window if the silver partitions aren't on disk (e.g. exporting from a
# gold-only checkout). Matches scripts/run_full_scale.sh FECHA_START/FECHA_END.
FALLBACK_WINDOW = "89 days, 2025 (2025-02 + 2025-05 + 2025-06; 20250201..20250630)"

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
    # SEASONAL headline delta(s) — INLINE (the story; must load with the map).
    # Jun (summer-onset) minus Feb (winter) per hex; null where either window
    # failed the per-season support gate. The full per-season fields ride in the
    # lazy seasons.json sidecar, NOT here.
    "weekend_hotspot_summer_minus_winter", "weekend_ratio_summer_minus_winter",
]

# Per-season sidecar (seasons.json) config. Three calendar month-windows, NOT a
# climate average. SHORT metric keys halve the JSON; nested {h3:{season:{...}}}
# avoids key-repetition bloat. The wide parquet col is "<metric>_<season>".
SEASON_KEYS = ("feb", "may", "jun")
SEASON_WINDOWS = [
    {"key": "feb", "label": "winter · Feb 2025", "days": 28},
    {"key": "may", "label": "spring · May 2025", "days": 31},
    {"key": "jun", "label": "summer-onset · Jun 2025", "days": 30},
]
# (long parquet metric name, short sidecar key, round-dp). Order = display order.
SEASON_METRICS = [
    ("weekend_weekday_ratio", "wwr", 3),
    ("weekend_hotspot_score", "whs", 3),
    ("leisure_share", "leis", 3),
    ("commute_share", "comm", 3),
    ("am_peak_share", "am", 3),
    ("pm_peak_share", "pm", 3),
    ("midday_share", "mid", 3),
    ("night_share", "night", 3),
    ("peak_hour_bucket", "peak", None),  # categorical string
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
              "geodemo_diversity",
              "weekend_hotspot_summer_minus_winter", "weekend_ratio_summer_minus_winter"):
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


def _silver_fechas(zoning: str = "distritos") -> list[str]:
    """Sorted distinct fecha=YYYYMMDD partition keys under the silver od_silver dir.

    Single source of truth for the analytic window's day set — used both by
    ``_derive_window`` (manifest provenance string) and ``_build_arcs`` (the
    trips/day divisor). Returns [] if the partitions aren't on disk.
    """
    part_root = SILVER_OD / f"zoning={zoning}"
    if not part_root.exists():
        return []
    return sorted(
        p.name.split("=", 1)[1]
        for p in part_root.iterdir()
        if p.is_dir() and p.name.startswith("fecha=") and p.name.split("=", 1)[1].isdigit()
    )


def _derive_window(zoning: str = "distritos") -> str:
    """Derive the mobility window string from the silver od_silver partition dirs.

    The gold features parquet is aggregated and has no fecha column, so provenance
    is read from the dated silver partitions (data/silver/od_silver/zoning=<z>/
    fecha=YYYYMMDD). Returns a string built from the actual min/max fecha, distinct
    day count and contributing month set — self-correcting, so it can never drift
    from the data on disk. Falls back to FALLBACK_WINDOW if silver is absent.
    """
    fechas = _silver_fechas(zoning)
    if not fechas:
        return FALLBACK_WINDOW
    lo, hi = fechas[0], fechas[-1]
    n_days = len(fechas)
    # Contributing YYYY-MM months in order, e.g. "2025-02 + 2025-05 + 2025-06".
    months = sorted({f"{f[:4]}-{f[4:6]}" for f in fechas})
    months_str = " + ".join(months)
    return f"{n_days} days, {months_str} ({lo}..{hi}), {zoning} zoning"


# Integer-valued distance/reach columns inherited from the v2 hexes.json. Reading
# the JSON into a DataFrame promotes them to float64 (they carry nulls), which
# serialises as "9439.0" — a cosmetic repr drift vs the v2 baseline that wrote
# bare ints "9439". Re-pin them to nullable Int64 so the JSON keeps integer repr
# (null where missing). Values are unchanged; this only fixes the textual repr.
INT_DISTANCE_COLS = [
    "climb_min_m", "eprtr_facility_min_m", "hospital_min_m",
    "sea_min_m", "train_reach_min", "yoga_min_m",
]


def _pin_int_distance_cols(df: pd.DataFrame) -> None:
    """Re-pin integer-valued distance cols to nullable Int64 (integer JSON repr).

    Only pins a column when every non-null value is integral, so we never silently
    truncate a genuinely fractional value.
    """
    for c in INT_DISTANCE_COLS:
        if c not in df.columns:
            continue
        nonnull = df[c].dropna()
        if nonnull.empty or ((nonnull % 1) == 0).all():
            df[c] = df[c].round().astype("Int64")


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


def _build_arcs(zoning: str = "distritos", top_n: int = ARC_TOP_N) -> list[dict] | None:
    """Build the top-N inter-zone OD arcs as trips/day, straight from silver.

    This is the canonical arc layer and replaces the stale gold ``arcs.json``
    passthrough (``_load_arcs``), which had regressed to 250 corridors whose
    ``flow`` was a raw 89-day SUM of ``viajes`` (range ~807k–7.87M). It mirrors
    the Spark ``catmob.pipeline_gold.od_arcs`` semantics exactly — inter-zone
    pairs, sum ``viajes`` over the window, take the strongest ``top_n``, anchor
    each endpoint at its zone centroid — but does it with a light pandas read of
    the dated od_silver partitions (no Spark), and crucially divides the windowed
    sum by the DAY COUNT so ``flow`` is expressed in **trips/day**.

    The divisor is the number of distinct fecha partitions on disk (derived, never
    hardcoded — the full-scale window is 89 days: Feb + May + Jun 2025), so it
    self-corrects to whatever window was actually ingested. Returns the deck.gl
    ArcLayer flat shape ``{source_lon, source_lat, target_lon, target_lat, flow}``;
    returns None if the silver partitions or the zones geojson are absent, so the
    caller can fall back to the gold passthrough.
    """
    fechas = _silver_fechas(zoning)
    part_root = SILVER_OD / f"zoning={zoning}"
    if not fechas or not ZONES_GEOJSON.exists():
        return None
    n_days = len(fechas)

    # Sum viajes per inter-zone (origen, destino) pair across the whole window.
    # Read only the three columns we need, one partition at a time, to keep the
    # ~390M-row scan inside a modest memory budget.
    import glob

    parts = sorted(glob.glob(str(part_root / "fecha=*" / "*.parquet")))
    agg: pd.DataFrame | None = None
    for p in parts:
        df = pd.read_parquet(p, columns=["origen", "destino", "viajes"])
        df = df[df["origen"] != df["destino"]]
        g = df.groupby(["origen", "destino"], as_index=False)["viajes"].sum()
        agg = g if agg is None else (
            pd.concat([agg, g], ignore_index=True)
            .groupby(["origen", "destino"], as_index=False)["viajes"].sum()
        )
    if agg is None or agg.empty:
        return None

    top = agg.nlargest(top_n, "viajes").copy()
    # raw window SUM -> trips/day (the unit the caption + tooltip promise).
    top["flow"] = top["viajes"] / n_days

    # Zone centroids in lon/lat (EPSG:4326) — matches ST_Centroid(geom_ll).
    import geopandas as gpd

    zones = gpd.read_file(ZONES_GEOJSON)[["ID", "geometry"]]
    cents = zones.geometry.centroid
    cmap = {
        str(zid): (float(pt.x), float(pt.y))
        for zid, pt in zip(zones["ID"].astype(str), cents)
    }

    out: list[dict] = []
    for r in top.itertuples(index=False):
        s = cmap.get(str(r.origen))
        t = cmap.get(str(r.destino))
        if s is None or t is None:  # zone not in geojson (Spark inner join drops it too)
            continue
        out.append({
            "source_lon": round(s[0], 6), "source_lat": round(s[1], 6),
            "target_lon": round(t[0], 6), "target_lat": round(t[1], 6),
            "flow": round(float(r.flow), 2),
        })
    return out


def _load_seasonal(src: dict) -> dict:
    """Read seasonal_long.parquet -> nested {h3_id: {season: {short:val}}}.

    The wide parquet has h3_id + ``<metric>_<season>`` columns. We pivot to the
    compact nested sidecar shape used by the browser:
        { "<h3_id>": { "feb": {wwr, whs, leis, comm, am, pm, mid, night, peak},
                       "may": {...}, "jun": {...} }, ... }
    SHORT keys (per SEASON_METRICS) halve the JSON; NaN/None scrubbed; a season
    whose every metric is null for a hex is OMITTED (keeps the sidecar lean — the
    browser treats a missing season as no-data grey, same as a null value).
    """
    p = src.get("seasonal_long")
    if not p or not p.exists():
        return {}
    df = pd.read_parquet(p)
    out: dict[str, dict] = {}
    recs = df.to_dict(orient="records")
    for r in recs:
        h3 = r.get("h3_id")
        if h3 is None:
            continue
        per_season: dict[str, dict] = {}
        for s in SEASON_KEYS:
            vals: dict[str, object] = {}
            for metric, short, dp in SEASON_METRICS:
                v = r.get(f"{metric}_{s}")
                if v is None:
                    continue
                if isinstance(v, float):
                    if math.isnan(v) or math.isinf(v):
                        continue
                    vals[short] = round(v, dp) if dp is not None else v
                elif pd.isna(v):
                    continue
                else:
                    vals[short] = v  # categorical string (peak_hour_bucket)
            if vals:
                per_season[s] = vals
        if per_season:
            out[h3] = per_season
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
    _pin_int_distance_cols(merged)
    _dump(hexes_path, _records_nan_safe(merged))
    print(f"OK hexes.json: {len(merged):,} hexes, +{len(mob.columns) - 1} mobility cols")

    # --- rhythm sibling: h3_id -> [24 floats] (lazy-loaded, NOT in hexes.json) -
    rmap = _load_rhythm_map(src_name, src)
    _dump(OUT / "rhythm.json", rmap)
    print(f"OK rhythm.json: {len(rmap):,} hex 24h profiles")

    # --- arcs: top-N strongest inter-zone corridors as TRIPS/DAY ---------------
    # Built straight from the dated silver od_silver partitions (light pandas read,
    # no Spark): sum viajes per inter-zone pair over the window, take the strongest
    # ARC_TOP_N, divide by the day count -> trips/day. Falls back to the gold
    # arcs.json passthrough only when silver isn't on disk (gold-only checkout).
    arcs = _build_arcs("distritos", ARC_TOP_N)
    if arcs is None:
        arcs = _load_arcs(src)
        print(f"·· arcs: silver absent — fell back to gold passthrough ({len(arcs):,})")
    _dump(OUT / "arcs.json", arcs)
    if arcs:
        _flows = [a["flow"] for a in arcs]
        print(f"OK arcs.json: {len(arcs):,} OD corridors  "
              f"flow(trips/day) {min(_flows):,.1f}..{max(_flows):,.1f}")
    else:
        print("OK arcs.json: 0 arcs")

    # --- seasonal sidecar: lazy seasons.json (nested per-(h3 x season), short keys)
    # Three calendar month-windows (feb/may/jun), NOT a climate average. Loaded by
    # the browser ONLY when a non-Pooled season is selected (mirror of rhythm.json).
    seasons = _load_seasonal(src)
    if seasons:
        _dump(OUT / "seasons.json", seasons)
        print(f"OK seasons.json: {len(seasons):,} hexes x up to {len(SEASON_KEYS)} month-windows")
    else:
        print("·· seasons.json: no seasonal_long parquet — skipped (rerun pipeline to emit)")

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
    # DERIVED from the silver od_silver fecha partitions (NOT hardcoded) so the
    # published window can never drift from the data actually shipped. Currently
    # the full-scale 2025 window: 89 days, 2025-02 + 2025-05 + 2025-06,
    # fecha 20250201..20250630, distritos zoning.
    manifest["mobility_window"] = _derive_window("distritos")
    manifest["mobility_method"] = "dasymetric zone->H3 crosswalk (Sedona ST_Intersection, EPSG:25831)"

    # --- SEASONAL block: month-window comparison contract (honest labelling) ----
    # The 3 windows are NOT climate seasons — they are three calendar month-windows.
    # The human labels (winter·Feb / spring·May / summer-onset·Jun) live HERE +
    # in the UI, never in the data (the data uses the unambiguous feb/may/jun key).
    if seasons:
        manifest["seasonal"] = {
            "windows": SEASON_WINDOWS,
            "pooled_label": "Pooled (all 89 days)",
            "metrics": [
                "weekend_weekday_ratio", "weekend_hotspot_score",
                "leisure_share", "commute_share",
                "am_peak_share", "pm_peak_share", "midday_share", "night_share",
                "peak_hour_bucket",
            ],
            "short_keys": {m: s for m, s, _ in SEASON_METRICS},
            "delta": {
                "column": "weekend_hotspot_summer_minus_winter",
                "label": "Weekend pull: summer-onset − winter (Jun−Feb)",
            },
            "ratio_delta": {
                "column": "weekend_ratio_summer_minus_winter",
                "label": "Weekend ÷ weekday ratio: summer-onset − winter (Jun−Feb)",
            },
            "sidecar": "seasons.json",
            "note": "three calendar month-windows, NOT a climate/seasonal average",
        }
        # Delta describe() for the headline diverging legend domain.
        for dc in ("weekend_hotspot_summer_minus_winter", "weekend_ratio_summer_minus_winter"):
            if dc in merged.columns:
                d = merged[dc].astype(float).describe()
                mstats[dc] = {k: round(float(d[k]), 3) for k in ("min", "50%", "max", "mean")}
        manifest["mobility_stats"] = mstats

    manifest["version"] = "v3-mobility"

    manifest_path.write_text(
        json.dumps(_scrub(manifest), indent=2, allow_nan=False), encoding="utf-8")
    print(f"OK manifest.json: typology_present={present}")
    print("   typology_counts:", manifest["typology_counts"])
    print("   mobility_stats.weekend_hotspot_score:", mstats.get("weekend_hotspot_score"))


if __name__ == "__main__":
    main()
