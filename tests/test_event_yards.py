"""Yard races: a glued 'yd' and a bare yard-only number are yards (2026-09-29)."""
import math
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

from event_parse import distanceFromEventShort as D           # noqa: E402

Y880 = 880 * 0.9144


def test_yard_spellings():
    for e in ("880y", "880Y", "880yd", "880 Yards", "880 Yard Run", "880", "Boys 880"):
        assert math.isclose(D(e)[0], Y880), e


def test_metric_races_stay_metric():
    assert D("880m")[0] == 880.0 and D("880 Meters")[0] == 880.0   # stated, trusted
    assert D("600")[0] == 600.0 and D("1000")[0] == 1000.0         # real metric races
    assert D("1600 Yards")[0] == 1600.0                             # a typo'd metric race
    assert D("3200")[0] == 3200.0
