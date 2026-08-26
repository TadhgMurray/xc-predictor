# Project: xc-predictor / scripts
# File:    why_unrated.py
# Purpose: One athlete, every row, the exact gate that killed each rating.
#
#     python scripts/why_unrated.py 12345678
#     python scripts/why_unrated.py 12345678 --sport TF
#
# ★ WHY. The owner's example, 2026-08-27: a Tufts runner with honest 24-25
#   minute 8Ks, rated in 2019-2020 and DASHED for every season since --
#   through the fill, which prices everything it can resolve a pool for.
#   That shape (rated then permanently unrated, spanning a school change)
#   smells like a person-level verdict -- grade_fix, athlete_season_level,
#   a pro flag -- poisoning pool resolution for whole seasons. This stops
#   the guessing: it re-runs the board build's own pool decision on each
#   row and prints the verdict chain, plus every person-level verdict table
#   the decision consults.
#
# Read-only.

import argparse
import os
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
sys.path.insert(0, "racecast")

from database import getConn                          # noqa: E402
import psycopg2.extras                                # noqa: E402

import build_ranking_results as B                     # noqa: E402
from fill_ratings import _rowPool                     # noqa: E402
from normalize_distance import poolBandFor            # noqa: E402

_MARK = "WHERE r.speed_rating IS NOT NULL"


def _sqlFor(sport, rated):
    sql = B._SQL[sport]
    assert _MARK in sql
    cond = ("COALESCE(r.person_id, r.athlete_id) = %(pid)s"
            + ("" if rated is None else
               f" AND r.speed_rating IS {'NOT ' if rated else ''}NULL"))
    return sql.replace(_MARK, "WHERE " + cond)


def _verdict(row, sport):
    if row.normalized_time is None:
        return ("NO NORMALIZED TIME -- the backfill skipped this row "
                "(wheelchair/no-distance/insane/unknown-pool census); "
                "nothing downstream can rate it")
    pool = _rowPool(row, sport)
    if pool is None:
        return ("POOL UNRESOLVABLE -- resolvePool returned None from these "
                "facts; the engine and the fill both refuse the row")
    bits = [f"pool {pool}"]
    lo, hi = poolBandFor(pool, sport)
    nt = float(row.normalized_time)
    if not (lo <= nt <= hi):
        bits.append(f"OUT OF BAND (nt {nt:.0f} vs {lo:.0f}-{hi:.0f}): "
                    "rated by the fill, never ranked")
    if not B.isRankablePool(pool):
        bits.append("pool not rankable (boards skip it)")
    if getattr(row, "grade_trust", "high") == "low":
        bits.append("grade trust LOW: rated, not ranked")
    if getattr(row, "dist_corrected", False):
        bits.append("corrected division: displayed, never ranked")
    if row.speed_rating is None:
        bits.append("speed_rating NULL -- if nt+pool are fine above, the "
                    "fill has not run since this row landed (or its pool "
                    "has no board constant)")
    else:
        bits.append(f"rated {float(row.speed_rating):.1f}")
    return "; ".join(bits)


def _table(cur, name, sql, params):
    cur.execute("SELECT to_regclass(%s)", (name,))
    if cur.fetchone()[0] is None:
        print(f"    {name}: table absent")
        return
    cur.execute(sql, params)
    rows = cur.fetchall()
    if not rows:
        print(f"    {name}: no rows for this person")
        return
    for r in rows:
        print(f"    {name}: {dict(r._asdict()) if hasattr(r, '_asdict') else r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person_id", type=int)
    ap.add_argument("--sport", choices=("XC", "TF"), default="XC")
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor() as cur:
            B.prepareGenderTemp(conn)
            if args.sport == "XC":
                B.prepareXcTfrrsDistTemp(conn)
            else:
                B.prepareTfStateTemp(conn)
        with conn.cursor(
                cursor_factory=psycopg2.extras.NamedTupleCursor) as cur:
            cur.execute(_sqlFor(args.sport, rated=None),
                        {"pid": args.person_id, "since": "1990-01-01"})
            rows = sorted(cur.fetchall(), key=lambda r: r.date)

            print(f"\n  person {args.person_id}, {args.sport}: "
                  f"{len(rows)} rows\n")
            for r in rows:
                print(f"  {r.date}  {str(r.school)[:26]:<26} "
                      f"grade {str(r.grade):<5} src {r.source:<6}")
                print(f"      {_verdict(r, args.sport)}")

            print("\n  person-level verdict tables (what resolvePool "
                  "consults):")
            pid = args.person_id
            _table(cur, "grade_fix",
                   "SELECT season, grade, level, method, trust FROM "
                   "grade_fix WHERE person_id = %s ORDER BY season", (pid,))
            _table(cur, "athlete_season_level",
                   "SELECT sport, ay, level FROM athlete_season_level "
                   "WHERE person_id = %s ORDER BY ay, sport", (pid,))
            _table(cur, "pro_athlete_season",
                   "SELECT season FROM pro_athlete_season "
                   "WHERE person_id = %s ORDER BY season", (pid,))
            _table(cur, "college_first_season",
                   "SELECT * FROM college_first_season "
                   "WHERE person_id = %s", (pid,))
            print("\n  READ: a season whose rows say NO NORMALIZED TIME "
                  "died in the backfill --\n  check the verdict tables for "
                  "the season's level/grade verdict. POOL\n  UNRESOLVABLE "
                  "with sane facts = a resolvePool gate; paste this output "
                  "back.")


if __name__ == "__main__":
    main()
