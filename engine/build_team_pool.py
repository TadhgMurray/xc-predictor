#!/usr/bin/env python3
"""
build_team_pool.py -- one row per anet team saying which pool it is, and why.

    python engine/build_team_pool.py --dry-run
    python engine/build_team_pool.py --write
    python engine/build_team_pool.py --dry-run --show 40

★ THE OWNER'S POOLING RULES (2026-09-18), agreed one at a time:

    - anet's own level decides: level 16 is a club (measured -- the census
      shows Oregon Track Club, Nike Oregon Track Club and Georgetown Running
      Club all at 16, against 4 for high schools and 8 for colleges);
    - a club with ANY professional in it is pro ("if anybody we've marked as
      pro is in a club, make entire club pro");
    - a team with fewer than 15 distinct athletes OVER ALL TIME is pro
      ("~3 athletes should actually be 15 and I mean it" ... "not 15 per year,
      15 over all time");
    - a missing anet team id infers NOTHING. Absence of a link is absence of
      evidence, not evidence of pro -- after the tfrrs bridge runs, what is
      left unlinked is mostly small COLLEGE programmes that missed the vote
      bars, and a college is not a pro.

★ WHY A TABLE AND NOT AN EDIT TO speed_ratings_db. The pool question is asked
  from several places on several keys, and the answer is currently spread
  across loadTeamLevels, loadClubPros and loadClubMajority. This states it
  ONCE, per team id, with the reason attached -- which is the same move as
  team_identity: stop recomputing an answer in three places. Nothing here
  changes a rating; the readers are wired up separately.

! WHAT ALREADY EXISTED AND IS REUSED, NOT REBUILT:
      speed_ratings_db.loadTeamLevels  -- anet level code -> level name
      speed_ratings_db.loadClubPros    -- teams with >= 1 pro (min_pros=1
                                          ALREADY, so the owner's club rule
                                          was implemented before he asked)
  The athlete-pool question ("is THIS PERSON pro this year") stays where it
  is, in loadClubMajority, and is deliberately untouched: it holds the owner's
  2026-09-14 rule that a collegian racing the Euros is not a professional,
  which a team-level flag must not override.

Table:

    team_pool (team_id PK, kind, reason, n_athletes, n_rows, level, n_pros)

`kind` is one of pro, club, college, hs, ms, elem, unknown.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ⚠ OVER ALL TIME, NOT PER SEASON, and the owner said so twice. Per season a
#   rural high school fielding eight runners is under the bar every year it
#   exists; over a team's whole history a real school clears 15 easily, so the
#   same number catches only the genuinely tiny entities meant by it.
PRO_MAX_ATHLETES = 15

# team_id 0 is anet's unattached sentinel, not an id.
REAL_TEAM = "team_id IS NOT NULL AND team_id <> 0"

# ! THE LEVELS A SCHOOL CAN BE, so classify can say "this is not a club" once.
#   college is here too: _collegeNames already keeps colleges out of
#   loadClubPros, and this makes that protection a property of the rule rather
#   than of one loader's name list.
_SCHOOL_LEVELS = ("hs", "ms", "elem", "college")

# ! THE FALLBACK MAPPING, used only when speed_ratings_db cannot be imported
#   (it pulls numpy and the model stack). Measured from the census output, and
#   deliberately NOT the primary source -- loadTeamLevels infers names from
#   codes over the whole corpus and is the one definition.
_LEVEL_FALLBACK = {2: "ms", 4: "hs", 8: "college", 16: "club"}

DDL = """
CREATE TABLE IF NOT EXISTS team_pool (
    team_id    int PRIMARY KEY,
    kind       text NOT NULL,
    reason     text NOT NULL,
    n_athletes int  NOT NULL DEFAULT 0,
    n_rows     bigint NOT NULL DEFAULT 0,
    level      text,
    n_pros     int  NOT NULL DEFAULT 0,
    built      date NOT NULL DEFAULT current_date)
"""


def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    got = cur.fetchone()
    return bool(got[0] if not isinstance(got, dict) else list(got.values())[0])


def teamLevels(cur, verbose=True):
    """{team_id: level name}. Prefers the repo's one definition."""
    try:
        from speed_ratings_db import loadTeamLevels
        levels, _codes, _n = loadTeamLevels()
        if verbose:
            print(f"  levels from speed_ratings_db.loadTeamLevels: "
                  f"{len(levels):,} teams named", flush=True)
        return {int(k): v for k, v in levels.items()}
    except Exception as exc:                              # noqa: BLE001
        if verbose:
            print(f"  loadTeamLevels unavailable ({type(exc).__name__}) -- "
                  f"falling back to anet_team.level codes {_LEVEL_FALLBACK}",
                  flush=True)
    cur.execute("SELECT team_id, level FROM anet_team WHERE level IS NOT NULL")
    return {int(t): _LEVEL_FALLBACK[int(lv)] for t, lv in cur.fetchall()
            if int(lv) in _LEVEL_FALLBACK}


def proTeams(cur, verbose=True):
    """{team_id: n pro athletes}. min_pros=1 is the owner's club rule."""
    try:
        from speed_ratings_db import loadClubPros
        by_team, _by_school = loadClubPros(min_pros=1)
        if verbose:
            print(f"  teams with >= 1 professional: {len(by_team):,}",
                  flush=True)
        return {int(k): int(v) for k, v in by_team.items()}
    except Exception as exc:                              # noqa: BLE001
        if verbose:
            print(f"  loadClubPros unavailable ({type(exc).__name__}) -- "
                  f"counting pro_athlete_season directly", flush=True)
    if not _tableExists(cur, "pro_athlete_season"):
        return {}
    # the same question, asked plainly: a pro athlete-season on a team's rows
    cur.execute(f"""
        WITH r AS (
            SELECT team_id, person_id, substr(date, 1, 4)::int AS yr
            FROM   results WHERE {REAL_TEAM} AND date ~ '^(19|20)[0-9][0-9]-'
            UNION ALL
            SELECT team_id, person_id, substr(date, 1, 4)::int
            FROM   results_tf WHERE {REAL_TEAM} AND date ~ '^(19|20)[0-9][0-9]-'
        )
        SELECT r.team_id, count(DISTINCT r.person_id)
        FROM   r JOIN pro_athlete_season p
               ON p.person_id = r.person_id AND p.season = r.yr
        GROUP  BY r.team_id
    """)
    return {int(t): int(n) for t, n in cur.fetchall()}


def teamSizes(cur):
    """{team_id: (n_athletes, n_rows)} over ALL TIME, both feeds."""
    cur.execute(f"""
        WITH r AS (
            SELECT team_id, person_id FROM results  WHERE {REAL_TEAM}
            UNION ALL
            SELECT team_id, person_id FROM results_tf WHERE {REAL_TEAM}
        )
        SELECT team_id, count(DISTINCT person_id), count(*)
        FROM   r GROUP BY team_id
    """)
    return {int(t): (int(a), int(n)) for t, a, n in cur.fetchall()}


# ★ PRECEDENCE IS THE WHOLE RULE, so it is one pure function and testable.
#   Smallness wins over anet's level on the owner's explicit instruction ("if
#   they're that small I'd prefer to make them pro"); a real college with
#   under fifteen athletes in its whole history is not a real college.
#
# ⚠⚠ THE PROFESSIONAL RULE IS A *CLUB* RULE, AND DROPPING THAT WORD BROKE THE
#    BOARDS (2026-09-20). The owner's sentence is "if anybody we've marked as
#    pro is in a CLUB, make entire club pro", and this module's own header and
#    tests/test_team_pool.py both say "a club with ANY professional in it is
#    pro". The code tested `if n_pros:` against EVERY level, so a high school
#    inherited the club rule.
#
#    That is not a rare edge. pro_flag.py classifies an athlete-SEASON, on
#    purpose, and its own docstring names the cases: "Lutkenhaus raced Millrose
#    as a junior", "Sadie Engelhardt forwent a high school outdoor season to
#    race professionally". So a senior flagged pro while still at their high
#    school puts n_pros=1 on THAT HIGH SCHOOL's team id -- and
#    loadClubPros excludes college NAMES (_collegeNames) but has never
#    excluded schools, because its only consumer until now was
#    loadClubMajority, whose per-athlete majority gate made that safe.
#
#    team_pro has no such gate (pool_resolve: "UNGATED, LIKE no_team"), so one
#    pro-flagged season repooled EVERY athlete who has ever worn that school's
#    vest, in every season, all the way back. A pool-relative rating does not
#    shift under that, it BENDS -- the ~50-point split the owner saw between
#    American Fork / Herriman / Belen Jesuit and the schools beside them on the
#    same result page.
#
# ! SO THE LEVEL IS TESTED FIRST, AND ONLY A CLUB INHERITS ITS PROS. A school
#   or college that produced a professional is still a school: that is what the
#   feed's level is FOR, and what the owner's word "club" was doing in the
#   rule. Smallness still outranks everything, unchanged.
def classify(n_athletes, level, n_pros, pro_max=PRO_MAX_ATHLETES):
    """(kind, reason). Pure."""
    if n_athletes < pro_max:
        return "pro", f"fewer than {pro_max} athletes all-time ({n_athletes})"
    if level in _SCHOOL_LEVELS:
        # ! COUNTED, NOT SILENT. A school that produced a professional is the
        #   case this rule used to swallow, so the reason says so and
        #   report() can price it.
        if n_pros:
            return level, (f"anet level (kept: {n_pros} professional "
                           f"athlete-season(s) raced for it, but a {level} is "
                           f"not a club)")
        return level, "anet level"
    if n_pros:
        return "pro", f"{n_pros} professional athlete(s) raced for it"
    if level == "club":
        return "club", "anet level says club"
    if level:
        return level, "anet level"
    return "unknown", "no anet level and big enough not to be called pro"


def build(cur, pro_max=PRO_MAX_ATHLETES, verbose=True):
    cur.execute(DDL)
    levels = teamLevels(cur, verbose)
    pros = proTeams(cur, verbose)
    if verbose:
        print("  counting athletes per team over all time "
              "(one pass over both tables)...", flush=True)
    sizes = teamSizes(cur)
    rows = []
    for team_id, (n_ath, n_rows) in sizes.items():
        level = levels.get(team_id)
        n_pros = pros.get(team_id, 0)
        kind, reason = classify(n_ath, level, n_pros, pro_max)
        rows.append((team_id, kind, reason, n_ath, n_rows, level, n_pros))
    return rows


def write(cur, rows):
    cur.execute("TRUNCATE team_pool")
    cur.executemany("""
        INSERT INTO team_pool (team_id, kind, reason, n_athletes, n_rows,
                               level, n_pros, built)
        VALUES (%s, %s, %s, %s, %s, %s, %s, current_date)
    """, rows)
    cur.execute("CREATE INDEX IF NOT EXISTS team_pool_kind_idx "
                "ON team_pool (kind)")
    return len(rows)


def report(rows, show=20, pro_max=PRO_MAX_ATHLETES):
    from collections import Counter
    kinds = Counter(r[1] for r in rows)
    print(f"\n  {len(rows):,} teams classified")
    print(f"    {'kind':<10} {'teams':>8} {'athletes':>12} {'rows':>14}")
    for kind, n in kinds.most_common():
        ath = sum(r[3] for r in rows if r[1] == kind)
        nr = sum(r[4] for r in rows if r[1] == kind)
        print(f"    {kind:<10} {n:>8,} {ath:>12,} {nr:>14,}")

    # ★★ THE LIST THE OWNER WAS PROMISED. The small-team rule is the one
    #    heuristic here, so what it catches has to be visible BEFORE it lands.
    #    Sorted by ROWS, not by athletes: a team with fourteen athletes and
    #    five thousand rows is the suspicious shape -- either a real programme
    #    whose people are mis-merged, or a relay squad raced to death.
    small = sorted((r for r in rows if r[1] == "pro"
                    and r[3] < pro_max), key=lambda r: -r[4])
    print(f"\n  called pro by the <{pro_max}-athlete rule: {len(small):,} teams. "
          f"The {min(show, len(small))} with the most rows:")
    print(f"    {'team':>8} {'ath':>5} {'rows':>9} {'level':<9} reason")
    for r in small[:show]:
        print(f"    {r[0]:>8} {r[3]:>5} {r[4]:>9,} {str(r[5] or '--'):<9} "
              f"{r[2]}")
    print(f"\n  ! if real schools are in that list they are the accepted price "
          f"of the rule,\n    but they should be seen here rather than found "
          f"later on a rankings page.")

    # ★★ AND THE COUNTERPART: THE SCHOOLS THE CLUB RULE USED TO SWALLOW.
    #    Before 2026-09-20 `if n_pros:` ran against every level, so a high
    #    school that produced one pro-flagged athlete-season was called pro and
    #    -- because team_pro is ungated -- took every athlete in its history
    #    out of the school pools with it. This is that set, now kept. It is
    #    printed for the same reason the list above is: the one heuristic in
    #    this file has to be visible before it lands.
    kept = sorted((r for r in rows if r[1] in _SCHOOL_LEVELS and r[6]),
                  key=lambda r: -r[6])
    print(f"\n  kept as schools DESPITE pro-flagged athlete-seasons: "
          f"{len(kept):,} team ids")
    if kept:
        print(f"    (a senior who races a professional field is still at their "
              f"school --\n     pro_flag classifies a SEASON, not a career)")
        print(f"    {'team':>8} {'pros':>5} {'ath':>6} {'rows':>9} level")
        for r in kept[:show]:
            print(f"    {r[0]:>8} {r[6]:>5} {r[3]:>6,} {r[4]:>9,} {r[5]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=20)
    ap.add_argument("--pro-max-athletes", type=int, default=PRO_MAX_ATHLETES,
                    help="the all-time distinct-athlete floor below which a "
                         "team is called pro (owner: 15)")
    args = ap.parse_args()
    if not (args.write or args.dry_run):
        ap.error("pass --dry-run or --write")

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            if not _tableExists(cur, "anet_team"):
                raise SystemExit("anet_team is missing; run "
                                 "scripts/anet_teams.py first")
            rows = build(cur, args.pro_max_athletes)
            report(rows, args.show, args.pro_max_athletes)
            if args.write:
                print(f"\n  wrote {write(cur, rows):,} rows to team_pool")
        if args.write:
            conn.commit()
            print("  committed.")
        else:
            conn.rollback()
            print("\n  DRY RUN -- nothing written.")


if __name__ == "__main__":
    main()
