"""Synthetic windward/leeward race generator.

Produces the same canonical DataFrame the data sources return (knots / degrees, 1 Hz), plus
the ground truth (maneuvers, mark roundings, start line) for tests. It can also write the data
to InfluxDB in the Signal K schema, to exercise the full pipeline before the boat is wired up:

    python -m sonsanalyzer.sim.simulator --influx            # writes one race ending now
    python -m sonsanalyzer.sim.simulator --influx --races 3  # three races on consecutive days
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..data import schema
from ..data.schema import wrap180, wrap360
from ..polar.polar import Polar

KN = 1852.0 / 3600.0  # knots -> m/s
EARTH_R = 6371000.0


@dataclass
class RaceTruth:
    gun: pd.Timestamp
    pin: tuple[float, float]
    boat: tuple[float, float]
    maneuvers: list[tuple[pd.Timestamp, str]] = field(default_factory=list)
    roundings: list[pd.Timestamp] = field(default_factory=list)
    finish: pd.Timestamp | None = None
    motoring: list[tuple[pd.Timestamp, pd.Timestamp]] = field(default_factory=list)


def _ll(x, y, lat0, lon0):
    lat = lat0 + np.rad2deg(y / EARTH_R)
    lon = lon0 + np.rad2deg(x / (EARTH_R * np.cos(np.deg2rad(lat0))))
    return lat, lon


def simulate_race(polar: Polar, gun: datetime, seed: int = 1, lat0: float = 59.03,
                  lon0: float = 10.62, tws_mean: float = 11.0, twd_mean: float = 200.0,
                  leg_nm: float = 1.2, laps: int = 2, current_kn: float = 0.3,
                  current_dir: float = 45.0, performance: float = 0.96,
                  prestart_s: int = 300, motor_before_s: int = 0, motor_after_s: int = 0,
                  engine_data: bool = True, motor_home_twa: float = 15.0,
                  ) -> tuple[pd.DataFrame, RaceTruth]:
    """Simulate one race. Optional motoring out to the course before the pre-start and
    motoring home after the finish (sails dropped head to wind for 90 s in between).
    engine_data=False leaves engine RPM empty, as on boats without an engine gateway."""
    rng = np.random.default_rng(seed)
    gun = pd.Timestamp(gun)
    gun = gun.tz_localize("UTC") if gun.tzinfo is None else gun.tz_convert("UTC")

    # Geometry (metres, x east / y north). Start line square to mean wind, 200 m long.
    up = np.array([np.sin(np.deg2rad(twd_mean)), np.cos(np.deg2rad(twd_mean))])
    right = np.array([up[1], -up[0]])  # 90 deg to the right of upwind, looking upwind
    line_mid = np.zeros(2)
    pin_xy = line_mid - right * 100  # pin = port (left) end looking upwind
    boat_xy = line_mid + right * 100
    windward = line_mid + up * leg_nm * 1852
    leeward = line_mid - up * 60
    marks = []
    for _ in range(laps):
        marks += [("upwind", windward), ("downwind", leeward)]
    marks[-1] = ("downwind", line_mid + up * 10)  # finish at the start line

    # Wind fields
    n_max = motor_before_s + prestart_s + 4 * 3600 + motor_after_s + 120
    t0 = motor_before_s + prestart_s  # sample index of the gun
    MOTOR_KN = 6.5
    t = np.arange(n_max)
    phase = rng.uniform(0, 2 * np.pi, 3)
    twd = twd_mean + 7 * np.sin(2 * np.pi * t / 540 + phase[0]) + np.cumsum(rng.normal(0, 0.08, n_max))
    tws = tws_mean + 1.8 * np.sin(2 * np.pi * t / 780 + phase[1]) + rng.normal(0, 0.35, n_max)
    tws = np.clip(tws, 2, None)
    cur = current_kn * KN * np.array([np.sin(np.deg2rad(current_dir)), np.cos(np.deg2rad(current_dir))])

    # Pre-start: hold a spot below the line, then approach close-hauled on starboard, slowly
    # at first and building speed over the last ~35 s, timed to hit the line ~3 s after the
    # gun at 55 % along it.
    tg0 = polar.targets(tws_mean)
    approach_s = 75
    build_s = 35
    v_full = tg0["up_bsp"] * performance * KN
    v_app = (0.45 * v_full * (approach_s - build_s) + 0.8 * v_full * (build_s + 3)) / (approach_s + 3)
    stb_hdg = wrap360(twd_mean - tg0["up_twa"])
    stb_vec = np.array([np.sin(np.deg2rad(stb_hdg)), np.cos(np.deg2rad(stb_hdg))])
    cross_pt = pin_xy + (boat_xy - pin_xy) * 0.55
    approach_from = cross_pt - (stb_vec * v_app + cur) * (approach_s + 3)

    rows = []
    truth = RaceTruth(gun=gun, pin=tuple(_ll(*pin_xy, lat0, lon0)), boat=tuple(_ll(*boat_xy, lat0, lon0)))
    hold_pt = approach_from.copy() - up * 40
    pos = hold_pt.copy()
    hdg = wrap360(twd_mean - 60)
    bsp = 1.0
    state = "hold"
    motor_start = None
    post_k = 0
    if motor_before_s:
        # come from downwind (harbour) motoring roughly head to wind
        pos = hold_pt - up * motor_before_s * MOTOR_KN * KN * 0.95
        hdg = wrap360(twd_mean)
        bsp = MOTOR_KN
        state = "motor_out"
        motor_start = gun - pd.Timedelta(seconds=t0)
    tack_side = 1  # +1 starboard (wind from starboard), -1 port
    leg_i = 0
    turn_kind = None
    tgt_twa = tg0["up_twa"]
    corridor = rng.uniform(250, 450)

    for k in range(n_max):
        rel = k - t0
        wd, ws = twd[k], tws[k]
        tg = polar.targets(ws)

        # ---------------- steering ----------------
        if state == "motor_out":
            to_pt = hold_pt - pos
            desired = wrap360(np.rad2deg(np.arctan2(to_pt[0], to_pt[1])))
            target_speed = MOTOR_KN
            if rel >= -prestart_s:
                truth.motoring.append((motor_start, gun + pd.Timedelta(seconds=rel)))
                state = "hold"
        if state == "cooldown":  # sails coming down, head to wind
            desired = wrap360(wd)
            target_speed = 1.5
            post_k += 1
            if post_k >= 90:
                state = "motor_home"
                motor_start = gun + pd.Timedelta(seconds=rel)
        if state == "motor_home":
            desired = wrap360(wd - motor_home_twa)
            target_speed = MOTOR_KN
            post_k += 1
            if post_k >= 90 + motor_after_s:
                truth.motoring.append((motor_start, gun + pd.Timedelta(seconds=rel)))
                break
        if state == "hold":
            # get to the approach point slowly
            to_pt = approach_from - pos
            dist_pt = np.hypot(*to_pt)
            desired = wrap360(np.rad2deg(np.arctan2(to_pt[0], to_pt[1]))) if dist_pt > 15 else wrap360(wd - 55)
            target_speed = 2.5 if dist_pt > 15 else 1.0
            if rel >= -approach_s:
                state = "approach"
        if state == "approach":
            # aim at the chosen point on the line, pace speed to arrive ~3 s after the gun
            to_pt = cross_pt - pos
            dist_pt = np.hypot(*to_pt)
            remaining = max(5.0, 3.0 - rel)
            desired = wrap360(np.rad2deg(np.arctan2(to_pt[0], to_pt[1])))
            cap = tg["up_bsp"] * performance * (0.45 if rel < -build_s else 1.0)
            target_speed = float(np.clip(dist_pt / remaining / KN, 1.0, cap))
            if rel >= 0:
                target_speed = tg["up_bsp"] * performance
            if rel >= 8:
                state, tack_side = "racing", 1
        if state in ("racing", "turning") and leg_i < len(marks):
            mode, mark = marks[leg_i]
            to_mark = mark - pos
            dist_mark = np.hypot(*to_mark)
            brg = wrap360(np.rad2deg(np.arctan2(to_mark[0], to_mark[1])))
            tgt_twa = tg["up_twa"] if mode == "upwind" else tg["down_twa"]

            if state == "racing":
                if dist_mark < 60:
                    truth.roundings.append(gun + pd.Timedelta(seconds=rel))
                    leg_i += 1
                    if leg_i >= len(marks):
                        truth.finish = gun + pd.Timedelta(seconds=rel)
                        if not motor_after_s:
                            break
                        state = "cooldown"
                    else:
                        corridor = rng.uniform(250, 450)
                        mode, mark = marks[leg_i]
                        tgt_twa = tg["up_twa"] if mode == "upwind" else tg["down_twa"]
                else:
                    other = wrap360(wd + tack_side * tgt_twa)  # heading on the other tack
                    lay = abs(wrap180(brg - other)) < 4
                    # sideways distance from the rhumb line, in the direction we are moving
                    axis = up if mode == "upwind" else -up
                    rhumb_right = np.array([axis[1], -axis[0]])
                    my_hdg = wrap360(wd - tack_side * tgt_twa)
                    lateral = np.sign(np.array([np.sin(np.deg2rad(my_hdg)), np.cos(np.deg2rad(my_hdg))]) @ rhumb_right)
                    offset = (pos - mark) @ rhumb_right
                    too_far = offset * lateral > corridor
                    if (lay or (too_far and dist_mark > 150)):
                        state = "turning"
                        turn_kind = "tack" if mode == "upwind" else "gybe"
                        tack_side = -tack_side
                        truth.maneuvers.append((gun + pd.Timedelta(seconds=rel), turn_kind))
            desired = wrap360(wd) if state == "cooldown" else wrap360(wd - tack_side * tgt_twa)
            if state == "cooldown":
                target_speed = 1.5
            if state == "racing" and dist_mark < 400:
                # fetching: steer straight at the mark when it is sailable on this tack
                off_wind = wrap180(wd - brg)
                sailable = (abs(off_wind) >= tgt_twa - 1) if mode == "upwind" else (abs(off_wind) <= tgt_twa + 1)
                if sailable and np.sign(off_wind) == tack_side:
                    desired = brg
            if state == "turning" and abs(wrap180(desired - hdg)) < 4:
                state = "racing"
                corridor = rng.uniform(250, 450)

        # ---------------- dynamics ----------------
        # Turning: the shortest rotation is correct for both tacks (through 0) and
        # gybes (through 180) because the two targets lie either side of the wind axis.
        rate = 6.0 if state == "turning" else 3.0
        d_h = wrap180(desired - hdg)
        hdg = wrap360(hdg + np.clip(d_h, -rate, rate) + rng.normal(0, 0.6))
        twa_now = wrap180(wd - hdg)
        if state == "racing":
            target_speed = polar.target_bsp(ws, abs(twa_now)) * performance
        elif state == "turning":
            target_speed = polar.target_bsp(ws, tgt_twa) * performance * (0.55 if turn_kind == "tack" else 0.8)
        bsp += (target_speed - bsp) / 7.0 + rng.normal(0, 0.03)
        bsp = max(0.0, bsp)

        vel = bsp * KN * np.array([np.sin(np.deg2rad(hdg)), np.cos(np.deg2rad(hdg))]) + cur
        pos = pos + vel
        sog = np.hypot(*vel) / KN
        cog = wrap360(np.rad2deg(np.arctan2(vel[0], vel[1])))

        u = ws * np.cos(np.deg2rad(twa_now)) + bsp
        v = ws * np.sin(np.deg2rad(twa_now))
        aws, awa = np.hypot(u, v), np.rad2deg(np.arctan2(v, u))
        motoring = state in ("motor_out", "motor_home")
        if motoring or state == "cooldown":
            heel = 0.0  # no sails up
        else:
            heel = -np.sign(awa) * min(28.0, 0.25 * aws ** 1.5 * abs(np.sin(np.deg2rad(awa))) ** 1.2)
        rpm = (2200 + rng.normal(0, 20)) if motoring else 0.0
        lat, lon = _ll(pos[0], pos[1], lat0, lon0)
        rows.append({
            "time": gun + pd.Timedelta(seconds=rel), "lat": lat, "lon": lon, "sog": sog, "cog": cog,
            "bsp": bsp, "hdg": hdg, "awa": awa, "aws": aws, "twa": twa_now, "tws": ws,
            "twd": wrap360(wd), "heel": heel + rng.normal(0, 0.5), "pitch": rng.normal(0, 1.0),
            "rudder": np.clip(d_h, -25, 25) * 0.5 + rng.normal(0, 0.8),
            "depth": 25 + 10 * np.sin(k / 400.0),
            "engine_rpm": rpm if engine_data else np.nan,
        })

    # sail a few minutes after the finish
    df = pd.DataFrame(rows).set_index("time")
    df.index = pd.DatetimeIndex(df.index)
    for c in schema.CANONICAL_COLUMNS:
        if c not in df:
            df[c] = np.nan
    df["leeway"] = np.nan
    df["hdg_mag"] = np.nan
    df["variation"] = np.nan
    return df, truth


# ---------------------------------------------------------------------------------------------
# Writing to InfluxDB in the Signal K schema
# ---------------------------------------------------------------------------------------------
def to_influx_points(df: pd.DataFrame) -> list[dict]:
    points = []
    by_col = {ch.column: ch for ch in schema.CHANNELS}
    for ts, row in df.iterrows():
        t = int(ts.timestamp())
        for col in ("sog", "cog", "bsp", "hdg", "awa", "aws", "twa", "tws", "twd", "rudder", "depth"):
            v = row.get(col)
            if pd.notna(v):
                ch = by_col[col]
                val = float(schema.to_si(v, ch.unit))
                points.append({"measurement": ch.path, "time": t,
                               "tags": {"context": "vessels.self", "source": "sim"},
                               "fields": {"value": val}})
        if pd.notna(row.get("engine_rpm")):
            points.append({"measurement": "propulsion.main.revolutions", "time": t,
                           "tags": {"context": "vessels.self", "source": "sim"},
                           "fields": {"value": float(row["engine_rpm"]) / 60.0}})
        points.append({"measurement": schema.POSITION_PATH, "time": t,
                       "tags": {"context": "vessels.self", "source": "sim"},
                       "fields": {"lat": float(row["lat"]), "lon": float(row["lon"])}})
        points.append({"measurement": schema.ATTITUDE_PATH, "time": t,
                       "tags": {"context": "vessels.self", "source": "sim"},
                       "fields": {"roll": float(np.deg2rad(row["heel"])),
                                  "pitch": float(np.deg2rad(row["pitch"])), "yaw": 0.0}})
    return points


def write_influx(df: pd.DataFrame, cfg: dict) -> int:
    from influxdb import InfluxDBClient

    ic = cfg["influx"]
    client = InfluxDBClient(ic["host"], ic["port"], ic["username"] or None, ic["password"] or None,
                            ic["database"])
    client.create_database(ic["database"])  # no-op if it exists
    points = to_influx_points(df)
    client.write_points(points, time_precision="s", batch_size=5000)
    return len(points)


def load_default_polar(data_dir) -> Polar:
    """Polar from data/polar.json, else the bundled ORC fixture (tests/fixtures)."""
    import json
    from pathlib import Path

    p = Path(data_dir) / "polar.json"
    if p.is_file():
        return Polar.load(p)
    fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "orc_cert_sample.json"
    return Polar.from_orc(json.loads(fixture.read_text()))


def main():  # pragma: no cover
    from ..config import load_config

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--influx", action="store_true", help="write to InfluxDB from config.toml")
    ap.add_argument("--races", type=int, default=1)
    ap.add_argument("--csv", help="write canonical CSV to this file instead")
    args = ap.parse_args()
    cfg = load_config()
    polar = load_default_polar(cfg["app"]["data_dir"])
    now = datetime.now(timezone.utc).replace(microsecond=0)
    frames = []
    for i in range(args.races):
        gun = now - timedelta(days=args.races - 1 - i, hours=2)
        df, truth = simulate_race(polar, gun, seed=i + 1, tws_mean=8 + 3 * i)
        frames.append(df)
        print(f"Race {i + 1}: gun {truth.gun}, {len(truth.maneuvers)} maneuvers, "
              f"pin {truth.pin}, boat {truth.boat}")
    df = pd.concat(frames)
    if args.csv:
        df.to_csv(args.csv)
        print(f"Wrote {len(df)} rows to {args.csv}")
    if args.influx:
        n = write_influx(df, cfg)
        print(f"Wrote {n} points to InfluxDB {cfg['influx']['host']}/{cfg['influx']['database']}")


if __name__ == "__main__":  # pragma: no cover
    main()
