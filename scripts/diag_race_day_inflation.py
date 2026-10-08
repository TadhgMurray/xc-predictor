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
  4. THE VENUE-DAY (--div only; owner, 2026-10-08, meet 271911 div 1083503:
     "how did this race come out as slow ... despite it being fast"). The
     page's own lookup -- the course cell (course_canonical by name, the
     nearest gps, distance at 100 m) and race_day_effect on that date --
     then EVERY division that shares the venue-day term (same course name,
     same date, any meet), each with its stored distance, rows, median
     time and its here-vs-later median, and race_day_suspect_division's
     rows for that day. One wrong division (a distance, a pool) can carry
     the whole day term the way Midlothian's three did.

    /srv/venv/bin/python scripts/diag_race_day_inflation.py --div 1083503 --sections 4
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
    ap.add_argument("--sections", default="0123",
                    help="which sections to print, e.g. --sections 0")
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
        # ★ 0. WHAT EACH RACE IS (owner's run, 2026-10-08: four Midlothian
        #   races read +12% against their runners' next races, three read
        #   -30%; one day term serves the whole venue-day, so races that are
        #   wrong by 40% drag it "slow" and over-credit the rest). Stored
        #   distance, profile genders, rating pools and the median time.
        if "0" in a.sections:
            print("\n0. THE RACES (stored distance, genders, pools, median time)")
        for d in (divs if "0" in a.sections else []):
            cur.execute("""
                SELECT m.division, m.distance,
                  (SELECT string_agg(g || ':' || n, ' ') FROM (
                     SELECT COALESCE(a.gender, '?') g, count(*) n FROM results r2
                     LEFT JOIN athletes a ON a.athlete_id = r2.athlete_id
                     WHERE r2.div_id = m.div_id AND r2.source = 'anet' GROUP BY 1) q),
                  (SELECT string_agg(p || ':' || n, ' ') FROM (
                     SELECT COALESCE(split_part(r2.rating_pool, '|', 1), '-') p, count(*) n
                     FROM results r2 WHERE r2.div_id = m.div_id AND r2.source = 'anet'
                     GROUP BY 1) q),
                  (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY r2.time_seconds)
                   FROM results r2 WHERE r2.div_id = m.div_id AND r2.source = 'anet'
                     AND r2.time_seconds > 0 AND r2.time_seconds < 999999)
                FROM meets m WHERE m.div_id = %s""", (d,))
            row = cur.fetchone()
            if row:
                dv, dist, gs, ps, med = row
                mt = f"{int(med // 60)}:{med % 60:04.1f}" if med else "-"
                print(f"  div {d}: {dv} | stored {dist} m | genders {gs} | pools {ps} | median {mt}")

        if "1" in a.sections:
            print("\n1. THE RACE (medians, %)")
        for d in (divs if "1" in a.sections else []):
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

        if "2" in a.sections:
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

        if "3" in a.sections:
            print("\n3. THE DAYS: XC venue-days Aug 20 - Sep 3 (day %, course %)")
            cur.execute("""SELECT course_name, distance_m, race_date,
                                  round(100 * day_effect::numeric, 2), round(100 * course_effect::numeric, 2), n_rows
                           FROM race_day_effect WHERE course_name NOT LIKE 'TF:%%'
                             AND race_date BETWEEN %s AND %s ORDER BY n_rows DESC LIMIT 40""",
                        (f"{a.year}-08-20", f"{a.year}-09-03"))
            for c, dm, d, dp, cp, n in cur.fetchall():
                print(f"  {d}  {str(c)[:44]:44} {dm or '':>6}  day {dp:>6}  course {cp}  rows {n}")
        if "4" in a.sections and a.div:
            _venueDay(cur, a.div)
        conn.rollback()


_LATER = """
    WITH f AS (SELECT r.person_id, r.speed_rating::float here, r.date::date dy
               FROM results r WHERE r.div_id = %s AND r.source = 'anet'
                 AND r.speed_rating IS NOT NULL AND r.person_id IS NOT NULL),
    x AS (SELECT f.*, (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY rr.speed_rating)
                       FROM ranking_results rr WHERE rr.person_id = f.person_id
                         AND rr.sport = 'XC' AND rr.race_date > f.dy
                         AND rr.race_date < f.dy + 60) later FROM f)
    SELECT count(*) n, count(later) nl,
           round((100 * percentile_cont(0.5) WITHIN GROUP (ORDER BY here / later - 1))::numeric, 1) hl
    FROM x"""


def _clock(sec):
    return f"{int(sec // 60)}:{sec % 60:04.1f}" if sec else "-"


def _venueDay(cur, div):
    print("\n4. THE VENUE-DAY")
    cur.execute("""SELECT meet_id, meet_name, division, distance, course_name,
                          gps_lat, gps_long, left(meet_date, 10)
                   FROM meets WHERE div_id = %s LIMIT 1""", (div,))
    row = cur.fetchone()
    if not row:
        print(f"  div {div}: no meets row")
        return
    meet, name, title, dist, course, lat, lon, day = row
    if not day:
        cur.execute("SELECT min(left(date, 10)) FROM results WHERE div_id = %s "
                    "AND source = 'anet'", (div,))
        day = cur.fetchone()[0]
    print(f"  div {div}: {name} / {title} | {dist} m | course {course!r} | {day} "
          f"| gps {lat},{lon}")
    # the page's cell (app._xc_course_join, distance at 100 m)
    cur.execute("""SELECT cc.canonical_id, cd0.distance_m, cd0.difficulty, cd0.n_results
                   FROM course_canonical cc
                   JOIN course_difficulties cd0 ON cd0.canonical_id = cc.canonical_id
                   WHERE cc.course_name = %(c)s
                     AND round(cd0.distance_m / 100.0) = round(%(d)s::numeric / 100.0)
                     AND cd0.difficulty IS NOT NULL
                   ORDER BY (power(cc.gps_lat - %(la)s, 2) + power(cc.gps_long - %(lo)s, 2))
                            NULLS LAST, cd0.n_results DESC NULLS LAST LIMIT 1""",
                {"c": course, "d": dist or 0, "la": lat, "lo": lon})
    cell = cur.fetchone()
    print(f"  page's course cell: {cell}")
    cid = cell[0] if cell else None
    cur.execute("""SELECT course_name, canonical_id, distance_m,
                          round(100 * day_effect::numeric, 2), round(100 * course_effect::numeric, 2), n_rows
                   FROM race_day_effect
                   WHERE race_date::text = %(day)s
                     AND (canonical_id = %(cid)s OR course_name = %(c)s)
                   ORDER BY n_rows DESC""", {"day": day, "cid": cid, "c": course})
    got = cur.fetchall()
    print(f"  race_day_effect on {day} for this course ({len(got)} rows; + = slow, credited):")
    for c, ci, dm, dp, cp, n in got:
        print(f"    {str(c)[:40]:40} cid {ci} {dm} m  day {dp}%  course {cp}%  rows {n}")
    cur.execute("""SELECT m.div_id, m.meet_id, m.meet_name, m.division, m.distance,
                          count(r.*) n,
                          percentile_cont(0.5) WITHIN GROUP (ORDER BY r.time_seconds)
                              FILTER (WHERE r.time_seconds > 0 AND r.time_seconds < 999999),
                          string_agg(DISTINCT split_part(r.rating_pool, '|', 1), ' ')
                   FROM meets m JOIN results r ON r.div_id = m.div_id AND r.source = 'anet'
                   WHERE m.course_name = %s AND left(r.date, 10) = %s
                   GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC""", (course, day))
    divs = cur.fetchall()
    print(f"  divisions sharing the venue-day ({len(divs)}): here-vs-later median, "
          f"+ = rated above their next 60 days")
    for d, m, mn, dv, ds, n, med, pools in divs:
        cur.execute(_LATER, (d,))
        nn, nl, hl = cur.fetchone()
        mark = " <-- this race" if d == div else ""
        print(f"    div {d} meet {m}: {str(mn)[:30]} / {dv} | {ds} m | {n} rows | "
              f"median {_clock(med)} | pools {pools} | here vs later {hl}% "
              f"(n {nl}){mark}")
    cur.execute("SELECT to_regclass('race_day_suspect_division')")
    if cur.fetchone()[0]:
        cur.execute("""SELECT meet_id, div_id, pool, distance_m, n_rows,
                              round(100 * division_day::numeric, 1), round(100 * race_u::numeric, 1),
                              round(100 * excess::numeric, 1), reconciled_m, snapped_m
                       FROM race_day_suspect_division
                       WHERE race_date::text = %(day)s
                         AND (canonical_id = %(cid)s OR course_name = %(c)s)""",
                    {"day": day, "cid": cid, "c": course})
        sus = cur.fetchall()
        print(f"  race_day_suspect_division that day: {sus or 'none'}")


if __name__ == "__main__":
    main()
