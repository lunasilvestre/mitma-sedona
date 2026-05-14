"""Health amenities loader.

Two source families:

* **OpenStreetMap** — ``amenity in (hospital, clinic, doctors, pharmacy)``.
  Already extracted in :mod:`catmob.io_osm`; this module surfaces them
  with the right category labels and computes per-hex distance/density.
* **CatSalut** — Catalan public health network has a public registry of
  hospitals and CAPs (Centre d'Atenció Primària). Used to cross-check OSM
  completeness; not a primary source.

Per-hex outputs:
- ``hospital_min_m`` — distance to nearest hospital
- ``pharmacy_density_per_km2`` — pharmacies within 1 km / area
- (future) walkability proxy: cycleway-to-road ratio + sidewalk presence
"""
from __future__ import annotations

CATSALUT_HOSPITALS_URL = (
    "https://analisi.transparenciacatalunya.cat/resource/yub2-3z85.csv"
)


def fetch_catsalut_hospitals():  # noqa: ANN201
    raise NotImplementedError("CatSalut fetcher implemented in M2.")
