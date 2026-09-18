#!/usr/bin/env python3
# Project: xc-predictor
# File:    scripts/diag_crest.py
# Purpose: Why does THIS school have the crest it has, or none at all?
#
#     python scripts/diag_crest.py "Penn State"
#     python scripts/diag_crest.py Oregon --state OR
#
# ★ WHY THIS EXISTS. "Penn State still doesn't have a logo" has now been
#   reported three times, and each answer was a different guess at which of
#   the six gates in anet_teams.teams() excluded it. The gates are knowable:
#   this walks them in order and says which one the school falls out of.
#
# ! READ ONLY, AND NO NETWORK.

import sys
import argparse

sys.path.insert(0, "scripts")

from database import getConn                            # noqa: E402


def clusters(cur, school):
    cur.execute("""
        SELECT school, state, n_athletes, is_primary, share
        FROM   school_identity
        WHERE  school ILIKE %s
        ORDER  BY n_athletes DESC
    """, (f"%{school}%",))
    return cur.fetchall()


def teamsFor(cur, school):
    """Every anet team whose stored school name matches, with anet's state."""
    cur.execute("""
        SELECT team_id, school, anet_state, state, city, level,
               (COALESCE(btrim(mascot_url), '') <> '') AS has_mascot_url,
               mascot
        FROM   anet_team
        WHERE  school ILIKE %s
        ORDER  BY anet_state, team_id
    """, (f"%{school}%",))
    return cur.fetchall()


def modalTeams(cur, school):
    """What the athlete-modal choice would pick, per assigned state."""
    cur.execute("""
        WITH t AS (
            SELECT school, team_id, person_id FROM results
            WHERE  team_id IS NOT NULL AND team_id <> 0 AND school ILIKE %(s)s
            UNION ALL
            SELECT school, team_id, person_id FROM results_tf
            WHERE  team_id IS NOT NULL AND team_id <> 0 AND school ILIKE %(s)s
        )
        SELECT t.school, t.team_id, count(*) AS rows,
               (SELECT anet_state FROM anet_team a WHERE a.team_id = t.team_id)
                   AS anet_state
        FROM   t GROUP BY 1, 2 ORDER BY count(*) DESC LIMIT 12
    """, {"s": f"%{school}%"})
    return cur.fetchall()


def links(cur, school):
    cur.execute("""SELECT to_regclass('school_team_link')""")
    if cur.fetchone()[0] is None:
        return None
    cur.execute("""
        SELECT tfrrs_school, state, team_id, n_athletes
        FROM   school_team_link WHERE tfrrs_school ILIKE %s
        ORDER  BY n_athletes DESC NULLS LAST
    """, (f"%{school}%",))
    return cur.fetchall()


def stored(cur, school):
    cur.execute("""
        SELECT school, state, level, kind, status, shared, override,
               (path IS NOT NULL) AS has_file, source_url, fetched
        FROM   school_logo WHERE school ILIKE %s
        ORDER  BY school, state, level
    """, (f"%{school}%",))
    return cur.fetchall()


def _table(cur, rows, indent="    "):
    if not rows:
        print(indent + "(none)")
        return
    cols = [d[0] for d in cur.description]
    txt = [[("" if v is None else str(v)) for v in
            (r.values() if isinstance(r, dict) else r)] for r in rows]
    w = [max(len(c), *(len(t[i]) for t in txt)) for i, c in enumerate(cols)]
    print(indent + "  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    print(indent + "  ".join("-" * w[i] for i in range(len(cols))))
    for t in txt:
        print(indent + "  ".join(t[i].ljust(w[i]) for i in range(len(cols))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("school")
    ap.add_argument("--state", default=None)
    args = ap.parse_args()

    with getConn() as conn:
        with conn.cursor() as cur:
            print(f"\n=== school_identity clusters for {args.school!r} ===")
            rows = clusters(cur, args.school)
            _table(cur, rows)
            print("  ! anet_teams.teams() requires n_athletes >= 3.")

            print(f"\n=== anet teams whose name matches ===")
            _table(cur, teamsFor(cur, args.school))
            print("  ★ anet_state is anet's OWN answer for where the team is. "
                  "The crest queue now picks the team matching the cluster's "
                  "state, and refuses one anet places elsewhere.")

            print(f"\n=== what the athlete-modal choice sees "
                  f"(rows per team, biggest first) ===")
            _table(cur, modalTeams(cur, args.school))
            print("  ⚠ a team with many rows but the WRONG anet_state is how "
                  "the wrong crest got in.")

            print(f"\n=== school_team_link (the tfrrs bridge) ===")
            lk = links(cur, args.school)
            if lk is None:
                print("    school_team_link does not exist -- run "
                      "scripts/link_tfrrs_to_anet.py (pipeline 10b0). "
                      "Without it, a cluster whose rows carry no anet team id "
                      "has no team at all and cannot be queued.")
            else:
                _table(cur, lk)

            print(f"\n=== school_logo rows stored ===")
            _table(cur, stored(cur, args.school))
            print("  ! the site draws a crest only when: path IS NOT NULL, "
                  "status = 'ok', override <> 'none', and NOT shared "
                  "(a placeholder worn by many schools is suppressed).")
        conn.rollback()
    print()


if __name__ == "__main__":
    main()
