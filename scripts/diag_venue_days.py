#!/usr/bin/env python3
"""
diag_venue_days.py -- why one venue's race days read the way they do. READ ONLY.

    /srv/venv/bin/python scripts/diag_venue_days.py --course "Mt. SAC"
    /srv/venv/bin/python scripts/diag_venue_days.py --course "Mt. SAC" --since 2021-08-01

★ WHY (owner, 2026-10-08, the Mt. SAC course page: course +6.6% in 2025
  while the Invitational's own days read "field ran +5..+6%" -- together
  +13% -- and the CIF-SS prelims/finals on the same cell read -3.7..-6.5%;
  "this is why difficulty seemed so low to me"). The course term is the
  cell's average over every day run there, and each day's term is what is
  left. If the split lines up with the FIELD (a stacked front runs faster,
  joint_solve's field-strength term) or the CALENDAR (a field at the end
  of its season has tapered, the season-end term -- the solve fits one OR
  the other, --importance), the fix is in the solve, not in the course.
  If it lines up with neither, the days are different courses wearing one
  name, and the fix is a key of their own (champ_course.py's mechanism).

  Per race day at the venue (race_day_effect rows whose course name
  matches), biggest field first within each date:
    day % / course %   the solve's terms (+ = slow, credited)
    rows               rated rows that day
    front              the mean rating of the top five of the day's
                       strongest division (the field term's covariate)
    median front       the same over all the day's divisions
    season-end         the share of the day's runners whose next 14 days
                       hold the last cross country race they run that
                       season (the season-end term's covariate, approx.)
    meets              the meet names that day
  and the correlation of the day term with front and season-end across the
  venue's days.
"""
import argparse
import math
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))


def _corr(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 4:
        return None
    a, b = zip(*pairs)
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in pairs)
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--course", default="", help="part of the course name")
    ap.add_argument("--meet", default="",
                    help="part of a meet NAME run there, e.g. 'Mt. SAC Invitational': "
                         "the courses its rows were run on are used (the engine's "
                         "course name is often not the one people say)")
    ap.add_argument("--since", default="1900-01-01")
    a = ap.parse_args()
    # ! PUNCTUATION-BLIND (owner's first run: "no race_day_effect rows
    #   match 'Mt. SAC'"): the engine's names and the page's need not agree
    #   on dots and spaces, so both sides are compared as letters+digits
    norm = "".join(ch for ch in a.course.lower() if ch.isalnum())
    nlike = "%" + norm + "%"
    NORM = "regexp_replace(lower({}), '[^a-z0-9]', '', 'g')"

    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET statement_timeout = '900s'")
        cur.execute(f"""SELECT canonical_id, course_name FROM course_canonical
                        WHERE {NORM.format('course_name')} LIKE %s""", (nlike,))
        canon = cur.fetchall() if norm else []
        if a.meet:
            # the courses the named meet ran on, biggest first
            # every WORD of it, in any order: "77th Annual Mt. SAC Cross
            # Country Invitational" holds mt, sac and invitational apart
            words = ["%" + "".join(ch for ch in w if ch.isalnum()) + "%"
                     for w in a.meet.lower().split()]
            words = [w for w in words if w != "%%"]
            cond = " AND ".join([f"{NORM.format('m.meet_name')} LIKE %s"] * len(words))
            cur.execute(f"""
                SELECT m.course_name, count(*) FROM meets m
                WHERE  {cond} AND m.course_name IS NOT NULL
                GROUP  BY 1 ORDER BY 2 DESC LIMIT 5""", words)
            got = cur.fetchall()
            print(f"courses {a.meet!r} ran on: {got}")
            if got:
                cur.execute("""SELECT canonical_id, course_name FROM course_canonical
                               WHERE course_name = ANY(%s)""", ([g[0] for g in got],))
                canon += cur.fetchall()
        cids = [c for c, _n in canon]
        names = sorted({n for _c, n in canon if n})
        print(f"courses matching {a.course!r}: {len(cids)} canonical ids, names {names[:8]}")
        cur.execute(f"""
            SELECT race_date::text, course_name, distance_m,
                   100 * day_effect, 100 * course_effect, n_rows
            FROM   race_day_effect
            WHERE  (canonical_id = ANY(%s)
                    OR (%s <> '%%%%' AND {NORM.format('course_name')} LIKE %s))
              AND  course_name NOT LIKE 'TF:%%'
              AND  race_date >= %s
            ORDER  BY race_date DESC, n_rows DESC""", (cids, nlike, nlike, a.since))
        days = cur.fetchall()
        if not days:
            print(f"no race_day_effect rows match {a.course!r}")
            conn.rollback()
            return
        print(f"{len(days)} race-day rows for {a.course!r}\n")
        print(f"  {'date':10} {'dist':>5} {'day%':>6} {'crs%':>6} {'rows':>6} "
              f"{'front':>6} {'med':>6} {'end':>5}  meets")
        out = []
        for day, cname, dist, dp, cp, n in days:
            # the day's divisions at a venue of this name, both feeds
            cur.execute("""
                WITH d AS (
                    SELECT r.meet_id, r.div_id, r.source, r.person_id,
                           r.speed_rating, COALESCE(m.meet_name, mt.meet_name) AS meet
                    FROM   results r
                    LEFT   JOIN meets m ON m.div_id = r.div_id AND r.source = 'anet'
                    LEFT   JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id
                                              AND r.source = 'tfrrs' AND mt.sport = 'XC'
                    WHERE  left(r.date, 10) = %(day)s
                      AND  (m.course_name = ANY(%(names)s) OR mt.venue_name = ANY(%(names)s))
                ),
                fronts AS (
                    SELECT meet_id, div_id, source, avg(speed_rating) AS front
                    FROM (SELECT *, row_number() OVER (PARTITION BY meet_id, div_id, source
                                                       ORDER BY speed_rating DESC) AS k
                          FROM d WHERE speed_rating IS NOT NULL) q
                    WHERE k <= 5 GROUP BY 1, 2, 3 HAVING count(*) = 5
                )
                SELECT (SELECT max(front) FROM fronts),
                       (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY front) FROM fronts),
                       (SELECT string_agg(DISTINCT left(meet, 40), ' | ') FROM d),
                       (SELECT array_agg(DISTINCT person_id) FROM d
                        WHERE person_id IS NOT NULL)""",
                        {"day": day, "names": names})
            front, med, meets, pids = cur.fetchone()
            end = None
            if pids:
                # the last XC race of their season: the latest result within
                # 120 days after this one; ends here if it is <= 14 days on
                cur.execute("""
                    SELECT avg(CASE WHEN last <= %(day)s::date + 14 THEN 1.0 ELSE 0.0 END)
                    FROM (SELECT person_id, max(left(date, 10))::date AS last
                          FROM results
                          WHERE person_id = ANY(%(p)s)
                            AND date >= %(day)s AND date < (%(day)s::date + 120)::text
                          GROUP BY 1) q""", {"day": day, "p": pids})
                end = cur.fetchone()[0]
            out.append((dp, front, end))
            print(f"  {day:10} {dist or '':>5} {dp:>+6.1f} {cp if cp is not None else float('nan'):>+6.1f} "
                  f"{n:>6} {front or float('nan'):>6.1f} {med or float('nan'):>6.1f} "
                  f"{(end if end is not None else float('nan')):>5.2f}  {meets or '-'}")
        conn.rollback()
    cf = _corr([o[0] for o in out], [o[1] for o in out])
    ce = _corr([o[0] for o in out], [float(o[2]) if o[2] is not None else None for o in out])
    print(f"\ncorrelation of the day term with front strength: "
          f"{cf if cf is None else round(cf, 2)}; with season-end share: "
          f"{ce if ce is None else round(ce, 2)}")
    print("  (negative: stronger / more tapered days read faster -- what the "
          "field and taper terms exist to absorb)")


if __name__ == "__main__":
    main()
