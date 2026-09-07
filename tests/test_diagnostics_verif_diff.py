import numpy as np
import pandas as pd
import pytest

from afv.diagnostics import qq, stratify, tail_conditional, variance_ratio
from afv.diff import diff_tables, summarise
from afv.schema import ScoreTable
from afv.verif_bridge import VERIF_COLUMNS, to_verif_text, verif_available, verif_mae_text
from afv.scores import mae


def _frame(smooth=0.5, n=200, seed=0):
    rng = np.random.default_rng(seed)
    o = rng.normal(280, 3, n)
    f = 280 + smooth * (o - 280) + rng.normal(0, 0.2, n)
    t0 = pd.Timestamp("2026-09-01", tz="UTC")
    return pd.DataFrame({"station_id": "s", "model": "m", "variable": "t2m", "lead_h": 6, "interp": "nearest",
                         "init_time": t0, "valid_time": t0 + pd.to_timedelta(np.arange(n), unit="h"),
                         "fcst": f, "obs": o, "lat": 60.0, "lon": 5.0, "station_elev_m": 10.0})


def test_variance_ratio_smoothed_forecast_below_one():
    vr = variance_ratio(_frame(0.5))
    assert vr["variance_ratio"].iloc[0] < 0.7


def test_qq_and_tail():
    df = _frame(0.5)
    q = qq(df, "m", "t2m")
    assert len(q) == 50 and (np.diff(q["obs_q"]) >= 0).all()
    t = tail_conditional(df, 0.9)
    assert t["n"].iloc[0] >= 15
    assert t["bias"].iloc[0] < 0     # a smoothed forecast underforecasts the upper tail


def test_stratify_partitions_without_loss():
    df = _frame()
    parts = stratify(df, "valid_hour")
    assert sum(len(p) for p in parts.values()) == len(df)


def test_verif_text_columns(tmp_path):
    df = _frame()
    p = to_verif_text(df, "m", "t2m", tmp_path / "m.txt")
    lines = p.read_text().splitlines()
    assert lines[0].split() == VERIF_COLUMNS
    assert len(lines) == len(df) + 1
    first = lines[1].split()
    assert first[0] == "20260901" and first[1] == "0" and first[2] == "6"


@pytest.mark.skipif(not verif_available(), reason="verif not installed")
def test_verif_parity_mae(tmp_path):
    df = _frame()
    p = to_verif_text(df, "m", "t2m", tmp_path / "m.txt")
    v = verif_mae_text([p])
    assert v is not None
    assert abs(v - mae(df["fcst"].values, df["obs"].values)) < 1e-3


def test_diff_tables_and_summary():
    cols = ["model", "variable", "lead_h", "stratum", "stratum_value", "metric", "value", "ci_lo", "ci_hi", "n"]
    a = ScoreTable(pd.DataFrame([["ifs", "t2m", 6, "all", "all", "mae", 1.0, 0.9, 1.1, 50],
                                 ["ifs", "t2m", 6, "all", "all", "bias", 0.5, 0.4, 0.6, 50]], columns=cols), "h", "c")
    b = ScoreTable(pd.DataFrame([["aifs", "t2m", 6, "all", "all", "mae", 0.7, 0.6, 0.8, 50],
                                 ["aifs", "t2m", 6, "all", "all", "bias", -0.6, -0.7, -0.5, 50]], columns=cols), "h", "c")
    d = diff_tables(a, b, "ifs", "aifs")
    assert len(d) == 2
    m = d[d["metric"] == "mae"].iloc[0]
    assert m["b_better"] and not m["ci_overlap"] and abs(m["delta"] + 0.3) < 1e-12
    bi = d[d["metric"] == "bias"].iloc[0]
    assert not bi["b_better"]              # |−0.6| > |0.5|
    s = summarise(d, "ifs", "aifs")
    assert "mae" in s and "bias" in s
