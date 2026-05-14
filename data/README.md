# Data Catalog

Single source of truth for every external dataset the pipeline depends on.
Every row also has a corresponding pandera schema in
`src/catmob/schemas.py` and a contract test under `tests/`.

The `data/` tree itself is **gitignored**; this README describes what *should*
appear after you run `scripts/fetch_*.sh` followed by notebook 01.

```
data/
├── README.md          (this file)
├── bronze/            raw → Parquet, schema-validated
│   ├── mitma/{daily|hourly}/<YYYY-MM>/*.parquet
│   ├── osm/cataluna_pruned.osm.pbf  +  parquet/{nodes,ways}.parquet
│   ├── gtfs/{rodalies,fgc}/*.txt    +  parquet/{stops,trips,…}.parquet
│   ├── air/{eea,xvpca,cams}/*.{csv,nc}
│   ├── thermal/landsat_lst_<year>_jja.tif
│   ├── biodiversity/{wdpa,inat,tcd}/...
│   ├── pollution/{eprtr,viirs}/...
│   └── health/{catsalut,…}/*.csv
├── silver/            Sedona spatial-joined, partitioned by month
│   ├── mitma_od_distritos/...
│   ├── osm_pois/...
│   ├── osm_network/...
│   ├── train_stations/...
│   └── isochrones/...
└── gold/              analysis-ready hex grid (~50k rows)
    └── h3_res8_catalonia.parquet
```

## Catalog

### Mobility

| Dataset | Source URL | Licence | Format | CRS | Refresh | Schema |
|---|---|---|---|---|---|---|
| MITMA v2 daily OD distritos | `https://movilidad-opendata.mitma.es/estudios_basicos/por-distritos/viajes/ficheros-diarios/<YYYY-MM>/` | MITMA Open Data ≈ CC BY 4.0 | CSV.gz, `;`-delim, UTF-8 | n/a | monthly | `MITMA_DAILY_OD_SCHEMA` |
| MITMA v2 hourly OD distritos | `https://movilidad-opendata.mitma.es/estudios_basicos/por-distritos/viajes/<YYYY-MM>/` | same | CSV.gz, `;`-delim, UTF-8 | n/a | monthly | `MITMA_HOURLY_OD_SCHEMA` |
| MITMA distritos zoning | `https://movilidad-opendata.mitma.es/zonificacion/zonificacion_distritos.*` | same | Shapefile + GeoJSON | EPSG:4326 | annual | `MITMA_ZONE_SCHEMA` |
| Renfe Rodalies GTFS | `https://transitfeeds.com/p/renfe/505` | open | GTFS zip | EPSG:4326 | quarterly | `GTFS_STOPS_SCHEMA`, `GTFS_FREQUENCY_SCHEMA` |
| FGC GTFS | `https://transit.land/feeds/f-fgc` | open | GTFS zip | EPSG:4326 | quarterly | same |

### Network & POIs

| Dataset | Source URL | Licence | Format | CRS | Refresh | Schema |
|---|---|---|---|---|---|---|
| OSM Cataluña | `https://download.geofabrik.de/europe/spain/cataluna-latest.osm.pbf` | ODbL | PBF | EPSG:4326 | weekly | `OSM_POI_SCHEMA`, `OSM_NETWORK_SCHEMA` |

Pre-pruned with `osmium tags-filter` per `src/catmob/io_osm.py:OSMIUM_TAG_FILTER`
to ~50 MB before Sedona ingest.

### Air quality

| Dataset | Source URL | Licence | Format | Frequency | Schema |
|---|---|---|---|---|---|
| EEA Air Quality e-Reporting (E1a) | `https://discomap.eea.europa.eu/Map/UI/AirQualityE1a/` | CC BY 2.5 EEA | CSV per pollutant per year | annual | `AIR_QUALITY_STATION_SCHEMA` |
| XVPCA Catalunya stations | `https://analisi.transparenciacatalunya.cat/resource/uy6k-2s8r.csv` | CC BY 4.0 | CSV | annual | `AIR_QUALITY_STATION_SCHEMA` |
| Copernicus CAMS Regional Reanalysis | `https://atmosphere.copernicus.eu/...` | Copernicus Open | NetCDF | monthly | `AIR_QUALITY_GRID_SCHEMA` |

### Thermal / Urban Heat Island

| Dataset | Source URL | Licence | Format | Schema |
|---|---|---|---|---|
| Landsat 8/9 Coll-2 L2 ST_B10 | Microsoft Planetary Computer STAC, collection `landsat-c2-l2` | open | COG (GeoTIFF) | `THERMAL_LST_SCHEMA` (post-processed) |

We summer-composite (JJA) and aggregate to H3 hex; UHI Δ vs rural reference.

### Biodiversity

| Dataset | Source URL | Licence | Format | Schema |
|---|---|---|---|---|
| WDPA / Natura 2000 | `https://www.protectedplanet.net/` | Mostly CC BY 4.0 | Shapefile + GeoJSON | `PROTECTED_AREA_SCHEMA` |
| Copernicus Tree Cover Density 10 m | `https://land.copernicus.eu/...` | Copernicus Open | COG | aggregated to hex |
| iNaturalist research-grade (via GBIF) | `https://api.gbif.org/v1/occurrence/search` (datasetKey `50c9509d-…`) | CC0 / CC BY-NC | JSON | `BIODIVERSITY_OBSERVATION_SCHEMA` |

### Pollution (non-air)

| Dataset | Source URL | Licence | Format |
|---|---|---|---|
| E-PRTR industrial emissions | `https://industry.eea.europa.eu/download` | CC BY 2.5 EEA | CSV / GeoPackage |
| VIIRS DNB monthly composites | Microsoft Planetary Computer STAC `viirs-monthly-v22` | open | COG |

### Health amenities

| Dataset | Source URL | Licence | Format |
|---|---|---|---|
| OSM amenities (hospital/clinic/doctors/pharmacy) | from same OSM PBF | ODbL | derived |
| CatSalut hospital registry | `https://analisi.transparenciacatalunya.cat/resource/yub2-3z85.csv` | CC BY 4.0 | CSV |

## Privacy & attribution

- **MITMA** is aggregated and anonymised at the *distrito* level (≥3,000
  zones); no individual or household-level data. Required attribution:
  *Datos de movilidad: Ministerio de Transportes y Movilidad Sostenible (MITMS)*.
- **OSM**: `© OpenStreetMap contributors, ODbL`.
- **EEA**: `© European Environment Agency (EEA)`.
- **Copernicus**: `Generated using Copernicus data and information funded by the European Union — Copernicus Climate Change Service / Atmosphere Monitoring Service`.
- **iNaturalist (via GBIF)**: cite GBIF download DOI + `CC BY-NC` for unaltered observations.
- **CatSalut / XVPCA**: `Generalitat de Catalunya — analisi.transparenciacatalunya.cat`.

A full attribution block is appended to `docs/catalonia_liveability.html`
and to the repo README.

## Sizes (estimated, after Catalonia subset)

| Layer | Size on disk |
|---|---|
| Bronze MITMA (1 month daily + 1 week hourly) | ~600 MB |
| Bronze OSM (pre-pruned PBF + Parquet) | ~120 MB |
| Bronze GTFS | ~10 MB |
| Bronze air (EEA + XVPCA + CAMS sample) | ~80 MB |
| Bronze thermal (one summer JJA composite) | ~250 MB |
| Bronze biodiversity (WDPA + iNat sample + tree cover) | ~400 MB |
| Bronze pollution (E-PRTR + VIIRS sample) | ~150 MB |
| Silver (Sedona joins, partitioned) | ~500 MB |
| Gold (`h3_res8_catalonia.parquet`) | ~25 MB |
| **Total** | **~2.1 GB** |

Atlas has 738 GB free on `/home/nls/Documents/dev`; no constraint.

## Refresh playbook

```bash
# Daily check (no-op if upstream unchanged)
scripts/fetch_mitma.sh   --kind daily   --month 2024-10
scripts/fetch_mitma.sh   --kind hourly  --week  2024-10-07
scripts/fetch_osm.sh     --bbox catalonia
scripts/fetch_air.sh     --year 2024
scripts/fetch_biodiversity.sh
scripts/fetch_pollution.sh
scripts/fetch_health.sh
# Re-render notebooks
scripts/render_notebooks.sh
```
