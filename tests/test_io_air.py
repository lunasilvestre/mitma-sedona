"""Tests for ``catmob.io_air`` — air-quality station parsing.

The XVPCA CSV uses Spanish/Catalan column names; the parser must rename and
validate against ``AIR_QUALITY_STATION_SCHEMA``.
"""
from __future__ import annotations

from pathlib import Path

from catmob import io_air


def test_parse_xvpca_renames_to_unified_schema(air_quality_station_fixture: Path) -> None:
    df = io_air.parse_xvpca_csv(air_quality_station_fixture)
    expected = {
        "station_id",
        "station_name",
        "operator",
        "lon",
        "lat",
        "year",
        "no2_annual_ugm3",
        "pm25_annual_ugm3",
        "pm10_annual_ugm3",
        "o3_8h_max_ugm3",
    }
    assert expected.issubset(df.columns)


def test_parse_xvpca_marks_operator(air_quality_station_fixture: Path) -> None:
    df = io_air.parse_xvpca_csv(air_quality_station_fixture)
    assert (df["operator"] == "XVPCA").all()


def test_parse_xvpca_coordinates_inside_catalonia(air_quality_station_fixture: Path) -> None:
    df = io_air.parse_xvpca_csv(air_quality_station_fixture)
    assert df["lon"].between(0.0, 4.0).all()
    assert df["lat"].between(40.5, 42.9).all()


def test_no2_values_are_nonneg_floats(air_quality_station_fixture: Path) -> None:
    df = io_air.parse_xvpca_csv(air_quality_station_fixture)
    assert (df["no2_annual_ugm3"] >= 0).all()
