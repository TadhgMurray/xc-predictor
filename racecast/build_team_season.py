"""
build_team_season.py -- rank every team by racing it.

    python racecast/build_team_season.py
    python racecast/build_team_season.py --sport XC --since 2015

Run from the PROJECT ROOT, after build_ranking_results.py, which is where
athlete_season comes from. Reads athlete_season, writes team_season.

★ ONE HYPOTHETICAL MEET PER BOARD. For each (scope, pool, sport, season) the
  eligible athlete-seasons are gathered, every team's top seven are entered,
  and the field is scored with the ordinary rules. See team_rank.py for why
  the ranking races the teams instead of averaging their ratings.

★ TWO SCOPES PER TEAM, AND THAT IS THE WHOLE STORAGE COST. A team sits in
  exactly one state, so it appears in its own state's meet and in the
  national one -- two rows per (team, pool, sport, year), never more.

⚠ POINTS ARE ONLY COMPARABLE WITHIN ONE BOARD. They are a finishing score
  against a particular field: the same squad scores differently in Ohio's
  meet than in the national one because the meet is different. Read the RANK
  across boards and the points within one. The board says so.

⚠ AND THE PER-STATE RATING OFFSET HITS TEAMS HARDER THAN ATHLETES. The
  national boards carry an unresolved offset of up to ~9 points between
  states (see rankings.py). For one athlete that is one athlete's error; for
  a team it is the same regional bias on all five scorers at once, pushing
  the same way rather than cancelling. State boards are unaffected -- a
  constant shift cannot reorder a field drawn from one state.
"""

import io
import sys
import time
import argparse

sys.path.insert(0, "scripts")
sys.path.insert(0, "racecast")
from database import getConn
from team_rank import rankTeams, raceStored, SQUAD

# ★ THE ESCAPER, IMPORTED, NOT REWRITTEN. COPY's TEXT format treats tab,
#   newline, carriage return and backslash as structure, and school names are
#   scraped free text that contains all four -- "Chicago Lakeside Rabbits"
#   carries a literal tab, which arrives as an extra column and aborts the
#   whole COPY. build_ranking_results already solved this; its copyField
#   docstring says in as many words that school names contain all three
#   separators. A second hand-rolled version of it is how the same bug gets
#   fixed once and shipped twice.
from build_ranking_results import copyField
from dbfast import dictRows, tuneSession

# ! THE SAME 51 CODES rankings.py USES, and for the same reason: there is no
#   nation column anywhere, state is the only geography stored, and a null
#   test would keep exactly the foreign meets it is meant to exclude (a
#   Canadian meet reads QC/ON/BC). Imported rather than retyped.
from rankings import US_STATES, POOLS

# ★ THE CEILING THAT KEEPS A PROFESSIONAL CLUB OFF THE HIGH SCHOOL BOARD.
#   Oregon Track Club topped the all-time hs_m board on a top-five average of
#   180, in years when the best real high school squad reached about 140 --
#   a pro club that pro_flag had not seen at enough seeded meets, published
#   as the best team in history. See pool_ceiling for why one global rail
#   cannot catch it and why the ceilings RISE as the pool gets weaker.
from pool_ceiling import withinPool, POOL_CEILING

# ⚠ A ONE-RACE SEASON IS A PERFORMANCE WEARING A SEASON'S NAME, and its mean
#   is that one race's noise -- about 3.3%, four rating points. Two is the
#   floor grade_sanity's rule 7 already uses to call a grade corroborated, so
#   the site draws its line there.
#
#   It is deliberately NOT the athlete board's min_races (8 XC, 20 TF). Those
#   ask "is this a real campaign for one runner"; this asks "should this
#   runner be among their team's seven", and a transfer with three races is
#   still on the team.
MIN_RACES = 2

# ★ ONE SHAPE, WRITTEN ONCE, USED FOR BOTH THE REAL TABLE AND THE SHADOW.
#   The shadow used to be built with `LIKE team_season INCLUDING ALL`, which
#   copies whatever shape is already in the database -- so adding a column
#   here needed a hand-written ALTER beside it, and forgetting one meant a
#   COPY naming a column the shadow did not have. Building both from the same
#   text makes the schema in this file the only schema there is.
_DDL = """
CREATE TABLE IF NOT EXISTS {name} (
    -- ★ WHICH MEET THIS ROW'S rank AND points COME FROM.
    --     'season'   the team's own year: every squad that raced in 2025,
    --                scored against each other. One winner per season.
    --     'alltime'  every squad of every season in ONE field. One winner,
    --                full stop -- and the only board that can answer "who
    --                was the best team" without picking a year first.
    --
    --   ⚠ BOTH ARE TRUE AND NEITHER REPLACES THE OTHER. Mixing them in one
    --     query is what put thirty first places on the same screen, so every
    --     query names a span; teams.py picks which.
    span         text    NOT NULL DEFAULT 'season',
    scope        text    NOT NULL,
    school       text    NOT NULL,
    state        text,
    pool         text    NOT NULL,
    sport        text    NOT NULL,
    year         int     NOT NULL,
    rank         int     NOT NULL,
    points       int     NOT NULL,
    n_athletes   int     NOT NULL,
    top5_mean    real,
    fifth_rating real,
    best_rating  real,
    -- ★ THE SEVEN RATINGS THAT RACED, so the board can be raced AGAIN
    --   against a different field. The site does that whenever a filter
    --   selects teams from more than one season: three seasons of stored
    --   boards hold three first places, and the only honest way to get one
    --   is to hold another meet. See team_rank.raceStored and teams.py.
    --   Seven because an eighth runner cannot affect any score.
    ratings      real[],
    -- ★ THE TEAM'S OWN UNITS, STAMPED (owner, 2026-09-08: "DI school in
    --   DIII filter: Washington WA ... NCAA DI"). The team board's unit
    --   filter used to resolve a division to a list of school NAMES and
    --   match on the name alone, because this table had no unit columns.
    --   Names are shared: "Washington" is a DI school in WA and a DIII
    --   one in MO, so a DIII filter pulled the DI team in.
    --
    -- ! athlete_season ALREADY CARRIES THESE, stamped per (school, state)
    --   at build time -- which is how the ATHLETE boards filter exactly
    --   while this one guessed. They ride through _SOURCE_SQL and land
    --   here, so a division filter is now the same column comparison on
    --   both boards and the two cannot disagree.
    division     text,
    region       text,
    conference   text,
    league       text,
    state_div    text,
    section_div  text,
    "class"      text,
    area         text,
    section      text,
    PRIMARY KEY (span, scope, school, state, pool, sport, year)
);
"""

# The unit columns a team carries, in one place: the DDL above, the
# SELECT below, _COLUMNS, and the row written by toRows all read this.
UNIT_COLS = ("division", "region", "conference", "league",
             "state_div", "section_div", "class", "area", "section")

_COLUMNS = ("span", "scope", "school", "state", "pool", "sport", "year",
            "rank", "points", "n_athletes", "top5_mean", "fifth_rating",
            "best_rating", "ratings") + UNIT_COLS

# ! ORDERED BY THE GROUP so one pass can be cut into boards without holding
#   the whole table. mean_rating is the athlete's season average -- the same
#   number the athlete board ranks, not a second opinion about the season.
_SOURCE_SQL = """
    SELECT sport, year, pool,
           -- ! NORMALISED. A team is keyed (school, state), so 'Ca' and 'CA'
           --   would be two teams sharing a name and splitting one squad --
           --   and half a squad cannot field five scorers.
           upper(btrim(state)) AS state,
           btrim(school)       AS school,
           person_id,
           mean_rating AS rating,
           -- the units athlete_season already carries, stamped per
           -- (school, state); every athlete of one team shares them
           "division",
           "region",
           "conference",
           "league",
           "state_div",
           "section_div",
           "class",
           "area",
           "section"
    FROM   athlete_season
    WHERE  mean_rating IS NOT NULL
      AND  n_races >= %(min_races)s
      AND  pool = ANY(%(pools)s)
      AND  state = ANY(%(states)s)
      AND  year >= %(since)s
      AND  (%(sport)s = 'both' OR sport = %(sport)s)
    ORDER  BY sport, year, pool
"""


def eligibleRows(rows, stats):
    """Pass rows through, dropping the ones no pool could have produced.

    ⚠ THE GRAIN IS THE ATHLETE, NOT THE TEAM, and that is the whole reason
      this works as a filter rather than as a post-hoc deletion. Drop the
      implausible RUNNERS and a club made entirely of them loses every
      entrant and stops being a team at all -- scoreRows lifts out anything
      that cannot field five. A real school with one mis-pooled transfer
      loses that one runner and still scores, on the six who are really
      theirs.

    ! COUNTED PER POOL AND PRINTED. A ceiling is a guess until somebody
      measures it, and a guess that is quietly deleting four thousand real
      high school seasons looks exactly like a guess that is working.
    """
    for row in rows:
        if withinPool(row["pool"], row["rating"]):
            yield row
        else:
            stats["dropped"][row["pool"]] = (
                stats["dropped"].get(row["pool"], 0) + 1)


def countingRows(rows, stats):
    """Pass rows through, counting them.

    ★ SO THE BUILD CAN SAY WHAT IT READ. This finished suspiciously fast on
      its first real run, and "fast" has two explanations that look identical
      from outside: the work is genuinely small, or a filter is throwing most
      of the corpus away before it starts. A row count settles it -- compare
      it against `SELECT count(*) FROM athlete_season` and the gap IS the
      filtering.
    """
    for row in rows:
        stats["read"] += 1
        stats["years"].add(row["year"])
        yield row


# ★ THE TEAM'S OWN STATE, NOT THE ONE IT RACED IN (owner, 2026-09-08).
#   athlete_season.state is the MODE of a season's rows, and a row's state
#   is where the RESULT happened (build_ranking_results: "a row's state is
#   where the RACE was"). A college races away most weekends, so the mode
#   is a travel state -- and since team_rank.teamKey keys a squad
#   (school, state), one team became several. The college board carried
#   BYU at ranks 5, 7 and 8 as WI, OK and FL, five and six athletes each,
#   instead of one BYU with seventeen; Air Force read OK, Oregon CA,
#   Furman FL.
#
# ⚠ WHICH BREAKS THIS MODULE'S OWN STATED INVARIANT, at the top of the
#   file: "A team sits in exactly one state, so it appears in its own
#   state's meet and in the national one -- two rows per (team, pool,
#   sport, year), never more." Three BYUs is that contract failing, not a
#   design choice, and it moves the ranking: the split squads score as
#   five- and six-man teams against full ones.
#
# ! THE ANSWER ALREADY EXISTED AND NOTHING ASKED IT. school_identity
#   clusters each school name by its athletes' home states, merges the
#   clusters that race each other (one BYU) and keeps apart the ones that
#   never do (Kingston WA and Kingston MO stay two schools).
#   school_identity.primaryState says so in its own docstring -- "The
#   identity table already answers the question; nothing was asking it" --
#   and it was written for the board's LABELS while the KEY went on using
#   the venue. teamState is that same verdict, so the state a team is
#   keyed by and the state it is shown under cannot disagree.
def resolvedStates(rows, stats):
    """Each row's state replaced by its school's own. Counts the moves."""
    from school_identity import teamState
    for row in rows:
        was = row.get("state")
        now = teamState(row.get("school"), row.get("pool"), was)
        if now != was:
            stats["restated"] += 1
            row["state"] = now
        yield row


def boards(rows):
    """Stream of athlete-seasons -> (scope, pool, sport, year, ranked teams).

    Pure, so the ranking half of this build is testable without a corpus.
    `rows` must arrive ordered by (sport, year, pool); the query does that,
    and grouping without it would hold every board in memory at once.
    """
    group, key = [], None
    for row in rows:
        this = (row["sport"], row["year"], row["pool"])
        if key is not None and this != key:
            yield from _boardsFor(key, group)
            group = []
        key = this
        group.append(row)
    if group:
        yield from _boardsFor(key, group)


def _boardsFor(key, athletes):
    """The national meet and each state's meet, for one (sport, year, pool)."""
    sport, year, pool = key

    yield ("usa", pool, sport, year, rankTeams(athletes))

    by_state = {}
    for a in athletes:
        if a.get("state"):
            by_state.setdefault(a["state"], []).append(a)
    for state, members in sorted(by_state.items()):
        yield (state, pool, sport, year, rankTeams(members))


def arrayLiteral(values):
    """[1.5, 2.5] -> '{1.5,2.5}', the TEXT form COPY wants for a real[].

    ! NO QUOTING NEEDED AND NONE DONE. These are floats formatted by repr, so
      they cannot contain a brace, a comma, a quote or a backslash -- the
      four things an array literal would need escaping for. The same is
      emphatically NOT true of the school column beside it, which is why
      every field still goes through copyField afterwards.
    """
    return "{" + ",".join(repr(round(float(v), 2)) for v in values) + "}"


# ! TWO SHAPES, ONE ANSWER. Pass one's teams come from rankTeams, which
#   nests the squad's units under "units"; pass two's come from raceStored,
#   which carries the stored ROW through with {**row} and so has them flat.
#   Reading only one shape leaves the other board's units NULL -- and the
#   all-time board is the one the site opens on, so that half would answer
#   a division filter with nothing.
def _unitTuple(team):
    src = team.get("units")
    if not isinstance(src, dict):
        src = team
    return tuple(src.get(c) for c in UNIT_COLS)


def toRows(board):
    scope, pool, sport, year, teams = board
    for t in teams:
        yield (("season", scope, t["school"], t["state"], pool, sport, year,
                t["rank"], t["points"], t["n_athletes"],
                t["top5_mean"], t["fifth_rating"], t["best_rating"],
                arrayLiteral(t["ratings"]))
               + _unitTuple(t))


# ! READ BACK FROM THE SHADOW, NOT ACCUMULATED IN MEMORY DURING PASS ONE.
#   Keeping every board of every season around to race at the end means
#   holding the whole table; reading it back one group at a time holds the
#   largest group. The rows are already written and already correct, so the
#   second pass costs one sequential scan.
_ALLTIME_SQL = """
    SELECT scope, pool, sport, school, state, year, ratings, n_athletes,
           top5_mean, fifth_rating, best_rating,
           -- ! THE UNITS RIDE INTO PASS TWO AS WELL, or the all-time board
           --   answers a division filter with nothing while the season
           --   boards answer it correctly -- and the all-time board is the
           --   one the site opens on.
           "division",
           "region",
           "conference",
           "league",
           "state_div",
           "section_div",
           "class",
           "area",
           "section"
    FROM   team_season_new
    WHERE  span = 'season'
    ORDER  BY scope, pool, sport
"""


def alltimeBoards(rows):
    """Season rows -> (scope, pool, sport, raced teams), one board per group.

    ★ EVERY SEASON IN ONE FIELD. A 2003 squad and a 2025 squad line up
      together, which is a comparison that means something only because the
      ratings are era-adjusted: 100 is the pool mean in either year.

    ⚠ ONE ROW PER TEAM-SEASON, so a school that was good for thirty years
      enters thirty times -- which is right. Its 2011 squad and its 2012
      squad are different teams and the board is a ranking of squads, not of
      programmes.

    `rows` must arrive ordered by (scope, pool, sport); the query does that,
    and grouping without it would hold the whole table at once.
    """
    group, key = [], None
    for row in rows:
        this = (row["scope"], row["pool"], row["sport"])
        if key is not None and this != key:
            yield key + (raceStored(group),)
            group = []
        key = this
        group.append(row)
    if group:
        yield key + (raceStored(group),)


def toAlltimeRows(board):
    scope, pool, sport, teams = board
    for t in teams:
        yield (("alltime", scope, t["school"], t["state"], pool, sport,
                t["year"], t["rank"], t["points"], t["n_athletes"],
                t["top5_mean"], t["fifth_rating"], t["best_rating"],
                arrayLiteral(t["ratings"]))
               + _unitTuple(t))


def build(conn, sport, since):
    """Fill team_season from athlete_season, replacing what is there.

    ⚠ SHADOW TABLE AND ONE SWAP, like build_ranking_results. A TRUNCATE plus
      a long insert serves an empty board for the length of the build, and a
      crash halfway serves half a board with no error anywhere.
    """
    import psycopg2.extras
    started = time.time()
    tuneSession(conn)
    with conn.cursor() as cur:
        # The real table only has to EXIST, for the rename at the end to
        # have something to rename; the shadow is where this run's rows go
        # and it is built to this file's shape, not to the old table's.
        cur.execute(_DDL.format(name="team_season"))
        cur.execute("DROP TABLE IF EXISTS team_season_new")
        cur.execute(_DDL.format(name="team_season_new"))
    conn.commit()

    params = {"min_races": MIN_RACES, "pools": sorted(POOLS),
              "states": list(US_STATES), "since": since, "sport": sport}

    # ★ A PLAIN CURSOR, TURNED INTO DICTS BY dbfast.dictRows. team_rank takes
    #   dicts and its self-check is written in dicts, so the rows still have
    #   to BE dicts -- but RealDictCursor builds an ordered-dict subclass per
    #   row and dict(zip(...)) does not. Measured over 2M rows: 11.8s against
    #   4.3s, and this build reads 10.7M of them.
    read = conn.cursor("team_src")
    read.itersize = 50000
    read.execute(_SOURCE_SQL, params)
    read_rows = dictRows(read)

    def flush(buf):
        # ! copy_expert WITH AN EXPLICIT COLUMN LIST, matching the rankings
        #   builder. copy_from's `columns=` does the same thing, but keeping
        #   one form means one place to look when a COPY misbehaves.
        buf.seek(0)
        with conn.cursor() as cur:
            cur.copy_expert(
                f"COPY team_season_new ({', '.join(_COLUMNS)}) FROM STDIN", buf)

    stats = {"read": 0, "years": set(), "teams": 0, "dropped": {},
             "restated": 0}
    # the school -> state map behind resolvedStates; a missing
    # school_identity table loads empty and every row keeps its own state
    from school_identity import loadLabels
    loadLabels(getConn)
    buf, n_rows, n_boards = io.StringIO(), 0, 0
    for board in boards(eligibleRows(
            resolvedStates(countingRows(read_rows, stats), stats), stats)):
        n_boards += 1
        stats["teams"] += len(board[4])
        for row in toRows(board):
            # map, not a generator expression: 401k rows/s against 341k,
            # measured over 200k rows. Same bytes out, one less frame per row.
            buf.write("\t".join(map(copyField, row)))
            buf.write("\n")
            n_rows += 1
        if buf.tell() > (8 << 20):
            flush(buf)
            buf = io.StringIO()
    read.close()
    if buf.tell():
        flush(buf)
    conn.commit()

    # ---- pass two: every season in one field -------------------------- #
    # ★ THE BOARD THE SITE OPENS ON. Without it "all seasons" can only be
    #   served as thirty stacked per-season boards, each with its own first
    #   place -- and no live race can fix that at national scale, because the
    #   field is a quarter of a million squads. Precomputing it is the only
    #   way the front page of this board has a #1 on it.
    at_started = time.time()
    at_read = conn.cursor("team_alltime")
    at_read.itersize = 50000
    at_read.execute(_ALLTIME_SQL)

    buf, at_rows, at_boards = io.StringIO(), 0, 0
    for board in alltimeBoards(dictRows(at_read)):
        at_boards += 1
        for row in toAlltimeRows(board):
            buf.write("\t".join(map(copyField, row)))
            buf.write("\n")
            at_rows += 1
        if buf.tell() > (8 << 20):
            flush(buf)
            buf = io.StringIO()
    at_read.close()
    if buf.tell():
        flush(buf)
    at_took = time.time() - at_started

    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS team_season_old")
        cur.execute("ALTER TABLE team_season RENAME TO team_season_old")
        cur.execute("ALTER TABLE team_season_new RENAME TO team_season")
        cur.execute("DROP TABLE team_season_old")
        cur.execute("ANALYZE team_season")
    conn.commit()
    # Named years, not `span` -- that word means the season/alltime column now.
    years = (f"{min(stats['years'])}-{max(stats['years'])}"
             if stats["years"] else "none")
    print(f"  read      {stats['read']:,} athlete-seasons "
          f"(n_races >= {MIN_RACES}, US states, rankable pools)")
    # ! LOUD, because a zero here is the failure mode. resolvedStates
    #   degrades silently when school_identity is missing or empty -- every
    #   row keeps its venue state and the teams split again -- and that is
    #   exactly how this bug survived: the identity table existed and the
    #   key never read it. A run that restates nothing has not fixed
    #   anything, whatever the board looks like.
    print(f"  restated  {stats['restated']:,} athlete-seasons moved to "
          f"their school's own state (0 means school_identity is missing "
          f"or empty -- run 10b_school_ids)")
    print(f"  seasons   {len(stats['years'])} ({years})")
    print(f"  boards    {n_boards:,}  (one national + one per state, "
          f"per pool/sport/season)")
    print(f"  teams     {stats['teams']:,} ranked, {n_rows:,} rows written")
    n_dropped = sum(stats["dropped"].values())
    if n_dropped:
        detail = ", ".join(f"{pool} {n:,} (>{POOL_CEILING.get(pool, '?')})"
                           for pool, n in sorted(stats["dropped"].items()))
        print(f"  ceiling   {n_dropped:,} athlete-seasons dropped as "
              f"implausible for their pool -- {detail}")
        print("            (see pool_ceiling.py; run audit_pool_ceilings.py "
              "before trusting these numbers)")
    else:
        print("  ceiling   0 athlete-seasons dropped -- either the pools are "
              "clean or the ceilings are too high")
    print(f"  alltime   {at_boards:,} boards, {at_rows:,} rows "
          f"({at_took:.0f}s) -- every season of a pool in one field")
    print(f"  took      {time.time() - started:.0f}s")
    # ⚠ COMPARE `read` WITH `SELECT count(*) FROM athlete_season`. A large gap
    #   is the filters doing their job -- or doing too much of it. The state
    #   list is the one to suspect: it is an explicit 51-code membership test,
    #   so an athlete-season whose state is null or oddly cased is dropped,
    #   and dropping enough of a school's runners drops the team itself below
    #   the five it needs to score.
    print("  (compare `read` against SELECT count(*) FROM athlete_season -- "
          "the gap is the filters)")


def main():
    ap = argparse.ArgumentParser(description="Rank teams by racing them. "
                                             "Run after build_ranking_results.")
    ap.add_argument("--sport", choices=["XC", "TF", "both"], default="both")
    ap.add_argument("--since", type=int, default=1990,
                    help="earliest SEASON year to rank")
    args = ap.parse_args()
    with getConn() as conn:
        build(conn, args.sport, args.since)


if __name__ == "__main__":
    main()
