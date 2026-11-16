#!/usr/bin/env python3
"""
diag_round_squads.py -- why a school is missing from a projected qualifier
round (owner, 2026-11-16: the D3 nationals projection left out North
Central (Ill.) and WashU; both ran last year's Midwest regional).

READ-ONLY. For one race of a past meet re-run on a later day (the way the
qualifier projection runs last season's regionals: upcoming_preview
.predictedPast -> predict._teamRosters, mode rerun), it prints each step for
the schools named:

  1. last year's entry: the school names in the race, and how many ran
  2. the narrowing the rerun applies: race gender, levels, each school's
     resolved state (predict._teamStates)
  3. the current squad predict._currentSquads finds for that name, with and
     without the state / level narrowing
  4. the live prediction of the round: is the school in it, and with whom

    set -a; . /etc/xc-predictor.env; set +a
    /srv/venv/bin/python scripts/diag_round_squads.py --meet 27045 --on 2026-11-14 \\
        --school "North Central (Ill.)" --school "Washington U."
"""
import argparse
import collections
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--meet", type=int, required=True)
    ap.add_argument("--source", default="tfrrs")
    ap.add_argument("--on", required=True, help="the day the round is re-run (ISO)")
    ap.add_argument("--school", action="append", default=[], help="a school name as last year's race spells it")
    ap.add_argument("--div", type=int, action="append", default=[], help="only these races (default: every race)")
    ap.add_argument("--predict", action="store_true", help="also run the model on the round (slow)")
    a = ap.parse_args(argv)

    import psycopg2.extras
    from database import getConn
    import last_edition as LE
    import predict as P
    import upcoming_preview as U

    want = [s.lower() for s in a.school]

    def mine(name):
        n = (name or "").lower()
        return any(w in n or n in w for w in want) if want else True

    with getConn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SET statement_timeout = '600s'")
        races = LE.editionRaces(cur, a.meet, "XC", a.source)
        year = P._currentSeason(cur, "XC")
        print(f"meet {a.source} {a.meet}, re-run on {a.on}; current season {year}")
        for r in races:
            div = int(r["div_id"])
            if a.div and div not in a.div:
                continue
            print(f"\n=== race {div}: {r.get('label')!r} ({r.get('gender') or '?'})")
            originals, basis = P.thisYearOriginals(cur, a.meet, div, "XC", source=a.source)
            originals = P.unpackIndividuals(originals)
            counts = collections.Counter(o.get("school") for o in originals)
            print(f"1. last year's entry: {len(originals)} runners, {len(counts)} schools")
            for s, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                if mine(s):
                    print(f"     {s!r}: {n} ran   is a team: {P.isTeam(s)}")
            ids = [o["person_id"] for o in originals]
            gender = P._raceGender(cur, a.meet, div, "XC", a.source, ids)
            levels = P._raceLevels(cur, originals, "XC", year)
            mstate = P._meetState(cur, a.meet, "XC", source=a.source)
            states = P._teamStates(cur, [dict(o) for o in originals], mstate)
            print(f"2. narrowing: gender {gender!r}, levels {levels!r}, meet state {mstate!r}")
            at_meet = sorted({o["school"] for o in originals if P.isTeam(o.get("school"))})
            for s in at_meet:
                if mine(s):
                    print(f"     state of {s!r}: {states.get(s)!r}")
            pick = [s for s in at_meet if mine(s)]
            if not pick:
                print("     (none of the named schools ran this race)")
                continue
            plain = P._currentSquads(cur, pick, "XC", year, gender=gender)
            full = P._currentSquads(cur, pick, "XC", year, gender=gender, levels=levels,
                                    states={s: states.get(s) for s in pick if states.get(s)})
            print("3. current squad found for each name:")
            for s in pick:
                p, f = plain.get(s) or [], full.get(s) or []
                print(f"     {s!r}: {len(p)} with gender only, {len(f)} after level + state narrowing")
                for row in f[:7] or p[:7]:
                    print(f"        {row.get('name')!s:<28} {row.get('pool')!s:<12} "
                          f"rating {row.get('rating')!s:<8} grade {row.get('grade')!s}")
            if a.predict:
                # ★ THE RAW MODEL ROWS TOO (2026-11-16: the selection ranked a
                #   round on "times" of ~70 s for an 8K): _trim keeps only the
                #   page's keys, so its input is caught here
                raw = {}
                _trim0 = U._trim

                def _catch(out, target):
                    raw["out"], raw["target"] = out, target
                    return _trim0(out, target)
                U._trim = _catch
                try:
                    pred = U.livePrediction(cur, a.meet, div, a.source, on=a.on)
                finally:
                    U._trim = _trim0
                tg = raw.get("target") or {}
                print(f"   target: distance {tg.get('distance')!r} {tg.get('distance_units') or tg.get('units') or ''} "
                      f"course {tg.get('course_id') or tg.get('venue') or tg.get('location_id')!r} "
                      f"date {tg.get('date')!r} mode {tg.get('mode')!r}")
                rows = (raw.get("out") or {}).get("runners") or []
                for s in pick:
                    mine = sorted((x for x in rows if x.get("school") == s),
                                  key=lambda x: x.get("place") or 9999)[:5]
                    for x in mine:
                        print(f"     {s[:18]:<18} {x.get('name')!s:<24} place {x.get('place')!s:<4} "
                              f"secs {x.get('seconds')!s:<8} norm {x.get('normalized')!s:<8} "
                              f"race_time {x.get('is_race_time')!s:<5} ratio {x.get('clock_ratio')!s:<8} "
                              f"basis {x.get('time_basis')!s} pool {x.get('time_pool')!s}")
                if not pred.get("available"):
                    print(f"4. prediction: not available ({pred.get('reason')})")
                    continue
                by = collections.Counter(x.get("school") for x in pred.get("runners") or [])
                print(f"4. prediction: {len(pred.get('runners') or [])} runners, {len(by)} schools")
                for s in pick:
                    print(f"     {s!r}: {by.get(s, 0)} runners in the predicted round")
                teams = [t for t in pred.get("teams") or [] if t.get("score") is not None]
                teams.sort(key=lambda t: t["score"])
                print("   top teams: " + ", ".join(f"{t.get('team')} {round(t['score'])}" for t in teams[:8]))
        conn.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
