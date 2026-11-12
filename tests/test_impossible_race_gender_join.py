"""engine/impossible_race.candidateSql (2026-11-10): the athlete's sex joined
once per athlete gives the SAME candidate rows as the correlated subquery
it replaced (GENDER_SUBQUERY) -- mixed sexes, exact ties, other letters,
no athletes row, person_id or athlete_id. Needs a scratch Postgres:
XCP_TEST_PG_DSN (skipped without one)."""
import os
import random
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

DSN = os.environ.get("XCP_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")


def _old(sql, I):
    # the new query with the join taken out and the subquery put back
    sql = re.sub(r"LEFT JOIN \(\s*SELECT athlete_id,.*?COALESCE\(r\.person_id, r\.athlete_id\)",
                 "", sql, flags=re.S)
    return sql.replace("ag.gender AS gender", f"{I.GENDER_SUBQUERY} AS gender")


def test_the_join_gives_the_subquerys_rows(monkeypatch):
    monkeypatch.setenv("XCP_06C_GENDER_JOIN", "1")      # opt-in since 2026-11-12
    import psycopg2
    import impossible_race as I
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        for seed in range(10):
            rnd = random.Random(seed)
            cur.execute("""DROP TABLE IF EXISTS athletes, results, results_tf, meets, meets_tfrrs,
                           dist_override, meets_tf""")
            cur.execute("CREATE TABLE athletes (athlete_id bigint, gender text)")
            cur.execute("""CREATE TABLE results (result_id bigint, source text, meet_id bigint, div_id bigint,
                           time_seconds double precision, normalized_time double precision,
                           person_id bigint, athlete_id bigint, rating_pool text)""")
            cur.execute("""CREATE TABLE results_tf (result_id bigint, source text, meet_id bigint, div_id bigint,
                           event_id bigint, event_short text, time_seconds double precision,
                           normalized_time double precision, person_id bigint, athlete_id bigint,
                           rating_pool text, is_relay int, is_field int)""")
            cur.execute("CREATE TABLE meets (div_id bigint, source text, distance real)")
            cur.execute("CREATE TABLE meets_tfrrs (meet_id bigint, sport text, distance real, division_distances jsonb)")
            cur.execute("CREATE TABLE dist_override (meet_id bigint, div_id bigint, distance real)")
            cur.execute("""CREATE TABLE meets_tf (meet_id bigint, div_id bigint, event_id bigint, source text,
                           distance_meters real)""")
            for aid in range(1, 80):
                for _ in range(rnd.randint(0, 4)):
                    cur.execute("INSERT INTO athletes VALUES (%s, %s)",
                                (aid, rnd.choice(["M", "F", "M", "F", "X", None])))
            cur.execute("INSERT INTO meets VALUES (1, 'anet', 5000), (2, 'anet', 3000)")
            cur.execute("INSERT INTO meets_tf VALUES (1, 1, 1, 'anet', 1600), (1, 1, 2, 'anet', 5000)")
            for i in range(600):
                cur.execute("INSERT INTO results VALUES (%s,'anet',1,%s,%s,1,%s,%s,NULL)",
                            (i, rnd.choice([1, 2]), rnd.uniform(400, 1100),
                             rnd.choice([None, rnd.randint(1, 90)]), rnd.randint(1, 90)))
                cur.execute("INSERT INTO results_tf VALUES (%s,'anet',1,1,%s,'1600m',%s,1,%s,%s,NULL,0,0)",
                            (i, rnd.choice([1, 2]), rnd.uniform(200, 1100),
                             rnd.choice([None, rnd.randint(1, 90)]), rnd.randint(1, 90)))
            for sport in ("XC", "TF"):
                new = I.candidateSql(cur, sport)
                cur.execute(new)
                got = sorted(cur.fetchall())
                cur.execute(_old(new, I))
                want = sorted(cur.fetchall())
                assert "ag.gender" in new and got == want and want, (seed, sport)
            conn.rollback()
    finally:
        conn.rollback()
        conn.close()
