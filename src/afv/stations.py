"""Station selection from the Frost API, Bergen northwards, frozen to CSV.

Frost facts used here (frost.met.no/concepts2.html, /ex_userquest, /dataclarifications.html):
* ``/sources/v0.jsonld?types=SensorSystem&county=...`` or ``geometry=POLYGON((lon lat, ...))``
* responses carry ``geometry.coordinates`` [lon, lat] and ``masl`` (metres above sea level)
* siting quality lives on ``/observations/availableTimeSeries/`` as exposureCategory (1 best, 5 bad)
  and performanceCategory (A best, E worst)
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import requests

from .schema import STATION_COLUMNS

FROST = "https://frost.met.no"


def _client_id(client_id: str | None) -> str:
    cid = client_id or os.environ.get("FROST_CLIENT_ID", "")
    if not cid:
        raise RuntimeError("FROST_CLIENT_ID is not set; see .env.example")
    return cid


def polygon_wkt(bbox: dict) -> str:
    x0, x1, y0, y1 = bbox["lon_min"], bbox["lon_max"], bbox["lat_min"], bbox["lat_max"]
    return f"POLYGON(({x0} {y0},{x0} {y1},{x1} {y1},{x1} {y0},{x0} {y0}))"


def fetch_sources(client_id: str | None = None, counties: list[str] | None = None,
                  bbox: dict | None = None, timeout: int = 60) -> pd.DataFrame:
    cid = _client_id(client_id)
    params: dict = {"types": "SensorSystem"}
    frames = []
    if counties:
        for c in counties:
            r = requests.get(f"{FROST}/sources/v0.jsonld", params={**params, "county": c}, auth=(cid, ""), timeout=timeout)
            r.raise_for_status()
            frames.append(_sources_to_df(r.json().get("data", [])))
    else:
        if bbox:
            params["geometry"] = polygon_wkt(bbox)
        r = requests.get(f"{FROST}/sources/v0.jsonld", params=params, auth=(cid, ""), timeout=timeout)
        r.raise_for_status()
        frames.append(_sources_to_df(r.json().get("data", [])))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=STATION_COLUMNS)
    return df.drop_duplicates("station_id").reset_index(drop=True)


def _sources_to_df(items: list[dict]) -> pd.DataFrame:
    rows = []
    for it in items:
        geom = it.get("geometry") or {}
        coords = geom.get("coordinates") or [None, None]
        rows.append({
            "station_id": it.get("id"),
            "name": it.get("name"),
            "lat": coords[1], "lon": coords[0],
            "elev_m": it.get("masl"),
            "county": it.get("county", ""),
            "station_class": "unknown",
            "valid_from": it.get("validFrom"), "valid_to": it.get("validTo"),
            "municipality": it.get("municipality", ""),
        })
    return pd.DataFrame(rows)


def available_series(client_id: str | None, station_ids: list[str], elements: list[str],
                     timeout: int = 60) -> pd.DataFrame:
    """Which stations report which elements, with siting and sensor quality categories."""
    cid = _client_id(client_id)
    rows = []
    for i in range(0, len(station_ids), 50):
        chunk = station_ids[i:i + 50]
        r = requests.get(f"{FROST}/observations/availableTimeSeries/v0.jsonld",
                         params={"sources": ",".join(chunk), "elements": ",".join(elements)},
                         auth=(cid, ""), timeout=timeout)
        if r.status_code == 412:      # no data for this combination
            continue
        r.raise_for_status()
        for it in r.json().get("data", []):
            rows.append({"station_id": it.get("sourceId", "").split(":")[0], "sensor": it.get("sourceId"),
                         "element": it.get("elementId"), "time_resolution": it.get("timeResolution"),
                         "time_offset": it.get("timeOffset"), "level": (it.get("level") or {}).get("value"),
                         "exposure": it.get("exposureCategory"), "performance": it.get("performanceCategory"),
                         "valid_from": it.get("validFrom"), "valid_to": it.get("validTo")})
    return pd.DataFrame(rows)


def classify(elev_m: float, mountain_min_elev_m: float = 600.0) -> str:
    """Coarse class from elevation only; coastal versus inland needs a coastline distance, which is
    not in Frost. Edit station_class by hand in the frozen CSV where you know better."""
    if pd.isna(elev_m):
        return "unknown"
    return "mountain" if elev_m >= mountain_min_elev_m else "lowland"


def select_stations(sources: pd.DataFrame, min_lat: float, bbox: dict | None, max_n: int,
                    required: pd.DataFrame | None = None, exposure_max: int | None = None,
                    performance_max: str | None = None, mountain_min_elev_m: float = 600.0,
                    active_only: bool = True) -> pd.DataFrame:
    df = sources.copy()
    df = df[df["lat"].notna() & df["lon"].notna()]
    df = df[df["lat"] >= min_lat]
    if bbox:
        df = df[(df["lon"] >= bbox["lon_min"]) & (df["lon"] <= bbox["lon_max"]) &
                (df["lat"] >= bbox["lat_min"]) & (df["lat"] <= bbox["lat_max"])]
    if active_only and "valid_to" in df:
        df = df[df["valid_to"].isna()]
    if required is not None and not required.empty:
        ok = required.copy()
        if exposure_max is not None and "exposure" in ok:
            ok = ok[ok["exposure"].fillna(9).astype(float) <= exposure_max]
        if performance_max is not None and "performance" in ok:
            ok = ok[ok["performance"].fillna("Z") <= performance_max]
        good_ids = ok.groupby("station_id")["element"].nunique()
        need = required["element"].nunique()
        good_ids = good_ids[good_ids >= need].index
        df = df[df["station_id"].isin(good_ids)]
    df["station_class"] = df["elev_m"].map(lambda e: classify(e, mountain_min_elev_m))
    df = df.sort_values(["county", "lat"]).head(max_n)
    return df[STATION_COLUMNS].reset_index(drop=True)


def freeze(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df[STATION_COLUMNS].to_csv(path, index=False)
    return path


def freeze_all(df: pd.DataFrame, path: str | Path) -> Path:
    """Freeze the schema columns first, then whatever else the source attached.

    The Alpine source attaches terrain descriptors to each station, and the claim that
    forecast error depends on terrain cannot be tested if the writer drops them. The
    schema columns keep their order so ``load_frozen`` is unaffected.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    extras = [c for c in df.columns if c not in STATION_COLUMNS]
    df[STATION_COLUMNS + extras].to_csv(path, index=False)
    return path


def load_frozen(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in STATION_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing station columns {missing}")
    return df
