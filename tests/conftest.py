import json
from pathlib import Path

import pytest

from sonsanalyzer.config import DEFAULTS
from sonsanalyzer.polar.polar import Polar
from sonsanalyzer.sim.simulator import simulate_race

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def cert():
    return json.loads((FIXTURES / "orc_cert_sample.json").read_text())


@pytest.fixture(scope="session")
def polar(cert):
    return Polar.from_orc(cert)


@pytest.fixture(scope="session")
def acfg():
    return dict(DEFAULTS["analysis"])


@pytest.fixture(scope="session")
def race(polar):
    """A simulated 2-lap windward/leeward race in 11 kn with ground truth."""
    return simulate_race(polar, "2026-06-01T11:00:00Z", seed=1, tws_mean=11)
