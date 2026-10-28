"""Rule 8's never_school, set-based (owner's dry run, 2026-10-27: the
per-candidate NOT EXISTS ran four hours under enable_nestloop = off). The
new statement must answer exactly what the old one did.

    XCP_TWIN_TEST_DSN="host=... dbname=..." runs it on Postgres
"""
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(ROOT, d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import pytest                                                    # noqa: E402
import grade_sanity as GS                                        # noqa: E402
from level_graph import _raceKeyExpr                             # noqa: E402

OLD = f"""
    SELECT c.person_id
    FROM   (SELECT DISTINCT person_id FROM tmp_club_cand) c
    WHERE  NOT EXISTS (
        SELECT 1 FROM (SELECT r.meet_id, r.div_id, r.source FROM results r
                       WHERE r.person_id = c.person_id
                       UNION ALL
                       SELECT r.meet_id, r.div_id, r.source FROM results_tf r
                       WHERE r.person_id = c.person_id) r
        JOIN race_top_level rl ON rl.race = {_raceKeyExpr('r')}
        WHERE rl.top_level IN ('elem', 'ms', 'hs'))"""


@pytest.mark.parametrize("seed", range(6))
def test_set_based_never_school_matches_the_old_statement(seed):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    from psycopg2.extras import execute_values
    rnd = random.Random(seed)
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("CREATE TEMP TABLE results (person_id bigint, meet_id bigint, div_id bigint, source text)")
        cur.execute("CREATE TEMP TABLE results_tf (person_id bigint, meet_id bigint, div_id bigint, source text)")
        cur.execute("CREATE TEMP TABLE race_top_level (race bigint, top_level text)")
        cur.execute("CREATE TEMP TABLE tmp_club_cand (person_id bigint, acad int)")
        races = [(m, d, s) for m in range(1, 30) for d in (1, 2) for s in ("anet", "tfrrs")]
        for table in ("results", "results_tf"):
            execute_values(cur, f"INSERT INTO {table} VALUES %s",
                           [(rnd.randrange(1, 60),) + rnd.choice(races) for _ in range(400)])
        execute_values(cur, "INSERT INTO tmp_club_cand VALUES %s",
                       [(rnd.randrange(1, 70), rnd.choice([2024, 2025])) for _ in range(80)])
        # a level for most races (some none): the key the code hashes
        cur.execute(f"""INSERT INTO race_top_level
                        SELECT DISTINCT {_raceKeyExpr('r')},
                               (ARRAY['elem','ms','hs','college','pro'])[1 + (r.meet_id + r.div_id) % 5]
                        FROM (SELECT meet_id, div_id, source FROM results
                              UNION SELECT meet_id, div_id, source FROM results_tf) r
                        WHERE r.meet_id % 7 <> 0""")
        cur.execute("SET enable_nestloop = off")
        cur.execute(OLD)
        want = sorted(r[0] for r in cur.fetchall())
        cur.execute(GS._NEVER_SCHOOL_SQL.format(race_r=_raceKeyExpr("r")))
        got = sorted(r[0] for r in cur.fetchall())
        assert got == want and want, (seed, len(want))
    finally:
        conn.rollback()
        conn.close()
