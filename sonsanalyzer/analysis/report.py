"""Full race analysis: ties together derive, legs, maneuvers and start, and builds summaries."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import derive, legs as legs_mod, maneuvers as man_mod, start as start_mod

WIND_BANDS = [0, 6, 9, 12, 16, 50]
WIND_LABELS = ["<6", "6-9", "9-12", "12-16", "16+"]

TRACK_COLUMNS = ["lat", "lon", "bsp", "sog", "cog", "hdg", "tws", "twd", "twa", "aws", "awa",
                 "heel", "rudder", "vmg", "polar_pct", "vmg_pct", "target_bsp", "target_twa",
                 "depth"]


def analyze_race(raw: pd.DataFrame, polar, acfg: dict, gun=None, line=None) -> dict:
    df = derive.enrich(raw, polar, acfg)
    mans = man_mod.detect(df, acfg)
    for m in mans:
        m["prestart"] = bool(gun is not None and m["time"] < pd.Timestamp(gun))
    racing_mans = [m for m in mans if not m["prestart"]]
    legs = legs_mod.segment(df, int(acfg.get("min_leg_s", 90)))
    start = None
    if gun is not None and line and line.get("pin") and line.get("boat"):
        start = start_mod.analyze(df, pd.Timestamp(gun), tuple(line["pin"]), tuple(line["boat"]), polar)
    racing = df.loc[pd.Timestamp(gun):] if gun is not None else df
    return {
        "summary": summary(racing),
        "legs": legs_mod.leg_stats(df, legs, racing_mans),
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
    return out


def _r(v):
    return None if pd.isna(v) else round(float(v), 2)


def _jsonable(m: dict) -> dict:
    out = dict(m)
    out["time"] = m["time"].isoformat()
    return out
