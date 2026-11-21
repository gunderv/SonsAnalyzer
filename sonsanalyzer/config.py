"""Configuration loading: defaults merged with an optional config.toml."""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "app": {"source": "influx", "host": "0.0.0.0", "port": 8080, "data_dir": "data"},
    "boat": {"name": "Sons of Hurricanes", "sail_no": "NOR 15666"},
    "influx": {
        "host": "localhost",
        "port": 8086,
        "database": "boatdata",
        "username": "",
        "password": "",
        "extra_where": "",
    },
    "analysis": {
        "smoothing_s": 3,
        "upwind_max_twa": 70,
        "downwind_min_twa": 110,
        "hysteresis_deg": 5,
        "min_leg_s": 90,
        "maneuver_window_before_s": 10,
        "maneuver_window_after_s": 30,
        "session_min_sog_kn": 2.0,
        "session_min_minutes": 15,
        "session_max_gap_minutes": 10,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | os.PathLike | None = None) -> dict:
    """Load config. Path order: argument, $SONSANALYZER_CONFIG, ./config.toml in project root."""
    candidates = [path, os.environ.get("SONSANALYZER_CONFIG"), PROJECT_ROOT / "config.toml"]
    cfg = copy.deepcopy(DEFAULTS)
    for cand in candidates:
        if cand and Path(cand).is_file():
            with open(cand, "rb") as fh:
                cfg = _merge(cfg, tomllib.load(fh))
            break
    if os.environ.get("SONSANALYZER_SOURCE"):
        cfg["app"]["source"] = os.environ["SONSANALYZER_SOURCE"]
    data_dir = Path(cfg["app"]["data_dir"])
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg["app"]["data_dir"] = str(data_dir)
    return cfg
