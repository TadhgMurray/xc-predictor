#!/usr/bin/env python
# Project: xc-predictor
# File:    scripts/mask_agreement.py
# Purpose: VALIDATE the level_mask fallback added to season_level._collectVotes
#          BEFORE 19.9M votes built on it are written.
#
# THE QUESTION
#   season_level now takes a race's level from `meets.level_mask & 14` when
#   race_level -- the team-derived verdict -- has nothing to say. That is only
#   sound if the two agree WHERE BOTH EXIST. If they measure different things,
#   19.9M fallback votes are built on the wrong thing.
#
# WHY THIS CANNOT BE READ OFF THE PRODUCTION TABLE
#   The fallback is written with an anti-join (`rl.race IS NULL`), so in
#   tmp_season_vote the two sources NEVER overlap by construction. The overlap
#   has to be recreated deliberately: run the same mask derivation WITHOUT the
#   anti-join and compare, race by race.
#
# ★ THIS IS A VERIFIER, SO IT MIRRORS RATHER THAN IMPORTS THE MASK RULE.
#   The `& 14` decode is written out again below instead of being imported from
#   season_level. A verifier that imports what it audits agrees with a bug --
#   the opposite of the producer/consumer case, where importing is right. The
#   race KEY is still imported, because that is a join key and not the thing
#   under test.
#
# READ ONLY. No writes, no temp tables outside the session.
#
# USAGE (from the project root)
#   python scripts\mask_agreement.py
#   python scripts\mask_agreement.py --sample 20     # show example mismatches

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "engine")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from database import getConn, initPool
from level_graph import _raceKeyExpr          # join key, not the thing tested


# ------------------------------------------------------------------ #
# CHUNK 0 -- THE MIRRORED RULE
# ------------------------------------------------------------------ #

# Deliberately re-stated, not imported. See the header.
#   bit 2 = ms, bit 4 = hs, bit 8 = college.
#   & 14 keeps exactly those three and drops elem (1) and open (16).
#   A survivor of 6, 12 or 14 means two or three bits -> genuinely mixed.
_MASK_CASE = "CASE {col} & 14 WHEN 2 THEN 'ms' WHEN 4 THEN 'hs' " \
             "WHEN 8 THEN 'college' END"

# (results table, join fragment exposing `mm`, mask column)
#
# ⚠ meets_tf carries an EVENT dimension -- 14.2M rows over 658k (meet, div)
#   pairs, a 21.5x fan-out. Collapsed with bit_or BEFORE the join. `meets` has
#   no event dimension but DOES need `source`, since anet and tfrrs share
#   div_id with different meanings.
_SOURCES = (
    ("results", """
        JOIN meets mm
          ON mm.meet_id = r.meet_id
         AND mm.div_id  = r.div_id
         AND mm.source  = r.source
     """, "mm.level_mask"),
    ("results_tf", """
        JOIN (SELECT meet_id, div_id,
                     bit_or(CASE WHEN level_mask BETWEEN 0 AND 1023
                                 THEN level_mask END) AS mask
                FROM meets_tf
               GROUP BY 1, 2) mm
          ON mm.meet_id = r.meet_id
         AND mm.div_id  = r.div_id
     """, "mm.mask"),
)


# ------------------------------------------------------------------ #
# CHUNK 1 -- PRIMITIVES
# ------------------------------------------------------------------ #

def banner(title):
    print()
    print("-" * 68)
    print(title)
    print("-" * 68)


def fetch(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def _overlapSql(table, join_sql, mask_col):
    """One race per row, with BOTH verdicts, for races that have both.

    DISTINCT because `results` holds one row per finisher and we are comparing
    RACES, not results -- counting per finisher would weight a 300-runner meet
    300x and the agreement rate would measure field size.
    """
    return f"""
        SELECT DISTINCT
               {_raceKeyExpr('r')}              AS race,
               rl.level                         AS team_level,
               {_MASK_CASE.format(col=mask_col)} AS mask_level
        FROM {table} r
        {join_sql}
        JOIN race_level rl ON rl.race = {_raceKeyExpr('r')}
        WHERE {mask_col} BETWEEN 0 AND 1023
          AND {mask_col} & 14 IN (2, 4, 8)
    """


# ------------------------------------------------------------------ #
# CHUNK 2 -- THE CONFUSION MATRIX
# ------------------------------------------------------------------ #

def confusion(cur, table, join_sql, mask_col):
    """team_level x mask_level counts over the overlapping races."""
    rows = fetch(cur, f"""
        WITH o AS ({_overlapSql(table, join_sql, mask_col)})
        SELECT team_level, mask_level, count(*) AS n
        FROM o GROUP BY 1, 2 ORDER BY 3 DESC
    """)
    if not rows:
        print("  no overlapping races")
        return 0, 0

    total = sum(n for _t, _m, n in rows)
    agree = sum(n for t, m, n in rows if t == m)

    levels = sorted({t for t, _m, _n in rows} | {m for _t, m, _n in rows},
                    key=lambda x: str(x))
    grid = {(t, m): n for t, m, n in rows}

    print(f"  rows = team verdict, cols = mask verdict   "
          f"({total:,} overlapping races)")
    print(f"    {'':<10}" + "".join(f"{str(m):>12}" for m in levels))
    for t in levels:
        cells = "".join(f"{grid.get((t, m), 0):>12}" for m in levels)
        print(f"    {str(t):<10}{cells}")

    pct = 100.0 * agree / total
    print(f"  AGREEMENT: {agree:,} / {total:,} = {pct:.2f}%")
    return agree, total


# ------------------------------------------------------------------ #
# CHUNK 3 -- WHERE THEY DISAGREE
# ------------------------------------------------------------------ #

def mismatches(cur, table, join_sql, mask_col, limit):
    """A sample of disagreeing races, so the pattern is visible rather than
    just its size. A systematic disagreement (always hs-vs-college one way)
    means something different from scattered noise."""
    rows = fetch(cur, f"""
        WITH o AS ({_overlapSql(table, join_sql, mask_col)})
        SELECT team_level, mask_level, count(*) AS n
        FROM o WHERE team_level IS DISTINCT FROM mask_level
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT %s
    """, (limit,))
    for t, m, n in rows:
        print(f"    team={str(t):<9} mask={str(m):<9} {n:>10,}")


# ------------------------------------------------------------------ #
# CHUNK 4 -- ORCHESTRATION
# ------------------------------------------------------------------ #

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=int, default=10,
                    help="how many mismatch classes to list per table")
    args = ap.parse_args()

    initPool()
    grand_agree = grand_total = 0
    with getConn() as conn, conn.cursor() as cur:
        for table, join_sql, mask_col in _SOURCES:
            banner(f"{table}: team verdict vs mask verdict")
            a, t = confusion(cur, table, join_sql, mask_col)
            grand_agree += a
            grand_total += t
            if t and a < t:
                print("  disagreement classes:")
                mismatches(cur, table, join_sql, mask_col, args.sample)
        conn.rollback()                      # nothing here writes

    banner("VERDICT")
    if not grand_total:
        print("  no overlap at all -- the two sources cover disjoint races, "
              "so this check cannot validate the fallback.")
        return
    pct = 100.0 * grand_agree / grand_total
    print(f"  overall {grand_agree:,} / {grand_total:,} = {pct:.2f}%")
    if pct >= 95.0:
        print("  >= 95%: the mask measures the same thing race_level does. "
              "The fallback is sound; write it.")
    elif pct >= 85.0:
        print("  85-95%: mostly aligned. Read the disagreement classes above "
              "-- if they are one-directional, the mask is biased, not noisy.")
    else:
        print("  < 85%: the mask is NOT measuring what race_level measures. "
              "Do NOT write 19.9M votes on it.")


if __name__ == "__main__":
    main()