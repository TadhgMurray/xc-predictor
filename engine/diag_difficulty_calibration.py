#!/usr/bin/env python3
"""
diag_difficulty_calibration.py -- is difficulty CALIBRATED, or merely monotone?

    python engine/diag_difficulty_calibration.py --since 2015
    python engine/diag_difficulty_calibration.py --since 2018 --sport TF
    python engine/diag_difficulty_calibration.py --since 2015 --bins 8

★ THE OWNER'S QUESTION (2026-09-18): "I want to see if as difficulty increases
  does the median of each percentile runner actually have their rating increase
  by such, or are they overfed. Generally feels like it."

★ WHY THIS IS THE FIRST THING TO MEASURE. A difficulty term can be monotone --
  harder courses get bigger numbers -- and still be wrong, because what matters
  is whether it is the RIGHT SIZE. The test needs no new model:

    if difficulty is honest, it has already been removed from the ratings, so
    the median rating of (say) the 50th-percentile finisher is THE SAME on hard
    courses as on easy ones. A rising line means hard races are over-credited
    -- the owner's "overfed" -- and the slope is how much, per point of
    difficulty, per percentile.

⚠ THE PERCENTILE IS WITHIN THE RACE, NOT THE POOL, and that is the whole
  design. Comparing "everyone on hard courses" with "everyone on easy courses"
  measures who SHOWS UP, not what the difficulty term did: championship courses
  are hard AND hold better fields, and that selection effect is far larger than
  the calibration error being looked for. Ranking inside each race and then
  comparing like position with like position removes it.

⚠ AND A FLAT LINE IS THE PASS CONDITION, not a rising one. It is easy to read
  this table backwards: we WANT no relationship. A positive slope is the bug.

! READ-ONLY, and it changes nothing. It reports a table and a slope per
  percentile band.

Output, per sport: difficulty bins down the side, percentile bands across, the
median speed_rating in each cell, then the slope of each column.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The positions compared. Inside each race, so a "10" is the runner a tenth of
# the way down THAT field.
BANDS = ((0.10, "p10"), (0.25, "p25"), (0.50, "p50"), (0.75, "p75"),
         (0.90, "p90"))
BAND_HALFWIDTH = 0.04          # a band is the runners within +/- this of it

# ! A RACE TOO SMALL HAS NO PERCENTILES. With eight finishers the 10th and 25th
#   percentile are the same person.
MIN_FIELD = 25

# ! AND A COURSE WITH TWO RACES HAS NO DIFFICULTY WORTH BINNING.
MIN_COURSE_RESULTS = 200

TABLES = {"XC": "results", "TF": "results_tf"}


def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    got = cur.fetchone()
    return bool(got[0] if not isinstance(got, dict) else list(got.values())[0])


def _sql(table, bins):
    """Median rating per (difficulty bin, percentile band).

    ★ ONE PASS. percent_rank() gives each row its position inside its own
      race; ntile() over the courses gives the difficulty bins; the bands are
      a lateral join so a row lands in at most one. Every CTE MATERIALIZED so
      none is re-run per row.
    """
    band_rows = ", ".join(f"({p}, '{name}')" for p, name in BANDS)
    return f"""
        WITH cd AS MATERIALIZED (
            SELECT course_name, difficulty
            FROM   course_difficulties
            WHERE  difficulty IS NOT NULL
              AND  COALESCE(n_results, 0) >= %(min_course)s
        ), binned AS MATERIALIZED (
            SELECT course_name, difficulty,
                   ntile({bins}) OVER (ORDER BY difficulty) AS dbin
            FROM   cd
        ), r AS MATERIALIZED (
            SELECT r.div_id, r.speed_rating::double precision AS rating,
                   b.dbin, b.difficulty,
                   percent_rank() OVER (PARTITION BY r.div_id, r.source
                                        ORDER BY r.speed_rating DESC) AS pr,
                   count(*) OVER (PARTITION BY r.div_id, r.source) AS field
            FROM   {table} r
            JOIN   meets m ON m.div_id = r.div_id AND m.source = r.source
            JOIN   binned b ON b.course_name = m.course_name
            WHERE  r.speed_rating IS NOT NULL
              AND  r.date ~ '^(19|20)[0-9][0-9]-'
              AND  substr(r.date, 1, 4)::int >= %(since)s
        ), band AS (
            SELECT * FROM (VALUES {band_rows}) AS t(p, name)
        )
        SELECT r.dbin, band.name,
               count(*)                                            AS n,
               avg(r.difficulty)                                   AS diff,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY r.rating) AS med
        FROM   r JOIN band
               ON r.pr BETWEEN band.p - %(hw)s AND band.p + %(hw)s
        WHERE  r.field >= %(min_field)s
        GROUP  BY r.dbin, band.name
        ORDER  BY r.dbin, band.name
    """


def slope(xs, ys):
    """Least squares slope of y on x. Pure; None when undefined."""
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 2:
        return None
    n = float(len(pts))
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    den = sum((x - mx) ** 2 for x, _ in pts)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in pts) / den


def report(rows, sport, bins):
    if not rows:
        print(f"  {sport}: no rated rows on binned courses.")
        return
    names = [n for _p, n in BANDS]
    cells, diff_of_bin, n_of_bin = {}, {}, {}
    for dbin, name, n, diff, med in rows:
        cells[(int(dbin), name)] = (float(med) if med is not None else None)
        diff_of_bin[int(dbin)] = float(diff) if diff is not None else None
        n_of_bin[int(dbin)] = n_of_bin.get(int(dbin), 0) + int(n)

    print(f"\n  === {sport}: median rating by difficulty bin and "
          f"within-race percentile ===")
    print(f"    {'bin':>4} {'difficulty':>11} {'rows':>10}  "
          + "".join(f"{nm:>9}" for nm in names))
    for b in sorted(diff_of_bin):
        d = diff_of_bin[b]
        line = f"    {b:>4} {d:>11.3f} {n_of_bin.get(b, 0):>10,}  "
        line += "".join(
            (f"{cells[(b, nm)]:>9.2f}" if cells.get((b, nm)) is not None
             else f"{'--':>9}") for nm in names)
        print(line)

    print(f"\n    slope of each column, rating points per point of difficulty:")
    print(f"    {'band':>8} {'slope':>10}   reading")
    for nm in names:
        xs = [diff_of_bin[b] for b in sorted(diff_of_bin)]
        ys = [cells.get((b, nm)) for b in sorted(diff_of_bin)]
        s = slope(xs, ys)
        if s is None:
            print(f"    {nm:>8} {'--':>10}")
            continue
        # ★ FLAT IS THE PASS. A positive slope is the owner's "overfed".
        reading = ("flat -- difficulty looks calibrated here"
                   if abs(s) < 0.05 else
                   f"OVERFED: harder courses credit this runner "
                   f"{s:+.2f}/difficulty point"
                   if s > 0 else
                   f"UNDER-credited by {s:+.2f}/difficulty point")
        print(f"    {nm:>8} {s:>10.3f}   {reading}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=int, default=2015)
    ap.add_argument("--sport", default="XC,TF")
    ap.add_argument("--bins", type=int, default=10)
    ap.add_argument("--min-field", type=int, default=MIN_FIELD)
    ap.add_argument("--min-course-results", type=int,
                    default=MIN_COURSE_RESULTS)
    ap.add_argument("--band-halfwidth", type=float, default=BAND_HALFWIDTH)
    args = ap.parse_args()

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            if not _tableExists(cur, "course_difficulties"):
                raise SystemExit("course_difficulties is missing -- run the "
                                 "difficulty build first")
            for sport in [s.strip().upper() for s in args.sport.split(",")
                          if s.strip()]:
                table = TABLES.get(sport)
                if not table or not _tableExists(cur, table):
                    continue
                print(f"\n  {sport}: scanning rated rows from {args.since} on "
                      f"courses with >= {args.min_course_results} results, "
                      f"fields of >= {args.min_field}...", flush=True)
                cur.execute(_sql(table, args.bins),
                            {"since": args.since, "hw": args.band_halfwidth,
                             "min_field": args.min_field,
                             "min_course": args.min_course_results})
                report(cur.fetchall(), sport, args.bins)
        conn.rollback()

    print("\n  HOW TO READ IT: a FLAT column means difficulty has already been "
          "removed from\n  the rating, which is what it is for. A column that "
          "RISES with difficulty means\n  hard races are over-credited, and "
          "the slope says by how much and to whom.\n  The percentile is "
          "within each race, so better fields at harder meets cannot\n  "
          "produce the effect on their own.")


if __name__ == "__main__":
    main()
