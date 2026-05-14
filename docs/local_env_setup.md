# Local environment — atlas-side Spark+Sedona without Docker

This is the **canonical recipe** for the local development environment on atlas
(or any Linux x86_64 box). It supersedes the original PLAN.md §15 idea of a
`pip install pyspark` venv: in practice we use **Miniforge3 + a conda env**,
because conda-forge ships prebuilt binaries for the GDAL/PROJ/GEOS/Java stack
and sidesteps the source-build failures that have repeatedly bit the Docker
path (PEP 668 numpy uninstall, GHCR auth, pyrobuf/setuptools breakage, etc.).

Once this env exists, every `pytest` and every M6 notebook execution runs
without touching Docker. The Docker compose stack is kept for outside-contributor
reproducibility (and for Valhalla in v2), not for daily dev.

---

## 1. One-time install (Miniforge3 + mamba)

Miniforge3 bundles `conda` + `mamba` and defaults to the conda-forge channel —
exactly what we want.

```bash
bash <(curl -fsSL https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh) -b -p ~/miniforge3
~/miniforge3/bin/conda init bash
~/miniforge3/bin/conda config --set auto_activate_base false   # keep base out of PS1
```

Open a new shell (or `source ~/.bashrc`) before the next step.

---

## 2. Create the `sedona` env (Python 3.11)

```bash
mamba create -n sedona -y \
  python=3.11 \
  geopandas rasterio pyarrow shapely 'h3-py>=4' xarray \
  pyspark openjdk=21 pyrosm \
  matplotlib altair tqdm pyyaml requests ipywidgets \
  pytest pytest-cov ruff pip

mamba activate sedona

pip install \
  'apache-sedona[spark]>=1.9' 'lonboard>=0.16' 'pydeck>=0.9' \
  'pystac-client>=0.7' 'pandera[pandas]>=0.20' \
  'pytest-benchmark>=4.0' 'nbmake>=1.5'

pip install --no-deps -e .
```

Total wall time on atlas: ~3–5 minutes (most of it the initial mamba solve).

---

## 3. Why Python 3.11, not 3.12

`pyrosm 0.6.2` is the latest release and is **the** blocker on Python ≥ 3.12:

- conda-forge ships builds only for py ≤ 3.11.
- The PyPI sdist fails at build time on modern setuptools — its `pyrobuf`
  build-dep raises `AttributeError: 'PyrobufDistribution' object has no
  attribute 'dry_run'` and upstream is unpatched.

The project's `requires-python = ">=3.11,<3.13"` already accommodates this.
Until pyrosm publishes a py3.12 wheel (or migrates off pyrobuf), **3.11 is
the only working local target**. CI still tests both 3.11 and 3.12 — 3.12
runs skip pyrosm-dependent code paths.

## 4. Why `--no-deps` on the editable install

By the time `pip install -e .` runs, every runtime dep from `pyproject.toml`
is already satisfied — `geopandas`, `pyspark`, etc. from conda-forge;
`apache-sedona`, `lonboard`, etc. from the prior pip step. Letting pip's
resolver loose on the editable install causes it to reach for `pyrosm` from
PyPI (the resolver doesn't know conda already provided it), which re-triggers
the build failure described above. `--no-deps` keeps it from re-resolving.

## 5. Versions landed (2026-05-14)

| Package | Version | Source |
|---|---|---|
| python | 3.11.15 | conda-forge |
| pyspark | 4.1.1 | conda-forge |
| openjdk | 21 | conda-forge |
| apache-sedona | 1.9.0 | PyPI |
| pyrosm | 0.6.2 | conda-forge |
| geopandas | 1.1.3 | conda-forge |
| rasterio | 1.4.4 | conda-forge |
| shapely | 2.1.2 | conda-forge |
| h3-py | 4.4.1 | conda-forge |
| pyarrow | (current) | conda-forge |
| lonboard | 0.16.0 | PyPI |
| pydeck | 0.9.2 | PyPI |
| pystac-client | 0.9.0 | PyPI |
| pandera | 0.31.1 | PyPI |
| catmob | 0.1.0 (editable, `src/catmob/`) | local |

PySpark 4.1.1 sits at the upper end of the project pin (`>=3.5,<5.0`).
If a Sedona 1.9 incompatibility surfaces, downgrade with
`mamba install -n sedona 'pyspark=3.5.*'`.

## 6. Verify

```bash
mamba activate sedona
pytest                 # expect: 44 passed in ~0.4 s
```

For a quick Spark/Sedona smoke test (M6 entry point):

```python
from sedona.spark import SedonaContext
sedona = SedonaContext.create(SedonaContext.builder().getOrCreate())
sedona.sql("SELECT ST_AsText(ST_Point(2.16, 41.39))").show()
# +---------------------------+
# |st_astext(st_point(2.16,...|
# +---------------------------+
# |POINT (2.16 41.39)         |
# +---------------------------+
```

## 7. Daily use

```bash
mamba activate sedona
cd ~/Documents/dev/mitma-sedona
# now: pytest, jupyter lab, python notebooks/01_*.py, etc.
```

## 8. Local env vs Docker — when to use which

| Use case | Path |
|---|---|
| pytest, notebook execution, scoring, viz HTML export | **local sedona env** |
| Outside-contributor reproducibility, CI parity check | docker compose |
| Valhalla bike isochrones (v2) | docker compose (`valhalla` service) |

The local env is the fast path. Docker remains the source of truth for
"clone-and-run" reproducibility once we publish the repo.

## 9. Teardown / rebuild

```bash
mamba env remove -n sedona -y
# then repeat §2
```

Rebuild is idempotent and takes ~3 min — cheap enough to treat as
disposable when chasing dependency conflicts.
