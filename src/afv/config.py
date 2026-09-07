"""YAML configuration with dotted access and a stable hash for provenance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


class Config(dict):
    """dict with attribute access; nested dicts are wrapped on the fly."""

    def __getattr__(self, key: str) -> Any:
        try:
            v = self[key]
        except KeyError as e:
            raise AttributeError(key) from e
        return Config(v) if isinstance(v, dict) else v

    def get_path(self, key: str, default: str | None = None) -> Path:
        return Path(self["paths"].get(key, default))

    @property
    def hash(self) -> str:
        return config_hash(self)


def load_config(path: str | Path) -> Config:
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    cfg = Config(raw)
    cfg["_source"] = str(path)
    return cfg


def config_hash(cfg: dict) -> str:
    """sha256 of the canonical JSON of the config, without the source path."""
    clean = {k: v for k, v in cfg.items() if not str(k).startswith("_")}
    blob = json.dumps(clean, sort_keys=True, default=str, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def enabled_variables(cfg: Config) -> list[str]:
    return [k for k, v in cfg["variables"].items() if v.get("enabled", True)]
