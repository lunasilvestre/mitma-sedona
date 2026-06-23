#!/usr/bin/env python
"""Export the OD-flows precompute for the FLOWMAP A/B variant -> docs/story_data/flows_fm/.

A SECOND FORK of scripts/export_od_flows.py. Where the v1 export emits flat,
GPU-ready arc geometry (.bin: interleaved endpoint positions + flow + rank for
deck.gl ArcLayer), Flowmap needs RELATIONAL data: a shared locations table keyed
by zone id, plus per-slice flows tables that reference those ids. This script
reuses the EXACT SAME upstream machinery as v1 — the silver partition bucketing,
the per-(origen,destino) viajes aggregation with the 24-slot hourly pivot, the
windowed-sum / day_count divisor, the top-N=8000 policy, and the distrito
centroid anchoring — and only changes the OUTPUT SHAPE (JSON, not .bin).

It writes ONLY into docs/story_data/flows_fm/ (a NEW sibling subtree) and must
NOT touch hexes.json, the v1 docs/story_data/flows/*.bin, manifest.json,
arcs.json, rhythm.json, seasons.json, or any score.

Outputs:
  1. locations.json   — every distrito zone REFERENCED by any slice, as
     [{id, name?, lon, lat}] anchored at the ZONES_GEOJSON centroid (the same
     centroid map v1 uses to anchor arc endpoints). name is omitted because the
     distritos geojson carries only ID + geometry (no label column).
  2. <window>_<daytype>.json — for each of the 6 (window x daytype) slices, a
     flows table [{origin, dest, count, hourly:[24]}] where count = trips/day
     (windowed viajes sum / this bucket's day_count, the SAME divisor as v1) and
     hourly[h] = that hour's trips/day, so Sum(hourly) ~= count and the page can
     scrub hour h by rebuilding count = hourly[h]. Top-N=8000 by count with the
     SAME deterministic (flow desc, origen asc, destino asc) tie-break as v1.
  3. flows_fm_index.json — slice list with per-slice counts and the global flow
     min/max (daily) and global hourly min/max.

Run with the sedona env python (needs pandas + geopandas)::
    /home/nls/miniforge3/envs/sedona/bin/python scripts/export_od_flows_fm.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

# Reuse v1's machinery verbatim so the two variants share an identical upstream
# pipeline (bucketing, aggregation, divisor, top-N, centroids). Only the emit
# differs. Importing keeps the A and B inputs provably the same code path.
import export_od_flows as v1  # noqa: E402  (sibling module, same scripts/ dir)

REPO = Path(__file__).resolve().parents[1]

# Output: a NEW sibling subtree, isolated from hexes.json AND from v1's flows/.
OUT = REPO / "docs" / "story_data" / "flows_fm"

# SAME top-N policy and cap as v1 (N=8000). Flowmap.gl clusters/declutters on the
# client, so the v1 cap is also flowmap-friendly: a larger relational set than 8k
# would only be culled at render time. Kept identical so the A/B compares the
# same underlying corridor set, not a different sampling.
N_TOP = v1.N_TOP
HOURS = v1.HOURS
WINDOWS = v1.WINDOWS
DAYTYPES = v1.DAYTYPES
WINDOW_LABELS = v1.WINDOW_LABELS

# Rounding conventions mirror v1: positions round(_, 6), flow/hourly round(_, 2).
POS_DP = 6
FLOW_DP = 2


def _build_slice_fm(
    window: str,
    daytype: str,
    fechas: list[str],
    cmap: dict[str, tuple[float, float]],
    zoning: str = "distritos",
) -> tuple[dict, list[dict], set[str]]:
    """Aggregate one (window, daytype) bucket -> trips/day -> top-N, then emit a
    RELATIONAL flows table (no geometry, just zone-id references + hourly).

    Returns (index_entry, flows_rows, referenced_zone_ids). Mirrors v1's
    _build_slice exactly up to the top-N step; the only divergence is the output
    (zone-id rows vs anchored .bin geometry). A pair is kept only if BOTH its
    endpoints resolve in the centroid map (inner-join parity with v1).
    """
    part_root = v1.SILVER_OD / f"zoning={zoning}"
    hcols = [f"h{h}" for h in range(HOURS)]
    day_count = len(fechas)

    # Identical aggregation to v1: per-(origen,destino) viajes sum + h0..h23 pivot.
    agg = v1._aggregate_bucket(part_root, fechas)
    # Identical per-day normalization: windowed SUM -> trips/day (and per hour).
    agg["flow"] = agg["viajes"] / day_count
    for c in hcols:
        agg[c] = agg[c] / day_count

    # Identical deterministic top-N: flow desc, then (origen, destino) asc.
    agg = (
        agg.sort_values(by=["flow", "origen", "destino"], ascending=[False, True, True])
        .head(N_TOP)
        .reset_index(drop=True)
    )

    # Inner-join parity: keep a pair only when both endpoints have a centroid.
    o_ids = agg["origen"].astype(str)
    d_ids = agg["destino"].astype(str)
    keep = np.array(
        [(o in cmap and d in cmap) for o, d in zip(o_ids, d_ids)], dtype=bool
    )
    agg = agg[keep].reset_index(drop=True)
    o_ids = o_ids[keep].reset_index(drop=True)
    d_ids = d_ids[keep].reset_index(drop=True)
    n = len(agg)

    flow = np.round(agg["flow"].to_numpy(dtype=np.float64), FLOW_DP)
    hours = np.round(agg[hcols].to_numpy(dtype=np.float64), FLOW_DP)

    referenced: set[str] = set()
    rows: list[dict] = []
    for i in range(n):
        o = o_ids.iat[i]
        d = d_ids.iat[i]
        referenced.add(o)
        referenced.add(d)
        rows.append(
            {
                "origin": o,
                "dest": d,
                "count": float(flow[i]),
                "hourly": [float(x) for x in hours[i]],
            }
        )

    key = f"{window}_{daytype}"
    out_path = OUT / f"{key}.json"
    out_path.write_text(
        json.dumps(rows, separators=(",", ":"), allow_nan=False), encoding="utf-8"
    )
    out_bytes = out_path.stat().st_size

    flow_min = float(flow.min()) if n else 0.0
    flow_max = float(flow.max()) if n else 0.0
    hour_min = float(hours.min()) if n else 0.0
    hour_max = float(hours.max()) if n else 0.0

    print(
        f"OK {key:14s} n={n:5d} day_count={day_count:2d} "
        f"count(trips/day) {flow_min:,.2f}..{flow_max:,.2f}  "
        f"hourly {hour_min:,.2f}..{hour_max:,.2f}  json={out_bytes}B"
    )

    entry = {
        "key": key,
        "window": window,
        "daytype": daytype,
        "is_weekend": daytype == "weekend",
        "n": n,
        "day_count": day_count,
        "flow_min": round(flow_min, FLOW_DP),
        "flow_max": round(flow_max, FLOW_DP),
        "hour_flow_min": round(hour_min, FLOW_DP),
        "hour_flow_max": round(hour_max, FLOW_DP),
        "flows_url": f"{key}.json",
        "flows_bytes": out_bytes,
        "includes_am_zones": True,  # _AM super-zones kept, same as v1
    }
    return entry, rows, referenced


def main() -> None:
    if not v1._silver_fechas("distritos"):
        raise SystemExit(f"no silver od_silver partitions on disk under {v1.SILVER_OD}")
    if not v1.ZONES_GEOJSON.exists():
        raise SystemExit(f"zones geojson absent: {v1.ZONES_GEOJSON}")

    OUT.mkdir(parents=True, exist_ok=True)
    buckets = v1._bucket_partitions("distritos")
    cmap = v1._load_centroids("distritos")
    print(f"centroid map: {len(cmap):,} zones; buckets: {len(buckets)}")

    slices: list[dict] = []
    all_referenced: set[str] = set()
    # Same deterministic slice order as v1 (feb_weekday, feb_weekend, ...).
    for window in WINDOWS:
        for daytype in DAYTYPES:
            fechas = sorted(buckets.get((window, daytype), []))
            if not fechas:
                print(f"·· {window}_{daytype}: no partitions on disk — skipped")
                continue
            entry, _rows, referenced = _build_slice_fm(
                window, daytype, fechas, cmap, "distritos"
            )
            slices.append(entry)
            all_referenced |= referenced

    # Shared locations table: ONLY the zones referenced by at least one slice,
    # anchored at the centroid map v1 uses. Sorted by id for a byte-stable file.
    # The distritos geojson has no name/label column (ID + geometry only), so
    # `name` is omitted rather than emitted empty.
    locations: list[dict] = []
    for zid in sorted(all_referenced):
        lon, lat = cmap[zid]
        locations.append(
            {"id": zid, "lon": round(float(lon), POS_DP), "lat": round(float(lat), POS_DP)}
        )
    loc_path = OUT / "locations.json"
    loc_path.write_text(
        json.dumps(locations, separators=(",", ":"), allow_nan=False), encoding="utf-8"
    )
    print(f"OK locations.json: {len(locations)} zones  {loc_path.stat().st_size}B")

    # Global domains across ALL slices (daily + hourly), so the page can hold a
    # single comparable width/color scale across slices and hours.
    g_flow_min = min((s["flow_min"] for s in slices), default=0.0)
    g_flow_max = max((s["flow_max"] for s in slices), default=0.0)
    g_hour_min = min((s["hour_flow_min"] for s in slices), default=0.0)
    g_hour_max = max((s["hour_flow_max"] for s in slices), default=0.0)

    index = {
        "version": 1,
        "variant": "flowmap",
        "unit": "trips_per_day",
        "n_per_slice": N_TOP,
        "hours": HOURS,
        "locations_url": "locations.json",
        "location_count": len(locations),
        "grid": {"windows": WINDOWS, "daytypes": DAYTYPES},
        "window_labels": WINDOW_LABELS,
        "global_flow_domain": [round(g_flow_min, FLOW_DP), round(g_flow_max, FLOW_DP)],
        "global_hour_flow_domain": [round(g_hour_min, FLOW_DP), round(g_hour_max, FLOW_DP)],
        "slices": slices,
        "notes": (
            "Flowmap (relational) A/B variant of the OD-flows applet. Same "
            "upstream pipeline as docs/story_data/flows (silver bucketing, "
            "per-(origen,destino) viajes aggregation with a 24h pivot, "
            "windowed-sum / day_count divisor, top-N=8000 deterministic "
            "tie-break, distrito-centroid anchoring) — only the output shape "
            "differs. locations.json = [{id,lon,lat}] for every referenced "
            "distrito (no name column in the source geojson). Each "
            "<window>_<daytype>.json = [{origin,dest,count,hourly[24]}] where "
            "count is trips/day and hourly[h] is that hour's trips/day "
            "(Sum(hourly)~=count); scrub hour h by setting count=hourly[h]. "
            "month-window comparison, NOT a climate average; 3 months on disk "
            "(Feb/May/Jun)."
        ),
    }
    (OUT / "flows_fm_index.json").write_text(
        json.dumps(index, indent=2, allow_nan=False), encoding="utf-8"
    )
    flows_total = sum(s["flows_bytes"] for s in slices)
    print(
        f"OK flows_fm_index.json: {len(slices)} slices  "
        f"global_flow_domain={index['global_flow_domain']}  "
        f"flows_total={flows_total}B"
    )


if __name__ == "__main__":
    main()
