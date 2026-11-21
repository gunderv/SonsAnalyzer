"""Polar performance model: target boatspeed for any TWS/TWA plus upwind/downwind targets.

Internally a dense grid (TWS points from the source x TWA 0..180 in 1 degree steps) is built
from the source curves and interpolated bilinearly. Speeds in knots, angles in degrees.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import numpy as np
from scipy.interpolate import RegularGridInterpolator

TWA_GRID = np.arange(0.0, 181.0, 1.0)


class Polar:
    def __init__(self, tws: list[float], curves: list[list[tuple[float, float]]],
                 targets: dict[str, list[float]] | None = None, meta: dict | None = None):
        """
        tws:     wind speeds (knots), ascending
        curves:  for each tws, list of (twa, bsp) points (any order, twa 0..180)
        targets: optional dict with lists per tws: beat_angle, beat_vmg, gybe_angle, run_vmg.
                 If omitted they are derived from the curves by maximising VMG.
        """
        self.meta = meta or {}
        self.tws = np.asarray(tws, dtype=float)
        rows = [self._dense_curve(pts) for pts in curves]
        self.bsp_grid = np.vstack(rows)  # shape (len(tws), len(TWA_GRID))
        if targets is None:
            targets = self._derive_targets()
        self.beat_angle = np.asarray(targets["beat_angle"], float)
        self.beat_vmg = np.asarray(targets["beat_vmg"], float)
        self.gybe_angle = np.asarray(targets["gybe_angle"], float)
        self.run_vmg = np.asarray(targets["run_vmg"], float)
        # add tws=0 row (no wind, no speed) so light air interpolates towards zero
        tws_ext = np.concatenate([[0.0], self.tws])
        grid_ext = np.vstack([np.zeros(len(TWA_GRID)), self.bsp_grid])
        self._interp = RegularGridInterpolator((tws_ext, TWA_GRID), grid_ext,
                                               bounds_error=False, fill_value=None)

    # ----- construction helpers -------------------------------------------------------------
    @staticmethod
    def _dense_curve(points: list[tuple[float, float]]) -> np.ndarray:
        pts = sorted((float(a), float(b)) for a, b in points if b is not None and b > 0)
        dedup: list[tuple[float, float]] = []
        for a, b in pts:
            if dedup and abs(dedup[-1][0] - a) < 1e-6:
                dedup[-1] = (a, max(b, dedup[-1][1]))
            else:
                dedup.append((a, b))
        twa = np.array([p[0] for p in dedup])
        bsp = np.array([p[1] for p in dedup])
        # Below the first (closest-hauled) point speed falls linearly to zero at TWA 0.
        if twa[0] > 0:
            twa = np.concatenate([[0.0], twa])
            bsp = np.concatenate([[0.0], bsp])
        return np.interp(TWA_GRID, twa, bsp)  # beyond last point: held constant

    def _derive_targets(self) -> dict[str, list[float]]:
        cos = np.cos(np.deg2rad(TWA_GRID))
        vmg = self.bsp_grid * cos
        up = np.argmax(vmg, axis=1)
        down = np.argmin(vmg, axis=1)
        rows = np.arange(len(self.tws))
        return {
            "beat_angle": TWA_GRID[up].tolist(), "beat_vmg": vmg[rows, up].tolist(),
            "gybe_angle": TWA_GRID[down].tolist(), "run_vmg": (-vmg[rows, down]).tolist(),
        }

    @classmethod
    def from_orc(cls, cert: dict) -> "Polar":
        """Build from an ORC certificate JSON (the object inside "rms", or the whole download).

        ORC 'Allowances' are time allowances in seconds per nautical mile; speed = 3600 / value.
          R52..R150: boatspeed allowance at that TWA
          Beat / BeatAngle: optimum upwind VMG and its TWA
          Run / GybeAngle:  optimum downwind VMG and its TWA
          DW180: allowance sailing dead downwind (TWA 180)
        """
        if "rms" in cert:
            cert = cert["rms"][0]
        allow = cert["Allowances"]
        tws = allow["WindSpeeds"]
        angles = allow["WindAngles"]

        def kn(v):
            return 3600.0 / v if v else None

        curves = []
        targets = {"beat_angle": [], "beat_vmg": [], "gybe_angle": [], "run_vmg": []}
        for i, _ in enumerate(tws):
            beat_angle = allow["BeatAngle"][i]
            beat_vmg = kn(allow["Beat"][i])
            gybe_angle = allow["GybeAngle"][i]
            run_vmg = kn(allow["Run"][i])
            pts = [(beat_angle, beat_vmg / np.cos(np.deg2rad(beat_angle)))]
            pts += [(a, kn(allow[f"R{a}"][i])) for a in angles if f"R{a}" in allow]
            pts.append((gybe_angle, run_vmg / abs(np.cos(np.deg2rad(gybe_angle)))))
            if "DW180" in allow:
                pts.append((180.0, kn(allow["DW180"][i])))
            curves.append(pts)
            targets["beat_angle"].append(beat_angle)
            targets["beat_vmg"].append(beat_vmg)
            targets["gybe_angle"].append(gybe_angle)
            targets["run_vmg"].append(run_vmg)
        meta = {k: cert.get(k) for k in ("YachtName", "SailNo", "Class", "RefNo", "VPPYear")}
        meta["source"] = "ORC"
        return cls(tws, curves, targets, meta)

    @classmethod
    def from_csv(cls, text: str, meta: dict | None = None) -> "Polar":
        """Read a polar table: first row 'twa/tws' then TWS values, following rows TWA then speeds.
        Delimiter (comma, semicolon or tab) is auto-detected; empty / 0 cells are ignored."""
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
        rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
        tws = [float(x) for x in rows[0][1:] if x.strip()]
        curves: list[list[tuple[float, float]]] = [[] for _ in tws]
        for r in rows[1:]:
            twa = float(r[0])
            for j, cell in enumerate(r[1:len(tws) + 1]):
                if cell.strip() and float(cell) > 0:
                    curves[j].append((twa, float(cell)))
        return cls(tws, curves, None, {**(meta or {}), "source": "CSV"})

    # ----- persistence ----------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "meta": self.meta, "tws": self.tws.tolist(), "twa": TWA_GRID.tolist(),
            "bsp": np.round(self.bsp_grid, 3).tolist(),
            "targets": {"beat_angle": self.beat_angle.tolist(), "beat_vmg": self.beat_vmg.tolist(),
                        "gybe_angle": self.gybe_angle.tolist(), "run_vmg": self.run_vmg.tolist()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Polar":
        curves = [list(zip(d["twa"], row)) for row in d["bsp"]]
        return cls(d["tws"], curves, d["targets"], d.get("meta"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1))

    @classmethod
    def load(cls, path: str | Path) -> "Polar":
        return cls.from_dict(json.loads(Path(path).read_text()))

    # ----- queries --------------------------------------------------------------------------
    def target_bsp(self, tws, twa):
        """Target boatspeed (knots). Accepts scalars or arrays; TWA sign is ignored."""
        tws = np.asarray(tws, float)
        twa = np.abs(np.asarray(twa, float))
        tws, twa = np.broadcast_arrays(tws, twa)
        tws_c = np.clip(np.nan_to_num(tws), 0, self.tws.max())
        twa_c = np.clip(np.nan_to_num(twa), 0, 180)
        pts = np.stack([tws_c.ravel(), twa_c.ravel()], axis=-1)
        out = self._interp(pts).reshape(tws.shape)
        out = np.where(np.isnan(tws) | np.isnan(twa), np.nan, out)
        return out.item() if out.ndim == 0 else out

    def targets(self, tws) -> dict:
        """Upwind and downwind targets (TWA, BSP, VMG) for the given TWS."""
        tws = np.asarray(tws, float)
        t = np.clip(tws, 0, self.tws.max())
        xs = np.concatenate([[0.0], self.tws])

        def lerp(values, zero_value):
            return np.interp(t, xs, np.concatenate([[zero_value], values]))

        up_twa = lerp(self.beat_angle, self.beat_angle[0])
        up_vmg = lerp(self.beat_vmg, 0.0)
        dn_twa = lerp(self.gybe_angle, self.gybe_angle[0])
        dn_vmg = lerp(self.run_vmg, 0.0)
        res = {
            "up_twa": up_twa, "up_vmg": up_vmg, "up_bsp": up_vmg / np.cos(np.deg2rad(up_twa)),
            "down_twa": dn_twa, "down_vmg": dn_vmg,
            "down_bsp": dn_vmg / np.abs(np.cos(np.deg2rad(dn_twa))),
        }
        nan = np.isnan(tws)
        res = {k: np.where(nan, np.nan, v) for k, v in res.items()}
        if tws.ndim == 0:
            res = {k: float(v) for k, v in res.items()}
        return res

    def table(self, tws_list=None, twa_list=(52, 60, 75, 90, 110, 120, 135, 150)) -> dict:
        """Compact table for display."""
        tws_list = list(self.tws) if tws_list is None else list(tws_list)
        rows = []
        for twa in twa_list:
            rows.append({"twa": twa, "bsp": [round(float(self.target_bsp(w, twa)), 2) for w in tws_list]})
        tg = self.targets(np.array(tws_list))
        return {"tws": tws_list, "rows": rows,
                "targets": {k: np.round(v, 2).tolist() for k, v in tg.items()}}
