#!/usr/bin/env python3
"""Why is the prediction off? Five measurements, on one real race.

    python scripts/diag_model_quality.py --meet 271911 --div 1 --sport XC

★ BECAUSE "IT'S JUST REALLY OFF" IS NOT A BUG REPORT YET, and this session
  has already been wrong four times by reasoning and right every time it
  measured. Each section below answers ONE of the owner's complaints with a
  number taken from the real model on a real field.

  Owner, 2026-09-17:
    "it almost doesn't care abotu tf"                        -> section C
    "it can't tell the difference between ppl taking actual
     breaks vs just a break in racing"                       -> section D
    "I don't think the weather is going right"               -> section E
    "it has me losing to my teamate who I beat in every
     single race that season"                                -> sections A, B

! IT BACKTESTS. The target is a race that ALREADY HAPPENED, and predict.py
  now cuts every athlete's history strictly before that date -- so this is
  the model with no knowledge of the day, scored against what the day did.

⚠ TWO FINDINGS ARE ALREADY IN THE CODE AND NEED NO RUN. They are printed at
  the end so they are not lost in the numbers:

    1. is_forecast IS ALWAYS 1 AT INFERENCE. predict._forecastExample passes
       is_forecast=True unconditionally. In TRAINING that flag marks an
       example whose recent races were deliberately HIDDEN -- the feature's
       own comment says so: "a real six-week gap and a truncated six-week gap
       produce the same number of days and mean opposite things". So the
       model learned to DISCOUNT a long gap when the flag is on, and at
       inference the flag is always on. Every real break is read as probably
       artificial. That is the owner's complaint, mechanised.

    2. THE CONTEXT VECTOR HAS NO FIELD. All 24 context features and all 21
       sequence features are properties of ONE athlete. The model never sees
       who else is racing, so "everybody here has the same gap, therefore
       this gap is normal" is not an inference it can make -- there is no
       channel for it. Same limit the venue embedding's own note describes:
       cross-athlete linkage "this model cannot reconstruct from its own
       loss".
"""
import argparse
import math
import os
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("racecast", "scripts", "engine", "model"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import psycopg2.extras                                        # noqa: E402
from database import getConn                                  # noqa: E402


def mmss(s):
    if s is None:
        return "--"
    s = float(s)
    return f"{int(s // 60)}:{s % 60:04.1f}"


def spearman(a, b):
    """Rank correlation, ties averaged."""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        out = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            r = 0.5 * (i + j) + 1.0
            for k in range(i, j + 1):
                out[order[k]] = r
            i = j + 1
        return out
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return num / (da * db) if da and db else 0.0


def inversions(pred, actual, groups=None):
    """(rate, n_pairs) -- pairs the prediction puts in the wrong order.
    `groups`: only count pairs sharing a group (i.e. teammates)."""
    bad = tot = 0
    for i in range(len(pred)):
        for j in range(i + 1, len(pred)):
            if groups is not None and (groups[i] != groups[j]
                                       or groups[i] is None):
                continue
            if actual[i] == actual[j]:
                continue
            tot += 1
            if (pred[i] < pred[j]) != (actual[i] < actual[j]):
                bad += 1
    return (bad / tot if tot else None), tot


# ------------------------------------------------------------------ #
#  THE FIELD, AS IT ACTUALLY RAN
# ------------------------------------------------------------------ #

def realField(cur, meet_id, div_id, sport):
    """Everyone who ran, with what they ran and who they ran for."""
    table = "results" if sport == "XC" else "results_tf"
    div = "AND r.div_id = %(div)s" if div_id else ""
    cur.execute(f"""
        SELECT DISTINCT ON (r.person_id)
               r.person_id, r.school, r.time_seconds, r.normalized_time,
               r.place, r.date,
               COALESCE(a.first_name,'') || ' '
                   || COALESCE(a.last_name,'') AS name
        FROM   {table} r
        LEFT   JOIN athletes a ON a.athlete_id = r.person_id
        WHERE  r.meet_id = %(meet)s {div}
          AND  r.person_id IS NOT NULL
          AND  r.time_seconds IS NOT NULL
        ORDER  BY r.person_id, r.time_seconds
    """, {"meet": meet_id, "div": div_id})
    return [dict(r) for r in cur.fetchall()]


# ------------------------------------------------------------------ #
#  A. CAN IT RANK THE FIELD AT ALL?
# ------------------------------------------------------------------ #

def sectionA(rows, preds, seasonRating):
    print("\n" + "=" * 70)
    print("A. ORDERING -- does it put the field in the right order?")
    print("=" * 70)
    got = [(r, p) for r, p in zip(rows, preds)
           if p.get("seconds") is not None and r.get("time_seconds")]
    if len(got) < 5:
        print("   too few predictable athletes to say anything")
        return
    actual = [float(r["time_seconds"]) for r, _p in got]
    model = [float(p["seconds"]) for _r, p in got]
    teams = [(r.get("school") or None) for r, _p in got]

    # ★ TWO DUMB BASELINES, because a number with nothing to beat is not a
    #   measurement. If the model cannot beat "sort by season rating", the
    #   architecture is not earning its 4M parameters.
    last = [float(p.get("baseline") or 0) for _r, p in got]
    rating = [-(seasonRating.get(r["person_id"]) or 0.0) for r, _p in got]

    print(f"   {len(got)} athletes with both a prediction and a result\n")
    print(f"   {'':<28}{'spearman':>10}{'inversions':>12}")
    for label, series in (("the model", model),
                          ("their last race alone", last),
                          ("season mean rating", rating)):
        if not any(series):
            continue
        rho = spearman(series, actual)
        inv, _n = inversions(series, actual)
        print(f"   {label:<28}{rho:>10.3f}{inv * 100:>11.1f}%")

    # ⚠ A WITHIN-TEAM RATE MEANS NOTHING ON ITS OWN, and reading it that way
    #   was a hole in the first version of this script. Teammates are CLOSER
    #   IN ABILITY than two random runners, so a higher inversion rate among
    #   them is expected of any predictor -- the pairs are simply harder. The
    #   question is whether the model is worse on them THAN THE BASELINES
    #   ARE, which is the only comparison that isolates the model.
    print(f"\n   {'WITHIN A TEAM':<28}{'inversions':>12}   "
          f"(teammates are closer, so every row here is higher)")
    for label, series in (("the model", model),
                          ("their last race alone", last),
                          ("season mean rating", rating)):
        if not any(series):
            continue
        inv, n = inversions(series, actual, groups=teams)
        if inv is not None:
            print(f"   {label:<28}{inv * 100:>11.1f}%   ({n:,} pairs)")
    print("   ★ This is the owner's complaint, counted -- same coach, same"
          "\n     schedule, same races. Compare the ROWS, not the number.")


# ------------------------------------------------------------------ #
#  B. IS IT JUST ECHOING THE LAST RACE?
# ------------------------------------------------------------------ #

def sectionB(rows, preds):
    print("\n" + "=" * 70)
    print("B. ANCHORING -- how much of the prediction is the last race?")
    print("=" * 70)
    print("   predictInterval returns baselineSeconds * exp(mu), and the")
    print("   baseline is ONE ROW: the athlete's most recent visible race.")
    print("   If exp(mu) sits near 1 for everybody, the network is a small")
    print("   correction on top of 'whatever you ran last', and whoever had")
    print("   the better last race wins every head-to-head by construction.\n")
    got = [(r, p) for r, p in zip(rows, preds)
           if p.get("seconds") is not None and p.get("baseline")]
    if not got:
        print("   no baselines available")
        return
    ratios = [float(p["normalized"] or p["seconds"]) / float(p["baseline"])
              for _r, p in got]
    ratios.sort()
    n = len(ratios)

    def pct(q):
        return ratios[min(n - 1, int(q * n))]
    print(f"   exp(mu) over {n} athletes -- 1.000 means 'exactly your last "
          f"race'")
    print(f"     p05 {pct(0.05):.3f}   p25 {pct(0.25):.3f}   "
          f"median {pct(0.50):.3f}   p75 {pct(0.75):.3f}   p95 {pct(0.95):.3f}")
    spread = pct(0.95) - pct(0.05)
    print(f"     90% of the field moves within {spread * 100:.1f}% of their "
          f"last race.")
    if spread < 0.06:
        print("   ⚠ THAT IS ABOUT ONE RACE'S NOISE (+-4 rating points is "
              "~3.3%).")
        print("     The model is re-reading the last race, not forming a view.")


# ------------------------------------------------------------------ #
#  C. DOES TRACK MATTER?
# ------------------------------------------------------------------ #

def sectionC(cur, target, ids, preds, predictTimes):
    print("\n" + "=" * 70)
    print("C. TRACK -- does a track season move an XC prediction?")
    print("=" * 70)
    print("   Ablation: re-predict with every TF row removed from every")
    print("   history, and measure how far the answers move.\n")
    import predict
    real = predict._historyRows

    def xcOnly(c, person_ids):
        out = {}
        for pid, rows in real(c, person_ids).items():
            kept = [r for r in rows if r.get("is_xc")]
            if kept:
                out[pid] = kept
        return out

    predict._historyRows = xcOnly
    try:
        ablated = predictTimes(cur, ids, target)
    finally:
        predict._historyRows = real

    # ! HOW MUCH TRACK WAS THERE TO REMOVE. "Removing it changed nothing" and
    #   "there was nothing to remove" print the same number, and they are
    #   different findings. This was missing from the first version.
    n_tf = [p.get("n_tf") or 0 for p in preds if p.get("n_tf") is not None]
    if n_tf:
        n_tf.sort()
        with_any = sum(1 for x in n_tf if x)
        print(f"   track rows in history: median {n_tf[len(n_tf) // 2]}, "
              f"max {n_tf[-1]}, {with_any}/{len(n_tf)} athletes have any")
        if not with_any:
            print("   ⚠ NOBODY HAD A TRACK RACE. This race cannot test the")
            print("     question; try a field with a track season behind it.")
            return

    moved = [abs(float(b["seconds"]) - float(a["seconds"]))
             / float(a["seconds"])
             for a, b in zip(preds, ablated)
             if a.get("seconds") and b.get("seconds")]
    lost = sum(1 for a, b in zip(preds, ablated)
               if a.get("seconds") and not b.get("seconds"))
    if not moved:
        print("   nothing comparable")
        return
    moved.sort()
    med = moved[len(moved) // 2]
    print(f"   {len(moved)} athletes compared, {lost} lost their history "
          f"entirely")
    print(f"   median move {med * 100:.2f}%   p90 "
          f"{moved[int(0.9 * len(moved))] * 100:.2f}%   "
          f"max {moved[-1] * 100:.2f}%")
    if med < 0.005:
        print("   ⚠ REMOVING EVERY TRACK RACE CHANGES ALMOST NOTHING.")
        print("     Two candidate reasons, and they need different fixes:")
        print("       - the sequence carries is_xc, but the CONTEXT carries")
        print("         no sport at all, so the model is never told which")
        print("         sport it is being asked to predict;")
        print("       - normalized_time is meant to be cross-sport already,")
        print("         so a track row may simply be redundant with the XC")
        print("         rows beside it. ISSUES A/B say that bridge is shaky.")


# ------------------------------------------------------------------ #
#  D. BREAKS
# ------------------------------------------------------------------ #

def sectionD(rows, preds, cur, sport):
    print("\n" + "=" * 70)
    print("D. BREAKS -- can it tell a lay-off from a normal off-season?")
    print("=" * 70)
    got = [(r, p) for r, p in zip(rows, preds)
           if p.get("seconds") and r.get("time_seconds") and p.get("gap_days")]
    if not got:
        print("   no gap information on the predictions")
        return
    bands = ((0, 21, "<=3wk"), (21, 60, "3wk-2mo"), (60, 150, "2-5mo"),
             (150, 400, "5-13mo"), (400, 1e9, "over a year"))
    print(f"   {'gap since last race':<16}{'n':>6}{'median error':>14}"
          f"{'bias':>10}")
    for lo, hi, label in bands:
        errs = [(float(p["seconds"]) - float(r["time_seconds"]))
                / float(r["time_seconds"])
                for r, p in got if lo <= p["gap_days"] < hi]
        if not errs:
            continue
        errs.sort()
        med = errs[len(errs) // 2]
        print(f"   {label:<16}{len(errs):>6}"
              f"{statistics.median(abs(e) for e in errs) * 100:>13.1f}%"
              f"{med * 100:>9.1f}%")
    allerr = [(float(p["seconds"]) - float(r["time_seconds"]))
              / float(r["time_seconds"]) for r, p in got]
    allerr.sort()
    overall = allerr[len(allerr) // 2]
    print(f"\n   OVERALL BIAS {overall * 100:+.1f}% over {len(allerr)} "
          f"athletes")
    if abs(overall) > 0.02:
        print("   ⚠ THAT IS NOT A BREAKS PROBLEM, IT IS A CALIBRATION ONE.")
        print("     A bias this size applies to everybody and has nothing to")
        print("     do with gaps. Re-run with --weather none: if it vanishes,")
        print("     the weather features are the cause, not the model.")

    print("\n   ★ READ THE BIAS COLUMN, NOT THE ERROR. A model that handles")
    print("     breaks correctly has no TREND down that column: a five-month")
    print("     gap in October is the normal off-season and should cost")
    print("     nothing. A bias that grows with the gap means it is charging")
    print("     everyone for a lay-off nobody took; a bias that stays flat")
    print("     while real lay-offs exist means it is charging nobody.")
    print("   ⚠ AND SEE THE is_forecast NOTE AT THE END -- at inference the")
    print("     flag that says 'this gap may be artificial' is always on.")


# ------------------------------------------------------------------ #
#  E. WEATHER
# ------------------------------------------------------------------ #

def sectionE(cur, target, ids, predictTimes):
    print("\n" + "=" * 70)
    print("E. WEATHER -- is it doing anything, and is it the right thing?")
    print("=" * 70)
    out = {}
    for want in ("none", "normal", "forecast"):
        t = dict(target)
        t["weather"] = want
        try:
            out[want] = predictTimes(cur, ids, t)
        except Exception as exc:                            # noqa: BLE001
            print(f"   {want}: unavailable ({exc})")
    base = out.get("none")
    if not base:
        print("   no no-weather baseline to compare against")
        return
    for want in ("normal", "forecast"):
        got = out.get(want)
        if not got:
            continue
        d = [(float(b["seconds"]) - float(a["seconds"])) / float(a["seconds"])
             for a, b in zip(base, got)
             if a.get("seconds") and b.get("seconds")]
        if not d:
            continue
        d.sort()
        med = d[len(d) // 2]
        print(f"   {want:<10} vs no-weather: median "
              f"{med * 100:+.2f}%   p05 {d[int(0.05 * len(d))] * 100:+.2f}%"
              f"   p95 {d[int(0.95 * len(d))] * 100:+.2f}%")
        if all(abs(x) < 1e-9 for x in d):
            print(f"      ! IDENTICAL TO NO-WEATHER. _weatherVariants could "
                  f"not build a\n        {want} row -- a forecast reaches 16 "
                  f"days out, so a past date\n        always lands here -- "
                  f"and fell back to the no-weather shape.")
        elif abs(med) > 0.02 and (d[-1] - d[0]) < abs(med):
            print("      ⚠ A NEAR-UNIFORM SHIFT, WHICH IS NOT WEATHER "
                  "MODELLING.")
            print("        Real conditions help some athletes more than "
                  "others; a constant")
            print("        offset for everybody is the signature of a FEATURE "
                  "DISTRIBUTION")
            print("        the model never saw in training, not of a race "
                  "being harder.")
    print("\n   ⚠ THE TRAIN/SERVE SKEW TO LOOK FOR. In TRAINING the target's")
    print("     weather is what was actually MEASURED that day. At inference")
    print("     the headline is the venue's CLIMATOLOGICAL NORMAL -- an")
    print("     average over years, which is systematically milder than any")
    print("     real day. If the model learned 'hot costs time', a normal")
    print("     that is never hot can never spend it.")
    print("   ! normalAt also returns wind_dir = None, so wind DIRECTION is")
    print("     always 0.0 at inference and was not in training.")


# ------------------------------------------------------------------ #
#  F. THE NORMAL AGAINST THE DAY
# ------------------------------------------------------------------ #

def sectionF(cur, spec, sport):
    """What the venue's climatological normal says, beside what that day
    actually was.

    ⚠ THIS IS THE TRAIN/SERVE SKEW, MADE CONCRETE. In training the target's
      weather is the row the backfill MEASURED for that meet. At inference
      the headline is normalAt: an average over years at that hour. If the
      two differ much, the model is being asked about a day that never
      happens.
    """
    print("\n" + "=" * 70)
    print("F. THE NORMAL vs THE DAY -- is inference asked about a real day?")
    print("=" * 70)
    import forecast as fc
    hour = fc.raceHour(sport)
    normal = fc.normalAt(cur, spec.get("gps_lat"), spec.get("gps_long"),
                         spec.get("date"), hour)
    if not normal:
        print("   the grid has no normal for this venue/date")
        return
    cur.execute("""
        SELECT temp_c, dew_point_c, humidity, apparent_temp_c,
               precipitation_mm, pressure_hpa, cloud_cover,
               wind_speed_km, wind_dir
        FROM   weather
        WHERE  meet_id = %(m)s
        LIMIT  1
    """, {"m": spec.get("meet_id")})
    row = cur.fetchone()
    print(f"   {'feature':<20}{'normal':>12}{'that day':>12}")
    keys = (("temp_c", "temp_c"), ("dew_point_c", "dew_point_c"),
            ("humidity", "humidity"), ("apparent_temp_c", "apparent_temp_c"),
            ("precipitation_mm", "precipitation_mm"),
            ("pressure_hpa", "pressure_hpa"), ("cloud_cover", "cloud_cover"),
            ("wind_speed_kmh", "wind_speed_km"), ("wind_dir", "wind_dir"))
    for nk, dk in keys:
        nv = normal.get(nk)
        dv = (row or {}).get(dk)
        print(f"   {nk:<20}{('--' if nv is None else f'{float(nv):.1f}'):>12}"
              f"{('--' if dv is None else f'{float(dv):.1f}'):>12}")
    if not row:
        print("\n   ! THIS MEET HAS NO MEASURED WEATHER AT ALL, so in "
              "training every\n     example targeting it carried the "
              "all-zero weather shape. Feeding\n     a real normal at "
              "inference asks the model a question it was never\n     asked "
              "about this race.")
    print("\n   ⚠ AND 0.0 MEANS TWO THINGS IN THIS CORPUS. _orZero turns a "
          "NULL into\n     0.0, so pressure 0 hPa -- physically impossible "
          "-- is how 'we do not\n     know' is spelled, in the same slot "
          "where 1013 is a real reading. Any\n     systematic gap between "
          "the weather-on and weather-off predictions\n     is that "
          "ambiguity, not meteorology.")


def seasonRatings(cur, ids, sport, year):
    cur.execute("""
        SELECT person_id, mean_rating FROM athlete_season
        WHERE person_id = ANY(%(ids)s) AND sport = %(s)s AND year = %(y)s
    """, {"ids": list(ids), "s": sport, "y": year})
    return {r["person_id"]: float(r["mean_rating"] or 0)
            for r in cur.fetchall()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meet", type=int, required=True)
    ap.add_argument("--div", type=int)
    ap.add_argument("--sport", default="XC")
    ap.add_argument("--skip", default="",
                    help="comma-separated section letters to skip, e.g. C,E")
    ap.add_argument("--model", default=None, metavar="PATH",
                    help="score a DIFFERENT checkpoint, so two baseline "
                         "rules can be compared on the same race. "
                         "target_stats.pkl, encoders.pkl and venue_vocab.pkl "
                         "must sit beside it -- predict.MODEL_DATA is the "
                         "checkpoint's own directory.")
    ap.add_argument("--weather", default="all",
                    choices=("none", "normal", "forecast", "both", "all"),
                    help="which weather A-D are measured under. 'all' takes "
                         "the NORMAL variant as the headline, which is what "
                         "the page shows; pass none to measure the model "
                         "with the weather features zeroed.")
    a = ap.parse_args()
    skip = {s.strip().upper() for s in a.skip.split(",") if s.strip()}

    # ! BEFORE importing predict, which reads RACECAST_MODEL at import time
    #   and caches the loaded model on the module.
    if a.model:
        os.environ["RACECAST_MODEL"] = os.path.abspath(a.model)

    import predict
    if a.model:
        print(f"scoring checkpoint {predict.MODEL_PATH}")
    st = predict.modelStatus()
    if not st.get("available"):
        print("model unavailable:", st.get("reason"))
        return

    with getConn() as conn:
        with conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            rows = realField(cur, a.meet, a.div, a.sport)
            if not rows:
                print("no field for that meet/division")
                return
            date = str(rows[0]["date"])[:10]
            print(f"\n{len(rows)} athletes ran meet {a.meet} "
                  f"div {a.div} on {date}")
            print("predicting it with every history cut strictly before "
                  "that date")

            target = {"meet_id": a.meet, "div_id": a.div, "sport": a.sport,
                      "date": date, "mode": "rerun_exact",
                      "weather": a.weather, "field": None}
            print(f"weather basis for sections A-D: {a.weather}")
            try:
                predict._loadModel()
                mode = float(predict._model.baseline_mode)
                hl = float(predict._model.baseline_half_life)
                print("baseline rule in this checkpoint: "
                      + ("ewma, half-life "
                         f"{hl:.0f}d" if mode else "last race alone"))
            except Exception:                               # noqa: BLE001
                pass
            ids = [r["person_id"] for r in rows]
            preds = predict._predictTimes(cur, ids, target)

            # the baseline each prediction was anchored on, and its gap
            hist = predict._historyRows(cur, ids)
            cut = predict._asDate(date)
            for r, p in zip(rows, preds):
                h = [x for x in (hist.get(r["person_id"]) or [])
                     if (predict._asDate(x.get("date")) or cut) < cut]
                if h:
                    p["baseline"] = h[-1].get("normalized_time")
                    last = predict._asDate(h[-1].get("date"))
                    p["gap_days"] = (cut - last).days if last else None
                    p["n_tf"] = sum(1 for x in h if not x.get("is_xc"))

            year = int(date[:4])
            if "A" not in skip:
                sectionA(rows, preds, seasonRatings(cur, ids, a.sport, year))
            if "B" not in skip:
                sectionB(rows, preds)
            if "C" not in skip:
                sectionC(cur, target, ids, preds, predict._predictTimes)
            if "D" not in skip:
                sectionD(rows, preds, cur, a.sport)
            if "E" not in skip:
                sectionE(cur, target, ids, predict._predictTimes)
            if "F" not in skip:
                try:
                    sectionF(cur, predict._targetSpec(cur, target), a.sport)
                except Exception as exc:                    # noqa: BLE001
                    print(f"\nF. unavailable ({exc})")

    print("\n" + "=" * 70)
    print("TWO FINDINGS THAT NEEDED NO RUN -- see this file's header")
    print("=" * 70)
    print("  1. is_forecast is ALWAYS 1 at inference "
          "(predict._forecastExample).")
    print("     In training it marks an example whose recent races were")
    print("     HIDDEN, so the model learned to discount long gaps when it")
    print("     is set. Every real break is therefore read as probably")
    print("     artificial.")
    print("  2. There is no field-relative feature anywhere. All 24 context")
    print("     and 21 sequence features describe ONE athlete, so 'everyone")
    print("     here has the same gap' is not an inference the architecture")
    print("     can make.")
    print()


if __name__ == "__main__":
    main()
