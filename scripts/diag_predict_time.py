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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meet", required=True)
    ap.add_argument("--sport", default="XC")
    ap.add_argument("--div", default=None)
    ap.add_argument("--date", default=None)
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

    print(f"\n  meet {a.meet} {a.sport} date={a.date} weather={a.weather}\n")
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            with Stage("modelStatus (loads model.pt)"):
                st = P.modelStatus()
            if not st["available"]:
                print(f"\n  model unavailable: {st['reason']}")
                return

            with Stage("_teamRosters"):
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
