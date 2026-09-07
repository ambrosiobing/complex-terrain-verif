# Datasheet

## Forecast fields

| Item | Value |
|---|---|
| Source | ECMWF Open Data, https://data.ecmwf.int/forecasts/ , via the `ecmwf-opendata` client |
| Models | `aifs-single` (data-driven deterministic, Lang et al. 2024, Moldovan et al. 2025), `ifs` (physics HRES) |
| Resolution | 0.25 degree (the only open-data resolution) |
| Cycles and steps | 00 and 12 UTC; 6 h steps, 6 to 72 h used |
| Parameters | 2t (K), 10u and 10v (m/s, combined to ws10), msl (Pa), tp (m, converted to mm, accumulated from init) |
| Retention | about four days on the server; every GRIB is cached with a sha256 manifest under `data/cache/grib/` |
| Standardised form | NetCDF, dims (lead_h, lat, lon), attrs model and init_time, under `data/cache/fields/` |
| Licence | CC BY 4.0 (ECMWF open data) |

## Observations

| Item | Value |
|---|---|
| Source | MET Norway Frost API, https://frost.met.no |
| Stations | SensorSystem sources in Vestland, Trondelag, Nordland and Troms, latitude at least 60.39, frozen to `data/static/stations_tier0.csv` |
| Elements | air_temperature (2 m), wind_speed (10 m), air_pressure_at_sea_level, sum(precipitation_amount PT1H) (Tier 1); ids confirmed with `afv elements` |
| Time | UTC; referenceTime is the end of the aggregation window; hourly resolution |
| Quality | qualityCode kept per row; 0 and 1 accepted by default; 6 (model data) and 7 (erroneous) removed always |
| Units | converted to K, m/s, Pa, mm to match the model fields |
| Sensor | official sensor `:0` only; duplicates on (station, element, level, offset, resolution) dropped |
| Licence | CC BY 4.0 (MET Norway), attribution "Data from The Norwegian Meteorological Institute" |

## Model orography proxy

| Item | Value |
|---|---|
| File | `data/static/orog_0p25_norway.nc` (variable orog_m) |
| Method | MEPS `surface_geopotential` / 9.80665, area-averaged onto the 0.25 degree grid with `afv.fetch_meps.orography_to_grid` |
| Purpose | lapse-rate correction of 2 m temperature; absent file switches the correction off |

## Regional baseline (Tier 1)

| Item | Value |
|---|---|
| Source | MET Norway THREDDS, `mepslatest/meps_det_2_5km_YYYYMMDDTHHZ.ncml` (OPeNDAP only) and `meps25epsarchive` |
| Variables | air_temperature_2m, wind_speed, air_pressure_at_sea_level, precipitation_amount_acc, surface_geopotential |
| Grid | Lambert conformal 2.5 km, 1069 x 949; station values by nearest neighbour (KD-tree on 2-D lat/lon) |

## Synthetic fixtures

`afv fixtures` writes a fake world under `data/fixtures/`: linear truth fields with a diurnal cycle, an `aifs-single` that is smoothed with a lead-dependent warm bias, an `ifs` that is sharp with more noise, four stations (one on a grid node at 10 m, one off-node at 610 m), hourly observations with one QC-7 hour and one gap per station, and a flat 100 m orography. They exist so CI and tests run offline; no number derived from them means anything about real models.
