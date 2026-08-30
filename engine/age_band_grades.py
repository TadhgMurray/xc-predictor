"""
age_band_grades.py -- decide, per division, whether "11-12" means two GRADES
or two AGES. Issue #47.

    python engine/age_band_grades.py              # report, writes nothing
    python engine/age_band_grades.py --write      # build age_band_result
    python engine/age_band_grades.py --review 40  # the divisions, by name

Run from the PROJECT ROOT, BEFORE grade_sanity.py -- it decides what a grade
SAYS, and every rule downstream reads the answer.

★ THE COLLISION. normalize_distance.GRADE_TO_LEVEL maps

        "7-8": "ms",  "9-10": "hs",  "11-12": "hs"

  because a school meet writes banded GRADES that way. USATF youth meets
  write banded AGES in exactly the same shape, and eleven-year-olds are not
  high school juniors. Measured on one 5000 m at a professional meet:

        Grant Fisher      19+      13:03.86   129.9
        William Kincaid   15-16    13:06.70   129.4
        Sean McGorty      11-12    13:14.97   152.4   <-- professional
        Thomas Ratcliffe  11-12    13:17.48   151.9   <-- professional

  Two professionals rated twenty-four points above the man who beat them,
  because "11-12" resolved to hs and hs_m's pool mean is far slower than the
  company they were actually keeping. rating = 100 * pool_mean / ability, so
  a pool that is 20% slower inflates by 20%.

★ THE DISCRIMINATOR IS ALREADY IN THE ROW, AND IT IS THE FIELD.
  "13-14", "15-16", "17-18" and "19+" have NO grade reading -- there is no
  thirteenth grade -- which is exactly why normGrade leaves them unmapped.
  They are therefore unambiguous evidence about the meet's convention:

      IF A DIVISION CONTAINS ANY UNAMBIGUOUS AGE BAND, THEN EVERY BANDED
      GRADE IN THAT DIVISION IS AN AGE BAND TOO.

  The race above carries 19+ and 15-16 beside 11-12, so 11-12 there means
  eleven- and twelve-year-olds and must resolve to no pool at all.

  This is grade_sanity's rule 5d in another costume -- a verdict about one
  entrant taken from what the rest of the field carries -- and it needs no
  new data, no model, and no per-meet list.

⚠ AND IT IS DELIBERATELY NOT THE SCHOOL STRING. "Nike Bowerman TC" is the
  tempting signal and HANDOFF §5 is a monument to why it fails: `Puma` is 161
  people with grades 1-12 and a 6.54 s best, AND Alex Botterill running
  1:45.6. One string, two populations, no rule that separates them. The field
  convention is a fact about the MEET, which is the thing actually in
  question.

! IT NULLS A GRADE, IT DOES NOT ASSIGN ONE. Knowing that "11-12" means ages
  does not tell you the athlete's grade -- an eleven-year-old is in about
  sixth. So the row simply loses its grade, and resolvePool falls through to
  the evidence it already trusts: the level of the field raced. Guessing a
  grade from an age would be inventing the thing this file exists to stop.

! AND IT FLAGS RESULTS, NOT PEOPLE. A pro who ran one youth-labelled meet as
  a teenager is not permanently ungradeable; only that division's rows are.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from psycopg2.extras import RealDictCursor

from database import getConn


# ★ THE UNAMBIGUOUS ONES -- bands with no possible grade reading. Taken from
#   normalize_distance's own "deliberately NOT mapped" list, which is the
#   record of what the corpus actually writes:
#       '13-14' '15-16' '17-18' '19+' '6U'
#
# ! '6U' IS IN, AND IT IS THE ONE THAT LOOKS WRONG. "6 and Under" is an age
#   band; "6" alone is a real grade. The two-character form only ever means
#   the former, and it appears beside the others.
#
# ⚠ '19+' EARNS ITS PLACE TWICE. It is unambiguous on its own AND it is the
#   commonest marker at exactly the meets where this matters -- open and
#   professional fields, where a banded grade beside it is certainly an age.
UNAMBIGUOUS_AGE = (r"^(13-14|15-16|17-18|19\+|6U|20\+|open)$")

# The ambiguous ones: real GRADE_TO_LEVEL keys that are also age bands.
# ! KEPT IN SYNC WITH GRADE_TO_LEVEL BY HAND, and asserted below rather than
#   trusted -- a key added there and missed here would be a silent hole.
AMBIGUOUS_BAND = (r"^(7-8|9-10|11-12)$")

# A division needs this many entrants carrying an unambiguous age band before
# its convention is settled.
# ★ ONE IS ENOUGH IN PRINCIPLE and two is what this uses, for the reason
#   grade_sanity's rule 2 gives: a single row can be a typo, and the cost of
#   being wrong here is stripping a real high schooler's grade.
MIN_AGE_MARKERS = 2


_DIVISIONS = """
    DROP TABLE IF EXISTS age_band_div;
    CREATE UNLOGGED TABLE age_band_div AS
    -- ! BOTH SPORTS, ONE PASS. The collision is a meet-naming convention and
    --   USATF youth meets run track and cross country alike.
    WITH allrows AS (
        SELECT 'XC'::text AS sport, meet_id, div_id, grade FROM results
        UNION ALL
        SELECT 'TF',            meet_id, div_id, grade FROM results_tf
    )
    SELECT sport, meet_id, div_id,
           count(*) FILTER (WHERE lower(btrim(grade)) ~ '{age}')  AS n_age,
           count(*) FILTER (WHERE lower(btrim(grade)) ~ '{band}') AS n_band,
           count(*)                                               AS n_rows
    FROM   allrows
    WHERE  grade IS NOT NULL
    GROUP  BY 1, 2, 3
    HAVING count(*) FILTER (WHERE lower(btrim(grade)) ~ '{age}') >= {min_age}
       AND count(*) FILTER (WHERE lower(btrim(grade)) ~ '{band}') > 0;
    CREATE INDEX ON age_band_div (sport, meet_id, div_id);
    ANALYZE age_band_div;
"""

# The rows whose grade must be read as an age and therefore discarded.
_RESULTS = """
    DROP TABLE IF EXISTS age_band_result;
    CREATE TABLE age_band_result AS
        SELECT 'XC'::text AS sport, r.result_id, r.person_id, r.grade
        FROM   results r
        JOIN   age_band_div d ON d.sport = 'XC'
                             AND d.meet_id = r.meet_id
                             AND d.div_id  = r.div_id
        WHERE  lower(btrim(r.grade)) ~ '{band}'
        UNION ALL
        SELECT 'TF', r.result_id, r.person_id, r.grade
        FROM   results_tf r
        JOIN   age_band_div d ON d.sport = 'TF'
                             AND d.meet_id = r.meet_id
                             AND d.div_id  = r.div_id
        WHERE  lower(btrim(r.grade)) ~ '{band}';
    ALTER TABLE age_band_result ADD PRIMARY KEY (sport, result_id);
    CREATE INDEX ON age_band_result (person_id);
    ANALYZE age_band_result;
"""

_SUMMARY = """
    SELECT (SELECT count(*) FROM age_band_div)                 AS divisions,
           (SELECT count(*) FROM age_band_result)              AS rows_flagged,
           (SELECT count(DISTINCT person_id) FROM age_band_result)
                                                               AS people,
           (SELECT count(*) FROM age_band_result WHERE sport = 'XC') AS xc,
           (SELECT count(*) FROM age_band_result WHERE sport = 'TF') AS tf
"""

_REVIEW = """
    SELECT d.sport, d.meet_id, d.div_id, d.n_age, d.n_band, d.n_rows,
           COALESCE(m.division, mt.meet_name, '')  AS label
    FROM   age_band_div d
    LEFT   JOIN meets m       ON m.div_id  = d.div_id
    LEFT   JOIN meets_tfrrs mt ON mt.meet_id = d.meet_id
    ORDER  BY d.n_band DESC
    LIMIT  %(lim)s
"""


def main():
    ap = argparse.ArgumentParser(
        description="Divisions where a banded grade means an AGE, not a grade.")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--review", type=int, default=0, metavar="N")
    args = ap.parse_args()

    # ! ASSERTED, NOT ASSUMED. AMBIGUOUS_BAND must name exactly the hyphenated
    #   keys GRADE_TO_LEVEL maps. One added there and missed here is a hole
    #   that reports success while letting the next McGorty through.
    from normalize_distance import GRADE_TO_LEVEL
    mapped = {k for k in GRADE_TO_LEVEL if "-" in k}
    listed = set(AMBIGUOUS_BAND.strip("^$()").split("|"))
    if mapped != listed:
        sys.exit(f"AMBIGUOUS_BAND is out of step with GRADE_TO_LEVEL:\n"
                 f"  mapped but not listed: {sorted(mapped - listed)}\n"
                 f"  listed but not mapped: {sorted(listed - mapped)}")

    with getConn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Built even for a dry run, then rolled back -- the report has to
        # describe what --write WOULD do. Same argument as wheelchair_flag.
        cur.execute(_DIVISIONS.format(age=UNAMBIGUOUS_AGE, band=AMBIGUOUS_BAND,
                                      min_age=MIN_AGE_MARKERS))
        cur.execute(_RESULTS.format(band=AMBIGUOUS_BAND))

        cur.execute(_SUMMARY)
        s = cur.fetchone()
        print(f"\n{s['divisions']:,} divisions read banded grades as AGES "
              f"(>= {MIN_AGE_MARKERS} unambiguous age markers in the field)")
        print(f"  {s['rows_flagged']:,} results lose their grade "
              f"({s['xc']:,} XC, {s['tf']:,} TF), across "
              f"{s['people']:,} people")
        print("  they keep every race; resolvePool falls through to the "
              "level of the field, which is evidence rather than a guess")

        if args.review:
            cur.execute(_REVIEW, {"lim": args.review})
            print("\nthe divisions, worst first:")
            for r in cur.fetchall():
                print(f"  {r['sport']}  {r['meet_id']}/{r['div_id']}  "
                      f"age={r['n_age']:>4} band={r['n_band']:>4} "
                      f"of {r['n_rows']:>4}   {(r['label'] or '')[:44]}")

        if args.write:
            conn.commit()
            print("\n[ageband] wrote age_band_div and age_band_result")
            print("          grade_sanity and the backfill must skip these "
                  "grades on the next run")
        else:
            conn.rollback()
            print("\n       DRY RUN -- nothing written. Pass --write.")


if __name__ == "__main__":
    main()
