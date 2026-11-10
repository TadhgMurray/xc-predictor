"""scripts/run_checklist.checkWheelchairPeople (2026-11-10): the count and
the five rows shown, by two indexed lookups from the short list, are the
same as the OR-of-EXISTS probe of every row it replaced. Needs a scratch
Postgres: XCP_TEST_PG_DSN (skipped without one)."""
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "engine"))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

DSN = os.environ.get("XCP_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")


def _old(t):
    return f"""
        SELECT {t}.result_id FROM {t}
        WHERE {t}.speed_rating IS NOT NULL
          AND (EXISTS (SELECT 1 FROM _ck_wcp w
                       WHERE w.person_id IS NOT NULL
                         AND w.person_id = {t}.person_id)
            OR ({t}.person_id IS NULL AND EXISTS (
                 SELECT 1 FROM _ck_wcp w
                 WHERE w.person_id IS NULL AND w.source = {t}.source
                   AND w.athlete_id = {t}.athlete_id)))"""


def test_two_lookups_count_and_show_what_the_probe_did(capsys):
    import psycopg2
    import run_checklist as C
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        for seed in range(15):
            rnd = random.Random(seed)
            cur.execute("DROP TABLE IF EXISTS results, results_tf, meets, wheelchair_person, _ck_wcp")
            cur.execute("""CREATE TABLE meets (meet_id bigint, div_id bigint, source text, division text)""")
            for t in ("results", "results_tf"):
                cur.execute(f"""CREATE TABLE {t} (result_id bigint, person_id bigint, athlete_id bigint,
                               source text, meet_id bigint, div_id bigint, event_short text,
                               speed_rating double precision)""")
            cur.execute("CREATE TABLE wheelchair_person (person_id bigint)")
            for m in range(20):
                cur.execute("INSERT INTO meets VALUES (%s,%s,%s,%s)",
                            (m, 1, rnd.choice(["anet", "tfrrs"]),
                             rnd.choice(["Boys Varsity", "Wheelchair 5K", "Girls JV"])))
            rid = 0
            for t in ("results", "results_tf"):
                for _ in range(800):
                    rid += 1
                    cur.execute(f"INSERT INTO {t} VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                                (rid, rnd.choice([None, rnd.randint(1, 60)]),
                                 rnd.choice([None, rnd.randint(1, 80)]),
                                 rnd.choice(["anet", "tfrrs"]), rnd.randint(0, 19), 1,
                                 rnd.choice(["800m", "Wheelchair 1500", "1600m"]),
                                 rnd.choice([None, 120.5, 99.0])))
            for p in rnd.sample(range(1, 70), 5):
                cur.execute("INSERT INTO wheelchair_person VALUES (%s)", (p,))
            C._mark = lambda *a, **k: None
            # the check builds _ck_wcp; keep it and compare its answer to the old probe's
            want = {}
            C.checkWheelchairPeople(cur)
            got_out = capsys.readouterr().out
            for t in ("results", "results_tf"):
                cur.execute(_old(t))
                want[t] = sorted(r[0] for r in cur.fetchall())
            shown_want = [f"{t} result {rid_}" for t in ("results", "results_tf")
                          for rid_ in want[t][:5]]
            for line in shown_want:
                assert line in got_out, (seed, line)
            # and the counts, through the new SQL directly
            for t in ("results", "results_tf"):
                rows = f"""
                    SELECT r.result_id FROM (SELECT DISTINCT person_id FROM _ck_wcp WHERE person_id IS NOT NULL) w
                    JOIN {t} r ON r.person_id = w.person_id WHERE r.speed_rating IS NOT NULL
                    UNION ALL
                    SELECT r.result_id FROM (SELECT DISTINCT source, athlete_id FROM _ck_wcp WHERE person_id IS NULL) w
                    JOIN {t} r ON r.source = w.source AND r.athlete_id = w.athlete_id
                    WHERE r.person_id IS NULL AND r.speed_rating IS NOT NULL"""
                cur.execute(rows)
                assert sorted(r[0] for r in cur.fetchall()) == want[t], (seed, t)
            conn.rollback()
    finally:
        conn.rollback()
        conn.close()
