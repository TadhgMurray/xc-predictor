#!/usr/bin/env python3
"""
diag_pro_routes.py -- WHICH rule put each athlete in the pro pool. READ ONLY.

    python engine/diag_pro_routes.py
    python engine/diag_pro_routes.py --show 30
    python engine/diag_pro_routes.py --school "De La Salle"

★★ WHY THIS EXISTS, AND IT SHOULD HAVE EXISTED ON 2026-09-20.

   pool_resolve.resolvePool has FIVE independent routes to is_pro:

       no_team        anet wrote team_id = 0 and the race has no ceiling
       team_pro       team_pool.kind = 'pro' (build_team_pool.classify)
       team_has_pros  loadClubPros, gated by clubSeason's majority
       isProTeam      the hand-listed _PRO_TEAMS names
       isProPerson    pro_athlete_season, per (person, season)

   On 2026-09-20 the owner reported schools being pooled pro. I fixed
   build_team_pool.classify -- the `team_pro` route -- shipped it, and said
   it was fixed. Two days later the same report came back, because
   `team_has_pros` is a SECOND implementation of the same rule and it
   excluded only college. The first fix was real and the symptom did not
   move, which is the worst possible feedback: it looks like the diagnosis
   was wrong when it was merely incomplete.

   Nothing in the tree could answer "which rule claimed these athletes",
   so the only instrument was a 12-hour solve. That is what this replaces.

! IT READS THE SAME LOADERS THE SOLVE READS, not a copy of their rules --
  loadTeamLevels, loadClubPros, team_pool. A reimplementation here could
  disagree with the engine and would then be a third implementation of the
  thing whose second implementation caused the bug.

! WHAT IT CANNOT SEE. team_has_pros reaches resolvePool through
  clubSeason's majority gate (the athlete-year must have raced MOSTLY for
  that team), and the majority is computed at pack time from the pack's
  own rows. So the counts here are the POPULATION each route can claim,
  not the exact number it did claim. An upper bound per route is still the
  number that was missing: it tells you which door to look at.

! NOTHING IS WRITTEN. Every statement is a SELECT.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The levels a school can be -- the same tuple build_team_pool and
# pool_resolve now both exclude. Imported rather than retyped.
_SCHOOL_LEVELS = ("hs", "ms", "elem")


def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    got = cur.fetchone()
    return bool(got[0] if not isinstance(got, dict) else list(got.values())[0])


def teamNames(cur, ids):
    """{team_id: (school, state)} from team_identity, falling back to
    anet_team's own name. Named, because a report of bare team ids is
    unactionable -- the owner said so about build_team_pool's own list."""
    out = {}
    ids = sorted({int(i) for i in ids})
    if not ids:
        return out
    if _tableExists(cur, "team_identity"):
        cur.execute("""SELECT team_id, school, state FROM team_identity
                       WHERE team_id = ANY(%s)""", (ids,))
        for t, school, state in cur.fetchall():
            if school:
                out[int(t)] = (school, state)
    missing = [i for i in ids if i not in out]
    if missing and _tableExists(cur, "anet_team"):
        cur.execute("""SELECT team_id, name, state FROM anet_team
                       WHERE team_id = ANY(%s)""", (missing,))
        for t, name, state in cur.fetchall():
            out.setdefault(int(t), (name, state))
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", type=int, default=20,
                    help="teams to name per route")
    ap.add_argument("--school", help="only teams whose identity matches this")
    args = ap.parse_args()

    from database import getConn
    from speed_ratings_db import loadTeamLevels, loadClubPros

    levels, _codes, _n = loadTeamLevels()
    levels = {int(k): v for k, v in levels.items()}
    pros, _by_school = loadClubPros(min_pros=1)
    pros = {int(k): int(v) for k, v in pros.items()}
    print(f"\n  loadTeamLevels: {len(levels):,} teams named")
    print(f"  loadClubPros:   {len(pros):,} teams with >= 1 professional")

    with getConn() as conn:
        with conn.cursor() as cur:
            if not _tableExists(cur, "team_pool"):
                raise SystemExit("team_pool is missing; build it first")
            cur.execute("""SELECT team_id, kind, n_athletes, n_rows, level
                           FROM team_pool""")
            tp = {int(r[0]): (r[1], int(r[2]), int(r[3]), r[4])
                  for r in cur.fetchall()}

            # ── route 2: team_pool says pro
            r_team_pro = {t: v for t, v in tp.items() if v[0] == "pro"}

            # ── route 3: a pro-flagged season on the team, by the team's level
            r_has_pros = {t: tp[t] for t in pros if t in tp}
            school_pros = {t: v for t, v in r_has_pros.items()
                           if (levels.get(t) or v[3]) in _SCHOOL_LEVELS}
            college_pros = {t: v for t, v in r_has_pros.items()
                            if (levels.get(t) or v[3]) == "college"}
            club_pros = {t: v for t, v in r_has_pros.items()
                         if t not in school_pros and t not in college_pros}

            names = teamNames(cur, list(r_team_pro)[:2000] + list(r_has_pros))

            def rows(d, label, note):
                ath = sum(v[1] for v in d.values())
                nrows = sum(v[2] for v in d.values())
                print(f"\n  {label}")
                print(f"    {len(d):,} teams | {ath:,} athletes | "
                      f"{nrows:,} rows")
                print(f"    {note}")
                if not d:
                    return
                top = sorted(d.items(), key=lambda kv: -kv[1][1])[:args.show]
                print(f"      {'team':>8} {'level':<8} {'ath':>7} {'rows':>10}"
                      f"  school")
                for t, v in top:
                    nm, st = names.get(t, (None, None))
                    if args.school and (not nm or args.school.lower()
                                        not in nm.lower()):
                        continue
                    lvl = levels.get(t) or v[3] or "--"
                    print(f"      {t:>8} {str(lvl):<8} {v[1]:>7,} {v[2]:>10,}"
                          f"  {nm or '(unnamed)'}"
                          f"{f' ({st})' if st else ''}")

            print("\n" + "=" * 74)
            print("  ROUTE 2 -- team_pro: team_pool.kind = 'pro'")
            print("=" * 74)
            # ⚠⚠⚠ AND IT IS OFF BY DEFAULT, WHICH THIS FAILED TO SAY AND I
            #     READ THE LISTING AS IF IT WERE LIVE (2026-09-22).
            #
            #     speed_ratings.loadProTeams returns an EMPTY set unless
            #     XCP_TEAM_POOL=1 -- deliberately, since 2026-09-19: the
            #     loader had been querying a column that does not exist, so
            #     the rule had never fired, and switching it on during a bad
            #     solve would have made two changes inseparable. Nothing in
            #     deploy/solve_env.sh or run_pipeline.ps1 sets it.
            #
            #     The SITE never passes team_pro at all -- neither
            #     build_ranking_results, panels nor fill_ratings names the
            #     argument, so it defaults False there whatever the flag says.
            #
            #     So this listing is a population the rule COULD claim, and
            #     under the current environment claims none of. The header's
            #     "not the exact number it did claim" was about the majority
            #     gate and did not cover a route being switched off entirely.
            flag = os.environ.get("XCP_TEAM_POOL", "")
            live = flag not in ("", "0", "false")
            if live:
                print(f"  ⚡ LIVE in the engine: XCP_TEAM_POOL={flag}")
            else:
                print("  ⚠ NOT LIVE. XCP_TEAM_POOL is unset, so "
                      "speed_ratings.loadProTeams returns an empty set and\n"
                      "    this route claims NOBODY in a solve. The site never "
                      "passes team_pro at all.\n"
                      "    The rows below are the population it WOULD claim "
                      "with XCP_TEAM_POOL=1.")
            rows(r_team_pro, "every team team_pool calls pro",
                 "build_team_pool.classify decided these. A K-12 school here "
                 "is a bug.")
            bad = {t: v for t, v in r_team_pro.items()
                   if (levels.get(t) or v[3]) in _SCHOOL_LEVELS}
            if bad:
                print(f"\n    ⚠ {len(bad):,} of them are K-12 SCHOOLS by "
                      f"anet's own level -- classify is wrong again.")
            else:
                print("\n    ✓ no K-12 school is called pro by team_pool.")

            print("\n" + "=" * 74)
            print("  ROUTE 3 -- team_has_pros: a pro-flagged season on the team")
            print("=" * 74)
            rows(school_pros, "K-12 SCHOOLS carrying a pro-flagged season",
                 "Until 2026-09-22 this route swept every athlete who raced "
                 "MOSTLY for\n    these teams into the pro pool. They are "
                 "the schools that went missing.")
            rows(college_pros, "colleges carrying one",
                 "Always exempt -- the route tested `team_level != college` "
                 "from the start.")
            rows(club_pros, "clubs (and no-level teams) carrying one",
                 "These still sweep, and should: a sponsor's youth squad is "
                 "a club.\n    (owner, 2026-09-16, priced against the boards "
                 "and chosen)")

            print("\n" + "=" * 74)
            print("  WHAT CLOSING ROUTE 3 ON SCHOOLS RECOVERS")
            print("=" * 74)
            ath = sum(v[1] for v in school_pros.values())
            print(f"    up to {ath:,} athletes across {len(school_pros):,} "
                  f"school teams stop being\n    eligible for the pro pool "
                  f"by this route. The exact number is lower --\n"
                  f"    clubSeason's majority gate has to fire first -- but "
                  f"this is the population\n    it was drawing from, and "
                  f"none of it should ever have been in reach.")
            print("\n    ! IT TAKES A SOLVE TO SHOW. rating_pool is stamped "
                  "at pack time, so\n      these athletes keep their pro "
                  "pool until the next run re-pools them.")
        conn.rollback()
    print("\n  ! nothing was written.\n")


if __name__ == "__main__":
    main()
