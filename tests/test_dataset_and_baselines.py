import numpy as np
import pandas as pd
import xarray as xr

from afv.baselines import climatology, lagged_ensemble, members_matrix, persistence
from afv.dataset import dataset_hash, read_dataset, write_dataset
from afv.match import match_field


def _matched(fixtures_dir, model="ifs"):
    stations = pd.read_csv(fixtures_dir / "stations_fixture.csv")
    obs = pd.read_parquet(fixtures_dir / "obs" / "obs.parquet")
    parts = []
    for f in sorted((fixtures_dir / "fields").glob(f"{model}_*.nc"))[:3]:
        with xr.open_dataset(f) as ds:
            parts.append(match_field(ds, stations, obs, ["t2m", "ws10"], ["nearest"], None, None))
    return pd.concat(parts, ignore_index=True), obs


def test_dataset_hash_stable_and_sensitive(fixtures_dir):
    m, _ = _matched(fixtures_dir)
    h1 = dataset_hash(m)
    h2 = dataset_hash(m.sample(frac=1.0, random_state=0))   # order independent
    assert h1 == h2
    m2 = m.copy()
    m2.loc[m2.index[0], "fcst"] += 0.001
    assert dataset_hash(m2) != h1


def test_write_and_read_dataset(fixtures_dir, tmp_path):
    m, _ = _matched(fixtures_dir)
    man = write_dataset(m, tmp_path / "matched.parquet")
    back = read_dataset(tmp_path / "matched.parquet")
    assert len(back) == man["rows"] == len(m)
    assert man["dataset_hash"] == dataset_hash(m)
    assert (tmp_path / "matched.manifest.json").exists()


def test_persistence_equals_obs_at_init(fixtures_dir):
    m, obs = _matched(fixtures_dir)
    p = persistence(m, obs)
    assert (p["model"] == "persistence").all()
    row = p.dropna(subset=["fcst"]).iloc[0]
    o = obs[(obs["station_id"] == row["station_id"]) & (obs["variable"] == row["variable"]) &
            (pd.to_datetime(obs["valid_time"], utc=True) == row["init_time"])]["value"].iloc[0]
    assert row["fcst"] == o
    assert len(p) == len(m)


def test_climatology_by_station_hour(fixtures_dir):
    m, obs = _matched(fixtures_dir)
    c = climatology(m, obs)
    assert (c["model"] == "climatology").all()
    assert c["fcst"].notna().all()
    # same station, same hour of day -> same climatological value
    c["h"] = pd.to_datetime(c["valid_time"], utc=True).dt.hour
    g = c.groupby(["station_id", "variable", "h"])["fcst"].nunique()
    assert (g == 1).all()


def test_lagged_ensemble_member_count(fixtures_dir):
    m, _ = _matched(fixtures_dir)
    lag = lagged_ensemble(m, "ifs", n_members=2, step_h=12)
    assert not lag.empty
    assert set(lag["member"]) == {0, 1}
    X, y = members_matrix(lag)
    assert X.shape[1] == 2 and X.shape[0] == len(y) and not np.isnan(X).any()
