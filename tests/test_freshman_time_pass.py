"""Same-name freshmen told apart by race times (owner, 2026-10-11: "it has
to be race times"). link_freshmen.timePairsFor joins a first-year to the one
same-name senior whose senior-to-college step is inside the measured band,
only when every other candidate on both sides is outside it.

    python -m pytest -q tests/test_freshman_time_pass.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "engine"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import link_freshmen as L                                       # noqa: E402

Y = 2026
BANDS = {"XC": (-6.0, 4.0, 1000)}
FRESH = {"p:1": ("Jake Smith", "Tufts", "M", 3), "p:2": ("Jake Smith", "Ohio St", "M", 3)}
SENIOR = {10: ("Jake Smith", "Lincoln", "M", False, False),
          20: ("Jake Smith", "Dublin", "M", False, False)}


def run(fr, sr, fresh=FRESH, senior=SENIOR):
    groups = L.ambiguousGroups(fresh, senior)
    return L.timePairsFor(groups, Y, fresh, senior, fr, sr, BANDS)


def test_each_finds_its_own_senior():
    fr = {(1, Y, "XC"): 140.0, (2, Y, "XC"): 110.0}
    sr = {(10, Y - 1, "XC"): 141.0, (20, Y - 1, "XC"): 111.0}
    pairs, _ = run(fr, sr)
    assert sorted((p[0], p[1]) for p in pairs) == [("p:1", 10), ("p:2", 20)]


def test_two_seniors_in_the_band_is_no_link():
    fr = {(1, Y, "XC"): 140.0, (2, Y, "XC"): 110.0}
    sr = {(10, Y - 1, "XC"): 141.0, (20, Y - 1, "XC"): 139.0}
    pairs, skipped = run(fr, sr)
    assert not [p for p in pairs if p[0] == "p:1"]


def test_a_senior_with_no_comparable_season_refuses_the_group():
    fr = {(1, Y, "XC"): 140.0, (2, Y, "XC"): 110.0}
    sr = {(10, Y - 1, "XC"): 141.0}                   # 20 has no XC season
    pairs, skipped = run(fr, sr)
    assert not pairs and skipped


def test_unlinked_tfrrs_ids_never_decide():
    fresh = {"p:1": FRESH["p:1"], "n:tfrrs:9": ("Jake Smith", "Yale", "M", 2)}
    fr = {(1, Y, "XC"): 140.0}
    sr = {(10, Y - 1, "XC"): 141.0, (20, Y - 1, "XC"): 111.0}
    pairs, _ = run(fr, sr, fresh=fresh)
    assert not pairs                                  # n:... could be senior 10's


def test_guards_still_apply():
    fr = {(1, Y, "XC"): 140.0, (2, Y, "XC"): 110.0}
    sr = {(10, Y - 1, "XC"): 141.0, (20, Y - 1, "XC"): 111.0}
    senior = dict(SENIOR)
    senior[10] = ("Jake Smith", "Lincoln", "M", True, False)   # already on tfrrs
    pairs, _ = run(fr, sr, senior=senior)
    assert [(p[0], p[1]) for p in pairs] == [("p:2", 20)]
