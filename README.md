# SonsAnalyzer

Race performance analysis for **Sons of Hurricanes** (Sinergia 40, NOR 15666), running on a
Raspberry Pi on board. Reads 1 Hz NMEA 2000 data that OpenPlotter/Signal K logs into
InfluxDB 1.x, and serves a web app to phones and laptops over the boat's WiFi, with no internet
needed.

**Setting up data collection on the boat:** see [docs/SETUP.md](docs/SETUP.md).

## What it does

| Page | Contents |
|---|---|
| **Races** | Sailing sessions found automatically in the log. **Find races** detects the racing in a session and creates the races, or create one by hand. |
| **Race** | Start gun and finish (estimated from the data, confirm or nudge ±1 min). Track on the chart, coloured by polar %, VMG %, speed, wind or leg type, with motoring, pre-start and after-finish greyed out. Replay, synced instrument charts, summary tiles. |
| ↳ Legs | Automatic upwind/reach/downwind legs: time, distance, TWS/TWD, BSP, TWA vs target, polar %, VMG %, heel. |
| ↳ Tacks & gybes | Every maneuver: entry/min speed, speed drop, in/out TWA, turn, recovery time and **metres lost**, with averages by wind band. Click to replay. |
| ↳ Wind bands | Performance per wind band and point of sail. Shows where the boat is slow. |
| ↳ Start | Speed and TWA at the gun vs target, time to full speed, ground gained to windward in the first 1 and 2 minutes vs target VMG. With start line positions (optional) also distance to the line, time to cross, position on the line and line bias. |
| **Polar** | The ORC polar, fetched from orc.org (or a CSV): diagram, beat/run targets, speed table. |
| **Live** | Boatspeed vs target, polar %, VMG, target TWA, last maneuver loss, engine on/off. Optional start-line ping buttons. |

### Only the racing is analysed

The log runs continuously, so it also contains motoring, the pre-start and sailing home.
These are left out automatically:

- **Motoring:** engine RPM above 300 (`propulsion.*.revolutions`). Without engine data:
  sustained pointing closer than 28° to the wind, speed above 130 % of the polar, or no heel
  in a breeze.
- **Race, gun and finish:** "racing" = under sail at ≥ 65 % of the beat/run target speed. A
  race is at least 15 minutes of mostly racing. The gun is estimated as the first whole minute
  at or after (start of full-speed sailing − 20 s). The finish is the last second of racing before
  the boat stops racing for good. Both are marked *estimated* until the crew confirms or
  changes them. Times set by the crew always win.

### How the numbers are computed

- **Polar target:** bilinear interpolation of the ORC certificate (TWS 4–24 kn × TWA), with
  ORC's beat and gybe angles and VMG as upwind/downwind targets. ORC allowances (s/nm) are
  converted to knots (3600 / allowance).
- **Polar %** = BSP / target BSP at the actual TWS and TWA. **VMG %** = |VMG| / target VMG.
  Hidden while nearly stopped or head to wind.
- **Maneuvers:** TWA changes sign and the steady state 10–25 s before and 15–30 s after is
  on opposite tacks. Close to 0° = tack, close to 180° = gybe.
- **Metres lost:** distance gained along the wind axis from 10 s before to 30 s after the
  turn, compared with continuing at the pre-turn VMG. Both are from GPS, so current cancels
  out.
- **Legs:** sailing mode with hysteresis (upwind < 70°, downwind > 110°). Legs shorter than
  90 s are merged into a neighbouring leg.

Thresholds are in `config.toml` under `[analysis]`.

## Run it

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
cp config.example.toml config.toml
python -m sonsanalyzer.polar.orc                 # fetch ORC polar (internet)
python -m sonsanalyzer.web.app                   # http://localhost:8080
```

**Demo mode** (no boat or database needed: three simulated races and a simulated live feed):

```bash
python -m sonsanalyzer.web.app --demo
```

**Simulated data into a real InfluxDB** (to test the whole pipeline before the boat is wired up):

```bash
python -m sonsanalyzer.sim.simulator --influx --races 3
```

**Tests:** `python -m pytest -q`. The simulator generates races with known tacks, gybes,
marks and start, and the tests check the analysis finds them.

## Project layout

```
sonsanalyzer/
  config.py            settings (config.toml + defaults)
  data/schema.py       Signal K path ↔ column mapping, unit conversion
  data/influx.py       InfluxDB 1.x reader (signalk-to-influxdb schema)
  data/demo.py         simulated data source for demo mode
  data/store.py        SQLite: races, start-line pings
  polar/polar.py       Polar model (targets, interpolation, CSV/JSON)
  polar/orc.py         ORC data-feed client (finds the certificate by sail number)
  analysis/derive.py   cleaning, true wind, VMG, polar %, sailing mode
  analysis/legs.py     leg segmentation and stats
  analysis/maneuvers.py tack/gybe detection and loss
  analysis/start.py    start-line analysis
  analysis/sessions.py session detection
  analysis/phases.py   race, gun and finish estimation
  analysis/report.py   puts it together for a race
  sim/simulator.py     windward/leeward race simulator
  web/                 FastAPI app, templates, JS (Leaflet + uPlot vendored for offline use)
deploy/sonsanalyzer.service   systemd unit for the Pi
docs/SETUP.md                 OpenPlotter / Signal K / InfluxDB setup guide
```

## Ideas for later

- Compare tacks and gybes across races and over the season (crew work trends).
- Wind shift analysis: TWD trend, lifted/headed time, tacking on shifts.
- Learn a "real" polar from our own data (top percentile per TWS/TWA bin) next to ORC.
- Sail inventory/crew notes per race, and sail-change detection.
- Import competitors' tracks (e.g. from tracking providers) for a head-to-head comparison.
- Export to Expedition/CSV/GPX.
