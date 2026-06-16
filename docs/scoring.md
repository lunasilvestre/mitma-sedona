# The liveability score

A per-hex weighted sum over ~25 features across 6 dimensions, computed at
H3 res-8 across 45,220 Catalonia hexes. Every dimension is now wired to real
data (no NULL-only columns). The score is still a **relative index** — a
starting question, not a guarantee.

[← back to README](../README.md)

## Dimensions

| Dimension | Sources |
|---|---|
| Mobility & accessibility | Euclidean train-station reach (5 km / 3 km metric buffers ≈ 25 / 15 min by bike), Renfe Rodalies + FGC GTFS frequency to BCN |
| Lifestyle | OSM `sport=climbing`, `sport=yoga` |
| Nature | OSM parks/coastline, Copernicus tree-cover density, Natura 2000, GBIF/iNaturalist density |
| Environmental health | XVPCA NO₂/PM₂.₅, Landsat 8/9 LST UHI Δ, VIIRS DNB light pollution |
| Penalties | OSM `landuse=industrial`, E-PRTR registry, motorway proximity |
| Health amenities | OSM hospitals ∪ CatSalut registry, OSM pharmacies |
| Mobility "vibe" | MITMA daily OD inflow/outflow, through-flow ratio |

Full constraint table: [PLAN.md §2](../PLAN.md). The scoring function is
`catmob.scoring.score_hex` (driven by [`configs/weights.yaml`](../configs/weights.yaml));
`score_dataframe` applies it across the gold parquet for each preset.

## How the score is built

Each hex starts at a `base_offset` of **50** points; signed terms add or
subtract, and the result is clipped to **[0, 100]**. Missing values
contribute zero to their term (NULL = neutral). **All distances are computed
in EPSG:25831 (UTM 31N) metres** — the v1 EPSG:4326 degree-buffer bug (~25%
E-W anisotropy at 41°N) is fixed.

### Amenity terms are saturating closeness *rewards*, not distance penalties

This is the central v2 correction. In v1 an amenity's *distance* carried a
**negative** weight, so a hex with **no** amenity (NULL → 0) out-scored a hex
that **had** one but far away (large negative). Absence beat far-presence —
backwards.

v2 replaces those with a saturating positive reward
(`catmob.scoring.closeness_reward`):

```
reward = W_pos × max(0, 1 − dist_m / 10000)
```

Full bonus `W_pos` at 0 m, decaying linearly to 0 at the **10 km catchment
edge**; NULL / absent → 0. Presence is always ≥ absence, and near > far. The
keys in `weights.yaml` now hold the positive max-bonus magnitude `W_pos`.

This form drives four terms — `climb_reward`, `yoga_reward`, `green_reward`,
and `hospital_reward`.

### Default-preset weights

| Term | Form | Default weight |
|---|---|---:|
| `base_offset` | flat start | +50 |
| `train_reach_per_min_under25` | `max(0, 25 − train_reach_min)` × w | +1.4 / min |
| `trains_to_bcn_per_30` | `trains_to_bcn_nearest / 30` × w | +1.0 |
| `climb_reward` | closeness reward (10 km) | +5.0 max |
| `yoga_reward` | closeness reward (10 km) | +4.0 max |
| `green_reward` | closeness reward (10 km) | +6.0 max |
| `sea_within_3km_bonus` | flat bonus if `sea_min_m` < 3 km | +6.0 |
| `tree_cover_pct` | `tree_cover_pct` × w | +0.15 / % |
| `natura2000_within_5km` | flat bonus if within 5 km | +5.0 |
| `biodiversity_obs_log` | `log1p(density)` × w | +1.0 |
| `hospital_reward` | closeness reward (10 km) | +5.0 max |
| `pharmacy_density_log` | `log1p(density)` × w | +1.0 |
| `no2_above_who_per_ugm3` | `max(0, no2 − 20)` × w (WHO 2021) | −0.5 |
| `pm25_above_who_per_ugm3` | `max(0, pm25 − 5)` × w (WHO 2021) | −1.2 |
| `uhi_per_degree` | `max(0, uhi_delta_c)` × w | −2.0 / °C |
| `viirs_radiance` | `viirs_radiance` × w | −0.05 |
| `industry_density` | `industry_density_per_km2` × w | −6.0 |
| `eprtr_inverse_dist` | `(1 / eprtr_facility_min_m)` × w (cap 50 km) | −0.001 |
| `motorway_within_500m` | flat penalty if true | −12.0 |

The amenity/nature terms are positive rewards; genuine harms (air, heat, light,
industry, E-PRTR proximity, motorway) stay negative penalties.

## Presets

Default weights are balanced across the six dimensions. Three presets
re-weight them for different priorities (each inherits `default` and overrides
a subset):

- **`nature_first`** — doubles the nature rewards (`green_reward` 6→**12**,
  `sea_within_3km_bonus` 6→**12**, `tree_cover_pct` 0.15→**0.30**,
  `natura2000_within_5km` 5→**10**, `biodiversity_obs_log` 1→**2**) and halves
  lifestyle (`climb_reward` 5→**2.5**, `yoga_reward` 4→**2**).
- **`quiet_strict`** — roughly triples the noise/heat/air harms
  (`no2_above_who_per_ugm3` −0.5→**−1.5**, `pm25_above_who_per_ugm3`
  −1.2→**−3.6**, `uhi_per_degree` −2→**−6**, `motorway_within_500m`
  −12→**−36**, `industry_density` −6→**−18**, `viirs_radiance` −0.05→**−0.15**)
  and turns the through-flow bias on (`mitma_through_ratio` **−0.5**).
- **`amenity_first`** — doubles lifestyle + health amenities (`climb_reward`
  5→**10**, `yoga_reward` 4→**8**, `hospital_reward` 5→**10**,
  `pharmacy_density_log` 1→**2**) and halves biodiversity/protected-area pull
  (`natura2000_within_5km` 5→**2.5**, `biodiversity_obs_log` 1→**0.5**).

Presets and the sensitivity analysis (`sensitivity_top10`) live in
[`configs/weights.yaml`](../configs/weights.yaml) and notebook 03.

## A note on honesty

The score is a **relative index**, not a guarantee that any hex is a good place
to live. These numbers come from a dev-scope window (7 days of March 2024 OD
data). Coverage is now full for the raster/zonal layers (tree cover, Natura
2000, VIIRS, LST/UHI, GTFS frequency, biodiversity), but some point/station
layers stay genuinely sparse — `yoga_min_m` ~15%, `pm25_ugm3` ~20%,
`train_reach_min` ~26% (see [data_sources.md](data_sources.md)). NULL feature
values are treated as a *distinct neutral state* ("none within catchment"),
never silently rendered as a penalty. The
[results retrospective](results.md) explains how this shows up in the default
ranking.

---

See also: [data_sources.md](data_sources.md) · [results.md](results.md) ·
[visualization.md](visualization.md)
