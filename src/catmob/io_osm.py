"""OpenStreetMap loader (Cataluña PBF).

Strategy:
1. Pre-prune the PBF with ``osmium tags-filter`` in ``scripts/fetch_osm.sh``
   to shrink the file from ~250 MB to <50 MB before Sedona ever sees it.
2. Inside Spark, use **Sedona's native PBF reader** (``format="osmpbf"``,
   available since Sedona 1.7.1). No osm2pgsql, no OSMnx.

The pandas helpers here are used by the test suite to validate small
fixture extracts; the Sedona path is implemented in M2 (Prompt B).
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd

from .schemas import OSM_POI_SCHEMA

# Tag-filter spec used by ``scripts/fetch_osm.sh``. One entry per
# ``key=value`` pair so individual values are inspectable; the bash script
# joins them with spaces when invoking ``osmium tags-filter``.
OSMIUM_TAG_FILTER: list[str] = [
    # Roads
    "highway=motorway", "highway=trunk", "highway=primary", "highway=secondary",
    "highway=motorway_link", "highway=trunk_link", "highway=bus_stop",
    # Rail / transit
    "railway=rail", "railway=station", "railway=stop", "railway=halt",
    "public_transport=stop_position", "public_transport=station",
    # Places
    "place=city", "place=town", "place=village", "place=suburb",
    # Leisure / nature
    "leisure=park", "leisure=garden", "leisure=nature_reserve",
    "leisure=sports_centre", "leisure=fitness_centre", "leisure=climbing",
    "natural=wood", "natural=water", "natural=coastline",
    # Land use
    "landuse=forest", "landuse=industrial", "landuse=residential",
    # Boundaries
    "boundary=administrative",
    # Health
    "amenity=hospital", "amenity=clinic", "amenity=doctors", "amenity=pharmacy",
    # Lifestyle
    "sport=climbing", "sport=yoga",
]

# OSM tag → POI category mapping. Each category is a list of *alternative*
# tag dicts; a POI matches a category if *any* alt-rule's keys all match.
POI_CATEGORY_RULES: dict[str, list[dict[str, str]]] = {
    "climbing": [{"sport": "climbing"}, {"leisure": "climbing"}],
    "yoga":     [{"sport": "yoga"}],
    "park":     [{"leisure": "park"}, {"leisure": "garden"}, {"leisure": "nature_reserve"}],
    "hospital": [{"amenity": "hospital"}],
    "clinic":   [{"amenity": "clinic"}],
    "doctors":  [{"amenity": "doctors"}],
    "pharmacy": [{"amenity": "pharmacy"}],
    "industry": [{"landuse": "industrial"}],
}


def categorise_pois(df: pd.DataFrame, categories: Iterable[str] | None = None) -> pd.DataFrame:
    """Assign a single ``category`` value per POI from its OSM tag dict.

    The OSM tag column must be named ``tags`` and contain a Python dict.
    POIs are matched in the order listed in ``categories`` (default = all
    rules in declaration order); the first match wins. POIs that match no
    rule are dropped. Empty/None tag dicts are dropped.
    """
    cats = list(categories) if categories else list(POI_CATEGORY_RULES.keys())
    rows: list[dict] = []
    for _, row in df.iterrows():
        tags = row.get("tags") or {}
        if not tags:
            continue
        for cat in cats:
            for rule in POI_CATEGORY_RULES[cat]:
                if all(tags.get(k) == v for k, v in rule.items()):
                    rows.append(
                        {
                            "osm_id": row["osm_id"],
                            "osm_type": row["osm_type"],
                            "category": cat,
                            "name": tags.get("name"),
                            "lon": row["lon"],
                            "lat": row["lat"],
                            "tags": tags,
                        }
                    )
                    break
            else:
                continue
            break
    out_df = pd.DataFrame(rows)
    if out_df.empty:
        return out_df
    return OSM_POI_SCHEMA.validate(out_df, lazy=True)


# ---------------------------------------------------------------------------
# Spark / Sedona — native osmpbf reader (Sedona >= 1.7)
# ---------------------------------------------------------------------------

# OSM tag → category mapping for Spark, expressed as a SQL CASE. Mirrors
# POI_CATEGORY_RULES above so the two paths cannot drift silently.
POI_CATEGORY_SQL_CASE = """
    CASE
      WHEN tags['sport'] = 'climbing' OR tags['leisure'] = 'climbing' THEN 'climbing'
      WHEN tags['sport'] = 'yoga' THEN 'yoga'
      WHEN tags['leisure'] IN ('park','garden','nature_reserve') THEN 'park'
      WHEN tags['amenity'] = 'hospital' THEN 'hospital'
      WHEN tags['amenity'] = 'clinic' THEN 'clinic'
      WHEN tags['amenity'] = 'doctors' THEN 'doctors'
      WHEN tags['amenity'] = 'pharmacy' THEN 'pharmacy'
      WHEN tags['landuse'] = 'industrial' THEN 'industry'
      ELSE NULL
    END
"""


def read_pbf_with_sedona(spark, pbf_path: str, *, kind: str = "all"):  # noqa: ANN001
    """Read an OSM PBF via Sedona's native ``osmpbf`` format.

    Sedona 1.9 returns columns ``id``, ``kind`` (node|way|relation),
    ``location`` (struct with ``longitude``/``latitude``), ``tags`` (map),
    ``refs`` (array of node ids), and metadata.
    """
    df = spark.read.format("osmpbf").load(pbf_path)
    if kind == "all":
        return df
    return df.where(f"kind = '{kind[:-1]}'")  # 'nodes' → 'node'


def extract_pois_with_sedona(spark, pbf_path: str):  # noqa: ANN001
    """Materialise a categorised POI table from an OSM PBF."""
    nodes = read_pbf_with_sedona(spark, pbf_path, kind="nodes")
    nodes.createOrReplaceTempView("osm_nodes")
    return spark.sql(f"""
        SELECT id   AS osm_id,
               'node' AS osm_type,
               {POI_CATEGORY_SQL_CASE} AS category,
               tags['name'] AS name,
               location.longitude AS lon,
               location.latitude  AS lat,
               tags
        FROM osm_nodes
        WHERE {POI_CATEGORY_SQL_CASE} IS NOT NULL
          AND location IS NOT NULL
    """)


def extract_stations_with_sedona(spark, pbf_path: str):  # noqa: ANN001
    """Return railway stations / halts as a node-level Sedona DataFrame.

    Used as a GTFS fallback when no real timetable feed is available. Each
    station carries a synthetic ``trips_per_day = 12`` so downstream code
    can treat it uniformly.
    """
    nodes = read_pbf_with_sedona(spark, pbf_path, kind="nodes")
    nodes.createOrReplaceTempView("osm_nodes_stations")
    return spark.sql("""
        SELECT CAST(id AS STRING) AS stop_id,
               COALESCE(tags['name'], CONCAT('station_', id)) AS stop_name,
               location.longitude AS lon,
               location.latitude  AS lat,
               12 AS trips_per_day,
               'osm' AS feed
        FROM osm_nodes_stations
        WHERE tags['railway'] IN ('station','halt')
          AND location IS NOT NULL
    """)


def extract_network_pyrosm(pbf_path: str, *, network_type: str = "driving"):
    """Extract a highway network from an OSM PBF using pyrosm.

    Returns a GeoDataFrame with ``osm_id``, ``kind``, ``subtype``, and
    ``geometry`` (LineString in EPSG:4326). ``network_type`` selects the
    pyrosm preset ("driving" pulls motorways + trunk + primary + ...);
    we tag rows with ``subtype = tags['highway']`` so the SQL filter for
    motorways stays intact.

    Sedona's native ``osmpbf`` reader does not yet emit ready geometries
    for ways, so we use pyrosm as a one-shot bronze build and let Sedona
    take over from the parquet.
    """
    import geopandas as gpd
    import pyrosm

    osm = pyrosm.OSM(pbf_path)
    gdf = osm.get_network(network_type=network_type)
    if gdf is None or gdf.empty:
        return gpd.GeoDataFrame(columns=["osm_id", "kind", "subtype", "geometry"], crs="EPSG:4326")
    subtype_col = "highway" if "highway" in gdf.columns else None
    out = gpd.GeoDataFrame(
        {
            "osm_id": gdf.get("id", gdf.index).astype("int64"),
            "kind": "highway",
            "subtype": gdf[subtype_col].astype(str) if subtype_col else "unknown",
            "geometry": gdf.geometry,
        },
        crs=gdf.crs,
    )
    return out.to_crs(epsg=4326)
