"""GOLD layer — per-H3 analytic features from od_silver + the S1 crosswalk.

Every zone->hex disaggregation here is a CHEAP broadcast equi-join on
``zone_id`` against the precomputed ``area_weight`` from
``pipeline_silver.build_zone_h3_xwalk`` — no per-query geometry intersection.

Themes (each a function returning a per-hex DataFrame keyed by ``h3_id``):

  * :func:`dasymetric_inflow_outflow` — corrected mitma_inflow/outflow/through_ratio
  * :func:`hourly_rhythm`            — 24h profile + am/pm/midday/night shares + peak bucket
  * :func:`weekend_hotspots`         — weekend/weekday ratio + leisure share + support
  * :func:`mobility_typology`        — interpretable indices -> MLlib BisectingKMeans label
  * :func:`geodemographic`           — segment-weighted shares + renta x edad entropy
  * :func:`od_arcs`                  — top-N inter-zone arcs (ST_MakeLine), browser-ready

All "disaggregate via S1" steps go through :func:`_disaggregate`.
"""
from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import Window

# MITMA actividad_destino vocabulary (verified on disk): casa, frecuente,
# no_frecuente, trabajo_estudio. Leisure = the "frecuente"/"no_frecuente"
# (recurrent/non-recurrent non-home, non-work) activities; commute =
# trabajo_estudio (work/study). "casa" (home) is neither.
LEISURE_ACTS = ("frecuente", "no_frecuente")
COMMUTE_ACTS = ("trabajo_estudio",)

# MITMA distancia bands (verified): '0.5-2','2-10','10-50','>50'. Long = '>50'.
LONG_DIST_BANDS = (">50",)


def _disaggregate(zone_metric_df: DataFrame, xwalk_df: DataFrame, value_cols, *, key="zone_id"):
    """Broadcast equi-join a per-zone metric onto hexes via area_weight.

    For additive flow quantities (trips), multiply by ``area_weight``; the
    caller aggregates ``SUM`` per ``h3_id``. For *share*/ratio columns the
    caller should disaggregate the NUMERATOR and DENOMINATOR separately and
    divide post-aggregation (shares are not additively reweightable).
    """
    joined = zone_metric_df.join(F.broadcast(xwalk_df), on=key, how="inner")
    agg = [F.sum(F.col(c) * F.col("area_weight")).alias(c) for c in value_cols]
    return joined.groupBy("h3_id").agg(*agg)


# ---------------------------------------------------------------------------
# Theme 0 — corrected dasymetric inflow / outflow / through-ratio
# ---------------------------------------------------------------------------

def dasymetric_inflow_outflow(od_daily_df: DataFrame, xwalk_df: DataFrame) -> DataFrame:
    """Recompute mitma_inflow_daily / outflow / through_ratio (area-weighted).

    daily mean across the sample's days, per zone, then disaggregated to hexes
    through S1. ``through_ratio = log(outflow/inflow)`` on the full cube
    (pivoted at 0 -> sink/source, matches the RdBu FIELDS entry semantics when
    the browser pivots at 0; the legacy column pivoted at 1.0, so we keep BOTH:
    a ``mitma_through_ratio`` (legacy linear ratio, pivot 1.0) and a
    ``mitma_log_through_ratio`` (pivot 0.0)).
    """
    n_days = od_daily_df.select("fecha").distinct().count() or 1

    inflow = (
        od_daily_df.groupBy(F.col("destino").alias("zone_id"))
        .agg((F.sum("viajes") / F.lit(n_days)).alias("viajes_in"))
    )
    outflow = (
        od_daily_df.groupBy(F.col("origen").alias("zone_id"))
        .agg((F.sum("viajes") / F.lit(n_days)).alias("viajes_out"))
    )
    zone = inflow.join(outflow, on="zone_id", how="outer").fillna(0.0)

    hex_flow = _disaggregate(zone, xwalk_df, ["viajes_in", "viajes_out"])
    return (
        hex_flow.withColumnRenamed("viajes_in", "mitma_inflow_daily")
        .withColumnRenamed("viajes_out", "mitma_outflow_daily")
        .withColumn(
            "mitma_through_ratio",
            F.col("mitma_outflow_daily")
            / F.when(F.col("mitma_inflow_daily") > 0, F.col("mitma_inflow_daily")),
        )
        .withColumn(
            "mitma_log_through_ratio",
            F.log(
                F.greatest(F.col("mitma_outflow_daily"), F.lit(1.0))
                / F.greatest(F.col("mitma_inflow_daily"), F.lit(1.0))
            ),
        )
    )


# ---------------------------------------------------------------------------
# Theme 1 — hourly rhythm (24h profile + peak shares + bucket)
# ---------------------------------------------------------------------------

def hourly_rhythm(od_silver_df: DataFrame, xwalk_df: DataFrame):
    """Per-hex hour-of-day profile, peak shares, peak bucket.

    Computed on INFLOW (trips arriving) so the hex's own diurnal pulse is
    captured. Builds a zone x periodo SUM, disaggregates each periodo bucket to
    hexes, then normalises per hex to a 24-element share vector.

    Returns (hex_scalars_df, rhythm_rows_df) where rhythm_rows_df is
    ``(h3_id, periodo, share)`` long-form for the lazy-loaded sibling JSON.
    """
    zp = (
        od_silver_df.groupBy(F.col("destino").alias("zone_id"), "periodo")
        .agg(F.sum("viajes").alias("viajes"))
    )
    # Disaggregate per (zone, periodo) keeping periodo through the join.
    joined = zp.join(F.broadcast(xwalk_df), on="zone_id", how="inner")
    hex_hour = joined.groupBy("h3_id", "periodo").agg(
        F.sum(F.col("viajes") * F.col("area_weight")).alias("h")
    )
    w = Window.partitionBy("h3_id")
    hex_hour = hex_hour.withColumn("day_total", F.sum("h").over(w)).withColumn(
        "share", F.col("h") / F.when(F.col("day_total") > 0, F.col("day_total"))
    )

    def band(lo, hi):
        return F.sum(F.when((F.col("periodo") >= lo) & (F.col("periodo") <= hi), F.col("share")).otherwise(0.0))

    scalars = hex_hour.groupBy("h3_id").agg(
        band(7, 9).alias("am_peak_share"),
        band(17, 19).alias("pm_peak_share"),
        band(11, 15).alias("midday_share"),
        (band(0, 5) + band(22, 23)).alias("night_share"),
        F.max_by("periodo", "h").alias("peak_hour"),
        F.first("day_total").alias("_dt"),
    ).drop("_dt")

    scalars = scalars.withColumn(
        "peak_hour_bucket",
        F.when((F.col("peak_hour") >= 5) & (F.col("peak_hour") <= 10), "morning")
        .when((F.col("peak_hour") >= 11) & (F.col("peak_hour") <= 15), "midday")
        .when((F.col("peak_hour") >= 16) & (F.col("peak_hour") <= 19), "evening")
        .otherwise("night"),
    )
    rhythm_rows = hex_hour.select("h3_id", "periodo", F.round("share", 4).alias("share"))
    return scalars, rhythm_rows


# ---------------------------------------------------------------------------
# Theme 2 — weekend hotspots
# ---------------------------------------------------------------------------

def weekend_hotspots(od_silver_df: DataFrame, xwalk_df: DataFrame, *, min_support: int = 50):
    """weekend_weekday_ratio (per-day MEANS) + leisure_share + support gate.

    Uses inflow. Per-day means, not raw sums (2 weekend vs 5 weekday days).
    """
    # daily inflow per zone split by weekend flag, then average across days.
    daily = (
        od_silver_df.groupBy(F.col("destino").alias("zone_id"), "fecha", "is_weekend")
        .agg(F.sum("viajes").alias("v"), F.sum("support_n").alias("n"))
    )
    by_flag = daily.groupBy("zone_id", "is_weekend").agg(
        F.avg("v").alias("mean_v"), F.sum("n").alias("support_n")
    )
    wk = (
        by_flag.groupBy("zone_id")
        .pivot("is_weekend", [True, False])
        .agg(F.first("mean_v"))
        .withColumnRenamed("true", "we_mean")
        .withColumnRenamed("false", "wd_mean")
    )
    support = (
        by_flag.groupBy("zone_id").agg(F.sum("support_n").alias("support_n"))
    )
    # leisure share over all inflow.
    leis = od_silver_df.groupBy(F.col("destino").alias("zone_id")).agg(
        F.sum(F.when(F.col("actividad_destino").isin(*LEISURE_ACTS), F.col("viajes")).otherwise(0.0)).alias("leis_v"),
        F.sum(F.when(F.col("actividad_destino").isin(*COMMUTE_ACTS), F.col("viajes")).otherwise(0.0)).alias("comm_v"),
        F.sum("viajes").alias("tot_v"),
    )
    zone = (
        wk.join(support, "zone_id", "outer")
        .join(leis, "zone_id", "outer")
        .withColumn(
            "weekend_weekday_ratio",
            F.col("we_mean") / F.when(F.col("wd_mean") > 0, F.col("wd_mean")),
        )
        .withColumn("leisure_share", F.col("leis_v") / F.when(F.col("tot_v") > 0, F.col("tot_v")))
        .withColumn("commute_share", F.col("comm_v") / F.when(F.col("tot_v") > 0, F.col("tot_v")))
    )
    # Disaggregate the support count (additive) and carry the (already-per-zone)
    # ratios down by area-weighted average to hexes.
    joined = zone.join(F.broadcast(xwalk_df), "zone_id", "inner")
    hexed = joined.groupBy("h3_id").agg(
        F.sum(F.col("support_n") * F.col("area_weight")).alias("support_n"),
        (F.sum(F.col("weekend_weekday_ratio") * F.col("area_weight")) / F.sum("area_weight")).alias("weekend_weekday_ratio"),
        (F.sum(F.col("leisure_share") * F.col("area_weight")) / F.sum("area_weight")).alias("leisure_share"),
        (F.sum(F.col("commute_share") * F.col("area_weight")) / F.sum("area_weight")).alias("commute_share"),
    )
    hexed = hexed.withColumn(
        "weekend_hotspot_score",
        F.when(
            F.col("support_n") >= min_support,
            F.col("weekend_weekday_ratio") * (F.lit(0.5) + F.col("leisure_share")),
        ),
    )
    return hexed


# ---------------------------------------------------------------------------
# Theme 3 — geodemographic shares + renta x edad entropy
# ---------------------------------------------------------------------------

def geodemographic(od_silver_df: DataFrame, xwalk_df: DataFrame):
    """Segment-weighted inflow shares + Shannon entropy over renta x edad.

    NA edad/sexo/renta kept as their own bucket (dropping biases the shares).
    """
    z = F.col("destino")
    base = od_silver_df.groupBy(z.alias("zone_id")).agg(
        F.sum("viajes").alias("tot"),
        F.sum(F.when(F.col("renta") == "<10", F.col("viajes")).otherwise(0.0)).alias("low_income"),
        F.sum(F.when(F.col("edad") == "0-25", F.col("viajes")).otherwise(0.0)).alias("youth"),
        F.sum(F.when(F.col("edad") == "65-100", F.col("viajes")).otherwise(0.0)).alias("senior"),
        F.sum(F.when(F.col("sexo") == "mujer", F.col("viajes")).otherwise(0.0)).alias("female"),
    )
    shares = (
        base.withColumn("low_income_inflow_share", F.col("low_income") / F.when(F.col("tot") > 0, F.col("tot")))
        .withColumn("youth_mobility_share", F.col("youth") / F.when(F.col("tot") > 0, F.col("tot")))
        .withColumn("senior_mobility_share", F.col("senior") / F.when(F.col("tot") > 0, F.col("tot")))
        .withColumn("female_share", F.col("female") / F.when(F.col("tot") > 0, F.col("tot")))
    )

    # Entropy over the joint renta x edad histogram, per zone.
    joint = od_silver_df.groupBy(z.alias("zone_id"), "renta", "edad").agg(
        F.sum("viajes").alias("cell")
    )
    jt = joint.withColumn("zt", F.sum("cell").over(Window.partitionBy("zone_id")))
    jt = jt.withColumn("p", F.col("cell") / F.when(F.col("zt") > 0, F.col("zt")))
    ent = jt.groupBy("zone_id").agg(
        (-F.sum(F.when(F.col("p") > 0, F.col("p") * F.log(F.col("p"))).otherwise(0.0))).alias("geodemo_diversity")
    )

    zone = shares.join(ent, "zone_id", "outer").select(
        "zone_id",
        "low_income_inflow_share", "youth_mobility_share",
        "senior_mobility_share", "female_share", "geodemo_diversity",
    )
    # Shares are area-weighted averages to hexes.
    joined = zone.join(F.broadcast(xwalk_df), "zone_id", "inner")
    cols = ["low_income_inflow_share", "youth_mobility_share", "senior_mobility_share", "female_share", "geodemo_diversity"]
    aggs = [
        (F.sum(F.col(c) * F.col("area_weight")) / F.sum("area_weight")).alias(c) for c in cols
    ]
    return joined.groupBy("h3_id").agg(*aggs)


# ---------------------------------------------------------------------------
# Theme 4 — mobility typology (interpretable indices -> MLlib KMeans)
# ---------------------------------------------------------------------------

def typology_indices(od_silver_df: DataFrame, xwalk_df: DataFrame):
    """Per-hex interpretable indices feeding the typology clusterer."""
    # intra-zone share (origen == destino) on inflow side.
    intra = od_silver_df.groupBy(F.col("destino").alias("zone_id")).agg(
        F.sum(F.when(F.col("origen") == F.col("destino"), F.col("viajes")).otherwise(0.0)).alias("intra"),
        F.sum("viajes").alias("tot_in"),
        F.sum(F.when(F.col("distancia").isin(*LONG_DIST_BANDS), F.col("viajes")).otherwise(0.0)).alias("long_v"),
        F.sum(F.when(F.col("actividad_destino").isin(*COMMUTE_ACTS), F.col("viajes")).otherwise(0.0)).alias("comm_v"),
        F.sum(F.when(F.col("actividad_destino").isin(*LEISURE_ACTS), F.col("viajes")).otherwise(0.0)).alias("leis_v"),
    )
    out_tot = od_silver_df.groupBy(F.col("origen").alias("zone_id")).agg(F.sum("viajes").alias("tot_out"))
    zone = (
        intra.join(out_tot, "zone_id", "outer").fillna(0.0)
        .withColumn("intra_zone_share", F.col("intra") / F.when(F.col("tot_in") > 0, F.col("tot_in")))
        .withColumn("long_trip_share", F.col("long_v") / F.when(F.col("tot_in") > 0, F.col("tot_in")))
        .withColumn("commute_minus_leisure", (F.col("comm_v") - F.col("leis_v")) / F.when(F.col("tot_in") > 0, F.col("tot_in")))
        .withColumn("sink_source", F.log(F.greatest(F.col("tot_out"), F.lit(1.0)) / F.greatest(F.col("tot_in"), F.lit(1.0))))
    )
    cols = ["intra_zone_share", "long_trip_share", "commute_minus_leisure", "sink_source"]
    joined = zone.join(F.broadcast(xwalk_df), "zone_id", "inner")
    aggs = [(F.sum(F.col(c) * F.col("area_weight")) / F.sum("area_weight")).alias(c) for c in cols]
    return joined.groupBy("h3_id").agg(*aggs)


def mobility_typology(spark, indices_df: DataFrame, *, k: int = 5, seed: int = 42):
    """Standardise the index vector and label each hex via MLlib BisectingKMeans.

    Returns (labelled_df with mobility_typology + cluster_id, centroids list).
    Uses MLlib (not ST_DBSCAN) because the geoStats catalog is unavailable on
    the Spark 4.1.1 / sedona-shaded-4.0 build (FoldableUnevaluable skew).
    """
    from pyspark.ml.clustering import KMeans
    from pyspark.ml.feature import StandardScaler, VectorAssembler
    from pyspark.sql import functions as _F

    all_cols = ["intra_zone_share", "long_trip_share", "commute_minus_leisure", "sink_source"]
    # Materialise the indices so the clusterer sees stable, non-lazy values
    # (a lazily re-evaluated upstream join can otherwise feed it constants and
    # collapse k-means to a single cluster).
    df = indices_df.fillna(0.0, subset=all_cols).cache()
    df.count()
    # Drop zero-variance columns — they break StandardScaler(withMean,withStd)
    # (std=0 -> NaN feature -> degenerate single-cluster collapse). On the
    # distritos sample long_trip_share has ~no within-Catalonia variance.
    stds = df.select(*[_F.stddev_pop(c).alias(c) for c in all_cols]).collect()[0].asDict()
    feat_cols = [c for c in all_cols if (stds[c] or 0.0) > 1e-9]
    if not feat_cols:  # pathological: nothing varies
        feat_cols = all_cols
    asm = VectorAssembler(inputCols=feat_cols, outputCol="_raw", handleInvalid="keep")
    scl = StandardScaler(inputCol="_raw", outputCol="_feat", withMean=True, withStd=True)
    vec = asm.transform(df)
    scaler_model = scl.fit(vec)
    scaled = scaler_model.transform(vec)

    # MLlib KMeans (k-means||) reliably finds the typology structure here;
    # BisectingKMeans on this scaled vector intermittently returns 1 cluster.
    kmeans = KMeans(k=k, seed=seed, featuresCol="_feat", predictionCol="cluster_id",
                    initMode="k-means||", initSteps=5, maxIter=50)
    model = kmeans.fit(scaled)
    labelled = model.transform(scaled)

    # Name clusters by their dominant standardised dimension (interpretable).
    centers = model.clusterCenters()
    names = _name_clusters(centers, feat_cols)
    mapping = F.create_map(*sum([[F.lit(i), F.lit(n)] for i, n in names.items()], []))
    labelled = labelled.withColumn("mobility_typology", mapping[F.col("cluster_id")])
    centroids = [
        {"cluster_id": i, "label": names[i], **{c: float(v) for c, v in zip(feat_cols, centers[i])}}
        for i in range(len(centers))
    ]
    return labelled.select("h3_id", "cluster_id", "mobility_typology", *feat_cols), centroids


def _name_clusters(centers, feat_cols):
    """Assign one interpretable label per cluster from its centroid signature.

    Centroids are in STANDARDISED (z-score) units. Each cluster is named for the
    single dimension on which its centroid is most extreme (largest |z|), with
    the sign choosing the pole. Labels are UNIQUE: if two clusters claim the
    same label, the one with the weaker signature falls back to its next-most
    extreme dimension, and any still-unclaimed cluster becomes 'mixed-balanced'.
    These six labels match CATEGORICAL.mobility_typology in geobrowser-map.js.
    """
    # (dimension, sign) -> label.
    POLE = {
        ("sink_source", +1): "employment-sink",
        ("sink_source", -1): "commuter-dormitory",
        ("intra_zone_share", +1): "self-contained",
        ("commute_minus_leisure", -1): "leisure-magnet",
        ("long_trip_share", +1): "transit-corridor",
    }
    # Rank each cluster's dimensions by |z|, strongest first.
    ranked = {}
    for i, c in enumerate(centers):
        d = dict(zip(feat_cols, c))
        ranked[i] = sorted(d.items(), key=lambda kv: -abs(kv[1]))

    names: dict[int, str] = {}
    claimed: set[str] = set()
    # Greedy: process clusters by how confident their top signature is.
    order = sorted(ranked, key=lambda i: -abs(ranked[i][0][1]) if ranked[i] else 0)
    for i in order:
        chosen = None
        for dim, val in ranked[i]:
            if abs(val) < 0.5:  # no characteristic signal left -> generic
                break
            label = POLE.get((dim, 1 if val > 0 else -1))
            if label and label not in claimed:
                chosen = label
                break
        if chosen is None:
            chosen = "mixed-balanced"
        names[i] = chosen
        claimed.add(chosen)
    return names


# ---------------------------------------------------------------------------
# Theme 5 — OD arcs via Sedona ST_MakeLine
# ---------------------------------------------------------------------------

def od_arcs(spark, od_daily_df: DataFrame, zones_geojson_path: str, *, top_n: int = 250,
            weekend_only: bool | None = None, id_field: str = "ID"):
    """Top-N inter-zone OD arcs as ST_MakeLine(centroid(o), centroid(d)).

    Returns a list of dicts in the existing arcs.json shape:
    ``{source:[lon,lat], target:[lon,lat], value:viajes}``.
    """
    df = od_daily_df.where("origen <> destino")
    if weekend_only is True:
        df = df.where("is_weekend = true")
    elif weekend_only is False:
        df = df.where("is_weekend = false")
    pairs = (
        df.groupBy("origen", "destino").agg(F.sum("viajes").alias("viajes"))
        .orderBy(F.desc("viajes")).limit(top_n)
    )
    pairs.createOrReplaceTempView("_arc_pairs")

    from catmob.pipeline_silver import read_zones_geojson

    zones = read_zones_geojson(spark, zones_geojson_path, id_field=id_field).selectExpr(
        "zone_id AS zid", "ST_Centroid(geom_ll) AS c"
    )
    zones.createOrReplaceTempView("_arc_zones")

    arcs = spark.sql(
        """
        SELECT ST_X(o.c) AS o_lon, ST_Y(o.c) AS o_lat,
               ST_X(d.c) AS d_lon, ST_Y(d.c) AS d_lat,
               p.viajes
        FROM _arc_pairs p
        JOIN _arc_zones o ON o.zid = p.origen
        JOIN _arc_zones d ON d.zid = p.destino
        """
    ).collect()
    return [
        {
            "source": [round(r["o_lon"], 5), round(r["o_lat"], 5)],
            "target": [round(r["d_lon"], 5), round(r["d_lat"], 5)],
            "value": round(float(r["viajes"]), 1),
        }
        for r in arcs
    ]
