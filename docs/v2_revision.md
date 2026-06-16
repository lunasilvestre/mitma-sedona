# v2 Revision Plan — From Dev-Scope Prototype to a Complete Liveability Index

**Status:** ✅ LARGELY EXECUTED — shipped as v2.3, live · **Date:** 2026-06-15 (plan) → 2026-06-16 (shipped) · **Author:** Nelson + Claude
**Scope:** path from the v1 dev-scope prototype (12 of 24 feature columns real, buffer-based mobility) to a fully populated, defensible Catalonia liveability index.

> ## Execution status (read first)
> This was the roadmap; **most of it is now DONE** and live at [explore.html](https://lunasilvestre.github.io/mitma-sedona/explore.html). The shipped v2.3 gold layer (`scripts/run_gold_v2.py` → `data/gold/h3_res8_catalonia_v2.parquet`, 45,220 hexes; coverage in `docs/story_data/manifest.json`) carries **all 24 feature dimensions on real data**:
> - **DONE — scoring correctness:** the v1 amenity *distance-penalty* flaw (absence out-scored far-presence) is fixed — `climb`/`yoga`/`green`/`hospital` are now **saturating positive closeness rewards** (`scoring.py:closeness_reward`, 10 km catchment, weight keys `climb_reward`/`yoga_reward`/`green_reward`/`hospital_reward` in `configs/weights.yaml`). Penalties stay negative (motorway, industry, eprtr, no2/pm25/uhi/viirs). All distances are computed in **EPSG:25831 metres** (the §5 reprojection fix landed) — the v1 degree-buffer anisotropy is gone.
> - **DONE — features wired (coverage from manifest):** `green_min_m` (100%), `tree_cover_pct` (100%), `natura2000_within_5km` (100%, 231 sites), `biodiversity_obs_density` (100%), `pharmacy_density_per_km2` (100%), `eprtr_facility_min_m` (98%, 107 facilities), `no2_ugm3` (~41%) + `pm25_ugm3` (~20%) from XVPCA stations, `lst_summer_median_c`+`uhi_delta_c` (100%), `viirs_radiance` (100%), real GTFS `trains_per_day_nearest` (91 distinct values — no longer the constant 12) + `trains_to_bcn_nearest` (72 distinct), `sea_min_m` (coastal strip). The scoring function needed **no change** — only data, exactly as §6 predicted.
> - **ONLY REMAINING — Wave 3:** `train_reach_min` via **Valhalla bike isochrones** (§3.1, §5) is still the metric circular-buffer fallback; the `VALHALLA_URL`-gated isochrone upgrade is the one deferred item, off the critical path. Optional layered extras also still open: EEA/CAMS air gap-fill on top of XVPCA-only NO₂/PM₂.₅.
>
> Everything below is the original plan text, kept intact for the reasoning and per-feature wiring detail; treat **L-effort Valhalla** as the only "pending" row in the §4 roadmap and §2 table.

> Cross-references: [`PLAN.md`](../PLAN.md) §2 (scoring table) and §15 (prototype scope), [`docs/sedona_sql_patterns.md`](sedona_sql_patterns.md) (canonical SQL idioms), [`NOTES_FROM_PROTOTYPE_RUN.md`](../NOTES_FROM_PROTOTYPE_RUN.md) (the constraints discovered while executing M6→M8), [`src/catmob/schemas.py`](../src/catmob/schemas.py) `GOLD_HEX_SCHEMA` (column contracts), and [`configs/weights.yaml`](../configs/weights.yaml) (active scoring weight keys, 4 presets).

---

## 1. Intro — what v1 shipped, and what was deliberately skipped

The v1 prototype ran end-to-end on **real Catalonia data** (7-day dev window, 2024-03-04..10) and produced `data/gold/h3_res8_catalonia.parquet` with ~50k H3 res-8 hexes. The `liveability_score` is real, but it is computed over a gold table where **only 12 of the 24 feature columns carry real signal**:

- **9 real columns:** `climb_min_m`, `yoga_min_m`, `hospital_min_m`, `motorway_within_500m`, `industry_density_per_km2`, `mitma_inflow_daily`, `mitma_outflow_daily`, `mitma_through_ratio`, `liveability_score`.
- **3 shortcut columns:** `train_reach_min` (5 km / 3 km Euclidean circles ≈ 25 / 15 min by bike at 41°N, **not** Valhalla isochrones), `trains_per_day_nearest` and `trains_to_bcn_nearest` (both a constant `12` synthetic fallback because the GTFS feeds were not on disk — see NOTES §10).
- **12 NULL columns** (schema slot + active weight exist, but notebook 02 never populates them): `green_min_m`, `sea_min_m`, `tree_cover_pct`, `natura2000_within_5km`, `biodiversity_obs_density`, `no2_ugm3`, `pm25_ugm3`, `lst_summer_median_c`, `uhi_delta_c`, `eprtr_facility_min_m`, `viirs_radiance`, `pharmacy_density_per_km2`.

**Why they were skipped — honestly:** The prototype's single hard goal (PLAN.md §15) was to prove the Spark+Sedona runtime could execute the pipeline once on real data and emit a clickable map. The spatial-join saga in NOTES §15–19 (JTS classloader split forcing `sedona.global.index=false`, which in turn broke `ST_KNN`; plan-lineage attribute explosion forcing per-aggregate silver parquet; degenerate MITMA polygons) consumed the run's budget. The NULL columns each need an *additional* data fetch (EEA/CAMS, Landsat/VIIRS STAC, WDPA, iNaturalist/GBIF, E-PRTR, Copernicus TCD) plus a hex-aggregation step that was out of scope for "make it run once." Critically, **`scoring.py` already has a weight key for every one of these columns**, and a NULL contributes `0` — so the index is *structurally complete* but *empirically thin*. Every NULL column is `weight × 0` today; populating it is pure upside, no scoring refactor.

The `io_*` module landscape today (from the amenities inventory + NOTES):

| Module | Status | Detail |
|---|---|---|
| `io_osm` | REAL | POIs + network extracted to bronze; parks, coastline, pharmacies present but not yet aggregated to hexes |
| `io_gtfs` | REAL (parser) | `compute_frequency` / `load_combined` ready; falls back to constant 12 when feeds absent |
| `io_air` | PARTIAL | `parse_xvpca_csv` REAL + tested; EEA + CAMS NetCDF readers are stubs (`NotImplementedError`) |
| `io_thermal` | STUB→REAL fns, not wired | `query_summer_lst_scenes`, `composite_lst_summer_median`, `lst_zonal_mean_per_hex` exist but notebook 02 never calls them |
| `io_biodiversity` | PARTIAL | `fetch_inat_observations` + WDPA parser ready; spatial aggregation missing |
| `io_pollution` | PARTIAL | `parse_eprtr_facilities` + `viirs_monthly_radiance` wrappers REAL; no hex-aggregation path |
| `io_health` | PARTIAL | OSM + CatSalut fetch ready; density aggregation missing |

This v2 plan turns each NULL/shortcut into a real, source-backed column, reusing the Sedona patterns already documented.

---

## 2. Summary table — every feature, current state → v2

Effort: **S** = a hex-aggregation step over data already fetchable; **M** = needs a new fetch/parser path + aggregation; **L** = needs an external service (Valhalla). Score impact is the magnitude of the column's contribution under the default preset (and the strongest preset where relevant).

| Feature (gold column) | Current state | v2 improvement | Data source | Effort | Score impact |
|---|---|---|---|---|---|
| `train_reach_min` | shortcut: 5/3 km Euclidean circles | Valhalla bike isochrone bands (10/15/25 min) → `ST_Intersects` → MIN(min)/hex | Valhalla self-hosted (OSM) | **L** | High — `train_reach_per_min_under25` 1.4/min <25; ~40% of hexes affected; 5–10% more accurate than buffers |
| `trains_per_day_nearest` | shortcut: constant 12 | nearest-stop join → GTFS `trips_per_day` lookup (fallback 12) | Renfe Rodalies + FGC GTFS | **S** | Medium — `trains_to_bcn_per_30` 1.0/30 trips (clipped) |
| `trains_to_bcn_nearest` | shortcut: constant 12 | nearest-stop join → GTFS `trips_to_bcn_core` (fallback 0) | same GTFS | **S** | High signal for Catalonia — same weight, BCN-specific |
| `green_min_m` | null | `sjoin_nearest` hex→parks, cap 8 km | OSM `leisure=park\|garden\|nature_reserve` | **S** | 5–10% variance — `green_per_200m` −1.0 (−2.0 nature_first) |
| `sea_min_m` | null | `sjoin_nearest` hex→coastline, cap 5 km | OSM `natural=coastline` | **S** | Flat `sea_within_3km_bonus` +6.0 (+12.0 nature_first); ~15% of hexes |
| `tree_cover_pct` | null | `RS_ZonalStats` mean of Copernicus TCD COG | Copernicus HRL Tree Cover Density 10 m | **M** | `tree_cover_pct` 0.15/% (0.30 nature_first); also UHI rural input |
| `natura2000_within_5km` | null | `ST_Buffer(centroid,5km)` ∩ WDPA → boolean | WDPA / Natura 2000 | **M** | Flat +5.0 (+10.0 nature_first); ~60% of hexes |
| `biodiversity_obs_density` | null | sjoin iNat points → COUNT/hex ÷ 0.735 km² | iNaturalist via GBIF API | **S** | `biodiversity_obs_log` log1p ×1.0 (2.0 nature_first) |
| `pharmacy_density_per_km2` | null | 1 km buffer sjoin COUNT ÷ 0.735 km² | OSM `amenity=pharmacy` | **S** | `pharmacy_density_log` log1p ×1.0 (2.0 amenity_first) |
| `hospital_min_m` | real | merge OSM + CatSalut registry, re-run `sjoin_nearest` | OSM + CatSalut | **S** | `hospital_per_400m` −1.0/400 m (−2.0 amenity_first) |
| `no2_ugm3` | null | nearest-station NO₂ (XVPCA∪EEA), cap 8 km; CAMS grid fallback | XVPCA + EEA + CAMS | **M** | `no2_above_who_per_ugm3` −0.5 (−1.5 quiet_strict), WHO thr 20 |
| `pm25_ugm3` | null | CAMS grid `RS_ZonalStats` preferred, station `sjoin_nearest` fallback | XVPCA + EEA + CAMS 10 km | **M** | `pm25_above_who_per_ugm3` −1.2 (−3.6 quiet_strict), WHO thr 5 — often the dominant urban penalty |
| `lst_summer_median_c` | null | STAC Landsat C2-L2 ST_B10, JJA median composite → `RS_ZonalStats` | Planetary Computer `landsat-c2-l2` | **M** | No direct weight — prerequisite for UHI |
| `uhi_delta_c` | null | hex LST − 25th-pct LST of rural hexes (tree>30% ∧ industry=0) | derived from LST | **M** | `uhi_per_degree` −2.0 (−6.0 quiet_strict); ~30–40% urban hexes |
| `eprtr_facility_min_m` | null | `sjoin_nearest` hex→E-PRTR facilities, cap 50 km | E-PRTR facility CSV (manual download) | **S** | `eprtr_inverse_dist` −0.001 × (1/m); sparse — ~90% of hexes >10 km |
| `viirs_radiance` | null | VIIRS DNB monthly composite → `RS_ZonalStats` mean | Planetary Computer `viirs-monthly-v22` | **M** | `viirs_radiance` −0.05 (−0.15 quiet_strict); tiebreaker, large in ports/industry |

The 9 already-real columns (`climb_min_m`, `yoga_min_m`, `industry_density_per_km2`, `motorway_within_500m`, `mitma_*`, `liveability_score`) carry over unchanged; the cross-cutting projection fix (§5) improves the distance-based ones.

---

## 3. Feature sections by group

Each item below lists: data source + access, pipeline wiring (`io_*` module → notebook step → gold column → scoring weight key), the Sedona pattern (by `docs/sedona_sql_patterns.md` section), dependencies, and score impact.

### 3.1 Mobility & Valhalla

**`train_reach_min` — Valhalla bike isochrones (replaces the 5/3 km circles).**
- *Source/access:* Valhalla bike-routing, self-hosted via `docker/docker-compose.yml --profile valhalla` (OSM-based, open-source). `VALHALLA_URL` env var; **falls back to the existing circular buffers if unset** — never blocks the pipeline.
- *Wiring:* `catmob.isochrones` wrapper → for each station in `bronze/osm/stations.parquet`, call Valhalla REST `/isochrone` for 15 + 25 min bike bands → union polygons per station → notebook 02: `ST_Intersects(iso.polygon, hex.geometry)` → `GROUP BY h3_id MIN(minutes)` → `train_reach_min` → `scoring.py train_reach_per_min_under25` (1.4/min, linear under 25, cliff above).
- *Sedona pattern:* §5 left-join + `MIN(...)` (Valhalla GeoJSON contours read as a Sedona spatial layer; the `MIN`/`NULL` split distinguishes "no station reachable" from "0 min"). Fallback is §5's circular-buffer variant already in place.
- *Dependencies:* Valhalla container (tile build — see §5 risks). NOTES §11. The wrapper `isochrones.py` already exists.
- *Score impact:* High. Core mobility signal; ~40% of Catalan hexes meaningfully affected; 5–10% accuracy gain over buffers (hills + routing constraints the Euclidean circle ignores).

**`trains_per_day_nearest` / `trains_to_bcn_nearest` — real GTFS frequency (replaces constant 12).**
- *Source/access:* Renfe Rodalies + FGC GTFS (open; quarterly). `io_gtfs.BCN_CORE_STOP_NAMES` defines the BCN-core stop set (Sants, Passeig de Gràcia, Plaça Espanya, …).
- *Wiring:* `io_gtfs.load_combined(rodalies_dir, fgc_dir)` → `{stops, freq}`; notebook 01 writes `frequency.parquet` when feeds present. Notebook 02: `sjoin_nearest(hex_m, stops_m)` → nearest `stop_id` → LEFT JOIN `frequency[trips_per_day]` and `frequency[trips_to_bcn_core]` → `trains_per_day_nearest` (fallback 12) and `trains_to_bcn_nearest` (fallback 0) → `scoring.py trains_to_bcn_per_30` (1.0/30, clipped).
- *Sedona pattern:* §5 nearest-station join + small Pandas LEFT JOIN (frequency table is a few thousand rows).
- *Dependencies:* GTFS feeds on disk (NOTES §10 — currently empty dirs). `io_gtfs.compute_frequency` already implemented; fallback is a no-op.
- *Score impact:* Medium for `trains_per_day`; high specificity for `trains_to_bcn` (jobs/universities/culture — the original liveability question).

### 3.2 Environmental health — air & thermal

**`no2_ugm3` — nearest air-quality station NO₂.**
- *Source/access:* XVPCA Catalan network (~80 stations, CC BY 4.0) — `io_air.parse_xvpca_csv` is REAL + tested. EEA e-Reporting CSV (CC BY 2.5, `discomap.eea.europa.eu`) — parser is a stub; column names vary by portal export. CAMS Regional grid (~0.1°, Copernicus) as optional gridded fallback — NetCDF unstacker is a stub.
- *Wiring:* `io_air.parse_xvpca_csv` (∪ `parse_eea_csv` when implemented) → `bronze/air/` → `AIR_QUALITY_STATION_SCHEMA`. Notebook 02: `sjoin_nearest(hex_m, stations_m[no2 NOT NULL], max_distance=8000)` (rural may extend to 30–50 km) → `no2_ugm3` → `scoring.py no2_above_who_per_ugm3` (penalty `max(0, no2−20) × w`).
- *Sedona pattern:* §5 envelope-prefilter + `ST_DistanceSpheroid` nearest (KNN variant); §4 `RS_ZonalStats`/IDW if interpolating from the CAMS COG instead.
- *Dependencies:* XVPCA ready; **blocking: EEA CSV schema discovery** (manual portal export, column names drift); optional CAMS NetCDF reader. Sedona session already wired.
- *Score impact:* High. WHO 2021 NO₂ threshold 20 µg/m³; ~40% of hexes affected; weight triples under quiet_strict. 10 µg/m³ over ≈ −5 to −15 pts.

**`pm25_ugm3` — PM2.5 annual mean (grid preferred, station fallback).**
- *Source/access:* Same stations as NO₂ (XVPCA column `pm25_anual` handled; test covers the `pm25_annual_ugm3` rename). CAMS Regional gridded 10 km (CC BY 4.0, monthly → annual mean) as the *preferred* field where available.
- *Wiring:* `io_air.parse_xvpca_csv[pm25]` + `io_air.cams_grid_to_dataframe` (to implement) → stations + grid. Notebook 02 / silver: `RS_ZonalStats(cams_raster, hex.geometry, 'mean')` **or** `sjoin_nearest(stations)` → `COALESCE` (grid preferred) → `pm25_ugm3` → `scoring.py pm25_above_who_per_ugm3` (`max(0, pm25−5) × w`, WHO 2021 threshold 5).
- *Sedona pattern:* §4 raster zonal stats (CAMS COG, per-tile sum/count) for the grid path; §5 sjoin_nearest for the station fallback — compute both, prefer grid.
- *Dependencies:* XVPCA ready; CAMS NetCDF reader to implement; raster zonal stats path.
- *Score impact:* Very high — stricter than NO₂ (threshold 5). Weight −1.2 default, −3.6 quiet_strict. Often the **primary driver** of the environmental-health penalty in urban areas.

**`lst_summer_median_c` — Landsat summer LST (prerequisite for UHI).**
- *Source/access:* Microsoft Planetary Computer STAC `landsat-c2-l2` (open), Landsat 8/9 Level-2 thermal band **ST_B10**, JJA (June–Aug) worst-case-heat window. DN → Kelvin: `DN × 0.00341802 + 149.0`; − 273.15 → °C.
- *Wiring:* `io_thermal.query_summer_lst_scenes(year=2024, bbox=CATALONIA_BBOX, max_cloud_pct=20)` → STAC items → `composite_lst_summer_median(..., out_path=.../lst_summer_jja_2024.tif)` (cloud-optimized GeoTIFF in `bronze/thermal/`). Notebook 02 / silver: Sedona read COG, `RS_Intersects` + `RS_ZonalStats(rast, hex.geometry, 'mean')` → `lst_summer_median_c`. **These functions exist but notebook 02 never calls them today.**
- *Sedona pattern:* §4 raster zonal stats (per-tile `sum`+`count`, then `SUM(sum)/NULLIF(SUM(cnt),0)` for multi-tile hexes; `RS_Intersects` pre-filter). §8f STAC reader can replace the `query_summer_lst_scenes` stub with one `sedona.read.format("stac")` call.
- *Dependencies:* `stackstac` + `pystac-client` + `planetary-computer` + `rioxarray`/GDAL (already pip-installed in the conda env per NOTES §1). Sedona ≥ 1.6 for `RS_ZonalStats`; ≥ 1.9 for the native STAC reader. Cloud-cover filter at query time keeps the stack small. No network blocker (PC public).
- *Score impact:* No direct weight — intermediate signal that enables `uhi_delta_c`.

**`uhi_delta_c` — Urban Heat Island delta.**
- *Source/access:* Derived from the LST composite (no new external data). Rural baseline from hexes with `tree_cover_pct < 30%` ∧ `industry_density_per_km2 = 0` (and optionally below-median MITMA inflow).
- *Wiring:* after `lst_summer_median_c` + `tree_cover_pct` exist: `PERCENTILE_CONT(0.25)` of LST over the rural subset → `rural_baseline`; `uhi_delta_c = lst_summer_median_c − rural_baseline` for all hexes (`THERMAL_LST_SCHEMA` range guards) → `scoring.py uhi_per_degree` (`max(0, uhi_delta_c) × w`).
- *Sedona pattern:* §4 (same LST raster step) then a pure-SQL window/quantile + scalar subtraction; **§8e `RS_MapAlgebra`** can do `out[0]=rast0[0]-rast1[0]` server-side against a rural-baseline raster.
- *Dependencies:* `lst_summer_median_c` (prereq), `tree_cover_pct` (biodiversity feature), `industry_density_per_km2` (already real). No external data.
- *Score impact:* Medium — secondary to absolute LST but the key heat signal for coastal-metro quality-of-life; quiet_strict triples it (−6.0). Strongest in BCN CBD + industrial zones; ~30–40% of hexes.

### 3.3 Nature & biodiversity

**`green_min_m` — nearest park distance.**
- *Source/access:* OSM Catalunya `leisure=park|garden|nature_reserve` (ODbL, weekly). Already in bronze.
- *Wiring:* `io_osm` parks → notebook 02 `sjoin_nearest(hex_centroids_m, parks_m, distance_col)` capped 8 km (same shape as `climb_min_m`) → `green_min_m` → `scoring.py green_per_200m`.
- *Sedona pattern:* §5 bbox-prefilter LEFT JOIN + `MIN(ST_DistanceSpheroid)` in EPSG:25831.
- *Dependencies:* parks in bronze; weight ready. None external.
- *Score impact:* 5–10% score variance; parks abundant, strong signal.

**`sea_min_m` — nearest coastline distance.**
- *Source/access:* OSM `natural=coastline` — already extracted into `network.parquet`.
- *Wiring:* filter `network.parquet` to coastline → notebook 02 `sjoin_nearest(hex_centroids_m, coastline_geoms_m)`, clip 5 km → `sea_min_m` → `scoring.py sea_within_3km_bonus`.
- *Sedona pattern:* §5 (project to EPSG:25831, sjoin_nearest).
- *Dependencies:* coastline in `network.parquet`; sjoin_nearest in use. None external.
- *Score impact:* +6.0 flat bonus (+12.0 nature_first); ~15% of hexes within 5 km; **unlocks the nature_first preset's coastal preference**.

**`tree_cover_pct` — Copernicus Tree Cover Density zonal mean.**
- *Source/access:* Copernicus HRL Tree Cover Density 10 m annual (CC BY 4.0, `land.copernicus.eu`); fetchable as COG via Planetary Computer.
- *Wiring:* fetch TCD COG → clip Catalonia → notebook 02 / silver `RS_ZonalStats(rast, hex.geometry, 'mean')` → `tree_cover_pct` → `scoring.py tree_cover_pct`.
- *Sedona pattern:* §4 (per-tile sum+count → divide; `RS_Intersects` pre-filter; `'mean'` returns percentage directly).
- *Dependencies:* Planetary Computer access; `pystac-client` + `rasterio`; notebook raster loop.
- *Score impact:* 0.15/% (0.30 nature_first); Catalonia 0–70%. Also feeds the UHI rural-baseline definition.

**`natura2000_within_5km` — protected-area proximity.**
- *Source/access:* WDPA / Natura 2000 shapefile or GeoJSON (CC BY 4.0, `protectedplanet.net`); manual download, annual. `io_biodiversity` WDPA parser ready.
- *Wiring:* `io_biodiversity.filter_wdpa_to_catalonia` → notebook 02 / silver `ST_Buffer(hex_centroid, 5000m)` `ST_Intersects` WDPA polygons → `GROUP BY h3_id` boolean → `natura2000_within_5km` → `scoring.py natura2000_within_5km`.
- *Sedona pattern:* §5 adapted — buffer the centroid then `ST_Intersects` against protected polygons; GROUP BY hex to a boolean.
- *Dependencies:* WDPA download committed; spatial join. No fetch blocker.
- *Score impact:* +5.0 flat (+10.0 nature_first); Natura 2000 covers ~30% of Catalonia → affects ~60% of hexes; **critical for nature_first**.

**`biodiversity_obs_density` — iNaturalist observations per km².**
- *Source/access:* iNaturalist research-grade observations via GBIF API (`datasetKey 50c9509d-22c7-4a22-a47d-8c48425ef4a7`, CC0/CC BY-NC, `api.gbif.org/v1/`). `io_biodiversity.fetch_inat_observations` ready (pulls ~50k records).
- *Wiring:* fetch → notebook 02 / silver sjoin points→hexes, `COUNT(*) per h3_id` ÷ 0.735 km² → `biodiversity_obs_density` → `scoring.py biodiversity_obs_log` (`log1p`).
- *Sedona pattern:* §5 sjoin + GROUP BY COUNT, then `log1p` in scoring (log scaling absorbs the 0–50+/km² range and the citizen-science clustering bias).
- *Dependencies:* `fetch_inat_observations` ready; GBIF free. None blocking.
- *Score impact:* log1p ×1.0 (2.0 nature_first); crowdsourced richness proxy.

### 3.4 Amenities & health

**`pharmacy_density_per_km2` — pharmacies within 1 km.**
- *Source/access:* OSM `amenity=pharmacy` (ODbL, weekly), already in `pois.parquet`.
- *Wiring:* filter `pois.parquet` to `category=pharmacy` → notebook 02 `ST_Buffer(hex_centroid, 1000m)` sjoin(contains) `COUNT per h3_id` ÷ 0.735 km² (same shape as `industry_density_per_km2`) → `pharmacy_density_per_km2` → `scoring.py pharmacy_density_log` (`log1p`).
- *Sedona pattern:* §2-style 1 km buffer + sjoin(contains) + GROUP BY COUNT.
- *Dependencies:* pharmacies in `pois.parquet`; weight ready. None external.
- *Score impact:* log1p ×1.0 (2.0 amenity_first); urban 3–5/km², suburban 0.5–1.

**`hospital_min_m` — nearest hospital (already real; v2 improves coverage).**
- *Source/access:* OSM `amenity=hospital` (primary, ODbL) ∪ CatSalut public hospital registry (secondary, CC BY 4.0, `analisi.transparenciacatalunya.cat/resource/yub2-3z85.csv`).
- *Wiring:* `io_osm` hospital nodes + `io_health.fetch_catsalut_hospitals` → normalize/union to `OSM_POI_SCHEMA` → notebook 02 `sjoin_nearest(hex_m, hospital_points_m, max_distance=8000)` → `hospital_min_m` → `scoring.py hospital_per_400m`. v2 delta is the **source merge** for complete coverage + a coverage-gap validation.
- *Sedona pattern:* §5 bbox-prefilter + `ST_DistanceSpheroid` → MIN; the merge is a Pandas union.
- *Dependencies:* both fetchers exist; union is a DataFrame op.
- *Score impact:* −1.0/400 m (−2.0 amenity_first); urban 0.5–1 km, rural 5–20 km.

### 3.5 Pollution & light

**`eprtr_facility_min_m` — nearest industrial facility.**
- *Source/access:* European Pollutant Release and Transfer Register (E-PRTR) facility CSV (CC BY 2.5 EEA, `industry.eea.europa.eu/download` → Spain subset; **manual download — no programmatic fetch exposed**; cache to `data/bronze/pollution/eprtr/spain.csv`). Catalonia subset ~200–300 major IPPC sites (refineries, power plants, chemical). `io_pollution.parse_eprtr_facilities` REAL (robust column rename).
- *Wiring:* `parse_eprtr_facilities(csv_path, bbox=CATALONIA_BBOX)` → `{facility_id, name, lon, lat, total_emissions_t}` → notebook 02 `sjoin_nearest(hex_m, facilities_m, max_distance=50000)` → `eprtr_facility_min_m` → `scoring.py eprtr_inverse_dist` (`(1/distance_m) × w`, strong near facilities).
- *Sedona pattern:* §6 broadcast + range-join — all ~300 facilities fit the broadcast threshold; `/*+ BROADCAST(facilities) */` + envelope prefilter avoids the full cross-product.
- *Dependencies:* manual E-PRTR CSV download/commit; parser ready.
- *Score impact:* Medium — inverse-distance is a strong *local* penalty (1 km → −1 pt, 100 m → −10 pts at default −0.001) but sparse: ~90% of hexes are >10 km away (unaffected). Drives the Tarragona/Martorell refinery-cluster lows. quiet_strict should raise the weight (e.g. −0.003).

**`viirs_radiance` — light pollution (VIIRS DNB).**
- *Source/access:* Planetary Computer STAC `viirs-monthly-v22` (open), VIIRS Day/Night Band monthly composites, 500 m, asset `avg_rad` (nW/cm²/sr). `io_pollution.viirs_monthly_radiance` wrapper REAL but not called.
- *Wiring:* `viirs_monthly_radiance(year=2024, month=…, out_path=.../viirs_dnb_2024_03.tif)` (or mean of 12 monthly composites for an annual field) → COG in `bronze/pollution/viirs/` → notebook 02 / silver `RS_Intersects` + `RS_ZonalStats(rast, hex.geometry, 'mean')` → `viirs_radiance` → `scoring.py viirs_radiance`.
- *Sedona pattern:* §4 raster zonal stats (single monthly asset → direct mean per hex; multi-tile via `SUM(sum)/NULLIF(SUM(cnt),0)`).
- *Dependencies:* `stackstac` + `pystac-client`; Sedona ≥ 1.6 `RS_ZonalStats`; GDAL for COG write. No blocker.
- *Score impact:* Low-medium — weak negative (−0.05 default, −0.15 quiet_strict), used as a tiebreaker. But coastal/industrial urban (Barcelona port) hit 50+ nW/cm²/sr → −2.5 to −7.5 pts.

---

## 4. Prioritised roadmap

Ordering by **value ÷ effort**, quick wins first. Every quick win reuses an `sjoin_nearest` / buffer-count / GROUP BY shape already proven in notebook 02 (NOTES §16) — minimal new code, big completeness gain.

### Wave 1 — quick wins (all effort **S**, no external service, mostly already-fetched data) — ✅ DONE (shipped in v2.3)

1. **`green_min_m`** (OSM parks → sjoin_nearest). Parks already in bronze; 5–10% score variance for ~one cell of code. Highest value/effort.
2. **`sea_min_m`** (OSM coastline → sjoin_nearest). Unlocks the nature_first coastal bonus; coastline already in `network.parquet`.
3. **`pharmacy_density_per_km2`** (1 km buffer COUNT). Mirrors the existing `industry_density` cell exactly.
4. **`biodiversity_obs_density`** (GBIF/iNat sjoin COUNT). Fetcher ready, GBIF free.
5. **`trains_per_day_nearest` + `trains_to_bcn_nearest`** (GTFS frequency join). Replaces the constant-12 shortcut; needs the feeds on disk (NOTES §10) — fetch is the only gate, the join is small. Real BCN-connectivity signal.
6. **`hospital_min_m` coverage merge** (OSM ∪ CatSalut). Already real; cheap completeness upgrade.
7. **`eprtr_facility_min_m`** (E-PRTR sjoin_nearest). One manual CSV download, then a broadcast join; parser ready.

### Wave 2 — medium lifts (STAC rasters + vector downloads, effort **M**) — ✅ DONE (shipped in v2.3; EEA/CAMS air gap-fill on top of XVPCA-only remains optional)

8. **`tree_cover_pct`** (Copernicus TCD `RS_ZonalStats`). Standalone *and* a prerequisite for UHI — do it before thermal.
9. **`natura2000_within_5km`** (WDPA buffer-intersect). Manual download + a buffer join; big preset impact.
10. **`no2_ugm3`** (station nearest-join). XVPCA ready; gated on EEA CSV schema discovery for full coverage — ship XVPCA-only first, add EEA later.
11. **`pm25_ugm3`** (CAMS grid zonal + station fallback). Strongest environmental penalty; needs the CAMS NetCDF reader.
12. **`viirs_radiance`** (VIIRS DNB `RS_ZonalStats`). Wrapper ready; same raster pattern as TCD.
13. **`lst_summer_median_c`** (Landsat JJA composite `RS_ZonalStats`). Functions exist, just need wiring; depends on Sedona STAC reader / `stackstac`.
14. **`uhi_delta_c`** (rural-baseline subtraction). Strictly after `lst_summer_median_c` **and** `tree_cover_pct` — pure SQL once both inputs land.

### Wave 3 — the big lift (effort **L**, external service) — ⬜ REMAINING (the only deferred item; `VALHALLA_URL`-gated, fallback already ships)

15. **`train_reach_min` via Valhalla bike isochrones.** Replaces the circular-buffer shortcut. Gated on a stable Valhalla container + tile build (§5). The fallback already ships, so this is a *quality* upgrade, not a blocker — slot it last and let `VALHALLA_URL` gate it.

**Sequencing dependencies to respect:** GTFS feeds before items 5; TCD (8) before UHI (14); LST (13) before UHI (14); the EPSG:25831 reprojection fix (§5) should land *once, up front* because every distance column (Wave 1 items 1–3, 6–7) benefits and it removes the degrees-vs-meters foot-gun (NOTES §12).

---

## 5. Dependencies & risks

**Projection / UTM (cross-cutting, do first).** v1 used degree thresholds on EPSG:4326, where `ST_Distance` returns degrees — the original motorway penalty `< 500` meant "within 500 degrees" and flagged the whole planet (NOTES §12). v1 worked around it with `ST_DWithin(..., 0.005, false)` ≈ 555 m. **v2 fix:** project hexes + all point/line layers to **EPSG:25831 (UTM 31N, the Catalan cartographic CRS)** and use meter-based `ST_Distance`/`sjoin_nearest`. Needs the geotools-wrapper PROJ database (already in the JAR bundle). This is the single highest-leverage correctness fix and unblocks every meter-cap in Wave 1.

**Valhalla tile build (item 15).** Docker compose builds failed four times historically (PEP 668 numpy uninstall, GHCR auth for the Valhalla image, pyrobuf/setuptools breakage — PLAN.md §15). The Valhalla tile build over the 251 MB Catalonia PBF must be staged and verified before the isochrone wrapper is useful. Mitigation: `VALHALLA_URL` gates the feature and the circular-buffer fallback always ships — Valhalla is never on the critical path.

**Planetary Computer STAC access (items 8, 12, 13).** `landsat-c2-l2`, `viirs-monthly-v22`, and the TCD COG are public, no auth/network blocker. Risks: (a) Landsat JJA **cloud contamination** — mitigated by `eo:cloud_cover < 20` at query time + median compositing, but a single-summer baseline is a documented limitation (PLAN.md §13). (b) `RS_ZonalStats` clipping is not automatic (Sedona issue #2409) — for tiny hexes vs huge scenes, `RS_Tile(rast, 256, 256)` upstream (patterns §4 caveat). (c) Needs `stackstac`/`pystac-client`/`planetary-computer`/`rioxarray` — installed in the conda env (NOTES §1), absent from the bare `.venv` fallback.

**GTFS feed availability (item 5).** `data/bronze/gtfs/{rodalies,fgc}/` are empty dirs (NOTES §10). The M2 GTFS fetcher was broken; v2 needs a working fetch (or a manually committed feed) before `trains_*` become real. Until then the constant-12/0 fallback stands. Feeds are quarterly — pin a snapshot.

**Manual-download sources (items 7, 9, and EEA for item 10).** E-PRTR, WDPA, and the EEA e-Reporting CSV have **no clean programmatic fetch**; they are click-through portal exports whose column names drift between exports. v2 must commit cached copies under `data/bronze/...` and make the parsers robust to column renames (E-PRTR parser already is). EEA schema discovery is the explicit blocker for full NO₂/PM2.5 coverage — ship XVPCA-only first.

**CAMS NetCDF reader (item 11).** The unstacker is a stub; PM2.5 grid path needs it implemented (xarray). Station fallback works without it, so PM2.5 can ship station-only first.

**Spark 4.1 × Sedona 1.9 runtime fragility (affects every raster/spatial item).** From NOTES §15–19: the JTS classloader split forced `sedona.global.index=false`, which disables `ST_KNN` (§16) — v2 must either (a) put the Sedona/geotools JARs on the app classloader via `spark.{driver,executor}.extraClassPath` at JVM launch (the untried-but-correct fix, NOTES §15 option 2) to re-enable the spatial index, or (b) keep the bbox-prefiltered `sjoin_nearest`/`MIN(ST_DistanceSpheroid)` pattern that v1 shipped (which all the §5/§6 patterns above assume). Also persist each per-hex aggregate to its own silver parquet before the gold compose to avoid the plan-lineage `ATTRIBUTE_NOT_FOUND` explosion (§17), and keep the MITMA polygon cleaning (`make_valid` + zero-area sub-part strip, §18) and the per-distrito H3 explode without `ST_Union_Aggr` (§19). Optionally pin pyspark `==4.0.x` or wait for Sedona 1.10 (`sedona-spark-shaded-4.1`) to silence the `FoldableUnevaluable` init noise (§4, §6).

**Compute / bandwidth.** Full-scale download ≈ 3.5 GB MITMA + ~3 GB other sources; atlas has 738 GB free (PLAN.md §13) — no constraint. Raster zonal stats over ~50k hexes × multi-tile Landsat/VIIRS is the heaviest new compute; the `RS_Intersects` pre-filter (patterns §4) keeps it tractable.

**Modelling-uncertainty caveats to document in notebook 03 (carried from PLAN.md §13):** sparse air stations + coarse CAMS → ±5 µg/m³; single-summer LST baseline; iNaturalist observation bias clusters around population centres/trails; OSM yoga-POI completeness is uneven.

---

## 6. Definition of done for v2

`data/gold/h3_res8_catalonia.parquet` has **all 24 feature columns carrying real, source-backed signal** (Valhalla optional via `VALHALLA_URL`, EEA/CAMS optional layered on XVPCA), every distance computed in EPSG:25831 meters, and the four `weights.yaml` presets producing visibly different rankings because the nature/environmental/pollution columns are no longer uniformly NULL. The scoring function needs **no change** — only data.
