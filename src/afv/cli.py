"""afv command line: stations, elements, fetch, obs, match, score, diag, report, diff, fixtures, info."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import __version__
from .config import enabled_variables, load_config


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def cmd_info(args):
    print(f"afv {__version__}")
    print(f"python {sys.version.split()[0]}")
    for mod in ("numpy", "pandas", "xarray", "scipy", "matplotlib", "pyarrow", "netCDF4"):
        try:
            m = __import__(mod)
            print(f"{mod} {getattr(m, '__version__', '?')}")
        except ImportError:
            print(f"{mod} missing")
    for opt in ("cfgrib", "eccodes", "earthkit.data", "ecmwf.opendata", "verif", "anemoi.inference"):
        try:
            __import__(opt)
            print(f"{opt} available")
        except Exception:
            print(f"{opt} not installed (optional)")
    return 0


def cmd_fixtures(args):
    from .fixtures import make_fixtures
    paths = make_fixtures(args.out)
    for k, v in paths.items():
        print(f"{k}: {v}")
    return 0


def cmd_sources(args):
    """What each domain can serve, and what it refuses and why."""
    from .sources import available, get_source
    for name in available():
        print(f"== {name}")
        try:
            mod = get_source(name)
        except Exception as e:
            print(f"   unavailable: {e}")
            continue
        params = getattr(mod, "PARAMETERS", None)
        if params is None:
            print(f"   serves: {', '.join(getattr(mod, 'VARIABLES', ()))}")
            continue
        served = [k for k, v in params.items() if v is not None]
        refused = [k for k, v in params.items() if v is None]
        print(f"   serves: {', '.join(served)}")
        for var in refused:
            why = getattr(mod, "NOT_MAPPED_REASON", {}).get(var, "not mapped")
            print(f"   refuses {var}: {why}")
    return 0


def cmd_serve(args):
    """Serve results that already exist. Computes nothing, writes nothing."""
    try:
        import uvicorn
    except ImportError:
        print("the server needs the 'server' extra: pip install -e '.[server]'", file=sys.stderr)
        return 2
    import os
    import socket

    from .server import create_app
    root = Path(args.root)
    if not root.is_dir():
        print(f"no results directory at {root}; run `afv score` first", file=sys.stderr)
        return 2

    # Refuse to start on a port something else already holds, and say what to do. Without
    # this the second server fails to bind, exits into the scrollback, and the browser
    # keeps talking to the first one, which may be running older code: the page looks
    # broken while the file on disk is correct.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        if probe.connect_ex((args.host, args.port)) == 0:
            print(f"something is already listening on {args.host}:{args.port}.\n"
                  f"Stop it first, or use --port, otherwise the browser keeps talking to it:\n"
                  f"  Stop-Process -Id (Get-NetTCPConnection -LocalPort {args.port} "
                  f"-State Listen).OwningProcess -Force", file=sys.stderr)
            return 2

    print(f"serving {root.resolve()} on http://{args.host}:{args.port} (read only)")
    if args.reload:
        # Reload needs an import string rather than an instance, so the root travels in
        # the environment, which is where the application reads it from by default.
        os.environ["AFV_RESULTS_ROOT"] = str(root.resolve())
        uvicorn.run("afv.server:app", host=args.host, port=args.port,
                    reload=True, reload_dirs=[str(Path(__file__).parent)], log_level="info")
    else:
        uvicorn.run(create_app(root), host=args.host, port=args.port, log_level="info")
    return 0


def _source_name(cfg, args) -> str:
    return getattr(args, "source", None) or cfg.get("source", "frost")


def cmd_stations(args):
    _load_env()
    from .fetch_obs import ELEMENTS
    from .stations import available_series, fetch_sources, freeze, freeze_all, select_stations
    cfg = load_config(args.config)
    st = cfg["stations"]

    name = _source_name(cfg, args)
    if name != "frost":
        from .sources import get_source
        mod = get_source(name)
        sel = mod.stations(cfg.get_path("cache_dir"),
                           mountain_min_elev_m=float(st["classes"]["mountain_min_elev_m"]),
                           bbox=st.get("bbox"), max_n=st.get("max_n"),
                           station_types=st.get("station_types"))
        out = freeze_all(sel, cfg.get_path("stations_csv"))
        print(f"{len(sel)} stations from {name} frozen to {out}")
        cols = [c for c in ("station_id", "name", "lat", "lon", "elev_m", "station_class") if c in sel.columns]
        print(sel[cols].to_string(index=False))
        return 0

    src = fetch_sources(None, counties=st.get("counties"), bbox=st.get("bbox"))
    print(f"{len(src)} SensorSystem sources returned")
    elements = [ELEMENTS[v]["elementId"] for v in enabled_variables(cfg) if v in ELEMENTS]
    req = available_series(None, src["station_id"].tolist(), elements) if not args.no_quality else None
    sel = select_stations(src, float(st["min_lat"]), st.get("bbox"), int(st["max_n"]), required=req,
                          exposure_max=st.get("exposure_max"), performance_max=st.get("performance_max"),
                          mountain_min_elev_m=float(st["classes"]["mountain_min_elev_m"]))
    out = freeze(sel, cfg.get_path("stations_csv"))
    print(f"{len(sel)} stations frozen to {out}")
    print(sel.to_string(index=False))
    return 0


def cmd_elements(args):
    _load_env()
    from .fetch_obs import ELEMENTS, resolve_elements
    df = resolve_elements(None, args.ids or None)
    print(df.to_string(index=False))
    missing = [v["elementId"] for v in ELEMENTS.values() if v["elementId"] not in set(df["id"])]
    if missing:
        print(f"NOT FOUND in /elements: {missing}  -> fix ELEMENTS in src/afv/fetch_obs.py")
    return 0


def cmd_fetch(args):
    _load_env()
    from .fetch_forecast import fetch_and_standardise, fetch_run, latest_available, runs_in_window
    cfg = load_config(args.config)
    runs = cfg["runs"]
    steps = list(runs["lead_hours"])
    models = args.model or list(cfg["models"])
    if args.latest:
        inits = [latest_available(models[0])]
    else:
        inits = runs_in_window(runs["window_start"], runs["window_end"], list(runs["init_hours"]))
    now = datetime.now(timezone.utc)
    done = []
    for init in inits:
        if init > now:
            continue
        for model in models:
            try:
                if args.download_only:
                    p = fetch_run(model, init, steps, cfg.get_path("cache_dir"))
                else:
                    p = fetch_and_standardise(model, init, steps, cfg.get_path("cache_dir"),
                                              cfg.get_path("fields_dir"), cfg["stations"].get("bbox"))
                done.append(str(p))
                print(f"ok {model} {init:%Y-%m-%dT%HZ} -> {p}")
            except Exception as e:  # keep going; open data has a short retention window
                print(f"skip {model} {init:%Y-%m-%dT%HZ}: {e}")
    print(f"{len(done)} files")
    return 0


def cmd_obs(args):
    _load_env()
    from .fetch_obs import fetch_observations, save_obs
    from .stations import load_frozen
    cfg = load_config(args.config)
    stations = load_frozen(cfg.get_path("stations_csv"))
    variables = [v for v in enabled_variables(cfg)]
    variables = ["tp1h" if v == "tp24" else v for v in variables]
    runs = cfg["runs"]
    t0 = pd.Timestamp(runs["window_start"], tz="UTC") - pd.Timedelta(days=1)
    t1 = pd.Timestamp(runs["window_end"], tz="UTC") + pd.Timedelta(hours=max(runs["lead_hours"]) + 24)

    name = _source_name(cfg, args)
    if name != "frost":
        from .sources import get_source
        obs = get_source(name).observations(cfg.get_path("cache_dir"), stations["station_id"].tolist(),
                                            variables, t0, t1)
    else:
        obs = fetch_observations(None, stations["station_id"].tolist(), variables, t0, t1, cfg.get_path("cache_dir"),
                                 qc_accept=set(cfg["qc"]["accept_codes"]))
    out = save_obs(obs, cfg.get_path("obs_dir") / "obs.parquet")
    print(f"{len(obs)} observation rows ({int(obs.get('qc_accepted', pd.Series(dtype=bool)).sum())} with accepted qc) -> {out}")
    return 0


def _load_inputs(cfg):
    from .stations import load_frozen
    stations = load_frozen(cfg.get_path("stations_csv"))
    obs = pd.read_parquet(cfg.get_path("obs_dir") / "obs.parquet")
    accept = set(cfg["qc"]["accept_codes"])
    obs = obs[obs["qc"].isin(accept)]
    fields = sorted(Path(cfg.get_path("fields_dir")).glob("*.nc"))
    return stations, obs, fields


def cmd_match(args):
    from .baselines import climatology, persistence
    from .dataset import write_dataset
    from .match import match_all, pairwise_complete
    cfg = load_config(args.config)
    stations, obs, fields = _load_inputs(cfg)
    if not fields:
        print("no field files; run `afv fetch` or `afv fixtures`", file=sys.stderr)
        return 2
    matched = match_all(fields, stations, obs, cfg)
    if matched.empty:
        print("nothing matched", file=sys.stderr)
        return 2
    parts = [matched]
    if cfg["baselines"].get("persistence"):
        parts.append(persistence(matched, obs))
    if cfg["baselines"].get("climatology"):
        parts.append(climatology(matched, obs))
    full = pd.concat(parts, ignore_index=True)
    if cfg["missing"].get("pairwise_complete", True):
        full = pairwise_complete(full, cfg["models"])
    out_dir = cfg.get_path("results_dir")
    man = write_dataset(full, out_dir / "matched.parquet", cfg)
    print(json.dumps(man, indent=2))
    return 0


def cmd_score(args):
    from .dataset import read_dataset, read_manifest
    from .scores import score_table, threshold_scores
    cfg = load_config(args.config)
    out_dir = cfg.get_path("results_dir")
    matched = read_dataset(out_dir / "matched.parquet")
    man = read_manifest(out_dir / "matched.parquet")
    st = score_table(matched, cfg, dataset_hash=man.get("dataset_hash", ""))
    st.to_parquet(out_dir / "scores.parquet")
    st.to_jsonl(out_dir / "scores.jsonl")
    for model, rows in st.rows.groupby("model"):
        sub = type(st)(rows=rows.reset_index(drop=True), dataset_hash=st.dataset_hash, config_hash=st.config_hash, afv_version=st.afv_version)
        sub.to_parquet(out_dir / f"scores_{model}.parquet")
    if "tp24" in set(matched["variable"]):
        thr = threshold_scores(matched[matched["variable"] == "tp24"], list(cfg["thresholds_mm_24h"]))
        thr.to_csv(out_dir / "threshold_scores_tp24.csv", index=False)
    show = st.rows[(st.rows["stratum"] == "all") & (st.rows["metric"] == "mae")]
    print("MAE by lead (primary interpolation, all stations):")
    print(show.pivot_table(index=["variable", "lead_h"], columns=["model"], values="value", aggfunc="first").round(3).to_string())
    print(f"dataset {st.dataset_hash} config {st.config_hash} -> {out_dir / 'scores.parquet'}")
    return 0


def cmd_diag(args):
    from .dataset import read_dataset
    from .diagnostics import activity_vs_lead, qq, tail_conditional, variance_ratio
    cfg = load_config(args.config)
    out_dir = cfg.get_path("results_dir")
    matched = read_dataset(out_dir / "matched.parquet")
    primary = cfg["interp"].get("primary", {})
    vr = variance_ratio(matched, primary)
    vr.to_csv(out_dir / "diag_variance_ratio.csv", index=False)
    tail = tail_conditional(matched, float(cfg["diagnostics"]["tail_quantile"]))
    tail.to_csv(out_dir / "diag_tail.csv", index=False)
    fields = sorted(Path(cfg.get_path("fields_dir")).glob("*.nc"))
    act = activity_vs_lead(fields, cfg["diagnostics"].get("activity_variable", "t2m"))
    act.to_csv(out_dir / "diag_activity.csv", index=False)
    qqs = []
    for (model, var), _ in matched.groupby(["model", "variable"]):
        q = qq(matched, model, var, interp=primary.get(var))
        q.to_csv(out_dir / f"diag_qq_{model}_{var}.csv", index=False)
        qqs.append(q)
    print(vr.round(3).to_string(index=False))
    return 0


def cmd_report(args):
    from .dataset import read_dataset, read_manifest
    from .diagnostics import qq
    from .report import render_report
    from .schema import ScoreTable
    cfg = load_config(args.config)
    out_dir = cfg.get_path("results_dir")
    st = ScoreTable.from_parquet(out_dir / "scores.parquet")
    matched = read_dataset(out_dir / "matched.parquet")
    man = read_manifest(out_dir / "matched.parquet")
    primary = cfg["interp"].get("primary", {})
    diag = {}
    for name in ("variance_ratio", "tail", "activity"):
        p = out_dir / f"diag_{name}.csv"
        diag[name] = pd.read_csv(p) if p.exists() else None
    diag["qq"] = [qq(matched, m, v, interp=primary.get(v)) for (m, v), _ in matched.groupby(["model", "variable"])]
    out = render_report(st, diag, out_dir, cfg, man)
    print(out)
    return 0


def cmd_diff(args):
    from .diff import diff_tables, summarise
    from .schema import ScoreTable
    a = ScoreTable.from_parquet(args.a)
    b = ScoreTable.from_parquet(args.b)
    la = a.rows["model"].iloc[0] if not a.rows.empty else "A"
    lb = b.rows["model"].iloc[0] if not b.rows.empty else "B"
    d = diff_tables(a, b, la, lb)
    print(summarise(d, la, lb))
    if args.out:
        d.to_csv(args.out, index=False)
        print(f"-> {args.out}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="afv", description="Station verification of open AI weather forecasts, Bergen northwards")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="versions and optional dependencies").set_defaults(fn=cmd_info)
    s = sub.add_parser("fixtures", help="write synthetic fixtures for offline runs"); s.add_argument("--out", default="data/fixtures"); s.set_defaults(fn=cmd_fixtures)
    sub.add_parser("sources", help="observation sources, what each serves and what it refuses").set_defaults(fn=cmd_sources)
    s = sub.add_parser("serve", help="read-only service over an existing results directory"); s.add_argument("--root", default="results"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8000); s.add_argument("--reload", action="store_true", help="restart on source changes, for development"); s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("stations", help="select and freeze stations (network)"); s.add_argument("--config", default="configs/default.yaml"); s.add_argument("--source"); s.add_argument("--no-quality", action="store_true"); s.set_defaults(fn=cmd_stations)
    s = sub.add_parser("elements", help="resolve Frost element ids (network)"); s.add_argument("ids", nargs="*"); s.set_defaults(fn=cmd_elements)
    s = sub.add_parser("fetch", help="download and standardise ECMWF open data (network)"); s.add_argument("--config", default="configs/default.yaml"); s.add_argument("--model", action="append"); s.add_argument("--latest", action="store_true"); s.add_argument("--download-only", action="store_true"); s.set_defaults(fn=cmd_fetch)
    s = sub.add_parser("obs", help="download observations (network)"); s.add_argument("--config", default="configs/default.yaml"); s.add_argument("--source"); s.set_defaults(fn=cmd_obs)
    s = sub.add_parser("match", help="build the matched ML-ready dataset"); s.add_argument("--config", default="configs/default.yaml"); s.set_defaults(fn=cmd_match)
    s = sub.add_parser("score", help="score the matched dataset"); s.add_argument("--config", default="configs/default.yaml"); s.set_defaults(fn=cmd_score)
    s = sub.add_parser("diag", help="realism diagnostics"); s.add_argument("--config", default="configs/default.yaml"); s.set_defaults(fn=cmd_diag)
    s = sub.add_parser("report", help="markdown report with figures"); s.add_argument("--config", default="configs/default.yaml"); s.set_defaults(fn=cmd_report)
    s = sub.add_parser("diff", help="compare two score tables"); s.add_argument("a"); s.add_argument("b"); s.add_argument("--out"); s.set_defaults(fn=cmd_diff)

    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
