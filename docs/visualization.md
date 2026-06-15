# Visualisation

How the liveability layers get rendered — from the Sedona/GeoArrow handoff to
the browser-native deck.gl pages.

[← back to README](../README.md)

## The interactive geo-browser

The headline artifact is **[explore.html](explore.html)** — a study geo-browser
that puts the liveability index on an interactive map beside a panel that
explains the analysis and links back to the repo (it showcases the analysis; it
does not embed source code).

- **Dark UI over satellite:** dark semi-transparent panels over keyless Esri
  World Imagery, with 45,220 H3 res-8 hexes on top.
- **Layers + controls:** the liveability output (viridis), a **preset** selector
  (default / nature_first / quiet_strict / amenity_first) and a **metric**
  selector that recolours the hexes by an analytic dimension (MITMA inflow,
  through-ratio on a diverging RdBu ramp, train/climbing reach as presence,
  industry density, motorway proximity); a **2.5D extrude** toggle; and input
  layers for MITMA OD arcs and OSM amenities.
- **Honest by design:** liveability is stated as a *relative index, a starting
  question, not a guarantee* — and sparse coverage is shown as a distinct
  slate-grey "none within reach" state, never as 0. The left panel carries the
  headline numbers, coverage bars, scope caveats and a Mermaid pipeline diagram.

The page is built from pinned CDN libraries only (deck.gl 9.3.2, MapLibre GL
4.7.1, h3-js 4.1.0, Mermaid 11), with **zero build step** — it serves straight
from GitHub Pages.

> This is a **dev-scope prototype**. Coverage is sparse for several amenity
> layers, and the liveability score is a relative index rather than a guarantee.

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
