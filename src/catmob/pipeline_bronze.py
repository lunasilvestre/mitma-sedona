"""BRONZE layer — raw MITMA viajes CSV.gz -> partitioned parquet (Sedona/Spark).

This is the **only** place the pipe-delimited 15-column ``.csv.gz`` is ever
touched. We write columnar parquet partitioned by
``zoning={distritos,municipios,gau} / kind={viajes,...} / fecha=YYYYMMDD``
with ``periodo`` (hour-of-day 0-23) kept as a COLUMN — partitioning on periodo
would explode 24x into tiny files.

CRITICAL FIX baked in: the historical ``data/bronze/mitma/daily`` directory was
a mislabeled byte-copy of the hourly files. There is exactly ONE hourly-grained
bronze table here; the *daily* view is a derived silver rollup
(``pipeline_silver.daily_rollup``), never a duplicate ingest.

Runs distributed: Spark reads the gz files in parallel, applies the
Catalonia origin/destination prefix filter at the SQL level (predicate
pushdown), and coalesces to ~128-256MB output files.
"""
from __future__ import annotations

from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, StringType, StructField, StructType

CATALONIA_PREFIXES = ("08", "17", "25", "43")

# The MITMA v2 distritos "viajes" on-disk schema (pipe-delimited, header row).
VIAJES_SCHEMA = StructType(
    [
        StructField("fecha", StringType(), False),
        StructField("periodo", StringType(), False),
        StructField("origen", StringType(), False),
        StructField("destino", StringType(), False),
        StructField("distancia", StringType(), False),
        StructField("actividad_origen", StringType(), False),
        StructField("actividad_destino", StringType(), False),
        StructField("estudio_origen_posible", StringType(), True),
        StructField("estudio_destino_posible", StringType(), True),
        StructField("residencia", StringType(), True),
        StructField("renta", StringType(), True),
        StructField("edad", StringType(), True),
        StructField("sexo", StringType(), True),
        StructField("viajes", DoubleType(), False),
        StructField("viajes_km", DoubleType(), False),
    ]
)


def read_viajes_csv(spark, paths, *, catalonia_only: bool = True):
    """Read one or more pipe-delimited MITMA viajes ``.csv.gz`` files.

    Parameters
    ----------
    spark
        SedonaContext-bound SparkSession (from :func:`catmob.spark.get_sedona`).
    paths
        Glob string or list of paths to ``*_Viajes_*.csv.gz``.
    catalonia_only
        Keep rows whose origin OR destination distrito prefix is in Catalonia
        (08/17/25/43). Applied as a pushed-down SQL predicate.

    Returns
    -------
    pyspark.sql.DataFrame
        Typed, with ``periodo`` cast to int and the row preserved as-is
        (viajes are population-expanded already — never re-expand).
    """
    df = (
        spark.read.option("sep", "|")
        .option("encoding", "UTF-8")
        .option("header", "true")
        .schema(VIAJES_SCHEMA)
        .csv(paths)
    )
    if catalonia_only:
        df = df.where(
            "substring(origen,1,2) IN ('08','17','25','43') "
            "OR substring(destino,1,2) IN ('08','17','25','43')"
        )
    # periodo is an integer hour-of-day; keep it numeric for window/peak math.
    df = df.withColumn("periodo", F.col("periodo").cast("int"))
    return df


def write_bronze(
    df,
    out_root: str,
    *,
    zoning: str = "distritos",
    kind: str = "viajes",
    target_file_mb: int = 192,
):
    """Write the typed viajes frame to the bronze lakehouse.

    Layout: ``{out_root}/zoning={zoning}/kind={kind}/fecha=YYYYMMDD/*.parquet``.
    ``periodo`` stays a column. We add the partition columns explicitly and
    let Spark's ``partitionBy`` handle the directory layout, coalescing so
    each ``fecha`` lands in a small number of ~128-256MB files rather than
    hundreds of tiny tasks-worth.
    """
    df = df.withColumn("zoning", F.lit(zoning)).withColumn("kind", F.lit(kind))
    # Coalesce per the rough on-disk size. The Catalonia daily slice is a few
    # hundred MB of CSV -> ~30-60MB parquet/day, so 1-2 files/day is right.
    n_days = df.select("fecha").distinct().count()
    df = df.repartition(max(1, n_days), "fecha")
    (
        df.write.mode("overwrite")
        .partitionBy("zoning", "kind", "fecha")
        .parquet(out_root.rstrip("/"))
    )
    return out_root


def ingest(
    spark,
    paths,
    out_root: str,
    *,
    zoning: str = "distritos",
    kind: str = "viajes",
    catalonia_only: bool = True,
):
    """End-to-end bronze ingest: read CSV.gz -> typed -> partitioned parquet."""
    df = read_viajes_csv(spark, paths, catalonia_only=catalonia_only)
    write_bronze(df, out_root, zoning=zoning, kind=kind)
    return df


def read_bronze(
    spark,
    out_root: str,
    *,
    zoning: str = "distritos",
    kind: str = "viajes",
    fecha_start: str | None = None,
    fecha_end: str | None = None,
    catalonia_only: bool = True,
):
    """Read the bronze lakehouse partition-PRUNED to a Catalonia-touching window.

    This is the SCALE-FILTER read path (review item 7): at full scale the bronze
    lakehouse is the all-Spain dump, and reading it whole OOMs a single JVM. We
    therefore push BOTH filters down to the scan so only the working set is
    materialised — GB, not TB:

      * ``fecha`` PARTITION PRUNING — only the ``[fecha_start, fecha_end]`` day
        partitions are touched (the ``fecha=YYYYMMDD`` directories outside the
        window are never opened). Pass an 8-char ``YYYYMMDD`` window.
      * Catalonia-touching ROW FILTER — ``origen`` OR ``destino`` distrito
        prefix in 08/17/25/43, a pushed-down predicate.
      * ``zoning`` / ``kind`` partition filters select the right sub-table.

    On the 7-day sample the window spans the whole table (no-op pruning), but the
    SAME code on atlas reads only the requested days of the all-Spain lakehouse.
    """
    df = (
        spark.read.parquet(out_root.rstrip("/"))
        .where(F.col("zoning") == zoning)
        .where(F.col("kind") == kind)
    )
    if fecha_start is not None:
        df = df.where(F.col("fecha") >= fecha_start)
    if fecha_end is not None:
        df = df.where(F.col("fecha") <= fecha_end)
    if catalonia_only:
        df = df.where(
            "substring(origen,1,2) IN ('08','17','25','43') "
            "OR substring(destino,1,2) IN ('08','17','25','43')"
        )
    df = df.withColumn("periodo", F.col("periodo").cast("int"))
    return df
