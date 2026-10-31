import numpy as np
import pandas as pd

from afv.scores import (
    add_strata,
    bias,
    block_bootstrap,
    contingency,
    crps_ensemble,
    ets,
    far,
    fbi,
    mae,
    pod,
    rmse,
    spread_skill,
    threshold_scores,
)

F = np.array([1.0, 2.0, 3.0, 4.0])
O = np.array([1.5, 1.5, 3.5, 3.0])   # errors -0.5, 0.5, -0.5, 1.0


def test_deterministic_hand_values():
    assert abs(bias(F, O) - 0.125) < 1e-12
    assert abs(mae(F, O) - 0.625) < 1e-12
    assert abs(rmse(F, O) - np.sqrt((0.25 + 0.25 + 0.25 + 1.0) / 4)) < 1e-12


def test_contingency_and_threshold_scores():
    f = np.array([1, 1, 0, 0, 1, 0])
    o = np.array([1, 0, 1, 0, 1, 0])
    a, b, c, d = contingency(f, o, 0.5)
    assert (a, b, c, d) == (2, 1, 1, 2)
    assert abs(pod(a, b, c, d) - 2 / 3) < 1e-12
    assert abs(far(a, b, c, d) - 1 / 3) < 1e-12
    assert abs(fbi(a, b, c, d) - 1.0) < 1e-12
    a_ref = (a + b) * (a + c) / 6
    assert abs(ets(a, b, c, d) - (a - a_ref) / (a + b + c - a_ref)) < 1e-12


def test_crps_two_member_closed_form():
    # members (0, 2), obs 1: term1 = mean(|0-1|,|2-1|) = 1; spread term fair = |0-2| / (2*2*1) * 2 pairs = 1
    # fair CRPS = 1 - 0.5 * E|X-X'| with E over i!=j = 2 -> 1 - 1 = 0
    c_fair = crps_ensemble(np.array([[0.0, 2.0]]), np.array([1.0]), fair=True)
    assert abs(c_fair - 0.0) < 1e-12
    # classic: spread term = mean over all i,j (incl. i=j) |Xi-Xj| / 2 = (0+2+2+0)/4/2 = 0.5 -> 1 - 0.5
    c = crps_ensemble(np.array([[0.0, 2.0]]), np.array([1.0]), fair=False)
    assert abs(c - 0.5) < 1e-12


def test_crps_degenerate_ensemble_equals_mae():
    members = np.repeat(F[:, None], 3, axis=1)
    assert abs(crps_ensemble(members, O, fair=False) - mae(F, O)) < 1e-12
    assert abs(crps_ensemble(members, O, fair=True) - mae(F, O)) < 1e-12


def test_spread_skill_shapes():
    members = np.array([[0.0, 2.0], [1.0, 3.0]])
    spread, skill = spread_skill(members, np.array([1.0, 2.0]))
    assert spread > 0 and skill == 0.0


def _matched_frame(n_days=6, per_day=10, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        day = pd.Timestamp("2026-09-01", tz="UTC") + pd.Timedelta(days=d)
        for k in range(per_day):
            o = rng.normal(280, 3)
            rows.append({"valid_time": day + pd.Timedelta(hours=k), "valid_date": day.strftime("%Y-%m-%d"),
                         "fcst": o + rng.normal(0.5, 1.0), "obs": o})
    return pd.DataFrame(rows)


def test_block_bootstrap_contains_point_and_is_reproducible():
    df = _matched_frame()
    p1, lo1, hi1 = block_bootstrap(df, mae, "valid_date", n=200, seed=3)
    p2, lo2, hi2 = block_bootstrap(df, mae, "valid_date", n=200, seed=3)
    assert (p1, lo1, hi1) == (p2, lo2, hi2)
    assert lo1 <= p1 <= hi1
    assert abs(p1 - mae(df["fcst"].values, df["obs"].values)) < 1e-12


def test_block_bootstrap_resamples_whole_days():
    """With one block the CI is undefined (NaN) rather than a spurious interval."""
    df = _matched_frame(n_days=1)
    p, lo, hi = block_bootstrap(df, mae, "valid_date", n=50)
    assert np.isnan(lo) and np.isnan(hi) and p > 0


def test_add_strata_terciles_and_hours():
    df = _matched_frame()
    df["variable"] = "t2m"
    m = add_strata(df)
    assert set(m["obs_tercile"]) <= {"low", "mid", "high", "na"}
    assert m["valid_hour"].between(0, 23).all()


def test_threshold_scores_table():
    df = pd.DataFrame({"model": ["m"] * 4, "lead_h": [6] * 4, "fcst": [0, 2, 6, 12], "obs": [0, 3, 4, 20]})
    t = threshold_scores(df, [1, 5, 10])
    assert set(t["threshold"]) == {1, 5, 10}
    assert t.loc[t["threshold"] == 1, "hits"].iloc[0] == 3
