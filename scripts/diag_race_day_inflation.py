#!/usr/bin/env python3
"""
diag_race_day_inflation.py -- why an early-season XC race rates its whole
field above their level. READ ONLY.

    /srv/venv/bin/python scripts/diag_race_day_inflation.py --meet "James Smith HS Invitational"
    /srv/venv/bin/python scripts/diag_race_day_inflation.py --div 1234567

★ WHY (owner, 2026-10-08: "things like this are where race-day fails").
  Midlothian James Smith HS Invitational, Aug 27 2026: "Day slow 10.3%",
  and nearly every runner +10-20% "vs level". Course + day reach a rating
  as ONE number -- the field's shortfall against each runner's SEASON
  ability -- and for a late-August opener that number can carry heat (the
  XC weather correction is off: fitted on old temperatures), early-season
  form, or a 2026 season ability that itself sits high. This splits them:

  1. THE RACE: median of (rating here / the runner's median XC rating in
     the 60 days after), (later / level) and (here / level), where level is
     the q80 of the 365 days before (above_level's own statistic).
       here~later, later>>level  -> the 2026 season reads high, not the day
       here>>later               -> the day term over-credits this race
  2. THE CALENDAR: the same here-vs-level median by week, Aug 1-Oct 5,
     2025 against 2026 (a 1-in-20 sample of rows).
  3. THE DAYS: race_day_effect for every XC venue-day Aug 20 - Sep 3 2026,
     biggest fields first -- is every late-August day slow, or this one?
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meet", help="part of the meet name")
    ap.add_argument("--div", type=int, help="the race's division id")
    ap.add_argument("--year", type=int, default=2026)
    a = ap.parse_args()
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET statement_timeout = '900s'")
        if a.div:
            divs = [a.div]
        else:
            cur.execute("""SELECT r.div_id, m.meet_name, m.division, count(*)
                           FROM results r JOIN meets m ON m.div_id = r.div_id
                           WHERE m.meet_name ILIKE %s AND r.source = 'anet'
                             AND left(r.date, 4) = %s
                           GROUP BY 1, 2, 3 ORDER BY 4 DESC""",
                        ("%" + (a.meet or "") + "%", str(a.year)))
            got = cur.fetchall()
            for d, mn, dv, n in got:
                print(f"  div {d}: {mn} / {dv} ({n} results)")
            divs = [d for d, *_ in got]
        print("\n1. THE RACE (medians, %)")
        for d in divs:
            cur.execute("""
                WITH f AS (SELECT r.person_id, r.speed_rating::float here, r.date::date dy
                           FROM results r WHERE r.div_id = %s AND r.source = 'anet'
                             AND r.speed_rating IS NOT NULL AND r.person_id IS NOT NULL),
                x AS (SELECT f.*,
                  (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY rr.speed_rating)
                   FROM ranking_results rr WHERE rr.person_id = f.person_id AND rr.sport = 'XC'
                     AND rr.race_date > f.dy AND rr.race_date < f.dy + 60) later,
                  (SELECT percentile_cont(0.8) WITHIN GROUP (ORDER BY rr.speed_rating)
                   FROM ranking_results rr WHERE rr.person_id = f.person_id AND rr.sport = 'XC'
                     AND rr.race_date < f.dy AND rr.race_date >= f.dy - 365) lvl
                  FROM f)
                SELECT count(*), count(later), count(lvl),
                  round((100 * percentile_cont(0.5) WITHIN GROUP (ORDER BY here / later - 1))::numeric, 1),
                  round((100 * percentile_cont(0.5) WITHIN GROUP (ORDER BY later / lvl - 1))::numeric, 1),
                  round((100 * percentile_cont(0.5) WITHIN GROUP (ORDER BY here / lvl - 1))::numeric, 1)
                FROM x""", (d,))
            n, nl, nv, hl, ll, hv = cur.fetchone()
            print(f"  div {d}: {n} rated ({nl} raced later, {nv} have a level)  "
                  f"here vs later {hl}   later vs level {ll}   here vs level {hv}")

        print("\n2. THE CALENDAR: median here-vs-level by week (%)")
        cur.execute("""
            WITH s AS (SELECT r.person_id, r.speed_rating::float here, r.date::date dy FROM results r
              WHERE r.speed_rating IS NOT NULL AND r.person_id IS NOT NULL AND r.result_id %% 20 = 0
                AND r.date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                AND (r.date BETWEEN %(a0)s AND %(a1)s OR r.date BETWEEN %(b0)s AND %(b1)s)),
            l AS (SELECT s.*, (SELECT percentile_cont(0.8) WITHIN GROUP (ORDER BY rr.speed_rating)
                  FROM ranking_results rr WHERE rr.person_id = s.person_id AND rr.sport = 'XC'
                    AND rr.race_date < s.dy AND rr.race_date >= s.dy - 365) lvl FROM s)
            SELECT extract(year FROM dy)::int, to_char(date_trunc('week', dy), 'MM-DD'), count(lvl),
              round((100 * percentile_cont(0.5) WITHIN GROUP (ORDER BY here / lvl - 1))::numeric, 1)
            FROM l WHERE lvl > 0 GROUP BY 1, 2 ORDER BY 2, 1""",
                    {"a0": f"{a.year - 1}-08-01", "a1": f"{a.year - 1}-10-05",
                     "b0": f"{a.year}-08-01", "b1": f"{a.year}-10-05"})
        for yr, wk, n, med in cur.fetchall():
            print(f"  {yr} week of {wk}: n={n:>6}  {med}")

        print("\n3. THE DAYS: XC venue-days Aug 20 - Sep 3 (day %, course %)")
        cur.execute("""SELECT course_name, distance_m, race_date,
                              round(100 * day_effect::numeric, 2), round(100 * course_effect::numeric, 2), n_rows
                       FROM race_day_effect WHERE course_name NOT LIKE 'TF:%%'
                         AND race_date BETWEEN %s AND %s ORDER BY n_rows DESC LIMIT 40""",
                    (f"{a.year}-08-20", f"{a.year}-09-03"))
        for c, dm, d, dp, cp, n in cur.fetchall():
            print(f"  {d}  {str(c)[:44]:44} {dm or '':>6}  day {dp:>6}  course {cp}  rows {n}")
        conn.rollback()


if __name__ == "__main__":
    main()
