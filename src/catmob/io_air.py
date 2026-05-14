"""Air quality loader — EEA, XVPCA, and CAMS.

Three sources, three roles:

* **EEA Air Quality e-Reporting** — official, EU-wide, station-level annual
  aggregates for NO₂/PM₂.₅/PM₁₀/O₃. Source of truth for compliance metrics.
  Discomap CSV: ``https://discomap.eea.europa.eu/Map/UI/AirQualityE1a/``.
* **XVPCA** (Xarxa de Vigilància i Previsió de la Contaminació Atmosfèrica) —
  Catalan government network, ~80 stations, denser coverage in Catalonia.
  Open data: ``https://analisi.transparenciacatalunya.cat/``.
* **CAMS Regional Reanalysis** (Copernicus Atmosphere Monitoring Service) —
  modelled gridded fields at ~0.1° resolution, fills in stationless areas.

Per hex we compute: nearest-station NO₂ + IDW-interpolated PM₂.₅ from CAMS.
The score function (``catmob.scoring``) uses WHO 2021 thresholds:
NO₂ > 20 µg/m³ annual mean = moderate penalty,
PM₂.₅ > 5 µg/m³ annual mean = moderate penalty.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .schemas import AIR_QUALITY_GRID_SCHEMA, AIR_QUALITY_STATION_SCHEMA

EEA_DOWNLOAD_BASE = "https://discomap.eea.europa.eu/Map/UI/AirQualityE1a/"
XVPCA_DATASET_URL = (
    "https://analisi.transparenciacatalunya.cat/resource/uy6k-2s8r.csv"
)
CAMS_REANALYSIS_PATH_HINT = "data/bronze/air/cams/no2_pm25_<year>.nc"


def parse_xvpca_csv(path: Path | str) -> pd.DataFrame:
    """Parse the XVPCA station CSV into the unified station schema."""
    df = pd.read_csv(path)
    # Normalise column names — XVPCA uses long Spanish names.
    rename_map = {
        "codi_estacio": "station_id",
        "nom_estacio": "station_name",
        "longitud": "lon",
        "latitud": "lat",
        "any": "year",
        "no2_anual": "no2_annual_ugm3",
        "pm25_anual": "pm25_annual_ugm3",
        "pm10_anual": "pm10_annual_ugm3",
        "o3_max8h": "o3_8h_max_ugm3",
    }
    df = df.rename(columns=rename_map)
    df["operator"] = "XVPCA"
    keep = list(AIR_QUALITY_STATION_SCHEMA.columns.keys())
    df = df.loc[:, [c for c in keep if c in df.columns]]
    return AIR_QUALITY_STATION_SCHEMA.validate(df, lazy=True)


def parse_eea_csv(path: Path | str) -> pd.DataFrame:
    """Parse an EEA E1a annual aggregate CSV. Implemented in M2."""
    raise NotImplementedError("EEA parser implemented in M2.")


def cams_grid_to_dataframe(nc_path: Path | str, *, var: str = "no2") -> pd.DataFrame:
    """Open a CAMS NetCDF and unstack into a (lon, lat, month, value) DataFrame.

    Implemented in M2 with xarray; here we expose the shape contract.
    """
    raise NotImplementedError("CAMS NetCDF unstacker implemented in M2.")
