# Data collection setup – OpenPlotter + Signal K + InfluxDB on the Raspberry Pi

This guide gets every value on the boat's NMEA 2000 network logged once per second into
InfluxDB 1.x on a Raspberry Pi 4, in the format SonsAnalyzer reads. Expect an evening at
the kitchen table plus an hour on board.

```
NMEA 2000 backbone ──► N2K interface ──► Signal K server ──► signalk-to-influxdb ──► InfluxDB 1.x
 (instruments, GPS,      (PICAN-M HAT      (part of           (plugin, 1 s)           database "boatdata"
  compass, wind…)         or USB gateway)   OpenPlotter)                                    │
                                                                                            ▼
                                             phone / laptop ◄── WiFi ◄── SonsAnalyzer web app (port 8080)
```

---

## 1. Hardware

| Item | Notes |
|---|---|
| Raspberry Pi 4, 4 GB or 8 GB | 2 GB works but is tight with InfluxDB + Signal K + analysis. |
| **USB 3 SSD** (≥ 64 GB) to boot from | Strongly recommended. InfluxDB writes all the time and wears out SD cards, often after one season. |
| NMEA 2000 interface | **PICAN-M HAT** (CAN + 12 V supply on one board, shows up as `can0`), or a USB gateway: **Actisense NGT-1 / NGX-1**, or **Yacht Devices YDNU-02**. Use a proper drop cable and T-piece. |
| 12 V → 5 V supply | PICAN-M includes one. Otherwise use a marine 12 V/5 V 3 A converter. Brown-outs while starting the engine corrupt databases. |
| Optional: RTC module | The Pi has no clock. Without internet, time comes from GPS (step 7). An RTC (e.g. DS3231) adds belt and braces. |

Power: let the Pi shut down cleanly (`sudo shutdown -h now`, or a shutdown button) before
switching off the instrument breaker. Pulling power mid-write is the most common cause of a
corrupted database.

## 2. Install OpenPlotter

1. Download the latest **OpenPlotter** image (64-bit, Debian Bookworm based) from
   <https://openmarine.net/openplotter> and write it to the SSD with Raspberry Pi Imager
   ("Use custom image"). Recent Pi 4 firmware boots from USB with no SD card.
2. Boot, then follow the first-run wizard: language, time zone (**Europe/Oslo**; logging is
   always UTC, the web app shows local time), and change the default password.
3. **OpenPlotter Settings → Network**: create the **Access Point** (e.g. SSID `SonsOfHurricanes`)
   so phones and laptops can join the Pi's own WiFi on board. If the boat has a 4G router,
   join it as a client instead. Either works.
4. Note the hostname (default `openplotter`). The Pi is then reachable at
   `http://openplotter.local` or `http://10.10.10.1` on the access point.

## 3. Connect NMEA 2000 to Signal K

The Signal K server is preinstalled. Its admin UI is at `http://openplotter.local:3000`.

**PICAN-M HAT**
1. OpenPlotter Settings → **CAN Bus** app → add the `can0` interface (MCP2515, 16 MHz, 250 kbit/s).
2. In the CAN Bus app, press **Add Signal K connection**, or in the Signal K admin:
   *Server → Data Connections → Add*: Type **NMEA 2000**, Source **canbus (canboatjs)**, Interface `can0`.

**Actisense NGT-1 / NGX-1 or YDNU-02 (USB)**
- *Server → Data Connections → Add*: Type **NMEA 2000**, Source **Actisense NGT-1 (canboatjs)**
  (YDNU-02: **Yacht Devices**), serial port = the device under `/dev/ttyUSB…` /
  `/dev/serial/by-id/…`, baud 115200.

Restart the server, then open **Data Browser**. Check that these paths are updating:

| Path | What | Needed for |
|---|---|---|
| `navigation.position` | GPS | map, distances, start, maneuver loss |
| `navigation.speedOverGround`, `navigation.courseOverGroundTrue` | GPS | |
| `navigation.speedThroughWater` | log / paddle wheel | polar %, VMG |
| `navigation.headingTrue` (or `headingMagnetic` + `magneticVariation`) | compass | true wind, maneuvers |
| `environment.wind.angleApparent`, `environment.wind.speedApparent` | masthead unit | true wind |
| `environment.wind.angleTrueWater`, `environment.wind.speedTrue`, `environment.wind.directionTrue` | instrument processor | **everything** |
| `navigation.attitude` | heel/pitch sensor | heel, motoring detection |
| `propulsion.<id>.revolutions` | engine (PGN 127488, Engine Rapid) | **motoring detection** (recommended) |
| `steering.rudderAngle` | rudder sensor | optional |
| `environment.depth.belowTransducer` | depth | optional |

**Engine RPM** is worth getting onto the bus. The log runs all the time, so it also records
motoring out, the pre-start and motoring home. With engine RPM, SonsAnalyzer leaves every
second under engine out of the analysis, exactly. Modern engines with an NMEA 2000 or J1939
interface (Volvo Penta EVC, Yanmar with gateway, …) send it already. Older engines can use a
small gateway that counts alternator W-terminal or tacho pulses (e.g. Yacht Devices Engine
Gateway). Without it, motoring is inferred from boat movement: pointing too close to the wind,
going faster than the polar, or no heel in a breeze. That works well, except for motoring
downwind at sailing speed, which can't be told apart from sailing.

## 4. True wind

Analysis quality depends on true wind more than anything else.

- **Preferred:** the instrument system (B&G H5000, Garmin/Nexus, Raymarine…) computes true
  wind with its own calibration and publishes it on the bus (PGN 130306). Signal K then fills
  `environment.wind.angleTrueWater` / `speedTrue` / `directionTrue` automatically.
- **If the instruments do not publish true wind:** install the **signalk-derived-data** plugin
  (*Appstore → Available*) and enable *True Wind Angle, Direction and Speed*. SonsAnalyzer
  also computes true wind from apparent wind + boatspeed if it is missing, so this is a bonus.

**Calibration matters.** A boatspeed log reading 5 % low makes the boat look 5 % slow on
every leg. A TWA that differs between tacks makes one tack look bad, and TWS drifting with heel
skews the wind-band tables. Spend a calm day on calibration: log speed against GPS
in slack water on both tacks, compass deviation, and the TWA tack-to-tack correction. Then
check that TWD stays roughly the same on both tacks and before and after a gybe.

## 5. InfluxDB 1.x

Debian Bookworm ships InfluxDB 1.x, which is all we need (InfluxQL, no tokens):

```bash
sudo apt update
sudo apt install -y influxdb influxdb-client
sudo systemctl enable --now influxdb
influx -execute 'CREATE DATABASE boatdata'
influx -execute 'SHOW DATABASES'
```

(For a newer 1.x than Debian's, add InfluxData's apt repository and install the `influxdb`
package. Do **not** install `influxdb2`: that is version 2.x with a different query language.)

Move the data to the SSD if the OS isn't already on it. Data lives in `/var/lib/influxdb`.

Retention: the default policy (`autogen`) keeps data forever. At 1 Hz, about 30 paths and
racing a few days a month, a full season stays well under 1 GB.

## 6. Signal K → InfluxDB

1. Signal K admin → *Appstore → Available* → install **signalk-to-influxdb**
   (the InfluxDB **1.x** plugin, *not* `signalk-to-influxdb2`). Restart.
2. *Server → Plugin Config → InfluxDb writer*:
   - Host `localhost`, Port `8086`, Database `boatdata`, no username/password
   - **Record only self data**: on (otherwise AIS targets fill the database)
   - **Resolution**: `1000` ms (one value per path per second)
   - Leave the path filter empty (record everything) or add the paths in the table above.
   - Enable the plugin.
3. The plugin writes one measurement per Signal K path (`navigation.speedThroughWater` …) with
   the value in field `value`, in SI units (m/s, radians), tagged with `source` and `context`.
   That is the schema SonsAnalyzer reads.

## 7. Clock from GPS

Without internet the Pi does not know the time after a power cycle, and every log line would
be stamped wrong. Install the **set-system-time** plugin in the Signal K Appstore, enable it,
and tick "use sudo" if asked. It sets the clock from the GPS a few seconds after boot.

## 8. Check that data is arriving

Sail or motor for a few minutes (or wait for GPS at the dock), then:

```bash
influx -database boatdata -execute 'SHOW MEASUREMENTS'
influx -database boatdata -execute 'SELECT * FROM "navigation.speedOverGround" ORDER BY time DESC LIMIT 5'
influx -database boatdata -execute 'SELECT * FROM "environment.wind.angleTrueWater" ORDER BY time DESC LIMIT 5'
influx -database boatdata -execute 'SHOW FIELD KEYS FROM "navigation.position"'
```

Expect a row per second with recent timestamps (UTC). `navigation.position` should show
`lat`/`lon` (or a `jsonValue`) field. SonsAnalyzer handles both.

## 9. Install SonsAnalyzer

Copy this project folder to the Pi (e.g. `/home/pi/SonsAnalyzer`) with a USB stick, `scp`, or
`git clone` from your repository. Then:

```bash
cd ~/SonsAnalyzer
sudo apt install -y python3-venv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt         # numpy/pandas/scipy come as ready-built ARM64 wheels
cp config.example.toml config.toml                # adjust if InfluxDB isn't on localhost
.venv/bin/python -m sonsanalyzer.polar.orc        # fetch the ORC polar – needs internet, do it on shore
.venv/bin/python -m pytest -q                     # optional self test
```

Run it as a service so it starts at boot:

```bash
sudo cp deploy/sonsanalyzer.service /etc/systemd/system/
sudo nano /etc/systemd/system/sonsanalyzer.service   # check User= and paths
sudo systemctl daemon-reload
sudo systemctl enable --now sonsanalyzer
journalctl -u sonsanalyzer -f                     # logs
```

Open **http://openplotter.local:8080** (or `http://10.10.10.1:8080` on the access point) on
a phone or laptop.

**Map tiles:** charts come from OpenStreetMap/OpenSeaMap and are cached on the Pi the first
time an area is viewed with internet. Open your usual race areas once at home (zoom in and out
a bit) and they will be there offline. Without tiles the track still shows on a plain
background.

## 10. Race-day routine

1. Power on before leaving the dock, and check the Live page shows data and the right time.
   Then just race. Nothing needs to be pressed on board.
2. Afterwards, on the Races page, press **Find races** next to the day's sailing session.
   Each race found becomes a race entry. The start gun and finish are estimated from the
   data, and motoring, the pre-start and everything after the finish are left out.
3. Open each race and check the **start gun time** at the top. If it is wrong, use
   **−1 min / +1 min** or type the exact time under Race settings, then **Confirm**. This can
   be done any time later.

Optional: to get distance to the line and line bias in the start analysis, ping both ends on
the Live page before the start, or type in the line positions under Race settings.

## Troubleshooting

| Symptom | Check |
|---|---|
| Races page: "Could not read the log" | `systemctl status influxdb`. Host, port and database in `config.toml`. |
| No sessions found | Is `navigation.speedOverGround` in `SHOW MEASUREMENTS`? Is the Pi clock right? |
| Polar % blank | Load the polar (Polar page → *Fetch from ORC*, needs internet). Check true wind is present. |
| Polar % consistently ~90 % or ~110 % | Boatspeed calibration (step 4). |
| One tack always looks worse | TWA calibration / masthead alignment (step 4). |
| Heel missing | `navigation.attitude` not on the bus. It needs an attitude sensor or a compass that outputs it. |
