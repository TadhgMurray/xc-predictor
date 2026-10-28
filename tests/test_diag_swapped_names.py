"""diag_swapped_names: two people whose names are each other's reversed, at
one school, never in one race -- and the checks that keep two kids apart.

    XCP_TWIN_TEST_DSN="host=... dbname=..." runs the SQL
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import diag_swapped_names as D                                   # noqa: E402
from person_name import personKeySql                             # noqa: E402


def test_classify_order():
    pairs = [(1, 2, "s", "M", "a", "b"), (3, 4, "s", "M", "c", "d"),
             (5, 6, "s", "M", "e", "f"), (7, 8, "s", "M", "g", "h")]
    kinds = D.classify(pairs, {(3, 4): 3}, {(5, 6)}, {(7, 8), (5, 6)})
    assert [r[0] for r in kinds["safe"]] == [1]
    assert [r[0] for r in kinds["crowded"]] == [3]
    assert [r[0] for r in kinds["same race"]] == [5]
    assert [r[0] for r in kinds["overlap"]] == [7]


def test_sql():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint, first_name text,"
                    " last_name text, gender text, school text)")
        cur.execute("CREATE TEMP TABLE results (person_id bigint, meet_id bigint, div_id bigint,"
                    " source text, date text, grade text)")
        cur.execute("CREATE TEMP TABLE results_tf (person_id bigint, meet_id bigint, div_id bigint,"
                    " event_id bigint, source text, date text, grade text)")
        cur.execute("""INSERT INTO athletes VALUES
            (1, 1, 'Ethan', 'Avila', 'M', 'Cedar'), (2, 2, 'Avila', 'Ethan', 'M', 'Cedar'),
            (3, 3, 'James', 'Thomas', 'M', 'Cedar'), (4, 4, 'Thomas', 'James', 'M', 'Cedar'),
            (5, 5, 'Ann', 'Lee', 'F', 'Cedar'), (6, 6, 'Lee', 'Ann', 'F', 'Oak'),
            (7, 7, 'Bo', 'Bo', 'M', 'Cedar'), (8, 8, 'Sam', 'Smith Jr', 'M', 'Cedar'),
            (9, 9, 'Smith Jr', 'Sam', 'M', 'Cedar')""")
        # James Thomas and Thomas James ran one track race: two kids
        cur.execute("INSERT INTO results_tf VALUES (3, 10, 1, 52, 'anet', '2025-04-01', '11'),"
                    " (4, 10, 1, 52, 'anet', '2025-04-01', '10')")
        cur.execute(D._KEYS.format(fwd=personKeySql("a.first_name", "a.last_name", keep_suffix=True),
                                   rev=personKeySql("a.last_name", "a.first_name", keep_suffix=True)))
        cur.execute(D._PAIRS)
        cur.execute("SELECT p1, p2, sch, g, n1, n2 FROM nk_pair ORDER BY p1")
        pairs = cur.fetchall()
        assert [(p[0], p[1]) for p in pairs] == [(1, 2), (3, 4), (8, 9)]  # not Lee (two schools), not Bo Bo
        cur.execute(D._CROWD)
        crowd = {(a, b): n for a, b, n in cur.fetchall()}
        cur.execute(D._SAME_RACE)
        same = set(cur.fetchall())
        cur.execute(D._OVERLAP)
        overlap = set(cur.fetchall())
        kinds = D.classify(pairs, crowd, same, overlap)
        assert [(r[0], r[1]) for r in kinds["safe"]] == [(1, 2), (8, 9)]
        assert [(r[0], r[1]) for r in kinds["same race"]] == [(3, 4)]
    finally:
        conn.rollback()
        conn.close()
