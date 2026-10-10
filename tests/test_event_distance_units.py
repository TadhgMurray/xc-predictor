"""
Event names read with their unit (sweep 2026-10-10, A7): tf_points
.eventDistanceKey keeps the Mile (no leading number) and keeps yard races
apart from metre races -- the venue records keyed on the leading number
dropped every Mile and filed "440 Yard Dash" as 440 m. The engine's sprint
parser now reads the sprint yard schedule (300 yd, 100 yd, 60 yd ...).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import pytest                                                   # noqa: E402

import tf_points                                                # noqa: E402
from tf_points import eventDistanceKey, eventDistance           # noqa: E402
from event_parse import (sprintDistanceFromEventShort,          # noqa: E402
                         distanceFromEventShort)


@pytest.mark.parametrize("name,metres,label", [
    ("1600 Meters", 1600.0, "1600m"),
    ("Boys 1600m", 1600.0, "1600m"),
    ("Men's Mile", 1609.344, "Mile"),
    ("Mile", 1609.344, "Mile"),
    ("Girls 1 Mile Run", 1609.344, "Mile"),
    ("1mile", 1609.344, "Mile"),
    ("2 Mile", 3218.688, "2 Mile"),
    ("440 Yard Dash", 402.336, "440y"),
    ("Boys 600 Yard Dash", 548.64, "600y"),
    ("300 yd", 274.32, "300y"),
    ("Women's 60 Yard Dash", 54.864, "60y"),
    ("100 Yard Dash", 91.44, "100y"),
    ("880y", 804.672, "880y"),
    ("1000 Yards", 914.4, "1000y"),
    ("400m", 400.0, "400m"),
    ("Men's 800 Meters", 800.0, "800m"),
    ("Men's 10,000 Meters", 10000.0, "10000m"),
    # the "1600 Yards" typo rule: a round metric number keeps its metres
    ("Men's 1600 Yards", 1600.0, "1600m"),
    # hurdles: the engine prices flat races only; the leading number stays
    ("110m Hurdles", 110.0, "110m"),
    ("Girls 300 Meter Hurdles", 300.0, "300m"),
])
def test_eventDistanceKey(name, metres, label):
    got = eventDistanceKey(name)
    assert got is not None, name
    assert got[0] == pytest.approx(metres, abs=1e-6), name
    assert got[1] == label, name


@pytest.mark.parametrize("name", ["4x400", "4xmile", "Shot Put", "hj", "", None])
def test_not_a_running_distance(name):
    assert eventDistanceKey(name) is None
    assert eventDistance(name) is None


def test_yards_never_share_a_key_with_metres():
    keys = {eventDistanceKey(n)[0] for n in
            ("440 Yard Dash", "400 Meters", "880 Yards", "800m",
             "Mile", "1600m", "300 yd", "300m")}
    assert len(keys) == 8


def test_stored_column_still_first():
    assert eventDistanceKey("whatever", 1600) == (1600.0, "1600m")
    assert eventDistanceKey("x", 1609.34)[1] == "Mile"      # rounded column
    assert eventDistance("Mile", 3200) == 3200.0


def test_without_engine_parser_falls_back_to_leading_number(monkeypatch):
    monkeypatch.setattr(tf_points, "_ratedDistance", None)
    assert eventDistance("1600 Meters") == 1600.0
    assert eventDistance("Mile") is None                     # the old answer


def test_event_parse_sprint_yards():
    for name, m in {"300 yd": 274.32, "100 Yard Dash": 91.44,
                    "60 yd dash": 54.864, "440 Yard Dash": 402.336,
                    "600 Yard Dash": 548.64,
                    "220 Yards": 201.168, "500 Yard Run": 457.2}.items():
        got = sprintDistanceFromEventShort(name)[0]
        assert got == pytest.approx(m), name
    # 200/400 "yards" were never imperial races: the number is the metres
    assert sprintDistanceFromEventShort("400 Yards")[0] == 400.0
    # metric sprints untouched
    assert sprintDistanceFromEventShort("60m")[0] == 60.0
    # the rated parser (>= 600 m) is unchanged by the added sprint yards
    assert distanceFromEventShort("880y")[0] == pytest.approx(804.672)
    assert distanceFromEventShort("Men's 1600 Yards")[0] == 1600.0


def test_readers_agree_on_one_mile_key():
    # the engine's exact table says 1609.34, its parser 1609.344: one key
    assert eventDistanceKey("1mile") == eventDistanceKey("Men's Mile")


def test_fallback_keeps_yards_for_hurdles_and_short_dashes():
    assert eventDistanceKey("60 yd hurdles") == (pytest.approx(54.864), "60y")
    assert eventDistanceKey("Boys 50 Yard Dash") == (pytest.approx(45.72), "50y")
    assert eventDistanceKey("60m Hurdles") == (60.0, "60m")
