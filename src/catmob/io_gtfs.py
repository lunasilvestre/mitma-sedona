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


# Catalonia stop bbox (lon_min, lon_max, lat_min, lat_max). The Renfe national
# feed ships all-Spain stops (only ~204 in Catalonia); the unified
# GTFS_STOPS_SCHEMA is strict on lon∈(0,4)/lat∈(40.5,42.9), so out-of-bbox
# stops MUST be dropped before validate or it raises.
STOP_BBOX = (0.0, 4.0, 40.5, 42.9)


def _read_csv_stripped(path: Path | str) -> pd.DataFrame:
    """read_csv with header whitespace stripped (some feeds pad column names)."""
    df = pd.read_csv(path, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    return df


def _read_gtfs_table(zip_path: Path, table: str) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(f"{table}.txt") as fh:
            df = pd.read_csv(fh, dtype=str)
            df.columns = [c.strip() for c in df.columns]
            return df


def _in_stop_bbox(lon: pd.Series, lat: pd.Series) -> pd.Series:
    lo_min, lo_max, la_min, la_max = STOP_BBOX
    return lon.between(lo_min, lo_max) & lat.between(la_min, la_max)


def load_stops(gtfs_dir: Path | str, *, feed: str) -> pd.DataFrame:
    """Load stops.txt from an unzipped GTFS dir into the unified schema.

    Bbox-prefilters to Catalonia (national feeds carry all-Spain stops) before
    schema validation, which is strict on the Catalan lon/lat ranges.
    """
    df = _read_csv_stripped(Path(gtfs_dir) / "stops.txt")
    df = df.dropna(subset=["stop_id", "stop_lon", "stop_lat"])
    out = pd.DataFrame(
        {
            "stop_id": df["stop_id"].astype(str),
            "stop_name": df["stop_name"].fillna("").astype(str),
            "lon": df["stop_lon"].astype(float),
            "lat": df["stop_lat"].astype(float),
            "feed": feed,
        }
    )
    out = out[_in_stop_bbox(out["lon"], out["lat"])].reset_index(drop=True)
    return GTFS_STOPS_SCHEMA.validate(out, lazy=True)


def _active_services(gd: Path, weekday: str) -> list[str]:
    """Service_ids running on a representative weekday.

    Prefers ``calendar.txt`` (the chosen weekday column == 1). FGC ships only
    ``calendar_dates.txt`` (no calendar.txt) → fall back to it: parse the
    service-exception dates (``exception_type == 1`` = added), pick the
    Wednesday whose service_ids cover the most exceptions, and treat those
    service_ids as the active weekday set.
    """
    cal_path = gd / "calendar.txt"
    if cal_path.exists():
        calendar = _read_csv_stripped(cal_path)
        return calendar.loc[calendar[weekday].astype(str) == "1", "service_id"].tolist()

    # FGC fallback — calendar_dates.txt only.
    import pandas as _pd

    cd = _read_csv_stripped(gd / "calendar_dates.txt")
    cd = cd[cd["exception_type"].astype(str) == "1"].copy()
    cd["_dt"] = _pd.to_datetime(cd["date"], format="%Y%m%d", errors="coerce")
    cd = cd.dropna(subset=["_dt"])
    weekday_idx = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }[weekday]
    wed = cd[cd["_dt"].dt.weekday == weekday_idx]
    if wed.empty:
        wed = cd  # no matching weekday at all → use every added date
    # Pick the single date whose service_ids cover the most exception rows.
    best_date = wed.groupby("date").size().idxmax()
    return wed.loc[wed["date"] == best_date, "service_id"].unique().tolist()


def compute_frequency(gtfs_dir: Path | str, *, weekday: str = "wednesday") -> pd.DataFrame:
    """Compute trips_per_day and trips_to_bcn_core per stop.

    ``weekday`` selects which calendar.txt boolean column to honour (defaults
    to wednesday). When the feed ships no calendar.txt (FGC), falls back to
    calendar_dates.txt (see ``_active_services``). The stop set is bbox-
    prefiltered to Catalonia so it lines up with ``load_stops``.
    """
    gd = Path(gtfs_dir)
    stops = _read_csv_stripped(gd / "stops.txt")
    trips = _read_csv_stripped(gd / "trips.txt")
    stop_times = _read_csv_stripped(gd / "stop_times.txt")

    # Restrict to in-bbox (Catalonia) stops so frequency aligns with load_stops.
    stops = stops.assign(
        _lon=pd.to_numeric(stops["stop_lon"], errors="coerce"),
        _lat=pd.to_numeric(stops["stop_lat"], errors="coerce"),
    )
    stops = stops[_in_stop_bbox(stops["_lon"], stops["_lat"])].copy()
    in_bbox_ids = set(stops["stop_id"].astype(str))

    active = _active_services(gd, weekday)
    weekday_trips = trips.loc[trips["service_id"].isin(active), "trip_id"].tolist()

    weekday_st = stop_times.loc[
        stop_times["trip_id"].isin(weekday_trips)
        & stop_times["stop_id"].isin(in_bbox_ids)
    ].copy()

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
