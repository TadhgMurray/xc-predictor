# Project: xc-predictor / tests
# File:    test_ncaa_select.py
# Purpose: the NCAA selection rules (ncaa_select.py, ncaa_rules.py) on toy
#          seasons small enough to work out by hand (2026-10-10). No
#          database, no model.
#
#   python -m pytest -q tests/test_ncaa_select.py
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                             # noqa: E402
import ncaa_rules as NR                                        # noqa: E402
import ncaa_select as S                                        # noqa: E402

D1 = NR.rules("d1")


def _toy(auto=1, slots=2):
    """Three regions of three teams. Team ids: A 0-2, B 3-5, C 6-8."""
    rules = dict(D1, auto_per_region=auto, at_large_teams=slots)
    orders = {"A": [0, 1, 2], "B": [3, 4, 5], "C": [6, 7, 8]}
    scores = {t: 30 + 10 * (t % 3) for t in range(9)}
    return rules, orders, scores


def _W(pairs, n=9):
    """[(winner, loser, day)] -> (W, last)."""
    return S.winMatrix(n, pairs)


# ------------------------------------------------------------------ #
#  the arithmetic the manuals print
# ------------------------------------------------------------------ #

def test_rules_arithmetic():
    assert NR.check()
    assert NR.RULES["d1"]["teams_total"] == 32 and NR.RULES["d1"]["at_large_teams"] == 14
    assert NR.RULES["d2"]["teams_total"] == 34 and NR.RULES["d2"]["auto_per_region"] == 3
    assert NR.RULES["d3"]["auto_per_region"] == 1 and NR.RULES["d3"]["at_large_teams"] == 22
    assert NR.nIndividualsMax("d3") == 70


def test_window_and_distance():
    lo, hi = NR.window("d1")
    assert (lo.isoformat(), hi.isoformat()) == ("2026-09-11", "2026-11-13")
    lo, hi = NR.window("d2")
    assert (hi - lo).days == 51
    assert NR.minDistance("d1", "M") == 7500 and NR.minDistance("d1", "F") == 4500
    assert NR.minDistance("d3", "M") == 7000 and NR.minDistance("d2", "F") == 5000


def test_region_spellings():
    assert NR.canonicalRegion("d1", "MID-ATLANTIC") == "Mid-Atlantic"
    assert NR.canonicalRegion("d1", "SOUTH CENTRAL") == "South Central"
    assert NR.canonicalRegion("d3", "East") == "Northeast"
    # an old alignment's name is not a current region
    assert NR.canonicalRegion("d3", "NEW ENGLAND") is None
    assert NR.canonicalRegion("d1", None) is None


# ------------------------------------------------------------------ #
#  counting Kolas wins
# ------------------------------------------------------------------ #

def _race(order, starters, dnf=()):
    return {"order": order, "dnf": list(dnf), "starters": starters}


def test_kolas_counts_wins_over_a_teams_only():
    seven = {0: {1, 2, 3, 4, 5, 6, 7}, 1: {11, 12, 13, 14, 15, 16, 17},
             2: {21, 22, 23, 24, 25, 26, 27}}
    # team 1 started only three of its regional seven: a B team
    starters = {0: {1, 2, 3, 4, 5}, 1: {11, 12, 13, 91, 92},
                2: {21, 22, 23, 24, 25}}
    race = _race([0, 1, 2], starters)
    got = set(S.raceWins(race, D1, seven))
    # beating the B team earns nothing; the B team's own win still counts
    assert (0, 1) not in got
    assert (1, 2) in got and (0, 2) in got


def test_d2_needs_a_teams_on_both_sides():
    rules = NR.rules("d2")
    seven = {0: {1, 2, 3, 4, 5, 6, 7}, 1: {11, 12, 13, 14, 15, 16, 17}}
    race = {"order": [0, 1], "starters": {0: {1, 2, 3, 4, 91}, 1: {11, 12, 13, 14, 15}},
            "dual": {(0, 1): 0}}
    assert S.raceWins(race, rules, seven) == []         # team 0 ran 4 of its 7
    race["starters"][0] = {1, 2, 3, 4, 5}
    assert S.raceWins(race, rules, seven) == [(0, 1)]


def test_d1_beats_a_team_that_did_not_finish_as_a_team():
    race = _race([0], {0: {1, 2, 3, 4, 5}, 1: {11, 12, 13, 14, 15}}, dnf=[1])
    assert S.raceWins(race, D1, None) == [(0, 1)]
    assert S.raceWins(race, NR.rules("d3"), None) == []


def test_every_win_counts_and_regionals_add_theirs():
    W, last = _W([(1, 3, "2026-09-20"), (1, 3, "2026-10-04"), (3, 1, "2026-10-18")])
    assert W[1, 3] == 2 and W[3, 1] == 1
    assert last[(1, 3)] == "2026-10-04"
    W2, last2 = S.addRegionals(W, last, {"A": [0, 1, 2]}, "2026-11-13")
    assert W2[0, 1] == 1 and W2[0, 2] == 1 and W2[1, 2] == 1 and W2[2, 0] == 0
    assert W[0, 1] == 0                                 # the season's own W untouched
    assert last2[(1, 2)] == "2026-11-13"


# ------------------------------------------------------------------ #
#  selection order
# ------------------------------------------------------------------ #

def test_kolas_picks_most_points_and_recounts():
    rules, orders, scores = _toy(auto=1, slots=2)
    # autos are 0, 3, 6. Team 1 beat 3 and 6 (2 points); team 4 beat 0 and,
    # crucially, team 1 -- worth nothing until 1 is in; team 7 beat 0 twice.
    W, last = _W([(1, 3, "a"), (1, 6, "a"), (4, 0, "a"), (4, 1, "b"),
                  (7, 0, "a"), (7, 0, "b")])
    strength = {1: 0, 4: 1, 7: 2}
    out = S.kolasSelect(orders, scores, W, last, rules, strength=strength, log=True)
    assert out["auto"] == [0, 3, 6]
    assert out["at_large"] == [1, 4]
    first, second = out["rounds"]
    assert first["candidates"] == {1: 2, 4: 1, 7: 2}
    # 1 and 7 tie on two: never met, no common opponent, both second at
    # their regionals, both ten points behind their winner -- every listed
    # step is level, and our last step (projected strength) decides
    assert first["why"] == "strength"
    # after 1 is in, 4's win over 1 counts: 4 rises from one point to two
    assert second["candidates"] == {2: 0, 4: 2, 7: 2}


def test_kolas_never_jumps_a_team_that_beat_it_at_regionals():
    rules, orders, scores = _toy(auto=1, slots=1)
    # team 2 has every win in the world; team 1 finished ahead of it
    W, last = _W([(2, 3, "a"), (2, 6, "a"), (2, 0, "a"), (4, 0, "a")])
    out = S.kolasSelect(orders, scores, W, last, rules, push=False)
    assert 2 not in out["at_large"]
    assert out["at_large"] == [4]


def test_push_takes_both_when_x_blocks_y_for_good():
    rules, orders, scores = _toy(auto=1, slots=2)
    W, last = _W([(2, 3, "a"), (2, 6, "a"), (2, 0, "a"), (4, 0, "a")])
    out = S.kolasSelect(orders, scores, W, last, rules, push=True, log=True)
    assert out["pushed"] == [1]
    assert 2 in out["at_large"]
    assert out["rounds"][0]["why"] == "push"


def test_no_push_when_y_gets_in_later_anyway():
    rules, orders, scores = _toy(auto=1, slots=3)
    # 1 has a point, so after 4 (2 points) and 1 go in, 2 moves up and is
    # picked on its own: no push needed
    W, last = _W([(2, 3, "a"), (2, 6, "a"), (2, 0, "a"), (4, 0, "a"), (4, 6, "a"),
                  (1, 6, "a")])
    out = S.kolasSelect(orders, scores, W, last, rules, push=True)
    assert out["pushed"] == []
    assert set(out["at_large"]) >= {1, 2}


def test_tie_goes_head_to_head_then_most_recent_win():
    rules, orders, scores = _toy(auto=1, slots=1)
    # 1 and 4 both have one point; 4 beat 1 head to head
    W, last = _W([(1, 6, "a"), (4, 6, "a"), (4, 1, "c")])
    out = S.kolasSelect(orders, scores, W, last, rules, log=True)
    assert out["at_large"] == [4] and out["rounds"][0]["why"] == "h2h"
    # 1-1 between them: the win closer to the regional decides
    W, last = _W([(1, 6, "a"), (4, 6, "a"), (4, 1, "2026-09-20"), (1, 4, "2026-10-30")])
    out = S.kolasSelect(orders, scores, W, last, rules)
    assert out["at_large"] == [1]


def test_tie_of_three_needs_an_unbeaten_team():
    rules, orders, scores = _toy(auto=1, slots=1)
    # 1, 4, 7 one point each; 1 beat 4 and 7; 4 beat 7
    W, last = _W([(1, 0, "a"), (4, 0, "a"), (7, 0, "a"),
                  (1, 4, "b"), (1, 7, "b"), (4, 7, "b")])
    out = S.kolasSelect(orders, scores, W, last, rules, log=True)
    assert out["at_large"] == [1]


def test_common_opponents_then_regional_place():
    rules, orders, scores = _toy(auto=1, slots=1)
    # 1 and 4 tied on one point each, never met; against common opponent 8,
    # 4 is 2-0 and 1 is 1-1
    W, last = _W([(1, 0, "a"), (4, 0, "a"), (4, 8, "a"), (4, 8, "b"),
                  (1, 8, "a"), (8, 1, "b")])
    out = S.kolasSelect(orders, scores, W, last, rules, log=True)
    assert out["at_large"] == [4] and out["rounds"][0]["why"] == "common"


def test_d2_net_wins_through_common_opponents():
    rules = dict(NR.rules("d2"), auto_per_region=1, at_large_teams=1)
    orders = {"A": [0, 1], "B": [2, 3], "C": [4, 5]}
    scores = {t: 40 + t for t in range(6)}
    # candidates 1, 3, 5. 1 never met 3, but 1 beat 0 and 0 beat 3: a chain
    # win for 1 over 3. 5 lost to 3 directly.
    W, last = S.winMatrix(6, [(1, 0, "a"), (0, 3, "b"), (3, 5, "c")])
    P = S.chainMatrices(W)
    assert S.d2Net(1, 3, W, P) == 1
    assert S.d2Net(3, 5, W, P) == 1
    out = S.d2Select(orders, scores, W, last, rules, log=True)
    assert out["at_large"] == [1]
    assert out["rounds"][0]["why"] == "net wins"


def test_d2_same_meet_chain_does_not_count():
    W, _ = S.winMatrix(4, [(0, 2, "m"), (2, 1, "m")])
    P = S.chainMatrices(W)
    single = np.full((4, 4), -1)
    single[0, 2] = single[2, 0] = 7
    single[2, 1] = single[1, 2] = 7
    assert S.d2Net(0, 1, W, P) == 1
    assert S.d2Net(0, 1, W, P, single) == 0


def test_d3_selection_has_no_push():
    rules = dict(NR.rules("d3"), auto_per_region=1, at_large_teams=2)
    _r, orders, scores = _toy()
    W, last = _W([(2, 3, "a"), (2, 6, "a"), (2, 0, "a"), (4, 0, "a")])
    out = S.d3Select(orders, scores, W, last, rules)
    assert out["pushed"] == []
    assert 2 not in out["at_large"][:1]


# ------------------------------------------------------------------ #
#  individuals
# ------------------------------------------------------------------ #

def test_d1_individuals_top_four_in_top_25_then_at_large():
    finish = {"A": list(range(100, 140)), "B": list(range(200, 240))}
    # region A: the first ten runners are on qualified team 1
    team_of = {r: (1 if r < 110 else None) for r in finish["A"]}
    team_of.update({r: None for r in finish["B"]})
    rules = dict(D1, ind_total=10)
    got = S.selectIndividuals(finish, team_of, {1}, rules)
    autos = [r for r, _g, k in got if k == "auto"]
    assert autos == [110, 111, 112, 113, 200, 201, 202, 203]
    at_large = [(r, g) for r, g, k in got if k == "at-large"]
    # the highest non-qualifiers: B's fifth (place 5) beats A's fifth (place 15)
    assert at_large == [(204, "B"), (205, "B")]


def test_d1_individuals_must_be_top_25():
    finish = {"A": list(range(100, 140))}
    team_of = {r: (1 if r < 123 else None) for r in finish["A"]}
    got = S.selectIndividuals(finish, team_of, {1}, dict(D1, ind_total=6))
    # places 24 and 25 are the only non-qualifiers inside the top 25
    assert [r for r, _g, _k in got] == [123, 124]


def test_d3_renumbers_and_takes_seven():
    finish = {"A": list(range(20))}
    team_of = {r: (5 if r % 2 == 0 else None) for r in finish["A"]}
    got = S.selectIndividuals(finish, team_of, {5}, NR.rules("d3"))
    assert [r for r, _g, _k in got] == [1, 3, 5, 7, 9, 11, 13]


def test_d2_individuals_top_two_top_five_and_ratio():
    rules = NR.rules("d2")
    finish = {"A": [1, 2, 3, 4, 5, 6, 7], "B": [11, 12, 13, 14, 15, 16, 17]}
    team_of = {1: 9, 2: None, 3: None, 4: None, 5: None, 6: None, 7: None}
    team_of.update({r: (8 if r < 14 else None) for r in finish["B"]})
    got = S.selectIndividuals(finish, team_of, {8, 9}, dict(rules, ind_at_large=1),
                              n_team_qual={"A": 1, "B": 4})
    auto = [r for r, _g, k in got if k == "auto"]
    # A: places 2..5 are non-qualifiers in the top five -> four autos
    assert auto[:4] == [2, 3, 4, 5]
    # B: top two non-qualifiers (places 4, 5)
    assert 14 in auto and 15 in auto
    # at-large ratio: A's next is place 6 (1/6), B's is place 6 (4/6)
    assert [r for r, _g, k in got if k == "at-large"] == [16]
