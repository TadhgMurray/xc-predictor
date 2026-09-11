# Project: xc-predictor / tests
# File:    test_meet_class.py
# Purpose: The championship class is one rule in one place, in three classes
#          fitted apart (league, qualifier, final), and three gates keep a
#          wrong class from moving a rating (engine/meet_class.py).
#
#   python -m pytest -q tests/test_meet_class.py
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import meet_class as mcl                                       # noqa: E402
import joint_solve as js                                       # noqa: E402
import run_joint as rj                                         # noqa: E402


def test_the_rule_on_names_that_matter():
    cases = {
        # ordinary, whatever else the name says
        "Woodbridge Invitational": 0, "Mt. SAC Invitational": 0,
        "Stanford Invitational": 0, "Great American XC Festival": 0,
        "Golden State Invitational": 0, "State Preview": 0,
        "Clovis Twilight": 0, "Nationals Preview Classic": 0,
        "Tri-State Invitational": 0, "Simplot Games": 0,
        "Great Southwest International": 0,      # not "national"
        "Nokia Classic": 0, "Pacific Coast Invitational": 0,
        # league-level
        "Big 8 League Finals": 1, "Orange County Championships": 1,
        "Metro Conference Championships": 1, "Big Ten Championships": 1,
        "Pacific Conference Championships": 1,   # "cif" inside Pacific is not CIF
        # a qualifying round
        "Section IV Championships": 2, "District 3 Championships": 2,
        "Region 5 Qualifier": 2, "State Meet Qualifying Invitational": 2,
        "CIF-SS Prelims": 2, "CIF State Prelims": 2, "UIL Region II-6A": 2,
        "PIAA District 3 Championships": 2, "NCAA West Regional": 2,
        # a final
        "CIF State Championships": 3, "NXN Northwest Regional": 3,
        "Foot Locker West Regional": 3, "Nike Cross Nationals": 3,
        "NCAA Division I Championships": 3, "NIRCA Nationals": 3,
        "MIAA Division 3A": 3, "OHSAA State Meet": 3,
    }
    for name, want in cases.items():
        assert mcl.classify(name) == want, (name, mcl.classify(name), want)
    # the feed's flag decides only when the name says nothing, and never
    # beats the invitational guard
    assert mcl.classify("Blue Devil Open", flag=True) == 2
    assert mcl.classify("Big Ten Championships", flag=True) == 1
    assert mcl.classify("Golden State Invitational", flag=True) == 0
    assert mcl.classify(None) == 0 and mcl.classify("") == 0
    assert mcl.N_CLASS == 3 and mcl.CLASS_NAMES[3] == "final"


def test_sql_is_the_same_rule_in_the_same_order():
    s = mcl.sql("COALESCE(m.meet_name, '')", "COALESCE(mt.is_championship, 0) = 1")
    assert s.startswith("CASE WHEN")
    order = [s.index(x) for x in (mcl.RX_INVITE, mcl.RX_PRELIM, mcl.RX_HS_NATIONAL,
                                  mcl.RX_QUAL, mcl.RX_FINAL, mcl.RX_LEAGUE,
                                  "is_championship")]
    assert order == sorted(order), "the SQL must test the classes in classify()'s order"
    assert "!~*" in s and "THEN 0" in s.split("THEN 2")[0], "the invitational guard comes first"
    assert "%" not in s and "{" not in s and "}" not in s
    for rx in (mcl.RX_INVITE, mcl.RX_KEEP, mcl.RX_PRELIM, mcl.RX_HS_NATIONAL,
               mcl.RX_QUAL, mcl.RX_FINAL, mcl.RX_LEAGUE):
        assert "%" not in rx and "{" not in rx and "\\" not in rx


def test_the_season_window():
    # XC: 1 October (day 61) is early, 1 November (day 92) is in, 20 Dec (141) is out
    assert not mcl.inWindow(np.array([0]), np.array([61.0]))[0]
    assert mcl.inWindow(np.array([0]), np.array([92.0]))[0]
    assert not mcl.inWindow(np.array([0]), np.array([141.0]))[0]
    # TF: a September "state" meet is out, mid-May is in
    assert not mcl.inWindow(np.array([1]), np.array([40.0]))[0]
    assert mcl.inWindow(np.array([1]), np.array([290.0]))[0]


def test_the_gates_in_the_design_builder(capsys):
    """A labelled row keeps the term only in season and only at a venue
    with two or more races in the pack; the three classes get three
    coefficients per (pool, sport)."""
    n = 14
    d_in = (100 + js.ACADEMIC_YEAR_START_DOY) % 365          # 9 November: XC in season
    d_out = (40 + js.ACADEMIC_YEAR_START_DOY) % 365          # 10 September: out
    d_tf = (290 + js.ACADEMIC_YEAR_START_DOY) % 365          # mid-May: TF in season
    cols = {
        "meet_class": np.array([3, 3, 3, 3, 3, 3, 1, 1, 0, 0, 3, 3, 2, 2]),
        "sport": np.array([0] * 10 + [1, 1, 0, 0]),
        "athlete": np.arange(n),
        "doy": np.array([d_in] * 4 + [d_out] * 2 + [d_in] * 4 + [d_tf] * 2 + [d_in] * 2),
        # cell 0 hosts two race days, cell 1 one, cell 2 two, cell 3 one,
        # cell 4 two, cell 5 two
        "course": np.array([0, 0, 1, 1, 0, 0, 2, 2, 3, 3, 4, 4, 5, 5]),
        "days": np.array([10, 20, 10, 10, 30, 40, 10, 20, 10, 10, 5, 15, 3, 9]),
    }
    keep = np.ones(n, dtype=bool)
    pool_of_athlete = np.zeros(n, dtype=np.int64)
    idx, n_imp, prior, labels = rj.importanceClasses(cols, keep, pool_of_athlete, ["hs_m"])
    assert n_imp == 6
    assert labels == ["hs_m:XC:c1", "hs_m:XC:c2", "hs_m:XC:c3",
                      "hs_m:TF:c1", "hs_m:TF:c2", "hs_m:TF:c3"]
    assert (prior == 0).all(), "no evidence, no taper: the prior mean is zero"
    # rows 0-1: a final, in season, two-race venue -> carried (XC c3 = index 2)
    assert list(idx[:2]) == [2, 2]
    # rows 2-3: a final but a ONE-race venue -> dropped
    assert list(idx[2:4]) == [-1, -1]
    # rows 4-5: a final at a two-race venue but out of season -> dropped
    assert list(idx[4:6]) == [-1, -1]
    # rows 6-7: league, in season, two-race venue -> carried (XC c1 = index 0)
    assert list(idx[6:8]) == [0, 0]
    # rows 8-9: ordinary
    assert list(idx[8:10]) == [-1, -1]
    # rows 10-11: a TF final in the outdoor window at a two-race venue -> TF c3 = index 5
    assert list(idx[10:12]) == [5, 5]
    # rows 12-13: an XC qualifier, in season, two-race venue -> XC c2 = index 1
    assert list(idx[12:14]) == [1, 1]
    out = capsys.readouterr().out
    assert "dropped as out of season 2" in out and "at a one-race venue 2" in out
    assert "league 2" in out and "qualifier 2" in out and "final 4" in out


def test_the_prior_mean_is_zero_and_the_expectation_is_ordered():
    assert js.IMP_PRIOR_MEAN == {1: 0.0, 2: 0.0, 3: 0.0}
    assert js.IMP_EXPECTED[3] < js.IMP_EXPECTED[2] < js.IMP_EXPECTED[1] < 0
