"""Realism diagnostics that sit next to the skill scores.

A model can win MAE by predicting the conditional mean (Bonavita 2024; Rasp et al. 2024). These
diagnostics make that visible: variance ratio and field activity per lead, Q-Q plots, and
tail-conditional errors.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import xarray as xr

from .scores import add_strata, mae


def variance_ratio(matched: pd.DataFrame, primary: dict | None = None) -> pd.DataFrame:
    """std(fcst) / std(obs) per model, variable, lead. Below 1 means the forecast is smoother."""
    m = matched.dropna(subset=["fcst", "obs"])
    if primary:
        m = m[[primary.get(v, i) == i for v, i in zip(m["variable"], m["interp"])]]
    rows = []
    for (model, var, lead), g in m.groupby(["model", "variable", "lead_h"]):
        so = g["obs"].std(ddof=1)
        rows.append({"model": model, "variable": var, "lead_h": int(lead), "n": len(g),
                     "std_fcst": g["fcst"].std(ddof=1), "std_obs": so,
                     "variance_ratio": (g["fcst"].std(ddof=1) / so) if so else np.nan})
    return pd.DataFrame(rows)


def activity_vs_lead(field_files: Iterable[Path], variable: str = "t2m") -> pd.DataFrame:
    """Spatial standard deviation of the field per lead, averaged over initialisations.

    Falling activity with lead while MAE stays flat is the blurring fingerprint.
    """
    rows = []
    for f in field_files:
        with xr.open_dataset(f) as ds:
            if variable not in ds:
                continue
            da = ds[variable]
            std = da.std(dim=("lat", "lon")).values
            for L, s in zip(ds["lead_h"].values, std):
                rows.append({"model": ds.attrs.get("model"), "init_time": ds.attrs.get("init_time"),
                             "variable": variable, "lead_h": int(L), "spatial_std": float(s)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.groupby(["model", "variable", "lead_h"], as_index=False)["spatial_std"].mean()


def qq(matched: pd.DataFrame, model: str, variable: str, quantiles: np.ndarray | None = None,
       interp: str | None = None) -> pd.DataFrame:
    """Forecast and observed quantiles side by side (Olivetti and Messori 2024)."""
    q = np.linspace(0.01, 0.99, 50) if quantiles is None else np.asarray(quantiles)
    m = matched[(matched["model"] == model) & (matched["variable"] == variable)].dropna(subset=["fcst", "obs"])
    if interp is not None:
        m = m[m["interp"] == interp]
    return pd.DataFrame({"q": q, "obs_q": np.quantile(m["obs"], q), "fcst_q": np.quantile(m["fcst"], q),
                         "model": model, "variable": variable})


def tail_conditional(matched: pd.DataFrame, q: float = 0.90, side: str = "upper") -> pd.DataFrame:
    """MAE and bias restricted to observed values beyond the ``q`` quantile (per variable).

    ``side`` = upper (heat, strong wind) or lower (cold). Small n is reported, not hidden.
    """
    rows = []
    m = matched.dropna(subset=["fcst", "obs"])
    for var, gv in m.groupby("variable"):
        thr = gv["obs"].quantile(q if side == "upper" else 1 - q)
        sel = gv[gv["obs"] >= thr] if side == "upper" else gv[gv["obs"] <= thr]
        for (model, lead), g in sel.groupby(["model", "lead_h"]):
            rows.append({"variable": var, "model": model, "lead_h": int(lead), "side": side, "q": q,
                         "threshold": float(thr), "n": len(g),
                         "mae": mae(g["fcst"].values, g["obs"].values),
                         "bias": float(np.mean(g["fcst"].values - g["obs"].values))})
    return pd.DataFrame(rows)


def stratify(matched: pd.DataFrame, by: str) -> dict[str, pd.DataFrame]:
    """Partition rows by a stratum column; parts are disjoint and cover every row."""
    m = add_strata(matched) if by in ("valid_hour", "obs_tercile", "valid_date") else matched
    return {str(k): g for k, g in m.groupby(by)}
