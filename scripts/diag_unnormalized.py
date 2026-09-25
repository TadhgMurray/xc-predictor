#!/usr/bin/env python3
"""
diag_unnormalized.py -- why a school's tfrrs track rows have no
normalized_time (and so no rating).

    /srv/venv/bin/python scripts/diag_unnormalized.py --school Tufts
    /srv/venv/bin/python scripts/diag_unnormalized.py --school Tufts --seasons 2023 2024

★ WHY (owner, 2026-09-25): diag_tfrrs_identity showed Tufts track with 1,742
  and 1,733 tfrrs rows in 2023 and 2024 and NOT ONE normalized, against 38-60
  in the years either side. This sorts every such row into the first reason
  that explains it, in the backfill's own order:

    field / relay      not a timed individual race -- never normalized
    no time            no time, or a non-finish sentinel
    not a distance     the event name parses to no distance of 800 m+ (the
                       engine's floor), or does not parse at all
    twin, anet has it  the same person has anet rows at this canon meet and
                       one of them is this event: the anet copy carries it
    twin, anet LACKS   same person at the same canon meet, but no anet row
                       at this distance -- the backfill drops the tfrrs row
                       anyway (its twin test is per person per meet, not per
                       event), so this race is lost from the ratings
    should normalize   none of the above

  and prints the commonest event names in the unexplained buckets.
  READ-ONLY.
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn                                  # noqa: E402
from event_parse import distanceFromEventShort                # noqa: E402

MIN_RATED_M = 800


def _dist(ev):
    try:
        got = distanceFromEventShort(ev)
    except Exception:                                        # noqa: BLE001
        return None
    d = got[0] if isinstance(got, tuple) else got
    try:
        return float(d) if d else None
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--school", required=True)
    ap.add_argument("--seasons", type=int, nargs="+", default=[2021, 2022, 2023,
                                                               2024, 2025])
    a = ap.parse_args()
    with getConn() as conn:
        with conn.cursor() as cur:
            for y in a.seasons:
                lo, hi = f"{y}-08-01", f"{y + 1}-08-01"
                cur.execute("""
                    SELECT result_id, person_id, canon_meet_id, event_short,
                           COALESCE(is_field::int, 0), COALESCE(is_relay::int, 0),
                           time_seconds, normalized_time
                    FROM   results_tf
                    WHERE  source = 'tfrrs' AND school = %s
                      AND  date >= %s AND date < %s
                """, (a.school, lo, hi))
                rows = cur.fetchall()
                pids = sorted({r[1] for r in rows if r[1] is not None})
                canons = sorted({r[2] for r in rows if r[2] is not None})
                anet = defaultdict(set)          # (pid, canon) -> {distance}
                if pids and canons:
                    cur.execute("""
                        SELECT person_id, canon_meet_id, event_short
                        FROM   results_tf
                        WHERE  source = 'anet' AND person_id = ANY(%s)
                          AND  canon_meet_id = ANY(%s)
                    """, (pids, canons))
                    for pid, canon, ev in cur.fetchall():
                        d = _dist(ev)
                        anet[(pid, canon)].add(round(d) if d else None)
                why, ev_by = Counter(), defaultdict(Counter)
                for rid, pid, canon, ev, fld, rel, t, nt in rows:
                    if nt is not None:
                        why["normalized"] += 1
                        continue
                    if fld or rel:
                        k = "field / relay"
                    elif t is None or float(t) <= 0 or 19999 <= float(t) <= 20001 \
                            or float(t) >= 100000:
                        k = "no time"
                    else:
                        d = _dist(ev)
                        if not d or d < MIN_RATED_M:
                            k = "not a distance"
                        elif (pid, canon) in anet:
                            k = ("twin, anet has it" if round(d) in anet[(pid, canon)]
                                 else "twin, anet LACKS")
                        else:
                            k = "should normalize"
                    why[k] += 1
                    ev_by[k][ev] += 1
                print(f"\n{a.school} tfrrs track, season {y}: {len(rows):,} rows")
                for k, n in why.most_common():
                    print(f"  {k:<20} {n:>7,}")
                for k in ("not a distance", "twin, anet LACKS", "should normalize"):
                    if ev_by[k]:
                        top = ", ".join(f"{e!r} {n}" for e, n in ev_by[k].most_common(8))
                        print(f"    {k}: {top}")


if __name__ == "__main__":
    main()
