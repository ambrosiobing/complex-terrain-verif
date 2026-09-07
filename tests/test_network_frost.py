"""Live checks against Frost. Skipped unless FROST_CLIENT_ID is set (never in CI)."""
import os

import pandas as pd
import pytest

pytestmark = pytest.mark.network

needs_frost = pytest.mark.skipif(not os.environ.get("FROST_CLIENT_ID"), reason="FROST_CLIENT_ID not set")


@needs_frost
def test_elements_resolve():
    from afv.fetch_obs import ELEMENTS, resolve_elements
    df = resolve_elements()
    found = set(df["id"])
    missing = [v["elementId"] for v in ELEMENTS.values() if v["elementId"] not in found]
    assert not missing, f"fix ELEMENTS in fetch_obs.py: {missing}"


@needs_frost
def test_one_station_six_hours_utc_and_qc(tmp_path):
    from afv.fetch_obs import fetch_observations
    obs = fetch_observations(None, ["SN50540"], ["t2m"], "2026-09-01T00:00Z", "2026-09-01T06:00Z", tmp_path)
    assert not obs.empty
    assert str(pd.to_datetime(obs["valid_time"]).dt.tz) == "UTC"
    assert obs["qc"].between(0, 5).all()
    assert obs["value"].between(250, 320).all()     # Kelvin


@needs_frost
def test_sources_bergen_northwards():
    from afv.stations import fetch_sources, select_stations
    src = fetch_sources(None, counties=["Vestland"])
    sel = select_stations(src, 60.39, None, 20)
    assert len(sel) > 0 and (sel["lat"] >= 60.39).all()
