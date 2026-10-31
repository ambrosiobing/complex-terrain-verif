# Diagrams

Mermaid, which GitHub renders from the Markdown itself, so there is no build step, no
generated image to go stale, and no toolchain to install before a reader can see the
design. Seven views: architecture, the run, the data contracts, the source interface, two
decision trees, and the stack with its licences.

## 1. Architecture: two domains, one pipeline

The point of the design is the join in the middle. Everything above it is national and
particular; everything below it is the same code whatever country the rows came from.

```mermaid
flowchart TB
    subgraph FC["Forecasts, the same for every domain"]
        OD["ECMWF open data<br/>AIFS single, IFS<br/>0.25 deg, same initial conditions"]
    end

    subgraph ALPS["Alpine domain"]
        PW["MeteoSwiss PeakWeather<br/>302 SwissMetNet stations<br/>10 min, 2017 onwards"]
        TOPO["Terrain per station<br/>slope, aspect, ruggedness<br/>from a 50 m elevation model"]
        ICON["ICON-CH1-EPS<br/>co-located, regional reference"]
    end

    subgraph NORD["Nordic domain"]
        FR["MET Norway Frost<br/>station observations<br/>quality flags carried"]
        MEPS["MEPS 2.5 km<br/>regional reference"]
    end

    SRC{{"afv.sources<br/>one interface:<br/>stations() and observations()"}}
    PW --> SRC
    TOPO --> SRC
    FR --> SRC

    SRC --> MATCH
    OD --> MATCH
    ICON --> MATCH
    MEPS --> MATCH

    MATCH["match<br/>interpolate to the point, correct for height,<br/>join on the exact UTC hour"]
    BASE["baselines<br/>persistence, climatology"]
    MATCH --> BASE --> DS

    DS[("matched.parquet<br/>+ manifest + content hash")]
    DS --> SCORE["score<br/>bias, MAE, RMSE<br/>block bootstrap intervals"]
    DS --> DIAG["diagnostics<br/>variance ratio, activity,<br/>quantile-quantile, tails"]
    DS --> REG["regimes<br/>blocked or zonal day"]

    SCORE --> REP["report.md, figures"]
    DIAG --> REP
    REG --> REP
    SCORE --> VB["verif export<br/>for forecasters' own tooling"]
```

## 2. The run, in order

Two phases, and the separation matters: everything after the fetch is repeatable offline,
which is why continuous integration can exercise the whole pipeline with no credentials.

```mermaid
sequenceDiagram
    autonumber
    actor U as Operator
    participant CLI as afv
    participant S as Source
    participant E as ECMWF open data
    participant D as Dataset layer
    participant R as Report

    U->>CLI: afv stations --config alps.yaml
    CLI->>S: stations(bbox, max_n, types)
    S-->>CLI: station table plus terrain
    CLI-->>U: frozen CSV

    U->>CLI: afv obs
    CLI->>S: observations(ids, variables, window)
    Note over S: refuses a variable it cannot serve honestly
    S-->>CLI: hourly observations, SI units
    CLI-->>D: obs.parquet

    U->>CLI: afv fetch
    CLI->>E: forecast fields for each initialisation
    E-->>CLI: GRIB
    CLI-->>D: standardised netCDF

    U->>CLI: afv match
    D-->>CLI: fields, observations, stations
    CLI->>CLI: interpolate, height correction, UTC join
    CLI->>CLI: add persistence and climatology
    CLI-->>D: matched.parquet plus manifest and hash

    U->>CLI: afv score, afv diag
    CLI-->>D: scores.parquet, diagnostics
    U->>CLI: afv report
    CLI->>R: report.md with sample sizes first
    R-->>U: numbers, each with its interval
```

## 3. Data contracts

Four tables and one rule: a module may only produce or consume these shapes. The validator
raises on a missing column or a wrong dtype rather than letting a string reach a numeric
column and surface later as a strange score.

```mermaid
classDiagram
    class Station {
        +str station_id
        +str name
        +float lat
        +float lon
        +float elev_m
        +str county
        +str station_class
        +to_row() dict
    }
    class Observation {
        +str station_id
        +datetime valid_time
        +str variable
        +float value
        +int qc
    }
    class Matched {
        +str station_id
        +str model
        +str variable
        +datetime init_time
        +datetime valid_time
        +int lead_h
        +float fcst_raw
        +float fcst
        +float obs
        +str interp
        +bool lapse_corrected
        +float model_elev_m
        +float station_elev_m
    }
    class ScoreTable {
        +DataFrame rows
        +str dataset_hash
        +str config_hash
        +str afv_version
        +to_parquet(path)
        +from_parquet(path)
    }
    class FieldMeta {
        +str model
        +datetime init_time
        +float grid_spacing_deg
        +stem() str
    }
    Station "1" --> "many" Observation : measures
    Station "1" --> "many" Matched : located at
    FieldMeta "1" --> "many" Matched : forecast for
    Matched "many" --> "1" ScoreTable : scored into
```

## 4. The source interface

Adding a country is this much work: one module with two functions and a row in the
registry. Nothing downstream changes, which is the whole reason the interface exists.

```mermaid
classDiagram
    class ObservationSource {
        <<interface>>
        +PARAMETERS dict
        +NOT_MAPPED_REASON dict
        +require_mappable(variables) list
        +stations(cache, bbox, max_n) DataFrame
        +observations(cache, ids, variables, t0, t1) DataFrame
    }
    class peakweather {
        +DATASET_TAG = v0.2.0
        +TERRAIN_COLUMNS dict
        +normalise_stations(raw) DataFrame
        +to_long_observations(wide) DataFrame
        +resample_hourly(obs) DataFrame
        +convert(values, unit_in, unit_out)
    }
    class frost {
        +VARIABLES tuple
        +needs FROST_CLIENT_ID
    }
    class registry {
        +SOURCES dict
        +get_source(name)
        +available() list
    }
    ObservationSource <|.. peakweather : implements
    ObservationSource <|.. frost : implements
    registry --> ObservationSource : resolves lazily
```

## 5. Decision tree: may this variable be scored in this domain?

The branch that matters is the third one. A quantity with the same name in two places is
not necessarily the same quantity, and the cost of assuming it is tends to be a large,
confident and wrong number rather than a crash.

```mermaid
flowchart TD
    A["variable requested, for example msl"] --> B{"defined for this source?"}
    B -- no --> X1["raise: name the variable<br/>and list what is known"]
    B -- yes --> C{"mapped, or deliberately refused?"}
    C -- refused --> X2["raise, and state the reason.<br/>msl: the dataset serves QFE at the<br/>station's altitude, not reduced to sea level"]
    C -- mapped --> D{"units match the model field?"}
    D -- no --> E{"conversion listed?"}
    E -- no --> X3["raise: no conversion.<br/>never pass the value through"]
    E -- yes --> F["convert"]
    D -- yes --> F
    F --> G{"ten-minute values?"}
    G -- yes --> H["aggregate to the hour by the<br/>variable's own rule:<br/>instantaneous, mean or total"]
    G -- no --> I["keep as served"]
    H --> J["admitted to the observation table"]
    I --> J
```

## 6. Decision tree: how a forecast is brought to a station

```mermaid
flowchart TD
    A["forecast field on a 0.25 degree grid"] --> B{"interpolation method"}
    B -- nearest --> C["value at the nearest grid point"]
    B -- bilinear --> D["weighted from the four surrounding points"]
    C --> E{"is the variable temperature?"}
    D --> E
    E -- no --> K["pair as it is"]
    E -- yes --> F{"model orography available?"}
    F -- no --> G{"configured fallback"}
    G -- disable --> K
    G -- proxy --> H["use the proxy surface,<br/>and flag the row"]
    F -- yes --> I["height difference =<br/>station elevation minus model surface"]
    H --> I
    I --> J["corrected value kept beside the raw one,<br/>never instead of it, with the difference in the row"]
    J --> K
    K --> L["matched row, carrying interp, lapse flag<br/>and both elevations so the choice can be judged"]
```

## 7. The stack, and who wrote which part

Boxes outside the dashed group are other people's work. The repository's own contribution
is the join between them, and the conditioning on terrain and circulation.

```mermaid
flowchart LR
    subgraph DATA["Data, external"]
        D1["PeakWeather<br/>BSD-3"]
        D2["ECMWF open data<br/>Apache-2.0"]
        D3["Frost<br/>CC BY 4.0"]
        D4["ERA5 via cdsapi<br/>Copernicus"]
    end
    subgraph METHOD["Method, external"]
        M1["titanlib<br/>spatial quality control"]
        M2["gridpp<br/>elevation-aware downscaling"]
        M3["xskillscore<br/>metrics"]
        M4["ConTrack<br/>blocking index"]
        M5["verif<br/>forecaster tooling"]
    end
    subgraph OWN["This repository"]
        O1["sources: one interface<br/>per national network"]
        O2["match, baselines,<br/>dataset with content hash"]
        O3["conditioning:<br/>terrain, tail, regime"]
        O4["report, diff, fixtures, CI"]
    end
    subgraph BASE["Foundation"]
        B1["xarray, pandas, numpy"]
        B2["pixi and conda-forge<br/>for the compiled parts"]
    end
    D1 --> O1
    D3 --> O1
    D2 --> O2
    D4 --> M4
    O1 --> O2
    M1 --> O2
    M2 --> O2
    O2 --> O3
    M3 --> O3
    M4 --> O3
    O3 --> O4
    O4 --> M5
    B1 --> O2
    B2 --> M1
    B2 --> M2
```

## 8. One request, and what the client does with the answer

National services publish hard limits and say, in the response, how long to wait. The
rule that matters is the split on the right: a status that can succeed later is worth
another attempt, and a status that is the provider's final answer is not. Retrying a 404
spends someone's quota to be told the same thing twice.

```mermaid
flowchart TD
    A["request, with the client identifying itself"] --> B{"in the cache already?"}
    B -- yes --> Z["return the cached bytes<br/>provider pays nothing"]
    B -- no --> C["send"]
    C --> D{"transport error<br/>or timeout?"}
    D -- yes --> W
    D -- no --> E{"status below 400?"}
    E -- yes --> Y["write the payload, and beside it a manifest:<br/>url, params, checksum, time"]
    Y --> Z
    E -- no --> F{"status is retriable?<br/>408, 425, 429, 5xx"}
    F -- no --> X["abort and report the status.<br/>another attempt changes nothing"]
    F -- yes --> W{"did the provider say<br/>how long to wait?"}
    W -- "Retry-After" --> G["wait exactly that"]
    W -- "rate limit reset, none left" --> H["wait until the reset"]
    W -- no --> I["exponential backoff with jitter<br/>1, 2, 4, 8, 16 s, capped"]
    G --> J{"attempts left?"}
    H --> J
    I --> J
    J -- yes --> C
    J -- no --> K["give up, and record every attempt<br/>so the gap appears in the report"]
```

The jitter is not decoration. Several clients backing off on the same curve return at the
same moment and recreate the burst that caused the refusal.

## 9. What a cached download is allowed to be

A gap in the archive is a fact about the archive, so it is recorded rather than quietly
closed. The report reads the manifest, which is why a shrinking sample shows up in the
output instead of hiding in it.

```mermaid
stateDiagram-v2
    [*] --> Requested
    Requested --> Cached: already on disk
    Requested --> Fetched: provider answered
    Requested --> Refused: rate limited or too large
    Requested --> Absent: outside the retention window
    Refused --> Fetched: after backoff, smaller chunk
    Refused --> Absent: still refused
    Fetched --> Verified: checksum written beside the file
    Cached --> Verified
    Verified --> InManifest: counted as present
    Absent --> InManifest: counted as a gap, named in the report
    InManifest --> [*]
```
