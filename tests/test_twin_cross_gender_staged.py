"""engine/twin_flag dup_cross_gender, staged (2026-11-10): prepareCrossGender
+ dupCrossGenderSql flag the SAME rows as the one query it replaced
(_dupCrossGenderOneQuery), under the rules' own session settings, on data
built to hit every branch -- NULL divisions, dates and meets, punctuation-
only names, two and three divisions, mixed and thin fields. Needs a scratch
Postgres: XCP_TEST_PG_DSN (skipped without one)."""
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

DSN = os.environ.get("XCP_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")


def _seed(cur, seed):
    rnd = random.Random(seed)
    cur.execute("DROP TABLE IF EXISTS t; DROP TABLE IF EXISTS person_gender")
    cur.execute("""CREATE TABLE t (result_id bigint, person_id bigint, source text,
                   meet_id bigint, div_id bigint, date text, athlete_name text,
                   time_seconds double precision)""")
    cur.execute("CREATE TABLE person_gender (person_id bigint, gender text, n_m int, n_f int)")
    names = ["Ann Lee", "ann  lee", "A.nn-Lee", "Bob Ray", "bob ray", "...", "  ", None,
             "Cy Fox", "cy fox"]
    rows, rid = [], 0
    for meet in range(6):
        for div in (1, 2, 3, None):
            # a field: enough gendered runners for fld to call it
            g = rnd.choice("MF")
            for k in range(rnd.randint(4, 9)):
                rid += 1
                pid = 1000 + rid
                rows.append((rid, pid, rnd.choice(["anet", "anet", None]), rnd.choice([meet, meet, None]),
                             div, rnd.choice(["2025-10-01T09:00", "2025-10-01", None]),
                             f"Runner {rid}", rnd.uniform(900, 1100)))
                cur.execute("INSERT INTO person_gender VALUES (%s,%s,%s,%s)",
                            (pid, g if rnd.random() < .9 else rnd.choice("MFX"), rnd.randint(0, 3), rnd.randint(0, 3)))
            # the duplicated runners, listed in several divisions
            for _ in range(rnd.randint(2, 6)):
                rid += 1
                pid = rnd.randint(1, 8)
                rows.append((rid, pid, "anet", meet, div, rnd.choice(["2025-10-01", "2025-10-01T10:00", None]),
                             rnd.choice(names), rnd.choice([1000.0, 1000.04, 1000.2, 50.0])))
    for pid in range(1, 9):
        cur.execute("INSERT INTO person_gender VALUES (%s,%s,%s,%s)",
                    (pid, rnd.choice("MF"), rnd.randint(0, 5), rnd.randint(0, 5)))
    cur.executemany("INSERT INTO t VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", rows)



def test_staged_cross_gender_flags_the_same_rows_as_the_one_query():
    import psycopg2
    import twin_flag as T
    conn = psycopg2.connect(DSN)
    conn.set_client_encoding("UTF8")
    try:
        cur = conn.cursor()
        T._session(conn, cur)
        hits = 0
        for seed in range(40):
            _seed(cur, seed)
            cur.execute("CREATE INDEX ON t (meet_id)")
            cur.execute(T._dupCrossGenderOneQuery("t", "XC"))
            want = sorted(r[0] for r in cur.fetchall())
            T.prepareRule(cur, "t", "XC", "dup_cross_gender")
            cur.execute(T.dupCrossGenderSql("t", "XC"))
            got = sorted(r[0] for r in cur.fetchall())
            assert got == want, (seed, set(want) ^ set(got))
            cur.execute("SHOW enable_nestloop")
            assert cur.fetchone()[0] == "off"
            hits += len(want)
        assert hits > 0
    finally:
        conn.rollback()
        conn.close()


def test_track_has_no_cross_gender_rule():
    import twin_flag as T
    assert "WHERE false" in T.dupCrossGenderSql("results_tf", "TF")
