# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 03 — Score, rank, visualise
#
# Reads `data/gold/h3_res8_catalonia.parquet`, applies the weighted
# liveability score, and renders three visualisation cells:
#
# 1. **Lonboard ArcLayer** — full MITMA OD for one weekday (mass density)
# 2. **pydeck H3HexagonLayer** — interactive constraint toggles (notebook)
# 3. **deck.gl raw HTML** — exported `docs/catalonia_liveability.html`
#
# Plus top-10 ranking and sensitivity analysis.

# %%
import os
import sys
from pathlib import Path

REPO = Path("/workspace") if Path("/workspace").exists() else Path.cwd().parent
sys.path.insert(0, str(REPO / "src"))

import pandas as pd
from catmob import scoring, viz, schemas
from sedona.spark import SedonaContext

config = SedonaContext.builder().appName("mitma-sedona-03-viz").getOrCreate()
sedona = SedonaContext.create(config)

# %% [markdown]
# ## 1. Load gold + score

# %%
gold_sdf = sedona.read.format("geoparquet").load(str(REPO / "data/gold/h3_res8_catalonia.parquet"))
gold_pdf = gold_sdf.toPandas()
print(f"Gold rows: {len(gold_pdf):,}")
gold_pdf = scoring.score_dataframe(gold_pdf, preset="default")
print(f"Score range: {gold_pdf.liveability_score.min():.1f} — {gold_pdf.liveability_score.max():.1f}")

# %% [markdown]
# ## 2. Top-10 ranking

# %%
top10 = gold_pdf.nlargest(10, "liveability_score")[
    ["h3_id", "liveability_score", "train_reach_min",
     "climb_min_m", "yoga_min_m", "hospital_min_m",
     "motorway_within_500m"]
]
top10

# %% [markdown]
# ## 3. Sensitivity across weight presets

# %%
sens = scoring.sensitivity_top10(gold_pdf, k=10)
sens

# %% [markdown]
# ## 4. Lonboard — MITMA daily OD for 2024-03-06 (Wed)
#
# Uses the GeoArrow zero-copy handoff (`docs/sedona_sql_patterns.md` §7).
# Builds OD lines with `ST_MakeLine(ST_Centroid(o), ST_Centroid(d))` then
# pre-computes `ST_StartPoint`/`ST_EndPoint` server-side so Lonboard's
# accessors are pure column lookups.

# %%
zones = sedona.read.format("geojson") \
        .load(str(REPO / "data/bronze/mitma/zones/zonificacion_distritos.geojson")) \
        .selectExpr("properties.id AS id", "ST_GeomFromGeoJSON(geometry) AS geom")
zones.createOrReplaceTempView("zones")
sedona.read.parquet(str(REPO / "data/bronze/mitma_parquet/daily")).createOrReplaceTempView("od")

flows_for_day = sedona.sql("""
    SELECT od.origen, od.destino, od.viajes,
           ST_StartPoint(ST_MakeLine(ST_Centroid(o.geom), ST_Centroid(d.geom))) AS source_pt,
           ST_EndPoint  (ST_MakeLine(ST_Centroid(o.geom), ST_Centroid(d.geom))) AS target_pt
    FROM od
    JOIN zones o ON o.id = od.origen
    JOIN zones d ON d.id = od.destino
    WHERE od.fecha = '20240306' AND od.viajes > 50 AND od.origen <> od.destino
""")
print(f"OD lines for 2024-03-06: {flows_for_day.count():,}")

# %%
arc_layer = viz.lonboard_arcs_from_sedona(flows_for_day, get_width="viajes / 1000")
import lonboard
m = lonboard.Map(layers=[arc_layer], view_state={"longitude": 1.7, "latitude": 41.6, "zoom": 8})
m

# %% [markdown]
# ## 5. pydeck — interactive constraint toggles (in-notebook)

# %%
import pydeck as pdk

hex_layer = pdk.Layer(
    "H3HexagonLayer", data=gold_pdf,
    get_hexagon="h3_id",
    get_elevation="liveability_score",
    elevation_scale=80,
    extruded=True, filled=True,
    get_fill_color="[liveability_score * 2.55, 100, 220 - liveability_score * 2.2, 220]",
    pickable=True,
)
pdk.Deck(
    layers=[hex_layer],
    initial_view_state=pdk.ViewState(longitude=1.7, latitude=41.6, zoom=8, pitch=45),
    map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
)

# %% [markdown]
# ## 6. Export hosted deck.gl HTML demo
#
# Replaces the synthetic `docs/preview_deck.html` with the real-data
# `docs/catalonia_liveability.html`. Standalone, opens in any browser,
# served by GitHub Pages.

# %%
arcs_pdf = (
    flows_for_day.selectExpr(
        "ST_X(source_pt) AS source_lon", "ST_Y(source_pt) AS source_lat",
        "ST_X(target_pt) AS target_lon", "ST_Y(target_pt) AS target_lat",
        "viajes AS flow")
    .toPandas()
)

pois_sdf = sedona.read.parquet(str(REPO / "data/bronze/osm/pois.parquet"))
poi_blocks = {
    "climbing": pois_sdf.filter("category='climbing'").select("name","lon","lat").toPandas(),
    "yoga":     pois_sdf.filter("category='yoga'").select("name","lon","lat").toPandas(),
    "hospital": pois_sdf.filter("category='hospital'").select("name","lon","lat").toPandas(),
}

out = viz.export_deck_html(
    REPO / "docs/catalonia_liveability.html",
    gold_pdf, arcs=arcs_pdf, pois=poi_blocks,
    title="Catalonia Liveability — final score",
)
print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
