"""Markdown report with tables and matplotlib figures. Every caption states the sample."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .schema import ScoreTable  # noqa: E402


def _fmt(v, nd=2):
    return "" if pd.isna(v) else f"{v:.{nd}f}"


def table_by_lead(rows: pd.DataFrame, variable: str, metric: str) -> str:
    r = rows[(rows["variable"] == variable) & (rows["metric"] == metric) & (rows["stratum"] == "all")]
    if r.empty:
        return "_no rows_\n"
    piv = r.pivot_table(index="lead_h", columns="model", values=["value", "ci_lo", "ci_hi", "n"], aggfunc="first")
    models = sorted(r["model"].unique())
    head = "| lead h | " + " | ".join(f"{m} ({metric}, 90% CI, n)" for m in models) + " |\n"
    head += "|---|" + "---|" * len(models) + "\n"
    body = ""
    for lead in sorted(r["lead_h"].unique()):
        cells = []
        for m in models:
            try:
                v = piv.loc[lead, ("value", m)]
                lo = piv.loc[lead, ("ci_lo", m)]
                hi = piv.loc[lead, ("ci_hi", m)]
                n = piv.loc[lead, ("n", m)]
                cells.append(f"{_fmt(v)} [{_fmt(lo)}, {_fmt(hi)}] n={int(n)}")
            except KeyError:
                cells.append("")
        body += f"| {lead} | " + " | ".join(cells) + " |\n"
    return head + body


def fig_lead_curves(rows: pd.DataFrame, variable: str, metric: str, out: Path) -> Path:
    r = rows[(rows["variable"] == variable) & (rows["metric"] == metric) & (rows["stratum"] == "all")]
    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    for model, g in r.groupby("model"):
        g = g.sort_values("lead_h")
        ax.plot(g["lead_h"], g["value"], marker="o", label=model)
        if g["ci_lo"].notna().any():
            ax.fill_between(g["lead_h"], g["ci_lo"], g["ci_hi"], alpha=0.15)
    ax.set_xlabel("lead time (h)")
    ax.set_ylabel(f"{metric} ({variable})")
    ax.set_title(f"{metric} vs lead, {variable}, block-bootstrap 90% CI")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def fig_variance_ratio(vr: pd.DataFrame, variable: str, out: Path) -> Path:
    r = vr[vr["variable"] == variable]
    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    for model, g in r.groupby("model"):
        g = g.sort_values("lead_h")
        ax.plot(g["lead_h"], g["variance_ratio"], marker="o", label=model)
    ax.axhline(1.0, color="k", lw=0.8, ls="--")
    ax.set_xlabel("lead time (h)")
    ax.set_ylabel("std(fcst) / std(obs)")
    ax.set_title(f"Smoothness check, {variable}: below 1 means the forecast is smoother than the observations")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def fig_qq(qqs: list[pd.DataFrame], variable: str, out: Path) -> Path:
    fig, ax = plt.subplots(figsize=(4.6, 4.6))
    for q in qqs:
        if q.empty or q["variable"].iloc[0] != variable:
            continue
        ax.plot(q["obs_q"], q["fcst_q"], marker=".", ms=3, label=q["model"].iloc[0])
    lims = ax.get_xlim()
    ax.plot(lims, lims, "k--", lw=0.8)
    ax.set_xlabel("observed quantile")
    ax.set_ylabel("forecast quantile")
    ax.set_title(f"Q-Q, {variable}: tails below the line are underforecast")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def render_report(st: ScoreTable, diag: dict, out_dir: Path, cfg, manifest: dict | None = None) -> Path:
    out_dir = Path(out_dir)
    figs = out_dir / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    rows = st.rows
    variables = sorted(rows["variable"].unique())
    man = manifest or {}
    lines = [f"# Verification report: {cfg.get('name', '')}", ""]
    lines += [f"Dataset hash `{st.dataset_hash}`, config hash `{st.config_hash}`, afv {st.afv_version}, created {st.created_utc}.", ""]
    if man:
        lines += [f"Sample: {man.get('rows')} matched rows, {man.get('stations')} stations, models {', '.join(man.get('models', []))}, "
                  f"initialisations {man.get('init_range', ['', ''])[0]} to {man.get('init_range', ['', ''])[1]} (UTC).", ""]
    primary = cfg["interp"].get("primary", {})
    lapse = cfg["lapse"]
    lines += ["Method: scores use the primary interpolation per variable (" +
              ", ".join(f"{k}: {v}" for k, v in primary.items()) +
              f"); 2 m temperature lapse correction {'on' if lapse.get('enabled') else 'off'}" +
              (f" at {lapse.get('gamma_k_per_m')} K/m" if lapse.get('enabled') else "") +
              f"; block bootstrap by {cfg['bootstrap']['block_col']}, {cfg['bootstrap']['n_resamples']} resamples, "
              f"{int(float(cfg['bootstrap']['ci']) * 100)}% CI; pairwise-complete sample across models. "
              "Climatology is the obs-window mean by station and hour, not a 30-year normal.", ""]
    for var in variables:
        lines += [f"## {var}", ""]
        for metric in ("mae", "bias", "rmse"):
            lines += [f"### {metric}", "", table_by_lead(rows, var, metric)]
            p = fig_lead_curves(rows, var, metric, figs / f"{var}_{metric}_lead.png")
            lines += [f"![{var} {metric}](figures/{p.name})", ""]
        vr = diag.get("variance_ratio")
        if vr is not None and not vr.empty and (vr["variable"] == var).any():
            p = fig_variance_ratio(vr, var, figs / f"{var}_variance_ratio.png")
            lines += ["### Smoothness", "", f"![{var} variance ratio](figures/{p.name})", ""]
        qqs = diag.get("qq", [])
        if qqs:
            p = fig_qq(qqs, var, figs / f"{var}_qq.png")
            lines += ["### Q-Q", "", f"![{var} qq](figures/{p.name})", ""]
        tail = diag.get("tail")
        if tail is not None and not tail.empty and (tail["variable"] == var).any():
            t = tail[tail["variable"] == var].sort_values(["model", "lead_h"])
            lines += ["### Tail (observations above the 90th percentile)", "",
                      "| model | lead h | n | MAE | bias |", "|---|---|---|---|---|"]
            lines += [f"| {r.model} | {r.lead_h} | {r.n} | {_fmt(r.mae)} | {_fmt(r.bias)} |" for r in t.itertuples()]
            lines += [""]
    act = diag.get("activity")
    if act is not None and not act.empty:
        lines += ["## Field activity vs lead (spatial std of the forecast field)", "",
                  "| model | lead h | spatial std |", "|---|---|---|"]
        lines += [f"| {r.model} | {r.lead_h} | {_fmt(r.spatial_std, 3)} |" for r in act.sort_values(["model", "lead_h"]).itertuples()]
        lines += [""]
    lines += ["## What this report does not show", "",
              "One short window, one season, no extremes; a few dozen stations with representativeness error at coastal and mountain sites; "
              "raw model output, not post-processed products; deterministic scores only unless a lagged ensemble was enabled; "
              "0.25 degree global fields against point observations. See docs/WHAT_THIS_DOES_NOT_SHOW.md.", ""]
    out = out_dir / "report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
