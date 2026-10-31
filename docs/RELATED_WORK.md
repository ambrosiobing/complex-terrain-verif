# Related work, and what this repository does not write itself

Verification of weather forecasts is an old field with good software in it. Writing a
quality-control routine, an elevation-aware interpolation or a blocking index from scratch
would mean reproducing, worse, work that is already published and tested. So this
repository is an integration. The table says what each piece is for, where it comes from
and under what licence, and the last column is the honest one: what would be lost by
writing it here instead.

The combination is what is new: station-level verification in complex terrain, conditioned
on terrain class, on the observed tail and on the circulation regime of the day, across an
Alpine and a Nordic domain with one pipeline.

## Data

| Project | Used for | Licence | Why not written here |
|---|---|---|---|
| [MeteoSwiss PeakWeather](https://github.com/MeteoSwiss/PeakWeather) | The Alpine observations: 302 SwissMetNet stations, ten-minute values from 2017, terrain descriptors from a 50 m elevation model, and co-located ICON-CH1-EPS forecasts | BSD-3-Clause | It is the national service's own curated record. A reconstruction from the live API would be worse and would take months to reach the same depth |
| [ECMWF open data](https://github.com/ecmwf/ecmwf-opendata) | AIFS and IFS forecasts from the same initial conditions | Apache-2.0 | The official client, with the retention and naming rules already encoded |
| [Herbie](https://github.com/blaylockbk/Herbie) | Alternative download path for the same forecasts, with its own cache | MIT | Handles model-by-model file layouts that change without notice |
| [Copernicus Climate Data Store](https://cds.climate.copernicus.eu/) via `cdsapi` | ERA5 geopotential for the circulation classification | Copernicus licence | The reanalysis itself |
| [MET Norway Frost](https://frost.met.no/) | Nordic observations, with quality flags and instrument metadata | CC BY 4.0 | The national record |

## Method

| Project | Used for | Licence | Why not written here |
|---|---|---|---|
| [titanlib](https://github.com/metno/titanlib) | Spatial quality control: spatial consistency test, buddy check, isolation check | BSD-3-Clause | Operational quality control at a national service. Deciding that one station disagrees with its neighbours for a physical reason rather than a sensor one is a research field, not a function |
| [gridpp](https://github.com/metno/gridpp) | Elevation-aware downscaling: elevation gradients, nearest neighbour at the same height, optimal interpolation, quantile mapping | LGPL-3.0 | The post-processing library behind an operational public forecast. A fixed lapse rate is wrong in exactly the case this repository exists to measure |
| [xskillscore](https://github.com/xarray-contrib/xskillscore) | Deterministic and probabilistic metrics on labelled arrays | Apache-2.0 | Correct, tested implementations of scores that are easy to write and easy to write wrongly |
| [verif](https://github.com/WFRT/verif) | Plots and command-line comparison; this repository exports to its format | BSD-3-Clause | Used by forecasters already, so results arrive in a form they can open |
| [ConTrack](https://github.com/steidani/ConTrack) | Blocking and circulation anomaly tracking, following Schwierz and others | MIT | Written and validated inside the European community whose regimes are being used here |
| [ExtremeWeatherBench](https://github.com/brightbandtech/ExtremeWeatherBench) | Case protocol for high-impact events | Apache-2.0 | A published protocol is better than a tail threshold invented for one repository |
| [WeatherReal-Benchmark](https://github.com/microsoft/WeatherReal-Benchmark) | Prior art for station-based evaluation and its quality-control steps | MIT | The closest existing work. Cited rather than rediscovered |
| [meteodata-lab](https://github.com/MeteoSwiss/meteodata-lab), [earthkit-data](https://github.com/ecmwf/earthkit-data) | GRIB handling and regridding, including to the Swiss grid | Apache-2.0 | Official readers for formats with many conventions |
| [wetterdienst](https://github.com/earthobservations/wetterdienst) | One interface to further national services as domains are added | MIT | Twenty-two services already covered, with caching and retries |

## Literature the design copies

The method is taken from published studies rather than assembled by taste.

- Bremnes, Nipen and Seierstad, *Nonlinear Processes in Geophysics* 31, 247, 2024. A data-driven model against physics-based forecasts at Norwegian stations. This pipeline copies its shape: same variables, same lead range, a regional model as the reference.
- Rasp and others, *Journal of Advances in Modeling Earth Systems*, 2024, WeatherBench 2. The metric portfolio, and its own argument for moving from gridded targets toward station observations.
- Ben Bouallegue and others, *Bulletin of the American Meteorological Society* 105(6), 2024. Comparing an AI model with an operational one from the same initial conditions, which is why this repository pulls both from the same open data release.
- Nipen and others, *Artificial Intelligence for the Earth Systems* 5(2), 2026. A national service running a data-driven model operationally on a stretched grid.
- Nipen and others, *Bulletin of the American Meteorological Society* 104(9), 2023. The verification tool this repository exports to.
- Tibaldi and Molteni, *Tellus A* 42, 343, 1990. The blocking index used to split days into blocked and zonal.
- Zambon and others, arXiv 2506.13652, 2025. The PeakWeather dataset paper.

Further reading was proposed for this repository and has not been read yet, so it is not
cited: a station-based evaluation in northern Norway, a weather-type reconstruction paper
behind the regime stratification, a stretched-grid thesis, a kriging and neural-network
downscaling paper for radar-sparse Alpine regions, and a benchmark preprint with a large
station collection. Each enters this page when it has been opened, and not before. A
citation nobody has read is the same defect as a result nobody has run.
