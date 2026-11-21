"""Smoke test of the web app in demo mode (simulated data, temporary data directory)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os

    data = tmp_path_factory.mktemp("data")
    cfg = data / "config.toml"
    cfg.write_text(f'[app]\nsource = "demo"\ndata_dir = "{data.as_posix()}"\n')
    os.environ["SONSANALYZER_CONFIG"] = str(cfg)
    from sonsanalyzer.web import app as appmod

    appmod.state = None
    with TestClient(appmod.app) as c:
        yield c
    os.environ.pop("SONSANALYZER_CONFIG", None)
    appmod.state = None


def test_pages_render(client):
    for path in ("/", "/polar", "/live", "/race/1"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "SonsAnalyzer" in r.text


def test_race_analysis_and_crud(client):
    races = client.get("/api/races").json()
    assert len(races) == 3
    a = client.get(f"/api/races/{races[0]['id']}/analysis").json()
    assert a["summary"]["avg_polar_pct"] > 80
    assert a["start"] and not a["start"].get("error")
    assert a["maneuvers"]
    r = client.post("/api/races", json={"name": "x", "start_time": races[0]["start_time"],
                                        "end_time": races[0]["end_time"]}).json()
    assert client.put(f"/api/races/{r['id']}", json={"name": "y"}).json()["name"] == "y"
    assert client.delete(f"/api/races/{r['id']}").json()["ok"]


def test_estimates_confirm_and_find_races(client):
    races = sorted(client.get("/api/races").json(), key=lambda r: r["start_time"])
    est = client.get(f"/api/races/{races[1]['id']}/analysis").json()  # demo race 2: no gun set
    assert est["phases"]["gun_source"] == "estimated"
    assert est["phases"]["motoring_s"] > 1500
    assert est["start"]["has_line"] is False
    # confirm the estimate
    client.put(f"/api/races/{races[1]['id']}", json={"gun_time": est["phases"]["gun"]})
    again = client.get(f"/api/races/{races[1]['id']}/analysis").json()
    assert again["phases"]["gun_source"] == "set"
    # find races in a session without creating
    found = client.post("/api/races/detect", json={"start": races[2]["start_time"],
                                                   "end": races[2]["end_time"]}).json()
    assert len(found["races"]) == 1 and "race_id" not in found["races"][0]


def test_live_ping_sessions_polar(client):
    assert client.get("/api/live").json()["ok"]
    p = client.post("/api/ping/pin", json={}).json()
    assert -90 < p["lat"] < 90
    assert client.post("/api/ping/nope", json={}).status_code == 400
    assert len(client.get("/api/sessions?days=5").json()) >= 3
    assert client.get("/api/polar").json()["polar"]["meta"]["SailNo"] == "NOR 15666"
