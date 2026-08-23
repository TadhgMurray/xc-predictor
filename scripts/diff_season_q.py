# Project: xc-predictor
# File:    scripts/diff_season_q.py
# Purpose: SHOW THE BOARD CHANGE BEFORE ANYTHING IS REBUILT.
#
#   build_ranking_results now takes athlete_season.mean_rating as the 80th
#   percentile of an athlete's races rather than their average, because the
#   average rewarded a season that only had championship races on file. That
#   is a claim about ordering, and ordering is what a team board IS, so this
#   prints the ordering both ways from the SAME ranking_results and lets the
#   difference be read rather than assumed.
#
# ⚠ READ-ONLY. Touches no table, builds nothing, swaps nothing. It computes
#   both season numbers in one query and runs them through build_team_season's
#   OWN scoring -- rankTeams, the pool ceiling, the eligibility rules -- so
#   what is printed is the board that build would produce, not a re-derivation
#   that could disagree with it.
#
# ! NEEDS ranking_results TO EXIST AND BE CURRENT. It reads the live table, so
#   run it after a pipeline that populated ranking_results and before the one
#   that would rebuild athlete_season.
#
# USAGE
#   python scripts/diff_season_q.py                     # hs_m + hs_f, 2025
#   python scripts/diff_season_q.py --pool hs_m --year 2019 --top 30
#   python scripts/diff_season_q.py --year all --movers 40
# ============================================================================
import argparse
import os
import sys

sys.path[:0] = ["scripts", "racecast", "engine"]
from database import getConn, initPool                       # noqa: E402
import build_team_season as T                                # noqa: E402
from build_ranking_results import _SEASON_Q                  # noqa: E402

# ★ BOTH NUMBERS FROM ONE SCAN. Computing them in separate queries would let
#   them disagree about which rows they saw if anything wrote in between.
_SQL = f"""
    SELECT sport, year, pool,
           upper(btrim(mode() WITHIN GROUP (ORDER BY state)))  AS state,
           btrim(mode() WITHIN GROUP (ORDER BY school))        AS school,
           person_id,
           avg(speed_rating)::real                            AS old_rating,
           (percentile_cont({_SEASON_Q})
                WITHIN GROUP (ORDER BY speed_rating))::real    AS new_rating,
           count(*)                                           AS n_races
    FROM   ranking_results
    WHERE  pool = ANY(%(pools)s)
      AND  (%(year)s = 0 OR year = %(year)s)
      AND  sport = %(sport)s
    GROUP  BY person_id, pool, sport, year
    HAVING count(*) >= {T.MIN_RACES}
    ORDER  BY sport, year, pool
"""


def rankWith(rows, field):
    """The real board, scored by build_team_season, using one of the two."""
    out = {}
    feed = [dict(r, rating=r[field]) for r in rows
            if r[field] is not None and r["state"] in T.US_STATES]
    for scope, pool, sport, year, teams in T.boards(feed):
        if scope != "usa":
            continue
        for t in teams:
            out[(pool, sport, year, t["school"], t["state"])] = t
    return out


def main():
    ap = argparse.ArgumentParser(description="Team board, mean vs quantile.")
    ap.add_argument("--pool", action="append",
                    help="repeatable; default hs_m and hs_f")
    ap.add_argument("--sport", default="XC")
    ap.add_argument("--year", default="2025",
                    help="a year, or 'all' for every season at once")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--movers", type=int, default=20)
    args = ap.parse_args()

    pools = args.pool or ["hs_m", "hs_f"]
    year = 0 if args.year == "all" else int(args.year)
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        cur.execute(_SQL, {"pools": pools, "year": year, "sport": args.sport})
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        conn.rollback()
    print(f"\n  {len(rows):,} athlete-seasons  "
          f"({args.sport}, {'/'.join(pools)}, "
          f"{'every year' if not year else year})")
    if not rows:
        sys.exit("  nothing to compare -- is ranking_results populated?")

    # ★ WHAT SHARE OF A SEASON IS ACTUALLY RACED ALL OUT -- measured, not
    #   guessed, because it is what decides the right quantile.
    #
    # ⚠ q HAS TO REACH THE HARD RACES OR IT FIXES NOTHING. The bias comes from
    #   thin seasons holding only championship races; an estimator only
    #   removes it if, for a FULL season, it lands inside that same top slice.
    #   If 25% of races are run hard, q must sit above 0.75; a q below the
    #   hard-race share leaves most of the gap in place. Pushing q higher is
    #   not free either -- the closer it gets to the maximum, the more it
    #   rewards simply having raced more often.
    deep = [r for r in rows if r["n_races"] >= 8]
    if deep:
        shares = []
        for r in deep:
            # the season number as a fraction of the way to the best race
            span = r["new_rating"] - r["old_rating"]
            shares.append(span)
        span = sorted(shares)[len(shares) // 2]
        print(f"\n  on {len(deep):,} seasons of 8+ races, the {_SEASON_Q:.0%} "
              f"race sits {span:+.1f} pts above the mean race")
    print(f"  q = {_SEASON_Q:.2f}. If the falls below are NOT the thin teams, "
          f"raise it toward 0.90;\n  if the risers are simply the teams with "
          f"the most races, lower it toward 0.70.")

    old, new = rankWith(rows, "old_rating"), rankWith(rows, "new_rating")

    # ! THE RACE COUNT IS THE WHOLE POINT, so it travels with every team.
    races = {}
    for r in rows:
        k = (r["pool"], r["sport"], r["year"], r["school"], r["state"])
        races.setdefault(k, []).append(r["n_races"])

    for pool in pools:
        keys = [k for k in old if k[0] == pool]
        if not keys:
            continue
        print(f"\n  ==== {pool} top {args.top} ====")
        print(f"  {'#':>3} {'team':<34}{'was':>6}{'now':>7}{'move':>7}"
              f"{'top5 mean':>11}{'med races':>11}")
        best = sorted(keys, key=lambda k: new[k]["rank"])[:args.top]
        for k in best:
            o, n = old[k].get("rank"), new[k]["rank"]
            rs = sorted(races.get(k, [0]))
            med = rs[len(rs) // 2]
            move = "" if o is None else f"{o - n:+d}" if o != n else "-"
            label = f"{k[3][:28]} ({k[4]}) {k[2]}"
            print(f"  {n:>3} {label:<34}{(o if o else 0):>6}{n:>7}{move:>7}"
                  f"{new[k]['top5_mean']:>11.1f}{med:>11}")

        # ⚠ AND WHO MOVED, WHICH IS THE ACTUAL EVIDENCE. If the change does
        #   what it claims, the teams that RISE should be the ones with more
        #   races each and the teams that FALL should be the thin ones.
        moved = [(old[k]["rank"] - new[k]["rank"], k) for k in keys
                 if k in new and old[k].get("rank") and new[k].get("rank")]
        moved.sort()
        print(f"\n  ---- {pool}: biggest falls ----")
        for d, k in moved[:args.movers // 2]:
            rs = sorted(races.get(k, [0]))
            print(f"   {d:+5d}  {k[3][:30]:<32}({k[4]}) {k[2]}  "
                  f"median {rs[len(rs)//2]} races/athlete")
        print(f"\n  ---- {pool}: biggest rises ----")
        for d, k in moved[::-1][:args.movers // 2]:
            rs = sorted(races.get(k, [0]))
            print(f"   {d:+5d}  {k[3][:30]:<32}({k[4]}) {k[2]}  "
                  f"median {rs[len(rs)//2]} races/athlete")

        # THE ONE NUMBER THAT SAYS WHETHER THE STORY HOLDS.
        up = [races.get(k, [0]) for d, k in moved if d > 2]
        down = [races.get(k, [0]) for d, k in moved if d < -2]
        if up and down:
            mu = sum(sum(x) / len(x) for x in up) / len(up)
            md = sum(sum(x) / len(x) for x in down) / len(down)
            print(f"\n  teams that ROSE  more than 2 places: "
                  f"{len(up):>5}, {mu:.1f} races/athlete")
            print(f"  teams that FELL  more than 2 places: "
                  f"{len(down):>5}, {md:.1f} races/athlete")
            print("  -> the change did what it claims"
                  if mu > md else
                  "  -> ⚠ THE RISERS DO NOT RACE MORE. The premise does not "
                  "hold on this data;\n     do not rebuild on it.")


if __name__ == "__main__":
    main()
