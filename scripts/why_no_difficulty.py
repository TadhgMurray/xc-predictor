#!/usr/bin/env python3
"""
why_no_difficulty.py -- every race on an athlete page with an empty Course
column, and why it is empty. READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/why_no_difficulty.py https://racecast.co/athlete/23975715

(owner, 2026-10-24: "a disgusting amount of difficulty columns not filled in
random places".) The page's own joins (app._xc_cell_join, the TF venue cell)
asked step by step, so each blank gets the first step that failed:

  XC  distance corrected   dist_override moved the division: blank on purpose
      no course name       neither meets.course_name nor meets_tfrrs.venue_name
      name not a course    no course_canonical row by that name (the solve
                           never saw it under that spelling)
      no cell at distance  the course is known, not at this distance (the
                           distances it has are listed)
  TF  field event          no difficulty by design
      no venue             no location_id (anet meets_tf / tfrrs geometry)
      venue not solved     a location with no TF:loc cell
Then a count per reason.
"""
import re
import sys
import os
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

_XC = """
    SELECT r.result_id, r.date::text, r.source, r.meet_id, r.div_id,
           COALESCE(m.meet_name, mt.meet_name) AS meet,
           {course} AS course, ({dist})::real AS dist,
           dov.distance AS dov_dist, COALESCE(m.distance, {blob}::real) AS listed,
           cd.difficulty AS shown, r.speed_rating
    FROM   results r
    LEFT JOIN meets m ON m.div_id = r.div_id AND m.meet_id = r.meet_id AND m.source = r.source
    {tfrrs}{dov}
    {cell}
    WHERE  r.person_id = %(p)s AND r.time_seconds IS NOT NULL
    ORDER  BY r.date DESC
"""

_TF = """
    SELECT r.result_id, r.date::text, r.source, r.meet_id, r.event_short,
           COALESCE(r.is_field, 0) AS is_field,
           COALESCE(m.location_id, g.location_id) AS loc,
           COALESCE(m.is_indoor, g.is_indoor, 0) AS indoor,
           cd.difficulty AS shown
    FROM   results_tf r
    LEFT JOIN LATERAL (SELECT m.location_id, m.is_indoor FROM meets_tf m
                       WHERE m.meet_id = r.meet_id AND m.source = r.source LIMIT 1) m ON TRUE
    LEFT JOIN tfrrs_meet_geometry g ON r.source = 'tfrrs' AND g.meet_id = r.meet_id AND g.sport = 'TF'
    LEFT JOIN course_difficulties cd
           ON cd.course_name = 'TF:loc:' || COALESCE(m.location_id, g.location_id)::text ||
              CASE WHEN COALESCE(m.is_indoor, g.is_indoor, 0) = 1 THEN ':in' ELSE ':out' END
    WHERE  r.person_id = %(p)s AND (r.time_seconds IS NOT NULL OR r.mark IS NOT NULL)
    ORDER  BY r.date DESC
"""


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    pid = int(re.search(r"(\d+)\s*$", sys.argv[1].rstrip("/")).group(1))
    import app as A
    from database import getConn
    xc_sql = _XC.format(course=A._xc_course_sql("r"), dist=A._xc_distance_sql("r"),
                        blob=A._blob("r"), tfrrs=A._tfrrs_join("r"),
                        dov=A._dist_override_join("r"), cell=A._xc_cell_join("r"))
    why = Counter()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(xc_sql, {"p": pid})
        xc = cur.fetchall()
        cur.execute(_TF, {"p": pid})
        tf = cur.fetchall()
        print(f"\n== person {pid}: {len(xc)} XC rows, {len(tf)} TF rows\n")
        for rid, d, src, mid, did, meet, course, dist, dov, listed, shown, sr in xc:
            corrected = dov is not None and listed is not None and abs(float(dov) - float(listed)) >= 1
            if shown is not None and not corrected:     # the page blanks a corrected one
                continue
            if corrected:
                reason = "XC distance corrected (blank on purpose)"
                extra = f"listed {listed:g} m, rated at {float(dov):g} m"
            elif not course:
                reason, extra = "XC no course name", ""
            else:
                cur.execute("SELECT canonical_id FROM course_canonical WHERE course_name = %s", (course,))
                cids = [c for (c,) in cur.fetchall()]
                if not cids:
                    cur.execute("""SELECT course_name FROM course_canonical
                                   WHERE lower(btrim(course_name)) = lower(btrim(%s)) LIMIT 3""", (course,))
                    near = [n for (n,) in cur.fetchall()]
                    reason = "XC name not a course"
                    extra = f"{course!r}" + (f"; differs only in case/spaces from {near}" if near else "")
                else:
                    cur.execute("""SELECT distance_m, round(100 * difficulty::numeric, 1), n_results
                                   FROM course_difficulties WHERE canonical_id = ANY(%s)
                                   ORDER BY distance_m""", (cids,))
                    cells = cur.fetchall()
                    reason = "XC no cell at distance"
                    extra = (f"{course!r} at {dist:g} m; cells: " if dist else f"{course!r}, no distance; cells: ") + \
                        (", ".join(f"{m}m {p}% (n={n})" for m, p, n in cells) or "none")
            why[reason] += 1
            print(f"  {d[:10]}  {src:5} {mid}/{did}  {(meet or '')[:40]:40}  rated {sr}  -> {reason}: {extra}")
        for rid, d, src, mid, ev, field, loc, indoor, shown in tf:
            if shown is not None:
                continue
            if field:
                reason, extra = "TF field event (by design)", ev
            elif loc is None:
                reason, extra = "TF no venue", f"{ev} (meet {mid}, {src})"
            else:
                reason, extra = "TF venue not solved", f"{ev} at TF:loc:{loc}:{'in' if indoor else 'out'}"
            why[reason] += 1
            print(f"  {d[:10]}  {src:5} {mid}  {(ev or '')[:20]:20}  -> {reason}: {extra}")
        conn.rollback()
    print("\n  blank Course cells by reason:")
    for r, n in why.most_common():
        print(f"    {n:4}  {r}")


if __name__ == "__main__":
    main()
