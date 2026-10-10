#!/usr/bin/env python3
# Project: xc-predictor / racecast
# File:    build_meet_forecasts.py
# Purpose: Freeze "what we said" before each posted cross country meet, and
#          score it once the results are in (owner, 2026-10-10, approved:
#          the weekly "how we did" loop). Pipeline step 13h, optional.
#
#     /srv/venv/bin/python racecast/build_meet_forecasts.py
#         forecast the week's posted XC meets that are not stored yet,
#         refresh tomorrow's, then score every stored meet that has run
#     ... --dry-run            say what would be written, write nothing
#     ... --meet 512340        only these meets (repeatable)
#     ... --no-score           forecasts only
#     ... --budget-minutes 60  stop cleanly after that long; the next run
#                              carries on where this one stopped
#
# ★ THE CALENDAR IS weekend.py's, THE PREDICTION upcoming_preview's. The
#   meets are the ones "Coming up" lists (posted, next seven days); each
#   race is predicted by upcoming_preview.livePrediction, the exact call the
#   preview page makes, and stored by meet_forecast.saveRace.
#
# ★ INCREMENTAL AND IDEMPOTENT (meet_forecast.forecastAction). A race is
#   written once when it first appears, rewritten once by the run on the
#   day before its meet, and never on or after the meet's day or once it
#   has a result. Running twice in a night writes nothing the second time.
#   Each race commits on its own: a stopped run loses at most the race it
#   was on.
#
# ★ SCORING IS meet_recap's. Every stored meet that has run is scored
#   against its results (meet_recap.scoreMeet), unscored ones always and
#   the last week's again (late results and corrections land through the
#   week after), and the compact score is kept on the row for /recaps and
#   the About page.
#
# ! OPTIONAL, OFF THE && CHAIN. Nothing downstream reads this table; a
#   failure here is logged and the boards publish regardless.
import argparse
import datetime
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "scripts", "engine", "model"):
    p = os.path.join(_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)


def postedXC(cur, today, only=None):
    """The posted cross country meets still ahead (date after today) on the
    coming week's calendar, soonest first."""
    from weekend import comingUp
    out = []
    for d in comingUp(cur, today):
        for m in d["meets"]:
            if m["sport"] != "XC" or not m.get("date") or m["date"] <= today.isoformat():
                continue
            if only and int(m["meet_id"]) not in only:
                continue
            out.append(m)
    out.sort(key=lambda m: (m["date"], -m["n_races"]))
    return out


def forecastMeet(cur, conn, m, today, dry):
    """Forecast one meet's races. Returns {written, kept, skipped, failed}."""
    import meet_forecast as MF
    import upcoming_preview as U
    got = {"written": 0, "kept": 0, "skipped": 0, "failed": 0}
    plan = U._meetPlanUncached(cur, int(m["meet_id"]), m["source"])
    if not plan or not plan.get("edition"):
        # ! no last edition, no field: the preview says the same
        got["skipped"] += len((plan or {}).get("races") or []) or 1
        conn.rollback()
        return got
    src = plan["source"]
    have = MF.existing(cur, src, plan["meet_id"]) if MF.tableReady(cur) else {}
    ran = MF.racesWithResults(cur, src, plan["meet_id"])
    for race in plan["races"]:
        act = MF.forecastAction(plan.get("date") or m["date"], today,
                                have.get(int(race["div_id"])), int(race["div_id"]) in ran)
        if act is None:
            got["kept"] += 1
            continue
        if dry:
            print(f"    would {act}: {plan['name']} ({plan['date']}) {race['label']}")
            got["written"] += 1
            continue
        pred = U.livePrediction(cur, plan["meet_id"], race["div_id"], src)
        if not pred.get("available"):
            print(f"    no prediction: {plan['name']} {race['label']}: {pred.get('reason')}")
            got["failed"] += 1
            conn.rollback()
            continue
        if MF.saveRace(cur, dict(plan, date=plan.get("date") or m["date"]), race, pred, today):
            got["written"] += 1
        else:
            got["kept"] += 1
        conn.commit()
    return got


def scoreAll(cur, conn, today, dry, deadline):
    import meet_forecast as MF
    import meet_recap as R
    from weekend import DAYS
    n_meets = n_races = 0
    for src, mid in MF.toScore(cur, today, DAYS):
        if time.time() > deadline:
            print("  scoring: out of budget; the rest next run")
            break
        stored = MF.loadMeet(cur, mid, src)
        scored = R.scoreMeet(cur, src, mid, stored)
        if dry:
            print(f"    would score {mid} ({src}): {len(scored)} of {len(stored)} races have results")
            continue
        for div, (_rc, sc) in scored.items():
            MF.saveScore(cur, src, mid, div, sc)
            n_races += 1
        n_meets += 1 if scored else 0
        conn.commit()
    return n_meets, n_races


def main():
    ap = argparse.ArgumentParser(description="Freeze the week's XC forecasts; score the ones that ran.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--meet", type=int, action="append", help="only this meet id (repeatable)")
    ap.add_argument("--no-score", action="store_true")
    ap.add_argument("--budget-minutes", type=float, default=None)
    a = ap.parse_args()
    t0 = time.time()
    deadline = t0 + 60 * a.budget_minutes if a.budget_minutes else float("inf")
    today = datetime.date.today()
    import psycopg2.extras
    import meet_forecast as MF
    from database import getConn
    with getConn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not a.dry_run:
                MF.ensureTable(cur)
                conn.commit()
            elif not MF.tableReady(cur):
                print(f"  {MF.TABLE} does not exist yet; a real run creates it")
            meets = postedXC(cur, today, set(a.meet or ()))
            conn.rollback()
            print(f"  {len(meets)} posted XC meet(s) after {today}")
            tot = {"written": 0, "kept": 0, "skipped": 0, "failed": 0}
            for i, m in enumerate(meets, 1):
                if time.time() > deadline:
                    print(f"  out of budget after {i - 1} meet(s); the rest next run")
                    break
                try:
                    got = forecastMeet(cur, conn, m, today, a.dry_run)
                except Exception as exc:                # noqa: BLE001
                    conn.rollback()
                    print(f"  {m['name']} ({m['meet_id']}): {type(exc).__name__}: {exc}")
                    got = {"failed": 1}
                for k, v in got.items():
                    tot[k] += v
            print(f"  forecasts: {tot['written']} written, {tot['kept']} already frozen or not due, "
                  f"{tot['skipped']} with no field, {tot['failed']} failed")
            if not a.no_score and (MF.tableReady(cur) or not a.dry_run):
                nm, nr = scoreAll(cur, conn, today, a.dry_run, deadline)
                print(f"  scored: {nr} race(s) at {nm} meet(s)")
            if a.dry_run:
                conn.rollback()
    print(f"  done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
