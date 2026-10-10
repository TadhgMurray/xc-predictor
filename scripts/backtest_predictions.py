#!/usr/bin/env python3
"""
backtest_predictions.py -- how good were the predictions, on races that have
been run. READ ONLY.

    /srv/venv/bin/python scripts/backtest_predictions.py --from 2026-09-01 --to 2026-10-05
    /srv/venv/bin/python scripts/backtest_predictions.py --from 2026-09-01 --to 2026-10-05 --weeks 0,3 --divs 60

    # a candidate model, before it replaces the live one (train.py --out-dir
    # writes a complete artifact folder; predict reads RACECAST_MODEL's):
    RACECAST_MODEL=/srv/models/new/model.pt /srv/venv/bin/python \
        scripts/backtest_predictions.py --from 2026-09-17 --to 2026-10-05

    # track: one distance EVENT of one division is a race (2026-10-10)
    /srv/venv/bin/python scripts/backtest_predictions.py --sport TF \
        --from 2026-03-01 --to 2026-06-15 --min-field 8

★ WHY (owner, 2026-10-08: "the last predictions round was really bad").
  Nothing scored the served predictions against what then happened, so
  "bad" had no number and no fix could be shown to help. This re-runs the
  predictor on finished XC races exactly as the page would have, with every
  runner's history cut off before the race:
    --weeks 0  history up to the day before (a "this weekend" prediction)
    --weeks 3  history up to three weeks before (a forecast; hidden_days
               and is_forecast are set from that cut, as on the site)
  and prints, per basis and lead time (within-race: the median miss once
  each race's own shift -- weather, footing, a long course -- is taken out,
  i.e. the part a better model could still win):
    median |error| %     the typical miss on a runner's time
    bias %               + = predicted too slow, - = too fast
    order                mean Spearman correlation, predicted vs actual
                         finishing order within each race (1 = perfect)
  Then --breakdown (default on) splits each basis's error by level and
  gender (the rating's pool), days since the runner's last race before
  the cut (quartiles of this run), the sport of that last race (XC/TF) and
  the number of prior races (quartiles) -- where a systematic bias lives.
  Two bases, on the same runners (those both could predict):
    rating  the athlete's recent rated form as a time at that race -- what
            the page serves by default (XCP_PREDICT_BASIS=rating)
    model   the transformer's own time
    base    the model's own anchor (its "you'll run what you ran" baseline)
            through the SAME conversion to a race time -- if base carries
            the model's bias too, the conversion is wrong, not the network
  The model's breakdown also splits by its conversion (time_basis).

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
    ap.add_argument("--no-breakdown", dest="breakdown", action="store_false")
    # ★ TRACK TOO (2026-10-10: "predictions for tf races are majorly messed
    #   up" had no number either). A track race is (meet, division, EVENT),
    #   the page's own unit, and only a distance event (event_parse prices
    #   it) -- the 100 and the shot put are not races the page predicts.
    ap.add_argument("--sport", choices=("XC", "TF"), default="XC")
    ap.add_argument("--val-only", action="store_true",
                    help="only runners on the model's VALIDATION side (the "
                         "10%% of athletes train.py never trained on): an "
                         "unflattered score on races the model saw")
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
        tf = a.sport == "TF"
        table = "results_tf" if tf else "results"
        # a track race is one event: grouped by it, running events only
        ev_col = ", event_id, min(event_short) AS event_short" if tf else ""
        ev_grp = ", event_id" if tf else ""
        running = ("AND COALESCE(is_field, 0) = 0 AND COALESCE(is_relay, 0) = 0"
                   if tf else "")
        # ! XC GROUPS BY THE DAY AS IT ALWAYS DID; a track event's prelim and
        #   final days are one event id, dated by its first day
        day = "min(left(date, 10))" if tf else "left(date, 10)"
        day_grp = "" if tf else ", left(date, 10)"
        cur.execute(f"""
            SELECT meet_id, div_id, source, {day} AS day,
                   count(*) AS n {ev_col}
            FROM   {table} r
            WHERE  date >= %s AND date <= %s {running}
              AND  person_id IS NOT NULL
              AND  time_seconds > 0 AND time_seconds < 19999
              -- ★ ONE RACE, ONE SAMPLE (sweep 2026-10-10). A race stored in
              --   both feeds (anet and tfrrs) is two (meet, div, source)
              --   groups, so it could be drawn twice and weigh double in
              --   every average. result_twin is the verdict the corpus, the
              --   engine and the boards all anti-join; the flagged copy's
              --   rows vanish here and its group falls under --min-field.
              AND  NOT EXISTS (SELECT 1 FROM result_twin x
                               WHERE x.sport = %s
                                 AND x.result_id = r.result_id)
            GROUP  BY meet_id, div_id, source {ev_grp}{day_grp}
            HAVING count(*) >= %s""",
                    (a.lo, a.hi + "~", a.sport, a.min_field))
        races = cur.fetchall()
        if tf:
            # ! A DISTANCE RACE BY ITS NAME, the page's own test
            #   (predict.trackRaces / event_parse)
            from event_parse import distanceFromEventShort
            races = [r for r in races
                     if distanceFromEventShort(r.get("event_short") or "")[0]]
        conn.rollback()
        random.Random(a.seed).shuffle(races)
        races = races[:a.divs]
        print(f"{len(races)} races sampled ({a.lo} to {a.hi}, fields >= {a.min_field})\n")

        acc = {}          # (basis, lead) -> {"err": [], "order": [], "races": 0}
        for i, race in enumerate(races, 1):
            day = datetime.date.fromisoformat(race["day"])
            # the event's own times, at a track meet (a miler's 800 that
            # day is another race)
            ev_where = "AND event_id = %s" if tf else ""
            ev_arg = (race["event_id"],) if tf else ()
            cur.execute(f"""SELECT DISTINCT ON (person_id) person_id, time_seconds
                           FROM {table} r WHERE meet_id = %s AND div_id = %s
                             AND source = %s AND person_id IS NOT NULL {ev_where}
                             AND time_seconds > 0 AND time_seconds < 19999
                             AND NOT EXISTS (SELECT 1 FROM result_twin x
                                             WHERE x.sport = %s
                                               AND x.result_id = r.result_id)
                           ORDER BY person_id, time_seconds""",
                        (race["meet_id"], race["div_id"], race["source"])
                        + ev_arg + (a.sport,))
            actual = {r["person_id"]: float(r["time_seconds"]) for r in cur.fetchall()}
            ids = sorted(actual)
            for lead in leads:
                target = {"mode": "rerun_exact", "sport": a.sport,
                          "meet_id": race["meet_id"], "div_id": race["div_id"],
                          "source": race["source"]}
                if tf:
                    target["event_id"] = race["event_id"]
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
                        and (model.get(p) or {}).get("seconds")
                        and (not a.val_only or _isValAthlete(p))]
                if len(both) < 3:
                    continue
                act = [actual[p] for p in both]
                facts = _facts(cur, both, cut, rated)
                for p in both:
                    facts[p]["difficulty"] = spec.get("course_difficulty")
                conn.rollback()
                base = {p: {"seconds": model[p].get("baseline_race"),
                            "time_basis": model[p].get("time_basis")}
                        for p in both if model[p].get("baseline_race")}
                for basis, got in (("rating", rated), ("model", model), ("base", base)):
                    if basis == "base" and len(base) < len(both):
                        continue
                    pred = [float(got[p]["seconds"]) for p in both]
                    s = acc.setdefault((basis, lead), {"err": [], "order": [], "races": 0,
                                                       "rows": [], "within": []})
                    errs = [100.0 * math.log(x / y) for x, y in zip(pred, act)]
                    s["err"] += errs
                    # ★ THE RACE'S OWN SHIFT TAKEN OUT (owner, 2026-10-10):
                    #   heat, mud, a long course move the whole field
                    #   together and nothing before the gun knows it. What is
                    #   left after subtracting the race's median miss is the
                    #   part a better model could still win.
                    shift = statistics.median(errs)
                    s["within"] += [e - shift for e in errs]
                    s["rows"] += [(e, dict(facts[p], clock=(model[p].get("time_basis") or "?")))
                                  for e, p in zip(errs, both)]
                    rho = _spearman(pred, act)
                    if rho is not None:
                        s["order"].append(rho)
                    s["races"] += 1
            ev_txt = f" {race['event_short']}" if tf else ""
            print(f"  [{i}/{len(races)}] {race['day']} meet {race['meet_id']} "
                  f"div {race['div_id']}{ev_txt} ({race['source']}) "
                  f"{len(ids)} runners", flush=True)

    print(f"\n{'basis':7} {'lead':>5} {'races':>6} {'runners':>8} "
          f"{'median |err|':>13} {'bias':>7} {'order':>6} {'within-race':>12}")
    for (basis, lead), s in sorted(acc.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if not s["err"]:
            continue
        med = statistics.median(abs(e) for e in s["err"])
        bias = statistics.median(s["err"])
        order = statistics.fmean(s["order"]) if s["order"] else float("nan")
        within = statistics.median(abs(e) for e in s["within"]) if s["within"] else float("nan")
        print(f"{basis:7} {lead:>4}w {s['races']:>6} {len(s['err']):>8} "
              f"{med:>12.2f}% {bias:>+6.2f}% {order:>6.3f} {within:>11.2f}%")
    if a.breakdown:
        _breakdown(acc)


def _facts(cur, ids, cut, rated):
    """{pid: {level, gap, sport, n}} as of the cut: the rating's pool,
    days since the last race of either sport, that race's sport, and how
    many races came before."""
    cur.execute("""
        SELECT person_id, 'XC' AS sport, max(left(date, 10)) AS last, count(*) AS n
        FROM results WHERE person_id = ANY(%(ids)s) AND date < %(cut)s
          AND time_seconds > 0 GROUP BY 1
        UNION ALL
        SELECT person_id, 'TF', max(left(date, 10)), count(*)
        FROM results_tf WHERE person_id = ANY(%(ids)s) AND date < %(cut)s
          AND COALESCE(is_field, 0) = 0 AND COALESCE(is_relay, 0) = 0
          AND time_seconds > 0 GROUP BY 1""", {"ids": ids, "cut": cut.isoformat()})
    by = {}
    for r in cur.fetchall():
        by.setdefault(r["person_id"], []).append(r)
    out = {}
    for p in ids:
        rows = by.get(p) or []
        last = max(rows, key=lambda r: r["last"]) if rows else None
        gap = ((cut - datetime.date.fromisoformat(last["last"])).days
               if last and last["last"] else None)
        out[p] = {"level": ((rated.get(p) or {}).get("form_pool") or "?").split("|")[0],
                  "gap": gap, "sport": last["sport"] if last else "?",
                  "n": sum(r["n"] for r in rows)}
    return out


def _isValAthlete(person_id):
    """feature_extraction.isValPerson -- IMPORTED, not copied (sweep
    2026-10-10). The copy hashed repr((False, pid)) while extraction hashes
    ("p", pid): a different 10%, so --val-only was scoring athletes the model
    trained on. predict already imports the extraction to predict at all.
    ! A person merged or re-keyed since the extraction may hash to the other
      side; the share that moves is small and only blurs, never flatters."""
    from feature_extraction import isValPerson
    return isValPerson(person_id)


# ★ BREAKS IN WEEKS A COACH WOULD NAME (owner, 2026-10-10: "it overestimates
#   people who take long breaks"). Quartiles of this run put every break
#   over ~4 months in one band; these split the season's rhythm from an
#   off-season and from a year or more away.
_BREAKS = ((14, "<=2wk"), (42, "2-6wk"), (120, "6wk-4mo"), (365, "4-12mo"),
           (None, ">1yr"))


def _breakBand(gap):
    if gap is None:
        return "?"
    for hi, label in _BREAKS:
        if hi is None or gap <= hi:
            return label
    return "?"


def _quartileBand(values):
    """value -> 'lo-hi' by this run's own quartiles."""
    vs = sorted(v for v in values if v is not None)
    if len(vs) < 4:
        return lambda v: "all"
    q = [vs[0]] + [vs[int(len(vs) * k / 4)] for k in (1, 2, 3)] + [vs[-1]]

    def band(v):
        if v is None:
            return "?"
        for i in range(4):
            if v <= q[i + 1]:
                return f"{q[i]}-{q[i + 1]}"
        return f"{q[3]}-{q[4]}"
    return band


def _breakdown(acc):
    for (basis, lead), s in sorted(acc.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        rows = s.get("rows") or []
        if not rows:
            continue
        gap_band = _quartileBand([f["gap"] for _, f in rows])
        n_band = _quartileBand([f["n"] for _, f in rows])
        # course difficulty by quartile of the RACES' values (each race's
        # runners share one), so a band is a set of courses, not of runners
        d_band = _quartileBand([round(f["difficulty"], 3) for _, f in rows
                                if f.get("difficulty") is not None])
        print(f"\n{basis} {lead}w by group (median bias, median |err|, runners)")
        for title, key in (("level", lambda f: f["level"]),
                           ("days since last race", lambda f: gap_band(f["gap"])),
                           ("break before race", lambda f: _breakBand(f["gap"])),
                           ("course difficulty", lambda f: d_band(
                               round(f["difficulty"], 3)
                               if f.get("difficulty") is not None else None)),
                           ("last race sport", lambda f: f["sport"]),
                           ("prior races", lambda f: n_band(f["n"])),
                           ("model's conversion", lambda f: f["clock"])):
            groups = {}
            for e, f in rows:
                groups.setdefault(key(f), []).append(e)
            cells = []
            for g, es in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                if len(es) < 20:
                    continue
                cells.append(f"{g}: {statistics.median(es):+.1f}% "
                             f"{statistics.median(abs(x) for x in es):.1f}% n{len(es)}")
            print(f"  {title:22} " + " | ".join(cells))


if __name__ == "__main__":
    main()
