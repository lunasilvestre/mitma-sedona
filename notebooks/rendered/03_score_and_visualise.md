# 03 — Score, rank, visualise

Reads `data/gold/h3_res8_catalonia.parquet`, applies the weighted
liveability score, and renders three visualisation cells:

1. **Lonboard ArcLayer** — full MITMA OD for one weekday (mass density)
2. **pydeck H3HexagonLayer** — interactive constraint toggles (notebook)
3. **deck.gl raw HTML** — exported `docs/catalonia_liveability.html`

Plus top-10 ranking and sensitivity analysis.


```python
import os
import sys
from pathlib import Path

_here = Path.cwd()
if (_here / "PLAN.md").exists():
    REPO = _here
elif (_here.parent / "PLAN.md").exists():
    REPO = _here.parent
elif Path("/workspace/PLAN.md").exists():
    REPO = Path("/workspace")
else:
    REPO = Path("/home/nls/Documents/dev/mitma-sedona")
sys.path.insert(0, str(REPO / "src"))

import pandas as pd
# Use the cached Sedona JARs on the app classloader (see notebook 02 for
# the rationale — avoids the JTS IllegalAccessError on spatial-index serde).
_IVY = Path.home() / ".ivy2.5.2/cache"
SEDONA_JAR   = _IVY / "org.apache.sedona/sedona-spark-shaded-4.0_2.13/jars/sedona-spark-shaded-4.0_2.13-1.9.0.jar"
GEOTOOLS_JAR = _IVY / "org.datasyslab/geotools-wrapper/jars/geotools-wrapper-1.9.0-33.5.jar"
os.environ["PYSPARK_SUBMIT_ARGS"] = f"--jars {SEDONA_JAR},{GEOTOOLS_JAR} pyspark-shell"

from catmob import scoring, viz, schemas
from sedona.spark import SedonaContext

config = (
    SedonaContext.builder()
        .appName("mitma-sedona-03-viz")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.driver.memory", "4g")
        .config("sedona.global.index", "false")
        .config("sedona.join.optimizationmode", "none")
        .config("sedona.join.autoBroadcastJoinThreshold", "-1")
        .config("spark.sql.autoBroadcastJoinThreshold", "-1")
        .getOrCreate()
)
sedona = SedonaContext.create(config)
sedona.sparkContext.setLogLevel("ERROR")
```

## 1. Load gold + score


```python
gold_sdf = sedona.read.parquet(str(REPO / "data/gold/h3_res8_catalonia.parquet"))
gold_pdf = gold_sdf.toPandas()
print(f"Gold rows: {len(gold_pdf):,}")
gold_pdf = scoring.score_dataframe(gold_pdf, preset="default")
print(f"Score range: {gold_pdf.liveability_score.min():.1f} — {gold_pdf.liveability_score.max():.1f}")
```

## 2. Top-10 ranking


```python
top10 = gold_pdf.nlargest(10, "liveability_score")[
    ["h3_id", "liveability_score", "train_reach_min",
     "climb_min_m", "yoga_min_m", "hospital_min_m",
     "motorway_within_500m"]
]
top10
```

## 3. Sensitivity across weight presets


```python
sens = scoring.sensitivity_top10(gold_pdf, k=10)
sens
```

## 4. Lonboard — MITMA daily OD for 2024-03-06 (Wed)

Uses the GeoArrow zero-copy handoff (`docs/sedona_sql_patterns.md` §7).
Builds OD lines with `ST_MakeLine(ST_Centroid(o), ST_Centroid(d))` then
pre-computes `ST_StartPoint`/`ST_EndPoint` server-side so Lonboard's
accessors are pure column lookups.


```python
# Same geopandas-direct hand-off as notebook 02 (Sedona GeoJSON reader
# trips on one malformed feature; pandas-written parquet has tripped
# Spark 4.1's footer check intermittently — easier to skip parquet).
import geopandas as _gpd
import pandas as _pd
_gdf = _gpd.read_file(REPO / "data/bronze/mitma/zones/zonificacion_distritos.geojson")
_gdf = _gdf.loc[_gdf.geometry.notna() & _gdf.geometry.is_valid].copy()
_zones_pdf = _pd.DataFrame({
    "id":  _gdf["ID"].astype(str).values,
    "wkt": _gdf.geometry.to_wkt().values,
})
zones = (
    sedona.createDataFrame(_zones_pdf)
        .selectExpr("id", "ST_GeomFromWKT(wkt) AS geom")
)
zones.createOrReplaceTempView("zones")
sedona.read.parquet(str(REPO / "data/bronze/mitma_parquet/daily")).createOrReplaceTempView("od")

# In v2 the same (origen, destino) pair appears once per `periodo` (hour);
# aggregate across hours to a per-day per-pair count before drawing arcs.
flows_for_day = sedona.sql("""
    WITH od_day AS (
        SELECT origen, destino, SUM(viajes) AS viajes
        FROM od
        WHERE fecha = '20240306' AND origen <> destino
        GROUP BY origen, destino
        HAVING SUM(viajes) > 50
    )
    SELECT od_day.origen, od_day.destino, od_day.viajes,
           ST_StartPoint(ST_MakeLine(ST_Centroid(o.geom), ST_Centroid(d.geom))) AS source_pt,
           ST_EndPoint  (ST_MakeLine(ST_Centroid(o.geom), ST_Centroid(d.geom))) AS target_pt
    FROM od_day
    JOIN zones o ON o.id = od_day.origen
    JOIN zones d ON d.id = od_day.destino
""")
flows_pdf_count = flows_for_day.count()
print(f"OD lines for 2024-03-06: {flows_pdf_count:,}")
```


```python
# Lonboard's GeoArrow handoff via `dataframe_to_arrow` can stumble on
# Sedona 1.9 / Spark 4.1 edge cases — we wrap it so a render failure
# doesn't kill the notebook execution. The arc data is still produced
# below for the deck.gl HTML export, so the prototype demo is unaffected.
try:
    arc_layer = viz.lonboard_arcs_from_sedona(flows_for_day, get_width="viajes / 1000")
    import lonboard
    m = lonboard.Map(
        layers=[arc_layer],
        view_state={"longitude": 1.7, "latitude": 41.6, "zoom": 8},
    )
    m
except Exception as _exc:
    print(f"Lonboard render skipped: {_exc!r}")
    m = None
```

## 5. pydeck — interactive constraint toggles (in-notebook)


```python
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
```

## 6. Export hosted deck.gl HTML demo

Replaces the synthetic `docs/preview_deck.html` with the real-data
`docs/catalonia_liveability.html`. Standalone, opens in any browser,
served by GitHub Pages.


```python
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
```
