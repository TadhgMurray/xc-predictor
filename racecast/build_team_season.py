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
from team_rank import rankTeams, SQUAD

# ! THE SAME 51 CODES rankings.py USES, and for the same reason: there is no
#   nation column anywhere, state is the only geography stored, and a null
#   test would keep exactly the foreign meets it is meant to exclude (a
#   Canadian meet reads QC/ON/BC). Imported rather than retyped.
from rankings import US_STATES, POOLS

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

_DDL = """
CREATE TABLE IF NOT EXISTS team_season (
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
    PRIMARY KEY (scope, school, state, pool, sport, year)
);
"""

_COLUMNS = ("scope", "school", "state", "pool", "sport", "year", "rank",
            "points", "n_athletes", "top5_mean", "fifth_rating", "best_rating")

# ! ORDERED BY THE GROUP so one pass can be cut into boards without holding
#   the whole table. mean_rating is the athlete's season average -- the same
#   number the athlete board ranks, not a second opinion about the season.
_SOURCE_SQL = """
    SELECT sport, year, pool, state, school, person_id,
           mean_rating AS rating
    FROM   athlete_season
    WHERE  mean_rating IS NOT NULL
      AND  n_races >= %(min_races)s
      AND  pool = ANY(%(pools)s)
      AND  state = ANY(%(states)s)
      AND  year >= %(since)s
      AND  (%(sport)s = 'both' OR sport = %(sport)s)
    ORDER  BY sport, year, pool
"""


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


def toRows(board):
    scope, pool, sport, year, teams = board
    for t in teams:
        yield (scope, t["school"], t["state"], pool, sport, year,
               t["rank"], t["points"], t["n_athletes"],
               t["top5_mean"], t["fifth_rating"], t["best_rating"])


def build(conn, sport, since):
    """Fill team_season from athlete_season, replacing what is there.

    ⚠ SHADOW TABLE AND ONE SWAP, like build_ranking_results. A TRUNCATE plus
      a long insert serves an empty board for the length of the build, and a
      crash halfway serves half a board with no error anywhere.
    """
    import psycopg2.extras
    started = time.time()
    with conn.cursor() as cur:
        cur.execute(_DDL)
        cur.execute("DROP TABLE IF EXISTS team_season_new")
        cur.execute("CREATE TABLE team_season_new (LIKE team_season "
                    "INCLUDING ALL)")
    conn.commit()

    params = {"min_races": MIN_RACES, "pools": sorted(POOLS),
              "states": list(US_STATES), "since": since, "sport": sport}

    read = conn.cursor("team_src", cursor_factory=psycopg2.extras.RealDictCursor)
    read.itersize = 50000
    read.execute(_SOURCE_SQL, params)

    def flush(buf):
        buf.seek(0)
        with conn.cursor() as cur:
            cur.copy_from(buf, "team_season_new", columns=_COLUMNS)

    buf, n_rows, n_boards = io.StringIO(), 0, 0
    for board in boards(read):
        n_boards += 1
        for row in toRows(board):
            buf.write("\t".join("\\N" if v is None else str(v) for v in row))
            buf.write("\n")
            n_rows += 1
        if buf.tell() > (8 << 20):
            flush(buf)
            buf = io.StringIO()
    read.close()
    if buf.tell():
        flush(buf)

    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS team_season_old")
        cur.execute("ALTER TABLE team_season RENAME TO team_season_old")
        cur.execute("ALTER TABLE team_season_new RENAME TO team_season")
        cur.execute("DROP TABLE team_season_old")
        cur.execute("ANALYZE team_season")
    conn.commit()
    print(f"  team_season: {n_rows:,} rows across {n_boards:,} boards "
          f"in {time.time() - started:.0f}s")


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
