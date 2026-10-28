# Project: xc-predictor / tests
# File:    test_diag_pooling.py
# Purpose: scripts/diag_pooling.py finds each kind of mis-pooled row it
#          names, on a scratch Postgres seeded with one athlete per kind.
#
#   XCP_TWIN_TEST_DSN=postgresql://... python -m pytest -q tests/test_diag_pooling.py
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402,F401
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TABLES = ("results", "results_tf", "grade_fix", "college_first_season",
           "pro_athlete_season", "pro_ability_season", "team_pool",
           "race_top_level", "race_level", "person_gender", "ranking_results",
           "tfrrs_hs_meets")


def _seed(cur):
    from level_graph import _raceKeyExpr
    cur.execute("DROP TABLE IF EXISTS " + ", ".join(_TABLES))
    cur.execute("""
        CREATE TABLE results (result_id bigint, person_id bigint, date text,
            grade text, source text, school text, team_id bigint,
            team_slug text, athlete_name text, meet_id bigint, div_id bigint,
            rating_pool text, speed_rating real);
        CREATE TABLE results_tf (result_id bigint, person_id bigint, date text,
            grade text, source text, school text, team_id bigint,
            team_slug text, athlete_name text, meet_id bigint, div_id bigint,
            event_id bigint, rating_pool text, speed_rating real);
        CREATE TABLE grade_fix (person_id bigint, season int, grade text,
            level text, method text, trust text);
        CREATE TABLE college_first_season (person_id bigint, first_date date);
        CREATE TABLE pro_athlete_season (person_id bigint, season int,
            pro_races int, round int);
        CREATE TABLE pro_ability_season (person_id bigint, season int,
            best_nt real, gender text);
        CREATE TABLE team_pool (team_id int, kind text, reason text,
            n_athletes int, n_rows bigint, level text, n_pros int,
            built date DEFAULT current_date);
        CREATE TABLE race_top_level (race bigint, top_level text,
            n_levels int, known_frac real, n_teams int);
        CREATE TABLE race_level (race bigint, level text, known_frac real,
            n_teams int);
        CREATE TABLE person_gender (person_id bigint, gender text, n_m int,
            n_f int, split boolean);
        CREATE TABLE ranking_results (sport text, result_id bigint,
            person_id bigint, pool text);
    """)
    xc, tf = [], []
    rid = [0]

    def x(pid, date, pool, grade=None, school="Some HS", team=10, slug=None,
          meet=None, source="anet"):
        rid[0] += 1
        xc.append((rid[0], pid, date, grade, source, school, team, slug,
                   f"P{pid}", meet or rid[0], 1, pool + "|XC", 100.0))
        return rid[0]

    def t(pid, date, pool, grade=None, school="Some HS", team=10):
        rid[0] += 1
        tf.append((rid[0], pid, date, grade, "anet", school, team, None,
                   f"P{pid}", rid[0], 1, 1, pool + "|TF", 100.0))
        return rid[0]

    # 1 flip: three hs rows and one college row in the 2024 XC season
    for d in ("2024-09-01", "2024-09-08", "2024-09-15"):
        x(1, d, "hs_m", "11")
    x(1, "2024-09-22", "college_m", "11")
    # 2 ms_in_hs (and ranked)
    r2 = t(2, "2025-04-01", "hs_m", "7")
    # 3 hs_in_ms
    t(3, "2025-04-01", "ms_f", "10")
    # 4 hs_in_college, no college season ever
    x(4, "2024-10-01", "college_m", "11")
    # 5 college_in_hs: a tfrrs college slug
    x(5, "2024-10-01", "hs_m", None, slug="CT_college_m_Foo", source="tfrrs")
    # 6 pro_in_school
    x(6, "2024-10-01", "hs_m", "12")
    # 7 pro_pool_slow
    x(7, "2024-10-01", "pro_m", None, team=0, school="Unattached")
    # 8 postcollege
    x(8, "2024-10-01", "college_m", None, school="Some TC", team=300)
    # 9 gender
    x(9, "2024-10-01", "hs_m", "10")
    # 10 unattached at an hs-ceiling race, rated college
    r10 = x(10, "2024-10-01", "college_m", None, team=0, school="Unattached",
            meet=9010)
    # 11 club: a team_pool pro team rated college
    x(11, "2024-10-01", "college_f", None, school="Elite TC", team=500)
    # 12 race_level: an ms row in an hs race
    x(12, "2024-10-01", "ms_m", "8", meet=9012)
    # 13 merge_wide: ms and college in one season
    x(13, "2024-10-01", "ms_m", "7")
    x(13, "2024-10-08", "college_m", "7")
    # 14 grade_back: grade 10 then grade 9
    x(14, "2023-10-01", "hs_m", "10")
    x(14, "2024-10-01", "hs_m", "9")
    # 15 grade_typo: one season reading 9 and 10
    x(15, "2024-09-01", "hs_m", "9")
    x(15, "2024-09-08", "hs_m", "10")
    # 16 tfrrs_hs: a Florida senior at a TFRRS state-site meet, rated college
    x(16, "2024-10-01", "college_m", "SR-4", school="Some FL HS", team=None,
      meet=7716, source="tfrrs")
    cur.execute("""CREATE TABLE tfrrs_hs_meets (meet_id bigint, sport text,
                       host text, date text, name text)""")
    cur.execute("INSERT INTO tfrrs_hs_meets VALUES (7716, 'XC', "
                "'fl.tfrrs.org', '2024-10-01', 'FHSAA')")
    cur.executemany("INSERT INTO results VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    "%s,%s,%s,%s)", xc)
    cur.executemany("INSERT INTO results_tf VALUES (%s,%s,%s,%s,%s,%s,%s,%s,"
                    "%s,%s,%s,%s,%s,%s)", tf)
    cur.execute("INSERT INTO ranking_results VALUES ('TF', %s, 2, 'hs_m')", (r2,))
    cur.execute("INSERT INTO pro_athlete_season VALUES (6, 2024, 3, 0)")
    cur.execute("INSERT INTO college_first_season VALUES (8, '2015-09-01')")
    cur.execute("INSERT INTO person_gender VALUES (9, 'F', 0, 5, false)")
    cur.execute("INSERT INTO team_pool VALUES (500, 'pro', 'n<15', 3, 9, NULL, 0)")
    cur.execute(f"""INSERT INTO race_top_level
                    SELECT {_raceKeyExpr('r')}, 'hs', 2, 1.0, 8
                    FROM results r WHERE r.result_id = %s""", (r10,))
    cur.execute(f"""INSERT INTO race_level
                    SELECT {_raceKeyExpr('r')}, 'hs', 1.0, 8
                    FROM results r WHERE r.person_id = 12""")


@pytest.fixture
def dsn():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        _seed(cur)
    yield dsn
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS " + ", ".join(_TABLES))
    conn.close()


def test_each_kind_is_found(dsn, capsys):
    import diag_pooling as D
    d = D.main(["--dsn", dsn, "--sample", "1", "--since", "2000-01-01",
                "--fill-check", "0", "--show", "3"])
    got = {k: (rows, ranked, people) for k, _t, rows, ranked, people in d.found}
    assert got["flip"][0] == 2                 # person 1's college row, 13's minority
    assert got["ms_in_hs"] == (1, 1, 1)        # and it is ranked
    assert got["hs_in_ms"][0] == 1
    assert got["hs_in_college"][0] == 2        # person 4, and person 1's
                                               # college row (grade 11 too)
    assert got["college_in_hs"][0] == 1
    assert got["pro_in_school"][0] == 1
    assert got["pro_pool_slow"][0] == 1
    assert got["postcollege"][0] == 1
    assert got["gender"][0] == 1
    assert got["unattached"][0] == 1
    assert got["club"][0] == 1
    assert got["race_level"][0] == 1
    assert got["merge_wide"][0] == 2
    assert got["grade_back"][0] == 1
    assert got["grade_typo"][0] == 2           # both rows of the season
    assert got["tfrrs_hs"][0] == 1
    out = capsys.readouterr().out
    assert "RANKED BY RATINGS MOVED" in out
    assert "nothing written" in out


def test_the_sample_scales_the_counts(dsn):
    import diag_pooling as D
    d = D.main(["--dsn", dsn, "--sample", "2", "--since", "2000-01-01",
                "--fill-check", "0", "--show", "0", "--only", "ms_in_hs"])
    rows = {k: r for k, _t, r, _rk, _p in d.found}
    # person 2 is even, so in the 1-in-2 sample, and counts twice
    assert rows["ms_in_hs"] == 2


# ---- the fill check: the board query's own joins, on a minimal schema ---- #

def _seedBoard(cur):
    """The extra tables and columns build_ranking_results' query joins, so
    the fill check runs the real query (not a copy of it)."""
    cur.execute("""
        DROP TABLE IF EXISTS athletes, meets, meets_tf, meets_tfrrs,
                             dist_override, result_twin, wheelchair_person,
                             athlete_season_level;
        ALTER TABLE results ADD COLUMN athlete_id bigint,
            ADD COLUMN canon_meet_id bigint, ADD COLUMN time_seconds real,
            ADD COLUMN normalized_time real;
        ALTER TABLE results_tf ADD COLUMN athlete_id bigint,
            ADD COLUMN canon_meet_id bigint, ADD COLUMN time_seconds real,
            ADD COLUMN normalized_time real, ADD COLUMN event_short text,
            ADD COLUMN mark text, ADD COLUMN is_field int,
            ADD COLUMN is_relay int;
        UPDATE results SET athlete_id = person_id, time_seconds = 1000,
                           normalized_time = 1000;
        UPDATE results_tf SET athlete_id = person_id, time_seconds = 600,
                              normalized_time = 1000, event_short = '3200m';
        CREATE TABLE athletes (athlete_id bigint, person_id bigint,
                               gender text, first_name text, last_name text);
        INSERT INTO athletes SELECT DISTINCT person_id, person_id, 'M'
                             FROM results;
        INSERT INTO athletes SELECT DISTINCT person_id, person_id, 'M'
                             FROM results_tf;
        CREATE TABLE meets (meet_id bigint, div_id bigint, source text,
                            distance real, state text, division text);
        INSERT INTO meets SELECT DISTINCT meet_id, div_id, source, 5000,
                                          'CA', 'Varsity' FROM results;
        CREATE TABLE meets_tf (meet_id bigint, div_id bigint, event_id bigint,
                               source text, state text);
        CREATE TABLE meets_tfrrs (meet_id bigint, source text, sport text,
                                  division_distances jsonb);
        CREATE TABLE dist_override (meet_id bigint, div_id bigint,
                                    distance real);
        CREATE TABLE result_twin (sport text, result_id bigint);
        CREATE TABLE wheelchair_person (person_id bigint);
        CREATE TABLE athlete_season_level (person_id bigint, ay int,
            sport text, level text);
    """)


def test_the_fill_check_runs_the_board_query(dsn, capsys):
    import psycopg2
    import diag_pooling as D
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        _seedBoard(cur)
        # one athlete the ENGINE pooled pro by team_id 0 that fill_ratings,
        # which never sees the team, pools by the grade
        cur.execute("""UPDATE results SET grade = '11', result_id = 97 * 1000
                       WHERE person_id = 7""")
    d = D.main(["--dsn", dsn, "--sample", "1", "--since", "2000-01-01",
                "--fill-check", "1000", "--show", "0", "--only", "fill"])
    out = capsys.readouterr().out
    assert "engine pro      fill hs" in out, out
    assert d.fill_share > 0
    # the fill as it decides now reads team_id 0 and agrees with the engine
    assert "with the engine's team facts" in out, out
    assert "fill now hs" not in out, out
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS athletes, meets, meets_tf, meets_tfrrs,"
                    " dist_override, result_twin, wheelchair_person,"
                    " athlete_season_level")
    conn.close()


def test_one_meet_by_division(dsn, capsys):
    """--meet alone prints that meet's pools and the rows whose own grade
    names another level, without building the sample."""
    import psycopg2
    import diag_pooling as D
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO results (result_id, person_id, date, grade,
                           source, school, team_id, athlete_name, meet_id,
                           div_id, rating_pool, speed_rating)
                       VALUES (9999, 99, '2024-10-01', '10', 'anet', 'X HS',
                               10, 'P99', 9012, 1, 'ms_m|XC', 120)""")
    conn.close()
    d = D.main(["--dsn", dsn, "--meet", "9012"])
    out = capsys.readouterr().out
    assert "XC div 1 (anet)  ms_m" in out
    assert "99  P99  1  10  X HS  10  ms_m  120.0" in out
    assert d.found == []                     # no sample was built
