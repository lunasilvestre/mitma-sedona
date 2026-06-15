# Quickstart

Two ways to run mitma-sedona locally: a 5-minute tests-only path (no Docker),
and the full Docker stack for the Sedona/Spark pipeline.

[← back to README](../README.md)

## Try it (5 min, no Docker)

Run the contract tests and open the standalone deck.gl preview. This needs only
a Python venv — no Spark, no Docker.

```bash
git clone git@github.com:lunasilvestre/mitma-sedona.git
cd mitma-sedona
python3 -m venv .venv && source .venv/bin/activate
pip install 'pandera[pandas]>=0.20' pytest pandas
PYTHONPATH=src pytest -q   # → 44 passed in 0.23s
xdg-open docs/preview_deck.html  # standalone deck.gl preview
```

For the curated Miniforge + conda recipe used during development, see
[local_env_setup.md](local_env_setup.md).

## Try it (10 min, full Docker stack)

Brings up the Sedona + Valhalla + JupyterLab compose stack for the real
pipeline (bronze ingest → gold layer → visualisation notebooks).

```bash
git clone git@github.com:lunasilvestre/mitma-sedona.git
cd mitma-sedona
docker compose -f docker/docker-compose.yml up -d
# → JupyterLab on http://localhost:8888 (token printed in logs)
# → Valhalla on   http://localhost:8002
```

## Tests

```bash
PYTHONPATH=src pytest -q       # → 44 passed in 0.23s
PYTHONPATH=src pytest -v       # full output
```

44 contract tests covering schema enforcement, MITMA CSV.gz parsing
(gzip + UTF-8 + semicolon + zone-ID padding), OSM POI categorisation,
XVPCA air-quality parsing, geo invariants, and URL builders. CI runs
them on every push.

---

See also: [architecture.md](architecture.md) ·
[data_sources.md](data_sources.md) · [results.md](results.md)
