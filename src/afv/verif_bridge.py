"""Bridge to MET Norway's ``verif`` (Nipen et al. 2023, BAMS) instead of reimplementing its plots.

verif text format, one file per forecast system, header line then rows:
    date hour leadtime location lat lon altitude obs fcst
``date`` is the initialisation date (YYYYMMDD), ``hour`` the initialisation hour, ``leadtime`` in
hours, ``location`` an integer id. verif aligns systems and silently drops any sample missing in one
of them, which is the same-sample rule this package applies explicitly.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pandas as pd

VERIF_COLUMNS = ["date", "hour", "leadtime", "location", "lat", "lon", "altitude", "obs", "fcst"]


def to_verif_text(matched: pd.DataFrame, model: str, variable: str, out: str | Path,
                  interp: str | None = None) -> Path:
    m = matched[(matched["model"] == model) & (matched["variable"] == variable)]
    if interp is not None:
        m = m[m["interp"] == interp]
    m = m.dropna(subset=["fcst", "obs"])
    init = pd.to_datetime(m["init_time"], utc=True)
    loc_ids = {s: i for i, s in enumerate(sorted(m["station_id"].unique()))}
    df = pd.DataFrame({
        "date": init.dt.strftime("%Y%m%d").astype(int),
        "hour": init.dt.hour.astype(int),
        "leadtime": m["lead_h"].astype(int),
        "location": m["station_id"].map(loc_ids).astype(int),
        "lat": m["lat"].astype(float),
        "lon": m["lon"].astype(float),
        "altitude": m["station_elev_m"].astype(float),
        "obs": m["obs"].astype(float),
        "fcst": m["fcst"].astype(float),
    })[VERIF_COLUMNS]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        fh.write(" ".join(VERIF_COLUMNS) + "\n")
        df.to_csv(fh, sep=" ", header=False, index=False, lineterminator="\n")
    return out


def verif_available() -> bool:
    return shutil.which("verif") is not None


def run_verif(inputs: list[Path], metric: str, extra: list[str] | None = None,
              out_png: Path | None = None) -> subprocess.CompletedProcess:
    if not verif_available():
        raise RuntimeError("verif is not on PATH; pip install verif")
    cmd = ["verif", *map(str, inputs), "-m", metric]
    if out_png is not None:
        cmd += ["-f", str(out_png)]
    if extra:
        cmd += list(extra)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def verif_mae_text(inputs: list[Path]) -> float | None:
    """Overall MAE as printed by ``verif -m mae -type text`` for the first system, or None."""
    proc = run_verif(inputs, "mae", extra=["-type", "text"])
    if proc.returncode != 0:
        return None
    vals = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                vals.append(float(parts[1]))
            except ValueError:
                continue
    return sum(vals) / len(vals) if vals else None
