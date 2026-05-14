"""GTFS loader — Renfe Rodalies + FGC.

Both feeds are standard GTFS zips. We:
1. Read ``stops.txt``, ``trips.txt``, ``stop_times.txt``, ``calendar.txt``.
2. Compute ``trips_per_day`` per stop on a representative weekday.
3. Compute ``trips_to_bcn_core`` — trips per stop whose route ultimately
   reaches a Barcelona "core" stop (Sants, Passeig de Gràcia, Catalunya,
   Plaça Espanya, Estació de França).

The output schema matches ``GTFS_STOPS_SCHEMA`` and ``GTFS_FREQUENCY_SCHEMA``
in :mod:`catmob.schemas`.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd

from .schemas import GTFS_FREQUENCY_SCHEMA, GTFS_STOPS_SCHEMA

#: Stop names treated as "Barcelona core" — case-insensitive substring match.
BCN_CORE_STOP_NAMES = (
    "barcelona-sants",
    "barcelona sants",
    "passeig de gràcia",
    "passeig de gracia",
    "barcelona-passeig",
    "catalunya",
    "plaça d'espanya",
    "plaça espanya",
    "plaza espanya",
    "estació de frança",
    "estacio de franca",
)


def _read_gtfs_table(zip_path: Path, table: str) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(f"{table}.txt") as fh:
            return pd.read_csv(fh, dtype=str)


def load_stops(gtfs_dir: Path | str, *, feed: str) -> pd.DataFrame:
    """Load stops.txt from an unzipped GTFS dir into the unified schema."""
    p = Path(gtfs_dir) / "stops.txt"
    df = pd.read_csv(p, dtype=str)
    out = pd.DataFrame(
        {
            "stop_id": df["stop_id"].astype(str),
            "stop_name": df["stop_name"].astype(str),
            "lon": df["stop_lon"].astype(float),
            "lat": df["stop_lat"].astype(float),
            "feed": feed,
        }
    )
    return GTFS_STOPS_SCHEMA.validate(out, lazy=True)


def compute_frequency(gtfs_dir: Path | str, *, weekday: str = "wednesday") -> pd.DataFrame:
    """Compute trips_per_day and trips_to_bcn_core per stop.

    ``weekday`` selects which calendar.txt boolean column to honour
    (defaults to wednesday — a representative weekday).
    """
    gd = Path(gtfs_dir)
    stops = pd.read_csv(gd / "stops.txt", dtype=str)
    trips = pd.read_csv(gd / "trips.txt", dtype=str)
    stop_times = pd.read_csv(gd / "stop_times.txt", dtype=str)
    calendar = pd.read_csv(gd / "calendar.txt", dtype=str)

    # Active services on the chosen weekday
    active = calendar.loc[calendar[weekday].astype(str) == "1", "service_id"].tolist()
    weekday_trips = trips.loc[trips["service_id"].isin(active), "trip_id"].tolist()

    weekday_st = stop_times.loc[stop_times["trip_id"].isin(weekday_trips)].copy()

    trips_per_day = (
        weekday_st.groupby("stop_id")["trip_id"].nunique()
            .rename("trips_per_day").reset_index()
    )

    # Identify trips whose path includes any BCN core stop
    name_lc = stops.assign(_lc=stops["stop_name"].str.lower())
    bcn_stop_ids = name_lc.loc[
        name_lc["_lc"].apply(lambda s: any(needle in s for needle in BCN_CORE_STOP_NAMES)),
        "stop_id",
    ].tolist()
    bcn_trips = weekday_st.loc[weekday_st["stop_id"].isin(bcn_stop_ids), "trip_id"].unique()

    trips_to_bcn = (
        weekday_st.loc[weekday_st["trip_id"].isin(bcn_trips)]
            .groupby("stop_id")["trip_id"].nunique()
            .rename("trips_to_bcn_core").reset_index()
    )

    out = (
        trips_per_day.merge(trips_to_bcn, on="stop_id", how="left")
            .fillna({"trips_to_bcn_core": 0})
    )
    out["trips_per_day"] = out["trips_per_day"].astype(int)
    out["trips_to_bcn_core"] = out["trips_to_bcn_core"].astype(int)
    return GTFS_FREQUENCY_SCHEMA.validate(out, lazy=True)


def load_combined(rodalies_dir: Path | str, fgc_dir: Path | str) -> dict[str, pd.DataFrame]:
    """Load both feeds and concatenate, returning {'stops': df, 'freq': df}."""
    stops = pd.concat(
        [load_stops(rodalies_dir, feed="rodalies"), load_stops(fgc_dir, feed="fgc")],
        ignore_index=True,
    )
    freq = pd.concat(
        [compute_frequency(rodalies_dir), compute_frequency(fgc_dir)],
        ignore_index=True,
    )
    return {"stops": stops, "freq": freq}
