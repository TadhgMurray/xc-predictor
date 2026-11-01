#!/usr/bin/env python3
"""
diag_div_stragglers.py -- rows of one race normalised at a different distance
from the rest of their own group. READ-ONLY.

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_div_stragglers.py --meet tfrrs:18170 --meet tfrrs:18175
    /srv/venv/bin/python scripts/diag_div_stragglers.py --sweep --since 2019     # every race

★ WHY (owner, 2026-11-01: Sagebrush 2021, tfrrs 18170 -- men 7K and women 6K
  in one division labelled 7000 m). Every woman was rated as a 6K except
  Paytin Drollinger, 141.9 (= a 14:24 5K) for 20:22; the Steve Reeder meet
  (18175) has three more. A combined division's women get their 6000 from a
  distance_pin part row ('g:F'), applied by the pool the row had WHEN IT WAS
  NORMALISED. A row whose gender was unknown or wrong then kept 7000; the
  rating is recomputed on today's pool, so it reads as a women's 7K.

  normalized_time / time_seconds is the distance factor each row was given.
  Inside one (race, pool) it should be one number give or take the weather;
  a row a few percent off its group's median is a straggler.

  per row: result, person, today's gender (person_gender), rated pool
  (rating_pool), time, normalized_time, factor, its group's median factor,
  rating -- and the race's distance_pin rows.
"""
import argparse
import os
import statistics
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GAP = 0.04          # a factor this far (4%) off its group's median is a straggler

_ROWS = """
    SELECT r.result_id, r.source, r.meet_id, r.div_id, r.person_id,
           COALESCE(a.name, '') AS name,
           pg.gender, split_part(COALESCE(r.rating_pool, ''), '|', 1) AS pool,
           r.time_seconds, r.normalized_time, r.speed_rating
    FROM   results r
    LEFT   JOIN person_gender pg ON pg.person_id = r.person_id
    LEFT   JOIN LATERAL (SELECT concat_ws(' ', a.first_name, a.last_name) AS name
                         FROM athletes a
                         WHERE a.athlete_id = COALESCE(r.person_id, r.athlete_id)
                         LIMIT 1) a ON TRUE
    WHERE  {where}
      AND  r.time_seconds > 0 AND r.normalized_time > 0
"""


def stragglers(rows, gap=GAP):
    """rows [(key, pool, factor, row)] -> [(row, group median)] whose factor
    is more than `gap` off the median of its (key, pool) group (3+ rows).
    Pure."""
    groups = defaultdict(list)
    for key, pool, factor, row in rows:
        if pool:                     # a row no pool rated is nobody's straggler
            groups[(key, pool)].append((factor, row))
    out = []
    for _k, g in groups.items():
        if len(g) < 3:
            continue
        med = statistics.median(f for f, _r in g)
        for f, row in g:
            if med and abs(f / med - 1.0) > gap:
                out.append((row, med))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meet", action="append", default=[],
                    help="source:meet_id, e.g. tfrrs:18170 (repeatable)")
    ap.add_argument("--sweep", action="store_true", help="every race since --since")
    ap.add_argument("--since", type=int, default=2019)
    ap.add_argument("--show", type=int, default=60)
    a = ap.parse_args()
    if not a.meet and not a.sweep:
        ap.error("--meet or --sweep")

    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        if a.meet:
            pairs = [(m.split(":")[0], int(m.split(":")[1])) for m in a.meet]
            where = " OR ".join("(r.source = %s AND r.meet_id = %s)" for _ in pairs)
            args = [x for p in pairs for x in p]
            cur.execute(_ROWS.format(where=f"({where})"), args)
        else:
            cur.execute("SET LOCAL statement_timeout = '30min'")
            cur.execute(_ROWS.format(where="r.date >= %s"), (f"{a.since}-08-01",))
        rows = cur.fetchall()
        keyed = [((r[1], r[2], r[3]), r[7], float(r[9]) / float(r[8]), r) for r in rows]
        bad = stragglers(keyed)

        if a.meet:
            by_div = defaultdict(list)
            for key, pool, f, r in keyed:
                by_div[key].append((pool, f, r))
            med = {}
            for key, pool, f, _r in keyed:
                med.setdefault((key, pool), []).append(f)
            bad_ids = {r[0] for r, _m in bad}
            for key in sorted(by_div):
                cur.execute("""SELECT part, action, listed, distance, status, kind
                               FROM distance_pin WHERE source = %s AND meet_id = %s
                                 AND div_id = %s""", key)
                print(f"\n== {key[0]} meet {key[1]} div {key[2]}: distance_pin {cur.fetchall()}")
                print(f"   {'':2}{'result':>20} {'person':>10} {'name':<24}{'g':>2} {'pool':<10}"
                      f"{'time':>8}{'norm':>9}{'factor':>8}{'median':>8}{'rating':>8}")
                for pool, f, r in sorted(by_div[key], key=lambda x: x[2][8]):
                    m = statistics.median(med[(key, pool)])
                    print(f"   {'<-' if r[0] in bad_ids else '':2}{r[0]:>20} {r[4] or 0:>10} "
                          f"{(r[5] or '')[:23]:<24}{r[6] or '?':>2} {pool or '?':<10}"
                          f"{float(r[8]):>8.1f}{float(r[9]):>9.1f}{f:>8.3f}{m:>8.3f}"
                          f"{float(r[10]) if r[10] is not None else float('nan'):>8.1f}")
        print(f"\n{len(bad):,} stragglers (factor more than {GAP:.0%} off their race-and-pool median)"
              f" in {len({r[1:4] for r, _m in bad}):,} races")
        if a.sweep:
            for r, m in sorted(bad, key=lambda x: -abs(float(x[0][9]) / float(x[0][8]) / x[1] - 1))[:a.show]:
                print(f"   {r[1]}:{r[2]} div {r[3]}  {r[0]}  {(r[5] or '')[:22]:<23}{r[6] or '?'} "
                      f"{r[7] or '?':<10} factor {float(r[9]) / float(r[8]):.3f} vs {m:.3f}  "
                      f"rating {r[10]}")
        conn.rollback()


if __name__ == "__main__":
    main()
