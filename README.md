# aifs-frost-verif

Station verification of open AI weather forecasts over northern Norway. ECMWF's data-driven AIFS and its physics-based IFS are pulled from the same initial conditions, matched to MET Norway station observations from Bergen northwards, and scored against fair baselines with block-bootstrap confidence intervals and a smoothness diagnostic next to every skill score.

One sentence claim: on this window and these stations, here is how the two models differ, with intervals, and here is what that does not tell you.

## Why this exists

- Headline AI-weather results are verified against reanalysis, which is training data for the models. Against station observations, errors run 15 to 45 percent larger (Gupta et al. 2025). This repository verifies against stations only.
- MSE-trained models drift toward the conditional mean and win RMSE while losing realism (Bonavita 2024; Rasp et al. 2024). Every table here is paired with a variance-ratio and field-activity diagnostic.
- At Norwegian stations a global AI model beat global NWP on temperature and lost to the 2.5 km regional model (Bremnes, Nipen and Seierstad 2024). The design copies that study: same variables, same lead range, and MEPS as the regional baseline in Tier 1.
- The matched table is written as an ML-ready parquet with a content hash, so a post-processing model can be trained on it later without touching the plumbing.

## What is in the box

```
src/afv/        stations, fetch_forecast, fetch_obs, fetch_meps, match, baselines, dataset,
                scores, diagnostics, verif_bridge, report, diff, cli, fixtures
configs/        default.yaml (Tier 0), fixtures.yaml (offline CI)
notebooks/      00 bootstrap, 01 fetch, 02 build dataset, 03 verify, 04 report, 05 anemoi on a T4
docs/           METHODOLOGY, DIAGRAMS, WHAT_THIS_DOES_NOT_SHOW, DEPLOY
data/           DATASHEET, static station list and orography proxy, cache (gitignored)
tests/          39 offline tests; network tests skipped unless FROST_CLIENT_ID is set
```

## Architecture

```
  ECMWF Open Data (same init)          Frost API (UTC, QC)          MEPS 2.5 km (Tier 1)
  aifs-single   ifs                    station obs                  thredds OPeNDAP
       |         |                          |                              |
       v         v                          v                              v
   fetch_forecast -> fields/*.nc      fetch_obs -> obs.parquet      fetch_meps -> matched rows
                 \__________________________|______________________________/
                                            v
                 match: nearest + bilinear, lapse 0.0065 K/m on t2m, exact UTC hour join
                 baselines: persistence, obs-window climatology, (lagged ensemble)
                 pairwise-complete sample
                                            v
                 results/<name>/matched.parquet  (+ manifest, dataset_hash)
                          |                     |                     |
                       scores              diagnostics           verif_bridge
              bias MAE RMSE, block-CI   variance ratio, activity,   MET Norway verif
              ETS FBI POD FAR (tp24)    Q-Q, tail, strata            text export
                          \_____________________|___________________/
                                            v
                             report.md + figures, diff A vs B
```

More views (concept, pipeline, module contracts, UML) in [docs/DIAGRAMS.md](docs/DIAGRAMS.md).

## 60-second offline quickstart (no credentials)

```
pip install -e ".[dev]"
afv fixtures --out data/fixtures
afv match  --config configs/fixtures.yaml
afv score  --config configs/fixtures.yaml
afv diag   --config configs/fixtures.yaml
afv report --config configs/fixtures.yaml
afv diff results/fixtures/scores_ifs.parquet results/fixtures/scores_aifs-single.parquet
pytest -q
```

The fixture world is synthetic: a smoothed, slowly warming fake AIFS and a sharp, noisier fake IFS. Its numbers mean nothing about the real models; they exist so the pipeline is testable end to end.

## Real run (Tier 0)

1. Request a Frost client id (email only) and put it in `.env` as `FROST_CLIENT_ID`. Set `AFV_USER_AGENT` too.
2. `afv elements` confirms the Frost element ids (three are flagged in `fetch_obs.py` until confirmed).
3. `afv stations` selects and freezes 30 to 60 stations in Vestland, Trondelag, Nordland and Troms.
4. `afv fetch` every evening while the window is open; open data is kept for about four days.
5. `afv obs`, then `afv match`, `afv score`, `afv diag`, `afv report`.

GRIB decoding on Windows and the Kaggle route are in [docs/DEPLOY.md](docs/DEPLOY.md).

## Tiers

| Tier | What | Compute | Claim it supports |
|---|---|---|---|
| 0 | AIFS-single vs IFS vs persistence and climatology; 2 m temperature and 10 m wind; 30 to 60 stations; 6 to 72 h | CPU laptop | Differences with intervals on this window; smoothing diagnostic |
| 1 | plus MEPS 2.5 km and 24 h precipitation with thresholds 1, 5, 10 mm | CPU, OPeNDAP | Global AI vs regional NWP at stations |
| 2 | one anemoi-inference run of `ecmwf/aifs-single-1.0` on a T4, compared with the operational AIFS; lagged-ensemble CRPS | Kaggle or Colab T4 | Anemoi end to end; reproducibility of ECMWF's output |
| 3 | roadmap: post-processing on the matched parquet, Bris fields if published, 90-day window, SEEPS | none yet | next steps |

## Results

Results appear under `results/<config name>/` after a run: `matched.parquet`, `scores.parquet`, `report.md`, `figures/`. Every table caption carries the sample size, the interpolation method and the lapse flag. Nothing is committed here until a real window has been scored.

## What this does not show

One short window and one season; a few dozen stations with representativeness error at coastal and mountain sites; raw model output, not post-processed Yr; deterministic scores unless the lagged ensemble is enabled; 0.25 degree global fields against point observations; nearest versus bilinear changes numbers, so both are shown; the lapse correction uses a proxy orography; the Frost QC accept list is a choice. The full list is in [docs/WHAT_THIS_DOES_NOT_SHOW.md](docs/WHAT_THIS_DOES_NOT_SHOW.md).

## Reproducibility

Every score table carries the dataset hash (order-independent content hash of the matched parquet), the config hash and the package version. Cached GRIBs have sha256 manifests. The same sample is used for every compared model.

## Related work this stands on

- Bremnes, Nipen and Seierstad 2024, Nonlinear Processes in Geophysics 31, 247: Pangu vs HRES, ENS and MEPS at 183 Norwegian stations.
- Ben Bouallegue et al. 2024, BAMS 105(6): same initial conditions, station and analysis verification.
- Rasp et al. 2024, JAMES: WeatherBench 2, metric portfolio and blurring.
- Nipen et al. 2026, AIES: Bris, MET Norway's stretched-grid model.
- Bonavita 2024, GRL: limitations of ML weather models.
- Olivetti and Messori 2024, GMD: extremes, Q-Q and tail scores.
- Brenowitz et al. 2025, GRL: lagged ensembles.
- Nipen et al. 2023, BAMS: verif.
- Lang et al. 2024 and Moldovan et al. 2025: AIFS.

## Licence and citation

Apache-2.0. Cite with [CITATION.cff](CITATION.cff). Data: ECMWF open data and MET Norway Frost, both CC BY 4.0.
