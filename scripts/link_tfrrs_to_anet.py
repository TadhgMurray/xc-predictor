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
def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    got = cur.fetchone()
    return bool(got[0] if not isinstance(got, dict) else list(got.values())[0])


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
    # ⚠⚠ anet_state FIRST, AND THE DRY RUN IS WHY (2026-09-17). `state` on
    #    anet_team is OUR guess -- anet_teams.storeTeam writes the queue's
    #    (school, state) pair there, inferred from where the athletes RACE --
    #    while `anet_state` is team["State"], where the school actually is.
    #    Preferring ours produced: Cornell NC, Ithaca WI, Tiffin IA, Hartnell
    #    TX, Cerritos AZ, Iowa Central CC IN, Pima (AZ) CC as WA. Every one a
    #    travel state, and every one of them would then have been written
    #    into school_team_link.state -- which build_school_identity uses as
    #    the CLUSTER's state and anet_teams matches the crest against. A
    #    wrong state here mints "Cornell (NC)".
    cur.execute("""
        SELECT team_id, school, upper(btrim(COALESCE(anet_state, state)))
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


# ⚠⚠ A SHARE BAR PUNISHES THE OLD PROGRAMMES (owner, 2026-09-18: "MIN_SHARE
#    = 0.60 kind of messes with it especially with older teams bcs tfrrs has a
#    ton of older college meets anet doesn't have").
#
#    `share` was n_ath / TOTAL votes for the string, so every transfer, every
#    mis-merged person and every guest dilutes it. For an era anet barely
#    covers there are only a handful of shared athletes to begin with, and
#    then a 4-1-1-1 split -- the right team beating each rival four to one --
#    scores 0.57 and is REJECTED. The bar asked "does this team own most of a
#    noisy field", when the question is "is this team clearly the answer".
#
# ★ SO: MARGIN OVER THE RUNNER-UP, not share of the field. 4-1-1-1 is a 4x
#   margin and obviously right; 4-3 is a 1.3x margin and obviously not. Share
#   is still computed and printed, because it is informative -- it is just no
#   longer the gate.
#
# ★★ AND THE NAME IS A SECOND, INDEPENDENT WITNESS (owner: "link with anet as
#    usual ... definitely by school"). Where the tfrrs string and the anet
#    team's own name are the same name -- decoration aside, by
#    school_name.relation, the module whose whole point is that judgement --
#    two unrelated signals agree, and demanding five athletes on top is asking
#    a thin era for evidence it cannot have. Those need MIN_ATHLETES_NAMED.
#
# ! A NAME MATCH ALONE IS STILL NOT ENOUGH, and that is deliberate. "Oregon"
#   names three schools; the athletes are what say WHICH. So the named tier
#   lowers the athlete floor, never removes it, and still wants a margin.
MARGIN = 3.0                # blind tier: the winner beats the runner-up 3x
MARGIN_NAMED = 2.0          # ... 2x when the names already agree
# ⚠ AND WHEN IN DOUBT, SEPARATE (owner, 2026-09-18: "I'd prefer to separate
#   more than over merge so just understand that"). A link is a MERGE, so
#   these floors are the place that preference bites. They are one higher than
#   the evidence strictly needs: an exact name plus three agreeing athletes
#   with a 2x margin is already a strong claim, and the old-programme case
#   this was loosened for (4-1-1-1) clears three comfortably. Two would also
#   have worked and is not worth the schools it would wrongly join.
MIN_ATHLETES_NAMED = 3    # the anet team's name IS this name
MIN_ATHLETES_PREFIX = 4   # ... is a word-prefix of it


# ⚠⚠ "Williams" vs "Williams College" IS A PREFIX, NOT "same", and that is
#    deliberate upstream: school_name._SUFFIX_NOISE refuses to strip level
#    words because Adrian College and Adrian Middle School are one string
#    apart. But the prefix case is this linker's MAIN case -- anet's rows say
#    "Williams" and tfrrs says "Williams College" -- so it cannot be thrown
#    away either.
#
# ★ SO THREE STRENGTHS, NOT TWO. An exact name is the strongest corroboration
#   and earns the lowest athlete floor. A prefix is real but weaker, because
#   "Oregon" is a prefix of "Oregon Episcopal" and those are two schools, so
#   it earns a floor in between. No name relation at all falls back to the
#   athletes alone. In every tier the athletes still have to agree AND out-vote
#   the runner-up; the name only ever buys a lower floor.
def _nameRelation(tfrrs_school, anet_school):
    """'same', 'prefix', or None. Falls back to None -- never a match -- if
    school_name cannot be imported, so a missing module only makes this
    stricter."""
    if not (tfrrs_school and anet_school):
        return None
    try:
        from school_name import isPrefixOf, nameKey, relation
    except Exception:                                 # noqa: BLE001
        return None
    if relation(tfrrs_school, anet_school) == "same":
        return "same"
    a, b = nameKey(tfrrs_school), nameKey(anet_school)
    if a and a == b:
        return "same"
    if isPrefixOf(tfrrs_school, anet_school) or isPrefixOf(anet_school,
                                                           tfrrs_school):
        return "prefix"
    return None


def decide(counted, teams, min_athletes=MIN_ATHLETES, min_share=None,
           margin=MARGIN, margin_named=MARGIN_NAMED,
           min_athletes_named=MIN_ATHLETES_NAMED,
           min_athletes_prefix=MIN_ATHLETES_PREFIX):
    """(links, rejected): a link per tfrrs string that has a clear winner.
    Pure -- the bars are the whole decision, so they are testable.

    ! min_share IS ACCEPTED AND IGNORED unless given. It was the gate and is
      now only a floor a caller can re-impose; passing it back is how the old
      behaviour is reproduced for comparison.
    """
    links, rejected = [], []
    for school, by_team in sorted(counted.items()):
        total = sum(v[0] for v in by_team.values())
        ranked = sorted(by_team.items(), key=lambda kv: (-kv[1][0], kv[0]))
        team_id, (n_ath, n_seas) = ranked[0]
        runner = ranked[1][1][0] if len(ranked) > 1 else 0
        share = n_ath / total if total else 0.0
        # no rival at all is an unbounded margin, not a division by zero
        ratio = (float("inf") if runner == 0 else n_ath / float(runner))
        anet_school, state = teams.get(team_id, (None, None))
        rel = _nameRelation(school, anet_school)
        floor = {"same": min_athletes_named,
                 "prefix": min_athletes_prefix}.get(rel, min_athletes)
        want = margin_named if rel else margin
        ok = (n_ath >= floor and ratio >= want
              and (min_share is None or share >= min_share))
        why = ("" if ok else
               f"{n_ath} athletes < {floor}" if n_ath < floor else
               # ! THREE DECIMALS, BECAUSE ONE PRODUCED A CONTRADICTION.
               #   The table prints round(ratio, 2) at .1f and this string
               #   printed the same number at .1f from the raw value, so
               #   Johnson & Wales came out as "1.9x ... margin 2.0x < 2.0x"
               #   -- one row asserting a number is both under and equal to
               #   the bar. It was never a comparison bug; it was 1.95 shown
               #   two ways. A rejection has to show enough digits to be
               #   believed.
               f"margin {ratio:.3f}x < {want}x" if ratio < want else
               f"share {share:.2f} < {min_share}")
        row = (school, team_id, state, "college", n_ath, n_seas,
               round(share, 4), anet_school, len(by_team),
               ("name+athletes" if rel == "same" else
                "prefix+athletes" if rel == "prefix" else "athletes"),
               (None if ratio == float("inf") else round(ratio, 2)), why)
        (links if ok else rejected).append(row)
    links.sort(key=lambda r: -r[4])
    rejected.sort(key=lambda r: -r[4])
    return links, rejected


# ★★ THE NUMBER THAT SAYS WHETHER THIS IS THIN (owner, 2026-09-18, on the
#    team-id rekey). The census counted school NAMES -- 4,658 bridged, against
#    614,844 names that never carry a team id -- and that reads like near-total
#    failure. It is the wrong denominator. tfrrs names are mostly one-off
#    spellings and roster statuses on a handful of rows each, while the few
#    hundred real college programmes carry nearly all the rows.
#
# ★ SO COUNT ROWS, NOT NAMES. What matters for keying the site on team_id is
#   what share of tfrrs RESULT ROWS can be given a team, and that is the only
#   figure that should decide how much work the unresolved remainder deserves.
def coverage(cur, tables=("results", "results_tf")):
    """[(table, tfrrs_rows, rows_a_link_covers, pct)] -- how much of the tfrrs
    corpus school_team_link can actually place."""
    out = []
    if not _tableExists(cur, "school_team_link"):
        return out
    for table in tables:
        cur.execute(f"""
            SELECT count(*),
                   count(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM school_team_link l
                       WHERE  l.tfrrs_school = r.school))
            FROM   {table} r
            WHERE  r.source = 'tfrrs' AND r.school IS NOT NULL
        """)
        tot, got = cur.fetchone()
        out.append((table, tot, got, (100.0 * got / tot) if tot else 0.0))
    return out


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
    ap.add_argument("--why", action="append", default=[], metavar="SUBSTR",
                    help="explain one tfrrs school string's fate: every anet "
                         "team its athletes vote for, that team's level, and "
                         "which gate rejected the link. Repeatable. "
                         "⚠ A STRING WITH NO CANDIDATES AT ALL is the common "
                         "case and it is NOT a threshold problem -- it means "
                         "no anet team on a college-named level shares an "
                         "athlete with it, usually because anet has no college "
                         "team for that school. The linker's ceiling is the "
                         "size of that set (2,034 teams on code 8 when this "
                         "was written, of which 1,943 are linked), so a miss "
                         "is far more often missing anet coverage than a bar "
                         "set too high.")
    ap.add_argument("--write", action="store_true", help="without this, a dry run")
    ap.add_argument("--show", type=int, default=30)
    ap.add_argument("--min-athletes", type=int, default=MIN_ATHLETES)
    # ⚠ NO LONGER A GATE. Left as an OPT-IN floor so the old behaviour can be
    #   reproduced for comparison -- see the comment above decide().
    ap.add_argument("--min-share", type=float, default=None,
                    help="re-impose the old share floor as an extra bar "
                         "(pre-2026-09-18 behaviour; it rejected old "
                         "programmes whose winner led 4-1-1-1)")
    ap.add_argument("--margin", type=float, default=MARGIN,
                    help="how many times the runner-up the winner must beat "
                         "when only the athletes agree")
    ap.add_argument("--margin-named", type=float, default=MARGIN_NAMED,
                    help="... and when the anet team's own name is the same "
                         "name, where two independent signals already agree")
    ap.add_argument("--min-athletes-prefix", type=int,
                    default=MIN_ATHLETES_PREFIX,
                    help="athlete floor when the anet name is a word-PREFIX "
                         "of the tfrrs name (Williams / Williams College). "
                         "Higher than the exact tier because Oregon is a "
                         "prefix of Oregon Episcopal.")
    ap.add_argument("--min-athletes-named", type=int,
                    default=MIN_ATHLETES_NAMED,
                    help="athlete floor for that named tier. Never zero: "
                         "\"Oregon\" names three schools and the athletes are "
                         "what say which.")
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
        links, rejected = decide(counted, teams, args.min_athletes,
                                 args.min_share, args.margin,
                                 args.margin_named, args.min_athletes_named,
                                 args.min_athletes_prefix)
        # ★★ --why: ONE STRING'S FATE, SPELLED OUT. Asked of Georgetown, the
        #    question was never "which bar rejected it" but "does anet have a
        #    Georgetown college team at all" -- and the two look identical in
        #    the summary, so this separates them.
        if args.why:
            print(f"\n  === why, per named string ===")
            low = {k.lower(): k for k in counted}
            for pat in args.why:
                hits = [orig for lk, orig in low.items() if pat.lower() in lk]
                if not hits:
                    print(f"\n  '{pat}': NO tfrrs string containing it shares "
                          f"an athlete-year with any anet COLLEGE team.")
                    print(f"      That is not a threshold: the string never "
                          f"reached a vote. Either anet has no college team\n"
                          f"      for the school, or its team sits on a level "
                          f"code that loadTeamLevels did not name 'college'.\n"
                          f"      Check with: scripts/anet_teams.py --school "
                          f"'{pat}'")
                    continue
                for name in sorted(hits):
                    by_team = counted[name]
                    tot = sum(v[0] for v in by_team.values()) or 1
                    rank = sorted(by_team.items(),
                                  key=lambda kv: (-kv[1][0], kv[0]))
                    print(f"\n  '{name}' -- {len(by_team)} candidate team(s), "
                          f"{tot} athlete votes")
                    print(f"      {'team':>8} {'votes':>6} {'share':>6} "
                          f"{'seasons':>8}  anet name / state")
                    for tid, (n_ath, n_seas) in rank[:8]:
                        a_name, a_state = teams.get(tid, (None, None))
                        print(f"      {tid:>8} {n_ath:>6} "
                              f"{n_ath / tot:>5.0%} {n_seas:>8}  "
                              f"{str(a_name)[:34]} / {a_state}")
                    verdict = [r for r in links if r[0] == name]
                    if verdict:
                        r = verdict[0]
                        print(f"      -> LINKED to {r[1]} ({r[7]}) by {r[9]}, "
                              f"margin {r[10]}x")
                    else:
                        r = [x for x in rejected if x[0] == name]
                        print(f"      -> NOT linked: "
                              + (r[0][11] if r else "no candidate cleared"))

        # ! BEFORE THE DETAIL, because it is the figure that decides whether
        #   the unresolved remainder is a footnote or the main event.
        for table, tot, got, pct in coverage(cur):
            print(f"  {table}: {got:,} of {tot:,} tfrrs rows "
                  f"({pct:.1f}%) already have a linkable school string")
        print(f"\n  {len(counted):,} tfrrs school strings share an athlete-year "
              f"with an anet college team")
        n_same = sum(1 for r in links if r[9] == "name+athletes")
        n_pref = sum(1 for r in links if r[9] == "prefix+athletes")
        print(f"  {len(links):,} link: {n_same:,} exact name "
              f"(>= {args.min_athletes_named} athletes), "
              f"{n_pref:,} name-prefix (>= {args.min_athletes_prefix}), "
              f"{len(links) - n_same - n_pref:,} athletes alone "
              f"(>= {args.min_athletes}); "
              f"{len(rejected):,} stay their own schools")
        # ! MARGIN IS THE GATE NOW, share is printed because it is
        #   informative. See the comment above decide().
        if args.min_share is not None:
            print(f"  (--min-share {args.min_share} re-imposed as an extra "
                  f"floor, the pre-2026-09-18 behaviour)")
        print(f"\n  {'tfrrs string':<34}{'team':>8} {'ST':<3} {'ath':>5} "
              f"{'sea':>4} {'share':>6} {'margin':>7}  {'basis':<14} anet's name")
        for r in links[:args.show]:
            sc, tid, st, n, ns, sh, anet_sc = r[0], r[1], r[2], r[4], r[5], r[6], r[7]
            mg = "inf" if r[10] is None else f"{r[10]:.1f}x"
            print(f"  {sc[:33]:<34}{tid:>8} {st or '--':<3} {n:>5} {ns:>4} "
                  f"{sh:>6.0%} {mg:>7}  {r[9]:<14} {anet_sc}")
        if rejected:
            print(f"\n  NOT LINKED (the remainder, kept separate) -- with the "
                  f"bar each one missed:")
            for r in rejected[:args.show]:
                sc, tid, st, n, ns, sh, anet_sc, k = (r[0], r[1], r[2], r[4],
                                                      r[5], r[6], r[7], r[8])
                mg = "inf" if r[10] is None else f"{r[10]:.1f}x"
                print(f"  {sc[:33]:<34}{tid:>8} {st or '--':<3} {n:>5} {ns:>4} "
                      f"{sh:>6.0%} {mg:>7}  {r[11]:<24} {k} cand.  {anet_sc}")
        if not args.write:
            print("\n  DRY RUN -- nothing written. --write to store the links.\n")
            return 0
        write(cur, links)
        conn.commit()
        print(f"\n  wrote {len(links):,} links to school_team_link.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
