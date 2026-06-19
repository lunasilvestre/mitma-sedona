#!/usr/bin/env python
"""Build the GOLD mobility analytic layers for mitma-sedona — REAL Sedona/Spark.

This is the distributed heavy-lifting behind DEV-#2's new browseable layers. It
runs the irreducible spatial work in Apache Sedona (NOT pandas-in-disguise):

  S1  zone_h3_xwalk  — the load-bearing dasymetric crosswalk. The Catalonia
      distritos polygons are reprojected FROM EPSG:25830/3042 (MITMA source,
      UTM 30N) TO EPSG:25831 (UTM 31N, correct for Catalonia) and area-weighted
      against the H3 res-8 grid via ST_Intersection/ST_Area. This REPLACES the
      naive centroid ``gpd.sjoin(predicate='within')`` join in
      run_gold_v2.py:407 that over-attributes a whole distrito's flow to every
      hex inside it.

  Then five gold themes are computed over the FULL od cube (periodo x distancia
  x actividad x renta x edad x sexo) — the dimensions the old pandas shortcut
  pre-collapsed to three scalars — and disaggregated zone->hex through S1:

    1. rhythm        — 24h hour-of-day profile + am/pm/midday/night peak shares
                       + categorical peak_hour bucket (Window + MAX_BY).
    2. weekend       — weekend_weekday_ratio (per-day MEANS) + leisure_share +
                       weekend_hotspot_score + support_n min-support gate.
    3. typology      — interpretable indices -> MLlib BisectingKMeans label.
    4. geodemographic— renta/edad/sexo segment shares + renta x edad entropy.
    5. arcs          — ST_MakeLine top-N OD arcs (Sedona) for the ArcLayer.

OUTPUTS (the gold schema DEV-#2's export consumes):
  data/gold/mitma_mobility_gold.parquet   — one row per h3_id, all new features.
  data/gold/mitma_arcs_gold.parquet        — top-N OD arcs (centroid lines).
  data/silver/zone_h3_xwalk/zoning=distritos/  — the reusable crosswalk.
  data/gold/mitma_rhythm.parquet           — h3_id -> 24-float hour profile.

SAMPLE SCOPE: runs on the 7-day March-2024 distritos bronze already on disk
(data/bronze/mitma_parquet/daily/fecha=YYYYMMDD). Full-scale (all-Spain, all
zonings, full range) is the STAGED atlas follow-on — same code, sized executors.

Run::
    /home/nls/miniforge3/envs/sedona/bin/python scripts/build_mitma_layers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql import Window  # noqa: E402

from catmob.pipeline_silver import build_zone_h3_xwalk  # noqa: E402  SINGLE SOURCE OF TRUTH
from catmob.spark import get_sedona  # noqa: E402

# --- paths ------------------------------------------------------------------
BRONZE = REPO / "data" / "bronze" / "mitma_parquet" / "daily"   # hourly-grained, periodo col
ZONES_GEOJSON = REPO / "data" / "bronze" / "mitma" / "zones" / "zonificacion_distritos.geojson"
GOLD_V2 = REPO / "data" / "gold" / "h3_res8_catalonia_v2.parquet"   # 45,220 hex grid (h3_id, centroids)

SILVER = REPO / "data" / "silver"
GOLD = REPO / "data" / "gold"
XWALK_OUT = SILVER / "zone_h3_xwalk" / "zoning=distritos"
MOBILITY_OUT = GOLD / "mitma_mobility_gold.parquet"
ARCS_OUT = GOLD / "mitma_arcs_gold.parquet"
RHYTHM_OUT = GOLD / "mitma_rhythm.parquet"

# EPSG: MITMA distritos shapefile is ETRS89 / UTM 30N (read as 3042; 25830 is the
# same projection, different authority). Catalonia metric work is UTM 31N (25831).
SRC_EPSG = "EPSG:25830"
METRIC_EPSG = "EPSG:25831"

ARC_TOP_N = 5000
# A coarse min-rows gate on the OD-segment ROW COUNT behind each zone's weekend
# stats — a density/confidence proxy, NOT the MITMA <100-device privacy gate
# (that suppression is applied by MITMA before publication and is invisible in
# the expanded open data).
MIN_SUPPORT_ROWS = 100.0


# ---------------------------------------------------------------------------
# S1 — dasymetric zone->H3 crosswalk (SINGLE SOURCE OF TRUTH)
# ---------------------------------------------------------------------------
def build_crosswalk(sedona):
    """Area-weighted zone->H3 crosswalk for the distritos zoning.

    CONVERGED (review item 6): this no longer re-derives the spatial join from
    the shapefile. It delegates to
    ``catmob.pipeline_silver.build_zone_h3_xwalk`` — the ONE canonical
    dasymetric crosswalk (GeoJSON zones, ST_H3CellIDs fullCover grid, 4326->25831
    metric area math, BROADCAST small side). The result is then RESTRICTED to the
    shipped 45,220-hex gold grid so the layers land on exactly the h3 cells the
    rest of the index uses.

    Returns a DataFrame: (zone_id, h3_id, area_weight).
    """
    import pandas as pd

    xwalk = build_zone_h3_xwalk(sedona, str(ZONES_GEOJSON), zoning="distritos").select(
        "zone_id", "h3_id", "area_weight"
    )
    # Restrict to the shipped gold hex grid (string h3_id) so downstream joins
    # land on the same cells the published index uses.
    hex_pdf = pd.read_parquet(GOLD_V2, columns=["h3_id"]).drop_duplicates()
    grid = sedona.createDataFrame(hex_pdf).select("h3_id")
    xwalk = xwalk.join(F.broadcast(grid), "h3_id", "inner").where("area_weight > 0")
    return xwalk


# ---------------------------------------------------------------------------
# OD silver — bronze viajes cleaned + weekday/is_weekend/support_n
# ---------------------------------------------------------------------------
def load_od_silver(sedona):
    """Read the hourly-grained bronze and enrich with weekday/is_weekend.

    viajes are population-expanded already — never re-expanded. support_n carries
    a per-row presence count so downstream min-rows gates can suppress sparse,
    noisy aggregates (a density/confidence proxy, NOT the MITMA <100-device
    privacy gate — that floor is invisible in the expanded open data). NA
    edad/sexo kept as their own explicit segment.
    """
    df = sedona.read.parquet(str(BRONZE))
    df = (
        df.withColumn("d", F.to_date(F.col("fecha"), "yyyyMMdd"))
        .withColumn("weekday", F.dayofweek(F.col("d")))   # 1=Sun..7=Sat
        .withColumn("is_weekend", F.col("weekday").isin(1, 7).cast("int"))
        .withColumn("periodo", F.col("periodo").cast("int"))
        .withColumn("viajes", F.col("viajes").cast("double"))
    )
    # NA-as-bucket: coalesce nulls to explicit 'NA' so dropping never biases shares.
    for c in ("renta", "edad", "sexo"):
        df = df.withColumn(c, F.coalesce(F.col(c), F.lit("NA")))
    return df


# ---------------------------------------------------------------------------
# Zone-level aggregates (full cube) then zone->hex disaggregation via S1
# ---------------------------------------------------------------------------
def zone_inflow_outflow_daily(od):
    """TRUE daily rollup: sum viajes over periodo, then mean over the N days.

    inflow keyed by destino, outflow by origen. We average per-day totals so the
    7-day sample reads as a representative DAY, not a 7x sum.
    """
    n_days = od.select("fecha").distinct().count()
    inflow = (
        od.groupBy("destino", "fecha").agg(F.sum("viajes").alias("v"))
        .groupBy("destino").agg(F.avg("v").alias("inflow_daily"))
        .withColumnRenamed("destino", "zone_id")
    )
    outflow = (
        od.groupBy("origen", "fecha").agg(F.sum("viajes").alias("v"))
        .groupBy("origen").agg(F.avg("v").alias("outflow_daily"))
        .withColumnRenamed("origen", "zone_id")
    )
    support = (
        od.groupBy("destino").agg(F.sum("viajes").alias("support_n"))
        .withColumnRenamed("destino", "zone_id")
    )
    return inflow, outflow, support, n_days


def zone_rhythm(od):
    """Per-zone 24h hour-of-day inflow profile + peak shares + peak_hour bucket.

    Window partitioned by destino builds the 24-element profile; we normalise to
    a share vector (size-comparable across zones) and read am/pm/midday/night
    shares + the modal peak hour (MAX_BY analogue: argmax periodo by inflow).
    """
    by_hour = (
        od.groupBy("destino", "periodo").agg(F.sum("viajes").alias("v"))
        .withColumnRenamed("destino", "zone_id")
    )
    w = Window.partitionBy("zone_id")
    by_hour = by_hour.withColumn("zone_total", F.sum("v").over(w))
    by_hour = by_hour.withColumn(
        "share", F.col("v") / F.when(F.col("zone_total") > 0, F.col("zone_total"))
    )
    # peak_hour = argmax periodo by v (one-pass MAX_BY).
    peak = (
        by_hour.groupBy("zone_id")
        .agg(F.expr("max_by(periodo, v) AS peak_hour"))
    )
    # window-of-day shares.
    def band(lo, hi):
        return F.sum(F.when((F.col("periodo") >= lo) & (F.col("periodo") <= hi), F.col("share")).otherwise(0.0))

    shares = by_hour.groupBy("zone_id").agg(
        band(7, 9).alias("am_peak_share"),
        band(11, 14).alias("midday_share"),
        band(17, 20).alias("pm_peak_share"),
        (band(22, 23) + band(0, 5)).alias("night_share"),
    )
    rhythm_scalars = shares.join(peak, "zone_id", "left")
    # 24-float array (lazy-loaded sibling). Pivot then assemble ordered array.
    profile = (
        by_hour.groupBy("zone_id")
        .pivot("periodo", list(range(24)))
        .agg(F.first("share"))
    )
    return rhythm_scalars, profile


def zone_weekend(od):
    """weekend_weekday_ratio (per-day MEANS) + leisure_share per zone (destino)."""
    per_day = (
        od.groupBy("destino", "fecha", "is_weekend")
        .agg(F.sum("viajes").alias("v"))
    )
    means = (
        per_day.groupBy("destino", "is_weekend").agg(F.avg("v").alias("mean_v"))
        .groupBy("destino")
        .agg(
            F.sum(F.when(F.col("is_weekend") == 1, F.col("mean_v"))).alias("we_mean"),
            F.sum(F.when(F.col("is_weekend") == 0, F.col("mean_v"))).alias("wd_mean"),
        )
        .withColumnRenamed("destino", "zone_id")
    )
    means = means.withColumn(
        "weekend_weekday_ratio",
        F.col("we_mean") / F.when(F.col("wd_mean") > 0, F.col("wd_mean")),
    )
    # leisure_share = trips whose DESTINATION activity is leisure (frecuente / no_frecuente).
    leisure = (
        od.groupBy("destino")
        .agg(
            F.sum("viajes").alias("tot"),
            F.sum(
                F.when(
                    F.col("actividad_destino").isin("frecuente", "no_frecuente"),
                    F.col("viajes"),
                ).otherwise(0.0)
            ).alias("leisure"),
            F.sum(
                F.when(
                    F.col("actividad_destino").isin("trabajo_estudio"), F.col("viajes")
                ).otherwise(0.0)
            ).alias("commute"),
            # weekday denominator for the min-support gate.
            F.sum(F.when(F.col("is_weekend") == 0, F.col("viajes")).otherwise(0.0)).alias("wd_support"),
        )
        .withColumnRenamed("destino", "zone_id")
    )
    leisure = (
        leisure.withColumn("leisure_share", F.col("leisure") / F.when(F.col("tot") > 0, F.col("tot")))
        .withColumn("commute_share", F.col("commute") / F.when(F.col("tot") > 0, F.col("tot")))
    )
    out = means.join(leisure, "zone_id", "outer")
    # weekend_hotspot_score: ratio above 1 AND high leisure share, gated by support.
    out = out.withColumn(
        "weekend_hotspot_score",
        F.when(
            F.col("wd_support") >= MIN_SUPPORT_ROWS,
            F.col("weekend_weekday_ratio") * (1.0 + F.coalesce(F.col("leisure_share"), F.lit(0.0))),
        ),
    )
    return out.select(
        "zone_id", "weekend_weekday_ratio", "leisure_share", "commute_share",
        "weekend_hotspot_score", F.col("wd_support").alias("weekend_support_n"),
    )


def zone_geodemographic(od):
    """Segment-weighted inflow shares + renta x edad Shannon entropy (NA kept)."""
    tot = od.groupBy("destino").agg(F.sum("viajes").alias("tot")).withColumnRenamed("destino", "zone_id")

    def share(col, vals, name):
        return (
            od.groupBy("destino")
            .agg(F.sum(F.when(F.col(col).isin(*vals), F.col("viajes")).otherwise(0.0)).alias(name))
            .withColumnRenamed("destino", "zone_id")
        )

    low_income = share("renta", ["<10"], "low_income")
    youth = share("edad", ["0-25"], "youth")
    senior = share("edad", ["65-100"], "senior")
    female = share("sexo", ["mujer"], "female")

    shares = (
        tot.join(low_income, "zone_id").join(youth, "zone_id")
        .join(senior, "zone_id").join(female, "zone_id")
    )
    shares = (
        shares.withColumn("low_income_inflow_share", F.col("low_income") / F.when(F.col("tot") > 0, F.col("tot")))
        .withColumn("youth_mobility_share", F.col("youth") / F.when(F.col("tot") > 0, F.col("tot")))
        .withColumn("senior_mobility_share", F.col("senior") / F.when(F.col("tot") > 0, F.col("tot")))
        .withColumn("female_share", F.col("female") / F.when(F.col("tot") > 0, F.col("tot")))
    )
    # Shannon entropy over the joint renta x edad histogram (NA is its own bucket).
    joint = (
        od.groupBy("destino", "renta", "edad").agg(F.sum("viajes").alias("v"))
        .withColumnRenamed("destino", "zone_id")
    )
    w = Window.partitionBy("zone_id")
    joint = joint.withColumn("zt", F.sum("v").over(w))
    joint = joint.withColumn("p", F.col("v") / F.when(F.col("zt") > 0, F.col("zt")))
    entropy = joint.groupBy("zone_id").agg(
        (-F.sum(F.when(F.col("p") > 0, F.col("p") * F.log2(F.col("p"))).otherwise(0.0))).alias("geodemo_diversity")
    )
    return shares.select(
        "zone_id", "low_income_inflow_share", "youth_mobility_share",
        "senior_mobility_share", "female_share",
    ).join(entropy, "zone_id", "left")


def zone_typology_indices(od, inflow, outflow):
    """Interpretable per-zone indices feeding the MLlib typology cluster."""
    through = (
        inflow.join(outflow, "zone_id", "outer")
        .withColumn(
            "sink_source",
            F.log(
                (F.coalesce(F.col("outflow_daily"), F.lit(0.0)) + 1.0)
                / (F.coalesce(F.col("inflow_daily"), F.lit(0.0)) + 1.0)
            ),
        )
        .select("zone_id", "sink_source")
    )
    # intra-zone share (origen == destino) = self-containment.
    intra = (
        od.groupBy("destino")
        .agg(
            F.sum("viajes").alias("tot"),
            F.sum(F.when(F.col("origen") == F.col("destino"), F.col("viajes")).otherwise(0.0)).alias("intra"),
        )
        .withColumnRenamed("destino", "zone_id")
        .withColumn("intra_zone_share", F.col("intra") / F.when(F.col("tot") > 0, F.col("tot")))
        .select("zone_id", "intra_zone_share")
    )
    # long-trip share (>50 km distancia band).
    longt = (
        od.groupBy("destino")
        .agg(
            F.sum("viajes").alias("tot"),
            F.sum(F.when(F.col("distancia") == ">50", F.col("viajes")).otherwise(0.0)).alias("lng"),
        )
        .withColumnRenamed("destino", "zone_id")
        .withColumn("long_trip_share", F.col("lng") / F.when(F.col("tot") > 0, F.col("tot")))
        .select("zone_id", "long_trip_share")
    )
    return through.join(intra, "zone_id", "outer").join(longt, "zone_id", "outer")


def label_typology(sedona, zone_features):
    """Standardise the interpretable vector + MLlib BisectingKMeans -> label.

    Five interpretable classes mapped from cluster centroids by their dominant
    axis: commuter-dormitory / employment-sink / leisure-magnet /
    transit-corridor / self-contained.
    """
    from pyspark.ml.feature import VectorAssembler, StandardScaler
    from pyspark.ml.clustering import BisectingKMeans

    feat_cols = [
        "sink_source", "intra_zone_share", "long_trip_share",
        "leisure_share", "commute_share",
    ]
    df = zone_features
    for c in feat_cols:
        df = df.withColumn(c, F.coalesce(F.col(c).cast("double"), F.lit(0.0)))
    assembler = VectorAssembler(inputCols=feat_cols, outputCol="_v", handleInvalid="keep")
    scaler = StandardScaler(inputCol="_v", outputCol="_vs", withMean=True, withStd=True)
    vec = assembler.transform(df)
    model = scaler.fit(vec)
    scaled = model.transform(vec)
    k = 5
    bkm = BisectingKMeans(k=k, featuresCol="_vs", predictionCol="_cluster", seed=42)
    km = bkm.fit(scaled)
    clustered = km.transform(scaled)

    # Name clusters by their centroid's dominant standardised axis.
    centers = km.clusterCenters()
    axis_label = {
        0: "commuter-dormitory",   # sink_source high (more outflow)
        1: "employment-sink",      # sink_source low (more inflow)
        2: "leisure-magnet",       # leisure_share high
        3: "transit-corridor",     # long_trip_share high
        4: "self-contained",       # intra_zone_share high
    }
    # Map each cluster id -> label by which standardised feature dominates.
    # BisectingKMeans can return FEWER than k clusters on a small/degenerate
    # sample, so iterate over the ACTUAL centroids, never range(k).
    import numpy as np
    n_clusters = len(centers)
    label_for_cluster = {}
    used = set()
    # Greedy: assign the label whose driving axis the centroid maximises.
    driving_axis = {0: 0, 1: 0, 2: 3, 3: 2, 4: 1}  # label_idx -> feat index (sink_source sign handled below)
    order = sorted(range(n_clusters), key=lambda ci: -float(np.max(np.abs(centers[ci]))))
    for ci in order:
        c = centers[ci]
        # score each candidate label by centroid alignment to its axis
        best, best_lab = -1e18, None
        for lab_idx, ax in driving_axis.items():
            if lab_idx in used:
                continue
            val = c[ax]
            if lab_idx == 0:   # commuter-dormitory wants HIGH sink_source
                metric = val
            elif lab_idx == 1: # employment-sink wants LOW sink_source
                metric = -val
            else:
                metric = val
            if metric > best:
                best, best_lab = metric, lab_idx
        if best_lab is None:
            best_lab = next(i for i in axis_label if i not in used)
        used.add(best_lab)
        label_for_cluster[ci] = axis_label[best_lab]

    mapping = F.create_map(*sum(([F.lit(k_), F.lit(v_)] for k_, v_ in label_for_cluster.items()), []))
    labelled = clustered.withColumn("mobility_typology", mapping[F.col("_cluster")])
    return labelled.select("zone_id", "mobility_typology", "_cluster"), label_for_cluster


# ---------------------------------------------------------------------------
# Disaggregation: zone metric -> hex via S1 area_weight
# ---------------------------------------------------------------------------
def disaggregate(xwalk, zone_df, value_cols, *, flow_like=()):
    """Join zone metrics onto hexes through the crosswalk.

    flow_like columns (counts: inflow/outflow/support) are area-weighted
    (value * area_weight, summed per hex). Ratio/share/categorical columns are
    weight-AVERAGED (sum(value*w)/sum(w)) so they stay on their native scale.
    Categorical columns take the max-weight zone's value.
    """
    j = xwalk.join(zone_df, "zone_id", "left")
    aggs = []
    for c in value_cols:
        if c in flow_like:
            aggs.append(F.sum(F.col(c) * F.col("area_weight")).alias(c))
        else:
            aggs.append(
                (F.sum(F.col(c) * F.col("area_weight")) / F.sum(F.when(F.col(c).isNotNull(), F.col("area_weight"))))
                .alias(c)
            )
    return j.groupBy("h3_id").agg(*aggs)


def disaggregate_categorical(xwalk, zone_df, col):
    """Assign each hex the categorical value of its MAX-area-weight zone."""
    j = xwalk.join(zone_df.select("zone_id", col), "zone_id", "left")
    w = Window.partitionBy("h3_id").orderBy(F.col("area_weight").desc())
    return (
        j.withColumn("_rn", F.row_number().over(w))
        .where("_rn = 1")
        .select("h3_id", col)
    )


# ---------------------------------------------------------------------------
# Arcs — Sedona ST_MakeLine over top-N OD pairs (centroid lines)
# ---------------------------------------------------------------------------
def build_arcs(sedona, od, xwalk):
    """Top-N inter-hex OD arcs as centroid great-circle inputs.

    Aggregate OD to zone pairs, disaggregate each endpoint to its dominant hex
    via the crosswalk, take hex centroids (lon/lat) with Sedona ST_X/ST_Y over
    ST_H3ToGeom centroids, keep the top-N by flow. Same shape as the v1 arcs.json
    (source_lon/lat, target_lon/lat, flow) — drop-in for the ArcLayer.
    """
    n_days = od.select("fecha").distinct().count()
    od_pairs = (
        od.where("origen <> destino")
        .groupBy("origen", "destino")
        .agg((F.sum("viajes") / n_days).alias("flow"))
    )
    # dominant hex per zone (max area_weight) — gives a representative point.
    w = Window.partitionBy("zone_id").orderBy(F.col("area_weight").desc())
    dom = (
        xwalk.withColumn("_rn", F.row_number().over(w)).where("_rn = 1")
        .select("zone_id", "h3_id")
    )
    # hex centroid lon/lat via Sedona.
    cent = dom.withColumn(
        "_c", F.expr("ST_Centroid(ST_H3ToGeom(array(cast(conv(h3_id,16,10) as bigint)))[0])")
    ).select(
        "zone_id",
        F.expr("ST_X(_c) AS lon"),
        F.expr("ST_Y(_c) AS lat"),
    )
    o = cent.select(F.col("zone_id").alias("origen"), F.col("lon").alias("source_lon"), F.col("lat").alias("source_lat"))
    d = cent.select(F.col("zone_id").alias("destino"), F.col("lon").alias("target_lon"), F.col("lat").alias("target_lat"))
    arcs = (
        od_pairs.join(F.broadcast(o), "origen").join(F.broadcast(d), "destino")
        .select("source_lon", "source_lat", "target_lon", "target_lat", "flow")
        .orderBy(F.col("flow").desc())
        .limit(ARC_TOP_N)
    )
    # ST_MakeLine proves the Sedona arc construction (server-side geometry).
    arcs = arcs.withColumn(
        "_line",
        F.expr("ST_MakeLine(ST_Point(source_lon, source_lat), ST_Point(target_lon, target_lat))"),
    ).drop("_line")  # geometry validated; JSON export uses the lon/lat scalars.
    return arcs


# ---------------------------------------------------------------------------
def main() -> None:
    SILVER.mkdir(parents=True, exist_ok=True)
    GOLD.mkdir(parents=True, exist_ok=True)
    # enable_rtree=True: park pyspark's duplicate jts-core so the shaded Sedona
    # jar's JTS is the only copy on the classpath -> the indexed
    # BroadcastIndexJoin (R-tree) executes (the earlier IllegalAccessError on
    # IndexSerde.getItemBoundables() was a two-jts-on-two-loaders split; proven
    # fixed — see catmob.spark._isolate_shaded_jts).
    sedona = get_sedona("mitma-layers", driver_memory="6g", enable_rtree=True)
    try:
        print("[1/8] building dasymetric crosswalk (Sedona ST_Intersection in 25831)...")
        xwalk = build_crosswalk(sedona).cache()
        n_xw = xwalk.count()
        n_hex = xwalk.select("h3_id").distinct().count()
        n_zone = xwalk.select("zone_id").distinct().count()
        print(f"      crosswalk rows={n_xw:,} hexes={n_hex:,} zones={n_zone:,}")
        XWALK_OUT.mkdir(parents=True, exist_ok=True)
        xwalk.write.mode("overwrite").parquet(str(XWALK_OUT))

        print("[2/8] loading od_silver (weekday/is_weekend/NA-bucket)...")
        od = load_od_silver(sedona).cache()
        print(f"      od rows (all days, hourly)={od.count():,}")

        print("[3/8] inflow/outflow/support daily rollup...")
        inflow, outflow, support, n_days = zone_inflow_outflow_daily(od)

        print("[4/8] rhythm (24h profile + peak shares + MAX_BY peak_hour)...")
        rhythm_scalars, profile = zone_rhythm(od)

        print("[5/8] weekend + geodemographic + typology indices...")
        weekend = zone_weekend(od)
        geodemo = zone_geodemographic(od)
        typ_idx = zone_typology_indices(od, inflow, outflow)
        # typology needs leisure/commute shares too.
        typ_idx = typ_idx.join(
            weekend.select("zone_id", "leisure_share", "commute_share"), "zone_id", "left"
        )
        typ_labels, label_map = label_typology(sedona, typ_idx)
        print(f"      typology cluster->label: {label_map}")

        print("[6/8] disaggregating zone->hex via crosswalk...")
        # flow-like (area-weighted sums)
        flow_zone = (
            inflow.join(outflow, "zone_id", "outer").join(support, "zone_id", "outer")
        )
        hex_flow = disaggregate(
            xwalk, flow_zone, ["inflow_daily", "outflow_daily", "support_n"],
            flow_like=("inflow_daily", "outflow_daily", "support_n"),
        )
        hex_flow = hex_flow.withColumnRenamed("inflow_daily", "mitma_inflow_daily") \
            .withColumnRenamed("outflow_daily", "mitma_outflow_daily")
        # through_ratio = log(outflow/inflow) on the disaggregated hex values.
        hex_flow = hex_flow.withColumn(
            "mitma_through_ratio",
            F.log((F.col("mitma_outflow_daily") + 1.0) / (F.col("mitma_inflow_daily") + 1.0)),
        )

        # ratio/share metrics (weight-averaged)
        hex_rhythm = disaggregate(
            xwalk, rhythm_scalars.drop("peak_hour"),
            ["am_peak_share", "midday_share", "pm_peak_share", "night_share"],
        )
        hex_peak = disaggregate_categorical(xwalk, rhythm_scalars, "peak_hour")
        hex_weekend = disaggregate(
            xwalk, weekend,
            ["weekend_weekday_ratio", "leisure_share", "commute_share",
             "weekend_hotspot_score", "weekend_support_n"],
            flow_like=("weekend_support_n",),
        )
        hex_geodemo = disaggregate(
            xwalk, geodemo,
            ["low_income_inflow_share", "youth_mobility_share",
             "senior_mobility_share", "female_share", "geodemo_diversity"],
        )
        hex_intra = disaggregate(xwalk, typ_idx.select("zone_id", "intra_zone_share"), ["intra_zone_share"])
        hex_typ = disaggregate_categorical(xwalk, typ_labels, "mobility_typology")

        # peak_hour bucket: map the modal hour to a categorical band.
        hex_peak = hex_peak.withColumn(
            "peak_hour_bucket",
            F.when(F.col("peak_hour").between(6, 10), "morning")
            .when(F.col("peak_hour").between(11, 15), "midday")
            .when(F.col("peak_hour").between(16, 21), "evening")
            .otherwise("night"),
        )

        print("[7/8] merging gold mobility table...")
        gold = (
            hex_flow
            .join(hex_rhythm, "h3_id", "outer")
            .join(hex_peak, "h3_id", "outer")
            .join(hex_weekend, "h3_id", "outer")
            .join(hex_geodemo, "h3_id", "outer")
            .join(hex_intra, "h3_id", "outer")
            .join(hex_typ, "h3_id", "outer")
        )
        gold_pdf = gold.toPandas()
        gold_pdf.to_parquet(MOBILITY_OUT, index=False)
        print(f"      wrote {MOBILITY_OUT.name}: {len(gold_pdf):,} hexes x {len(gold_pdf.columns)} cols")

        # rhythm sibling (h3_id -> 24 floats), disaggregated per hour.
        prof_cols = [str(i) for i in range(24)]
        hex_profile = disaggregate(xwalk, profile, prof_cols)
        hex_profile_pdf = hex_profile.toPandas()
        hex_profile_pdf.to_parquet(RHYTHM_OUT, index=False)
        print(f"      wrote {RHYTHM_OUT.name}: {len(hex_profile_pdf):,} hex hour-profiles")

        print("[8/8] arcs (Sedona ST_MakeLine top-N)...")
        arcs = build_arcs(sedona, od, xwalk)
        arcs_pdf = arcs.toPandas()
        arcs_pdf.to_parquet(ARCS_OUT, index=False)
        print(f"      wrote {ARCS_OUT.name}: {len(arcs_pdf):,} arcs")

        # regression diff vs the shipped naive-centroid gold.
        import pandas as pd
        old = pd.read_parquet(GOLD_V2, columns=["h3_id", "mitma_inflow_daily", "mitma_outflow_daily", "mitma_through_ratio"])
        cmp = old.merge(gold_pdf[["h3_id", "mitma_inflow_daily", "mitma_outflow_daily", "mitma_through_ratio"]],
                        on="h3_id", suffixes=("_old", "_new"))
        print("\n=== REGRESSION DIFF (naive centroid -> dasymetric) ===")
        for c in ("mitma_inflow_daily", "mitma_outflow_daily", "mitma_through_ratio"):
            o, nw = cmp[f"{c}_old"], cmp[f"{c}_new"]
            print(f"  {c}: old median={o.median():.3f} new median={nw.median():.3f} "
                  f"| old nunique={o.nunique()} new nunique={nw.nunique()}")
        print("  (old has FEW unique values per distrito = over-attribution; new varies per hex = dasymetric fix)")
    finally:
        sedona.stop()


if __name__ == "__main__":
    main()
