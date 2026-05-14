"""Valhalla bike isochrones — client wrapper with on-disk cache.

The ``valhalla`` service in ``docker/docker-compose.yml`` exposes its HTTP
API on port 8002 (default; ``VALHALLA_URL`` env override). For each train
station we compute a multi-band isochrone (typically 15 + 25 minutes) and
store the GeoJSON polygon as Parquet under ``data/silver/isochrones/``.

The result is a Sedona-compatible polygon (WKT/WKB), keyed by
``(station_id, minutes)`` so a re-run skips the network round-trip.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests

VALHALLA_URL = os.environ.get("VALHALLA_URL", "http://localhost:8002")
CACHE_ROOT = Path(os.environ.get("CATMOB_ISOCHRONE_CACHE", "data/silver/isochrones"))
DEFAULT_CONTOUR_MINUTES = (15, 25)


@dataclass(frozen=True)
class IsochroneResult:
    station_id: str
    minutes: int
    geojson: dict
    source: str  # 'cache' | 'fresh'


def _cache_key(station_id: str, lat: float, lon: float, minutes: int) -> Path:
    h = hashlib.sha1(f"{station_id}|{lat:.6f}|{lon:.6f}|{minutes}".encode()).hexdigest()[:12]
    return CACHE_ROOT / station_id / f"{station_id}__{minutes}min__{h}.geojson"


def bike_isochrone(
    station_id: str,
    lat: float,
    lon: float,
    minutes: int = 25,
    *,
    valhalla_url: str = VALHALLA_URL,
    timeout: float = 30.0,
) -> IsochroneResult:
    """Request a bike isochrone for a station from Valhalla, using disk cache."""
    cache_path = _cache_key(station_id, lat, lon, minutes)
    if cache_path.exists():
        return IsochroneResult(station_id, minutes, json.loads(cache_path.read_text()), "cache")

    payload = {
        "locations": [{"lat": lat, "lon": lon}],
        "costing": "bicycle",
        "costing_options": {"bicycle": {"bicycle_type": "Hybrid"}},
        "contours": [{"time": minutes}],
        "polygons": True,
        "denoise": 0.5,
        "generalize": 50,
    }
    r = requests.post(f"{valhalla_url}/isochrone", json=payload, timeout=timeout)
    r.raise_for_status()
    geojson = r.json()

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(geojson))
    return IsochroneResult(station_id, minutes, geojson, "fresh")


def batch_isochrones(
    stations: Iterable[tuple[str, float, float]],
    minutes: tuple[int, ...] = DEFAULT_CONTOUR_MINUTES,
    *,
    valhalla_url: str = VALHALLA_URL,
) -> list[IsochroneResult]:
    """Compute isochrones for a list of stations × minutes bands."""
    out: list[IsochroneResult] = []
    for station_id, lat, lon in stations:
        for m in minutes:
            try:
                out.append(bike_isochrone(station_id, lat, lon, m, valhalla_url=valhalla_url))
            except Exception as e:  # noqa: BLE001
                print(f"WARN  isochrone {station_id} @ {m}min failed: {e}")
    return out
