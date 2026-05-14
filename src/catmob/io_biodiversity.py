"""Biodiversity layers — WDPA / Natura 2000 / iNaturalist / Tree Cover.

* **WDPA** (World Database on Protected Areas) — protected.planet.net.
  We filter to Catalonia (Spain ISO3 ESP, then bbox-clip) and to designations
  relevant for biodiversity weighting (Natura 2000, Parc Natural, Reserva,
  Espais Naturals Protegits).
* **Copernicus Tree Cover Density** (10 m, latest year) — used as a continuous
  greenness/biodiversity proxy. Aggregated to hex via raster zonal stats.
* **iNaturalist research-grade observations** — used as a point-density proxy
  for biodiversity richness; queried via GBIF API
  (datasetKey = ``50c9509d-22c7-4a22-a47d-8c48425ef4a7``).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

import pandas as pd
import requests

from .schemas import BIODIVERSITY_OBSERVATION_SCHEMA, PROTECTED_AREA_SCHEMA

WDPA_DOWNLOAD_BASE = "https://www.protectedplanet.net/en/thematic-areas/wdpa"
GBIF_OCCURRENCE_API = "https://api.gbif.org/v1/occurrence/search"
INATURALIST_DATASET_KEY = "50c9509d-22c7-4a22-a47d-8c48425ef4a7"

CATALONIA_BBOX: tuple[float, float, float, float] = (0.15, 40.50, 3.35, 42.90)


# ---------------------------------------------------------------------------
# WDPA — read a previously-downloaded shapefile / GeoJSON, filter, validate.
# WDPA bulk downloads require a manual click-through; for automation we
# recommend the wdpar R package or downloading the Spain extract once and
# committing the resulting Catalonia subset Parquet.
# ---------------------------------------------------------------------------

def filter_wdpa_to_catalonia(
    wdpa_path: Path | str,
    *,
    bbox: tuple[float, float, float, float] = CATALONIA_BBOX,
) -> pd.DataFrame:
    """Read a WDPA shapefile/GeoJSON, clip to Catalonia bbox, validate."""
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.read_file(str(wdpa_path))
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
    bbox_geom = box(*bbox)
    catalonia = gdf[gdf.intersects(bbox_geom)].copy()

    rename = {
        "WDPAID":     "wdpa_id",
        "NAME":       "name",
        "DESIG_ENG":  "designation",
        "IUCN_CAT":   "iucn_category",
    }
    catalonia = catalonia.rename(columns=rename)
    catalonia["wdpa_id"] = catalonia["wdpa_id"].astype(str)

    out = catalonia[["wdpa_id", "name", "designation", "iucn_category", "geometry"]]
    return PROTECTED_AREA_SCHEMA.validate(out, lazy=True)


# ---------------------------------------------------------------------------
# iNaturalist (via GBIF) — research-grade observations
# ---------------------------------------------------------------------------

def _gbif_pages(
    bbox: tuple[float, float, float, float],
    year_from: int,
    year_to: int,
    *,
    page_size: int = 300,
    sleep_s: float = 0.2,
) -> Iterator[list[dict]]:
    """Yield pages of GBIF occurrence records for iNaturalist + bbox + date."""
    lon_min, lat_min, lon_max, lat_max = bbox
    geometry_wkt = (
        f"POLYGON(({lon_min} {lat_min}, {lon_max} {lat_min}, "
        f"{lon_max} {lat_max}, {lon_min} {lat_max}, {lon_min} {lat_min}))"
    )
    params = {
        "datasetKey": INATURALIST_DATASET_KEY,
        "geometry": geometry_wkt,
        "year": f"{year_from},{year_to}",
        "hasCoordinate": "true",
        "hasGeospatialIssue": "false",
        "occurrenceStatus": "PRESENT",
        "limit": page_size,
        "offset": 0,
    }
    while True:
        r = requests.get(GBIF_OCCURRENCE_API, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        results = body.get("results", [])
        if not results:
            break
        yield results
        if body.get("endOfRecords", True):
            break
        params["offset"] += page_size
        time.sleep(sleep_s)


def fetch_inat_observations(
    *,
    bbox: tuple[float, float, float, float] = CATALONIA_BBOX,
    year_from: int = 2018,
    year_to: int = 2024,
    max_records: int = 50_000,
) -> pd.DataFrame:
    """Fetch iNaturalist research-grade observations from GBIF, validated.

    For Catalonia the per-year volume is in the tens of thousands; the
    ``max_records`` cap is mostly a sanity guard. The default 6-year window
    aligns with the MITMA window (Q1+Q2 2024) plus comparison years.
    """
    rows: list[dict] = []
    for page in _gbif_pages(bbox, year_from, year_to):
        for rec in page:
            if rec.get("identificationVerificationStatus") not in (
                "research", "Research Grade", None
            ):
                continue
            rows.append({
                "observation_id": str(rec.get("key")),
                "source": "inaturalist",
                "lon": float(rec["decimalLongitude"]),
                "lat": float(rec["decimalLatitude"]),
                "year": int(rec.get("year") or 0),
                "taxon_kingdom": rec.get("kingdom"),
                "research_grade": True,
            })
            if len(rows) >= max_records:
                break
        if len(rows) >= max_records:
            break

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return BIODIVERSITY_OBSERVATION_SCHEMA.validate(df, lazy=True)


# ---------------------------------------------------------------------------
# Tree cover density — Copernicus HRL Tree Cover Density 10 m (zonal stat).
# The dataset requires Copernicus-Land registration; the helper computes
# per-hex TCD from a previously-downloaded GeoTIFF.
# ---------------------------------------------------------------------------

def compute_tree_cover_per_hex(tcd_raster_path: Path | str, hex_grid_path: Path | str) -> pd.DataFrame:
    """Mean tree-cover-density % per H3 hex via rasterstats."""
    try:
        from rasterstats import zonal_stats  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "compute_tree_cover_per_hex needs `rasterstats`. Install: pip install rasterstats"
        ) from e
    import geopandas as gpd

    hexes = gpd.read_file(hex_grid_path)
    stats = zonal_stats(hexes, str(tcd_raster_path), stats=["mean"], nodata=255)
    hexes["tree_cover_pct"] = [s["mean"] for s in stats]
    return hexes[["h3_id", "tree_cover_pct"]]
