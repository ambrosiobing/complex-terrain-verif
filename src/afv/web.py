"""The landing page for a locally hosted instance.

Self-contained on purpose: no fonts, scripts or stylesheets from anywhere else. This page
is meant to work on a laptop with the network unplugged, behind a tunnel, or on a machine
that is not allowed to reach the internet at all, and a page that silently needs a content
delivery network fails in exactly those places.

The roadmap below is the single source of truth for what is built and what is not. It is
rendered here, and the same list is what the documentation describes, so the two cannot
drift apart into a page that promises more than the repository does.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Literal

Status = Literal["done", "next", "later"]

STATUS_LABEL = {"done": "built", "next": "next", "later": "later"}


@dataclass(frozen=True)
class Item:
    title: str
    status: Status
    detail: str
    needs: str = ""


#: What exists, what is next, and what is honestly out of reach for now. Keeping the
#: "needs" column filled is what stops "later" from reading as "soon".
ROADMAP: tuple[Item, ...] = (
    Item("Two domains behind one source interface", "done",
         "An observation network is a module and a configuration file, not a second pipeline. "
         "The Alps and the Nordics share every line downstream of the source."),
    Item("Alpine observations from a published dataset", "done",
         "302 Swiss stations, ten-minute values from 2017, with terrain descriptors from a 50 m "
         "elevation model. No credential and no daily harvesting."),
    Item("Refusals that are stated, not silent", "done",
         "A variable the data cannot honestly support is refused by name with the reason. "
         "Station pressure is not mean sea level pressure, so it is not scored against it."),
    Item("Offline fixtures and continuous integration", "done",
         "The whole pipeline runs end to end on synthetic data with no network and no "
         "credentials, which is what makes the tests meaningful to anyone else."),
    Item("One HTTP client for every provider", "done",
         "Retries what can succeed later, refuses what cannot, honours the provider's own "
         "Retry-After, caches by request with a checksum, and logs every attempt so a gap "
         "shows in the report."),
    Item("This service, read only, in a container", "done",
         "Scores, stations, manifests, reports and figures over HTTP. Loopback by default, "
         "non-root, read-only filesystem, every capability dropped."),
    Item("Baseline forecasts computed on request", "done",
         "Persistence and hour-of-day climatology, calculated here from the run's own "
         "observations. Real methods, no weights, and the baselines every score is measured "
         "against."),

    Item("A first real run", "next",
         "Everything on this page has so far only met synthetic data. Until a run against real "
         "forecasts and real observations exists, there is no result here, and the Results "
         "section says so.",
         "a download of one month of observations, and a few hours"),
    Item("Spatial quality control", "next",
         "Buddy checks and a spatial consistency test, so a sensor fault is not scored as a "
         "model error. Rejections named by station in the report, never dropped quietly.",
         "titanlib from conda-forge"),
    Item("Terrain-aware pairing", "next",
         "Elevation gradients and nearest-neighbour-at-the-same-height instead of one fixed "
         "lapse rate, which is wrong in the valley inversion this project exists to measure. "
         "Reported beside the simple correction, never blended into it.",
         "gridpp from conda-forge"),
    Item("Conditioning on circulation", "next",
         "Scores split by blocked and zonal days, so a stable winter inversion is not averaged "
         "together with a convective summer afternoon.",
         "reanalysis geopotential, and a blocking index"),
    Item("The service and the container tested in continuous integration", "done",
         "The service extra is installed in the pipeline, so its tests run rather than skip, "
         "and a guard fails the build if the collected test count drops. A second job builds "
         "the image, runs it against a mounted run, and checks the page renders and reaches "
         "outside for nothing."),
    Item("A scheduled archive", "next",
         "Open forecast data is kept for about four days, so an unattended daily fetch is what "
         "turns a short window into a season.",
         "a machine that stays on"),
    Item("The rest of the Nordic networks", "next",
         "Sweden, Denmark, Finland and Iceland, each confirmed against its own interface before "
         "it is promised rather than after.",
         "one client each"),

    Item("Running a model here", "later",
         "Forecasts of our own rather than the published ones. Deliberately absent: a "
         "verification tool that trained its own model would be scoring itself, and would stop "
         "running on an ordinary laptop.",
         "a GPU with 16 GB or more, and tens of gigabytes of weights"),
    Item("Convective nowcasting", "later",
         "Fifteen to 120 minutes ahead from radar. The architecture is written down; none of it "
         "is implemented.",
         "a radar archive, a GPU, and a training budget"),
    Item("An interactive map", "later",
         "Stations and fields in a browser. The service already returns the JSON such a front "
         "end would consume.",
         "a front-end project"),
    Item("Running many domains at once", "later",
         "Not a bottleneck at this size, and saying so is cheaper than building for it.",
         "a scheduler and a cluster"),
)

CSS = """
*,*::before,*::after{box-sizing:border-box}
body{margin:0;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
color:#1a1a2e;background:#f6f8fa}
a{color:#00558c}
a:hover{color:#003a61}
.wrap{max-width:1040px;margin:0 auto;padding:0 20px 56px}
header{background:#1a1a2e;color:#fff;padding:34px 0 30px;margin-bottom:26px}
header .wrap{padding-bottom:0}
h1{margin:0 0 6px;font-size:26px;letter-spacing:.2px}
.sub{margin:0;color:#aebdd0;font-size:14.5px;max-width:74ch}
.tag{display:inline-block;margin-top:14px;padding:3px 10px;border-radius:999px;font-size:12px;
background:#2f5b85;color:#fff}
h2{font-size:17px;margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid #d8dee6}
p{max-width:78ch}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:18px 0 0}
.card{background:#fff;border:1px solid #e1e6ec;border-radius:8px;padding:13px 15px}
.card .k{font-size:12px;color:#5b6876;text-transform:uppercase;letter-spacing:.6px}
.card .v{font-size:21px;font-weight:600;margin-top:3px}
.card .n{font-size:12px;color:#6b7785;margin-top:2px}
.note{background:#fff8e6;border:1px solid #f0dca8;border-radius:8px;padding:13px 15px;margin:18px 0}
.note strong{color:#7a5c00}
table{border-collapse:collapse;width:100%;margin-top:10px;background:#fff;
border:1px solid #e1e6ec;border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:9px 13px;border-bottom:1px solid #eef1f5;vertical-align:top;font-size:14px}
th{background:#eef2f7;font-weight:600;font-size:12.5px;text-transform:uppercase;letter-spacing:.5px;color:#44505e}
tr:last-child td{border-bottom:0}
code{background:#eef2f7;padding:1px 5px;border-radius:4px;font-size:12.8px;
font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:600;
text-transform:uppercase;letter-spacing:.5px;white-space:nowrap}
.done{background:#dff3e4;color:#1d6b35}
.next{background:#dde9f7;color:#16507f}
.later{background:#ecedf0;color:#5b6876}
.needs{color:#6b7785;font-size:12.6px;display:block;margin-top:3px}
footer{margin-top:40px;padding-top:16px;border-top:1px solid #d8dee6;color:#6b7785;font-size:13px}
.legend{color:#5b6876;font-size:13px;margin:6px 0 0}
nav{position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid #d8dee6;
box-shadow:0 1px 3px rgba(16,24,40,.05)}
nav .wrap{padding:0 20px;display:flex;gap:4px;flex-wrap:wrap;align-items:center}
nav a{display:inline-block;padding:11px 12px;text-decoration:none;font-size:13.5px;
font-weight:500;color:#44505e;border-bottom:2px solid transparent}
nav a:hover{color:#00558c;border-bottom-color:#00558c;background:#f6f8fa}
nav a.ext{margin-left:auto;color:#00558c}
h2{scroll-margin-top:52px}
.btn{display:inline-block;padding:3px 9px;margin:0 4px 3px 0;border-radius:5px;
background:#eef2f7;border:1px solid #dde4ec;color:#00558c;text-decoration:none;font-size:12.6px}
.btn:hover{background:#e3ebf4;border-color:#c2d2e2}
.muted{color:#6b7785;font-size:13px}
"""


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _roadmap_rows(status: Status) -> str:
    rows = []
    for item in (i for i in ROADMAP if i.status == status):
        needs = f'<span class="needs">needs: {_esc(item.needs)}</span>' if item.needs else ""
        rows.append(
            f'<tr><td><span class="pill {item.status}">{STATUS_LABEL[item.status]}</span></td>'
            f"<td><strong>{_esc(item.title)}</strong><br>{_esc(item.detail)}{needs}</td></tr>"
        )
    return "\n".join(rows)


def _try_it(run: str | None, stations: list[str], variables: list[str]) -> str:
    """Ready-made links for the routes that take parameters.

    Without this the page lists ``/runs/{run}/nowcast`` as text and the reader has to
    discover a station identifier and assemble a query string by hand. A landing page that
    requires typing to reach its own contents is a page nobody uses twice.
    """
    if not run or not stations:
        return ('<p class="muted">No run with matched observations yet, so there is nothing to '
                "query. Produce one with <code>afv match</code>.</p>")
    var = _esc(variables[0] if variables else "t2m")
    rows = []
    for sid in stations:
        s = _esc(sid)
        rows.append(
            f"<tr><td><code>{s}</code></td><td>"
            f'<a class="btn" href="/runs/{_esc(run)}/nowcast?station_id={s}&amp;variable={var}&amp;horizon_h=12">'
            f"nowcast, 12 h</a>"
            f'<a class="btn" href="/runs/{_esc(run)}/forecast?station_id={s}&amp;variable={var}">'
            f"what the models said</a>"
            "</td></tr>"
        )
    other = "".join(
        f'<a class="btn" href="/runs/{_esc(run)}/scores?variable={_esc(v)}&amp;metric=mae">'
        f"scores, {_esc(v)}, MAE</a>" for v in variables
    )
    return f"""<p>Everything below is a link. Nothing here needs typing.</p>
<table>
<tr><th style="width:120px">Station</th><th>Computed or retrieved, for <code>{var}</code></th></tr>
{"".join(rows)}
</table>
<p style="margin-top:12px">{other}
<a class="btn" href="/runs/{_esc(run)}/manifest">manifest</a>
<a class="btn" href="/runs/{_esc(run)}/report">report</a></p>"""


def render_home(*, version: str, results_root: str, runs: list[dict], engine: str,
                station_count: int | None = None, only_fixtures: bool = True,
                sample_run: str | None = None, sample_stations: list[str] | None = None,
                sample_variables: list[str] | None = None) -> str:
    """The landing page. Everything it claims comes from the arguments, not from hope."""
    run_rows = "\n".join(
        "<tr><td><code>{name}</code></td><td>{scores}</td><td>{report}</td>"
        '<td><a href="/runs/{name}/scores">scores</a> &middot; '
        '<a href="/runs/{name}/manifest">manifest</a> &middot; '
        '<a href="/runs/{name}/report">report</a></td></tr>'.format(
            name=_esc(r["run"]),
            scores="yes" if r.get("has_scores") else "no",
            report="yes" if r.get("has_report") else "no",
        )
        for r in runs
    ) or '<tr><td colspan="4">No runs yet. Produce one with <code>afv match</code> and <code>afv score</code>.</td></tr>'

    banner = (
        '<div class="note"><strong>Nothing here is a measurement yet.</strong> '
        "The only run present is the synthetic fixture set, which exists so the pipeline can be "
        "exercised end to end with no network and no credentials. Its numbers say nothing about "
        "any real model. A real run states its period, station count and paired-value count "
        "before it states a score.</div>"
        if only_fixtures else
        '<div class="note"><strong>Read the sample size before the score.</strong> '
        "Every table carries its interval and the number of paired values behind it.</div>"
    )

    stations = f"{station_count}" if station_count is not None else "n/a"
    try_it = _try_it(sample_run, sample_stations or [], sample_variables or [])

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>complex-terrain-verif</title>
<style>{CSS}</style></head>
<body>
<header><div class="wrap">
<h1>complex-terrain-verif</h1>
<p class="sub">Station verification of open AI weather forecasts in complex terrain:
the Alps and the Nordics. A 0.25 degree grid cannot see the valley a station stands in,
and this measures the size of that gap.</p>
<span class="tag">local instance &middot; read only &middot; version {_esc(version)}</span>
</div></header>

<nav><div class="wrap">
<a href="#overview">Overview</a>
<a href="#try">Try it</a>
<a href="#runs">Runs</a>
<a href="#routes">Routes</a>
<a href="#roadmap">Roadmap</a>
<a class="ext" href="/docs">Interactive API &rarr;</a>
</div></nav>

<div class="wrap">

<div class="cards">
  <div class="card"><div class="k">Runs</div><div class="v">{len(runs)}</div>
    <div class="n">in {_esc(results_root)}</div></div>
  <div class="card"><div class="k">Stations</div><div class="v">{_esc(stations)}</div>
    <div class="n">in the newest run</div></div>
  <div class="card"><div class="k">Query engine</div><div class="v">{_esc(engine)}</div>
    <div class="n">parquet, read in place</div></div>
  <div class="card"><div class="k">Models run here</div><div class="v">0</div>
    <div class="n">forecasts are retrieved</div></div>
</div>

{banner}

<h2 id="overview">What this instance does</h2>
<p>It serves results that were computed offline, and it computes two baseline forecasts on
request. It does not run a machine-learning model, it fetches nothing, and it has no write
path. The distinction is reported in every prediction response rather than left to be
assumed.</p>

<table>
<tr><th>Method</th><th>Where it comes from</th></tr>
<tr><td><strong>persistence</strong></td><td><span class="pill done">computed here</span>
the last observation at the station, carried forward</td></tr>
<tr><td><strong>hour-of-day climatology</strong></td><td><span class="pill done">computed here</span>
the mean for that station and hour, over the run's own observations</td></tr>
<tr><td><strong>AIFS, IFS and the rest</strong></td><td><span class="pill later">retrieved</span>
produced by their providers, fetched and matched offline</td></tr>
</table>

<h2 id="try">Try it</h2>
{try_it}

<h2 id="runs">Runs on this instance</h2>
<table>
<tr><th>Run</th><th>Scores</th><th>Report</th><th>Open</th></tr>
{run_rows}
</table>

<h2 id="routes">Routes</h2>
<table>
<tr><th>Route</th><th>Returns</th></tr>
<tr><td><a href="/docs"><code>/docs</code></a></td><td>every route, with its parameters, generated from the code</td></tr>
<tr><td><a href="/health"><code>/health</code></a></td><td>version, results root, query engine</td></tr>
<tr><td><a href="/sources"><code>/sources</code></a></td><td>what each observation source serves, and what it refuses and why</td></tr>
<tr><td><a href="/runs"><code>/runs</code></a></td><td>the runs present on this instance</td></tr>
<tr><td><code>/runs/{{run}}/scores</code></td><td>scores, filtered by variable, model, metric, stratum and lead</td></tr>
<tr><td><code>/runs/{{run}}/nowcast</code></td><td><strong>computed here:</strong> persistence and climatology for one station</td></tr>
<tr><td><code>/runs/{{run}}/forecast</code></td><td>what every model predicted for one station, with its provenance</td></tr>
<tr><td><code>/runs/{{run}}/report</code></td><td>the run's written report</td></tr>
</table>

<h2 id="roadmap">Roadmap</h2>
<p class="legend">Three states, and the third is the honest one: <strong>built</strong> is
running now, <strong>next</strong> needs work but no new hardware, and <strong>later</strong>
names what it would take so that it cannot be read as soon.</p>

<table>
<tr><th style="width:84px">State</th><th>Item</th></tr>
{_roadmap_rows("done")}
{_roadmap_rows("next")}
{_roadmap_rows("later")}
</table>

<footer>
Apache-2.0. Data under their providers' terms: ECMWF open data and MET Norway Frost CC BY 4.0,
MeteoSwiss PeakWeather BSD-3-Clause. This instance is read only and runs no model.
<a href="https://github.com/ambrosiobing/complex-terrain-verif">Source</a>.
</footer>

</div></body></html>
"""
