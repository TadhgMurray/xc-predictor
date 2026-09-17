#!/usr/bin/env python3
"""
link_tfrrs_to_anet.py -- which anet TEAM each tfrrs school string is, learned
from the athletes who appear in both feeds.

    python scripts/link_tfrrs_to_anet.py                 # DRY RUN, prints it
    python scripts/link_tfrrs_to_anet.py --show 60
    python scripts/link_tfrrs_to_anet.py --write         # school_team_link

★ THE OWNER'S PLAN (2026-09-16): "We have a bunch of teams, which currently
  we are combining by name/location. We need to combine by (team id, name,
  location) to help tfrrs combine. What we should do is scrape anet for all
  the team ids we need. Then we combine with tfrrs using our already
  combined athletes that contain both schools with races from tfrrs and
  anet. That should get us over the hump, and any remaining tfrrs schools
  just put as their own schools still but keep separate and under current
  logic so they don't fuck with the overwhelming majority. Same for any
  team id = 0 teams."

WHY A LINK AND NOT A NAME MATCH
    anet spells the college "Williams College" and its athletes' rows say
    "Williams"; tfrrs spells it "Williams College" too, but carries no anet
    team id at all. Every attempt to join those by STRING has failed in a
    different direction -- and each failure was silent. The athletes are the
    join that needs no spelling: person_id is already merged across the two
    feeds, so an athlete with anet rows on team 21570 and tfrrs rows in the
    same season IS that team's athlete, and the tfrrs string they wear is
    that team's name.

⚠ THE TRAP, AND THE GUARD. A person's HIGH SCHOOL anet rows and their
  COLLEGE tfrrs rows can share a calendar year -- spring track for the high
  school, autumn cross country for the college. Linking on a shared person
  and a shared year alone would therefore marry a high school to a college,
  which is the very collision this is meant to end. So a vote only counts
  when the ANET TEAM'S OWN LEVEL IS COLLEGE (anet_team.level, named by
  speed_ratings_db.loadTeamLevels -- measured: 4 is hs, 8 is college). A
  high school team can never be voted onto a tfrrs college string.

⚠ AND A MAJORITY, NOT A WITNESS. MIN_ATHLETES athletes must agree and the
  winner must hold MIN_SHARE of the string's votes, so one transfer, one
  mis-merged person or one guest runner cannot mint a link. Everything that
  does not clear those bars is left OUT -- it stays its own school under the
  name/home-state logic, which is the owner's rule for the remainder.

Writes nothing without --write. The table it writes is additive:
    school_team_link (tfrrs_school, team_id, state, level, n_athletes,
                      n_seasons, share, source)
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ! BOTH BARS ARE ABOUT ONE THING: a link is a claim about a whole team, so
#   it has to be carried by a team's worth of people.
MIN_ATHLETES = 5
MIN_SHARE = 0.60

DDL = """
CREATE TABLE IF NOT EXISTS school_team_link (
    tfrrs_school text    NOT NULL,
    team_id      bigint  NOT NULL,
    state        text,
    level        text,
    n_athletes   int     NOT NULL,
    n_seasons    int     NOT NULL,
    share        real    NOT NULL,
    source       text    NOT NULL DEFAULT 'coathlete',
    built        date    NOT NULL DEFAULT current_date,
    PRIMARY KEY (tfrrs_school))
"""


def collegeTeams(cur):
    """{team_id: (school, state)} for anet teams whose LEVEL IS COLLEGE.
    The guard: a high school team is not a candidate at all."""
    from speed_ratings_db import loadTeamLevels, printTeamLevels
    by_team, meaning, _rows = loadTeamLevels()
    college = {t for t, lv in by_team.items() if lv == "college"}
    print(f"  anet level codes: {meaning}")
    print(f"  {len(college):,} anet teams are colleges")
    if not college:
        # ⚠ SAY WHICH KIND OF NOTHING THIS IS. "No college teams" has three
        #   causes and they need three different answers; printing one line
        #   for all of them is how the last run looked like a no-op when it
        #   was a table-shape problem (UndefinedColumn on results_tf.team_slug,
        #   2026-09-17 -- speed_ratings_db.loadTeamLevels now probes for it,
        #   but a database with NEITHER the slug NOR a college_directory
        #   cannot name a college code at all and will land here).
        print("\n  ! NO anet LEVEL CODE COULD BE NAMED 'college'.")
        if not meaning:
            print("    No code could be named at all -- anet_team is empty or "
                  "has no `level`. Run scripts/anet_teams.py --unfetched "
                  "--write first.")
        else:
            print(f"    Codes that WERE named: {meaning}. A code is called "
                  f"college when its rows carry no grade AND a quarter of its "
                  f"teams' schools are known colleges -- which needs either "
                  f"results_tf.team_slug (added by "
                  f"database._migrateResultsAddTeamSlug, and only on rows "
                  f"scraped since) or the college_directory table "
                  f"(scripts/build_college_directory.py).")
        print("    Or state the codes outright and re-run, e.g.")
        print('      XCP_ANET_LEVELS="8=college,4=hs" python '
              'scripts/link_tfrrs_to_anet.py --show 60')
        # ! THE EVIDENCE, HERE, NOT A POINTER TO IT. This is the table the
        #   codes were judged from -- rows per code, the grade shares, and the
        #   share of each code's teams whose school is a known college. It is
        #   what says WHICH code should have been named, and a reader who has
        #   to go and run something else to see it mostly does not.
        if _rows:
            print("")
            printTeamLevels(meaning, _rows)
        print("")
        return {}
    cur.execute("""
        SELECT team_id, school, upper(btrim(COALESCE(state, anet_state)))
        FROM   anet_team WHERE team_id = ANY(%s)
    """, (sorted(college),))
    return {int(t): (sc, st) for t, sc, st in cur.fetchall()}


# ★ ONE PASS PER TABLE, AND THE JOIN IS (person, year). The anet leg carries
#   a team id; the tfrrs leg carries a school string. Their intersection per
#   athlete-year is the evidence.
#
# ! DISTINCT PERSON PER (string, team), NOT ROWS. A tfrrs school with one
#   prolific athlete is not a link; five athletes is.
#
# ⚠⚠ AND IT HAD NEVER ONCE RUN (owner, 2026-09-17: "it's hanging now after
#    the 2,034 teams are college"). It was not hanging -- until this morning
#    collegeTeams returned {} and main() exited before reaching here, so the
#    first time this query executed was the first time anybody waited on it.
#
#    As written it was two independent aggregates of the WHOLE table --
#    `anet` over every row on a college team, `tf` over every tfrrs row in a
#    54M-row table -- each materialised in full and then joined. The tfrrs
#    side does not depend on the teams at all, so the expensive half was
#    computed without ever being narrowed by the cheap half.
#
# ★ SO THE SMALL SIDE IS BUILT FIRST, INDEXED, AND THE BIG SIDE IS STREAMED
#   THROUGH IT. The anet leg on 2,034 teams is small and reachable by index;
#   materialise it, index (person_id, yr), ANALYZE so the planner knows it is
#   small, and the tfrrs rows then hash-join against it in one scan instead
#   of being grouped in their entirety first.
#
# ! THE COUNTS ARE UNCHANGED. The old `tf` CTE pre-grouped to distinct
#   (person, yr, school); count(DISTINCT ...) is insensitive to duplicate
#   rows, so folding that grouping into the join gives the same numbers.
#
# ! AND IT SAYS WHERE IT IS. This project has learnt the same lesson at least
#   four times (rebuild_overrides, --reground, the extraction): a silent slow
#   step is indistinguishable from a hung one, and the person waiting cannot
#   tell whether to keep waiting.
# ⚠⚠ NOT `team_id = ANY(<2,034 ids>)`. MEASURED, WITH PLANS (2026-09-17,
#    scripts/diag_link_plan.py, after the owner said "even with an index"):
#
#      table        rows       size    = ANY cost    JOIN cost   ratio
#      results      39,280,600  12 GB  42,965,052    1,701,897    25x
#      results_tf  191,308,768  72 GB  184,231,311   9,364,835    20x
#
# ★ AND THE REASON IS THE DISTINCT, NOT THE ARRAY. I guessed twice -- first a
#   missing index, then a per-row walk of the 2,034-element array -- and the
#   plan says it is neither. What the planner actually did with = ANY:
#
#      Unique
#        -> Gather Merge -> Incremental Sort   (Presorted Key: person_id)
#             -> Parallel Index Scan using idx_results_person
#                  Index Cond: (person_id IS NOT NULL)
#                  Filter: (team_id = ANY (...))
#
#   It walked the WHOLE TABLE through the person_id index -- random heap
#   access across 12 GB, and 72 GB on results_tf -- for no other reason than
#   to hand the DISTINCT its rows already sorted. `person_id IS NOT NULL` as
#   an Index Cond selects essentially everything; the team filter was applied
#   afterwards, per row, as a filter. That is where the 124 s went.
#
#   Joining a relation instead removes the temptation: the planner cannot
#   keep the sort, so it takes a Seq Scan and a HashAggregate -- sequential
#   I/O, no sort, one hash probe per row. That is the whole 20-25x.
#
# ! THE team_id INDEXES ARE NOT WHAT FIXED IT AND THE PLANNER DOES NOT USE
#   THEM HERE. Both exist now (idx_results_team, idx_results_tf_team) and
#   both plans ignore them: ~10% of rows match, which is far past the point
#   where a seq scan wins. They may earn their keep elsewhere; they earned
#   nothing here, and I should not have proposed them before reading a plan.
#
# ! AND max_parallel_workers_per_gather IS 2 ON THIS SERVER, not 0 -- the
#   earlier note claiming the scan was single-threaded was describing
#   XCP_DB_QUIET, which this job does not run under.
_TEAMS_SQL = """
    CREATE TEMP TABLE ltl_teams (team_id int PRIMARY KEY)
"""

_ANET_SQL = """
    CREATE TEMP TABLE ltl_anet AS
    SELECT DISTINCT r.person_id, substr(r.date, 1, 4)::int AS yr, r.team_id
    FROM   {table} r
    JOIN   ltl_teams t ON t.team_id = r.team_id
    WHERE  r.person_id IS NOT NULL
      AND  r.date ~ '^(19|20)[0-9][0-9]-'
      AND  (%(since)s::int IS NULL OR substr(r.date, 1, 4)::int >= %(since)s)
"""

_VOTE_SQL = """
    SELECT btrim(r.school) AS school, a.team_id,
           count(DISTINCT r.person_id) AS n_athletes,
           count(DISTINCT a.yr)        AS n_seasons
    FROM   {table} r
    JOIN   ltl_anet a ON a.person_id = r.person_id
                     AND a.yr = substr(r.date, 1, 4)::int
    WHERE  r.person_id IS NOT NULL AND r.source = 'tfrrs'
      AND  r.school IS NOT NULL AND btrim(r.school) <> ''
      AND  r.date ~ '^(19|20)[0-9][0-9]-'
      AND  (%(since)s::int IS NULL OR substr(r.date, 1, 4)::int >= %(since)s)
    GROUP  BY 1, 2
"""


def votes(cur, teams, since=None, tables=None, verbose=True):
    """{tfrrs school: {team_id: (n_athletes, n_seasons)}} over both tables."""
    import time
    out = {}
    # ! THE HashAggregate SPILLS ON results_tf AND THE PLAN SAYS SO
    #   ("Planned Partitions: 4" over 14.4M estimated groups at work_mem
    #   256MB). Raised for this job only, and only where the server lets us
    #   -- SET LOCAL dies with the transaction either way.
    try:
        cur.execute("SET LOCAL work_mem = %s",
                    (os.environ.get("XCP_LINK_WORK_MEM", "1GB"),))
    except Exception:                                         # noqa: BLE001
        pass
    # the college ids as a RELATION, built once for every table below
    cur.execute("DROP TABLE IF EXISTS ltl_teams")
    cur.execute(_TEAMS_SQL)
    cur.execute("INSERT INTO ltl_teams SELECT unnest(%s::int[])",
                (sorted(teams),))
    cur.execute("ANALYZE ltl_teams")
    for table in (tables or ("results", "results_tf")):
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = %s
                         AND column_name = 'team_id'""", (table,))
        if cur.fetchone() is None:
            if verbose:
                print(f"  {table}: no team_id column, skipped", flush=True)
            continue
        t0 = time.time()
        if verbose:
            # ⚠ AND SAY WHETHER THIS IS A SCAN (owner, 2026-09-17: "it hangs
            #   on results_tf"). It was not hanging -- NOTHING INDEXED
            #   team_id, so `team_id = ANY(<2,034 teams>)` is a sequential
            #   pass over the whole table. The previous version of this line
            #   asserted the opposite ("indexed on team_id, so this is the
            #   quick half"), which is worse than silence: it told the
            #   person waiting that a ten-minute scan was the fast part.
            cur.execute("""SELECT 1 FROM pg_indexes
                           WHERE tablename = %s AND indexdef LIKE '%%(team_id%%'""",
                        (table,))
            indexed = cur.fetchone() is not None
            cur.execute(f"SELECT reltuples::bigint FROM pg_class "
                        f"WHERE oid = '{table}'::regclass")
            approx = cur.fetchone()[0] or 0
            # ! HONEST ABOUT WHICH IT IS. Without an index this is a scan of
            #   the table -- but a HASH JOIN against 2,034 ids, which is a
            #   different thing from the per-row array walk it replaced.
            #   scripts/diag_link_plan.py prints both plans side by side.
            print(f"  {table}: collecting the college teams' athlete-years "
                  f"-- hash join against {len(teams):,} team ids, "
                  + (f"index scan on team_id" if indexed else
                     f"scanning ~{approx:,} rows (no team_id index; "
                     f"scripts/add_page_indexes.py declares one)")
                  + "...", flush=True)
        cur.execute("DROP TABLE IF EXISTS ltl_anet")
        cur.execute(_ANET_SQL.format(table=table), {"since": since})
        cur.execute("CREATE INDEX ltl_anet_idx ON ltl_anet (person_id, yr)")
        cur.execute("ANALYZE ltl_anet")
        cur.execute("SELECT count(*), count(DISTINCT person_id) FROM ltl_anet")
        n_rows, n_people = cur.fetchone()
        if verbose:
            print(f"    {n_rows:,} athlete-years over {n_people:,} people "
                  f"({time.time() - t0:.0f}s)", flush=True)
        if not n_rows:
            cur.execute("DROP TABLE IF EXISTS ltl_anet")
            continue
        t1 = time.time()
        if verbose:
            print(f"    now scanning {table} for the tfrrs rows those same "
                  f"people wrote -- ONE pass, several minutes on the big "
                  f"table, no output until it lands", flush=True)
        cur.execute(_VOTE_SQL.format(table=table), {"since": since})
        got = cur.fetchall()
        if verbose:
            print(f"    {len(got):,} (string, team) pairs "
                  f"({time.time() - t1:.0f}s)", flush=True)
        for school, team_id, n_ath, n_seas in got:
            cell = out.setdefault(school, {}).setdefault(int(team_id), [0, 0])
            cell[0] += int(n_ath)
            cell[1] = max(cell[1], int(n_seas))
        cur.execute("DROP TABLE IF EXISTS ltl_anet")
    cur.execute("DROP TABLE IF EXISTS ltl_teams")
    return out


def decide(counted, teams, min_athletes=MIN_ATHLETES, min_share=MIN_SHARE):
    """(links, rejected): a link per tfrrs string that has a clear winner.
    Pure -- the bars are the whole decision, so they are testable."""
    links, rejected = [], []
    for school, by_team in sorted(counted.items()):
        total = sum(v[0] for v in by_team.values())
        team_id, (n_ath, n_seas) = max(by_team.items(),
                                       key=lambda kv: (kv[1][0], -kv[0]))
        share = n_ath / total if total else 0.0
        anet_school, state = teams.get(team_id, (None, None))
        row = (school, team_id, state, "college", n_ath, n_seas,
               round(share, 4), anet_school, len(by_team))
        if n_ath >= min_athletes and share >= min_share:
            links.append(row)
        else:
            rejected.append(row)
    links.sort(key=lambda r: -r[4])
    rejected.sort(key=lambda r: -r[4])
    return links, rejected


def write(cur, links):
    from scrape_school_logos import ensureTable
    ensureTable(cur, DDL)
    cur.execute("DELETE FROM school_team_link WHERE source = 'coathlete'")
    cur.executemany("""
        INSERT INTO school_team_link (tfrrs_school, team_id, state, level,
                                      n_athletes, n_seasons, share, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'coathlete')
        ON CONFLICT (tfrrs_school) DO UPDATE
        SET team_id = EXCLUDED.team_id, state = EXCLUDED.state,
            level = EXCLUDED.level, n_athletes = EXCLUDED.n_athletes,
            n_seasons = EXCLUDED.n_seasons, share = EXCLUDED.share,
            built = current_date
    """, [r[:7] for r in links])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="without this, a dry run")
    ap.add_argument("--show", type=int, default=30)
    ap.add_argument("--min-athletes", type=int, default=MIN_ATHLETES)
    ap.add_argument("--min-share", type=float, default=MIN_SHARE)
    ap.add_argument("--tables", default="results,results_tf",
                    help="which result tables to gather evidence from. "
                         "`results` alone is the XC half and is much the "
                         "smaller scan -- an answer from it is a real answer, "
                         "just with less evidence behind it, so a borderline "
                         "link may fall under MIN_ATHLETES.")
    ap.add_argument("--since", type=int, default=None, metavar="YEAR",
                    help="only athlete-years from YEAR on. The whole corpus "
                         "is two full passes over 54M rows; --since 2015 is "
                         "the same answer for any team still running, in a "
                         "fraction of the time. Fewer years is less "
                         "evidence, so a link may drop below MIN_ATHLETES.")
    args = ap.parse_args()

    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        teams = collegeTeams(cur)
        if not teams:
            # ! NOT A FAILURE, AND IT IS A PIPELINE STEP NOW (10b0, before
            #   10b reads school_team_link). A database on which anet_teams
            #   has never run has no college teams to link -- a state of the
            #   data, not an error -- and the identity build falls back to
            #   the directory and the home state exactly as it did before
            #   this table existed. Exiting 1 here would put a red step in
            #   every summary of a correct run.
            print("  nothing to link: no anet team is marked college yet. "
                  "Run scripts/anet_teams.py --unfetched --write first; "
                  "school_identity falls back to the college directory and "
                  "the home-state inference until then.")
            return 0
        print(f"\n  the evidence pass: two tables, one scan each. The tfrrs "
              f"half is the slow one and says nothing while it runs."
              + (f" (--since {args.since})" if args.since else ""), flush=True)
        counted = votes(cur, teams, since=args.since,
                        tables=[t.strip() for t in args.tables.split(",")
                                if t.strip()])
        links, rejected = decide(counted, teams, args.min_athletes, args.min_share)
        print(f"\n  {len(counted):,} tfrrs school strings share an athlete-year "
              f"with an anet college team")
        print(f"  {len(links):,} link (>= {args.min_athletes} athletes and "
              f">= {args.min_share:.0%} of the string's votes); "
              f"{len(rejected):,} do not and stay their own schools\n")
        print(f"  {'tfrrs string':<34}{'team':>8} {'ST':<3} {'ath':>5} "
              f"{'sea':>4} {'share':>6}  anet's name")
        for sc, tid, st, _lv, n, ns, sh, anet_sc, _k in links[:args.show]:
            print(f"  {sc[:33]:<34}{tid:>8} {st or '--':<3} {n:>5} {ns:>4} "
                  f"{sh:>6.0%}  {anet_sc}")
        if rejected:
            print(f"\n  NOT LINKED (the remainder, kept separate):")
            for sc, tid, st, _lv, n, ns, sh, anet_sc, k in rejected[:args.show]:
                print(f"  {sc[:33]:<34}{tid:>8} {st or '--':<3} {n:>5} {ns:>4} "
                      f"{sh:>6.0%}  {k} candidate team(s)  {anet_sc}")
        if not args.write:
            print("\n  DRY RUN -- nothing written. --write to store the links.\n")
            return 0
        write(cur, links)
        conn.commit()
        print(f"\n  wrote {len(links):,} links to school_team_link.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
