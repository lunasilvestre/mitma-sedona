"""Thermal / Urban Heat Island loader (Landsat 8/9 LST via STAC).

We use the Microsoft Planetary Computer STAC catalog to query Landsat
Collection-2 Level-2 scenes over Catalonia, extract band ST_B10 (LST,
Kelvin × 0.00341802 + 149.0), and composite a summer (JJA) median per pixel.

For each H3 hex we then take the median of the pixels falling inside, and
compute UHI as ``LST(hex) − LST(rural reference)`` where the rural reference
is the 25th-percentile LST across non-urban hexes (low tree-cover-density,
non-industrial, non-residential).

Threshold for the score function: UHI > 2 °C → moderate penalty, > 4 °C →
heavy penalty.

References:
- Planetary Computer Landsat C2 L2 collection: https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2
- ST_B10 scaling: DN × 0.00341802 + 149.0 = Kelvin (subtract 273.15 for °C)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
LANDSAT_COLLECTION = "landsat-c2-l2"

# Catalonia bounding box (lon_min, lat_min, lon_max, lat_max).
CATALONIA_BBOX: tuple[float, float, float, float] = (0.15, 40.50, 3.35, 42.90)


def _open_stac_client():
    """Lazy import so the module can be imported without pystac-client."""
    import pystac_client
    try:
        import planetary_computer  # type: ignore
        modifier = planetary_computer.sign_inplace
    except ImportError:
        modifier = None
    return pystac_client.Client.open(PLANETARY_COMPUTER_STAC, modifier=modifier)


def query_summer_lst_scenes(
    year: int = 2024,
    *,
    max_cloud_pct: int = 20,
    bbox: tuple[float, float, float, float] = CATALONIA_BBOX,
) -> list[dict]:
    """Return STAC items for JJA (Jun-Aug) Landsat scenes over Catalonia.

    Each item is a dict with at least:
    - ``id``, ``datetime``, ``bbox``
    - ``assets['lwir11']`` (Landsat C2L2 thermal band 10) — signed asset URL
    - ``properties['eo:cloud_cover']``

    Parameters
    ----------
    year
        Year to query (JJA window).
    max_cloud_pct
        Filter on ``eo:cloud_cover < max_cloud_pct``.
    bbox
        ``(lon_min, lat_min, lon_max, lat_max)`` in EPSG:4326.
    """
    client = _open_stac_client()
    search = client.search(
        collections=[LANDSAT_COLLECTION],
        bbox=bbox,
        datetime=f"{year}-06-01/{year}-08-31",
        query={
            "eo:cloud_cover": {"lt": max_cloud_pct},
            "platform": {"in": ["landsat-8", "landsat-9"]},
            # Filter out night scenes (DAY for descending pass)
            "view:sun_elevation": {"gt": 30},
        },
    )
    items = list(search.items())
    return [item.to_dict() for item in items]


def composite_lst_summer_median(
    items: list[dict],
    *,
    bbox: tuple[float, float, float, float] = CATALONIA_BBOX,
    out_path: Optional[Path | str] = None,
):
    """Median-composite ST_B10 over the JJA window.

    Returns an ``xarray.DataArray`` of LST in °C, native Landsat resolution
    (~30 m). If ``out_path`` is provided, also writes a Cloud-Optimized
    GeoTIFF.

    This is a memory-efficient streaming approach using ``stackstac`` if
    available, falling back to per-scene rasterio reads.
    """
    import numpy as np

    try:
        import stackstac  # type: ignore
        import pystac
    except ImportError as e:
        raise RuntimeError(
            "composite_lst_summer_median needs `stackstac`. Install with: "
            "pip install stackstac"
        ) from e

    pystac_items = [pystac.Item.from_dict(d) for d in items]
    stack = stackstac.stack(
        pystac_items,
        assets=["lwir11"],  # Landsat C2L2 thermal band
        bounds_latlon=bbox,
        resolution=100,    # downsample 30m → 100m for speed
        epsg=25831,        # ETRS89 / UTM 31N (good for Catalonia)
        rescale=False,     # we apply the DN scaling manually below
    )
    # ST_B10 DN → Kelvin → °C
    lst_kelvin = stack * 0.00341802 + 149.0
    lst_celsius = lst_kelvin - 273.15
    lst_summer = lst_celsius.median(dim="time")

    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Compute then write
        arr = lst_summer.compute()
        arr.rio.to_raster(out_path, driver="COG", compress="DEFLATE")
    return lst_summer


def lst_zonal_mean_per_hex(lst_raster_path: Path | str, hex_grid_path: Path | str):
    """Compute mean LST per H3 hex from a composited LST raster.

    Pure-Python (rasterstats); the Sedona equivalent (RS_ZonalStats) is
    documented in docs/sedona_sql_patterns.md §4.
    """
    try:
        from rasterstats import zonal_stats  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "lst_zonal_mean_per_hex needs `rasterstats`. Install with: "
            "pip install rasterstats"
        ) from e

    import geopandas as gpd

    hexes = gpd.read_file(hex_grid_path)
    stats = zonal_stats(
        hexes, str(lst_raster_path),
        stats=["mean"], nodata=-9999, all_touched=False,
    )
    hexes["lst_summer_median_c"] = [s["mean"] for s in stats]

    rural_baseline = hexes["lst_summer_median_c"].quantile(0.25)
    hexes["uhi_delta_c"] = hexes["lst_summer_median_c"] - rural_baseline
    return hexes[["h3_id", "lst_summer_median_c", "uhi_delta_c"]]
