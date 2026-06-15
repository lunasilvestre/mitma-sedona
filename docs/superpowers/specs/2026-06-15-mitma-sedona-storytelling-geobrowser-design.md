# mitma-sedona — Storytelling Geo-Browser & Docs Refactor

**Status:** design locked (2026-06-15) · branch `feat/story-geobrowser`
**Author:** Claude Code session (atlas) · for @lunasilvestre
**OB1:** `4ff33207` (design-lock), `3a63b47c` (data-reality), `c89028f7` (pivot), `b37a06b7` (current-state)

---

## 1. Goal

Turn the mitma-sedona repo's documentation surface into two things at once:

1. A **scrollytelling geo-narrative** — heavily inspired by
   [perspetiva.aminhaterra.pt](https://perspetiva.aminhaterra.pt/pt) — that
   walks a reader through the liveability pipeline's *inputs → steps → outputs*
   on an interactive map, using 2.5D H3 hex analytic layers for story-telling.
2. A **project-browsing helper** — the same page embeds code blocks and deep,
   pinned GitHub permalinks so a reader can jump from the narrative into the
   actual source (notebooks, scoring, viz, SQL, tests).

Plus the previously-approved hygiene: split the monolithic `README.md` into
focused `docs/` files and keep `docs/index.html` a lean index hub.

The reusable storytelling **engine** is spun out as an in-repo subproduct
(`site/engine/`) — a lightweight, keyless, static alternative to NASA's
veda-ui — so it can later be lifted into its own repo with `git filter-repo`.

## 2. Reference: what perspetiva does (and what we copy)

Verified by source teardown (not assumption):

- **Stack:** Next.js + Mapbox GL (`mapbox/standard` 3D). We **do not** copy the
  stack — we reproduce the *experience* on our existing keyless deck.gl +
  MapLibre stack.
- **Engine (copy this):** a single `position: sticky` map behind scrolling
  `.story-panel` cards; a native **IntersectionObserver** observes each panel
  and dispatches **CustomEvents** (`map-reveal`, `map-compare`,
  `chapter-enter`, …) that the map component listens for and reacts to (camera
  `flyTo` + layer opacity/visibility). The panel-DOM and the map renderer never
  couple directly — that CustomEvent seam is what makes the engine reusable.
  No scrollama, no framework, no third-party story-map template.
- **Flow (adapt):** hero → conceptual intro ("anatomy of…") → three-question
  TOC → chapter breaks with sticky-map panels → reveal/compare swipe → payoff →
  interactive explorer → CTA.
- **What we deliberately diverge on:** their warm *light* editorial palette →
  we use a **dark** UI over **satellite** imagery (see §4); their proprietary
  raster tiles → our vector H3/OD/POI layers.

## 3. Locked decisions

| Fork | Decision |
|---|---|
| Landing page | Lean `index.html` hub **+** separate story page (`docs/story.html`) |
| Geo-browser tech | Reuse deck.gl 9.3.2 + MapLibre 4.7.1 + h3-js 4.1.0 (already in `viz.py`) |
| Data | One-shot read of the gold parquet via the `sedona` env python; **no Spark** |
| README split | ~6 focused `docs/` files + lean README |
| Visual identity | **Dark** semi-transparent UI panels over **keyless Esri World Imagery satellite**; 2.5D extruded H3 hexes on top |
| Fidelity / scope | **High fidelity**, full scope, executed via **heavy parallel** delegation |
| Scrollytelling engine | Hand-rolled IntersectionObserver + CustomEvent (zero-dep), mirrors perspetiva |
| Subproduct | **Spin out `site/engine/` inside the repo** (not a new repo yet); veda-ui alternative |

## 4. Visual system

- **Basemap:** Esri World Imagery raster, keyless —
  `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`
  as a MapLibre `raster` source. **Attribution required** (Esri, Maxar, Earthstar
  Geographics, GIS community) — render it in the map credits. Optional keyless
  **labels overlay** (CARTO dark-matter *labels-only*) so place names read over imagery.
- **UI chrome:** dark, reusing the repo's existing tokens — bg `#0a0e1a`,
  panel `rgba(14,18,30,0.92)` + `backdrop-filter: blur(8px)`, text `#e8eaf2`,
  accent `#88d4ff`, hairline `rgba(255,255,255,0.08)`.
- **Thematic layers (loud, over the muted satellite):** H3 hexes in a
  documented **colourblind-safe** ramp (viridis/magma), 2.5D extruded by the
  active chapter's metric. OD arcs cyan→magenta. POIs by OSM-domain colour (§8).
- **Type/motion:** keep system-ui/Inter stack; reveal-on-scroll, camera eased,
  all IntersectionObserver-gated (not time-based). Honour
  `prefers-reduced-motion` (no auto camera moves; instant layer swaps).
- **Honesty guard (ported from perspetiva):** liveability is a *relative index,
  a starting question, not a guarantee* — stated in the panel.

## 5. Architecture

```
site/                         # the reusable engine (subproduct) + content
  engine/                     # GENERIC — knows nothing about Catalonia
    scrolly.js                # IntersectionObserver → CustomEvent controller (~120 lines)
    story-map.js              # sticky deck.gl/MapLibre map; listens for CustomEvents
    codeblock.js              # highlight.js init + "view on GitHub" permalink buttons
    story.css                 # dark scrollytelling layout (sticky slot + panels + reveal/compare)
    story.template.html       # shell: <head> CDN libs + panel scaffold placeholders
  content/                    # MITMA-SPECIFIC
    chapters.json             # the manifest: ordered chapters {id, prose, mapState, layers, src}
    chapters/*.md             # per-chapter prose (optional, or inline in json)
    data/                     # generated per-chapter payloads (gitignored or committed small)
scripts/
  export_story_payload.py     # env-python: gold parquet → per-chapter JSON + manifest metrics
  build_story.py              # reads chapters.json, slices code snippets @STORY_REF, renders → docs/story.html
docs/
  index.html                  # lean hub (links story.html + the 6 docs + repo)
  story.html                  # GENERATED scrollytelling page (build_story.py output)
  story_data/                 # GENERATED per-chapter payloads served to the page
  quickstart.md architecture.md scoring.md data_sources.md visualization.md results.md
  .nojekyll                   # serve app/ + underscores verbatim
```

**Serving:** `.github/workflows/pages.yml` already copies `docs/*` → `_site/`
on push to `main` (paths filter `docs/**`). `docs/story.html` + `docs/story_data/`
publish with **zero new infra**. (The build runs locally / pre-commit, not in CI,
for v1; a CI build+staleness-guard is a phase-3 add.)

**Engine ↔ content boundary (the templating seam):** `site/engine/` is generic;
the only contract is `chapters.json`'s schema. A future standalone repo =
`git filter-repo --path site/engine --path scripts/build_story.py`.

## 6. The scrollytelling engine (`site/engine/scrolly.js`)

- One `IntersectionObserver` over `.story-panel` elements,
  `rootMargin: '0px 0px -50% 0px'`, `threshold: 0.5` (perspetiva's tuning).
- On a panel entering, dispatch `new CustomEvent('chapter-enter', {detail: {id}})`.
- `story-map.js` holds the single deck.gl instance + MapLibre satellite basemap;
  it maps each chapter id → `{ flyTo:{lon,lat,zoom,pitch,bearing}, layers:[…] }`
  and applies it via `deck.setProps({ viewState, layers })` with eased transition.
- **reveal** (swipe wipe between two map states via CSS `clip-path`) and
  **compare** (two side-by-side deck canvases) are engine-level modes triggered
  by `map-reveal` / `map-compare` events from specially-tagged panels.
- Lazy: heavy layer payloads `fetch()`ed on first chapter that needs them.
- Picking/tooltips on at all chapters (per-hex value for the active metric).

## 7. Chapters (grounded in the *verified* gold data)

Gold parquet = **45,220 rows × 12 cols**, `liveability_score` **not stored**
(recomputed via `catmob.scoring.score_dataframe`). Coverage drives chapter type:

| # | Chapter | Metric (col) | Coverage | Layer treatment |
|---|---|---|---|---|
| 0 | Hero / intro | — | — | full-bleed satellite, no data yet |
| 1 | "Is it connected?" | `mitma_inflow_daily`, `mitma_through_ratio` | 100% | 2.5D extrude (flow) — the strong opener |
| 2 | "Can you get a train?" | `train_reach_min` | 22% | **presence** map; null = "no station in reach" (distinct grey state) |
| 3 | "Life within reach" | `climb_min_m`, `yoga_min_m`, `hospital_min_m` | 10–37% | **presence**/proximity dots; nulls explicit |
| 4 | "What it costs" (penalties) | `industry_density_per_km2`, `motorway_within_500m` | 100% | 2.5D extrude; penalty = desaturated/inverted visual |
| 5 | "Who comes, who leaves" | `mitma_through_ratio` | 100% | diverging ramp (sink↔source) |
| 6 | **Finale: liveability** | `liveability_score` (recomputed) | 100% | 2.5D extrude + colourblind ramp; preset toggle (default/nature_first/quiet_strict/amenity_first) |
| 7 | Explorer | all | — | free-roam map, hex click → score breakdown; "jump to a place" |

**Null handling rule:** a missing value is a *distinct state* ("none within
cap"), never rendered as 0 — honesty over smooth fields. Sparse chapters lead
with the *pattern of presence* ("most of Catalonia is far from a climbing gym"),
which is itself the finding.

## 8. OSM-standard-inspired symbology (adapted for dark/satellite)

Philosophy (from osm-carto): **muted reference, loud thematic.** Over satellite,
"muted" = thin/translucent reference vectors; "loud" = the extruded hexes.

- **Distrito boundaries:** thin translucent admin line (dashed), label at z9+.
- **OSM POIs:** Osmic/Maki **CC0** icons, domain colours — medical maroon,
  pharmacy cross, sport brown, wellness teal/violet, industry mauve;
  **constant screen size** (not zoom-scaled), zoom-gated (hide < z11).
- **Highway network:** cased-line hierarchy (motorway pink `#e990a0` … ) with
  **width-by-zoom** `interpolate`; motorway doubles as the noise-penalty cue.
- **Railway + stations:** dashed "ladder" line; station square sized by
  `trains_per_day` where available, else uniform.
- **H3 hexes:** colourblind-safe ramp at ~0.7 opacity so satellite reads through;
  extrusion is the secondary channel; `elevationScale` shrinks as zoom rises.

Replace `viz.py`'s ad-hoc 5-stop `scoreColor` with a documented viridis/magma LUT.

## 9. Project-browser (code blocks + pinned permalinks)

- Each chapter links to its real source. Chapter→source map (from the tree):
  ingest → `notebooks/01_data_ingest.py` + `scripts/fetch_*.sh`;
  gold/H3 → `notebooks/02_liveability_layer.py` + `src/catmob/schemas.py`;
  score → `src/catmob/scoring.py` (`score_hex`) + `configs/weights.yaml`;
  viz → `src/catmob/viz.py`; SQL → `docs/sedona_sql_patterns.md` (anchored);
  trust → `tests/` + `NOTES_FROM_PROTOTYPE_RUN.md`.
- **Pin to a tag, not `main`:** cut `story-v1`; one constant `STORY_REF` in
  `build_story.py` feeds every `blob/<ref>/<path>#L<a>-L<b>` permalink.
- **Snippets auto-extracted at build** from marker comments
  (`# story:scoring:score_hex` … `# story:end`) so embeds survive line drift;
  line numbers feed only the permalink. `highlight.js` from CDN (zero build).
- Phase-3: CI `--check` that fails if `docs/story.html` is stale vs tagged source.

## 10. Data pipeline (env-free of Spark)

`scripts/export_story_payload.py`, run with
`/home/nls/miniforge3/envs/sedona/bin/python` (has pandas + pyarrow):

1. `pd.read_parquet('data/gold/h3_res8_catalonia.parquet')`.
2. `score_dataframe(df, preset=p)` for each of the 4 presets (for chapter 6 toggle).
3. Per chapter, emit `docs/story_data/<chapter>.json` = `[{h3_id, value, …}]`
   (+ recomputed score). Compute headline metrics (min/median/max/mean/coverage)
   into `docs/story_data/manifest.json`.
4. OD arcs + POIs: extract from the existing `docs/catalonia_liveability.html`
   `PAYLOAD` (no re-derivation needed) into `docs/story_data/{arcs,pois}.json`.

**Requires Bash** (to invoke the env python) — gated on user re-enabling Bash
after the pause (see §14).

## 11. File map

**New:** `site/engine/{scrolly.js,story-map.js,codeblock.js,story.css,story.template.html}`,
`site/content/{chapters.json,chapters/*.md}`, `scripts/{export_story_payload.py,build_story.py}`,
`docs/{story.html,.nojekyll}`, `docs/story_data/*`, `docs/{quickstart,architecture,scoring,data_sources,visualization,results}.md`,
this spec.
**Modified:** `README.md` (slim), `docs/index.html` (lean hub + story card + version fix),
`src/catmob/viz.py` (documented LUT; optional dup-`export_deck_html` cleanup),
selected `src/`/`notebooks/` (add `# story:` marker comments).
**Out of scope:** re-running Spark/conda pipeline; wiring the 13 null M2 columns;
PMTiles optimization; pushing/enabling Pages (user's call).

## 12. Work breakdown (parallel waves)

- **Wave A (parallel, Write-only — unblocked now):**
  A1 docs split (6 docs + lean README + lean index.html);
  A2 engine scaffold (`site/engine/*`, dark/satellite, sample data);
  A3 content scaffold (`chapters.json` + prose from §7);
  A4 symbology module (§8) + Esri satellite wiring;
  A5 source markers + chapter→source map (Read/Edit, no Bash).
- **Wave B (needs Bash):** B1 `export_story_payload.py` + run it → real
  `docs/story_data/*`; B2 `build_story.py` → `docs/story.html`.
- **Wave C (integration + verify):** wire real data into the engine; cut
  `story-v1` tag; project-browser permalinks; **visual validation** of the map
  (geospatial-visual-validation skill); link-check; markdown lint.

## 13. Verification

- `export_story_payload.py` record counts == 45,220; per-column coverage matches
  §7; recomputed default-preset score distribution matches README
  (median 50, max 64, mean 40.7).
- `docs/story.html` loads; each chapter's camera + layer fires; reveal/compare
  work; tooltips show the active metric; reduced-motion respected.
- **Visual validation** (mandatory for geo output): render the map, eyeball hex
  alignment over satellite, ramp legibility, no wash/over-extrusion occluding
  the base, null-state distinct from low-value.
- All internal doc links resolve post-split; markdown lint clean; permalinks
  resolve at `story-v1`.

## 14. Open items / risks

- **Bash currently denied** by the auto-mode classifier (pause boundary). Wave B
  (data export) and the `story-v1` tag need it. Wave A proceeds without it.
- Esri World Imagery is fair-use + attribution; fine for a portfolio piece.
- 45k hexes × several chapters: if sluggish, aggregate to res-7 parents for
  overview zooms (deck handles 45k fine in testing).
- Story prose should stay honest about the prototype's dev-scope (7 days, sparse
  amenity coverage, score = relative index).

## 15. Pivot 2 (2026-06-15) — drop scrollytelling + code-presentation; adopt the wildfire geo-browser

User redirect: the scrollytelling story and the project-browser (embedded code
blocks + GitHub source permalinks) are **dropped**. Rationale: this is an applied
spatial-data-science repo; the external site should **support/showcase** the
analysis and link back to the repo, not mirror its source.

New target = the **wildfire-exposure-eo geo-browser** pattern (`/home/nls/Documents/dev/wildfire-exposure-eo/docs/index.html` + `app/app.js`):
a left study `<aside>` (narrative, layer toggles tagged input/output with honest
captions, legend, headline metrics, scope caveats, **Mermaid pipeline DAG**, repo
links) beside a right full-height map. Single page `docs/explore.html`.

Kept: dark UI over **Esri satellite**, 2.5D extruded H3 hexes, the colourblind
ramps + slate-grey nulls, the exported `docs/story_data/*` (45,220 hexes), the
6-doc split + lean `index.html` hub (now linking `explore.html`). Reuse the deck.gl
layer logic from `docs/app/story-map.js`.

Retired: `docs/story.html`, `site/engine/scrolly.js`, `codeblock.js`, the
`site/content/chapters` narrative, the reusable-storytelling subproduct framing,
and the marker/permalink reconciliation (no code embeds → moot).

**Success bar:** the geo-browser must actually render the H3 hexes over satellite
(screenshot-verified), since the prior story page's hex layer could not be
certified in headless.
