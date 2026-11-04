"""One runner on two pages, found in bulk (owner, 2026-11-04: Mya Curtis)."""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for sub in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import find_split_people as F   # noqa: E402


def _s(pid, name, school, year, n, grade=None, level="college", g="f"):
    return {"person_id": pid, "name": name, "school": school, "level": level,
            "gender": g, "year": year, "n_races": n, "grade": grade}


def test_same_name_school_and_season_is_one_runner():
    seasons = [_s(16152289, "Mya Curtis", "Iowa Western CC", 2026, 2, "SO-2"),
               _s(16152289, "Mya Curtis", "Iowa Western CC", 2025, 8, "FR-1"),
               _s(29638265, "Mya Curtis", "Iowa Western CC", 2026, 1, "SO")]
    assert F.findPairs(seasons) == [(29638265, 16152289, "Mya Curtis", "Iowa Western CC")]


def test_a_minted_placeholder_never_keeps():
    seasons = [_s(1_009_000_001, "Ann Lee", "Tufts", 2026, 9), _s(33000000, "Ann Lee", "Tufts", 2026, 2)]
    assert F.findPairs(seasons)[0][:2] == (1_009_000_001, 33000000)


def test_no_shared_season_a_grade_clash_or_another_school_stays_apart():
    assert F.findPairs([_s(1, "Ann Lee", "Tufts", 2025, 3), _s(2, "Ann Lee", "Tufts", 2026, 3)]) == []
    assert F.findPairs([_s(1, "Ann Lee", "Tufts", 2026, 3, "FR-1"),
                        _s(2, "Ann Lee", "Tufts", 2026, 3, "SR-4")]) == []
    assert F.findPairs([_s(1, "Ann Lee", "Tufts", 2026, 3), _s(2, "Ann Lee", "Bates", 2026, 3)]) == []
    assert F.findPairs([_s(1, "Ann Lee", "Tufts", 2026, 3), _s(2, "Ann Lee Jr", "Tufts", 2026, 3)]) == []


def test_grades_compare_in_any_spelling():
    assert F.gradeKey("SO-2", "college") == F.gradeKey("So", "college") == F.gradeKey("14", "college")
    assert F.gradeKey("11th", "hs") == F.gradeKey("Jr", "hs") == "11"


def test_two_runners_in_one_race_are_two():
    class C:
        def execute(self, sql, p=None): pass
        def fetchall(self):
            return [(1, "2026-10-30", 9, 1100.0), (2, "2026-10-30", 9, 1180.0),
                    (3, "2026-10-30", 9, 1300.0), (4, "2026-10-30", 7, 1300.4)]
    bad = F.clashes(C(), [(2, 1, "x", "s"), (4, 3, "y", "s")])
    assert bad == {(2, 1)}            # 1300.0 and 1300.4 are one run in two feeds


def test_chains_and_forks_merge_into_one_page():
    """Nightly 2026-11-04: one page named the 'old' of two merges failed the run."""
    pairs = [(2, 1, "A", "S"), (3, 2, "A", "S"), (2, 4, "A", "T")]
    got = F.oneKeeper(pairs, {1: 50, 2: 3, 3: 1, 4: 10})
    olds = [o for o, *_x in got]
    assert len(olds) == len(set(olds)) == 3            # each page moves once
    assert {k for _o, k, *_x in got} == {1}             # all to the page with most races
    got = F.oneKeeper([(1_009_000_001, 7, "B", "S")], {1_009_000_001: 90, 7: 2})
    assert got == [(1_009_000_001, 7, "B", "S")]        # never a placeholder as keeper
