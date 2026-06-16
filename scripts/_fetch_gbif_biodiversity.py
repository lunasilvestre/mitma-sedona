#!/usr/bin/env python
"""Standalone keyless GBIF occurrence fetch for biodiversity_obs_density.

Source: GBIF occurrence search API (https://api.gbif.org/v1/occurrence/search).
Scope:  iNaturalist Research-grade dataset (datasetKey 50c9509d-...), country=ES,
        clipped server-side to the Catalonia bbox (lon 0.15..3.35, lat 40.5..42.9)
        via a WKT polygon, years 2018-2024, coordinates present, no geospatial
        issue, occurrenceStatus=PRESENT. Keyless (no account/token).

Lands at data/bronze/biodiversity/gbif_occurrences.parquet — a distinct path
from the existing nature/inaturalist_catalonia.parquet. Does NOT import or edit
src/catmob. Paginates a representative sample (default cap 50k).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import requests

GBIF_OCCURRENCE_API = "https://api.gbif.org/v1/occurrence/search"
INATURALIST_DATASET_KEY = "50c9509d-22c7-4a22-a47d-8c48425ef4a7"

# Catalonia bbox: lon_min lat_min lon_max lat_max (matches CATALONIA_BBOX).
LON_MIN, LAT_MIN, LON_MAX, LAT_MAX = 0.15, 40.50, 3.35, 42.90
GEOMETRY_WKT = (
    f"POLYGON(({LON_MIN} {LAT_MIN}, {LON_MAX} {LAT_MIN}, "
    f"{LON_MAX} {LAT_MAX}, {LON_MIN} {LAT_MAX}, {LON_MIN} {LAT_MIN}))"
)

MAX_RECORDS = 50_000
PAGE_SIZE = 300  # GBIF search hard caps offset+limit at 100_000


def fetch() -> pd.DataFrame:
    base_params = {
        "datasetKey": INATURALIST_DATASET_KEY,
        "country": "ES",
        "geometry": GEOMETRY_WKT,
        "year": "2018,2024",
        "hasCoordinate": "true",
        "hasGeospatialIssue": "false",
        "occurrenceStatus": "PRESENT",
        "limit": PAGE_SIZE,
        "offset": 0,
    }
    rows: list[dict] = []
    session = requests.Session()
    while len(rows) < MAX_RECORDS:
        params = dict(base_params, offset=len(rows) // PAGE_SIZE * PAGE_SIZE)
        # Use a running offset that matches how many we've consumed.
        params["offset"] = base_params["offset"]
        r = session.get(GBIF_OCCURRENCE_API, params=params, timeout=60)
        r.raise_for_status()
        body = r.json()
        results = body.get("results", [])
        if not results:
            break
        for rec in results:
            lat = rec.get("decimalLatitude")
            lon = rec.get("decimalLongitude")
            if lat is None or lon is None:
                continue
            # Defensive bbox clip (server already filters, but enforce it).
            if not (LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX):
                continue
            rows.append({
                "observation_id": str(rec.get("key")),
                "source": "gbif_inaturalist",
                "lon": float(lon),
                "lat": float(lat),
                "year": int(rec.get("year") or 0),
                "taxon_kingdom": rec.get("kingdom"),
                "species": rec.get("species"),
                "basis_of_record": rec.get("basisOfRecord"),
                "research_grade": True,
            })
            if len(rows) >= MAX_RECORDS:
                break
        if body.get("endOfRecords", True):
            break
        base_params["offset"] += PAGE_SIZE
        time.sleep(0.15)
    return pd.DataFrame(rows)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).resolve().parents[1]
        / "data/bronze/biodiversity/gbif_occurrences.parquet"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    df = fetch()
    if df.empty:
        print("ERROR: GBIF returned 0 records", file=sys.stderr)
        return 1
    df.to_parquet(out, index=False)
    print(f"wrote {out}: {len(df):,} occurrences")
    print(f"  lon range: {df.lon.min():.4f}..{df.lon.max():.4f}")
    print(f"  lat range: {df.lat.min():.4f}..{df.lat.max():.4f}")
    print(f"  year range: {df.year.min()}..{df.year.max()}")
    print(f"  kingdoms: {df.taxon_kingdom.value_counts().to_dict()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
