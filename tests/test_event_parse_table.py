# Project: xc-predictor / tests
# File:    test_event_parse_table.py
# Purpose: engine/event_parse.distanceFromEventShort, table-driven: the
#          behaviour it already had, and the sweep 2026-10-10 additions --
#          word numbers ("Two Mile", "Half Mile"), a LEADING level word
#          ("Open 3200m"), grade ordinals after the event ("2 Mile Run 7th
#          Grade"), and the abbreviated steeple ("3000 S/C") rejected. Also
#          (finding 6) no steeple key in EVENT_DISTANCES_TF.
#
#     python -m pytest -q tests/test_event_parse_table.py
import math
import os
import sys

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from event_parse import distanceFromEventShort as D           # noqa: E402
from normalize_distance import EVENT_DISTANCES_TF, parseEventShort  # noqa: E402

MILE = 1609.344
YD = 0.9144

CASES = [
    # --- behaviour it already had ---------------------------------------
    ("800m", 800, None),
    ("1600m", 1600, None),
    ("1mile", 1609.34, None),
    ("2mile", 3218.69, None),
    ("3200m", 3200, None),
    ("Men's 800 Meters", 800, "M"),
    ("Women's 5000 Meters", 5000, "F"),
    ("Women's 10,000 Meters", 10000, "F"),
    ("Men's Mile", MILE, "M"),
    ("Mile", MILE, None),
    ("Men's 1600 Yards", 1600, "M"),
    ("880y", 880 * YD, None),
    ("880", 880 * YD, None),
    ("Boys 5-8 1 Mile Run", MILE, "M"),
    ("10-km", 10000, None),
    ("10km", 10000, None),
    ("5k", 5000, None),
    ("1600 (Section 2)", 1600, None),
    ("3200m Open", 3200, None),
    ("1600m Varsity", 1600, None),
    ("Girls 3200 Meter Run Finals", 3200, "F"),
    ("Men's 600 Meters", 600, "M"),
    ("Misc 1600", 1600, None),
    # rejects it already had
    ("2007th Boys", None, None),
    ("sprintmed2248", None, None),
    ("distmed12,4,8,16", None, None),
    ("4x400m", None, None),
    ("4xmile", None, None),
    ("100m", None, None),
    ("Men's 110 Meter Hurdles", None, "M"),
    ("Women's 3000 Steeplechase", None, "F"),
    ("3ksteeple", None, None),
    ("3218m-rw", None, None),
    ("Men's Shot Put", None, "M"),
    ("", None, None),
    (None, None, None),
    # --- sweep 2026-10-10 -----------------------------------------------
    ("Two Mile", 2 * MILE, None),
    ("Two Miles", 2 * MILE, None),
    ("Boys Two Mile Run", 2 * MILE, "M"),
    ("Half Mile", MILE / 2, None),
    ("One Mile", MILE, None),
    ("Open 3200m", 3200, None),
    ("Varsity 1600", 1600, None),
    ("JV 800m", 800, None),
    ("Freshman Boys 1600m", 1600, "M"),
    ("Boys Varsity 1600", 1600, "M"),
    ("Invitational 3200", 3200, None),
    ("2 Mile Run 7th Grade", 2 * MILE, None),
    ("1500 meter run 7th & 8th", 1500, None),
    ("7th Grade 1600m", 1600, None),
    ("3000 S/C", None, None),
    ("3000m SC", None, None),
    ("3000 Meter S/C", None, None),
    ("3000mSC", None, None),
    ("2000mSC", None, None),
    ("3000msc", None, None),
]


@pytest.mark.parametrize("ev,metres,gender", CASES)
def test_event_table(ev, metres, gender):
    got_m, got_g = D(ev)
    if metres is None:
        assert got_m is None, (ev, got_m)
    else:
        assert got_m is not None and math.isclose(got_m, metres, abs_tol=1e-6), (ev, got_m)
    assert got_g == gender, (ev, got_g)


def test_no_steeple_is_a_flat_distance_in_the_exact_dict():
    for key in EVENT_DISTANCES_TF:
        assert parseEventShort(key)["kind"] == "flat", key


def test_steeple_events_still_read_as_steeple():
    # the steeple's own label/kind comes from parseEventShort, untouched
    assert parseEventShort("3000mSC") == {"meters": 3000.0, "kind": "steeple"}
