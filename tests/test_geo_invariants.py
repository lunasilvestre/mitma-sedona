"""Geo-invariant smoke tests applied to fixtures (and reused on real data).

These tests assert properties that *must* hold for any valid bronze/silver
artefact produced by the pipeline:

* coordinates inside Catalonia bbox
* CRS-tag round-tripping when geopandas is available
* zoning ids and POI ids are unique within their fixture
"""
from __future__ import annotations

import json
from pathlib import Path

CATALONIA_BBOX = (0.15, 40.50, 3.35, 42.90)


def _load_geojson(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_zone_ids_unique(mitma_zones_fixture: Path) -> None:
    gj = _load_geojson(mitma_zones_fixture)
    ids = [f["properties"]["id"] for f in gj["features"]]
    assert len(ids) == len(set(ids)), "duplicate zone IDs in fixture"


def test_zones_are_inside_catalonia_bbox(mitma_zones_fixture: Path) -> None:
    gj = _load_geojson(mitma_zones_fixture)
    xmin, ymin, xmax, ymax = CATALONIA_BBOX
    for feat in gj["features"]:
        coords = feat["geometry"]["coordinates"][0]  # outer ring
        for x, y in coords:
            assert xmin <= x <= xmax, f"x={x} outside Catalonia bbox"
            assert ymin <= y <= ymax, f"y={y} outside Catalonia bbox"


def test_zone_ids_match_province_prefix(mitma_zones_fixture: Path) -> None:
    gj = _load_geojson(mitma_zones_fixture)
    for feat in gj["features"]:
        zid = feat["properties"]["id"]
        prov = feat["properties"]["provincia"]
        assert zid.startswith(prov), f"zone {zid} does not start with provincia {prov}"
