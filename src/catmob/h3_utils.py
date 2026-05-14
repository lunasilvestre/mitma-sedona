"""H3 grid utilities — generation, aggregation, and Sedona integration.

The two roads: pure-Python (``h3-py``) for unit tests and small grids; pure
SQL (``ST_H3CellIDs``, ``ST_H3ToGeom``) for Spark/Sedona at scale (see
docs/sedona_sql_patterns.md §1).

The analytical grain for mitma-sedona is **H3 res 8** (~0.7 km² hexes).
Catalonia at res 8 ≈ 50,000 hexes after boundary clipping.
"""
from __future__ import annotations

from typing import Iterable

#: Catalonia bounding box (lon_min, lat_min, lon_max, lat_max), EPSG:4326.
CATALONIA_BBOX: tuple[float, float, float, float] = (0.15, 40.50, 3.35, 42.90)
DEFAULT_RES = 8


def cells_for_bbox(
    bbox: tuple[float, float, float, float] = CATALONIA_BBOX,
    res: int = DEFAULT_RES,
) -> list[str]:
    """Generate every H3 cell whose centroid is inside ``bbox``.

    Pure-Python; uses h3-py polyfill for the bounding rectangle. For
    irregular boundaries (the Catalonia coastline + Pyrenees), prefer the
    SQL path :func:`cells_for_polygon_sql` which uses
    ``ST_H3CellIDs(geom, res, fullCover=true)``.
    """
    import h3

    lon_min, lat_min, lon_max, lat_max = bbox
    polygon = h3.LatLngPoly(
        [
            (lat_min, lon_min),
            (lat_min, lon_max),
            (lat_max, lon_max),
            (lat_max, lon_min),
        ]
    )
    return list(h3.polygon_to_cells(polygon, res))


def cell_to_centroid(cell: str) -> tuple[float, float]:
    """Return (lon, lat) of the H3 cell centroid."""
    import h3

    lat, lon = h3.cell_to_latlng(cell)
    return (lon, lat)


def cell_to_boundary(cell: str) -> list[tuple[float, float]]:
    """Return the H3 cell boundary as a list of (lon, lat) vertices."""
    import h3

    return [(lng, lat) for lat, lng in h3.cell_to_boundary(cell)]


def cells_to_dataframe(cells: Iterable[str]):
    """Materialise an H3 cell iterable as a DataFrame with centroid columns."""
    import pandas as pd

    rows = []
    for c in cells:
        lon, lat = cell_to_centroid(c)
        rows.append({"h3_id": c, "lon_centroid": lon, "lat_centroid": lat})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Sedona / Spark path — see docs/sedona_sql_patterns.md §1
# ---------------------------------------------------------------------------

H3_GRID_SQL = """
WITH cell_ids AS (
    SELECT DISTINCT cell_id
    FROM {distritos_view}
    LATERAL VIEW EXPLODE(ST_H3CellIDs(geometry, {res}, true)) AS cell_id
),
hexes AS (
    SELECT cell_id AS h3_id,
           ST_H3ToGeom(ARRAY(cell_id))[0] AS geometry
    FROM cell_ids
)
SELECT h3_id,
       ST_X(ST_Centroid(geometry)) AS lon_centroid,
       ST_Y(ST_Centroid(geometry)) AS lat_centroid,
       geometry
FROM hexes
WHERE ST_Intersects(geometry,
                    (SELECT ST_Union_Aggr(geometry) FROM {boundary_view}))
"""


def build_grid_sql(distritos_view: str = "distritos", boundary_view: str = "catalonia_boundary",
                   res: int = DEFAULT_RES) -> str:
    """Render the H3-grid SQL for a Sedona session."""
    return H3_GRID_SQL.format(
        distritos_view=distritos_view,
        boundary_view=boundary_view,
        res=res,
    )
