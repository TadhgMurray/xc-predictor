#!/usr/bin/env python3
"""
explain_board.py -- why a rankings board is slow: every query the API runs
for one request, timed, with its plan.

    /srv/venv/bin/python scripts/explain_board.py "board=ability&pool=hs_m&sport=both&scope=usa&scale=hs&limit=50&offset=0&sort=rating"
    /srv/venv/bin/python scripts/explain_board.py "<query string>" --plans

★ WHY (owner, 2026-09-29: the boards are "very slow to load"). Measured on
  the live site the same day: the default board -- ability, hs_m, sport=both
  -- took 3.5-5.1 s, the same board for one sport 0.5-0.9 s, the performance
  and course boards 0.3 s. The index the board orders on exists
  (athlete_season (pool, mean_rating DESC, person_id)), so the time is
  somewhere this repo cannot see from outside: a plan that does not use it,
  a filter that defeats it, or a step around the query. This runs the API's
  own code path (parseFilters, applyUnitFilters, the board function,
  stampBoardRows) against the live database with every statement timed, and
  with --plans prints EXPLAIN (ANALYZE, BUFFERS) for each.

Read-only: EXPLAIN ANALYZE executes the statement, and every statement here
is a SELECT; the transaction is rolled back at the end.
"""
import argparse
import os
import sys
import time
from urllib.parse import parse_qs

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(_ROOT, _p))


class _Args(dict):
    """request.args enough for parseFilters: .get and .getlist."""
    def getlist(self, k):
        v = super().get(k)
        return [] if v is None else (v if isinstance(v, list) else [v])

    def get(self, k, default=None, type=None):                  # noqa: A002
        v = super().get(k)
        if isinstance(v, list):
            v = v[0] if v else None
        if v is None:
            return default
        return type(v) if type else v


class _Timed:
    """A cursor that times every statement and can EXPLAIN it first."""
    def __init__(self, cur, plans):
        self._cur, self._plans, self.log = cur, plans, []

    def execute(self, sql, params=None):
        if self._plans and sql.lstrip().upper().startswith(("SELECT", "WITH")):
            self._cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
            plan = "\n".join(r[0] if not isinstance(r, dict) else list(r.values())[0]
                             for r in self._cur.fetchall())
        else:
            plan = None
        t0 = time.time()
        self._cur.execute(sql, params)
        self.log.append((time.time() - t0, " ".join(sql.split())[:160], plan))

    def __getattr__(self, name):
        return getattr(self._cur, name)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="the /api/rankings query string")
    ap.add_argument("--plans", action="store_true", help="print EXPLAIN ANALYZE")
    a = ap.parse_args()
    import psycopg2.extras
    from database import getConn
    from rankings import (parseFilters, getAbilityRankings, getPerformanceRankings,
                          getPrRankings)
    from school_units import applyUnitFilters
    args = _Args({k: (v if len(v) > 1 else v[0])
                  for k, v in parse_qs(a.query.lstrip("?")).items()})
    f, err = parseFilters(args)
    if err:
        raise SystemExit(f"parseFilters: {err}")
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as raw:
            cur = _Timed(raw, a.plans)
            t0 = time.time()
            uerr = applyUnitFilters(cur, f, args)
            if uerr:
                raise SystemExit(f"applyUnitFilters: {uerr}")
            t1 = time.time()
            rows = {"performance": getPerformanceRankings, "pr": getPrRankings,
                    "ability": getAbilityRankings}[f["board"]](cur, f)
            t2 = time.time()
            try:
                from app import stampBoardRows
                stampBoardRows(rows, rating_keys=("rating", "best_rating"))
            except Exception as exc:                          # noqa: BLE001
                print(f"(stampBoardRows skipped: {type(exc).__name__}: {exc})")
            t3 = time.time()
            conn.rollback()
    print(f"\nunit filters {t1 - t0:6.2f}s   board query {t2 - t1:6.2f}s   "
          f"HS stamping {t3 - t2:6.2f}s   rows {len(rows)}")
    for secs, sql, plan in cur.log:
        print(f"\n  {secs:6.2f}s  {sql}")
        if plan:
            print("    " + plan.replace("\n", "\n    "))


if __name__ == "__main__":
    main()
