"""Detect tacks and gybes and measure what they cost.

Detection: the true wind angle changes sign (wind moves to the other side of the boat).
Close to 0 deg it is a tack, close to 180 deg it is a gybe. Each candidate is checked against
the steady state before and after, so wobbling dead downwind or luffing head-to-wind is
not counted.

Loss: over a fixed window (default 10 s before to 30 s after the moment the bow/stern passes
through the wind) we compare the distance the boat actually gained along the wind axis with
what it would have gained continuing at its pre-maneuver VMG. Both use GPS (over ground),
so current largely cancels.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.schema import wrap180

KN_TO_MS = 1852.0 / 3600.0


def _circ_mean_deg(a: np.ndarray) -> float:
    a = a[~np.isnan(a)]
    if a.size == 0:
        return np.nan
    r = np.deg2rad(a)
    return float(np.rad2deg(np.arctan2(np.sin(r).mean(), np.cos(r).mean())))


def detect(df: pd.DataFrame, acfg: dict) -> list[dict]:
    before = int(acfg.get("maneuver_window_before_s", 10))
    after = int(acfg.get("maneuver_window_after_s", 30))
    twa = df["twa"].to_numpy(dtype=float)
    hdg = df["hdg"].to_numpy(dtype=float)
    bsp = df["bsp"].to_numpy(dtype=float)
    n = len(df)
    if n < 60:
        return []

    sign = np.sign(twa)
    flips = np.where((sign[1:] * sign[:-1]) < 0)[0] + 1
    events = []
    last_t = -10**9
    for i in flips:
        if i - last_t < 20:  # one maneuver can wobble across the wind several times
            continue
        pre = slice(max(0, i - 25), max(0, i - 10))
        post = slice(min(n, i + 15), min(n, i + 30))
        pre_twa = twa[pre]
        post_twa = twa[post]
        if np.isnan(pre_twa).all() or np.isnan(post_twa).all():
            continue
        pre_med = np.nanmedian(pre_twa)
        post_med = np.nanmedian(post_twa)
        if np.sign(pre_med) == np.sign(post_med):
            continue  # came back to the same side - not a maneuver
        if abs(pre_med) < 90 and abs(post_med) < 90:
            kind = "tack"
        elif abs(pre_med) > 90 and abs(post_med) > 90:
            kind = "gybe"
        else:
            continue  # e.g. rounding a mark from upwind to downwind - handled as leg change
        turn = abs(wrap180(_circ_mean_deg(hdg[post]) - _circ_mean_deg(hdg[pre])))
        # deep downwind gybes (TWA ~170) turn only ~20 deg, tacks turn 70-100 deg
        if np.isnan(turn) or turn < (40 if kind == "tack" else 10):
            continue
        if np.nanmedian(bsp[pre]) < 2.0:
            continue  # pre-start / drifting
        events.append(_measure(df, i, kind, pre_med, post_med, turn, before, after))
        last_t = i
    return [e for e in events if e is not None]


def _measure(df, i, kind, pre_twa, post_twa, turn, before, after) -> dict:
    n = len(df)
    t = df.index
    bsp = df["bsp"].to_numpy(float)
    vmg = np.abs(df["vmg"].to_numpy(float))
    x = df["x"].to_numpy(float)
    y = df["y"].to_numpy(float)
    twd = df["twd"].to_numpy(float)
    tws = df["tws"].to_numpy(float)

    pre = slice(max(0, i - 25), max(0, i - 10))
    entry_bsp = float(np.nanmean(bsp[pre]))
    entry_vmg = float(np.nanmean(vmg[pre]))
    w0, w1 = max(0, i - before), min(n - 1, i + after)
    min_bsp = float(np.nanmin(bsp[w0:w1 + 1]))
    min_bsp_t = int(np.nanargmin(bsp[w0:w1 + 1])) + w0

    # Recovery: first time after the turn when VMG is back to 90 % of entry VMG (max 90 s)
    # (head to wind VMG is momentarily inflated, so start once the boat is on its new course)
    twa = df["twa"].to_numpy(float)
    k0 = next((k for k in range(i, min(n, i + 60))
               if not np.isnan(twa[k]) and abs(wrap180(twa[k] - post_twa)) < 10), i)
    recover_s = None
    for k in range(k0, min(n, i + 90)):
        if not np.isnan(vmg[k]) and vmg[k] >= 0.9 * entry_vmg:
            recover_s = int(k - i)
            break

    loss_m = None
    wind_dir = _circ_mean_deg(twd[max(0, i - 25):min(n, i + 30)])
    if not np.isnan(wind_dir) and not np.isnan(x[[w0, w1]]).any():
        ux, uy = np.sin(np.deg2rad(wind_dir)), np.cos(np.deg2rad(wind_dir))
        sgn = 1.0 if kind == "tack" else -1.0  # tacks gain upwind, gybes gain downwind

        def along(a, b):
            return sgn * ((x[b] - x[a]) * ux + (y[b] - y[a]) * uy)

        a, b = max(0, i - 25), max(0, i - 10)
        ground_vmg = along(a, b) / max(1, b - a)  # m/s from GPS before the maneuver
        if np.isnan(ground_vmg) or ground_vmg <= 0:
            ground_vmg = entry_vmg * KN_TO_MS
        expected = ground_vmg * (w1 - w0)
        loss_m = round(float(expected - along(w0, w1)), 1)

    pre_side = "stb" if pre_twa > 0 else "port"
    return {
        "time": t[i], "index": int(i), "kind": kind,
        "direction": f"{pre_side}->{'port' if pre_side == 'stb' else 'stb'}",
        "tws": round(float(np.nanmean(tws[pre])), 1),
        "entry_bsp": round(entry_bsp, 2), "entry_vmg": round(entry_vmg, 2),
        "entry_twa": round(float(abs(pre_twa)), 1), "exit_twa": round(float(abs(post_twa)), 1),
        "min_bsp": round(min_bsp, 2), "min_bsp_after_s": int(min_bsp_t - i),
        "speed_drop_pct": round(100 * (1 - min_bsp / entry_bsp), 1) if entry_bsp > 0 else None,
        "turn_deg": round(float(turn), 0), "recover_s": recover_s, "loss_m": loss_m,
        "lat": _f(df["lat"].iloc[i]), "lon": _f(df["lon"].iloc[i]),
    }


def _f(v):
    return None if pd.isna(v) else float(v)


def summarize(maneuvers: list[dict]) -> dict:
    """Averages per kind and per wind band."""
    if not maneuvers:
        return {"by_kind": [], "by_band": []}
    m = pd.DataFrame(maneuvers)
    bands = [0, 6, 9, 12, 16, 50]
    labels = ["<6", "6-9", "9-12", "12-16", "16+"]
    m["band"] = pd.cut(m["tws"], bands, labels=labels, right=False)
    agg = {"loss_m": "mean", "recover_s": "mean", "speed_drop_pct": "mean", "index": "count"}

    def table(g):
        t = g.agg(agg).rename(columns={"index": "count"}).reset_index()
        t = t[t["count"] > 0]
        return t.round(1).replace({np.nan: None}).to_dict("records")

    by_kind = table(m.groupby("kind"))
    by_band = table(m.groupby(["kind", "band"], observed=True))
    for r in by_band:
        r["band"] = str(r["band"])
    return {"by_kind": by_kind, "by_band": by_band}
