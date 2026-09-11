# Project: xc-predictor / tests
# File:    test_meet_class.py
# Purpose: The taper term's covariate is the race's SEASON-END SHARE, read
#          off the athletes' own calendars (run_joint.seasonEndShare), not a
#          meet name. The name classes (engine/meet_class.py) are kept as a
#          diagnostic the log cross-tabulates the share against, so their
#          rule is still pinned here.
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


def test_the_name_rule_on_names_that_matter():
    cases = {
        "Woodbridge Invitational": 0, "Mt. SAC Invitational": 0,
        "Golden State Invitational": 0, "State Preview": 0,
        "Nationals Preview Classic": 0, "Great Southwest International": 0,
        "Big 8 League Finals": 1, "Pacific Conference Championships": 1,
        "Section IV Championships": 2, "Region 5 Qualifier": 2,
        "CIF-SS Prelims": 2, "CIF State Prelims": 2, "UIL Region II-6A": 2,
        "PIAA District 3 Championships": 2, "NCAA West Regional": 2,
        "CIF State Championships": 3, "NXN Northwest Regional": 3,
        "Foot Locker West Regional": 3, "Nike Cross Nationals": 3,
        "NCAA Division I Championships": 3, "MIAA Division 3A": 3,
    }
    for name, want in cases.items():
        assert mcl.classify(name) == want, (name, mcl.classify(name), want)
    assert mcl.classify("Blue Devil Open", flag=True) == 2
    assert mcl.classify("Golden State Invitational", flag=True) == 0
    assert mcl.classify(None) == 0 and mcl.N_CLASS == 3


def test_sql_is_the_same_rule_in_the_same_order():
    s = mcl.sql("COALESCE(m.meet_name, '')", "COALESCE(mt.is_championship, 0) = 1")
    order = [s.index(x) for x in (mcl.RX_INVITE, mcl.RX_PRELIM, mcl.RX_HS_NATIONAL,
                                  mcl.RX_QUAL, mcl.RX_FINAL, mcl.RX_LEAGUE,
                                  "is_championship")]
    assert order == sorted(order)
    assert "%" not in s and "{" not in s and "}" not in s


def test_the_season_window_still_exists_for_the_census():
    assert mcl.inWindow(np.array([0]), np.array([92.0]))[0]
    assert not mcl.inWindow(np.array([1]), np.array([40.0]))[0]


def _fake_pack():
    """Seven athlete-seasons, five races. Race P (cell 4, 70 days ago) and
    race A (cell 0, 60 days ago): everyone's mid-season. Race B (cell 1, 40
    days ago): the last race for athletes 0-2, mid-season for 3 and 4.
    Race C (cell 2, 25 days ago): the last race for athletes 3 and 4.
    Athlete 5 has only two races (B, C) and does not vote; athlete 6's
    season is still running (race D, cell 3, 5 days ago) and does not
    vote either."""
    rows = []          # (athlete, cell, days_ago)
    for a in range(5):
        rows.append((a, 4, 70))
        rows.append((a, 0, 60))
        rows.append((a, 1, 40))
    for a in (3, 4):
        rows.append((a, 2, 25))
    rows.append((5, 1, 40)); rows.append((5, 2, 25))
    rows.append((6, 0, 60)); rows.append((6, 1, 40)); rows.append((6, 3, 5))
    ath = np.array([r[0] for r in rows]); cel = np.array([r[1] for r in rows])
    days = np.array([r[2] for r in rows], dtype=np.float64)
    n = ath.size
    cols = {"athlete": ath, "course": cel, "days": days,
            "sport": np.zeros(n, dtype=np.int64),
            "meet_class": np.where(cel == 2, 3, 0)}
    return cols, ath, n


def test_the_share_is_read_off_the_calendars(capsys):
    cols, ath, n = _fake_pack()
    keep = np.ones(n, dtype=bool)
    pool_of_athlete = np.zeros(7, dtype=np.int64)
    idx, w, n_imp, prior, labels, share = rj.seasonEndShare(
        cols, keep, ath, 7, pool_of_athlete, ["hs_m"])
    assert n_imp == 2 and labels == ["hs_m:XC", "hs_m:TF"]
    assert (prior == 0).all()
    race, _ = rj.raceCodes(cols["course"], cols["days"])
    by_cell = {int(c): float(share[race[np.flatnonzero(cols["course"] == c)[0]]])
               for c in (0, 1, 2, 3, 4)}
    # races P and A: nobody's season ends within two weeks -> 0
    assert by_cell[4] == 0.0 and by_cell[0] == 0.0
    # race B: athletes 0, 1, 2 end here (3 of the 5 voting: 0-4; athlete 5
    # has two races, athlete 6 is still running)
    assert abs(by_cell[1] - 3 / 5) < 1e-12
    # race C: athletes 3 and 4 end here and vote; athlete 5 does not vote
    assert by_cell[2] == 1.0
    # race D: the running season's race carries nothing
    assert by_cell[3] == 0.0
    # rows carry their race's share as the weight and the (pool, sport) index
    assert (w[cols["course"] == 2] == 1.0).all()
    assert (idx[cols["course"] == 2] == 0).all()
    assert (idx[cols["course"] == 0] == -1).all() and (w[cols["course"] == 0] == 0).all()
    out = capsys.readouterr().out
    assert "season-end taper" in out and "by name class 3" in out


def test_the_design_takes_a_continuous_weight():
    cols, ath, n = _fake_pack()
    keep = np.ones(n, dtype=bool)
    idx, w, n_imp, prior, labels, share = rj.seasonEndShare(
        cols, keep, ath, 7, np.zeros(7, dtype=np.int64), ["hs_m"])
    race, _ = rj.raceCodes(cols["course"], cols["days"])
    D = js.Design(ath, cols["course"], race, imp=idx, n_imp=n_imp,
                  imp_prior=prior, imp_w=w)
    assert D.n_imp == 2 and np.allclose(D.imp_w, w)
    b = D.unpack(np.zeros(D.n_total))
    assert b["imp"] is not None and b["imp"].size == 2


def test_the_prior_is_zero_and_the_expectation_negative():
    assert js.IMP_PRIOR_MEAN == 0.0 and js.IMP_EXPECTED < 0
    assert rj.SEASON_END_DAYS == 14 and rj.SEASON_CLOSED_DAYS > rj.SEASON_END_DAYS


def test_the_importance_flag_and_the_indoor_level_parse():
    p = rj.buildParser()
    a = rj.applyImplications(p.parse_args([]), p)
    assert a.importance == "field" and a.indoor_level == js.IND_LEVEL_DEFAULT
    kw = rj.sharedTermKwargs(a)
    assert kw["importance"] == "field" and kw["indoor_level"] == js.IND_LEVEL_DEFAULT
    a = rj.applyImplications(p.parse_args(["--no-importance"]), p)
    assert rj.sharedTermKwargs(a)["importance"] == "none"
    a = rj.applyImplications(p.parse_args(["--importance", "season-end"]), p)
    assert rj.sharedTermKwargs(a)["importance"] == "season-end"
    a = rj.applyImplications(p.parse_args(["--indoor-level", "fit"]), p)
    assert a.indoor_level is None
    a = rj.applyImplications(p.parse_args(["--indoor-level", "0.02"]), p)
    assert a.indoor_level == 0.02
    import pytest
    with pytest.raises(SystemExit):
        rj.applyImplications(p.parse_args(["--indoor-level", "lots"]), p)


def test_the_field_rows_cover_every_row_with_a_cell(capsys):
    course = np.array([0, 1, -1, 2])
    sport = np.array([0, 1, 1, 0])
    pool_row = np.array([0, 0, 1, 1])
    idx, n_imp, prior, labels = rj.fieldTermRows(course, sport, pool_row, ["hs_m", "hs_f"])
    assert idx.tolist() == [0, 1, -1, 2] and n_imp == 4
    assert (prior == 0).all() and labels == ["hs_m:XC", "hs_m:TF", "hs_f:XC", "hs_f:TF"]
    assert "field strength" in capsys.readouterr().out
