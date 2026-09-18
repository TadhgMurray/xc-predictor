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


def _hasCol(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                     AND column_name = %s""", (table, col))
    return cur.fetchone() is not None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=int, default=2015)
    ap.add_argument("--min-each", type=int, default=MIN_EACH)
    args = ap.parse_args()

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            if not _hasCol(cur, "meets_tf", "is_indoor"):
                raise SystemExit("meets_tf.is_indoor does not exist here")

            print(f"\n=== 1. the same athlete, the same year, both surfaces "
                  f"(from {args.since}) ===", flush=True)
            # ★ WITHIN-ATHLETE-YEAR, so the field cannot produce the effect.
            cur.execute("""
                WITH r AS MATERIALIZED (
                    SELECT r.person_id,
                           substr(r.date, 1, 4)::int AS season,
                           CASE WHEN COALESCE(m.is_indoor, 0) = 1
                                THEN 1 ELSE 0 END AS ind,
                           r.speed_rating::double precision AS rating
                    FROM   results_tf r
                    JOIN   meets_tf m ON m.div_id = r.div_id
                                     AND m.source = r.source
                    WHERE  r.speed_rating IS NOT NULL
                      AND  r.person_id IS NOT NULL
                      AND  r.date ~ '^(19|20)[0-9][0-9]-'
                      AND  substr(r.date, 1, 4)::int >= %(since)s
                ), per AS MATERIALIZED (
                    SELECT person_id, season,
                           count(*) FILTER (WHERE ind = 1) AS n_in,
                           count(*) FILTER (WHERE ind = 0) AS n_out,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY rating)
                               FILTER (WHERE ind = 1) AS med_in,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY rating)
                               FILTER (WHERE ind = 0) AS med_out
                    FROM   r GROUP BY person_id, season
                )
                SELECT count(*),
                       percentile_cont(0.5) WITHIN GROUP
                           (ORDER BY med_in - med_out),
                       avg(med_in - med_out),
                       percentile_cont(0.25) WITHIN GROUP
                           (ORDER BY med_in - med_out),
                       percentile_cont(0.75) WITHIN GROUP
                           (ORDER BY med_in - med_out)
                FROM   per
                WHERE  n_in >= %(each)s AND n_out >= %(each)s
            """, {"since": args.since, "each": args.min_each})
            n, med, mean, q1, q3 = cur.fetchone()
            print(f"    athlete-years with >= {args.min_each} races on each "
                  f"surface: {n:,}")
            if n:
                print(f"    indoor rating MINUS outdoor rating, same person, "
                      f"same year:")
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
