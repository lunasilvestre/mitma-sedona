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

#: On-disk VIIRS night-time-lights annual composite over Catalonia (COG,
#: EPSG:4326, float32 nW/cm²/sr, fill = -3.402823e38). Built by the v2 fetch
#: wave; the dead ``viirs-monthly-v22`` PC collection no longer resolves.
VIIRS_CATALONIA_TIF = "data/bronze/pollution/viirs/viirs_ntl_2024_catalonia.tif"

#: float32 NaN-fill sentinel used by the VIIRS COG (GDAL default nodata).
VIIRS_NODATA = -3.402823e38


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
# VIIRS radiance — zonal mean per H3 cell from an on-disk COG.
# Mirrors catmob.io_biodiversity.compute_tree_cover_per_hex (rasterstats zonal
# mean over H3 cell-boundary POLYGONS). The raster and the polygons are both
# EPSG:4326, so NO reprojection is needed before the zonal pass.
# ---------------------------------------------------------------------------

def compute_viirs_radiance_per_hex(viirs_raster_path, hex_polys) -> "pd.DataFrame":
    """Mean VIIRS night-light radiance per H3 cell via rasterstats.

    Parameters
    ----------
    viirs_raster_path
        Path to the VIIRS COG (EPSG:4326, float32, fill = ``VIIRS_NODATA``).
    hex_polys
        A GeoDataFrame of H3 cell-boundary polygons in EPSG:4326 with an
        ``h3_id`` column (``h3.cell_to_boundary``). Both layers share the
        raster CRS, so the zonal pass runs without reprojection.

    Returns ``DataFrame[h3_id, viirs_radiance]`` with radiance clamped to
    ``max(0, value)`` (negative dark-current artefacts -> 0; the schema slot
    is ``ge(0)``). NULL where no pixel falls inside the cell.
    """
    try:
        from rasterstats import zonal_stats  # type: ignore
    except ImportError as e:  # pragma: no cover - env guard
        raise RuntimeError(
            "compute_viirs_radiance_per_hex needs `rasterstats`. Install: "
            "pip install rasterstats"
        ) from e

    stats = zonal_stats(
        hex_polys, str(viirs_raster_path),
        stats=["mean"], nodata=VIIRS_NODATA, all_touched=True,
    )
    out = hex_polys[["h3_id"]].copy()
    out["viirs_radiance"] = [
        (max(0.0, float(s["mean"])) if s["mean"] is not None else None)
        for s in stats
    ]
    return out


# ---------------------------------------------------------------------------
# VIIRS DNB monthly — Planetary Computer STAC (legacy; collection now dead)
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
