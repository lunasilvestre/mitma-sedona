"""GOLD layer — per-H3 analytic features from od_silver + the S1 crosswalk.

Every zone->hex disaggregation here is a CHEAP broadcast equi-join on
``zone_id`` against the precomputed ``area_weight`` from
``pipeline_silver.build_zone_h3_xwalk`` — no per-query geometry intersection.

Themes (each a function returning a per-hex DataFrame keyed by ``h3_id``):

  * :func:`dasymetric_inflow_outflow` — corrected mitma_inflow/outflow/through_ratio
  * :func:`hourly_rhythm`            — 24h profile + am/pm/midday/night shares + peak bucket
  * :func:`weekend_hotspots`         — weekend/weekday ratio + leisure share + support
  * :func:`mobility_typology`        — interpretable indices -> MLlib KMeans label
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


def _disaggregate_ratios(zone_metric_df: DataFrame, xwalk_df: DataFrame, ratio_specs, *, key="zone_id"):
    """Disaggregate RATIO/share columns correctly: carry the raw numerator and
    denominator as area-weighted SUMS through the crosswalk, then divide AFTER
    the per-hex groupBy. This is the review-major fix — a ratio is NOT additively
    reweightable, so area-weighting the ratio directly (``sum(r*w)/sum(w)``) is
    statistically wrong (it mixes incommensurable per-zone proportions). The
    only correct disaggregation re-derives the ratio at hex level from the
    area-weighted parts.

    Parameters
    ----------
    ratio_specs : list[tuple[str, str, str]]
        ``(out_col, numerator_col, denominator_col)`` triples. ``numerator_col``
        and ``denominator_col`` are RAW count columns on ``zone_metric_df``
        (e.g. ``female_count`` / ``known_sex_count``); the helper disaggregates
        BOTH as area-weighted sums and emits ``out_col = num_hex / den_hex``.

    Returns a per-hex DataFrame with each ``out_col`` plus the underlying
    area-weighted ``*_num`` / ``*_den`` sums (so callers can also surface
    coverage fractions or recombine).
    """
    # Collect the distinct raw count columns we must carry down.
    raw_cols = []
    for _out, num, den in ratio_specs:
        for c in (num, den):
            if c not in raw_cols:
                raw_cols.append(c)
    joined = zone_metric_df.join(F.broadcast(xwalk_df), on=key, how="inner")
    aggs = [F.sum(F.col(c) * F.col("area_weight")).alias(c) for c in raw_cols]
    hexed = joined.groupBy("h3_id").agg(*aggs)
    for out, num, den in ratio_specs:
        hexed = hexed.withColumn(
            out, F.col(num) / F.when(F.col(den) > 0, F.col(den))
        )
    return hexed


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
    """weekend_weekday_ratio (per-day MEANS) + leisure/commute shares + support.

    Uses inflow. CORRECT disaggregation (review-major fix): every ratio is
    rebuilt at hex level from area-weighted NUMERATOR and DENOMINATOR sums via
    :func:`_disaggregate_ratios` — never by area-weighting the per-zone ratio.

      * ``weekend_weekday_ratio`` = (per-day-mean weekend inflow) /
        (per-day-mean weekday inflow). num = ``we_mean``, den = ``wd_mean``;
        both are additive flow-rate quantities so the hex ratio is the
        area-weighted-mean-weekend / area-weighted-mean-weekday.
      * ``leisure_share`` = leisure inflow / total inflow.
      * ``commute_share`` = commute inflow / total inflow.

    ``support_n`` is an area-weighted OD-segment row count (a coarse
    density/confidence proxy — NOT the MITMA <100-device privacy gate, which is
    invisible in the expanded open data; see FIELDS registry + risk note).
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
    # leisure / commute / total inflow COUNTS (numerators + denominator).
    leis = od_silver_df.groupBy(F.col("destino").alias("zone_id")).agg(
        F.sum(F.when(F.col("actividad_destino").isin(*LEISURE_ACTS), F.col("viajes")).otherwise(0.0)).alias("leis_v"),
        F.sum(F.when(F.col("actividad_destino").isin(*COMMUTE_ACTS), F.col("viajes")).otherwise(0.0)).alias("comm_v"),
        F.sum("viajes").alias("tot_v"),
    )
    zone = (
        wk.join(support, "zone_id", "outer")
        .join(leis, "zone_id", "outer")
        .fillna(0.0, subset=["we_mean", "wd_mean", "leis_v", "comm_v", "tot_v", "support_n"])
    )

    # Disaggregate every ratio from its area-weighted parts; support_n is an
    # additive count carried straight through the area weights.
    hexed = _disaggregate_ratios(
        zone, xwalk_df,
        [
            ("weekend_weekday_ratio", "we_mean", "wd_mean"),
            ("leisure_share", "leis_v", "tot_v"),
            ("commute_share", "comm_v", "tot_v"),
        ],
    )
    hex_support = _disaggregate(zone, xwalk_df, ["support_n"])
    hexed = hexed.join(hex_support, "h3_id", "inner")
    hexed = hexed.withColumn(
        "weekend_hotspot_score",
        F.when(
            F.col("support_n") >= min_support,
            F.col("weekend_weekday_ratio") * (F.lit(0.5) + F.col("leisure_share")),
        ),
    ).select(
        "h3_id", "support_n", "weekend_weekday_ratio",
        "leisure_share", "commute_share", "weekend_hotspot_score",
    )
    return hexed


# ---------------------------------------------------------------------------
# Theme 3 — geodemographic shares + renta x edad entropy
# ---------------------------------------------------------------------------

def geodemographic(od_silver_df: DataFrame, xwalk_df: DataFrame):
    """Geodemographic inflow shares over the KNOWN subset + entropy in BITS.

    Two review-major fixes:

    1. DENOMINATOR (named shares): the per-segment NA mass (~26-43% of trips for
       sexo/edad) was sitting in the denominator, so e.g. ``female_share``
       collapsed to ~0.075 instead of ~0.5. Each named share is now divided over
       the KNOWN subset for that variable:
         * ``female_share`` = mujer / (hombre + mujer)
         * ``youth_mobility_share`` / ``senior_mobility_share`` = band / known-age
         * ``low_income_inflow_share`` = <10 renta / known-renta
       We ALSO emit ``*_of_all_trips`` companions (denominator = all inflow,
       NA included) and ``*_coverage`` = known / all, so the NA-coverage
       fraction is explicit rather than silently deflating the share.

    2. RATIO DISAGGREGATION: every share is rebuilt at hex level from
       area-weighted NUMERATOR and DENOMINATOR sums (``_disaggregate_ratios``),
       never by area-weighting the per-zone ratio.

    3. ``geodemo_diversity`` is Shannon entropy over the joint renta x edad
       histogram, now in BITS (log base 2). The hex value is recomputed from the
       area-weighted joint cell counts, so it is a true per-hex entropy, not an
       area-weighted average of per-zone entropies.
    """
    z = F.col("destino")
    base = od_silver_df.groupBy(z.alias("zone_id")).agg(
        F.sum("viajes").alias("tot"),
        # --- numerators ---
        F.sum(F.when(F.col("renta") == "<10", F.col("viajes")).otherwise(0.0)).alias("low_income"),
        F.sum(F.when(F.col("edad") == "0-25", F.col("viajes")).otherwise(0.0)).alias("youth"),
        F.sum(F.when(F.col("edad") == "65-100", F.col("viajes")).otherwise(0.0)).alias("senior"),
        F.sum(F.when(F.col("sexo") == "mujer", F.col("viajes")).otherwise(0.0)).alias("female"),
        # --- KNOWN-subset denominators (exclude the NA bucket) ---
        F.sum(F.when(F.col("renta") != "NA", F.col("viajes")).otherwise(0.0)).alias("known_renta"),
        F.sum(F.when(F.col("edad") != "NA", F.col("viajes")).otherwise(0.0)).alias("known_edad"),
        F.sum(F.when(F.col("sexo").isin("hombre", "mujer"), F.col("viajes")).otherwise(0.0)).alias("known_sexo"),
    )

    # Per-hex shares from area-weighted numerator/denominator sums. KNOWN-subset
    # denominators give the honest demographic proportion; the *_of_all_trips
    # companions keep the all-inflow denominator for transparency.
    shares = _disaggregate_ratios(
        base, xwalk_df,
        [
            # honest shares over the known subset
            ("female_share", "female", "known_sexo"),
            ("youth_mobility_share", "youth", "known_edad"),
            ("senior_mobility_share", "senior", "known_edad"),
            ("low_income_inflow_share", "low_income", "known_renta"),
            # *_of_all_trips companions (denominator = all inflow incl. NA)
            ("female_of_all_trips", "female", "tot"),
            ("youth_of_all_trips", "youth", "tot"),
            ("senior_of_all_trips", "senior", "tot"),
            ("low_income_of_all_trips", "low_income", "tot"),
            # NA-coverage fractions (known / all) — surface the NA mass
            ("sexo_coverage", "known_sexo", "tot"),
            ("edad_coverage", "known_edad", "tot"),
            ("renta_coverage", "known_renta", "tot"),
        ],
    )

    # Entropy over the joint renta x edad histogram, in BITS, recomputed at hex
    # level from area-weighted joint cell counts.
    joint = od_silver_df.groupBy(z.alias("zone_id"), "renta", "edad").agg(
        F.sum("viajes").alias("cell")
    )
    joined = joint.join(F.broadcast(xwalk_df), "zone_id", "inner")
    hex_cells = joined.groupBy("h3_id", "renta", "edad").agg(
        F.sum(F.col("cell") * F.col("area_weight")).alias("cell")
    )
    ht = hex_cells.withColumn("ht", F.sum("cell").over(Window.partitionBy("h3_id")))
    ht = ht.withColumn("p", F.col("cell") / F.when(F.col("ht") > 0, F.col("ht")))
    LN2 = float(__import__("math").log(2.0))
    ent = ht.groupBy("h3_id").agg(
        (
            -F.sum(F.when(F.col("p") > 0, F.col("p") * F.log(F.col("p"))).otherwise(0.0))
            / F.lit(LN2)  # nats -> bits
        ).alias("geodemo_diversity")
    )

    keep = [
        "h3_id",
        "female_share", "youth_mobility_share", "senior_mobility_share", "low_income_inflow_share",
        "female_of_all_trips", "youth_of_all_trips", "senior_of_all_trips", "low_income_of_all_trips",
        "sexo_coverage", "edad_coverage", "renta_coverage",
    ]
    return shares.select(*keep).join(ent, "h3_id", "outer")


# ---------------------------------------------------------------------------
# Theme 4 — mobility typology (interpretable indices -> MLlib KMeans)
# ---------------------------------------------------------------------------

# Typology feature set. The review found sink_source = log(out/in) spans only
# ~-0.075..0.033 at daily distrito resolution (every distrito both sends and
# receives a near-balanced ~population worth of trips), so it CANNOT support a
# commuter-dormitory vs employment-sink split. We therefore build the typology
# on the dimensions that GENUINELY vary on this sample: self-containment,
# leisure pull, commute pull, and long-trip exposure. sink_source is still
# carried as a descriptive column (and surfaced as such) but is NOT a cluster
# feature, and the labels are derived from the actual centroids.
TYPOLOGY_FEATURES = ["intra_zone_share", "leisure_share", "commute_share", "long_trip_share"]


def typology_indices(od_silver_df: DataFrame, xwalk_df: DataFrame):
    """Per-hex interpretable indices feeding the typology clusterer.

    Every share is rebuilt at hex level from area-weighted numerator/denominator
    sums (``_disaggregate_ratios``). ``sink_source`` is carried as a descriptive
    column only (see TYPOLOGY_FEATURES) — its daily-distrito range is too narrow
    to anchor a sink/source typology, so it is excluded from the cluster vector.
    """
    inb = od_silver_df.groupBy(F.col("destino").alias("zone_id")).agg(
        F.sum(F.when(F.col("origen") == F.col("destino"), F.col("viajes")).otherwise(0.0)).alias("intra"),
        F.sum("viajes").alias("tot_in"),
        F.sum(F.when(F.col("distancia").isin(*LONG_DIST_BANDS), F.col("viajes")).otherwise(0.0)).alias("long_v"),
        F.sum(F.when(F.col("actividad_destino").isin(*COMMUTE_ACTS), F.col("viajes")).otherwise(0.0)).alias("comm_v"),
        F.sum(F.when(F.col("actividad_destino").isin(*LEISURE_ACTS), F.col("viajes")).otherwise(0.0)).alias("leis_v"),
    )
    out_tot = od_silver_df.groupBy(F.col("origen").alias("zone_id")).agg(F.sum("viajes").alias("tot_out"))
    zone = inb.join(out_tot, "zone_id", "outer").fillna(
        0.0, subset=["intra", "tot_in", "long_v", "comm_v", "leis_v", "tot_out"]
    )

    # Per-hex shares from area-weighted parts.
    hexed = _disaggregate_ratios(
        zone, xwalk_df,
        [
            ("intra_zone_share", "intra", "tot_in"),
            ("long_trip_share", "long_v", "tot_in"),
            ("leisure_share", "leis_v", "tot_in"),
            ("commute_share", "comm_v", "tot_in"),
        ],
    )
    # sink_source as a DESCRIPTIVE column: recombine the area-weighted in/out
    # flow sums, then take log(out/in). (Kept for transparency; not a cluster
    # feature — see TYPOLOGY_FEATURES.)
    hex_flow = _disaggregate(zone, xwalk_df, ["tot_in", "tot_out"])
    hex_flow = hex_flow.withColumnRenamed("tot_in", "_in").withColumnRenamed("tot_out", "_out")
    hexed = hexed.join(hex_flow, "h3_id", "inner").withColumn(
        "sink_source",
        F.log(F.greatest(F.col("_out"), F.lit(1.0)) / F.greatest(F.col("_in"), F.lit(1.0))),
    )
    return hexed.select(
        "h3_id", "intra_zone_share", "long_trip_share",
        "leisure_share", "commute_share", "sink_source",
    )


def mobility_typology(spark, indices_df: DataFrame, *, k: int = 5, seed: int = 42):
    """Standardise the index vector and label each hex via MLlib KMeans.

    Returns (labelled_df with mobility_typology + cluster_id, centroids list).
    Clusters on TYPOLOGY_FEATURES (dimensions that genuinely vary on this
    sample); sink_source is excluded (too narrow a daily-distrito range to
    anchor a sink/source split). Labels are derived from the ACTUAL centroids
    in raw (un-standardised) units, so the names describe what the data shows.
    """
    from pyspark.ml.clustering import KMeans
    from pyspark.ml.feature import StandardScaler, VectorAssembler
    from pyspark.sql import functions as _F

    all_cols = list(TYPOLOGY_FEATURES)
    # Materialise the indices so the clusterer sees stable, non-lazy values
    # (a lazily re-evaluated upstream join can otherwise feed it constants and
    # collapse k-means to a single cluster).
    df = indices_df.fillna(0.0, subset=all_cols).cache()
    df.count()
    # Drop zero-variance columns — they break StandardScaler(withMean,withStd)
    # (std=0 -> NaN feature -> degenerate single-cluster collapse).
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

    # Raw-unit cluster means (un-standardise) for honest, data-driven labels.
    centers_std = model.clusterCenters()
    means = {c: float(scaler_model.mean[i]) for i, c in enumerate(feat_cols)}
    stds_v = {c: float(scaler_model.std[i]) for i, c in enumerate(feat_cols)}
    centers_raw = [
        {c: means[c] + centers_std[i][j] * stds_v[c] for j, c in enumerate(feat_cols)}
        for i in range(len(centers_std))
    ]
    # Population (global) means for the same features, to read each centroid as
    # "above/below typical" rather than from arbitrary z-scores.
    pop = df.select(*[_F.avg(c).alias(c) for c in feat_cols]).collect()[0].asDict()

    names = _name_clusters(centers_std, centers_raw, pop, feat_cols)
    mapping = F.create_map(*sum([[F.lit(i), F.lit(n)] for i, n in names.items()], []))
    labelled = labelled.withColumn("mobility_typology", mapping[F.col("cluster_id")])
    centroids = [
        {
            "cluster_id": i,
            "label": names[i],
            **{c: round(centers_raw[i][c], 4) for c in feat_cols},
            "_z": {c: round(float(centers_std[i][j]), 3) for j, c in enumerate(feat_cols)},
        }
        for i in range(len(centers_std))
    ]
    # Carry the cluster features PLUS any descriptive columns present on the
    # input (e.g. sink_source — kept for transparency though not a cluster axis).
    extra = [c for c in indices_df.columns
             if c not in ("h3_id", *all_cols) and c in labelled.columns]
    return labelled.select("h3_id", "cluster_id", "mobility_typology", *all_cols, *extra), centroids


def _name_clusters(centers_std, centers_raw, pop, feat_cols):
    """Label each cluster from its ACTUAL centroid (raw units + z-scores).

    Honest, data-driven naming: a cluster only earns a pole label when its
    centroid is genuinely extreme on that dimension (|z| >= 0.5 in standardised
    units). The poles are the dimensions that actually vary on this sample:

      intra_zone_share high   -> self-contained
      leisure_share    high   -> leisure-magnet
      commute_share    high   -> commuter-corridor   (work/study pull)
      long_trip_share  high   -> transit-corridor    (long through-trips)

    A cluster whose strongest signal is below the threshold becomes
    'mixed-balanced'. Labels are unique: a weaker claimant falls back to its
    next dimension. We deliberately DO NOT emit sink/source labels — sink_source
    is not a cluster feature (its daily-distrito range is too narrow).
    """
    # (dimension, sign) -> label. Only the genuinely-varying high poles.
    POLE = {
        ("intra_zone_share", +1): "self-contained",
        ("leisure_share", +1): "leisure-magnet",
        ("commute_share", +1): "commuter-corridor",
        ("long_trip_share", +1): "transit-corridor",
    }
    # Rank each cluster's dimensions by |z|, strongest first.
    ranked = {}
    for i, c in enumerate(centers_std):
        d = dict(zip(feat_cols, c))
        ranked[i] = sorted(d.items(), key=lambda kv: -abs(kv[1]))

    names: dict[int, str] = {}
    claimed: set[str] = set()
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
