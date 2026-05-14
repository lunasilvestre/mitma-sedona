"""Non-air pollution layers — E-PRTR industrial emissions + VIIRS night lights.

* **E-PRTR** (European Pollutant Release and Transfer Register) — facility-level
  emissions to air/water/soil for major industrial sites in Europe.
  Source: ``https://industry.eea.europa.eu/``. Per-hex feature: distance to
  nearest E-PRTR facility weighted by reported emissions intensity.
* **VIIRS Day/Night Band monthly composites** — NOAA's nighttime radiance
  product, a robust proxy for light pollution. STAC available via Microsoft
  Planetary Computer (collection ``viirs-monthly-v22``).

Implementations land in M2 (Prompt C).
"""
from __future__ import annotations

EPRTR_DOWNLOAD_BASE = "https://industry.eea.europa.eu/download"
VIIRS_STAC_COLLECTION = "viirs-monthly-v22"


def fetch_eprtr_catalonia():  # noqa: ANN201
    raise NotImplementedError("E-PRTR fetcher implemented in M2.")


def viirs_monthly_radiance(year: int, month: int):  # noqa: ANN201
    raise NotImplementedError("VIIRS STAC fetcher implemented in M2.")
