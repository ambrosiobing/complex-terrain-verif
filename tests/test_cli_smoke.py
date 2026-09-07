"""Offline end to end: fixtures -> match -> score -> diag -> report -> diff."""
from pathlib import Path

import pandas as pd
import yaml

from afv.cli import main


def test_pipeline_end_to_end(fixtures_cfg_path):
    cfg = yaml.safe_load(Path(fixtures_cfg_path).read_text(encoding="utf-8"))
    results = Path(cfg["paths"]["results_dir"])
    assert main(["match", "--config", str(fixtures_cfg_path)]) == 0
    assert (results / "matched.parquet").exists() and (results / "matched.manifest.json").exists()
    m = pd.read_parquet(results / "matched.parquet")
    assert set(m["model"]) == {"aifs-single", "ifs", "persistence", "climatology"}
    # pairwise complete: every (station, var, init, lead, interp) key has all four rows
    k = m.groupby(["station_id", "variable", "init_time", "lead_h", "interp"])["model"].nunique()
    assert (k == 4).all()

    assert main(["score", "--config", str(fixtures_cfg_path)]) == 0
    s = pd.read_parquet(results / "scores.parquet")
    assert set(s["metric"]) == {"bias", "mae", "rmse"}
    assert (results / "scores.meta.json").exists()
    assert (results / "scores_ifs.parquet").exists() and (results / "scores_aifs-single.parquet").exists()
    # the fixture world: aifs is smoother with a growing warm bias; ifs is sharper with more noise
    allrows = s[(s["stratum"] == "all") & (s["variable"] == "t2m") & (s["metric"] == "bias")]
    aifs_bias = allrows[allrows["model"] == "aifs-single"].sort_values("lead_h")["value"].values
    assert aifs_bias[-1] > aifs_bias[0]

    assert main(["diag", "--config", str(fixtures_cfg_path)]) == 0
    vr = pd.read_csv(results / "diag_variance_ratio.csv")
    a = vr[(vr["model"] == "aifs-single") & (vr["variable"] == "t2m")]["variance_ratio"].mean()
    i = vr[(vr["model"] == "ifs") & (vr["variable"] == "t2m")]["variance_ratio"].mean()
    assert a < i                              # the smoothing diagnostic sees the smoothed model

    assert main(["report", "--config", str(fixtures_cfg_path)]) == 0
    rep = (results / "report.md").read_text(encoding="utf-8")
    assert "## t2m" in rep and "What this report does not show" in rep
    assert (results / "figures" / "t2m_mae_lead.png").exists()

    assert main(["diff", str(results / "scores_ifs.parquet"), str(results / "scores_aifs-single.parquet"),
                 "--out", str(results / "diff.csv")]) == 0
    assert (results / "diff.csv").exists()


def test_info_runs():
    assert main(["info"]) == 0
