"""Observation sources, one per national network, behind a single interface.

The pipeline downstream of this package (match, score, diagnostics, report) does not know
which country an observation came from. It knows the two tables in ``afv.schema``:
``STATION_COLUMNS`` and ``OBS_COLUMNS``. A source has one responsibility: produce those
two tables from whatever shape its provider happens to serve.

Adding a country is therefore a module here plus a row in ``SOURCES``, not a second
pipeline.
"""
from __future__ import annotations

from typing import Callable

SourceLoader = Callable[[], object]


class SourceError(RuntimeError):
    """A source could not produce its tables, with the reason stated."""


def _load_peakweather():
    from . import peakweather
    return peakweather


def _load_frost():
    from . import frost
    return frost


#: name -> loader. Loaders are lazy because most sources carry an optional dependency.
SOURCES: dict[str, SourceLoader] = {
    "peakweather": _load_peakweather,   # Switzerland, SwissMetNet, via the MeteoSwiss dataset
    "frost": _load_frost,               # Norway, MET Norway
}


def get_source(name: str):
    """Return the module implementing ``name``, or raise naming what is available."""
    try:
        return SOURCES[name]()
    except KeyError:
        raise SourceError(f"unknown source {name!r}; available: {sorted(SOURCES)}") from None


def available() -> list[str]:
    return sorted(SOURCES)
