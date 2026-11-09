#!/usr/bin/env python3
"""
explain_school_page.py -- every statement one school page runs, under
EXPLAIN (ANALYZE, BUFFERS), slowest first.

    /srv/venv/bin/python scripts/explain_school_page.py --school "Perrysburg"
    /srv/venv/bin/python scripts/explain_school_page.py --school "BYU" --sport TF --state UT

★ WHY (2026-11-09). The school page is the slow log's costliest page kind
  and runs a dozen statements; explain_pages.py covers some of them without
  the state chip, which most school pages carry. This runs the page's own
  functions (school.py, school_identity.py) the way app.school_page does,
  records the SQL each one sends, then EXPLAINs exactly those statements
  with their real parameters. Read-only: everything runs in one transaction
  that is rolled back.
"""
import argparse
import os
import re
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "racecast", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import psycopg2.extras  # noqa: E402
from database import getConn  # noqa: E402


class _Recorder:
    """A cursor that remembers every statement it is given."""

    def __init__(self, cur, log):
        self._cur, self._log = cur, log

    def execute(self, sql, params=None):
        t = time.time()
        out = self._cur.execute(sql, params)
        self._log.append((sql, params, time.time() - t))
        return out

    def __getattr__(self, name):
        return getattr(self._cur, name)

    def __iter__(self):
        return iter(self._cur)


def _run(cur, school, sport, state):
    import school as S
    from school_identity import stateChips
    chips, primary = stateChips(cur, school, include=state)
    state = state or (primary if chips else None)
    year = S.currentSeason(cur, school, sport, state, primary)
    S.schoolHeader(cur, school, state, primary)
    S.schoolYears(cur, school, state, primary)
    if year:
        S.schoolRoster(cur, school, year, sport, state=state, primary=primary)
    S.schoolMeets(cur, school, sport, state=state, primary=primary)
    S.schoolBest(cur, school, sport, limit=100, state=state, primary=primary)
    S.schoolTopAthletes(cur, school, sport, limit=100, state=state, primary=primary)
    return state, primary, year


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--school", required=True)
    ap.add_argument("--sport", default="XC", choices=("XC", "TF"))
    ap.add_argument("--state", default=None, help="the ?state= chip (default: the page's own)")
    ap.add_argument("--lines", type=int, default=30, help="plan lines shown per statement")
    a = ap.parse_args(argv)
    log = []
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as raw:
            rec = _Recorder(raw, log)
            state, primary, year = _run(rec, a.school, a.sport, a.state)
            print(f"{a.school} ({a.sport}): state chip {state or '-'}, primary {primary or '-'}, "
                  f"current season {year or '-'}; {len(log)} statements, "
                  f"{sum(t for _s, _p, t in log):.2f} s as run")
            stmts = [(s, p, t) for s, p, t in log
                     if re.match(r"\s*(SELECT|WITH)\b", re.sub(r"--[^\n]*", "", s), re.I)]
            for sql, params, took in sorted(stmts, key=lambda x: -x[2]):
                head = " ".join(re.sub(r"--[^\n]*", "", sql).split())[:110]
                print("\n" + "=" * 78)
                print(f"{took * 1000:8.1f} ms as run   {head}")
                print("=" * 78)
                try:
                    raw.execute("SAVEPOINT ex")
                    raw.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
                    plan = [r["QUERY PLAN"] for r in raw.fetchall()]
                    raw.execute("RELEASE SAVEPOINT ex")
                except Exception as exc:                    # noqa: BLE001
                    raw.execute("ROLLBACK TO SAVEPOINT ex")
                    print(f"  EXPLAIN failed: {type(exc).__name__}: {exc}")
                    continue
                for line in plan[:a.lines]:
                    flag = "  <<" if ("Seq Scan" in line and any(
                        f" {t} " in line + " " for t in ("ranking_results", "athlete_season",
                                                         "results", "results_tf", "athletes"))) else ""
                    print(line + flag)
                if len(plan) > a.lines:
                    print(f"  ... {len(plan) - a.lines} more lines")
                    print("\n".join(l for l in plan[-3:] if "Time" in l))
        conn.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
