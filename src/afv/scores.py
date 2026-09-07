"""Deterministic and probabilistic scores with block-bootstrap confidence intervals.

Own implementations exist so that the CI can run without MET Norway's ``verif``; a parity test
checks them against verif when it is installed (see verif_bridge).
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from .schema import SCORE_COLUMNS, ScoreTable


# Deterministic ------------------------------------------------------------------------

def bias(f: np.ndarray, o: np.ndarray) -> float:
    return float(np.nanmean(np.asarray(f) - np.asarray(o)))


def mae(f: np.ndarray, o: np.ndarray) -> float:
    return float(np.nanmean(np.abs(np.asarray(f) - np.asarray(o))))


def rmse(f: np.ndarray, o: np.ndarray) -> float:
    return float(np.sqrt(np.nanmean((np.asarray(f) - np.asarray(o)) ** 2)))


# Threshold (2x2) -----------------------------------------------------------------------

def contingency(f: np.ndarray, o: np.ndarray, thr: float) -> tuple[int, int, int, int]:
    """(hits, false alarms, misses, correct negatives) for event ``x >= thr``."""
    fe = np.asarray(f) >= thr
    oe = np.asarray(o) >= thr
    a = int(np.sum(fe & oe))
    b = int(np.sum(fe & ~oe))
    c = int(np.sum(~fe & oe))
    d = int(np.sum(~fe & ~oe))
    return a, b, c, d


def pod(a, b, c, d) -> float:
    return a / (a + c) if (a + c) else float("nan")


def far(a, b, c, d) -> float:
    return b / (a + b) if (a + b) else float("nan")


def fbi(a, b, c, d) -> float:
    return (a + b) / (a + c) if (a + c) else float("nan")


def ets(a, b, c, d) -> float:
    n = a + b + c + d
    if n == 0:
        return float("nan")
    a_ref = (a + b) * (a + c) / n
    den = a + b + c - a_ref
    return (a - a_ref) / den if den else float("nan")


# Probabilistic ------------------------------------------------------------------------

def crps_ensemble(members: np.ndarray, obs: np.ndarray, fair: bool = True) -> float:
    """Mean CRPS of an ensemble (n_samples, n_members) against observations (n_samples,).

    ``fair=True`` uses the finite-ensemble-unbiased spread term (sum over i != j divided by
    m(m-1)); ``fair=False`` gives the classic empirical CRPS. A degenerate ensemble reduces to MAE.
    """
    x = np.asarray(members, dtype=float)
    y = np.asarray(obs, dtype=float)[:, None]
    m = x.shape[1]
    term1 = np.mean(np.abs(x - y), axis=1)
    diff = np.abs(x[:, :, None] - x[:, None, :])
    if fair and m > 1:
        term2 = diff.sum(axis=(1, 2)) / (2.0 * m * (m - 1))
    else:
        term2 = diff.mean(axis=(1, 2)) / 2.0
    return float(np.nanmean(term1 - term2))


def spread_skill(members: np.ndarray, obs: np.ndarray) -> tuple[float, float]:
    """(ensemble spread as mean std, RMSE of the ensemble mean)."""
    x = np.asarray(members, dtype=float)
    spread = float(np.nanmean(np.nanstd(x, axis=1, ddof=1)))
    skill = rmse(np.nanmean(x, axis=1), np.asarray(obs, dtype=float))
    return spread, skill


# Block bootstrap ----------------------------------------------------------------------

def block_bootstrap(df: pd.DataFrame, score_fn: Callable[[np.ndarray, np.ndarray], float],
                    block_col: str = "valid_date", n: int = 1000, ci: float = 0.90,
                    seed: int = 0) -> tuple[float, float, float]:
    """Point estimate and CI of ``score_fn(fcst, obs)`` resampling whole blocks (days).

    Station scores are correlated in time; resampling days instead of rows keeps that structure.
    """
    d = df.dropna(subset=["fcst", "obs"])
    if d.empty:
        return float("nan"), float("nan"), float("nan")
    point = score_fn(d["fcst"].values, d["obs"].values)
    blocks = [g for _, g in d.groupby(block_col, sort=False)]
    if len(blocks) < 2 or n <= 0:
        return point, float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    f_blocks = [b["fcst"].values for b in blocks]
    o_blocks = [b["obs"].values for b in blocks]
    k = len(blocks)
    stats = np.empty(n)
    for i in range(n):
        idx = rng.integers(0, k, k)
        f = np.concatenate([f_blocks[j] for j in idx])
        o = np.concatenate([o_blocks[j] for j in idx])
        stats[i] = score_fn(f, o)
    alpha = (1.0 - ci) / 2.0
    lo, hi = np.nanquantile(stats, [alpha, 1.0 - alpha])
    return point, float(lo), float(hi)


def _fast_bootstrap_det(d: pd.DataFrame, block_col: str, n: int, ci: float, seed: int) -> dict[str, tuple]:
    """Bias, MAE, RMSE with block bootstrap from per-block sufficient statistics (fast path)."""
    err = d["fcst"].values - d["obs"].values
    g = pd.DataFrame({"b": d[block_col].values, "e": err, "a": np.abs(err), "s": err ** 2, "c": 1.0})
    agg = g.groupby("b", sort=False)[["e", "a", "s", "c"]].sum().values
    k = agg.shape[0]
    tot = agg.sum(axis=0)
    point = {"bias": tot[0] / tot[3], "mae": tot[1] / tot[3], "rmse": np.sqrt(tot[2] / tot[3])}
    if k < 2 or n <= 0:
        return {m: (v, float("nan"), float("nan")) for m, v in point.items()}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, size=(n, k))
    res = agg[idx].sum(axis=1)  # (n, 4)
    with np.errstate(invalid="ignore", divide="ignore"):
        b = res[:, 0] / res[:, 3]
        a = res[:, 1] / res[:, 3]
        r = np.sqrt(res[:, 2] / res[:, 3])
    alpha = (1.0 - ci) / 2.0
    out = {}
    for name, arr in (("bias", b), ("mae", a), ("rmse", r)):
        lo, hi = np.nanquantile(arr, [alpha, 1.0 - alpha])
        out[name] = (float(point[name]), float(lo), float(hi))
    return out


# Score table ---------------------------------------------------------------------------

def add_strata(matched: pd.DataFrame, tail_q: float = 0.90) -> pd.DataFrame:
    """Add valid_date, valid_hour and obs_tercile columns used for blocks and strata."""
    m = matched.copy()
    vt = pd.to_datetime(m["valid_time"], utc=True)
    m["valid_date"] = vt.dt.strftime("%Y-%m-%d")
    m["valid_hour"] = vt.dt.hour.astype(int)
    m["obs_tercile"] = "na"
    for var, grp in m.groupby("variable"):
        o = grp["obs"].dropna()
        if len(o) >= 3:
            q1, q2 = o.quantile([1 / 3, 2 / 3]).values
            lab = np.where(grp["obs"] <= q1, "low", np.where(grp["obs"] <= q2, "mid", "high"))
            m.loc[grp.index, "obs_tercile"] = np.where(grp["obs"].isna(), "na", lab)
    return m


def score_table(matched: pd.DataFrame, cfg, dataset_hash: str = "", primary_only: bool = True) -> ScoreTable:
    """Bias, MAE, RMSE per (model, variable, lead_h, stratum) with block-bootstrap CIs.

    ``primary_only`` scores each variable with its primary interpolation method only; the
    alternative method is scored under stratum ``interp`` so both stay visible.
    """
    bs = cfg["bootstrap"]
    strata = list(cfg.get("strata", ["all"]))
    primary = cfg["interp"].get("primary", {})
    m = add_strata(matched, cfg.get("diagnostics", {}).get("tail_quantile", 0.90))
    m = m.dropna(subset=["fcst", "obs"])
    rows = []

    def emit(sub: pd.DataFrame, model, var, lead, stratum, sval):
        res = _fast_bootstrap_det(sub, bs["block_col"], int(bs["n_resamples"]), float(bs["ci"]), int(bs["seed"]))
        for metric, (v, lo, hi) in res.items():
            rows.append([model, var, int(lead), stratum, str(sval), metric, v, lo, hi, int(len(sub))])

    for (model, var, lead), g in m.groupby(["model", "variable", "lead_h"], sort=True):
        prim = primary.get(var, g["interp"].iloc[0])
        gp = g[g["interp"] == prim] if primary_only and (g["interp"] == prim).any() else g
        for stratum in strata:
            if stratum == "all":
                emit(gp, model, var, lead, "all", "all")
            elif stratum in gp.columns:
                for sval, gs in gp.groupby(stratum):
                    emit(gs, model, var, lead, stratum, sval)
        for interp, gi in g.groupby("interp"):
            if interp != prim:
                emit(gi, model, var, lead, "interp", interp)
    out = pd.DataFrame(rows, columns=SCORE_COLUMNS)
    from . import __version__
    return ScoreTable(rows=out, dataset_hash=dataset_hash, config_hash=cfg.hash, afv_version=__version__)


def threshold_scores(matched: pd.DataFrame, thresholds: list[float]) -> pd.DataFrame:
    """ETS, FBI, POD, FAR per (model, lead_h, threshold) for an accumulated variable."""
    rows = []
    for (model, lead), g in matched.dropna(subset=["fcst", "obs"]).groupby(["model", "lead_h"]):
        for thr in thresholds:
            a, b, c, d = contingency(g["fcst"].values, g["obs"].values, thr)
            rows.append({"model": model, "lead_h": int(lead), "threshold": thr, "n": a + b + c + d,
                         "hits": a, "false_alarms": b, "misses": c, "correct_neg": d,
                         "ets": ets(a, b, c, d), "fbi": fbi(a, b, c, d), "pod": pod(a, b, c, d), "far": far(a, b, c, d)})
    return pd.DataFrame(rows)
