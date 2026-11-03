"""racecast/qualifying.py: the published rules applied to a set of rounds.
No database.

    python -m pytest -q tests/test_qualifying.py
"""
import math
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import qualifying as Q                                       # noqa: E402


def _region(tag, n_teams=6, per=7, strength=0.0, extras=3):
    """Teams tag0..tagN, team k slower than k-1; `extras` unattached
    individuals fastest of all."""
    rows, pid = [], 0
    for i in range(extras):
        rows.append((f"{tag}-ind{i}", f"{tag}-solo{i}", 900.0 + i, 60.0))
    for rank in range(per):
        for k in range(n_teams):
            rows.append((f"{tag}-{k}-{rank}", f"{tag}{k}", 1000.0 + 10 * k + 60 * rank,
                         50.0 + strength - k))
    return rows


def test_the_rules_find_their_meets():
    assert Q.ruleFor("NCAA Division I Cross Country Championships", "tfrrs")["key"] == "ncaa_d1"
    assert Q.ruleFor("NCAA Division II Cross Country Championships", "tfrrs")["key"] == "ncaa_d2"
    assert Q.ruleFor("2025 NCAA Division III Cross Country Championships", "tfrrs")["key"] == "ncaa_d3"
    assert Q.ruleFor("NCAA Division I Northeast Region Cross Country Championships", "tfrrs") is None
    assert Q.ruleFor("Nike Cross Nationals 2025", "anet")["key"] == "nxn"
    assert Q.ruleFor("NXR Southeast Regional", "anet") is None
    assert Q.isRound(Q.ruleFor("NCAA Division I Cross Country Championships"),
                     "NCAA Division I Mountain Region Cross Country Championships")
    assert not Q.isRound(Q.ruleFor("NCAA Division I Cross Country Championships"),
                         "NCAA Division II Midwest Region Championships")


def test_d1_by_its_manual():
    rule = Q.ruleFor("NCAA Division I Cross Country Championships", "tfrrs")
    rounds = {f"r{i}": _region(f"r{i}", n_teams=12, strength=i) for i in range(9)}
    field = Q.selectField(rule, rounds)
    import collections
    per = collections.Counter(field.values())
    teams = {s for s, n in per.items() if n >= 5}       # a team runs seven; an individual alone
    assert len(teams) == 18 + 14
    assert all(f"r{i}0" in teams and f"r{i}1" in teams for i in range(9))   # the auto bids
    # at-large keeps regional order: no region's fourth team before its third
    for i in range(9):
        if f"r{i}3" in teams:
            assert f"r{i}2" in teams
    assert sum(1 for s in field.values() if s in teams) == 32 * 7
    # 4 auto individuals a region (all 3 solos + the first non-qualifier), + 2 at-large
    assert sum(1 for s in field.values() if s not in teams) == 9 * 4 + 2


def test_at_large_teams_go_to_the_strongest_head_of_each_queue():
    got = Q.atLargeTeams({"a": ["a2", "a3"], "b": ["b2"]}, 2,
                         lambda k, s: {"a2": 1, "a3": 9, "b2": 5}[s])
    assert got == [("b", "b2"), ("a", "a2")]           # a3 waits behind a2
    assert Q.teamStrength([(1, "x", 1.0, None)], "x") == -math.inf


def test_d2_individuals_by_its_ratio():
    # region a sent 3 teams, b sent 1: a's 10th beats b's 5th (3/10 > 1/5? no: 0.3 > 0.2)
    got = Q.atLargeIndividuals({"a": [(10, "a10"), (11, "a11")], "b": [(5, "b5")]}, 2,
                               {"a": 3, "b": 1})
    assert got == ["a10", "a11"]


def test_the_check_scores_people_and_teams():
    import qualifying_check as C
    field = {1: "A", 2: "A", 3: "A", 4: "A", 5: "A", 9: "Z"}
    fin = [(1, "A"), (2, "A"), (3, "A"), (4, "A"), (6, "A"), (7, "B"), (8, "B"), (10, "B"),
           (11, "B"), (12, "B")]
    f, p, th, tr, miss, extra = C.score(field, fin)
    assert (round(f, 2), round(p, 2), th, tr, miss, extra) == (0.4, 0.67, 1, 2, ["B"], [])


def test_a_round_not_run_is_taken_from_its_forecast(monkeypatch):
    rule = Q.ruleFor("NCAA Division III Cross Country Championships", "tfrrs")
    monkeypatch.setattr(Q, "_roundMeets", lambda cur, r, d, w: [(i, f"R{i}", "2026-11-14")
                                                               for i in range(4)])
    ran = {0: _region("a"), 1: _region("b")}
    monkeypatch.setattr(Q, "roundResults", lambda cur, r, mid, g, s=None: ran.get(mid, []))
    asked = []

    def predict(mid, g):
        asked.append(mid)
        return _region(f"p{mid}")
    field, n_pred = Q.fieldFromRounds(None, rule, "2026-11-22", "F", predict)
    assert n_pred == 2 and asked == [2, 3] and field
    assert Q.fieldFromRounds(None, rule, "2026-11-22", "F") == (None, 0)   # 2 ran < MIN_ROUNDS


def test_a_learned_route_found_again_and_its_field(monkeypatch, tmp_path):
    import json
    path = tmp_path / "routes.json"
    path.write_text(json.dumps({"routes": [
        {"source": "anet", "meet": "springfield state championships", "race": "Class A Boys",
         "gender": "M", "rounds": [
             {"meet": "north regional", "race": "Class A Boys", "gender": "M",
              "date": "2025-10-25", "teams": 2, "individuals": 3},
             {"meet": "south regional", "race": "Class A Boys", "gender": "M",
              "date": "2025-10-25", "teams": 2, "individuals": 3}]},
        {"source": "anet", "meet": "springfield state championships", "race": "Class B Boys",
         "gender": "M", "rounds": []}]}))
    monkeypatch.setattr(Q, "ROUTES_PATH", str(path))
    Q._ROUTES["mtime"] = None
    r = Q.learnedRoute("2026 Springfield State Championships", "anet", "Class A Boys", "M")
    assert r and r["race"] == "Class A Boys"
    assert Q.learnedRoute("2026 Springfield State Championships", "anet", "Boys 5K", "M") is None
    rows = _region("n", n_teams=5, extras=0)
    got = Q.simpleQualifiers(rows, 2, 3)
    assert sum(1 for s in got.values() if s in ("n0", "n1")) == 14 and len(got) == 17
    monkeypatch.setattr(Q, "_thisRound", lambda cur, rnd, day, src: (
        (1, 1, False) if rnd["meet"] == "north regional" else None))
    assert Q.learnedField(None, r, "2026-11-07", lambda mid, div: rows) == (None, 0), \
        "one of two rounds found: not enough (ROUTE_FOUND)"
    monkeypatch.setattr(Q, "_thisRound", lambda cur, rnd, day, src: (1, 1, False))
    field, n_pred = Q.learnedField(None, r, "2026-11-07", lambda mid, div: rows)
    assert n_pred == 2 and len(field) == 17                  # both found (the same rows twice)


def test_nxr_rounds_are_the_regional_championships_only():
    rule = Q.ruleFor("Nike Cross Nationals", "anet")
    for name in ("NXR Northwest Regional Championships", "NXR Northeast & New York Regional Open",
                 "NXR South Regional Championships & Community Race XC25"):
        assert Q.isRound(rule, name), name
    for name in ("NXR Midwest MS & Community Race", "NXR Southwest Citizens Race",
                 "NXR NE/NY Kids Race Presented by USATF New York",
                 "NXR Northwest Grade School/Middle School Championships"):
        assert not Q.isRound(rule, name), name
    assert Q.isRound(rule, "NYS Federation Championships at NXR")
    races = [{"label": "Boys Championship"}, {"label": "Boys Community 5K"}]
    assert Q.pickRace(races) == [races[0]]
    assert Q.pickRace([{"label": "Men 10k"}, {"label": "Men 8k"}]) == [{"label": "Men 10k"},
                                                                          {"label": "Men 8k"}]


def test_unattached_runners_are_never_a_team():
    for s in ("Unattached", "Unattached IL", "UNA-CO", "UNA-UT", "Independent"):
        assert not Q.isTeam(s), s
    assert Q.isTeam("American Fork") and Q.isTeam("Christ School")
    rows = [(i, "UNA-CO", 900.0 + i, None) for i in range(6)] + \
        [(10 + i, "Lone Peak", 950.0 + i, None) for i in range(5)]
    assert Q.teamOrder(rows) == ["Lone Peak"]


def test_at_large_teams_can_come_from_another_meet():
    rule = dict(Q.ruleFor("Nike Cross Nationals", "anet"), teams_at_large=2)
    rounds = {f"r{i}": _region(f"r{i}", n_teams=4, strength=0) for i in range(3)}
    pools = {"cif": _region("ca", n_teams=3, strength=20, extras=0)}
    import collections
    field = Q.selectField(rule, rounds, pools)
    per = collections.Counter(field.values())
    teams = {s for s, n in per.items() if n >= 5}
    assert {"ca0", "ca1"} <= teams and "ca2" not in teams          # the two strongest at-large
    assert all(f"r{i}0" in teams and f"r{i}1" in teams for i in range(3))
    # a school already in automatically is not picked twice
    got = Q.atLargeTeams({"a": ["x", "y"], "b": ["x", "z"]}, 2, lambda k, s: 1, taken={"x"})
    assert sorted(s for _k, s in got) == ["y", "z"]


def test_the_predictions_field_from_qualifiers(monkeypatch):
    import predict as P
    import upcoming_preview as U

    class Cur:
        def execute(self, *a, **k):
            pass
    monkeypatch.setattr(P, "_exactField", lambda *a, **k: [])
    monkeypatch.setattr(U, "meetPlan", lambda *a, **k: {"name": "x"})
    monkeypatch.setattr(U, "qualifiedFieldInfo", lambda cur, plan, div: (
        {1: "A", 2: "A", 3: "B"}, {"rule": "ncaa_d1", "rounds": 9, "predicted": 9}))
    monkeypatch.setattr(P, "_currentSeason", lambda cur, s: 2026)
    monkeypatch.setattr(P, "_athleteEntries", lambda cur, ids, s, y: [
        {"person_id": i, "school": "Z", "name": f"n{i}", "grade": "SR", "pool": "college_m",
         "rating": 100.0 + i} for i in sorted(ids)])
    monkeypatch.setattr(P, "_teamStates", lambda cur, rows, st: {})
    monkeypatch.setattr(P, "_stampCrests", lambda *a, **k: None)
    out = U.qualifiedMeetField(Cur(), 5, 2, "tfrrs")
    assert out["basis"] == {"rule": "ncaa_d1", "rounds": 9, "predicted": 9, "kind": "qualified"}
    a, b = out["teams"]
    assert (a["school"], [r["person_id"] for r in a["runners"]], a["entered"]) == ("A", [2, 1], 2)
    assert b["school"] == "B"                                 # the school they qualified for
    monkeypatch.setattr(P, "_exactField", lambda *a, **k: [{"person_id": 1}])
    assert U.qualifiedMeetField(Cur(), 5, 2, "tfrrs") is None   # it has run: the field that ran


def test_upcoming_championships_from_last_years_meets(monkeypatch):
    import datetime
    import upcoming_preview as U

    class Cur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return [("tfrrs", 1, "2025 NCAA Division I Cross Country Championships", "2025-11-22"),
                    ("anet", 2, "2025 MSHSL Class A State Championships", "2025-11-08"),
                    ("anet", 3, "Some Invitational", "2025-11-15"),
                    ("anet", 4, "2025 MSHSL Class AA State Championships", "2025-10-25")]
    monkeypatch.setattr(Q, "_loadRoutes", lambda: {("anet", "mshsl class a state championships"): [{}],
                                                   ("anet", "mshsl class aa state championships"): [{}]})
    got = U.upcomingChampionships(Cur(), datetime.date(2026, 11, 3))
    assert [(m["meet_id"], m["date"], m["kind"]) for m in got] == [
        (2, "2026-11-07", "state"), (1, "2026-11-21", "national")]   # a year on, ahead only


def test_a_race_with_no_gender_takes_last_years_runners(monkeypatch):
    import last_edition as LE
    import predict as P
    import upcoming_preview as U
    asked = {}

    class Cur:
        def execute(self, sql, args=None):
            if args:
                asked["div"] = args[1]

        def fetchall(self):
            return [(1,), (2,)]
    monkeypatch.setattr(LE, "editionRaces", lambda cur, mid, sport, src: [
        {"div_id": 50, "label": "Division I"}, {"div_id": 51, "label": "Division I"},
        {"div_id": 52, "label": "Division II"}])
    monkeypatch.setattr(P, "_fieldGender", lambda cur, ids, sport: "F" if asked["div"] == 51 else "M")
    plan = {"source": "anet", "edition": {"meet_id": 9},
            "races": [{"div_id": 7, "label": "Division I"}, {"div_id": 8, "label": "Division I"}]}
    assert U.lastYearGender(Cur(), plan, plan["races"][1]) == "F"     # the second of its name
    assert U.lastYearGender(Cur(), plan, plan["races"][0]) == "M"
    assert U.lastYearGender(Cur(), {"races": []}, {"div_id": 1, "label": "x"}) is None
