#!/usr/bin/env python3
"""
why_two_rows.py -- an athlete page lists one race twice. READ ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/why_two_rows.py https://racecast.co/athlete/1234567
    /srv/venv/bin/python scripts/why_two_rows.py 1234567 2026-09-05
    /srv/venv/bin/python scripts/why_two_rows.py \
        https://racecast.co/meet/xc/28510 https://racecast.co/meet/xc/285474

The second form takes the two copies of the meet (the school page links
them; no athlete URL needed) and pairs their rows: by person, and by time
alone, with the gap and the duplicate flag of each pair.

(owner, 2026-10-13: MIT Alumni Cross Country Race, Sep 5 2026, on one page
as place 1 rated 134.9 and place 28 rated 126.6 -- "a duplicated meet, one
from anet one from tfrrs".)

Two copies of ONE run are the same time, and 04c's twin_same_day drops the
tfrrs one. Place 1 and place 28 at ratings eight points apart are not one
run: they are two runners' rows under one person -- one of them linked to
the wrong athlete. This prints, for every day the person has more than one
cross country row: each row's feed, meet, place, time, the feed's own name
and athlete id, its twin flag, and every link rule that moved it; then
which of the two it is.
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))


def parseArgs(argv):
    if not argv or len(argv) > 2:
        sys.exit(__doc__)
    m = re.search(r"/athlete/(\d+)", argv[0])
    person = int(m.group(1)) if m else int(argv[0])
    day = argv[1] if len(argv) == 2 else None
    return person, day


def _fmt(ts):
    return f"{int(ts // 60)}:{ts % 60:04.1f}" if ts else "-"


def compareMeets(m1, m2):
    from database import getConn
    with getConn() as c, c.cursor() as cur:
        rows = {}
        for m in (m1, m2):
            cur.execute("""
                SELECT r.result_id, r.source, r.div_id, r.place, r.time_seconds,
                       r.person_id, r.athlete_name, substr(r.date, 1, 10),
                       r.speed_rating, r.canon_meet_id, NULL,
                       (SELECT string_agg(t.reason, ',') FROM result_twin t
                        WHERE t.sport = 'XC' AND t.result_id = r.result_id)
                FROM   results r
                WHERE  r.meet_id = %s""", (m,))
            rows[m] = cur.fetchall()
            by = {}
            for r in rows[m]:
                k = (r[1], r[2], r[10], r[7])
                b = by.setdefault(k, [0, 0, 0, 0])
                b[0] += 1
                b[1] += r[8] is not None
                b[2] += r[5] is not None
                b[3] += r[11] is not None
            print(f"meet {m}:")
            for (src, dv, g, d), (n, nr, np_, nt) in sorted(by.items(), key=str):
                print(f"  {src:6} div {dv} {d}: {n} rows, {np_} with a "
                      f"person, {nr} rated, {nt} flagged duplicate")
        a, b = rows[m1], rows[m2]
        bp = {}
        for r in b:
            if r[5] is not None:
                bp.setdefault(r[5], []).append(r)
        same_p = [(x, y) for x in a if x[5] is not None for y in bp.get(x[5], [])]
        print(f"\nsame person in both copies: {len(same_p)}")
        for x, y in same_p[:25]:
            gap = abs((x[4] or 0) - (y[4] or 0))
            print(f"  person {x[5]} '{x[6] or y[6] or ''}': {x[1]} place {x[3]} "
                  f"{_fmt(x[4])} | {y[1]} place {y[3]} {_fmt(y[4])}  gap {gap:.1f}s "
                  f"dates {x[7]}/{y[7]}  flags {x[11] or '-'}/{y[11] or '-'}")
        bt = sorted((r[4], r) for r in b if r[4])
        import bisect
        keys = [t for t, _r in bt]
        timed = 0
        for x in a:
            if not x[4]:
                continue
            i = bisect.bisect_left(keys, x[4] - 1.0)
            if i < len(keys) and keys[i] < x[4] + 1.0:
                timed += 1
        print(f"rows of {m1} with a time within 1 s in {m2}: {timed} of {len(a)}")
        print("\n  -> same person, gap under 1 s, same date, no flag: 04c has not "
              "run since. Gap of 1 s or more, or different dates: the feeds disagree "
              "on the time or day. No same person but times line up: the copies' "
              "runners were never joined (two persons each).")


def main():
    if len(sys.argv) == 3 and all("/meet/" in x for x in sys.argv[1:]):
        ids = [int(re.search(r"/meet/xc/(\d+)", x).group(1)) for x in sys.argv[1:]]
        return compareMeets(*ids)
    person, day = parseArgs(sys.argv[1:])
    from database import getConn
    with getConn() as c, c.cursor() as cur:
        cur.execute("""
            SELECT substr(date, 1, 10) FROM results
            WHERE  person_id = %s AND date ~ '^[0-9]{4}-'
              AND  (%s::text IS NULL OR substr(date, 1, 10) = %s)
            GROUP  BY 1 HAVING count(*) > 1 ORDER BY 1""", (person, day, day))
        days = [r[0] for r in cur.fetchall()]
        if not days:
            sys.exit(f"person {person}: no day with two cross country rows")
        for d in days:
            print(f"\n{d}")
            cur.execute("""
                SELECT r.result_id, r.source, r.meet_id, r.div_id, r.place,
                       r.time_seconds, r.athlete_id, r.athlete_name, a.school,
                       r.speed_rating, r.canon_meet_id,
                       (SELECT string_agg(t.reason, ',') FROM result_twin t
                        WHERE t.sport = 'XC' AND t.result_id = r.result_id)
                FROM   results r
                LEFT   JOIN athletes a ON r.source = 'anet' AND a.athlete_id = r.athlete_id
                WHERE  r.person_id = %s AND substr(r.date, 1, 10) = %s
                ORDER  BY r.source, r.place""", (person, d))
            rows = cur.fetchall()
            for (rid, src, m, dv, pl, ts, aid, nm, sch, sr, canon, twin) in rows:
                cur.execute("""SELECT rule, from_person, linked_at::date
                               FROM person_link_log
                               WHERE sport = 'XC' AND result_id = %s
                               ORDER BY linked_at""", (rid,))
                links = cur.fetchall()
                t = f"{int(ts // 60)}:{ts % 60:04.1f}" if ts else "-"
                print(f"  {src:6} {m}/{dv} place {pl}  {t}  rated {sr}  "
                      f"feed id {aid} '{nm or ''}' {sch or ''}  canon {canon}  "
                      f"twin {twin or '-'}")
                for rule, frm, when in links:
                    print(f"         moved here by {rule} from person {frm} ({when})")
                if not links:
                    print("         no link rule moved it: the person was built with it")
            times = [r[5] for r in rows if r[5]]
            if len(times) > 1 and max(times) - min(times) < 1.0:
                print("  -> ONE run in two feeds: twin_same_day should flag the tfrrs "
                      "copy at the next 04c.")
            else:
                print("  -> TWO RUNNERS under one person: the row whose feed name or "
                      "id does not match the athlete was linked to them wrongly "
                      "(the rule above that moved it is the bug).")


if __name__ == "__main__":
    main()
