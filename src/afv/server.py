"""A read-only service over results this repository has already computed.

What it is for: making a scored run available to a browser, a dashboard or a colleague
without sending parquet files around, and without anyone needing the toolchain installed.
Point it at a results directory and it serves the score tables, the station list, the
manifest, the report and the figures.

What it deliberately is not: a forecasting service. It does not run a model, it does not
fetch anything, and it has no write path. Everything it returns was produced offline by
``afv match`` and ``afv score``, and the manifest it serves says which run and which
content hash. A service that could also compute would need a GPU and would make the thing
unrunnable for most readers; a service that could write would need an authorisation story
that a read-only one simply does not have.

Two engineering notes:

* **Queries go through DuckDB when it is installed, and pandas when it is not.** DuckDB
  reads parquet without loading it, which matters once a run is millions of rows. The two
  backends must agree, and a test asserts that they do, because an optional accelerator
  that quietly answers differently is worse than no accelerator.
* **Every path from a request is resolved and checked against the root.** A run name is
  user input, and ``../`` in user input is how a read-only service starts serving private
  keys.

Run it with::

    pip install -e ".[server]"
    uvicorn afv.server:app --host 127.0.0.1 --port 8000

or through the container in ``docs/SERVING.md``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

try:  # optional, and the service works without it
    import duckdb
except ImportError:  # pragma: no cover
    duckdb = None

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
except ImportError as e:  # pragma: no cover
    raise ImportError("the server needs the 'server' extra: pip install -e '.[server]'") from e

from . import __version__

DEFAULT_ROOT = Path(os.environ.get("AFV_RESULTS_ROOT", "results"))
MAX_ROWS = 50_000


# Safety ---------------------------------------------------------------------------------

def resolve_run(root: Path, run: str) -> Path:
    """Resolve a run directory, refusing anything that escapes the root.

    The run name arrives from the network. Joining user input to a path and trusting the
    result is the oldest way to serve a file nobody meant to publish.
    """
    root = Path(root).resolve()
    candidate = (root / run).resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(status_code=400, detail="run name escapes the results root")
    if not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"no such run: {run}")
    return candidate


# Query backends --------------------------------------------------------------------------

def query_scores_pandas(path: Path, filters: dict[str, Any], limit: int) -> list[dict]:
    df = pd.read_parquet(path)
    for col, value in filters.items():
        if value is not None and col in df.columns:
            df = df[df[col] == value]
    return json.loads(df.head(limit).to_json(orient="records"))


def query_scores_duckdb(path: Path, filters: dict[str, Any], limit: int) -> list[dict]:
    where, params = [], []
    for col, value in filters.items():
        if value is not None:
            where.append(f"{col} = ?")
            params.append(value)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT * FROM read_parquet(?){clause} LIMIT {int(limit)}"
    con = duckdb.connect()
    try:
        df = con.execute(sql, [str(path), *params]).fetch_df()
    finally:
        con.close()
    return json.loads(df.to_json(orient="records"))


def query_scores(path: Path, filters: dict[str, Any], limit: int = MAX_ROWS,
                 engine: str | None = None) -> list[dict]:
    """Same answer either way; DuckDB is chosen when present because it does not load the file."""
    use = engine or ("duckdb" if duckdb is not None else "pandas")
    if use == "duckdb" and duckdb is None:
        raise HTTPException(status_code=503, detail="duckdb is not installed")
    return (query_scores_duckdb if use == "duckdb" else query_scores_pandas)(path, filters, limit)


# Prediction ----------------------------------------------------------------------------
#
# Two of these are computed here and one is not, and the difference is reported in every
# response rather than left for a reader to assume.
#
# * persistence and climatology are *computed on request* from the observations in the
#   run. They are real forecast methods, they need no model weights and no accelerator,
#   and they are the baselines every score in this repository is measured against. A
#   forecast that cannot beat them is not worth serving.
# * the machine-learning and physics forecasts are *retrieved*. They were produced
#   elsewhere, fetched, and matched offline. This service does not run a model, and a
#   response that implied otherwise would be the same defect as a score with no run
#   behind it.

PERSISTENCE_NOTE = (
    "Computed here, from the last observation at this station carried forward. It is the "
    "simplest forecast there is and it is hard to beat at short lead times; the lead at "
    "which a model overtakes it is the boundary between nowcasting and forecasting."
)
CLIMATOLOGY_NOTE = (
    "Computed here, as the mean for this station and hour of day over the observations in "
    "this run. It is a window climatology, not a thirty-year normal, so it carries that "
    "window's weather."
)
RETRIEVED_NOTE = (
    "Retrieved, not computed here. Produced by its provider, fetched and matched offline. "
    "This service runs no model."
)


def observations_from_matched(df: pd.DataFrame, station_id: str, variable: str) -> pd.Series:
    """The observed series for one station and variable, from the matched table.

    The matched table repeats an observation once per model, so it is reduced to one value
    per valid time before anything is computed from it.
    """
    required = {"station_id", "variable", "valid_time", "obs"}
    missing = required - set(df.columns)
    if missing:
        # A matched table without these is not a table this can forecast from. Returning
        # empty makes the route answer "no observations" instead of raising a 500, which is
        # what a reader following a link from the landing page would otherwise hit.
        return pd.Series(dtype="float64")
    sel = df[(df["station_id"] == station_id) & (df["variable"] == variable)]
    sel = sel.dropna(subset=["obs"])
    if sel.empty:
        return pd.Series(dtype="float64")
    one = sel.drop_duplicates(subset=["valid_time"]).set_index("valid_time")["obs"].sort_index()
    return one


def nowcast_persistence(obs: pd.Series, horizon_h: int, step_h: int = 1) -> list[dict]:
    """Carry the last observation forward. Constant by construction, and honest about it."""
    if obs.empty:
        return []
    last_time = obs.index[-1]
    last_value = float(obs.iloc[-1])
    return [
        {"lead_h": h, "valid_time": (last_time + pd.Timedelta(hours=h)).isoformat(),
         "value": last_value}
        for h in range(step_h, horizon_h + 1, step_h)
    ]


def nowcast_climatology(obs: pd.Series, horizon_h: int, step_h: int = 1) -> list[dict]:
    """The mean for this hour of day, over the observations available in this run.

    A run need not contain every hour. A window of six-hourly observations has means for
    four hours of the day and nothing for the other twenty, and silently returning the
    overall mean for those would produce a flat line that looks like a broken model. So
    each point says which of the two it is, and the caller can see the difference.
    """
    if obs.empty:
        return []
    by_hour = obs.groupby(obs.index.hour).mean()
    overall = float(obs.mean())
    last_time = obs.index[-1]
    out = []
    for h in range(step_h, horizon_h + 1, step_h):
        t = last_time + pd.Timedelta(hours=h)
        has_hour = t.hour in by_hour.index
        out.append({
            "lead_h": h,
            "valid_time": t.isoformat(),
            "value": float(by_hour[t.hour]) if has_hour else overall,
            "estimated_from": "hour_of_day" if has_hour else "overall_mean",
        })
    return out


def _list_runs(root: Path) -> list[dict]:
    """The runs present, and what each one holds. Used by the page and by the route."""
    if not Path(root).is_dir():
        return []
    out = []
    for d in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        out.append({
            "run": d.name,
            "has_scores": (d / "scores.parquet").exists(),
            "has_matched": (d / "matched.parquet").exists(),
            "has_report": (d / "report.md").exists(),
        })
    return out


# Application -------------------------------------------------------------------------------

def create_app(root: Path | str = DEFAULT_ROOT) -> FastAPI:
    root = Path(root)
    app = FastAPI(
        title="complex-terrain-verif",
        version=__version__,
        description="Read-only access to station verification results. Computes nothing.",
    )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home() -> str:
        """A page a person can read, built from what this instance actually holds."""
        from .web import render_home
        present = _list_runs(root)
        station_count = None
        sample_run = None
        sample_stations: list[str] = []
        sample_variables: list[str] = []
        newest = next((r for r in present if r.get("has_matched")), None)
        if newest is not None:
            try:
                df = pd.read_parquet(root / newest["run"] / "matched.parquet",
                                     columns=["station_id", "variable"])
                station_count = int(df["station_id"].nunique())
                sample_run = newest["run"]
                # A handful is enough to make the page usable without turning it into a
                # directory listing of three hundred stations.
                sample_stations = sorted(df["station_id"].astype(str).unique())[:8]
                sample_variables = sorted(df["variable"].astype(str).unique())
            except Exception:
                station_count = None
        return render_home(
            version=__version__,
            results_root=str(root),
            runs=present,
            engine="duckdb" if duckdb is not None else "pandas",
            station_count=station_count,
            only_fixtures=bool(present) and {r["run"] for r in present} <= {"fixtures"},
            sample_run=sample_run,
            sample_stations=sample_stations,
            sample_variables=sample_variables,
        )

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "results_root": str(root),
            "root_exists": root.is_dir(),
            "query_engine": "duckdb" if duckdb is not None else "pandas",
        }

    @app.get("/sources")
    def sources() -> list[dict]:
        """What each observation source serves, and what it refuses and why."""
        from .sources import available, get_source
        out = []
        for name in available():
            try:
                mod = get_source(name)
            except Exception as e:
                out.append({"name": name, "available": False, "reason": str(e)})
                continue
            params = getattr(mod, "PARAMETERS", None)
            if params is None:
                out.append({"name": name, "available": True,
                            "serves": list(getattr(mod, "VARIABLES", ())), "refuses": {}})
                continue
            out.append({
                "name": name,
                "available": True,
                "serves": [k for k, v in params.items() if v is not None],
                "refuses": {k: getattr(mod, "NOT_MAPPED_REASON", {}).get(k, "not mapped")
                            for k, v in params.items() if v is None},
            })
        return out

    @app.get("/runs")
    def runs() -> list[dict]:
        return _list_runs(root)

    @app.get("/runs/{run}/manifest")
    def manifest(run: str) -> dict:
        d = resolve_run(root, run)
        path = d / "matched.manifest.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="this run has no manifest")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/runs/{run}/scores")
    def scores(run: str,
               variable: str | None = Query(None),
               model: str | None = Query(None),
               metric: str | None = Query(None),
               stratum: str | None = Query("all"),
               lead_h: int | None = Query(None),
               limit: int = Query(5000, ge=1, le=MAX_ROWS),
               engine: str | None = Query(None, pattern="^(duckdb|pandas)$")) -> list[dict]:
        d = resolve_run(root, run)
        path = d / "scores.parquet"
        if not path.exists():
            raise HTTPException(status_code=404, detail="this run has no scores")
        filters = {"variable": variable, "model": model, "metric": metric,
                   "stratum": stratum, "lead_h": lead_h}
        return query_scores(path, filters, limit, engine)

    @app.get("/runs/{run}/stations")
    def stations(run: str, limit: int = Query(1000, ge=1, le=MAX_ROWS)) -> list[dict]:
        d = resolve_run(root, run)
        matched = d / "matched.parquet"
        if not matched.exists():
            raise HTTPException(status_code=404, detail="this run has no matched table")
        df = pd.read_parquet(matched)
        cols = [c for c in ("station_id", "lat", "lon", "station_elev_m", "station_class", "county")
                if c in df.columns]
        uniq = df[cols].drop_duplicates(subset=["station_id"]).head(limit)
        return json.loads(uniq.to_json(orient="records"))

    @app.get("/runs/{run}/nowcast")
    def nowcast(run: str,
                station_id: str = Query(..., description="station identifier, from /runs/{run}/stations"),
                variable: str = Query("t2m"),
                horizon_h: int = Query(12, ge=1, le=72),
                step_h: int = Query(1, ge=1, le=12)) -> dict:
        """Compute a baseline forecast here, now, from this run's observations.

        Two methods, both computed on request and neither of them a learned model. This is
        the only route in the service that calculates a prediction rather than returning
        one, and the response says which is which.
        """
        d = resolve_run(root, run)
        matched_path = d / "matched.parquet"
        if not matched_path.exists():
            raise HTTPException(status_code=404, detail="this run has no matched table")
        df = pd.read_parquet(matched_path)
        obs = observations_from_matched(df, station_id, variable)
        if obs.empty:
            raise HTTPException(
                status_code=404,
                detail=f"no observations for station {station_id!r} and variable {variable!r} in this run")
        return {
            "run": run,
            "station_id": station_id,
            "variable": variable,
            "issued_from": obs.index[-1].isoformat(),
            "last_observed_value": float(obs.iloc[-1]),
            "n_observations_used": int(obs.size),
            # Which hours of the day this run actually has. A short or coarse window covers
            # few of them, and that is why a climatology point may fall back to the overall
            # mean; the series says so point by point.
            "hours_of_day_available": sorted({int(h) for h in obs.index.hour}),
            "forecasts": {
                "persistence": {"computed_here": True, "method": PERSISTENCE_NOTE,
                                "series": nowcast_persistence(obs, horizon_h, step_h)},
                "climatology": {"computed_here": True, "method": CLIMATOLOGY_NOTE,
                                "series": nowcast_climatology(obs, horizon_h, step_h)},
            },
            "limitations": [
                "These are baselines, not a model. They exist so that a model's skill can be "
                "measured against something cheap, and they are what every score in this "
                "repository is compared with.",
                "They are computed from the observations contained in this run only. A run "
                "covering two weeks carries two weeks of weather and no climate.",
                "Nothing here is operational. The run may be old; check issued_from before "
                "reading a lead time as a time of day.",
                "No machine-learning model is executed by this service. For what those models "
                "predicted, use /runs/{run}/forecast, which retrieves rather than computes.",
            ],
        }

    @app.get("/runs/{run}/forecast")
    def forecast(run: str,
                 station_id: str = Query(...),
                 variable: str = Query("t2m"),
                 init_time: str | None = Query(None, description="ISO initialisation; default is the latest")) -> dict:
        """What every model in this run predicted for one station, beside the observation.

        Retrieved, not computed. The provenance block says so for each model.
        """
        d = resolve_run(root, run)
        matched_path = d / "matched.parquet"
        if not matched_path.exists():
            raise HTTPException(status_code=404, detail="this run has no matched table")
        df = pd.read_parquet(matched_path)
        sel = df[(df["station_id"] == station_id) & (df["variable"] == variable)]
        if sel.empty:
            raise HTTPException(status_code=404,
                                detail=f"nothing for station {station_id!r} and variable {variable!r}")
        if init_time:
            wanted = pd.Timestamp(init_time)
            if wanted.tzinfo is None:
                wanted = wanted.tz_localize("UTC")
            sel = sel[sel["init_time"] == wanted]
            if sel.empty:
                raise HTTPException(status_code=404, detail=f"no run initialised at {init_time}")
        else:
            sel = sel[sel["init_time"] == sel["init_time"].max()]

        computed_here = {"persistence", "climatology"}
        series = []
        for lead, grp in sel.groupby("lead_h"):
            row = {"lead_h": int(lead),
                   "valid_time": grp["valid_time"].iloc[0].isoformat(),
                   "observed": None if pd.isna(grp["obs"].iloc[0]) else float(grp["obs"].iloc[0]),
                   "models": {}}
            for model, g in grp.groupby("model"):
                value = g["fcst"].iloc[0]
                row["models"][str(model)] = None if pd.isna(value) else float(value)
            series.append(row)

        models = sorted({str(m) for m in sel["model"].unique()})
        return {
            "run": run,
            "station_id": station_id,
            "variable": variable,
            "init_time": sel["init_time"].iloc[0].isoformat(),
            "series": sorted(series, key=lambda r: r["lead_h"]),
            "provenance": {
                m: {"computed_here": m in computed_here,
                    "note": (PERSISTENCE_NOTE if m == "persistence"
                             else CLIMATOLOGY_NOTE if m == "climatology"
                             else RETRIEVED_NOTE)}
                for m in models
            },
            "limitations": [
                "This service executes no machine-learning model. Those values were produced "
                "by their providers and matched to this station offline.",
                "Values are at the station after interpolation from the model grid, and for "
                "temperature after a height correction. The raw value and the correction flag "
                "are in the matched table.",
                "One station and one initialisation is an anecdote. The scores are the claim.",
            ],
        }

    @app.get("/runs/{run}/report", response_class=PlainTextResponse)
    def report(run: str) -> str:
        d = resolve_run(root, run)
        path = d / "report.md"
        if not path.exists():
            raise HTTPException(status_code=404, detail="this run has no report")
        return path.read_text(encoding="utf-8")

    @app.get("/runs/{run}/figures/{name}")
    def figure(run: str, name: str):
        d = resolve_run(root, run)
        figures = (d / "figures").resolve()
        candidate = (figures / name).resolve()
        if figures not in candidate.parents or not candidate.is_file():
            raise HTTPException(status_code=404, detail="no such figure")
        if candidate.suffix.lower() not in {".png", ".svg"}:
            raise HTTPException(status_code=400, detail="only images are served")
        return FileResponse(candidate)

    return app


app = create_app()
