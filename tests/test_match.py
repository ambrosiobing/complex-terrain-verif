import numpy as np
import pandas as pd
import xarray as xr

from afv.match import accumulate_obs, deaccumulate, interp_to_stations, lapse_correct, match_field, pairwise_complete


def test_nearest_on_node(linear_field):
    v = interp_to_stations(linear_field, np.array([60.5]), np.array([5.5]), "nearest")
    assert v.shape == (1, 1)
    assert v[0, 0] == 10.0 * 60.5 + 5.5


def test_bilinear_exact_on_linear_field(linear_field):
    lat, lon = 60.37, 5.63
    v = interp_to_stations(linear_field, np.array([lat]), np.array([lon]), "bilinear")
    assert abs(v[0, 0] - (10.0 * lat + lon)) < 1e-9


def test_nearest_differs_offnode(linear_field):
    lat, lon = 60.37, 5.63
    n = interp_to_stations(linear_field, np.array([lat]), np.array([lon]), "nearest")[0, 0]
    b = interp_to_stations(linear_field, np.array([lat]), np.array([lon]), "bilinear")[0, 0]
    assert n != b
    assert n == 10.0 * 60.25 + 5.75   # nearest node


def test_longitude_wrap(linear_field):
    da = linear_field.assign_coords(lon=linear_field["lon"] + 360.0 - 10.0)  # grid 355..356 deg
    v = interp_to_stations(da, np.array([60.5]), np.array([-4.5]), "nearest")
    assert v[0, 0] == 10.0 * 60.5 + 5.5


def test_lapse_600m_higher_is_3p9K_colder():
    t = lapse_correct(np.array([280.0]), model_elev_m=np.array([100.0]), station_elev_m=np.array([700.0]))
    assert abs(t[0] - (280.0 - 3.9)) < 1e-9


def test_lapse_zero_and_nan():
    t = lapse_correct(np.array([280.0, 280.0]), np.array([100.0, np.nan]), np.array([100.0, 500.0]))
    assert t[0] == 280.0 and t[1] == 280.0


def test_lapse_sign_lower_station_is_warmer():
    t = lapse_correct(np.array([280.0]), np.array([500.0]), np.array([0.0]))
    assert t[0] > 280.0


def test_deaccumulate_running_total():
    tp = pd.Series([0.0, 1.0, 3.0, 3.5, 6.0], index=[0, 6, 12, 18, 24])
    d = deaccumulate(tp, 6)
    assert np.isnan(d.loc[0])
    assert d.loc[6] == 1.0 and d.loc[12] == 2.0 and d.loc[24] == 2.5
    d24 = deaccumulate(tp, 24)
    assert d24.loc[24] == 6.0 and np.isnan(d24.loc[18])


def test_deaccumulate_clips_negative_noise():
    tp = pd.Series([0.0, 1.0, 0.999], index=[0, 6, 12])
    assert deaccumulate(tp, 6).loc[12] == 0.0


def test_accumulate_obs_22_of_24_rule():
    idx = pd.date_range("2026-09-01T00:00Z", periods=48, freq="1h")
    h = pd.Series(1.0, index=idx)
    h.iloc[30] = np.nan
    h.iloc[31] = np.nan
    a = accumulate_obs(h, 24, 22)
    assert a.iloc[47] == 22.0          # two missing, 22 present: allowed
    h.iloc[32] = np.nan
    a = accumulate_obs(h, 24, 22)
    assert np.isnan(a.iloc[47])        # three missing: rejected


def test_match_field_joins_obs_on_exact_utc_hour(fixtures_dir):
    stations = pd.read_csv(fixtures_dir / "stations_fixture.csv")
    obs = pd.read_parquet(fixtures_dir / "obs" / "obs.parquet")
    f = sorted((fixtures_dir / "fields").glob("ifs_*.nc"))[0]
    with xr.open_dataset(f) as ds:
        m = match_field(ds, stations, obs, ["t2m", "ws10"], ["nearest", "bilinear"], None, None)
    assert set(m["interp"]) == {"nearest", "bilinear"}
    assert (m["valid_time"] == m["init_time"] + pd.to_timedelta(m["lead_h"], unit="h")).all()
    assert m["obs"].notna().mean() > 0.9
    assert not m["lapse_corrected"].any()


def test_match_field_lapse_applied_with_orography(fixtures_dir):
    stations = pd.read_csv(fixtures_dir / "stations_fixture.csv")
    obs = pd.read_parquet(fixtures_dir / "obs" / "obs.parquet")
    f = sorted((fixtures_dir / "fields").glob("ifs_*.nc"))[0]
    orog = xr.open_dataset(fixtures_dir / "orog_fixture.nc")["orog_m"]
    with xr.open_dataset(f) as ds:
        m = match_field(ds, stations, obs, ["t2m"], ["bilinear"], orog, 0.0065)
    hi = m[m["station_id"] == "SNF002"].iloc[0]       # 610 m station, 100 m model surface
    assert hi["lapse_corrected"]
    assert abs((hi["fcst_raw"] - hi["fcst"]) - 0.0065 * 510) < 1e-9


def test_pairwise_complete_keeps_only_common_samples():
    base = dict(variable="t2m", init_time=pd.Timestamp("2026-09-01", tz="UTC"), lead_h=6, interp="nearest")
    rows = [
        dict(station_id="a", model="ifs", fcst=1.0, obs=1.0, **base),
        dict(station_id="a", model="aifs-single", fcst=1.0, obs=1.0, **base),
        dict(station_id="a", model="persistence", fcst=1.0, obs=1.0, **base),
        dict(station_id="b", model="ifs", fcst=1.0, obs=1.0, **base),           # aifs missing for b
        dict(station_id="c", model="ifs", fcst=1.0, obs=np.nan, **base),
        dict(station_id="c", model="aifs-single", fcst=1.0, obs=np.nan, **base),
    ]
    df = pd.DataFrame(rows)
    out = pairwise_complete(df, ["ifs", "aifs-single"])
    assert set(out["station_id"]) == {"a"}
    assert len(out) == 3
