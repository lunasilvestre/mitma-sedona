#!/usr/bin/env python
"""End-to-end MITMA deep-Spark pipeline runner (bronze -> silver -> gold).

Runs the real PySpark + Apache Sedona lakehouse on the EXISTING 7-day
March-2024 distritos sample (no bulk download). Produces:

  data/bronze/mitma_lakehouse/zoning=distritos/kind=viajes/fecha=*/   (parquet)
  data/silver/zone_h3_xwalk/zoning=distritos/                          (parquet)
  data/silver/od_silver/zoning=distritos/                              (parquet)
  data/gold/mitma_features/zoning=distritos/h3_mitma_features.parquet  (per-hex)
  data/gold/mitma_features/zoning=distritos/typology_centroids.json
  data/gold/mitma_features/zoning=distritos/rhythm_long.parquet
  data/gold/mitma_features/zoning=distritos/arcs.json / arcs_weekend.json

Run with the sedona env python::

    /home/nls/miniforge3/envs/sedona/bin/python scripts/run_mitma_pipeline.py \
        --zoning distritos --sample

``--regression`` additionally diffs the recomputed dasymetric
mitma_inflow/outflow/through_ratio against the shipped
data/gold/h3_res8_catalonia_v2.parquet and prints the magnitude of the
correctness shift.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from pyspark.sql import functions as F  # noqa: E402

from catmob import pipeline_bronze as B  # noqa: E402
from catmob import pipeline_gold as G  # noqa: E402
from catmob import pipeline_silver as S  # noqa: E402
from catmob.spark import get_sedona  # noqa: E402

DATA = REPO / "data"
ZONES_GEOJSON = str(DATA / "bronze/mitma/zones/zonificacion_distritos.geojson")
BRONZE_ROOT = str(DATA / "bronze/mitma_lakehouse")
SILVER_XWALK = str(DATA / "silver/zone_h3_xwalk")
SILVER_OD = str(DATA / "silver/od_silver")
GOLD_ROOT = DATA / "gold/mitma_features"


# Per-season metric columns carried into the wide sidecar, in a stable order.
# weekend_* come from weekend_hotspots_seasonal; the *_share rhythm cols +
# peak_hour_bucket from hourly_rhythm_seasonal. (support_n is per-season too but
# is a confidence proxy, not a display metric — dropped from the wide sidecar.)
SEASONAL_WEEKEND_COLS = ["weekend_weekday_ratio", "leisure_share", "commute_share",
                         "weekend_hotspot_score"]
SEASONAL_RHYTHM_COLS = ["am_peak_share", "pm_peak_share", "midday_share", "night_share",
                        "peak_hour_bucket"]
SEASON_KEYS = ("feb", "may", "jun")


def _pivot_seasonal_wide(weekend_s, rhythm_s):
    """Pivot per-(h3, season) seasonal frames to ONE wide per-hex frame.

    Each metric becomes ``<metric>_<season>`` (e.g. weekend_hotspot_score_jun).
    Returns the wide Spark DataFrame keyed on h3_id. NULL-safe by construction:
    a (hex, season) absent from a frame yields NULL for that suffixed column
    (e.g. a hex that failed the per-season support gate -> null hotspot, never 0).
    """
    from pyspark.sql import functions as _F

    metric_cols = SEASONAL_WEEKEND_COLS + SEASONAL_RHYTHM_COLS

    def _wide(df, cols):
        # pivot season -> columns, agg first() of each metric. pivot on a fixed
        # value list so feb/may/jun columns always exist (a fully-empty window
        # still yields an all-null column, not a missing one).
        p = df.groupBy("h3_id").pivot("season", list(SEASON_KEYS)).agg(
            *[_F.first(c).alias(c) for c in cols]
        )
        # Spark names pivoted cols "<season>_<metric>"; rename to "<metric>_<season>".
        ren = p
        for s in SEASON_KEYS:
            for c in cols:
                src = f"{s}_{c}"
                if src in ren.columns:
                    ren = ren.withColumnRenamed(src, f"{c}_{s}")
        return ren

    wk_wide = _wide(weekend_s, SEASONAL_WEEKEND_COLS)
    rh_wide = _wide(rhythm_s, SEASONAL_RHYTHM_COLS)
    wide = wk_wide.join(rh_wide, "h3_id", "outer")

    # INLINE headline delta(s): summer-onset (Jun) minus winter (Feb). NULL-safe —
    # null wherever either window failed the support gate (honesty over a fake 0).
    wide = wide.withColumn(
        "weekend_hotspot_summer_minus_winter",
        _F.col("weekend_hotspot_score_jun") - _F.col("weekend_hotspot_score_feb"),
    ).withColumn(
        "weekend_ratio_summer_minus_winter",
        _F.col("weekend_weekday_ratio_jun") - _F.col("weekend_weekday_ratio_feb"),
    )
    return wide, metric_cols


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zoning", default="distritos")
    ap.add_argument("--sample-glob", default=str(DATA / "bronze/mitma/daily/2024-03/*_Viajes_distritos.csv.gz"))
    ap.add_argument("--k", type=int, default=5, help="typology clusters")
    ap.add_argument("--min-support", type=int, default=50)
    ap.add_argument("--min-support-seasonal", type=int, default=50,
                    help="per-season support gate for weekend_hotspot_{feb,may,jun} "
                         "(each window is ~1/3 the days; lower to ~min_support//3 only if "
                         "a window is too sparse to read)")
    ap.add_argument("--regression", action="store_true")
    ap.add_argument("--driver-memory", default="8g")
    # SCALE-FILTER (review item 7): read the bronze lakehouse partition-pruned to
    # a Catalonia-touching [fecha_start, fecha_end] window instead of re-ingesting
    # the CSV glob. On atlas this keeps the working set GB-scale, not the
    # all-Spain TB dump. --from-bronze skips CSV ingest and reads the parquet
    # lakehouse with fecha partition pruning.
    ap.add_argument("--from-bronze", action="store_true",
                    help="read the partitioned bronze lakehouse (pruned) instead of re-ingesting CSV")
    ap.add_argument("--fecha-start", default=None, help="YYYYMMDD window start (inclusive)")
    ap.add_argument("--fecha-end", default=None, help="YYYYMMDD window end (inclusive)")
    ap.add_argument("--rtree", action="store_true",
                    help="enable the indexed R-tree BroadcastIndexJoin (parks pyspark's "
                         "jts-core). NOTE: incompatible with the bronze PARQUET read in the "
                         "same JVM on this Spark/Sedona pair — the jts isolation breaks the "
                         "parquet codegen (PrimitiveStringifier). The R-tree path is proven "
                         "in isolation (see tests/notes); the integrated pipeline defaults to "
                         "the correct non-indexed RangeJoin so it can read the lakehouse.")
    args = ap.parse_args()

    gold_dir = GOLD_ROOT / f"zoning={args.zoning}"
    gold_dir.mkdir(parents=True, exist_ok=True)

    # Default to the safe non-indexed RangeJoin: it reads the bronze parquet
    # lakehouse correctly AND pushes ST_Intersects down (correct + fast on the
    # 584-zone broadcast side). The indexed R-tree BroadcastIndexJoin is PROVEN
    # to execute on the crosswalk in isolation (--rtree), but parking pyspark's
    # duplicate jts-core to enable it breaks the parquet read codegen in the same
    # JVM on this Spark-4.1.1/sedona-shaded-4.0 pair — so the integrated pipeline
    # (which must scan parquet) stays on RangeJoin. See review item 5 notes.
    spark = get_sedona(app_name=f"mitma-pipeline-{args.zoning}",
                       driver_memory=args.driver_memory,
                       enable_rtree=args.rtree)
    print(f"[spark] sedona.join.optimizationmode={spark.conf.get('sedona.join.optimizationmode')} "
          f"(R-tree {'ON' if args.rtree else 'off — RangeJoin, parquet-safe'})")
    spark.sparkContext.setLogLevel("ERROR")

    # ---- BRONZE ----------------------------------------------------------
    if args.from_bronze:
        print(f"[bronze] reading lakehouse {BRONZE_ROOT} "
              f"(window={args.fecha_start}..{args.fecha_end}, Catalonia-touching, partition-pruned)")
        bronze = B.read_bronze(
            spark, BRONZE_ROOT, zoning=args.zoning, kind="viajes",
            fecha_start=args.fecha_start, fecha_end=args.fecha_end,
        )
    else:
        print(f"[bronze] ingesting {args.sample_glob}")
        bronze = B.ingest(spark, args.sample_glob, BRONZE_ROOT, zoning=args.zoning, kind="viajes")
        if args.fecha_start or args.fecha_end:
            if args.fecha_start:
                bronze = bronze.where(F.col("fecha") >= args.fecha_start)
            if args.fecha_end:
                bronze = bronze.where(F.col("fecha") <= args.fecha_end)
    n_bronze = bronze.count()
    fechas = sorted(r["fecha"] for r in bronze.select("fecha").distinct().collect())
    per_day = bronze.groupBy("fecha").count().orderBy("fecha").collect()
    print(f"[bronze] {n_bronze:,} Catalonia OD rows across {len(fechas)} days: {fechas}")
    for r in per_day:
        print(f"         fecha={r['fecha']}  rows={r['count']:,}")

    # SEASONAL preflight: count distinct days per month-window so a partial
    # download fails LOUD here rather than shipping a silent all-null season
    # column / delta. Only enforce a month's full count if that month is present
    # at all (a slice run with e.g. --fecha-end mid-Feb is allowed; an EMPTY
    # season would otherwise make its pivot column — and the jun-feb delta — null).
    season_days = {"feb": [], "may": [], "jun": []}
    for f in fechas:
        key = {"02": "feb", "05": "may", "06": "jun"}.get(f[4:6])
        if key:
            season_days[key].append(f)
    EXPECTED_DAYS = {"feb": 28, "may": 31, "jun": 30}
    print(f"[bronze] season day-counts: "
          + ", ".join(f"{k}={len(v)}" for k, v in season_days.items()))
    is_full = (args.fecha_start in (None, "20250201") and args.fecha_end in (None, "20250630"))
    if is_full:
        for k, exp in EXPECTED_DAYS.items():
            got = len(season_days[k])
            if got != exp:
                raise SystemExit(
                    f"[bronze] SEASONAL PREFLIGHT FAIL: {k} window has {got} days, expected {exp}. "
                    f"Partial download would ship a silent all-null season/delta — aborting.")

    # ---- SILVER S1: crosswalk -------------------------------------------
    print("[silver] building zone_h3_xwalk (dasymetric, EPSG:25831 area math)")
    xwalk = S.build_zone_h3_xwalk(spark, ZONES_GEOJSON, zoning=args.zoning)
    xwalk = xwalk.persist()
    n_xwalk_rows = xwalk.count()
    n_hexes = xwalk.select("h3_id").distinct().count()
    n_zones = xwalk.select("zone_id").distinct().count()
    print(f"[silver] xwalk rows={n_xwalk_rows:,}  hexes={n_hexes:,}  zones={n_zones:,}")
    # area_weight sum-per-zone sanity (should be ~1.0 for interior zones).
    zsum = xwalk.groupBy("zone_id").agg(F.sum("area_weight").alias("w")).select(
        F.mean("w").alias("mean"), F.expr("percentile_approx(w,0.5)").alias("median"),
        F.min("w").alias("min"), F.max("w").alias("max")
    ).collect()[0]
    print(f"[silver] area_weight sum/zone  mean={zsum['mean']:.4f} median={zsum['median']:.4f} "
          f"min={zsum['min']:.4f} max={zsum['max']:.4f}  (expect ~1.0)")
    (xwalk.write.mode("overwrite").partitionBy("zoning")
        .parquet(SILVER_XWALK))

    # ---- SILVER S2: od_silver -------------------------------------------
    print("[silver] building od_silver (weekday/is_weekend/support_n, NA preserved)")
    od_silver = S.build_od_silver(bronze).persist()
    od_daily = S.daily_rollup(od_silver).persist()
    n_daily = od_daily.count()
    wk = od_silver.groupBy("is_weekend").agg(F.countDistinct("fecha").alias("days")).collect()
    print(f"[silver] od_daily rows={n_daily:,}  day-split={[ (r['is_weekend'], r['days']) for r in wk]}")
    (od_silver.write.mode("overwrite").partitionBy("fecha")
        .parquet(SILVER_OD + f"/zoning={args.zoning}"))

    # ---- GOLD themes -----------------------------------------------------
    print("[gold] theme 0: dasymetric inflow/outflow/through_ratio")
    flow = G.dasymetric_inflow_outflow(od_daily, xwalk)

    print("[gold] theme 1: hourly rhythm")
    rhythm_scalars, rhythm_long = G.hourly_rhythm(od_silver, xwalk)

    print("[gold] theme 2: weekend hotspots")
    weekend = G.weekend_hotspots(od_silver, xwalk, min_support=args.min_support)

    # SEASONAL sidecar (display-only; pooled metrics + default score untouched).
    # Three calendar month-windows (feb/may/jun), NOT a climate average. The
    # pooled calls above are unchanged; these are ADDITIONAL per-(h3,season)
    # frames. seasonal min_support defaults to the pooled value but can be
    # lowered (args.min_support_seasonal) since each window is ~1/3 the days.
    print("[gold] theme 1s+2s: seasonal rhythm + weekend (feb/may/jun month-windows)")
    weekend_s = G.weekend_hotspots_seasonal(od_silver, xwalk, min_support=args.min_support_seasonal)
    rhythm_s = G.hourly_rhythm_seasonal(od_silver, xwalk)

    print("[gold] theme 3: geodemographic")
    geodemo = G.geodemographic(od_silver, xwalk)

    print("[gold] theme 4: typology (MLlib KMeans)")
    indices = G.typology_indices(od_silver, xwalk).persist()
    typ, centroids = G.mobility_typology(spark, indices, k=args.k)

    print("[gold] theme 5: OD arcs (ST_MakeLine)")
    arcs_all = G.od_arcs(spark, od_daily, ZONES_GEOJSON, top_n=250, weekend_only=None)
    arcs_we = G.od_arcs(spark, od_daily, ZONES_GEOJSON, top_n=250, weekend_only=True)

    # hex centroids for the gold table.
    cents = S.hex_centroids(spark, xwalk)

    # ---- merge all per-hex features -------------------------------------
    print("[gold] merging per-hex feature table")
    feats = (
        cents
        .join(flow, "h3_id", "left")
        .join(rhythm_scalars, "h3_id", "left")
        .join(weekend, "h3_id", "left")
        .join(geodemo, "h3_id", "left")
        .join(typ.select(
            "h3_id", "mobility_typology", "cluster_id",
            "intra_zone_share", "long_trip_share", "sink_source",
        ), "h3_id", "left")
    )
    # SEASONAL pivot: one wide per-hex frame (<metric>_<feb|may|jun>) + the inline
    # jun-feb delta(s). The delta(s) ride into feats_pd -> h3_mitma_features.parquet
    # (and on into hexes.json INLINE, since the delta is the story). The ~27
    # suffixed per-season cols go to a SEPARATE seasonal_long.parquet so they
    # don't bloat hexes.json (export ships them as the lazy seasons.json sidecar).
    seasonal_wide, seasonal_metric_cols = _pivot_seasonal_wide(weekend_s, rhythm_s)
    DELTA_COLS = ["weekend_hotspot_summer_minus_winter", "weekend_ratio_summer_minus_winter"]
    delta_only = seasonal_wide.select("h3_id", *DELTA_COLS)
    feats = feats.join(delta_only, "h3_id", "left")

    feats_pd = feats.toPandas()
    out_parquet = gold_dir / "h3_mitma_features.parquet"
    feats_pd.to_parquet(out_parquet, index=False)
    print(f"[gold] -> {out_parquet}  ({len(feats_pd):,} hexes, {feats_pd.shape[1]} cols)")
    print(f"[gold] columns: {list(feats_pd.columns)}")
    for dc in DELTA_COLS:
        nn = feats_pd[dc].notna().sum()
        if nn:
            print(f"[gold] {dc}: {nn:,} non-null  "
                  f"min={feats_pd[dc].min():.3f} median={feats_pd[dc].median():.3f} "
                  f"max={feats_pd[dc].max():.3f}")

    # seasonal_long sibling: the wide per-(h3 x season) suffixed cols (NOT the
    # delta, which lives inline) -> parquet. export_mitma_layers.py reads this and
    # emits the lazy seasons.json sidecar (nested, short keys).
    seasonal_pd = seasonal_wide.drop(*DELTA_COLS).toPandas()
    seasonal_parquet = gold_dir / "seasonal_long.parquet"
    seasonal_pd.to_parquet(seasonal_parquet, index=False)
    print(f"[gold] -> {seasonal_parquet}  ({len(seasonal_pd):,} hexes, "
          f"{seasonal_pd.shape[1] - 1} per-season cols)")

    # rhythm long-form sibling (h3_id, periodo, share) -> parquet for export.
    rhythm_long.write.mode("overwrite").parquet(str(gold_dir / "rhythm_long.parquet"))

    # typology centroids + arcs json
    (gold_dir / "typology_centroids.json").write_text(json.dumps(centroids, indent=2))
    (gold_dir / "arcs.json").write_text(json.dumps(arcs_all))
    (gold_dir / "arcs_weekend.json").write_text(json.dumps(arcs_we))

    # typology distribution sanity
    typ_dist = (
        feats_pd["mobility_typology"].value_counts(dropna=False).to_dict()
    )
    print(f"[gold] typology distribution: {typ_dist}")
    print(f"[gold] typology centroids: {[c['label'] for c in centroids]}")

    # ---- REGRESSION DIFF -------------------------------------------------
    if args.regression:
        _regression_diff(feats_pd)

    spark.stop()
    print("PIPELINE OK")


def _regression_diff(feats_pd) -> None:
    import numpy as np
    import pandas as pd

    legacy_path = DATA / "gold/h3_res8_catalonia_v2.parquet"
    if not legacy_path.exists():
        print("[regression] legacy gold not found; skipping")
        return
    legacy = pd.read_parquet(legacy_path)[
        ["h3_id", "mitma_inflow_daily", "mitma_outflow_daily", "mitma_through_ratio"]
    ].rename(columns={
        "mitma_inflow_daily": "legacy_inflow",
        "mitma_outflow_daily": "legacy_outflow",
        "mitma_through_ratio": "legacy_ratio",
    })
    new = feats_pd[["h3_id", "mitma_inflow_daily", "mitma_outflow_daily", "mitma_through_ratio"]]
    m = legacy.merge(new, on="h3_id", how="inner")
    print(f"[regression] joined {len(m):,} hexes (legacy {len(legacy):,})")
    for col, leg in [("mitma_inflow_daily", "legacy_inflow"),
                     ("mitma_outflow_daily", "legacy_outflow"),
                     ("mitma_through_ratio", "legacy_ratio")]:
        a, b = m[leg].astype(float), m[col].astype(float)
        ok = a.notna() & b.notna()
        a, b = a[ok], b[ok]
        rel = np.abs(b - a) / np.where(np.abs(a) > 1e-9, np.abs(a), np.nan)
        corr = np.corrcoef(a, b)[0, 1] if len(a) > 2 else float("nan")
        print(f"[regression] {col}: legacy[median={a.median():.3f}] new[median={b.median():.3f}] "
              f"corr={corr:.3f} median|rel_diff|={np.nanmedian(rel):.2%}")
    # The through-ratio is the headline correctness fix: legacy was ~1.0 flat.
    new_ratio = m["mitma_through_ratio"].dropna()
    print(f"[regression] NEW through_ratio spread: min={new_ratio.min():.3f} "
          f"p25={new_ratio.quantile(.25):.3f} median={new_ratio.median():.3f} "
          f"p75={new_ratio.quantile(.75):.3f} max={new_ratio.max():.3f}  "
          f"(legacy was ~1.0 flat — naive centroid join over-attribution)")


if __name__ == "__main__":
    main()
