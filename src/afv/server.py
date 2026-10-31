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
    from fastapi.responses import FileResponse, PlainTextResponse
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


# Application -------------------------------------------------------------------------------

def create_app(root: Path | str = DEFAULT_ROOT) -> FastAPI:
    root = Path(root)
    app = FastAPI(
        title="complex-terrain-verif",
        version=__version__,
        description="Read-only access to station verification results. Computes nothing.",
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
        if not root.is_dir():
            return []
        out = []
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            out.append({
                "run": d.name,
                "has_scores": (d / "scores.parquet").exists(),
                "has_matched": (d / "matched.parquet").exists(),
                "has_report": (d / "report.md").exists(),
            })
        return out

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
