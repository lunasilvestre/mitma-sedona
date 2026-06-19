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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zoning", default="distritos")
    ap.add_argument("--sample-glob", default=str(DATA / "bronze/mitma/daily/2024-03/*_Viajes_distritos.csv.gz"))
    ap.add_argument("--k", type=int, default=5, help="typology clusters")
    ap.add_argument("--min-support", type=int, default=50)
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
    ap.add_argument("--no-rtree", action="store_true",
                    help="use the non-indexed RangeJoin (default: indexed R-tree BroadcastIndexJoin)")
    args = ap.parse_args()

    gold_dir = GOLD_ROOT / f"zoning={args.zoning}"
    gold_dir.mkdir(parents=True, exist_ok=True)

    # Default to the proven indexed R-tree spatial join (review item 5); the
    # crosswalk is the one heavy spatial join and benefits from the index at
    # scale. --no-rtree falls back to the safe non-indexed RangeJoin.
    spark = get_sedona(app_name=f"mitma-pipeline-{args.zoning}",
                       driver_memory=args.driver_memory,
                       enable_rtree=not args.no_rtree)
    print(f"[spark] sedona.join.optimizationmode={spark.conf.get('sedona.join.optimizationmode')} "
          f"(R-tree {'ON' if not args.no_rtree else 'off'})")
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

    print("[gold] theme 3: geodemographic")
    geodemo = G.geodemographic(od_silver, xwalk)

    print("[gold] theme 4: typology (MLlib BisectingKMeans)")
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
    feats_pd = feats.toPandas()
    out_parquet = gold_dir / "h3_mitma_features.parquet"
    feats_pd.to_parquet(out_parquet, index=False)
    print(f"[gold] -> {out_parquet}  ({len(feats_pd):,} hexes, {feats_pd.shape[1]} cols)")
    print(f"[gold] columns: {list(feats_pd.columns)}")

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
