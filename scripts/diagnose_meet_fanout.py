#!/usr/bin/env python3
# File: scripts/diagnose_meet_fanout.py
# Purpose: The meet fan-out guard quarantines a meet link when a tfrrs meet is
#          claimed by MORE THAN ONE anet meet (ambiguous: which anet meet is it?).
#          A huge TF quarantine could mean (a) genuine ambiguity -- tfrrs fragments
#          legitimately matching several anet meets because low-conf meet links are
#          noisy -- or (b) something pathological. This measures it: for meet links
#          at/above the threshold, how many anet meets does each tfrrs meet claim?
# Read-only.
import argparse
import sys
sys.path.insert(0, "scripts")
from database import getConn

THRESH = {"TF": 2, "XC": 1}   # match merge_links.py


def _query(sql, params=()):
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def _report(sport):
    lo = THRESH[sport]
    print(f"\n=== {sport}: tfrrs-meet fan-out among meet links (conf >= {lo}) ===")
    # distribution: how many anet meets each tfrrs meet is linked to
    rows = _query("""
        SELECT anet_claims, count(*) AS n_tfrrs_meets
        FROM (
            SELECT tfrrs_id, count(DISTINCT anet_id) AS anet_claims
            FROM entity_links
            WHERE entity_type='meet' AND sport=%s AND confidence >= %s
            GROUP BY tfrrs_id
        ) s
        GROUP BY anet_claims ORDER BY anet_claims
    """, (sport, lo))
    total_tfrrs = sum(n for _c, n in rows)
    clean = sum(n for c, n in rows if c == 1)
    print(f"  tfrrs meets total: {total_tfrrs:,}")
    print(f"  claimed by exactly 1 anet meet (kept):    {clean:,} "
          f"({100.0*clean/total_tfrrs:.1f}%)")
    print(f"  claimed by >1 anet meet (quarantined):    {total_tfrrs-clean:,} "
          f"({100.0*(total_tfrrs-clean)/total_tfrrs:.1f}%)")
    print("  fan-out distribution (anet meets per tfrrs meet):")
    for claims, n in rows[:12]:
        print(f"    {claims:>4} anet meet(s): {n:>8,} tfrrs meets")
    if len(rows) > 12:
        worst = rows[-1]
        print(f"    ... worst: one tfrrs meet claimed by {worst[0]} anet meets")


def _parseArgs():
    p = argparse.ArgumentParser()
    p.add_argument("--sport", choices=["TF", "XC"])
    return p.parse_args()


def main():
    args = _parseArgs()
    for sport in ([args.sport] if args.sport else ["TF", "XC"]):
        _report(sport)
    print("\n=== done. ===")


if __name__ == "__main__":
    main()