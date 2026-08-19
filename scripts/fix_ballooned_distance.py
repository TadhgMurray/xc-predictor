# Project: xc-predictor
# Author:  Tadhg Murray
# File:    scripts/fix_ballooned_distance.py
# Purpose: REPAIR the handful of XC divisions whose meets.distance is a canonical
#          distance ballooned by *1609.344 (a metre value wrongly converted as if
#          it were miles). The census (census_ballooned_distance.py) SIZED this at
#          19 divisions / 185 results; this script FIXES it at the source.
#
#   WHY FIX THE SOURCE (meets) AND NOT THE SYMPTOM (results)
#   -------------------------------------------------------
#   The hierarchy is one-to-many:
#
#         meets (div_id PK, distance)          <-- 19 corrupt rows (the CAUSE)
#            |  1
#            |  N
#         results (div_id FK)                  <-- 185 rows inherit it (the SYMPTOM)
#
#   Correcting the 19 meets rows means every result under them resolves to the
#   right distance on the NEXT normalize backfill — no per-result write, and the
#   distance FITTER (which also reads meets.distance) stops ingesting the poison
#   too. One write site, whole blast radius healed. This is option (a) from the
#   census discussion: repair at the source.
#
#   SAFETY MODEL (same as the backfill)
#   -----------------------------------
#   DRY-RUN BY DEFAULT: prints the exact before/after rows and writes nothing.
#   --apply performs the UPDATEs inside ONE transaction (19 rows — trivially
#   atomic). Re-running after a successful apply is a NO-OP: the corrected values
#   are no longer ballooned, so _unBalloon returns None for them and they are not
#   selected. Idempotent by construction, same as every writer in this project.
#
#   RUN:
#       python scripts/fix_ballooned_distance.py            # dry run (default)
#       python scripts/fix_ballooned_distance.py --apply    # write the 19 rows

import sys
import argparse

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# THE DETECTOR  —  identical logic to the census (one idea, one home).
#   A division is corrupt iff its stored distance divided by the mile
#   factor lands on a canonical XC distance; that quotient is the truth.
#   Duplicated here deliberately as a SELF-CONTAINED script rather than
#   cross-importing the census — but the constants and helper MUST stay
#   in sync with census_ballooned_distance.py (!! KEEP IN SYNC !!).
# ================================================================== #

_CANONICAL_XC = (3000.0, 3200.0, 4000.0, 5000.0, 6000.0, 8000.0, 10000.0)
_MILE = 1609.34          # detector factor; see note below on the 1609.344 data
_REL_TOL = 0.01          # relative tolerance; absorbs rounding in the stored value


def _unBalloon(distance):
    """
    Purpose : if `distance` is a canonical XC distance ballooned by the mile
              factor, return the recovered TRUE canonical; else None.
    Argument: distance — a raw meets.distance value (metres; may be None/garbage).
    Output  : recovered canonical distance (float) or None.

    NOTE ON THE CONSTANT: the data was ballooned by the EXACT mile 1609.344
    (5000*1609.344 = 8046720 exactly); we divide by 1609.34. That 0.004
    discrepancy shifts the QUOTIENT by ~0.0002%, far inside _REL_TOL, so the
    match still fires — and crucially we RETURN THE CANONICAL `c`, not the
    quotient `q`, so the recovered value is exactly 5000.0, not 4999.99. The
    detector's imprecision cannot leak into the value we write.

    Syntax:
      - guard None / non-positive so the division is always meaningful.
      - q = distance / _MILE undoes the erroneous mile multiply.
      - next((c for c in ... if <relative error small>), None) returns the FIRST
        canonical q matches, else None (find-first-or-nothing idiom).
    """
    if distance is None or distance <= 0:
        return None
    q = distance / _MILE
    return next((c for c in _CANONICAL_XC
                 if abs(q - c) / c <= _REL_TOL), None)


# ================================================================== #
# FIND  —  load the corrupt divisions (the write list).
#   Small helper: one query, classify in Python with the shared detector,
#   return only the rows we intend to touch, each carrying old + new.
# ================================================================== #

def _findCorruptDivisions(cur):
    """
    Purpose : scan meets and return every division that needs correcting.
    Argument: cur — an open read cursor.
    Output  : a list of (div_id, old_distance, recovered_distance) tuples — ONLY
              the corrupt divisions (clean ones are filtered out).

    Mechanics:
      - `SELECT div_id, distance FROM meets` : one full read (meets is small and
        anet-only; cheap).
      - for each row, _unBalloon(distance) is None for clean rows (skipped) or the
        recovered canonical for corrupt rows (kept, with its old value alongside
        so the report and the UPDATE both have the before/after pair).
    """
    cur.execute("SELECT div_id, distance FROM meets")
    corrupt = []
    for div_id, distance in cur.fetchall():
        recovered = _unBalloon(distance)
        if recovered is not None:
            corrupt.append((div_id, distance, recovered))
    return corrupt


# ================================================================== #
# BLAST RADIUS  —  count affected results (for the report only).
#   Not needed to perform the fix, but printed so the operator sees how
#   many result rows this correction will (eventually) heal.
# ================================================================== #

def _countAffectedResults(cur, corrupt_div_ids):
    """
    Purpose : per corrupt division, how many result rows sit under it.
    Arguments: cur — read cursor; corrupt_div_ids — list of the poisoned div_ids.
    Output  : { div_id : result_count } (a division with no results is absent).

    Syntax:
      - `WHERE div_id = ANY(%s)` : psycopg2 adapts a Python list to a Postgres
        array; `= ANY(array)` is set membership — ONE indexed query, not N.
      - `GROUP BY div_id` + `COUNT(*)` : one count per division in a single pass.
    """
    if not corrupt_div_ids:
        return {}
    cur.execute("""
        SELECT div_id, COUNT(*)
        FROM results
        WHERE div_id = ANY(%s)
        GROUP BY div_id
    """, (list(corrupt_div_ids),))
    return {div_id: n for div_id, n in cur}


# ================================================================== #
# REPORT  —  print exactly what will change, before changing it.
# ================================================================== #

def _printPlan(corrupt, results_by_div, apply):
    """
    Purpose : show the operator the before/after for every division we will touch,
              and the total result blast radius, BEFORE any write.
    Arguments: corrupt — (div_id, old, new) list; results_by_div — counts;
               apply — True if we are about to write (affects the header verb).
    Output  : None (prints).

    Mechanics: sort by blast radius descending so the most impactful correction
    reads first; sum the counts for a headline total.
    """
    mode = "APPLY" if apply else "DRY RUN"
    total_results = sum(results_by_div.get(d, 0) for d, _o, _n in corrupt)
    print("=" * 70)
    print(f"FIX BALLOONED meets.distance  —  {mode}")
    print("=" * 70)
    print(f"divisions to correct : {len(corrupt):>6,}")
    print(f"results healed (next backfill): {total_results:>6,}")
    print("-" * 70)
    for div_id, old, new in sorted(corrupt,
                                   key=lambda t: results_by_div.get(t[0], 0),
                                   reverse=True):
        rows = results_by_div.get(div_id, 0)
        print(f"  div={div_id:<12}  {old:>14.1f}m  ->  {new:>7.0f}m  "
              f"({rows:,} results)")


# ================================================================== #
# WRITE  —  one UPDATE per division, all inside ONE transaction.
#   19 rows: no batching needed. execute is fine; the transaction makes
#   it all-or-nothing so a mid-run failure leaves meets untouched.
# ================================================================== #

def _applyFix(conn, corrupt):
    """
    Purpose : write the corrected distances into meets, atomically.
    Arguments: conn — a write connection; corrupt — (div_id, old, new) list.
    Output  : number of rows updated (int).

    Mechanics / syntax:
      - We use a single cursor and loop the (new, div_id) updates; with only 19
        rows the per-row round trip is negligible and the code stays obvious.
      - `WHERE div_id = %s` targets the PK — exactly one row per statement.
      - `conn.commit()` ONCE at the end makes the whole set one transaction: if
        any statement raised, we would never reach commit and Postgres discards
        the lot. rowcount is summed as a written-count sanity check.
      - We pass `new` (the canonical float) as the value — NOT the quotient — so
        the stored value is exactly 5000.0 etc.
    """
    cur = conn.cursor()
    written = 0
    for div_id, _old, new in corrupt:
        cur.execute("UPDATE meets SET distance = %s WHERE div_id = %s",
                    (new, div_id))
        written += cur.rowcount
    conn.commit()
    return written


# ================================================================== #
# ORCHESTRATION
# ================================================================== #

def _run(conn, apply):
    """
    Purpose : find -> report -> (optionally) write, in that order.
    Arguments: conn — a connection; apply — write iff True.
    Output  : None (prints; writes only under --apply).

    The read (find + count) uses a plain cursor; the write, if any, uses the
    same connection since there is no concurrent server-side stream to conflict
    with (unlike the backfill, which needs two connections).
    """
    with conn.cursor() as cur:
        corrupt = _findCorruptDivisions(cur)
        corrupt_ids = [d for d, _o, _n in corrupt]
        results_by_div = _countAffectedResults(cur, corrupt_ids)

    _printPlan(corrupt, results_by_div, apply)

    if not corrupt:
        print("-" * 70)
        print("nothing to fix (no ballooned divisions found).")
        return

    if apply:
        written = _applyFix(conn, corrupt)
        print("-" * 70)
        print(f"APPLIED: {written:,} meets rows corrected.")
    else:
        print("-" * 70)
        print("DRY RUN: no rows written. Re-run with --apply to write.")


def main():
    ap = argparse.ArgumentParser(
        description="Fix *1609.344 ballooned meets.distance rows (XC).")
    ap.add_argument("--apply", action="store_true",
                    help="write the corrections (default: dry run, writes nothing).")
    args = ap.parse_args()

    initPool()
    conn = getConn()
    if not hasattr(conn, "cursor") and hasattr(conn, "__enter__"):
        conn = conn.__enter__()

    _run(conn, args.apply)


if __name__ == "__main__":
    main()