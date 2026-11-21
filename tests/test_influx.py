"""InfluxSource against a fake client that answers InfluxQL like the real server would."""
import re

import numpy as np
import pandas as pd
import pytest

from sonsanalyzer.config import DEFAULTS
from sonsanalyzer.data import schema
from sonsanalyzer.data.influx import InfluxSource
from sonsanalyzer.sim.simulator import to_influx_points


class FakeResult:
    def __init__(self, points):
        self._points = points

    def get_points(self):
        return iter(self._points)


class FakeClient:
    """Stores points (as produced by the simulator writer) and evaluates simple queries."""

    def __init__(self, points):
        self.data: dict[str, list[dict]] = {}
        for p in points:
            self.data.setdefault(p["measurement"], []).append({"time": p["time"], **p["fields"]})
        self.queries = []

    def query(self, q, epoch=None):
        self.queries.append(q)
        if q == "SHOW MEASUREMENTS":
            return FakeResult([{"name": m} for m in self.data])
        m = re.match(r'SHOW FIELD KEYS FROM "(.+)"', q)
        if m:
            keys = [k for k in self.data[m.group(1)][0] if k != "time"]
            return FakeResult([{"fieldKey": k} for k in keys])
        m = re.match(r'SELECT (.+) FROM "(.+?)" WHERE (.+?)(?: GROUP BY.*)?$', q)
        sel, meas, _ = m.groups()
        out = []
        for row in self.data.get(meas, []):
            rec = {"time": row["time"] * 1000}
            for agg, field, alias in re.findall(r'(\w+)\("(\w+)"\) AS (\w+)', sel):
                rec[alias] = row[field]
            out.append(rec)
        return FakeResult(out)


def test_fetch_roundtrip(race):
    df, truth = race
    part = df.loc[truth.gun: truth.gun + pd.Timedelta(minutes=10)]
    client = FakeClient(to_influx_points(part))
    src = InfluxSource(DEFAULTS, client=client)
    got = src.fetch(part.index[0].to_pydatetime(), part.index[-1].to_pydatetime() + pd.Timedelta(seconds=1))
    assert len(got) == len(part)
    for col in ("bsp", "sog", "tws", "aws"):
        assert np.allclose(got[col].to_numpy(), part[col].to_numpy(), atol=1e-6)
    for col in ("twa", "awa", "hdg", "twd", "heel"):
        err = (got[col].to_numpy() - part[col].to_numpy() + 180) % 360 - 180
        assert np.abs(err).max() < 1e-6, col
    assert np.allclose(got["lat"], part["lat"]) and np.allclose(got["lon"], part["lon"])
    # queries are time-bounded and grouped
    assert any("GROUP BY time(1s)" in q for q in client.queries)


def test_object_values_stored_as_json(race):
    """Some plugin versions store object values as a JSON string field."""
    import json
    df, truth = race
    part = df.loc[truth.gun: truth.gun + pd.Timedelta(minutes=2)]
    pts = []
    for p in to_influx_points(part):
        if p["measurement"] == schema.POSITION_PATH:
            f = p["fields"]
            p = {**p, "fields": {"jsonValue": json.dumps({"latitude": f["lat"], "longitude": f["lon"]})}}
        elif p["measurement"] == schema.ATTITUDE_PATH:
            p = {**p, "fields": {"jsonValue": json.dumps(p["fields"])}}
        pts.append(p)
    src = InfluxSource(DEFAULTS, client=FakeClient(pts))
    got = src.fetch(part.index[0].to_pydatetime(), part.index[-1].to_pydatetime() + pd.Timedelta(seconds=1))
    assert np.allclose(got["lat"], part["lat"]) and np.allclose(got["lon"], part["lon"])
    assert np.allclose(got["heel"], part["heel"], atol=1e-6)


def test_missing_channels_become_nan(race):
    df, truth = race
    part = df.loc[truth.gun: truth.gun + pd.Timedelta(minutes=2)]
    pts = [p for p in to_influx_points(part) if p["measurement"] != "environment.wind.angleTrueWater"]
    src = InfluxSource(DEFAULTS, client=FakeClient(pts))
    got = src.fetch(part.index[0].to_pydatetime(), part.index[-1].to_pydatetime())
    assert got["twa"].isna().all()
    assert set(schema.CANONICAL_COLUMNS) <= set(got.columns)
