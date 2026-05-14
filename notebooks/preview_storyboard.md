# Notebook 03 — Visual Storyboard (Preview)

This file is the **storyboard** for `03_score_and_visualise.ipynb`. It documents
the planned cell structure with concrete prose, code skeletons, and
visualisation choices so reviewers can see the shape of the deliverable
before the notebook is filled in. It pairs with the standalone deck.gl
preview at [`docs/preview_deck.html`](../docs/preview_deck.html).

The notebook is the **technical narrative**; the HTML is the **shareable
demo**. Same data, two surfaces.

---

## Cell 1 — Goal & inputs

> ### Where in Catalonia could I live well?
>
> This notebook scores every H3 res-8 hex in Catalonia on a multi-criteria
> liveability index combining mobility (train + bike reach to BCN), lifestyle
> amenities (climbing, yoga), nature (green/sea/biodiversity), environmental
> health (NO₂, PM₂.₅, urban heat island, light pollution), and penalties
> (industry density, motorway noise, E-PRTR facilities).

| Inputs | Source | Path |
|---|---|---|
| Gold hex grid | notebook 02 | `data/gold/h3_res8_catalonia.parquet` |
| MITMA OD daily | bronze | `data/bronze/mitma/daily/2024-10/*.parquet` |
| MITMA OD hourly | bronze | `data/bronze/mitma/hourly/2024-10/*.parquet` |
| Train station registry | silver | `data/silver/train_stations.parquet` |

| Outputs |
|---|
| Top-10 ranked hex table (markdown) |
| Lonboard cell — full MITMA daily flow density |
| pydeck cell — interactive constraint-toggle map |
| `docs/catalonia_liveability.html` — standalone deck.gl 3D demo |
| 4× PNG screenshots → `docs/screenshots/` for the README |

Wall-clock: ~15 min.

---

## Cell 2 — SedonaContext + Lonboard import

```python
from pathlib import Path
import lonboard
import pydeck as pdk
import pandas as pd
import geopandas as gpd
from sedona.spark import SedonaContext

sedona = SedonaContext.create(spark_session)
gold = (
    sedona.read.parquet("data/gold/h3_res8_catalonia.parquet")
    .selectExpr("*", "ST_GeomFromWKB(geometry_wkb) AS geometry")
)
print(f"Gold rows: {gold.count():,}")
gold.printSchema()
```

---

## Cell 3 — Score function (transparent, tweakable weights)

```python
WEIGHTS = {
    # Accessibility
    "train_reach":          1.4,   # max(0, 20 - reach_min)
    "trains_to_bcn":        0.033, # service / 30
    # Lifestyle
    "climbing":            -0.005,  # -dist_m / 200
    "yoga":                -0.004,
    # Nature
    "green":               -0.005,
    "sea_bonus":            6.0,   # if dist < 3km
    "tree_cover":           0.15,  # +pct * 0.15
    "natura2000":           5.0,
    # Penalties
    "industry":            -6.0,
    "motorway_500m":      -12.0,
    # Environmental health
    "no2_above_who":       -0.5,   # per µg/m³ above 20
    "pm25_above_who":      -1.2,   # per µg/m³ above 5
    "uhi":                 -2.0,   # per °C
    "viirs_radiance":      -0.05,  # light pollution
    # Health amenities
    "hospital":            -0.0025,
    # Mobility vibe
    "mitma_through_ratio":  0.0,   # disabled by default; turn on for "quiet" bias
}
```

The full formula lives in `src/catmob/scoring.py`; this cell shows only the
weight vector so reviewers can see how each dimension contributes.

---

## Cell 4 — Lonboard: MITMA daily flow density (the "what's happening" view)

> The **baseline** view: every MITMA OD pair for a single representative
> weekday (2024-10-15), drawn as an Arc layer over CARTO dark matter, with
> GeoArrow handoff from Sedona for zero-copy rendering.

```python
from sedona.utils.adapter import sedona_dataframe_to_arrow

od_arrow = sedona_dataframe_to_arrow(
    sedona.sql("""
        SELECT origin_geom, destination_geom, viajes
        FROM mitma_od_daily
        WHERE fecha = '20241015' AND viajes > 50
    """)
)
arc = lonboard.ArcLayer.from_arrow(
    od_arrow,
    get_source_position="origin_geom",
    get_target_position="destination_geom",
    get_source_color=[136, 212, 255, 200],
    get_target_color=[255, 107, 157, 200],
    get_width="viajes / 80",
)
lonboard.Map(layers=[arc], view_state={"longitude": 1.7, "latitude": 41.6, "zoom": 8})
```

**Why Lonboard for this layer:** GeoArrow path scales to millions of arcs
without down-sampling, which a pure pydeck/JSON serialisation choke on.

---

## Cell 5 — pydeck: interactive constraint toggles

> The **exploration** view: each constraint on a checkbox, weights on
> sliders. Move the slider, watch the candidate set shrink in real time.

```python
from ipywidgets import Checkbox, FloatSlider, VBox, HBox, interactive_output

# Map to pydeck Layer factories
def make_layer(cat, df):
    return pdk.Layer(
        "ScatterplotLayer", df, get_position=["lon", "lat"],
        get_radius=200, get_fill_color=[255, 107, 107, 220], pickable=True,
    )

# H3HexagonLayer for the score (extruded by score)
hex_layer = pdk.Layer(
    "H3HexagonLayer", gold_pdf,
    get_hexagon="h3_id", get_elevation="liveability_score",
    elevation_scale=80, extruded=True, filled=True,
    get_fill_color="[liveability_score * 2.5, 100, 200 - liveability_score * 2, 220]",
    pickable=True,
)
deck = pdk.Deck(
    layers=[hex_layer], initial_view_state=pdk.ViewState(
        longitude=1.7, latitude=41.6, zoom=8, pitch=45,
    ),
    map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
)
deck.show()
```

**Why pydeck for this layer:** notebook-embedded interactivity, Python state
binds to JS layers, ipywidgets feel native.

---

## Cell 6 — deck.gl raw: hosted 3D demo

> The **shareable** artefact. We export the gold hex grid + arcs to a
> single self-contained HTML page and write it to `docs/`. GitHub Pages
> serves it; the README links to it.

```python
from catmob.viz import export_deck_html

export_deck_html(
    out_path=Path("docs/catalonia_liveability.html"),
    hexes=gold_pdf,
    arcs=arcs_pdf,
    pois={"climbing": climb_pdf, "yoga": yoga_pdf},
    title="Catalonia Liveability — final scoring",
)
```

**Why deck.gl raw HTML for this layer:** zero dependencies, opens in any
browser, no Jupyter/Python needed by the recipient. `docs/preview_deck.html`
in this repo is a *preliminary* version of this output, hand-built with
synthetic data so reviewers can see the planned look-and-feel today.

---

## Cell 7 — Top-10 ranked table

```python
top10 = (
    gold_pdf.sort_values("liveability_score", ascending=False)
            .head(10)
            .merge(municipalities_pdf, on="municipio_id")
            [["rank", "municipi", "liveability_score",
              "train_reach_min", "trains_to_bcn",
              "climb_min_m", "yoga_min_m", "green_min_m", "sea_min_m",
              "no2_ugm3", "pm25_ugm3", "uhi_delta_c"]]
)
top10.style.background_gradient(subset=["liveability_score"], cmap="viridis")
```

A short prose paragraph after the table explains *why* each top hex earned
its rank — the dimensions that pulled it up.

---

## Cell 8 — Sensitivity analysis

> How robust is the top-10 to weight changes? We re-run with four
> alternative weight vectors and tabulate the Jaccard overlap.

| Weight preset | Description |
|---|---|
| `default` | balanced (the canonical one) |
| `nature_first` | climb/yoga halved, green/sea/tree-cover doubled |
| `quiet_strict` | motorway/UHI/NO₂ tripled |
| `amenity_first` | climb/yoga doubled, biodiversity halved |

```python
overlaps = sensitivity_jaccard(gold_pdf, presets=PRESETS, k=10)
overlaps  # DataFrame: preset × preset
```

A score is **trustworthy** when the top-10 is mostly stable across
sensible weight presets — i.e. Jaccard ≥ 0.6 against `default`.

---

## Cell 9 — Caveats & honest limitations

A short prose section, not code, listing what this analysis does *not*
capture:

- Yoga POI completeness in OSM is uneven; results biased toward visible-on-OSM.
- MITMA does not report mode share; we infer "low car-dependence" indirectly.
- Air quality interpolated from sparse stations + CAMS coarse grid; ±5 µg/m³ uncertainty.
- UHI computed from one-summer Landsat composite; inter-annual variability not addressed.
- Personal preferences (school quality, social network) are not in the model.

---

## Visualisation stack — best representation matrix

| Use case | Library | Layer types | Why |
|---|---|---|---|
| Million-arc raw flow | **Lonboard** | `ArcLayer` (GeoArrow) | Browser-native, no down-sampling; only Lonboard handles the volume cleanly inside Jupyter |
| Aggregated OD flow lines | Lonboard | `LineLayer` from H3 OD aggregation | Cleaner than raw arcs at low zoom |
| Bidirectional OD with magnitude | deck.gl | `ArcLayer` + curvature | Source/target colour gradient encodes direction |
| Multi-flow merged channels | deck.gl-community | `FlowMapLayer` | Best-in-class OD cartography (Boyandin); use for the BCN-region close-up cell |
| Hex liveability score | deck.gl + pydeck | `H3HexagonLayer` (extruded) | Score → height + colour double-encoding |
| Constraint toggles (interactive) | **pydeck** | per-layer `Layer` + `ipywidgets` | Python state ↔ JS layers, feels native in JupyterLab |
| Hosted standalone demo | **deck.gl raw HTML** | the same layers, exported | Zero install for viewers, GitHub Pages friendly |
| POI clusters | deck.gl | `ScatterplotLayer` | Standard, fast, supports per-point styling |
| Train network | deck.gl | `ScatterplotLayer` (stations) + `PathLayer` (lines) | PathLayer for routes when GTFS shapes are loaded |
| Heat / UHI gradient | Lonboard | `BitmapLayer` from rasterised LST GeoTIFF | One layer, 10 m pixels, sub-second render |

The deck.gl preview at `docs/preview_deck.html` already exercises 4 of
these (H3HexagonLayer, ArcLayer, LineLayer, ScatterplotLayer) on synthetic
data; production swaps the synthetic source for `data/gold/*.parquet`.
