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
