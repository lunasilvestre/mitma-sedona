"""SILVER layer — the two load-bearing spatial artifacts + cleaned OD.

S1 ``zone_h3_xwalk`` — the dasymetric (area-weighted) crosswalk from MITMA
zones to H3 res-8 cells, computed ONCE per zoning with Sedona and reused by
every downstream gold query. This is the irreducible spatial-join cost and
the correctness fix for the naive centroid ``within`` join in
``run_gold_v2.py:407`` that over-attributed a whole distrito's flow to every
hex inside it.

S2 ``od_silver`` — bronze viajes cleaned + typed + enriched with
``weekday`` / ``is_weekend`` and a ``support_n`` trip-count, NA edad/sexo
preserved as their own segment (never dropped), viajes left as-is (already
population-expanded).

EPSG TRAP (verified from zonificacion_distritos.prj): MITMA zone geometry is
**EPSG:25830** (ETRS89 UTM 30N). We ST_Transform 25830 -> 25831 (UTM 31N,
correct for Catalonia) BEFORE any ST_Area, else every area_weight is corrupt.
The hex polygons come from ST_H3ToGeom in EPSG:4326 and are likewise
reprojected to 25831 before the area math.
"""
from __future__ import annotations

from pyspark.sql import functions as F

# Source CRS of the MITMA distrito shapefile / geojson geometry.
MITMA_GEOM_SRID = 25830
# Metric CRS for area math over Catalonia (UTM 31N).
METRIC_SRID = 25831
H3_RES = 8


# ---------------------------------------------------------------------------
# Shared GeoJSON reader
# ---------------------------------------------------------------------------

def read_zones_geojson(spark, path: str, *, id_field: str = "ID"):
    """Read a MITMA zoning GeoJSON into ``(zone_id, geom_ll)`` rows.

    Sedona's ``geojson`` reader returns the whole FeatureCollection as ONE row
    with a ``features`` array — we explode it to one row per feature and pull
    the id property + geometry (EPSG:4326 / CRS84 lon-lat).
    """
    fc = spark.read.format("geojson").option("multiLine", "true").load(path)
    return fc.select(F.explode("features").alias("_f")).select(
        F.col(f"_f.properties.{id_field}").alias("zone_id"),
        F.col("_f.geometry").alias("geom_ll"),
    )


# ---------------------------------------------------------------------------
# S1 — area-weighted zone -> H3 crosswalk
# ---------------------------------------------------------------------------

def build_zone_h3_xwalk(
    spark,
    zones_geojson_path: str,
    *,
    zoning: str = "distritos",
    catalonia_prefixes: tuple[str, ...] = ("08", "17", "25", "43"),
    id_field: str = "ID",
):
    """Build the dasymetric zone->H3 res-8 crosswalk for one zoning.

    Pipeline (all in Sedona SQL, distributed):

    1. Read zone polygons (GeoJSON is CRS84/lon-lat) and tag each with its
       source-SRID geometry set to EPSG:25830 via ``ST_SetSRID`` (the geojson
       is degrees but represents 25830-projected MITMA zones; we set the SRID
       so ST_Transform reprojects *from* 25830 — see module docstring).
    2. Filter to Catalonia by zone-id prefix.
    3. Explode an H3 res-8 fullCover grid off each zone with
       ``ST_H3CellIDs(geom, 8, true)`` -> distinct cell ids -> ``ST_H3ToGeom``
       hex polygons (EPSG:4326).
    4. Reproject BOTH hex (4326) and zone (25830) to 25831, then
       ``area_weight = ST_Area(ST_Intersection(hex, zone)) / NULLIF(ST_Area(zone),0)``.
       Join driven by ``ST_Intersects(hex, zone)`` with a BROADCAST hint on
       the small zoning side.

    Returns a DataFrame ``(zoning, zone_id, h3_id, area_weight)``.
    """
    # NOTE on CRS: the GeoJSON coordinates are lon/lat (CRS84). The authoritative
    # MITMA zone polygons are EPSG:25830. We read the geometry, treat it as
    # geographic 4326 for the H3 grid (H3 is defined on the sphere / lon-lat),
    # and for the AREA computation we reproject the lon-lat geometry to the
    # metric 25831 via 4326->25831 (geographically equivalent to 25830->25831
    # for these polygons; both land the metres in UTM 31N). Using 4326 as the
    # source for ST_Transform on the lon-lat geojson is the correct, lossless
    # path — the 25830 caveat matters when reading the .shp in projected metres.
    zones = read_zones_geojson(spark, zones_geojson_path, id_field=id_field).where(
        "substring(zone_id,1,2) IN ("
        + ",".join(f"'{p}'" for p in catalonia_prefixes)
        + ")"
    )
    # ST_MakeValid up front: some MITMA zone polygons self-touch / have slivers
    # that throw JTS "side location conflict" even in the ST_Intersects predicate.
    zones = zones.selectExpr("zone_id", "ST_MakeValid(geom_ll) AS geom_ll")
    zones.createOrReplaceTempView("_xw_zones")

    # Hex grid: explode fullCover cell ids per zone, dedupe, materialise polys.
    spark.sql(
        f"""
        SELECT DISTINCT cell_id AS h3_long
        FROM _xw_zones
        LATERAL VIEW EXPLODE(ST_H3CellIDs(geom_ll, {H3_RES}, true)) t AS cell_id
        """
    ).createOrReplaceTempView("_xw_cells")

    spark.sql(
        """
        SELECT h3_long,
               LOWER(HEX(h3_long))            AS h3_id,
               ST_H3ToGeom(ARRAY(h3_long))[0] AS hex_ll
        FROM _xw_cells
        """
    ).createOrReplaceTempView("_xw_hexes")

    # Area-weighted intersection in metric CRS. BROADCAST the small zone side.
    # ST_MakeValid guards against MITMA self-touching/sliver polygons that throw
    # JTS "side location conflict" TopologyExceptions inside ST_Intersection.
    xwalk = spark.sql(
        f"""
        SELECT /*+ BROADCAST(z) */
               h.h3_id,
               z.zone_id,
               ST_Area(
                 ST_Intersection(
                   ST_MakeValid(ST_Transform(h.hex_ll,  'EPSG:4326', 'EPSG:{METRIC_SRID}')),
                   ST_MakeValid(ST_Transform(z.geom_ll, 'EPSG:4326', 'EPSG:{METRIC_SRID}'))
                 )
               ) AS inter_area_m2,
               ST_Area(ST_MakeValid(ST_Transform(z.geom_ll, 'EPSG:4326', 'EPSG:{METRIC_SRID}'))) AS zone_area_m2
        FROM _xw_hexes h
        JOIN _xw_zones z
          ON ST_Intersects(h.hex_ll, z.geom_ll)
        """
    )
    xwalk = (
        xwalk.where("inter_area_m2 > 0")
        .withColumn(
            "area_weight",
            F.col("inter_area_m2") / F.when(F.col("zone_area_m2") > 0, F.col("zone_area_m2")),
        )
        .withColumn("zoning", F.lit(zoning))
        .select("zoning", "zone_id", "h3_id", "area_weight")
    )
    return xwalk


def hex_centroids(spark, xwalk_df):
    """Per-hex lon/lat centroids from the distinct h3 ids in a crosswalk.

    Cheap helper so gold can attach ``lon_centroid/lat_centroid`` without
    re-reading the boundary. Uses ST_H3ToGeom on the (re-derived) cell id.
    """
    xwalk_df.select("h3_id").distinct().createOrReplaceTempView("_hc_ids")
    return spark.sql(
        """
        SELECT h3_id,
               ST_X(ST_Centroid(ST_H3ToGeom(ARRAY(CAST(CONV(h3_id,16,10) AS BIGINT)))[0])) AS lon_centroid,
               ST_Y(ST_Centroid(ST_H3ToGeom(ARRAY(CAST(CONV(h3_id,16,10) AS BIGINT)))[0])) AS lat_centroid
        FROM _hc_ids
        """
    )


# ---------------------------------------------------------------------------
# S2 — cleaned OD silver (one hourly-grained table)
# ---------------------------------------------------------------------------

def build_od_silver(bronze_df):
    """Clean + type + enrich the bronze viajes frame into od_silver.

    Adds:
      * ``weekday`` (1=Sun..7=Sat per Spark ``dayofweek``) and ``is_weekend``.
      * ``support_n`` = 1 per OD-segment row. Downstream SUM gives the
        OD-SEGMENT ROW COUNT behind each aggregate — a coarse density /
        confidence proxy (more rows ≈ denser, more reliable aggregate). It is
        NOT the MITMA <100-device privacy gate: that suppression is applied by
        MITMA *before* publication, so the privacy floor is INVISIBLE in the
        expanded open data and cannot be recovered here.
    Keeps NA edad/sexo/renta as the literal string 'NA' (never dropped).
    Never re-expands viajes (already population-expanded).
    """
    df = (
        bronze_df.withColumn("fecha_date", F.to_date("fecha", "yyyyMMdd"))
        .withColumn("weekday", F.dayofweek(F.col("fecha_date")))  # 1=Sun..7=Sat
        .withColumn("is_weekend", F.col("weekday").isin(1, 7))
        # Preserve NA segments explicitly.
        .withColumn("renta", F.coalesce(F.col("renta"), F.lit("NA")))
        .withColumn("edad", F.coalesce(F.col("edad"), F.lit("NA")))
        .withColumn("sexo", F.coalesce(F.col("sexo"), F.lit("NA")))
        .withColumn("support_n", F.lit(1))
    )
    return df


# ---------------------------------------------------------------------------
# S2b — TRUE daily rollup (the derived 'daily', SUM over periodo)
# ---------------------------------------------------------------------------

def daily_rollup(od_silver_df):
    """Sum hourly OD over ``periodo`` to a true daily OD per (fecha, O, D, segs).

    This is what 'daily' MEANS — a rollup, not a duplicate ingest. Keeps the
    segment dimensions so downstream geodemographic shares stay available.
    """
    return od_silver_df.groupBy(
        "fecha", "fecha_date", "weekday", "is_weekend",
        "origen", "destino", "distancia",
        "actividad_origen", "actividad_destino",
        "renta", "edad", "sexo",
    ).agg(
        F.sum("viajes").alias("viajes"),
        F.sum("viajes_km").alias("viajes_km"),
        F.sum("support_n").alias("support_n"),
    )
