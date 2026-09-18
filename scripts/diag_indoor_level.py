#!/usr/bin/env python3
"""
diag_indoor_level.py -- is an indoor track being rated as EASIER than outdoor?

    python scripts/diag_indoor_level.py
    python scripts/diag_indoor_level.py --since 2018

★ THE OWNER'S CLAIM (2026-09-18): "indoor is rated difficulty wise as if it is
  4% easy. This is obv not true." And, when told joint_solve had fixed it:
  "The indoor is not fixed. Also we don't use joint solve we use the bracketed
  engine."

  He is right about the engine. `bracket_engine.py:430` pins the vote-weighted
  mean of D per (sport, era), and `cell_sport` is ONE BIT -- XC or TF -- so
  indoor and outdoor track are pinned TOGETHER. Their combined mean is
  anchored; the split between them is free, and nothing asserts it.
  joint_solve asserts IND_LEVEL_DEFAULT = 0.012 (indoor 1.2% SLOWER) for
  exactly this reason. The bracket engine has no equivalent.

★ THIS MEASURES THE SIZE OF IT, CHEAPLY AND READ-ONLY, so the claim is a
  number before anything is changed. Two independent readings:

  1. THE SAME ATHLETE, THE SAME SEASON, BOTH SURFACES. The only comparison that
     cannot be explained by who races indoors: if a runner's indoor ratings sit
     systematically ABOVE their outdoor ones in the same winter-to-spring year,
     indoor is being treated as easier -- the rating is giving back more than
     the surface took.

     ⚠ THIS IS THE ONE TO BELIEVE. Comparing all indoor rows against all
       outdoor rows measures the field, not the surface: indoor meets are
       disproportionately collegiate and championship.

  2. The raw per-surface medians, for context only, with that caveat attached.

! is_indoor IS AN INTEGER, NOT A BOOLEAN (see model/feature_extraction.py:828
  -- `CASE WHEN m.is_indoor` is a type error), so it is compared to 1.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MIN_EACH = 3          # races on each surface before an athlete-year counts

# ⚠⚠ THIS FILLED THE SERVER'S TEMP SPACE ON ITS FIRST RUN (owner, 2026-09-18:
#    "DiskFull: could not write to file base/pgsql_tmp/..."), while an 11-hour
#    scrape was running. The first version asked for percentile_cont per
#    (person, season) over every rated results_tf row -- 192M rows sorted into
#    ~10M groups, which spills to disk without limit.
#
# ★ SO IT IS TWO CHEAP PASSES INSTEAD OF ONE EXPENSIVE ONE:
#      1. COUNT per athlete-year, which hash-aggregates and never sorts, to
#         find the few athlete-years that have races on BOTH surfaces;
#      2. compute the medians for ONLY those, which is a small set.
#    And scripts/pg_guard bounds the connection so a future mistake aborts the
#    query rather than the disk.
#
# ! AND THE DEFAULT WINDOW IS NARROW. An answer from four seasons is the same
#   answer; --since 2015 is available once it is known to be affordable.
DEFAULT_SINCE = 2021


def _hasCol(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                     AND column_name = %s""", (table, col))
    return cur.fetchone() is not None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=int, default=DEFAULT_SINCE,
                    help="narrow by default: the first run over "
                         "2015+ filled the server's temp space")
    ap.add_argument("--min-each", type=int, default=MIN_EACH)
    ap.add_argument("--athletes", action="store_true",
                    help="also run the within-athlete-year check. It scans "
                         "result rows and is the slow confirmation; section 1 "
                         "reads the published difficulty and is the answer.")
    args = ap.parse_args()

    from database import getConn
    from pg_guard import guard
    with getConn() as conn:
        with conn.cursor() as cur:
            # ! BEFORE ANY QUERY. A read-only diagnostic must not be able to
            #   fill the disk under a running scrape -- it did, once.
            guard(cur)
            if not _hasCol(cur, "meets_tf", "is_indoor"):
                raise SystemExit("meets_tf.is_indoor does not exist here")

            # ★★ THE CHEAP, DIRECT ANSWER, and the one this should have started
            #    with (owner, 2026-09-18: "idk what ur doing for that query but
            #    its def awful it should not be this hard"). Correct: the owner
            #    said indoor is wrong DIFFICULTY-wise, and the difficulty per
            #    cell is written down -- course_difficulties, 74k rows. The
            #    first version scanned 192M result rows to infer a number the
            #    engine already publishes, and filled the disk doing it.
            #
            # ! THE SURFACE IS IN THE CELL KEY. bracket_engine.priorGroupOfKeys
            #   reads it as: starts with "TF:", and the part before "@" ends
            #   with ":in" for indoor. So that is how it is asked here, rather
            #   than by guessing a prefix.
            print(f"\n=== 1. the difficulty the engine PUBLISHED, per surface "
                  f"===", flush=True)
            cur.execute("""
                SELECT CASE WHEN split_part(course_name, '@', 1) LIKE '%%:in'
                            THEN 'indoor' ELSE 'outdoor' END AS surface,
                       count(*),
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY difficulty),
                       avg(difficulty),
                       percentile_cont(0.25) WITHIN GROUP (ORDER BY difficulty),
                       percentile_cont(0.75) WITHIN GROUP (ORDER BY difficulty)
                FROM   course_difficulties
                WHERE  course_name LIKE 'TF:%%'
                  AND  difficulty IS NOT NULL AND difficulty > -0.9
                GROUP  BY 1 ORDER BY 1
            """)
            got = {r[0]: r[1:] for r in cur.fetchall()}
            print(f"    {'surface':<9} {'cells':>8} {'median':>9} {'mean':>9} "
                  f"{'p25':>9} {'p75':>9}")
            for surface in ("indoor", "outdoor"):
                if surface not in got:
                    print(f"    {surface:<9} {'--':>8}")
                    continue
                n, med, mean, q1, q3 = got[surface]
                print(f"    {surface:<9} {n:>8,} {med:>9.4f} {mean:>9.4f} "
                      f"{q1:>9.4f} {q3:>9.4f}")
            if "indoor" in got and "outdoor" in got:
                gap = float(got["indoor"][1]) - float(got["outdoor"][1])
                print(f"\n    indoor median MINUS outdoor median: {gap:+.4f}")
                # ★ SIGN AND SIZE, IN WORDS. difficulty is a fraction, so this
                #   reads directly as a percentage.
                if gap < -0.005:
                    print(f"    -> INDOOR IS PUBLISHED AS {abs(gap) * 100:.1f}% "
                          f"EASIER than outdoor. That is the owner's claim,\n"
                          f"       measured. bracket_engine pins the mean of D "
                          f"per (sport, era) with\n"
                          f"       sport as ONE BIT, so indoor and outdoor are "
                          f"anchored together and the\n"
                          f"       split between them is free -- nothing "
                          f"asserts an indoor level.")
                elif gap > 0.005:
                    print(f"    -> indoor is published as "
                          f"{gap * 100:.1f}% HARDER, which is the expected "
                          f"direction.")
                else:
                    print(f"    -> no material difference at this scale.")

            if not args.athletes:
                print(f"\n    (--athletes adds the within-athlete check, which "
                      f"scans result rows;\n     it is the slower confirmation, "
                      f"not the measurement.)")

            if args.athletes:
                print(f"\n=== 1b. the same athlete, the same year, both surfaces "
                      f"(from {args.since}) ===", flush=True)
                # ★ PASS 1: counts only. No sort, no spill -- this is what makes
                #   the whole thing affordable.
                print("    pass 1: which athlete-years have both surfaces...",
                      flush=True)
                cur.execute("""
                    CREATE TEMP TABLE _ind_both ON COMMIT DROP AS
                    SELECT r.person_id, substr(r.date, 1, 4)::int AS season
                    FROM   results_tf r
                    JOIN   meets_tf m ON m.div_id = r.div_id
                                     AND m.source = r.source
                    WHERE  r.speed_rating IS NOT NULL
                      AND  r.person_id IS NOT NULL
                      AND  r.date ~ '^(19|20)[0-9][0-9]-'
                      AND  substr(r.date, 1, 4)::int >= %(since)s
                    GROUP  BY 1, 2
                    HAVING count(*) FILTER (WHERE COALESCE(m.is_indoor, 0) = 1)
                               >= %(each)s
                       AND count(*) FILTER (WHERE COALESCE(m.is_indoor, 0) = 0)
                               >= %(each)s
                """, {"since": args.since, "each": args.min_each})
                cur.execute("CREATE INDEX ON _ind_both (person_id, season)")
                cur.execute("SELECT count(*) FROM _ind_both")
                n_both = cur.fetchone()[0]
                print(f"    athlete-years with >= {args.min_each} races on each "
                      f"surface: {n_both:,}")
                if not n_both:
                    print("    -> nothing to compare. Widen --since or lower "
                          "--min-each.")
                    n_both = 0

                # ★ PASS 2: medians for that small set only.
                print("    pass 2: the indoor-minus-outdoor gap for those...",
                      flush=True)
                cur.execute("""
                    WITH per AS (
                        SELECT b.person_id, b.season,
                               percentile_cont(0.5) WITHIN GROUP
                                   (ORDER BY r.speed_rating::double precision)
                                   FILTER (WHERE COALESCE(m.is_indoor, 0) = 1)
                                   AS med_in,
                               percentile_cont(0.5) WITHIN GROUP
                                   (ORDER BY r.speed_rating::double precision)
                                   FILTER (WHERE COALESCE(m.is_indoor, 0) = 0)
                                   AS med_out
                        FROM   _ind_both b
                        JOIN   results_tf r
                               ON r.person_id = b.person_id
                              AND substr(r.date, 1, 4)::int = b.season
                        JOIN   meets_tf m ON m.div_id = r.div_id
                                         AND m.source = r.source
                        WHERE  r.speed_rating IS NOT NULL
                        GROUP  BY 1, 2
                    )
                    SELECT count(*),
                           percentile_cont(0.5) WITHIN GROUP
                               (ORDER BY med_in - med_out),
                           avg(med_in - med_out),
                           percentile_cont(0.25) WITHIN GROUP
                               (ORDER BY med_in - med_out),
                           percentile_cont(0.75) WITHIN GROUP
                               (ORDER BY med_in - med_out)
                    FROM   per WHERE med_in IS NOT NULL AND med_out IS NOT NULL
                """)
                n, med, mean, q1, q3 = cur.fetchone()
                if n:
                    print(f"    indoor rating MINUS outdoor rating, same person, "
                          f"same year  (n = {n:,}):")
                    print(f"      median {med:+.3f}   mean {mean:+.3f}   "
                          f"IQR {q1:+.3f} .. {q3:+.3f}")
                    # ★ SAY WHICH WAY IS WRONG, in the output.
                    if med is not None and med > 0.3:
                        print(f"      -> INDOOR IS RATED TOO GENEROUSLY by about "
                              f"{med:+.2f} rating points.\n"
                              f"         The surface is being treated as easier "
                              f"than it is, so the\n"
                              f"         rating gives back more than it took. "
                              f"This is the owner's claim,\n"
                              f"         and bracket_engine has no asserted indoor "
                              f"level to stop it.")
                    elif med is not None and med < -0.3:
                        print(f"      -> indoor is rated too HARSHLY by "
                              f"{med:+.2f} points.")
                    else:
                        print(f"      -> no systematic surface bias at this scale.")

            print(f"\n=== 2. raw per-surface medians (context only) ===")
            cur.execute("""
                SELECT CASE WHEN COALESCE(m.is_indoor, 0) = 1
                            THEN 'indoor' ELSE 'outdoor' END AS surface,
                       count(*),
                       percentile_cont(0.5) WITHIN GROUP
                           (ORDER BY r.speed_rating::double precision)
                FROM   results_tf r
                JOIN   meets_tf m ON m.div_id = r.div_id AND m.source = r.source
                WHERE  r.speed_rating IS NOT NULL
                  AND  r.date ~ '^(19|20)[0-9][0-9]-'
                  AND  substr(r.date, 1, 4)::int >= %(since)s
                GROUP  BY 1 ORDER BY 1
            """, {"since": args.since})
            for surface, cnt, med2 in cur.fetchall():
                print(f"    {surface:<8} {cnt:>14,} rows   median rating "
                      f"{med2:8.2f}")
            print(f"    ⚠ do NOT read a surface bias off these two numbers: "
                  f"indoor meets are\n      disproportionately collegiate and "
                  f"championship, so this compares fields,\n      not "
                  f"surfaces. Section 1 is the one that controls for that.")
        conn.rollback()


if __name__ == "__main__":
    main()
