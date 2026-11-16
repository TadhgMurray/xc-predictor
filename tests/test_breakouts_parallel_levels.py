"""racecast/build_breakouts (2026-11-16): the levels scored side by side on
their own sessions give the same breakout_rows / breakout_meta as the one
session did. The scoring itself is stood in for (each level writes its own
rows through its own temp table, as _level does); this checks the
plumbing -- the committed shadow, per-session temp tables, the meta order,
the swap. Needs a scratch Postgres: XCP_TEST_PG_DSN (skipped without one)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, d) for d in ("racecast", "scripts", "engine")]
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
DSN = os.environ.get("XCP_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")


def _run(monkeypatch, jobs):
    import psycopg2
    import database
    import build_breakouts as BB
    cfg = dict(database._PG_CONFIG_WITH_TIMEOUT)
    cfg.update(dict(kv.split("=", 1) for kv in DSN.split()))
    monkeypatch.setattr(database, "_PG_CONFIG_WITH_TIMEOUT", cfg)
    database._pool = None
    database._slots = None
    monkeypatch.setenv("XCP_BREAKOUT_JOBS", str(jobs))
    monkeypatch.setattr(sys, "argv", ["build_breakouts.py"])
    monkeypatch.setattr(BB, "_unitCols", lambda cur: ([], False))
    monkeypatch.setattr(BB.B, "latestDates", lambda cur: {})
    monkeypatch.setattr(BB.B, "seasonFor", lambda sport, latest: 2026)
    monkeypatch.setattr(BB.B, "LEVELS", {"hs-boys": ("hs_m", 0), "hs-girls": ("hs_f", 0),
                                          "ms-boys": ("ms_m", 0), "ms-girls": ("ms_f", 0)})

    def fake_level(cur, sport, level, year, units, has_kind):
        import datetime
        cur.execute("DROP TABLE IF EXISTS bo_season")
        cur.execute("CREATE TEMP TABLE bo_season AS SELECT g AS i FROM generate_series(1, 40) g")
        seed = (sum(map(ord, sport + level)) % 7) + 1
        cur.execute("""INSERT INTO breakout_rows_new (kind, sport, level, year, result_id, person_id,
                                                      race_date, jump)
                       SELECT CASE WHEN i %% 3 = 0 THEN 'pr' ELSE 'jump' END, %s, %s, %s,
                              i * 100 + %s, i, DATE '2026-10-01' + i, i * %s FROM bo_season""",
                    (sport, level, year, seed, seed))
        n = cur.rowcount
        return n, 0, datetime.date(2026, 11, seed)
    monkeypatch.setattr(BB, "_level", fake_level)
    BB.main()
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute("SELECT * FROM breakout_rows ORDER BY sport, level, result_id")
    rows = cur.fetchall()
    cur.execute("SELECT sport, level, year, anchor FROM breakout_meta ORDER BY sport, level")   # not its build time
    meta = cur.fetchall()
    conn.close()
    database._pool = None
    return rows, meta


def test_side_by_side_levels_write_what_one_session_wrote(monkeypatch):
    one = _run(monkeypatch, 1)
    four = _run(monkeypatch, 4)
    assert one[0] and one == four
