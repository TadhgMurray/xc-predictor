#!/usr/bin/env python3
"""
cross_division_dupes.py -- one run listed in two divisions of one meet.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P scripts/cross_division_dupes.py                  # last 120 days
    $P scripts/cross_division_dupes.py --days 800 --show 80

★ WHY (owner, 2026-11-05, Trey Davidson, NYU). athletic.net's Al Carius and
  Paul Olsen Open 2026 holds him twice at 25:36.3: 1st in the men's 8K
  (division 1157056) and 27th in the women's 6K (1157057, which it titles
  "Open Men"). The men's row was given a page of its own and filed as a
  woman (132.9, #1 college junior woman in DIII); the women's-race row sits
  on his real page as a 6K (81.5). No rule saw it: dup_same_feed needs one
  person in one division.

★ WHAT IT LISTS: two rows of one feed, one meet, one day, the same name and
  the same time to the tenth, in two divisions; for each division the gender
  its other runners are (person_gender, a majority of 70%), the persons the
  two rows are on and how many races each holds. The row in the division
  whose field is not the established runner's gender is the error.
  READ-ONLY.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

_PAIRS = """
    SELECT a.source, a.meet_id, left(a.date, 10) AS day, a.athlete_name,
           a.result_id, a.div_id, a.person_id, b.result_id, b.div_id, b.person_id,
           a.time_seconds
    FROM   results a
    JOIN   results b ON b.source = a.source AND b.meet_id = a.meet_id
                    AND left(b.date, 10) = left(a.date, 10) AND b.div_id > a.div_id
                    AND abs(b.time_seconds - a.time_seconds) < 0.05
                    AND lower(regexp_replace(b.athlete_name, '[^A-Za-z]+', '', 'g'))
                      = lower(regexp_replace(a.athlete_name, '[^A-Za-z]+', '', 'g'))
    WHERE  a.date >= %(since)s AND a.time_seconds > 0 AND b.time_seconds > 0
      AND  NULLIF(btrim(a.athlete_name), '') IS NOT NULL
"""

_FIELD = """
    SELECT r.source, r.meet_id, r.div_id,
           count(*) FILTER (WHERE pg.gender = 'M') AS m,
           count(*) FILTER (WHERE pg.gender = 'F') AS f
    FROM   results r JOIN person_gender pg ON pg.person_id = r.person_id
    WHERE  (r.source, r.meet_id, r.div_id) IN (SELECT * FROM unnest(%(s)s::text[], %(m)s::bigint[], %(d)s::bigint[]))
    GROUP  BY 1, 2, 3
"""


def fieldGender(m, f, share=0.7):
    """'M' / 'F' when share of the known runners are one, else None. Pure."""
    n = (m or 0) + (f or 0)
    if n < 5:
        return None
    return "M" if m >= share * n else "F" if f >= share * n else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--show", type=int, default=40)
    a = ap.parse_args(argv)
    import datetime
    from database import getConn
    since = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = 0")
        cur.execute(_PAIRS, {"since": since})
        pairs = cur.fetchall()
        divs = sorted({(p[0], p[1], p[5]) for p in pairs} | {(p[0], p[1], p[8]) for p in pairs})
        field = {}
        if divs:
            cur.execute(_FIELD, {"s": [d[0] for d in divs], "m": [d[1] for d in divs],
                                 "d": [d[2] for d in divs]})
            field = {(s, m, d): fieldGender(nm, nf) for s, m, d, nm, nf in cur.fetchall()}
        pids = sorted({p[6] for p in pairs if p[6]} | {p[9] for p in pairs if p[9]})
        races, gender = {}, {}
        if pids:
            cur.execute("SELECT person_id, count(*) FROM results WHERE person_id = ANY(%s) GROUP BY 1", (pids,))
            races = dict(cur.fetchall())
            cur.execute("SELECT person_id, gender FROM person_gender WHERE person_id = ANY(%s)", (pids,))
            gender = dict(cur.fetchall())
        conn.rollback()
    mixed = 0
    lines = []
    for src, meet, day, name, ra, da, pa, rb, db, pb, t in pairs:
        ga, gb = field.get((src, meet, da)), field.get((src, meet, db))
        if not (ga and gb and ga != gb):
            continue                     # one race posted twice the same way: dup rules' business
        mixed += 1
        est = pa if races.get(pa, 0) >= races.get(pb, 0) else pb
        g = gender.get(est)
        wrong = ra if ga != g else rb if gb != g else None
        lines.append(f"  {day}  /race/xc/{meet}/{da} ({ga}) + /{db} ({gb})  {name}  {t:.1f}s  "
                     f"pages {pa}({races.get(pa, 0)}) / {pb}({races.get(pb, 0)}), runner {g or '?'}"
                     f"  -> wrong copy {wrong or '?'}")
    print(f"[cross-division] {len(pairs):,} name+time pairs in two divisions of one meet since {since}; "
          f"{mixed:,} across a men's and a women's field (one run in the other gender's race)")
    for ln in lines[:a.show]:
        print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
