#!/usr/bin/env python3
"""
diag_pro_rule.py -- what the <15-athlete rule is actually catching.

    python engine/diag_pro_rule.py
    python engine/diag_pro_rule.py --show 40

★ WHY THIS EXISTS. The first real team_pool build called 12,310 teams pro, and
  11,998 of them -- 97% -- came from the <15-athlete rule rather than from a
  professional racing for them. The rule is the owner's and is not in question
  here ("~3 athletes should actually be 15 and I mean it" ... "not 15 per year,
  15 over all time"). What IS in question is whether the thing being counted
  is a team. Four of the twenty biggest catches came in a block:

        74823  12 ath  436 rows  hs
        74824  12 ath  436 rows  hs
        74826  12 ath  436 rows  hs
        74828  12 ath  436 rows  hs

  Identical counts on four consecutive ids is what ONE school split across
  four team ids looks like. If that is what it is, the bar is being applied to
  a quarter of a roster at a time and the rule is stricter than it reads.

! THIS MEASURES, IT DOES NOT CHANGE ANYTHING. No writes, no table, no rule.
  The question it answers is a number: how many of the 11,998 would clear 15
  if the count were per SCHOOL (team_identity's school+state) rather than per
  team id -- and, separately, how many are genuinely alone at their school.
  A diagnostic first, because five theories about difficulty got refuted in
  one day and this one is cheap to check.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PRO_MAX_ATHLETES = 15


def _tableExists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    got = cur.fetchone()
    return bool(got[0] if not isinstance(got, dict) else list(got.values())[0])


# ! THE SIBLING SET IS (school, state) FROM team_identity, not a name match.
#   team_identity exists precisely so that "which school is this team" is
#   answered in one place, and its state now comes from the MEET rather than
#   from a guess.
SQL_SIBLINGS = """
WITH pro AS (
    SELECT tp.team_id, tp.n_athletes, tp.n_rows, tp.level
    FROM   team_pool tp
    WHERE  tp.kind = 'pro' AND tp.reason LIKE 'fewer than%%'
), named AS (
    SELECT p.*, lower(btrim(ti.school)) AS school, ti.state
    FROM   pro p LEFT JOIN team_identity ti ON ti.team_id = p.team_id
)
SELECT school, state,
       count(*)            AS n_ids,
       sum(n_athletes)     AS sum_athletes,
       sum(n_rows)         AS sum_rows,
       min(team_id)        AS a_team
FROM   named
WHERE  school IS NOT NULL AND school <> ''
GROUP  BY school, state
ORDER  BY count(*) DESC, sum(n_rows) DESC
"""

# The honest count: DISTINCT athletes across all of a school's team ids, which
# is not the sum -- the same runner appears on the xc id and the tf id.
SQL_DISTINCT = """
WITH pro AS (
    SELECT tp.team_id FROM team_pool tp
    WHERE  tp.kind = 'pro' AND tp.reason LIKE 'fewer than%%'
), ids AS (
    SELECT p.team_id, lower(btrim(ti.school)) AS school, ti.state
    FROM   pro p JOIN team_identity ti ON ti.team_id = p.team_id
    WHERE  COALESCE(btrim(ti.school), '') <> ''
), r AS (
    SELECT i.school, i.state, x.person_id
    FROM   ids i JOIN results x ON x.team_id = i.team_id
    UNION
    SELECT i.school, i.state, x.person_id
    FROM   ids i JOIN results_tf x ON x.team_id = i.team_id
)
SELECT school, state, count(DISTINCT person_id) AS n_athletes
FROM   r GROUP BY school, state
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", type=int, default=20,
                    help="how many schools to print in each list")
    ap.add_argument("--bar", type=int, default=PRO_MAX_ATHLETES,
                    help="the athlete bar to test against (default 15)")
    args = ap.parse_args()

    # ! getConn IS A CONTEXT MANAGER -- it hands the connection back to the
    #   pool when the block ends, which is why every other script in the tree
    #   opens it with `with`. Calling it bare returns the manager object.
    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            _report(cur, args)
        conn.rollback()      # read-only by construction; nothing to keep


def _report(cur, args):
    for t in ("team_pool", "team_identity"):
        if not _tableExists(cur, t):
            raise SystemExit(f"{t} is missing; build it first")

    cur.execute(SQL_SIBLINGS)
    rows = [tuple(r) if not isinstance(r, dict) else tuple(r.values())
            for r in cur.fetchall()]
    if not rows:
        raise SystemExit("no team was called pro by the athlete rule")

    n_ids = sum(r[2] for r in rows)
    shared = [r for r in rows if r[2] > 1]
    alone = len(rows) - len(shared)
    print(f"\n  {n_ids:,} teams called pro by the <{args.bar}-athlete rule "
          f"sit on {len(rows):,} schools")
    print(f"    {alone:,} schools hold exactly one such team id")
    print(f"    {len(shared):,} schools hold more than one "
          f"({sum(r[2] for r in shared):,} of the team ids)")

    print(f"\n  the {args.show} schools holding the most of them:")
    print(f"    {'school':<34} {'st':<3} {'ids':>4} {'Σath':>6} {'Σrows':>9}")
    for school, state, ids, sath, srows, _a in rows[:args.show]:
        print(f"    {school[:34]:<34} {(state or '--'):<3} {ids:>4} "
              f"{sath:>6} {srows:>9,}")

    # ★ AND THE NUMBER THAT DECIDES IT. Merging only matters if the DISTINCT
    #   roster across a school's ids clears the bar -- four ids sharing one
    #   twelve-runner roster still total twelve.
    print("\n  counting distinct athletes per school across all of its ids "
          "(one pass over both tables)...", flush=True)
    cur.execute(SQL_DISTINCT)
    per_school = {}
    for r in cur.fetchall():
        school, state, n = (tuple(r) if not isinstance(r, dict)
                            else tuple(r.values()))
        per_school[(school, state)] = int(n)

    would_clear = sorted(
        ((n, s, st) for (s, st), n in per_school.items() if n >= args.bar),
        reverse=True)
    print(f"    {len(would_clear):,} of {len(per_school):,} schools clear "
          f"{args.bar} on their DISTINCT roster across every id they hold")
    ids_by_school = {(r[0], r[1]): r[2] for r in rows}
    freed = sum(ids_by_school.get((s, st), 0) for _n, s, st in would_clear)
    print(f"    that is {freed:,} of the {n_ids:,} team ids -- the teams the "
          f"rule catches only because the roster is split")
    print(f"\n  the {args.show} biggest of those:")
    print(f"    {'school':<34} {'st':<3} {'ids':>4} {'distinct ath':>13}")
    for n, s, st in would_clear[:args.show]:
        print(f"    {s[:34]:<34} {(st or '--'):<3} "
              f"{ids_by_school.get((s, st), 0):>4} {n:>13}")

    print("\n  ! nothing was written. If `freed` is large the bar is being "
          "applied to a fraction of a roster;\n    if it is small the rule is "
          "catching what it says it catches and these are simply tiny teams.")


if __name__ == "__main__":
    main()
