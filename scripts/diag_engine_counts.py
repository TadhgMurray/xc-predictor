#!/usr/bin/env python3
"""Two counts that decide two engine questions, before anyone writes code.

    python scripts/diag_engine_counts.py
    python scripts/diag_engine_counts.py --exact        # no sampling

★ BOTH ARE "IS THIS WORTH DOING", NOT "HOW DO I DO IT". Each answers a
  question that has been argued twice in this session and measured zero
  times.

  A. HOW MUCH OF THE CORPUS IS RATED AGAINST A COURSE NOBODY MEASURED?

     COALESCE(cd.difficulty, 0.0) reads an unfitted course as difficulty 0
     -- EXACTLY AVERAGE -- and computeAthleteAbilities' own comment calls
     that "what we do not know this course should mean". It is not: zero is
     an assertion. If the course is really hard, everyone who raced it looks
     slow, the difficulty is absorbed into their ABILITY, and because the
     solve is joint that error then leaks into every other course they ran.
     One unmeasured course pollutes a neighbourhood of the graph.

     And it is silent. Such a rating renders identically to one built on 400
     measured courses.

     If this is 2% it is a footnote. If it is 20% it is a headline, and it
     would partly explain why close athletes cannot be separated.

  B. IS THE TRACK ANCHOR EVEN AVAILABLE?

     speed_ratings re-centres difficulties to RESULT-WEIGHTED MEAN ZERO, so
     "difficulty 0" means "the average course in our corpus right now" -- a
     number that MOVES when the corpus changes. A physical reference does
     not: an outdoor track 5000 m is the same thing in 2019 and 2026.
     Anchoring XC to the zero TF already uses would also turn the fitted
     sport gap into a testable quantity rather than a free parameter.

     ⚠ BUT THE ANCHOR POPULATION IS SELECTIVE. Track 5000 m fields are
     mostly good distance runners. Pinning a corpus of everyone to a thin,
     non-representative slice can be WORSE than the corpus mean. So: how
     many athletes actually have both a rated XC season and an outdoor track
     5000 m, and are they representative?

! SAMPLED BY DEFAULT AND IT SAYS SO. A is a join over tens of millions of
  rows; a share is the one thing sampling answers honestly. --exact when the
  number matters.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("racecast", "scripts", "engine", "model"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import psycopg2.extras                                        # noqa: E402
from database import getConn                                  # noqa: E402


def pct(a, b):
    return f"{100.0 * a / b:.1f}%" if b else "--"


# ------------------------------------------------------------------ #
#  A. rated rows sitting on an imputed difficulty
# ------------------------------------------------------------------ #

def sectionA(cur, sample):
    print("\n" + "=" * 70)
    print("A. RATED ROWS WHOSE COURSE WAS NEVER MEASURED")
    print("=" * 70)
    tbl = "results r" + (f" TABLESAMPLE SYSTEM ({sample})" if sample else "")
    if sample:
        print(f"   ({sample}% sample -- a share is what sampling answers "
              f"honestly; --exact for the count)")
    cur.execute(f"""
        SELECT split_part(split_part(r.rating_pool, '|', 1), '_', 1) AS lvl,
               count(*)                                   AS rated,
               count(*) FILTER (WHERE cd.difficulty IS NULL) AS imputed,
               count(*) FILTER (WHERE cc.canonical_id IS NULL) AS no_venue
        FROM   {tbl}
        LEFT   JOIN meets m ON m.meet_id = r.meet_id
                           AND m.div_id  = r.div_id
                           AND m.source  = r.source
        -- ⚠ AND meets_tfrrs, WHICH THE FIRST VERSION OF THIS QUERY MISSED.
        --   `meets` is the anet table; tfrrs XC venues live in meets_tfrrs,
        --   keyed on meet_id alone. Joining only `meets` reported 26.7% of
        --   COLLEGE rows as having no venue -- which was this query's bug,
        --   not the engine's: speed_ratings_db._xcQuery COALESCEs the two
        --   exactly as below. Measuring the engine with a join the engine
        --   does not use measures nothing.
        LEFT   JOIN meets_tfrrs mt ON r.source = 'tfrrs'
                                  AND mt.meet_id = r.meet_id
                                  AND mt.sport = 'XC'
        LEFT   JOIN course_canonical cc
                    ON cc.course_name = COALESCE(m.course_name, mt.venue_name)
                   AND round(cc.gps_lat::numeric,  5)
                       = round(COALESCE(m.gps_lat, mt.gps_lat)::numeric,  5)
                   AND round(cc.gps_long::numeric, 5)
                       = round(COALESCE(m.gps_long, mt.gps_long)::numeric, 5)
        LEFT   JOIN course_difficulties cd
                    ON cd.canonical_id = cc.canonical_id
                   AND cd.distance_m = (round(COALESCE(
                           m.distance,
                           (mt.division_distances -> r.div_id::text
                              ->> 'distance')::real,
                           mt.distance) / 100.0) * 100)::int
        WHERE  r.speed_rating IS NOT NULL
        GROUP  BY 1
        ORDER  BY 2 DESC
    """)
    rows = [dict(x) for x in cur.fetchall()]
    if not rows:
        print("   no rated rows found")
        return
    print(f"\n   {'level':<12}{'rated':>12}{'d=0 imputed':>14}"
          f"{'share':>9}{'no venue at all':>17}")
    tot = imp = 0
    for r in rows:
        tot += r["rated"]
        imp += r["imputed"]
        print(f"   {str(r['lvl'] or '?'):<12}{r['rated']:>12,}"
              f"{r['imputed']:>14,}{pct(r['imputed'], r['rated']):>9}"
              f"{r['no_venue']:>17,}")
    print(f"\n   OVERALL {pct(imp, tot)} of rated rows sit on a course with "
          f"no fitted difficulty.")
    share = imp / tot if tot else 0
    if share < 0.03:
        print("   → a footnote. Mark it in the UI if convenient, do not "
              "re-plumb the engine.")
    elif share < 0.12:
        print("   → worth marking on the page, and worth excluding from the "
              "SOLVE's\n     evidence even if the rating still renders.")
    else:
        print("   ⚠ A HEADLINE. This much imputed 'exactly average' is a "
              "large silent\n     error source, and because the solve is "
              "joint it does not stay local.")


# ------------------------------------------------------------------ #
#  B. is there an anchor population
# ------------------------------------------------------------------ #

def sectionB(cur):
    print("\n" + "=" * 70)
    print("B. IS THE OUTDOOR-TRACK-5000 ANCHOR AVAILABLE?")
    print("=" * 70)
    cur.execute("""
        WITH xc AS (
            SELECT DISTINCT person_id,
                   split_part(split_part(pool, '|', 1), '_', 1) AS lvl
            FROM   athlete_season
            WHERE  sport = 'XC' AND mean_rating IS NOT NULL
        ), tf AS (
            SELECT DISTINCT r.person_id
            FROM   results_tf r
            JOIN   meets_tf m ON m.meet_id  = r.meet_id
                             AND m.div_id   = r.div_id
                             AND m.event_id = r.event_id
            WHERE  r.person_id IS NOT NULL
              AND  r.time_seconds IS NOT NULL
              AND  COALESCE(m.is_indoor, 0) = 0
              AND  m.distance_meters BETWEEN 4900 AND 5100
        )
        SELECT xc.lvl,
               count(*)                                        AS rated_xc,
               count(*) FILTER (WHERE tf.person_id IS NOT NULL) AS both
        FROM   xc LEFT JOIN tf ON tf.person_id = xc.person_id
        GROUP  BY 1
        ORDER  BY 2 DESC
    """)
    rows = [dict(x) for x in cur.fetchall()]
    if not rows:
        print("   nothing to count")
        return
    print(f"\n   {'level':<12}{'rated in XC':>14}{'+ outdoor 5000':>16}"
          f"{'share':>9}")
    tot = both = 0
    for r in rows:
        tot += r["rated_xc"]
        both += r["both"]
        print(f"   {str(r['lvl'] or '?'):<12}{r['rated_xc']:>14,}"
              f"{r['both']:>16,}{pct(r['both'], r['rated_xc']):>9}")
    print(f"\n   OVERALL {both:,} of {tot:,} ({pct(both, tot)}) rated XC "
          f"athletes have an outdoor track 5000.")

    # ⚠ AND ARE THEY THE SAME PEOPLE? An anchor fitted on the fast half of
    #   the corpus pins the scale where the fast half lives.
    cur.execute("""
        WITH tf AS (
            SELECT DISTINCT r.person_id
            FROM   results_tf r
            JOIN   meets_tf m ON m.meet_id  = r.meet_id
                             AND m.div_id   = r.div_id
                             AND m.event_id = r.event_id
            WHERE  r.person_id IS NOT NULL
              AND  r.time_seconds IS NOT NULL
              AND  COALESCE(m.is_indoor, 0) = 0
              AND  m.distance_meters BETWEEN 4900 AND 5100
        )
        SELECT (tf.person_id IS NOT NULL) AS has_5k,
               count(*) AS n,
               round(avg(s.mean_rating)::numeric, 1) AS mean_rating,
               round(percentile_cont(0.5) WITHIN GROUP
                     (ORDER BY s.mean_rating)::numeric, 1) AS median_rating
        FROM   athlete_season s
        LEFT   JOIN tf ON tf.person_id = s.person_id
        WHERE  s.sport = 'XC' AND s.mean_rating IS NOT NULL
        GROUP  BY 1
    """)
    print(f"\n   {'group':<24}{'n':>12}{'mean rating':>14}"
          f"{'median':>10}")
    for r in cur.fetchall():
        label = ("has an outdoor 5000" if r["has_5k"] else "XC only")
        print(f"   {label:<24}{r['n']:>12,}{float(r['mean_rating'] or 0):>14.1f}"
              f"{float(r['median_rating'] or 0):>10.1f}")
    print("\n   ★ IF THE TWO ROWS' RATINGS ARE CLOSE, the anchor population "
          "is\n     representative and the gauge can move onto it. If the "
          "5000 group is\n     much faster, anchoring there pins the scale "
          "where the fast half lives\n     and the corpus mean is the "
          "safer zero.")


# ------------------------------------------------------------------ #
#  C. does the MODEL's extraction see the same corpus the engine rates?
# ------------------------------------------------------------------ #

def sectionC(cur, sample):
    """⚠ A HYPOTHESIS WITH TWO READINGS, AND ONLY A COUNT SETTLES IT.

    feature_extraction._XC_SQL joins the venue with a bare

        JOIN meets m ON r.div_id = m.div_id
                    AND r.meet_id = m.meet_id
                    AND r.source  = m.source

    -- an INNER join, and the file contains ZERO references to meets_tfrrs.
    The engine's own _xcQuery LEFT JOINs both tables and COALESCEs them.

    Reading one: speed_ratings_db's header lists "INNER JOIN meets --
    anet-only table; deleted tfrrs again" among the bugs it FIXED, and
    build_course_canonical UNIONs meets with meets_tfrrs, which would be
    pointless if `meets` already held tfrrs venues. If that is right, every
    tfrrs-sourced XC result is dropped from the TRAINING CORPUS -- and
    college XC is largely tfrrs.

    Reading two: extraction's own comment says "`meets` is disambiguated by
    source", implying `meets` carries more than one source and the join is
    fine.

    Both cannot be true. This counts it.

    ★ IT IS A MODEL BUG IF IT IS ONE, NOT AN ENGINE BUG. The engine rates
      these rows correctly; the question is whether the transformer was ever
      shown them.
    """
    print("\n" + "=" * 70)
    print("C. DOES THE MODEL'S EXTRACTION SEE THE WHOLE CORPUS?")
    print("=" * 70)
    tbl = "results r" + (f" TABLESAMPLE SYSTEM ({sample})" if sample else "")
    if sample:
        print(f"   ({sample}% sample)")
    cur.execute(f"""
        SELECT r.source,
               split_part(split_part(r.rating_pool, '|', 1), '_', 1) AS lvl,
               count(*) AS rated,
               count(*) FILTER (WHERE m.meet_id IS NOT NULL) AS in_meets
        FROM   {tbl}
        LEFT   JOIN meets m ON m.meet_id = r.meet_id
                           AND m.div_id  = r.div_id
                           AND m.source  = r.source
        WHERE  r.speed_rating IS NOT NULL
        GROUP  BY 1, 2
        ORDER  BY 3 DESC
    """)
    rows = [dict(x) for x in cur.fetchall()]
    if not rows:
        print("   nothing rated")
        return
    print(f"\n   {'source':<10}{'level':<10}{'rated':>12}"
          f"{'in `meets`':>13}{'REACHES TRAINING':>19}")
    tot = keep = 0
    for r in rows:
        tot += r["rated"]
        keep += r["in_meets"]
        print(f"   {str(r['source'] or '?'):<10}{str(r['lvl'] or '?'):<10}"
              f"{r['rated']:>12,}{r['in_meets']:>13,}"
              f"{pct(r['in_meets'], r['rated']):>19}")
    print(f"\n   OVERALL {pct(keep, tot)} of rated XC rows can survive "
          f"extraction's INNER JOIN.")
    lost = 1.0 - (keep / tot if tot else 1.0)
    if lost < 0.02:
        print("   → `meets` carries every source. The extraction join is "
              "fine and\n     reading two is right.")
    else:
        print("   ⚠ READING ONE. Those rows are rated by the engine, shown "
              "on the site,")
        print("     and INVISIBLE TO THE MODEL -- an inner join to a table "
              "that does not")
        print("     hold their source drops them before a single feature is "
              "built.")
        print("     The engine's header lists this exact bug among ones it "
              "already fixed")
        print("     once: 'INNER JOIN meets -- anet-only table; deleted "
              "tfrrs again.'")
        print("     Fix is extraction-side and needs a RE-EXTRACTION.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exact", action="store_true",
                    help="no sampling in section A (slow, exact)")
    ap.add_argument("--sample", type=float, default=1.0,
                    help="percent of results to sample for A. Default 1.")
    ap.add_argument("--skip", default="")
    a = ap.parse_args()
    skip = {s.strip().upper() for s in a.skip.split(",") if s.strip()}

    with getConn() as conn:
        with conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if "A" not in skip:
                sectionA(cur, None if a.exact else a.sample)
            if "B" not in skip:
                sectionB(cur)
            if "C" not in skip:
                sectionC(cur, None if a.exact else a.sample)
    print()


if __name__ == "__main__":
    main()
