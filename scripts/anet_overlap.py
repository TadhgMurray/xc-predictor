#!/usr/bin/env python3
"""
anet_overlap.py -- how much of athletic.net's cross country we could have
from somewhere else (owner, 2026-11-12, after athletic.net put its results
call behind a challenge: "I can't see a way the site can continue").

READ-ONLY. For every athletic.net XC meet of the season that has results
here, is the same meet also in:
  - AthleticLIVE (alive_meets names the athletic.net meet ids it is), or
  - a timer's own site (meets_ext: same date, same state when both name
    one, names agreeing -- sources.anet_twins.match, the matcher the
    nightly uses; here without its "as many races" test, since the question
    is "is the meet there", not "may athletic.net's copy be skipped")?

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/anet_overlap.py                  # this season
    /srv/venv/bin/python scripts/anet_overlap.py --since 2025-08-15 --until 2025-12-15

Prints meets and results covered, by source; the states with the most
athletic.net-only results; and the timers that stand in for the most meets.
"""
import argparse
import collections
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _has(cur, t):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (t,))
    return bool(cur.fetchone()[0])


def main(argv=None):
    from sources.anet_twins import match
    from database import getConn
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--since", default="2026-08-15")
    ap.add_argument("--until", default="2099-12-31")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args(argv)
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SET statement_timeout = '600s'")
        cur.execute("""
            SELECT m.meet_id, max(m.meet_name), max(m.meet_date), max(m.state)
            FROM   meets m
            WHERE  m.meet_date BETWEEN %s AND %s
            GROUP  BY m.meet_id""", (a.since, a.until))
        meets = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
        ids = sorted(meets)
        cur.execute("""
            SELECT meet_id, count(*) FROM results
            WHERE  source = 'anet' AND meet_id = ANY(%s)
            GROUP  BY meet_id""", (ids,))
        n_res = dict(cur.fetchall())
        meets = {k: v for k, v in meets.items() if k in n_res}
        alive = set()
        if _has(cur, "public.alive_meets"):
            cur.execute("""SELECT DISTINCT unnest(anet_ids) FROM alive_meets
                           WHERE sport = 'XC' AND meet_date BETWEEN %s AND %s""",
                        (a.since, a.until))
            alive = {r[0] for r in cur.fetchall()} & set(meets)
        timers = collections.defaultdict(list)
        if _has(cur, "public.meets_ext"):
            cur.execute("""SELECT ext_meet_id, meet_name, meet_date, state, source
                           FROM meets_ext
                           WHERE sport = 'XC' AND meet_date BETWEEN %s AND %s""",
                        (a.since, a.until))
            for ext, name, day, st, src in cur.fetchall():
                timers[str(day)[:10]].append((ext, name, day, st, src))
        conn.rollback()

    by_timer = collections.Counter()
    covered_t = set()
    for mid, (name, day, st) in meets.items():
        if mid in alive:
            continue
        cands = timers.get(str(day)[:10]) or []
        hit = match(name, day, st, [c[:4] for c in cands])
        if hit is not None:
            covered_t.add(mid)
            by_timer[next(c[4] for c in cands if c[0] == hit)] += 1

    total_m, total_r = len(meets), sum(n_res[m] for m in meets)
    def line(label, ms):
        r = sum(n_res[m] for m in ms)
        print(f"  {label:<34}{len(ms):>7,} meets ({100 * len(ms) / max(total_m, 1):5.1f}%)"
              f"{r:>11,} results ({100 * r / max(total_r, 1):5.1f}%)")
    print(f"athletic.net XC meets with results, {a.since} .. {a.until}: "
          f"{total_m:,} meets, {total_r:,} results")
    line("also on AthleticLIVE", alive)
    line("also on a timer's own site", covered_t)
    line("covered by either", alive | covered_t)
    only = set(meets) - alive - covered_t
    line("athletic.net ONLY", only)

    st_only = collections.Counter()
    for m in only:
        st_only[meets[m][2] or "?"] += n_res[m]
    print(f"\nstates with the most athletic.net-only results:")
    for st, r in st_only.most_common(a.top):
        print(f"  {st:<4}{r:>10,}")
    print(f"\ntimer sources standing in for the most meets:")
    for src, n in by_timer.most_common(a.top):
        print(f"  {src:<40}{n:>6,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
