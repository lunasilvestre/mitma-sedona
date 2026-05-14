"""MITMA v2 OD-flow loader.

Source bucket: ``https://movilidad-opendata.mitma.es/``

Two file kinds matter for this project:

* **Daily OD distritos** — one ``.csv.gz`` per day under
  ``estudios_basicos/por-distritos/viajes/ficheros-diarios/``.
* **Hourly OD distritos** — one ``.csv.gz`` per day under
  ``estudios_basicos/por-distritos/viajes/<YYYY-MM>/``.

Both files are **semicolon-delimited UTF-8** and use **string zone IDs with
leading-zero padding** (e.g. ``"08019_AC02"``). Casting zone IDs to int is a
known footgun and produces silent mis-joins downstream.

This module is a thin wrapper around either pandas (for tests/fixtures) or a
SedonaContext-bound DataFrameReader (for the real pipeline). The Sedona path
is deferred to M2 implementation; the pandas path is used by tests.
"""
from __future__ import annotations

import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from .schemas import MITMA_DAILY_OD_SCHEMA, MITMA_HOURLY_OD_SCHEMA

# Catalonia province codes (INE) — used to filter zone-ID prefixes early.
CATALONIA_PROVINCE_PREFIXES: tuple[str, ...] = ("08", "17", "25", "43")

MITMA_BASE_URL = "https://movilidad-opendata.mitma.es"


@dataclass(frozen=True)
class MitmaFile:
    """Locator for a single MITMA file (does not fetch on its own)."""

    kind: str  # 'daily' | 'hourly'
    date: str  # YYYY-MM-DD
    url: str
    local_path: Path

    @property
    def schema(self):
        return MITMA_DAILY_OD_SCHEMA if self.kind == "daily" else MITMA_HOURLY_OD_SCHEMA


def parse_csv_gz(
    path: Path | str,
    *,
    kind: str,
    catalonia_only: bool = True,
) -> pd.DataFrame:
    """Parse a MITMA gzipped CSV into a validated pandas DataFrame.

    Parameters
    ----------
    path
        Path to a ``.csv.gz`` file (local). Tested with the fixture under
        ``tests/fixtures/mitma_daily_sample.csv.gz``.
    kind
        ``'daily'`` or ``'hourly'`` — selects the schema applied to validate.
    catalonia_only
        If True (default), keep only rows whose ``origen`` OR ``destino`` zone
        starts with a Catalonia province prefix (08/17/25/43).

    Returns
    -------
    pandas.DataFrame
        Schema-validated. Raises ``pandera.errors.SchemaError`` on violation.

    Notes
    -----
    The Sedona/Spark equivalent will read the same files via
    ``spark.read.option("sep", ";").option("encoding", "UTF-8").csv(...)`` and
    apply the same schema as ``pandera_pyspark`` checks; we keep the pandas
    path here because it's exercised by the test suite without a JVM.
    """
    if kind not in {"daily", "hourly"}:
        raise ValueError(f"kind must be 'daily' or 'hourly', got {kind!r}")

    path = Path(path)
    open_fn = gzip.open if path.suffix == ".gz" else open
    with open_fn(path, "rt", encoding="utf-8") as fh:
        df = pd.read_csv(
            fh,
            sep=";",
            dtype={
                "fecha": str,
                "origen": str,
                "destino": str,
                "distancia": str,
                "actividad_origen": str,
                "actividad_destino": str,
                "residencia": str,
                "renta": str,
                "edad": str,
                "sexo": str,
            },
        )

    if catalonia_only:
        df = filter_catalonia(df)

    schema = MITMA_DAILY_OD_SCHEMA if kind == "daily" else MITMA_HOURLY_OD_SCHEMA
    return schema.validate(df, lazy=True)


def filter_catalonia(df: pd.DataFrame) -> pd.DataFrame:
    """Keep rows where origin OR destination is in Catalonia."""
    mask = df["origen"].str.startswith(CATALONIA_PROVINCE_PREFIXES) | df[
        "destino"
    ].str.startswith(CATALONIA_PROVINCE_PREFIXES)
    return df.loc[mask].reset_index(drop=True)


def build_url(kind: str, date: str) -> str:
    """Build the canonical MITMA URL for a given day.

    Examples
    --------
    >>> build_url("daily", "2024-10-15")
    'https://movilidad-opendata.mitma.es/estudios_basicos/por-distritos/viajes/ficheros-diarios/2024-10/20241015_Viajes_distritos.csv.gz'
    >>> build_url("hourly", "2024-10-15")
    'https://movilidad-opendata.mitma.es/estudios_basicos/por-distritos/viajes/2024-10/20241015_Viajes_distritos.csv.gz'
    """
    if kind not in {"daily", "hourly"}:
        raise ValueError(f"kind must be 'daily' or 'hourly', got {kind!r}")
    yyyymm = date[:7]  # 'YYYY-MM'
    yyyymmdd = date.replace("-", "")  # 'YYYYMMDD'
    if kind == "daily":
        return (
            f"{MITMA_BASE_URL}/estudios_basicos/por-distritos/viajes/"
            f"ficheros-diarios/{yyyymm}/{yyyymmdd}_Viajes_distritos.csv.gz"
        )
    return (
        f"{MITMA_BASE_URL}/estudios_basicos/por-distritos/viajes/"
        f"{yyyymm}/{yyyymmdd}_Viajes_distritos.csv.gz"
    )


def plan_files(
    kind: str,
    dates: Iterable[str],
    *,
    cache_dir: Path | str = "data/bronze/mitma",
) -> list[MitmaFile]:
    """Return MitmaFile descriptors for a date range, no I/O performed."""
    cache_dir = Path(cache_dir)
    out: list[MitmaFile] = []
    for date in dates:
        url = build_url(kind, date)
        local = cache_dir / kind / date[:7] / Path(url).name
        out.append(MitmaFile(kind=kind, date=date, url=url, local_path=local))
    return out


# ---------------------------------------------------------------------------
# Spark / Sedona path
# ---------------------------------------------------------------------------

def _spark_schema_for(kind: str):
    """Build a Spark StructType matching MITMA_DAILY_OD_SCHEMA / HOURLY."""
    from pyspark.sql.types import (
        DoubleType,
        IntegerType,
        StringType,
        StructField,
        StructType,
    )

    fields = [
        StructField("fecha",             StringType(),  False),
        StructField("origen",            StringType(),  False),
        StructField("destino",           StringType(),  False),
        StructField("distancia",         StringType(),  False),
        StructField("actividad_origen",  StringType(),  False),
        StructField("actividad_destino", StringType(),  False),
        StructField("residencia",        StringType(),  True),
        StructField("renta",             StringType(),  True),
        StructField("edad",              StringType(),  True),
        StructField("sexo",              StringType(),  True),
        StructField("viajes",            DoubleType(),  False),
        StructField("viajes_km",         DoubleType(),  False),
    ]
    if kind == "hourly":
        fields.insert(1, StructField("periodo", IntegerType(), False))
    return StructType(fields)


def read_with_sedona(
    spark,  # noqa: ANN001  pyspark.sql.SparkSession
    paths: list[str] | str,
    *,
    kind: str = "daily",
    catalonia_only: bool = True,
):
    """Read MITMA OD files via Spark with the documented schema.

    Uses the Wherobots-aligned semicolon-delimited UTF-8 read pattern (see
    docs/sedona_sql_patterns.md §2). Filters at the SQL level so only
    Catalonia-touching rows are materialised.
    """
    if kind not in {"daily", "hourly"}:
        raise ValueError(f"kind must be 'daily' or 'hourly', got {kind!r}")
    schema = _spark_schema_for(kind)

    df = (
        spark.read.option("sep", ";")
            .option("encoding", "UTF-8")
            .option("header", "true")
            .schema(schema)
            .csv(paths)
    )
    if catalonia_only:
        df = df.where(
            "substring(origen,1,2) IN ('08','17','25','43') "
            "OR substring(destino,1,2) IN ('08','17','25','43')"
        )
    return df


def write_bronze_parquet(df, out_dir: str, *, kind: str, partition_by: str = "fecha"):
    """Write a Spark DataFrame to bronze/, partitioned for fast filter pushdown."""
    (
        df.write.mode("overwrite")
          .partitionBy(partition_by)
          .parquet(f"{out_dir.rstrip('/')}/{kind}/")
    )
