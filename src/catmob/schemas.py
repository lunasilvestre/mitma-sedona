"""Pandera schema contracts for every bronze-layer dataset.

These schemas are the single source of truth for what we consider a valid
ingest. They are imported by:

* the I/O modules (`io_mitma`, `io_osm`, `io_air`, …) — every fetcher ends
  with `SCHEMA.validate(df)` so a malformed source fails loudly;
* notebook ``01_data_ingest`` — same validation, executed inline so a stale
  source surfaces during exploration;
* the test suite — fixtures under ``tests/fixtures/`` are validated against
  these schemas in ``tests/test_schemas.py``.

Schemas are deliberately **strict on critical columns** (zone IDs, geometry,
counts) and **permissive on enrichment columns** (sex, age, income) because
those drift across MITMA releases and we do not want CI red because of an
upstream rename we can adapt to.
"""
from __future__ import annotations

import pandera.pandas as pa
from pandera import Check, Column

# ---------------------------------------------------------------------------
# MITMA v2 — Origin/Destination flows
# ---------------------------------------------------------------------------

# v2 distance bands (string, never numeric — preserves "0.5-2" without loss)
_DISTANCE_BANDS = ["0.5-2", "2-10", "10-50", "50-100", ">100"]
_ACTIVITY_VALUES = ["casa", "trabajo_estudio", "frecuente", "no_frecuente"]
_RENTA_VALUES = ["<10", "10-15", ">15"]
_EDAD_VALUES = ["0-25", "25-45", "45-65", "65-100", "NA"]
_SEXO_VALUES = ["hombre", "mujer", "NA"]

#: Daily OD flows, distritos zoning, MITMA v2 (2022-onwards).
#: Source: ``movilidad-opendata.mitma.es/estudios_basicos/por-distritos/viajes/ficheros-diarios``.
MITMA_DAILY_OD_SCHEMA = pa.DataFrameSchema(
    {
        # YYYYMMDD as string (no Date parsing — we keep the raw token).
        "fecha": Column(str, Check.str_matches(r"^\d{8}$")),
        # Zone IDs are strings with leading zeros: never cast to int.
        "origen": Column(str, Check.str_length(min_value=4, max_value=24)),
        "destino": Column(str, Check.str_length(min_value=4, max_value=24)),
        "distancia": Column(str, Check.isin(_DISTANCE_BANDS)),
        "actividad_origen": Column(str, Check.isin(_ACTIVITY_VALUES)),
        "actividad_destino": Column(str, Check.isin(_ACTIVITY_VALUES)),
        # Trip counts and passenger-km — float (MITMA publishes fractions).
        "viajes": Column(float, Check.ge(0)),
        "viajes_km": Column(float, Check.ge(0)),
        # Optional enrichment columns: present in some releases, missing in others.
        "residencia": Column(str, nullable=True, required=False),
        "renta": Column(str, nullable=True, required=False, checks=Check.isin(_RENTA_VALUES)),
        "edad": Column(str, nullable=True, required=False, checks=Check.isin(_EDAD_VALUES)),
        "sexo": Column(str, nullable=True, required=False, checks=Check.isin(_SEXO_VALUES)),
    },
    strict="filter",
    coerce=True,
)

#: Hourly OD flows, distritos zoning, MITMA v2.
#: Adds ``periodo`` (0–23) on top of the daily schema.
MITMA_HOURLY_OD_SCHEMA = MITMA_DAILY_OD_SCHEMA.add_columns(
    {"periodo": Column(int, Check.in_range(0, 23))}
)

#: Distritos zoning table — id ↔ municipality ↔ INE codes.
MITMA_ZONE_SCHEMA = pa.DataFrameSchema(
    {
        "id": Column(str, Check.str_length(min_value=4, max_value=24), unique=True),
        "name": Column(str, nullable=True),
        "municipio_id": Column(str, nullable=True),
        "provincia": Column(str, Check.str_matches(r"^\d{2}$")),
        "geometry": Column(object, nullable=False),  # Shapely geom (WKB/WKT also OK)
    },
    coerce=True,
)


# ---------------------------------------------------------------------------
# OpenStreetMap — Cataluña
# ---------------------------------------------------------------------------

#: Generic POI extract (climbing, yoga, hospital, pharmacy, …).
OSM_POI_SCHEMA = pa.DataFrameSchema(
    {
        "osm_id": Column(int, unique=True),
        "osm_type": Column(str, Check.isin(["node", "way", "relation"])),
        "category": Column(str),  # 'climbing' | 'yoga' | 'hospital' | 'park' | …
        "name": Column(str, nullable=True),
        "lon": Column(float, Check.in_range(0.0, 4.0)),  # Catalonia bbox
        "lat": Column(float, Check.in_range(40.5, 42.9)),
        "tags": Column(object, nullable=True),  # dict
    },
    coerce=True,
)

#: Network features (highway, railway, coastline) as LineString geoms.
OSM_NETWORK_SCHEMA = pa.DataFrameSchema(
    {
        "osm_id": Column(int),
        "kind": Column(str, Check.isin(["highway", "railway", "coastline"])),
        "subtype": Column(str, nullable=True),  # 'motorway' | 'rail' | …
        "geometry": Column(object, nullable=False),
    },
    coerce=True,
)


# ---------------------------------------------------------------------------
# GTFS — Renfe Rodalies + FGC
# ---------------------------------------------------------------------------

GTFS_STOPS_SCHEMA = pa.DataFrameSchema(
    {
        "stop_id": Column(str, unique=True),
        "stop_name": Column(str),
        "lon": Column(float, Check.in_range(0.0, 4.0)),
        "lat": Column(float, Check.in_range(40.5, 42.9)),
        "feed": Column(str, Check.isin(["rodalies", "fgc"])),
    },
    coerce=True,
)

GTFS_FREQUENCY_SCHEMA = pa.DataFrameSchema(
    {
        "stop_id": Column(str, unique=True),
        "trips_per_day": Column(int, Check.ge(0)),
        "trips_to_bcn_core": Column(int, Check.ge(0)),
    },
    coerce=True,
)


# ---------------------------------------------------------------------------
# Air quality — EEA + XVPCA + CAMS
# ---------------------------------------------------------------------------

#: Station-level annual aggregates (NO2, PM2.5, PM10, O3 — µg/m³).
AIR_QUALITY_STATION_SCHEMA = pa.DataFrameSchema(
    {
        "station_id": Column(str, unique=True),
        "station_name": Column(str),
        "operator": Column(str, Check.isin(["EEA", "XVPCA"])),
        "lon": Column(float, Check.in_range(0.0, 4.0)),
        "lat": Column(float, Check.in_range(40.5, 42.9)),
        "year": Column(int, Check.in_range(2018, 2030)),
        "no2_annual_ugm3": Column(float, Check.ge(0), nullable=True),
        "pm25_annual_ugm3": Column(float, Check.ge(0), nullable=True),
        "pm10_annual_ugm3": Column(float, Check.ge(0), nullable=True),
        "o3_8h_max_ugm3": Column(float, Check.ge(0), nullable=True),
    },
    coerce=True,
)

#: CAMS regional gridded means (10 km grid, monthly).
AIR_QUALITY_GRID_SCHEMA = pa.DataFrameSchema(
    {
        "lon": Column(float, Check.in_range(0.0, 4.0)),
        "lat": Column(float, Check.in_range(40.5, 42.9)),
        "month": Column(str, Check.str_matches(r"^\d{4}-\d{2}$")),
        "no2_ugm3": Column(float, Check.ge(0)),
        "pm25_ugm3": Column(float, Check.ge(0)),
    },
    coerce=True,
)


# ---------------------------------------------------------------------------
# Thermal / Urban Heat Island — Landsat 8/9 LST via STAC
# ---------------------------------------------------------------------------

#: Per-pixel Land Surface Temperature, summer composite, °C.
THERMAL_LST_SCHEMA = pa.DataFrameSchema(
    {
        "lon": Column(float, Check.in_range(0.0, 4.0)),
        "lat": Column(float, Check.in_range(40.5, 42.9)),
        "year": Column(int, Check.in_range(2018, 2030)),
        "lst_summer_median_c": Column(float, Check.in_range(-10.0, 70.0)),
        "uhi_delta_c": Column(float, Check.in_range(-10.0, 20.0)),
    },
    coerce=True,
)


# ---------------------------------------------------------------------------
# Biodiversity — WDPA / Natura 2000 / iNaturalist / CLC tree cover
# ---------------------------------------------------------------------------

PROTECTED_AREA_SCHEMA = pa.DataFrameSchema(
    {
        "wdpa_id": Column(str, unique=True),
        "name": Column(str),
        "designation": Column(str),  # 'Natura 2000', 'Parc Natural', …
        "iucn_category": Column(str, nullable=True),
        "geometry": Column(object, nullable=False),
    },
    coerce=True,
)

BIODIVERSITY_OBSERVATION_SCHEMA = pa.DataFrameSchema(
    {
        "observation_id": Column(str, unique=True),
        "source": Column(str, Check.isin(["inaturalist", "gbif"])),
        "lon": Column(float, Check.in_range(0.0, 4.0)),
        "lat": Column(float, Check.in_range(40.5, 42.9)),
        "year": Column(int, Check.in_range(2010, 2030)),
        "taxon_kingdom": Column(str, nullable=True),
        "research_grade": Column(bool, nullable=True),
    },
    coerce=True,
)


# ---------------------------------------------------------------------------
# Health amenities — derived from OSM_POI_SCHEMA (categorical filter)
# ---------------------------------------------------------------------------

HEALTH_AMENITY_CATEGORIES = {"hospital", "clinic", "doctors", "pharmacy"}


# ---------------------------------------------------------------------------
# Gold layer — H3 hex grid with all liveability features
# ---------------------------------------------------------------------------

GOLD_HEX_SCHEMA = pa.DataFrameSchema(
    {
        "h3_id": Column(str, unique=True, checks=Check.str_length(15, 15)),
        "lon_centroid": Column(float, Check.in_range(0.0, 4.0)),
        "lat_centroid": Column(float, Check.in_range(40.5, 42.9)),
        # Mobility / accessibility
        "train_reach_min": Column(float, Check.ge(0), nullable=True),
        "trains_per_day_nearest": Column(int, Check.ge(0), nullable=True),
        "trains_to_bcn_nearest": Column(int, Check.ge(0), nullable=True),
        # Amenity proximities (metres)
        "climb_min_m": Column(float, Check.ge(0), nullable=True),
        "yoga_min_m": Column(float, Check.ge(0), nullable=True),
        "hospital_min_m": Column(float, Check.ge(0), nullable=True),
        "pharmacy_density_per_km2": Column(float, Check.ge(0), nullable=True),
        # Nature
        "green_min_m": Column(float, Check.ge(0), nullable=True),
        "sea_min_m": Column(float, Check.ge(0), nullable=True),
        "tree_cover_pct": Column(float, Check.in_range(0.0, 100.0), nullable=True),
        "natura2000_within_5km": Column(bool, nullable=True),
        "biodiversity_obs_density": Column(float, Check.ge(0), nullable=True),
        # Penalties
        "industry_density_per_km2": Column(float, Check.ge(0), nullable=True),
        "motorway_within_500m": Column(bool, nullable=True),
        "eprtr_facility_min_m": Column(float, Check.ge(0), nullable=True),
        # Air quality
        "no2_ugm3": Column(float, Check.ge(0), nullable=True),
        "pm25_ugm3": Column(float, Check.ge(0), nullable=True),
        # Heat
        "lst_summer_median_c": Column(float, nullable=True),
        "uhi_delta_c": Column(float, nullable=True),
        # Light pollution (VIIRS DNB radiance, nW/cm²/sr)
        "viirs_radiance": Column(float, Check.ge(0), nullable=True),
        # Mobility vibe check
        "mitma_inflow_daily": Column(float, Check.ge(0), nullable=True),
        "mitma_outflow_daily": Column(float, Check.ge(0), nullable=True),
        "mitma_through_ratio": Column(float, nullable=True),
        # Final score
        "liveability_score": Column(float, Check.in_range(0.0, 100.0), nullable=True),
    },
    coerce=True,
)


# Convenience registry — used by ``tests/test_schemas.py`` to enumerate.
SCHEMA_REGISTRY: dict[str, pa.DataFrameSchema] = {
    "mitma_daily_od": MITMA_DAILY_OD_SCHEMA,
    "mitma_hourly_od": MITMA_HOURLY_OD_SCHEMA,
    "mitma_zones": MITMA_ZONE_SCHEMA,
    "osm_poi": OSM_POI_SCHEMA,
    "osm_network": OSM_NETWORK_SCHEMA,
    "gtfs_stops": GTFS_STOPS_SCHEMA,
    "gtfs_frequency": GTFS_FREQUENCY_SCHEMA,
    "air_quality_station": AIR_QUALITY_STATION_SCHEMA,
    "air_quality_grid": AIR_QUALITY_GRID_SCHEMA,
    "thermal_lst": THERMAL_LST_SCHEMA,
    "protected_area": PROTECTED_AREA_SCHEMA,
    "biodiversity_observation": BIODIVERSITY_OBSERVATION_SCHEMA,
    "gold_hex": GOLD_HEX_SCHEMA,
}
