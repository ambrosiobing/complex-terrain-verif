"""The read-only service, tested offline against the fixture run.

Three things are worth testing here and the third is the one that matters: that the
endpoints return what they claim, that the two query backends agree, and that a run name
arriving from the network cannot reach outside the results root.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

fastapi = pytest.importorskip("fastapi", reason="needs the server extra")
from fastapi.testclient import TestClient  # noqa: E402

from afv.server import create_app, query_scores_pandas  # noqa: E402

try:
    import duckdb  # noqa: F401

    from afv.server import query_scores_duckdb
    HAS_DUCKDB = True
except ImportError:  # pragma: no cover
    HAS_DUCKDB = False


SCORE_ROWS = [
    {"model": "aifs-single", "variable": "t2m", "lead_h": 6, "stratum": "all",
     "stratum_value": "all", "metric": "mae", "value": 0.60, "ci_lo": 0.4, "ci_hi": 0.8, "n": 31},
    {"model": "ifs", "variable": "t2m", "lead_h": 6, "stratum": "all",
     "stratum_value": "all", "metric": "mae", "value": 0.66, "ci_lo": 0.5, "ci_hi": 0.9, "n": 31},
    {"model": "aifs-single", "variable": "ws10", "lead_h": 12, "stratum": "all",
     "stratum_value": "all", "metric": "rmse", "value": 1.10, "ci_lo": 0.9, "ci_hi": 1.3, "n": 31},
    {"model": "aifs-single", "variable": "t2m", "lead_h": 6, "stratum": "station_class",
     "stratum_value": "mountain", "metric": "mae", "value": 1.20, "ci_lo": 0.9, "ci_hi": 1.6, "n": 8},
]

MATCHED_ROWS = [
    {"station_id": "ABO", "lat": 46.49, "lon": 7.56, "station_elev_m": 1321.0,
     "station_class": "valley", "county": "CH", "model": "ifs", "variable": "t2m"},
    {"station_id": "JUN", "lat": 46.55, "lon": 7.98, "station_elev_m": 3571.0,
     "station_class": "mountain", "county": "CH", "model": "ifs", "variable": "t2m"},
    {"station_id": "ABO", "lat": 46.49, "lon": 7.56, "station_elev_m": 1321.0,
     "station_class": "valley", "county": "CH", "model": "aifs-single", "variable": "t2m"},
]


@pytest.fixture
def results_root(tmp_path):
    # The results root is a subdirectory, and a real run sits OUTSIDE it holding a file of
    # the same name. Without that, a traversal test passes merely because the escaped path
    # does not exist, which is a 404 from the existence check and not a refusal from the
    # guard. Deleting the guard then leaves the suite green, which is what happened here
    # before this fixture was arranged this way.
    outside = tmp_path / "outside"
    (outside / "figures").mkdir(parents=True)
    pd.DataFrame(SCORE_ROWS).to_parquet(outside / "scores.parquet", index=False)
    (outside / "report.md").write_text("private", encoding="utf-8")

    root = tmp_path / "results"
    run = root / "alps"
    (run / "figures").mkdir(parents=True)
    pd.DataFrame(SCORE_ROWS).to_parquet(run / "scores.parquet", index=False)
    pd.DataFrame(MATCHED_ROWS).to_parquet(run / "matched.parquet", index=False)
    (run / "report.md").write_text("# report\n\n31 paired values.\n", encoding="utf-8")
    (run / "matched.manifest.json").write_text(
        json.dumps({"dataset_hash": "abc123", "rows": 3, "stations": 2}), encoding="utf-8")
    (root / "empty_run").mkdir()
    # A one-pixel image is enough to prove the route and the suffix rule.
    (run / "figures" / "mae.png").write_bytes(
        bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a"
                      "49444154789c6300010000050001"))
    return root


@pytest.fixture
def client(results_root):
    return TestClient(create_app(results_root))


# The service answers for itself ------------------------------------------------------

def test_health_reports_the_root_and_the_engine(client, results_root):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["root_exists"] is True
    assert body["query_engine"] in {"duckdb", "pandas"}


def test_runs_lists_what_each_run_has(client):
    rows = {r["run"]: r for r in client.get("/runs").json()}
    assert rows["alps"]["has_scores"] is True
    assert rows["alps"]["has_report"] is True
    assert rows["empty_run"]["has_scores"] is False


def test_sources_states_what_is_refused_and_why(client):
    by_name = {s["name"]: s for s in client.get("/sources").json()}
    assert "t2m" in by_name["peakweather"]["serves"]
    assert "msl" in by_name["peakweather"]["refuses"]
    assert "QFE" in by_name["peakweather"]["refuses"]["msl"]


# Results -------------------------------------------------------------------------------

def test_scores_filter_by_variable_and_metric(client):
    rows = client.get("/runs/alps/scores", params={"variable": "t2m", "metric": "mae"}).json()
    assert len(rows) == 2
    assert {r["model"] for r in rows} == {"aifs-single", "ifs"}


def test_scores_default_to_the_all_stratum(client):
    rows = client.get("/runs/alps/scores").json()
    assert all(r["stratum"] == "all" for r in rows)
    assert len(rows) == 3


def test_scores_can_ask_for_another_stratum(client):
    rows = client.get("/runs/alps/scores", params={"stratum": "station_class"}).json()
    assert len(rows) == 1
    assert rows[0]["stratum_value"] == "mountain"


def test_manifest_is_served(client):
    assert client.get("/runs/alps/manifest").json()["dataset_hash"] == "abc123"


def test_stations_are_unique(client):
    rows = client.get("/runs/alps/stations").json()
    assert len(rows) == 2                      # three matched rows, two stations
    assert {r["station_id"] for r in rows} == {"ABO", "JUN"}


def test_report_is_served_as_text(client):
    r = client.get("/runs/alps/report")
    assert r.status_code == 200
    assert "31 paired values" in r.text


def test_a_figure_is_served(client):
    r = client.get("/runs/alps/figures/mae.png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")


# The refusals, which are the half that matters ---------------------------------------------

def test_resolve_run_refuses_an_escape_that_exists(results_root):
    # The decisive case: the target is a real directory holding a real scores.parquet, one
    # level outside the root. Only the root check can refuse this, so if the check is
    # removed this test fails, which is the whole point of having it.
    from fastapi import HTTPException

    from afv.server import resolve_run

    assert (results_root.parent / "outside" / "scores.parquet").exists()
    with pytest.raises(HTTPException) as e:
        resolve_run(results_root, "../outside")
    assert e.value.status_code == 400

    # And the same through the application, in case a future router normalises differently.
    app_client = TestClient(create_app(results_root))
    r = app_client.get("/runs/%2e%2e%2foutside/scores")
    assert r.status_code in (400, 404)
    if r.status_code == 200:  # pragma: no cover
        raise AssertionError("the service served a run from outside its root")


def test_resolve_run_accepts_a_legitimate_run(results_root):
    from afv.server import resolve_run
    assert resolve_run(results_root, "alps").name == "alps"


def test_an_absolute_path_is_refused(client):
    r = client.get("/runs/C:/Windows/scores")
    assert r.status_code in (400, 404)


def test_an_unknown_run_is_a_clean_404(client):
    assert client.get("/runs/atlantis/scores").status_code == 404


def test_a_run_without_scores_says_so(client):
    r = client.get("/runs/empty_run/scores")
    assert r.status_code == 404
    assert "no scores" in r.json()["detail"]


def test_only_images_are_served_from_the_figures_route(client, results_root):
    (results_root / "alps" / "figures" / "notes.txt").write_text("not an image", encoding="utf-8")
    assert client.get("/runs/alps/figures/notes.txt").status_code == 400


def test_a_figure_name_cannot_escape_the_figures_directory(client):
    assert client.get("/runs/alps/figures/../report.md").status_code in (400, 404)


def test_the_limit_is_bounded(client):
    assert client.get("/runs/alps/scores", params={"limit": 10 ** 9}).status_code == 422


# The two backends must agree -----------------------------------------------------------------

@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb is not installed")
def test_duckdb_and_pandas_return_the_same_rows(results_root):
    # An optional accelerator that quietly answers differently is worse than none.
    path = results_root / "alps" / "scores.parquet"
    filters = {"variable": "t2m", "metric": "mae", "stratum": "all"}
    a = query_scores_pandas(path, filters, 100)
    b = query_scores_duckdb(path, filters, 100)
    key = lambda rows: sorted((r["model"], r["lead_h"], round(r["value"], 9)) for r in rows)  # noqa: E731
    assert key(a) == key(b)


@pytest.mark.skipif(not HAS_DUCKDB, reason="duckdb is not installed")
def test_the_engine_can_be_chosen_per_request(client):
    a = client.get("/runs/alps/scores", params={"engine": "pandas", "variable": "t2m"}).json()
    b = client.get("/runs/alps/scores", params={"engine": "duckdb", "variable": "t2m"}).json()
    assert len(a) == len(b) == 2


def test_an_unknown_engine_is_refused(client):
    assert client.get("/runs/alps/scores", params={"engine": "sqlite"}).status_code == 422
