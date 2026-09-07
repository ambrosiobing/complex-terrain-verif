"""Grid to station matching: interpolation, lapse-rate correction, UTC alignment, accumulation.

This is the scientifically load-bearing module. Every choice here is a config entry and is
echoed in docs/METHODOLOGY.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import xarray as xr

from .schema import MATCHED_COLUMNS, ensure_utc

G0 = 9.80665  # m s-2, to turn geopotential into metres


# Interpolation -------------------------------------------------------------------------

def interp_to_stations(da: xr.DataArray, lats: np.ndarray, lons: np.ndarray, method: str) -> np.ndarray:
    """Point values of a (…, lat, lon) regular-grid DataArray at station coordinates.

    ``method`` is ``nearest`` or ``bilinear``. Returns an array with the leading dims of ``da``
    followed by a station dim. Longitudes are wrapped to the grid's convention.
    """
    lon_grid = da["lon"].values
    lons = np.asarray(lons, dtype=float).copy()
    if lon_grid.min() >= 0 and (lons < 0).any():
        lons = np.where(lons < 0, lons + 360.0, lons)
    if lon_grid.min() < 0 and (lons > 180).any():
        lons = np.where(lons > 180, lons - 360.0, lons)
    pts_lat = xr.DataArray(np.asarray(lats, dtype=float), dims="station")
    pts_lon = xr.DataArray(lons, dims="station")
    if method == "nearest":
        out = da.sel(lat=pts_lat, lon=pts_lon, method="nearest")
    elif method == "bilinear":
        out = da.interp(lat=pts_lat, lon=pts_lon, method="linear")
    else:
        raise ValueError(f"unknown interpolation method {method!r}")
    return out.transpose(..., "station").values


# Lapse-rate correction ----------------------------------------------------------------

def lapse_correct(t_model: np.ndarray, model_elev_m: np.ndarray, station_elev_m: np.ndarray,
                  gamma: float = 0.0065) -> np.ndarray:
    """Move a model 2 m temperature from model orography to station height.

    A station that sits ``dz`` metres above the model surface is ``gamma * dz`` colder.
    ``t_corr = t_model - gamma * (station_elev - model_elev)``. NaN elevations leave t unchanged.
    """
    dz = np.asarray(station_elev_m, dtype=float) - np.asarray(model_elev_m, dtype=float)
    corr = np.where(np.isnan(dz), 0.0, gamma * dz)
    return np.asarray(t_model, dtype=float) - corr


def model_elevation_at(orog: xr.DataArray | None, lats, lons, method: str = "bilinear") -> np.ndarray:
    if orog is None:
        return np.full(len(lats), np.nan)
    return interp_to_stations(orog, lats, lons, method)


def load_orography(path: str | Path | None) -> xr.DataArray | None:
    """Model orography in metres on the field grid, or None when the file is absent."""
    if path is None:
        return None
    path = Path(path)
    if not path.exists():
        return None
    ds = xr.open_dataset(path)
    if "orog_m" in ds:
        return ds["orog_m"].load()
    if "surface_geopotential" in ds:
        return (ds["surface_geopotential"] / G0).rename("orog_m").load()
    raise ValueError(f"{path}: expected variable orog_m or surface_geopotential")


# Accumulation -------------------------------------------------------------------------

def deaccumulate(tp_by_lead: pd.Series, window_h: int) -> pd.Series:
    """Window totals from a running total indexed by lead hour.

    ``tp_window(L) = tp(L) - tp(L - window_h)``; leads without a partner are NaN. Small negative
    values from GRIB packing are clipped to zero.
    """
    s = tp_by_lead.sort_index()
    prev = s.reindex(s.index - window_h)
    out = pd.Series(s.values - prev.values, index=s.index)
    return out.clip(lower=0.0)


def accumulate_obs(hourly: pd.Series, window_h: int, min_hours_present: int) -> pd.Series:
    """Rolling window sums of hourly observations ending at each hour.

    Requires at least ``min_hours_present`` non-missing hours in the window, else NaN.
    ``hourly`` must be indexed by a regular hourly DatetimeIndex.
    """
    h = hourly.asfreq("1h")
    counts = h.notna().rolling(window_h).sum()
    sums = h.fillna(0.0).rolling(window_h).sum()
    return sums.where(counts >= min_hours_present)


# Matching -------------------------------------------------------------------------------

def open_field(path: str | Path) -> xr.Dataset:
    ds = xr.open_dataset(path)
    for d in ("lead_h", "lat", "lon"):
        if d not in ds.dims:
            raise ValueError(f"{path}: field file must have dim {d}")
    return ds


def match_field(ds: xr.Dataset, stations: pd.DataFrame, obs: pd.DataFrame, variables: Iterable[str],
                methods: Iterable[str], orog: xr.DataArray | None, lapse_gamma: float | None,
                tolerance_min: int = 0) -> pd.DataFrame:
    """Match one field file (one model, one init) to all stations and observations.

    Returns rows in MATCHED_COLUMNS order. Observations are joined on exact valid time (UTC)
    unless ``tolerance_min`` > 0, in which case the nearest observation within the tolerance is
    used.
    """
    model = str(ds.attrs["model"])
    init = pd.Timestamp(ds.attrs["init_time"])
    init = init.tz_localize("UTC") if init.tzinfo is None else init.tz_convert("UTC")
    leads = ds["lead_h"].values.astype(int)
    lats = stations["lat"].values
    lons = stations["lon"].values
    model_elev = model_elevation_at(orog, lats, lons)
    obs = obs.copy()
    obs["valid_time"] = ensure_utc(obs["valid_time"])

    frames = []
    for var in variables:
        if var not in ds:
            continue
        for method in methods:
            vals = interp_to_stations(ds[var], lats, lons, method)  # (lead, station)
            n_lead, n_st = vals.shape
            df = pd.DataFrame({
                "station_id": np.tile(stations["station_id"].values, n_lead),
                "lead_h": np.repeat(leads, n_st).astype(int),
                "fcst_raw": vals.reshape(-1),
            })
            df["model"] = model
            df["variable"] = var
            df["init_time"] = init
            df["valid_time"] = init + pd.to_timedelta(df["lead_h"], unit="h")
            df["interp"] = method
            df["station_elev_m"] = np.tile(stations["elev_m"].values.astype(float), n_lead)
            df["model_elev_m"] = np.tile(model_elev, n_lead)
            df["lat"] = np.tile(lats, n_lead)
            df["lon"] = np.tile(lons, n_lead)
            df["station_class"] = np.tile(stations.get("station_class", pd.Series(["unknown"] * n_st)).values, n_lead)
            df["county"] = np.tile(stations.get("county", pd.Series([""] * n_st)).values, n_lead)
            if var == "t2m" and lapse_gamma is not None and not np.all(np.isnan(model_elev)):
                df["fcst"] = lapse_correct(df["fcst_raw"].values, df["model_elev_m"].values,
                                           df["station_elev_m"].values, lapse_gamma)
                df["lapse_corrected"] = True
            else:
                df["fcst"] = df["fcst_raw"]
                df["lapse_corrected"] = False
            o = obs.loc[obs["variable"] == var, ["station_id", "valid_time", "value", "qc"]]
            o = o.rename(columns={"value": "obs", "qc": "obs_qc"})
            if tolerance_min > 0:
                df = df.sort_values("valid_time")
                o = o.sort_values("valid_time")
                df = pd.merge_asof(df, o, on="valid_time", by="station_id",
                                   tolerance=pd.Timedelta(minutes=tolerance_min), direction="nearest")
            else:
                df = df.merge(o, on=["station_id", "valid_time"], how="left")
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=MATCHED_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    out["obs_qc"] = out["obs_qc"].astype("Int64")
    out["lead_h"] = out["lead_h"].astype(int)
    return out[MATCHED_COLUMNS]


def match_all(field_files: Iterable[Path], stations: pd.DataFrame, obs: pd.DataFrame, cfg) -> pd.DataFrame:
    """Match every field file listed under the configured models."""
    variables = [k for k, v in cfg["variables"].items() if v.get("enabled", True)]
    methods = list(cfg["interp"]["methods"])
    lapse_cfg = cfg["lapse"]
    orog = load_orography(cfg["paths"].get("orography_nc")) if lapse_cfg.get("enabled", False) else None
    gamma = float(lapse_cfg["gamma_k_per_m"]) if (lapse_cfg.get("enabled", False) and orog is not None) else None
    tol = int(cfg.get("time", {}).get("match_tolerance_min", 0))
    parts = []
    for f in field_files:
        with open_field(f) as ds:
            if str(ds.attrs.get("model")) not in set(cfg["models"]):
                continue
            parts.append(match_field(ds, stations, obs, variables, methods, orog, gamma, tol))
    if not parts:
        return pd.DataFrame(columns=MATCHED_COLUMNS)
    return pd.concat(parts, ignore_index=True)


def pairwise_complete(matched: pd.DataFrame, models: Iterable[str]) -> pd.DataFrame:
    """Keep only samples where every listed model and the observation are present.

    The same-sample rule from the literature: never compare models on different cases.
    Baseline rows (persistence, climatology) ride along with the kept keys.
    """
    models = list(models)
    key = ["station_id", "variable", "init_time", "lead_h", "interp"]
    core = matched[matched["model"].isin(models) & matched["obs"].notna() & matched["fcst"].notna()]
    counts = core.groupby(key)["model"].nunique()
    good = counts[counts == len(models)].index
    idx = pd.MultiIndex.from_frame(matched[key])
    return matched[idx.isin(good)].reset_index(drop=True)
