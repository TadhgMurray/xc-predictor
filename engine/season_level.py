# Project: xc-predictor
# Author:  Tadhg Murray
# File:    engine/season_level.py
# Purpose: turn per-RACE level verdicts into a per-ATHLETE-SEASON level, so an
#          unattached athlete can be pooled at all.
#
#   THE PROBLEM THIS SOLVES
#   -----------------------
#   Pieter Heesters, 2026-03-26, road 10 km:
#
#       school 'Unattached'   grade '12'   ->  poolFor returns hs_m
#       29:43 for 10 km rated 143.65 against a HIGH SCHOOL pool mean.
#
#   He finished high school in 2022. The grade is four years stale, copied
#   forward by a scraper that had nothing better to write.
#
#   level_graph cannot save him. 'Unattached' is in _JUNK and MUST STAY THERE:
#   it appears in hs, college and pro races alike, so as a graph node it is a
#   hub joining every level to every other and one propagation round would leak
#   across the whole graph. A SCHOOL-level graph structurally cannot classify an
#   athlete who has no school.
#
#   So the evidence moves to the RACE. level_graph.raceLevels writes race_level:
#   the level of each race, from >= 90% of its known teams, unanimous. This
#   module aggregates those verdicts per athlete-season.
#
#   ★ WHY PER SEASON AND NOT PER RACE
#   ---------------------------------
#   The engine keys on (person_id, pool) -- pair_all.py line 82:
#
#       group = athleteSeasonCodes(cols["athlete"], cols["year"])
#             = ((person_id, pool), year)
#
#   so a pool that changes race-to-race SPLITS ONE ATHLETE INTO TWO UNKNOWNS,
#   each fitted on half the evidence. That is the same fragmentation as the
#   duplicate-profile bug, introduced deliberately.
#
#   normalize_distance.poolFor already names the case: Cooper Lutkenhaus raced
#   Millrose in February and a Texas UIL district meet six weeks later, as a
#   high school junior. A pro MEET does not imply a pro ATHLETE.
#
#   Per-season aggregation plus a unanimity gate handles both:
#
#       Pieter 2026      2 races, both open  -> unanimous -> grade overridden
#       Lutkenhaus 2025  1 pro, 12 hs        -> split     -> grade stands
#
#   One rule, no special cases.
#
#   ⚠ WHAT THIS DOES NOT FIX. Pieter's four person_ids are still four athletes
#     to the engine. This gives each fragment the right POOL; it does not merge
#     them. Identity is a separate, measured, parked problem.

import os
import sys

# ★ scripts/ ON THE PATH AT IMPORT TIME, NOT INSIDE __main__. `database` and
#   `corrections` live there, and a module imported BY another script never
#   runs its own __main__ block -- so a path fix that only happens there works
#   when the file is run directly and fails when it is imported.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE),
           os.path.join(os.path.dirname(_HERE), "scripts")):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# The race key is defined in level_graph. IMPORTED, NOT MIRRORED: a mirror is
# for verifier-vs-audited independence; a producer and its consumer are two
# callers of one definition. Two copies of a hash is one edit away from a
# consumer whose races match nothing.
from level_graph import _raceKeyExpr

# ★ THE ARBITRATION LIVES IN normalize_distance, NOT HERE. That module is the
#   pooling SSOT -- it owns GRADE_TO_LEVEL, getPool and poolFor, and both
#   fitters import it. A second copy of the grade-vs-race rule in this file
#   would be a mirror, and a mirror is for verifier-vs-audited independence.
#   This module is a PRODUCER of season_level and poolFor is its CONSUMER:
#   two callers of one definition -> import.
from normalize_distance import arbitrateLevel, levelToPool, POOLABLE_LEVELS


# ------------------------------------------------------------------ #
#  POLICY CONSTANTS -- the tunable parts, named, in one place
# ------------------------------------------------------------------ #

# How many DECIDED races a season needs before its verdict may override grade.
#
# ★ 1 IS DEFENSIBLE AND IT IS NOT "ONE DATA POINT". A race verdict is already
#   backed by >= 90% of >= 6 known teams agreeing; the evidence lives in the
#   FIELD, not in the count of races. Requiring 2 would be requiring the
#   evidence twice.
#
# ⚠ BUT IT MATTERS FOR PIETER SPECIFICALLY. His 2026 races sit under two
#   different person_ids, one race each. At MIN_DECIDED = 2 neither fragment
#   qualifies and he stays broken. Raise this only if the audit shows one-race
#   overrides going wrong.
MIN_DECIDED_RACES = 1

# ------------------------------------------------------------------ #
# CHUNK 1 -- ACADEMIC YEAR
# ------------------------------------------------------------------ #

def _academicYearExpr(alias="r"):
    """
    July onward belongs to the year the season STARTS, so fall XC and the
    following spring TF share one label.

    Matches the convention already used by the grade backfill (newest 1.7).
    Getting this wrong would split every athlete's XC and TF into two seasons
    and halve the evidence behind each verdict.

    substr(date,6,2) is the month: `date` is TEXT in ISO form, so character
    positions are stable. Cast to int for the comparison.
    """
    return f"""
        CASE WHEN substr({alias}.date, 6, 2)::int >= 7
             THEN substr({alias}.date, 1, 4)::int
             ELSE substr({alias}.date, 1, 4)::int - 1
        END"""


# ------------------------------------------------------------------ #
# CHUNK 2 -- COLLECT THE VOTES
# ------------------------------------------------------------------ #

def _collectVotes(cur):
    """
    tmp_season_vote: one row per (person_id, academic year, level, count).

    Joins every result row to its race's verdict. Rows whose race has no
    verdict contribute nothing -- they are neither evidence for nor against.

    Both sports, in one temp table. An athlete's XC and TF races are the SAME
    season and must vote together; counting one sport alone would let a
    cross-country season and a track season disagree about one person.
    """
    cur.execute("DROP TABLE IF EXISTS tmp_season_vote")
    # ★ THE SPORT IS NOW RECORDED. The collector already knew it -- it loops
    #   results then results_tf -- but threw it away. Keeping it is what lets
    #   _resolveSeasons produce a per-sport verdict as well as a combined one.
    cur.execute("""CREATE TEMP TABLE tmp_season_vote
                   (person_id bigint, ay int, sport text, level text, n int)""")

    for table, sport in (("results", "XC"), ("results_tf", "TF")):
        cur.execute(f"""
            INSERT INTO tmp_season_vote (person_id, ay, sport, level, n)
            SELECT r.person_id,
                   {_academicYearExpr('r')} AS ay,
                   '{sport}',
                   rl.level,
                   count(*)
            FROM {table} r
            JOIN race_level rl ON rl.race = {_raceKeyExpr('r')}
            WHERE r.person_id IS NOT NULL
              AND r.date IS NOT NULL
            GROUP BY 1, 2, 3, 4
        """)
        print(f"    {table}: {cur.rowcount:,} season-level votes")

    _collectMaskVotes(cur)

    cur.execute("CREATE INDEX ON tmp_season_vote (person_id, ay, sport)")
    cur.execute("ANALYZE tmp_season_vote")


# ------------------------------------------------------------------ #
# CHUNK 2b -- THE FALLBACK VOTE: meets.level_mask
# ------------------------------------------------------------------ #

# The bit layout, decoded against grade evidence (newest 1.1). Only the three
# SCHOOL levels are usable:
#
#     bit 1 =  1  elem          bit 4 =  8  college
#     bit 2 =  2  ms            bit 5 = 16  open / club
#     bit 3 =  4  hs
#
# ★ `& 14` ISOLATES ms|hs|college (2+4+8) AND DISCARDS THE REST. A result of
#   exactly 2, 4 or 8 means ONE bit survived -> unambiguous. 6, 12 and 14 mean
#   two or three did -> genuinely mixed, no vote.
#
# ★ BIT 16 IS NOT A LEVEL AND MUST NEVER BE USED AS ONE. Open means anyone may
#   enter; 1,245,230 rows / 155,066 people with grades 9-12 race open
#   divisions. `& 14` drops it, which is the entire reason the mask is safe
#   here. The elem bit is dropped too -- Pieter's collegiate 10 km carries
#   mask 25 (= 16 + 8 + 1), and that stray elem bit is junk.
_MASK_TO_LEVEL = {2: "ms", 4: "hs", 8: "college"}


def _collectMaskVotes(cur):
    """Add votes from meets.level_mask, ONLY for races race_level could not
    decide.

    ★ WHY THIS EXISTS. race_level is derived from >= 90% of a race's KNOWN
      TEAMS agreeing. A field of `Unattached` athletes has no known teams, so
      the race gets no verdict, so the season gets no votes, so season_level is
      NULL and the stale grade wins by default. That is exactly the population
      this module was written for -- Pieter Heesters' 2026 road 10 km sits in a
      division literally named 'Collegiate' with level_mask 25 (& 14 = 8,
      college), and nothing was reading it.

    ★ FALLBACK, NOT COMPETITOR. Team evidence says who ACTUALLY RACED; the mask
      says who was ELIGIBLE. The first is stronger, so where race_level has a
      verdict this adds nothing and every season that resolves today resolves
      identically. Pure coverage gain, nothing re-litigated -- the same
      property poolFor protects with its school=None default.

    ⚠ meets_tf IS NOT KEYED ON (meet_id, div_id). It carries an EVENT
      dimension: 14.2M rows across 658k pairs, a 21.5x fan-out that once
      produced a 570M-row result against a 191M-row table. The mask is
      collapsed with bit_or in a subquery BEFORE the join. `meets` (XC) has no
      event dimension, so its join is safe as written -- but it DOES need
      `source`, since anet and tfrrs share div_id with different meanings.

      bit_or across a TF container's events is safe for the same reason `& 14`
      is: if two events disagree on level the OR sets two bits, `& 14` is no
      longer a single bit, and the row does not vote.
    """
    level_case = " ".join(f"WHEN {bit} THEN '{lvl}'"
                          for bit, lvl in _MASK_TO_LEVEL.items())

    sources = (
        # (results table, meet source expression, needs a source match)
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

    # The sport is implied by the table, so it is derived here rather than
    # added as a fourth tuple element -- one place to be wrong instead of two.
    for table, join_sql, mask_col in sources:
        sport = "XC" if table == "results" else "TF"

        cur.execute(f"""
            INSERT INTO tmp_season_vote (person_id, ay, sport, level, n)
            SELECT r.person_id,
                   {_academicYearExpr('r')} AS ay,
                   '{sport}',
                   CASE {mask_col} & 14 {level_case} END AS level,
                   count(*)
            FROM {table} r
            {join_sql}
            -- The anti-join: only races race_level could NOT decide.
            LEFT JOIN race_level rl ON rl.race = {_raceKeyExpr('r')}
            WHERE r.person_id IS NOT NULL
              AND r.date IS NOT NULL
              AND rl.race IS NULL
              AND {mask_col} BETWEEN 0 AND 1023
              AND {mask_col} & 14 IN ({",".join(str(b) for b in _MASK_TO_LEVEL)})
            -- ⚠ ORDINALS, AND THE SELECT LIST GREW BY ONE. Adding `sport` as
            --   the third expression pushed `level` from 3 to 4, and this
            --   still said 1,2,3 -- so it grouped by (person, ay, sport) and
            --   left the CASE ungrouped. Postgres caught it; an ordinal that
            --   shifts onto another valid column would not have been caught.
            GROUP BY 1, 2, 3, 4
        """)
        print(f"    {table}: {cur.rowcount:,} mask-fallback votes")


# ------------------------------------------------------------------ #
# CHUNK 3 -- RESOLVE EACH SEASON
# ------------------------------------------------------------------ #

# Levels ordered LOW to HIGH. "Higher" means older / more advanced, and the
# order is what makes the dominance rule below meaningful.
#
# ★ THE ORDER IS AN AGE ORDER, NOT A DIFFICULTY ORDER. elem < ms < hs <
#   college < pro is the sequence a career actually moves through, and it
#   moves through it ONE WAY. That is the whole justification for letting the
#   higher level win a mixed season: a 7th grader cannot acquire a Diamond
#   League start, but a professional can and does enter college meets.
_LEVEL_ORDER = ("elem", "ms", "hs", "college", "pro")

# A higher level needs this share of a season's decided races to take it.
#
# ★ WHY A SHARE AND NOT UNANIMITY. Unanimity was the original rule and it is
#   wrong for the pro/college boundary. Fouad Messaoudi's 2026 season holds
#   two `pro` races (Rabat Diamond League) and one `college` race (Arkansas):
#   under unanimity that is a CONTRADICTION and the season is declined, so he
#   falls back to his scraped grade of 11 and lands in hs_m rated 136.
#   But pro and college are not contradictory for him -- both are true.
#
# ★ AND WHY IT IS STILL SAFE FOR LUTKENHAUS. He raced Millrose once against
#   twelve Texas UIL high school meets: 1/13 = 7.7%, far under the threshold,
#   so the higher level does NOT take his season and his grade stands. The
#   threshold is what separates "an athlete who competes at this level" from
#   "an athlete who visited it once".
HIGHER_LEVEL_SHARE = 0.33


def _resolveSeasons(cur, min_decided=MIN_DECIDED_RACES,
                    share=HIGHER_LEVEL_SHARE):
    """
    athlete_season_level: one row per (person_id, ay) with a usable verdict.

    THE RULE, in order:

      1. Enough decided races               sum(n) >= min_decided
      2. Take the HIGHEST level that holds  >= `share` of the season's
         decided races.

    So a season that is 100% one level resolves to that level exactly as
    before -- the old unanimity rule is the special case where the highest
    level holds 100%.

    ⚠ `unanimous` KEEPS ITS NAME AND ITS MEANING. It still records whether
      every decided race agreed, because that is genuinely different evidence
      from "the top level cleared a threshold", and a consumer filtering on it
      should keep getting the strict set. The new column `chosen_share` says
      how strong the winning level's claim was, so a reader can set their own
      bar without re-running this.

    ★ EXCLUSION STAYS VISIBLE: seasons that resolve to nothing are still
      written, with level NULL, rather than being absent and leaving a
      consumer to guess whether they were considered.
    """
    order_sql = ",".join(f"('{lvl}',{i})" for i, lvl in enumerate(_LEVEL_ORDER))

    cur.execute("DROP TABLE IF EXISTS athlete_season_level")
    # ⚠ THE KEY GAINED `sport`. Rows come in three flavours: 'XC', 'TF', and
    #   'ALL'. The 'ALL' row is today's verdict, computed over both sports and
    #   unchanged, and it is what a consumer falls back to when the sport it
    #   wants has no votes. A join on (person_id, ay) alone now fans out to
    #   three rows -- every join in this codebase pins the sport.
    cur.execute("""CREATE TABLE athlete_season_level (
                       person_id    bigint  NOT NULL,
                       ay           int     NOT NULL,
                       sport        text    NOT NULL,
                       level        text,
                       n_decided    int     NOT NULL,
                       n_levels     int     NOT NULL,
                       chosen_share real,
                       unanimous    boolean NOT NULL,
                       PRIMARY KEY (person_id, ay, sport))""")
    cur.execute(f"""
        INSERT INTO athlete_season_level
              (person_id, ay, sport, level, n_decided, n_levels, chosen_share,
               unanimous)
        WITH ord(level, rank) AS (VALUES {order_sql}),
        -- ★ GROUPING SETS, not two queries. This produces the per-sport rows
        --   AND the combined row in one pass over the votes. Two passes would
        --   scan twice and, worse, could drift apart.
        --   COALESCE turns the grouping set's NULL sport into 'ALL'.
        tot AS (
            SELECT person_id, ay, COALESCE(sport, 'ALL') AS sport,
                   sum(n)                AS n_decided,
                   count(DISTINCT level) AS n_levels
            FROM   tmp_season_vote
            GROUP  BY GROUPING SETS ((person_id, ay, sport), (person_id, ay))
        ),
        shares AS (
            SELECT v.person_id, v.ay, t.sport, v.level,
                   sum(v.n)::float / t.n_decided AS share,
                   o.rank
            FROM   tmp_season_vote v
            -- The join is deliberately loose on sport: a vote counts toward
            -- its own sport's row AND toward the combined 'ALL' row.
            JOIN   tot t ON t.person_id = v.person_id AND t.ay = v.ay
                        AND (t.sport = v.sport OR t.sport = 'ALL')
            LEFT   JOIN ord o ON o.level = v.level
            GROUP  BY v.person_id, v.ay, t.sport, v.level, t.n_decided, o.rank
        ),
        -- ★ HIGHEST QUALIFYING LEVEL, not the most common one. `rank DESC`
        --   with the share filter already applied is the whole rule: among
        --   the levels that cleared the bar, the oldest wins.
        pick AS (
            SELECT DISTINCT ON (person_id, ay, sport)
                   person_id, ay, sport, level, share
            FROM   shares
            WHERE  share >= {share}
            ORDER  BY person_id, ay, sport, rank DESC NULLS LAST, share DESC
        )
        SELECT t.person_id, t.ay, t.sport,
               CASE WHEN t.n_decided >= {min_decided} THEN p.level END,
               t.n_decided,
               t.n_levels,
               p.share,
               (t.n_levels = 1 AND t.n_decided >= {min_decided})
        FROM   tot t
        LEFT   JOIN pick p ON p.person_id = t.person_id AND p.ay = t.ay
                          AND p.sport = t.sport
    """)
    total = cur.rowcount
    cur.execute("CREATE INDEX ON athlete_season_level (level)")
    cur.execute("ANALYZE athlete_season_level")

    cur.execute("""SELECT count(*) FILTER (WHERE level IS NOT NULL),
                          count(*) FILTER (WHERE level IS NOT NULL
                                             AND NOT unanimous),
                          count(*) FILTER (WHERE level IS NULL)
                   FROM athlete_season_level""")
    resolved, mixed, none_ = cur.fetchone()
    print(f"    {total:,} athlete-seasons considered: {resolved:,} resolved "
          f"({mixed:,} of them mixed-level), {none_:,} undecided")

    cur.execute("""SELECT level, count(*) FROM athlete_season_level
                   WHERE level IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""")
    for lvl, n in cur.fetchall():
        print(f"        {lvl:<10}{n:>12,}")
    return resolved


# ------------------------------------------------------------------ #
# CHUNK 4 -- THE ARBITRATION, AS A PURE FUNCTION
# ------------------------------------------------------------------ #

# The arbitration and pool-building functions are imported from
# normalize_distance at the top of this module -- see the note there.
# Nothing is redefined here on purpose.


# ------------------------------------------------------------------ #
# CHUNK 5 -- CONSUMER SIDE
# ------------------------------------------------------------------ #
#
# Two ways to get season_level to poolFor. Prefer the first.
#
#   A) JOIN IT IN THE BACKFILL'S SOURCE QUERY  <- do this
#      One extra LEFT JOIN, the level arrives as a column on the row, zero
#      Python memory. The row already carries person_id and date, which is
#      exactly the join key.
#
#   B) LOAD A DICT
#      For a caller whose query cannot be changed. ⚠ COSTLY: one tuple key and
#      one str per athlete-season. At a few million seasons that is ~1 GB of
#      Python objects. Only use it if A is impossible.


def academicYearOf(date):
    """
    Python twin of _academicYearExpr. July onward = the year the season starts.

    ★ THESE TWO MUST AGREE. If the SQL and the Python disagree by one year the
      lookup silently misses and every override quietly stops firing -- no
      error, just a no-op. Kept adjacent so a change to one is obvious.

    Accepts a date/datetime or an ISO 'YYYY-MM-DD' string.
    """
    if date is None:
        return None
    if isinstance(date, str):
        year, month = int(date[0:4]), int(date[5:7])
    else:
        year, month = date.year, date.month
    return year if month >= 7 else year - 1


def seasonLevelJoinSql(results_alias="r", out="season_level"):
    """
    The LEFT JOIN + column to splice into a backfill source query.

    Returns (join_sql, select_sql). LEFT, not INNER: an athlete-season with no
    race verdict must still produce its row and fall back to grade. An INNER
    join here would silently drop every un-verdicted row from the backfill.

    Only `unanimous` rows carry a level -- split seasons store NULL -- so the
    unanimity gate is enforced by the data, not re-implemented by each caller.
    """
    join = f"""
        LEFT JOIN athlete_season_level asl
               ON asl.person_id = {results_alias}.person_id
              AND asl.ay = CASE WHEN substr({results_alias}.date, 6, 2)::int >= 7
                                THEN substr({results_alias}.date, 1, 4)::int
                                ELSE substr({results_alias}.date, 1, 4)::int - 1
                           END"""
    select = f"asl.level AS {out}"
    return join, select


def loadSeasonLevels(conn, only_unanimous=True):
    """
    {(person_id, ay): level} for callers that cannot change their query.

    ⚠ MEMORY. See the note at the top of this chunk. Prefer the join.
    """
    sql = ("SELECT person_id, ay, level FROM athlete_season_level "
           "WHERE level IS NOT NULL")
    if only_unanimous:
        sql += " AND unanimous"
    with conn.cursor() as cur:
        cur.execute(sql)
        # sys.intern on the level collapses millions of duplicate 'hs' strings
        # onto one object. The tuple keys still dominate; this only trims the
        # values.
        out = {(pid, ay): sys.intern(lvl) for pid, ay, lvl in cur}
    print(f"    loaded {len(out):,} season levels")
    return out


# ------------------------------------------------------------------ #
# CHUNK 6 -- AUDIT
# ------------------------------------------------------------------ #

def audit(cur, limit=25):
    """
    How often does the season verdict CONTRADICT the grade, and where?

    ★ RUN THIS BEFORE WIRING THE OVERRIDE IN. The whole change is justified by
      the claim that contradictions are stale grades. If the table is full of
      seasons where grade says 'hs' and the race says 'college' for ordinary
      high schoolers, the race verdicts are wrong and the override would make
      ratings worse, not better.
    """
    cur.execute("""
        SELECT asl.level AS race_level,
               CASE WHEN r.grade ~ '^[0-9]+$' THEN 'numeric' ELSE 'word' END
                   AS grade_kind,
               count(*) AS rows
        FROM athlete_season_level asl
        JOIN results r ON r.person_id = asl.person_id
        WHERE asl.unanimous
        GROUP BY 1, 2
        ORDER BY 3 DESC
        LIMIT %s
    """, (limit,))
    print("\n[season] race verdict vs grade kind")
    for lvl, kind, n in cur.fetchall():
        print(f"    {lvl:<9} {kind:<8} {n:>12,}")


# ------------------------------------------------------------------ #
# CHUNK 7 -- ENTRY POINT
# ------------------------------------------------------------------ #

def main(live=False):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*) FROM information_schema.tables
                           WHERE table_name = 'race_level'""")
            if not cur.fetchone()[0]:
                print("[season] race_level does not exist -- run "
                      "level_graph.py --write first.")
                return

            print("[season] collecting race verdicts per athlete-season...")
            _collectVotes(cur)

            print("\n[season] resolving...")
            _resolveSeasons(cur)

            audit(cur)

            if live:
                conn.commit()
                print("\n[season] wrote athlete_season_level")
            else:
                conn.rollback()
                print("\n[season] DRY RUN -- pass --write to save")


if __name__ == "__main__":
    main(live="--write" in sys.argv)