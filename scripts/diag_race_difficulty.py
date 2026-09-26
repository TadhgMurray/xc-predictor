#!/usr/bin/env python3
"""
diag_race_difficulty.py -- why a race page shows no course difficulty (or
an odd one).

    /srv/venv/bin/python scripts/diag_race_difficulty.py "/race/xc/280314/1116228?r=69975426"

★ WHY (owner, 2026-09-26: "race with no difficulty?" at that URL, and "races
  with 130 results, 130 athletes only run once, getting 20% difficulty").
  The race header finds its difficulty in three joins (app.get_race_header):
  the meet row gives the course name and GPS; course_canonical matches the
  NAME and the GPS to 5 decimals; course_difficulties holds the cell at the
  race's distance rounded to 100 m. This prints each step and says which one
  came up empty -- or, when a difficulty exists, how many of the race's
  runners have any OTHER rated race (the linkage the solve had to place it).
  READ-ONLY.
"""
import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("XCP_DB_QUIET", "1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    a = ap.parse_args()
    m = re.search(r"/race/xc/(\d+)/(\d+)", a.path)
    if not m:
        ap.error("give a /race/xc/<meet>/<div> path")
    meet_id, div_id = int(m.group(1)), int(m.group(2))
    args = dict(re.findall(r"[?&](\w+)=([^&]+)", a.path))

    import psycopg2.extras
    import app as A
    with A.getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            src, idx, others = A._xc_meet_sources(cur, meet_id, args, div_id)
            print(f"race {meet_id}/{div_id}: source picked {src!r} "
                  f"(alt {idx}; {len(others)} other meet(s) share this id)")
            hdr = A.get_race_header(cur, meet_id, div_id, source=src)
            if not hdr:
                print("  no header at all -- no results for this race and source")
                return
            print(f"  header: course {hdr.get('course_name')!r}, distance "
                  f"{hdr.get('distance')}, difficulty {hdr.get('difficulty')}, "
                  f"cell distance {hdr.get('cell_distance_m')}, source {hdr.get('source')}")

            cur.execute("""SELECT course_name, gps_lat, gps_long, distance, source
                           FROM meets WHERE meet_id = %s AND div_id = %s""",
                        (meet_id, div_id))
            mrows = cur.fetchall()
            cur.execute("""SELECT venue_name AS course_name, gps_lat, gps_long,
                                  NULL::real AS distance, 'tfrrs' AS source
                           FROM meets_tfrrs WHERE meet_id = %s AND sport = 'XC'""",
                        (meet_id,))
            mrows += cur.fetchall()
            print(f"\n  1. meet rows ({len(mrows)}):")
            for r in mrows:
                print(f"     {r['source']:<6} course {r['course_name']!r}  gps "
                      f"({r['gps_lat']}, {r['gps_long']})  distance {r['distance']}")
            if not mrows:
                print("     NONE -- no meet row, so no course and no GPS: no difficulty")
            names = sorted({r["course_name"] for r in mrows if r["course_name"]})
            if not names:
                print("     no course name on any meet row: no difficulty")
                return

            cur.execute("""SELECT canonical_id, course_name, gps_lat, gps_long
                           FROM course_canonical WHERE course_name = ANY(%s)""",
                        (names,))
            canon = cur.fetchall()
            print(f"\n  2. course_canonical rows with that name ({len(canon)}):")
            gps = {(round(float(r['gps_lat']), 5), round(float(r['gps_long']), 5))
                   for r in mrows if r['gps_lat'] is not None and r['gps_long'] is not None}
            for c in canon:
                key = (round(float(c['gps_lat']), 5), round(float(c['gps_long']), 5)) \
                    if c['gps_lat'] is not None else None
                hit = "MATCHES this meet's GPS" if key in gps else "gps differs"
                print(f"     canon {c['canonical_id']}  ({c['gps_lat']}, "
                      f"{c['gps_long']})  {hit}")
            if not canon:
                print("     NONE -- the course was never canonicalised (it has no "
                      "cell in any solve)")
            matched = [c for c in canon if c['gps_lat'] is not None and
                       (round(float(c['gps_lat']), 5),
                        round(float(c['gps_long']), 5)) in gps]
            if canon and not matched:
                print("     none matches this meet's GPS to 5 decimals: THIS is why "
                      "the page shows no difficulty")

            ids = [c["canonical_id"] for c in (matched or canon)]
            if ids:
                # SELECT *: the live table's columns beyond these three vary
                cur.execute("""SELECT * FROM course_difficulties
                               WHERE canonical_id = ANY(%s)
                               ORDER BY canonical_id, distance_m""", (ids,))
                cells = cur.fetchall()
                print(f"\n  3. course_difficulties cells for those courses ({len(cells)}):")
                want = (round(float(hdr['distance']) / 100.0) * 100
                        if hdr.get('distance') else None)
                for c in cells:
                    mark = "  <- this race's distance" if c['distance_m'] == want else ""
                    extra = "  ".join(f"{k} {c[k]}" for k in c
                                      if k.startswith("n_"))
                    print(f"     canon {c['canonical_id']}  {c['distance_m']} m  "
                          f"difficulty {c['difficulty']}  {extra}{mark}")
                if want and not any(c['distance_m'] == want for c in cells):
                    print(f"     no cell at {want} m: the solve did not place a "
                          "cell here (too few linked runners?), so no difficulty")

            # the linkage the solve had: runners with ANY other rated race
            cur.execute("""
                SELECT count(DISTINCT r.person_id) AS runners,
                       count(DISTINCT r.person_id) FILTER (WHERE EXISTS (
                           SELECT 1 FROM results o
                           WHERE o.person_id = r.person_id
                             AND o.speed_rating IS NOT NULL
                             AND NOT (o.meet_id = r.meet_id AND o.div_id = r.div_id)))
                         AS linked_xc,
                       count(DISTINCT r.person_id) FILTER (WHERE EXISTS (
                           SELECT 1 FROM results_tf o
                           WHERE o.person_id = r.person_id
                             AND o.speed_rating IS NOT NULL)) AS linked_tf
                FROM results r
                WHERE r.meet_id = %s AND r.div_id = %s
                  AND (%s::text IS NULL OR r.source = %s)
                  AND r.person_id IS NOT NULL
            """, (meet_id, div_id, src, src))
            lk = cur.fetchone()
            print(f"\n  4. linkage: {lk['runners']} runners with a person; "
                  f"{lk['linked_xc']} have another rated XC race, "
                  f"{lk['linked_tf']} have a rated track race")
            if lk["runners"] and not lk["linked_xc"] and not lk["linked_tf"]:
                print("     NOBODY here has another rated race: the course cannot "
                      "be placed from this race alone -- any difficulty it shows "
                      "is a prior, not a measurement")


if __name__ == "__main__":
    main()
