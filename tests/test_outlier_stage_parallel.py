"""engine/rating_outliers.buildStage filled side by side (2026-11-10): the
block-range workers stage exactly the rows the one INSERT did. Needs a
scratch Postgres: XCP_TEST_PG_DSN (skipped without one)."""
import os
import random
import sys
from contextlib import contextmanager

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

DSN = os.environ.get("XCP_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")


def test_the_workers_stage_the_one_inserts_rows(monkeypatch):
    monkeypatch.setenv("XCP_OUTLIER_FILL_PARALLEL", "1")   # opt-in since 2026-11-12
    import psycopg2
    import rating_outliers as R

    @contextmanager
    def connect():
        c = psycopg2.connect(DSN)
        try:
            yield c
        finally:
            c.close()

    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        rnd = random.Random(3)
        cur.execute("DROP TABLE IF EXISTS results, results_tf, result_twin, impossible_result, rating_outlier CASCADE")
        for t in ("results", "results_tf"):
            cur.execute(f"""CREATE TABLE {t} (result_id bigint, person_id bigint, date text,
                            speed_rating real, is_relay int)""")
            rows = [(i, rnd.choice([None, rnd.randint(1, 3000)]),
                     rnd.choice(["2024-10-05", "2025-03-01", "bad", "1999-12-31T09:00"]),
                     rnd.choice([None, rnd.uniform(60, 160)]), rnd.choice([None, 0, 1]))
                    for i in range(40000)]
            cur.executemany(f"INSERT INTO {t} VALUES (%s,%s,%s,%s,%s)", rows)
        cur.execute("CREATE TABLE result_twin (sport text, result_id bigint)")
        cur.execute("INSERT INTO result_twin SELECT 'XC', g FROM generate_series(1, 40000, 37) g")
        cur.execute("CREATE TABLE impossible_result (sport text, result_id bigint)")
        cur.execute("INSERT INTO impossible_result SELECT 'TF', g FROM generate_series(5, 40000, 53) g")
        cur.execute("CREATE TABLE rating_outlier (sport text, result_id bigint, side text)")
        cur.execute("INSERT INTO rating_outlier SELECT 'TF', g, 'fast' FROM generate_series(7, 40000, 41) g")
        conn.commit()
        assert R._blocks(cur, "results") > 8           # several workers' worth of blocks

        def staged():
            cur.execute(f"SELECT key, sport, result_id, person_id, d, rating, nb, judge "
                        f"FROM {R.STAGE} ORDER BY key")
            return cur.fetchall()

        for since in (None, 2025):
            R.buildStage(cur, since=since, shards=3, verbose=False)
            one = staged()
            R.buildStage(cur, since=since, shards=3, verbose=False, connect=connect, streams=4)
            two = staged()
            assert one and two == one, since
    finally:
        conn.rollback()
        conn.close()
