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

Implementation lands in M2 (Prompt C) — this module currently exposes the
shape contract and the STAC query template only.
"""
from __future__ import annotations

PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
LANDSAT_COLLECTION = "landsat-c2-l2"

# Catalonia bounding box (lon_min, lat_min, lon_max, lat_max).
CATALONIA_BBOX: tuple[float, float, float, float] = (0.15, 40.50, 3.35, 42.90)


def query_summer_lst_scenes(year: int, *, max_cloud_pct: int = 20) -> list[dict]:
    """Return STAC items for JJA (Jun-Aug) Landsat scenes over Catalonia.

    Implementation deferred to M2.
    """
    raise NotImplementedError("STAC query implemented in M2.")


def composite_lst_summer_median(items: list[dict]) -> "xr.DataArray":  # noqa: F821
    """Median-composite ST_B10 over the JJA window. Implementation in M2."""
    raise NotImplementedError("LST composite implemented in M2.")
