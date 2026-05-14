"""Biodiversity layers — WDPA / Natura 2000 / iNaturalist / Tree Cover.

* **WDPA** (World Database on Protected Areas) — protected.planet.net.
  We filter to Catalonia and to designations relevant for biodiversity
  weighting (Natura 2000, Parc Natural, Reserva, Espais Naturals Protegits).
* **Copernicus Tree Cover Density** (10 m, 2021) — used as a continuous
  greenness/biodiversity proxy.
* **iNaturalist research-grade observations** — used as a point-density proxy
  for biodiversity richness; query via GBIF API (datasetKey = iNat).

Implementations land in M2 (Prompt C).
"""
from __future__ import annotations

WDPA_DOWNLOAD_BASE = "https://www.protectedplanet.net/en/thematic-areas/wdpa"
TREE_COVER_DENSITY_STAC_COLLECTION = "esa-cci-lc"  # adjust at impl-time
GBIF_OCCURRENCE_API = "https://api.gbif.org/v1/occurrence/search"
INATURALIST_DATASET_KEY = "50c9509d-22c7-4a22-a47d-8c48425ef4a7"

CATALONIA_BBOX: tuple[float, float, float, float] = (0.15, 40.50, 3.35, 42.90)


def fetch_wdpa_catalonia():  # noqa: ANN201
    raise NotImplementedError("WDPA fetcher implemented in M2.")


def query_inat_observations(year_from: int = 2018):  # noqa: ANN201
    raise NotImplementedError("iNaturalist GBIF fetcher implemented in M2.")


def compute_tree_cover_per_hex(hex_grid):  # noqa: ANN001, ANN201
    raise NotImplementedError("Tree-cover aggregation implemented in M2.")
