"""Nordic observations from MET Norway's Frost API.

This is an adapter, not an implementation. The working Frost client is
:mod:`afv.fetch_obs` and the station selection is :mod:`afv.stations`; both predate the
source interface and are unchanged. This module exists so that the Nordic and Alpine
domains are reached the same way, and so that the thing the pipeline asks for is the same
question in both cases: give me a station table and a table of observations.

Frost needs a credential. ``FROST_CLIENT_ID`` in the environment or in ``.env``; see
``.env.example``. The Alpine source needs none, which is why that domain is the one that
runs in a first evening.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

VARIABLES = ("t2m", "ws10", "msl", "tp1h")


def require_mappable(variables: list[str]) -> list[str]:
    """Frost serves all four, including mean sea level pressure, which the Swiss set omits."""
    from ..fetch_obs import ELEMENTS
    unknown = [v for v in variables if v not in ELEMENTS]
    if unknown:
        raise RuntimeError(f"variables {unknown} are not defined for Frost; known: {sorted(ELEMENTS)}")
    return list(variables)


def stations(cache_dir: str | Path, mountain_min_elev_m: float = 600.0,
             bbox: dict | None = None, max_n: int | None = None,
             counties: list[str] | None = None, **kwargs) -> pd.DataFrame:
    """Station table in this repository's schema. Needs the network and a credential."""
    from ..stations import fetch_sources, select_stations
    src = fetch_sources(None, counties=counties, bbox=bbox)
    return select_stations(src, float(bbox["lat_min"]) if bbox else 0.0, bbox,
                           int(max_n or len(src)),
                           mountain_min_elev_m=mountain_min_elev_m)


def observations(cache_dir: str | Path, station_ids: list[str], variables: list[str],
                 t0, t1, qc_accept: set[int] | None = None, **kwargs) -> pd.DataFrame:
    """Observations in this repository's schema. Needs the network and a credential."""
    from ..fetch_obs import fetch_observations
    require_mappable(variables)
    return fetch_observations(None, list(station_ids), list(variables), t0, t1, cache_dir,
                              qc_accept=qc_accept)
