"""
diag_region_offset.py -- is there a REGION offset in the ratings? The
travelling-athlete test (issue 192), straight from ranking_results.

    python scripts/diag_region_offset.py                       # hs_m, XC, by state
    python scripts/diag_region_offset.py --sport TF --pool hs_f
    python scripts/diag_region_offset.py --by section_div      # CA sections etc.
    python scripts/diag_region_offset.py --year 2024 --days 14

Every athlete-season has a HOME region (the region of most of its races)
and, for some, races AWAY from it. For each away race, the athlete's
mean rating at home within --days of it; the difference away - home,
averaged per athlete-season, then per region. Read:

  by HOME region: negative = that region's own venues rate its athletes
    HIGH (they drop when they travel); the cells there are too easy in
    the solve, the region's ratings are inflated.
  by AWAY region: positive = that region's venues hand visitors a HIGHER
    rating than they carry at home; its cells are too hard in the solve.

A region that is off by x in the solve shows about -x by home and +x by
away. Noise: the athletes who travel are a selected set (the good ones,
to the big invitationals), and the --days window is there so a May
championship is not compared with a March opener. --by state uses the
venue's state on the row; any other unit (section_div, state_div,
league, district, county, division, region, conference) is the school's
unit, and a MEET's region is the unit most of its finishers carry.
Read-only.
"""
import argparse
import sys

sys.path.insert(0, "scripts")
from database import getConn                                   # noqa: E402

_UNITS = ("state", "section_div", "state_div", "league", "district",
          "county", "division", "region", "conference", "class", "area")

_ROWS_STATE = """
    CREATE TEMP TABLE r AS
    SELECT person_id, year, speed_rating, race_date, state AS reg
    FROM   ranking_results
    WHERE  sport = %(sport)s AND pool = %(pool)s
      AND  speed_rating IS NOT NULL AND state IS NOT NULL
      AND  person_id IS NOT NULL {year}
"""

_ROWS_UNIT = """
    CREATE TEMP TABLE r AS
    WITH rr AS (
        SELECT person_id, year, speed_rating, race_date, meet_id, {unit} AS own
        FROM   ranking_results
        WHERE  sport = %(sport)s AND pool = %(pool)s
          AND  speed_rating IS NOT NULL AND {unit} IS NOT NULL
          AND  person_id IS NOT NULL AND meet_id IS NOT NULL {year}
    ),
    meet AS (
        SELECT DISTINCT ON (meet_id, year) meet_id, year, own AS reg
        FROM   (SELECT meet_id, year, own, count(*) AS n FROM rr
                GROUP BY 1, 2, 3) m
        ORDER  BY meet_id, year, n DESC, own
    )
    SELECT rr.person_id, rr.year, rr.speed_rating, rr.race_date, meet.reg
    FROM   rr JOIN meet USING (meet_id, year)
"""

_DIFF = """
    WITH home AS (
        SELECT DISTINCT ON (person_id, year) person_id, year, reg AS home
        FROM   (SELECT person_id, year, reg, count(*) AS n FROM r
                GROUP BY 1, 2, 3) h
        ORDER  BY person_id, year, n DESC, reg
    ),
    away AS (
        SELECT a.person_id, a.year, home.home, a.reg AS away,
               a.speed_rating AS ra,
               (SELECT avg(h.speed_rating) FROM r h
                WHERE  h.person_id = a.person_id AND h.year = a.year
                  AND  h.reg = home.home
                  AND  abs(h.race_date - a.race_date) <= %(days)s) AS rh
        FROM   r a JOIN home USING (person_id, year)
        WHERE  a.reg <> home.home
    ),
    per AS (
        SELECT person_id, year, home, away, avg(ra - rh) AS d
        FROM   away WHERE rh IS NOT NULL
        GROUP  BY 1, 2, 3, 4
    )
    SELECT {key}, count(*), avg(d),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY d),
           stddev(d) / sqrt(count(*))
    FROM   per
    GROUP  BY {key}
    HAVING count(*) >= %(min_n)s
    ORDER  BY avg(d)
"""


def _table(title, rows):
    print(f"\n{title}")
    print(f"  {'region':<22}{'athlete-seasons':>16}{'mean':>9}{'median':>9}{'se':>7}")
    for reg, n, mean, med, se in rows:
        print(f"  {str(reg):<22}{n:>16,}{mean:>+9.2f}{med:>+9.2f}"
              f"{(se or 0):>7.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="XC", choices=("XC", "TF"))
    ap.add_argument("--pool", default="hs_m")
    ap.add_argument("--by", default="state", choices=_UNITS)
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--days", type=int, default=21,
                    help="home races within this many days of the away race")
    ap.add_argument("--min-n", type=int, default=30)
    args = ap.parse_args()
    p = {"sport": args.sport, "pool": args.pool, "days": args.days,
         "min_n": args.min_n, "year": args.year}
    year = "AND year = %(year)s" if args.year else ""
    with getConn() as conn, conn.cursor() as cur:
        if args.by == "state":
            cur.execute(_ROWS_STATE.format(year=year), p)
        else:
            cur.execute(_ROWS_UNIT.format(unit=args.by, year=year), p)
        cur.execute("CREATE INDEX ON r (person_id, year)")
        cur.execute("ANALYZE r")
        cur.execute("SELECT count(*), count(DISTINCT reg) FROM r")
        n_rows, n_reg = cur.fetchone()
        print(f"{args.pool} {args.sport} by {args.by}"
              f"{' ' + str(args.year) if args.year else ''}: {n_rows:,} rated "
              f"rows over {n_reg} regions; away - home rating within "
              f"{args.days} days, per athlete-season")
        cur.execute(_DIFF.format(key="home"), p)
        _table("BY HOME REGION (negative = its own venues rate its athletes "
               "high; they drop away)", cur.fetchall())
        cur.execute(_DIFF.format(key="away"), p)
        _table("BY AWAY REGION (positive = its venues rate visitors higher "
               "than they carry at home)", cur.fetchall())
        conn.rollback()


if __name__ == "__main__":
    main()
