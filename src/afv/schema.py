"""Data contracts shared by every module.

Three tables flow through the pipeline:

* stations   one row per station (Frost SensorSystem), frozen to CSV
* obs        long table of observations, SI units matching the model output
* matched    one row per (station, model, variable, init, lead, interp), the ML-ready dataset
* scores     one row per (model, variable, lead_h, stratum, metric) with bootstrap CI

Field files (NetCDF) hold model output on a regular lat/lon grid with dims (lead_h, lat, lon)
and attributes ``model`` and ``init_time`` (ISO 8601, UTC).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# Column names, single source of truth -------------------------------------------------

STATION_COLUMNS = ["station_id", "name", "lat", "lon", "elev_m", "county", "station_class"]

OBS_COLUMNS = ["station_id", "valid_time", "variable", "value", "qc"]

MATCHED_COLUMNS = [
    "station_id", "model", "variable", "init_time", "valid_time", "lead_h",
    "fcst_raw", "fcst", "obs", "obs_qc", "interp", "lapse_corrected",
    "model_elev_m", "station_elev_m", "lat", "lon", "station_class", "county",
]

SCORE_COLUMNS = ["model", "variable", "lead_h", "stratum", "stratum_value", "metric",
                 "value", "ci_lo", "ci_hi", "n"]

# Field file conventions ---------------------------------------------------------------

FIELD_DIMS = ("lead_h", "lat", "lon")
FIELD_VARS = {          # name in field file -> unit; model params are converted on fetch
    "t2m": "K",
    "ws10": "m s-1",
    "msl": "Pa",
    "tp": "mm",           # accumulated from forecast start
}


@dataclass(frozen=True)
class Station:
    station_id: str
    name: str
    lat: float
    lon: float
    elev_m: float
    county: str = ""
    station_class: str = "unknown"   # coastal | inland | mountain | unknown

    def to_row(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FieldMeta:
    """What a field file is: which model, which initialisation, where it came from."""
    model: str
    init_time: datetime
    source_uri: str = ""
    grid_spacing_deg: float = 0.25

    @property
    def stem(self) -> str:
        return f"{self.model}_{self.init_time:%Y%m%dT%HZ}"


@dataclass
class ScoreTable:
    """Scores plus the provenance needed to reproduce them."""
    rows: pd.DataFrame
    dataset_hash: str
    config_hash: str
    created_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    afv_version: str = ""

    def to_parquet(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df = self.rows.copy()
        df.attrs = {}
        df.to_parquet(path, index=False)
        meta = {"dataset_hash": self.dataset_hash, "config_hash": self.config_hash,
                "created_utc": self.created_utc, "afv_version": self.afv_version}
        path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return path

    def to_jsonl(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            header = {"_meta": {"dataset_hash": self.dataset_hash, "config_hash": self.config_hash,
                                "created_utc": self.created_utc, "afv_version": self.afv_version}}
            fh.write(json.dumps(header) + "\n")
            for rec in self.rows.to_dict(orient="records"):
                fh.write(json.dumps(rec, default=_json_default) + "\n")
        return path

    @classmethod
    def from_parquet(cls, path: str | Path) -> ScoreTable:
        path = Path(path)
        rows = pd.read_parquet(path)
        meta_path = path.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        return cls(rows=rows, dataset_hash=meta.get("dataset_hash", ""),
                   config_hash=meta.get("config_hash", ""),
                   created_utc=meta.get("created_utc", ""), afv_version=meta.get("afv_version", ""))


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    raise TypeError(f"not serialisable: {type(o)}")


# Validation ---------------------------------------------------------------------------

class SchemaError(ValueError):
    pass


def validate(df: pd.DataFrame, columns: list[str], name: str = "table") -> None:
    """Raise SchemaError when required columns are missing or obviously mistyped."""
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise SchemaError(f"{name}: missing columns {missing}")
    for c in ("valid_time", "init_time"):
        if c in columns and c in df.columns and not pd.api.types.is_datetime64_any_dtype(df[c]):
            raise SchemaError(f"{name}: column {c} must be datetime64, got {df[c].dtype}")
    for c in ("fcst", "obs", "value", "lat", "lon"):
        if c in columns and c in df.columns and not pd.api.types.is_numeric_dtype(df[c]):
            raise SchemaError(f"{name}: column {c} must be numeric, got {df[c].dtype}")
    if "lead_h" in columns and "lead_h" in df.columns and not pd.api.types.is_integer_dtype(df["lead_h"]):
        raise SchemaError(f"{name}: lead_h must be integer, got {df['lead_h'].dtype}")


def ensure_utc(s: pd.Series) -> pd.Series:
    """Return a tz-aware UTC datetime series whatever came in (naive is taken as UTC)."""
    s = pd.to_datetime(s)
    if getattr(s.dt, "tz", None) is None:
        return s.dt.tz_localize("UTC")
    return s.dt.tz_convert("UTC")
