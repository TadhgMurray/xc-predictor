"""diag_predict_time.py -- where does a prediction's minute actually go?

    python scripts/diag_predict_time.py --meet 271911 --sport XC \
                                        --date 2026-10-03

★ BECAUSE GUESSING HAS COST TWO DAYS. The predictions page has now been slow
  for three different reasons in a row, and each time the shape of the fault
  (a timeout, an HTML error page) said nothing about WHICH stage was slow. I
  guessed the corrections import (3.3 s, not 60) and then the feature builder
  (0.24 ms an athlete). Neither guess was unreasonable and both were wrong.

  This times every stage of the real call against the real database, so the
  next answer comes from a measurement rather than from a plausible story.

! IT RUNS predictTeam's OWN STEPS, in order, not an approximation of them --
  the same _teamRosters, _fullField, _historyRows, _targetSpec,
  _weatherVariants and forward pass the route reaches. A diagnostic that
  measures something adjacent to the code is how the conversions diagnostic
  was wrong twice.
"""
import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import psycopg2.extras                                        # noqa: E402
from database import getConn                                  # noqa: E402


class Stage:
    """A timed block that prints as it finishes, so a run that never gets
    past stage two still tells you stage two was the problem."""
    rows = []

    def __init__(self, label):
        self.label = label

    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *exc):
        dt = time.perf_counter() - self.t
        Stage.rows.append((self.label, dt))
        print(f"  {self.label:<34} {dt * 1000:9.1f} ms", flush=True)
        return False


def explain(a):
    """The indexes that exist, and the plan the history lookup actually gets.

    ⚠ THE TWO CAUSES LOOK IDENTICAL FROM OUTSIDE. A 52-second _historyRows
      is a sequential scan either way -- because the index was never built,
      or because it exists and the planner cannot reach it through the
      predicate. Only the plan distinguishes them, and the fix is different
      for each.
    """
    import predict as P
    fx = P._fx()
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            print("\n  indexes on the two result tables")
            cur.execute("""
                SELECT tablename, indexname, indexdef
                FROM   pg_indexes
                WHERE  schemaname = 'public'
                  AND  tablename IN ('results', 'results_tf')
                ORDER  BY tablename, indexname
            """)
            for r in cur.fetchall():
                cols = r["indexdef"].split("USING", 1)[-1]
                print(f"    {r['tablename']:<12} {r['indexname']:<34}{cols}")

            cur.execute("SELECT to_regclass('public.weather')")
            row = cur.fetchone()
            has_weather = (row[0] if not isinstance(row, dict)
                           else row.get("to_regclass")) is not None

            # a realistic field: the ids the timing run just used
            cur.execute("""SELECT person_id FROM athlete_season
                           WHERE sport = %s AND mean_rating IS NOT NULL
                           LIMIT 433""", (a.sport,))
            ids = sorted({r["person_id"] for r in cur.fetchall()})
            print(f"\n  EXPLAIN over {len(ids)} athletes, sport {a.sport}")

            sql = fx.personResultsSql(a.sport, has_weather=has_weather)
            hour = fx.XC_DEFAULT_HOUR if a.sport == "XC" else fx.TF_DEFAULT_HOUR

            # ★ BOTH WAYS, SO THE COLLAPSE LIMIT IS VISIBLE RATHER THAN
            #   ARGUED ABOUT. The query joins 14 relations and the default
            #   limit is 8: past it the planner joins in WRITTEN order and
            #   the 16,221-row override VALUES list becomes an inner loop.
            for limit in (None, 16):
                if limit:
                    cur.execute(f"SET LOCAL join_collapse_limit = {limit}")
                    cur.execute(f"SET LOCAL from_collapse_limit = {limit}")
                    print(f"\n  --- with join_collapse_limit = {limit} ---")
                else:
                    print("\n  --- as it plans today (default limit 8) ---")
                cur.execute("EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, COSTS OFF) "
                            + sql, (hour, fx.MIN_NORMALIZED_TIME, ids, ids))
                rows = [list(r.values())[0] for r in cur.fetchall()]
                # the shape and the number, not the id arrays
                for line in rows:
                    t = line.strip()
                    if (t.startswith(("Nested Loop", "Hash Join", "Hash Left",
                                      "Hash Right", "Merge", "Values Scan",
                                      "Execution Time", "Planning Time"))
                            or "Rows Removed by Join Filter" in t):
                        print("    " + line[:100])
            cur.connection.rollback()   # drop the SET LOCAL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meet", required=True)
    ap.add_argument("--sport", default="XC")
    ap.add_argument("--div", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--explain", action="store_true",
                    help="show the indexes on results/results_tf and EXPLAIN "
                         "the history lookup, instead of timing stages. The "
                         "one question a stage timing cannot answer: is the "
                         "scan there because no index exists, or because the "
                         "planner would not use one?")
    ap.add_argument("--weather", default="both",
                    help="both (the page's default), normal, or none -- "
                         "'both' costs a second forward pass and a forecast "
                         "fetch, so it is worth timing separately")
    a = ap.parse_args()

    import predict as P

    target = {"mode": "rerun", "meet_id": int(a.meet), "sport": a.sport,
              "weather": a.weather}
    if a.div:
        target["div_id"] = a.div
    if a.date:
        target["date"] = a.date

    if a.explain:
        explain(a)
        return

    print(f"\n  meet {a.meet} {a.sport} date={a.date} weather={a.weather}\n")
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            with Stage("modelStatus (loads model.pt)"):
                st = P.modelStatus()
            if not st["available"]:
                print(f"\n  model unavailable: {st['reason']}")
                return

            # ★ _teamRosters, BROKEN OPEN. It is a stage made of five
            #   queries and "6.4 s" names none of them. Each is run here in
            #   the order the real call runs them, on the same cursor, so
            #   the numbers add up to what the stage costs.
            import roster as R
            meet_id = int(a.meet)
            div = int(a.div) if a.div and str(a.div).isdigit() else None
            with Stage("  _exactField (who ran it)"):
                originals = P._exactField(cur, meet_id, div, a.sport)
            at_meet = sorted({r["school"] for r in originals if r.get("school")})
            print(f"     -> {len(originals)} originals, {len(at_meet)} schools")

            with Stage("  _fieldGender"):
                gender = P._fieldGender(
                    cur, [r["person_id"] for r in originals], a.sport)

            with Stage("  _currentSeason"):
                season = P._currentSeason(cur, a.sport)
            print(f"     -> season {season}")

            # ! THE CARRY-FORWARD WINDOW asks how many races each school has
            #   run -- 396 schools, one GROUP BY over ranking_results.
            with Stage("  roster.racesRun (the window)"):
                run = R.racesRun(cur, at_meet, a.sport, season)
            print(f"     -> {len(run)} schools have raced this season")

            with Stage("  _squadsForYear (this season)"):
                P._squadsForYear(cur, at_meet, a.sport, season, gender=gender)
            with Stage("  _squadsForYear (last season, carried)"):
                P._squadsForYear(cur, at_meet, a.sport, season - 1,
                                 exclude_terminal=True, active_year=season,
                                 gender=gender)

            with Stage("_teamRosters (all of it, again)"):
                roster = P._teamRosters(cur, [], target, set(), set())
            print(f"     -> {len(roster)} on the named teams")

            with Stage("_fullField"):
                field = P._fullField(cur, roster, target)
            ids = [r["person_id"] for r in field]
            print(f"     -> {len(ids)} in the whole field")

            # the inside of _predictTimes, stage by stage
            with Stage("_historyRows  (the id lookup)"):
                hist = P._historyRows(cur, ids)
            n_rows = sum(len(v) for v in hist.values())
            print(f"     -> {len(hist)} athletes, {n_rows:,} corpus rows")

            with Stage("_targetSpec"):
                spec = P._targetSpec(cur, target)
            with Stage("_weatherVariants"):
                variants = P._weatherVariants(cur, spec, a.weather)
            print(f"     -> {list(variants)}")

            with Stage("_predictTimes  (everything, again)"):
                preds = P._predictTimes(cur, ids, target)
            got = sum(1 for p in preds if p and p.get("seconds") is not None)
            print(f"     -> {got}/{len(preds)} predicted")

            with Stage("_score"):
                P._score(field, preds)

    print()
    total = sum(dt for lab, dt in Stage.rows
                if not lab.startswith("_predictTimes"))
    print(f"  the stages above sum to {total:.1f} s; gunicorn kills at 60")
    worst = max(Stage.rows, key=lambda r: r[1])
    print(f"  slowest single stage: {worst[0]} at {worst[1]:.1f} s")


if __name__ == "__main__":
    main()
