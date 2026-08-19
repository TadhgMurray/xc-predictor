"""
bad_distance.py -- find races whose recorded distance is wrong.

THE ESTIMATOR
    For every race, take the athletes in it and compare their normalized time
    THERE against their own median normalized time elsewhere that season. The
    median of that ratio is the race's anomaly.

★ WHY PER-ATHLETE AND NOT "FASTER THAN THE VENUE'S BEST". A venue's record
  depends on who turned up. An invitational that happens to draw a state
  champion looks anomalous under a record-based test and is not. Comparing each
  athlete to THEMSELVES cancels field strength completely -- the same estimator
  that measured Steens Mountain at 1.41 and cleared The Hydrangea Ranch at 1.10.

★ AND WHY THE MEDIAN. One mis-parsed time in a field of sixty should not
  condemn the race; a distance error moves EVERY row in it. The median responds
  to the second and ignores the first, which is exactly the discrimination
  wanted.

★ WHAT A RATIO MEANS. ratio = 0.80 means the field ran 20% faster than they do
  elsewhere -- so the true distance is about 0.80x what was recorded. The
  suggested distance is the recorded one scaled by the ratio and snapped to the
  nearest convention, because a race is far more likely to be a 1600 recorded
  as 2414 than a 1931.

⚠ THIS FINDS DISTANCE ERRORS AND ALSO REAL EFFECTS. A course at altitude, in
  mud, or genuinely brutal produces a high ratio honestly -- Steens Mountain is
  1.41 because athletes really do run 41% slower there. The discriminator is
  CONSISTENCY: a venue whose every race is slow has hard terrain, a venue with
  one anomalous race among many normal ones has a bad row. Both are reported.

Measures only. Emits corrections.py lines to paste.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, "scripts")
for _p in (_HERE, _ROOT,
           os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# Race distances people actually use. A wrong distance is nearly always a
# CONVENTION recorded against the wrong race, not an arbitrary number.
_CONVENTIONS = (1200, 1500, 1600, 2000, 2400, 2500, 3000, 3200, 4000, 4023,
                4800, 5000, 6000, 8000, 10000)

# ★ THE CELL IS ITS OWN CONTROL.
#
#   A (venue, distance) cell carries ONE difficulty, fitted across all its
#   races. So the question is not "is this race slow" -- Perseverance Trail is
#   a mountain trail and every race there is slow, honestly. The question is
#   "does THIS race imply a different difficulty than the cell's OTHER races".
#
#   Per race, the implied difficulty is the median of
#
#       normalized_time / (that athlete's own median elsewhere this season)
#
#   which cancels field strength. Comparing that against the CELL's median of
#   the same quantity then cancels terrain, altitude, surface and everything
#   else constant at the venue. What is left can only be that race.
#
#   Two earlier attempts failed for opposite reasons and are worth recording:
#
#     comparing a race to the VENUE'S FASTEST EVER
#         flags any race that drew a strong field.
#
#     comparing SPEED RATINGS to a season median
#         flags freshman, novice and middle-school divisions at major venues --
#         Van Cortlandt Varsity 1 "should be 4023m", Mt. SAC frosh "4000m",
#         Crystal Springs 4th-6th grade "1500m". Those athletes rate low there
#         because the FIELD is weak, not because the course is short. It was
#         measuring field strength wearing a distance error's clothes.
#
#   This version can only see WITHIN-CELL disagreement, which is exactly the
#   Allens Meadows mechanism: 4,159 results across 30 races at 2414m, and one
#   girls novice race at 423s.
_SQL = """
WITH mc AS MATERIALIZED (
  SELECT meet_id, min(course_name) AS cn FROM meets
  WHERE COALESCE(TRIM(course_name), '') <> '' GROUP BY meet_id
),
r AS MATERIALIZED (
  SELECT r.person_id, r.meet_id, r.div_id, r.normalized_time AS nt,
         mc.cn, m.distance,
         CASE WHEN EXTRACT(month FROM r.date::date) >= 7
              THEN EXTRACT(year FROM r.date::date)::int
              ELSE EXTRACT(year FROM r.date::date)::int - 1 END AS season
  FROM results r
  JOIN mc ON mc.meet_id = r.meet_id
  JOIN meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
  WHERE r.normalized_time > 0 AND m.distance > 0
    AND r.date ~ '^(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}$'
    AND r.date >= %s
),
elsewhere AS (
  SELECT a.person_id, a.season, a.meet_id, a.div_id,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY b.nt) AS nt_else
  FROM r a JOIN r b
    ON b.person_id = a.person_id AND b.season = a.season
   AND (b.meet_id, b.div_id) IS DISTINCT FROM (a.meet_id, a.div_id)
  GROUP BY 1, 2, 3, 4
),
race AS (
  SELECT r.meet_id, r.div_id, min(r.cn) AS cn, min(r.distance) AS distance,
         count(*) AS n,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY r.nt / e.nt_else) AS ratio
  FROM r JOIN elsewhere e
    ON e.person_id = r.person_id AND e.season = r.season
   AND e.meet_id = r.meet_id AND e.div_id = r.div_id
  GROUP BY 1, 2 HAVING count(*) >= %s
),
-- the CELL: every race at this venue at this distance
cell AS (
  SELECT cn, distance, count(*) AS races, sum(n) AS rows_,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY ratio) AS cell_ratio
  FROM race GROUP BY 1, 2 HAVING count(*) >= %s
)
SELECT race.meet_id, race.div_id, race.cn, race.distance, race.n,
       race.ratio, c.cell_ratio, c.races, c.rows_,
       m.division, min(res.date) AS date
FROM race
JOIN cell c ON c.cn = race.cn AND c.distance = race.distance
JOIN meets m ON m.meet_id = race.meet_id AND m.div_id = race.div_id
JOIN results res ON res.meet_id = race.meet_id AND res.div_id = race.div_id
WHERE race.ratio / c.cell_ratio < %s OR race.ratio / c.cell_ratio > %s
GROUP BY 1,2,3,4,5,6,7,8,9,10
ORDER BY abs(ln(race.ratio / c.cell_ratio)) DESC
LIMIT %s
"""


def snap(distance, ratio):
    """
    The implied true distance, snapped to the nearest convention.

    Returns None when nothing is within 12% -- better to report the anomaly and
    let a human decide than to invent a distance no race is run at.
    """
    if not distance:
        return None
    target = distance * ratio
    best = min(_CONVENTIONS, key=lambda c: abs(c - target))
    return best if abs(best - target) / max(target, 1) <= 0.12 else None


def venueContext(cur, cn, since):
    """
    How the venue's OTHER races behave.

    ★ THE DISCRIMINATOR. A venue where every race is slow has hard terrain --
      Steens Mountain sits at 1.41 across all of it, honestly. A venue with one
      anomalous race among many normal ones has a bad row. Without this, real
      terrain and bad data look identical.
    """
    cur.execute("""
        SELECT count(*) AS races,
               round(percentile_cont(0.5) WITHIN GROUP
                     (ORDER BY m.distance)::numeric, 0) AS median_distance
        FROM results r
        JOIN (SELECT meet_id, min(course_name) AS cn FROM meets
              GROUP BY meet_id) mc ON mc.meet_id = r.meet_id
        JOIN meets m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
        WHERE mc.cn = %s AND r.date >= %s
        GROUP BY mc.cn
    """, (cn, since))
    row = cur.fetchone()
    return row if row else (0, None)


def main(since="2015-01-01", min_field=15, min_races=4,
         lo=0.88, hi=1.14, limit=60):
    """
    lo/hi are the race's ratio RELATIVE TO ITS CELL, so 1.14 means "this race
    implies a difficulty 14% higher than the rest of this venue at this
    distance".
    """
    from database import getConn

    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL, (since, min_field, min_races, lo, hi, limit))
        rows = cur.fetchall()

        print(f"[dist] {len(rows)} races disagreeing with their own "
              f"(venue, distance) cell by more than "
              f"{min(1 - lo, hi - 1):.0%}, since {since}\n")
        print("     rel   race   cell  cell    n  recorded  sugg   "
              "date        venue / division")
        fixes = []
        for (meet_id, div_id, cn, dist, n, ratio, cell_ratio, races, rows_,
             division, date) in rows:
            rel = float(ratio) / float(cell_ratio)
            # ★ SNAP ON THE RELATIVE RATIO. The cell's own ratio is the venue
            #   being hard or easy, which is REAL and must not be corrected
            #   away; only the part where this race disagrees with its cell is
            #   a candidate distance error.
            sug = snap(dist, rel)
            flag = ""
            if sug and dist and abs(sug - dist) > 1:
                fixes.append((meet_id, div_id, sug, cn, division, rel,
                              races, rows_))
                flag = " <-"
            print(f"    {rel:5.3f} {float(ratio):6.3f} "
                  f"{float(cell_ratio):6.3f} {races:>5} {n:>4} "
                  f"{str(dist or '?'):>9} {str(sug or '-'):>6}  {date}  "
                  f"{(cn or '?')[:24]:<24} {(division or '')[:12]}{flag}")

        if not fixes:
            print("\n[dist] no race suggests a clean convention distance.")
            return

        print(f"\n[dist] {len(fixes)} suggested entries. The cell column is the")
        print("       evidence: a race disagreeing with 30 others at the same")
        print("       venue and distance is a bad row, not hard terrain.\n")
        print("# _DISTANCE_OVERRIDES_XC")
        for meet_id, div_id, sug, cn, division, rel, races, rows_ in fixes:
            print(f"    ({meet_id}, {div_id}): {sug},"
                  f"  # {(cn or '?')[:30]} {(division or '')[:14]} "
                  f"{rel:.3f}x its cell ({races} races, {rows_:,} rows)")


if __name__ == "__main__":
    def opt(name, cast, default):
        for a in sys.argv[1:]:
            if a.startswith(f"--{name}="):
                return cast(a.split("=", 1)[1])
        return default

    main(since=opt("since", str, "2015-01-01"),
         min_field=opt("min-field", int, 15),
         min_races=opt("min-races", int, 4),
         lo=opt("lo", float, 0.88),
         hi=opt("hi", float, 1.14),
         limit=opt("limit", int, 60))