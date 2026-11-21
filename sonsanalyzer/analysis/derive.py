"""Clean the raw canonical DataFrame and add derived performance channels.

Added columns:
  x, y          local metric coordinates (m, east/north) around the session's mean position
  dist_m        distance sailed since previous sample (GPS)
  vmg           velocity made good to windward through water (kn, + upwind, - downwind)
  mode          'upwind' | 'reach' | 'downwind'
  target_bsp    polar boatspeed for current TWS/TWA
  polar_pct     bsp / target_bsp * 100
  target_twa    optimum TWA (upwind or downwind depending on side of 90)
  target_vmg    optimum VMG for current TWS
  vmg_pct       |vmg| / target_vmg * 100 (only upwind/downwind)
  twa_delta     |twa| - target_twa (positive = sailing lower/wider than target)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.schema import wrap180
from ..polar.polar import Polar

EARTH_R = 6371000.0


def circular_rolling(series: pd.Series, window: int, signed: bool = True) -> pd.Series:
    """Rolling circular mean of an angle in degrees (centered)."""
    if window <= 1:
        return series
    rad = np.deg2rad(series)
    s = np.sin(rad).rolling(window, center=True, min_periods=1).mean()
    c = np.cos(rad).rolling(window, center=True, min_periods=1).mean()
    deg = np.rad2deg(np.arctan2(s, c))
    deg = deg.where(series.notna())
    return deg if signed else deg % 360.0


def fill_wind(df: pd.DataFrame) -> pd.DataFrame:
    """Fill heading and true wind columns that the instruments did not provide."""
    if df["hdg"].isna().all() and df["hdg_mag"].notna().any():
        df["hdg"] = (df["hdg_mag"] + df["variation"].fillna(0)) % 360
    if df["hdg"].isna().all():
        df["hdg"] = df["cog"]  # last resort: no compass, use COG
    if df["bsp"].isna().all():
        df["bsp"] = df["sog"]  # no log: treat SOG as boatspeed

    # True wind (water referenced) from apparent wind and boatspeed where missing
    need = df["twa"].isna() & df["awa"].notna() & df["aws"].notna() & df["bsp"].notna()
    if need.any():
        awa = np.deg2rad(df.loc[need, "awa"])
        aws = df.loc[need, "aws"]
        bsp = df.loc[need, "bsp"]
        u = aws * np.cos(awa) - bsp
        v = aws * np.sin(awa)
        df.loc[need, "twa"] = np.rad2deg(np.arctan2(v, u))
        df.loc[need, "tws"] = np.hypot(u, v)
    need_twd = df["twd"].isna() & df["twa"].notna() & df["hdg"].notna()
    df.loc[need_twd, "twd"] = (df.loc[need_twd, "hdg"] + df.loc[need_twd, "twa"]) % 360
    # If TWA missing but TWD known (some instruments only publish TWD)
    need_twa = df["twa"].isna() & df["twd"].notna() & df["hdg"].notna()
    df.loc[need_twa, "twa"] = wrap180(df.loc[need_twa, "twd"] - df.loc[need_twa, "hdg"])
    return df


def classify_mode(abs_twa: pd.Series, upwind_max: float, downwind_min: float,
                  hysteresis: float) -> pd.Series:
    """Sailing mode with hysteresis so the label does not flicker at the boundaries."""
    out = np.empty(len(abs_twa), dtype=object)
    state = None
    for i, a in enumerate(abs_twa.to_numpy()):
        if np.isnan(a):
            out[i] = state
            continue
        if state is None:
            state = "upwind" if a < upwind_max else "downwind" if a > downwind_min else "reach"
        elif state == "upwind" and a > upwind_max + hysteresis:
            state = "downwind" if a > downwind_min else "reach"
        elif state == "downwind" and a < downwind_min - hysteresis:
            state = "upwind" if a < upwind_max else "reach"
        elif state == "reach":
            if a < upwind_max - hysteresis:
                state = "upwind"
            elif a > downwind_min + hysteresis:
                state = "downwind"
        out[i] = state
    return pd.Series(out, index=abs_twa.index)


def add_xy(df: pd.DataFrame) -> pd.DataFrame:
    lat0 = df["lat"].mean()
    lon0 = df["lon"].mean()
    if np.isnan(lat0):
        df["x"] = df["y"] = df["dist_m"] = np.nan
        return df
    df["x"] = np.deg2rad(df["lon"] - lon0) * EARTH_R * np.cos(np.deg2rad(lat0))
    df["y"] = np.deg2rad(df["lat"] - lat0) * EARTH_R
    df["dist_m"] = np.hypot(df["x"].diff(), df["y"].diff()).fillna(0)
    df.attrs["origin"] = (float(lat0), float(lon0))
    return df


def enrich(raw: pd.DataFrame, polar: Polar | None, acfg: dict) -> pd.DataFrame:
    """Return a new DataFrame with cleaned + derived channels."""
    df = raw.copy()
    for c in ("hdg_mag", "variation"):
        if c not in df:
            df[c] = np.nan
    df = fill_wind(df)

    w = int(acfg.get("smoothing_s", 3))
    for col in ("twa", "awa", "heel", "rudder", "leeway"):
        df[col] = circular_rolling(df[col], w, signed=True)
    for col in ("twd", "hdg", "cog"):
        df[col] = circular_rolling(df[col], w, signed=False)
    for col in ("bsp", "sog", "tws", "aws"):
        df[col] = df[col].rolling(w, center=True, min_periods=1).mean()

    df = add_xy(df)
    abs_twa = df["twa"].abs()
    df["vmg"] = df["bsp"] * np.cos(np.deg2rad(df["twa"]))
    df["mode"] = classify_mode(abs_twa, acfg["upwind_max_twa"], acfg["downwind_min_twa"],
                               acfg["hysteresis_deg"])

    if polar is not None:
        tws = df["tws"].to_numpy()
        df["target_bsp"] = polar.target_bsp(tws, abs_twa.to_numpy())
        tg = polar.targets(tws)
        upwind_side = abs_twa < 90
        df["target_twa"] = np.where(upwind_side, tg["up_twa"], tg["down_twa"])
        df["target_vmg"] = np.where(upwind_side, tg["up_vmg"], tg["down_vmg"])
        df["target_vmg_bsp"] = np.where(upwind_side, tg["up_bsp"], tg["down_bsp"])
        with np.errstate(divide="ignore", invalid="ignore"):
            df["polar_pct"] = 100 * df["bsp"] / df["target_bsp"]
            vmg_pct = 100 * df["vmg"].abs() / df["target_vmg"]
        df["vmg_pct"] = vmg_pct.where(df["mode"].isin(["upwind", "downwind"]))
        df["twa_delta"] = abs_twa - df["target_twa"]
        # Ignore percentages when nearly stopped (start line, drifting)
        # ... and when far inside the close-hauled angle (head to wind during a tack),
        # where the polar target tends to zero and the ratio is meaningless
        slow = (df["bsp"] < 1.0) | (df["tws"] < 2.0) | (abs_twa < 0.75 * tg["up_twa"])
        df.loc[slow, ["polar_pct", "vmg_pct"]] = np.nan
    else:
        for c in ("target_bsp", "target_twa", "target_vmg", "target_vmg_bsp", "polar_pct",
                  "vmg_pct", "twa_delta"):
            df[c] = np.nan
    return df
