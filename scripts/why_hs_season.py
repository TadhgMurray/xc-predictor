#!/usr/bin/env python3
"""
why_hs_season.py -- why is this athlete's season on the high school boards?
READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/why_hs_season.py https://racecast.co/athlete/1234567

(owner, 2026-10-13, Jake Ritter: Railroad Athletics (TX), "Professional" in
2025 and 2026, "Grade 11" in 2024 and national #1 high school boy at 147.7.)

For every academic season of the person: grade_fix's verdict (level,
grade, method, trust), the pools athlete_season put it in, and rule 8's
three signals (engine/grade_sanity.py, "the adult club's 12"):

  1. every team that season is a club / unattached / a country (no school)
  2. every race's ceiling (race_top_level) is college or pro -- each race is
     printed with its ceiling, so the one that blocks is visible
  3. the club is adult: share of its athlete-seasons the last build pooled
     pro or college (> 0.5), or the athlete was past school already
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    m = re.search(r"(\d+)\s*$", sys.argv[1].rstrip("/"))
    pid = int(m.group(1))
    from database import getConn
    from grade_sanity import ADULT_CLUB_SHARE, CLUB_NAME_RE, NOT_SCHOOL_RE, _ACAD_R
    from level_graph import _raceKeyExpr
    with getConn() as c, c.cursor() as cur:
        cur.execute("SELECT season, level, grade, method, trust FROM grade_fix "
                    "WHERE person_id = %s ORDER BY season", (pid,))
        verdict = {r[0]: r[1:] for r in cur.fetchall()}
        cur.execute("SELECT year, sport, pool, mean_rating FROM athlete_season "
                    "WHERE person_id = %s ORDER BY year, sport", (pid,))
        pools = {}
        for y, sp, pool, rt in cur.fetchall():
            pools.setdefault(y, []).append(f"{sp} {pool} {rt and round(float(rt), 1)}")
        cur.execute("SELECT to_regclass('race_top_level') IS NOT NULL")
        have_top = cur.fetchone()[0]
        top = (f"(SELECT top_level || ' (' || n_levels || ' levels, known '"
               f" || round(known_frac::numeric, 2) || ')' FROM race_top_level"
               f" WHERE race = {_raceKeyExpr('r')})") if have_top else "NULL"
        cur.execute(f"""
            SELECT acad, sport, day, meet, school, grade, ceiling FROM (
              SELECT {_ACAD_R} AS acad, 'XC' AS sport, r.date AS day,
                     (SELECT min(meet_name) FROM meets mm WHERE mm.meet_id = r.meet_id) AS meet,
                     r.school, r.grade::text AS grade, {top} AS ceiling
              FROM results r WHERE r.person_id = %(p)s
              UNION ALL
              SELECT {_ACAD_R}, 'TF', r.date,
                     (SELECT min(meet_name) FROM meets_tf_meta mm WHERE mm.meet_id = r.meet_id),
                     r.school, r.grade::text, {top}
              FROM results_tf r WHERE r.person_id = %(p)s) x
            ORDER BY day""", {"p": pid})
        rows = cur.fetchall()
        club_re = re.compile(CLUB_NAME_RE.replace(r"\m", r"\b").replace(r"\M", r"\b"))
        clubs = sorted({(r[4] or "").strip().lower() for r in rows
                        if club_re.search((r[4] or "").lower())})
        share = {}
        for club in clubs:
            cur.execute(f"""
                SELECT count(DISTINCT (c.person_id, c.acad)) FILTER (WHERE s.pool ~ '^(pro|college)_'),
                       count(DISTINCT (c.person_id, c.acad))
                FROM  (SELECT person_id, {_ACAD_R} AS acad FROM results r
                       WHERE lower(btrim(r.school)) = %(c)s AND r.person_id IS NOT NULL
                       UNION
                       SELECT person_id, {_ACAD_R} FROM results_tf r
                       WHERE lower(btrim(r.school)) = %(c)s AND r.person_id IS NOT NULL) c
                JOIN   athlete_season s ON s.person_id = c.person_id AND s.year = c.acad""",
                        {"c": club})
            a, n = cur.fetchone()
            share[club] = (a, n)
        c.rollback()

    not_school = re.compile(NOT_SCHOOL_RE)
    by = {}
    for acad, sport, day, meet, school, grade, ceil in rows:
        by.setdefault(acad, []).append((sport, day, meet, school, grade, ceil))
    for acad in sorted(by):
        rs = by[acad]
        v = verdict.get(acad)
        print(f"\n== academic {acad}: verdict {v or '(none)'}; pools {pools.get(acad) or '-'}")
        schools = sorted({(s or "").strip() for _sp, _d, _m, s, _g, _c in rs})
        is_school = [s for s in schools
                     if not (club_re.search(s.lower()) or not_school.search(s.lower()))]
        print(f"   teams: {schools}")
        print(f"   [1] no school team: {'YES' if not is_school else 'NO -- school-shaped: ' + str(is_school)}")
        blocking = [(d, mt, cl) for _sp, d, mt, _s, _g, cl in rs
                    if cl is not None and not cl.startswith(("college", "pro"))]
        unknown = sum(1 for *_x, cl in rs if cl is None)
        print(f"   [2] every race's ceiling college/pro: "
              f"{'YES' if not blocking else 'NO'} ({unknown} races have no ceiling)")
        for sport, d, mt, s, g, cl in rs:
            mark = "  <-- blocks [2]" if (d, mt, cl) in blocking else ""
            print(f"       {sport} {str(d)[:10]}  {(mt or '')[:44]:44}  team {s!r} grade {g!r}  "
                  f"ceiling {cl or '-'}{mark}")
        for cl in sorted({(s or "").strip().lower() for _sp, _d, _m, s, _g, _c in rs} & set(share)):
            a, n = share[cl]
            print(f"   [3] club {cl!r}: {a} of {n} athlete-seasons pooled pro/college "
                  f"({100.0 * a / n if n else 0:.0f}%; adult if > {100 * ADULT_CLUB_SHARE:.0f}%)")


if __name__ == "__main__":
    main()
