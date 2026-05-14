"""Non-air pollution layers — E-PRTR industrial emissions + VIIRS night lights.

* **E-PRTR** (European Pollutant Release and Transfer Register) — facility-level
  emissions to air/water/soil for major industrial sites in Europe.
  Dataset hub: ``https://industry.eea.europa.eu/``. We ship a CSV reader for
  a previously-downloaded "Spain" extract; bulk download requires click-through.
* **VIIRS Day/Night Band monthly composites** — NOAA's nighttime radiance
  product, a robust proxy for light pollution. STAC available via Microsoft
  Planetary Computer (collection ``viirs-monthly-v22``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
VIIRS_STAC_COLLECTION = "viirs-monthly-v22"

CATALONIA_BBOX: tuple[float, float, float, float] = (0.15, 40.50, 3.35, 42.90)


# ---------------------------------------------------------------------------
# E-PRTR — facility CSV (Spain extract)
# ---------------------------------------------------------------------------

def parse_eprtr_facilities(
    csv_path: Path | str,
    *,
    bbox: tuple[float, float, float, float] = CATALONIA_BBOX,
) -> pd.DataFrame:
    """Parse an E-PRTR Spain facility CSV, filter to Catalonia bbox.

    Returns DataFrame: ``facility_id, name, lon, lat, total_emissions_t``.
    """
    df = pd.read_csv(csv_path)
    rename = {
        "FacilityName": "name",
        "facility_name": "name",
        "FacilityID": "facility_id",
        "facility_id": "facility_id",
        "Long": "lon", "Longitude": "lon",
        "Lat": "lat",  "Latitude": "lat",
        "TotalQuantity": "total_emissions_t",
        "PollutantReleaseAndTransferQuantityKg": "total_emissions_kg",
    }
    df = df.rename(columns=rename)
    if "total_emissions_t" not in df.columns and "total_emissions_kg" in df.columns:
        df["total_emissions_t"] = df["total_emissions_kg"] / 1000.0

    keep = ["facility_id", "name", "lon", "lat"]
    if "total_emissions_t" in df.columns:
        keep.append("total_emissions_t")
    df = df[[c for c in keep if c in df.columns]].copy()
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df = df.dropna(subset=["lon", "lat"])
    lon_min, lat_min, lon_max, lat_max = bbox
    return df[
        (df["lon"].between(lon_min, lon_max)) & (df["lat"].between(lat_min, lat_max))
    ].reset_index(drop=True)


# ---------------------------------------------------------------------------
# VIIRS DNB monthly — Planetary Computer STAC
# ---------------------------------------------------------------------------

def _open_stac_client():
    import pystac_client
    try:
        import planetary_computer  # type: ignore
        modifier = planetary_computer.sign_inplace
    except ImportError:
        modifier = None
    return pystac_client.Client.open(PLANETARY_COMPUTER_STAC, modifier=modifier)


def viirs_monthly_radiance(
    year: int,
    month: int,
    *,
    bbox: tuple[float, float, float, float] = CATALONIA_BBOX,
    out_path: Optional[Path | str] = None,
):
    """Fetch a single VIIRS DNB monthly composite over Catalonia."""
    try:
        import stackstac  # type: ignore
        import pystac
    except ImportError as e:
        raise RuntimeError(
            "viirs_monthly_radiance needs `stackstac`. Install: pip install stackstac"
        ) from e

    client = _open_stac_client()
    search = client.search(
        collections=[VIIRS_STAC_COLLECTION],
        bbox=bbox,
        datetime=f"{year}-{month:02d}-01/{year}-{month:02d}-28",
    )
    items = list(search.items())
    if not items:
        raise RuntimeError(f"No VIIRS items for {year}-{month:02d}")

    stack = stackstac.stack(
        [pystac.Item.from_dict(it.to_dict()) for it in items],
        assets=["avg_rad"],
        bounds_latlon=bbox,
        resolution=500,
        epsg=25831,
    )
    composite = stack.median(dim="time")
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        composite.compute().rio.to_raster(out_path, driver="COG", compress="DEFLATE")
    return composite
