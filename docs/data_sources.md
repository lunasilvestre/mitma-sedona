# Data sources

Every upstream dataset, what it contributes, and its licence. The full catalog
with download links lives in [data/README.md](../data/README.md).

[← back to README](../README.md)

## Sources & licences

The **Wired** column tracks whether the source is ingested through the
bronze→silver→gold pipeline and produces real feature columns in the published
gold grid (`data/gold/h3_res8_catalonia.parquet`). The v2.3 grid is wired across
mobility, amenities, nature, environmental health, and penalties; only the GBIF
biodiversity *species* observations remain un-landed (the column is held NULL
and contributes 0 — Natura 2000 and tree-cover stand in for the nature signal).

| Source | What | Licence | Wired |
|---|---|---|---|
| MITMA v2 OD distritos (daily + hourly) | mobility flows (inflow/outflow/through-ratio) | MITMA Open Data ≈ CC BY 4.0 | ✅ |
| OSM Cataluña PBF | POIs (climb/yoga/hospital/pharmacy) + network (motorway) + boundaries | ODbL | ✅ |
| Renfe Rodalies + FGC GTFS | train frequencies (`trains_per_day_nearest`, `trains_to_bcn_nearest`) | open | ✅ |
| EEA + XVPCA + Copernicus CAMS | air quality (`no2_ugm3`, `pm25_ugm3`) | CC BY 2.5 / CC BY 4.0 / Copernicus | ✅ XVPCA stations |
| Landsat 8/9 LST (Microsoft Planetary Computer STAC) | urban heat island (`uhi_delta_c`, `lst_summer_median_c`) | open | ✅ |
| EEA Natura 2000 + Copernicus Tree-Cover-Density 2018 | nature (`natura2000_within_5km`, `tree_cover_pct`) | CC BY 4.0 | ✅ |
| iNaturalist / GBIF | biodiversity species observations (`biodiversity_obs_density`) | CC BY 4.0 / CC BY-NC | ⏳ not landed (NULL → 0) |
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

## Data window

Default data window = **Q1+Q2 2024 daily MITMA + all March 2024 hourly MITMA**
(full-scale, ~3.5 GB MITMA bronze). Use `--scope dev` for fast local iteration
— the prototype run in [results.md](results.md) used the dev window
(2024-03-04..10).

## Attribution

Each upstream dataset retains its own licence; see the attribution block in the
[README](../README.md#attribution).

---

See also: [scoring.md](scoring.md) · [architecture.md](architecture.md) ·
[results.md](results.md)
