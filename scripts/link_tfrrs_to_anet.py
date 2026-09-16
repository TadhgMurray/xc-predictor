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
    from speed_ratings_db import loadTeamLevels
    by_team, meaning, _rows = loadTeamLevels()
    college = {t for t, lv in by_team.items() if lv == "college"}
    print(f"  anet level codes: {meaning}")
    print(f"  {len(college):,} anet teams are colleges")
    if not college:
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
_SQL = """
    WITH anet AS (
        SELECT r.person_id, substr(r.date, 1, 4)::int AS yr, r.team_id
        FROM   {table} r
        WHERE  r.person_id IS NOT NULL
          AND  r.team_id = ANY(%(teams)s)
          AND  r.date ~ '^(19|20)[0-9][0-9]-'
        GROUP  BY 1, 2, 3
    ), tf AS (
        SELECT r.person_id, substr(r.date, 1, 4)::int AS yr,
               btrim(r.school) AS school
        FROM   {table} r
        WHERE  r.person_id IS NOT NULL AND r.source = 'tfrrs'
          AND  r.school IS NOT NULL AND btrim(r.school) <> ''
          AND  r.date ~ '^(19|20)[0-9][0-9]-'
        GROUP  BY 1, 2, 3
    )
    SELECT tf.school, anet.team_id,
           count(DISTINCT tf.person_id) AS n_athletes,
           count(DISTINCT tf.yr)        AS n_seasons
    FROM   tf JOIN anet ON anet.person_id = tf.person_id AND anet.yr = tf.yr
    GROUP  BY 1, 2
"""


def votes(cur, teams):
    """{tfrrs school: {team_id: (n_athletes, n_seasons)}} over both tables."""
    out = {}
    for table in ("results", "results_tf"):
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = %s
                         AND column_name = 'team_id'""", (table,))
        if cur.fetchone() is None:
            continue
        cur.execute(_SQL.format(table=table), {"teams": sorted(teams)})
        for school, team_id, n_ath, n_seas in cur.fetchall():
            cell = out.setdefault(school, {}).setdefault(int(team_id), [0, 0])
            cell[0] += int(n_ath)
            cell[1] = max(cell[1], int(n_seas))
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
    args = ap.parse_args()

    from database import getConn
    with getConn() as conn, conn.cursor() as cur:
        teams = collegeTeams(cur)
        if not teams:
            print("no anet college teams -- run anet_teams.py --unfetched first")
            return 1
        counted = votes(cur, teams)
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
