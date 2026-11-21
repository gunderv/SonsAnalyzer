import numpy as np
import pandas as pd
import pytest

from sonsanalyzer.analysis import derive, legs, maneuvers, report, sessions, start
from sonsanalyzer.sim.simulator import simulate_race


def test_true_wind_derived_from_apparent(race, polar, acfg):
    df, _ = race
    raw = df.copy()
    truth = raw[["twa", "tws"]].copy()
    raw[["twa", "tws", "twd"]] = np.nan
    out = derive.fill_wind(raw)
    err = (out["twa"] - truth["twa"] + 180) % 360 - 180
    assert err.abs().median() < 0.5
    assert (out["tws"] - truth["tws"]).abs().median() < 0.1


def test_polar_pct_reflects_simulated_performance(race, polar, acfg):
    df, truth = race
    d = derive.enrich(df, polar, acfg).loc[truth.gun + pd.Timedelta(minutes=2):]
    # simulator sails at 96 % of polar
    assert 92 <= d["polar_pct"].median() <= 99


def test_maneuvers_match_ground_truth(race, polar, acfg):
    df, truth = race
    d = derive.enrich(df, polar, acfg)
    found = [m for m in maneuvers.detect(d, acfg) if m["time"] > truth.gun]
    matched = sum(
        any(m["kind"] == kind and abs((m["time"] - t).total_seconds()) < 20 for m in found)
        for t, kind in truth.maneuvers)
    assert matched >= 0.8 * len(truth.maneuvers)
    assert len(found) <= len(truth.maneuvers) + 1  # no spurious detections
    tacks = [m for m in found if m["kind"] == "tack"]
    assert tacks, "expected tacks"
    losses = [m["loss_m"] for m in tacks if m["loss_m"] is not None]
    assert 0 < np.median(losses) < 40  # simulated tack loses some metres
    assert all(m["min_bsp"] < m["entry_bsp"] for m in tacks)


def test_no_maneuvers_on_straight_line(polar, acfg):
    idx = pd.date_range("2026-06-01", periods=600, freq="1s", tz="UTC")
    n = len(idx)
    df = pd.DataFrame({"lat": 59 + np.arange(n) * 1e-5, "lon": np.full(n, 10.6), "sog": 6.5,
                       "cog": 0.0, "bsp": 6.5, "hdg": 0.0, "awa": 30.0, "aws": 16.0,
                       "twa": 42 + np.random.default_rng(0).normal(0, 3, n), "tws": 11.0,
                       "twd": 42.0, "heel": -18.0, "pitch": 0.0, "rudder": 0.0, "depth": 20.0,
                       "leeway": np.nan, "hdg_mag": np.nan, "variation": np.nan}, index=idx)
    d = derive.enrich(df, polar, acfg)
    assert maneuvers.detect(d, acfg) == []


def test_legs_follow_course(race, polar, acfg):
    df, truth = race
    d = derive.enrich(df, polar, acfg)
    lg = legs.segment(d, acfg["min_leg_s"])
    modes = [leg["mode"] for leg in lg]
    assert modes == ["upwind", "downwind", "upwind", "downwind"]
    # leg changes close to the simulated mark roundings
    for leg, rounding in zip(lg[1:], truth.roundings):
        assert abs((leg["start"] - rounding).total_seconds()) < 60


def test_start_analysis_geometry(race, polar, acfg):
    df, truth = race
    d = derive.enrich(df, polar, acfg)
    res = start.analyze(d, truth.gun, truth.pin, truth.boat, polar)
    assert res["line_length_m"] == pytest.approx(200, abs=2)
    assert not res["over_early"]
    assert 0 < res["dist_at_gun_m"] < 40
    assert 40 <= res["cross_position_pct"] <= 70  # simulator aims at 55 % from the pin
    assert 0 <= res["crossed_after_s"] <= 15
    # line was set square to the mean wind -> small bias
    assert res["bias_deg"] < 10


def test_start_over_early_when_line_moved_back(race, polar, acfg):
    df, truth = race
    d = derive.enrich(df, polar, acfg)
    # shift the line 100 m towards the pre-start side -> boat is over at the gun
    import math
    twd = math.radians(200)
    dlat = -100 * math.cos(twd) / 6371000 * 180 / math.pi
    dlon = -100 * math.sin(twd) / (6371000 * math.cos(math.radians(59.03))) * 180 / math.pi
    pin = (truth.pin[0] + dlat, truth.pin[1] + dlon)
    boat = (truth.boat[0] + dlat, truth.boat[1] + dlon)
    res = start.analyze(d, truth.gun, pin, boat, polar)
    assert res["over_early"]


def test_full_report_is_json_serialisable(race, polar, acfg):
    import json
    df, truth = race
    r = report.analyze_race(df, polar, acfg, gun=truth.gun,
                            line={"pin": truth.pin, "boat": truth.boat})
    json.dumps(r, allow_nan=False, default=str)
    assert r["summary"]["avg_polar_pct"] is not None
    assert r["bands"]
    assert len(r["track"]["t"]) == len(r["track"]["lat"])
    assert r["track"]["t"][0] == int(df.index[0].timestamp() * 1000)  # epoch ms


def test_session_detection():
    idx = pd.date_range("2026-06-01 08:00", periods=600, freq="1min", tz="UTC")
    sog = pd.Series(0.2, index=idx)
    sog.iloc[60:180] = 6.0          # 2 h sailing
    sog.iloc[185:200] = 5.0         # after a 5 min stop -> same session
    sog.iloc[400:405] = 4.0         # 5 min only -> too short
    out = sessions.detect_sessions(sog, 2.0, 15, 10)
    assert len(out) == 1
    assert out[0]["minutes"] == 140
