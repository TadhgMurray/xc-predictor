#!/usr/bin/env python3
"""
diag_link_plan.py -- why link_tfrrs_to_anet's evidence pass is slow, with
plans instead of opinions.

    python scripts/diag_link_plan.py                 # plans only, no timing
    python scripts/diag_link_plan.py --analyze       # really run them
    python scripts/diag_link_plan.py --analyze --timeout 120

Reads nothing but statistics and plans. Writes nothing. --analyze EXECUTES
the queries (under a statement_timeout, so it cannot hang the terminal the
way the thing it is diagnosing did).

★ THE SUSPICION IT EXISTS TO TEST. The pass filters
`r.team_id = ANY(<2,034 college team ids>)`, and Postgres evaluates a
scalar-array comparison by walking the array FOR EVERY ROW. On a 54M-row
table that is up to 110 billion integer comparisons, single-threaded
(XCP_DB_QUIET sets max_parallel_workers_per_gather = 0). The measured
timings point straight at it: the filter took 124 s and the hash join that
reads the WHOLE table took 13 s.

If that is right, an index on team_id is not the fix and may not even be
chosen -- the fix is to put the 2,034 ids in a TABLE and let the planner
hash them, one probe per row instead of a scan of 2,034.

Both forms are printed side by side so the plans settle it.
"""
import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE, os.path.join(_ROOT, "racecast"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TABLES = ("results", "results_tf")

# the two shapes, same rows either way
_ANY_FORM = """
    SELECT DISTINCT r.person_id, substr(r.date, 1, 4)::int AS yr, r.team_id
    FROM   {table} r
    WHERE  r.person_id IS NOT NULL
      AND  r.team_id = ANY(%(teams)s)
      AND  r.date ~ '^(19|20)[0-9][0-9]-'
"""

_JOIN_FORM = """
    SELECT DISTINCT r.person_id, substr(r.date, 1, 4)::int AS yr, r.team_id
    FROM   {table} r
    JOIN   ltl_teams t ON t.team_id = r.team_id
    WHERE  r.person_id IS NOT NULL
      AND  r.date ~ '^(19|20)[0-9][0-9]-'
"""


def facts(cur):
    print("=" * 72)
    print("  THE TABLES")
    print("=" * 72)
    for table in TABLES:
        cur.execute("SELECT to_regclass(%s)", (table,))
        if cur.fetchone()[0] is None:
            print(f"  {table}: absent")
            continue
        cur.execute(f"""SELECT reltuples::bigint,
                               pg_size_pretty(pg_total_relation_size('{table}'))
                        FROM pg_class WHERE oid = '{table}'::regclass""")
        n, size = cur.fetchone()
        cur.execute("""SELECT null_frac, n_distinct FROM pg_stats
                       WHERE tablename = %s AND attname = 'team_id'""", (table,))
        row = cur.fetchone()
        if row is None:
            stat = "no statistics for team_id (never ANALYZEd, or no column)"
        else:
            null_frac, n_distinct = row
            stat = (f"team_id NULL on {100 * (null_frac or 0):.1f}% of rows, "
                    f"n_distinct {n_distinct:,.0f}")
        print(f"  {table:<12} ~{n:>14,} rows  {size:>10}   {stat}")
        cur.execute("""SELECT indexname, indexdef FROM pg_indexes
                       WHERE tablename = %s ORDER BY indexname""", (table,))
        idx = cur.fetchall()
        team_idx = [i for i, d in idx if "(team_id" in d]
        print(f"               {len(idx)} indexes; on team_id: "
              f"{team_idx or 'NONE'}")
    print()
    print("  SETTINGS THAT DECIDE THIS")
    for name in ("max_parallel_workers_per_gather", "work_mem",
                 "effective_cache_size", "random_page_cost",
                 "enable_seqscan"):
        cur.execute(f"SHOW {name}")
        print(f"    {name:<34} {cur.fetchone()[0]}")
    print()


def teamIds(cur):
    """The college team ids, exactly as the real job computes them."""
    from speed_ratings_db import loadTeamLevels
    by_team, meaning, _rows = loadTeamLevels()
    college = sorted(t for t, lv in by_team.items() if lv == "college")
    print(f"  level codes: {meaning}")
    print(f"  {len(college):,} college teams\n")
    return college


def explain(cur, label, sql, params, analyze, timeout):
    print("=" * 72)
    print(f"  {label}")
    print("=" * 72)
    what = "EXPLAIN (ANALYZE, BUFFERS, TIMING)" if analyze else "EXPLAIN"
    try:
        if analyze:
            cur.execute(f"SET LOCAL statement_timeout = '{int(timeout)}s'")
        t0 = time.time()
        cur.execute(f"{what} {sql}", params)
        for (line,) in cur.fetchall():
            print("   " + line)
        if analyze:
            print(f"   [wall {time.time() - t0:.1f}s]")
    except Exception as exc:                                  # noqa: BLE001
        print(f"   FAILED: {type(exc).__name__}: {exc}")
        try:
            cur.connection.rollback()
        except Exception:                                     # noqa: BLE001
            pass
    print()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analyze", action="store_true",
                    help="actually execute the queries and report real rows "
                         "and timings (bounded by --timeout)")
    ap.add_argument("--timeout", type=int, default=180,
                    help="seconds per query with --analyze (default 180). A "
                         "timeout is an ANSWER, not a failure.")
    ap.add_argument("--tables", default=",".join(TABLES))
    a = ap.parse_args()

    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        facts(cur)
        college = teamIds(cur)
        if not college:
            print("  no college teams -- nothing to plan")
            return 1
        # the same ids, in a table, so the planner can hash them
        cur.execute("DROP TABLE IF EXISTS ltl_teams")
        cur.execute("CREATE TEMP TABLE ltl_teams (team_id int PRIMARY KEY)")
        cur.execute("INSERT INTO ltl_teams SELECT unnest(%s::int[])",
                    (college,))
        cur.execute("ANALYZE ltl_teams")

        for table in [t.strip() for t in a.tables.split(",") if t.strip()]:
            explain(cur, f"{table}: = ANY(array of {len(college):,})  [the "
                         f"current shape]",
                    _ANY_FORM.format(table=table), {"teams": college},
                    a.analyze, a.timeout)
            explain(cur, f"{table}: JOIN a table of the same {len(college):,} "
                         f"ids  [the proposed shape]",
                    _JOIN_FORM.format(table=table), {}, a.analyze, a.timeout)
        cur.execute("DROP TABLE IF EXISTS ltl_teams")
    print("  Read the top node of each plan. What matters:")
    print("    * 'Filter: (team_id = ANY (...))' with a big 'Rows Removed by")
    print("      Filter' is the per-row array walk -- that is the fault.")
    print("    * 'Hash Join' / 'Hash Cond: (r.team_id = t.team_id)' is one")
    print("      hash probe per row instead, which is the fix.")
    print("    * A 'Parallel Seq Scan' cannot appear while")
    print("      max_parallel_workers_per_gather is 0 (XCP_DB_QUIET=1).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
