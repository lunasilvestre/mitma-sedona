"""Tests for ``catmob.io_mitma`` — the MITMA OD-CSV loader.

Coverage targets:

* gzipped semicolon-delimited UTF-8 parsing
* string preservation of zone IDs (no int casting)
* schema validation enforces enum constraints (distancia, actividad, …)
* Catalonia province filter keeps cross-border (e.g. Catalonia ↔ Valencia)
  when *one* end is inside Catalonia
* URL builders match the documented bucket layout exactly
* hourly schema requires ``periodo``
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pandera.errors
import pytest

from catmob import io_mitma
from catmob.schemas import MITMA_DAILY_OD_SCHEMA, MITMA_HOURLY_OD_SCHEMA


# ---------------------------------------------------------------------------
# parse_csv_gz
# ---------------------------------------------------------------------------

def test_parse_daily_fixture_validates(mitma_daily_fixture: Path) -> None:
    df = io_mitma.parse_csv_gz(mitma_daily_fixture, kind="daily")
    assert len(df) > 0
    # Every row in the fixture has at least one Catalonia province endpoint.
    assert df["origen"].str[:2].isin({"08", "17", "25", "43"}).any()


def test_parse_hourly_fixture_includes_periodo(mitma_hourly_fixture: Path) -> None:
    df = io_mitma.parse_csv_gz(mitma_hourly_fixture, kind="hourly")
    assert "periodo" in df.columns
    assert df["periodo"].between(0, 23).all()


def test_zone_ids_are_strings_with_padding(mitma_daily_fixture: Path) -> None:
    df = io_mitma.parse_csv_gz(mitma_daily_fixture, kind="daily")
    # Either pandas-2 ``object`` or pandas-3 ``string`` is acceptable as long
    # as values stay textual (never coerced to int — the failure mode this
    # test guards against).
    assert pd.api.types.is_string_dtype(df["origen"]) or df["origen"].dtype == object
    assert pd.api.types.is_string_dtype(df["destino"]) or df["destino"].dtype == object
    # All zone IDs should have leading-padded numeric prefix (province code).
    assert (df["origen"].str[:2].str.isdigit()).all()
    # And the province prefix must preserve any leading zero (e.g. "08").
    assert df["origen"].str.startswith(("08", "17", "25", "43", "28", "46")).all()


def test_distance_band_is_categorical(mitma_daily_fixture: Path) -> None:
    df = io_mitma.parse_csv_gz(mitma_daily_fixture, kind="daily")
    allowed = {"0.5-2", "2-10", "10-50", "50-100", ">100"}
    assert set(df["distancia"].unique()).issubset(allowed)


def test_invalid_distance_band_rejected(tmp_path: Path) -> None:
    """Schema must reject a distance value outside the documented bands."""
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "fecha;origen;destino;distancia;actividad_origen;actividad_destino;"
        "viajes;viajes_km\n"
        "20241015;08019_AC02;08019_AC03;100-200;casa;casa;1.0;1.0\n",
        encoding="utf-8",
    )
    # ``lazy=True`` validation surfaces failures as ``SchemaErrors`` (plural);
    # plain ``SchemaError`` only when validation is non-lazy.
    with pytest.raises((pandera.errors.SchemaError, pandera.errors.SchemaErrors)):
        io_mitma.parse_csv_gz(bad, kind="daily")


def test_catalonia_filter_keeps_cross_border_rows(mitma_daily_fixture: Path) -> None:
    """A row with a Valencia (46) origin and Catalonia destination must pass."""
    df = io_mitma.parse_csv_gz(mitma_daily_fixture, kind="daily", catalonia_only=True)
    has_cross_border = (
        (df["origen"].str.startswith("46") & df["destino"].str.startswith(("08", "17", "25", "43")))
        | (df["destino"].str.startswith("46") & df["origen"].str.startswith(("08", "17", "25", "43")))
    )
    assert has_cross_border.any(), "expected at least one Catalonia↔Valencia row"


def test_catalonia_filter_off_keeps_more(mitma_daily_fixture: Path) -> None:
    df_all = io_mitma.parse_csv_gz(mitma_daily_fixture, kind="daily", catalonia_only=False)
    df_cat = io_mitma.parse_csv_gz(mitma_daily_fixture, kind="daily", catalonia_only=True)
    assert len(df_all) >= len(df_cat)


# ---------------------------------------------------------------------------
# build_url
# ---------------------------------------------------------------------------

def test_build_url_daily_matches_documented_layout() -> None:
    url = io_mitma.build_url("daily", "2024-10-15")
    assert url == (
        "https://movilidad-opendata.mitma.es/estudios_basicos/por-distritos/"
        "viajes/ficheros-diarios/2024-10/20241015_Viajes_distritos.csv.gz"
    )


def test_build_url_hourly_matches_documented_layout() -> None:
    url = io_mitma.build_url("hourly", "2024-10-15")
    assert url == (
        "https://movilidad-opendata.mitma.es/estudios_basicos/por-distritos/"
        "viajes/2024-10/20241015_Viajes_distritos.csv.gz"
    )


def test_build_url_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        io_mitma.build_url("weekly", "2024-10-15")


# ---------------------------------------------------------------------------
# plan_files
# ---------------------------------------------------------------------------

def test_plan_files_yields_one_per_date(tmp_path: Path) -> None:
    files = io_mitma.plan_files(
        "daily",
        ["2024-10-01", "2024-10-02", "2024-10-03"],
        cache_dir=tmp_path,
    )
    assert len(files) == 3
    assert files[0].url.endswith("20241001_Viajes_distritos.csv.gz")
    assert all(f.local_path.parent.name == "2024-10" for f in files)


def test_plan_files_attaches_correct_schema() -> None:
    files = io_mitma.plan_files("daily", ["2024-10-15"])
    assert files[0].schema is MITMA_DAILY_OD_SCHEMA
    files = io_mitma.plan_files("hourly", ["2024-10-15"])
    assert files[0].schema is MITMA_HOURLY_OD_SCHEMA


# ---------------------------------------------------------------------------
# filter_catalonia (direct)
# ---------------------------------------------------------------------------

def test_filter_catalonia_drops_pure_outside_rows() -> None:
    df = pd.DataFrame(
        [
            {"origen": "28079_AC01", "destino": "28079_AC02"},  # Madrid ↔ Madrid
            {"origen": "28079_AC01", "destino": "08019_AC02"},  # Madrid → BCN
            {"origen": "08019_AC02", "destino": "08019_AC03"},  # BCN ↔ BCN
        ]
    )
    out = io_mitma.filter_catalonia(df)
    assert len(out) == 2
    assert "28079_AC02" not in out["destino"].tolist() or "28079_AC01" in out["origen"].tolist()
