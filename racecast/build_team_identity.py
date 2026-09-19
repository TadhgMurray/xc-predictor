#!/usr/bin/env python3
"""
build_team_identity.py -- a school IS an anet team id.

    python racecast/build_team_identity.py --dry-run
    python racecast/build_team_identity.py --write

★ WHY THIS REPLACES THE INFERENCE (owner, 2026-09-18): "Ur gonna do schools
  by team id. Only, everywhere ... Issue is ppl going to wrong school."

  school_identity infers identity by clustering athletes' home states, and
  that inference loses to its own evidence whenever a name is shared:

      Georgetown, team 20898, anet_state DC -- athlete-modal state ID
      today's clusters:  Georgetown (TX) 804 primary,  Georgetown (DC) 386

  So the university is outranked by a Texas high school and its athletes are
  filed under the wrong school. Wyoming is the same shape: team 21595 is WY,
  and Wyoming (MI) 464 beats Wyoming (WY) 334.

★ anet ALREADY KNOWS. A team id is anet's own identifier, and anet_state is
  anet's own record of where that team is -- a FACT we fetched, not a
  quantity we computed. No amount of athlete counting is allowed to overrule
  it. That is the whole change: identity stops being a conclusion and goes
  back to being a lookup.

⚠ THIS IS ADDITIVE, NOT A RENAME, and deliberately so. results.school stays
  exactly as scraped: the ratings key on person_id and never on school, and
  engine/college_flag keys pools on the school STRING, so rewriting strings
  would move pools and ratings for no gain. team_identity sits beside those
  tables as the answer to "which school is this row's team", and the pages
  read it.

! A ROW WITH NO TEAM ID KEEPS ITS NAME AND ITS (name, state) CLUSTER, which
  is the owner's own call: "everything should still be name state secondary".
  That is 37.1M tfrrs rows today -- every tfrrs row carries no team id at all
  -- so this table covers anet and the bridge covers the rest, later. Nothing
  here merges a nameless row into a team on a guess.

Table:

    team_identity (team_id PK, school, state, city, level,
                   n_athletes, n_rows, states_seen)

`state` is anet_state first and the modal row state only as a fallback, so a
team anet places nowhere still gets the best answer available rather than
none.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DDL = """
CREATE TABLE IF NOT EXISTS team_identity (
    team_id     int PRIMARY KEY,
    school      text NOT NULL,
    state       text,
    city        text,
    level       int,
    n_athletes  int  NOT NULL DEFAULT 0,
    n_rows      bigint NOT NULL DEFAULT 0,
    states_seen int  NOT NULL DEFAULT 0,
    built       date NOT NULL DEFAULT current_date)
"""

# team_id 0 is anet's unattached sentinel, not an id -- the same exclusion
# anet_teams.teams() makes.
REAL_TEAM = "team_id IS NOT NULL AND team_id <> 0"


def _realTeam(alias):
    """REAL_TEAM qualified for a joined query, where a bare `team_id` is
    ambiguous."""
    return f"{alias}.team_id IS NOT NULL AND {alias}.team_id <> 0"


# ⚠⚠ THE STATE IS ON THE MEET, NOT ON THE RESULT (2026-09-19: this script
#    crashed with `column "state" does not exist` the first time the overnight
#    chain actually reached it with --write). results / results_tf carry no
#    state at all; meets.state and meets_tf.state do, on the same join keys
#    fit_states_offsets uses -- (div_id, source) for XC, and
#    (meet_id, div_id, event_id, source) for TF.
#
# ! LEFT JOIN, DELIBERATELY. n_rows and n_athletes must stay counts of the
#   team's rows whether or not its meet is present, so a missing meet costs
#   the row its state and nothing else. An INNER join here would silently
#   shrink every team that has an unmatched meet.


def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    got = cur.fetchone()
    return bool(got[0] if not isinstance(got, dict) else list(got.values())[0])


def _requireColumns(cur, table, cols):
    """Fail now, with the whole list, if the table lacks a column this build
    reads. See the note in build()."""
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s""",
                (table,))
    have = set()
    for row in cur.fetchall():
        have.add(row[0] if not isinstance(row, dict) else row["column_name"])
    if not have:
        raise SystemExit(f"{table} does not exist")
    missing = [c for c in cols if c not in have]
    if missing:
        raise SystemExit(
            f"{table} is missing {', '.join(missing)} — this script's SQL "
            f"reads them. It has: {', '.join(sorted(have))}")


def build(cur, verbose=True):
    """Fill team_identity from anet_team plus the rows that name each team."""
    def say(msg):
        if verbose:
            print(msg, flush=True)

    if not _tableExists(cur, "anet_team"):
        raise SystemExit("anet_team is missing; run scripts/anet_teams.py first")

    # ! CHECKED BEFORE THE SCAN, NOT DURING IT. The `state` bug above surfaced
    #   as a crash after "counting rows and athletes per team..." had already
    #   printed, and on a night when it had a full pass over 232M rows to get
    #   through first. Every column this build depends on is now confirmed in
    #   the catalogue first, which costs one query and names what is missing.
    _requireColumns(cur, "results", ("team_id", "person_id", "div_id", "source"))
    _requireColumns(cur, "results_tf", ("team_id", "person_id", "div_id",
                                        "event_id", "meet_id", "source"))
    _requireColumns(cur, "meets", ("div_id", "source", "state"))
    _requireColumns(cur, "meets_tf", ("meet_id", "div_id", "event_id", "source",
                                      "state"))
    _requireColumns(cur, "anet_team", ("team_id", "school", "anet_state",
                                       "city", "level"))

    cur.execute(DDL)

    # ★ ONE PASS OVER THE ROWS, into a temp table with an index. Counting
    #   athletes per team is a grouping over 232M rows, and doing it inside
    #   the upsert would make the whole statement one un-resumable scan.
    say("  counting rows and athletes per team (one pass over both tables)...")
    cur.execute("DROP TABLE IF EXISTS _ti_rows")
    cur.execute(f"""
        CREATE TEMP TABLE _ti_rows AS
        WITH r AS (
            SELECT r.team_id, r.person_id, m.state
            FROM   results r
            LEFT   JOIN meets m
                   ON m.div_id = r.div_id AND m.source = r.source
            WHERE  {_realTeam("r")}
            UNION ALL
            SELECT r.team_id, r.person_id, m.state
            FROM   results_tf r
            LEFT   JOIN meets_tf m
                   ON m.meet_id  = r.meet_id AND m.div_id = r.div_id
                  AND m.event_id = r.event_id AND m.source = r.source
            WHERE  {_realTeam("r")}
        )
        SELECT team_id,
               count(*)                          AS n_rows,
               count(DISTINCT person_id)         AS n_athletes,
               count(DISTINCT NULLIF(btrim(state), '')) AS states_seen,
               -- the modal state of the rows, for a team anet places nowhere
               mode() WITHIN GROUP (ORDER BY NULLIF(upper(btrim(state)), ''))
                                                 AS modal_state
        FROM   r GROUP BY team_id
    """)
    cur.execute("CREATE INDEX _ti_rows_idx ON _ti_rows (team_id)")
    cur.execute("SELECT count(*) FROM _ti_rows")
    say(f"    {cur.fetchone()[0]:,} teams named by rows")

    # ⚠ anet_state FIRST, ALWAYS. anet_team.state is the modal state of the
    #   rows that named the team when it was stored -- the same inference this
    #   table exists to stop trusting -- so it is not even a fallback here.
    #   The row-modal state is, because it is at least computed fresh and only
    #   reached when anet states nothing.
    say("  writing team_identity (anet_state first, row-modal as fallback)...")
    cur.execute("""
        INSERT INTO team_identity
            (team_id, school, state, city, level, n_athletes, n_rows,
             states_seen, built)
        SELECT t.team_id,
               btrim(t.school),
               COALESCE(NULLIF(upper(btrim(t.anet_state)), ''), x.modal_state),
               NULLIF(btrim(t.city), ''),
               t.level,
               COALESCE(x.n_athletes, 0),
               COALESCE(x.n_rows, 0),
               COALESCE(x.states_seen, 0),
               current_date
        FROM   anet_team t
        LEFT   JOIN _ti_rows x ON x.team_id = t.team_id
        WHERE  COALESCE(btrim(t.school), '') <> ''
        ON CONFLICT (team_id) DO UPDATE SET
            school      = EXCLUDED.school,
            state       = EXCLUDED.state,
            city        = EXCLUDED.city,
            level       = EXCLUDED.level,
            n_athletes  = EXCLUDED.n_athletes,
            n_rows      = EXCLUDED.n_rows,
            states_seen = EXCLUDED.states_seen,
            built       = current_date
    """)
    written = cur.rowcount
    cur.execute("CREATE INDEX IF NOT EXISTS team_identity_school_idx "
                "ON team_identity (lower(btrim(school)))")
    cur.execute("CREATE INDEX IF NOT EXISTS team_identity_state_idx "
                "ON team_identity (state)")
    return written


def report(cur):
    cur.execute("SELECT count(*), count(*) FILTER (WHERE state IS NOT NULL), "
                "count(*) FILTER (WHERE n_rows > 0) FROM team_identity")
    n, with_state, used = cur.fetchone()
    print(f"\n  team_identity: {n:,} teams, {with_state:,} with a state, "
          f"{used:,} that rows actually name")

    # ! THE TEAMS THE ROWS NAME AND WE STILL HAVE NO METADATA FOR. Until this
    #   is zero, keying on team_id leaves those rows unlabelled -- which is
    #   why anet_teams --unfetched runs before this does.
    cur.execute(f"""
        SELECT count(DISTINCT x.team_id) FROM (
            SELECT team_id FROM results WHERE {REAL_TEAM}
            UNION ALL
            SELECT team_id FROM results_tf WHERE {REAL_TEAM}
        ) x LEFT JOIN team_identity ti ON ti.team_id = x.team_id
        WHERE ti.team_id IS NULL
    """)
    missing = cur.fetchone()[0]
    print(f"  teams the rows name with no metadata yet: {missing:,}"
          + ("   <- run anet_teams.py --unfetched first" if missing else ""))

    # ★ THE NAMES THAT REALLY ARE SEVERAL SCHOOLS, now stated rather than
    #   inferred: one name, several teams, several states.
    cur.execute("""
        SELECT lower(btrim(school)) AS k, count(*) AS teams,
               count(DISTINCT state) AS states, sum(n_athletes) AS ath
        FROM   team_identity WHERE n_athletes > 0
        GROUP  BY 1 HAVING count(DISTINCT state) > 1
        ORDER  BY ath DESC LIMIT 12
    """)
    rows = cur.fetchall()
    if rows:
        print(f"\n  names held by teams in more than one state "
              f"(the collisions, by anet's own answer):")
        print(f"    {'name':<34} {'teams':>6} {'states':>7} {'athletes':>10}")
        for k, teams, states, ath in rows:
            print(f"    {k[:34]:<34} {teams:>6} {states:>7} {ath or 0:>10,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not (args.write or args.dry_run):
        ap.error("pass --dry-run or --write")

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            n = build(cur)
            print(f"  {n:,} rows written")
            report(cur)
        if args.write:
            conn.commit()
            print("\n  committed.")
        else:
            conn.rollback()
            print("\n  DRY RUN -- rolled back.")


if __name__ == "__main__":
    main()
