# Results — working prototype (dev scope, 2024-03-04..10)

The end-to-end Sedona run on real Catalonia data, the v2 scoring it now feeds,
plus the retrospective on what tripped us and how we fixed it.

[← back to README](../README.md)

> **Dev-scope prototype.** These numbers come from the dev data window (7 days
> of March 2024 OD flows), and the liveability score is a *relative index*, not
> a guarantee. Coverage is full for the raster/zonal layers but still sparse on
> some point/station layers — see the retrospective for the caveats.

## Prototype artifacts

The bronze ingest + the geopandas gold layer (`scripts/run_gold_v2.py`) + the
geo-browser produced:

| Artifact | Count / size | Notes |
|---|---|---|
| `data/bronze/mitma_parquet/{daily,hourly}/` | 27,697,944 OD rows × 252 MB | 7 days × 24 hours, Catalonia-touching |
| `data/bronze/osm/pois.parquet` | 4,935 POIs | climbing 219 · yoga 67 · hospital 36 · pharmacy 411 · industry · park/clinic/doctors |
| `data/bronze/osm/network.parquet` | 364,530 highway ways | from pyrosm — Sedona's native PBF reader doesn't yet build way geometries |
| `data/bronze/osm/stations.parquet` | 475 stations / halts | OSM `railway=station\|halt`, GTFS fallback |
| **`data/gold/h3_res8_catalonia_v2.parquet`** | **45,220 hexes × ~28 features** | 3.8 MB on disk; all 6 dimensions real-data-wired |
| `docs/explore.html` | geo-browser | 15 toggleable metrics, satellite/dark/light/OSM basemaps, hex-opacity slider |

## Top hexes by liveability score (default weights)

The default ranking is now led by **Barcelona core + the well-served coast**,
because the index finally discriminates. The top hexes combine high
`trains_to_bcn_nearest` frequency, near-doorstep amenities (the saturating
closeness rewards), and either coastal proximity or dense urban access — and
they shrug off the heat/air penalties that the v1 score never applied.

Six hexes hit the clipped maximum of **100.0**: a cluster in the Barcelona
metro core (lon ≈ 2.12–2.15, lat ≈ 41.43–41.45) and a Maresme coastal cluster
(lon ≈ 2.6–2.65, sea within ~2.6 km, ~200 trains/day to BCN core). 152 hexes
score ≥ 90.

The Barcelona-metro band (lon 2.10–2.22, lat 41.36–41.46, 145 hexes) averages
**76.2** (median 75.6) — the strongest cluster in Catalonia. This is the
opposite of the v1 artifact, where empty inland Girona/Lleida towns wrongly
maxed out at 64 because no penalty applied and every amenity distance was NULL
(beyond the cap). That flaw is gone.

The **lowest** hexes are now the inland Lleida plain near a motorway with
extreme summer UHI (≈ 16 °C delta), scoring as low as **7.9** — the heat and
motorway penalties bite exactly where they should.

## Score distribution

Across all 45,220 hexes (default preset):

| | min | 25% | median | 75% | max | mean | std |
|---|---:|---:|---:|---:|---:|---:|---:|
| **default** | 7.92 | 46.28 | **59.27** | 68.88 | 100.0 | 57.09 | 14.28 |
| nature_first | 13.70 | 58.89 | 72.95 | 86.95 | 100.0 | 72.01 | 17.32 |
| quiet_strict | 0.00 | 3.41 | 34.88 | 64.30 | 96.58 | 35.36 | 28.37 |
| amenity_first | 6.62 | 45.78 | 58.50 | 68.00 | 100.0 | 56.67 | 14.69 |

(v1 was median 50 / max 64 — the score now uses its full range and the presets
produce visibly different rankings.)

### Reus — a worked example of why the score now discriminates

The Reus window (lon 1.05–1.16, lat 41.12–41.20, 117 hexes) scores a default
**mean ≈ 51.5** (median 53.4) — up from the v1 baseline of 21.64, but
deliberately *not* a top-tier score. Every Reus hex is within train reach, so
mobility is strong; but `trains_to_bcn_nearest` is ~0 (it's on the Tarragona
line, not the BCN-core spine) and the median summer **UHI delta is ~10 °C**,
which costs ~−20 points at the default `uhi_per_degree` of −2.0/°C. Great local
access, real urban-heat penalty — the index captures both, where v1 (no UHI
term) could not.

## Per-feature coverage

Fraction of the 45,220 hexes carrying a real (non-NULL) value:

| Layer | Coverage | Layer | Coverage |
|---|---:|---|---:|
| green_min_m | 100% | tree_cover_pct | 100% |
| natura2000_within_5km | 100% | biodiversity_obs_density | 100% |
| viirs_radiance | 100% | lst / uhi_delta_c | 100% |
| trains_per_day / to_bcn | 100% | pharmacy_density | 100% |
| motorway / industry / mitma | 100% | eprtr_facility_min_m | 98% |
| climb_min_m | 49% | no2_ugm3 | 41% |
| hospital_min_m | 30% | train_reach_min | 26% |
| pm25_ugm3 | 20% | yoga_min_m | 15% |
| sea_min_m | 6% | | |

The raster/zonal layers are fully populated; the remaining gaps are honest
sparsity in the point/station sources (yoga POIs, the PM2.5 station net, the
coastline within 5 km). NULL is treated as neutral, never as a penalty.

## Map

For the interactive results, see the geo-browser at
[explore.html](explore.html) (described in [visualization.md](visualization.md))
— 15 toggleable analytic metrics over satellite/dark/light/OSM basemaps, with a
hex-opacity slider and study panel. Metric recolour is consistent: brighter =
more liveable.

## What I learned (retrospective)

The two correctness fixes that reshaped the ranking:

1. **Amenity terms were inverted.** v1 put a *negative* weight on amenity
   *distance*, so a hex with **no** amenity (NULL → 0) out-scored a hex with a
   **far** one (large negative) — absence beat far-presence. v2 makes them
   saturating positive closeness rewards (`W_pos × max(0, 1 − dist/10000)`), so
   presence ≥ absence and near > far. This alone is why empty inland towns no
   longer top the chart.
2. **Every distance was in degrees.** v1 buffered in EPSG:4326, ~25%
   anisotropic at 41°N. v2 computes all distances/buffers in EPSG:25831 metres.

The pipeline lessons that still bite:

3. **MITMA's v2 distritos schema has drifted**: the file is pipe-delimited
   now, every row carries `periodo` (hour-of-day), and there are two extra
   `estudio_*_posible` columns. The pandera fixtures in the test suite still
   use the older `;`-delimited form, so the contract tests stayed green even
   after the Spark schema was rewritten.
4. **The Sedona pip package doesn't ship Java JARs.** Pinning
   `spark.jars.packages` to `sedona-spark-shaded-4.0_2.13:1.9.0` +
   `geotools-wrapper:1.9.0-33.5` got us a working `SedonaContext` on Spark 4.1
   in ~10 s on the first call (Maven download), under a second thereafter.
5. **Catalonia's distrito GeoJSON has self-intersecting polygons and a couple
   of degenerate sub-multipolygons near Barcelona** that JTS refuses
   (TopologyException at 2.059, 41.383). `make_valid()` + an `area > 0` filter
   handles both — the H3 explode is well-behaved once they're cleaned.
6. **The gold layer runs faster as plain pandas + geopandas + h3-py than via
   Sedona** at this data size (45 k hexes × 5 k POIs × 364 k ways), because the
   Sedona + Spark 4.1 spatial-index serde tripped a classloader-mismatch
   `IllegalAccessError` on every spatial join. The bronze + visualisation steps
   stay on Sedona (where it pays off on the 27 M-row MITMA daily aggregation).
   See [NOTES_FROM_PROTOTYPE_RUN.md](../NOTES_FROM_PROTOTYPE_RUN.md) §3 / §5.

The only term still on a shortcut is `train_reach_min` (metric 5/3 km Euclidean
circles, not Valhalla bike isochrones) — an optional, deprioritised quality
refinement; the rest of the index is real, source-backed data.

---

See also: [scoring.md](scoring.md) · [data_sources.md](data_sources.md) ·
[visualization.md](visualization.md)
