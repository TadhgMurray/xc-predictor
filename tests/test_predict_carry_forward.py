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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))
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


def test_terminal_grades_are_normalised_spellings():
    # Only SPELLINGS should need listing now, never capitalisations -- the
    # query upper-cases and trims before comparing.
    assert predict._TERMINAL_GRADES == ["12", "12TH", "SR", "SENIOR"], \
        predict._TERMINAL_GRADES
    for g in predict._TERMINAL_GRADES:
        assert g == g.upper().strip(), f"{g!r} is not already normalised"
    print(f"  terminal grades {predict._TERMINAL_GRADES} ... OK")


def test_aging_out_compares_normalised():
    cur = FakeCursor()
    predict._squadsForYear(cur, ["Alpha"], "XC", 2025, exclude_terminal=True)
    assert "UPPER(BTRIM(COALESCE(s.grade, '')))" in cur.sql, cur.sql
    assert "<> ALL(%(term)s)" in cur.sql, cur.sql
    assert cur.params["term"] == predict._TERMINAL_GRADES
    print("  aging-out upper-cases and trims before comparing ... OK")


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


def test_currentSquads_asks_for_both_on_the_carry_path():
    """The carry-forward must request BOTH guards, not just the old one."""
    calls = []
    saved = predict._squadsForYear

    def spy(cur, schools, sport, year, exclude_terminal=False,
            active_year=None):
        calls.append({"year": year, "terminal": exclude_terminal,
                      "active": active_year})
        return {} if year == 2026 else {"Alpha": [{"person_id": 1}]}

    predict._squadsForYear = spy
    try:
        out = predict._currentSquads(None, ["Alpha"], "XC", 2026)
    finally:
        predict._squadsForYear = saved

    assert len(calls) == 2, calls
    assert calls[0] == {"year": 2026, "terminal": False, "active": None}
    assert calls[1] == {"year": 2025, "terminal": True, "active": 2026}, calls
    assert out["Alpha"][0]["carried"] is True
    print(f"  carry path asks year=2025 terminal=True active=2026 . OK")


if __name__ == "__main__":
    for fn in [test_terminal_grades_are_normalised_spellings,
               test_aging_out_compares_normalised,
               test_no_grade_clause_when_not_aging_out,
               test_transfers_are_excluded_only_when_asked,
               test_currentSquads_asks_for_both_on_the_carry_path]:
        fn()
    print("\nall carry-forward tests passed")
