# Methodology

Every decision below is a key in `configs/default.yaml`. Change the key, not the code.

## Design lineage

| Choice | Copied from | Reference |
|---|---|---|
| Variables 2 m temperature and 10 m wind speed at Norwegian SYNOP stations, leads to +72 h, regional NWP (MEPS) as a baseline | Bremnes, Nipen and Seierstad 2024, NPG 31, 247 | doi 10.5194/npg-31-247-2024 |
| Competing models from the same initial conditions, verified against stations not reanalysis | Ben Bouallegue et al. 2024, BAMS 105(6) | doi 10.1175/BAMS-D-23-0162.1 |
| Metric portfolio (bias, MAE, RMSE, CRPS where an ensemble exists) plus a realism diagnostic beside the scores | Rasp et al. 2024, WeatherBench 2, JAMES | doi 10.1029/2023MS004019 |
| Report mean skill and tail behaviour together | Nipen et al. 2026, Bris, AIES | doi 10.1175/AIES-D-25-0001.1 |
| Q-Q plots and tail-conditional scores | Olivetti and Messori 2024, GMD 17, 2347 | doi 10.5194/gmd-17-2347-2024 |
| Lagged ensembles for probabilistic scores from deterministic runs | Brenowitz et al. 2025, GRL | arXiv 2401.15305 |
| Fair CRPS for small ensembles | Lang et al. 2026, AIFS-CRPS, npj AI | doi 10.1038/s44387-026-00073-7 |
| Scoring engine for plots | Nipen et al. 2023, verif, BAMS 104(9) | doi 10.1175/BAMS-D-22-0253.1 |

## Fixed decisions

| Topic | Setting | Why |
|---|---|---|
| Truth | Frost station observations, sensor `:0`, qualityCode in {0, 1}; codes 6 and 7 never | Reanalysis is training data for the AI models and flatters them by 15 to 45% (Gupta et al. 2025). Code 6 is model output. |
| Time | UTC throughout; valid = init + lead; exact-hour join. Frost referenceTime is the end of the aggregation window | Norwegian local time shifts by 1 to 2 h and destroys the diurnal cycle; the end-of-window convention is an off-by-one trap for accumulations. |
| Interpolation | nearest and bilinear both computed; primary bilinear for t2m, ws10, msl; nearest for tp | Both are defensible; showing both stops the choice hiding in the result. |
| Lapse rate | 0.0065 K/m on (station elevation minus model elevation), t2m only; raw value kept | Elevation is the dominant systematic error for 2 m temperature in Norway. Model orography for 0.25 degree fields is a MEPS surface-geopotential area average, provenance in DATASHEET. |
| Wind | ws10 = hypot(10u, 10v) against Frost `wind_speed` (10-minute mean at 10 m); no height correction | Instantaneous model wind against a 10-minute mean is a known mismatch; stated, not corrected. |
| Precipitation (Tier 1) | tp24(L) = tp(L) minus tp(L minus 24) from the running total; obs summed from hourly values with at least 22 of 24 hours present; thresholds 1, 5, 10 mm per 24 h; ETS, FBI, POD, FAR | Skill rises with accumulation window (Aga 2022); ML underpredicts heavy rates (Gjosaeter 2022), so thresholds are reported separately. |
| Sample | pairwise complete across the configured models; station-lead pairs below 80% coverage dropped from CI tables | Same-sample rule; verif applies it silently, this code applies it explicitly and logs it. |
| Uncertainty | block bootstrap by valid date, 1000 resamples, 90% interval, seed 0 | Station scores are correlated within a day; naive intervals are too narrow (Wilks 2019). |
| Strata | all, station class, county, valid hour, observed tercile | Optimal model depends on region, event type and lead (Olivetti and Messori 2024b). |
| Diagnostics | variance ratio std(fcst)/std(obs) per lead; spatial std of the forecast field per lead; Q-Q; MAE and bias above the 90th percentile | MSE-trained models drift to the conditional mean and win RMSE while losing realism (Bonavita 2024; Rasp et al. 2024). |
| Baselines | persistence; obs-window climatology by station and hour; MEPS (Tier 1); lagged ensemble of three inits (Tier 2) | Beating a global NWP model at stations is a weak claim without the regional model (Bremnes et al. 2024). |

## Data quality rules carried from the theses and MET documentation

- Frost `-1` precipitation means no precipitation, converted to 0.
- Parallel sensors (`:1` and up) and duplicate offsets or levels are dropped; one row per (station, element, level, offset, resolution).
- Over-large Frost requests fail with 403, not 429; requests are chunked by station group and two-day window and issued sequentially.
- Winter gauge undercatch of snow in wind makes the observation, not the model, the error source (Aga 2022). This repository does not correct for it; state it when a winter window is used.
- MET Nordic 1 km assimilates the very stations used here; it is never used as truth or as a competitor.
- Station hold-out, not only time hold-out, when any model is trained on the matched table (Tangen 2020).
