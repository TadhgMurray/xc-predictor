"""The newest season decides only when it says something (owner, 2026-11-04,
Lyndon State: 'Big East' from one stray 2017 row)."""
import os
import sys
from collections import Counter

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for sub in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import check_school_units as C   # noqa: E402


def test_one_stray_row_does_not_beat_the_record():
    conf = Counter({("NAC", "2012"): 1, ("NAC", "2013"): 1, ("NAC", "2015"): 1,
                    ("BIG EAST", "2017"): 1, ("NAC", "2017"): 1})
    assert C.current(conf)[0] == "NAC"
    region = Counter({("NEW ENGLAND", "2015"): 4, ("NEW ENGLAND", "2016"): 1,
                      ("MIDEAST", "2025"): 1})
    assert C.current(region)[:2] == ("NEW ENGLAND", "2015")


def test_a_season_that_decides_still_wins():
    moved = Counter({("NAC", "2023"): 12, ("LITTLE EAST", "2025"): 2})
    assert C.current(moved)[:2] == ("LITTLE EAST", "2025")
    assert C.current(Counter({("NAC", "2025"): 1}))[:2] == ("NAC", "2025")   # the only evidence
