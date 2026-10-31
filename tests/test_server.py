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

# A matched table as the pipeline actually writes one: a forecast and an observation per
# station, model and valid time. The obs column matters here, because the landing page
# links to routes that forecast from it.
_T0 = pd.Timestamp("2026-10-01 00:00", tz="UTC")
MATCHED_ROWS = []
for _i, (_sid, _lat, _lon, _elev, _cls) in enumerate(
        [("ABO", 46.49, 7.56, 1321.0, "valley"), ("JUN", 46.55, 7.98, 3571.0, "mountain")]):
    for _lead in (6, 12):
        for _model in ("ifs", "aifs-single"):
            MATCHED_ROWS.append({
                "station_id": _sid, "lat": _lat, "lon": _lon, "station_elev_m": _elev,
                "station_class": _cls, "county": "CH", "model": _model, "variable": "t2m",
                "init_time": _T0, "valid_time": _T0 + pd.Timedelta(hours=_lead),
                "lead_h": _lead, "fcst": 280.0 + _lead * 0.1, "obs": 280.0 + _lead * 0.12,
            })


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
    assert len(rows) == 2                      # many matched rows, two stations
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


# Prediction: what is computed here, and what is only retrieved ------------------------------

DIURNAL_ROWS = []
_base = pd.Timestamp("2026-09-01 00:00", tz="UTC")
for _h in range(48):
    _t = _base + pd.Timedelta(hours=_h)
    # A clean diurnal cycle so the hour-of-day mean has a known answer.
    _value = 280.0 + 5.0 * ((_t.hour % 24) / 24.0)
    for _model in ("aifs-single", "ifs"):
        DIURNAL_ROWS.append({
            "station_id": "ABO", "model": _model, "variable": "t2m",
            "init_time": _base, "valid_time": _t, "lead_h": _h,
            "fcst": _value + (0.1 if _model == "ifs" else -0.1), "obs": _value,
        })


@pytest.fixture
def diurnal_root(tmp_path):
    root = tmp_path / "results"
    run = root / "diurnal"
    run.mkdir(parents=True)
    pd.DataFrame(DIURNAL_ROWS).to_parquet(run / "matched.parquet", index=False)
    return root


@pytest.fixture
def diurnal_client(diurnal_root):
    return TestClient(create_app(diurnal_root))


def test_observations_are_deduplicated_across_models(diurnal_root):
    from afv.server import observations_from_matched
    df = pd.read_parquet(diurnal_root / "diurnal" / "matched.parquet")
    obs = observations_from_matched(df, "ABO", "t2m")
    # 48 hours, two models per hour, so the table has 96 rows and the series must have 48.
    assert len(df) == 96
    assert obs.size == 48


def test_persistence_carries_the_last_observation_forward(diurnal_root):
    from afv.server import nowcast_persistence, observations_from_matched
    df = pd.read_parquet(diurnal_root / "diurnal" / "matched.parquet")
    obs = observations_from_matched(df, "ABO", "t2m")
    series = nowcast_persistence(obs, horizon_h=3)
    assert [p["lead_h"] for p in series] == [1, 2, 3]
    assert {round(p["value"], 6) for p in series} == {round(float(obs.iloc[-1]), 6)}


def test_climatology_uses_the_hour_of_day_mean_when_it_has_one(diurnal_root):
    from afv.server import nowcast_climatology, observations_from_matched
    df = pd.read_parquet(diurnal_root / "diurnal" / "matched.parquet")
    obs = observations_from_matched(df, "ABO", "t2m")
    series = nowcast_climatology(obs, horizon_h=3)
    # Every hour is present twice over two days, so each point is a real hour-of-day mean.
    assert all(p["estimated_from"] == "hour_of_day" for p in series)
    first = series[0]
    expected_hour = (obs.index[-1] + pd.Timedelta(hours=1)).hour
    assert first["value"] == pytest.approx(280.0 + 5.0 * (expected_hour / 24.0))


def test_climatology_says_when_it_fell_back_to_the_overall_mean():
    # A run holding only midnight observations has no mean for any other hour, and a flat
    # line that does not say so looks like a broken model rather than a missing hour.
    from afv.server import nowcast_climatology
    times = pd.date_range("2026-09-01", periods=4, freq="24h", tz="UTC")
    obs = pd.Series([280.0, 282.0, 284.0, 286.0], index=times)
    series = nowcast_climatology(obs, horizon_h=2)
    assert [p["estimated_from"] for p in series] == ["overall_mean", "overall_mean"]
    assert series[0]["value"] == pytest.approx(283.0)


def test_nowcast_route_computes_and_labels_both_methods(diurnal_client):
    r = diurnal_client.get("/runs/diurnal/nowcast",
                           params={"station_id": "ABO", "variable": "t2m", "horizon_h": 6})
    assert r.status_code == 200
    body = r.json()
    assert body["n_observations_used"] == 48
    assert body["hours_of_day_available"] == list(range(24))
    for method in ("persistence", "climatology"):
        assert body["forecasts"][method]["computed_here"] is True
        assert len(body["forecasts"][method]["series"]) == 6
    assert any("baselines, not a model" in line for line in body["limitations"])


def test_nowcast_refuses_a_station_it_has_no_observations_for(diurnal_client):
    r = diurnal_client.get("/runs/diurnal/nowcast", params={"station_id": "NOPE"})
    assert r.status_code == 404
    assert "no observations" in r.json()["detail"]


def test_nowcast_horizon_is_bounded(diurnal_client):
    assert diurnal_client.get("/runs/diurnal/nowcast",
                              params={"station_id": "ABO", "horizon_h": 500}).status_code == 422


def test_forecast_route_marks_retrieved_models_as_not_computed_here(diurnal_client):
    body = diurnal_client.get("/runs/diurnal/forecast",
                              params={"station_id": "ABO", "variable": "t2m"}).json()
    prov = body["provenance"]
    # The claim that matters: this service did not produce these numbers.
    assert prov["aifs-single"]["computed_here"] is False
    assert prov["ifs"]["computed_here"] is False
    assert "runs no model" in prov["aifs-single"]["note"]
    assert any("executes no machine-learning model" in line for line in body["limitations"])


def test_forecast_series_pairs_each_model_with_the_observation(diurnal_client):
    body = diurnal_client.get("/runs/diurnal/forecast",
                              params={"station_id": "ABO", "variable": "t2m"}).json()
    first = body["series"][0]
    assert set(first["models"]) == {"aifs-single", "ifs"}
    assert first["observed"] == pytest.approx(first["models"]["ifs"] - 0.1)


def test_forecast_refuses_an_unknown_initialisation(diurnal_client):
    r = diurnal_client.get("/runs/diurnal/forecast",
                           params={"station_id": "ABO", "init_time": "1999-01-01T00:00:00Z"})
    assert r.status_code == 404


# The landing page ----------------------------------------------------------------------

def test_home_page_renders_and_is_self_contained(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    page = r.text
    assert "complex-terrain-verif" in page
    # Nothing is loaded from elsewhere: this must work with the network unplugged.
    for remote in ("http://cdn", "https://cdn", "googleapis", "unpkg", "jsdelivr", "<script"):
        assert remote not in page, remote


def test_home_page_reports_what_the_instance_actually_holds(client):
    page = client.get("/").text
    assert "alps" in page                      # the run present in the fixture root
    assert "Models run here" in page and ">0<" in page


def test_home_page_warns_while_only_fixtures_are_present(tmp_path):
    root = tmp_path / "results"
    (root / "fixtures").mkdir(parents=True)
    pd.DataFrame(SCORE_ROWS).to_parquet(root / "fixtures" / "scores.parquet", index=False)
    page = TestClient(create_app(root)).get("/").text
    assert "Nothing here is a measurement yet" in page


def test_home_page_drops_the_warning_once_a_real_run_exists(client):
    # The fixture root holds a run named "alps", so the banner must not claim otherwise.
    page = client.get("/").text
    assert "Nothing here is a measurement yet" not in page


def test_home_page_escapes_a_run_name():
    # Tested at the renderer, because Windows refuses to create a directory with these
    # characters, and a guard that can only be exercised on one operating system is a
    # guard that stops being exercised.
    from afv.web import render_home
    page = render_home(version="0.2.0", results_root="results", engine="pandas",
                       runs=[{"run": "<script>alert(1)</script>", "has_scores": True,
                              "has_report": False, "has_matched": False}])
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_home_page_escapes_the_results_root_and_engine():
    from afv.web import render_home
    page = render_home(version='0.2.0"><b>', results_root="<i>root</i>", engine="pandas", runs=[])
    assert "<i>root</i>" not in page
    assert "<b>" not in page


def test_roadmap_appears_with_all_three_states(client):
    page = client.get("/").text
    for label in ("built", "next", "later"):
        assert f">{label}<" in page


def test_roadmap_later_items_say_what_they_need():
    from afv.web import ROADMAP
    # "later" without a cost reads as "soon", which is the thing this column prevents.
    for item in ROADMAP:
        if item.status == "later":
            assert item.needs, item.title


def test_roadmap_has_items_in_every_state():
    from afv.web import ROADMAP
    states = {i.status for i in ROADMAP}
    assert states == {"done", "next", "later"}


def test_the_first_real_run_is_still_on_the_roadmap_as_next():
    # This is the item that decides whether anything here is a result. If it is ever moved
    # to "built", that must be a deliberate edit accompanied by a real run.
    from afv.web import ROADMAP
    first_run = [i for i in ROADMAP if i.title == "A first real run"]
    assert first_run and first_run[0].status == "next"


def test_home_page_has_navigation_to_every_section(client):
    page = client.get("/").text
    for anchor in ("#overview", "#try", "#runs", "#routes", "#roadmap"):
        assert f'href="{anchor}"' in page, anchor
    assert 'href="/docs"' in page


def test_home_page_offers_ready_made_links_so_nothing_needs_typing(client):
    # The complaint this answers: a page that lists /runs/{run}/nowcast as text makes the
    # reader find a station id and assemble a query string by hand.
    page = client.get("/").text
    assert "nowcast?station_id=ABO" in page
    assert "forecast?station_id=ABO" in page
    assert "Nothing here needs typing" in page


def test_home_page_says_so_when_there_is_nothing_to_query(tmp_path):
    root = tmp_path / "results"
    (root / "scores_only").mkdir(parents=True)
    pd.DataFrame(SCORE_ROWS).to_parquet(root / "scores_only" / "scores.parquet", index=False)
    page = TestClient(create_app(root)).get("/").text
    assert "nothing to query" in page


def test_prefilled_links_resolve_on_this_instance(client):
    # A link on the page that returns 404 is worse than no link, so one of each is followed.
    import re
    page = client.get("/").text
    for pattern in (r'href="(/runs/[^"]*nowcast[^"]*)"', r'href="(/runs/[^"]*forecast[^"]*)"'):
        href = re.search(pattern, page).group(1).replace("&amp;", "&")
        assert client.get(href).status_code == 200, href
