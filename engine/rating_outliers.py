#!/usr/bin/env python3
"""
rating_outliers.py -- a result too far from its athlete's own season is data,
not a performance.

    python engine/rating_outliers.py --dry-run --since 2015
    python engine/rating_outliers.py --write --since 2015
    python engine/rating_outliers.py --dry-run --since 2020 --sigma 5,8,10,15

★ THE OWNER'S RULE (2026-09-18): "If a result is more than 5-15 sigma away
  (from their season's median or mean or whatever) do not rate or rank."

⚠⚠ MEAN AND STANDARD DEVIATION CANNOT DO THIS JOB, and that is the whole
   design. A season holds a handful of races, so ONE huge outlier drags the
   mean towards itself and inflates the very standard deviation it is being
   measured against -- a 10-sigma error in a 6-race season routinely scores
   under 2. The test would be defeated by its own subject.

★ SO: MEDIAN AND MAD. The median ignores the outlier entirely, and MAD (the
   median absolute deviation, scaled by 1.4826 to read as a normal sigma) is
   likewise unmoved by it. This is the standard robust pair and it is the
   reason the owner's "5-15 sigma" range is usable at all -- against a mean,
   the same numbers would catch almost nothing.

⚠ AND THE TWO SIDES ARE NOT THE SAME QUESTION. A wildly SLOW race is a jog, a
  rust-buster, an injury or a workout, and it is ordinary -- athletes do it
  every season on purpose. A wildly FAST one is a wrong distance, a wrong
  time, a mis-merged person or a hand-timed relay split. So the fast side is
  tight and the slow side is off by default (--slow-sigma to enable it).

! MAD = 0 IS COMMON AND WOULD DIVIDE BY ZERO. An athlete with three nearly
  identical ratings has a MAD of zero, and then every deviation is infinite.
  MIN_SPREAD is a floor in rating points, so such a season needs a real gap
  rather than any gap at all.

! FEW RACES MEANS NO OPINION. Below MIN_RACES a median and a MAD are noise;
  those seasons are left alone rather than guessed at.

★ RANK-ONLY FIRST, and deliberately. This writes a table and changes no
  rating: dropping a row from the ratings changes the model's INPUT and every
  rating downstream of it, while dropping it from the boards is cosmetic and
  reversible. Look at the counts, then wire it.

Table:

    rating_outlier (result_id, sport, person_id, season, rating, med, sigma,
                    n_races, z, side)
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 1.4826 * MAD estimates sigma for a normal distribution -- the standard
# constant, so "sigma" in the owner's sentence keeps its ordinary meaning.
MAD_TO_SIGMA = 1.4826

# ! IN RATING POINTS. A season whose ratings differ by less than this has no
#   measurable spread, so nothing in it can be a confident outlier.
MIN_SPREAD = 2.0

MIN_RACES = 5
FAST_SIGMA = 8.0            # the owner's range is 5-15; the middle to start
SLOW_SIGMA = None           # off: a slow race is a jog, not an error

TABLES = {"XC": "results", "TF": "results_tf"}

DDL = """
CREATE TABLE IF NOT EXISTS rating_outlier (
    result_id bigint NOT NULL,
    sport     text   NOT NULL,
    person_id bigint,
    season    int,
    rating    double precision,
    med       double precision,
    sigma     double precision,
    n_races   int,
    z         double precision,
    side      text   NOT NULL,
    built     date   NOT NULL DEFAULT current_date,
    PRIMARY KEY (result_id, sport))
"""


def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    got = cur.fetchone()
    return bool(got[0] if not isinstance(got, dict) else list(got.values())[0])


def _hasCol(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                     AND column_name = %s""", (table, col))
    return cur.fetchone() is not None


# ★ ONE QUERY, THREE PASSES OVER A MATERIALIZED SET. median, then MAD (which
#   needs the median), then the deviation (which needs both). Each CTE is
#   MATERIALIZED so Postgres does not re-run the ones below it per row -- the
#   same mistake that made an earlier crest query take fifteen minutes.
def _zSql(table, since):
    where_since = ("AND substr(r.date, 1, 4)::int >= %(since)s" if since else "")
    return f"""
        WITH r AS MATERIALIZED (
            SELECT r.result_id, r.person_id,
                   substr(r.date, 1, 4)::int AS season,
                   r.speed_rating::double precision AS rating
            FROM   {table} r
            WHERE  r.speed_rating IS NOT NULL
              AND  r.person_id IS NOT NULL
              AND  r.date ~ '^(19|20)[0-9][0-9]-'
              {where_since}
        ), med AS MATERIALIZED (
            SELECT person_id, season, count(*) AS n_races,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY rating) AS med
            FROM   r GROUP BY person_id, season
            HAVING count(*) >= %(min_races)s
        ), dev AS MATERIALIZED (
            SELECT r.result_id, r.person_id, r.season, r.rating,
                   m.med, m.n_races, abs(r.rating - m.med) AS ad
            FROM   r JOIN med m
                   ON m.person_id = r.person_id AND m.season = r.season
        ), mad AS MATERIALIZED (
            SELECT person_id, season,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY ad) AS mad
            FROM   dev GROUP BY person_id, season
        )
        SELECT d.result_id, d.person_id, d.season, d.rating, d.med, d.n_races,
               -- ! THE FLOOR IS WHY A ZERO MAD DOES NOT DIVIDE BY ZERO.
               GREATEST({MAD_TO_SIGMA} * k.mad, %(min_spread)s) AS sigma,
               (d.rating - d.med)
                 / GREATEST({MAD_TO_SIGMA} * k.mad, %(min_spread)s) AS z
        FROM   dev d JOIN mad k
               ON k.person_id = d.person_id AND k.season = d.season
    """


def scan(cur, sport, since=None, min_races=MIN_RACES, min_spread=MIN_SPREAD,
         verbose=True):
    """[(result_id, person_id, season, rating, med, n_races, sigma, z)]
    for every rated row whose season has enough races. No threshold applied --
    the caller picks, so several can be reported from one scan."""
    table = TABLES[sport]
    if not (_tableExists(cur, table) and _hasCol(cur, table, "speed_rating")):
        if verbose:
            print(f"  {table}: no speed_rating column, skipped")
        return []
    if verbose:
        print(f"  {table}: scanning rated rows"
              + (f" from {since}" if since else " (all years)")
              + " -- median, MAD, then deviation", flush=True)
    cur.execute(_zSql(table, since),
                {"since": since, "min_races": min_races,
                 "min_spread": min_spread})
    return cur.fetchall()


def counts(rows, thresholds, slow=None):
    """{threshold: (n_fast, n_slow)} -- what each bar would catch."""
    out = {}
    for t in thresholds:
        fast = sum(1 for r in rows if r[7] is not None and r[7] >= t)
        slw = (sum(1 for r in rows if r[7] is not None and r[7] <= -t)
               if slow else 0)
        out[t] = (fast, slw)
    return out


def flagged(rows, sport, fast=FAST_SIGMA, slow=SLOW_SIGMA):
    out = []
    for (rid, pid, season, rating, med, n_races, sigma, z) in rows:
        if z is None:
            continue
        if z >= fast:
            out.append((rid, sport, pid, season, rating, med, sigma,
                        n_races, z, "fast"))
        elif slow is not None and z <= -slow:
            out.append((rid, sport, pid, season, rating, med, sigma,
                        n_races, z, "slow"))
    return out


def write(cur, rows):
    cur.execute(DDL)
    cur.execute("DELETE FROM rating_outlier")
    cur.executemany("""
        INSERT INTO rating_outlier (result_id, sport, person_id, season,
                                    rating, med, sigma, n_races, z, side)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (result_id, sport) DO NOTHING
    """, rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", type=int, default=None,
                    help="only seasons from this year on. The whole corpus is "
                         "a full pass over every rated row in both tables; "
                         "start narrow.")
    ap.add_argument("--sigma", default="5,8,10,15",
                    help="report the count each of these bars would catch")
    ap.add_argument("--fast-sigma", type=float, default=FAST_SIGMA,
                    help="the bar actually written")
    ap.add_argument("--slow-sigma", type=float, default=None,
                    help="also flag results this far BELOW the median. Off by "
                         "default: a slow race is a jog, not a data error.")
    ap.add_argument("--min-races", type=int, default=MIN_RACES)
    ap.add_argument("--min-spread", type=float, default=MIN_SPREAD)
    ap.add_argument("--show", type=int, default=15)
    args = ap.parse_args()
    if not (args.write or args.dry_run):
        ap.error("pass --dry-run or --write")
    bars = [float(x) for x in args.sigma.split(",") if x.strip()]

    from database import getConn
    from pg_guard import guard
    all_rows, all_flagged = {}, []
    with getConn() as conn:
        with conn.cursor() as cur:
            # ! A READ-ONLY DIAGNOSTIC MUST NOT BE ABLE TO FILL THE DISK.
            #   diag_indoor_level did exactly that on 2026-09-18, under a
            #   running scrape. Bounds this connection only.
            guard(cur)
            for sport in ("XC", "TF"):
                rows = scan(cur, sport, args.since, args.min_races,
                            args.min_spread)
                all_rows[sport] = rows
                print(f"    {len(rows):,} rated rows in seasons with "
                      f">= {args.min_races} races")
                got = counts(rows, bars, args.slow_sigma)
                print(f"    {'sigma':>7} {'fast':>10} {'slow':>10}   "
                      f"share of rows")
                for t in bars:
                    f, s = got[t]
                    pct = (100.0 * (f + s) / len(rows)) if rows else 0.0
                    print(f"    {t:>7.0f} {f:>10,} {s:>10,}   {pct:8.4f}%")
                all_flagged += flagged(rows, sport, args.fast_sigma,
                                       args.slow_sigma)

            # ★ THE WORST ONES, NAMED. A bar is only as good as what it
            #   catches, and these are the rows to eyeball before wiring it.
            worst = sorted(all_flagged, key=lambda r: -abs(r[8]))[:args.show]
            if worst:
                print(f"\n  the {len(worst)} furthest out at "
                      f"{args.fast_sigma:g} sigma:")
                print(f"    {'result':>12} {'sport':<5} {'person':>10} "
                      f"{'yr':>5} {'rating':>8} {'median':>8} {'sigma':>7} "
                      f"{'n':>4} {'z':>8}")
                for r in worst:
                    print(f"    {r[0]:>12} {r[1]:<5} {str(r[2]):>10} "
                          f"{r[3]:>5} {r[4]:>8.2f} {r[5]:>8.2f} "
                          f"{r[6]:>7.2f} {r[7]:>4} {r[8]:>8.1f}")

            print(f"\n  {len(all_flagged):,} rows would be flagged at "
                  f"{args.fast_sigma:g} sigma"
                  + (f" (and {args.slow_sigma:g} slow)" if args.slow_sigma
                     else ", fast side only"))
            if args.write:
                print(f"  wrote {write(cur, all_flagged):,} rows to "
                      f"rating_outlier")
        if args.write:
            conn.commit()
            print("  committed. NOTHING READS THIS YET -- ranks and ratings "
                  "are unchanged until the readers are wired.")
        else:
            conn.rollback()
            print("  DRY RUN -- nothing written.")


if __name__ == "__main__":
    main()
