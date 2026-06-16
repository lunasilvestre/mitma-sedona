# v2 Build Playbook — turn-key execution

**Companion to [`docs/v2_revision.md`](v2_revision.md).** That doc is the *strategy* (what/why, value÷effort waves). This is the *HOW*: per feature — the verified acquisition command, the exact code change, the gold column + weight key, the validation check + expected coverage, and an on-disk-NOW vs fetch-needed vs gated tag.

Ground rules baked into every entry below:
- **Single working tree.** `src/catmob/*.py` and `notebooks/` are owned by a concurrent agent; all `io_*`/notebook edits here are returned as **TEXT to apply**, not applied. The recon agents created only new `scripts/fetch_*_v2.sh` + new `data/bronze/**` (never editing shared code).
- **Only real contract columns.** Every `goldColumn` maps to a `GOLD_HEX_SCHEMA` slot (`src/catmob/schemas.py`) and every weight key exists in `configs/weights.yaml`. No new scoring keys are introduced — each NULL column is `weight × 0` today, so populating is pure upside (matches `v2_revision.md` §6 DoD).
- **CRS.** Distance math runs in **EPSG:25831** (ETRS89/UTM31N — ICGC-confirmed Catalan CRS). Centroids stay stored in EPSG:4326 (the gold schema range-checks lon∈(0,4)/lat∈(40.5,42.9)); only the working copy is reprojected. Do **not** switch to 25830 — the `.prj` sidecar on the unused `.shp` declares UTM30N but nb02 reads the WGS84 GeoJSON.
- **Env.** Python is `/home/nls/miniforge3/envs/sedona/bin/python` (has geopandas/rasterio/rioxarray/pyarrow/pystac_client/planetary_computer/xarray). Missing: `stackstac`, `dask`, `osgeo`/gdal, `awscli`, `cdsapi`. System `python3` lacks pyarrow — never use it.
- **Sedona vs pure-Python.** The **gold join ships pure-Python (geopandas/h3)** — it sidesteps the four Spark-4.1×Sedona-1.9 JVM landmines (NOTES §15–19) and runs ~10× faster. Sedona `RS_ZonalStats`/`ST_*` snippets are the documented *alternative* for nb03/04 scale; raster zonal stats use the `rasterstats` fallback in the gold path.

---

## 0. DO-FIRST: EPSG:25831 reprojection correctness fix

**This is the single highest-leverage change and it gates nothing — apply it before any data work.**

| | |
|---|---|
| **goldColumn** | `train_reach_min` (`Column(float, ge(0), nullable=True)`) |
| **on disk** | ON DISK — inputs `data/bronze/osm/stations.parquet` (21 parts, OSM railway nodes) + `data/silver/hex_centroids.parquet`. Output column already written to gold but computed **anisotropically wrong**. |
| **acquisition** | none — pure recompute. |
| **weight key** | `train_reach_per_min_under25` = 1.4 (scoring: `max(0, 25 - train_reach_min) * w`, linear under 25, cliff above). |

**The bug** — `notebooks/02_liveability_layer.py` Step 3, lines 129–130:
```python
union_5km = stations_gdf.buffer(0.045).union_all()   # stations_gdf is crs=EPSG:4326
union_3km = stations_gdf.buffer(0.027).union_all()
```
`.buffer()` on EPSG:4326 buffers in **degrees**. At 41°N, 0.045° = ~5.0 km N–S but only ~3.75 km E–W → an ellipse ~25% skewed in E–W, not a 5 km circle.

**The fix** — mirror the already-correct Step 4/6 pattern (reproject first):
```python
stations_m = stations_gdf.to_crs(epsg=25831)
union_5km = stations_m.buffer(5000).union_all()
union_3km = stations_m.buffer(3000).union_all()
hex_m = hexes.to_crs(epsg=25831)
hexes["train_reach_min"] = np.where(
    hex_m.geometry.within(union_3km), 15,
    np.where(hex_m.geometry.within(union_5km), 25, np.nan),
)
```
Keep `hexes` centroids stored in EPSG:4326 (schema range check). This buffer path is the **fallback** kept exactly as-is when Valhalla is off (see §11).

**Validation:** after the fix, count of hexes with `train_reach_min ∈ {15,25}` should *rise* on the E–W axis vs the ellipse; sanity-check that no hex within 5 km road of a station is NaN. `train_reach_min` stays NULL beyond 5 km (correct — cliff weight).

**Sedona-path note (TEXT, not for the gold join):** every distance/penalty column's Sedona SQL form must `ST_Transform(geom,'EPSG:4326','EPSG:25831')` once into a metric column before `ST_Distance(...) < 500`, else it compares *degrees* (NOTES §12 flagged the whole planet at `< 500`; the patched form used `ST_DWithin(...,0.005,false)`). The PROJ DB ships in the pinned `org.datasyslab:geotools-wrapper:1.9.0-33.5` jar. `ST_KNN` is unavailable when `sedona.global.index=false`, so use bbox-prefilter + `MIN(ST_DistanceSpheroid)` per `sedona_sql_patterns.md` §5/§6.

---

## 1. NATURE — Wave-1 on-disk wins (`green_min_m`, `sea_min_m`) + amenity density

### 1a. `green_min_m` — READY-NOW (on disk)

| | |
|---|---|
| **on disk** | YES. `data/bronze/nature/green_polys.parquet` (149 MB, 71,763 green polygons, GeoParquet EPSG:4326) — already extracted by `scripts/fetch_nature.sh` (`osm` step ran). Source PBF `data/bronze/osm/cataluna-latest.osm.pbf` (251 MB) on disk. |
| **acquisition (verified)** | none — re-extracted from on-disk PBF with `ogr2ogr` (GDAL 3.10 present; `osmium` is NOT installed). Re-run: `bash scripts/fetch_nature.sh` (idempotent). |
| **weight key** | `green_per_200m` = -1.0 (`nature_first` -2.0). Scoring caps green at 4000 m. |

**Code change** — `notebooks/02_liveability_layer.py`, after the POI-distance cell (lines 147–173):
```python
green = gpd.read_parquet(REPO/"data/bronze/nature/green_polys.parquet")
green = green[green.geom_type.isin(["Polygon","MultiPolygon"])]      # drop 151 GeometryCollection
green_m = green.to_crs(epsg=25831)[["geometry"]].reset_index(drop=True)
nearest = gpd.sjoin_nearest(hex_m, green_m, how="left", distance_col="_dist", max_distance=8000)
hexes["green_min_m"] = hexes["h3_id"].map(nearest.groupby("h3_id")["_dist"].min())
```
For strict "parks only" semantics, prefilter `green[green["leisure"].isin(["park","garden","nature_reserve"])]` (the extracted set is broader: adds `landuse=forest/meadow`, `natural=wood/grassland`).

**Validation / expected coverage:** ~100% of hexes hit within 8 km (verified 2000/2000 sample, median 14 m). **Effort: S.**

### 1b. `sea_min_m` — READY-NOW (on disk)

| | |
|---|---|
| **on disk** | YES. `data/bronze/nature/coastline.parquet` (2.4 MB, 2,002 `natural=coastline` MultiLineStrings, EPSG:4326). NOT in v1 `network.parquet` (highway-only). |
| **acquisition (verified)** | none — re-extracted from PBF. GDAL OSM driver hides `natural` in `other_tags`, so the SQL used `WHERE other_tags LIKE '%"natural"=>"coastline"%'`. |
| **weight key** | `sea_within_3km_bonus` = +6.0 (`nature_first` +12.0). **Flat** bonus when `sea_min_m < 3000` — does NOT scale; leaving interior hexes NaN is correct. |

**Code change:**
```python
coast = gpd.read_parquet(REPO/"data/bronze/nature/coastline.parquet").to_crs(epsg=25831)
nearest = gpd.sjoin_nearest(hex_m, coast[["geometry"]].reset_index(drop=True),
                            how="left", distance_col="_dist", max_distance=5000)
hexes["sea_min_m"] = hexes["h3_id"].map(nearest.groupby("h3_id")["_dist"].min())
```

**Validation / expected coverage:** ~6–15% of grid (coastal strip) within 5 km (verified 134/2000 sample, median 2207 m). **Effort: S.**

### 1c. `pharmacy_density_per_km2` — READY-NOW (on disk)

| | |
|---|---|
| **on disk** | YES. `data/bronze/osm/pois.parquet` has 3,217 `category=pharmacy` nodes. Optional authoritative cross-check fetched: `data/bronze/health/catsalut/pharmacies.csv` (3,304 Farmàcia rows). |
| **acquisition** | none new — already on disk via `fetch_osm.sh`. CatSalut cross-check = Socrata `nrmq-ytje` (keyless), already written. |
| **weight key** | `pharmacy_density_log` = 1.0 (`amenity_first` 2.0). Scoring applies `log1p(density)*w`. |

**Code change** — clone the existing `industry_density_per_km2` cell exactly:
```python
pharm = pois_m.loc[pois_m["category"]=="pharmacy", ["geometry"]].reset_index(drop=True)
hex_buf_1km = gpd.GeoDataFrame({"h3_id": hexes["h3_id"].values},
                               geometry=hex_m_centroid.buffer(1000), crs=hex_m_centroid.crs)
hits = gpd.sjoin(hex_buf_1km, pharm, predicate="contains")
counts = hits.groupby("h3_id").size()
hexes["pharmacy_density_per_km2"] = hexes["h3_id"].map(counts).fillna(0) / 0.735   # res-8 hex area
```

**Validation:** density 0 in empty rural hexes, peaks in Barcelona core. **Effort: S.**

### 1d. `biodiversity_obs_density` — READY-NOW after one keyless fetch

| | |
|---|---|
| **on disk** | NOT yet materialized. `scripts/fetch_nature.sh` (`inat` step) delegates to the tested `catmob.io_biodiversity.fetch_inat_observations` → `data/bronze/nature/inaturalist_catalonia.parquet`. |
| **acquisition (verified live)** | GBIF occurrence API, `datasetKey=50c9509d-22c7-4a22-a47d-8c48425ef4a7` (iNaturalist Research-grade) + WKT Catalonia bbox + `year=2018,2024`. Keyless. 437,165 records available; fetcher caps `max_records=50000`. |
| **weight key** | `biodiversity_obs_log` = 1.0 (`nature_first` 2.0). `log1p` absorbs citizen-science clustering bias. |

**Code change:**
```python
obs = pd.read_parquet(REPO/"data/bronze/nature/inaturalist_catalonia.parquet")
obs["h3_id"] = [h3.latlng_to_cell(la, lo, 8) for la, lo in zip(obs.lat, obs.lon)]
counts = obs.groupby("h3_id").size()
hexes["biodiversity_obs_density"] = hexes["h3_id"].map(counts).fillna(0) / 0.735
```

**Validation:** clusters around trails/cities (documented bias). **Effort: S.**

### 1e. `tree_cover_pct` — FETCH-NEEDED (keyless raster)

| | |
|---|---|
| **on disk** | NO → `data/bronze/nature/tcd_2018_catalonia.tif`. |
| **acquisition (verified keyless)** | EEA discomap ImageServer `exportImage` GeoTIFF — `https://image.discomap.eea.europa.eu/arcgis/rest/services/GioLandPublic/HRL_TreeCoverDensity_2018/ImageServer/exportImage?bbox=...&bboxSR=4326&imageSR=4326&size=5000,4200&format=tiff&pixelType=U8&noData=255&f=image`. Values 0–100, 255=nodata. **Better than the plan's Copernicus click-through** (no registration). `scripts/fetch_nature.sh` (`tcd` step). |
| **weight key** | `tree_cover_pct` = 0.15 (`nature_first` 0.30). Also feeds the UHI rural baseline (§3). |

**Code change** — `catmob.io_biodiversity.compute_tree_cover_per_hex` already does `rasterstats.zonal_stats(..., nodata=255)`:
```python
from catmob.io_biodiversity import compute_tree_cover_per_hex
tc = compute_tree_cover_per_hex(REPO/"data/bronze/nature/tcd_2018_catalonia.tif", hex_polygons)
hexes["tree_cover_pct"] = hexes["h3_id"].map(tc.set_index("h3_id")["tree_cover_pct"])
```
**Foot-gun:** needs **h3 POLYGON** geometry (`h3.cell_to_boundary`), not the centroids in `silver/hex_centroids.parquet`. Build cell boundaries or use Sedona `RS_ZonalStats`.

**Validation / coverage:** every hex 0–100; forested NE high, urban core low. **Effort: M.**

### 1f. `natura2000_within_5km` — FETCH-NEEDED (keyless vector)

| | |
|---|---|
| **on disk** | NO → `data/bronze/nature/natura2000_catalonia.parquet`. |
| **acquisition (verified keyless)** | EEA bio.discomap MapServer query — `https://bio.discomap.eea.europa.eu/arcgis/rest/services/ProtectedSites/Natura2000_Dyna_WM/MapServer/0/query` with `esriGeometryEnvelope` over Catalonia, `f=geoJSON`. 224 sites. **Sidesteps WDPA/Protected-Planet token** (API verified 401 without token). `scripts/fetch_nature.sh` (`natura2000` step). |
| **weight key** | `natura2000_within_5km` = 5.0 (`nature_first` 10.0, `amenity_first` 2.5). Boolean flat bonus. |

**Code change:**
```python
n2k = gpd.read_parquet(REPO/"data/bronze/nature/natura2000_catalonia.parquet").to_crs(epsg=25831)
n2k_union = n2k.geometry.union_all()
hexes["natura2000_within_5km"] = hex_m_centroid.geometry.buffer(5000).intersects(n2k_union)
```
**Foot-gun:** `io_biodiversity.filter_wdpa_to_catalonia` expects WDPA cols (`WDPAID/NAME/DESIG_ENG`); EEA source has `SITECODE/SITENAME/SITETYPE` — bypass that helper or add a rename adapter.

**Validation:** boolean true for ~protected-area-adjacent hexes. **Effort: M.**

---

## 2. POLLUTION & HEALTH — amenity merge + E-PRTR

### 2a. `hospital_min_m` (CatSalut coverage merge) — READY-NOW (on disk)

| | |
|---|---|
| **on disk** | YES. OSM 36 `category=hospital` nodes (drive v1 today) + `data/bronze/health/catsalut/hospitals.csv` (70 geocoded hospitals) fetched. |
| **acquisition (verified)** | CatSalut Socrata `8gmd-gz7i` "Equipaments de Catalunya", `categoria LIKE '%HOSPITALS%'` → 70 rows (vs OSM 36 — real coverage gain). Keyless. The hardcoded `io_health.CATSALUT_HOSPITALS_URL` `yub2-3z85` is **DEAD (404)**. |
| **weight key** | `hospital_per_400m` = -1.0 (`amenity_first` -2.0). v2 delta is **source coverage only**, no weight change. |

**Code change** — build OSM ∪ CatSalut before the existing `sjoin_nearest`:
```python
osm_h = pois_m.loc[pois_m["category"]=="hospital","geometry"]
cs = pd.read_csv(REPO/"data/bronze/health/catsalut/hospitals.csv")
cs_gdf = gpd.GeoDataFrame(cs, geometry=gpd.points_from_xy(cs["longitud"], cs["latitud"]),
                          crs="EPSG:4326").to_crs(25831)
hosp = pd.concat([gpd.GeoDataFrame(geometry=osm_h.reset_index(drop=True), crs=hex_m.crs),
                  cs_gdf[["geometry"]]]).drop_duplicates()
# existing: sjoin_nearest(hex_m, hosp, max_distance=8000, distance_col="_dist") -> hospital_min_m
```
**Foot-gun:** CatSalut cols are Catalan `longitud/latitud`. Repointing `io_health.py` to `8gmd-gz7i` is a hand-off; the notebook can bypass `io_health` entirely by reading the CSV. **Effort: S.**

### 2b. `eprtr_facility_min_m` — FETCH-NEEDED (semi-manual)

| | |
|---|---|
| **on disk** | NO → `data/bronze/pollution/eprtr/spain.csv`. |
| **acquisition (verified)** | EEA "Industrial Reporting under IED + E-PRTR" — **no clean per-file CSV**; only a Nextcloud bulk zip (~2.4 GB) at `https://sdi.eea.europa.eu/webdav/datastore/public/eea_t_ied-eprtr_p_2007-2023_v14_r00/`. `scripts/fetch_pollution.sh` (section 2) tries the bulk URLs, unzips, greps `*facilit*.csv`, else prints manual instructions. |
| **weight key** | `eprtr_inverse_dist` = -0.001. Scoring applies `(1/distance_m)*w`. Cap 50 km. |

**Code change** — `parse_eprtr_facilities` is ready (robust to col renames):
```python
from catmob.io_pollution import parse_eprtr_facilities
fac = parse_eprtr_facilities(REPO/"data/bronze/pollution/eprtr/spain.csv", bbox=CATALONIA_BBOX)
fac_gdf = gpd.GeoDataFrame(fac, geometry=gpd.points_from_xy(fac.lon, fac.lat),
                           crs="EPSG:4326").to_crs(25831)
nearest = gpd.sjoin_nearest(hex_m, fac_gdf, how="left", distance_col="_dist", max_distance=50000)
hexes["eprtr_facility_min_m"] = hexes["h3_id"].map(nearest.groupby("h3_id")["_dist"].min())
```

**Validation:** ~90% of hexes >10 km away (sparse). **Effort: S** (once CSV lands).

---

## 3. THERMAL — Landsat LST → UHI (STAC raster)

### 3a. `lst_summer_median_c` — FETCH-NEEDED (keyless STAC)

| | |
|---|---|
| **on disk** | NO → `data/bronze/thermal/lst_summer_jja_<YEAR>.tif` (100 m, EPSG:25831 COG, °C). |
| **acquisition (verified keyless live)** | Microsoft Planetary Computer STAC, collection `landsat-c2-l2`, asset `lwir11` (= ST_B10). 82 Landsat-8/9 scenes Catalonia JJA-2024, cloud<20; hrefs SAS-signed via `planetary_computer.sign_inplace` — **no API key**. DN→Kelvin `x*0.00341802 + 149.0` then −273.15→°C. `scripts/fetch_thermal.sh` drives `io_thermal.query_summer_lst_scenes` + `composite_lst_summer_median`. |
| **weight key** | **none** — `lst_summer_median_c` is intermediate-only (`THERMAL_LST_SCHEMA`, gold slot `Column(float, nullable=True)`). Its score effect is entirely via `uhi_delta_c`. |

**Code change / wiring:** gold join uses the existing `catmob.io_thermal.lst_zonal_mean_per_hex` (rasterstats); persist to its own `data/silver/lst_per_hex.parquet` (NOTES §17) before gold compose. Sedona alt = `RS_ZonalStats` per-tile sum/count then divide (`sedona_sql_patterns.md` §4).

**Correctness hand-off (TEXT):** `io_thermal.composite_lst_summer_median` does **not** mask Landsat C2L2 fill value `0` before the median (0 → 149 K → −124 °C drags the median). Add `.where(stack != 0)` before `.median(dim="time")` (the fetch script's rioxarray fallback already does this correctly — reference impl). Also: `stackstac` is MISSING from the sedona env — the script installs it on demand with a rioxarray per-scene-median fallback.

**Validation:** per-hex °C in a plausible summer range (~20–45 °C); urban cores hotter. **Effort: M.**

### 3b. `uhi_delta_c` — DERIVED (no fetch; depends on 3a)

| | |
|---|---|
| **on disk** | NO — fully derived from `lst_summer_median_c`. |
| **acquisition** | none — `uhi_delta_c = hex LST − rural-baseline LST`. |
| **weight key** | `uhi_per_degree` = -2.0 (`quiet_strict` -6.0). Scoring: `max(0, uhi_delta_c) * w`. Schema `Column(float, nullable=True)` (range −10..20). |

**Code change** — pandas fallback already in `io_thermal.lst_zonal_mean_per_hex`:
```python
rural = hexes["lst_summer_median_c"].quantile(0.25)   # ship global 25th-pctile baseline first
hexes["uhi_delta_c"] = hexes["lst_summer_median_c"] - rural
```
Tighten later to the strict rural mask `tree_cover_pct < 30 AND industry_density_per_km2 == 0` (`v2_revision.md` §3.2) once §1e TCD lands.

**Validation:** clip to [−10, 20]; positive (warmer than rural) for urban hexes. **Effort: M (sequencing: strictly after 3a).**

---

## 4. AIR QUALITY (station nearest-join + optional grid)

### 4a. `no2_ugm3` — FETCH-NEEDED (keyless station)

| | |
|---|---|
| **on disk** | NO → `data/bronze/air/`. |
| **acquisition (verified live)** | XVPCA Socrata resource **`tasf-thgu`** (NOT the stale `uy6k-2s8r`), hourly. `GET https://analisi.transparenciacatalunya.cat/resource/tasf-thgu.csv?$where=date_extract_y(data)=2024 AND contaminant in ('NO2','PM2.5')`. Keyless. 23,832 NO2 station-day rows for 2024; fields `codi_eoi, data, contaminant, latitud, longitud, h01..h24`. `scripts/fetch_air_v2.sh`. |
| **weight key** | `no2_above_who_per_ugm3` = -0.5 (`quiet_strict` -1.5). Penalty `max(0, no2-20)*w` (WHO 2021). Already wired in `score_10km.py`. |

**Decision: station nearest-join (NOT grid)** — XVPCA is dense (~140 stations) and NO2 is steep-gradient/traffic-driven.

**Hand-off (TEXT):** rewrite `io_air.parse_xvpca_csv` — it expects the *annual* resource (`no2_anual`) but `tasf-thgu` is hourly. Melt `h01..h24`, `groupby(codi_eoi, contaminant).mean()`, rename → `no2_annual_ugm3` / `pm25_annual_ugm3`, `station_id/lat/lon`, `operator='XVPCA'`, `year=2024`, then `AIR_QUALITY_STATION_SCHEMA.validate()`.

**Code change** — notebook air cell mirroring the POI `sjoin_nearest`:
```python
st = gpd.GeoDataFrame(stations[stations.no2_annual_ugm3.notna()],
                      geometry=gpd.points_from_xy(stations.lon, stations.lat), crs=4326).to_crs(25831)
nearest = gpd.sjoin_nearest(hex_m, st[["no2_annual_ugm3","geometry"]], how="left",
                            distance_col="_d", max_distance=8000)
hexes["no2_ugm3"] = hexes.h3_id.map(nearest.groupby("h3_id")["no2_annual_ugm3"].first())
```

**Validation:** hexes within 8 km of a station get a value; urban NO2 above 20 → penalty. **Effort: M.**

### 4b. `pm25_ugm3` — FETCH-NEEDED (station) + GATED grid

| | |
|---|---|
| **on disk** | NO → `data/bronze/air/`. |
| **acquisition** | Station: same `tasf-thgu` (`contaminant='PM2.5'`, only 5,151 station-days — sparse). **Grid (GATED):** CAMS European reanalysis `cams-europe-air-quality-reanalyses` (0.1°, `particulate_matter_2.5um`, `interim_reanalysis`) — needs free ADS account + `~/.cdsapirc` + `cdsapi` (not installed). Gated behind `CAMS_FETCH=1` so it never blocks. |
| **weight key** | `pm25_above_who_per_ugm3` = -1.2 (`quiet_strict` -3.6). Penalty `max(0, pm25-5)*w` (WHO 2021 — often the dominant urban penalty). |

**Decision: grid zonal-mean PREFERRED, station nearest-join FALLBACK** (PM2.5 regionally smooth, XVPCA stations sparse; `v2_revision.md` §3.2). COALESCE grid over station: `hexes["pm25_ugm3"] = grid_value.combine_first(station_value)`.

**Hand-off (TEXT):** `io_air.cams_grid_to_dataframe` is a `NotImplementedError` stub — implement with xarray (open NetCDF, select `pm2p5` surface, annual mean over months, write Catalonia-cropped COG); `io_air.parse_eea_csv` is also a stub pointing at the retired discomap base (EEA gap-fill via Azure `https://eeadmz1-downloads-api-appservice.azurewebsites.net/ParquetFile`, NO2 URI `.../pollutant/8`, PM2.5 `.../6001`, `dataset:1`=E1a — optional, XVPCA covers Catalonia alone).

**Validation:** ship XVPCA-station first (zero gate beyond parser rewrite); layer CAMS grid when account exists. **Effort: M (station) / gated (grid).**

---

## 5. POLLUTION — VIIRS night-lights (GATED)

| | |
|---|---|
| **goldColumn** | `viirs_radiance` (`Column(float, ge(0), nullable=True)`) |
| **on disk** | NO. `data/bronze/pollution/viirs/` empty. |
| **acquisition (verified)** | The pinned PC collection `viirs-monthly-v22` is **DEAD (404)** and `stackstac` is absent → `io_pollution.viirs_monthly_radiance` double-fails. Keyless replacement: AWS Open Data `s3://globalnightlight/` (anonymous read confirmed) — but **2012–2020 only** and needs `awscli`+`gdal` (absent). Current-year path = NASA Black Marble `VNP46A3` via LAADS — needs `$EARTHDATA_TOKEN` (not keyless) + HDF5→GeoTIFF. Both gated in `scripts/fetch_pollution.sh` (section 1) so the column stays NULL = `weight×0` if absent. |
| **weight key** | `viirs_radiance` = -0.05 (`quiet_strict` -0.15). A tiebreaker — NULL is low-harm. |

**Code change (when raster lands):** rasterstats/rioxarray zonal mean over h3 cell polygons → `hexes["viirs_radiance"] = hexes["h3_id"].map(rad_by_h3)`. Sedona alt `RS_ZonalStats` sum/count.

**Blockers:** dead PC source + missing `stackstac`/`awscli`/`gdal`. **Effort: M, GATED — sequence last.**

---

## 6. MOBILITY — real GTFS frequency

### 6a. `trains_per_day_nearest` + 6b. `trains_to_bcn_nearest` — FETCH-NEEDED (keyless, but blocked by 2 parser bugs)

| | |
|---|---|
| **on disk** | NO. `data/bronze/gtfs/rodalies/` and `/fgc/` are **empty dirs**. v1 emits the synthetic constant `12`. |
| **acquisition (verified live 2026-06-16)** | Renfe Rodalies GTFS `https://ssl.renfe.com/ftransit/Fichero_CER_FOMENTO/fomento_transit.zip` (14.4 MB, CC BY 4.0). FGC GTFS `https://www.fgc.cat/google/google_transit.zip` (1.2 MB). **Both URLs in the existing `fetch_gtfs.sh` are DEAD** (transitfeeds mirror retired; fgc wp-content 404). New idempotent `scripts/fetch_gtfs_v2.sh` fixes both (lands 8 tables/feed, re-run skips). |
| **weight keys** | `trains_per_day_nearest` → `trains_to_bcn_per_30` = 1.0 (per 30 trips, clipped). `trains_to_bcn_nearest` → **same** `trains_to_bcn_per_30` key (one weight drives both train columns in scoring.py). Fallbacks: `trains_per_day_nearest` → 12; `trains_to_bcn_nearest` → **0** (`v2_revision.md` §2). |

**Wiring** — `io_gtfs.compute_frequency()` already computes `trips_per_day` and `trips_to_bcn_core` per stop (`GTFS_FREQUENCY_SCHEMA`). Notebook 02 §3, after fetch:
```python
g = io_gtfs.load_combined(REPO/"data/bronze/gtfs/rodalies", REPO/"data/bronze/gtfs/fgc")
# project hex centroids + g['stops'] to 25831, sjoin_nearest -> nearest stop_id per hex
hexes["trains_per_day_nearest"]  = hexes["h3_id"].map(nearest_stop_id).map(
    g["freq"].set_index("stop_id")["trips_per_day"]).fillna(12).astype(int)
hexes["trains_to_bcn_nearest"]   = hexes["h3_id"].map(nearest_stop_id).map(
    g["freq"].set_index("stop_id")["trips_to_bcn_core"]).fillna(0).astype(int)
```
BCN-core stop names verified present (Barcelona-Sants 71801, Passeig de Gràcia 71802, Plaça Catalunya 78805, Estació de França 79400; FGC Plaça Catalunya PC). `trains_to_bcn_nearest` is the higher-signal Catalonia column.

**TWO `io_gtfs.py` PARSER BUGS that block these (TEXT hand-off — `src/catmob` owned by concurrent agent):**
1. **National feed bbox.** `fomento_transit.zip` is all-Spain (1155 stops, only 204 in Catalonia). `load_stops` calls `GTFS_STOPS_SCHEMA.validate` which is strict on lon∈(0,4)/lat∈(40.5,42.9) → the 951 non-Catalan stops **must be bbox-prefiltered (lon 0..4 & lat 40.5..42.9) BEFORE validate** or it raises.
2. **FGC has no `calendar.txt`.** `compute_frequency` reads `calendar.txt` unconditionally; FGC ships `calendar_dates.txt` only → `FileNotFoundError`. Add a fallback: pick the modal Wednesday service date from `calendar_dates.txt` `exception_type=1` and treat those `service_ids` as active.

**Documented limitation:** `trips_to_bcn_core` is computed per-feed independently (two `compute_frequency` calls concatenated) — no cross-feed interchange. Acceptable for v2.

**Validation:** after fix, `trains_per_day_nearest` varies by station (no longer constant 12); `trains_to_bcn_nearest` highest near BCN-core lines. **Effort: S (fetch) — but gated on the 2 parser bugs.**

---

## 7. MOBILITY — Valhalla bike isochrones (GATED, never on critical path)

| | |
|---|---|
| **goldColumn** | `train_reach_min` (precision upgrade over the §0 buffer fallback) |
| **on disk** | Buffer shortcut on disk (§0). Catalonia PBF on disk (`cataluna-latest.osm.pbf`). No isochrones (`data/silver/isochrones/` absent). |
| **acquisition (verified)** | Self-hosted Valhalla, keyless. `docker compose -f docker/docker-compose.yml --profile valhalla up -d` builds tiles from the on-disk PBF; verify `curl -f http://localhost:8002/status`. **Compose image fix:** `ghcr.io/gis-ops/docker-valhalla:latest` is archived/401-gated — switch to `ghcr.io/nilsnolde/docker-valhalla/valhalla:latest` (verified anon-pullable). |
| **weight key** | `train_reach_per_min_under25` = 1.4 — **unchanged**; only input precision improves. |

**Wiring** — `isochrones.py` already wraps `batch_isochrones(stations, minutes=(15,25))` (bicycle, polygons, GeoJSON cache). Notebook 02 §3:
```python
if os.environ.get("VALHALLA_URL"):
    results = isochrones.batch_isochrones([(sid,lat,lon) for ...], minutes=(15,25))
    # union per minutes-band -> hexes within union_15 -> 15 elif within union_25 -> 25 else nan
else:
    # KEEP the §0 buffer fallback EXACTLY as-is  (v2_revision §5 + compose comment mandate this)
```
`VALHALLA_URL` gates the feature; the buffer fallback always ships, so Valhalla is a **quality upgrade sequenced last** (Wave 3). The `isochrones.py` wrapper + cache already exist; only the container + notebook branch are new.

**Effort: L, GATED.**

---

## DATA-READINESS MATRIX

| Feature | gold column | on disk NOW? | fetchable now (keyless)? | gated? | weight key | effort |
|---|---|---|---|---|---|---|
| Reproj fix | `train_reach_min` | ✅ inputs | n/a (recompute) | — | `train_reach_per_min_under25` | S |
| Green dist | `green_min_m` | ✅ green_polys.parquet | ✅ | — | `green_per_200m` | S |
| Sea dist | `sea_min_m` | ✅ coastline.parquet | ✅ | — | `sea_within_3km_bonus` | S |
| Pharmacy density | `pharmacy_density_per_km2` | ✅ pois.parquet | ✅ | — | `pharmacy_density_log` | S |
| Hospital merge | `hospital_min_m` | ✅ pois + catsalut.csv | ✅ | — | `hospital_per_400m` | S |
| Biodiversity | `biodiversity_obs_density` | ⬜ | ✅ GBIF | — | `biodiversity_obs_log` | S |
| Tree cover | `tree_cover_pct` | ⬜ | ✅ EEA ImageServer | — | `tree_cover_pct` | M |
| Natura 2000 | `natura2000_within_5km` | ⬜ | ✅ EEA MapServer | — | `natura2000_within_5km` | M |
| E-PRTR | `eprtr_facility_min_m` | ⬜ | ⚠️ bulk-zip / manual | — | `eprtr_inverse_dist` | S |
| Trains/day | `trains_per_day_nearest` | ⬜ (empty dirs) | ✅ Renfe+FGC | 🔒 2 parser bugs | `trains_to_bcn_per_30` | S |
| Trains→BCN | `trains_to_bcn_nearest` | ⬜ | ✅ Renfe+FGC | 🔒 2 parser bugs | `trains_to_bcn_per_30` | S |
| NO2 | `no2_ugm3` | ⬜ | ✅ XVPCA tasf-thgu | 🔒 parser rewrite | `no2_above_who_per_ugm3` | M |
| PM2.5 | `pm25_ugm3` | ⬜ | ✅ XVPCA (station) | 🔒 grid=CAMS account | `pm25_above_who_per_ugm3` | M |
| LST summer | `lst_summer_median_c` | ⬜ | ✅ PC STAC | 🔒 stackstac + fill-mask | (intermediate) | M |
| UHI | `uhi_delta_c` | ⬜ derived | n/a | 🔒 needs LST first | `uhi_per_degree` | M |
| VIIRS | `viirs_radiance` | ⬜ | ⚠️ AWS 2012-2020 | 🔒 awscli+gdal / Earthdata token | `viirs_radiance` | M |
| Valhalla iso | `train_reach_min` (precision) | ⬜ | n/a (docker build) | 🔒 VALHALLA_URL | `train_reach_per_min_under25` | L |

**Counts:** READY-NOW (on disk, no fetch) = **5** (`train_reach_min` reproj, `green_min_m`, `sea_min_m`, `pharmacy_density_per_km2`, `hospital_min_m`). FETCH-NEEDED (keyless, no gate beyond fetch) = **5** (`biodiversity_obs_density`, `tree_cover_pct`, `natura2000_within_5km`, `eprtr_facility_min_m` [⚠️ semi-manual], plus station `no2_ugm3` once its parser is rewritten). GATED = **6** (`trains_per_day_nearest`, `trains_to_bcn_nearest` [parser bugs], `pm25_ugm3` grid [CAMS account], `lst_summer_median_c`→`uhi_delta_c` [stackstac+fill-mask], `viirs_radiance` [dead source / tokens], Valhalla precision).

**v2 fetch wave (2026-06-16):** 4 of the 5 fetch-needed features now have bronze data on disk + downstream-verified. `tree_cover_pct` → `data/bronze/treecover/tcd_2018_catalonia.tif`; `natura2000_within_5km` → `data/bronze/natura2000/natura2000_catalonia.parquet`; `eprtr_facility_min_m` → `data/bronze/pollution/eprtr/spain.csv` (parser-clean, 107 post-clip); station `no2_ugm3`/`pm25_ugm3` raw → `data/bronze/air/xvpca/xvpca_hourly_2024.csv` (still needs the `io_air.parse_xvpca_csv` hourly→annual rewrite before it's a usable gold column). Only `biodiversity_obs_density` (GBIF) remains un-fetched in this wave. Wiring foot-guns to honor: paths land in feature-named dirs (treecover/natura2000/pollution/air), NOT the playbook's `data/bronze/nature/` default; TCD/N2K aggregation needs h3 cell **polygons** (`h3.cell_to_boundary`), not the silver centroids; N2K columns are EEA `SITECODE/SITENAME/SITETYPE` (bypass `filter_wdpa_to_catalonia`); `fetch_nature.sh` TCD size must be `4400,3900` (ImageServer `maxImageHeight=4100`), E-PRTR/CAMS bulk-zip URLs are dead — use the keyless discomap ArcGIS FeatureServer.

---

## RECOMMENDED SEQUENTIAL BUILD ORDER

Single working tree → one ordered list, lowest-risk/highest-leverage first. Wiring marked **TEXT** is handed to the agent owning `src/catmob`/`notebooks`; fetches run independently.

1. **EPSG:25831 reprojection fix** (§0) — correctness, no fetch, gates nothing. Highest leverage.
2. **Wave-1 on-disk wins** — `green_min_m` (§1a) → `sea_min_m` (§1b) → `pharmacy_density_per_km2` (§1c) → `hospital_min_m` merge (§2a). All data on disk, all clone proven nb02 cells. Persist each per-hex aggregate to its own `data/silver/` parquet (NOTES §17) before gold compose.
3. **Wave-1 keyless fetches** — `biodiversity_obs_density` (§1d, GBIF) → `tree_cover_pct` (§1e, EEA TCD) → `natura2000_within_5km` (§1f, EEA N2K) → `eprtr_facility_min_m` (§2b, bulk-zip/manual). Build h3 cell polygons here (TCD + N2K need polygon geometry).
4. **GTFS frequency** (§6) — run `scripts/fetch_gtfs_v2.sh` (verified live), then land the two `io_gtfs.py` parser fixes (bbox prefilter; FGC `calendar_dates` fallback) before `trains_per_day_nearest` / `trains_to_bcn_nearest`.
5. **Air quality** (§4) — rewrite `io_air.parse_xvpca_csv` (hourly→annual), then station `no2_ugm3` and station `pm25_ugm3`; layer the CAMS grid for PM2.5 only if the ADS account exists.
6. **STAC rasters** (§3, §5) — `lst_summer_median_c` → `uhi_delta_c` (install `stackstac`, apply the fill-value-0 mask), then `viirs_radiance` (gated on the replacement source + `awscli`/`gdal` or Earthdata token).
7. **Valhalla isochrones** (§7) — last; switch the compose image to the nilsnolde successor, build tiles, set `VALHALLA_URL`. The §0 buffer fallback always ships, so this is pure upside off the critical path.

**Single highest-leverage next build:** the **EPSG:25831 reprojection fix (§0)** — S-effort, no fetch, no dependency, fixes a ~25% anisotropic distortion already baked into a shipped scored column, and establishes the metric-CRS pattern every later distance feature reuses.
