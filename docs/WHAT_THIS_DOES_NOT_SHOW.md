# What this does not show

Read this before quoting any number from a report produced by this repository.

1. **One short window, one season.** Open data is kept for about four days, so the evaluation window is whatever was cached, typically one to two weeks in September. WeatherBench 2 notes that even one year is too short for extremes. No statement about seasons, climatology or rare events follows from these scores.
2. **A few dozen stations, not the network.** Thirty to sixty stations from Bergen northwards. Station observations carry representativeness error: a station may not represent its model grid box, and coastal and mountain sites are the worst cases (Rasp et al. 2024; Bremnes et al. 2024).
3. **Raw model output, not a product.** AIFS and IFS fields are used as published. Yr and MET Nordic are post-processed against the same stations; comparing raw models to them would be circular, and comparing raw to post-processed is a different experiment whose ranking can differ (Bremnes et al. 2024).
4. **0.25 degree global fields against points.** A 2.5 km regional model (MEPS, Tier 1) is not like for like with a 31 km global model. The literature says the regional baseline must still be shown; it does not say the comparison is fair in resolution.
5. **Interpolation is a choice.** Nearest and bilinear give different numbers. Both are computed; tables state which one they use.
6. **The lapse-rate correction uses a proxy.** Open data has no orography field, so model elevation comes from MEPS surface geopotential averaged onto the 0.25 degree grid, or the correction is switched off. The raw value is kept in every row.
7. **Quality control is a choice.** Frost qualityCode 0 and 1 are accepted by default; code 6 (model data) and 7 (erroneous) are never accepted. The raw code is stored in every row so the filter is reproducible.
8. **Deterministic scores unless a lagged ensemble is enabled.** MAE and RMSE reward smooth forecasts. That is why the variance ratio and field activity appear next to them; they are diagnostics, not scores.
9. **Baselines are simple.** Persistence is the observation at initialisation. Climatology is the obs-window mean by station and hour of day, not a 30-year normal.
10. **Confidence intervals are block bootstraps by day.** They respect within-day correlation and nothing else. Between-station correlation on the same day is inside the block, which is right; across-day persistence of errors is not modelled.
11. **Same-sample rule.** Scores are computed only where every compared model and the observation exist. This shrinks the sample and is stated in the manifest.
12. **Extremes.** The tail table restricts to observations above the 90th percentile of a one-week window. That is not an extreme in any climatological sense.
