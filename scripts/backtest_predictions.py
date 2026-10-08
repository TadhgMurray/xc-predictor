#!/usr/bin/env python3
"""
backtest_predictions.py -- how good were the predictions, on races that have
been run. READ ONLY.

    /srv/venv/bin/python scripts/backtest_predictions.py --from 2026-09-01 --to 2026-10-05
    /srv/venv/bin/python scripts/backtest_predictions.py --from 2026-09-01 --to 2026-10-05 --weeks 0,3 --divs 60

★ WHY (owner, 2026-10-08: "the last predictions round was really bad").
  Nothing scored the served predictions against what then happened, so
  "bad" had no number and no fix could be shown to help. This re-runs the
  predictor on finished XC races exactly as the page would have, with every
  runner's history cut off before the race:
    --weeks 0  history up to the day before (a "this weekend" prediction)
    --weeks 3  history up to three weeks before (a forecast; hidden_days
               and is_forecast are set from that cut, as on the site)
  and prints, per basis and lead time:
    median |error| %     the typical miss on a runner's time
    bias %               + = predicted too slow, - = too fast
    order                mean Spearman correlation, predicted vs actual
                         finishing order within each race (1 = perfect)
  Two bases, on the same runners (those both could predict):
    rating  the athlete's recent rated form as a time at that race -- what
            the page serves by default (XCP_PREDICT_BASIS=rating)
    model   the transformer's own time

! THE MODEL'S OWN TRAINING SET. A race before model.pt was written may have
  been a training target (validation is athlete-disjoint, not by date), so
  the model's numbers on those races are flattered. The header prints the
  model's date; --from at or after it is a fair test. The ratings carry a
  milder version (the solve that rated the earlier races also saw this
  one).
"""
import argparse
import datetime
import math
import os
import random
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)


def _spearman(a, b):
    n = len(a)
    if n < 3:
        return None

    def ranks(x):
        order = sorted(range(n), key=lambda i: x[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and x[order[j + 1]] == x[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="lo", required=True)
    ap.add_argument("--to", dest="hi", required=True)
    ap.add_argument("--weeks", default="0,3",
                    help="lead times in weeks, comma separated")
    ap.add_argument("--divs", type=int, default=40,
                    help="how many races to sample (each costs a few seconds)")
    ap.add_argument("--min-field", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    leads = [int(w) for w in a.weeks.split(",") if w.strip()]

    import psycopg2.extras
    import predict as P
    from database import getConn

    if P.modelStatus().get("available"):
        made = datetime.date.fromtimestamp(os.path.getmtime(P.MODEL_PATH))
        print(f"model.pt written {made}: races before it may be training targets")
    else:
        print(f"model unavailable: {P.modelStatus().get('reason')}")

    with getConn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout = '600s'")
        cur.execute("""
            SELECT meet_id, div_id, source, left(date, 10) AS day, count(*) AS n
            FROM   results
            WHERE  date >= %s AND date <= %s
              AND  person_id IS NOT NULL
              AND  time_seconds > 0 AND time_seconds < 19999
            GROUP  BY 1, 2, 3, 4 HAVING count(*) >= %s""",
                    (a.lo, a.hi + "~", a.min_field))
        races = cur.fetchall()
        conn.rollback()
        random.Random(a.seed).shuffle(races)
        races = races[:a.divs]
        print(f"{len(races)} races sampled ({a.lo} to {a.hi}, fields >= {a.min_field})\n")

        acc = {}          # (basis, lead) -> {"err": [], "order": [], "races": 0}
        for i, race in enumerate(races, 1):
            day = datetime.date.fromisoformat(race["day"])
            cur.execute("""SELECT DISTINCT ON (person_id) person_id, time_seconds
                           FROM results WHERE meet_id = %s AND div_id = %s
                             AND source = %s AND person_id IS NOT NULL
                             AND time_seconds > 0 AND time_seconds < 19999
                           ORDER BY person_id, time_seconds""",
                        (race["meet_id"], race["div_id"], race["source"]))
            actual = {r["person_id"]: float(r["time_seconds"]) for r in cur.fetchall()}
            ids = sorted(actual)
            for lead in leads:
                target = {"mode": "rerun_exact", "sport": "XC",
                          "meet_id": race["meet_id"], "div_id": race["div_id"],
                          "source": race["source"]}
                cut = day - datetime.timedelta(weeks=lead)
                if lead:
                    target["history_before"] = cut.isoformat()
                try:
                    spec = P._targetSpec(cur, target)
                    rated = P._ratingTimes(cur, ids, spec, cut)
                    model = dict(zip(ids, P._predictTimes(cur, ids, target, spec=spec)))
                except Exception as exc:                 # noqa: BLE001
                    conn.rollback()
                    print(f"  race {race['meet_id']}/{race['div_id']} lead {lead}w: {exc}")
                    continue
                conn.rollback()
                both = [p for p in ids
                        if (rated.get(p) or {}).get("seconds")
                        and (model.get(p) or {}).get("seconds")]
                if len(both) < 3:
                    continue
                act = [actual[p] for p in both]
                for basis, got in (("rating", rated), ("model", model)):
                    pred = [float(got[p]["seconds"]) for p in both]
                    s = acc.setdefault((basis, lead), {"err": [], "order": [], "races": 0})
                    s["err"] += [100.0 * math.log(x / y) for x, y in zip(pred, act)]
                    rho = _spearman(pred, act)
                    if rho is not None:
                        s["order"].append(rho)
                    s["races"] += 1
            print(f"  [{i}/{len(races)}] {race['day']} meet {race['meet_id']} "
                  f"div {race['div_id']} ({race['source']}) {len(ids)} runners", flush=True)

    print(f"\n{'basis':7} {'lead':>5} {'races':>6} {'runners':>8} "
          f"{'median |err|':>13} {'bias':>7} {'order':>6}")
    for (basis, lead), s in sorted(acc.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if not s["err"]:
            continue
        med = statistics.median(abs(e) for e in s["err"])
        bias = statistics.median(s["err"])
        order = statistics.fmean(s["order"]) if s["order"] else float("nan")
        print(f"{basis:7} {lead:>4}w {s['races']:>6} {len(s['err']):>8} "
              f"{med:>12.2f}% {bias:>+6.2f}% {order:>6.3f}")


if __name__ == "__main__":
    main()
