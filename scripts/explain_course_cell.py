# Project: xc-predictor / scripts
# File:    explain_course_cell.py
# Purpose: Who voted a course's difficulty -- every division at one venue,
#          with the numbers that expose a poisoned cell.
#
#     python scripts/explain_course_cell.py "Cabell Midland"
#
# ★ WHY. Bill Hill Memorial at Cabell Midland rendered +0.155 -- about 19
#   fake rating points on a clean 3000m race. Difficulty is solved from the
#   normalized times of every race in the cell, so one division with a wrong
#   distance (or one whose correction renormalized it onto a different
#   scale) drags every OTHER race at the venue. This lists the cell's
#   constituents so the poisoner is a row in a table instead of a hunch:
#   look for a division whose median normalized time is far from its
#   neighbours at the same distance, or whose median pace at the APPLIED
#   distance is implausible.
#
# Read-only. Groups by the same snapped distance the venue key uses
# (round(d/100)*100), because a cell is (venue, distance), not a venue.

import argparse
import sys

sys.path.insert(0, "scripts")

from database import getConn                     # noqa: E402

_SQL = """
    WITH rows AS (
        SELECT r.meet_id, r.div_id, r.source, r.date,
               r.time_seconds, r.normalized_time, r.speed_rating,
               COALESCE(dov.distance, m.distance,
                        (mt.division_distances -> r.div_id::text
                           ->> 'distance')::real)      AS applied,
               COALESCE(m.distance,
                        (mt.division_distances -> r.div_id::text
                           ->> 'distance')::real)      AS listed,
               COALESCE(m.division,
                        mt.division_distances -> r.div_id::text
                          ->> 'div_name')              AS div_name,
               COALESCE(m.meet_name, mt.meet_name)     AS meet_name
        FROM   results r
        LEFT   JOIN meets m ON m.meet_id = r.meet_id
                           AND m.div_id = r.div_id AND m.source = r.source
        LEFT   JOIN meets_tfrrs mt ON r.source = 'tfrrs'
                                  AND mt.meet_id = r.meet_id
                                  AND mt.sport = 'XC'
        LEFT   JOIN dist_override dov ON dov.meet_id = r.meet_id
                                     AND dov.div_id = r.div_id
        WHERE  COALESCE(m.course_name, mt.venue_name) ILIKE %(course)s
    )
    SELECT meet_id, div_id, source, min(meet_name) AS meet_name,
           min(div_name)                           AS div_name,
           min(date)                               AS date,
           count(*)                                AS n,
           min(applied)                            AS applied,
           min(listed)                             AS listed,
           percentile_cont(0.5) WITHIN GROUP
               (ORDER BY time_seconds)             AS med_time,
           percentile_cont(0.5) WITHIN GROUP
               (ORDER BY normalized_time)          AS med_nt,
           count(normalized_time)                  AS n_nt
    FROM   rows
    GROUP  BY meet_id, div_id, source
    ORDER  BY (round(min(applied) / 100.0) * 100), min(date)
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("course", help="course name (ILIKE; %% wildcards ok)")
    args = ap.parse_args()
    like = args.course if "%" in args.course else f"%{args.course}%"

    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL, {"course": like})
        rows = cur.fetchall()
    if not rows:
        print(f"  no divisions at a course matching {like!r}")
        return

    print(f"\n  {len(rows)} divisions at {like!r}, grouped by the venue "
          "cell they vote in.\n  A cell's difficulty is solved from med_nt; "
          "one outlier med_nt at a\n  distance is the poisoner.\n")
    hdr = (f"    {'cell':>6} {'meet/div':>18} {'src':<6}{'date':<12}"
           f"{'n':>5} {'applied':>8} {'listed':>8} {'med time':>9} "
           f"{'s/m':>6} {'med_nt':>8}  division")
    print(hdr)
    print("    " + "-" * (len(hdr) - 4))
    last_cell = None
    for (meet, div, src, meet_name, div_name, date, n, applied, listed,
         med_t, med_nt, n_nt) in rows:
        cell = int(round((applied or 0) / 100.0) * 100)
        if cell != last_cell:
            if last_cell is not None:
                print()
            last_cell = cell
        pace = (med_t / applied) if (med_t and applied) else None
        corr = "*" if (applied and listed
                       and abs(applied - listed) >= 1) else " "
        print(f"    {cell:>6} {f'{meet}/{div}':>18} {src:<6}"
              f"{(date or '?'):<12}{n:>5} "
              f"{(applied or 0):>7.0f}{corr} {(listed or 0):>8.0f} "
              f"{(med_t or 0):>9.1f} "
              f"{(pace or 0):>6.3f} {(med_nt or 0):>8.1f}  "
              f"{(div_name or meet_name or '?')[:34]}")
    print("\n    * = corrected distance (override differs from the scrape)."
          "\n    s/m sanity: HS varsity medians run ~0.20-0.24; under 0.18 "
          "or over 0.30\n    at the applied distance means the distance is "
          "lying, and so is the vote.")


if __name__ == "__main__":
    main()
