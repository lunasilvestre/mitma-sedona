"""Descriptive statistics helpers for notebook 04.

Pure pandas. Heavy lifting (full MITMA aggregation) is done by Sedona in
the notebook; these helpers operate on the post-aggregation DataFrames.
"""
from __future__ import annotations

import pandas as pd

WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def add_weekday(df: pd.DataFrame, *, fecha_col: str = "fecha") -> pd.DataFrame:
    """Add a ``weekday`` (mon..sun) column from a YYYYMMDD string column."""
    out = df.copy()
    dt = pd.to_datetime(out[fecha_col], format="%Y%m%d")
    out["weekday"] = dt.dt.dayofweek.map(dict(enumerate(WEEKDAY_NAMES)))
    out["is_weekend"] = dt.dt.dayofweek >= 5
    out["month"] = dt.dt.to_period("M").astype(str)
    return out


def daily_totals_by_weekday(df: pd.DataFrame, *, value_col: str = "viajes") -> pd.DataFrame:
    """Average daily total by weekday — for the "weekday vs weekend" chart."""
    if "weekday" not in df.columns:
        df = add_weekday(df)
    daily = df.groupby(["fecha", "weekday"])[value_col].sum().reset_index()
    return daily.groupby("weekday")[value_col].mean().reindex(WEEKDAY_NAMES).reset_index()


def hourly_profile(df: pd.DataFrame, *, value_col: str = "viajes") -> pd.DataFrame:
    """Weekday × hour-of-day total — for the heatmap."""
    if "weekday" not in df.columns:
        df = add_weekday(df)
    return (
        df.groupby(["weekday", "periodo"])[value_col].sum()
          .unstack("periodo").reindex(WEEKDAY_NAMES).fillna(0)
    )


def peak_hour_per_corridor(df: pd.DataFrame, *, n: int = 20) -> pd.DataFrame:
    """For top-N OD corridors by total flow, return their peak hour."""
    od_total = df.groupby(["origen", "destino"])["viajes"].sum().nlargest(n).reset_index()
    top_pairs = list(zip(od_total["origen"], od_total["destino"]))
    sub = df[df.set_index(["origen", "destino"]).index.isin(top_pairs)]
    peak = (
        sub.groupby(["origen", "destino", "periodo"])["viajes"].sum().reset_index()
           .sort_values(["origen", "destino", "viajes"], ascending=[True, True, False])
           .drop_duplicates(["origen", "destino"])
           .rename(columns={"periodo": "peak_hour"})
    )
    return od_total.merge(peak[["origen", "destino", "peak_hour"]], on=["origen", "destino"])


def anomaly_days(df: pd.DataFrame, *, value_col: str = "viajes", sigma: float = 3.0) -> pd.DataFrame:
    """Days whose total deviates more than ``sigma``σ from the mean."""
    daily = df.groupby("fecha")[value_col].sum().reset_index()
    mu = daily[value_col].mean()
    sd = daily[value_col].std()
    daily["z_score"] = (daily[value_col] - mu) / sd
    return daily.loc[daily["z_score"].abs() > sigma].sort_values("z_score")


def distance_band_share(df: pd.DataFrame) -> pd.DataFrame:
    """Share of viajes by distance band, separated by weekend vs weekday."""
    if "is_weekend" not in df.columns:
        df = add_weekday(df)
    grp = df.groupby(["is_weekend", "distancia"])["viajes"].sum().reset_index()
    grp["share"] = grp.groupby("is_weekend")["viajes"].transform(lambda x: x / x.sum())
    return grp
