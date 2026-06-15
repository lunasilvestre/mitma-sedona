# Data sources

Every upstream dataset, what it contributes, and its licence. The full catalog
with download links lives in [data/README.md](../data/README.md).

[← back to README](../README.md)

## Sources & licences

| Source | What | Licence |
|---|---|---|
| MITMA v2 OD distritos (daily + hourly) | mobility flows | MITMA Open Data ≈ CC BY 4.0 |
| OSM Cataluña PBF | POIs + network + boundaries | ODbL |
| Renfe Rodalies + FGC GTFS | train frequencies | open |
| EEA + XVPCA + Copernicus CAMS | air quality | CC BY 2.5 / CC BY 4.0 / Copernicus |
| Landsat 8/9 LST (Microsoft Planetary Computer STAC) | urban heat island | open |
| WDPA / Natura 2000 + Copernicus TCD + iNaturalist (GBIF) | biodiversity | CC BY 4.0 / CC BY-NC |
| E-PRTR + VIIRS DNB | non-air pollution | CC BY 2.5 / open |
| CatSalut hospital registry | health amenities | CC BY 4.0 |

Full catalog: [data/README.md](../data/README.md).

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
