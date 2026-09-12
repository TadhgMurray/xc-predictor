#!/usr/bin/env python3
"""
meet_cells.py -- which cells do these meets vote in, and what is at this
track? Read-only; needs the database.

    scripts/meet_cells.py --meet "Foot Locker" --meet "Champs Sports" --meet Eastbay
    scripts/meet_cells.py --tf-loc 99426 --tf-loc 88489

--meet    every XC meet whose name contains the text: the course name the
          scrape gave it, the canonical course id the engine keys it under
          (the cell), the years, and the rows. A national final that turns
          up under three ids in twelve years is three thin cells, and the
          board number of each is a different question from the one asked.
--tf-loc  a track cell by its location id (TF:loc:<id>:out): the location's
          name columns, its meets by year, and the events raced there with
          the median time per metre, so a track at -7% on the board with a
          same-athlete bracket of zero is read for what it is: a distance
          that lies, not a fast track.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn                                    # noqa: E402

_MEET_SQL = """
    WITH rows AS (
        SELECT r.meet_id, r.div_id, r.source, r.date,
               COALESCE(m.meet_name, mt.meet_name)        AS meet_name,
               COALESCE(m.course_name, mt.venue_name)     AS course_name,
               cc.canonical_id, cc.canonical_name
        FROM   results r
        LEFT   JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
        LEFT   JOIN meets_tfrrs mt ON r.source = 'tfrrs'
                                  AND mt.meet_id = r.meet_id AND mt.sport = 'XC'
        LEFT   JOIN course_canonical cc
               ON cc.course_name = COALESCE(m.course_name, mt.venue_name)
              AND round(cc.gps_lat::numeric, 5)
                = round(COALESCE(m.gps_lat, mt.gps_lat)::numeric, 5)
              AND round(cc.gps_long::numeric, 5)
                = round(COALESCE(m.gps_long, mt.gps_long)::numeric, 5)
        WHERE  COALESCE(m.meet_name, mt.meet_name) ILIKE %(pat)s
    )
    SELECT canonical_id, min(canonical_name), course_name,
           substr(date::text, 1, 4) AS yr, count(*) AS n,
           count(DISTINCT meet_id || '/' || div_id::text) AS divisions,
           min(meet_name)
    FROM   rows
    GROUP  BY canonical_id, course_name, yr
    ORDER  BY canonical_id NULLS LAST, yr
"""


def meets(cur, text):
    cur.execute(_MEET_SQL, {"pat": f"%{text}%"})
    rows = cur.fetchall()
    print(f"\n== meets named like {text!r}: {len(rows)} (cell, course name, year) groups ==")
    if not rows:
        return
    print(f"  {'cell':>10} {'year':>5} {'rows':>7} {'divs':>5}  canonical name | course name | a meet name")
    for cid, cname, course, yr, n, divs, mname in rows:
        key = f"XC:{cid}:" if cid is not None else "NO CELL"
        print(f"  {key:>10} {str(yr):>5} {n:>7,} {divs:>5}  {cname or '-'} | {course or '-'} | {mname}")
    cells = {}
    for cid, cname, course, yr, n, divs, mname in rows:
        cells.setdefault(cid, [0, set(), cname])[0] += n
        cells[cid][1].add(yr)
    print("  by cell:")
    for cid, (n, yrs, cname) in sorted(cells.items(), key=lambda t: -t[1][0]):
        key = f"XC:{cid}:" if cid is not None else "NO CELL"
        yrs = {str(y) for y in yrs if y}
        span = f"{min(yrs)}-{max(yrs)}" if yrs else "?"
        print(f"    {key:>10} {n:>8,} rows  {span} ({len(yrs)} years)  {cname or '-'}")
    print("  read: the cell key is what scripts/diagnose.py --key takes. Rows with "
          "NO CELL carry no venue and vote nowhere.")


def _columns(cur, table):
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s""", (table,))
    return [r[0] for r in cur.fetchall()]


def track(cur, loc):
    have = _columns(cur, "meets_tf")
    namecols = [c for c in have if any(w in c.lower() for w in
                                       ("location", "venue", "facility", "city", "state"))
                and c != "location_id"]
    sel = ", ".join(f"min({c}::text)" for c in namecols) if namecols else "NULL"
    indoor = ("COALESCE(is_indoor, 0)" if "is_indoor" in have else "0")
    cur.execute(f"""
        SELECT substr(meet_date::text, 1, 4) AS yr, {indoor} AS indoor,
               count(DISTINCT meet_id) AS meets, min(meet_name), max(meet_name), {sel}
        FROM   meets_tf
        WHERE  location_id = %(loc)s
        GROUP  BY yr, indoor ORDER BY yr, indoor""", {"loc": loc})
    rows = cur.fetchall()
    print(f"\n== track location {loc} (TF:loc:{loc}:out / :in): {len(rows)} year groups ==")
    if not rows:
        print("  no meets_tf rows carry this location_id")
        return
    if namecols:
        print(f"  name columns: {', '.join(namecols)} -> "
              + " | ".join(str(v) for v in rows[0][5:]))
    print(f"  {'year':>5} {'in':>3} {'meets':>6}  first meet name .. last meet name")
    for r in rows:
        print(f"  {str(r[0]):>5} {r[1]:>3} {r[2]:>6}  {r[3]} .. {r[4]}")
    # the events raced there: distance, rows, median seconds per metre
    rcols = _columns(cur, "results_tf")
    dist_expr = ("m.distance_meters" if "distance_meters" in have else "NULL")
    ev = "r.event_short" if "event_short" in rcols else "NULL"
    time_col = next((c for c in ("time_seconds", "seconds", "mark_seconds") if c in rcols), None)
    if time_col is None:
        print("  (results_tf has no time column I know; events not listed)")
        return
    cur.execute(f"""
        SELECT {ev} AS event, {dist_expr} AS dist_m, count(*) AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY r.{time_col}) AS med_t,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY r.normalized_time) AS med_nt
        FROM   results_tf r
        JOIN   meets_tf m ON m.meet_id = r.meet_id
                         AND m.div_id = r.div_id AND m.source = r.source
        WHERE  m.location_id = %(loc)s
        GROUP  BY 1, 2 ORDER BY n DESC LIMIT 25""", {"loc": loc})
    evs = cur.fetchall()
    print(f"  {'event':<14} {'dist_m':>7} {'rows':>7} {'med time':>9} {'s/m':>6} {'med_nt':>8}")
    for event, dist, n, med_t, med_nt in evs:
        spm = (med_t / dist) if (med_t and dist) else None
        print(f"  {str(event):<14} {(dist or 0):>7.0f} {n:>7,} {(med_t or 0):>9.1f} "
              f"{(spm or 0):>6.3f} {(med_nt or 0):>8.1f}")
    print("  read: s/m for a HS 1600 sits near 0.17-0.20 and a 3200 near 0.19-0.22; "
          "a whole track at 0.16 or 0.24 is a distance that lies (a 1500 booked as "
          "a 1600 is 6.8%), and that is the board number, not the surface.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meet", action="append", default=[], help="part of an XC meet name")
    ap.add_argument("--tf-loc", action="append", default=[], type=int,
                    help="a track location id (TF:loc:<id>)")
    args = ap.parse_args()
    if not args.meet and not args.tf_loc:
        ap.error("give --meet TEXT or --tf-loc ID")
    with getConn() as conn, conn.cursor() as cur:
        for text in args.meet:
            meets(cur, text)
        for loc in args.tf_loc:
            track(cur, loc)


if __name__ == "__main__":
    main()
