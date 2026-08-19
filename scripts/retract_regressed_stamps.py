"""
============================================================================
 retract_regressed_stamps.py -- find (and surgically undo) stamps whose
                                verdict regressed to HOLD
============================================================================

 THE SITUATION
 -------------
 Stamps are MONOTONE: the merge and stamp_fanout only ever add person_id,
 never remove it. That was fine until fuzzy enlarged the quarantine --
 now some links stamped in round one have a round-two verdict of HOLD
 (a new fuzzy profile joined their cluster and, e.g., the co-appearance
 falsifier proved two-people). Their rows still carry the round-one
 stamp: a weld the current evidence disputes, LIVE in the data.

 This script:
   Q-R  CENSUS: hold-verdict links whose tfrrs rows carry that exact
        link's stamp -- rows and people, split by the rule that now
        holds them (co-appearance regressions are the urgent ones:
        those welds are PROVABLY wrong).
   Q-S  SAMPLES: a few with names, so the retraction is approved with
        eyes, per house rules.
   --apply: NULL person_id on exactly those rows. The scope is the
        join's equality r.person_id = f.anet_id -- a row carrying some
        OTHER link's stamp can never match, so retraction is surgical
        by construction. Idempotent: retracted rows no longer match.

 READ-ONLY without --apply. Usage:
     python scripts/retract_regressed_stamps.py
     python scripts/retract_regressed_stamps.py --sport XC --apply
============================================================================
"""

# ===========================================================================
# CHUNK 1: IMPORTS + CONSTANTS
# ===========================================================================

import argparse

from database import getConn

# Sport decides the TABLE, source decides the FILTER (§0 invariant).
SPORT_TABLE = {"XC": "results", "TF": "results_tf"}

# THE PRINCIPLE: retraction is for DISPUTED welds, not DEFERRED ones.
# Pruned spurs were held for TOPOLOGY (cut so a chained cluster could
# decompose), not because any evidence disputed them -- an exact conf-1
# spur's round-one stamp is ~99%-likely correct and stays. Every other
# hold rule (co-appearance, no-clear-winner, outmargined, complex,
# weak-no-school) is an evidential dispute -> retract.
DEFERRED_RULES = ["pruned-exact-spur", "pruned-fuzzy-spur"]


# ===========================================================================
# CHUNK 2: THE CENSUS -- how big is the regression, and of which kind
# ===========================================================================

def _regressionCensus(cur, sport, deferred):
    # -----------------------------------------------------------------
    # Purpose:  Q-R -- per hold-RULE, count the rows and people whose
    #           stamp the current verdict touches. Called TWICE per
    #           sport with the flag flipped:
    #             deferred=False -> the DISPUTED set (what --apply
    #                               retracts): co-appearance is
    #                               provably wrong; the rest are
    #                               out-evidenced or tangled.
    #             deferred=True  -> the DEFERRED-KEPT set (pruned
    #                               spurs): shown for visibility,
    #                               NEVER retracted.
    # Arguments:
    #   cur      -- open cursor
    #   sport    -- 'XC' or 'TF'; picks the results table
    #   deferred -- which of the two populations to count (above)
    # Output:  list of (rule, rows, people) tuples.
    #
    # The join, read aloud: for every HOLD verdict, find tfrrs rows
    # (keyed by native_id -- always) that carry THAT LINK's anet_id as
    # person_id. The equality on person_id is both the finder and the
    # safety scope.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    # = ANY(%s): the usual list->array adaptation. The same predicate
    # selects either population; only the NOT flips.
    rule_test = "f.rule = ANY(%s)" if deferred else "NOT (f.rule = ANY(%s))"
    cur.execute(
        f"SELECT f.rule, count(*), count(DISTINCT r.native_id) "
        f"FROM fanout_resolutions f "
        f"JOIN {table} r "
        f"  ON r.native_id = f.tfrrs_id "
        f" AND r.source = 'tfrrs' "               # §0: source in the join
        f" AND r.person_id = f.anet_id "          # carries THIS link's stamp
        f"WHERE f.sport = %s AND f.decision = 'hold' "
        f"  AND {rule_test} "
        f"GROUP BY f.rule ORDER BY f.rule",
        (sport, DEFERRED_RULES),
    )
    return cur.fetchall()


# ===========================================================================
# CHUNK 3: SAMPLES -- approve the retraction with eyes
# ===========================================================================

def _sampleRegressions(cur, sport, limit):
    # -----------------------------------------------------------------
    # Purpose:  Q-S -- a few regressed links with names and the rule
    #           that now holds them, so 'retract' is a decision made on
    #           visible cases, not a count.
    # Arguments: cur; sport; limit.
    # Output:   None -- prints.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    cur.execute(
        # LAYERED on purpose: DISTINCT dedupes INSIDE the subquery (one
        # line per link even if the athlete has many rows); the shuffle
        # happens OUTSIDE, on the already-deduplicated set. DISTINCT +
        # ORDER BY random() in ONE level is illegal in Postgres: sort
        # keys are computed per input row, so two 'duplicate' rows with
        # different random() values have no coherent collapsed order.
        f"SELECT * FROM ("
        f"  SELECT DISTINCT f.anet_id, f.tfrrs_id, f.rule, f.evidence "
        f"  FROM fanout_resolutions f "
        f"  JOIN {table} r ON r.native_id = f.tfrrs_id "
        f"                AND r.source = 'tfrrs' AND r.person_id = f.anet_id "
        f"  WHERE f.sport = %s AND f.decision = 'hold' "
        f"    AND NOT (f.rule = ANY(%s)) "         # disputed only -- what --apply acts on
        f") links "                         # subqueries in FROM must be named
        f"ORDER BY random() LIMIT %s",
        (sport, DEFERRED_RULES, limit),
    )
    for anet_id, tfrrs_id, rule, evidence in cur.fetchall():
        # names via the usual two lookups (variants caveat applies)
        cur.execute("SELECT first_name, last_name FROM athletes "
                    "WHERE athlete_id = %s LIMIT 1", (anet_id,))
        row = cur.fetchone()
        a = f"{row[0] or ''} {row[1] or ''}".strip() if row else "?"
        cur.execute(f"SELECT athlete_name FROM {table} "
                    f"WHERE native_id = %s AND source = 'tfrrs' LIMIT 1",
                    (tfrrs_id,))
        row = cur.fetchone()
        t = (row[0] if row else None) or "?"
        print(f"      {a!r:28s} <-> {t!r:28s} held by {rule} ({evidence})")


# ===========================================================================
# CHUNK 4: THE RETRACTION -- surgical by construction
# ===========================================================================

def _retract(cur, conn, sport):
    # -----------------------------------------------------------------
    # Purpose:  NULL person_id on exactly the regressed rows. One
    #           UPDATE (the population is small -- this is an edge
    #           case's cleanup, not a bulk job); the WHERE is the SAME
    #           join as the census, so what was counted is what is
    #           retracted -- no separate predicate to drift.
    # Arguments: cur, conn (one commit); sport.
    # Output:   int -- rows retracted.
    # -----------------------------------------------------------------
    table = SPORT_TABLE[sport]
    cur.execute(
        f"UPDATE {table} AS r "
        f"SET person_id = NULL "                  # the advertised undo, surgically scoped
        f"FROM fanout_resolutions f "
        f"WHERE r.native_id = f.tfrrs_id "
        f"  AND r.source = 'tfrrs' "
        f"  AND r.person_id = f.anet_id "         # ONLY the disputed weld can match
        f"  AND f.sport = %s AND f.decision = 'hold' "
        f"  AND NOT (f.rule = ANY(%s))",          # deferred spurs KEEP their stamps
        (sport, DEFERRED_RULES),
    )
    n = cur.rowcount
    conn.commit()
    return n


# ===========================================================================
# CHUNK 5: MAIN -- census (disputed + deferred) -> samples -> (retract)
# ===========================================================================

def _printCensus(census, header):
    # -----------------------------------------------------------------
    # Purpose:  one census block with its header -- pulled out because
    #           main now prints two of these per sport (disputed and
    #           deferred-kept) and the formatting must not fork.
    # Arguments: census -- list of (rule, rows, people); header -- str.
    # Output:   None -- prints.
    # -----------------------------------------------------------------
    print(f"    {header}")
    if not census:
        print("      (none)")
        return
    print(f"      {'rule':<18} {'rows':>8} {'people':>8}")
    for rule, rows, people in census:
        print(f"      {rule:<18} {rows:>8,} {people:>8,}")


def main():
    parser = argparse.ArgumentParser(
        description="Find/retract stamps whose fan-out verdict regressed to hold.")
    parser.add_argument("--sport", choices=["XC", "TF"], default=None)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--apply", action="store_true",
                        help="retract disputed welds (default: census + samples only)")
    args = parser.parse_args()

    sports = [args.sport] if args.sport else ["XC", "TF"]
    mode = "APPLY (retracting)" if args.apply else "CENSUS ONLY"
    print(f"=== regressed-stamp check -- {mode} ===")

    with getConn() as conn:
        cur = conn.cursor()
        for sport in sports:
            print(f"\n--- {sport} ---")
            disputed = _regressionCensus(cur, sport, deferred=False)
            deferred = _regressionCensus(cur, sport, deferred=True)
            _printCensus(disputed, "DISPUTED (what --apply retracts):")
            _printCensus(deferred, "DEFERRED-KEPT (pruned spurs; stamps stay):")
            if not disputed:
                continue
            print("    samples (disputed only):")
            _sampleRegressions(cur, sport, args.samples)

            if args.apply:
                n = _retract(cur, conn, sport)
                print(f"    retracted {n:,} rows")
                left = _regressionCensus(cur, sport, deferred=False)
                print(f"    post-check: {sum(r[1] for r in left):,} disputed rows remain "
                      f"(expect 0)")

    if not args.apply:
        print("\nCENSUS done. co-appearance regressions are provably-wrong welds; "
              "if the samples look right, re-run with --apply.")


if __name__ == "__main__":
    main()