"""
Issues #82 and #83: who carries forward when a school has no current-season
row yet.

  #82  a graduated senior must not carry. The terminal-grade test used to be
       exact equality against a hand-listed set of case variants, so "12th",
       "Senior" and anything whitespace-padded slipped through.
  #83  a transfer must not carry either. They are not a terminal grade, so
       aging-out left them on the OLD school's squad while they raced for the
       new one.

_squadsForYear is pure SQL, so these assert the query it builds. A fake cursor
captures the statement and its parameters -- no database.
"""
import os
import re
import sys

# ⚠ _squadsForYear STAMPS THE HS-EQUIVALENT NOW, and pool_view -> conversions
#   -> database -> config resolves a connection AT IMPORT. config.py refuses
#   to default a password on purpose, so without this the import raises and
#   this test cannot run on any machine that has no credentials. Nothing here
#   opens a connection; the value is never used. Same shim as the dozen other
#   suites that touch a module with a DB import in its chain.
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

_ROOT = os.path.join(os.path.dirname(__file__), "..")
# engine/ too: _currentSquads reads season_year.academicYear to decide
# whether the season it was handed is already over.
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))
import predict                                                  # noqa: E402


class FakeCursor:
    """Captures the last statement and params; returns no rows."""
    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())        # collapse whitespace
        self.params = params or {}

    def fetchall(self):
        return []


def test_terminal_grades_are_keys_not_spellings():
    """★ SPELLINGS WERE THE BUG, NOT A DETAIL OF IT. Listing them meant the
    list had to be complete, and it never was: "Sr.", "SR-4" and a bare "16"
    are all seniors that ["12", "12TH", "SR", "SENIOR"] let through, and each
    one stayed on a carried-forward roster for a year after they left.

    gradeKeySql normalises instead -- digits out of the string, the
    fr/so/jr/sr prefixes read, a college 13-16 mapped onto its class word --
    so the terminal set is two KEYS and cannot be incomplete."""
    assert predict._TERMINAL_KEYS == ("12", "sr"), predict._TERMINAL_KEYS
    # ⚠ the old list must not survive beside them: two answers to "who is a
    #   senior" is how the incomplete one gets picked up again
    assert not hasattr(predict, "_TERMINAL_GRADES")
    print(f"  terminal keys {predict._TERMINAL_KEYS} .................. OK")


def test_aging_out_compares_the_grade_key():
    cur = FakeCursor()
    predict._squadsForYear(cur, ["Alpha"], "XC", 2025, exclude_terminal=True)
    # the normalisation, not a literal: this is gradeKeySql's own shape
    assert "regexp_replace(lower(trim(s.grade))" in cur.sql, cur.sql
    assert "ARRAY['fr','so','jr','sr']" in cur.sql, cur.sql
    assert "<> ALL(%(term_keys)s)" in cur.sql, cur.sql
    assert cur.params["term_keys"] == list(predict._TERMINAL_KEYS)
    # ! AN UNKNOWN GRADE IS NOT A SENIOR. NULL and blank stay on the roster
    #   and are flagged for a human, rather than being aged out on a guess.
    assert "s.grade IS NULL OR BTRIM(s.grade) = ''" in cur.sql, cur.sql
    # the dead spelling list must not still be riding along as a parameter
    assert "term" not in cur.params, cur.params
    print("  aging-out compares the normalised grade key ........ OK")


def test_no_grade_clause_when_not_aging_out():
    cur = FakeCursor()
    predict._squadsForYear(cur, ["Alpha"], "XC", 2026)
    assert "BTRIM" not in cur.sql, cur.sql
    print("  the current-season query has no grade filter ...... OK")


def test_transfers_are_excluded_only_when_asked():
    cur = FakeCursor()
    predict._squadsForYear(cur, ["Alpha"], "XC", 2025,
                           exclude_terminal=True, active_year=2026)
    assert "NOT EXISTS" in cur.sql, cur.sql
    assert "c.year = %(active_yr)s" in cur.sql, cur.sql
    assert cur.params["active_yr"] == 2026
    # ! MATCHED ON PERSON, NOT SCHOOL -- the carry-forward only runs for a
    #   school with no current row, so any current row is at another school.
    assert "c.person_id = s.person_id" in cur.sql, cur.sql

    plain = FakeCursor()
    predict._squadsForYear(plain, ["Alpha"], "XC", 2025, exclude_terminal=True)
    assert "NOT EXISTS" not in plain.sql, plain.sql
    print("  transfers excluded only with active_year ........... OK")


class MeetCountCursor:
    """Answers roster.racesRun and nothing else: how many meets the school
    has run this season, which is what decides the carry-forward window."""
    def __init__(self, n):
        self.n = n
        self.rows = []

    def execute(self, sql, params=None):
        assert "count(DISTINCT rr.meet_id)" in " ".join(sql.split()), sql
        self.rows = [{"school": s, "n": self.n}
                     for s in ((params or {}).get("schools") or [])]

    def fetchall(self):
        return self.rows


def _spyOn(empty_for):
    """Swap _squadsForYear for a recorder. Returns (calls, restore)."""
    calls = []
    saved = predict._squadsForYear

    def spy(cur, schools, sport, year, exclude_terminal=False,
            active_year=None, gender=None, levels=None):
        # ! levels IS ACCEPTED AND NOT RECORDED. This file is about the
        #   carry-forward WINDOW; which level a squad is filtered to is
        #   test_school_level_filter's question, and recording it here would
        #   only make every expected dict in this file carry a None.
        calls.append({"year": year, "terminal": exclude_terminal,
                      "active": active_year, "gender": gender})
        return ({} if year in empty_for
                else {"Alpha": [{"person_id": 1, "rating": 120.0,
                                 "pool": "hs_m"}]})

    predict._squadsForYear = spy
    return calls, lambda: setattr(predict, "_squadsForYear", saved)


def _now():
    import datetime
    from season_year import academicYear
    return academicYear(datetime.date.today())


def test_currentSquads_asks_for_both_on_the_carry_path():
    """The carry-forward must request BOTH guards, not just the old one."""
    now = _now()
    calls, restore = _spyOn(empty_for={now})
    try:
        out = predict._currentSquads(MeetCountCursor(0), ["Alpha"], "XC", now)
    finally:
        restore()

    assert len(calls) == 2, calls
    # the season IS the current one, so the first read needs no aging
    assert calls[0] == {"year": now, "terminal": False, "active": None,
                    "gender": None}
    assert calls[1] == {"year": now - 1, "terminal": True, "active": now,
                        "gender": None}, calls
    assert out["Alpha"][0]["carried"] is True
    print(f"  carry path: {now} plain, then {now - 1} aged+active={now} . OK")


def test_the_window_is_races_run_not_an_empty_roster():
    """★ THE CARRY IS PER SCHOOL AND PER RACE (owner, 2026-09-15). A school
    that HAS raced still carries its returners, because the roster of a
    season two meets old is still mostly a question about the future."""
    now = _now()
    calls, restore = _spyOn(empty_for=set())   # the school HAS current rows
    try:
        predict._currentSquads(MeetCountCursor(2), ["Alpha"], "XC", now)
    finally:
        restore()
    assert len(calls) == 2, ("two races in, the returners still carry", calls)
    assert calls[1]["year"] == now - 1 and calls[1]["terminal"] is True

    # ⚠ AND THE THIRD RACE CLOSES IT. Past the window, the roster is whoever
    #   has actually run -- which is exactly "ran none of the first three",
    #   since anyone who ran any race has a row of their own.
    calls, restore = _spyOn(empty_for=set())
    try:
        predict._currentSquads(MeetCountCursor(3), ["Alpha"], "XC", now)
    finally:
        restore()
    assert len(calls) == 1, ("three races in, no carry", calls)
    print("  the window is the team's first three races ......... OK")


def test_a_finished_season_is_aged_out_on_the_MAIN_path():
    """★ THE BUG THE FIRST FIX MISSED (owner, 2026-08-31).

    _currentSeason returns the season the BOARDS show, which in August is
    still last season. Every school has a row for it, so `missing` is empty,
    the carry-forward never runs, and the aging-out that lived only on that
    path never executed. The squad came back as last year's roster.
    """
    now = _now()
    stale = now - 1
    calls, restore = _spyOn(empty_for=set())      # every school HAS a row
    try:
        # ! NO CURSOR NEEDED: a stale season never asks for the race count,
        #   because a finished season is not a window into anything.
        predict._currentSquads(None, ["Alpha"], "XC", stale)
    finally:
        restore()

    assert len(calls) == 1, "no carry-forward should run -- the season is over"
    assert calls[0] == {"year": stale, "terminal": True, "active": now,
                        "gender": None}, \
        f"a finished season must be aged out on the main path: {calls[0]}"
    print(f"  finished season {stale}: aged out + active={now} on the "
          f"main path . OK")


def test_the_current_season_is_not_aged_out():
    """The live season must NOT be aged: those athletes are racing now."""
    now = _now()
    calls, restore = _spyOn(empty_for=set())
    try:
        predict._currentSquads(MeetCountCursor(9), ["Alpha"], "XC", now)
    finally:
        restore()
    assert calls[0] == {"year": now, "terminal": False, "active": None,
                        "gender": None}, calls
    print(f"  the live season {now} is left alone ................ OK")


if __name__ == "__main__":
    for fn in [test_terminal_grades_are_keys_not_spellings,
               test_aging_out_compares_the_grade_key,
               test_no_grade_clause_when_not_aging_out,
               test_transfers_are_excluded_only_when_asked,
               test_currentSquads_asks_for_both_on_the_carry_path,
               test_the_window_is_races_run_not_an_empty_roster,
               test_a_finished_season_is_aged_out_on_the_MAIN_path,
               test_the_current_season_is_not_aged_out]:
        fn()
    print("\nall carry-forward tests passed")
