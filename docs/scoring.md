# The liveability score

A per-hex weighted sum over ~25 features across 6 dimensions, computed at
H3 res-8. The score is a **relative index** — a starting question, not a
guarantee.

[← back to README](../README.md)

## Dimensions

| Dimension | Sources |
|---|---|
| Mobility & accessibility | Valhalla bike isochrones from train stations, Renfe + FGC GTFS frequency to BCN |
| Lifestyle | OSM `sport=climbing`, `sport=yoga` |
| Nature | OSM parks/forest/coastline, Copernicus tree cover, WDPA / Natura 2000, iNaturalist density |
| Environmental health | EEA + XVPCA + CAMS NO₂/PM₂.₅, Landsat 8/9 LST UHI Δ, VIIRS DNB light pollution |
| Penalties | OSM `landuse=industrial`, E-PRTR registry, motorway proximity |
| Health amenities | OSM hospitals/pharmacies, CatSalut registry |
| Mobility "vibe" | MITMA daily OD inflow/outflow, through-flow ratio |

Full constraint table: [PLAN.md §2](../PLAN.md).

## Weights & presets

Default weights are balanced across the six dimensions. Three presets
re-weight the dimensions for different priorities:

- **`nature_first`** — favours green/sea proximity and biodiversity.
- **`quiet_strict`** — penalises noise and industry harder.
- **`amenity_first`** — favours health amenities and lifestyle POIs.

Presets and the sensitivity analysis live in
[`configs/weights.yaml`](../configs/weights.yaml) and notebook 03. The scoring
function is `catmob.scoring.score_hex` (driven by the weights YAML), and
`score_dataframe` applies it across the gold parquet for each preset.

## A note on honesty

The score is a **relative index**, not a guarantee that any hex is a good place
to live. Sparse amenity coverage (see [data_sources.md](data_sources.md)) means
many hexes carry NULL feature values that are treated as a *distinct state*
("none within cap"), never silently rendered as 0. The
[results retrospective](results.md) explains how this shows up in the default
ranking.

---

See also: [data_sources.md](data_sources.md) · [results.md](results.md) ·
[visualization.md](visualization.md)
