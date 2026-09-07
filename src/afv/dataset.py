"""The ML-ready matched dataset: parquet plus a manifest with a content hash."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .schema import MATCHED_COLUMNS, validate


def dataset_hash(df: pd.DataFrame) -> str:
    """Order-independent sha256 of the content (16 hex chars)."""
    cols = [c for c in MATCHED_COLUMNS if c in df.columns]
    d = df[cols].copy()
    for c in ("init_time", "valid_time"):
        if c in d:
            d[c] = pd.to_datetime(d[c], utc=True).astype("int64")
    h = np.sort(np.asarray(pd.util.hash_pandas_object(d, index=False).values, dtype="uint64"))
    return hashlib.sha256(h.tobytes()).hexdigest()[:16]


def write_dataset(df: pd.DataFrame, path: str | Path, cfg=None) -> dict:
    validate(df, MATCHED_COLUMNS, "matched")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    df["obs_qc"] = df["obs_qc"].astype("Int64")
    df.to_parquet(path, index=False)
    manifest = {
        "path": str(path),
        "rows": int(len(df)),
        "columns": list(df.columns),
        "dataset_hash": dataset_hash(df),
        "config_hash": getattr(cfg, "hash", None) if cfg is not None else None,
        "models": sorted(df["model"].dropna().unique().tolist()),
        "variables": sorted(df["variable"].dropna().unique().tolist()),
        "stations": int(df["station_id"].nunique()),
        "init_range": [str(pd.to_datetime(df["init_time"]).min()), str(pd.to_datetime(df["init_time"]).max())],
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def read_dataset(path: str | Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    validate(df, MATCHED_COLUMNS, "matched")
    return df


def read_manifest(path: str | Path) -> dict:
    p = Path(path).with_suffix(".manifest.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
