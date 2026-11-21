"""Full race analysis: ties together derive, legs, maneuvers and start, and builds summaries."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import derive, legs as legs_mod, maneuvers as man_mod, phases, start as start_mod

WIND_BANDS = [0, 6, 9, 12, 16, 50]
WIND_LABELS = ["<6", "6-9", "9-12", "12-16", "16+"]

TRACK_COLUMNS = ["lat", "lon", "bsp", "sog", "cog", "hdg", "tws", "twd", "twa", "aws", "awa",
                 "heel", "rudder", "vmg", "polar_pct", "vmg_pct", "target_bsp", "target_twa",
                 "depth", "engine_rpm"]


def _ts(t):
    if t is None or t == "":
        return None
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def analyze_race(raw: pd.DataFrame, polar, acfg: dict, gun=None, finish=None, line=None) -> dict:
    """Analyse one race window.

    gun / finish: times set by the crew. When missing they are estimated from the data
    (see phases.py). Only the part between gun and finish, and not motoring, counts towards
    the summary, legs, wind bands and maneuver averages.
    """
    df = derive.enrich(raw, polar, acfg)
    est = phases.pick_race(phases.find_races(df, acfg), df.index[0], df.index[-1])
    gun_ts, finish_ts = _ts(gun), _ts(finish)
    info = {
        "gun": gun_ts or (est["gun"] if est else None),
        "gun_source": "set" if gun_ts else ("estimated" if est else None),
        "finish": finish_ts or (est["finish"] if est else None),
        "finish_source": "set" if finish_ts else ("estimated" if est else None),
        "estimated_gun": est["gun"] if est else None,
        "estimated_finish": est["finish"] if est else None,
        "motoring_source": df.attrs.get("motoring_source"),
        "motoring_s": int(df["motoring"].sum()),
    }
    g = info["gun"] or df.index[0]
    f = info["finish"] or df.index[-1]
    phase = np.where(df.index < g, "pre", np.where(df.index > f, "post", "race"))
    df["phase"] = phase
    racing = df[(df["phase"] == "race") & ~df["motoring"]]

    mans = man_mod.detect(df, acfg)
    for m in mans:
        m["phase"] = "pre" if m["time"] < g else "post" if m["time"] > f else "race"
        m["prestart"] = m["phase"] != "race"  # shown greyed / hidden by default
    racing_mans = [m for m in mans if m["phase"] == "race"]

    race_df = df.loc[g:f]
    legs = legs_mod.segment(race_df, int(acfg.get("min_leg_s", 90)))
    start = None
    if info["gun"] is not None:
        pin = tuple(line["pin"]) if line and line.get("pin") else None
        boat = tuple(line["boat"]) if line and line.get("boat") else None
        start = start_mod.analyze(df, info["gun"], pin, boat, polar)
    return {
        "phases": info,
        "summary": summary(racing),
        "legs": legs_mod.leg_stats(race_df, legs, racing_mans),
        "maneuvers": [_jsonable(m) for m in mans],
        "maneuver_summary": man_mod.summarize(racing_mans),
        "bands": wind_bands(racing),
        "start": start,
        "track": track_payload(df),
    }


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    dur = (df.index[-1] - df.index[0]).total_seconds()

    def m(col, sub=None):
        s = (df if sub is None else df[df["mode"] == sub])[col]
        v = s.mean()
        return None if pd.isna(v) else round(float(v), 2)

    share = df["mode"].value_counts(normalize=True).round(3).to_dict()
    return {
        "start": df.index[0].isoformat(), "end": df.index[-1].isoformat(),
        "duration_s": dur,
        "distance_nm": round(float(df["dist_m"].sum() / 1852), 2) if "dist_m" in df else None,
        "avg_tws": m("tws"), "max_tws": _r(df["tws"].max()), "avg_bsp": m("bsp"),
        "max_bsp": _r(df["bsp"].max()), "avg_polar_pct": m("polar_pct"),
        "upwind_vmg_pct": m("vmg_pct", "upwind"), "downwind_vmg_pct": m("vmg_pct", "downwind"),
        "reach_polar_pct": m("polar_pct", "reach"),
        "upwind_twa_delta": m("twa_delta", "upwind"), "downwind_twa_delta": m("twa_delta", "downwind"),
        "mode_share": share,
    }


def wind_bands(df: pd.DataFrame) -> list[dict]:
    """Average performance per wind band and mode - shows where the boat is fast or slow."""
    d = df.dropna(subset=["tws", "mode"]).copy()
    if d.empty:
        return []
    d["band"] = pd.cut(d["tws"], WIND_BANDS, labels=WIND_LABELS, right=False)
    d["abs_twa"] = d["twa"].abs()
    g = d.groupby(["mode", "band"], observed=True)
    t = g.agg(seconds=("bsp", "size"), bsp=("bsp", "mean"), target_bsp=("target_bsp", "mean"),
              polar_pct=("polar_pct", "mean"), vmg=("vmg", lambda s: s.abs().mean()),
              target_vmg=("target_vmg", "mean"), vmg_pct=("vmg_pct", "mean"),
              twa=("abs_twa", "mean"), target_twa=("target_twa", "mean"),
              heel=("heel", lambda s: s.abs().mean())).reset_index()
    t = t[t["seconds"] >= 30]
    t["band"] = t["band"].astype(str)
    return t.round(2).replace({np.nan: None}).to_dict("records")


def track_payload(df: pd.DataFrame, max_points: int = 20000) -> dict:
    """Column-oriented arrays for the browser (map + charts)."""
    step = max(1, int(np.ceil(len(df) / max_points)))
    d = df.iloc[::step]
    # epoch milliseconds, independent of the index's internal resolution (ns / us / s)
    out = {"t": d.index.as_unit("ms").asi8.tolist(), "step_s": step}
    for c in TRACK_COLUMNS:
        if c in d:
            vals = d[c].astype(float).round(5 if c in ("lat", "lon") else 2)
            out[c] = [None if pd.isna(v) else v for v in vals.tolist()]
    out["mode"] = [m if isinstance(m, str) else None for m in d["mode"].tolist()]
    if "phase" in d:
        out["phase"] = d["phase"].tolist()
    return out


def _r(v):
    return None if pd.isna(v) else round(float(v), 2)


def _jsonable(m: dict) -> dict:
    out = dict(m)
    out["time"] = m["time"].isoformat()
    return out
