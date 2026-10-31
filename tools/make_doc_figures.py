#!/usr/bin/env python3
"""Regenerate the figures in docs/figures from a scored run.

Committed images age badly and nobody can tell what produced them, so they are generated
by this script from a score table on disk and never drawn by hand.

    python tools/make_doc_figures.py                     # the fixture run
    python tools/make_doc_figures.py --results results/alps --tag alps

Every figure's title states which run it came from, because the fixture numbers are
synthetic and must never be mistaken for a measurement.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

COLOURS = {
    "aifs-single": "#1f77b4",
    "ifs": "#d62728",
    "persistence": "#7f7f7f",
    "climatology": "#2ca02c",
}
LABELS = {
    "aifs-single": "AIFS (data-driven)",
    "ifs": "IFS (physics)",
    "persistence": "persistence (nowcast)",
    "climatology": "climatology",
}


def _all_rows(scores: pd.DataFrame, variable: str, metric: str) -> pd.DataFrame:
    sel = scores[(scores["stratum"] == "all") & (scores["variable"] == variable)
                 & (scores["metric"] == metric)]
    return sel.sort_values("lead_h")


def error_vs_lead(scores: pd.DataFrame, variable: str, tag: str, out: Path,
                  metric: str = "mae", unit: str = "K") -> Path:
    """Error against lead time, with the bootstrap interval as a band."""
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=140)
    for model, grp in _all_rows(scores, variable, metric).groupby("model"):
        c = COLOURS.get(model, "#444444")
        ax.plot(grp["lead_h"], grp["value"], marker="o", ms=3.5, lw=1.6, color=c,
                label=LABELS.get(model, model))
        if grp["ci_lo"].notna().any():
            ax.fill_between(grp["lead_h"], grp["ci_lo"], grp["ci_hi"], color=c, alpha=0.13, lw=0)
    ax.set_xlabel("lead time (hours)")
    ax.set_ylabel(f"{metric.upper()} ({unit})")
    ax.set_title(f"{metric.upper()} against lead time, {variable}, all stations [{tag}]", fontsize=10)
    ax.grid(alpha=0.25, lw=0.6)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    path = out / f"{metric}_vs_lead_{variable}_{tag}.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def skill_against_persistence(scores: pd.DataFrame, variable: str, tag: str, out: Path) -> Path:
    """Where a forecast starts to beat the simplest nowcast, and by how much.

    Skill is 1 - MAE(model) / MAE(persistence). Zero is no better than carrying the last
    observation forward; above zero the forecast is adding something. The crossover is the
    honest way to show the nowcasting and forecasting ranges on one axis.
    """
    rows = _all_rows(scores, variable, "mae").pivot_table(index="lead_h", columns="model",
                                                          values="value", aggfunc="first")
    if "persistence" not in rows.columns:
        raise SystemExit("this run has no persistence baseline, so there is nothing to compare against")
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=140)
    for model in [m for m in rows.columns if m != "persistence"]:
        skill = 1.0 - rows[model] / rows["persistence"]
        ax.plot(rows.index, skill, marker="o", ms=3.5, lw=1.6,
                color=COLOURS.get(model, "#444444"), label=LABELS.get(model, model))
    ax.axhline(0.0, color="#7f7f7f", lw=1.2, ls="--")
    ax.annotate("zero: no better than carrying the last observation forward",
                xy=(0.02, 0.0), xycoords=("axes fraction", "data"),
                xytext=(0, 6), textcoords="offset points", fontsize=7.5, color="#555555",
                bbox=dict(boxstyle="square,pad=0.15", fc="white", ec="none", alpha=0.85))
    ax.set_xlabel("lead time (hours)")
    ax.set_ylabel("skill against persistence")
    ax.set_title(f"Forecast skill against the nowcast baseline, {variable} [{tag}]", fontsize=10)
    ax.grid(alpha=0.25, lw=0.6)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    path = out / f"skill_vs_persistence_{variable}_{tag}.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="regenerate the figures used in the documentation")
    p.add_argument("--results", default="results/fixtures", help="directory holding scores.parquet")
    p.add_argument("--tag", default="fixtures", help="label printed on every figure")
    p.add_argument("--out", default="docs/figures")
    args = p.parse_args(argv)

    scores = pd.read_parquet(Path(args.results) / "scores.parquet")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    written = [
        error_vs_lead(scores, "t2m", args.tag, out, metric="mae", unit="K"),
        error_vs_lead(scores, "t2m", args.tag, out, metric="bias", unit="K"),
        skill_against_persistence(scores, "t2m", args.tag, out),
    ]
    for w in written:
        print(w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
