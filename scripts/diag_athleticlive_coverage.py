#!/usr/bin/env python3
"""
diag_athleticlive_coverage.py -- what share of athletic.net's meets and
results AthleticLIVE also carries. READ ONLY.

    cd /srv/xc-predictor && set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_athleticlive_coverage.py

★ WHY (owner, 2026-10-23): "we should scrape all of athletic live (what
  percent of athletic net is athletic live?), then scrape locals". Every
  AthleticLIVE meet names its athletic.net meet (docs/athleticlive/
  meets_2025_26.csv: 19,262 meets, 99% with an athletic.net id, collected
  from AthleticLIVE's public meet list one week per request). Matched here
  to the anet meets in our results tables, per season and sport: the share
  of MEETS and the share of RESULTS (big meets are the timed ones), and by
  state.
"""
import csv
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)

CSV = os.path.join(_ROOT, "docs", "athleticlive", "meets_2025_26.csv")
SEASONS = (("XC fall 2025", "results", "2025-08-01", "2025-12-15", "XC"),
           ("TF winter+spring 2026", "results_tf", "2025-12-01", "2026-07-31", "TF"))


def aliveIds(sport):
    """{athletic.net meet id: (timer, state)} for AthleticLIVE meets of a sport."""
    out = {}
    with open(CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            kind = r.get("anet_sport") or ("XC" if r.get("o") == "xc" else "TF")
            if kind != sport:
                continue
            for a in (r.get("anet_ids") or "").split():
                if a.isdigit():
                    out[int(a)] = (r.get("timer") or r.get("timer_slug") or "", r.get("state") or "")
    return out


def main():
    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        for label, table, d0, d1, sport in SEASONS:
            alive = aliveIds(sport)
            cur.execute(f"""
                SELECT meet_id, count(*) FROM {table}
                WHERE source = 'anet' AND date >= %s AND date <= %s
                  AND time_seconds > 0 AND time_seconds < 999999
                GROUP BY meet_id""", (d0, d1 + "T99"))
            meets = dict(cur.fetchall())
            on = {m: n for m, n in meets.items() if m in alive}
            n_m, n_r = len(meets), sum(meets.values())
            print(f"\n== {label}: athletic.net {n_m:,} meets, {n_r:,} results in our tables")
            print(f"   on AthleticLIVE: {len(on):,} meets ({100 * len(on) / max(n_m, 1):.0f}%), "
                  f"{sum(on.values()):,} results ({100 * sum(on.values()) / max(n_r, 1):.0f}%)")
            missing = [a for a in alive if a not in meets]
            print(f"   AthleticLIVE meets of this season not in our tables: {len(missing):,} "
                  f"(not scraped yet, or no results)")
            # by size of meet
            for lo, hi in ((0, 50), (50, 200), (200, 1000), (1000, 10 ** 9)):
                sub = {m: n for m, n in meets.items() if lo <= n < hi}
                s_on = sum(1 for m in sub if m in alive)
                print(f"     meets with {lo}-{hi if hi < 10 ** 9 else '+'} results: "
                      f"{len(sub):>6,}   on AthleticLIVE {100 * s_on / max(len(sub), 1):>3.0f}%")
            # by state, from the meets table's state where we have it
            mtable = "meets" if table == "results" else "meets_tf_meta"
            scol = "state"
            try:
                cur.execute(f"SELECT DISTINCT meet_id, {scol} FROM {mtable} WHERE meet_id = ANY(%s)",
                            (list(meets),))
                st = dict(cur.fetchall())
            except Exception:                               # noqa: BLE001
                conn.rollback()
                st = {}
            by = defaultdict(lambda: [0, 0, 0, 0])
            for m, n in meets.items():
                b = by[st.get(m) or "?"]
                b[0] += 1
                b[1] += n
                if m in alive:
                    b[2] += 1
                    b[3] += n
            print("   by state (meets, % on AthleticLIVE by meets / by results):")
            for s, (nm, nr, am, ar) in sorted(by.items(), key=lambda kv: -kv[1][1])[:30]:
                print(f"     {s:<4} {nm:>6,}   {100 * am / max(nm, 1):>3.0f}% / {100 * ar / max(nr, 1):>3.0f}%")
            conn.rollback()


if __name__ == "__main__":
    main()
