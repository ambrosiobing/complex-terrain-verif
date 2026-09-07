"""ECMWF Open Data: AIFS-single and IFS from the same initial conditions, cached and standardised.

Facts (github.com/ecmwf/ecmwf-opendata; data.ecmwf.int listing, 7 Sep 2026):
* ``model="aifs-single"`` (data-driven) and ``model="ifs"`` (physics), 0.25 degree only
* files appear 7 to 9 h after base time and are kept for about four days, so cache daily
* AIFS single oper has 6 h steps 0 to 360 h; ``tp`` is accumulated from the forecast start (m)
GRIB decoding needs cfgrib plus eccodes, or earthkit-data. Both are optional extras; without them
this module can still download, and the standardised NetCDF can be produced on Kaggle.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .schema import FIELD_VARS

PARAMS_DEFAULT = ["2t", "10u", "10v", "msl", "tp"]


def _client(source: str = "ecmwf"):
    try:
        from ecmwf.opendata import Client
    except ImportError as e:
        raise RuntimeError("pip install ecmwf-opendata") from e
    return Client(source=source)


def fetch_run(model: str, init_time: datetime, steps: list[int], cache_dir: str | Path,
              params: list[str] | None = None, source: str = "ecmwf") -> Path:
    """Download one run (all requested steps and params) to a cached GRIB2 file. Returns its path."""
    params = params or PARAMS_DEFAULT
    cache = Path(cache_dir) / "grib"
    cache.mkdir(parents=True, exist_ok=True)
    stem = f"{model}_{init_time:%Y%m%dT%HZ}_{min(steps)}-{max(steps)}h"
    target = cache / f"{stem}.grib2"
    if target.exists() and target.stat().st_size > 0:
        return target
    client = _client(source)
    request = dict(model=model, type="fc", date=init_time.strftime("%Y%m%d"), time=init_time.hour,
                   step=steps, param=params, target=str(target))
    if model == "ifs":
        request["stream"] = "oper" if init_time.hour in (0, 12) else "scda"
    client.retrieve(**request)
    _write_manifest(target, request)
    return target


def _write_manifest(path: Path, request: dict) -> None:
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = {"file": path.name, "sha256": h, "bytes": path.stat().st_size, "request": request,
            "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path.with_suffix(".manifest.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")


def latest_available(model: str = "aifs-single", source: str = "ecmwf") -> datetime:
    client = _client(source)
    return client.latest(model=model, type="fc")


def open_grib(path: str | Path, bbox: dict | None = None) -> xr.Dataset:
    """Open a GRIB2 file with cfgrib (preferred) or earthkit-data, optionally cut to a bbox."""
    path = Path(path)
    ds = None
    try:
        import cfgrib  # noqa: F401
        parts = []
        for key in ({"typeOfLevel": "heightAboveGround", "level": 2}, {"typeOfLevel": "heightAboveGround", "level": 10},
                    {"typeOfLevel": "meanSea"}, {"typeOfLevel": "surface"}):
            try:
                parts.append(xr.open_dataset(path, engine="cfgrib", backend_kwargs={"filter_by_keys": key}))
            except Exception:
                continue
        ds = xr.merge(parts, compat="override") if parts else None
    except ImportError:
        pass
    if ds is None:
        try:
            import earthkit.data as ekd
            ds = ekd.from_source("file", str(path)).to_xarray()
        except ImportError as e:
            raise RuntimeError("no GRIB decoder: pip install cfgrib eccodes, or earthkit-data, or run fetch on Kaggle") from e
    if "latitude" in ds.dims:
        ds = ds.rename({"latitude": "lat", "longitude": "lon"})
    if bbox is not None:
        lon = ds["lon"]
        if float(lon.max()) > 180 and bbox["lon_min"] < 0:
            ds = ds.assign_coords(lon=(((lon + 180) % 360) - 180)).sortby("lon")
        ds = ds.sel(lat=slice(bbox["lat_max"], bbox["lat_min"]) if ds["lat"][0] > ds["lat"][-1] else slice(bbox["lat_min"], bbox["lat_max"]),
                    lon=slice(bbox["lon_min"], bbox["lon_max"]))
    return ds


def standardise(ds: xr.Dataset, model: str, init_time: datetime, source_uri: str = "") -> xr.Dataset:
    """Map GRIB short names to the field-file convention: dims (lead_h, lat, lon), SI units, tp in mm."""
    name_map = {"t2m": "t2m", "2t": "t2m", "u10": "u10", "10u": "u10", "v10": "v10", "10v": "v10",
                "msl": "msl", "tp": "tp", "prmsl": "msl"}
    out = {}
    for k, v in name_map.items():
        if k in ds:
            out[v] = ds[k]
    if "step" in ds.dims or "step" in ds.coords:
        lead = (pd.to_timedelta(ds["step"].values) / pd.Timedelta(hours=1)).astype(int)
    else:
        raise ValueError("GRIB dataset has no step coordinate")
    res = {}
    if "t2m" in out:
        res["t2m"] = out["t2m"]
    if "u10" in out and "v10" in out:
        res["ws10"] = np.hypot(out["u10"], out["v10"])
    if "msl" in out:
        res["msl"] = out["msl"]
    if "tp" in out:
        tp = out["tp"]
        res["tp"] = tp * 1000.0 if str(tp.attrs.get("units", "m")).startswith("m") else tp
    std = xr.Dataset({k: v.rename(k) for k, v in res.items()})
    std = std.rename({"step": "lead_h"}) if "step" in std.dims else std
    std = std.assign_coords(lead_h=("lead_h", np.asarray(lead)))
    for v in std.data_vars:
        std[v].attrs["units"] = FIELD_VARS[v]
    std = std.transpose("lead_h", "lat", "lon").sortby("lat").sortby("lon")
    std.attrs = {"model": model, "init_time": init_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "source_uri": source_uri, "grid_spacing_deg": 0.25}
    return std


def fetch_and_standardise(model: str, init_time: datetime, steps: list[int], cache_dir: str | Path,
                          fields_dir: str | Path, bbox: dict | None = None) -> Path:
    grib = fetch_run(model, init_time, steps, cache_dir)
    ds = open_grib(grib, bbox)
    std = standardise(ds, model, init_time, source_uri=f"ecmwf-opendata:{grib.name}")
    fields = Path(fields_dir)
    fields.mkdir(parents=True, exist_ok=True)
    out = fields / f"{model}_{init_time:%Y%m%dT%HZ}.nc"
    std.to_netcdf(out)
    return out


def runs_in_window(window_start: str, window_end: str, init_hours: list[int]) -> list[datetime]:
    days = pd.date_range(window_start, window_end, freq="1D")
    return [datetime(d.year, d.month, d.day, h, tzinfo=timezone.utc) for d in days for h in init_hours]
