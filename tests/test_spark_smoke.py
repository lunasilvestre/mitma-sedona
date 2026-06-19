"""W0 smoke test for the Sedona session factory (catmob.spark.get_sedona).

Boots a local SedonaContext, runs the load-bearing primitives the pipeline
depends on (ST_H3CellIDs fullCover, a tiny ST_Contains spatial join, the
25830->25831 ST_Transform), and stops cleanly. Skips (does not fail) when
pyspark/sedona/JDK are not importable, so the pure-python CI stays green.

Run explicitly with the sedona env::

    /home/nls/miniforge3/envs/sedona/bin/python -m pytest tests/test_spark_smoke.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

sedona_spark = pytest.importorskip("sedona.spark", reason="sedona not installed")


@pytest.fixture(scope="module")
def spark():
    from catmob.spark import get_sedona

    try:
        s = get_sedona(app_name="catmob-smoke", driver_memory="2g")
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"could not start SedonaContext: {exc}")
    yield s
    s.stop()


def test_h3_cellids_fullcover(spark):
    """ST_H3CellIDs(fullCover=true) tiles a small BCN box at res 8."""
    n = spark.sql(
        "SELECT COUNT(*) c FROM ("
        "  SELECT explode(ST_H3CellIDs("
        "    ST_GeomFromWKT('POLYGON((2.0 41.3, 2.3 41.3, 2.3 41.5, 2.0 41.5, 2.0 41.3))'),"
        "    8, true)) AS h3)"
    ).collect()[0]["c"]
    assert n > 500  # ~935 cells over the box


def test_contains_spatial_join(spark):
    """A tiny ST_Contains join keeps only the inside point."""
    spark.sql(
        "SELECT ST_GeomFromWKT('POLYGON((0 0,10 0,10 10,0 10,0 0))') AS poly, 'A' AS name"
    ).createOrReplaceTempView("polys")
    spark.sql(
        "SELECT ST_GeomFromWKT('POINT(5 5)') AS pt, 'inside' AS lbl "
        "UNION ALL SELECT ST_GeomFromWKT('POINT(20 20)') AS pt, 'outside' AS lbl"
    ).createOrReplaceTempView("pts")
    hits = spark.sql(
        "SELECT pt.lbl FROM polys p JOIN pts pt ON ST_Contains(p.poly, pt.pt)"
    ).collect()
    assert [r["lbl"] for r in hits] == ["inside"]


def test_transform_25830_to_25831(spark):
    """ST_Transform 25830->25831 runs (the EPSG trap reprojection)."""
    x = spark.sql(
        "SELECT ST_X(ST_Transform("
        "  ST_SetSRID(ST_Point(430000.0, 4580000.0), 25830),"
        "  'EPSG:25830', 'EPSG:25831')) AS x"
    ).collect()[0]["x"]
    assert isinstance(x, float)
