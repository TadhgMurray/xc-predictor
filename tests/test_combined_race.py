"""
Issue #86: several divisions scored as ONE race.

Owner's ruling: a school entered in two divisions is TWO teams, distinctly
labelled, by default -- with an option to coalesce them into one squad.
Coalescing is capped at seven, or a school entered twice would field fourteen.
"""
import os
import sys

_ROOT = os.path.join(os.path.dirname(__file__), "..")
for _p in ("engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
import predict                                                  # noqa: E402


def _e(pid, school):
    return {"person_id": pid, "school": school, "name": f"R{pid}"}


def _install(per_div, labels, gender="M"):
    """Point _combinedRoster's three helpers at fixtures. Returns an undo.

    ! _fieldGender IS STUBBED TOO, since coalescing reads it per division to
      keep boys and girls apart. One gender by default, which is the ordinary
      meet; the cross-gender case passes its own.
    """
    saved = (predict._teamRosters, predict._divisionLabel,
             predict._fieldGender)
    predict._teamRosters = lambda cur, schools, one, *a, **k: [
        dict(e) for e in per_div[one["div_id"]]]
    predict._divisionLabel = lambda cur, m, d, s: labels.get(d)
    predict._fieldGender = lambda cur, ids, sport: gender

    def undo():
        (predict._teamRosters, predict._divisionLabel,
         predict._fieldGender) = saved
    return undo


PER_DIV = {
    "1": [_e(1, "Cabell Midland"), _e(2, "Cabell Midland"), _e(3, "Alpha")],
    "2": [_e(4, "Cabell Midland"), _e(5, "Cabell Midland"), _e(6, "Beta")],
}
LABELS = {"1": "Varsity", "2": "JV"}


def test_a_school_in_two_divisions_becomes_two_labelled_teams():
    undo = _install(PER_DIV, LABELS)
    try:
        out = predict._combinedRoster(
            None, {"meet_id": 9, "coalesce": False}, ["1", "2"], "XC", "rerun")
    finally:
        undo()
    schools = sorted({e["school"] for e in out})
    assert "Cabell Midland (Varsity)" in schools, schools
    assert "Cabell Midland (JV)" in schools, schools
    assert "Cabell Midland" not in schools, schools
    # ! A SCHOOL IN ONLY ONE DIVISION IS NOT SUFFIXED -- that would be noise.
    assert "Alpha" in schools and "Beta" in schools, schools
    print(f"  two divisions -> {schools} ... OK")


def test_coalesce_keeps_the_bare_name():
    undo = _install(PER_DIV, LABELS)
    try:
        out = predict._combinedRoster(
            None, {"meet_id": 9, "coalesce": True}, ["1", "2"], "XC", "rerun")
    finally:
        undo()
    schools = {e["school"] for e in out}
    assert schools == {"Cabell Midland", "Alpha", "Beta"}, schools
    print("  coalesced -> one 'Cabell Midland' squad ............ OK")


def test_one_row_per_person():
    """An athlete entered in two divisions must not run twice in one race."""
    dup = {"1": [_e(1, "Alpha")], "2": [_e(1, "Alpha"), _e(2, "Alpha")]}
    undo = _install(dup, LABELS)
    try:
        out = predict._combinedRoster(
            None, {"meet_id": 9}, ["1", "2"], "XC", "rerun")
    finally:
        undo()
    assert sorted(e["person_id"] for e in out) == [1, 2], out
    print("  an athlete in both divisions appears once .......... OK")


def test_coalesced_squad_is_capped_at_seven():
    """Fourteen runners under one name would take fourteen places and push
    every other team down -- an advantage no real team could have."""
    field = [_e(i, "Big") for i in range(14)] + [_e(100, "Small")]
    preds = ([{"seconds": 900 + i} for i in range(14)]
             + [{"seconds": 905}])
    f2, p2 = predict._capCoalesced(field, preds)

    big = [f for f in f2 if f["school"] == "Big"]
    assert len(big) == predict.MAX_PER_TEAM, len(big)
    # the SEVEN FASTEST, not the first seven encountered
    assert sorted(f["person_id"] for f in big) == list(range(7)), big
    assert any(f["school"] == "Small" for f in f2), "other teams were trimmed"
    assert len(f2) == len(p2), "field and preds fell out of step"
    print(f"  14 -> {len(big)} kept, fastest seven ................ OK")


def test_the_cap_leaves_unpredictable_runners_alone():
    """They are not in the scoring order at all; _score already drops them."""
    field = [_e(1, "Big"), _e(2, "Big")]
    preds = [{"seconds": None}, {"seconds": 900}]
    f2, p2 = predict._capCoalesced(field, preds)
    assert len(f2) == 2 and len(p2) == 2, (f2, p2)
    print("  an unpredictable runner is not trimmed ............. OK")


def test_unattached_is_never_capped():
    """Unattached is not a team -- capping it to seven would invent one."""
    field = [_e(i, "Unattached") for i in range(10)]
    preds = [{"seconds": 900 + i} for i in range(10)]
    f2, _ = predict._capCoalesced(field, preds)
    assert len(f2) == 10, len(f2)
    print("  unattached runners are all kept .................... OK")


# ------------------------------------------------------------------ #
# Coalesce has to be VISIBLE, or it cannot be checked (owner, 2026-09-01:
# "need to check coalesce actually works... which div does it end up
# showing in?").
# ------------------------------------------------------------------ #

def test_coalesce_never_merges_across_genders():
    """A boys team and a girls team are two teams, not a fourteen-runner squad.

    ⚠ IT USED TO MERGE THEM. _score groups on the school name and
      _capCoalesced keeps the FASTEST SEVEN of whatever lands under it -- so
      a school entered in a boys race and a girls race was trimmed to its
      seven fastest, which is the boys. The girls team did not lose; it
      silently stopped existing. Mixed-gender races are explicitly allowed
      (owner's ruling), which is exactly why this matters: both teams have
      to be on the start line.
    """
    calls = {"n": 0}

    class Cur:
        """_fieldGender per division, answering from the fixture."""
        def execute(self, sql, params=None):
            calls["n"] += 1

        def fetchall(self):
            return []

    def fake_rosters(cur, schools, target, *a, **k):
        d = target.get("div_id")
        # D2 boys and D3 girls; Alpha is in both, Beta only in the boys race.
        if d == 10:
            return [{"person_id": 1, "name": "A1", "school": "Alpha"},
                    {"person_id": 2, "name": "B1", "school": "Beta"}]
        return [{"person_id": 3, "name": "A2", "school": "Alpha"}]

    saved = (predict._teamRosters, predict._divisionLabel,
             predict._fieldGender)
    predict._teamRosters = fake_rosters
    predict._divisionLabel = lambda cur, m, d, s: {10: "D2", 11: "D3"}[d]
    predict._fieldGender = lambda cur, ids, sport: "M" if 1 in ids else "F"
    try:
        out = predict._combinedRoster(
            Cur(), {"meet_id": 1, "coalesce": True, "sport": "XC"},
            [10, 11], "XC", "rerun")
    finally:
        (predict._teamRosters, predict._divisionLabel,
         predict._fieldGender) = saved

    by = {}
    for e in out:
        by.setdefault(e["school"], []).append(e["person_id"])

    assert sorted(by) == ["Alpha (Boys)", "Alpha (Girls)", "Beta"], sorted(by)
    assert by["Alpha (Boys)"] == [1], by
    assert by["Alpha (Girls)"] == [3], by
    # ! BETA IS UNTOUCHED. Only a school on BOTH sides is split; suffixing
    #   every team would put "(Boys)" on schools that only ran one race.
    assert by["Beta"] == [2], by
    print("  coalesce keeps boys and girls as two teams ......... OK")


def test_coalesce_still_merges_within_one_gender():
    """The ordinary case: two boys divisions, one squad, bare name."""
    def fake_rosters(cur, schools, target, *a, **k):
        d = target.get("div_id")
        return [{"person_id": 1 if d == 10 else 2, "name": "A",
                 "school": "Alpha"}]

    class Cur:
        def execute(self, sql, params=None):
            pass

        def fetchall(self):
            return []

    saved = (predict._teamRosters, predict._divisionLabel,
             predict._fieldGender)
    predict._teamRosters = fake_rosters
    predict._divisionLabel = lambda cur, m, d, s: {10: "D2", 11: "D3"}[d]
    predict._fieldGender = lambda cur, ids, sport: "M"
    try:
        out = predict._combinedRoster(
            Cur(), {"meet_id": 1, "coalesce": True, "sport": "XC"},
            [10, 11], "XC", "rerun")
    finally:
        (predict._teamRosters, predict._divisionLabel,
         predict._fieldGender) = saved

    assert {e["school"] for e in out} == {"Alpha"}, out
    assert sorted(e["person_id"] for e in out) == [1, 2], out
    print("  and still merges two divisions of one gender ...... OK")


def test_coalesced_team_names_the_divisions_it_came_from():
    """A coalesced squad appears ONCE, under its bare name -- which is
    indistinguishable from a team that only ever ran one division. The
    divisions it was drawn from are the only sign the checkbox did anything.
    """
    field = [
        {"person_id": 1, "name": "A", "school": "Alpha", "div_label": "D2"},
        {"person_id": 2, "name": "B", "school": "Alpha", "div_label": "D2"},
        {"person_id": 3, "name": "C", "school": "Alpha", "div_label": "D3"},
        {"person_id": 4, "name": "D", "school": "Alpha", "div_label": "D3"},
        {"person_id": 5, "name": "E", "school": "Alpha", "div_label": "D3"},
        {"person_id": 6, "name": "F", "school": "Beta",  "div_label": "D2"},
        {"person_id": 7, "name": "G", "school": "Beta",  "div_label": "D2"},
        {"person_id": 8, "name": "H", "school": "Beta",  "div_label": "D2"},
        {"person_id": 9, "name": "I", "school": "Beta",  "div_label": "D2"},
        {"person_id": 10, "name": "J", "school": "Beta", "div_label": "D2"},
    ]
    preds = [{"seconds": 900 + i} for i in range(len(field))]
    out, _finishers = predict._score(field, preds)
    by = {t["team"]: t for t in out}

    # Alpha ran both divisions and was coalesced into one squad.
    assert by["Alpha"]["divs"] == ["D2", "D3"], by["Alpha"]
    # Beta only ever ran one, so there is nothing to say -- a note there
    # would just restate the division the section already sits under.
    assert by["Beta"]["divs"] is None, by["Beta"]
    print("  a coalesced squad names the divisions it came from . OK")


def test_divs_is_absent_without_labels():
    """An ordinary single-division race carries no div_label at all, and must
    not grow an empty note."""
    field = [{"person_id": i, "name": str(i), "school": "Alpha"}
             for i in range(1, 8)]
    preds = [{"seconds": 900 + i} for i in range(7)]
    out, _finishers = predict._score(field, preds)
    assert out[0]["divs"] is None, out[0]
    print("  a single-division race says nothing extra ......... OK")


if __name__ == "__main__":
    for fn in [test_a_school_in_two_divisions_becomes_two_labelled_teams,
               test_coalesce_keeps_the_bare_name,
               test_one_row_per_person,
               test_coalesced_squad_is_capped_at_seven,
               test_the_cap_leaves_unpredictable_runners_alone,
               test_unattached_is_never_capped,
               test_coalesce_never_merges_across_genders,
               test_coalesce_still_merges_within_one_gender,
               test_coalesced_team_names_the_divisions_it_came_from,
               test_divs_is_absent_without_labels]:
        fn()
    print("\nall combined-race tests passed")
