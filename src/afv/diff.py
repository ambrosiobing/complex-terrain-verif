"""Compare two score tables: where do they differ, and is the difference outside the CIs?"""
from __future__ import annotations

import pandas as pd

from .schema import ScoreTable

KEY = ["variable", "lead_h", "stratum", "stratum_value", "metric"]


def diff_tables(a: ScoreTable | pd.DataFrame, b: ScoreTable | pd.DataFrame,
                label_a: str = "A", label_b: str = "B") -> pd.DataFrame:
    ra = a.rows if isinstance(a, ScoreTable) else a
    rb = b.rows if isinstance(b, ScoreTable) else b
    m = ra.merge(rb, on=KEY, suffixes=(f"_{label_a}", f"_{label_b}"))
    va, vb = f"value_{label_a}", f"value_{label_b}"
    m["delta"] = m[vb] - m[va]
    # CIs overlap when the lower of one is below the upper of the other
    lo_a, hi_a, lo_b, hi_b = (f"ci_lo_{label_a}", f"ci_hi_{label_a}", f"ci_lo_{label_b}", f"ci_hi_{label_b}")
    m["ci_overlap"] = ~((m[hi_a] < m[lo_b]) | (m[hi_b] < m[lo_a]))
    # for error metrics smaller is better; bias is judged by absolute value
    better = m.apply(lambda r: (abs(r[vb]) < abs(r[va])) if r["metric"] == "bias" else (r[vb] < r[va]), axis=1)
    m["b_better"] = better
    cols = KEY + [f"model_{label_a}", f"model_{label_b}", va, vb, "delta", "ci_overlap", "b_better", f"n_{label_a}", f"n_{label_b}"]
    return m[[c for c in cols if c in m.columns]]


def summarise(d: pd.DataFrame, label_a: str = "A", label_b: str = "B") -> str:
    if d.empty:
        return "no comparable rows"
    allr = d[d["stratum"] == "all"]
    lines = []
    for metric, g in allr.groupby("metric"):
        nb = int(g["b_better"].sum())
        sep = int((~g["ci_overlap"] & g["b_better"]).sum())
        sepa = int((~g["ci_overlap"] & ~g["b_better"]).sum())
        lines.append(f"{metric}: {label_b} better in {nb}/{len(g)} lead-variable cells; "
                     f"CIs separated in favour of {label_b} in {sep}, of {label_a} in {sepa}")
    return "\n".join(lines)
