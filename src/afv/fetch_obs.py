"""Observations from the Frost API into the OBS table, SI units matching the model output.

Frost facts used here (frost.met.no/python_example.html, /langex_python, /dataclarifications.html):
* HTTP Basic with the client id as user and an empty password
* the ``data`` array nests one ``observations`` list per element, offset and level; flatten by hand
* all times are UTC; referenceTime is the END of an aggregation window
* qualityCode 0 and 1 are good; 6 is model data and is never accepted; an over-large request fails
  with 403 (not 429): split by time and issue sequentially
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pandas as pd
import requests

from .schema import OBS_COLUMNS

FROST_OBS = "https://frost.met.no/observations/v0.jsonld"
FROST_ELEMENTS = "https://frost.met.no/elements/v0.jsonld"

# --- FROST ELEMENTS: confirm ids, time resolution and level against /elements before Tier 0 ---
# Confirmed from official examples: air_temperature, air_pressure_at_sea_level,
#   sum(precipitation_amount PT10M), sum(precipitation_amount P1D), mean(wind_speed P1D).
# Flagged (resolve with `afv elements`): wind_speed, wind_from_direction, sum(precipitation_amount PT1H).
ELEMENTS: dict[str, dict] = {
    "t2m":  {"elementId": "air_temperature",            "timeresolutions": "PT1H", "levels": "2",  "unit_in": "degC", "unit_out": "K"},
    "ws10": {"elementId": "wind_speed",                 "timeresolutions": "PT1H", "levels": "10", "unit_in": "m/s",  "unit_out": "m/s"},
    "msl":  {"elementId": "air_pressure_at_sea_level",  "timeresolutions": "PT1H", "levels": None, "unit_in": "hPa",  "unit_out": "Pa"},
    "tp1h": {"elementId": "sum(precipitation_amount PT1H)", "timeresolutions": "PT1H", "levels": None, "unit_in": "mm", "unit_out": "mm"},
}
QC_ACCEPT_DEFAULT = {0, 1}
QC_NEVER = {6, 7}
# -----------------------------------------------------------------------------------------------


def _client_id(client_id: str | None) -> str:
    cid = client_id or os.environ.get("FROST_CLIENT_ID", "")
    if not cid:
        raise RuntimeError("FROST_CLIENT_ID is not set; see .env.example")
    return cid


def convert(value: float, unit_in: str, unit_out: str) -> float:
    if unit_in == unit_out:
        return value
    if (unit_in, unit_out) == ("degC", "K"):
        return value + 273.15
    if (unit_in, unit_out) == ("hPa", "Pa"):
        return value * 100.0
    raise ValueError(f"no conversion {unit_in} -> {unit_out}")


def resolve_elements(client_id: str | None = None, ids: list[str] | None = None, timeout: int = 60) -> pd.DataFrame:
    """Look up element definitions so the flagged ids in ELEMENTS can be confirmed or corrected."""
    cid = _client_id(client_id)
    ids = ids or [v["elementId"] for v in ELEMENTS.values()]
    r = requests.get(FROST_ELEMENTS, params={"ids": ",".join(ids), "lang": "en-US"}, auth=(cid, ""), timeout=timeout)
    r.raise_for_status()
    rows = [{"id": it.get("id"), "name": it.get("name"), "unit": it.get("unit"),
             "status": it.get("status"), "cf_name": (it.get("cfConvention") or {}).get("standardName")}
            for it in r.json().get("data", [])]
    return pd.DataFrame(rows)


def flatten(payload: dict) -> pd.DataFrame:
    rows = []
    for item in payload.get("data", []):
        sid = item.get("sourceId", "")
        rt = item.get("referenceTime")
        for ob in item.get("observations", []):
            rows.append({"sourceId": sid, "referenceTime": rt, "elementId": ob.get("elementId"),
                         "value": ob.get("value"), "unit": ob.get("unit"),
                         "timeOffset": ob.get("timeOffset"), "timeResolution": ob.get("timeResolution"),
                         "level": (ob.get("level") or {}).get("value"), "qualityCode": ob.get("qualityCode")})
    return pd.DataFrame(rows)


def _windows(t0: pd.Timestamp, t1: pd.Timestamp, days: int):
    cur = t0
    while cur < t1:
        nxt = min(cur + pd.Timedelta(days=days), t1)
        yield cur, nxt
        cur = nxt


def fetch_observations(client_id: str | None, station_ids: list[str], variables: list[str],
                       t0: str | pd.Timestamp, t1: str | pd.Timestamp, cache_dir: str | Path,
                       chunk_days: int = 2, stations_per_request: int = 10, sleep_s: float = 0.2,
                       qc_accept: set[int] | None = None, timeout: int = 120) -> pd.DataFrame:
    """Sequential, cached, chunked download. Returns OBS_COLUMNS with SI units and raw qc kept.

    Rows whose qualityCode is in QC_NEVER are dropped before anything else; rows outside
    ``qc_accept`` are kept in the returned frame but flagged so the caller decides (the matched
    table keeps ``obs_qc`` so the filter is reproducible).
    """
    cid = _client_id(client_id)
    cache = Path(cache_dir) / "frost_raw"
    cache.mkdir(parents=True, exist_ok=True)
    t0 = pd.Timestamp(t0, tz="UTC") if pd.Timestamp(t0).tzinfo is None else pd.Timestamp(t0)
    t1 = pd.Timestamp(t1, tz="UTC") if pd.Timestamp(t1).tzinfo is None else pd.Timestamp(t1)
    frames = []
    for var in variables:
        spec = ELEMENTS[var]
        for i in range(0, len(station_ids), stations_per_request):
            chunk = station_ids[i:i + stations_per_request]
            for a, b in _windows(t0, t1, chunk_days):
                key = f"{var}_{i:03d}_{a:%Y%m%dT%H}_{b:%Y%m%dT%H}.json"
                f = cache / key
                if f.exists():
                    payload = json.loads(f.read_text(encoding="utf-8"))
                else:
                    params = {"sources": ",".join(chunk), "elements": spec["elementId"],
                              "referencetime": f"{a:%Y-%m-%dT%H:%M:%S}Z/{b:%Y-%m-%dT%H:%M:%S}Z",
                              "timeresolutions": spec["timeresolutions"]}
                    if spec.get("levels"):
                        params["levels"] = spec["levels"]
                    r = requests.get(FROST_OBS, params=params, auth=(cid, ""), timeout=timeout)
                    if r.status_code == 412:          # no data available for this combination
                        payload = {"data": []}
                    elif r.status_code == 403:
                        raise RuntimeError("Frost returned 403: request too large or bad client id; reduce chunk_days or stations_per_request")
                    else:
                        r.raise_for_status()
                        payload = r.json()
                    f.write_text(json.dumps(payload), encoding="utf-8")
                    time.sleep(sleep_s)
                df = flatten(payload)
                if df.empty:
                    continue
                df["variable"] = var
                frames.append(df)
    if not frames:
        return pd.DataFrame(columns=OBS_COLUMNS)
    raw = pd.concat(frames, ignore_index=True)
    raw["station_id"] = raw["sourceId"].str.split(":").str[0]
    raw["sensor"] = raw["sourceId"].str.split(":").str[1].fillna("0")
    raw = raw[raw["sensor"] == "0"]                       # official sensor only
    raw = raw.drop_duplicates(subset=["station_id", "variable", "referenceTime", "elementId", "level", "timeOffset", "timeResolution"])
    raw["qc"] = pd.to_numeric(raw["qualityCode"], errors="coerce").fillna(-1).astype(int)
    raw = raw[~raw["qc"].isin(QC_NEVER)]
    raw["valid_time"] = pd.to_datetime(raw["referenceTime"], utc=True)
    out = []
    for var, g in raw.groupby("variable"):
        spec = ELEMENTS[var]
        vals = pd.to_numeric(g["value"], errors="coerce")
        if var.startswith("tp"):
            vals = vals.where(vals >= 0, 0.0)            # Frost uses -1 for "no precipitation"
        g = g.assign(value=[convert(v, spec["unit_in"], spec["unit_out"]) for v in vals])
        out.append(g[["station_id", "valid_time", "variable", "value", "qc"]])
    obs = pd.concat(out, ignore_index=True)
    accept = QC_ACCEPT_DEFAULT if qc_accept is None else set(qc_accept)
    obs["qc_accepted"] = obs["qc"].isin(accept)
    return obs


def save_obs(obs: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    obs.to_parquet(path, index=False)
    return path
