"""
diag_meet_outliers.py -- READ ONLY. Finds meets whose whole field is misrated.

Writes nothing.

    python scripts/diag_meet_outliers.py
    python scripts/diag_meet_outliers.py --sport TF --min-field 20

WHY THIS EXISTS -- and why diag_engine_quality's C1 could not do it

    Backlog item 1 reports a SPECIFIC symptom: the UCSB field rated +40-53 over
    its own season average. diag_engine_quality measured the season-wide weekly
    average and found a smooth -2.2 -> +1.4 trend, which is an order of
    magnitude too small to contain a +40 meet. Averaging over every meet in a
    week hides one bad meet completely.

    THE UNIT MATTERS. Item 1 is a per-MEET anomaly, so the measurement has to be
    per meet.

WHAT A HIGH FIELD DEVIATION MEANS
    Every athlete in one division shares a course, a difficulty, a distance and
    a date. If the WHOLE field rates far above its own season average, the
    athletes did not all have a good day -- something about the race was scored
    wrong. Candidates, roughly in order:
      1. course difficulty too high -> every adjusted time too fast
      2. wrong distance on meets.distance -> normalized_time wrong for everyone
      3. mixed pools in one division -> pool_mean wrong for part of the field
      4. stale normalized_time from an older pickle vintage

    This does not distinguish them. It produces the WORKLIST; the trace script
    is what identifies which.
"""

import argparse

from psycopg2.extras import RealDictCursor

from database import getConn


_TABLE = {"XC": "results", "TF": "results_tf"}

# XC seasons run roughly Aug-Dec, TF Mar-Jul. Used only to label the output.
_SEASON_START_DOY = {"XC": 213, "TF": 60}

# A field needs enough rated athletes for its mean to mean anything. Below ~10,
# two bad rows dominate and every thin division floats to the top.
_MIN_FIELD = 10


_MEET_OUTLIER_SQL = """
WITH raced AS (
    SELECT r.person_id,
           r.meet_id,
           r.div_id,
           r.speed_rating,
           substring(r.date, 1, 4)::int                 AS season_year,
           EXTRACT(DOY FROM r.date::date)::int          AS doy
    FROM {table} r
    WHERE r.speed_rating IS NOT NULL
      AND r.person_id IS NOT NULL
      AND r.date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}$'
),
seasoned AS (
    -- Each athlete's own mean for that season, and how many races it rests on.
    -- Comparing a race to the ATHLETE'S OWN mean is what controls for field
    -- quality: a meet can legitimately draw fast runners, and a raw meet
    -- average could not tell that apart from a scoring error.
    SELECT raced.*,
           avg(speed_rating) OVER w AS season_mean,
           count(*)          OVER w AS season_races
    FROM raced
    WINDOW w AS (PARTITION BY person_id, season_year)
),
fields AS (
    SELECT meet_id,
           div_id,
           min(season_year)                                    AS season_year,
           min(doy)                                            AS doy,
           count(*)                                            AS field_size,
           avg(speed_rating - season_mean)                     AS field_dev,
           avg(season_mean)                                    AS base_rating,
           stddev_pop(speed_rating - season_mean)              AS dev_spread
    FROM seasoned
    WHERE season_races >= 4          -- a season mean needs a season behind it
    GROUP BY meet_id, div_id
    HAVING count(*) >= %(min_field)s
)
SELECT f.*,
       m.course_name,
       m.distance,
       m.division
FROM fields f
LEFT JOIN LATERAL (
    SELECT course_name, distance, division
    FROM meets
    WHERE meets.meet_id = f.meet_id AND meets.div_id = f.div_id
    LIMIT 1
) m ON TRUE
WHERE %(name)s IS NULL OR m.course_name ILIKE %(name)s
ORDER BY abs(f.field_dev) DESC
LIMIT %(limit)s
"""


# _weekOf
# Purpose:   day-of-year -> week of season, matching diag_engine_quality.
# Detail:    integer division, so doy == start lands in week 1, not week 0.
def _weekOf(doy, sport):
    return ((doy - _SEASON_START_DOY[sport]) // 7) + 1


_DISTRIBUTION_SQL = """
WITH raced AS (
    SELECT person_id, meet_id, div_id, speed_rating,
           substring(date, 1, 4)::int AS season_year
    FROM {table}
    WHERE speed_rating IS NOT NULL
      AND person_id IS NOT NULL
      AND date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}$'
),
seasoned AS (
    SELECT raced.*,
           avg(speed_rating) OVER w AS season_mean,
           count(*)          OVER w AS season_races
    FROM raced
    WINDOW w AS (PARTITION BY person_id, season_year)
),
fields AS (
    SELECT meet_id, div_id,
           count(*)                        AS field_size,
           avg(speed_rating - season_mean) AS field_dev
    FROM seasoned
    WHERE season_races >= 4
    GROUP BY meet_id, div_id
    HAVING count(*) >= %(min_field)s
)
SELECT count(*)                                            AS fields,
       round(avg(field_dev)::numeric, 2)                   AS mean_dev,
       round(stddev_pop(field_dev)::numeric, 2)            AS sd_dev,
       count(*) FILTER (WHERE abs(field_dev) > 10)         AS beyond_10,
       count(*) FILTER (WHERE abs(field_dev) > 20)         AS beyond_20,
       count(*) FILTER (WHERE abs(field_dev) > 40)         AS beyond_40
FROM fields
"""



# ★ WHAT DISTANCE WOULD EXPLAIN THE BIAS. Same chain the splitter uses:
#
#       normalized_time = t * (anchor / d) ** K      =>   nt is proportional to d ** -K
#       rating          is proportional to 1 / nt
#   so  rating_label / rating_true = (d_label / d_true) ** K
#   and d_true = d_label * (base / (base + dev)) ** (1 / K)
#
# A field rating HIGH ran a course SHORTER than its label, and this says by
# how much. It is the same number a hand adjudication would reach, which is
# the point: the worklist should hand you the candidate, not just the alarm.
_K = {"XC": 1.06, "TF": 1.10}


def _impliedDistance(distance, base, dev, sport):
    if not distance or not base or base + dev <= 0:
        return None
    return float(distance) * (base / (base + dev)) ** (1.0 / _K[sport])


def _overrideFor(meet_id, div_id, sport):
    """Is this field already covered by a distance override, and at what?

    Answering "no override" and "an override that is not being applied" are
    completely different bugs, and the worklist could not tell them apart.
    """
    try:
        import sys
        sys.path.insert(0, "engine")
        import corrections
        table = (corrections._DISTANCE_OVERRIDES_XC if sport == "XC"
                 else corrections._DISTANCE_OVERRIDES_TF)
    except Exception:
        return None
    for key in ((meet_id, div_id), (int(meet_id), int(div_id))):
        try:
            if key in table:
                return table[key]
        except (TypeError, ValueError):
            pass
    return None


def _fetch(sql, params):
    with getConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()


def reportDistribution(sport, minField):
    """
    How common are misrated fields, and how extreme do they get?

    THIS IS THE GATE. Read it BEFORE the worklist. Any corpus this size has a
    worst meet, and a top-50 list will always look alarming whether or not a
    problem exists. beyond_40 is the number that speaks to item 1 directly: it
    counts fields at or past the reported UCSB magnitude. A handful out of
    hundreds of thousands is a normal tail. Thousands is a systematic fault.
    """
    row = _fetch(_DISTRIBUTION_SQL.format(table=_TABLE[sport]),
                 {"min_field": minField})[0]

    print(f"\n  FIELD-DEVIATION DISTRIBUTION ({sport}, "
          f"fields with >= {minField} rated athletes)")
    print(f"    fields          {row['fields']:,}")
    print(f"    mean dev        {row['mean_dev']}")
    print(f"    sd of dev       {row['sd_dev']}")
    print(f"    |dev| > 10      {row['beyond_10']:,}")
    print(f"    |dev| > 20      {row['beyond_20']:,}")
    print(f"    |dev| > 40      {row['beyond_40']:,}   <-- UCSB magnitude")


def reportWorklist(sport, minField, limit, name=None):
    """
    The worst fields, for hand inspection.

    dev_spread is printed alongside field_dev and the two must be read together:
      HIGH dev, LOW spread  -> the whole field shifted by a constant. That is a
                               race-level scoring error -- difficulty, distance,
                               or a stale normalization. This is item 1's shape.
      HIGH dev, HIGH spread -> the field disagrees internally. More likely mixed
                               pools in one division, which is a pool problem,
                               not a course problem.
    """
    rows = _fetch(_MEET_OUTLIER_SQL.format(table=_TABLE[sport]),
                  {"min_field": minField, "limit": limit,
                   "name": f"%{name}%" if name else None})

    scope = f" matching {name!r}" if name else ""
    print(f"\n  WORST FIELDS (top {limit} by |field_dev|{scope})")
    if not rows:
        print(f"\n    nothing{scope} with a field of {minField}+ rated "
              f"athletes. Widen with --min-field, or check the spelling "
              f"against meets.course_name.")
        return
    print(f"\n    {'dev':>7} {'spread':>7} {'n':>5} {'label':>6} "
          f"{'implied':>7} {'ovr':>7} {'year':>5} {'meet/div':>18}  course")

    for row in rows:
        meetDiv = f"{row['meet_id']}/{row['div_id']}"
        course = (row["course_name"] or "(no meets row)")[:34]
        implied = _impliedDistance(row["distance"], row["base_rating"],
                                   row["field_dev"], sport)
        ovr = _overrideFor(row["meet_id"], row["div_id"], sport)
        label_s = f"{row['distance']:.0f}" if row["distance"] else "-"
        imp_s = f"{implied:.0f}" if implied else "-"
        # ! AN OVERRIDE THAT IS SET AND STILL WRONG IS THE LOUD CASE. The
        #   distance was decided and the field is STILL off, so the override
        #   is either not reaching this row or is itself wrong.
        ovr_s = "-" if ovr is None else f"{ovr:.0f}!"
        print(f"    {row['field_dev']:>+7.1f} {row['dev_spread']:>7.1f} "
              f"{row['field_size']:>5} {label_s:>6} {imp_s:>7} {ovr_s:>7} "
              f"{row['season_year']:>5} {meetDiv:>18}  {course}")

    print("\n    label = meets.distance   implied = what the field's bias says")
    print("    implied UNDER-corrects: each athlete's season mean includes")
    print("    this race, so the bias it is measured against is already")
    print("    pulled toward it. A field really run at 4000 under a 5000")
    print("    label reads implied 4251. Treat it as a floor on the error,")
    print("    not as the distance to write.")
    print("    ovr   = a distance override already set for this meet/div;")
    print("            a value there next to a big dev means the override is")
    print("            not reaching these rows, or is wrong itself.")

    print("\n    READ: high dev + LOW spread = whole field shifted = race-level")
    print("    scoring error (difficulty / distance / stale normalization).")
    print("    High spread = the field disagrees = more likely mixed pools.")
    print("    Cross-check the wk column: if these cluster in weeks 1-3, item 1")
    print("    is confirmed as an early-season effect. If they scatter across")
    print("    the calendar, it is NOT seasonal and item 1 is misnamed.")


def main():
    parser = argparse.ArgumentParser(
        description="Find meets whose whole field is misrated. Read only.")
    parser.add_argument("--sport", choices=["XC", "TF"], default="XC")
    parser.add_argument("--min-field", type=int, default=_MIN_FIELD)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--name", default=None,
                        help="only fields whose course_name "
                             "contains this (case-insensitive), "
                             "e.g. --name yellowjacket")
    args = parser.parse_args()

    print("=" * 78)
    print(f"MEET-LEVEL OUTLIER DIAGNOSTIC -- {args.sport}")
    print("=" * 78)

    reportDistribution(args.sport, args.min_field)
    reportWorklist(args.sport, args.min_field, args.limit, args.name)

    print("\nNothing was written. This script is read only.\n")


if __name__ == "__main__":
    main()