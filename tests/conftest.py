import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr
import yaml

from afv.fixtures import make_fixtures


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("fixtures")
    make_fixtures(d, seed=0)
    return d


@pytest.fixture(scope="session")
def fixtures_cfg_path(fixtures_dir, tmp_path_factory) -> Path:
    """The repo's configs/fixtures.yaml with paths pointed at the temp fixtures."""
    repo = Path(__file__).resolve().parents[1]
    with (repo / "configs" / "fixtures.yaml").open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    results = tmp_path_factory.mktemp("results")
    cfg["paths"] = {
        "cache_dir": str(fixtures_dir), "fields_dir": str(fixtures_dir / "fields"),
        "obs_dir": str(fixtures_dir / "obs"), "stations_csv": str(fixtures_dir / "stations_fixture.csv"),
        "orography_nc": str(fixtures_dir / "orog_fixture.nc"), "results_dir": str(results),
    }
    cfg["bootstrap"]["n_resamples"] = 50
    p = tmp_path_factory.mktemp("cfg") / "fixtures.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


@pytest.fixture
def linear_field() -> xr.DataArray:
    """5x5 grid where value = 10*lat + lon, so bilinear interpolation is exact."""
    lat = np.arange(60.0, 61.01, 0.25)
    lon = np.arange(5.0, 6.01, 0.25)
    vals = 10.0 * lat[:, None] + lon[None, :]
    return xr.DataArray(vals[None, :, :], dims=("lead_h", "lat", "lon"),
                        coords={"lead_h": [6], "lat": lat, "lon": lon})


def has_frost() -> bool:
    return bool(os.environ.get("FROST_CLIENT_ID"))
