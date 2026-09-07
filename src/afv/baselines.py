"""Fair baselines expressed as pseudo-models so they flow through the same scoring code.

* persistence   forecast = the observation valid at initialisation time
* climatology   forecast = mean observation for that station and hour of day over the obs window
                (an obs-window climatology, not a 30-year normal; say so in every caption)
* lagged ensemble  members = deterministic forecasts from successive initialisations that are
                valid at the same time (Brenowitz et al. 2025), for CRPS and spread-skill
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import MATCHED_COLUMNS, ensure_utc

KEY = ["station_id", "variable", "init_time", "lead_h", "valid_time", "interp",
       "station_elev_m", "model_elev_m", "lat", "lon", "station_class", "county", "obs", "obs_qc"]


def _template(matched: pd.DataFrame, reference_model: str | None = None) -> pd.DataFrame:
    ref = reference_model or matched["model"].iloc[0]
    t = matched.loc[matched["model"] == ref, KEY].drop_duplicates(
        subset=["station_id", "variable", "init_time", "lead_h", "interp"]).copy()
    return t


def persistence(matched: pd.DataFrame, obs: pd.DataFrame, reference_model: str | None = None) -> pd.DataFrame:
    t = _template(matched, reference_model)
    o = obs[["station_id", "variable", "valid_time", "value"]].copy()
    o["valid_time"] = ensure_utc(o["valid_time"])
    o = o.rename(columns={"valid_time": "init_time", "value": "fcst"})
    t = t.merge(o, on=["station_id", "variable", "init_time"], how="left")
    t["model"] = "persistence"
    t["fcst_raw"] = t["fcst"]
    t["lapse_corrected"] = False
    return t[MATCHED_COLUMNS]


def climatology(matched: pd.DataFrame, obs: pd.DataFrame, reference_model: str | None = None) -> pd.DataFrame:
    t = _template(matched, reference_model)
    o = obs[["station_id", "variable", "valid_time", "value"]].copy()
    o["hour"] = ensure_utc(o["valid_time"]).dt.hour
    clim = o.groupby(["station_id", "variable", "hour"], as_index=False)["value"].mean().rename(columns={"value": "fcst"})
    t["hour"] = ensure_utc(t["valid_time"]).dt.hour
    t = t.merge(clim, on=["station_id", "variable", "hour"], how="left").drop(columns="hour")
    t["model"] = "climatology"
    t["fcst_raw"] = t["fcst"]
    t["lapse_corrected"] = False
    return t[MATCHED_COLUMNS]


def lagged_ensemble(matched: pd.DataFrame, model: str, n_members: int = 3, step_h: int = 6,
                    interp: str | None = None) -> pd.DataFrame:
    """Long table (…, member, fcst) of forecasts from ``n_members`` successive inits valid at the same time.

    Member 0 is the newest initialisation. Only valid times with all members present are kept.
    """
    m = matched[matched["model"] == model]
    if interp is not None:
        m = m[m["interp"] == interp]
    m = m.dropna(subset=["fcst"])
    rows = []
    for (st, var, vt, ip), g in m.groupby(["station_id", "variable", "valid_time", "interp"]):
        g = g.sort_values("lead_h")
        base = int(g["lead_h"].iloc[0])
        wanted = [base + k * step_h for k in range(n_members)]
        sel = g[g["lead_h"].isin(wanted)]
        if len(sel) < n_members:
            continue
        for k, (_, r) in enumerate(sel.iterrows()):
            rows.append({"station_id": st, "variable": var, "valid_time": vt, "interp": ip,
                         "member": k, "lead_h": int(r["lead_h"]), "fcst": r["fcst"], "obs": r["obs"]})
    return pd.DataFrame(rows)


def members_matrix(lagged: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Pivot a lagged-ensemble table into (n_samples, n_members) plus the obs vector."""
    p = lagged.pivot_table(index=["station_id", "variable", "valid_time", "interp"],
                           columns="member", values="fcst")
    o = lagged.groupby(["station_id", "variable", "valid_time", "interp"])["obs"].first().reindex(p.index)
    keep = p.notna().all(axis=1) & o.notna()
    return p[keep].values, o[keep].values
