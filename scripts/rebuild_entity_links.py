#!/usr/bin/env python3
# Project: xc-predictor
# File:    dedup/rebuild_entity_links.py
# Purpose: Re-threshold the dedup at ANY shared-finisher gate WITHOUT re-running
#          the expensive match. The build (build_entity_links.py) does the costly
#          part -- loading ~100M rows and matching name+time -- and now persists
#          its raw output to athlete_meet_match. THIS script reads only that small
#          intermediate and rebuilds entity_links at whatever gate you pass. A
#          gate change drops from "a full dedup" to "one GROUP BY".
#
#          THE IDEA (before the mechanics):
#          athlete_meet_match has one row per (athlete-pair, meet-pair) that the
#          matcher found agreeing. Everything entity_links contained is derivable
#          from it by two aggregations:
#
#            1. CONFIRM MEETS: a meet pair is "real" if >= GATE distinct athlete
#               pairs share it. Count athletes per meet pair; keep those >= gate.
#            2. SCORE ATHLETES: an athlete pair's confidence is how many DISTINCT
#               confirmed meet pairs it appears in. Join the athlete rows to the
#               confirmed meets and count.
#
#          That's exactly what the Python build did at the end -- just expressed
#          as SQL over the saved rows, so it costs seconds and can be re-run at
#          gate=5, 4, 3, ... to compare recall against precision.
#
#          NON-DESTRUCTIVE to source tables: it only rewrites entity_links (the
#          derived link table), never results / results_tf / athletes.

import argparse
import sys

sys.path.insert(0, "scripts")
from database import getConn

USE_FUZZY = False


# ================================================================== #
# CHUNK 1 -- DB ACCESS (all SQL funnels through here)
# ================================================================== #


# _query
# Purpose: Read-only query -> rows. Used for the dry-run counts.
# Arguments: sql (named %(...)s placeholders), params dict.
# Output:  list of tuples.
def _query(sql, params=None):
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params or {})
        return cur.fetchall()


# _executeTx
# Purpose: Run several statements in ONE transaction (so entity_links is never
#          left empty mid-rebuild). Commits once at the end.
# Arguments: statements -- list of (sql, params) pairs, run in order.
# Output:  none.
def _executeTx(statements):
    with getConn() as conn:
        cur = conn.cursor()
        for sql, params in statements:
            cur.execute(sql, params or {})
        conn.commit()


# ================================================================== #
# CHUNK 2 -- THE REBUILD SQL (the two aggregations, as reusable CTEs)
# ================================================================== #
#
# Both the dry-run count and the live rebuild share the SAME CTE chain, so the
# numbers you preview are exactly the numbers you'd write -- no chance of the
# preview and the action diverging.


# _cteChain
# Purpose: Build the shared CTE prefix that turns athlete_meet_match into
#          confirmed meets + scored athletes at a given gate.
# Arguments: none (the gate is bound as a %(gate)s param by the caller).
# Output:  a SQL string ending after the last CTE (no trailing SELECT/INSERT),
#          ready to have a SELECT or INSERT...SELECT appended.
def _cteChain():
    return """
        WITH am AS (
            -- DISTINCT collapses an athlete who matched twice in the same meet
            -- (e.g. ran two events) to ONE (athlete-pair, meet-pair) row, so the
            -- counts below mean exactly "distinct athletes" / "distinct meets".
            SELECT DISTINCT sport, anet_id, tfrrs_id, anet_meet, tfrrs_meet
            FROM athlete_meet_match
        ),
        meet_shared AS (                       -- step 1: athletes per meet pair
            SELECT sport, anet_meet, tfrrs_meet, count(*) AS shared
            FROM am GROUP BY sport, anet_meet, tfrrs_meet
        ),
        confirmed AS (                         -- meets that clear the gate
            SELECT sport, anet_meet, tfrrs_meet, shared
            FROM meet_shared WHERE shared >= %(gate)s
        ),
        athlete_conf AS (                      -- step 2: confirmed meets per athlete
            SELECT am.sport, am.anet_id, am.tfrrs_id, count(*) AS confidence
            FROM am
            JOIN confirmed c USING (sport, anet_meet, tfrrs_meet)
            -- mirror the build: a null id on either side can't be a link.
            WHERE am.anet_id IS NOT NULL AND am.tfrrs_id IS NOT NULL
            GROUP BY am.sport, am.anet_id, am.tfrrs_id
        )
    """


# ================================================================== #
# CHUNK 3 -- DRY RUN (count what the gate would produce)
# ================================================================== #


# _previewCounts
# Purpose: Show, per sport, how many meet and athlete links a given gate yields,
#          so you can compare gates before committing one.
# Arguments: gate (int).
# Output:  none (prints).
def _previewCounts(gate):
    print(f"  gate = {gate}: links this gate WOULD produce")
    meets = _query(_cteChain() + """
        SELECT sport, count(*) FROM confirmed GROUP BY sport ORDER BY sport
    """, {"gate": gate})
    aths = _query(_cteChain() + """
        SELECT sport, count(*) FROM athlete_conf GROUP BY sport ORDER BY sport
    """, {"gate": gate})
    for sport, n in meets:
        print(f"    {sport}  meet links:    {n:>10,}")
    for sport, n in aths:
        print(f"    {sport}  athlete links: {n:>10,}")


# ================================================================== #
# CHUNK 4 -- LIVE REBUILD (truncate + re-insert entity_links)
# ================================================================== #
#
# A gate change fully REDEFINES the link set (lowering adds rows; raising removes
# them), so the honest rebuild is truncate-then-insert, not upsert -- upsert would
# leave stale rows behind when the gate rises. Both happen in one transaction.


# _rebuildSql
# Purpose: The single INSERT that writes BOTH link kinds from the CTE chain via
#          UNION ALL (one statement, so it shares the chain and one plan).
# Output:  SQL string.
def _rebuildSql():
    return _cteChain() + """
        INSERT INTO entity_links
            (entity_type, sport, anet_id, tfrrs_id, confidence, method)
        SELECT 'meet', sport, anet_meet, tfrrs_meet, shared, 'shared-results'
        FROM confirmed
        UNION ALL
        SELECT 'athlete', sport, anet_id, tfrrs_id, confidence, 'confirmed-meet-finisher'
        FROM athlete_conf
        ON CONFLICT (entity_type, sport, anet_id, tfrrs_id)
            DO UPDATE SET confidence = EXCLUDED.confidence
    """


# _rebuild
# Purpose: Clear entity_links and re-insert it at the chosen gate, in one
#          transaction so the table is never observed empty by another reader.
# Arguments: gate (int), truncate (bool).
# Output:  none (prints).
def _rebuild(gate, truncate):
    steps = []
    if truncate:
        steps.append(("TRUNCATE entity_links", None))
    steps.append((_rebuildSql(), {"gate": gate}))
    _executeTx(steps)
    print(f"  rebuilt entity_links at gate = {gate} "
          f"({'truncated first' if truncate else 'upsert, no truncate'}).")


# ================================================================== #
# CHUNK 5 -- CLI / DRIVER
# ================================================================== #


# _parseArgs
# Purpose: Every knob a flag. --gate is the new shared-finisher minimum; --apply
#          is the safety latch (defaults to dry-run, like the merge script).
# Output:  parsed args.
def _parseArgs():
    p = argparse.ArgumentParser(
        description="Rebuild entity_links at any gate from athlete_meet_match.")
    p.add_argument("--gate", type=int, required=True,
                   help="shared-finisher minimum to confirm a meet (was 5).")
    p.add_argument("--apply", action="store_true",
                   help="actually rewrite entity_links (default: dry-run counts only).")
    p.add_argument("--no-truncate", action="store_true",
                   help="upsert instead of truncate+insert (only safe when LOWERING the gate).")
    return p.parse_args()


# main
# Purpose: Preview the gate's output; rewrite entity_links only with --apply.
# Output:  none.
def main():
    args = _parseArgs()
    mode = "APPLY (rewriting entity_links)" if args.apply else "DRY RUN (counts only)"
    print(f"=== rebuild entity_links -- {mode} ===\n")

    _previewCounts(args.gate)

    if not args.apply:
        print("\nDRY RUN done. Re-run with --apply to write this gate.")
        return

    print()
    _rebuild(args.gate, truncate=not args.no_truncate)
    print("\n=== done. Inspect with inspect_entity_links.py before merging. ===")


if __name__ == "__main__":
    main()