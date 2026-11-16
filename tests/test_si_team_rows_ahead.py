"""racecast/build_school_identity (2026-11-16): si_team_raw copied from the
table 09e_si_team_rows built ahead holds exactly the rows the in-place scan
builds; without the variable, or with the table gone, the scan runs.
Needs a scratch Postgres: XCP_TEST_PG_DSN (skipped without one)."""
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, d) for d in ("racecast", "scripts", "engine")]
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
DSN = os.environ.get("XCP_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")


def test_ahead_equals_in_place(monkeypatch):
    import psycopg2
    import database
    cfg = dict(database._PG_CONFIG_WITH_TIMEOUT)
    cfg.update(dict(kv.split("=", 1) for kv in DSN.split()))
    monkeypatch.setattr(database, "_PG_CONFIG_WITH_TIMEOUT", cfg)
    database._pool = None
    database._slots = None
    import build_school_identity as SI
    monkeypatch.setattr(SI, "getConn", database.getConn)
    rnd = random.Random(3)
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS results, results_tf, anet_team, si_team_raw_pre")
    for t in ("results", "results_tf"):
        cur.execute(f"CREATE TABLE {t} (person_id bigint, school text, team_id bigint)")
        cur.executemany(f"INSERT INTO {t} VALUES (%s,%s,%s)",
                        [(rnd.choice([None, rnd.randint(1, 50)]), rnd.choice([None, "A", "B ", "C"]),
                          rnd.choice([None, 0, 1, 2, 3, 4])) for _ in range(3000)])
    cur.execute("CREATE TABLE anet_team (team_id bigint, state text, anet_state text)")
    cur.execute("INSERT INTO anet_team VALUES (1,'ca',NULL),(2,'NY','ny'),(3,' ',''),(4,NULL,'tx')")
    conn.commit()

    def build(env):
        if env:
            monkeypatch.setenv("XCP_SI_TEAM_RAW", env)
        else:
            monkeypatch.delenv("XCP_SI_TEAM_RAW", raising=False)
        c = psycopg2.connect(DSN)
        k = c.cursor()
        SI._teamRows(k)
        k.execute("SELECT src, person_id, school, state, n FROM si_team_raw")
        rows = sorted(k.fetchall(), key=repr)
        c.commit(); c.close()                                # as 10b commits its phase
        return rows

    in_place = build(None)
    SI.teamRowsInto("si_team_raw_pre")
    ahead = build("si_team_raw_pre")
    assert in_place and ahead == in_place
    cur.execute("SELECT to_regclass('si_team_raw_pre')")
    assert cur.fetchone()[0] is None                        # consumed
    assert build("si_team_raw_pre") == in_place             # gone: the scan runs
    conn.close()
    database._pool = None
