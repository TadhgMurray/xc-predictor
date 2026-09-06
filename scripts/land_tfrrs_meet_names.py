#!/usr/bin/env python3
"""land_tfrrs_meet_names.py -- every tfrrs track row gets its meet's name.

    /srv/venv/bin/python scripts/land_tfrrs_meet_names.py            # census
    /srv/venv/bin/python scripts/land_tfrrs_meet_names.py --apply    # write

Every page that names a track meet reads meets_tf on the row's own
(div_id, meet_id, event_id, source). tfrrs rows had meets_tf rows only
where a geometry stamp existed (land_tfrrs_geometry_in_meets_tf), and
those carried no name -- so a college row rendered as "Meet results ->"
with no venue (owner, 2026-09-06: "we have the data to fix that, and
change all references"). The data is meets_tfrrs (the name, 60k track
meets) and tfrrs_meet_geometry (the venue). Rather than teach ten
queries a second table, this lands the name where they all already
look:
  1. tfrrs rows in meets_tf with no name take meets_tfrrs's;
  2. every tfrrs (div, meet, event) in results_tf with no meets_tf row
     gets one, named, with the stamp's geometry where there is one.
Idempotent; anet rows are never touched. Pipeline step 06, before the
pack (its venue keys) and the boards.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from database import getConn                          # noqa: E402

_FILL = """
    UPDATE meets_tf m
    SET    meet_name = mt.meet_name
    FROM   meets_tfrrs mt
    WHERE  m.source = 'tfrrs' AND NULLIF(btrim(m.meet_name), '') IS NULL
      AND  mt.meet_id = m.meet_id AND mt.sport = 'TF'
      AND  mt.meet_name IS NOT NULL
"""

# ! PER SOURCE. The first census joined on the key alone and found nothing
#   missing: an anet row on the same (div, meet, event) counted as present.
#   meets_tf's key has no source, so a tfrrs meet-event under a key an anet
#   row holds can NEVER have its own row -- the pages fall back to
#   _tfrrsMeetMeta for those (app.py); the rest are landed here.
_MISSING = """
    SELECT DISTINCT r.div_id, r.meet_id, r.event_id
    FROM   results_tf r
    LEFT JOIN meets_tf m ON m.div_id = r.div_id AND m.meet_id = r.meet_id
                        AND m.event_id = r.event_id AND m.source = 'tfrrs'
    WHERE  r.source = 'tfrrs' AND r.div_id IS NOT NULL AND r.event_id IS NOT NULL
      AND  m.div_id IS NULL
"""

_COLLIDING = """
    SELECT count(*) FROM (""" + _MISSING + """) x
    JOIN   meets_tf a ON a.div_id = x.div_id AND a.meet_id = x.meet_id
                     AND a.event_id = x.event_id AND a.source <> 'tfrrs'
"""

_INSERT = """
    INSERT INTO meets_tf (div_id, meet_id, event_id, source, id_system, meet_name,
                          track_type, track_length, is_indoor, location_id)
    SELECT x.div_id, x.meet_id, x.event_id, 'tfrrs',
           (SELECT min(id_system) FROM results_tf r
             WHERE r.source = 'tfrrs' AND r.div_id = x.div_id
               AND r.meet_id = x.meet_id AND r.event_id = x.event_id),
           mt.meet_name, g.track_type, g.track_length, g.is_indoor, g.location_id
    FROM  (""" + _MISSING + """) x
    LEFT JOIN meets_tfrrs mt ON mt.meet_id = x.meet_id AND mt.sport = 'TF'
    LEFT JOIN tfrrs_meet_geometry g ON g.meet_id = x.meet_id AND g.sport = 'TF'
    ON CONFLICT (div_id, meet_id, event_id) DO NOTHING
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write (default: census)")
    a = ap.parse_args()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM meets_tf WHERE source = 'tfrrs' AND NULLIF(btrim(meet_name), '') IS NULL")
        nameless = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) FROM ({_MISSING}) s")
        missing = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM meets_tfrrs WHERE sport = 'TF' AND meet_name IS NOT NULL")
        named = cur.fetchone()[0]
        print(f"  tfrrs rows in meets_tf without a name: {nameless:,}")
        cur.execute(_COLLIDING)
        colliding = cur.fetchone()[0]
        print(f"  tfrrs (div, meet, event) in results_tf with no tfrrs row in meets_tf: {missing:,}")
        print(f"    of which the key is held by an anet row (cannot be landed; "
              f"the pages fall back to meets_tfrrs): {colliding:,}")
        print(f"  meets_tfrrs track meets carrying a name: {named:,}")
        if not a.apply:
            print("  (census only; --apply writes)")
            return
        t0 = time.time()
        cur.execute(_FILL)
        print(f"  named {cur.rowcount:,} existing tfrrs rows ({time.time() - t0:.0f}s)")
        t0 = time.time()
        cur.execute(_INSERT)
        print(f"  inserted {cur.rowcount:,} tfrrs rows ({time.time() - t0:.0f}s)")
        conn.commit()
        cur.execute("SELECT count(*) FROM meets_tf WHERE source = 'tfrrs' AND NULLIF(btrim(meet_name), '') IS NULL")
        print(f"  still nameless (no meets_tfrrs name): {cur.fetchone()[0]:,}")
        cur.execute("ANALYZE meets_tf")
        conn.commit()


if __name__ == "__main__":
    main()
