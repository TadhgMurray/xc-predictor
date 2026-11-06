#!/usr/bin/env python3
"""
copy_align.py -- one race listed twice is rated once: a runner only one copy
lists is priced off the other copy's field.

    set -a; . /etc/xc-predictor.env; set +a; P=/srv/venv/bin/python
    $P engine/copy_align.py                         # DRY RUN, last 150 days
    $P engine/copy_align.py --since 2000-01-01      # every season
    $P engine/copy_align.py --write                 # apply (nightly 09c3)

★ WHY (owner, 2026-11-06, /race/xc/25861/1: "Dalton Mortensen here?").
  The 2025 BYU Autumn Classic is on tfrrs and on athletic.net. The duplicate
  rules take the tfrrs rows that match an athletic.net row; Dalton Mortensen,
  unattached, is on tfrrs only, so his row stayed -- in a "race" of two
  rated rows. The solve fitted the day (heat, altitude) on the athletic.net
  copy's full field; his copy had nobody to read it from. One 19:55: Noah
  Jenkins 115.2 on athletic.net, Mortensen 128.9 on tfrrs, and Mortensen on
  the college board.

★ THE RULE. Two divisions of one day are COPIES when MIN_SHARED+ persons run
  both within a second. The copy with more rated rows is the reference. A
  rated row of the other copy whose person is not in the reference is priced
  from the reference's rated rows of its pool around its time: within one
  race the course, the distance and the day are the same for all, so a
  rating goes as 1/time and

      expected = median over j of rating_j * time_j / time_i

  over the reference rows within NEAR of its time (else all of them, at
  least MIN_REF). A row more than TOL from that is set to it.

! ONE RACE, NOT TWO THAT OVERLAP (owner's dry run, 2026-11-06: 129 -> 230,
  93 -> 31). Two divisions sharing five runners can be a combined race and
  one of its parts. So: SAME_FRAC of the smaller division shared, the row
  inside the reference's times, and a move past MAX_MOVE is listed, never
  written. The duplicate rules' flagged rows COUNT as shared (they are the
  proof of the copy) but are never moved.
! BOTH WAYS: a copy rated low is as wrong as one rated high; the reference's
  field decides. IDEMPOTENT: an aligned row reads true the next night.
! LOGGED: copy_align_row holds every row moved, from what to what.
"""
import argparse
import math
import os
import statistics
import sys
import time
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MIN_SHARED = 5      # persons in both divisions, within SAME_S, before they are copies
SAME_FRAC = 0.6     # ... and this share of the smaller division (one race, not two that overlap)
SAME_S = 1.0
RANGE = 0.03        # a row outside the reference's times (by this much) is not in that race
MAX_MOVE = 0.20     # a copy 20%+ off the reference is a different race: listed, never moved
MIN_REF = 3         # reference rows (of the row's pool) before a row is priced from them
NEAR = 0.08         # ... preferring those within 8% of its time
TOL = 0.02          # a row this far (log) from the reference's price is moved


def expected(t, ref):
    """t: the row's time; ref: [(time, rating)] of the reference copy, one
    pool. The reference's price for t, or None. Pure."""
    ref = [(float(tj), float(rj)) for tj, rj in ref if tj and rj and tj > 0 and rj > 0]
    near = [(tj, rj) for tj, rj in ref if abs(math.log(tj / t)) <= NEAR]
    use = near if len(near) >= MIN_REF else ref
    if len(use) < MIN_REF:
        return None
    return statistics.median(rj * tj / t for tj, rj in use)


def plan(divs, shared, flagged=frozenset(), wide=None):
    """divs: {div: [(result_id, person, pool, time, rating)]}; shared:
    {(divA, divB): n persons in both}; flagged: result ids the duplicate
    rules took (shared, never moved). -> [(result_id, old, new, ref div)]
    for the rows to move; `wide`, a list, collects the moves past MAX_MOVE.
    Pure."""
    out = []
    for (a, b), n in shared.items():
        if a not in divs or b not in divs:
            continue
        if n < max(MIN_SHARED, SAME_FRAC * min(len(divs[a]), len(divs[b]))):
            continue
        rated = lambda d: sum(1 for r in divs[d] if r[4] and r[0] not in flagged)  # noqa: E731
        ref, other = (a, b) if rated(a) >= rated(b) else (b, a)
        in_ref = {r[1] for r in divs[ref]}
        by_pool = defaultdict(list)
        for rid, _pid, pool, t, s in divs[ref]:
            if s and rid not in flagged:
                by_pool[pool].append((t, s))
        times = [r[3] for r in divs[ref] if r[3]]
        lo, hi = min(times) * (1 - RANGE), max(times) * (1 + RANGE)
        for rid, pid, pool, t, s in divs[other]:
            if not s or rid in flagged or pid in in_ref or not t or not lo <= t <= hi:
                continue
            e = expected(float(t), by_pool.get(pool, []))
            if not e or abs(math.log(float(s) / e)) <= TOL:
                continue
            if abs(math.log(float(s) / e)) > MAX_MOVE:
                if wide is not None:
                    wide.append((rid, float(s), round(e, 2), ref))
                continue
            out.append((rid, float(s), round(e, 2), ref))
    return out


_ROWS = """
    SELECT r.result_id, r.person_id, r.source, r.meet_id, r.div_id, left(r.date, 10) AS d,
           r.time_seconds, r.speed_rating, COALESCE(r.rating_pool, '') AS pool,
           EXISTS (SELECT 1 FROM result_twin x
                   WHERE x.sport = 'XC' AND x.result_id = r.result_id) AS flagged
    FROM   results r
    WHERE  r.date >= %(lo)s AND r.date < %(hi)s AND r.date ~ '^[0-9]{4}-'
      AND  r.person_id IS NOT NULL AND r.time_seconds BETWEEN 60 AND 14400
"""

_LOG_DDL = """
CREATE TABLE IF NOT EXISTS copy_align_row (
    result_id bigint, old real, new real, ref_source text, ref_meet bigint, ref_div bigint,
    at timestamptz NOT NULL DEFAULT now())
"""


def alignYear(getConn, lo, hi, write, show):
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(_ROWS, {"lo": lo, "hi": hi})
            raw = cur.fetchall()
        conn.rollback()
        divs = defaultdict(list)
        by_pd = defaultdict(list)
        flagged = set()
        for rid, pid, src, meet, div, d, t, s, pool, flag in raw:
            if flag:
                flagged.add(rid)
            key = (src, int(meet), int(div), d)
            divs[key].append((rid, pid, pool, float(t), float(s) if s else None))
            by_pd[(pid, d)].append((key, float(t)))
        shared = defaultdict(int)
        for rows in by_pd.values():
            if len(rows) < 2:
                continue
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    (ka, ta), (kb, tb) = rows[i], rows[j]
                    if ka != kb and abs(ta - tb) <= SAME_S:
                        shared[tuple(sorted((ka, kb)))] += 1
        wide = []
        moves = plan(divs, shared, flagged, wide)
        n_pairs = sum(1 for (a, b), n in shared.items()
                      if n >= max(MIN_SHARED, SAME_FRAC * min(len(divs[a]), len(divs[b]))))
        print(f"[copy align] {lo[:4]}: {len(raw):,} rows, {n_pairs:,} pairs of copies, "
              f"{len(moves):,} rows priced off the other copy"
              f"{f', {len(wide):,} more than {MAX_MOVE:.0%} off left alone' if wide else ''}",
              flush=True)
        for rid, old, new, ref in sorted(moves, key=lambda m: -abs(m[1] - m[2]))[:show]:
            print(f"    {old:6.1f} -> {new:6.1f}  result {rid}  (reference /race/xc/{ref[1]}/{ref[2]}, "
                  f"{ref[0]}, {ref[3]})")
        if not write or not moves:
            return len(moves)
        from psycopg2.extras import execute_values
        with conn.cursor() as cur:
            cur.execute(_LOG_DDL)
            execute_values(cur, """UPDATE results r SET speed_rating = v.new
                                   FROM (VALUES %s) v(result_id, new)
                                   WHERE r.result_id = v.result_id""",
                           [(rid, new) for rid, _o, new, _r in moves])
            execute_values(cur, """INSERT INTO copy_align_row
                                   (result_id, old, new, ref_source, ref_meet, ref_div) VALUES %s""",
                           [(rid, old, new, ref[0], ref[1], ref[2]) for rid, old, new, ref in moves])
        conn.commit()
        return len(moves)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default=None, help="first date (default: 150 days ago)")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=20)
    a = ap.parse_args(argv)
    import datetime
    since = a.since or (datetime.date.today() - datetime.timedelta(days=150)).isoformat()
    from database import getConn
    t0, total = time.time(), 0
    for year in range(int(since[:4]), datetime.date.today().year + 1):
        total += alignYear(getConn, max(since, f"{year}-01-01"), f"{year + 1}-01-01",
                           a.write, a.show)
    print(f"[copy align] {'written' if a.write else 'DRY RUN'}: {total:,} rows "
          f"({(time.time() - t0) / 60:.1f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
