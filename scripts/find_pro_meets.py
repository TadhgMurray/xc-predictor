"""
find_pro_meets.py -- surface candidate PROFESSIONAL meets for curation.

    python scripts/find_pro_meets.py                 # both sports
    python scripts/find_pro_meets.py --sport TF
    python scripts/find_pro_meets.py --min-races 200 --top 80

Run from the PROJECT ROOT, like panels.py. Read only.

★ WHY MEET-BASED, NOT NAME-BASED

  School-name matching was measured and it fails badly. Of the 113 school
  strings a curated brand+country list caught, essentially none was a pro team:

      Denmark      3,732 races, grade 10   -> Denmark High School
      Mexico       1,866 races, grade 11   -> Mexico High School, Missouri
      Bowerman Track 9,105 races, grade 8  -> a youth club, not Bowerman TC
      Tahoka         555 races, grade 12   -> caught by the 'hoka' substring
      Central Oregon Running Klub          -> 'on running' inside 'Oregon Running'

  34,942 races would have been pooled as professional. The string a pro types
  into a roster is the same string a rural high school is named -- there is no
  matching rule that separates them, because there is no difference to match.

  A MEET is different. A professional does not race in a Varsity division, and
  a high schooler does not race the Diamond League. The field is the evidence.

★ HOW THE SCORE WORKS -- and what it deliberately does not do

  This does NOT decide anything. It ranks meets by how unlike a scholastic
  field they look, so the list can be read and curated by someone who knows the
  sport. Every signal below has a defensible failure mode:

    scholastic_pct   share of rows with a plain grade 1-12. A pro meet should
                     be ~0. But so is an alumni race, and tfrrs rows carry NULL
                     grades everywhere, so a low value alone means nothing.

    level_mask       divisions that permit MS/HS/elementary (bits 2|4|16 = 22)
                     cannot be pro. This is the strongest NEGATIVE signal --
                     it does not prove pro, it proves not-pro.

    med_rating       pool-relative, 100 = pool median. A field of professionals
                     rates far above it. Contaminated by whatever pool the
                     engine currently thinks they are in, which is the thing
                     being fixed -- so treat it as suggestive.

    career_span      years between an athlete's first and last race in the
                     corpus. A pro career runs 10-15 years; a high schooler's
                     runs 4. Median across the field is hard to fake.

  A meet scoring high on all four is worth a look. A meet scoring high on one
  is noise.
"""

import sys
import argparse

sys.path.insert(0, "scripts")
from database import getConn

import psycopg2.extras


# Bits 2 | 4 | 16 = middle school | high school | elementary.
#
# THE DECODE, measured from the grade distribution under each mask value:
#     1  = open        (appears alongside every level; alone -> "Open")
#     2  = MS          (4.05M grades 6-8, modal division "Middle School")
#     4  = HS          (25.2M grades 9-12, modal division "Varsity")
#     8  = college     (11,149 college grades, 6647m, modal "College")
#    16  = elementary  (raises the grade 1-5 share wherever it is set)
#
# Open (1) and college (8) are NOT excluded: a pro races open, and a
# post-collegiate athlete legitimately appears in college divisions.
SCHOLASTIC_BITS = 22


_SQL = {
    "XC": """
        WITH career AS (
            SELECT person_id,
                   max(substring(date, 1, 4)::int)
                     - min(substring(date, 1, 4)::int) AS span
            FROM results
            WHERE person_id IS NOT NULL
              AND date ~ '^(19|20)[0-9]{2}-'
            GROUP BY 1
        )
        SELECT 'XC'                                      AS sport,
               btrim(m.meet_name)                        AS meet_name,
               count(*)                                  AS races,
               count(DISTINCT r.person_id)               AS athletes,
               count(DISTINCT r.meet_id)                 AS meet_ids,
               min(r.date)                               AS first_date,
               max(r.date)                               AS last_date,
               round(avg(m.distance))                    AS mean_dist,
               100.0 * count(*) FILTER (
                   WHERE r.grade ~ '^0?([1-9]|1[0-2])$') / count(*)
                                                         AS scholastic_pct,
               100.0 * count(*) FILTER (
                   WHERE (m.level_mask & %(bits)s) <> 0) / count(*)
                                                         AS scholastic_mask_pct,
               percentile_cont(0.5) WITHIN GROUP
                   (ORDER BY r.speed_rating)             AS med_rating,
               percentile_cont(0.5) WITHIN GROUP
                   (ORDER BY c.span)                     AS med_career_span
        FROM results r
        JOIN meets m ON m.div_id = r.div_id AND m.source = r.source
        LEFT JOIN career c ON c.person_id = r.person_id
        WHERE m.meet_name IS NOT NULL
        GROUP BY 1, 2
        HAVING count(*) >= %(min_races)s
    """,
    "TF": """
        WITH career AS (
            SELECT person_id,
                   max(substring(date, 1, 4)::int)
                     - min(substring(date, 1, 4)::int) AS span
            FROM results_tf
            WHERE person_id IS NOT NULL
              AND date ~ '^(19|20)[0-9]{2}-'
            GROUP BY 1
        )
        SELECT 'TF'                                      AS sport,
               btrim(m.meet_name)                        AS meet_name,
               count(*)                                  AS races,
               count(DISTINCT r.person_id)               AS athletes,
               count(DISTINCT r.meet_id)                 AS meet_ids,
               min(r.date)                               AS first_date,
               max(r.date)                               AS last_date,
               round(avg(m.distance_meters))             AS mean_dist,
               100.0 * count(*) FILTER (
                   WHERE r.grade ~ '^0?([1-9]|1[0-2])$') / count(*)
                                                         AS scholastic_pct,
               100.0 * count(*) FILTER (
                   WHERE (m.level_mask & %(bits)s) <> 0) / count(*)
                                                         AS scholastic_mask_pct,
               percentile_cont(0.5) WITHIN GROUP
                   (ORDER BY r.speed_rating)             AS med_rating,
               percentile_cont(0.5) WITHIN GROUP
                   (ORDER BY c.span)                     AS med_career_span
        FROM results_tf r
        -- FOUR columns. meets_tf holds one row per EVENT: 14.17M rows over
        -- 658K (meet_id, div_id) pairs, ~21.5 each. Dropping meet_id and
        -- event_id from a diagnostic once produced a 900-billion-row nested
        -- loop that ran 17 minutes before being killed.
        JOIN meets_tf m
          ON m.meet_id  = r.meet_id
         AND m.div_id   = r.div_id
         AND m.event_id = r.event_id
         AND m.source   = r.source
        LEFT JOIN career c ON c.person_id = r.person_id
        WHERE m.meet_name IS NOT NULL
        GROUP BY 1, 2
        HAVING count(*) >= %(min_races)s
    """,
}


def proScore(row):
    """
    0-100, higher = less like a scholastic field.

    A WEIGHTED SUM, NOT A CLASSIFIER. Each term is a fraction of its own
    maximum, so no term can dominate on scale alone. The weights say which
    evidence is strongest, and the mask term is heaviest because it is the only
    one that is nearly impossible to fake -- a division that permits high
    schoolers has high schoolers in it.

    Nothing consumes this score. It exists to ORDER the list for reading.
    """
    mask_free = 1.0 - float(row["scholastic_mask_pct"] or 0) / 100.0
    grade_free = 1.0 - float(row["scholastic_pct"] or 0) / 100.0

    # Career span past 6 years: a high school career cannot exceed 4, so
    # anything beyond is post-scholastic. Capped at 12 so a handful of
    # 20-year masters careers cannot carry a meet on their own.
    span = float(row["med_career_span"] or 0)
    span_term = max(0.0, min(1.0, (span - 6.0) / 6.0))

    # Rating past 120: pool-relative, so 100 is the pool median and a
    # professional field sits far above it. Capped at 160.
    rating = float(row["med_rating"] or 0)
    rating_term = max(0.0, min(1.0, (rating - 120.0) / 40.0))

    return 100.0 * (0.45 * mask_free + 0.25 * grade_free
                    + 0.20 * span_term + 0.10 * rating_term)


def report(rows, top):
    rows = sorted(rows, key=proScore, reverse=True)

    print(f"\n  {'score':>6} {'sport':>5} {'races':>9} {'athl':>7} "
          f"{'dist':>6} {'sch%':>6} {'mask%':>6} {'rating':>7} "
          f"{'span':>5}  meet")
    print("  " + "-" * 110)

    for row in rows[:top]:
        print(f"  {proScore(row):>6.1f} {row['sport']:>5} "
              f"{int(row['races']):>9,} {int(row['athletes']):>7,} "
              f"{int(row['mean_dist'] or 0):>6} "
              f"{float(row['scholastic_pct'] or 0):>6.1f} "
              f"{float(row['scholastic_mask_pct'] or 0):>6.1f} "
              f"{float(row['med_rating'] or 0):>7.1f} "
              f"{float(row['med_career_span'] or 0):>5.1f}  "
              f"{row['meet_name'][:60]}")

    print("\n  sch%    rows with a plain grade 1-12. A pro meet is ~0.")
    print("  mask%   rows whose DIVISION permits MS/HS/elementary. The")
    print("          strongest signal -- it proves NOT-pro, not pro.")
    print("  rating  pool-relative median; 100 is the pool median.")
    print("  span    median years between an athlete's first and last race.")
    print("\n  READ THE NAMES. The score orders the list; it does not decide.")
    print("  A meet high on mask% is scholastic no matter what else it scores.")


def main():
    parser = argparse.ArgumentParser(
        description="Find candidate professional meets. Read only.")
    parser.add_argument("--sport", choices=["XC", "TF", "both"], default="both")
    parser.add_argument("--min-races", type=int, default=100,
                        help="minimum rows for a meet name to be considered")
    parser.add_argument("--top", type=int, default=60)
    args = parser.parse_args()

    sports = ("XC", "TF") if args.sport == "both" else (args.sport,)
    rows = []

    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET work_mem = '512MB'")
            for sport in sports:
                print(f"\n  scanning {sport}...")
                cur.execute(_SQL[sport], {"bits": SCHOLASTIC_BITS,
                                          "min_races": args.min_races})
                got = cur.fetchall()
                rows.extend(got)
                print(f"  {len(got):,} meet names with "
                      f">= {args.min_races} rows")

    print("\n" + "=" * 112)
    print(f"CANDIDATE PROFESSIONAL MEETS -- top {args.top} of {len(rows):,}")
    print("=" * 112)
    report(rows, args.top)
    print("\nNothing was written. This script is read only.\n")


if __name__ == "__main__":
    main()