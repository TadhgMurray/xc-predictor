#!/usr/bin/env python3
"""
print_status.py -- the owner's status page, printed.

    /srv/venv/bin/python scripts/print_status.py

  The same checks /account/status shows (racecast/site_status.py): the last
  pipeline run, the tfrrs identity gap, this season's rated rows, blank
  athletes, queries running over 5 seconds, open issue reports. READ-ONLY.
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn                                  # noqa: E402
import site_status as S                                       # noqa: E402


def _err(block):
    if isinstance(block, dict) and block.get("error"):
        print(f"  could not read: {block['error']}")
        return True
    return False


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true",
                    help="do not save the slow counts for /account/status")
    a = ap.parse_args()
    t0 = time.time()
    with getConn() as conn:
        # the slow counts, with no time limit; saved so the page can show
        # them without scanning a season on every load
        st = S.gather(conn, live_heavy=True)
        if not a.no_save:
            S.saveSnapshot(conn, {k: st[k] for k in S.HEAVY})
    print(f"SITE STATUS  {st['now']:%Y-%m-%d %H:%M}  season {st['season']}"
          f"  ({time.time() - t0:.0f}s"
          f"{'' if a.no_save else ', counts saved for /account/status'})")

    print("\nPIPELINE")
    pl = st["pipeline"]
    if not _err(pl):
        run = pl["latest"]
        state = (f"{len(run['failed'])} FAILED" if run["failed"]
                 else "running or stopped mid-step" if run["open"]
                 else "every step ok")
        print(f"  latest {run['dir']}: {state}; {len(run['steps'])} steps, "
              f"{S.ago(run['seconds'])} of step time")
        for s in run["open"]:
            print(f"  ! {s['step']}: no summary line, log idle {S.ago(s['idle_s'])}")
        for s in run["steps"]:
            if not s["ok"]:
                print(f"  ! {s['step']} FAILED after {S.ago(s['seconds'])}")
                for line in s.get("tail") or []:
                    print(f"      {line}")
        for r in pl["recent"]:
            print(f"  earlier {r['dir']}: "
                  + (f"failed {', '.join(r['failed'])}" if r["failed"]
                     else "stopped mid-step" if r["open"] else "ok"))

    print("\nTFRRS ROWS TIED TO A PERSON")
    if not _err(st["identity"]):
        print(f"  {'sport':<6}{'season':<8}{'rows':>10}{'person':>10}"
              f"{'id only':>10}{'normalized':>12}{'rated':>10}")
        for r in st["identity"]:
            print(f"  {r['sport']:<6}{r['season']:<8}{r['rows']:>10,}"
                  f"{r['person']:>10,}{r['native_only']:>10,}"
                  f"{r['normalized']:>12,}{r['rated']:>10,}")

    print("\nTHIS SEASON'S RATED ROWS")
    if not _err(st["rated"]):
        for r in st["rated"]:
            print(f"  {r['sport']:<4}{r['source']:<18}{r['rows']:>10,} rows"
                  f"{r['rated']:>10,} rated   latest {r['latest']}")

    print("\nBLANK ATHLETES")
    if not _err(st["blank"]):
        print(f"  {st['blank']['blank']:,} anet athletes with no name on any row; "
              f"{st['blank']['queued_meets']:,} anet meets queued")

    print("\nQUERIES OVER 5 SECONDS")
    if not _err(st["queries"]):
        if not st["queries"]:
            print("  none")
        for q in st["queries"]:
            print(f"  {q['pid']:>7} {S.ago(q['seconds']):>7} {q['app'][:20]:<20} "
                  f"{q['query'][:90]}")

    print("\nOPEN REPORTS")
    rep = st["reports"]
    if not _err(rep):
        print(f"  {rep['open']} open")
        for r in rep["rows"][:10]:
            print(f"  #{r['id']} {r['at']:%m-%d} {r['kind']:<9} "
                  f"{(r['page'] or '-')[:40]:<40} {r['detail'][:60]}")


if __name__ == "__main__":
    main()
