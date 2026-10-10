"""
A class-year grade ("2026") graduates and reads as a grade (sweep
2026-10-10, A12): rankings.gradeKeySql keys it '12'/'sr' in its senior
season and after (so roster.TERMINAL_KEYS drops it), a lower grade before;
grade_label.advanceGrade reads it against the season instead of returning
None forever, and predict._advanced passes the season through.
No database: the SQL is parsed (pglast), and evaluated with duckdb only if
that happens to be installed.
"""
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))

import pytest                                                   # noqa: E402

from grade_label import (advanceGrade, classYearGrade,          # noqa: E402
                         isClassYear, gradeLabel)
import rankings                                                 # noqa: E402
import roster                                                   # noqa: E402


# ------------------------------------------------------------ grade_label
@pytest.mark.parametrize("grade,season,pool,want", [
    ("2026", 2025, "hs_m", "12"),        # 2025-26: the class of 2026 are seniors
    ("2027", 2025, "hs_f", "11"),
    ("2029", 2025, "hs_m", "9"),
    ("2031", 2025, "ms_m", "7"),
    ("2026", 2026, "hs_m", None),        # the season opening in G: graduated
    ("2020", 2025, "hs_m", None),
    ("2026", 2025, "college_m", "SR-4"),
    ("2027", 2025, "college_f", "JR-3"),
    ("2029", 2025, "college_m", "FR-1"),
    ("2030", 2025, "college_m", None),   # no college class four years out
    ("2040", 2025, "hs_m", None),        # below grade 1: not a grade
    ("12", 2025, "hs_m", None),          # not a class year
])
def test_classYearGrade(grade, season, pool, want):
    assert classYearGrade(grade, season, pool) == want


def test_isClassYear_is_anchored():
    assert isClassYear("2026") and isClassYear(" 1999 ")
    for g in ("12", "202", "20261", "SR-4", "2026th", "3026", None, ""):
        assert not isClassYear(g)


def test_advanceGrade_reads_class_year_against_season():
    assert advanceGrade("2026", 1, "hs_m", season=2025) == "12"
    assert advanceGrade("2026", 1, "hs_m", season=2026) is None
    # no season: unreadable, as before
    assert advanceGrade("2026", 1, "hs_m") is None
    # every other spelling is untouched by the season
    assert advanceGrade("10", 1, "hs_m", season=2030) == "11"
    assert advanceGrade("JR-3", 1, "college_m", season=2030) == "SR-4"
    assert advanceGrade("12", 1, "hs_m", season=2025) is None


def test_gradeLabel_unchanged():
    assert gradeLabel("2026", "hs_m") == "Class of 2026"


def test_predict_advanced_passes_season():
    import predict
    assert predict._advanced("2028", 1, "hs_m", season=2026) == "11"
    # graduated: the stored class year stays (prints "Class of 2026")
    assert predict._advanced("2026", 1, "hs_m", season=2026) == "2026"
    assert predict._advanced("2026", 1, "hs_m") == "2026"


# ------------------------------------------------------------ gradeKeySql
def test_gradeKeySql_parses_and_reads_the_rows_year():
    pglast = pytest.importorskip("pglast")
    sql = rankings.gradeKeySql("s")
    assert "s.year + 1" in sql and "(19|20)[0-9][0-9]" in sql
    pglast.parse_sql(f"SELECT {sql.replace('%%', '%')} FROM athlete_season s")
    clause = roster.graduatedClause("s")
    pglast.parse_sql("SELECT 1 FROM athlete_season s WHERE TRUE "
                     + clause.replace("%(term_keys)s", "ARRAY['12','sr']")
                             .replace("%%", "%"))


def test_gradeKeySql_class_year_branch_comes_first():
    sql = rankings.gradeKeySql()
    # the digit branch would key "2026" as 202; the class-year branch must
    # be reached before it
    assert sql.index("(19|20)") < sql.index("~ '^[0-9]'")


def test_gradeKeySql_evaluates():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    con.execute("CREATE TABLE s(grade TEXT, pool TEXT, year INT)")
    rows = [("2026", "hs_m", 2025, "12"), ("2026", "hs_m", 2027, "12"),
            ("2027", "hs_m", 2025, "11"), ("2029", "hs_f", 2025, "9"),
            ("2026", "college_m", 2025, "sr"), ("2028", "college_m", 2025, "so"),
            ("2029", "college_m", 2025, "fr"),
            ("2035", "college_m", 2025, "2035"),
            ("12", "hs_m", 2025, "12"), ("Sr", "hs_m", 2025, "12"),
            ("SR-4", "college_m", 2025, "sr")]
    con.executemany("INSERT INTO s VALUES (?,?,?)", [r[:3] for r in rows])
    expr = rankings.gradeKeySql("s").replace("%%", "%")
    got = con.execute(f"SELECT {expr} FROM s").fetchall()
    assert [g[0] for g in got] == [r[3] for r in rows]
    assert set(roster.TERMINAL_KEYS) == {"12", "sr"}
