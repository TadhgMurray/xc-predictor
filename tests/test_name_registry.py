"""scripts/name_registry.py (2026-11-10): the registry built once per 04a,
refreshed from the triggers' move list, gives gather() the SAME namesakes as
the full read of the corpus it replaces -- after person moves, renames,
relay flips, inserts and deletes on all three tables, and after a writer
that rolls back. Needs a scratch Postgres: XCP_TEST_PG_DSN (skipped
without one)."""
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

FIRST = ["Jake", "Ana", "Bo", "Cy", "Dee", "José", "Liam", "Mia"]
LAST = ["Smith", "Lee", "O'Neil", "Nguyen", "Smith-Jones", "Ruiz"]


def _name(rnd):
    f, l = rnd.choice(FIRST), rnd.choice(LAST)
    return rnd.choice([f"{f} {l}", f"{l}, {f}", f"{f.upper()} {l}", f"  {f} {l} ", ""])


def _seed(cur, rnd):
    cur.execute("DROP TABLE IF EXISTS athletes, results, results_tf, lu_person_moves CASCADE")
    cur.execute("CREATE TABLE athletes (athlete_id bigint, person_id bigint, first_name text, last_name text)")
    cur.execute("CREATE TABLE results (result_id bigint, person_id bigint, athlete_name text)")
    cur.execute("CREATE TABLE results_tf (result_id bigint, person_id bigint, athlete_name text, is_relay int)")
    for t in ("athletes", "results", "results_tf"):
        cur.execute(f"CREATE INDEX ON {t} (person_id)")
    cur.executemany("INSERT INTO athletes VALUES (%s,%s,%s,%s)",
                    [(i, rnd.choice([None, rnd.randint(1, 60)]), rnd.choice(FIRST), rnd.choice(LAST + [""]))
                     for i in range(150)])
    cur.executemany("INSERT INTO results VALUES (%s,%s,%s)",
                    [(i, rnd.choice([None, rnd.randint(1, 60)]), _name(rnd)) for i in range(600)])
    cur.executemany("INSERT INTO results_tf VALUES (%s,%s,%s,%s)",
                    [(i, rnd.choice([None, rnd.randint(1, 60)]), _name(rnd), rnd.choice([None, 0, 1]))
                     for i in range(600)])


def _mutate(cur, rnd):
    for _ in range(rnd.randint(5, 40)):
        op = rnd.random()
        t = rnd.choice(["athletes", "results", "results_tf"])
        idcol = "athlete_id" if t == "athletes" else "result_id"
        rid = rnd.randint(0, 700)
        if op < .4:
            cur.execute(f"UPDATE {t} SET person_id = %s WHERE {idcol} = %s",
                        (rnd.choice([None, rnd.randint(1, 80)]), rid))
        elif op < .55:
            if t == "athletes":
                cur.execute("UPDATE athletes SET first_name = %s WHERE athlete_id = %s", (rnd.choice(FIRST), rid))
            else:
                cur.execute(f"UPDATE {t} SET athlete_name = %s WHERE result_id = %s", (_name(rnd), rid))
        elif op < .65 and t == "results_tf":
            cur.execute("UPDATE results_tf SET is_relay = %s WHERE result_id = %s", (rnd.choice([0, 1]), rid))
        elif op < .75:
            cur.execute(f"DELETE FROM {t} WHERE {idcol} = %s", (rid,))
        elif op < .9:
            if t == "athletes":
                cur.execute("INSERT INTO athletes VALUES (%s,%s,%s,%s)",
                            (1000 + rid, rnd.randint(1, 80), rnd.choice(FIRST), rnd.choice(LAST)))
            elif t == "results":
                cur.execute("INSERT INTO results VALUES (%s,%s,%s)", (1000 + rid, rnd.randint(1, 80), _name(rnd)))
            else:
                cur.execute("INSERT INTO results_tf VALUES (%s,%s,%s,%s)",
                            (1000 + rid, rnd.randint(1, 80), _name(rnd), 0))
        else:   # an unrelated column: no move
            cur.execute(f"UPDATE {t} SET {idcol} = {idcol} WHERE {idcol} = %s", (rid,))


def _keysSql(cur, keys):
    cur.execute("DROP TABLE IF EXISTS lu_keys")
    cur.execute("CREATE TEMP TABLE lu_keys (nm text PRIMARY KEY)")
    for k in keys:
        cur.execute("INSERT INTO lu_keys VALUES (%s) ON CONFLICT DO NOTHING", (k,))


def test_the_registry_gives_the_full_reads_namesakes_through_every_kind_of_change():
    import psycopg2
    import name_registry as NR
    conn = psycopg2.connect(DSN)
    conn.set_client_encoding("UTF8")
    other = psycopg2.connect(DSN)          # a second writer, as another linker would be
    other.set_client_encoding("UTF8")
    try:
        checked = 0
        for seed in range(12):
            rnd = random.Random(seed)
            cur = conn.cursor()
            _seed(cur, rnd)
            conn.commit()
            assert NR.install(conn)
            for rnd_round in range(6):
                oc = other.cursor()
                _mutate(oc, rnd)
                (other.commit if rnd.random() < .8 else other.rollback)()
                _mutate(cur, rnd)
                conn.commit()
                # every name key the corpus holds now, a random subset judged
                cur.execute(f"SELECT DISTINCT nm FROM ({NR._pairsSql()}) z WHERE nm IS NOT NULL")
                allk = [r[0] for r in cur.fetchall()]
                keys = rnd.sample(allk, k=max(1, len(allk) // 2)) if allk else ["x"]
                _keysSql(cur, keys)
                cur.execute(f"SELECT person_id, nm, name FROM ({NR._pairsSql()}) z "
                            f"WHERE nm IN (SELECT nm FROM lu_keys)")
                want = sorted(cur.fetchall(), key=repr)
                NR.refresh(cur)
                cur.execute("SELECT DISTINCT person_id, nm, name FROM lu_reg "
                            "WHERE nm IN (SELECT nm FROM lu_keys)")
                got = sorted(cur.fetchall(), key=repr)
                assert got == want, (seed, rnd_round, set(want) ^ set(got))
                checked += len(want)
                (conn.commit if rnd.random() < .7 else conn.rollback)()   # a dry-run pass rolls back
            NR.uninstall(conn)
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'lu_person_move%'")
            assert cur.fetchone()[0] == 0                                 # nothing left behind
            conn.rollback()
        assert checked > 200
    finally:
        conn.rollback(); conn.close()
        other.rollback(); other.close()


def test_off_switch_leaves_gather_on_the_full_read(monkeypatch):
    import psycopg2
    import name_registry as NR
    monkeypatch.setenv("XCP_LINK_REGISTRY", "0")
    conn = psycopg2.connect(DSN)
    try:
        assert NR.install(conn) is False and not NR.ready(conn)
    finally:
        conn.close()
