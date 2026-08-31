"""
A school that brought five or fewer runners to the original meet must not come
back with a full seven in the prediction.

The two cap sites (meetField, which the page shows, and _teamRosters, which the
model scores) are exercised against fake DB helpers, so this needs no database.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))
import predict                                                  # noqa: E402


# ------------------------------------------------------------------ #
# the pure helpers
# ------------------------------------------------------------------ #

def test_cap_table():
    # 0 = not at the original meet (a manually named school) -> rulebook seven
    assert predict.squadCap(0) == 7
    for n in (1, 2, 3, 4, 5):
        assert predict.squadCap(n) == n, n
    for n in (6, 7, 8, 25):
        assert predict.squadCap(n) == 7, n
    print("  cap table 0->7, 1..5->n, 6+->7 ................... OK")


def test_counts_ignore_schoolless_rows():
    originals = [{"school": "Alpha"}, {"school": "Alpha"},
                 {"school": None}, {"school": ""}, {"school": "Beta"}]
    c = predict.countsBySchool(originals)
    assert c == {"Alpha": 2, "Beta": 1}, c
    print("  countsBySchool ignores schoolless rows ........... OK")


# ------------------------------------------------------------------ #
# the two real call sites, with the database faked out
# ------------------------------------------------------------------ #

def _person(pid, school, name=None):
    return {"person_id": pid, "name": name or f"R{pid}", "school": school}


def _install(monkey_originals, monkey_squads):
    """Point predict's three DB helpers at fixtures. Returns an undo."""
    saved = (predict._exactField, predict._currentSquads,
             predict._currentSeason)
    predict._exactField = lambda cur, m, d, s: monkey_originals
    predict._currentSquads = lambda cur, schools, s, y: {
        k: v for k, v in monkey_squads.items() if k in schools}
    predict._currentSeason = lambda cur, s: 2026
    def undo():
        (predict._exactField, predict._currentSquads,
         predict._currentSeason) = saved
    return undo


# Big brought seven; Small brought two individuals; Mid brought six.
ORIGINALS = ([_person(i, "Big") for i in range(1, 8)]
             + [_person(101, "Small"), _person(102, "Small")]
             + [_person(i, "Mid") for i in range(200, 206)])

# every school has a deep current squad -- ten each
SQUADS = {
    "Big":   [{"person_id": 1000 + i, "name": f"B{i}"} for i in range(10)],
    "Small": [{"person_id": 2000 + i, "name": f"S{i}"} for i in range(10)],
    "Mid":   [{"person_id": 3000 + i, "name": f"M{i}"} for i in range(10)],
}


def test_meetField_caps_the_small_school():
    undo = _install(ORIGINALS, SQUADS)
    try:
        out = predict.meetField(None, meet_id=1, div_id=None, sport="XC",
                                when="thisyear")
    finally:
        undo()
    teams = {t["school"]: t for t in out["teams"]}

    assert len(teams["Small"]["runners"]) == 2, teams["Small"]["runners"]
    assert len(teams["Big"]["runners"]) == 7
    assert len(teams["Mid"]["runners"]) == 7, "six is a team one short"

    # ! CAPPED, NOT DISCARDED -- the rest must remain hand-addable. Small's
    #   dropped list is the 8 squad members past the cap PLUS the 2 originals
    #   who have no current-season row (the graduated-or-injured path).
    dropped_ids = {d["person_id"] for d in teams["Small"]["dropped"]}
    assert {2002 + i for i in range(8)} <= dropped_ids, dropped_ids
    assert {101, 102} <= dropped_ids, dropped_ids
    assert len(teams["Small"]["dropped"]) == 10, teams["Small"]["dropped"]
    print(f"  meetField: Small {len(teams['Small']['runners'])}, "
          f"Mid {len(teams['Mid']['runners'])}, "
          f"Big {len(teams['Big']['runners'])} .............. OK")


def test_teamRosters_agrees_with_meetField():
    """If these disagree the page shows one lineup and the model scores another."""
    undo = _install(ORIGINALS, SQUADS)
    try:
        shown = predict.meetField(None, meet_id=1, div_id=None, sport="XC",
                                  when="thisyear")
        scored = predict._teamRosters(
            None, schools=None,
            target={"mode": "rerun", "sport": "XC", "meet_id": 1})
    finally:
        undo()

    by_school_shown = {t["school"]: len(t["runners"]) for t in shown["teams"]}
    scored_counts = {}
    ids_to_school = {e["person_id"]: sch for sch, sq in SQUADS.items()
                     for e in sq}
    for e in scored:
        sch = ids_to_school[e["person_id"]]
        scored_counts[sch] = scored_counts.get(sch, 0) + 1

    assert scored_counts == {"Big": 7, "Mid": 7, "Small": 2}, scored_counts
    for sch, n in scored_counts.items():
        assert by_school_shown[sch] == n, (sch, by_school_shown[sch], n)
    print(f"  _teamRosters matches meetField: {scored_counts} ... OK")


def test_a_school_that_brought_one_gets_one():
    solo = [_person(1, "Solo")] + [_person(i, "Big") for i in range(10, 17)]
    undo = _install(solo, {**SQUADS, "Solo": [{"person_id": 9000 + i,
                                               "name": f"X{i}"}
                                              for i in range(10)]})
    try:
        out = predict.meetField(None, meet_id=1, div_id=None, sport="XC",
                                when="thisyear")
    finally:
        undo()
    teams = {t["school"]: t for t in out["teams"]}
    assert len(teams["Solo"]["runners"]) == 1, teams["Solo"]["runners"]
    print("  one individual stays one, not seven ............... OK")


if __name__ == "__main__":
    for fn in [test_cap_table,
               test_counts_ignore_schoolless_rows,
               test_meetField_caps_the_small_school,
               test_teamRosters_agrees_with_meetField,
               test_a_school_that_brought_one_gets_one]:
        fn()
    print("\nall squad-cap tests passed")
