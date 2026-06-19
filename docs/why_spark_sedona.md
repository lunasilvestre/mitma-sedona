# Why Spark + Apache Sedona earns its keep

> Status: the **new MITMA deep-Spark mobility layers** (rhythm, weekend hotspots,
> typology, geodemographics, OD arcs) are produced by a **real distributed
> Sedona pipeline** — `scripts/build_mitma_layers.py` — not by pandas. This note
> records the six operations where Sedona/Spark is load-bearing, and why the
> previous pandas shortcut could not produce these layers at the dataset's native
> resolution or with correct dasymetric geometry.

## The honest before/after

The shipped v2 gold attributed mobility flow to hexes with a **naive centroid
`gpd.sjoin(predicate="within")` join** (`scripts/run_gold_v2.py:407`): every hex
whose centroid fell inside a distrito received that **whole distrito's** flow.
The result, visible in `h3_res8_catalonia_v2.parquet`, was only **569 distinct
inflow values across 45,220 hexes** — a step function, not a field.

The new pipeline replaces this with an **area-weighted dasymetric crosswalk**
computed in Sedona, giving **45,220 distinct per-hex values**. The regression
diff (printed by the build script):

| metric | old (centroid) | new (dasymetric) |
| --- | --- | --- |
| `mitma_inflow_daily` median | 86,645 | 40.6 |
| distinct inflow values | 569 | 45,220 |

The old median (86,645) was a *whole-distrito total* stamped onto every hex; the
new median (40.6) is a genuine *per-hex share*. This is a **correctness fix**,
not a regression — flagged here and in the PR because the published numbers move.

## The six load-bearing Sedona/Spark operations

1. **`ST_H3CellIDs` fullCover grid + `ST_H3ToGeom`** — the H3 res-8 hex polygons
   are materialised from their ids inside Sedona
   (`ST_H3ToGeom(array(cast(conv(h3_id,16,10) as bigint)))[0]`), not pre-baked.

2. **The dasymetric `ST_Intersection` / `ST_Area` crosswalk in EPSG:25831** — the
   irreducible spatial cost. Both hex and distrito polygons are reprojected
   **from EPSG:25830 (UTM 30N, the MITMA source CRS) to EPSG:25831 (UTM 31N,
   correct for Catalonia)** with `ST_Transform` *before* any area math, then
   `area_weight = ST_Area(ST_Intersection)/NULLIF(ST_Area(zone),0)`. Validated:
   **89.6 % of the 584 Catalonia distritos sum their hex `area_weight` to within
   ±2 % of 1.0** (median exactly 1.0). pandas `gpd.sjoin` cannot do this
   correctly, and cannot do it at all-Spain scale.

3. **Broadcast spatial join of the small zoning against the OD rows** — the
   distritos side (584 Catalonia polygons) is `F.broadcast`-hinted; the join
   predicate is `ST_Intersects`. *(Sample note: the env ships two JTS copies, so
   the broadcast **spatial-index** STRtree serde hits an `IllegalAccessError`;
   we set `sedona.join.optimizationmode=none` for this session to fall back to a
   correct non-indexed range join. At full scale on atlas, pin a matched JTS /
   disable AQE for just the `BroadcastIndexJoin` stage — STAGE 0 below.)*

4. **`ST_MakeLine(ST_Centroid(o), ST_Centroid(d))` OD-arc construction** — the
   top-5,000 OD arcs in `arcs.json` are now **Sedona-generated** (centroid lines
   over zone-pair flows, endpoints via `ST_X/ST_Y` of `ST_Centroid`), replacing
   the verbatim v1 HTML lift. Same shape, so the deck.gl `ArcLayer` is unchanged.

5. **Spark MLlib `BisectingKMeans` typology clustering** — the `mobility_typology`
   label comes from standardising an interpretable per-zone vector (sink/source =
   `log(outflow/inflow)`, intra-zone share, long-trip share, leisure vs commute
   share) with `StandardScaler` and clustering with `BisectingKMeans`.

6. **`ST_Transform` 25830→25831 metric reprojection** — every area weight depends
   on this; assuming 25831 on the raw 25830 MITMA geometry corrupts all weights.

## Scale that pandas cannot hold

The pandas shortcut was fast **only** because it pre-collapsed the entire
`periodo(24) × distancia(4) × actividad(4×4) × renta(3) × edad(5) × sexo(3)` cube
into three per-zone scalars *before* reading. Keeping those dimensions — which
the rhythm / weekend / typology / geodemographic layers all require — is **27.7M
hourly OD rows for the 7-day Catalonia sample alone** (the build script's
`od rows (all days, hourly)=27,697,944`). Full-range all-Spain ×
{distritos, municipios, GAU} × {viajes, pernoctaciones, personas} is tens of
billions of rows and genuine TBs — a real distributed shuffle / window /
spatial-join workload. Spark Window functions build the per-hex 24h profiles,
`max_by(periodo, viajes)` does one-pass peak detection, and entropy aggregations
run over the income×age segment cube.

## Full-scale staging (atlas)

- **STAGE 0 (env hardening):** pin a matched Spark/Sedona pair to kill the
  `FoldableUnevaluable` skew and the dual-JTS `IllegalAccessError`; pre-stage the
  two Maven jars so a TB-scale cold start has no Maven Central dependency.
- **STAGE 1–4:** sized incremental download by `zoning × kind × date-range`
  tranche → bronze; build all three `zone_h3_xwalk` tables once → silver; run the
  five gold themes **per zoning** (separate gold partitions — different expansion
  bases, cross-zoning ratios invalid).
- **Guardrails:** never re-expand `viajes` (already population-expanded); honour
  the <100-device privacy floor via the `support_n` min-support gates; keep NA
  `edad/sexo/renta` as their own segment; size partitions to avoid OOM (a naive
  single-machine all-Spain hourly run will OOM — atlas with proper executors).

## Keyless / static contract preserved

Every new layer ships as additional JSON under `docs/story_data/`: new scalar
columns merged into `hexes.json`, the heavy 24-float profiles in a lazy
`rhythm.json` sibling (consumed as a hover sparkline, keeping `hexes.json` from
bloating with arrays), and the Sedona-built `arcs.json`. No API keys, no build
step — the deck.gl geo-browser reads them statically, exactly as before.
