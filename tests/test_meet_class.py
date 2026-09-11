# Project: xc-predictor / tests
# File:    test_meet_class.py
# Purpose: The championship class is one rule in one place, and three gates
#          keep a wrong class from moving a rating (engine/meet_class.py).
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
        "Woodbridge Invitational": 0, "Mt. SAC Invitational": 0,
        "Stanford Invitational": 0, "Great American XC Festival": 0,
        "Golden State Invitational": 0, "State Preview": 0,
        "Clovis Twilight": 0, "Nationals Preview Classic": 0,
        "Tri-State Invitational": 0, "Simplot Games": 0,
        "Great Southwest International": 0,      # not "national"
        "Big 8 League Finals": 1, "Orange County Championships": 1,
        "District 3 Championships": 1, "Metro Conference Championships": 1,
        "CIF State Championships": 2, "Section IV Championships": 2,
        "NXN Northwest Regional": 2, "Foot Locker West Regional": 2,
        "Nike Cross Nationals": 2, "NCAA Division I Championships": 2,
        "Region 5 Qualifier": 2, "State Meet Qualifying Invitational": 2,
        "CIF-SS Prelims": 2, "NIRCA Nationals": 2,
    }
    for name, want in cases.items():
        assert mcl.classify(name) == want, (name, mcl.classify(name), want)
    # the feed's flag outranks the name, but not the invitational guard
    assert mcl.classify("Blue Devil Open", flag=True) == 2
    assert mcl.classify("Golden State Invitational", flag=True) == 0
    assert mcl.classify(None) == 0 and mcl.classify("") == 0


def test_sql_is_the_same_rule():
    s = mcl.sql("COALESCE(m.meet_name, '')", "COALESCE(mt.is_championship, 0) = 1")
    assert s.startswith("CASE WHEN")
    assert "!~*" in s and "THEN 0" in s.split("THEN 2")[0], "the invitational guard comes first"
    assert "is_championship" in s and s.index("is_championship") < s.index(mcl.RX_2)
    assert "%" not in s and "{" not in s and "}" not in s
    for rx in (mcl.RX_2, mcl.RX_1, mcl.RX_INVITE, mcl.RX_KEEP):
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
    with two or more races in the pack."""
    n = 12
    cols = {
        "meet_class": np.array([2, 2, 2, 2, 2, 2, 1, 1, 0, 0, 2, 2]),
        "sport": np.array([0] * 8 + [0, 0, 1, 1]),
        "athlete": np.arange(n),
        # academic day 100 = 9 November (XC in season); day 40 = 10 Sept (out)
        "doy": np.array([(100 + js.ACADEMIC_YEAR_START_DOY) % 365] * 4
                        + [(40 + js.ACADEMIC_YEAR_START_DOY) % 365] * 2
                        + [(100 + js.ACADEMIC_YEAR_START_DOY) % 365] * 4
                        + [(290 + js.ACADEMIC_YEAR_START_DOY) % 365] * 2),
        # cell 0 hosts two race days, cell 1 one, cell 2 two, cell 3 one, cell 4 two
        "course": np.array([0, 0, 1, 1, 0, 0, 2, 2, 3, 3, 4, 4]),
        "days": np.array([10, 20, 10, 10, 30, 40, 10, 20, 10, 10, 5, 15]),
    }
    keep = np.ones(n, dtype=bool)
    pool_of_athlete = np.zeros(n, dtype=np.int64)
    idx, n_imp, prior, labels = rj.importanceClasses(cols, keep, pool_of_athlete, ["hs_m"])
    assert n_imp == 4 and labels == ["hs_m:XC:c1", "hs_m:XC:c2", "hs_m:TF:c1", "hs_m:TF:c2"]
    assert (prior == 0).all(), "no evidence, no taper: the prior mean is zero"
    # rows 0-1: class 2, in season, two-race venue -> carried (index 1)
    assert list(idx[:2]) == [1, 1]
    # rows 2-3: class 2 but a ONE-race venue -> dropped
    assert list(idx[2:4]) == [-1, -1]
    # rows 4-5: class 2 at a two-race venue but out of season -> dropped
    assert list(idx[4:6]) == [-1, -1]
    # rows 6-7: class 1, in season, two-race venue -> carried (index 0)
    assert list(idx[6:8]) == [0, 0]
    # rows 8-9: ordinary
    assert list(idx[8:10]) == [-1, -1]
    # rows 10-11: TF class 2 in the outdoor window at a two-race venue -> index 3
    assert list(idx[10:12]) == [3, 3]
    out = capsys.readouterr().out
    assert "dropped as out of season 2" in out and "at a one-race venue 2" in out


def test_the_prior_mean_is_zero_and_the_expectation_is_kept():
    assert js.IMP_PRIOR_MEAN == {1: 0.0, 2: 0.0}
    assert js.IMP_EXPECTED[2] < js.IMP_EXPECTED[1] < 0
