"""Reusable Sedona/Spark session factory for the MITMA deep-Spark pipeline.

Every script in the bronze->silver->gold lakehouse imports
:func:`get_sedona` so the JVM environment, the two pinned Maven coordinates,
and the AQE/broadcast tuning live in exactly ONE place — no
"did-you-activate-the-env" gotcha, no copy-pasted ``spark.jars.packages``
string drifting between notebooks and scripts.

Pinned coordinates (the SEDONA_PACKAGES already proven in notebook 01):

    org.apache.sedona:sedona-spark-shaded-4.0_2.13:1.9.0
    org.datasyslab:geotools-wrapper:1.9.0-33.5

Sedona's "4.0" shaded artifact is also the right pick for Spark 4.1.x on the
sample (local[*]); at full scale on *atlas* pin a matched Spark/Sedona pair
to kill the FoldableUnevaluable version skew (see docs/why_spark_sedona.md
STAGE 0).

Run with the sedona env python::

    /home/nls/miniforge3/envs/sedona/bin/python -c \
        "from catmob.spark import get_sedona; s=get_sedona(); print(s.version); s.stop()"
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

# The two Maven coordinates proven in notebooks/01_data_ingest.py.
SEDONA_PACKAGES = (
    "org.apache.sedona:sedona-spark-shaded-4.0_2.13:1.9.0,"
    "org.datasyslab:geotools-wrapper:1.9.0-33.5"
)

# Conda env JVM that ships with the sedona env (Java 21).
DEFAULT_JAVA_HOME = "/home/nls/miniforge3/envs/sedona/lib/jvm"


def _pyspark_jts_jar() -> str | None:
    """Locate pyspark's bundled ``jts-core`` jar.

    CLASSLOADER FIX: the sedona-spark-shaded jar bundles its own (byte-identical
    JTS 1.20.0) copy of ``org.locationtech.jts`` loaded by the Spark
    MutableURLClassLoader (from ``--packages``), while pyspark ships the same
    JTS on the app/system loader. Sedona's R-tree ``IndexSerde`` (shaded loader)
    then tries to call the package-private ``AbstractSTRtree.getItemBoundables()``
    on an instance from the *other* loader -> ``IllegalAccessError`` during the
    broadcast spatial-index serialize. Pinning the pyspark jts-core jar onto
    ``spark.{driver,executor}.extraClassPath`` (the SYSTEM classpath, parent to
    the MutableURLClassLoader) makes both classes resolve from one loader and
    removes the split.
    """
    try:
        import pyspark

        jars = glob.glob(str(Path(pyspark.__file__).parent / "jars" / "jts-core-*.jar"))
        return jars[0] if jars else None
    except Exception:
        return None


def _ensure_jvm_env(java_home: str | None = None) -> None:
    """Set JAVA_HOME + SPARK_LOCAL_IP before the JVM is launched.

    Idempotent: only fills a variable if it is unset, so an operator who has
    already exported a different JDK is respected.
    """
    jh = java_home or os.environ.get("JAVA_HOME") or DEFAULT_JAVA_HOME
    if Path(jh).exists():
        os.environ["JAVA_HOME"] = jh
    # Pin the driver bind address — avoids the "unable to bind to a loopback"
    # flake on machines with an exotic /etc/hosts (the sample box included).
    os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
    # Keep Arrow on for the GeoArrow handoff and toPandas paths.
    os.environ.setdefault("PYARROW_IGNORE_TIMEZONE", "1")


def get_sedona(
    app_name: str = "mitma-deep-spark",
    *,
    master: str = "local[*]",
    java_home: str | None = None,
    driver_memory: str = "6g",
    shuffle_partitions: int | None = None,
    extra_conf: dict[str, str] | None = None,
):
    """Build (or fetch) a SedonaContext-bound SparkSession.

    Parameters
    ----------
    app_name
        Spark application name (shows up in the UI / logs).
    master
        ``local[*]`` on the sample box. On *atlas* pass the cluster master /
        leave Spark to read ``spark-defaults.conf``.
    java_home
        Override the JDK; defaults to the conda env JVM.
    driver_memory
        Local-mode driver heap. Bump for the area-weighted crosswalk shuffle.
    shuffle_partitions
        If given, pins ``spark.sql.shuffle.partitions`` (AQE coalesces down
        from here). ``None`` leaves the Spark default (200) so AQE governs.
    extra_conf
        Any additional ``spark.*`` / ``sedona.*`` keys to set.

    Returns
    -------
    pyspark.sql.SparkSession
        With Sedona's ST_/RS_/H3 functions registered. Call ``.stop()`` when
        done (or use :func:`sedona_session`).
    """
    _ensure_jvm_env(java_home)

    # Import here (not at module top) so that merely importing this module in a
    # JVM-less context (e.g. doc build) does not require pyspark.
    from sedona.spark import SedonaContext

    builder = (
        SedonaContext.builder()
        .appName(app_name)
        .master(master)
        .config("spark.jars.packages", SEDONA_PACKAGES)
        .config("spark.driver.memory", driver_memory)
    )
    builder = (
        builder
        # AQE on — coalesces tiny shuffle partitions after the crosswalk join.
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        # Sedona spatial-join tuning (docs/sedona_sql_patterns.md §6).
        # optimizationmode="none" on THIS build (Spark 4.1.1 + sedona-shaded-4.0):
        # the indexed BroadcastIndexJoin serializes a JTS R-tree via Sedona's
        # IndexSerde, which on this version-skewed pair throws an
        # IllegalAccessError (IndexSerde [shaded loader] vs AbstractSTRtree
        # [pyspark loader] split — getItemBoundables() is package-private). The
        # non-indexed RangeJoin still pushes ST_Intersects down and is correct;
        # the small (584-zone) broadcast side keeps it fast at sample scale.
        # On *atlas* with a matched Spark/Sedona pair (STAGE 0) flip this back to
        # "all" to restore the R-tree-indexed join for billions of OD rows.
        .config("sedona.join.optimizationmode", "none")
        .config("sedona.join.autoBroadcastJoinThreshold", "100MB")
        .config("sedona.global.indextype", "rtree")
        .config("spark.sql.autoBroadcastJoinThreshold", "50MB")
        # Keep the local UI quiet; flip on for debugging.
        .config("spark.ui.showConsoleProgress", "false")
        # Arrow for toPandas / GeoArrow handoff.
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        # Parquet niceties.
        .config("spark.sql.parquet.compression.codec", "snappy")
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
    )
    if shuffle_partitions is not None:
        builder = builder.config("spark.sql.shuffle.partitions", str(shuffle_partitions))
    for k, v in (extra_conf or {}).items():
        builder = builder.config(k, v)

    config = builder.getOrCreate()
    sedona = SedonaContext.create(config)
    return sedona


class sedona_session:  # noqa: N801 - context-manager naming
    """Context manager: ``with sedona_session() as s: ...`` (auto-stop)."""

    def __init__(self, *args, **kwargs):
        self._args = args
        self._kwargs = kwargs
        self.spark = None

    def __enter__(self):
        self.spark = get_sedona(*self._args, **self._kwargs)
        return self.spark

    def __exit__(self, exc_type, exc, tb):
        if self.spark is not None:
            self.spark.stop()
        return False
