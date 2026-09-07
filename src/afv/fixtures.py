"""Synthetic fixtures so the whole pipeline runs offline, in CI and in tests.

The synthetic world has a known truth field. Two fake models sample it: ``aifs-single`` is a
smoothed, slightly biased version (mimicking the literature's blurring), ``ifs`` is sharper with
more random error. Observations are truth at station points plus noise, with one QC-flagged hour
and one gap per station.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .schema import FIELD_VARS, OBS_COLUMNS, STATION_COLUMNS

GRID_LAT = np.arange(60.0, 61.01, 0.25)   # 5 points
GRID_LON = np.arange(5.0, 6.01, 0.25)     # 5 points


def _truth(lat: np.ndarray, lon: np.ndarray, t_hours: float) -> dict[str, np.ndarray]:
    """Smooth analytic fields: linear in lat/lon plus a diurnal cycle. Shapes broadcast."""
    lat = np.asarray(lat)[..., :, None] if lat.ndim == 1 else lat
    lon = np.asarray(lon)[..., None, :] if lon.ndim == 1 else lon
    diurnal = 3.0 * np.sin(2 * np.pi * (t_hours - 6.0) / 24.0)
    t2m = 283.15 - 4.0 * (lat - 60.0) + 1.0 * (lon - 5.0) + diurnal
    ws10 = 4.0 + 2.0 * (lon - 5.0) + 0.5 * np.cos(2 * np.pi * t_hours / 24.0)
    msl = 101325.0 - 200.0 * (lat - 60.0)
    return {"t2m": t2m, "ws10": ws10, "msl": msl}


def make_fixtures(out_dir: str | Path, seed: int = 0) -> dict[str, Path]:
    out = Path(out_dir)
    (out / "fields").mkdir(parents=True, exist_ok=True)
    (out / "obs").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    # Stations: one on a grid node at 10 m, one off-node at 610 m, one coastal, one inland.
    stations = pd.DataFrame([
        ["SNF001", "Node Fixture", 60.50, 5.50, 10.0, "Fixture", "coastal"],
        ["SNF002", "Offnode Fixture", 60.37, 5.63, 610.0, "Fixture", "mountain"],
        ["SNF003", "Inland Fixture", 60.80, 5.20, 120.0, "Fixture", "inland"],
        ["SNF004", "Edge Fixture", 60.125, 5.875, 40.0, "Fixture", "coastal"],
    ], columns=STATION_COLUMNS)
    stations_csv = out / "stations_fixture.csv"
    stations.to_csv(stations_csv, index=False)

    # Orography: flat 100 m model surface so the lapse correction is testable.
    orog = xr.Dataset({"orog_m": (("lat", "lon"), np.full((len(GRID_LAT), len(GRID_LON)), 100.0))},
                      coords={"lat": GRID_LAT, "lon": GRID_LON})
    orog_nc = out / "orog_fixture.nc"
    orog.to_netcdf(orog_nc)

    leads = np.arange(6, 49, 6)
    inits = pd.date_range("2026-09-01T00:00Z", "2026-09-04T12:00Z", freq="12h")
    for init in inits:
        for model in ("aifs-single", "ifs"):
            data = {v: np.empty((len(leads), len(GRID_LAT), len(GRID_LON))) for v in ("t2m", "ws10", "msl")}
            for i, L in enumerate(leads):
                t_hours = (init + pd.Timedelta(hours=int(L))).hour + (init + pd.Timedelta(hours=int(L))).minute / 60
                tr = _truth(GRID_LAT, GRID_LON, t_hours)
                for v in data:
                    if model == "aifs-single":
                        # smoother (damped gradients) and a small warm bias growing with lead
                        smooth = 0.6 * tr[v] + 0.4 * tr[v].mean()
                        noise = rng.normal(0, 0.3 if v != "msl" else 30.0, tr[v].shape)
                        bias_ = (0.02 * L if v == "t2m" else 0.0)
                        data[v][i] = smooth + noise + bias_
                    else:
                        noise = rng.normal(0, 0.8 if v != "msl" else 60.0, tr[v].shape) * (1 + L / 48)
                        data[v][i] = tr[v] + noise
            data["tp"] = np.cumsum(rng.gamma(0.4, 0.8, (len(leads), len(GRID_LAT), len(GRID_LON))), axis=0)
            ds = xr.Dataset({v: (("lead_h", "lat", "lon"), data[v], {"units": FIELD_VARS[v]}) for v in data},
                            coords={"lead_h": leads, "lat": GRID_LAT, "lon": GRID_LON},
                            attrs={"model": model, "init_time": init.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "source_uri": "synthetic", "grid_spacing_deg": 0.25})
            ds.to_netcdf(out / "fields" / f"{model}_{init:%Y%m%dT%HZ}.nc")

    # Observations: hourly truth at stations plus noise; QC 0/1 mostly, one code 7 and one gap.
    times = pd.date_range("2026-08-31T00:00Z", "2026-09-07T00:00Z", freq="1h")
    rows = []
    for _, st in stations.iterrows():
        bad_hour = rng.integers(10, len(times) - 10)
        gap_hour = rng.integers(10, len(times) - 10)
        for k, t in enumerate(times):
            if k == gap_hour:
                continue
            tr = _truth(np.array([st.lat]), np.array([st.lon]), t.hour)
            # station is at its own elevation: true air temperature colder by lapse vs the 100 m surface
            t_obs = float(tr["t2m"][0, 0]) - 0.0065 * (st.elev_m - 100.0) + rng.normal(0, 0.3)
            qc = 7 if k == bad_hour else int(rng.choice([0, 0, 0, 1]))
            rows.append([st.station_id, t, "t2m", t_obs, qc])
            rows.append([st.station_id, t, "ws10", max(0.0, float(tr["ws10"][0, 0]) + rng.normal(0, 0.4)), qc])
            rows.append([st.station_id, t, "msl", float(tr["msl"][0, 0]) + rng.normal(0, 20.0), qc])
    obs = pd.DataFrame(rows, columns=OBS_COLUMNS)
    obs["valid_time"] = pd.to_datetime(obs["valid_time"], utc=True)
    obs_parquet = out / "obs" / "obs.parquet"
    obs.to_parquet(obs_parquet, index=False)

    (out / "README.md").write_text(
        "Synthetic fixtures generated by `afv fixtures`. Not real data. Regenerate at will.\n", encoding="utf-8")
    return {"stations": stations_csv, "orography": orog_nc, "obs": obs_parquet, "fields": out / "fields"}
