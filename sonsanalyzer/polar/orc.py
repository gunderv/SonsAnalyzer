"""Fetch the ORC certificate for a boat from ORC's public data feed (data.orc.org).

Needs internet - run on shore. The parsed polar is stored locally (data/polar.json) so the
analyzer works offline afterwards.

Usage:  python -m sonsanalyzer.polar.orc "NOR 15666"
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from .polar import Polar

BASE = "https://data.orc.org/public/WPub.dll"
HEADERS = {"User-Agent": "SonsAnalyzer/0.1 (sailing performance tool)"}
# Prefer the standard ORC International certificate, then ORC Club
FAMILY_STANDARD = "1"
CERT_PREFERENCE = ["International", "Club"]


def _norm(sail_no: str) -> str:
    return "".join(sail_no.upper().split()).replace("-", "")


def find_certificate(sail_no: str, timeout: int = 60) -> dict:
    """Look up the active certificate list for the boat's country and pick the best match."""
    country = "".join(c for c in sail_no if c.isalpha())[:3].upper()
    r = requests.get(BASE, params={"action": "activecerts", "CountryId": country},
                     headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    target = _norm(sail_no)
    matches = []
    for row in root.iter("ROW"):
        rec = {child.tag: (child.text or "") for child in row}
        if _norm(rec.get("SailNo", "")) == target:
            matches.append(rec)
    if not matches:
        raise LookupError(f"No active ORC certificate found for {sail_no} in {country}")

    def rank(rec):
        fam = 0 if rec.get("Family") == FAMILY_STANDARD else 1
        name = rec.get("CertName", "").lower()
        pref = next((i for i, p in enumerate(CERT_PREFERENCE) if p.lower() in name), 9)
        return fam, pref

    # Stable sorts: newest first, then by family/certificate preference
    matches.sort(key=lambda m: m.get("dxtDate", ""), reverse=True)
    matches.sort(key=rank)
    return matches[0]


def download_certificate(ref_no: str, timeout: int = 60) -> dict:
    r = requests.get(BASE, params={"action": "DownBoatRMS", "RefNo": ref_no, "ext": "json"},
                     headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return json.loads(r.content.decode("utf-8-sig"))


def fetch_polar(sail_no: str, data_dir: str | Path) -> Polar:
    """Find, download and store the ORC polar. Returns the Polar."""
    rec = find_certificate(sail_no)
    cert = download_certificate(rec["RefNo"])
    data_dir = Path(data_dir)
    (data_dir / "orc_certificate.json").write_text(json.dumps(cert, indent=1))
    polar = Polar.from_orc(cert)
    polar.meta.update({"CertName": rec.get("CertName"), "FamilyName": rec.get("FamilyName"),
                       "Expiry": rec.get("Expiry"), "dxtDate": rec.get("dxtDate")})
    polar.save(data_dir / "polar.json")
    return polar


if __name__ == "__main__":  # pragma: no cover
    from ..config import load_config

    cfg = load_config()
    sail = sys.argv[1] if len(sys.argv) > 1 else cfg["boat"]["sail_no"]
    p = fetch_polar(sail, cfg["app"]["data_dir"])
    print(f"Saved polar for {p.meta.get('YachtName')} {p.meta.get('SailNo')} "
          f"({p.meta.get('CertName')}, RefNo {p.meta.get('RefNo')})")
    for row in p.table()["rows"]:
        print(row)
