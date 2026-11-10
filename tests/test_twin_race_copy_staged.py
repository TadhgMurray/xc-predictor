"""engine/twin_flag dup_race_copy, staged (2026-11-10): prepareRaceCopy +
dupRaceCopySql flag the SAME rows as the one query it replaced
(_dupRaceCopyOneQuery), for both sports, under the rules' own session
settings, on data built to hit every branch: whole copies, 90% copies,
partial copies (prelims and finals), NULL events and divisions, track
round codes (H counts as P), copies 91+ days apart, size and date ties.
Needs a scratch Postgres: XCP_TEST_PG_DSN (skipped without one)."""
import os
import random
import sys

import pytest
from datetime import date as _date, timedelta as _td

_BASE = _date(2025, 1, 1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

DSN = os.environ.get("XCP_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")


def _seed(cur, seed):
    rnd = random.Random(seed)
    for t in ("results", "results_tf"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    cur.execute("""CREATE TABLE results (result_id bigint, person_id bigint, source text,
                   meet_id bigint, div_id bigint, date text, time_seconds double precision)""")
    cur.execute("""CREATE TABLE results_tf (result_id bigint, person_id bigint, source text,
                   meet_id bigint, div_id bigint, event_id bigint, round text, date text,
                   time_seconds double precision)""")
    cur.execute("CREATE INDEX ON results (meet_id)")
    cur.execute("CREATE INDEX ON results_tf (meet_id, div_id, event_id)")
    rid = [0]

    def race(n_people, base_day):
        people = rnd.sample(range(1, 400), n_people)
        return [(p, round(rnd.uniform(250, 1300), 2)) for p in people], base_day

    def put(table, field, meet, div, day, ev=None, rnd_code=None, keep=1.0, jitter=False):
        out = []
        for p, t in field:
            if rnd.random() > keep:
                continue
            rid[0] += 1
            tt = t + (rnd.choice([0.3, -0.4]) if jitter and rnd.random() < .5 else 0)
            date = (_BASE + _td(days=day)).isoformat() + rnd.choice(["", "T10:00"])
            if table == "results":
                out.append((rid[0], p, "anet", meet, div, date, tt))
            else:
                out.append((rid[0], p, "anet", meet, div, ev, rnd_code, date, tt))
        return out

    for table in ("results", "results_tf"):
        rows = []
        for m in range(30):
            field, day = race(rnd.randint(4, 30), rnd.randint(0, 250))
            ev = rnd.choice([1, 2, None]) if table == "results_tf" else None
            rc = rnd.choice([None, "F", "P", "H", " final", "heat 2"]) if table == "results_tf" else None
            div = rnd.choice([1, 2, None])
            rows += put(table, field, 1000 + m, div, day, ev, rc)
            kind = rnd.random()
            if kind < .35:      # a whole copy, maybe a few days on
                rows += put(table, field, 2000 + m, rnd.choice([1, 3]), day + rnd.choice([0, 3, 95]),
                            ev, rnd.choice([rc, "P", "F"]))
            elif kind < .6:     # a 90%-ish copy
                rows += put(table, field, 3000 + m, 1, day + rnd.randint(0, 20), ev, rc, keep=.92)
            elif kind < .7:     # prelims vs final: same people, other times
                rows += put(table, field, 1000 + m, 9, day, ev, "P", jitter=True)
            elif kind < .85:    # a later, BIGGER listing holding this race whole: a partial loser
                extra, _ = race(rnd.randint(len(field), len(field) + 15), day)
                rows += put(table, field + extra, 4000 + m, 1, day + rnd.randint(1, 5), ev, rc)
            # rows the size count must not see, in the divisions just made:
            # enough to tip the 90% test if the filter were dropped
            if table == "results" or rnd.random() < .7:
                for _ in range(rnd.randint(0, 4)):
                    rid[0] += 1
                    bad = rnd.choice(["noperson", "slow", "date"])
                    pid = None if bad == "noperson" else rnd.randint(1, 400)
                    t = 200000.0 if bad == "slow" else 900.0
                    dt = "unknown" if bad == "date" else (_BASE + _td(days=day)).isoformat()
                    meet = rnd.choice([1000 + m, 2000 + m, 3000 + m, 4000 + m])
                    if table == "results":
                        rows.append((rid[0], pid, "anet", meet, rnd.choice([1, div]), dt, t))
                    else:
                        rows.append((rid[0], pid, "anet", meet, rnd.choice([1, div]), ev, rc, dt, t))
            # junk rows the filters must drop
        rid[0] += 1
        if table == "results":
            rows.append((rid[0], None, "anet", 1, 1, "2025-01-01", 900.0))
        else:
            rows.append((rid[0], 5, "anet", 1, 1, 1, None, "bad-date", 900.0))
        cols = 7 if table == "results" else 9
        cur.executemany(f"INSERT INTO {table} VALUES ({','.join(['%s'] * cols)})", rows)
    cur.execute("ANALYZE results; ANALYZE results_tf")


def test_staged_race_copy_flags_the_same_rows_as_the_one_query():
    import psycopg2
    import twin_flag as T
    conn = psycopg2.connect(DSN)
    conn.set_client_encoding("UTF8")          # the rules carry ★ in comments
    try:
        cur = conn.cursor()
        T._session(conn, cur)                     # nested loops off, as the build runs
        total = whole_losers = partial_losers = 0
        for seed in range(25):
            _seed(cur, seed)
            for sport, table in (("XC", "results"), ("TF", "results_tf")):
                cur.execute(T._dupRaceCopyOneQuery(table, sport))
                want = sorted(r[0] for r in cur.fetchall())
                T.prepareRaceCopy(cur, table, sport)
                cur.execute(T.dupRaceCopySql(table, sport))
                got = sorted(r[0] for r in cur.fetchall())
                assert got == want, (seed, sport, set(want) ^ set(got))
                cur.execute(f"SELECT count(*) FILTER (WHERE whole), count(*) FILTER (WHERE NOT whole) "
                            f"FROM _rc_loser_{sport.lower()}")
                w, p_ = cur.fetchone()
                whole_losers += w; partial_losers += p_
                cur.execute("SHOW enable_nestloop")
                assert cur.fetchone()[0] == "off"   # the session's setting put back
                total += len(want)
        assert total > 100                        # the data reached every branch
        assert partial_losers > 0 and whole_losers > 0
    finally:
        conn.rollback()
        conn.close()


def test_explain_plans_every_stage_without_running_it(capsys):
    import psycopg2
    import twin_flag as T
    conn = psycopg2.connect(DSN)
    conn.set_client_encoding("UTF8")          # the rules carry ★ in comments
    try:
        cur = conn.cursor()
        _seed(cur, 1)
        conn.commit()
        T.explain(conn, "TF:dup_race_copy")
        out = capsys.readouterr().out
        for part in ("rows", "matched", "divs", "size", "loser", "flag"):
            assert f"stage _rc_{part}_tf" in out
    finally:
        conn.rollback()
        conn.close()
