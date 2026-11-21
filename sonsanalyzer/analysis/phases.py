"""Find the racing part of a sailing session: race blocks, estimated gun time and finish.

Building block: a per-second "racing" signal = under sail (not motoring) and sailing at a
good fraction of the polar target. Pre-start manoeuvring, drifting, dropping sails and
motoring all fall below it, while tacks and gybes are short dips that smoothing removes.

* Race block: a stretch of at least `min_race_minutes` where most seconds are racing.
* Gun estimate: the first moment of sustained full-speed sailing in the block, moved to a
  whole minute (starts are signalled on whole minutes). Boats normally reach full speed
  between ~40 s before and ~20 s after the gun, so the estimate is usually right, but it is
  only an estimate: the crew confirms it or nudges it by a minute.
* Finish estimate: the last second of racing before the boat stops racing for good (sails
  down, drifting, or the engine starting).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def racing_signal(df: pd.DataFrame, fraction: float = 0.65) -> pd.Series:
    """True where the boat is sailing at racing pace: not motoring and boatspeed at least
    `fraction` of the beat/run target speed for the current wind. (The beat/run target is
    used rather than the target for the actual TWA, which drops to zero head to wind.)"""
    bsp = df["bsp"].rolling(15, center=True, min_periods=1).mean()
    motoring = df["motoring"] if "motoring" in df else pd.Series(False, index=df.index)
    if df["target_vmg_bsp"].notna().any():
        good = bsp >= fraction * df["target_vmg_bsp"]
    else:  # no polar: fall back to a fraction of the session's typical speed
        good = bsp >= fraction * bsp.quantile(0.9)
    return (good & (bsp > 2.0) & ~motoring.astype(bool)).fillna(False)


def _blocks(mask: np.ndarray, min_len: int, max_gap: int) -> list[tuple[int, int]]:
    blocks: list[list[int]] = []
    start = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            blocks.append([start, i])
            start = None
    if start is not None:
        blocks.append([start, len(mask)])
    merged: list[list[int]] = []
    for b in blocks:
        if merged and b[0] - merged[-1][1] <= max_gap:
            merged[-1][1] = b[1]
        else:
            merged.append(b)
    return [(a, b) for a, b in merged if b - a >= min_len]


def find_races(df: pd.DataFrame, acfg: dict) -> list[dict]:
    """Detect races in an enriched DataFrame (output of derive.enrich)."""
    if len(df) < 120:
        return []
    r_np = racing_signal(df).to_numpy(dtype=float)
    full = racing_signal(df, 0.85).to_numpy(dtype=float)
    # mostly racing over a 5 minute window -> part of a race
    smooth = pd.Series(r_np).rolling(300, center=True, min_periods=30).mean().to_numpy()
    min_len = int(acfg.get("min_race_minutes", 15)) * 60
    races = []
    for a, b in _blocks(smooth > 0.6, min_len, max_gap=120):
        # Start: first second near the block start from which the boat sails at close to full
        # speed (85 % of target) for most of the next 3 minutes. Approaching the line at
        # partial speed in the last minute does not count yet.
        fwd = pd.Series(full[::-1]).rolling(180, min_periods=60).mean().to_numpy()[::-1]
        lo, hi = max(0, a - 300), min(len(full), a + 600)
        start_i = next((i for i in range(lo, hi) if full[i] and fwd[i] >= 0.7), a)
        # Finish: last racing second near the block end, followed by mostly not racing
        bwd = pd.Series(r_np).rolling(180, min_periods=60).mean().to_numpy()
        lo, hi = max(start_i, b - 300), min(len(r_np), b + 300)
        finish_i = next((i for i in range(hi - 1, lo - 1, -1) if r_np[i] and bwd[i] >= 0.85), b - 1)
        t_start = df.index[start_i]
        # Crews normally reach full speed between ~40 s before and ~20 s after the gun:
        # take the first whole minute at or after (t_start - 20 s).
        gun = (t_start - pd.Timedelta(seconds=20)).ceil("1min")
        finish = df.index[finish_i]
        races.append({
            "gun": gun, "racing_from": t_start, "finish": finish,
            "window_start": gun - pd.Timedelta(minutes=6),
            "window_end": finish + pd.Timedelta(minutes=3),
            "duration_s": (finish - gun).total_seconds(),
        })
    return races


def pick_race(races: list[dict], start, end) -> dict | None:
    """The detected race that overlaps the window [start, end] most."""
    best, best_overlap = None, 0.0
    for r in races:
        a = max(pd.Timestamp(start), r["gun"])
        b = min(pd.Timestamp(end), r["finish"])
        overlap = (b - a).total_seconds()
        if overlap > best_overlap:
            best, best_overlap = r, overlap
    return best
