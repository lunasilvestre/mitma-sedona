# Visualisation

How the liveability layers get rendered — from the Sedona/GeoArrow handoff to
the browser-native deck.gl pages.

[← back to README](../README.md)

## The interactive geo-browser

The headline artifact is **[explore.html](explore.html)** — a study geo-browser
that puts the liveability index on an interactive map beside a panel that
explains the analysis and links back to the repo (it showcases the analysis; it
does not embed source code).

- **Selectable basemaps:** dark semi-transparent panels over a switchable
  basemap — Satellite (keyless Esri World Imagery), Dark (CARTO dark matter),
  Light (CARTO Positron), or OSM (OpenFreeMap Bright) — with the 45,220 H3 res-8
  hexes on top, and a **hex-opacity slider** to fade the grid against the map.
- **Layers + controls:** the liveability output (default colouring), a **preset**
  selector (default / nature_first / quiet_strict / amenity_first) and a
  **metric** selector that recolours the hexes by any of **15 analytic
  dimensions** — MITMA inflow and through-ratio (diverging RdBu), train reach
  and trains-to-BCN, climbing reach, tree cover, Natura 2000, biodiversity
  density, NO₂, PM2.5, UHI delta, VIIRS night-light, E-PRTR proximity, industry
  density and motorway proximity. Recolouring is consistent: **bright = more
  liveable** across every metric. A **2.5D extrude** toggle and input layers for
  MITMA OD arcs and OSM amenities round out the controls.
- **Honest by design:** liveability is stated as a *relative index, a starting
  question, not a guarantee* — and sparse coverage is shown as a distinct
  slate-grey "none within reach" state, never as 0. The left study panel carries
  the headline numbers (45,220 hexes; default-preset median ≈ 59), per-column
  coverage bars, scope caveats and a Mermaid pipeline diagram.

The page is built from pinned CDN libraries only (deck.gl 9.3.2, MapLibre GL
4.7.1, h3-js 4.1.0, Mermaid 11), with **zero build step** — it serves straight
from GitHub Pages at
<https://lunasilvestre.github.io/mitma-sedona/explore.html>.

> **Scope caveat.** The data window is dev-scope (7 days of March 2024 for the
> hourly MITMA flows), coverage is still sparse for a few amenity-proximity
> layers (yoga, sea, hospital), and the liveability score is a *relative* index
> rather than a guarantee. The scoring itself is now sound: amenity terms are
> saturating closeness rewards (near beats far beats absent) and all distances
> are computed in EPSG:25831 metres.

## Visualisation stack

| Layer | Library | Why |
|---|---|---|
| Million-arc raw flow | **Lonboard** (GeoArrow handoff from Sedona) | browser-native at scale |
| Interactive constraint toggles | **pydeck** + ipywidgets | python state ↔ js layers |
| Hosted 3D demo | **deck.gl** raw HTML | zero install for viewers |
| H3 score (extruded) | `H3HexagonLayer` | score = height + colour |
| MITMA OD | `ArcLayer` (curvature + colour gradient) | direction encoded |
| POIs | `ScatterplotLayer` | standard, fast |
| Aggregated flows | `LineLayer` | cleaner at low zoom |

## Standalone pages

- **[preview_deck.html](preview_deck.html)** — synthetic-data preview, renders
  with no backend. Exercises `H3HexagonLayer` (extruded score), `ArcLayer`
  (MITMA OD from BCN), `ScatterplotLayer` (climbing & yoga POIs), with layer
  toggles, sliders, and hover tooltips.
- **[catalonia_liveability.html](catalonia_liveability.html)** — the earlier
  self-contained deck.gl page built from the gold parquet, kept for reference.
  Superseded by [explore.html](explore.html).

---

See also: [scoring.md](scoring.md) · [results.md](results.md) ·
[architecture.md](architecture.md)
