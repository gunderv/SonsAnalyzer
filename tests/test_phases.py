"""Motoring detection, race/gun/finish estimation and the line-free start analysis."""
import numpy as np
import pandas as pd
import pytest

from sonsanalyzer.analysis import derive, phases, report, start
from sonsanalyzer.data.store import Store
from sonsanalyzer.sim.simulator import simulate_race

GUN = "2026-06-01T11:00:00Z"


def outing(polar, seed=1, tws=11.0, engine=True):
    """Motor out 20 min, race, drop sails, motor home 15 min."""
    return simulate_race(polar, GUN, seed=seed, tws_mean=tws, motor_before_s=1200,
                         motor_after_s=900, engine_data=engine)


def truth_mask(df, truth):
    m = pd.Series(False, index=df.index)
    for a, b in truth.motoring:
        m.loc[a:b] = True
    return m


@pytest.mark.parametrize("engine", [True, False])
def test_motoring_detected(polar, acfg, engine):
    df, truth = outing(polar, engine=engine)
    d = derive.enrich(df, polar, acfg)
    assert d.attrs["motoring_source"] == ("engine" if engine else "heuristic")
    t = truth_mask(d, truth)
    detected = d["motoring"]
    assert (t & ~detected).sum() < 60          # < 1 min of motoring missed
    assert (detected & ~t).sum() < 60          # < 1 min of sailing called motoring
    # nothing under engine counts towards performance
    assert d.loc[detected, "polar_pct"].isna().all()
    assert (d.loc[detected, "mode"] == "motoring").all()


@pytest.mark.parametrize("seed,tws,engine", [(1, 11, True), (2, 7, False), (3, 16, False), (4, 5, True)])
def test_gun_and_finish_estimated(polar, acfg, seed, tws, engine):
    df, truth = outing(polar, seed, tws, engine)
    d = derive.enrich(df, polar, acfg)
    races = phases.find_races(d, acfg)
    assert len(races) == 1
    r = races[0]
    assert r["gun"] == truth.gun
    assert abs((r["finish"] - truth.finish).total_seconds()) < 30


def test_two_races_in_one_outing(polar, acfg):
    df1, t1 = simulate_race(polar, GUN, seed=7, tws_mean=10, motor_before_s=900)
    gun2 = (t1.finish + pd.Timedelta(minutes=25)).ceil("1min")
    df2, t2 = simulate_race(polar, gun2, seed=8, tws_mean=10, prestart_s=900, motor_after_s=600)
    df = pd.concat([df1, df2]).sort_index()
    df = df[~df.index.duplicated()]
    df = df.reindex(pd.date_range(df.index[0], df.index[-1], freq="1s"))
    d = derive.enrich(df, polar, acfg)
    races = phases.find_races(d, acfg)
    assert [r["gun"] for r in races] == [t1.gun, t2.gun] or \
        all(abs((r["gun"] - t.gun).total_seconds()) <= 60 for r, t in zip(races, (t1, t2)))
    assert len(races) == 2


def test_report_uses_estimates_and_excludes_motoring(polar, acfg):
    df, truth = outing(polar)
    r = report.analyze_race(df, polar, acfg)
    ph = r["phases"]
    assert ph["gun_source"] == "estimated" and ph["finish_source"] == "estimated"
    assert pd.Timestamp(ph["gun"]) == truth.gun
    assert ph["motoring_s"] > 1500
    # summary covers only the race: average polar % is the simulated performance (~96 %)
    assert 90 <= r["summary"]["avg_polar_pct"] <= 100
    assert "motoring" not in r["summary"]["mode_share"]
    assert [leg["mode"] for leg in r["legs"]] == ["upwind", "downwind", "upwind", "downwind"]
    assert all(m["phase"] == "race" for m in r["maneuvers"] if not m["prestart"])
    # crew-set times win over estimates
    r2 = report.analyze_race(df, polar, acfg, gun=truth.gun + pd.Timedelta(minutes=1))
    assert r2["phases"]["gun_source"] == "set"
    assert pd.Timestamp(r2["phases"]["gun"]) == truth.gun + pd.Timedelta(minutes=1)


def test_start_without_line(race, polar, acfg):
    df, truth = race
    d = derive.enrich(df, polar, acfg)
    s = start.analyze(d, truth.gun, polar=polar)
    assert s["has_line"] is False
    assert 0 < s["bsp_at_gun"] <= s["target_bsp"] * 1.1
    assert s["time_to_full_speed_s"] is not None and -60 <= s["time_to_full_speed_s"] <= 60
    assert s["gain_120s_m"] > 0 and s["ideal_120s_m"] > 0
    assert 50 <= s["gain_120s_pct"] <= 110
    with_line = start.analyze(d, truth.gun, truth.pin, truth.boat, polar)
    assert with_line["has_line"] and "dist_at_gun_m" in with_line
    assert len(with_line["series"]["dist_m"]) == len(with_line["series"]["bsp"])


def test_store_migrates_finish_time(tmp_path):
    import sqlite3
    con = sqlite3.connect(tmp_path / "sonsanalyzer.sqlite")
    con.execute("CREATE TABLE races (id INTEGER PRIMARY KEY, name TEXT, start_time TEXT, end_time TEXT, "
                "gun_time TEXT, pin_lat REAL, pin_lon REAL, boat_lat REAL, boat_lon REAL, notes TEXT, updated_at TEXT)")
    con.commit()
    con.close()
    st = Store(tmp_path)
    rid = st.create_race({"name": "x", "start_time": GUN, "end_time": GUN, "finish_time": GUN})
    assert st.get_race(rid)["finish_time"].startswith("2026-06-01T11:00")
    st.update_race(rid, {"finish_time": None})
    assert st.get_race(rid)["finish_time"] is None
