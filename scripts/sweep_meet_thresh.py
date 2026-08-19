#!/usr/bin/env python3
# File: scripts/sweep_meet_thresh.py
# Purpose: For meet links, show the 1:1-clean rate at several confidence
#          thresholds, so the meet threshold is picked where fan-out collapses
#          rather than guessed. 1:1-clean = tfrrs meet claimed by exactly one anet
#          meet (what the fan-out guard keeps). Read-only.
import argparse
import sys
sys.path.insert(0, "scripts")
from database import getConn


def _query(sql, params=()):
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def _cleanRate(sport, thr):
    # among meet links >= thr, how many tfrrs meets are claimed by exactly 1 anet meet
    rows = _query("""
        SELECT
          count(*) AS total,
          count(*) FILTER (WHERE claims = 1) AS clean
        FROM (
            SELECT tfrrs_id, count(DISTINCT anet_id) AS claims
            FROM entity_links
            WHERE entity_type='meet' AND sport=%s AND confidence >= %s
            GROUP BY tfrrs_id
        ) s
    """, (sport, thr))
    total, clean = rows[0]
    return total or 0, clean or 0


def _report(sport, thresholds):
    print(f"\n=== {sport}: meet 1:1-clean rate by threshold ===")
    print(f"  {'thresh':>7} {'tfrrs meets':>12} {'kept 1:1':>10} {'clean%':>8}")
    for thr in thresholds:
        total, clean = _cleanRate(sport, thr)
        pct = (100.0 * clean / total) if total else 0.0
        print(f"  {thr:>7} {total:>12,} {clean:>10,} {pct:>7.1f}%")


def _parseArgs():
    p = argparse.ArgumentParser()
    p.add_argument("--sport", choices=["TF", "XC"], default="TF")
    return p.parse_args()


def main():
    args = _parseArgs()
    thresholds = [5, 10, 20, 30, 50, 75, 100, 150]
    _report(args.sport, thresholds)
    print("\n=== pick the threshold where clean% flattens near ~100. ===")


if __name__ == "__main__":
    main()