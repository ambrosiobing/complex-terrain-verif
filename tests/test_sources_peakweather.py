"""The Swiss source, tested without the network.

Everything here runs on synthetic frames shaped like the published metadata, which was read
from the dataset on Saturday 31 October 2026 and is recorded in the module under test. The
live call is marked ``network`` and is deselected by default.

The cases that matter most are the refusals. A source that quietly passes a pressure in the
wrong reduction, or guesses which column holds the station identifier, produces a number
that looks like a result.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from afv.schema import OBS_COLUMNS, STATION_COLUMNS
from afv.sources import available, get_source
from afv.sources.peakweather import (
    PARAMETERS,
    PeakWeatherError,
    classify,
    convert,
    normalise_stations,
    require_mappable,
    resample_hourly,
    to_long_observations,
)

# Fixtures shaped like the published files -------------------------------------------------

def raw_stations() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "station_name": ["Adelboden", "Aigle", "Jungfraujoch"],
            "latitude": [46.491703, 46.326647, 46.547],
            "longitude": [7.560703, 6.924472, 7.985],
            "station_height": [1321.38, 381.02, 3571.0],
            "dem": [1317.77, 381.10, 3565.0],
            "station_type": ["meteo_station", "meteo_station", "meteo_station"],
            "SLOPE_2000M_SIGRATIO1": [11.53, 0.13, 25.0],
            "TPI_2000M": [-53.30, -0.26, 400.0],
            "STD_2000M": [120.94, 0.0, 300.0],
        },
        index=pd.Index(["ABO", "AIG", "JUN"], name="nat_abbr"),
    )
    return df


def wide_observations() -> pd.DataFrame:
    """Ten-minute values with (station, parameter) columns, as the dataset serves them."""
    times = pd.date_range("2024-07-01 00:00", "2024-07-01 01:00", freq="10min")
    cols = pd.MultiIndex.from_product([["ABO", "AIG"], ["temperature", "wind_speed"]])
    rng = np.random.default_rng(0)
    data = rng.normal(15.0, 1.0, size=(len(times), len(cols)))
    df = pd.DataFrame(data, index=times, columns=cols)
    df.index.name = "datetime"
    return df


# Station table ----------------------------------------------------------------------------

def test_normalise_stations_gives_the_repository_schema():
    out = normalise_stations(raw_stations())
    for col in STATION_COLUMNS:
        assert col in out.columns, col
    assert list(out["station_id"]) == ["ABO", "AIG", "JUN"]
    assert out["county"].unique().tolist() == ["CH"]


def test_terrain_columns_travel_with_the_station():
    out = normalise_stations(raw_stations())
    # The Alpine claim is that error depends on terrain, so these must not be dropped.
    for col in ("dem_m", "slope_2km", "tpi_2km", "std_2km", "dem_minus_station_m"):
        assert col in out.columns, col
    abo = out.set_index("station_id").loc["ABO"]
    assert abs(abo["dem_minus_station_m"] - (1317.77 - 1321.38)) < 1e-6


def test_classify_uses_the_configured_threshold():
    assert classify(1800.0, 1500.0) == "mountain"
    assert classify(400.0, 1500.0) == "valley"
    assert classify(1800.0, 2000.0) == "valley"
    assert classify(float("nan")) == "unknown"


def test_station_classes_split_the_alps():
    out = normalise_stations(raw_stations(), mountain_min_elev_m=1500.0)
    by_id = out.set_index("station_id")["station_class"].to_dict()
    assert by_id == {"ABO": "valley", "AIG": "valley", "JUN": "mountain"}


def test_normalise_stations_refuses_an_unexpected_shape():
    bad = pd.DataFrame({"foo": [1], "bar": [2]})
    with pytest.raises(PeakWeatherError):
        normalise_stations(bad)


def test_normalise_stations_refuses_a_table_missing_a_column():
    raw = raw_stations().drop(columns=["station_height"])
    with pytest.raises(PeakWeatherError, match="missing columns"):
        normalise_stations(raw)


# Units and the refusal that matters --------------------------------------------------------

def test_convert_celsius_to_kelvin():
    assert convert(0.0, "degC", "K") == pytest.approx(273.15)
    assert convert(np.array([0.0, 10.0]), "degC", "K").tolist() == pytest.approx([273.15, 283.15])


def test_convert_refuses_an_unlisted_pair():
    # A pass-through default is how a value in the wrong unit reaches a column that
    # says otherwise, so an unknown pair has to raise.
    with pytest.raises(PeakWeatherError, match="no conversion"):
        convert(1.0, "hPa", "K")


def test_pressure_is_refused_because_it_is_the_wrong_reduction():
    # QFE at station altitude is not mean sea level pressure. Scoring one against the
    # other is a large, confident and meaningless bias, so the source refuses it by name.
    assert PARAMETERS["msl"] is None
    with pytest.raises(PeakWeatherError, match="QFE"):
        require_mappable(["t2m", "msl"])


def test_require_mappable_names_an_unknown_variable():
    with pytest.raises(PeakWeatherError, match="not defined"):
        require_mappable(["t2m", "nonsense"])


def test_require_mappable_passes_the_served_variables():
    assert require_mappable(["t2m", "ws10"]) == ["t2m", "ws10"]


# Observations -------------------------------------------------------------------------------

def test_to_long_observations_from_the_wide_layout():
    out = to_long_observations(wide_observations(), ["t2m", "ws10"])
    assert list(out.columns) == OBS_COLUMNS
    assert set(out["variable"]) == {"t2m", "ws10"}
    assert set(out["station_id"]) == {"ABO", "AIG"}
    assert str(out["valid_time"].dt.tz) == "UTC"


def test_temperature_is_converted_to_kelvin_on_the_way_through():
    wide = wide_observations()
    wide.loc[:, ("ABO", "temperature")] = 10.0
    out = to_long_observations(wide, ["t2m"])
    assert out[out["station_id"] == "ABO"]["value"].unique().tolist() == pytest.approx([283.15])


def test_wind_speed_passes_through_unchanged():
    wide = wide_observations()
    wide.loc[:, ("AIG", "wind_speed")] = 3.5
    out = to_long_observations(wide, ["ws10"])
    assert out[out["station_id"] == "AIG"]["value"].unique().tolist() == pytest.approx([3.5])


def test_to_long_observations_from_the_long_layout():
    long = pd.DataFrame({
        "station_id": ["ABO", "ABO"],
        "datetime": pd.to_datetime(["2024-07-01 00:00", "2024-07-01 00:10"]),
        "parameter": ["temperature", "temperature"],
        "value": [10.0, 11.0],
    })
    out = to_long_observations(long, ["t2m"])
    assert len(out) == 2
    assert out["value"].tolist() == pytest.approx([283.15, 284.15])


def test_to_long_observations_refuses_a_layout_it_does_not_recognise():
    with pytest.raises(PeakWeatherError, match="unrecognised observation layout"):
        to_long_observations(pd.DataFrame({"a": [1], "b": [2]}), ["t2m"])


def test_resample_aggregates_each_variable_by_its_own_rule():
    # Temperature is instantaneous and takes the value at the hour; wind is a mean;
    # precipitation is a total. One rule for all three would be wrong for two of them.
    times = pd.date_range("2024-07-01 00:00", "2024-07-01 00:50", freq="10min", tz="UTC")
    rows = []
    for t in times:
        rows.append(("ABO", t, "t2m", 280.0 + times.get_loc(t), 0))
        rows.append(("ABO", t, "ws10", 2.0, 0))
        rows.append(("ABO", t, "tp1h", 1.0, 0))
    obs = pd.DataFrame(rows, columns=OBS_COLUMNS)
    out = resample_hourly(obs).set_index("variable")["value"]
    assert out["t2m"] == pytest.approx(285.0)     # last of the hour
    assert out["ws10"] == pytest.approx(2.0)      # mean
    assert out["tp1h"] == pytest.approx(6.0)      # sum of six ten-minute totals


def test_empty_input_stays_empty_and_keeps_its_columns():
    empty = pd.DataFrame(columns=OBS_COLUMNS)
    assert resample_hourly(empty).empty


# Registry --------------------------------------------------------------------------------

def test_registry_lists_both_domains():
    assert "peakweather" in available()
    assert "frost" in available()


def test_registry_refuses_an_unknown_source():
    from afv.sources import SourceError
    with pytest.raises(SourceError, match="unknown source"):
        get_source("atlantis")


@pytest.mark.network
def test_station_table_downloads_and_normalises(tmp_path):
    from afv.sources.peakweather import stations
    df = stations(tmp_path, max_n=12)
    assert len(df) == 12
    assert df["lat"].between(45.5, 48.0).all()
    assert df["elev_m"].max() > 2000      # the subset must still reach the high stations
