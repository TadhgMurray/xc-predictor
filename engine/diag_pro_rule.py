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

⚠⚠ WHAT THE ANSWER TURNED OUT TO BE (2026-09-21). The owner, after the run:
  "there are a lot fo schools/high schools that must be pooling wrong". It was
  this rule, and the block of four `hs` ids above was the tell. A K-12 school
  is now exempt from the athlete bar (build_team_pool._SMALL_EXEMPT_LEVELS),
  so it is no longer called pro for being small -- whether it is one school
  under four ids or genuinely a school with nine runners, neither is a pro
  team. The bar still binds college, club and no-level teams.

  So this script now counts EVERY team under the bar, not only the ones still
  condemned by it, and prints the split. That keeps it answering the question
  it was written for -- is the thing being counted a team? -- and makes the
  size of the exemption visible next to it.

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
    SELECT tp.team_id, tp.n_athletes, tp.n_rows, tp.level, tp.kind
    FROM   team_pool tp
    WHERE  tp.n_athletes < %(bar)s
), named AS (
    SELECT p.*, lower(btrim(ti.school)) AS school, ti.state
    FROM   pro p LEFT JOIN team_identity ti ON ti.team_id = p.team_id
)
SELECT school, state,
       count(*)            AS n_ids,
       sum(n_athletes)     AS sum_athletes,
       sum(n_rows)         AS sum_rows,
       min(team_id)        AS a_team,
       count(*) FILTER (WHERE kind = 'pro')  AS n_still_pro
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
    WHERE  tp.n_athletes < %(bar)s
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

    cur.execute(SQL_SIBLINGS, {"bar": args.bar})
    rows = [tuple(r) if not isinstance(r, dict) else tuple(r.values())
            for r in cur.fetchall()]
    if not rows:
        raise SystemExit(f"no team in team_pool has fewer than {args.bar} "
                         f"athletes")

    n_ids = sum(r[2] for r in rows)
    still_pro = sum(r[6] for r in rows)
    shared = [r for r in rows if r[2] > 1]
    alone = len(rows) - len(shared)
    print(f"\n  {n_ids:,} teams under the {args.bar}-athlete bar "
          f"sit on {len(rows):,} schools")
    # ★ THE SPLIT THE 2026-09-21 EXEMPTION MADE. Before it, still_pro was
    #   every one of these; the difference is the K-12 schools that used to
    #   be pooled pro for being small.
    print(f"    {still_pro:,} are still pooled pro by the bar "
          f"(college, club, no level)")
    print(f"    {n_ids - still_pro:,} are K-12 schools the bar no longer "
          f"reaches")
    print(f"    {alone:,} schools hold exactly one such team id")
    print(f"    {len(shared):,} schools hold more than one "
          f"({sum(r[2] for r in shared):,} of the team ids)")

    print(f"\n  the {args.show} schools holding the most of them:")
    print(f"    {'school':<34} {'st':<3} {'ids':>4} {'pro':>4} "
          f"{'Σath':>6} {'Σrows':>9}")
    for school, state, ids, sath, srows, _a, npro in rows[:args.show]:
        print(f"    {school[:34]:<34} {(state or '--'):<3} {ids:>4} "
              f"{npro:>4} {sath:>6} {srows:>9,}")

    # ★ AND THE NUMBER THAT DECIDES IT. Merging only matters if the DISTINCT
    #   roster across a school's ids clears the bar -- four ids sharing one
    #   twelve-runner roster still total twelve.
    print("\n  counting distinct athletes per school across all of its ids "
          "(one pass over both tables)...", flush=True)
    cur.execute(SQL_DISTINCT, {"bar": args.bar})
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
    print(f"    Either way the K-12 ones are no longer pooled pro over it "
          f"-- only the {still_pro:,}\n    college/club/no-level ids above "
          f"still are.")


if __name__ == "__main__":
    main()
