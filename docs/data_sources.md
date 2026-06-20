# Data sources

Every upstream dataset, what it contributes, and its licence. The full catalog
with download links lives in [data/README.md](../data/README.md).

[← back to README](../README.md)

## Sources & licences

The **Wired** column tracks whether the source is ingested through the
bronze→silver→gold pipeline and produces real feature columns in the published
gold grid (`data/gold/h3_res8_catalonia_v2.parquet`). The v2.3 grid is wired
across mobility, amenities, nature, environmental health, and penalties —
**every dimension carries real signal** (per-column coverage varies; see the
manifest fractions below).

| Source | What | Licence | Wired |
|---|---|---|---|
| MITMA v2 OD distritos (daily + hourly) | mobility flows (inflow/outflow/through-ratio) | MITMA Open Data ≈ CC BY 4.0 | ✅ |
| OSM Cataluña PBF | POIs (climb/yoga/hospital/pharmacy) + network (motorway) + boundaries | ODbL | ✅ |
| Renfe Rodalies + FGC GTFS | train frequencies (`trains_per_day_nearest`, `trains_to_bcn_nearest`) | open | ✅ |
| EEA + XVPCA + Copernicus CAMS | air quality (`no2_ugm3`, `pm25_ugm3`) | CC BY 2.5 / CC BY 4.0 / Copernicus | ✅ XVPCA stations |
| Landsat 8/9 LST (Microsoft Planetary Computer STAC) | urban heat island (`uhi_delta_c`, `lst_summer_median_c`) | open | ✅ |
| EEA Natura 2000 + Copernicus Tree-Cover-Density 2018 | nature (`natura2000_within_5km`, `tree_cover_pct`) | CC BY 4.0 | ✅ |
| iNaturalist / GBIF | biodiversity species observations (`biodiversity_obs_density`) | CC BY 4.0 / CC BY-NC | ✅ (density per hex, 100% coverage; ~6.2k hexes non-zero) |
| E-PRTR + VIIRS DNB | non-air pollution (`eprtr_facility_min_m`, `viirs_radiance`) | CC BY 2.5 / open | ✅ |
| CatSalut / OSM hospital + pharmacy POIs | health amenities (`hospital_min_m`, `pharmacy_density_per_km2`) | CC BY 4.0 | ✅ |

Full catalog: [data/README.md](../data/README.md).

Coverage is non-uniform — the published manifest
([docs/story_data/manifest.json](story_data/manifest.json)) records per-column
fractions. Layers like tree-cover, Natura 2000, VIIRS, UHI and the MITMA flows
cover 100% of the 45,220 hexes; station-interpolated air quality is sparser
(NO₂ ≈ 41%, PM2.5 ≈ 20%, both station-net limited), and several amenity-proximity
layers are partial (climb ≈ 49%, hospital ≈ 30%, yoga ≈ 15%, sea ≈ 6%). A NULL is
treated as neutral, never as a zero penalty.

## Data windows

Two MITMA windows are in play, for two distinct purposes — keep them apart:

- **Liveability-score mobility input — 7-day dev window, 2024-03-04..10.** The
  published score's `mitma_inflow_daily` / `outflow` / `through_ratio` terms are
  built from this dev-scope slice of Q1+Q2 2024 daily + all-March-2024 hourly
  MITMA (`--scope dev`; full Q1+Q2 fetch ≈ 3.5 GB bronze). This is the gold the
  shipped v2.3 score was computed on (see [results.md](results.md)).
- **Deep-Spark mobility/season display layers — 89-day 2025 window.** The
  additive Sedona deep-Spark layers (24-hour rhythm, weekend hotspots, KMeans
  typology, geodemographics, OD arcs, and the month/season dimension) are built
  from a separate **390,238,741-row** OD scan over **89 days of 2025** — three
  calendar month-windows, **Feb (28d) + May (31d) + Jun (30d)** — attributed to
  hexes by the area-weighted dasymetric crosswalk. These are a *month-window
  comparison, not a seasonal/climate average*, and they ship at **weight 0** —
  the default published score is unchanged. Full method:
  [why_spark_sedona.md](why_spark_sedona.md).

## Attribution

Each upstream dataset retains its own licence; see the attribution block in the
[README](../README.md#attribution).

---

See also: [scoring.md](scoring.md) · [architecture.md](architecture.md) ·
[results.md](results.md)
