#!/usr/bin/env python3
"""
why_race_high.py -- is this race rated above the same runners' other races,
and is it THIS DAY or THE COURSE every year? READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/why_race_high.py "https://racecast.co/race/xc/254287/1015294#r60948388"
    /srv/venv/bin/python scripts/why_race_high.py 254287 1015294

(owner, 2026-10-24: Marek Butkiewicz, Shamrock Invitational 2025 at Berrien
County Fairgrounds, 15:13 rated 152.6 = 13:33 -- "just way too fucking
high". The course reads +10.8% and the field check passed.)

THE TEST, per runner in the division with 2+ other rated XC races that
season (Aug-Dec, same year):
      excess = 100 ln(rating here / median of their other races)
and against their BEST other race. A race a whole field rates several
percent above its own season -- above even its season bests -- is
over-credited; one runner far above a normal field had a day.

Then the same median excess for EVERY race at the course and distance
(anet, by course name): high every year = the course number is too hard;
high one year = that day.

  ! The other races carry their own course and day errors (an early-season
    slow day the rating does not credit reads low), so a few percent is
    noise; the course history is what separates a course from a day.
"""
import re
import statistics
import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

MIN_OTHER = 2
HISTORY = 40

_FIELD = """
    WITH here AS (
        SELECT r.result_id, r.person_id, r.athlete_id, r.speed_rating, r.time_seconds,
               r.normalized_time, substr(r.date::text, 1, 4) AS yr
        FROM   results r
        WHERE  r.meet_id = %(m)s AND r.div_id = %(d)s AND r.source = %(s)s
          AND  r.speed_rating > 0 AND r.person_id IS NOT NULL)
    SELECT h.person_id,
           (SELECT min(COALESCE(a.first_name, '') || ' ' || COALESCE(a.last_name, ''))
            FROM athletes a WHERE a.athlete_id = h.athlete_id) AS name,
           h.speed_rating, h.time_seconds, h.normalized_time,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY o.speed_rating) AS med,
           max(o.speed_rating) AS best, count(*) AS n
    FROM   here h
    JOIN   results o ON o.person_id = h.person_id AND o.result_id <> h.result_id
                    AND o.speed_rating > 0
                    AND substr(o.date::text, 1, 4) = h.yr
                    AND substr(o.date::text, 6, 2) BETWEEN '08' AND '12'
    GROUP  BY 1, 2, 3, 4, 5
    HAVING count(*) >= %(k)s
"""


def excess(here, other):
    import math
    return 100.0 * math.log(float(here) / float(other))


def fieldSummary(rows):
    """rows: (pid, name, rating, time, nt, med, best, n) -> (n, median
    excess vs median, median excess vs best). Pure."""
    if not rows:
        return 0, None, None
    em = [excess(r[2], r[5]) for r in rows]
    eb = [excess(r[2], r[6]) for r in rows]
    return len(rows), statistics.median(em), statistics.median(eb)


def parseTarget(argv):
    if len(argv) == 2:
        m = re.search(r"/race/xc/(\d+)/(\d+)", argv[1])
        if not m:
            sys.exit(__doc__)
        return int(m.group(1)), int(m.group(2))
    if len(argv) == 3:
        return int(argv[1]), int(argv[2])
    sys.exit(__doc__)


def main():
    meet, div = parseTarget(sys.argv)
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT source, course_name, distance, meet_name FROM meets
                       WHERE meet_id = %s AND div_id = %s ORDER BY source LIMIT 1""", (meet, div))
        got = cur.fetchone()
        if not got:
            sys.exit(f"no anet meets row for {meet}/{div}")
        src, course, dist, mname = got
        cur.execute("SELECT min(date::text) FROM results WHERE meet_id = %s AND div_id = %s AND source = %s",
                    (meet, div, src))
        day = (cur.fetchone()[0] or "")[:10]
        print(f"\n{mname}  {day}  ({meet}/{div}, {src})  course {course!r}  {dist} m")
        # race_day_effect names a course by its canonical name: find the ids
        cur.execute("SELECT canonical_id FROM course_canonical WHERE lower(btrim(course_name)) = lower(btrim(%s))",
                    (course or "",))
        cids = [r[0] for r in cur.fetchall()]
        dist_m = int(round(float(dist or 0)))

        def solved(on_day):
            cur.execute("""SELECT round(100 * day_effect::numeric, 2), round(100 * course_effect::numeric, 2)
                           FROM race_day_effect WHERE canonical_id = ANY(%s) AND race_date::text = %s
                             AND distance_m = %s LIMIT 1""", (cids, on_day, dist_m))
            return cur.fetchone() or (None, None)
        d_pct, c_pct = solved(day)
        print(f"  solve: course (this race's era) {c_pct}%   that day {d_pct}% "
              f"(+ = slow; cross country ratings take a fast day only)")

        cur.execute(_FIELD, {"m": meet, "d": div, "s": src, "k": MIN_OTHER})
        rows = sorted(cur.fetchall(), key=lambda r: -float(r[2]))
        n, em, eb = fieldSummary(rows)
        print(f"\n  THIS RACE vs THE SAME RUNNERS' OTHER RACES THAT SEASON ({n} runners with {MIN_OTHER}+)")
        print(f"  {'runner':24} {'time':>7} {'raw->nt':>8} {'here':>6} {'median':>7} {'best':>6} "
              f"{'vs med':>7} {'vs best':>8}  n")
        for pid, name, rt, t, nt, med, best, k in rows[:25]:
            adj = f"{100 * (float(nt) / float(t) - 1):+.1f}%" if nt and t else "-"
            print(f"  {(name or str(pid))[:24]:24} {int(t) // 60}:{float(t) % 60:04.1f} {adj:>8} "
                  f"{float(rt):6.1f} {float(med):7.1f} {float(best):6.1f} "
                  f"{excess(rt, med):+6.1f}% {excess(rt, best):+7.1f}%  {k}")
        if n:
            print(f"\n  FIELD: median {em:+.1f}% vs their season medians, {eb:+.1f}% vs their season bests")

        cur.execute("""SELECT DISTINCT m.meet_id, m.div_id FROM meets m
                       WHERE m.source = 'anet' AND lower(btrim(m.course_name)) = lower(btrim(%s))
                         AND round(m.distance) = round(%s::numeric)""", (course or "", dist or 0))
        divs = cur.fetchall()
        hist = []
        for hm, hd in divs:
            cur.execute("SELECT min(date::text), count(*) FROM results WHERE meet_id = %s AND div_id = %s "
                        "AND source = 'anet' AND speed_rating > 0", (hm, hd))
            hday, hn = cur.fetchone()
            if hday:
                hist.append((hday[:10], hm, hd, hn))
        hist = sorted(hist)[-HISTORY:]
        print(f"\n  EVERY RACE AT {course!r} {dist} m (anet): the field vs its own season")
        print(f"  {'date':10}  {'meet/div':>16}  {'rated':>5}  {'with base':>9}  {'vs med':>7}  {'vs best':>8}"
              f"  {'course':>7}  {'day':>6}  name")
        for hday, hm, hd, hn in hist:
            cur.execute(_FIELD, {"m": hm, "d": hd, "s": "anet", "k": MIN_OTHER})
            hn2, hem, heb = fieldSummary(cur.fetchall())
            cur.execute("SELECT min(meet_name) FROM meets WHERE meet_id = %s AND div_id = %s", (hm, hd))
            nm = cur.fetchone()[0] or ""
            mark = "  <-" if (hm, hd) == (meet, div) else ""
            hd_pct, hc_pct = solved(hday)
            cd = f"{'-' if hc_pct is None else f'{hc_pct}%':>7}  {'-' if hd_pct is None else f'{hd_pct}%':>6}"
            if hn2:
                print(f"  {hday}  {f'{hm}/{hd}':>16}  {hn:5}  {hn2:9}  {hem:+6.1f}%  {heb:+7.1f}%  {cd}  {nm[:30]}{mark}")
            else:
                print(f"  {hday}  {f'{hm}/{hd}':>16}  {hn:5}  {0:9}        -         -  {cd}  {nm[:30]}{mark}")
        conn.rollback()


if __name__ == "__main__":
    main()
