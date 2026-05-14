"""Shared pytest fixtures for the data-loading test suite.

Fixtures keep tests dependency-light: small CSVs / dicts under
``tests/fixtures/`` exercise the parsing & validation paths without
needing network, Spark, or large downloads.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def mitma_daily_fixture(fixtures_dir: Path) -> Path:
    return fixtures_dir / "mitma_daily_sample.csv.gz"


@pytest.fixture(scope="session")
def mitma_hourly_fixture(fixtures_dir: Path) -> Path:
    return fixtures_dir / "mitma_hourly_sample.csv.gz"


@pytest.fixture(scope="session")
def mitma_zones_fixture(fixtures_dir: Path) -> Path:
    return fixtures_dir / "mitma_zones_sample.geojson"


@pytest.fixture(scope="session")
def osm_poi_fixture(fixtures_dir: Path) -> pd.DataFrame:
    """Tiny POI table including positive matches for every category rule."""
    return pd.DataFrame(
        [
            # Climbing gym (Sharma Climbing BCN, real-ish coords)
            {
                "osm_id": 100001,
                "osm_type": "node",
                "lon": 2.197,
                "lat": 41.404,
                "tags": {"sport": "climbing", "name": "Sharma Climbing BCN"},
            },
            # Yoga studio
            {
                "osm_id": 100002,
                "osm_type": "node",
                "lon": 2.158,
                "lat": 41.401,
                "tags": {"sport": "yoga", "name": "Yoga Sala"},
            },
            # Hospital
            {
                "osm_id": 100003,
                "osm_type": "way",
                "lon": 2.150,
                "lat": 41.418,
                "tags": {"amenity": "hospital", "name": "Hospital Clínic"},
            },
            # Pharmacy
            {
                "osm_id": 100004,
                "osm_type": "node",
                "lon": 2.165,
                "lat": 41.388,
                "tags": {"amenity": "pharmacy", "name": "Farmàcia"},
            },
            # Industrial landuse
            {
                "osm_id": 100005,
                "osm_type": "way",
                "lon": 2.244,
                "lat": 41.351,
                "tags": {"landuse": "industrial"},
            },
            # Unmatched POI — should be dropped by categoriser
            {
                "osm_id": 100006,
                "osm_type": "node",
                "lon": 2.170,
                "lat": 41.380,
                "tags": {"shop": "bakery"},
            },
        ]
    )


@pytest.fixture(scope="session")
def air_quality_station_fixture(fixtures_dir: Path) -> Path:
    return fixtures_dir / "xvpca_stations_sample.csv"
