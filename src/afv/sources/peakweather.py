"""Swiss observations from the MeteoSwiss PeakWeather dataset.

PeakWeather is SwissMetNet published as a dataset rather than as an API: 302 stations,
ten-minute values, 2017 onwards, with per-station terrain descriptors taken from a 50 m
digital elevation model and co-located ICON-CH1-EPS forecasts. That matters here for one
practical reason: the Alpine track needs no daily harvesting and no four-day retention
window, so a result exists on the first day rather than after three weeks of collecting.

Facts below were read from the published metadata on Saturday 31 October 2026, from
``huggingface.co/datasets/MeteoSwiss/PeakWeather`` at tag ``v0.2.0``, files
``parameters.parquet`` and ``stations.parquet``. They are recorded here so that a later
failure is traceable to a change in the dataset rather than to a mystery.

Station table columns, index ``nat_abbr``:
    station_name, latitude, longitude, station_height, swiss_easting, swiss_northing,
    dem, station_type (meteo_station | rain_gauge), and terrain descriptors at two
    scales: SLOPE_, ASPECT_, TPI_, STD_, WE_DERIVATIVE_, SN_DERIVATIVE_ with the
    suffixes 2000M and 10000M.

Parameters, with the units the dataset serves:
    temperature     degC    air temperature 2 m above ground, instantaneous
    wind_speed      m/s     scalar mean over ten minutes
    wind_gust       m/s     one-second gust peak, maximum over ten minutes
    wind_direction  deg     circular mean over ten minutes
    precipitation   mm      ten-minute total
    pressure        hPa     QFE at barometric altitude
    humidity        %       relative humidity
    sunshine        min     ten-minute total

One of those is a trap and is handled by refusing it: ``pressure`` is **QFE**, the
pressure at the station's own altitude, while the model field this repository carries is
``msl``, reduced to mean sea level. The two differ by roughly a hectopascal per eight
metres, so at Jungfraujoch they are hundreds of hectopascals apart. Scoring one against
the other would produce a confident, enormous and meaningless bias, so the mapping below
omits it and :func:`require_mappable` says why.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..schema import OBS_COLUMNS, STATION_COLUMNS

DATASET_TAG = "v0.2.0"
BASE_URL = f"https://huggingface.co/datasets/MeteoSwiss/PeakWeather/resolve/{DATASET_TAG}/data/"
METADATA_FILES = ("stations.parquet", "parameters.parquet")

#: afv variable -> how to get it from PeakWeather. ``None`` means deliberately not mapped.
PARAMETERS: dict[str, dict | None] = {
    "t2m": {"parameter": "temperature", "unit_in": "degC", "unit_out": "K", "agg": "last"},
    "ws10": {"parameter": "wind_speed", "unit_in": "m/s", "unit_out": "m/s", "agg": "mean"},
    "tp1h": {"parameter": "precipitation", "unit_in": "mm", "unit_out": "mm", "agg": "sum"},
    "msl": None,
}

NOT_MAPPED_REASON = {
    "msl": (
        "PeakWeather serves pressure as QFE at the station's own altitude, not reduced to "
        "mean sea level. The model field is mean sea level pressure. Reducing QFE needs the "
        "station temperature profile and introduces an error of its own, so this domain scores "
        "temperature and wind until the reduction is implemented and tested."
    ),
}

#: Terrain descriptors kept alongside the station table. The point of the Alpine domain is
#: that error depends on terrain, so these travel with the station and are not thrown away.
TERRAIN_COLUMNS = {
    "dem": "dem_m",
    "SLOPE_2000M_SIGRATIO1": "slope_2km",
    "SLOPE_10000M_SIGRATIO1": "slope_10km",
    "ASPECT_2000M_SIGRATIO1": "aspect_2km",
    "ASPECT_10000M_SIGRATIO1": "aspect_10km",
    "TPI_2000M": "tpi_2km",
    "TPI_10000M": "tpi_10km",
    "STD_2000M": "std_2km",
    "STD_10000M": "std_10km",
}


class PeakWeatherError(RuntimeError):
    pass


# Conversions ---------------------------------------------------------------------------

def convert(values, unit_in: str, unit_out: str):
    """Convert an array or scalar, refusing any pair that is not listed.

    Refusing is the point. A pass-through default is how a pressure in hectopascals
    reaches a table whose column says pascals.
    """
    if unit_in == unit_out:
        return values
    table = {
        ("degC", "K"): lambda v: v + 273.15,
        ("hPa", "Pa"): lambda v: v * 100.0,
        ("min", "s"): lambda v: v * 60.0,
    }
    try:
        return table[(unit_in, unit_out)](values)
    except KeyError:
        raise PeakWeatherError(f"no conversion {unit_in} -> {unit_out}") from None


def require_mappable(variables: list[str]) -> list[str]:
    """Return the variables this source can serve, raising on one it deliberately refuses."""
    unknown = [v for v in variables if v not in PARAMETERS]
    if unknown:
        raise PeakWeatherError(
            f"variables {unknown} are not defined for this source; known: {sorted(PARAMETERS)}"
        )
    refused = [v for v in variables if PARAMETERS[v] is None]
    if refused:
        why = "; ".join(f"{v}: {NOT_MAPPED_REASON.get(v, 'not mapped')}" for v in refused)
        raise PeakWeatherError(f"this source does not serve {refused}. {why}")
    return list(variables)


# Station table -------------------------------------------------------------------------

def classify(elev_m: float, mountain_min_elev_m: float = 1500.0) -> str:
    """Terrain class from height alone, which is crude and is meant to be.

    A finer classification belongs in the terrain descriptors that travel with the station.
    This exists so the existing stratification by ``station_class`` keeps working.
    """
    if elev_m is None or (isinstance(elev_m, float) and np.isnan(elev_m)):
        return "unknown"
    return "mountain" if float(elev_m) >= mountain_min_elev_m else "valley"


def normalise_stations(raw: pd.DataFrame, mountain_min_elev_m: float = 1500.0) -> pd.DataFrame:
    """PeakWeather's station table to this repository's station schema, plus terrain.

    Pure, so it is tested without the network. ``raw`` is ``stations.parquet`` as served:
    indexed by the station abbreviation, with the columns listed in the module docstring.
    """
    if raw.index.name != "nat_abbr" and "nat_abbr" not in raw.columns:
        raise PeakWeatherError(
            f"station table is not shaped as expected: index {raw.index.name!r}, "
            f"columns {list(raw.columns)[:6]}"
        )
    df = raw.reset_index() if raw.index.name == "nat_abbr" else raw.copy()
    missing = [c for c in ("nat_abbr", "station_name", "latitude", "longitude", "station_height")
               if c not in df.columns]
    if missing:
        raise PeakWeatherError(f"station table is missing columns {missing}")

    out = pd.DataFrame({
        "station_id": df["nat_abbr"].astype(str),
        "name": df["station_name"].astype(str),
        "lat": df["latitude"].astype(float),
        "lon": df["longitude"].astype(float),
        "elev_m": df["station_height"].astype(float),
        "county": "CH",
        "station_class": [classify(e, mountain_min_elev_m) for e in df["station_height"]],
    })
    if "station_type" in df.columns:
        out["station_type"] = df["station_type"].astype(str)
    for src, dst in TERRAIN_COLUMNS.items():
        if src in df.columns:
            out[dst] = pd.to_numeric(df[src], errors="coerce").astype(float)
    # How far the 50 m model surface sits from the station's own height. Small by
    # construction here; the large difference is against the forecast model's orography,
    # which match.py computes. This column is the sanity check on the station metadata.
    if "dem_m" in out.columns:
        out["dem_minus_station_m"] = out["dem_m"] - out["elev_m"]
    # Schema columns first and in their declared order, terrain after, so a reader of the
    # frozen CSV sees the contract before the extras and `load_frozen` is unaffected.
    extras = [c for c in out.columns if c not in STATION_COLUMNS]
    return out[STATION_COLUMNS + extras]


def fetch_station_table(cache_dir: str | Path, timeout: int = 120) -> pd.DataFrame:
    """Download only the two small metadata files, never the observation years."""
    import requests

    cache = Path(cache_dir) / "peakweather"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / "stations.parquet"
    if not target.exists():
        r = requests.get(BASE_URL + "stations.parquet", timeout=timeout)
        r.raise_for_status()
        target.write_bytes(r.content)
    return pd.read_parquet(target)


def stations(cache_dir: str | Path, mountain_min_elev_m: float = 1500.0,
             bbox: dict | None = None, max_n: int | None = None,
             station_types: list[str] | None = None) -> pd.DataFrame:
    """The station table for this domain, in this repository's schema."""
    df = normalise_stations(fetch_station_table(cache_dir), mountain_min_elev_m)
    if station_types and "station_type" in df.columns:
        df = df[df["station_type"].isin(station_types)]
    if bbox:
        df = df[(df["lat"].between(bbox["lat_min"], bbox["lat_max"]))
                & (df["lon"].between(bbox["lon_min"], bbox["lon_max"]))]
    df = df.sort_values("station_id").reset_index(drop=True)
    if max_n:
        # Keep the terrain spread rather than the alphabet: the tallest, the lowest, then
        # a regular walk through what is left, so a small subset still spans the valleys
        # and the peaks that this repository exists to tell apart.
        df = _spread_by_elevation(df, max_n)
    return df.reset_index(drop=True)


def _spread_by_elevation(df: pd.DataFrame, n: int) -> pd.DataFrame:
    if n >= len(df):
        return df
    ordered = df.sort_values("elev_m").reset_index(drop=True)
    idx = np.unique(np.linspace(0, len(ordered) - 1, num=n).round().astype(int))
    return ordered.iloc[idx].sort_values("station_id")


# Observations ---------------------------------------------------------------------------

def to_long_observations(wide: pd.DataFrame, variables: list[str]) -> pd.DataFrame:
    """PeakWeather observations, in either shape it serves, to ``OBS_COLUMNS``.

    Accepts a frame indexed by time with ``(station, parameter)`` columns, or a long frame
    carrying ``station_id``/``datetime``/``parameter``/``value``. Anything else raises and
    names what it got, because silently guessing the layout is how a station identifier
    ends up in a value column.

    ``qc`` is set to 0, meaning accepted. PeakWeather is already quality controlled by the
    provider and does not publish a per-value flag, so the column records that rather than
    inventing a code. Spatial checks are a separate step and belong in ``afv.qc``.
    """
    require_mappable(variables)
    wanted = {PARAMETERS[v]["parameter"]: v for v in variables}

    if isinstance(wide.columns, pd.MultiIndex):
        frame = wide.stack(level=list(range(wide.columns.nlevels)), future_stack=True).reset_index()
        names = [c for c in frame.columns if c not in (0, "0")]
        if len(names) < 3:
            raise PeakWeatherError(f"could not unstack observations; got columns {list(frame.columns)}")
        frame.columns = ["valid_time", "station_id", "parameter", "value"][:len(frame.columns)]
    elif {"station_id", "parameter", "value"}.issubset(set(wide.columns)):
        frame = wide.rename(columns={"datetime": "valid_time", "time": "valid_time"}).copy()
    else:
        raise PeakWeatherError(
            "unrecognised observation layout; expected MultiIndex columns (station, parameter) "
            f"or a long frame with station_id/parameter/value, got columns {list(wide.columns)[:8]}"
        )

    frame = frame[frame["parameter"].isin(wanted)].copy()
    if frame.empty:
        return pd.DataFrame(columns=OBS_COLUMNS)
    frame["variable"] = frame["parameter"].map(wanted)
    for var, spec in ((v, PARAMETERS[v]) for v in variables):
        sel = frame["variable"] == var
        if sel.any():
            frame.loc[sel, "value"] = convert(
                pd.to_numeric(frame.loc[sel, "value"], errors="coerce"),
                spec["unit_in"], spec["unit_out"],
            )
    frame["valid_time"] = pd.to_datetime(frame["valid_time"], utc=True)
    frame["qc"] = 0
    out = frame[["station_id", "valid_time", "variable", "value", "qc"]].copy()
    out["station_id"] = out["station_id"].astype(str)
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    return out.dropna(subset=["value"]).reset_index(drop=True)


def resample_hourly(obs: pd.DataFrame) -> pd.DataFrame:
    """Ten-minute values to the exact UTC hours the model is matched on.

    Each variable is aggregated the way its own definition requires: an instantaneous
    temperature is taken at the hour, a mean wind is averaged over it, a precipitation
    total is summed. Using one rule for all three would be wrong for two of them.
    """
    if obs.empty:
        return obs
    out = []
    for var, grp in obs.groupby("variable"):
        agg = PARAMETERS[var]["agg"] if PARAMETERS.get(var) else "mean"
        g = grp.set_index("valid_time").sort_index()
        how = {"last": "last", "mean": "mean", "sum": "sum"}[agg]
        res = (g.groupby("station_id")["value"].resample("1h").agg(how).reset_index())
        res["variable"] = var
        res["qc"] = 0
        out.append(res)
    joined = pd.concat(out, ignore_index=True)
    return joined[OBS_COLUMNS].dropna(subset=["value"]).reset_index(drop=True)


def observations(cache_dir: str | Path, station_ids: list[str], variables: list[str],
                 t0, t1, years: list[int] | None = None) -> pd.DataFrame:
    """Observations for the window, hourly, in this repository's schema. Needs the network.

    The first call downloads the observation years into ``cache_dir``, which is several
    hundred megabytes per year, so ``years`` is derived from the window rather than taken
    as the whole archive.
    """
    require_mappable(variables)
    try:
        from peakweather.dataset import PeakWeatherDataset
    except ImportError as e:
        raise PeakWeatherError(
            "the peakweather package is not installed; install the 'alps' extra"
        ) from e

    t0 = pd.Timestamp(t0, tz="UTC") if pd.Timestamp(t0).tzinfo is None else pd.Timestamp(t0)
    t1 = pd.Timestamp(t1, tz="UTC") if pd.Timestamp(t1).tzinfo is None else pd.Timestamp(t1)
    years = years or sorted({t0.year, t1.year})
    params = [PARAMETERS[v]["parameter"] for v in variables]

    ds = PeakWeatherDataset(root=str(Path(cache_dir) / "peakweather"), years=years,
                            parameters=params, extended_topo_vars="none",
                            extended_nwp_pars="none")
    raw = ds.get_observations(stations=list(station_ids) or None, parameters=params,
                              first_date=t0.tz_localize(None), last_date=t1.tz_localize(None))
    return resample_hourly(to_long_observations(raw, variables))
