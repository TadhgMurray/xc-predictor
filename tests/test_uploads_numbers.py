"""uploads.matchMeets: a different number in the name is a different meet
(owner, 2026-10-11, GHSA sectionals held as duplicates of each other)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: F401,E402

import pytest  # noqa: E402

pytest.importorskip("flask")
import uploads as U  # noqa: E402


def test_other_sectionals_on_the_same_day_are_not_duplicates():
    cands = [{"name": "GHSA 6A Sectional 2 (Regions 2,3,5,6)", "venue": "Cobb County Corps Property"},
             {"name": "GHSA 4A Sectional 2 (Regions 3,5,6,7)", "venue": "ONE Church"}]
    assert U.matchMeets("GHSA 6A Boys Sectional 1 - Regions 1, 4, 7, and 8", "Rochelle, GA", cands) == []


def test_the_same_meet_named_with_fewer_numbers_still_matches():
    cands = [{"name": "GHSA 6A Sectional 1 (Regions 1,4,7,8)", "venue": "Rochelle"}]
    got = U.matchMeets("GHSA 6A Sectional 1", "Rochelle, GA", cands)
    assert len(got) == 1
    # a year on one side only is not a conflict
    got = U.matchMeets("2024 GHSA 6A Sectional 1", None, cands)
    assert len(got) == 1
