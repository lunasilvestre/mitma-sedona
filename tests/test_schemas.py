"""Smoke-tests for every pandera schema in ``catmob.schemas``.

Goal: ensure the schemas themselves are well-formed (column types, checks
parse), and that the registry is complete. These are extremely fast (<100 ms
total) and run on every CI build.
"""
from __future__ import annotations

import pandera.pandas as pa
import pytest

from catmob import schemas


def test_registry_is_non_empty() -> None:
    assert len(schemas.SCHEMA_REGISTRY) >= 10
    for name, schema in schemas.SCHEMA_REGISTRY.items():
        assert isinstance(schema, pa.DataFrameSchema), f"{name} not a DataFrameSchema"


@pytest.mark.parametrize("name", list(schemas.SCHEMA_REGISTRY.keys()))
def test_schema_has_at_least_one_column(name: str) -> None:
    schema = schemas.SCHEMA_REGISTRY[name]
    assert len(schema.columns) > 0, f"{name} has no columns defined"


def test_mitma_hourly_extends_daily() -> None:
    """The hourly schema must contain every column the daily schema has."""
    daily_cols = set(schemas.MITMA_DAILY_OD_SCHEMA.columns.keys())
    hourly_cols = set(schemas.MITMA_HOURLY_OD_SCHEMA.columns.keys())
    missing = daily_cols - hourly_cols
    assert not missing, f"Hourly schema missing columns from daily: {missing}"
    assert "periodo" in hourly_cols


def test_gold_hex_columns_cover_all_dimensions() -> None:
    """The gold layer must expose every dimension the score function reads."""
    cols = set(schemas.GOLD_HEX_SCHEMA.columns.keys())
    required_dimensions = {
        # Mobility / accessibility
        "train_reach_min", "trains_to_bcn_nearest",
        # Amenities (lifestyle)
        "climb_min_m", "yoga_min_m",
        # Nature
        "green_min_m", "sea_min_m", "tree_cover_pct", "natura2000_within_5km",
        # Penalties
        "industry_density_per_km2", "motorway_within_500m", "eprtr_facility_min_m",
        # Air
        "no2_ugm3", "pm25_ugm3",
        # Heat
        "uhi_delta_c", "lst_summer_median_c",
        # Light
        "viirs_radiance",
        # Health
        "hospital_min_m", "pharmacy_density_per_km2",
        # Mobility vibe
        "mitma_inflow_daily", "mitma_outflow_daily",
        # Final
        "liveability_score",
    }
    missing = required_dimensions - cols
    assert not missing, f"Gold schema missing dimension columns: {missing}"


def test_air_quality_station_requires_lat_lon_year() -> None:
    schema = schemas.AIR_QUALITY_STATION_SCHEMA
    for col in ("station_id", "operator", "lon", "lat", "year"):
        assert col in schema.columns, f"missing required column {col}"


def test_protected_area_schema_has_geometry() -> None:
    assert "geometry" in schemas.PROTECTED_AREA_SCHEMA.columns
