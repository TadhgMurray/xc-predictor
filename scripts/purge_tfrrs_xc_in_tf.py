#!/usr/bin/env python3
# Project: xc-predictor
# File:    scripts/purge_tfrrs_xc_in_tf.py
# Purpose: Remove the cross country races that were saved into the TRACK
#          tables (owner, 2026-09-17: "xc race duplicating and being put
#          into track under name Race Results").
#
#     python scripts/purge_tfrrs_xc_in_tf.py              # evidence, no writes
#     python scripts/purge_tfrrs_xc_in_tf.py --write      # delete what it shows
#
# ⚠⚠ THIS SCRIPT HAS BEEN WRONG TWICE, IN OPPOSITE DIRECTIONS, AND THAT IS
#    WHY IT IS BUILT THE WAY IT IS.
#
#    v1 matched `meets WHERE source = 'tfrrs'` and found NOTHING, because
#    `meets` is anet-only -- TFRRS meet metadata lives in meets_tfrrs.
#
#    v2 matched "has a meets_tfrrs XC row AND has track rows" and proposed
#    deleting 9,372 meets and 5,371,425 results_tf rows -- Penn Relays, the
#    Houston ISD zone meet, "Men's 1500 Race Walk". REAL TRACK MEETS. The
#    reason is saveTFRRSMeetMeta: it writes a meets_tfrrs row whenever the
#    meta panel parses, whether or not a single result was found. So the old
#    sport-blind _isMeetPage let an XC CLAIM on a real track meet write a
#    junk meets_tfrrs sport='XC' row with no XC results at all. v2 read that
#    junk row as proof the track data was bogus and proposed deleting the
#    track meet instead. Exactly backwards.
#
# ★ SO THE TEST IS POSITIVE EVIDENCE ON THE TRACK SIDE, not the presence of
#   a row on the XC side. What a cross country page parsed as track actually
#   looks like, measured:
#     1. its meets_tf rows have NO meet name -- the track parser found no
#        meet name on a page that is not a track meet page; and
#     2. its track rows DUPLICATE cross country rows: same person, same day,
#        same time to a hundredth.
#   Meet 27037 is the case: 436 rows, both signatures, and its cross country
#   twin sitting in `results`. A real track meet has a name, so it can never
#   match (1), whatever junk sits beside it in meets_tfrrs.
#
# ! AND IT WILL NOT DELETE AT SCALE WITHOUT BEING TOLD TWICE. Over
#   MAX_MEETS meets it refuses and asks for --force, because the honest
#   answer to "my signature matched ten thousand meets" is that the
#   signature is wrong again.

import sys
import argparse

sys.path.insert(0, "scripts")

from database import getConn                            # noqa: E402

# Above this, the signature is more likely broken than the corpus.
MAX_MEETS = 200

# Signature (1): a tfrrs track meet-division with no name of its own and
# none in meets_tf_meta either. Grouped to the meet.
_CANDIDATES = """
    WITH nameless AS (
        SELECT DISTINCT m.meet_id
        FROM   meets_tf m
        LEFT   JOIN meets_tf_meta mt ON mt.meet_id = m.meet_id
        WHERE  m.source = 'tfrrs'
          AND  NULLIF(btrim(COALESCE(m.meet_name, '')), '') IS NULL
          AND  NULLIF(btrim(COALESCE(mt.meet_name, '')), '') IS NULL
    )
    SELECT n.meet_id,
           count(*)                        AS tf_rows,
           count(x.result_id)              AS dup_of_xc,
           min(t.event_short)              AS an_event,
           min(t.date)                     AS first_date
    FROM   nameless n
    JOIN   results_tf t
           ON  t.meet_id = n.meet_id AND t.source = 'tfrrs'
    LEFT   JOIN results x
           ON  x.person_id = t.person_id
           AND x.date      = t.date
           AND abs(x.time_seconds - t.time_seconds) < 0.01
           AND x.source    = 'tfrrs'
    GROUP  BY n.meet_id
    -- signature (2): at least one track row is provably a cross country row
    HAVING count(x.result_id) > 0
    ORDER  BY count(*) DESC
"""


def candidates(cur):
    cur.execute(_CANDIDATES)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def purge(conn, ids, chunk=200):
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
            #   reading 'done'. The fixed _isMeetPage deletes it as a
            #   non-meet for that sport.
            cur.execute("DELETE FROM meet_queue WHERE source = 'tfrrs' "
                        "AND sport = 'TF' AND meet_id = ANY(%s)", (batch,))
            gone["meet_queue"] += cur.rowcount
            conn.commit()
            print(f"    {min(i + chunk, len(ids)):,}/{len(ids):,} meets",
                  flush=True)
    return gone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help=f"delete even above {MAX_MEETS} meets")
    ap.add_argument("--meet", type=int, nargs="*",
                    help="only these meet ids, and only if they match")
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor() as cur:
            print("  looking for TRACK meet-divisions with no name at all "
                  "whose rows duplicate a cross country row...", flush=True)
            rows = candidates(cur)
        conn.rollback()

        if args.meet:
            keep = set(args.meet)
            skipped = [r["meet_id"] for r in rows if r["meet_id"] not in keep]
            rows = [r for r in rows if r["meet_id"] in keep]
            missing = keep - {r["meet_id"] for r in rows}
            if missing:
                print(f"  these do NOT match the signature and are left "
                      f"alone: {sorted(missing)}")
            if skipped:
                print(f"  {len(skipped):,} other matching meets ignored "
                      f"because --meet was given")

        if not rows:
            print("  nothing matches: no track rows look like a cross "
                  "country race stored twice.")
            return

        total = sum(r["tf_rows"] for r in rows)
        dups = sum(r["dup_of_xc"] for r in rows)
        print(f"  {len(rows):,} meets, {total:,} results_tf rows, "
              f"{dups:,} of them provably a cross country row")
        print("     meet_id   tf_rows  dup_of_xc  first_date  an event")
        for r in rows[:40]:
            print(f"    {r['meet_id']:>8}  {r['tf_rows']:>8,}  "
                  f"{r['dup_of_xc']:>9,}  {str(r['first_date'])[:10]:<10}  "
                  f"{str(r['an_event'])[:40]}")
        if len(rows) > 40:
            print(f"    ... and {len(rows) - 40:,} more")

        if not args.write:
            print("  DRY RUN -- read the table above, then pass --write.")
            return

        # ⚠ THE BRAKE. See the header: a signature that matches thousands of
        #   meets has been wrong every time so far.
        if len(rows) > MAX_MEETS and not args.force:
            print(f"  REFUSING: {len(rows):,} meets is more than "
                  f"{MAX_MEETS}, and a signature that broad has been wrong "
                  f"twice. Check the table above names only meets that are "
                  f"NOT real track meets, then pass --force.")
            return

        print("  deleting...", flush=True)
        gone = purge(conn, [r["meet_id"] for r in rows])
        print(f"  deleted: {gone}")


if __name__ == "__main__":
    main()
