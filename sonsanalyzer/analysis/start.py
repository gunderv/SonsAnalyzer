"""Start line analysis.

Inputs: gun time and the two line ends (pin = port end, boat = committee boat / starboard
end), each as (lat, lon). Distances are computed in a local metric frame.

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


def analyze(df: pd.DataFrame, gun: pd.Timestamp, pin: tuple[float, float],
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

    gun = pd.Timestamp(gun)
    gun = gun.tz_localize("UTC") if gun.tzinfo is None else gun.tz_convert("UTC")
    win = df.loc[gun - pd.Timedelta(seconds=180): gun + pd.Timedelta(seconds=120)]
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
    bsp = win["bsp"].to_numpy()
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
    target = None
    if polar is not None and not np.isnan(win["tws"].iloc[i_gun]):
        target = float(polar.targets(float(win["tws"].iloc[i_gun]))["up_bsp"])

    series = {
        "t": [round(v, 1) for v in t_rel.tolist()],
        "dist_m": [None if np.isnan(v) else round(float(v), 1) for v in dist],
        "sog": [None if np.isnan(v) else round(float(v), 2) for v in sog],
        "bsp": [None if np.isnan(v) else round(float(v), 2) for v in bsp],
    }
    return {
        "gun": gun.isoformat(),
        "line_length_m": round(length, 1),
        "line_bearing": round(float(np.rad2deg(np.arctan2(u[0], u[1])) % 360), 1),
        "dist_at_gun_m": round(d_gun, 1),
        "over_early": over_early,
        "position_pct": round(float(along[i_gun]), 1),
        "sog_at_gun": _r(sog[i_gun]), "bsp_at_gun": _r(bsp[i_gun]),
        "target_bsp": None if target is None else round(target, 2),
        "time_to_line_s": None if time_to_line is None else round(time_to_line, 1),
        "crossed_after_s": None if cross_i is None else round(float(t_rel[cross_i]), 1),
        "cross_position_pct": None if cross_i is None else round(float(along[cross_i]), 1),
        "bias_deg": None if bias is None else round(abs(bias), 1),
        "favoured_end": favoured,
        "favoured_advantage_m": None if advantage is None else round(advantage, 1),
        "twd_at_gun": None if twd_gun is None else round(twd_gun, 1),
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
