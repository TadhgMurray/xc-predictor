"""engine/twin_flag.dupCrossGenderSql, one pass (2026-11-10): the same rows as
the two-scan query it replaced, on adversarial data -- NULL divisions, NULL
dates and meets, punctuation-only names, two and three divisions, mixed
fields. Needs a scratch Postgres: XCP_TEST_PG_DSN (skipped without one)."""
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

DSN = os.environ.get("XCP_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")

# the query as it was before 2026-11-10: the reference
_KEY = "lower(regexp_replace(r.athlete_name, '[^A-Za-z]+', '', 'g'))"
OLD_HEAD = f"""
        WITH k AS (
            SELECT r.source, r.meet_id, left(r.date, 10) AS day, {_KEY} AS nk,
                   round(r.time_seconds::numeric, 1) AS rt
            FROM   t r
            WHERE  r.time_seconds BETWEEN 60 AND 14400
              AND  NULLIF(btrim(r.athlete_name), '') IS NOT NULL
            GROUP  BY 1, 2, 3, 4, 5
            HAVING count(DISTINCT r.div_id) = 2),
        rows AS (
            SELECT r.result_id, r.person_id, r.source, r.meet_id, r.div_id, k.day, k.nk, k.rt
            FROM   t r JOIN k ON k.source = r.source AND k.meet_id = r.meet_id
                   AND left(r.date, 10) = k.day AND {_KEY} = k.nk
                   AND round(r.time_seconds::numeric, 1) = k.rt),
        fld AS ("""


def _old(new_sql):
    return OLD_HEAD + new_sql.split("fld AS (", 1)[1]


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


def test_one_pass_gives_the_same_rows_as_the_two_scans():
    import psycopg2
    from twin_flag import dupCrossGenderSql
    new = dupCrossGenderSql("t", "XC")
    old = _old(new)
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        hits = 0
        for seed in range(40):
            _seed(cur, seed)
            cur.execute(old)
            a = sorted(r[0] for r in cur.fetchall())
            cur.execute(new)
            b = sorted(r[0] for r in cur.fetchall())
            assert a == b, (seed, a, b)
            hits += len(a)
        assert hits > 0               # the data did reach the final join
    finally:
        conn.rollback()
        conn.close()
