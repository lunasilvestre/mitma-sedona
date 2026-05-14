"""Tests for ``catmob.io_osm`` — POI categorisation rules.

Sedona PBF reading is integration-level and lives under M2; here we exercise
the pure-Python categoriser used both by the test suite and by the Sedona
post-processing step (which receives a Pandas DataFrame after a
``.toPandas()`` collect on the filtered POI subset).
"""
from __future__ import annotations

import pandas as pd
import pytest

from catmob import io_osm


def test_categoriser_keeps_only_matching_pois(osm_poi_fixture: pd.DataFrame) -> None:
    out = io_osm.categorise_pois(osm_poi_fixture)
    # The fixture has 5 matched and 1 unmatched POI.
    assert len(out) == 5
    assert "shop" not in out["category"].unique()


def test_categoriser_assigns_correct_categories(osm_poi_fixture: pd.DataFrame) -> None:
    out = io_osm.categorise_pois(osm_poi_fixture)
    cat_for_id = dict(zip(out["osm_id"], out["category"]))
    assert cat_for_id[100001] == "climbing"
    assert cat_for_id[100002] == "yoga"
    assert cat_for_id[100003] == "hospital"
    assert cat_for_id[100004] == "pharmacy"
    assert cat_for_id[100005] == "industry"


def test_categoriser_can_be_restricted(osm_poi_fixture: pd.DataFrame) -> None:
    out = io_osm.categorise_pois(osm_poi_fixture, categories=["climbing", "yoga"])
    assert set(out["category"].unique()) == {"climbing", "yoga"}
    assert len(out) == 2


def test_osmium_tag_filter_covers_critical_keys() -> None:
    """The pre-prune spec must cover every category we later depend on."""
    spec = " ".join(io_osm.OSMIUM_TAG_FILTER)
    for needle in (
        "highway=motorway",
        "railway=rail",
        "amenity=hospital",
        "amenity=pharmacy",
        "sport=climbing",
        "sport=yoga",
        "leisure=park",
        "natural=coastline",
        "landuse=industrial",
    ):
        assert needle in spec, f"OSMIUM_TAG_FILTER missing {needle!r}"


def test_categoriser_handles_empty_input() -> None:
    empty = pd.DataFrame(columns=["osm_id", "osm_type", "lon", "lat", "tags"])
    out = io_osm.categorise_pois(empty)
    assert out.empty


def test_categoriser_skips_pois_with_missing_tags() -> None:
    df = pd.DataFrame(
        [
            {"osm_id": 1, "osm_type": "node", "lon": 2.16, "lat": 41.39, "tags": None},
            {"osm_id": 2, "osm_type": "node", "lon": 2.16, "lat": 41.39, "tags": {}},
        ]
    )
    out = io_osm.categorise_pois(df)
    assert out.empty
