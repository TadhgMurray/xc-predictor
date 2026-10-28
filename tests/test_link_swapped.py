"""link_swapped: a runner held as two people with first and last swapped is
joined -- but not two first names, not an unattached pool, not a name with
digits, not a chain; and --undo puts every row back.

    XCP_TWIN_TEST_DSN="host=... dbname=..." runs apply / undo on Postgres
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import link_swapped as L                                         # noqa: E402

STATS = {"ethan": (500, 3), "avila": (2, 300), "thomas": (900, 400), "jackson": (300, 280),
         "breanne": (40, 0), "geary": (0, 60)}


def _d(pair, kind="safe", rows=None, pairs_of=None, real=True):
    return L.decide(pair, kind, STATS, rows or {}, pairs_of or {}, lambda s: real)


def test_the_name_the_right_way_round_keeps_the_id():
    p = (1, 2, "cedar", "M", "Ethan", "Avila", "Avila", "Ethan")
    # 'Ethan Avila' keeps it even with the smaller career
    assert _d(p, rows={1: 3, 2: 12}) == ("link", 1, 2)
    p = (1, 2, "cedar", "M", "Avila", "Ethan", "Ethan", "Avila")
    assert _d(p, rows={1: 30, 2: 1}) == ("link", 2, 1)


def test_unclear_names_keep_the_bigger_career():
    p = (1, 2, "cedar", "M", "Mpoy", "Chetiace", "Chetiace", "Mpoy")   # our data cannot say
    assert _d(p, rows={1: 3, 2: 12}) == ("link", 2, 1)
    assert _d(p, rows={1: 5, 2: 5}) == ("link", 1, 2)          # a tie: the lower id


def test_what_is_refused():
    assert _d((1, 2, "s", "M", "Thomas", "Jackson", "Jackson", "Thomas"))[0] == \
        "both names are first names"
    assert _d((1, 2, "s", "F", "Breanne", "Geary", "Geary", "Breanne"))[0] == "link"
    assert _d((1, 2, "s", "M", "Artsiuk1", "Ryhor", "Ryhor", "Artsiuk"))[0] == "digits in a name"
    assert _d((1, 2, "44-unattached", "M", "Ethan", "Avila", "Avila", "Ethan"),
              real=False)[0] == "no real school"
    assert _d((1, 2, "s", "M", "Ethan", "Avila", "Avila", "Ethan"),
              pairs_of={1: 2})[0] == "in two pairs"
    for kind in ("crowded", "overlap", "same race"):
        assert _d((1, 2, "s", "M", "Ethan", "Avila", "Avila", "Ethan"), kind=kind)[0] == kind


def test_rare_names_are_not_first_names():
    assert not L.isFirstName("Mpoy", {"mpoy": (3, 0)})          # too few uses to say
    assert L.isFirstName("Thomas", STATS) and not L.isFirstName("Avila", STATS)


def test_apply_and_undo_on_postgres():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS t_swapped CASCADE; CREATE SCHEMA t_swapped;"
                " SET search_path TO t_swapped")
    try:
        cur.execute("CREATE TABLE athletes (athlete_id bigint, person_id bigint, first_name text,"
                    " last_name text, gender text, school text)")
        cur.execute("CREATE TABLE results (result_id bigint PRIMARY KEY, person_id bigint,"
                    " meet_id bigint, div_id bigint, source text, date text, grade text)")
        cur.execute("CREATE TABLE results_tf (result_id bigint PRIMARY KEY, person_id bigint,"
                    " meet_id bigint, div_id bigint, event_id bigint, source text, date text,"
                    " grade text)")
        cur.execute("""INSERT INTO athletes VALUES
            (10, 10, 'Ethan', 'Avila', 'M', 'Cedar'), (20, 20, 'Avila', 'Ethan', 'M', 'Cedar'),
            (21, 20, 'Avila', 'Ethan', 'M', 'Cedar Middle'),
            (30, 30, 'Thomas', 'Jackson', 'M', 'Cedar'), (40, 40, 'Jackson', 'Thomas', 'M', 'Cedar')""")
        cur.execute("""INSERT INTO results VALUES (1, 10, 100, 1, 'anet', '2025-09-01', '10'),
                       (2, 10, 101, 1, 'anet', '2025-09-08', '10'),
                       (3, 20, 102, 1, 'anet', '2025-09-15', '10')""")
        cur.execute("INSERT INTO results_tf VALUES (4, 20, 200, 1, 52, 'anet', '2026-04-01', '10')")
        conn.commit()
        pairs, kinds, stats, rows_of = L.candidates(cur)
        verdicts = L.judge(pairs, kinds, {"thomas": (900, 400), "jackson": (300, 280)}, rows_of)
        conn.rollback()
        cur.execute("SET search_path TO t_swapped")
        assert verdicts[(10, 20)][0] == "link" and verdicts[(10, 20)][1] == 10
        assert verdicts[(30, 40)][0] == "both names are first names"
        out = L.write(conn, [(20, 10, "Avila Ethan", "Ethan Avila", "cedar")])
        assert out["XC rows moved"] == 1 and out["TF rows moved"] == 1
        assert out["athletes rows repointed"] == 2
        cur.execute("SELECT count(*) FROM results WHERE person_id = 10")
        assert cur.fetchone()[0] == 3
        cur.execute("SELECT new_id FROM person_redirect WHERE old_id = 20")
        assert cur.fetchone()[0] == 10
        L.undo(conn, "20")
        cur.execute("SET search_path TO t_swapped")
        cur.execute("SELECT person_id FROM results WHERE result_id = 3")
        assert cur.fetchone()[0] == 20
        cur.execute("SELECT person_id FROM results_tf WHERE result_id = 4")
        assert cur.fetchone()[0] == 20
        cur.execute("SELECT athlete_id, person_id FROM athletes WHERE athlete_id IN (20, 21)"
                    " ORDER BY 1")
        assert cur.fetchall() == [(20, 20), (21, 20)]
        cur.execute("SELECT count(*) FROM person_redirect WHERE old_id = 20")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT old_id FROM swapped_veto")
        assert cur.fetchall() == [(20,)]
    finally:
        conn.rollback()
        cur.execute("DROP SCHEMA IF EXISTS t_swapped CASCADE")
        conn.commit()
        conn.close()
