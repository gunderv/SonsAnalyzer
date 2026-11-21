"""FastAPI web app served from the Raspberry Pi.

Run:  uvicorn sonsanalyzer.web.app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import io
import logging
import math
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from fastapi import Body, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..analysis import report, sessions
from ..config import load_config
from ..data.store import Store
from ..polar import orc
from ..polar.polar import Polar
from ..sim.simulator import load_default_polar

log = logging.getLogger("sonsanalyzer")
HERE = Path(__file__).resolve().parent

TILE_SOURCES = {
    "osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "seamark": "https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png",
}
TILE_HEADERS = {"User-Agent": "SonsAnalyzer/0.1 (sailing performance tool)"}


class State:
    def __init__(self):
        self.cfg = load_config()
        self.data_dir = Path(self.cfg["app"]["data_dir"])
        self.store = Store(self.data_dir)
        self.polar = self._load_polar()
        self.source = self._make_source()
        self._cache: dict = {}
        self._lock = threading.Lock()

    def _load_polar(self) -> Polar | None:
        p = self.data_dir / "polar.json"
        if p.is_file():
            return Polar.load(p)
        if self.cfg["app"]["source"] == "demo":
            return load_default_polar(self.data_dir)
        return None

    def _make_source(self):
        if self.cfg["app"]["source"] == "demo":
            from ..data.demo import DemoSource

            src = DemoSource(self.polar or load_default_polar(self.data_dir))
            src.seed_store(self.store)
            return src
        from ..data.influx import InfluxSource

        return InfluxSource(self.cfg)

    def forget(self, race_id: int) -> None:
        """Drop cached analyses of a race (its settings changed)."""
        with self._lock:
            for key in [k for k in self._cache if k[0] == race_id]:
                del self._cache[key]

    def polar_version(self) -> float:
        p = self.data_dir / "polar.json"
        return p.stat().st_mtime if p.is_file() else 0.0


state: State | None = None


@asynccontextmanager
async def lifespan(_app):
    global state
    state = State()
    yield


app = FastAPI(title="SonsAnalyzer", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def S() -> State:
    global state
    if state is None:
        state = State()
    return state


def clean(obj):
    """Make numpy / pandas values JSON friendly (NaN -> None)."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if math.isnan(obj) or math.isinf(obj) else float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    return obj


def parse_time(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------------------------------
# Pages
# ------------------------------------------------------------------------------------------
def page(request: Request, name: str, **ctx):
    s = S()
    return templates.TemplateResponse(request, name, {
        "boat": s.cfg["boat"], "source": s.cfg["app"]["source"], "has_polar": s.polar is not None,
        "version": __version__, **ctx})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return page(request, "index.html", active="races")


@app.get("/race/{race_id}", response_class=HTMLResponse)
def race_page(request: Request, race_id: int):
    race = S().store.get_race(race_id)
    if not race:
        raise HTTPException(404, "Race not found")
    return page(request, "race.html", active="races", race=race)


@app.get("/polar", response_class=HTMLResponse)
def polar_page(request: Request):
    return page(request, "polar.html", active="polar")


@app.get("/live", response_class=HTMLResponse)
def live_page(request: Request):
    return page(request, "live.html", active="live")


# ------------------------------------------------------------------------------------------
# API: sessions and races
# ------------------------------------------------------------------------------------------
@app.get("/api/sessions")
def api_sessions(days: int = 30):
    s = S()
    end = datetime.now(timezone.utc)
    act = s.source.activity(end - timedelta(days=days), end)
    a = s.cfg["analysis"]
    return sessions.detect_sessions(act, a["session_min_sog_kn"], a["session_min_minutes"],
                                    a["session_max_gap_minutes"])


@app.post("/api/races/detect")
def api_detect_races(data: dict = Body(...)):
    """Find races (gun/finish estimates) in a time window, e.g. a sailing session.
    With "create": true they are also saved as races (gun/finish left empty = estimated)."""
    from ..analysis import derive, phases

    s = S()
    start, end = parse_time(data["start"]), parse_time(data["end"])
    raw = s.source.fetch(start, end)
    if raw.empty:
        raise HTTPException(404, "No boat data in this time window")
    df = derive.enrich(raw, s.polar, s.cfg["analysis"])
    found = phases.find_races(df, s.cfg["analysis"])
    out = []
    for n, r in enumerate(found, 1):
        item = {k: r[k] for k in ("gun", "finish", "window_start", "window_end", "duration_s")}
        if data.get("create"):
            local = r["gun"].tz_convert(data.get("tz") or "UTC") if data.get("tz") else r["gun"]
            name = f"Race {local:%d.%m.%Y %H:%M}" + (f" #{n}" if len(found) > 1 else "")
            rid = s.store.create_race({"name": name, "start_time": r["window_start"].to_pydatetime(),
                                       "end_time": r["window_end"].to_pydatetime()})
            item["race_id"] = rid
        out.append(item)
    return clean({"races": out, "motoring_source": df.attrs.get("motoring_source")})


@app.get("/api/races")
def api_races():
    return S().store.list_races()


@app.post("/api/races")
def api_create_race(data: dict = Body(...)):
    for k in ("name", "start_time", "end_time"):
        if not data.get(k):
            raise HTTPException(400, f"{k} is required")
    rid = S().store.create_race(data)
    return S().store.get_race(rid)


@app.put("/api/races/{race_id}")
def api_update_race(race_id: int, data: dict = Body(...)):
    s = S()
    if not s.store.get_race(race_id):
        raise HTTPException(404)
    s.store.update_race(race_id, data)
    s.forget(race_id)
    return s.store.get_race(race_id)


@app.delete("/api/races/{race_id}")
def api_delete_race(race_id: int):
    S().store.delete_race(race_id)
    S().forget(race_id)
    return {"ok": True}


def resolve_line(race: dict) -> dict | None:
    """Start line: manual values on the race > pings before the gun > line from the bus."""
    if race.get("pin_lat") is not None and race.get("boat_lat") is not None:
        return {"pin": (race["pin_lat"], race["pin_lon"]), "boat": (race["boat_lat"], race["boat_lon"]),
                "source": "race"}
    if not race.get("gun_time"):
        return None
    s = S()
    gun = parse_time(race["gun_time"])
    pin = s.store.latest_ping("pin", gun)
    boat = s.store.latest_ping("boat", gun)
    if pin and boat:
        return {"pin": (pin["lat"], pin["lon"]), "boat": (boat["lat"], boat["lon"]), "source": "pings"}
    try:
        line = s.source.start_line(gun - timedelta(hours=1), gun + timedelta(minutes=1))
    except Exception:  # noqa: BLE001 - optional data
        line = None
    if line:
        return {**line, "source": "instruments"}
    return None


@app.get("/api/races/{race_id}/analysis")
def api_analysis(race_id: int):
    s = S()
    race = s.store.get_race(race_id)
    if not race:
        raise HTTPException(404)
    key = (race_id, race["updated_at"], s.polar_version())
    with s._lock:
        if key in s._cache:
            return JSONResponse(s._cache[key])
    raw = s.source.fetch(parse_time(race["start_time"]), parse_time(race["end_time"]))
    if raw.empty or raw[["lat", "bsp", "sog"]].isna().all().all():
        raise HTTPException(404, "No boat data in this time window")
    line = resolve_line(race)
    gun = parse_time(race["gun_time"]) if race.get("gun_time") else None
    finish = parse_time(race["finish_time"]) if race.get("finish_time") else None
    result = report.analyze_race(raw, s.polar, s.cfg["analysis"], gun=gun, finish=finish, line=line)
    result["race"] = race
    result["line_source"] = line["source"] if line else None
    result["has_polar"] = s.polar is not None
    payload = clean(result)
    with s._lock:
        if len(s._cache) > 8:
            s._cache.clear()
        s._cache[key] = payload
    return JSONResponse(payload)


# ------------------------------------------------------------------------------------------
# API: polar
# ------------------------------------------------------------------------------------------
@app.get("/api/polar")
def api_polar():
    p = S().polar
    if p is None:
        return {"polar": None}
    curves = {}
    for tws in p.tws:
        twa = list(range(30, 181, 2))
        curves[str(float(tws))] = [round(float(v), 2) for v in p.target_bsp(np.full(len(twa), tws), np.array(twa))]
    return clean({"polar": {"meta": p.meta, "table": p.table(), "curves": curves,
                            "curve_twa": list(range(30, 181, 2))}})


@app.post("/api/polar/fetch")
def api_polar_fetch(data: dict = Body(default={})):
    s = S()
    sail_no = data.get("sail_no") or s.cfg["boat"]["sail_no"]
    try:
        s.polar = orc.fetch_polar(sail_no, s.data_dir)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except requests.RequestException as e:
        raise HTTPException(502, f"Could not reach ORC (internet needed): {e}")
    s._cache.clear()
    return clean({"ok": True, "meta": s.polar.meta})


@app.post("/api/polar/upload")
async def api_polar_upload(file: UploadFile):
    s = S()
    text = (await file.read()).decode("utf-8-sig")
    try:
        polar = Polar.from_csv(text, {"name": file.filename})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not parse polar CSV: {e}")
    polar.save(s.data_dir / "polar.json")
    s.polar = polar
    s._cache.clear()
    return clean({"ok": True, "meta": polar.meta})


# ------------------------------------------------------------------------------------------
# API: live + start line pings
# ------------------------------------------------------------------------------------------
@app.get("/api/live")
def api_live():
    s = S()
    df = s.source.latest(60)
    if df.empty:
        return {"ok": False, "message": "No recent data"}
    from ..analysis import derive, maneuvers

    d = derive.enrich(df, s.polar, s.cfg["analysis"])
    last = d.ffill().iloc[-1]
    out = {"ok": True, "time": d.index[-1].isoformat()}
    for c in ("lat", "lon", "bsp", "sog", "cog", "hdg", "tws", "twd", "twa", "aws", "awa", "heel",
              "vmg", "polar_pct", "vmg_pct", "target_bsp", "target_twa", "target_vmg",
              "target_vmg_bsp", "depth", "engine_rpm"):
        out[c] = last.get(c)
    out["mode"] = last.get("mode")
    out["age_s"] = (datetime.now(timezone.utc) - d.index[-1].to_pydatetime()).total_seconds()
    if s.polar is not None and not pd.isna(last.get("tws")):
        out["targets"] = s.polar.targets(float(last["tws"]))
    mans = maneuvers.detect(d, s.cfg["analysis"]) if len(d) >= 60 else []
    out["last_maneuver"] = mans[-1] if mans else None
    # distance to the pinged line (if both ends pinged in the last 12 h)
    now = datetime.now(timezone.utc)
    pin, boat = s.store.latest_ping("pin", now), s.store.latest_ping("boat", now)
    out["line"] = None
    if pin and boat and not pd.isna(last.get("lat")):
        out["line"] = line_distance(last["lat"], last["lon"], pin, boat)
    return clean(out)


def line_distance(lat, lon, pin, boat) -> dict:
    lat0 = (pin["lat"] + boat["lat"]) / 2
    k = 6371000 * math.pi / 180

    def xy(la, lo):
        return np.array([(lo - pin["lon"]) * k * math.cos(math.radians(lat0)), (la - pin["lat"]) * k])

    b = xy(boat["lat"], boat["lon"])
    p = xy(lat, lon)
    length = float(np.hypot(*b))
    u = b / length if length else b
    along = float(p @ u)
    perp = float(abs(p[0] * u[1] - p[1] * u[0]))
    return {"distance_m": round(perp, 1), "along_pct": round(100 * along / length, 1) if length else None,
            "length_m": round(length, 1)}


@app.post("/api/ping/{end_name}")
def api_ping(end_name: str, data: dict = Body(default={})):
    if end_name not in ("pin", "boat"):
        raise HTTPException(400, "end must be 'pin' or 'boat'")
    s = S()
    lat, lon = data.get("lat"), data.get("lon")
    if lat is None or lon is None:
        df = s.source.latest(20)
        pos = df[["lat", "lon"]].dropna() if not df.empty else df
        if pos.empty:
            raise HTTPException(409, "No GPS position available")
        lat, lon = float(pos["lat"].iloc[-1]), float(pos["lon"].iloc[-1])
    return s.store.add_ping(end_name, float(lat), float(lon))


@app.get("/api/pings")
def api_pings():
    return S().store.list_pings()


# ------------------------------------------------------------------------------------------
# Map tile cache (works offline for areas viewed once while online)
# ------------------------------------------------------------------------------------------
@app.get("/tiles/{layer}/{z}/{x}/{y}.png")
def tiles(layer: str, z: int, x: int, y: int):
    if layer not in TILE_SOURCES or not (0 <= z <= 18):
        raise HTTPException(404)
    path = S().data_dir / "tiles" / layer / str(z) / str(x) / f"{y}.png"
    if path.is_file():
        return Response(path.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=604800"})
    try:
        r = requests.get(TILE_SOURCES[layer].format(z=z, x=x, y=y), headers=TILE_HEADERS, timeout=6)
    except requests.RequestException:
        raise HTTPException(404)
    if r.status_code != 200 or not r.content:
        raise HTTPException(404)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return Response(r.content, media_type="image/png")


@app.get("/api/status")
def api_status():
    s = S()
    return {"version": __version__, "source": s.cfg["app"]["source"],
            "polar": s.polar.meta if s.polar else None}


def main():  # pragma: no cover
    import argparse
    import os

    import uvicorn

    ap = argparse.ArgumentParser(description="SonsAnalyzer web app")
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("--demo", action="store_true", help="use simulated races instead of InfluxDB")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()
    if args.config:
        os.environ["SONSANALYZER_CONFIG"] = args.config
    if args.demo:
        os.environ["SONSANALYZER_SOURCE"] = "demo"
    cfg = load_config()
    uvicorn.run("sonsanalyzer.web.app:app", host=cfg["app"]["host"], port=args.port or int(cfg["app"]["port"]))


if __name__ == "__main__":  # pragma: no cover
    main()
