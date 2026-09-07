"""Optional point baseline: MET Norway Locationforecast 2.0 (api.met.no).

Terms of service (api.met.no/doc/TermsOfService): a descriptive User-Agent with a contact is
mandatory, coordinates at most 4 decimals, fewer than 20 requests per second per application,
honour Expires and If-Modified-Since. Pass ``altitude`` so the service applies its own height
correction. Its "initialisation" is the fetch time; treat it as a post-processed product, not a
raw model, when you compare.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import requests

URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"


def fetch_locationforecast(lat: float, lon: float, altitude: int | None = None,
                           user_agent: str | None = None, timeout: int = 30) -> pd.DataFrame:
    ua = user_agent or os.environ.get("AFV_USER_AGENT", "")
    if not ua or "/" not in ua:
        raise RuntimeError("set AFV_USER_AGENT to 'appname/version contact' per api.met.no terms")
    params = {"lat": f"{lat:.4f}", "lon": f"{lon:.4f}"}
    if altitude is not None:
        params["altitude"] = int(altitude)
    r = requests.get(URL, params=params, headers={"User-Agent": ua}, timeout=timeout)
    r.raise_for_status()
    js = r.json()
    rows = []
    for ts in js["properties"]["timeseries"]:
        det = ts["data"]["instant"]["details"]
        rows.append({"valid_time": pd.Timestamp(ts["time"]),
                     "t2m": det.get("air_temperature", float("nan")) + 273.15,
                     "ws10": det.get("wind_speed", float("nan")),
                     "msl": det.get("air_pressure_at_sea_level", float("nan")) * 100.0})
    df = pd.DataFrame(rows)
    df["fetched_utc"] = datetime.now(timezone.utc)
    return df
