"""
============================================================================
 stamp_fanout.py -- apply the inspected fan-out verdicts to person_id
============================================================================

 THE SITUATION
 -------------
 resolve_fanout.py wrote a verdict per quarantined link; the inspector
 confirmed the stamps look right. This script APPLIES them: for every
 fanout_resolutions row with decision='stamp', set person_id = anet_id
 on the tfrrs result rows of that link.

 WHY ONLY THE TFRRS SIDE IS WRITTEN
 ----------------------------------
 The canonical id is the anet_id, and anet rows were SEEDED with
 person_id = athlete_id in the schema migration -- so the anet side of
 every stamped link is already correct by construction. We stamp only
 tfrrs rows (keyed by native_id -- the proven key column; athlete_id is
 NULL on 100% of tfrrs rows). Because "correct by construction" is an
 assumption about a migration, a PREFLIGHT spot-checks it on real rows
 and aborts loudly if seeding doesn't hold.

 SAFETY PROPERTIES (same contract as merge_links.py):
   - soft: writes ONLY person_id; never deletes, never touches source data
   - reversible: NULL person_id for source='tfrrs' undoes everything
   - idempotent: the IS DISTINCT FROM guard makes re-runs no-ops
   - DRY RUN by default: counts the exact rows the real run would write
     (rows, not links -- links-processed counters are how a 43K-person
     gap hid for a week)

 Usage:
     python scripts/stamp_fanout.py               # DRY RUN, both sports
     python scripts/stamp_fanout.py --sport XC    # dry, one sport
     python scripts/stamp_fanout.py --apply       # actually write
============================================================================
"""

# ===========================================================================
# CHUNK 1: IMPORTS + CONSTANTS
# ===========================================================================

import argparse
import time

import psycopg2.extras                 # execute_values: fast bulk INSERT

from database import getConn

# Sport decides the TABLE, source decides the FILTER (§0 invariant).
SPORT_TABLE = {"XC": "results", "TF": "results_tf"}

BATCH = 20000          # stage rows per UPDATE batch (merge_links' number)
PREFLIGHT_SAMPLE = 5   # anet ids to spot-check for person_id seeding


# ===========================================================================
# CHUNK 2: LOAD -- the pairs this run will act on
# ===========================================================================

def _loadStampPairs(cur, sport):
    # -----------------------------------------------------------------
    # Purpose:  read the inspected verdicts we're applying: every
    #           decision='stamp' link for one sport, as (tfrrs_id,
    #           canon) pairs where canon = anet_id (same canonical-id
    #           convention as merge_links.py).
    # Arguments:
    #   cur   -- open cursor (one connection for the whole run --
    #            the temp stage table lives on it)
    #   sport -- 'XC' or 'TF' (UPPERCASE)
    # Output:  list of (tfrrs_id, canon) tuples. DISTINCT because a
    #          tfrrs_id appears once per link and stamp-decision links
    #          are 1-anet-per-tfrrs by construction -- but DISTINCT
    #          costs nothing and protects against a future rule change.
    # -----------------------------------------------------------------
    cur.execute(
        "SELECT DISTINCT tfrrs_id, anet_id FROM fanout_resolutions "
        "WHERE sport = %s AND decision = 'stamp'",
        (sport,),
    )
    return cur.fetchall()


# ===========================================================================
# CHUNK 3: PREFLIGHT -- verify the assumption this script stands on
# ===========================================================================

def _preflightAnetSeed(cur, sport, pairs):
    # -----------------------------------------------------------------
    # Purpose:  the one-side-only stamp is correct ONLY IF anet rows
    #           already carry person_id = athlete_id (the §3 seeding).
    #           Spot-check that on a few anet ids from this very stamp
    #           set: read their actual rows and compare. Any mismatch
    #           -> return False and the caller ABORTS before writing.
    # Arguments:
    #   cur   -- open cursor
    #   sport -- picks the results table
    #   pairs -- the (tfrrs_id, canon) list; canon IS the anet_id
    # Output:  True if every sampled anet id checks out; False + a
    #          printed explanation otherwise.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    # slice, don't sample randomly: determinism makes a failure
    # reproducible, and WHICH ids we check doesn't matter -- seeding
    # is all-or-nothing per migration, not per athlete.
    for _, canon in pairs[:PREFLIGHT_SAMPLE]:
        cur.execute(
            f"SELECT count(*), "
            # rows whose person_id is anything OTHER than the seed value;
            # IS DISTINCT FROM treats NULL as a mismatch too (<> wouldn't)
            f"       count(*) FILTER (WHERE person_id IS DISTINCT FROM athlete_id) "
            f"FROM {table} "
            f"WHERE athlete_id = %s AND source = 'anet'",
            (canon,),
        )
        total, mismatched = cur.fetchone()
        if mismatched > 0:
            print(f"  PREFLIGHT FAIL: anet {canon} has {mismatched}/{total} rows "
                  f"where person_id != athlete_id -- the seeding assumption is "
                  f"broken; a one-side stamp would leave links half-stamped. "
                  f"Investigate before applying.")
            return False
    print(f"  preflight OK: {PREFLIGHT_SAMPLE} anet ids seeded as expected")
    return True


# ===========================================================================
# CHUNK 4: THE STAMP -- stage, then batched UPDATEs (merge_links' pattern)
# ===========================================================================

def _stagePairs(cur, pairs):
    # -----------------------------------------------------------------
    # Purpose:  load the pairs into an indexed temp table so the
    #           UPDATE can join set-based instead of one statement per
    #           link. seq (bigserial: auto-incrementing) gives the
    #           batcher contiguous ranges to walk.
    # Arguments:
    #   cur   -- open cursor (temp tables live on ITS connection --
    #            this is why the whole run shares one connection)
    #   pairs -- list of (tfrrs_id, canon)
    # Output:  None -- creates + fills + indexes _fanout_stage.
    # -----------------------------------------------------------------
    cur.execute("""
        DROP TABLE IF EXISTS _fanout_stage;
        CREATE TEMP TABLE _fanout_stage (
            seq bigserial, tfrrs_key bigint, canon bigint
        )
    """)
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO _fanout_stage (tfrrs_key, canon) VALUES %s",
        pairs,
        page_size=10000,
    )
    cur.execute("CREATE INDEX _fanout_stage_seq ON _fanout_stage (seq)")


def _stampBatches(cur, conn, table, apply_mode):
    # -----------------------------------------------------------------
    # Purpose:  walk the stage in seq ranges of BATCH; per range either
    #           COUNT the rows the UPDATE would touch (dry run) or run
    #           the UPDATE and commit (apply). Reports ROWS, the
    #           counter this whole saga taught us to demand.
    # Arguments:
    #   cur, conn  -- cursor + its connection (commit per batch)
    #   table      -- 'results' or 'results_tf'
    #   apply_mode -- False = dry (count only), True = write
    # Output:  total rows (counted or updated).
    # -----------------------------------------------------------------
    cur.execute("SELECT min(seq), max(seq) FROM _fanout_stage")
    lo, hi = cur.fetchone()
    if lo is None:                     # empty stage: nothing to do
        return 0

    # the join is IDENTICAL in both modes -- the dry count is honest
    # because it is literally the same predicate the UPDATE uses.
    join_where = (
        f"FROM _fanout_stage s "
        f"WHERE r.native_id = s.tfrrs_key AND r.source = 'tfrrs' "  # §0: source explicit
        f"  AND s.seq >= %s AND s.seq < %s "
        f"  AND r.person_id IS DISTINCT FROM s.canon"               # idempotency guard
    )

    total, k = 0, lo
    while k <= hi:                     # walk [lo, hi] in BATCH-sized windows
        k2 = k + BATCH
        t0 = time.time()
        if apply_mode:
            cur.execute(f"UPDATE {table} AS r SET person_id = s.canon {join_where}",
                        (k, k2))
            n = cur.rowcount           # rows the UPDATE actually changed
            conn.commit()              # durable per batch; a crash loses one batch
        else:
            cur.execute(f"SELECT count(*) FROM {table} r {join_where.replace('FROM _fanout_stage s ', ', _fanout_stage s ', 1)}",
                        (k, k2))
            n = cur.fetchone()[0]
        total += n
        print(f"      [{time.time()-t0:5.1f}s] seq [{k:,} .. {k2:,})  {n:,} rows",
              flush=True)
        k = k2
    return total


# ===========================================================================
# CHUNK 5: POST-CHECK -- did the stamp land? (read-back, not trust)
# ===========================================================================

def _postCheck(cur, sport):
    # -----------------------------------------------------------------
    # Purpose:  after an apply: among stamp-decision links, how many
    #           tfrrs rows STILL lack the canonical person_id? Expected
    #           ~0; nonzero survivors are ids with zero rows (known,
    #           benign) or something new worth a look. This is the
    #           read-back verify_merge_landed should have been.
    # Arguments: cur; sport.
    # Output:   int -- unstamped rows remaining under stamp-decision links.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    cur.execute(
        f"SELECT count(*) FROM fanout_resolutions f "
        f"JOIN {table} r ON r.native_id = f.tfrrs_id AND r.source = 'tfrrs' "
        f"WHERE f.sport = %s AND f.decision = 'stamp' "
        f"  AND r.person_id IS DISTINCT FROM f.anet_id",
        (sport,),
    )
    return cur.fetchone()[0]


# ===========================================================================
# CHUNK 6: MAIN
# ===========================================================================

def _stampSport(cur, conn, sport, apply_mode):
    # -----------------------------------------------------------------
    # Purpose:  one sport end to end: load -> preflight -> stage ->
    #           stamp -> (post-check if applied).
    # Arguments: cur, conn; sport; apply_mode (False = dry run).
    # Output:   None -- prints the report.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    pairs = _loadStampPairs(cur, sport)
    print(f"    {len(pairs):,} stamp-decision links")
    if not pairs:
        return

    if not _preflightAnetSeed(cur, sport, pairs):
        print(f"    ABORTED {sport}: preflight failed, nothing written.")
        return

    _stagePairs(cur, pairs)
    total = _stampBatches(cur, conn, table, apply_mode)
    verb = "updated" if apply_mode else "WOULD update"
    print(f"    {verb} {total:,} rows in {table}")

    if apply_mode:
        leftover = _postCheck(cur, sport)
        print(f"    post-check: {leftover:,} unstamped rows remain "
              f"under stamp-decision links (expect ~0)")


def main():
    parser = argparse.ArgumentParser(
        description="Apply decision='stamp' fan-out verdicts to person_id.")
    parser.add_argument("--sport", choices=["XC", "TF"], default=None)
    parser.add_argument("--apply", action="store_true",
                        help="actually write (default: dry run that counts rows)")
    args = parser.parse_args()

    sports = [args.sport] if args.sport else ["XC", "TF"]
    mode = "APPLY (writing person_id)" if args.apply else "DRY RUN (counting only)"
    print(f"=== stamp fan-out verdicts -- {mode} ===")

    with getConn() as conn:            # ONE connection: the temp stage lives on it
        cur = conn.cursor()
        for sport in sports:
            print(f"\n--- {sport} ---")
            _stampSport(cur, conn, sport, args.apply)
        if not args.apply:
            conn.rollback()            # dry run leaves nothing behind, not even temps

    if not args.apply:
        print("\nDRY RUN done. If the row counts look right, re-run with --apply.")
    else:
        print("\nDone. To undo JUST this pass: NULL person_id on rows whose "
              "native_id is in fanout_resolutions stamp rows. To undo ALL "
              "dedup: NULL person_id for source='tfrrs'.")


if __name__ == "__main__":
    main()