#!/usr/bin/env python3
"""
diag_track_as_xc.py -- track races filed as cross country. READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/diag_track_as_xc.py              # all four reads, top 30 each
    $P scripts/diag_track_as_xc.py --top 60

★ WHY (owner, 2026-11-10: "some big issues are tracks as xc courses. try to
  find some of these"). A track race in the XC table is rated as a course
  with a day term: a track 1600 or 3200 on a "course" the solve prices like
  grass. twin_flag already drops a meet whose NAME says track
  (xc_placeholder) and an XC row that is the same person's track row to the
  tenth on the same day (xc_track_copy). This lists what those miss:

  1. TRACK MEETS IN THE XC TABLE: an XC meet whose date and name match a
     track meet (meets_tf), so the whole meet was filed twice or filed
     wrong.
  2. A SHIFTED COPY: an XC row whose person has a track row within three
     days at the same distance (1%) and the same time (1 s): the same run,
     dated apart by the feeds.
  3. TRACK DISTANCES OFF SEASON: XC divisions at 800-3218 m run February to
     July (2020-21 left out: COVID moved the seasons), by meet.
  4. FAST VENUES AT TRACK DISTANCES: course cells at a track distance that
     rate 4% or more FASTER than a typical course -- what a track looks like
     to the solve.

Each block prints a count and the biggest examples with the rows' best
rating, so a block that is empty or all real races can be read as such.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

TRACK_M = (800, 1000, 1500, 1600, 1609, 2000, 3000, 3200, 3218)

_SQL = {
    "track meets in the XC table": """
        WITH xc AS (
            SELECT r.meet_id, left(r.date, 10) AS day, count(*) AS n, max(r.speed_rating) AS best
            FROM   results r
            WHERE  r.source = 'anet' AND r.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
            GROUP  BY 1, 2),
        tf AS (
            SELECT DISTINCT r.meet_id, left(r.date, 10) AS day
            FROM   results_tf r
            WHERE  r.source = 'anet' AND r.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}')
        SELECT xm.meet_name, xc.day, xc.n, round(xc.best::numeric, 1), tm.meet_name
        FROM   xc
        JOIN   (SELECT DISTINCT ON (meet_id) meet_id, meet_name FROM meets) xm ON xm.meet_id = xc.meet_id
        JOIN   tf ON tf.day = xc.day
        JOIN   (SELECT DISTINCT ON (meet_id) meet_id, meet_name FROM meets_tf) tm ON tm.meet_id = tf.meet_id
        WHERE  lower(regexp_replace(xm.meet_name, '[^a-z0-9]', '', 'gi'))
             = lower(regexp_replace(tm.meet_name, '[^a-z0-9]', '', 'gi'))
        ORDER  BY xc.n DESC
        LIMIT  %(top)s""",
    "XC rows that are a track row up to three days apart": """
        SELECT xm.meet_name, left(x.date, 10), x.time_seconds, round(x.speed_rating::numeric, 1),
               t.event_short, left(t.date, 10)
        FROM   results x
        JOIN   results_tf t ON t.person_id = x.person_id
                           AND abs(t.time_seconds - x.time_seconds) < 1.0
                           AND t.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                           AND abs(left(t.date, 10)::date - left(x.date, 10)::date) BETWEEN 1 AND 3
        LEFT   JOIN (SELECT DISTINCT ON (meet_id) meet_id, meet_name FROM meets) xm ON xm.meet_id = x.meet_id
        WHERE  x.person_id IS NOT NULL AND x.time_seconds > 100
          AND  x.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
        ORDER  BY x.speed_rating DESC NULLS LAST
        LIMIT  %(top)s""",
    "track distances, February to July (not 2020-21)": """
        SELECT m.meet_name, left(r.date, 10), m.distance, count(*), round(max(r.speed_rating)::numeric, 1)
        FROM   results r
        JOIN   meets m ON m.div_id = r.div_id
        WHERE  r.source = 'anet' AND r.date ~ '^[0-9]{4}-0[2-7]-'
          AND  left(r.date, 4) NOT IN ('2020', '2021')
          AND  round(CASE WHEN m.distance < 50 THEN m.distance * 1609.34 ELSE m.distance END)
               = ANY(%(track_m)s)
        GROUP  BY 1, 2, 3
        ORDER  BY 4 DESC
        LIMIT  %(top)s""",
    "course cells at a track distance rating 4%+ fast": """
        SELECT course_name, distance_m, round(difficulty::numeric, 3), n_results, n_meets
        FROM   course_rank
        WHERE  distance_m = ANY(%(track_m)s) AND difficulty <= -0.04 AND n_results >= 30
        ORDER  BY n_results DESC
        LIMIT  %(top)s""",
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args(argv)
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '20min'")
        for title, sql in _SQL.items():
            print(f"\n== {title}")
            try:
                cur.execute("SAVEPOINT q")
                cur.execute(sql, {"top": args.top, "track_m": list(TRACK_M)})
                rows = cur.fetchall()
                cur.execute("RELEASE SAVEPOINT q")
            except Exception as exc:                       # noqa: BLE001
                cur.execute("ROLLBACK TO SAVEPOINT q")
                print(f"   (not available: {type(exc).__name__}: {str(exc).splitlines()[0]})")
                continue
            if not rows:
                print("   none")
            for r in rows:
                print("   " + "  |  ".join("" if v is None else str(v) for v in r))
        conn.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
