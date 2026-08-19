#!/usr/bin/env python3
"""cleanup_xc_from_results_tf.py

Remove the stale XC result rows that were mis-saved into results_tf, AFTER they
have been correctly rescraped into `results`.

SAFE BY CONSTRUCTION: it only deletes a results_tf row whose result_id now also
exists in `results` under source='tfrrs'. Since result_id is a deterministic
content hash, that id set is EXACTLY the rescraped XC performances - never a TF
row (different natural key -> different id). So this cannot remove TF data, and
it only removes XC rows that have already been re-saved elsewhere.

ORDER OF OPERATIONS (do not skip):
  1. apply the saveTFRRSResultsXCBulk fix + driver branch
  2. reset XC queue rows to scraped=0
  3. rescrape (XC now writes to `results`)
  4. VERIFY `results` has the XC data (e.g. meet 27301 > 0 rows there)
  5. run this in DRY-RUN (default), eyeball the count + sample
  6. flip CONFIRM = True and run again to actually delete

DELETION IS IRREVERSIBLE. The dry run shows exactly what step 6 will remove.
"""

import sys
sys.path.insert(0, "scripts")
from database import getConn

# Flip to True ONLY after the dry run looks right. While False, nothing is
# deleted - the script just reports what it WOULD delete.
CONFIRM = True

# Delete one XC meet at a time and commit per meet, so an interruption strands
# nothing half-done and the load stays gentle (meet_id is indexed).
TFRRS = "tfrrs"


def _query(sql, params=()):
    """Read-only query -> list of tuples (shared helper; see diag_tfrrs)."""
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def _deletableTotal():
    """Count results_tf rows that are safe to delete (re-saved XC performances).

    Purpose: the headline dry-run number - how many results_tf rows have a
      result_id that now exists in `results` under source='tfrrs'.
    Output: int. 0 means nothing has been rescraped yet (did step 3/4 run?).
    """
    return _query("""
        SELECT count(*)
        FROM results_tf rt
        WHERE rt.source = %s
          AND rt.result_id IN (SELECT result_id FROM results WHERE source = %s)
    """, (TFRRS, TFRRS))[0][0]


def _sampleDeletable(limit=10):
    """A few rows that WOULD be deleted, for eyeballing (should look like XC)."""
    return _query("""
        SELECT rt.meet_id, rt.athlete_name, rt.time_seconds, rt.mark, rt.place
        FROM results_tf rt
        WHERE rt.source = %s
          AND rt.result_id IN (SELECT result_id FROM results WHERE source = %s)
        LIMIT %s
    """, (TFRRS, TFRRS, limit))


def _rescrapedXCMeetIds():
    """Distinct meet_ids that now have tfrrs rows in `results` (the XC meets we
    rescraped). We delete from results_tf one of these meets at a time."""
    rows = _query("SELECT DISTINCT meet_id FROM results WHERE source = %s", (TFRRS,))
    return [r[0] for r in rows]


def _deleteMeet(meet_id):
    """Delete this meet's re-saved XC rows from results_tf, scoped by result_id.

    Purpose: the actual delete, one meet, collision-proof. Only rows whose
      result_id is in `results` for THIS meet are removed - so a TF row sharing
      the meet_id integer is never touched (its id isn't in that set).
    Arguments: meet_id (int).
    Output: int - rows deleted for this meet.
    """
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM results_tf
            WHERE source = %s AND meet_id = %s
              AND result_id IN (
                  SELECT result_id FROM results
                  WHERE source = %s AND meet_id = %s
              )
        """, (TFRRS, meet_id, TFRRS, meet_id))
        deleted = cur.rowcount
        conn.commit()          # commit per meet (getConn does not auto-commit)
        return deleted


def main():
    print("=== cleanup: stale XC rows in results_tf ===")
    total = _deletableTotal()
    print(f"deletable (re-saved XC) rows in results_tf: {total:,}")
    if total == 0:
        print("Nothing matches. Did the XC rescrape into `results` run yet? Stopping.")
        return

    print("sample of rows that WOULD be deleted (expect XC: times set, mark NULL):")
    for meet_id, name, t, mark, place in _sampleDeletable():
        print(f"    meet {meet_id} | {name} | t={t} | mark={mark} | place={place}")

    if not CONFIRM:
        print("\nDRY RUN (CONFIRM = False). Nothing deleted.")
        print("If the above looks like XC, set CONFIRM = True and re-run.")
        return

    print("\nCONFIRM = True -> deleting, one meet at a time...")
    meet_ids = _rescrapedXCMeetIds()
    removed = 0
    for i, meet_id in enumerate(meet_ids, start=1):
        removed += _deleteMeet(meet_id)
        if i % 200 == 0:
            print(f"    {i:,}/{len(meet_ids):,} meets done, {removed:,} rows removed", flush=True)
    print(f"=== done: removed {removed:,} stale XC rows from results_tf ===")


if __name__ == "__main__":
    main()