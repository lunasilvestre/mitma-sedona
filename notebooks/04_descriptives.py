# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 04 — Descriptive statistics & charts
#
# Justifies the firepower of `--scope full`. Without 6 months of daily +
# 1 month of hourly data, none of these charts are interesting.
#
# **8 publication-quality charts** saved to `docs/screenshots/`:
#
# 1. Weekday vs weekend daily-flow bar chart (per province + Catalonia total)
# 2. Per-weekday hourly profile heatmap
# 3. Rush-hour identification — top-20 BCN-bound corridors
# 4. Distance-band shifts by weekday
# 5. Activity-pair matrix (`casa→trabajo_estudio` etc.)
# 6. Per-province comparison
# 7. Anomaly day surface (Easter / Spring break)
# 8. Seasonality across Q1+Q2 2024

# %%
import sys
from pathlib import Path

REPO = Path("/workspace") if Path("/workspace").exists() else Path.cwd().parent
sys.path.insert(0, str(REPO / "src"))

import altair as alt
import matplotlib.pyplot as plt
import pandas as pd
from catmob import stats
from sedona.spark import SedonaContext

config = SedonaContext.builder().appName("mitma-sedona-04-stats").getOrCreate()
sedona = SedonaContext.create(config)

SCREENSHOT_DIR = REPO / "docs/screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# %% [markdown]
# ## 1. Aggregate to pandas

# %%
daily_total = (
    sedona.read.parquet(str(REPO / "data/bronze/mitma_parquet/daily"))
        .groupBy("fecha", "origen")
        .sum("viajes")
        .withColumnRenamed("sum(viajes)", "viajes")
        .toPandas()
)
daily_total["provincia"] = daily_total["origen"].str[:2]
daily_total = stats.add_weekday(daily_total)
print(f"Daily aggregate rows: {len(daily_total):,}")

hourly_total = (
    sedona.read.parquet(str(REPO / "data/bronze/mitma_parquet/hourly"))
        .groupBy("fecha", "periodo", "origen", "destino", "distancia",
                 "actividad_origen", "actividad_destino")
        .sum("viajes")
        .withColumnRenamed("sum(viajes)", "viajes")
        .toPandas()
)
hourly_total = stats.add_weekday(hourly_total)
print(f"Hourly aggregate rows: {len(hourly_total):,}")

# %% [markdown]
# ## 2. Chart 1 — Weekday vs weekend

# %%
wkd = stats.daily_totals_by_weekday(daily_total)
fig, ax = plt.subplots(figsize=(8, 4))
colors = ["#88d4ff"]*5 + ["#ff6b9d"]*2
ax.bar(wkd["weekday"], wkd["viajes"], color=colors)
ax.set_title("MITMA daily flows — weekday vs weekend (Catalonia)")
ax.set_ylabel("Average daily trips")
fig.tight_layout()
fig.savefig(SCREENSHOT_DIR / "01_weekday_vs_weekend.png", dpi=150)
plt.show()

# %% [markdown]
# ## 3. Chart 2 — Per-weekday hourly profile (heatmap)

# %%
heat = stats.hourly_profile(hourly_total)
fig, ax = plt.subplots(figsize=(12, 4))
im = ax.imshow(heat.values, aspect="auto", cmap="viridis")
ax.set_yticks(range(7), heat.index)
ax.set_xticks(range(24), [f"{h:02d}" for h in range(24)])
ax.set_xlabel("Hour of day")
ax.set_title("MITMA hourly flows — weekday × hour (March 2024)")
fig.colorbar(im, label="Total trips")
fig.tight_layout()
fig.savefig(SCREENSHOT_DIR / "02_hourly_heatmap.png", dpi=150)
plt.show()

# %% [markdown]
# ## 4. Chart 3 — Rush-hour identification (top-20 corridors)

# %%
peaks = stats.peak_hour_per_corridor(hourly_total, n=20)
print(peaks.to_string(index=False))

# %% [markdown]
# ## 5. Chart 4 — Distance-band shifts by weekday

# %%
band = stats.distance_band_share(hourly_total)
chart = (
    alt.Chart(band).mark_bar()
        .encode(
            x=alt.X("distancia:N", sort=["0.5-2","2-10","10-50","50-100",">100"]),
            y=alt.Y("share:Q", axis=alt.Axis(format=".0%")),
            color=alt.Color("is_weekend:N", title="Weekend?"),
            column="is_weekend:N",
        )
        .properties(title="Distance-band share — weekday vs weekend")
)
chart.save(str(SCREENSHOT_DIR / "04_distance_band.png"))
chart

# %% [markdown]
# ## 6. Chart 5 — Activity-pair matrix

# %%
act_matrix = (
    hourly_total.groupby(["actividad_origen", "actividad_destino"])["viajes"]
        .sum().unstack().fillna(0)
)
fig, ax = plt.subplots(figsize=(6, 5))
im = ax.imshow(act_matrix.values, cmap="magma")
ax.set_xticks(range(len(act_matrix.columns)), act_matrix.columns, rotation=45)
ax.set_yticks(range(len(act_matrix.index)), act_matrix.index)
ax.set_title("Activity-pair flow matrix")
fig.colorbar(im, label="Total trips")
fig.tight_layout()
fig.savefig(SCREENSHOT_DIR / "05_activity_matrix.png", dpi=150)
plt.show()

# %% [markdown]
# ## 7. Chart 6 — Per-province comparison

# %%
prov = (
    daily_total.groupby(["provincia", "weekday"])["viajes"].mean().unstack()
        .reindex(["08", "17", "25", "43"])
        .rename(index={"08":"Barcelona","17":"Girona","25":"Lleida","43":"Tarragona"})
)
prov.plot(kind="bar", figsize=(10, 4), title="Average daily flows by province × weekday")
plt.tight_layout()
plt.savefig(SCREENSHOT_DIR / "06_per_province.png", dpi=150)
plt.show()

# %% [markdown]
# ## 8. Chart 7 — Anomaly days (3σ)

# %%
anom = stats.anomaly_days(daily_total)
print("Anomaly days (3σ from mean):")
print(anom)

# %% [markdown]
# ## 9. Chart 8 — Seasonality across Q1+Q2 2024

# %%
seasonal = (
    daily_total.groupby(["fecha"])["viajes"].sum().reset_index()
        .assign(date=lambda d: pd.to_datetime(d["fecha"], format="%Y%m%d"))
        .sort_values("date")
)
fig, ax = plt.subplots(figsize=(12, 4))
ax.plot(seasonal["date"], seasonal["viajes"], color="#88d4ff", linewidth=1.5)
ax.set_title("MITMA daily flows — Q1+Q2 2024 (Catalonia)")
ax.set_ylabel("Total trips")
fig.autofmt_xdate()
fig.tight_layout()
fig.savefig(SCREENSHOT_DIR / "08_seasonality.png", dpi=150)
plt.show()
