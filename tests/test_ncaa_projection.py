# Project: xc-predictor / tests
# File:    test_ncaa_projection.py
# Purpose: build_ncaa_projection's pure half on a toy season: races to wins,
#          the region lookup, and one whole projection end to end
#          (2026-10-10). No database.
#
#   python -m pytest -q tests/test_ncaa_projection.py
import os
import random
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_ncaa_projection as B                              # noqa: E402
import ncaa_rules as NR                                        # noqa: E402


def toySeason(div="d1", per_region=6, seed=3, n_races=12):
    """A season: every region of the division with `per_region` teams of
    eight runners, the teams' strength falling with their number, and
    `n_races` invitationals of random teams. Returns (athletes, races)."""
    rng = random.Random(seed)
    rules = NR.rules(div)
    athletes, pid = [], 1
    for ri, reg in enumerate(rules["regions"]):
        for k in range(per_region):
            base = 150 - 3 * k - 0.3 * ri
            for j in range(8):
                athletes.append({"person_id": pid, "name": f"Runner {pid}",
                                 "school": f"{reg} U{k}", "state": "ZZ",
                                 "rating": base - j * 0.8 + rng.random(),
                                 "n_races": 3, "region": reg.upper(),
                                 "conference": f"{reg} Conf"})
                pid += 1
    by_team = {}
    for a in athletes:
        by_team.setdefault(a["school"], []).append(a)
    names = sorted(by_team)
    races = []
    for i in range(n_races):
        field = rng.sample(names, 8)
        rows = []
        for sc in field:
            for a in by_team[sc]:
                t = 1800 * 150 / a["rating"] * (1 + rng.gauss(0, 0.02))
                rows.append({"person_id": a["person_id"], "school": sc,
                             "time_seconds": t, "status": None})
        races.append({"id": i, "day": f"2026-10-{1 + i:02d}", "name": f"Inv {i}",
                      "rows": rows})
    return athletes, races


def test_dual_winner():
    assert B.dualWinner([1, 2, 3, 4, 5], [6, 7, 8, 9, 10]) == "a"
    assert B.dualWinner([1, 2, 3, 4], [6, 7, 8, 9, 10]) is None      # four cannot score
    # 1+4+5+8+9 = 27 against 2+3+6+7+10 = 28 -- a wins, by one
    assert B.dualWinner([1, 4, 5, 8, 9], [2, 3, 6, 7, 10]) == "a"


def test_race_rows_keep_other_schools_places():
    rows = [{"person_id": 100 + i, "school": "A", "time_seconds": 900 + 2 * i, "status": None}
            for i in range(5)]
    rows += [{"person_id": 200 + i, "school": "B", "time_seconds": 901 + 2 * i, "status": None}
             for i in range(5)]
    team_of = {100 + i: 0 for i in range(5)}
    team_of.update({200 + i: 1 for i in range(5)})
    race = B.raceFromRows(rows, team_of, {}, dual=True)
    assert race["order"] == [0, 1]
    assert race["dual"] == {(0, 1): 0}
    # an outside school's five runners in front change no order between A and B
    rows += [{"person_id": 300 + i, "school": "C", "time_seconds": 800 + i, "status": None}
             for i in range(5)]
    assert B.raceFromRows(rows, team_of, {})["order"] == [0, 1]


def test_dnf_team_is_recorded():
    rows = [{"person_id": i, "school": "A", "time_seconds": 900 + i, "status": None}
            for i in range(5)]
    rows += [{"person_id": 10 + i, "school": "B", "time_seconds": 950 + i if i < 3 else None,
              "status": None if i < 3 else "DNF"} for i in range(5)]
    team_of = {i: 0 for i in range(5)}
    team_of.update({10 + i: 1 for i in range(5)})
    race = B.raceFromRows(rows, team_of, {})
    assert race["order"] == [0] and race["dnf"] == [1]


def test_region_resolution_order():
    official = B.loadOfficial()
    # the manual's D3 list places Tufts in Region I
    assert B.resolveRegion("d3", "M", "Tufts", "MA", None, official, {}) == ("Northeast", "manual")
    # an override wins over everything
    ov = {("d3", "", "tufts", ""): "Metro"}
    assert B.resolveRegion("d3", "M", "Tufts", "MA", None, official, ov) == ("Metro", "override")
    # D1 has no list: the data's region, when it is a current one
    assert B.resolveRegion("d1", "M", "Iowa State", "IA", "MIDWEST", official, {}) == ("Midwest", "data")
    assert B.resolveRegion("d1", "M", "Nowhere", "ZZ", "NEW ENGLAND", official, {}) == (None, None)
    # D3 realigned for 2026: an old regional's vote never places a team
    assert B.resolveRegion("d3", "M", "Nowhere", "ZZ", "GREAT LAKES", official, {}) == (None, None)


def test_official_lists_cover_every_region():
    official = B.loadOfficial()
    for div in ("d2", "d3"):
        for g in ("M", "F"):
            regions = {r for vals in official[(div, g)].values() for _s, r in vals}
            assert {NR.canonicalRegion(div, r) for r in regions} == set(NR.rules(div)["regions"])


def test_projection_end_to_end():
    athletes, races = toySeason("d1")
    teams, team_of = B.buildTeams(athletes, "d1", "M", {}, {})
    out = B.project("d1", "men", teams, team_of, races, draws=60, seed=1)
    rules = NR.rules("d1")
    assert out["counts"]["placed"] == len(rules["regions"]) * 6
    sel = out["selection"]
    assert len(sel["auto"]) == 18
    assert len(sel["at_large"]) + len(sel["pushed"]) == 14
    # a team's chances add up: in automatically, at large, or out
    for t in out["teams"]:
        assert abs(t["p_qual"] - t["p_auto"] - t["p_at_large"]) < 0.2
    # every draw sends the full field: 32 teams' worth of qualifying
    assert abs(sum(t["p_qual"] for t in out["teams"]) - 100 * 32) < 1
    # one winner per nationals
    assert abs(sum(t["p_win"] for t in out["teams"]) - 100) < 1
    # the strongest team of each region (U0) is nearly always in
    best = [t for t in out["teams"] if t["school"].endswith("U0")]
    assert min(t["p_qual"] for t in best) > 50
    # at-large rounds show the board each time
    assert out["selection"]["rounds"] and out["selection"]["rounds"][0]["candidates"]
    # individuals: four per region at most among autos, 38 in all at most
    assert len(out["individuals"]) <= 38


def test_projection_d2_and_d3_run():
    for div, n_auto in (("d2", 3), ("d3", 1)):
        athletes, races = toySeason(div, per_region=6)
        # D3 trusts no data region (a new alignment): the override file
        # places every toy team, as it would a team the manual misspells
        ov = {(div, "", a["school"].lower(), "ZZ"): a["region"] for a in athletes}
        teams, team_of = B.buildTeams(athletes, div, "F", {}, ov)
        assert all(t["region_src"] == "override" for t in teams)
        out = B.project(div, "women", teams, team_of, races, draws=30, seed=2)
        rules = NR.rules(div)
        assert len(out["selection"]["auto"]) == n_auto * len(rules["regions"])
        assert len(out["selection"]["at_large"]) == rules["at_large_teams"]
