# Diagrams

Fixed-width ASCII so they render anywhere. Four views: concept, pipeline, architecture, UML.

## 1. Concept: from a grid to a score

```
  ECMWF Open Data, same initial conditions, 0.25 degree, 6 h steps
  +---------------------+    +---------------------+
  | AIFS-single (AI)    |    | IFS (physics)       |
  +----------+----------+    +----------+----------+
             |  interpolate (nearest | bilinear) to the station point
             v                          v
        fcst_raw(t2m, ws10, msl)   fcst_raw(...)
             |  t2m only: lapse correction  fcst = fcst_raw - 0.0065 K/m * (z_station - z_model)
             v
  MEPS 2.5 km (Tier 1) ----nearest---->  fcst(meps)
  persistence  = obs at initialisation ->  fcst(persistence)
  climatology  = obs mean by station and hour of day (window) -> fcst(climatology)

  Frost API observations (UTC, referenceTime = end of window, qualityCode in {0,1})
             |
             v
        obs(station, valid_time, variable)

  match on (station, valid_time = init + lead) --> MatchedSample rows (the ML-ready parquet)
             |
             v
  scores per (model, variable, lead, stratum): bias, MAE, RMSE with block-bootstrap CI
  diagnostics: variance ratio, field activity, Q-Q, tail-conditional error
```

## 2. Pipeline: how a run flows

```
  NETWORK (once a day while the window is open)        OFFLINE (repeatable, CI-tested)

  afv stations  --Frost /sources, availableTimeSeries--> data/static/stations_tier0.csv
  afv elements  --Frost /elements-------------------> confirm ELEMENTS ids
  afv fetch     --ecmwf-opendata AIFS + IFS---------> data/cache/grib/*.grib2 (+ manifest sha256)
                 \-- cfgrib | earthkit --standardise--> data/cache/fields/{model}_{init}.nc
  afv obs       --Frost /observations, chunked, sequential, cached--> data/cache/obs/obs.parquet

                                                        afv match   fields + stations + obs
                                                           |  interp x2, lapse, UTC join
                                                           |  + persistence + climatology
                                                           |  pairwise-complete filter
                                                           v
                                                        results/<name>/matched.parquet (+ manifest, dataset_hash)
                                                           |
                                            +--------------+---------------+
                                            v                              v
                                        afv score                       afv diag
                                        bias, MAE, RMSE                 variance ratio, activity,
                                        block bootstrap CI              Q-Q, tail, strata
                                        scores.parquet/.jsonl           diag_*.csv
                                            +--------------+---------------+
                                                           v
                                                        afv report  -> report.md + figures/
                                                        afv diff    -> A vs B, CI overlap
                                                        verif_bridge -> verif text files -> verif plots
```

## 3. Architecture: modules and data contracts

```
  configs/*.yaml ----> config.Config (dotted access, config_hash)
                          |
     +--------------------+---------------------------------------------------+
     |                    |                     |                              |
  stations.py        fetch_forecast.py      fetch_obs.py                  fetch_meps.py (Tier 1)
  Frost /sources     ecmwf-opendata         Frost /observations           thredds OPeNDAP
  -> STATION_COLUMNS -> field NetCDF        -> OBS_COLUMNS                -> MATCHED rows (nearest)
                       dims lead_h,lat,lon    SI units, qc kept
                          |                     |
                          +----------+----------+
                                     v
                                 match.py              baselines.py
                                 interp_to_stations    persistence, climatology,
                                 lapse_correct         lagged_ensemble
                                 match_field/match_all
                                 pairwise_complete
                                     v
                                 dataset.py  ---- MATCHED_COLUMNS parquet + manifest (dataset_hash)
                                     v
              +----------------------+----------------------+
              v                      v                      v
          scores.py             diagnostics.py         verif_bridge.py
          bias/mae/rmse         variance_ratio         to_verif_text
          ets/fbi/pod/far       activity_vs_lead       run_verif
          crps_ensemble         qq, tail_conditional
          block_bootstrap       stratify
          score_table -> ScoreTable (SCORE_COLUMNS, dataset_hash, config_hash)
              v                      v
          report.py  <---------------+            diff.py
          render_report (md + png)                diff_tables, summarise
```

## 4. UML: the core data classes

```
  +--------------------------+      +-----------------------------+
  | Station                  |      | FieldMeta                   |
  +--------------------------+      +-----------------------------+
  | station_id: str          |      | model: str                  |
  | name: str                |      | init_time: datetime (UTC)   |
  | lat, lon: float          |      | source_uri: str             |
  | elev_m: float            |      | grid_spacing_deg: float     |
  | county: str              |      +-----------------------------+
  | station_class: str       |      | stem() -> "{model}_{init}"  |
  +--------------------------+      +-----------------------------+
             |  1                                 | 1
             |                                    |
             v  *                                 v  *
  +---------------------------------------------------------------+
  | MatchedSample (one parquet row, MATCHED_COLUMNS)              |
  +---------------------------------------------------------------+
  | station_id, model, variable                                   |
  | init_time, valid_time (UTC), lead_h: int                      |
  | fcst_raw, fcst, obs: float   obs_qc: int                      |
  | interp: nearest | bilinear   lapse_corrected: bool            |
  | model_elev_m, station_elev_m, lat, lon                        |
  | station_class, county                                         |
  +---------------------------------------------------------------+
             |  aggregated by (model, variable, lead_h, stratum)
             v
  +---------------------------------------------------------------+
  | ScoreTable                                                    |
  +---------------------------------------------------------------+
  | rows: DataFrame[SCORE_COLUMNS]                                |
  |   model, variable, lead_h, stratum, stratum_value,            |
  |   metric, value, ci_lo, ci_hi, n                              |
  | dataset_hash: str   config_hash: str                          |
  | created_utc: str    afv_version: str                          |
  +---------------------------------------------------------------+
  | to_parquet(path)  to_jsonl(path)  from_parquet(path)          |
  +---------------------------------------------------------------+
```
