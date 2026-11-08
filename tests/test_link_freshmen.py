"""The freshman linker's matching rules (owner, 2026-09-25: "all freshman are
unknown... they have not been linked with anet"). Pure: no database.

    python -m pytest -q tests/test_link_freshmen.py
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import link_freshmen as L                                    # noqa: E402


def test_names_normalise_across_the_two_feeds():
    assert L.normName("HENOK, Meba") == "meba henok"
    assert L.normName("Meba  Henok") == "meba henok"
    assert L.normName("Liu-Walter, Zachary") == "zachary liu walter"
    assert L.normName("Cher") is None and L.normName(None) is None


FRESH = {  # tfrrs pid: (name, college, gender, n)
    101: ("Jane Frosh", "Tufts", "F", 4),
    102: ("Sam Twin", "Tufts", "M", 3),
    103: ("Alex Solo", "Williams", "M", 5),
    104: ("Pat Swap", "Tufts", "F", 2),
    105: ("Lee Taken", "Tufts", "M", 2),
}
SENIOR = {  # anet pid: (name, high school, gender, has_tfrrs)
    1: ("Jane Frosh", "Newton North", "F", False),
    2: ("Sam Twin", "Acton-Boxborough", "M", False),
    3: ("Sam Twin", "Brookline", "M", True),     # already linked -- still counts
    4: ("Alex Solo", "Wayland", "M", False),
    5: ("Pat Swap", "Lexington", "M", False),     # gender differs
    6: ("Lee Taken", "Needham", "M", True),       # already on tfrrs
}


def test_only_unique_agreeing_strangers_pair():
    pairs, skipped = L.pairsFor(FRESH, SENIOR)
    got = {(t, a) for t, a, *_ in pairs}
    assert got == {(101, 1), (103, 4)}
    assert skipped == {"name not unique": 1, "gender differs": 1,
                       "senior already on tfrrs": 1}


def test_a_senior_claimed_twice_across_years_joins_neither():
    pairs = [(101, 1, "a", "c", "h"), (201, 1, "a", "c", "h"),
             (103, 4, "b", "c", "h"), (103, 4, "b", "c", "h")]
    assert [(t, a) for t, a, *_ in L.dedupePairs(pairs)] == [(103, 4)]


def test_academic_year():
    import datetime
    assert L.academicYear(datetime.date(2026, 9, 25)) == 2026
    assert L.academicYear(datetime.date(2026, 5, 1)) == 2025


def test_only_a_college_freshman_grade_counts():
    """! The first dry run paired seniors with 'first-years' at a junior high
    -- tfrrs hosts some school meets too."""
    import re
    fr = re.compile(L.FRESHMAN_RE)
    for g in ("fr", "fr-1", "freshman", "13"):
        assert fr.match(g), g
    for g in ("8", "12", "so-2", "sr", "7th"):
        assert not fr.match(g), g


def test_unlinked_tfrrs_identities_pair_by_their_athlete_id():
    fresh = {"n:tfrrs:8812345": ("Jane Frosh", "Tufts", None, 3)}
    senior = {1: ("Jane Frosh", "Newton North", "F", False)}
    pairs, _ = L.pairsFor(fresh, senior)
    assert [(t, a) for t, a, *_ in pairs] == [("n:tfrrs:8812345", 1)]


def test_a_senior_racing_open_college_meets_unattached_is_still_a_senior():
    # owner, 2026-10-11 (Connor Fanberg): only a COLLEGE tfrrs row keeps a
    # senior out of the freshman pass, not an unattached high-school one
    import inspect
    import link_freshmen as LF
    src = inspect.getsource(LF.seniors)
    # has_tfrrs (two tables) and, since 2026-11-04, the colleges those rows are at
    assert src.count("r.source = 'tfrrs' AND {collegeSql('r')}") == 4
    assert "r.source = 'tfrrs')" not in src and "r.source = 'tfrrs'\n" not in src


def test_a_nickname_finds_the_senior_only_when_unique():
    """2026-11-02: Chris Hovet (tfrrs, Tufts) and Christopher Hovet (his
    high school senior year)."""
    fresh = {201: ("Chris Hovet", "Tufts", "M", 3), 202: ("Will Burch", "Tufts", "M", 2)}
    senior = {11: ("Christopher Hovet", "Somewhere HS", "M", False),
              12: ("William Burch", "A HS", "M", False), 13: ("Bill Burch", "B HS", "M", False)}
    pairs, skipped = L.pairsFor(fresh, senior)
    assert [(t, a) for t, a, *_x in pairs] == [(201, 11)]
    assert skipped.get("nickname not unique") == 1      # two Burch seniors



def test_a_namesake_of_the_other_gender_is_no_rival():
    """2026-11-04 ("do 1"): Jordan Smith the senior girl does not make the
    first-year boy ambiguous; an unknown gender still counts."""
    fresh = {301: ("Jordan Smith", "Tufts", "M", 3)}
    senior = {21: ("Jordan Smith", "A HS", "M", False, False),
              22: ("Jordan Smith", "B HS", "F", False, False)}
    pairs, _ = L.pairsFor(fresh, senior)
    assert [(t, a) for t, a, *_x in pairs] == [(301, 21)]
    senior[23] = ("Jordan Smith", "C HS", None, False, False)
    pairs, skipped = L.pairsFor(fresh, senior)
    assert pairs == [] and skipped.get("name not unique") == 1


def test_a_senior_already_at_this_college_is_this_first_year():
    """2026-11-04: 615 skipped as "senior already on tfrrs" -- one runner
    whose college rows were split between the high school person and a
    stranger. The same college joins; another college does not."""
    fresh = {401: ("Ann Lee", "Tufts University", "F", 4)}
    same = {31: ("Ann Lee", "Some HS", "F", True, False, ("Tufts",))}
    pairs, skipped = L.pairsFor(fresh, same)
    assert [(t, a) for t, a, *_x in pairs] == [(401, 31)]
    other = {31: ("Ann Lee", "Some HS", "F", True, False, ("Bates",))}
    pairs, skipped = L.pairsFor(fresh, other)
    assert pairs == [] and skipped.get("senior already on tfrrs") == 1


def test_a_college_namesake_on_the_main_feed_keeps_the_pair_apart():
    """Owner, 2026-11-08: the college feed's copies of Hayden Buck's East Texas
    Baptist races were joined to a Guam senior of the same name, though the
    main feed already had those races under the Tennessee Hayden Buck."""
    import link_freshmen as LF
    pairs = [("p:-9", 29370341, "Hayden Buck", "East Texas Baptist", "Guam"),
             ("p:-8", 777, "Ann Lee", "Tufts", "Rivers")]
    ns = {LF.normName("Hayden Buck"): [(19764025, "East Texas Baptist")],
          LF.normName("Ann Lee"): [(777, "Tufts")]}           # the senior herself: no rival
    hs = {19764025: {(2023, "gatlinburg-pittman"), (2024, "gatlinburg-pittman")},
          29370341: {(2023, "guam"), (2024, "guam")}, 777: {(2024, "rivers")}}
    kept, dropped = LF.dropCollegeNamesakes(pairs, ns, hs)
    assert dropped == 1 and [p[1] for p in kept] == [777]
