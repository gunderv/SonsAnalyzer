import numpy as np
import pytest

from sonsanalyzer.polar.polar import Polar


def kn(allowance):
    return 3600.0 / allowance


def test_orc_grid_points_match_certificate(polar, cert):
    allow = cert["rms"][0]["Allowances"]
    for i, tws in enumerate(allow["WindSpeeds"]):
        for twa in allow["WindAngles"]:
            expected = kn(allow[f"R{twa}"][i])
            assert polar.target_bsp(tws, twa) == pytest.approx(expected, abs=0.01)


def test_beat_and_run_targets(polar, cert):
    allow = cert["rms"][0]["Allowances"]
    i = allow["WindSpeeds"].index(10)
    t = polar.targets(10)
    assert t["up_twa"] == pytest.approx(allow["BeatAngle"][i])
    assert t["up_vmg"] == pytest.approx(kn(allow["Beat"][i]))
    assert t["down_twa"] == pytest.approx(allow["GybeAngle"][i])
    assert t["down_vmg"] == pytest.approx(kn(allow["Run"][i]))
    # upwind target speed = VMG / cos(beat angle) and lies on the polar curve
    assert polar.target_bsp(10, t["up_twa"]) == pytest.approx(t["up_bsp"], rel=0.01)


def test_interpolation_and_edges(polar):
    a, b = polar.target_bsp(10, 90), polar.target_bsp(12, 90)
    assert a < polar.target_bsp(11, 90) < b
    assert polar.target_bsp(0, 90) == pytest.approx(0)
    assert polar.target_bsp(40, 90) == pytest.approx(polar.target_bsp(24, 90))  # clamped
    assert polar.target_bsp(10, -90) == polar.target_bsp(10, 90)  # sign ignored
    arr = polar.target_bsp(np.array([8, 12, np.nan]), np.array([60, 120, 90]))
    assert arr.shape == (3,) and np.isnan(arr[2])


def test_roundtrip_dict(polar):
    p2 = Polar.from_dict(polar.to_dict())
    for tws, twa in [(6, 45), (11, 100), (20, 170)]:
        assert p2.target_bsp(tws, twa) == pytest.approx(polar.target_bsp(tws, twa), abs=0.005)


def test_csv_polar_derives_targets():
    text = "twa/tws;6;10\n40;4.5;6.0\n60;5.5;7.0\n90;6.0;7.5\n140;5.0;7.2\n180;4.0;6.0\n"
    p = Polar.from_csv(text)
    t = p.targets(10)
    assert 38 <= t["up_twa"] <= 50
    assert 130 <= t["down_twa"] <= 180
    assert p.target_bsp(10, 90) == pytest.approx(7.5)
