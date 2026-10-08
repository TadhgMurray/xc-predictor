# Project: xc-predictor / tests
# File:    test_grade_sanity_national_team.py
# Purpose: grade_sanity reads no grade off a national team's row.
#
# ★ WHY (owner, 2026-10-08, John Rivera, /athlete/12652858: Lakewood Ranch
#   HS, Ole Miss FR-1 2017 to 2021, a professional since). His 2025-03-21
#   World Indoors rows, school 'Puerto Rico', carry grade '12' and his
#   2023-08-27 Worlds row '10' -- the federation's column. The two '12's
#   corroborated academic 2024 as a twelfth grader; rule 3b's progression
#   arithmetic turned that into 'graduated', right by accident. The page
#   stopped quoting these rows in a860c29; the verdicts did not.
#
# ! THE ROW STAYS, ITS GRADE GOES. A national-team race still counts as a
#   race the athlete ran, and still sits in the fields rule 4 reads -- as an
#   ungraded entrant, so a World Indoors field reads 'pro' by rule 4's own
#   no-grade arm instead of 'hs' off everyone's junk '12'.
#
# Runs the whole of resolve() on a scratch Postgres, like test_thin_field_sql:
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
import os
import sys

import pytest

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))

import grade_sanity as GS                                        # noqa: E402
from pool_resolve import isNationalTeam                          # noqa: E402

DB = "xcp_nt_grade_test"
RIVERA = 12652858
LONER = 600              # one ungraded race, in a college field NT rows crowd

_rows = []


def _row(pid, date, grade, meet, school, t, event=1):
    _rows.append((len(_rows) + 1, pid, date, grade, meet, 1, "anet", t, school, event, 0))


def _fixture():
    _rows.clear()
    # high school, corroborated 9..12 (implied class year 2016)
    for i, ay in enumerate(range(2013, 2017)):
        for k, mm in enumerate(("09", "10")):
            _row(RIVERA, f"{ay}-{mm}-10", str(9 + i), 100 + 2 * i + k,
                 "Lakewood Ranch", 250 + k)
    # Ole Miss, the tfrrs eligibility form: college began 2017
    for i, (ay, g) in enumerate(((2017, "FR-1"), (2018, "SO-2"), (2019, "JR-3"),
                                 (2020, "SR-4"), (2021, "Sr"))):
        for k, mm in enumerate(("09", "10")):
            _row(RIVERA, f"{ay}-{mm}-12", g, 200 + 2 * i + k, "Ole Miss", 230 + k)
    # academic 2023: the 2023 Worlds ('10') and an unattached indoor 'Senior'
    _row(RIVERA, "2023-08-27", "10", 900, "Puerto Rico", 105.1)
    for pid, s, g in ((901, "Spain", "10"), (902, "Kenya National Team", "12"),
                      (903, "Great Britain & N.I.", "11"), (904, "USA", None)):
        _row(pid, "2023-08-27", g, 900, s, 105.5)
    _row(RIVERA, "2023-12-02", "Senior", 910, "Unattached", 108.0)
    for pid, s, g in ((911, "Ole Miss", "JR"), (912, "Arkansas", "SO"),
                      (913, "LSU", "SR"), (914, "Alabama", "FR")):
        _row(pid, "2023-12-02", g, 910, s, 109.0)
    # academic 2024: World Indoors, heat and final, '12' on every country
    for ev, t in ((1, 106.2), (2, 105.8)):
        _row(RIVERA, "2025-03-21", "12", 920, "Puerto Rico", t, event=ev)
        for pid, s in ((921, "Spain (ESP)"), (922, "Kenya"), (923, "Ethiopia")):
            _row(pid, "2025-03-21", "12", 920, s, t + 0.3, event=ev)
        _row(924, "2025-03-21", None, 920, "USA", t + 0.4, event=ev)
    # academic 2026: Brooks Beasts and Puerto Rico, both '-', college fields
    _row(RIVERA, "2026-08-15", "-", 930, "Brooks Beasts", 104.0)
    _row(RIVERA, "2026-08-19", "-", 940, "Puerto Rico", 104.5)
    for meet in (930, 940):
        for pid, s, g in ((931, "Georgia", "SR"), (932, "Oregon", "JR"),
                          (933, "Stanford", "SO"), (934, "BYU", "SR")):
            _row(pid, f"2026-08-{15 if meet == 930 else 19}", g, meet, s, 105.0)
    # five national-team '12's outnumber the four collegians in meet 940
    for pid, s in ((941, "Spain"), (942, "Kenya"), (943, "Ethiopia"),
                   (944, "Ireland"), (945, "Japan")):
        _row(pid, "2026-08-19", "12", 940, s, 105.2)
    _row(LONER, "2026-08-19", "-", 940, "Unattached", 106.0)
    return _rows


@pytest.fixture
def cur():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run resolve()")
    import psycopg2
    from psycopg2.extras import execute_values
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB}")
        c.execute(f"CREATE DATABASE {DB}")
    test_dsn = " ".join(p for p in dsn.split() if not p.startswith("dbname=")) + f" dbname={DB}"
    cx = psycopg2.connect(test_dsn)
    c = cx.cursor()
    cols = ("result_id bigint, person_id bigint, date text, grade text, meet_id bigint, "
            "div_id bigint, source text, time_seconds float8, school text")
    c.execute(f"CREATE TABLE results ({cols})")
    c.execute(f"CREATE TABLE results_tf ({cols}, event_id bigint, is_relay int)")
    execute_values(c, "INSERT INTO results_tf VALUES %s", _fixture())
    yield c
    cx.close()
    with admin.cursor() as c2:
        c2.execute(f"DROP DATABASE IF EXISTS {DB}")
    admin.close()


def _verdicts(cur):
    return {k: v for k, v in GS.resolve(cur).items() if k[0] in (RIVERA, LONER)}


def test_ntschool_is_pool_resolves_classification(cur):
    GS._nationalTeamSchools(cur)
    cur.execute(f"SELECT school FROM {GS._NT_TABLE}")
    got = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT school FROM results_tf WHERE grade IS NOT NULL")
    want = {r[0] for r in cur.fetchall() if isNationalTeam(r[0])}
    assert got == want
    assert {"Puerto Rico", "Spain (ESP)", "Kenya National Team"} <= got
    # a US school named for a country, the US team, a club: not national teams
    assert not got & {"Georgia", "USA", "Brooks Beasts", "Unattached", "Ole Miss"}


def test_the_control_reproduces_the_fault(cur, monkeypatch):
    # ⚠ WITHOUT THE TABLE, the fixture is the 2026-10-08 grade_fix: academic
    #   2024 corroborated '12' and 3b's arithmetic made it 'graduated'; the
    #   loner's field read 'hs' off the national teams' '12's.
    def _empty(c):
        c.execute(f"DROP TABLE IF EXISTS {GS._NT_TABLE}; "
                  f"CREATE UNLOGGED TABLE {GS._NT_TABLE} (school text PRIMARY KEY)")
    monkeypatch.setattr(GS, "_nationalTeamSchools", _empty)
    v = _verdicts(cur)
    assert v[(RIVERA, 2024)]["method"] == "graduated"
    assert (v[(RIVERA, 2023)]["method"], v[(RIVERA, 2023)]["level"]) == ("field", "college")
    assert v[(LONER, 2026)]["level"] == "hs"


def test_a_national_team_row_is_not_grade_evidence(cur):
    v = _verdicts(cur)
    # ★ 2024: nothing of his own left to corroborate, and World Indoors has
    #   no graded entrant -- rule 4's no-grade arm, not 3b's accident
    assert v[(RIVERA, 2024)] == {"grade": None, "level": "pro",
                                 "method": "no_grades", "trust": "high"}
    # ★ 2023: the Worlds '10' no longer folds his 'Senior' away (rule 2b ties
    #   go to the number), so the season reads 'SR' -- a third season of it
    #   after 2020's SR-4 and 2021's Sr, which rule 5 calls a grade that
    #   stopped advancing. It was a field 'college' off the '10'.
    assert v[(RIVERA, 2023)] == {"grade": None, "level": "pro",
                                 "method": "stale_grade", "trust": "high"}
    # ! 5e unchanged: 2026 is a college field nine seasons after 2017
    assert v[(RIVERA, 2026)]["method"] == "post_collegiate"
    # the progression still reads high school off his real high school grades
    assert [v[(RIVERA, ay)]["grade"] for ay in range(2013, 2017)] == ["9", "10", "11", "12"]
    # ! the countries do not out-vote the collegians in the loner's field
    assert v[(LONER, 2026)] == {"grade": None, "level": "college",
                                "method": "field", "trust": "low"}


def test_the_row_stays_and_its_grade_goes(cur):
    GS.resolve(cur)
    cur.execute("""SELECT count(*), count(grade), count(grade_folded),
                          count(raw_grade), count(definite)
                   FROM allraces a JOIN ntschool nt ON true
                   WHERE a.person_id = %s
                     AND a.acad IN (2023, 2024) AND a.raw_grade IS DISTINCT FROM 'Senior'
                     AND EXISTS (SELECT 1 FROM results_tf r
                                 WHERE r.person_id = a.person_id
                                   AND r.school = nt.school
                                   AND r.meet_id = a.meet_id)""", (RIVERA,))
    n, g, gf, raw, d = cur.fetchone()
    assert n == 3 and (g, gf, raw, d) == (0, 0, 0, 0)


def test_rule7_stages_no_national_team_row():
    # rule 7 reads results / results_tf directly, not allraces
    import inspect
    src = inspect.getsource(GS.eliteFieldSeasons)
    assert "{_NT_TABLE} nt" in src and "nt.school = r.school" in src
