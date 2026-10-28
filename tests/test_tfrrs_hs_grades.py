"""scripts/link_tfrrs_hs.fixHsGrades (pooling review P0, 2026-10-26): a
state site's class words become grades 9-12, and only at its high school
meets. On a scratch Postgres when XCP_TWIN_TEST_DSN is set."""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")


def test_class_words_become_grades_at_hs_meets_only():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    import link_tfrrs_hs as L
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("""
        CREATE TEMP TABLE results (result_id int, source text, meet_id bigint, grade text);
        CREATE TEMP TABLE results_tf (result_id int, source text, meet_id bigint, grade text);
        CREATE TEMP TABLE tfrrs_hs_meets (meet_id bigint, sport text);
        INSERT INTO tfrrs_hs_meets VALUES (10, 'XC'), (20, 'TF');
        INSERT INTO results VALUES (1,'tfrrs',10,'SR'), (2,'tfrrs',10,'JR-3'), (3,'tfrrs',10,'fr'),
                                   (4,'tfrrs',10,'12'), (5,'tfrrs',11,'SR'), (6,'anet',10,'SR'),
                                   (7,'tfrrs',20,'SO'), (8,'tfrrs',10,'Sr.'), (9,'tfrrs',10,'SOPH');
        INSERT INTO results_tf VALUES (1,'tfrrs',20,'SO-2'), (2,'tfrrs',10,'SO');
    """)
    got = L.fixHsGrades(cur)
    cur.execute("SELECT result_id, grade FROM results ORDER BY 1")
    xc = dict(cur.fetchall())
    cur.execute("SELECT result_id, grade FROM results_tf ORDER BY 1")
    tf = dict(cur.fetchall())
    conn.rollback()
    assert xc == {1: "12", 2: "11", 3: "9", 4: "12", 5: "SR", 6: "SR", 7: "SO", 8: "12", 9: "SOPH"}
    assert tf == {1: "10", 2: "SO"}
    assert got == {"results": 4, "results_tf": 1}
