#!/usr/bin/env python3
# Project: xc-predictor / racecast
# File:    build_meet_track.py
# Purpose: Backtest the past editions of meets ONCE and store each race's
#          score in meet_track_race, for the predictions page's "on past
#          editions of this meet the model picked the winner X of Y times"
#          line (meet_track.py; owner, 2026-10-10, item 14).
#
#     /srv/venv/bin/python racecast/build_meet_track.py
#         the past editions of every cross country meet on the coming
#         week's calendar (weekend.comingUp) -- the meets people predict
#     /srv/venv/bin/python racecast/build_meet_track.py --meet 275685 --meet 251223
#         one meet's (or several meets') past editions
#     /srv/venv/bin/python racecast/build_meet_track.py --from 2025-08-15 --to 2025-11-30
#         every XC race in a date window (a backfill)
#     ... --budget-minutes 90    stop cleanly after that long; a later run
#                                carries on where this one stopped
#     ... --redo                 re-score races already stored (after a
#                                new model or a re-rating)
#     ... --all-seasons          every past edition, not just this season's
#
# ★ THIS SEASON BY DEFAULT (owner, 2026-10-11: 4,165 races back to 2025-10,
#   one at a time, a line each). Without --from/--to the races are cut at
#   the start of the current academic season (engine/season_year.py, the
#   one clock); --all-seasons restores the full history. Progress is one
#   line per percent of the races (dbfast.Progress) plus a summary.
#
# ★ THE AS-RAN BACKTEST, THROUGH THE SERVED PATH. Each race is predicted as
#   scripts/backtest_predictions.py predicts it at lead 0: target
#   rerun_exact, the runners who actually ran it, every history cut at the
#   race's own date -- and through predict._servedTimes, the basis the page
#   serves (XCP_PREDICT_BASIS), so the record is the page's own, not the
#   network's alone.
#
# ★ INCREMENTAL. A race already in the table is skipped unless --redo, so
#   the first run is the long one and every later run scores only the races
#   that are new since. Each race commits on its own: a stopped run loses at
#   most the race it was on.
#
# ! THE SAME FIELD FLOOR AS THE BACKTEST (backtest_predictions --min-field,
#   20): a three-runner race's "winner" is a coin with three sides, and it
#   would flatter the hit rate. Read from that script's own argparse default
#   so the two cannot drift.
#
# ! READ-MOSTLY: the only write is meet_track_race, created here
#   (IF NOT EXISTS) and upserted per race. No swap is needed -- a row is
#   one race's fact, and a reader summing a half-built set says less, not
#   something wrong.
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

_ISO = r"^(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}"


def _backtestMinField():
    """backtest_predictions.py's --min-field default, read off its parser
    source rather than copied."""
    import re
    path = os.path.join(_ROOT, "scripts", "backtest_predictions.py")
    try:
        src = open(path, encoding="utf-8").read()
        m = re.search(r'"--min-field",\s*type=int,\s*default=(\d+)', src)
        if m:
            return int(m.group(1))
    except OSError:
        pass
    return 20


MIN_FIELD = _backtestMinField()


def ensureTable(conn):
    import meet_track as MT
    with conn.cursor() as cur:
        cur.execute(MT.DDL)
    conn.commit()


def editionMeets(cur, source, name, state):
    """[{meet_id, name, state}] -- every meet of this feed that is an
    edition of `name` in `state` (meet_track.editionKey's rule)."""
    from last_edition import _candidates, editionName
    want = editionName(name)
    st = (state or "").strip().upper()
    out = []
    for c in _candidates(cur, "XC", source, name):
        if editionName(c["name"]) != want:
            continue
        if (c.get("state") or "").strip().upper() != st:
            continue
        out.append(c)
    return out


def racesOf(cur, meet_ids, source, min_field, lo=None, hi=None):
    """[{meet_id, div_id, source, day, n, meet_name, state}] -- the races of
    these meets (or of a date window) with a field at the floor."""
    where, p = ["r.person_id IS NOT NULL", "r.time_seconds > 0",
                "r.time_seconds < 19999", f"r.date ~ '{_ISO}'"], {"n": min_field}
    if meet_ids is not None:
        where.append("r.meet_id = ANY(%(ids)s) AND r.source = %(src)s")
        p.update(ids=list(meet_ids), src=source)
    if lo:
        where.append("r.date >= %(lo)s")
        p["lo"] = lo
    if hi:
        where.append("r.date <= %(hi)s")
        p["hi"] = hi + "~"
    cur.execute(f"""
        SELECT r.meet_id, r.div_id, r.source, left(min(r.date), 10) AS day,
               count(DISTINCT r.person_id) AS n
        FROM   results r
        WHERE  {' AND '.join(where)}
        GROUP  BY r.meet_id, r.div_id, r.source
        HAVING count(DISTINCT r.person_id) >= %(n)s
        ORDER  BY 4 DESC""", p)
    races = [dict(r) for r in cur.fetchall()]
    # the meet's name and state, once per meet (the edition key)
    keys = sorted({(r["meet_id"], r["source"]) for r in races})
    names = {}
    for mid, src in keys:
        cur.execute("""SELECT min(meet_name) AS name, min(state) AS state
                       FROM meets WHERE meet_id = %s AND source = %s""", (mid, src))
        row = cur.fetchone()
        if (not row or not row["name"]) and src == "tfrrs":
            cur.execute("""SELECT meet_name AS name, state FROM meets_tfrrs
                           WHERE meet_id = %s AND sport = 'XC' LIMIT 1""", (mid,))
            row = cur.fetchone()
        names[(mid, src)] = (row or {}).get("name"), (row or {}).get("state")
    for r in races:
        r["meet_name"], r["state"] = names.get((r["meet_id"], r["source"]), (None, None))
    return [r for r in races if r["meet_name"]]


def stored(cur):
    import meet_track as MT
    cur.execute(f"SELECT meet_id, div_id, source FROM {MT.TABLE}")
    return {(r["meet_id"], r["div_id"], r["source"]) for r in cur.fetchall()}


def scoreOne(cur, race):
    """Backtest one race; the row to store, or None."""
    import predict as P
    import meet_track as MT
    cur.execute("""SELECT DISTINCT ON (person_id) person_id, time_seconds
                   FROM results WHERE meet_id = %s AND div_id = %s AND source = %s
                     AND person_id IS NOT NULL
                     AND time_seconds > 0 AND time_seconds < 19999
                   ORDER BY person_id, time_seconds""",
                (race["meet_id"], race["div_id"], race["source"]))
    actual = {r["person_id"]: float(r["time_seconds"]) for r in cur.fetchall()}
    ids = sorted(actual)
    if len(ids) < MIN_FIELD:
        return None
    target = {"mode": "rerun_exact", "sport": "XC", "meet_id": race["meet_id"],
              "div_id": race["div_id"], "source": race["source"]}
    preds = P._servedTimes(cur, ids, target)
    got = MT.scoreRace(actual, {p: (x or {}).get("seconds") for p, x in zip(ids, preds)})
    if not got:
        return None
    got.update(meet_id=race["meet_id"], div_id=race["div_id"], source=race["source"],
               edition_key=MT.editionKey(race["meet_name"], race.get("state")),
               meet_name=race["meet_name"], race_date=race["day"],
               basis=P._predictBasis())
    return got


def upsert(conn, row):
    import meet_track as MT
    cols = ("meet_id", "div_id", "source", "edition_key", "meet_name", "race_date",
            "n_runners", "n_predicted", "winner_actual", "winner_pred", "picked",
            "abs_err", "median_err", "median_err_pct", "basis")
    with conn.cursor() as cur:
        cur.execute(f"""
            INSERT INTO {MT.TABLE} ({', '.join(cols)}, sport, computed_at)
            VALUES ({', '.join('%(' + c + ')s' for c in cols)}, 'XC', now())
            ON CONFLICT (meet_id, div_id, source) DO UPDATE SET
            {', '.join(f'{c} = EXCLUDED.{c}' for c in cols[3:])}, computed_at = now()
        """, row)
    conn.commit()


def seasonStart(today=None):
    """'YYYY-MM-DD': the first day of the current season (season_year's
    academic year, opening in ACADEMIC_START_MONTH)."""
    from season_year import academicYear, ACADEMIC_START_MONTH
    today = today or datetime.date.today()
    return datetime.date(academicYear(today), ACADEMIC_START_MONTH, 1).isoformat()


def targets(cur, args):
    """The races to score, newest first, by the mode the flags pick."""
    if args.lo or args.hi:
        return racesOf(cur, None, None, args.min_field, args.lo, args.hi)
    lo = None if args.all_seasons else seasonStart()
    if lo:
        print(f"  this season only: races from {lo} (--all-seasons for every "
              f"past edition)", flush=True)
    meets = []                                    # (meet_id, source)
    if args.meet:
        for mid in args.meet:
            meets.append((int(mid), args.source))
    else:
        import weekend
        for day in weekend.comingUp(cur):
            for m in day["meets"]:
                if m["sport"] == "XC":
                    meets.append((int(m["meet_id"]), m["source"]))
        print(f"  {len(meets)} cross country meets on the coming week's calendar")
    from last_edition import upcomingMeet
    out, seen = [], set()
    for mid, src in meets:
        up = upcomingMeet(cur, mid, "XC", src)
        if not up or not up.get("name"):
            continue
        eds = [c["meet_id"] for c in editionMeets(cur, up["source"], up["name"], up.get("state"))
               if c["meet_id"] != mid]
        if not eds:
            continue
        for r in racesOf(cur, eds, up["source"], args.min_field, lo=lo):
            k = (r["meet_id"], r["div_id"], r["source"])
            if k not in seen:
                seen.add(k)
                out.append(r)
    out.sort(key=lambda r: r["day"], reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--meet", action="append", help="a meet id whose past editions to score")
    ap.add_argument("--source", default="anet", help="the feed of --meet (anet or tfrrs)")
    ap.add_argument("--from", dest="lo")
    ap.add_argument("--to", dest="hi")
    ap.add_argument("--min-field", type=int, default=MIN_FIELD)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--budget-minutes", type=float, default=None)
    ap.add_argument("--all-seasons", action="store_true",
                    help="every past edition, not just the current season's")
    args = ap.parse_args()

    import psycopg2.extras
    from database import getConn
    from dbfast import Progress, interruptible
    interruptible()                       # one Ctrl-C cancels the statement
    t0 = time.time()
    with getConn() as conn:
        ensureTable(conn)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        races = targets(cur, args)
        done = set() if args.redo else stored(cur)
        conn.rollback()
        todo = [r for r in races if (r["meet_id"], r["div_id"], r["source"]) not in done]
        print(f"  {len(races)} races found, {len(races) - len(todo)} already scored, "
              f"{len(todo)} to score (field >= {args.min_field})", flush=True)
        n_ok = n_pick = n_none = n_fail = 0
        misses = []
        prog = Progress("races", len(todo))
        for i, race in enumerate(todo, 1):
            if args.budget_minutes and time.time() - t0 > 60 * args.budget_minutes:
                print(f"  budget reached after {i - 1} races; run again to carry on")
                break
            try:
                row = scoreOne(cur, race)
                conn.rollback()                    # the reads; the write commits itself
                if row:
                    upsert(conn, row)
                    n_ok += 1
                    n_pick += bool(row["picked"])
                    if row.get("median_err") is not None:
                        misses.append(row["median_err"])
                else:
                    n_none += 1
            except Exception as exc:               # noqa: BLE001
                conn.rollback()
                n_fail += 1
                # a failure is rare and worth its own line
                print(f"  ! {race['day']} {race['meet_name']} (meet {race['meet_id']} "
                      f"div {race['div_id']}, {race['source']}): "
                      f"{type(exc).__name__}: {exc}", flush=True)
            prog.tick(extra=f"{n_ok} scored, {n_pick} winners picked; "
                            f"last {race['day']} {race['meet_name']}")
    misses.sort()
    med = f", median miss {misses[len(misses) // 2]:.1f}s" if misses else ""
    print(f"  {n_ok} races scored ({n_pick} winners picked{med}), "
          f"{n_none} with nothing predicted, {n_fail} failed, in "
          f"{datetime.timedelta(seconds=int(time.time() - t0))}")


if __name__ == "__main__":
    main()
