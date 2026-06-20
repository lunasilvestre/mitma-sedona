# UI test harness — `docs/explore.html`

Headless-browser smoke test for the deck.gl geo-browser. It serves `docs/` over
a local static server, drives **headless Chromium with software WebGL**
(SwiftShader — no GPU required), waits for the map to actually render, captures
screenshots, and verifies the canvas is non-blank. Built for re-running after
each rebuild / UI update with Claude Code.

## Run it

From the repo root:

```bash
scripts/ui_test.sh             # serve + render + screenshot + verify
scripts/ui_test.sh --install   # first time: npm install + playwright chromium
```

Or via npm:

```bash
cd tests/ui
npm install                    # installs playwright + chromium (postinstall)
npm run smoke                  # == node smoke.mjs
```

Exit code is `0` only when a **real screenshot of a rendered map** was captured.
A JSON summary is printed to stdout (`smoke_works`, `map_rendered`,
`smoke_screenshot_path`, `console_errors_seen`, `failed_requests`, `blockers`).

## What you get

Screenshots land in `tests/ui/proof/_smoke/` (git-ignored, regenerated each run):

- `default.png` — full page (narrative panel + map).
- `default.canvas.png` — map area only (basemap + deck.gl hexes).

## Why software WebGL

deck.gl draws the H3 hexes to a WebGL `<canvas>`. Headless has no GPU, so we
force ANGLE's SwiftShader backend; without it the canvas stays blank. The flags
live in `lib/browser.mjs` → `SOFTWARE_GL_FLAGS`:

```
--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader
--ignore-gpu-blocklist --enable-webgl --no-sandbox --disable-dev-shm-usage
```

The smoke run reports the active renderer, e.g.
`ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device ...), SwiftShader driver)`.

## Files

| File | Role |
| --- | --- |
| `lib/server.mjs` | Serves `docs/` via `python3 -m http.server` on an auto-picked free port. Tracks only the child PID; `stop()` kills that one process (never a broad pkill). |
| `lib/browser.mjs` | Launches headless Chromium (software GL), opens `/explore.html`, waits for render (`#map-deck` + `window.gb.deck` + hex data + networkidle + settle), captures both screenshots, collects console / page errors / failed requests, and samples canvas pixels. |
| `smoke.mjs` | End-to-end entrypoint wiring server + browser; writes `proof/_smoke/`, prints the JSON summary, sets the exit code. |
| `../../scripts/ui_test.sh` | One-command wrapper (`--install` to bootstrap the browser). |

## Reuse for other views

The page accepts deep-link query params (`?basemap=`, `?preset=`, `?metric=`,
`?extrude=1`, `?arcs=1`, `?pois=1`, `?methodology=1`). Pass them through
`openExplore(browser, baseUrl, { query: "metric=tree_cover_pct&arcs=1" })` to
capture alternate states.

## Render-ready signal

The page exposes `window.gb` (the `GeoBrowser` instance) and appends
`<canvas id="map-deck">`. The harness waits until `gb.deck` exists, `gb._hexes`
is populated, and the canvas is sized — then settles network + a short delay so
deck.gl finishes its first GPU draw before the screenshot.

## Requirements

`node`, `npm`, `python3`. No GPU, no API keys. The page pulls deck.gl / MapLibre
/ h3-js from unpkg and Esri World Imagery tiles, so the run needs network access.
