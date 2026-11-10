# Project: xc-predictor / tests
# File:    test_tfrrs_mixed_race.py
# Purpose: a TFRRS race run by boys and girls together is not one gender, and
#          a "0:00.0" is no time. Both off a real page.
#
#   python -m pytest -q tests/test_tfrrs_mixed_race.py
#
# The fixture is fl.tfrrs.org's Bronco's Ranch (meet 28712, 2026-10-25),
# trimmed to its events list and result table as served: one race, "Boys' &
# Girls' 5000 Meters", 24 runners from Discovery_m, Davenport_High_School_m/_f
# and Ridgeview_Global_Studies_f, nine of them at place 0 with "0:00.0".
# Before the fix every runner was stamped F and the division was named
# "Boys' & Girls' 5000 Meters", which backfill_normalize reads as F.
import os
import sys
from collections import Counter

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("tfrrs/parser", "tfrrs/scraper", "backfill", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

pytest.importorskip("bs4")
pytest.importorskip("lxml")

import parse_xc_page as P                                       # noqa: E402
import tfrrs_xc_divisions as D                                  # noqa: E402

FIXTURE = os.path.join(_ROOT, "tests", "fixtures", "tfrrs", "xc_28712_mixed_race.html")


def _rows():
    with open(FIXTURE, encoding="utf-8") as fh:
        return P.parseXCPage(fh.read(), 28712)


def _genderFromDivName(name):
    # backfill_normalize._genderFromDivName, copied: importing that module
    # pulls in the whole normaliser and its database layer
    t = (name or "").lower()
    if "women" in t or "girls" in t or "female" in t:
        return "F"
    if "men" in t or "boys" in t or "male" in t:
        return "M"
    return None


def test_each_runner_keeps_their_teams_gender():
    rows = _rows()
    assert len(rows) == 24
    for r in rows:
        want = {"m": "M", "f": "F"}[r["team_slug"].rsplit("_", 1)[1]]
        assert r["gender"] == want, r
    assert Counter(r["gender"] for r in rows) == Counter({"M": 15, "F": 9})


def test_the_two_genders_are_two_divisions_named_for_their_gender():
    rows, blob = D.applyDivisions(_rows())
    names = {r["gender"]: r["event_name"] for r in rows}
    assert names == {"M": "Boys' 5000 Meters (mixed race)", "F": "Girls' 5000 Meters (mixed race)"}
    assert len(blob) == 2
    for info in blob.values():
        assert info["distance"] == 5000.0
    # the name is what the normaliser reads gender from
    for r in rows:
        assert _genderFromDivName(blob[str(r["div_id"])]["div_name"]) == r["gender"]


def test_zero_time_is_no_time():
    rows = _rows()
    zero = [r for r in rows if r["place"] == 0]
    assert len(zero) == 9
    assert all(r["time_seconds"] is None and r["status"] == "NT" for r in zero)
    assert min(r["time_seconds"] for r in rows if r["place"]) == 1302.0


def test_a_one_gender_title_still_decides():
    assert P._namesBothGenders("Men's and Women's 4 Mile")
    assert not P._namesBothGenders("Women's 6k")
    assert not P._namesBothGenders("Freshmen Girls 2 Mile")
    assert P._mixedRaceName("Men + Women 6K", "F", False) == "Women's 6K (mixed race)"
    assert P._mixedRaceName("Class 1A", "M", True, "FL_college_m_X") == "Men's Class 1A"
    assert P._mixedRaceName("Class 1A", "F", True, "FL_Lake_Mary_f") == "Girls' Class 1A"
