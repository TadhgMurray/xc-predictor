#!/usr/bin/env python3
"""
team_identity_census.py -- can a school be identified by anet team_id alone?

    python scripts/team_identity_census.py
    python scripts/team_identity_census.py --probe Georgetown --probe Wyoming

★ WHY, BEFORE ANY REWRITE (owner, 2026-09-18): "Ur gonna do schools by team
  id. Only, everywhere ... Anything else left we'll just let live as separate
  school text." That is the right shape -- Georgetown is a team in DC and no
  amount of athlete counting should be able to move it to TX -- but how big
  "anything else" turns out to be decides whether the leftover is a footnote
  or half the site. This measures it instead of assuming.

! READ-ONLY. No writes, no network.

What it answers, in order:
  1. what share of result rows carry a usable team_id, per feed and sport;
  2. how many schools that is, against how many (school, state) clusters the
     current identity has -- i.e. what the site would be keyed on instead;
  3. how much of the team-less remainder could be bridged (team_slug,
     school_team_link) and how much genuinely cannot;
  4. for a named school, every team anet has for it and where anet puts it,
     beside the clusters the current identity built.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# team_id 0 is anet's unattached sentinel, not an id -- the same exclusion
# anet_teams.teams() makes, so these counts match what a rebuild could use.
REAL_TEAM = "team_id IS NOT NULL AND team_id <> 0"


def _exists(cur, table):
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    got = cur.fetchone()
    return (got[0] if not isinstance(got, dict) else list(got.values())[0])


def _has_col(cur, table, col):
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = %s
                     AND column_name = %s""", (table, col))
    return cur.fetchone() is not None


def coverage(cur, table):
    """(total, with_team, with_slug_only, with_neither) for one row table."""
    slug = "team_slug" if _has_col(cur, table, "team_slug") else None
    slug_ok = f"COALESCE(btrim({slug}), '') <> ''" if slug else "false"
    cur.execute(f"""
        SELECT count(*),
               count(*) FILTER (WHERE {REAL_TEAM}),
               count(*) FILTER (WHERE NOT ({REAL_TEAM}) AND {slug_ok}),
               count(*) FILTER (WHERE NOT ({REAL_TEAM}) AND NOT ({slug_ok}))
        FROM   {table}
    """)
    return tuple(cur.fetchone())


def bySource(cur, table):
    if not _has_col(cur, table, "source"):
        return []
    cur.execute(f"""
        SELECT source, count(*), count(*) FILTER (WHERE {REAL_TEAM})
        FROM   {table} GROUP BY source ORDER BY source
    """)
    return cur.fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="append", default=[],
                    help="a school name to show team by team (repeatable)")
    args = ap.parse_args()

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            print("\n=== 1. do result rows carry a team_id? ===")
            print(f"    {'table':<12} {'rows':>14} {'with team':>14} "
                  f"{'slug only':>12} {'neither':>14}  team %")
            for table in ("results", "results_tf"):
                if not _exists(cur, table):
                    continue
                tot, team, slug, none = coverage(cur, table)
                pct = (100.0 * team / tot) if tot else 0.0
                print(f"    {table:<12} {tot:>14,} {team:>14,} "
                      f"{slug:>12,} {none:>14,}  {pct:5.1f}%")
                for src, n, t in bySource(cur, table):
                    p = (100.0 * t / n) if n else 0.0
                    print(f"      {src:<10} {n:>14,} {t:>14,}"
                          f"{'':>12}{'':>14}  {p:5.1f}%")

            print("\n=== 2. what the site would be keyed on ===")
            cur.execute(f"""
                SELECT count(DISTINCT team_id) FROM (
                    SELECT team_id FROM results WHERE {REAL_TEAM}
                    UNION ALL
                    SELECT team_id FROM results_tf WHERE {REAL_TEAM}
                ) x
            """)
            n_teams = cur.fetchone()[0]
            print(f"    distinct team ids the rows name:        {n_teams:,}")
            if _exists(cur, "anet_team"):
                cur.execute("SELECT count(*), count(*) FILTER ("
                            "WHERE COALESCE(btrim(anet_state), '') <> '') "
                            "FROM anet_team")
                at, ast_ = cur.fetchone()
                print(f"    teams anet_team has metadata for:      {at:,}"
                      f"   ({ast_:,} with a state anet states)")
                cur.execute(f"""
                    SELECT count(DISTINCT x.team_id) FROM (
                        SELECT team_id FROM results WHERE {REAL_TEAM}
                        UNION ALL
                        SELECT team_id FROM results_tf WHERE {REAL_TEAM}
                    ) x
                    LEFT JOIN anet_team t ON t.team_id = x.team_id
                    WHERE t.team_id IS NULL
                """)
                print(f"    ... named by rows but NOT fetched yet: "
                      f"{cur.fetchone()[0]:,}   <- these need a fetch first")
            if _exists(cur, "school_identity"):
                cur.execute("SELECT count(*), count(DISTINCT school) "
                            "FROM school_identity")
                cl, names = cur.fetchone()
                print(f"    today's identity: {cl:,} (school, state) clusters "
                      f"over {names:,} names")

            print("\n=== 3. the team-less remainder ===")
            # ★ THE REAL QUESTION: how many SCHOOL NAMES exist only on rows
            #   with no team id? Those are the pages that would stand alone.
            cur.execute(f"""
                WITH r AS (
                    SELECT school, {REAL_TEAM} AS has_team FROM results
                    WHERE school IS NOT NULL
                    UNION ALL
                    SELECT school, {REAL_TEAM} AS has_team FROM results_tf
                    WHERE school IS NOT NULL
                )
                SELECT count(*) FROM (
                    SELECT school FROM r GROUP BY school
                    HAVING bool_and(NOT has_team)
                ) x
            """)
            print(f"    school names that NEVER carry a team id: "
                  f"{cur.fetchone()[0]:,}")
            if _exists(cur, "school_team_link"):
                cur.execute("SELECT count(DISTINCT tfrrs_school), "
                            "count(DISTINCT team_id) FROM school_team_link")
                a, b = cur.fetchone()
                print(f"    school_team_link bridges {a:,} tfrrs names "
                      f"to {b:,} teams")

            # ★★ AND THE EVIDENCE THE OWNER NAMED: one athlete under two
            #    (name, state) pairs is one school. Counted here so the merge
            #    rule can be sized before it is written.
            print("\n=== 4. athletes whose rows name more than one team ===")
            cur.execute(f"""
                WITH r AS (
                    SELECT person_id, team_id FROM results WHERE {REAL_TEAM}
                      AND person_id IS NOT NULL
                    UNION ALL
                    SELECT person_id, team_id FROM results_tf WHERE {REAL_TEAM}
                      AND person_id IS NOT NULL
                )
                SELECT count(*) FROM (
                    SELECT person_id FROM r GROUP BY person_id
                    HAVING count(DISTINCT team_id) > 1
                ) x
            """)
            print(f"    people who raced for 2+ team ids: "
                  f"{cur.fetchone()[0]:,}   (transfers AND the same school "
                  f"spelled twice)")

            for name in args.probe:
                print(f"\n=== 5. {name!r} team by team ===")
                if _exists(cur, "anet_team"):
                    cur.execute("""
                        SELECT team_id, school, anet_state, state, city, level
                        FROM   anet_team
                        WHERE  school ILIKE %s ORDER BY team_id
                    """, (f"%{name}%",))
                    rows = cur.fetchall()
                    print(f"    {'team':<9} {'anet school':<30} "
                          f"{'anet_state':<11} {'modal':<7} city")
                    for tid, sc, ast_, st, city, _lv in rows:
                        print(f"    {tid:<9} {str(sc)[:30]:<30} "
                              f"{str(ast_ or '?'):<11} {str(st or '?'):<7} "
                              f"{city or ''}")
                if _exists(cur, "school_identity"):
                    cur.execute("""
                        SELECT school, state, n_athletes, is_primary
                        FROM   school_identity WHERE school ILIKE %s
                        ORDER  BY n_athletes DESC LIMIT 15
                    """, (f"%{name}%",))
                    print(f"    -- today's clusters --")
                    for sc, st, n, prim in cur.fetchall():
                        print(f"    {sc[:34]:<34} {st:<4} {n:>7,} "
                              f"{'primary' if prim else ''}")
        conn.rollback()

    print("\n  1 says whether keying on team_id covers the corpus.")
    print("  3 says how big 'let it live as separate school text' really is.")
    print("  5 is the Georgetown/Wyoming case: anet's own state, per team.")


if __name__ == "__main__":
    main()
