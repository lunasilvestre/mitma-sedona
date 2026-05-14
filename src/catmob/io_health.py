"""Health amenities loader.

Two source families:

* **OpenStreetMap** — ``amenity in (hospital, clinic, doctors, pharmacy)``.
  Already extracted in :mod:`catmob.io_osm`; this module surfaces them
  with the right category labels and computes per-hex distance/density.
* **CatSalut** — Catalan public health network, public registry of hospitals
  on the Generalitat's open-data portal. Used to cross-check OSM completeness.

Per-hex outputs:
- ``hospital_min_m`` — distance to nearest hospital
- ``pharmacy_density_per_km2`` — pharmacies within 1 km / area
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from .schemas import OSM_POI_SCHEMA

# Generalitat de Catalunya open-data portal — public hospital registry.
CATSALUT_HOSPITALS_URL = (
    "https://analisi.transparenciacatalunya.cat/resource/yub2-3z85.csv"
    "?$limit=5000"
)


def fetch_catsalut_hospitals(
    *,
    cache_path: Path | str | None = None,
) -> pd.DataFrame:
    """Fetch CatSalut hospital registry CSV and return as OSM_POI_SCHEMA shape."""
    if cache_path and Path(cache_path).exists():
        df = pd.read_csv(cache_path)
    else:
        r = requests.get(CATSALUT_HOSPITALS_URL, timeout=30)
        r.raise_for_status()
        if cache_path:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            Path(cache_path).write_bytes(r.content)
        df = pd.read_csv(StringIO(r.text))

    # Column names vary; the canonical CatSalut dataset has at minimum
    # codi (code), nom (name), longitud, latitud. Adapt defensively.
    name_col = next((c for c in df.columns if c.lower() in
                     ("nom", "nombre", "denominacio", "name")), df.columns[1])
    lon_col = next((c for c in df.columns if c.lower() in
                    ("longitud", "longitude", "lon", "lng")), None)
    lat_col = next((c for c in df.columns if c.lower() in
                    ("latitud", "latitude", "lat")), None)
    code_col = next((c for c in df.columns if c.lower() in
                     ("codi", "codigo", "id")), df.columns[0])

    if lon_col is None or lat_col is None:
        raise ValueError(
            f"CatSalut CSV missing lon/lat. Columns seen: {list(df.columns)[:10]}"
        )

    out = pd.DataFrame({
        "osm_id": df[code_col].astype(str).map(lambda x: abs(hash(x)) % (10**9)),
        "osm_type": "node",
        "category": "hospital",
        "name": df[name_col].astype(str),
        "lon": pd.to_numeric(df[lon_col], errors="coerce"),
        "lat": pd.to_numeric(df[lat_col], errors="coerce"),
        "tags": [{"source": "catsalut", "code": str(c)} for c in df[code_col]],
    })
    out = out.dropna(subset=["lon", "lat"])
    return OSM_POI_SCHEMA.validate(out, lazy=True)
