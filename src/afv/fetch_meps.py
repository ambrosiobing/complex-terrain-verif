"""Tier 1: MET Norway MEPS 2.5 km as the regional NWP baseline, via OPeNDAP.

Verified 7 Sep 2026 on thredds.met.no (meps_det_2_5km_YYYYMMDDTHHZ.ncml, OPeNDAP only):
variables air_temperature_2m, x_wind_10m, y_wind_10m, wind_speed, wind_direction,
precipitation_amount_acc, air_pressure_at_sea_level, surface_geopotential; dims time=67, y=1069,
x=949, singleton height dims; projection_lambert. MEPS is on a Lambert grid, so station values are
extracted by nearest neighbour with a KD-tree on the 2-D lat/lon arrays and emitted directly as
matched rows (interp = nearest).
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import xarray as xr
from scipy.spatial import cKDTree

from .match import G0
from .schema import MATCHED_COLUMNS, ensure_utc

MEPS_LATEST = "https://thredds.met.no/thredds/dodsC/mepslatest/meps_det_2_5km_{init:%Y%m%dT%H}Z.ncml"
MEPS_ARCHIVE = "https://thredds.met.no/thredds/dodsC/meps25epsarchive/{init:%Y/%m/%d}/meps_det_2_5km_{init:%Y%m%dT%H}Z.nc"
VARS = {"t2m": "air_temperature_2m", "ws10": "wind_speed", "msl": "air_pressure_at_sea_level", "tp": "precipitation_amount_acc"}


def open_meps(init: datetime, archive: bool = False) -> xr.Dataset:
    url = (MEPS_ARCHIVE if archive else MEPS_LATEST).format(init=init)
    return xr.open_dataset(url)


def _squeeze(da: xr.DataArray) -> xr.DataArray:
    for d in list(da.dims):
        if d not in ("time", "y", "x") and da.sizes[d] == 1:
            da = da.isel({d: 0}, drop=True)
    return da


def station_indices(ds: xr.Dataset, lats: np.ndarray, lons: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lat2 = ds["latitude"].values
    lon2 = ds["longitude"].values
    tree = cKDTree(np.column_stack([lat2.ravel(), lon2.ravel()]))
    _, idx = tree.query(np.column_stack([lats, lons]))
    iy, ix = np.unravel_index(idx, lat2.shape)
    return iy, ix


def meps_to_matched(ds: xr.Dataset, init: datetime, stations: pd.DataFrame, obs: pd.DataFrame,
                    variables: list[str], leads: list[int], lapse_gamma: float | None = 0.0065) -> pd.DataFrame:
    iy, ix = station_indices(ds, stations["lat"].values, stations["lon"].values)
    times = pd.to_datetime(ds["time"].values)
    init_ts = pd.Timestamp(init).tz_localize("UTC") if pd.Timestamp(init).tzinfo is None else pd.Timestamp(init)
    lead_all = ((times - init_ts.tz_localize(None)) / pd.Timedelta(hours=1)).astype(int)
    keep = np.isin(lead_all, leads)
    orog = _squeeze(ds["surface_geopotential"]).isel(time=0).values / G0 if "surface_geopotential" in ds else None
    model_elev = orog[iy, ix] if orog is not None else np.full(len(stations), np.nan)
    obs = obs.copy()
    obs["valid_time"] = ensure_utc(obs["valid_time"])
    frames = []
    for var in variables:
        name = VARS.get(var)
        if name is None or name not in ds:
            continue
        da = _squeeze(ds[name]).isel(time=np.where(keep)[0])
        vals = da.values[:, iy, ix]                    # (lead, station)
        n_lead, n_st = vals.shape
        df = pd.DataFrame({"station_id": np.tile(stations["station_id"].values, n_lead),
                           "lead_h": np.repeat(lead_all[keep], n_st).astype(int),
                           "fcst_raw": vals.reshape(-1)})
        df["model"] = "meps"
        df["variable"] = var
        df["init_time"] = init_ts
        df["valid_time"] = init_ts + pd.to_timedelta(df["lead_h"], unit="h")
        df["interp"] = "nearest"
        df["station_elev_m"] = np.tile(stations["elev_m"].values.astype(float), n_lead)
        df["model_elev_m"] = np.tile(model_elev, n_lead)
        df["lat"] = np.tile(stations["lat"].values, n_lead)
        df["lon"] = np.tile(stations["lon"].values, n_lead)
        df["station_class"] = np.tile(stations["station_class"].values, n_lead)
        df["county"] = np.tile(stations["county"].values, n_lead)
        if var == "t2m" and lapse_gamma is not None and orog is not None:
            df["fcst"] = df["fcst_raw"] - lapse_gamma * (df["station_elev_m"] - df["model_elev_m"])
            df["lapse_corrected"] = True
        else:
            df["fcst"] = df["fcst_raw"]
            df["lapse_corrected"] = False
        o = obs.loc[obs["variable"] == var, ["station_id", "valid_time", "value", "qc"]].rename(columns={"value": "obs", "qc": "obs_qc"})
        df = df.merge(o, on=["station_id", "valid_time"], how="left")
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=MATCHED_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    out["obs_qc"] = out["obs_qc"].astype("Int64")
    return out[MATCHED_COLUMNS]


def orography_to_grid(ds: xr.Dataset, lat_grid: np.ndarray, lon_grid: np.ndarray) -> xr.Dataset:
    """Area-average MEPS surface geopotential onto a regular lat/lon grid (model orography proxy
    for 0.25 degree fields, which carry no orography in open data)."""
    orog = _squeeze(ds["surface_geopotential"]).isel(time=0).values / G0
    lat2 = ds["latitude"].values.ravel()
    lon2 = ds["longitude"].values.ravel()
    dlat = float(np.diff(lat_grid).mean())
    dlon = float(np.diff(lon_grid).mean())
    iy = np.floor((lat2 - (lat_grid[0] - dlat / 2)) / dlat).astype(int)
    ix = np.floor((lon2 - (lon_grid[0] - dlon / 2)) / dlon).astype(int)
    ok = (iy >= 0) & (iy < len(lat_grid)) & (ix >= 0) & (ix < len(lon_grid))
    sums = np.zeros((len(lat_grid), len(lon_grid)))
    cnt = np.zeros_like(sums)
    np.add.at(sums, (iy[ok], ix[ok]), orog.ravel()[ok])
    np.add.at(cnt, (iy[ok], ix[ok]), 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(cnt > 0, sums / cnt, np.nan)
    return xr.Dataset({"orog_m": (("lat", "lon"), mean)}, coords={"lat": lat_grid, "lon": lon_grid},
                      attrs={"source": "MEPS surface_geopotential area-averaged", "note": "proxy for 0.25 deg model orography"})
