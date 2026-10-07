"""A high school race inside a college athlete's season is somebody else's
race (the NESCAC review, 2026-09-29: Jared Rife, Tyler Johnson, Nick Walker
and Max Bennett each carrying the 2025-10-26 Middlesex League Championship).

    python -m pytest -q tests/test_level_conflict.py

The decision is pure and tested here on small row lists; the SQL that feeds
it is tested for shape here and on a real Postgres through the twin_flag
fixture (tests/test_twin_flag.py, XCP_TWIN_TEST_DSN) and below.
"""
import io
import os
import re
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import level_conflict as LC                                     # noqa: E402

C, H = LC.COLLEGE, LC.HS


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


def season(pid, ay, *rows):
    """rows as (result_id, day, level) -> the decide() input shape"""
    return [(rid, pid, ay, day, lv) for rid, day, lv in rows]


# ---- the decision ------------------------------------------------------ #

def test_the_reviews_shape_flags_the_one_high_school_race():
    """Nick Walker (Bates): a college autumn and one Middlesex League row on
    2025-10-26 -> only that row goes."""
    rows = season(1, 2025,
                  (11, "2025-09-06", C), (12, "2025-09-20", C),
                  (13, "2025-10-18", C), (14, "2025-11-01", C),
                  (15, "2025-10-26", H))
    assert LC.decide(rows) == {15: (1, 2025, H)}


def test_the_reverse_flags_the_college_row_on_a_high_schooler():
    rows = season(2, 2025,
                  (21, "2025-09-13", H), (22, "2025-10-26", H),
                  (23, "2025-11-08", H), (24, "2025-09-20", C))
    assert set(LC.decide(rows)) == {24}


def test_a_tie_is_no_evidence_so_nothing_goes_and_it_is_reported():
    """Person 6339154 (the first server run, 2026-09-29): one Kenston HS race,
    one RPI race. "A tie flags both" deleted a real athlete's season."""
    rows = season(3, 2025, (31, "2025-09-06", C), (32, "2025-10-26", H))
    assert LC.decide(rows) == {}
    flags, ties = LC.judge(rows)
    assert flags == {} and ties == [(3, 2025)]


def test_a_race_in_both_feeds_votes_once():
    """DAYS, not rows: a college meet stored twice must not outvote two
    high school days (grade_sanity's Diego Eseverri lesson)."""
    rows = season(4, 2025,
                  (41, "2025-09-06", C), (42, "2025-09-06", C),
                  (43, "2025-09-20", H), (44, "2025-10-04", H))
    assert set(LC.decide(rows)) == {41, 42}, "one college day loses to two"


def test_high_school_then_college_is_a_graduation_not_a_conflict():
    """A December graduate racing indoor for a college in January."""
    rows = season(5, 2025,
                  (51, "2025-12-13", H), (52, "2025-12-20", H),
                  (53, "2026-01-17", C), (54, "2026-02-14", C))
    assert LC.decide(rows) == {}


def test_but_never_back_and_not_on_the_same_day():
    back = season(6, 2025, (61, "2026-01-17", C), (62, "2026-04-18", H),
                  (63, "2026-02-14", C))
    assert set(LC.decide(back)) == {62}
    same_day = season(7, 2025, (71, "2026-01-17", H), (72, "2026-01-17", C),
                      (73, "2026-02-01", C))
    assert set(LC.decide(same_day)) == {71}


def test_one_level_per_season_is_left_alone():
    rows = (season(8, 2024, (81, "2024-10-19", H), (82, "2024-11-02", H))
            + season(8, 2025, (83, "2025-10-18", C)))
    assert LC.decide(rows) == {}, "a senior autumn, then a college autumn"


def test_seasons_and_persons_are_judged_separately():
    rows = (season(9, 2025, (91, "2025-09-06", C), (92, "2025-09-20", C),
                   (93, "2025-10-26", H))
            + season(9, 2023, (94, "2023-10-20", H))          # his own HS year
            + season(10, 2025, (101, "2025-10-26", H)))       # another person
    assert set(LC.decide(rows)) == {93}


def test_rows_without_a_level_or_a_season_do_not_vote():
    rows = season(11, 2025, (111, "2025-09-06", C), (112, "2025-10-26", H),
                  (113, "2025-10-01", None), (114, "2025-10-02", None))
    rows.append((115, 11, None, "2025-10-03", H))
    # 115 has no season, so it does not turn the tie into a high school win
    assert LC.judge(rows) == ({}, [(11, 2025)])


def test_guests_counts_distinct_days():
    side = {C: [(1, "2025-09-06"), (2, "2025-09-06"), (3, "2025-09-20")],
            H: [(4, "2025-10-26")]}
    assert LC.guests(side) == (H,)
    assert LC.guests({C: [(1, "d1")], H: [(2, "d2")]}) == (), "a tie flags nothing"


# ---- the SQL that says what a row is ------------------------------------ #

def test_college_needs_tfrrs_and_a_college_signal_not_the_grade_form_alone():
    """The first server run (2026-09-29): Winnisquam Regional HS 'SR-4',
    Westminster Academy 'SO-2', The Benjamin School 'JR-3' -- tfrrs-hosted
    high school meets print the eligibility form too."""
    sql = LC.collegeSql("r")
    assert "r.source = 'tfrrs'" in sql
    slug = "COALESCE(split_part(lower(r.team_slug), '_', 2), '')"
    assert f"{slug} = 'college' OR" in sql, "the slug alone is enough"
    # the form counts only on a slugless row AT A KNOWN COLLEGE
    form = sql.split(" OR ", 1)[1]
    assert f"{slug} = ''" in form
    assert "'^(FR|SO|JR|SR)-[1-6]$'" in form, "redshirts and fifth years too"
    assert f"r.school IN (SELECT school FROM {LC.COLLEGE_SCHOOLS})" in form
    assert form.count(" AND ") >= 2, "slugless AND form AND school, all three"
    # the bare word is ambiguous (9-12 or 13-16) and must NOT count
    rx = re.compile(r"^(FR|SO|JR|SR)-[1-6]$")
    # SO-3 / JR-4 a redshirt, SR-5 / SR-6 a fifth or sixth year (Dylan
    # Schubert, Furman, SR-5)
    for g in ("FR-1", "SO-2", "JR-3", "SR-4", "SO-3", "JR-4", "SR-5", "SR-6"):
        assert rx.match(g)
    for g in ("SR", "Fr", "12", "SR-7", "FR-0", "13-14"):
        assert not rx.match(g), g


def test_a_school_is_a_college_by_a_witness_and_never_with_a_high_school_slug():
    known = {"Middlebury", "Hamilton", "Trinity (Conn.)"}.__contains__
    evidence = [
        ("Middlebury", False, False),       # the directory knows it
        ("Conn College", True, False),      # tfrrs put a college slug on it
        ("Winnisquam", False, False),       # nobody says college
        ("Westminster Academy", False, False),
        ("The Benjamin School", False, False),
        ("Hamilton", False, True),          # a college's name, a high school's slug
        ("Trinity (Conn.)", True, True),    # any high school slug vetoes
        (None, True, False), ("", True, False),
    ]
    assert LC.collegeSchools(evidence, known) == {"Middlebury", "Conn College"}


def test_the_directory_does_not_know_the_servers_high_schools():
    """The directory's own lookup, on a directory of the colleges those names
    come closest to: exact, then the feed's short form, then every token."""
    import build_college_directory as B
    entries = {B.normName(n): [(st, n)] for n, st in (
        ("Westminster College", "PA"), ("Westminster College", "MO"),
        ("Benjamin Franklin Institute of Technology", "MA"),
        ("Bates College", "ME"), ("Rensselaer Polytechnic Institute", "NY"),
        ("Middlebury College", "VT"))}
    for hs in ("Winnisquam", "Westminster Academy", "The Benjamin School", "Kenston"):
        assert B.lookup(entries, hs) is None, hs
    for college in ("Bates", "Middlebury", "RPI"):
        assert B.lookup(entries, college) is not None, college


def test_high_school_needs_a_numeric_grade_and_a_team():
    sql = LC.hsSql("h")
    assert "btrim(h.grade) ~ '^(9|10|11|12)$'" in sql
    assert "COALESCE(h.team_id, -1) <> 0" in sql, "anet team 0 is no team"
    assert "NOT IN ('college', 'club')" in sql
    rx = re.compile(r"^(9|10|11|12)$")
    for g in ("9", "10", "11", "12"):
        assert rx.match(g)
    for g in ("8", "13", "SR", "11-12", "12th", "-"):
        assert not rx.match(g), g


def test_level_asks_college_first_and_names_both():
    sql = LC.levelSql("r")
    assert sql.index("'college'") < sql.index("'hs'")
    assert sql.startswith("(CASE WHEN") and sql.endswith("END)")


def test_the_season_is_the_one_clock_and_skips_non_dates():
    from season_year import seasonYearSqlInt
    sql = LC.acadSql("r")
    assert seasonYearSqlInt(None, "r.date") in sql
    assert "r.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'" in sql, "'TBA' is no season"


def test_the_stages_narrow_before_they_read_rows():
    stages = LC.stagedSql("results")
    labels = [label for label, _ in stages]
    assert labels == ["college cells", "cells holding high school rows too",
                      "their rows"]
    col, cell, rows = (sql for _, sql in stages)
    assert "CREATE TEMP TABLE lc_col" in col and "r.source = 'tfrrs'" in col
    assert "JOIN   lc_col c" in cell and LC.hsSql("r") in cell
    assert "FROM   lc_cell c" in rows and "JOIN   results r ON r.person_id = c.person_id" in rows
    for sql in (col, cell, rows):
        assert "%" not in sql, "run without parameters, but keep them clean"


def test_the_rule_is_what_prepare_decided():
    assert LC.ruleSql("results", "XC") == "SELECT result_id FROM tw_level_conflict"
    src = read("engine", "level_conflict.py")
    assert "SET enable_nestloop = on" in src and "SHOW enable_nestloop" in src, \
        "the nested loop is turned on for the index read and handed back"


# ---- the wiring ---------------------------------------------------------- #

def test_the_flag_rides_result_twin_so_every_reader_already_honours_it():
    src = read("engine", "twin_flag.py")
    assert "import level_conflict as LC" in src
    assert "(LC.REASON, LC.ruleSql))" in src, "the last rule: a twin files as a twin"
    assert "prepareRule(cur, table, sport, reason)" in src.split("def _runSport(")[1]
    assert "_runSport(" in src.split("def build(")[1]
    assert LC.REASON == "level_conflict"
    sh = read("deploy", "run_pipeline.sh")
    assert "level_conflict" in sh
    assert sh.index("step 04a_link_tfrrs") < sh.index("step 04c_twins") < sh.index("step 07_pack")


def test_the_linkers_ask_the_same_question_before_they_weld():
    lf = read("scripts", "link_freshmen.py")
    assert "from level_conflict import collegeSql, hsSql" in lf
    assert "bool_or({collegeSql('x')})" in lf and "COALESCE(college, false)" in lf
    assert "AS still_hs" in lf and "(a.y::text || '-08-01')" in lf
    lin = read("scripts", "link_idless_by_name.py")
    assert "from level_conflict import acadSql, collegeSql, hsSql" in lin
    assert "NOT (r.is_college AND EXISTS" in lin


def test_a_senior_still_in_high_school_is_not_paired():
    import link_freshmen as L
    fresh = {101: ("Nick Walker", "Bates", "M", 6),
             102: ("Max Bennett", "Conn College", "M", 5)}
    senior = {1: ("Nick Walker", "Winchester", "M", False, True),   # races HS in Y
              2: ("Max Bennett", "Lexington", "M", False, False)}
    pairs, skipped = L.pairsFor(fresh, senior)
    assert [(t, a) for t, a, *_ in pairs] == [(102, 2)]
    assert skipped == {"senior still in high school": 1}


# ---- on a real Postgres, when one is offered ------------------------------ #
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"

def _pg():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import importlib
    for name in [n for n in sys.modules if n == "psycopg2" or n.startswith("psycopg2.")]:
        if not hasattr(sys.modules[name], "__file__"):
            del sys.modules[name]
    return importlib.import_module("psycopg2").connect(dsn)


def test_refresh_replaces_only_its_own_reason():
    conn = _pg()
    cur = conn.cursor()
    cur.execute(open(os.path.join(ROOT, "tests", "fixtures", "twin_rules.sql"),
                     encoding="utf-8").read())
    cur.execute("INSERT INTO result_twin VALUES ('XC', 701, 'twin_race'), "
                "('XC', 9005, 'twin_race'), ('XC', 1, 'level_conflict')")
    got = {s: LC.prepare(cur, t, s) for s, t in LC.TABLES.items()}
    assert sorted(got["XC"]) == [9005, 9014], "the 6003 tie flags nothing"
    assert sorted(got["TF"]) == [9115, 9116]
    cur.execute("SELECT school FROM lc_college_school ORDER BY 1")
    assert [r[0] for r in cur.fetchall()] == ["Middlebury", "RPI", "Williams"], \
        "Hamilton is vetoed by its hs slug; Winnisquam has no witness"
    # the ties are kept for the report (the last prepare was TF's: none)
    LC.prepare(cur, "results", "XC")
    cur.execute("SELECT person_id, acad FROM lc_tie")
    assert cur.fetchall() == [(6003, 2025)]
    stub = type(sys)("twin_flag")
    stub.ensureTable = lambda c: None
    saved = sys.modules.get("twin_flag")
    sys.modules["twin_flag"] = stub
    try:
        LC.refresh(conn, got)
    finally:
        if saved is not None:
            sys.modules["twin_flag"] = saved
        else:
            del sys.modules["twin_flag"]
    cur = conn.cursor()
    cur.execute("SELECT sport, result_id, reason FROM result_twin ORDER BY 1, 2")
    assert cur.fetchall() == [
        ("TF", 9115, "level_conflict"), ("TF", 9116, "level_conflict"),
        ("XC", 701, "twin_race"),
        ("XC", 9005, "twin_race"),              # an earlier reason keeps its row
        ("XC", 9014, "level_conflict")], "the stale id 1 is gone"
    cur.execute("DROP TABLE result_twin")
    conn.commit()


def test_the_freshman_guards_on_postgres():
    """seniors() marks the one who kept racing high school; freshmen() drops a
    tfrrs 'Fr' with no college row (a high school meet on tfrrs)."""
    import link_freshmen as L
    conn = _pg()
    cur = conn.cursor()
    cur.execute("""
        DROP TABLE IF EXISTS results, results_tf, athletes, person_gender;
        CREATE TABLE results (result_id bigint, person_id bigint, native_id bigint,
          source text, date text, grade text, school text, athlete_name text,
          team_id int, team_slug text, time_seconds double precision,
          id_system text);
        CREATE TABLE results_tf (LIKE results);
        ALTER TABLE results_tf ADD COLUMN is_relay int;
        CREATE TABLE athletes (athlete_id bigint, first_name text, last_name text,
          gender text);
        INSERT INTO athletes VALUES (500, 'Nick', 'Walker', 'M'), (600, 'Max', 'Bennett', 'M');
        -- 500: a senior in 2024-25 whose id races high school again in autumn 2025
        INSERT INTO results VALUES
          (1, 500, NULL, 'anet', '2024-10-20', '12', 'Winchester', NULL, 77, NULL, 900),
          (2, 500, NULL, 'anet', '2025-10-26', '11', 'Winchester', NULL, 77, NULL, 988.5),
          (3, 600, NULL, 'anet', '2024-10-20', '12', 'Lexington',  NULL, 78, NULL, 950);
        -- two tfrrs first-years of 2025: one college, one a tfrrs HS meet 'Fr'
        INSERT INTO results VALUES
          (-1, NULL, 81, 'tfrrs', '2025-09-06', 'FR-1', 'Bates', 'Nick Walker', NULL, 'ME_college_m_Bates', 1500),
          (-2, NULL, 82, 'tfrrs', '2025-09-06', 'Fr', 'Forest Park', 'Max Bennett', NULL, NULL, 1100);
    """)
    try:
        sen = L.seniors(cur, [2025])[2025]
        assert sen[500][4] is True and sen[600][4] is False
        fr = L.freshmen(cur, [2025])[2025]
        assert set(fr) == {"n:tfrrs:81"}, "the high school 'Fr' is not a college first-year"
        pairs, skipped = L.pairsFor(fr, sen)
        assert pairs == [] and skipped == {"senior still in high school": 1}
    finally:
        conn.rollback()          # a failure must not leave the tables locked
        conn.close()
