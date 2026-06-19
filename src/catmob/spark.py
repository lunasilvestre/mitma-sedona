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
sample (local[*]). The indexed R-tree BroadcastIndexJoin works on this pair
once pyspark's duplicate ``jts-core`` is parked so the shaded jar's JTS is the
only copy on the classpath — pass ``enable_rtree=True`` (see
:func:`_isolate_shaded_jts`). Without that, the safe non-indexed RangeJoin is
used (``optimizationmode='none'``), which is correct and fast at sample scale.

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


def _pyspark_jts_jars() -> list[Path]:
    """Locate pyspark's bundled ``jts-core`` jar(s)."""
    try:
        import pyspark

        return [Path(p) for p in glob.glob(
            str(Path(pyspark.__file__).parent / "jars" / "jts-core-*.jar")
        )]
    except Exception:
        return []


# Sentinel suffix for the parked pyspark jts jar (see _isolate_shaded_jts).
_JTS_PARKED_SUFFIX = ".disabled_for_sedona_rtree"


def _isolate_shaded_jts() -> list[tuple[Path, Path]]:
    """Make the shaded Sedona jar's JTS the ONLY ``jts-core`` on the classpath.

    THE proven fix for the R-tree ``BroadcastIndexJoin`` (review item 5).

    Root cause (diagnosed on Spark 4.1.1 + sedona-spark-shaded-4.0_2.13:1.9.0):
    the shaded Sedona jar bundles JTS *un-relocated* at ``org.locationtech.jts``
    and is loaded by the Spark ``MutableURLClassLoader`` (from ``--packages``),
    while pyspark ALSO ships ``jts-core-1.20.0.jar`` on the ``app`` loader. When
    Sedona serialises a broadcast R-tree, its ``IndexSerde`` (shaded loader)
    calls the *package-private* ``AbstractSTRtree.getItemBoundables()`` on an
    instance resolved from the *other* loader -> ``IllegalAccessError`` and the
    indexed join dies. (Verified dead ends: pinning the pyspark jar onto
    ``extraClassPath`` keeps two loaders; ``userClassPathFirst`` breaks Sedona
    init; the unshaded artifact hits the Spark-4.0-vs-4.1 ``FoldableUnevaluable``
    skew.)

    Parking pyspark's ``jts-core`` jar (renaming it aside) leaves the shaded
    jar's JTS as the single copy, so ``IndexSerde`` and ``AbstractSTRtree``
    resolve from one loader and the ``BroadcastIndexJoin`` (``SpatialIndex
    RTREE``) executes. This MUST run before the JVM launches (pyspark globs
    ``jars/*.jar`` at gateway start). Returns the list of ``(parked, original)``
    paths so the caller / atexit can restore them.
    """
    moved: list[tuple[Path, Path]] = []
    for jar in _pyspark_jts_jars():
        parked = jar.with_name(jar.name + _JTS_PARKED_SUFFIX)
        try:
            jar.rename(parked)
            moved.append((parked, jar))
        except OSError:
            # Read-only install or already parked — leave it; restore what we did.
            continue
    if moved:
        import atexit

        def _restore():
            for parked, original in moved:
                if parked.exists() and not original.exists():
                    try:
                        parked.rename(original)
                    except OSError:
                        pass

        atexit.register(_restore)
    return moved


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
    enable_rtree: bool = False,
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
    enable_rtree
        If True, isolate the shaded Sedona jar's JTS (park pyspark's
        ``jts-core``) and set ``sedona.join.optimizationmode='all'`` so the
        indexed ``BroadcastIndexJoin`` (R-tree) is used and actually executes on
        this Spark/Sedona pair. Default False keeps the safe non-indexed
        ``RangeJoin`` (``optimizationmode='none'``) which is correct and fast at
        sample scale. See :func:`_isolate_shaded_jts` for the root cause.
    extra_conf
        Any additional ``spark.*`` / ``sedona.*`` keys to set.

    Returns
    -------
    pyspark.sql.SparkSession
        With Sedona's ST_/RS_/H3 functions registered. Call ``.stop()`` when
        done (or use :func:`sedona_session`).
    """
    _ensure_jvm_env(java_home)

    # R-tree path: park pyspark's duplicate jts-core BEFORE the JVM launches so
    # the shaded Sedona jar's JTS is the only copy on the classpath (the proven
    # fix for the IndexSerde IllegalAccessError — see _isolate_shaded_jts).
    if enable_rtree:
        _isolate_shaded_jts()

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
        # optimizationmode: "all" enables the indexed BroadcastIndexJoin (R-tree);
        # "none" falls back to the non-indexed RangeJoin (still pushes
        # ST_Intersects down, correct, fast on the small 584-zone broadcast side).
        # On THIS build (Spark 4.1.1 + sedona-shaded-4.0) the R-tree IndexSerde
        # throws IllegalAccessError UNLESS pyspark's duplicate jts-core is parked
        # (enable_rtree=True does this — _isolate_shaded_jts). So we only switch
        # to "all" when the caller has asked for (and we have isolated) the
        # R-tree path; otherwise we stay on the safe "none" mode.
        .config("sedona.join.optimizationmode", "all" if enable_rtree else "none")
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
