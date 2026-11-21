"""Start analysis.

Always (needs only the gun time): speed and angle at the gun, time to reach target speed,
and how much ground was gained to windward in the first one and two minutes, compared with
sailing at the polar's target VMG from the gun.

With a start line (pin = port end, boat = committee boat / starboard end, each (lat, lon)):
distance to the line at the gun, time to cross, position on the line, line bias.

Sign convention: distance to line is positive on the pre-start side and negative when over
the line. The pre-start side is the side where the boat is 60-120 s before the gun.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EARTH_R = 6371000.0


def _to_xy(lat, lon, origin):
    lat0, lon0 = origin
    x = np.deg2rad(np.asarray(lon) - lon0) * EARTH_R * np.cos(np.deg2rad(lat0))
    y = np.deg2rad(np.asarray(lat) - lat0) * EARTH_R
    return x, y


def analyze(df: pd.DataFrame, gun: pd.Timestamp, pin: tuple[float, float] | None = None,
            boat: tuple[float, float] | None = None, polar=None) -> dict | None:
    gun = pd.Timestamp(gun)
    gun = gun.tz_localize("UTC") if gun.tzinfo is None else gun.tz_convert("UTC")
    perf = performance(df, gun)
    if perf is None:
        return {"error": "No boat data around the gun time"}
    if pin is None or boat is None:
        return {**perf, "has_line": False}
    line = line_metrics(df, gun, pin, boat, polar)
    if line is None or "error" in line:
        return {**perf, "has_line": False, "line_error": (line or {}).get("error")}
    series = {**perf["series"], "dist_m": line.pop("series")["dist_m"]}
    return {**perf, **line, "series": series, "has_line": True}


def performance(df: pd.DataFrame, gun: pd.Timestamp) -> dict | None:
    """Start metrics that need no start line."""
    win = df.loc[gun - pd.Timedelta(seconds=180): gun + pd.Timedelta(seconds=180)]
    if win.empty or win["bsp"].isna().all():
        return None
    t_rel = (win.index - gun).total_seconds().to_numpy()
    i_gun = int(np.argmin(np.abs(t_rel)))
    bsp = win["bsp"].to_numpy(float)
    target = win["target_vmg_bsp"].to_numpy(float) if "target_vmg_bsp" in win else np.full(len(win), np.nan)

    def at(arr, i, w=2):
        seg = arr[max(0, i - w):i + w + 1]
        return None if np.isnan(seg).all() else float(np.nanmean(seg))

    bsp_gun, target_gun = at(bsp, i_gun), at(target, i_gun)
    # time to full speed: first moment from 60 s before the gun with BSP >= 90 % of the
    # beat target held for 10 s (relative to the gun; negative = already at speed before)
    t_full = None
    ok = bsp >= 0.9 * target
    for k in range(len(win)):
        if t_rel[k] >= -60 and ok[k:k + 10].all() and len(ok[k:k + 10]) == 10:
            t_full = float(t_rel[k])
            break

    # windward gain after the gun vs target VMG
    gains = {}
    twd_gun = _circ_mean(win["twd"].to_numpy(float)[max(0, i_gun - 30):i_gun + 30])
    has_xy = "x" in win and win["x"].notna().any()
    for secs in (60, 120):
        j = i_gun + secs
        if not has_xy or twd_gun is None or j >= len(win) or np.isnan(win["x"].iloc[[i_gun, j]]).any():
            gains[secs] = (None, None)
            continue
        w = np.array([np.sin(np.deg2rad(twd_gun)), np.cos(np.deg2rad(twd_gun))])
        d = np.array([win["x"].iloc[j] - win["x"].iloc[i_gun], win["y"].iloc[j] - win["y"].iloc[i_gun]])
        gained = float(d @ w)
        tv = win["target_vmg"].iloc[i_gun:j].mean() if "target_vmg" in win else np.nan
        ideal = float(tv * 1852 / 3600 * secs) if not np.isnan(tv) else None
        gains[secs] = (gained, ideal)

    sog = win["sog"].to_numpy(float)
    twa = np.abs(win["twa"].to_numpy(float))
    return {
        "gun": gun.isoformat(),
        "bsp_at_gun": _r(bsp_gun), "sog_at_gun": _r(at(sog, i_gun)),
        "target_bsp": _r(target_gun),
        "pct_at_gun": None if not bsp_gun or not target_gun else round(100 * bsp_gun / target_gun, 1),
        "twa_at_gun": _r(at(twa, i_gun)), "tws_at_gun": _r(at(win["tws"].to_numpy(float), i_gun)),
        "twd_at_gun": None if twd_gun is None else round(twd_gun, 1),
        "time_to_full_speed_s": None if t_full is None else round(t_full, 0),
        "gain_60s_m": _r(gains[60][0]), "ideal_60s_m": _r(gains[60][1]),
        "gain_120s_m": _r(gains[120][0]), "ideal_120s_m": _r(gains[120][1]),
        "gain_120s_pct": None if not gains[120][0] or not gains[120][1]
        else round(100 * gains[120][0] / gains[120][1], 1),
        "series": {
            "t": [round(v, 1) for v in t_rel.tolist()],
            "bsp": [_r(v) for v in bsp], "sog": [_r(v) for v in sog],
            "target": [_r(v) for v in target], "twa": [_r(v) for v in twa],
        },
    }


def line_metrics(df: pd.DataFrame, gun: pd.Timestamp, pin: tuple[float, float],
                 boat: tuple[float, float], polar=None) -> dict | None:
    if "x" not in df or df["x"].isna().all():
        return None
    origin = df.attrs.get("origin") or (float(df["lat"].mean()), float(df["lon"].mean()))
    px, py = _to_xy(pin[0], pin[1], origin)
    bx, by = _to_xy(boat[0], boat[1], origin)
    line = np.array([bx - px, by - py], dtype=float)
    length = float(np.hypot(*line))
    if length < 5:
        return {"error": "Start line ends are less than 5 m apart"}
    u = line / length
    normal = np.array([-u[1], u[0]])  # left-hand normal of pin -> boat

    win = df.loc[gun - pd.Timedelta(seconds=180): gun + pd.Timedelta(seconds=180)]
    if win.empty or win["x"].isna().all():
        return {"error": "No position data around the gun time"}

    rel_x = win["x"].to_numpy() - px
    rel_y = win["y"].to_numpy() - py
    side_raw = rel_x * normal[0] + rel_y * normal[1]
    along = (rel_x * u[0] + rel_y * u[1]) / length * 100  # % from pin

    t_rel = (win.index - gun).total_seconds().to_numpy()
    pre_mask = (t_rel >= -120) & (t_rel <= -60) & ~np.isnan(side_raw)
    ref = np.nanmedian(side_raw[pre_mask]) if pre_mask.any() else np.nanmedian(side_raw[t_rel < 0])
    sgn = 1.0 if ref >= 0 else -1.0
    dist = sgn * side_raw  # + = behind line

    i_gun = int(np.argmin(np.abs(t_rel)))
    d_gun = float(dist[i_gun])
    over_early = bool(d_gun < 0)

    # Crossing: first time after (or at) the gun moving from + to <= 0 inside the line ends
    cross_i = None
    for k in range(max(1, i_gun), len(dist)):
        if dist[k - 1] > 0 >= dist[k]:
            cross_i = k
            break
    sog = win["sog"].to_numpy()
    cog = win["cog"].to_numpy()
    # Speed towards the line at the gun
    heading_vec = np.array([np.sin(np.deg2rad(cog[i_gun])), np.cos(np.deg2rad(cog[i_gun]))])
    v_perp = float(-(sgn * normal) @ heading_vec * sog[i_gun] * 1852 / 3600)  # m/s towards line
    time_to_line = d_gun / v_perp if v_perp > 0.2 and d_gun > 0 else None

    # Line bias: which end is favoured relative to the wind at the gun
    twd = win["twd"].to_numpy()
    bias = advantage = favoured = None
    twd_gun = _circ_mean(twd[max(0, i_gun - 30):i_gun + 1])
    if twd_gun is not None:
        # unit vector pointing upwind (TWD is where the wind comes from)
        w = np.array([np.sin(np.deg2rad(twd_gun)), np.cos(np.deg2rad(twd_gun))])
        proj = float(u @ w)  # >0: boat end is further upwind
        bias = float(np.rad2deg(np.arcsin(np.clip(proj, -1, 1))))
        advantage = abs(proj) * length
        favoured = "boat" if proj > 0 else "pin"
    series = {"dist_m": [None if np.isnan(v) else round(float(v), 1) for v in dist]}
    return {
        "line_length_m": round(length, 1),
        "line_bearing": round(float(np.rad2deg(np.arctan2(u[0], u[1])) % 360), 1),
        "dist_at_gun_m": round(d_gun, 1),
        "over_early": over_early,
        "position_pct": round(float(along[i_gun]), 1),
        "time_to_line_s": None if time_to_line is None else round(time_to_line, 1),
        "crossed_after_s": None if cross_i is None else round(float(t_rel[cross_i]), 1),
        "cross_position_pct": None if cross_i is None else round(float(along[cross_i]), 1),
        "bias_deg": None if bias is None else round(abs(bias), 1),
        "favoured_end": favoured,
        "favoured_advantage_m": None if advantage is None else round(advantage, 1),
        "pin": list(pin), "boat": list(boat),
        "series": series,
    }


def _circ_mean(a: np.ndarray):
    a = a[~np.isnan(a)]
    if a.size == 0:
        return None
    r = np.deg2rad(a)
    return float(np.rad2deg(np.arctan2(np.sin(r).mean(), np.cos(r).mean())) % 360)


def _r(v):
    return None if v is None or np.isnan(v) else round(float(v), 2)
