#!/usr/bin/env python3
# Project: xc-predictor
# File:    scripts/purge_tfrrs_xc_in_tf.py
# Purpose: Remove the cross country meets that were saved into the TRACK
#          tables (owner, 2026-09-17: "xc race duplicating and being put
#          into track under name Race Results").
#
#     python scripts/purge_tfrrs_xc_in_tf.py             # counts only
#     python scripts/purge_tfrrs_xc_in_tf.py --write     # delete them
#
# ★ THE CAUSE IS FIXED IN tfrrs/driver/run_tfrrs.py. prefill_tfrrs_queue
#   seeds every TFRRS id under BOTH sports on purpose -- an id does not say
#   what it is -- and the wrong one is meant to be deleted when the page is
#   classified. _isMeetPage ignored the sport it was asked about and said
#   "yes, a real meet" to any page with cross country tables, so the TF
#   claim, which fetches the bare /results/<id> that TFRRS also serves the
#   XC meet at, handed an XC page to the track parser. This script clears
#   what that wrote; the driver stops it happening again.
#
# ⚠ THE TEST IS THE ID, NOT THE EVENT NAME. "Race Results" is what the
#   track parser made of an XC heading, but it is not the only thing it
#   made and a real track meet could carry any name. One TFRRS id is one
#   meet and one sport, so a TFRRS meet_id present in `meets` (XC) is not a
#   track meet, whatever ended up in meets_tf under it. That is exact, and
#   it needs no guess about spellings.
#
# ! ANET IS NEVER TOUCHED. anet and tfrrs number their meets independently,
#   so an id shared across the two sources means nothing at all; both sides
#   of the match are pinned to source='tfrrs'.

import sys
import argparse

sys.path.insert(0, "scripts")

from database import getConn                            # noqa: E402

_FIND = """
    SELECT mt.meet_id
    FROM   (SELECT DISTINCT meet_id FROM meets_tf WHERE source = 'tfrrs') mt
    JOIN   (SELECT DISTINCT meet_id FROM meets    WHERE source = 'tfrrs') mx
           ON mx.meet_id = mt.meet_id
"""


def find(cur):
    cur.execute(_FIND)
    return [r[0] for r in cur.fetchall()]


def counts(cur, ids):
    """(track results, track meet-divisions) sitting under those ids."""
    cur.execute("SELECT count(*) FROM results_tf "
                "WHERE source = 'tfrrs' AND meet_id = ANY(%s)", (ids,))
    n_res = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM meets_tf "
                "WHERE source = 'tfrrs' AND meet_id = ANY(%s)", (ids,))
    return n_res, cur.fetchone()[0]


def sample(cur, ids, n=15):
    cur.execute("""SELECT meet_id, meet_name, event_short, count(*) AS n
                   FROM   meets_tf
                   WHERE  source = 'tfrrs' AND meet_id = ANY(%s)
                   GROUP  BY meet_id, meet_name, event_short
                   ORDER  BY n DESC LIMIT %s""", (ids, n))
    return cur.fetchall()


def purge(conn, ids, chunk=500):
    """Delete in batches, committing each: these are big tables and a single
    transaction over all of them is a long lock for no benefit."""
    gone = {"results_tf": 0, "meets_tf": 0, "meet_queue": 0}
    with conn.cursor() as cur:
        for i in range(0, len(ids), chunk):
            batch = ids[i:i + chunk]
            cur.execute("DELETE FROM results_tf "
                        "WHERE source = 'tfrrs' AND meet_id = ANY(%s)", (batch,))
            gone["results_tf"] += cur.rowcount
            cur.execute("DELETE FROM meets_tf "
                        "WHERE source = 'tfrrs' AND meet_id = ANY(%s)", (batch,))
            gone["meets_tf"] += cur.rowcount
            # ★ AND THE QUEUE ROW, so the next run re-decides instead of
            #   reading 'done' and leaving the hole. The fixed _isMeetPage
            #   will delete it again as an event page.
            cur.execute("DELETE FROM meet_queue WHERE source = 'tfrrs' "
                        "AND sport = 'TF' AND meet_id = ANY(%s)", (batch,))
            gone["meet_queue"] += cur.rowcount
            conn.commit()
            print(f"    {min(i + chunk, len(ids)):,}/{len(ids):,} meets", flush=True)
    return gone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="actually delete (default is a dry run)")
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor() as cur:
            print("  finding TFRRS ids that are cross country meets but also "
                  "have track rows...", flush=True)
            ids = find(cur)
            if not ids:
                print("  none: nothing was written into the track tables.")
                return
            n_res, n_div = counts(cur, ids)
            print(f"  {len(ids):,} meets, {n_res:,} results_tf rows, "
                  f"{n_div:,} meets_tf rows")
            print("  the biggest, by row count:")
            for meet_id, name, ev, n in sample(cur, ids):
                print(f"    {meet_id}  {str(name)[:40]:<40} "
                      f"{str(ev)[:24]:<24} {n:,}")
        conn.rollback()
        if not args.write:
            print("  DRY RUN -- pass --write to delete.")
            return
        print("  deleting...", flush=True)
        gone = purge(conn, ids)
        print(f"  deleted: {gone}")


if __name__ == "__main__":
    main()
