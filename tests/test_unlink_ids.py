"""unlink.py numbers split people above every minted range (2026-09-29): max()+1
fell inside the DirectAthletics mint range, where a later mint could reuse it."""
import os
import re
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))


def test_split_base_is_above_every_mint():
    import link_tfrrs_rows as L
    src = open(os.path.join(_ROOT, "engine", "unlink.py")).read()
    base = int(re.search(r"SPLIT_BASE = ([0-9_]+)", src).group(1).replace("_", ""))
    top = max(L.MINT_BASE.values()) + L.MINT_MAX_NATIVE
    assert base >= top, (base, top)
    assert "WHERE person_id >= %(b)s" in src and "max(int(cur.fetchone()[0]) + 1, SPLIT_BASE)" in src


def _season(season, cls, school, n, name):
    return (season, cls, None if cls is None else "x", school, n, name)


def test_class_year_only_from_school_rows():
    import unlink
    q = unlink.classYearsSql()
    assert "unattached" in q and "{SCHOOL_SHAPED}" not in q and "{ONLY}" not in q
    assert "IN (SELECT person_id FROM rj_ids)" in unlink.classYearsSql("rj_ids")


def test_rejoin_pairs():
    import unlink
    # Jack Falkowski (owner, 2026-10-12): school seasons class of 2025; the
    # unattached season now carries no class year -> one person, rejoined
    hs = [_season(y, 2025, "Christian Brothers Academy", 6, "Jack Falkowski")
          for y in (2022, 2023, 2024, 2025)]
    un = [_season(2026, None, "Unattached", 4, "Jack Falkowski")]
    got = unlink.rejoinPairs({10: hs, 20: un}, [(10, 20, [2026])])
    assert got == [(10, 20, [2026])]
    # a different name on the split side -> left apart
    other = [_season(2026, None, "Unattached", 4, "Brett Smith")]
    assert unlink.rejoinPairs({10: hs, 20: other}, [(10, 20, [2026])]) == []
    # a school-graded split the class years still separate -> left apart
    kid = [_season(2026, 2031, "Some Middle School", 4, "Jack Falkowski")]
    assert unlink.rejoinPairs({10: hs, 20: kid}, [(10, 20, [2026])]) == []
    # one side gone -> nothing
    assert unlink.rejoinPairs({10: hs}, [(10, 20, [2026])]) == []


def test_rejoin_on_postgres():
    """The whole --rejoin --write round trip, and its undo, on a scratch
    Postgres (XCP_TWIN_TEST_DSN)."""
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import importlib
    psycopg2 = importlib.import_module("psycopg2")
    import unlink
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("""
        DROP TABLE IF EXISTS results, results_tf, person_split, person_link_log;
        CREATE TABLE results (result_id bigint, person_id bigint, date text,
                              grade text, school text, athlete_name text);
        CREATE TABLE results_tf (LIKE results);
        CREATE TABLE person_split (old_person_id bigint, season int,
                                  new_person_id bigint, class_year int, school text,
                                  PRIMARY KEY (old_person_id, season));""")
    rows = [(i, 10, f"{y}-10-01", str(g), "Christian Brothers Academy", "Jack Falkowski")
            for i, (y, g) in enumerate(((2021, 9), (2022, 10), (2023, 11), (2024, 12)), 1)]
    # the split-off unattached season, and a real second person split the same way
    rows += [(11, 2000000001, "2026-02-01", "9", "Unattached", "Jack Falkowski"),
             (12, 2000000001, "2026-03-01", "9", "Unattached", "Jack Falkowski"),
             (21, 30, "2021-10-01", "12", "Crater", "Sam Lee"),
             (22, 2000000002, "2026-10-01", "7", "Hanby Middle", "Sam Lee")]
    cur.executemany("INSERT INTO results VALUES (%s,%s,%s,%s,%s,%s)", rows)
    cur.executemany("INSERT INTO person_split VALUES (%s,%s,%s,NULL,NULL)",
                    [(10, 2026, 2000000001), (30, 2026, 2000000002)])
    assert unlink.rejoin(cur, do_write=True) == 1
    cur.execute("SELECT result_id, person_id FROM results WHERE result_id IN (11, 12, 22) ORDER BY 1")
    assert cur.fetchall() == [(11, 10), (12, 10), (22, 2000000002)]
    cur.execute("SELECT old_person_id FROM person_split")
    assert cur.fetchall() == [(30,)]
    assert unlink.rejoin(cur, do_write=True) == 0, "idempotent"
    unlink.rejoinUndo(cur)
    cur.execute("SELECT person_id FROM results WHERE result_id = 11")
    assert cur.fetchone() == (2000000001,)
    cur.execute("SELECT count(*) FROM person_split")
    assert cur.fetchone() == (2,)
    conn.rollback()
    conn.close()
