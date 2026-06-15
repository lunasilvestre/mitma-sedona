# mitma-sedona

[![tests](https://img.shields.io/badge/tests-44%20passing-green.svg)](docs/quickstart.md#tests)
[![python](https://img.shields.io/badge/python-3.11+-blue.svg)](#)
[![sedona](https://img.shields.io/badge/sedona-1.9+-orange.svg)](#)
[![spark](https://img.shields.io/badge/spark-4.1-orange.svg)](#)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **Where in Catalonia could I live well?** Within bike-reach of a train station that connects to Barcelona, with climbing gyms and yoga nearby, close to green or sea, away from heavy industry and motorway noise — and breathing clean air, away from urban heat and light pollution, near biodiversity, with health amenities at hand.

A multi-criteria liveability index over Catalonia, computed at H3 res-8
(~0.7 km² hexes) with Apache Sedona on Spark for the spatial-join heavy lifting.
It is also a portfolio piece: a real personal question, answered with real data
engineering.

**→ [Explore the interactive story map](docs/story.html)** — a dark,
satellite-backed scrollytelling geo-browser that walks through the pipeline's
inputs → steps → outputs. *(Dev-scope prototype: sparse coverage in places, and
the liveability score is a relative index, not a guarantee.)*

## Docs

| Doc | What's in it |
|---|---|
| [docs/quickstart.md](docs/quickstart.md) | Run it in 5 min (tests + preview) or 10 min (full Docker stack) |
| [docs/architecture.md](docs/architecture.md) | Repo layout + Bronze→Silver→Gold lakehouse + Sedona SQL idioms |
| [docs/scoring.md](docs/scoring.md) | The liveability score: 6 dimensions, weights, and presets |
| [docs/data_sources.md](docs/data_sources.md) | Every source + licence + the default data window |
| [docs/visualization.md](docs/visualization.md) | The deck.gl/Lonboard stack and the story-map geo-browser |
| [docs/results.md](docs/results.md) | Prototype artifacts, Top-10, distribution + the retrospective |
| **[docs/story.html](docs/story.html)** | **The interactive story map** (scrollytelling geo-browser) |
| [PLAN.md](PLAN.md) | Canonical planning doc + full milestone breakdown |
| [NOTES_FROM_PROTOTYPE_RUN.md](NOTES_FROM_PROTOTYPE_RUN.md) | "What tripped us / how we fixed it" log |

Milestones are tracked in [PLAN.md](PLAN.md) — through **M6, working prototype
on real Catalonia data** (the latest run; see [docs/results.md](docs/results.md)).

## Attribution

- *Datos de movilidad: Ministerio de Transportes y Movilidad Sostenible (MITMS)*
- *© OpenStreetMap contributors, ODbL*
- *© European Environment Agency (EEA)*
- *Generated using Copernicus data and information funded by the European Union — Copernicus Climate Change Service / Atmosphere Monitoring Service*
- *iNaturalist (via GBIF) — CC BY-NC*
- *Generalitat de Catalunya — analisi.transparenciacatalunya.cat*

## Licence

[MIT](LICENSE) — code only. Each upstream dataset retains its own
licence; see the attribution block above and
[docs/data_sources.md](docs/data_sources.md).

---

Built by [@lunasilvestre](https://github.com/lunasilvestre) on atlas
with Cowork + Claude Code.
