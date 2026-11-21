"""Split a race into legs by sailing mode (upwind / reach / downwind)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _runs(labels: list) -> list[list]:
    """Run-length encode -> [[label, start_idx, end_idx_exclusive], ...]."""
    runs: list[list] = []
    for i, lab in enumerate(labels):
        if runs and runs[-1][0] == lab:
            runs[-1][2] = i + 1
        else:
            runs.append([lab, i, i + 1])
    return runs


def segment(df: pd.DataFrame, min_leg_s: int = 90) -> list[dict]:
    """Return legs as dicts with start/end timestamps and integer positions.

    Runs shorter than `min_leg_s` (e.g. a brief bear-away) are absorbed into the longer
    neighbour until every leg is at least that long.
    """
    modes = df["mode"].ffill().bfill().tolist()
    if not modes or modes[0] is None:
        return []
    runs = _runs(modes)
    while len(runs) > 1:
        lengths = [r[2] - r[1] for r in runs]
        i = int(np.argmin(lengths))
        if lengths[i] >= min_leg_s:
            break
        if i == 0:
            j = 1
        elif i == len(runs) - 1:
            j = i - 1
        else:
            j = i - 1 if lengths[i - 1] >= lengths[i + 1] else i + 1
        lo, hi = min(i, j), max(i, j)
        merged = [runs[j][0], runs[lo][1], runs[hi][2]]
        runs[lo:hi + 1] = [merged]
        # merge equal neighbours created by the absorption
        k = 0
        while k < len(runs) - 1:
            if runs[k][0] == runs[k + 1][0]:
                runs[k:k + 2] = [[runs[k][0], runs[k][1], runs[k + 1][2]]]
            else:
                k += 1
    idx = df.index
    return [{"n": n + 1, "mode": lab, "i0": a, "i1": b,
             "start": idx[a], "end": idx[b - 1]} for n, (lab, a, b) in enumerate(runs)]


def leg_stats(df: pd.DataFrame, legs: list[dict], maneuvers: list[dict] | None = None) -> list[dict]:
    out = []
    for leg in legs:
        d = df.iloc[leg["i0"]:leg["i1"]]
        dur = (leg["end"] - leg["start"]).total_seconds() + 1
        n_man = 0
        if maneuvers:
            n_man = sum(1 for m in maneuvers if leg["start"] <= m["time"] <= leg["end"])
        # distance made good along the wind axis (m)
        made_good = np.nan
        if d["x"].notna().sum() > 1 and d["twd"].notna().any():
            twd = np.deg2rad(_circ_mean(d["twd"]))
            dx = d["x"].dropna().iloc[-1] - d["x"].dropna().iloc[0]
            dy = d["y"].dropna().iloc[-1] - d["y"].dropna().iloc[0]
            made_good = abs(dx * np.sin(twd) + dy * np.cos(twd))
        out.append({
            "n": leg["n"], "mode": leg["mode"],
            "start": leg["start"].isoformat(), "end": leg["end"].isoformat(),
            "duration_s": dur,
            "distance_nm": float(d["dist_m"].sum() / 1852) if "dist_m" in d else None,
            "made_good_nm": float(made_good / 1852) if not np.isnan(made_good) else None,
            "avg_tws": _mean(d["tws"]), "avg_twd": _circ_mean(d["twd"]),
            "avg_bsp": _mean(d["bsp"]), "avg_sog": _mean(d["sog"]),
            "avg_abs_twa": _mean(d["twa"].abs()), "avg_vmg": _mean(d["vmg"].abs()),
            "avg_polar_pct": _mean(d["polar_pct"]), "avg_vmg_pct": _mean(d["vmg_pct"]),
            "avg_twa_delta": _mean(d["twa_delta"]),
            "avg_heel": _mean(d["heel"].abs()), "maneuvers": n_man,
        })
    return out


def _mean(s: pd.Series):
    v = s.mean()
    return None if pd.isna(v) else round(float(v), 2)


def _circ_mean(s: pd.Series):
    s = s.dropna()
    if s.empty:
        return None
    r = np.deg2rad(s)
    return round(float(np.rad2deg(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360), 1)
