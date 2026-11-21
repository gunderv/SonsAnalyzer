"""Find sailing sessions (periods where the boat was moving) from per-minute mean SOG."""
from __future__ import annotations

import pandas as pd


def detect_sessions(activity: pd.Series, min_sog: float = 2.0, min_minutes: int = 15,
                    max_gap_minutes: int = 10) -> list[dict]:
    moving = activity[activity >= min_sog]
    if moving.empty:
        return []
    sessions = []
    start = prev = moving.index[0]
    for t in moving.index[1:]:
        if (t - prev) > pd.Timedelta(minutes=max_gap_minutes):
            sessions.append((start, prev))
            start = t
        prev = t
    sessions.append((start, prev))
    out = []
    for a, b in sessions:
        b = b + pd.Timedelta(minutes=1)
        minutes = (b - a).total_seconds() / 60
        if minutes >= min_minutes:
            seg = activity.loc[a:b]
            out.append({"start": a.isoformat(), "end": b.isoformat(),
                        "minutes": round(minutes), "avg_sog": round(float(seg.mean()), 1),
                        "max_sog": round(float(seg.max()), 1)})
    return sorted(out, key=lambda s: s["start"], reverse=True)
