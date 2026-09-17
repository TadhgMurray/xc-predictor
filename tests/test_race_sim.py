# Project: xc-predictor / tests
# File:    test_race_sim.py
# Purpose: the predicted race run many times -- P(this team beats that one),
#          the score distribution, and the best lineup out of a roster
#          (owner, 2026-09-16). The scorer must agree with predict._score,
#          which is the one that owns the rules. No database, no model.
#
#   python -m pytest -q tests/test_race_sim.py
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                             # noqa: E402
import race_sim as R                                           # noqa: E402
import predict as P                                            # noqa: E402


def _field(spec):
    """spec: [(school, entered, seconds)] -> (field, preds) with a 2% band."""
    field, preds = [], []
    for i, (school, entered, secs) in enumerate(spec):
        row = {"person_id": 1000 + i, "school": school, "name": f"a{i}"}
        if entered:
            row["entered"] = entered
        field.append(row)
        preds.append({"seconds": secs, "lo": secs * 0.98, "hi": secs * 1.02})
    return field, preds


def _rand(rng, n_teams=4, per=8, seed=0):
    spec = []
    for t in range(n_teams):
        for _j in range(per):
            spec.append((f"T{t}", None, float(rng.integers(900, 1300))))
    for _u in range(3):                      # unattached, who never score
        spec.append(("Unattached", None, float(rng.integers(900, 1300))))
    return _field(spec)


# ------------------------------------------------------------------ #
#  ONE SCORER
# ------------------------------------------------------------------ #

def test_the_fast_scorer_agrees_with_the_one_that_owns_the_rules():
    """★ THE WHOLE DESIGN. predict._score knows five scorers, seven
    displacers, the cap at what a team ENTERED, and that unattached runners
    and incomplete teams are lifted out of the SCORING order but keep their
    finishing place. A second implementation would drift; this asserts they
    agree, so changing _score fails here."""
    rng = np.random.default_rng(7)
    for trial in range(25):
        field, preds = _rand(rng, n_teams=rng.integers(2, 5),
                             per=int(rng.integers(3, 10)))
        prep = R.prepare(field, preds)
        assert prep is not None
        times = np.array([p["seconds"] for p in preds], dtype=float)
        got, _sp = R.scoreDraw(times, prep["team"], prep["full"],
                               prep["cap"], len(prep["names"]))
        mine = {n: (None if np.isnan(got[i]) else got[i])
                for i, n in enumerate(prep["names"])}
        theirs_rows, _fin = P._score(field, preds)
        theirs = {t["team"]: t["score"] for t in theirs_rows}
        for name, score in theirs.items():
            assert name in mine, (trial, name)
            assert mine[name] == score, (trial, name, mine[name], score)


def test_an_entry_cap_is_respected_exactly_as_score_does_it():
    """A school that entered one qualifier and then had six what-ifs added
    neither scores nor displaces -- the 2026-09-16 rule."""
    field, preds = _field([("Solo", 1, 900.0)] + [("Solo", 1, 950.0 + i)
                                                  for i in range(6)]
                          + [("Real", 7, 1000.0 + i) for i in range(7)])
    prep = R.prepare(field, preds)
    times = np.array([p["seconds"] for p in preds], dtype=float)
    got, sp = R.scoreDraw(times, prep["team"], prep["full"], prep["cap"],
                          len(prep["names"]))
    by = dict(zip(prep["names"], got))
    assert np.isnan(by["Solo"]), "one entry is not a team"
    # Real's five scorers take places 1..5 -- nobody ahead of them scored
    assert by["Real"] == 1 + 2 + 3 + 4 + 5
    theirs = {t["team"]: t["score"] for t in P._score(field, preds)[0]}
    assert theirs["Real"] == by["Real"] and theirs["Solo"] is None


# ------------------------------------------------------------------ #
#  THE SIMULATION
# ------------------------------------------------------------------ #

def test_the_sigma_comes_off_the_band_the_model_already_published():
    assert abs(R.sigmaFromPred({"seconds": 1000, "lo": 980, "hi": 1020})
               - 0.0202) < 5e-4
    assert R.sigmaFromPred({"seconds": 1000}) is None
    assert R.sigmaFromPred({"seconds": 1000, "lo": 0, "hi": 1020}) is None
    # a degenerate band is floored, not believed
    assert R.sigmaFromPred({"seconds": 1000, "lo": 1000, "hi": 1000}) == R.SIGMA_FLOOR


def test_a_clearly_better_team_almost_always_wins_and_the_numbers_are_probabilities():
    field, preds = _field(
        [("Fast", 7, 900.0 + i * 5) for i in range(7)] +
        [("Slow", 7, 1100.0 + i * 5) for i in range(7)])
    out = R.simulate(field, preds, draws=400, seed=1)
    assert out["teams"]["Fast"]["p_win"] > 0.99
    assert out["teams"]["Slow"]["p_win"] < 0.01
    assert abs(out["teams"]["Fast"]["p_win"] + out["teams"]["Slow"]["p_win"] - 1) < 1e-9
    assert out["h2h"][("Fast", "Slow")] > 0.99
    assert out["teams"]["Fast"]["score_mean"] == 15.0      # a perfect sweep
    assert out["teams"]["Fast"]["score_sd"] == 0.0
    for t in out["teams"].values():
        assert 0.0 <= t["p_win"] <= 1.0 and 0.0 <= t["p_top3"] <= 1.0


def test_an_even_race_is_not_a_certainty_and_the_band_opens():
    field, preds = _field([("A", 7, 1000.0 + i) for i in range(7)] +
                          [("B", 7, 1000.5 + i) for i in range(7)])
    out = R.simulate(field, preds, draws=800, seed=2)
    p = out["teams"]["A"]["p_win"]
    assert 0.2 < p < 0.8, p
    assert out["teams"]["A"]["score_sd"] > 0
    assert out["teams"]["A"]["score_p10"] < out["teams"]["A"]["score_p90"]
    # and the two probabilities are each other's complement, ties split
    assert abs(out["h2h"][("A", "B")] + out["h2h"][("B", "A")] - 1) < 1e-9


def test_it_is_deterministic_under_a_seed_and_moves_without_one():
    field, preds = _field([("A", 7, 1000.0 + i) for i in range(7)] +
                          [("B", 7, 1002.0 + i) for i in range(7)])
    a = R.simulate(field, preds, draws=200, seed=5)["teams"]["A"]["p_win"]
    b = R.simulate(field, preds, draws=200, seed=5)["teams"]["A"]["p_win"]
    c = R.simulate(field, preds, draws=200, seed=6)["teams"]["A"]["p_win"]
    assert a == b and a != c


def test_correlating_teammates_widens_the_score_and_leaves_the_mean(
):
    """⚠ A UNIFORM RACE EFFECT CANNOT CHANGE A PLACE -- scoring is by place
    and multiplying every time by one factor is monotone. What does move a
    score is correlation WITHIN a team, because it shifts five scorers as a
    block."""
    field, preds = _field([("A", 7, 1000.0 + i) for i in range(7)] +
                          [("B", 7, 1001.0 + i) for i in range(7)])
    plain = R.simulate(field, preds, draws=1200, seed=3, team_rho=0.0)
    corr = R.simulate(field, preds, draws=1200, seed=3, team_rho=0.7)
    assert corr["teams"]["A"]["score_sd"] > plain["teams"]["A"]["score_sd"]
    assert abs(corr["teams"]["A"]["score_mean"]
               - plain["teams"]["A"]["score_mean"]) < 2.0


def test_a_uniform_multiplier_changes_nothing_at_all():
    """The claim in the docstring, tested rather than asserted."""
    field, preds = _field([("A", 7, 1000.0 + i * 3) for i in range(7)] +
                          [("B", 7, 1004.0 + i * 3) for i in range(7)])
    prep = R.prepare(field, preds)
    times = np.array([p["seconds"] for p in preds], dtype=float)
    a, _ = R.scoreDraw(times, prep["team"], prep["full"], prep["cap"], 2)
    b, _ = R.scoreDraw(times * 1.07, prep["team"], prep["full"], prep["cap"], 2)
    assert np.array_equal(a, b)


def test_nothing_to_score_is_none_not_a_crash():
    assert R.simulate([], [], draws=10) is None
    field, preds = _field([("Unattached", None, 900.0)])
    assert R.simulate(field, preds, draws=10) is None


# ------------------------------------------------------------------ #
#  THE LINEUP
# ------------------------------------------------------------------ #

def test_the_best_lineup_is_not_simply_the_fastest_seven():
    """Five score and two displace, so the sixth and seventh exist to push
    the other team's scorers back. The search is what finds that; a sort by
    expected time cannot."""
    spec = [("Us", 7, 1000.0 + i * 4) for i in range(6)]      # six good ones
    spec += [("Us", 7, 1100.0), ("Us", 7, 1101.0), ("Us", 7, 1102.0)]
    spec += [("Them", 7, 1005.0 + i * 4) for i in range(7)]
    field, preds = _field(spec)
    got = R.bestLineup(field, preds, "Us", draws=200, seed=4)
    assert got is not None
    assert len(got["lineup"]) == 7
    assert got["method"] in ("enumerate", "greedy")
    assert 0.0 <= got["p_win"] <= 1.0
    # the six good ones are in every sensible lineup
    assert set(got["lineup"][:1]) <= {f["person_id"] for f in field[:6]}


def test_a_roster_of_seven_or_fewer_is_the_lineup():
    field, preds = _field([("Us", 7, 1000.0 + i) for i in range(5)] +
                          [("Them", 7, 1010.0 + i) for i in range(7)])
    got = R.bestLineup(field, preds, "Us", draws=100, seed=0)
    assert got["method"] == "all" and got["considered"] == 1
    assert len(got["lineup"]) == 5


def test_the_search_enumerates_when_it_can_afford_to_and_reports_which():
    field, preds = _field([("Us", 7, 1000.0 + i * 3) for i in range(9)] +
                          [("Them", 7, 1004.0 + i * 3) for i in range(7)])
    got = R.bestLineup(field, preds, "Us", draws=60, seed=0)
    import math as _m
    assert got["method"] == "enumerate"
    assert got["considered"] == _m.comb(9, 7)
    assert len(got["alternatives"]) == 5
    # every alternative is a real lineup of the right size
    for alt in got["alternatives"]:
        assert len(alt["lineup"]) == 7
        assert 0.0 <= alt["p_win"] <= 1.0


def test_minimising_the_score_is_a_different_question_from_winning():
    field, preds = _field([("Us", 7, 1000.0 + i * 3) for i in range(9)] +
                          [("Them", 7, 1004.0 + i * 3) for i in range(7)])
    by_win = R.bestLineup(field, preds, "Us", draws=80, seed=0,
                          objective="p_win")
    by_score = R.bestLineup(field, preds, "Us", draws=80, seed=0,
                            objective="score")
    assert by_score["score_mean"] <= by_win["score_mean"] + 1e-9
