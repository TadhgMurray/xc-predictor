"""
level_graph.py -- rebuild school levels by propagating through races.

WHY NOT A NAME LIST
    school_levels.pkl resolves a school string to a level by exact dictionary
    lookup. Audited against the NCAA/NAIA institution lists, 128 colleges are
    tagged youth -- Duke as 'ms', Alabama, Auburn, Colorado, Florida, Cornell,
    Arkansas, Liberty as 'hs' -- and 1,642 colleges are absent entirely. The
    dictionary is overwhelmingly high schools, so an ambiguous short name
    defaults to 'hs'.

    A name list cannot fix that, because the ambiguity is real: "Cornell" IS a
    university and IS a high school. No amount of matching separates them.

★ THE SIGNAL IS CO-MEMBERSHIP, NOT SPELLING. Teams race their own level. If a
  race is 90% known-college teams, the rest are college too -- whatever they are
  called. Cornell in an NCAA regional is the university; Cornell in a New York
  sectional is the high school, and the two are distinguishable by the company
  they keep even though the string is identical.

HOW IT RUNS
    SEED       hand-picked races per level. The operator names them; this
               module does not guess.
    PROPAGATE  a race with >= THRESHOLD known teams of one level assigns that
               level to its remaining teams. Repeat to a fixed point.
    CONTEST    a school that would be assigned two different levels is NOT
               assigned either. It is recorded, because that is exactly the
               Cornell case and it needs a second key -- state, or the meet --
               rather than a guess.

★ TEAMS, NOT ATHLETES. The unit is the distinct school string in a race. One
  college entering forty runners is one team, and cannot outvote forty high
  schools entering seven each.

Writes school_level_graph. Changes nothing the engine reads until you say so.
"""

import os
import sys
sys.path.insert(0, 'scripts')
# ------------------------------------------------------------------ #
#  SEEDS -- the only hand-authored part
# ------------------------------------------------------------------ #
#
# ★ EDIT THESE. Each entry is a regex matched against meet_name, plus an
#   optional list of explicit meet_ids for races a name cannot pin down.
#
#   Pick races that are UNAMBIGUOUSLY one level and BROAD -- a national
#   championship touches hundreds of teams at once, which is what makes round 1
#   large enough to propagate from. A dual meet seeds almost nothing.
#
# ⚠ POSTGRES WORD BOUNDARY IS \y, NOT \b. In Postgres POSIX regex \b means a
#   BACKSPACE character, so a pattern like \bncaa\b silently matches nothing --
#   no error, just an empty seed. Use \y (or \m / \M for start / end of word).
#
# ⚠ EXCLUSIONS MATTER AS MUCH AS INCLUSIONS. 'olympic games' caught AAU Junior
#   Olympics (8,984 athletes) when pro_flag was built; 'diamond league' caught a
#   Youth Diamond League Series. Anything with youth/junior/masters in the name
#   is not the meet you mean.
_SEEDS = {
    "college": {
        # NCAA and NAIA/NJCAA only. Both are unambiguous and broad -- the D-III
        # outdoor championships alone touch 203 teams.
        "include": r"(\yncaa\y|\ynaia\y|\ynjcaa\y)",
        # ⚠ NOT conference names. 'sec' matched "New Zealand SECondary Schools
        #   Track, Field and Road Race Championships" -- 166 teams of HIGH
        #   SCHOOLERS, which would have seeded the entire NZ school system as
        #   college. 'pre-nationals' is likewise dominated by "North Shore HS
        #   Pre-National Invitational", not the NCAA pre-nats.
        "exclude": r"(secondary|high school|\yhs\y|youth|junior|middle)",
        "meet_ids": [],
    },
    "hs": {
        # NXN/NXR and Foot Locker are national HS series. The state associations
        # are the broadest seeds available anywhere -- NYSPHSAA touches 407
        # teams, CIF State 373.
        "include": r"(nike cross national|\ynxn\y|nike cross regional|\ynxr\y"
                   r"|foot ?locker|footlocker"
                   r"|\ycif\y|nysphsaa|nyphsaa|njsiaa|\yuil\y|ohsaa|mhsaa"
                   r"|\ypiaa\y|\yvhsl\y|tssaa|\yghsa\y|fhsaa"
                   r"|running ?lane)",
        # ⚠ OHSAA also runs a MIDDLE SCHOOL state championship (259 teams), and
        #   NXR runs a Grade School/Middle School race. Both would poison the hs
        #   seed with younger athletes.
        "exclude": r"(middle school|junior high|\yjr\y|grade school|elementary"
                   r"|\yms\y|ncaa|college|masters)",
        "meet_ids": [],
    },
    "pro": {
        "include": r"(diamond league|wanda diamond|world athletics champ"
                   r"|european athletics champ)",
        # ⚠ 'olympic games' and 'olympiad' are DELIBERATELY ABSENT. Every single
        #   match was AAU Junior Olympics -- 837 teams of twelve-year-olds at one
        #   meet. The real Olympics are not distinguishable by name from it.
        #   'continental tour' is also out: the matches are Cape Miller, COSMA
        #   Cup and Challenger meets of 6-27 teams, not the World Athletics tour.
        "exclude": r"(youth|junior|\yjr\y|\yaau\y|masters|senior games"
                   r"|little athletics|age group|diamond league #|high school)",
        "meet_ids": [],
    },
    "ms": {
        # State middle-school championships, which are broad: OHSAA 259 teams,
        # KTCCCA 185, Indiana 183.
        "include": r"((middle school|junior high|jr\.? high|\yms\y|\yjh\y)"
                   r".*(state|championship|invitational)"
                   r"|flyra|ktccca)",
        "exclude": r"(high school (state|champ)|ncaa|college|grade school"
                   r"|elementary)",
        "meet_ids": [],
    },
    "elem": {
        # Tennessee State Elementary XC and CPS Elementary are the only broad
        # elementary meets in the corpus -- 92 and 89 teams.
        "include": r"(elementary)",
        # ⚠ 'grade school' is excluded: its best match is "NXR Northwest Grade
        #   School/MIDDLE School Championships", which mixes the two levels, and
        #   the rest are 22-27 team parochial meets too small to seed from.
        "exclude": r"(middle|junior high|high school|college)",
        "meet_ids": [],
    },
}

_THRESHOLD = 0.90       # share of a race's teams that must already be known
_MIN_TEAMS = 6          # below this, "90% of teams" is one or two schools
_MAX_ROUNDS = 12

# School strings that are not teams. These appear in every race and would
# short-circuit the whole graph by linking unrelated levels together.
_JUNK = r"^\s*$|^0$|unattached|^una$|^unat|individual|^n/?a$|^\?+$"

# team_id = 0 is a sentinel, not a team -- one "La Salle High School" row
# carries it. Treated exactly like NULL.
#
# ⚠ RENDERED, NOT INTERPOLATED. A Python one-element tuple str()s as "(0,)"
#   and SQL rejects the trailing comma: `IN (0,)` is a syntax error. Build the
#   list explicitly so a one-element set works the same as a many-element one.
_TEAM_ID_SENTINELS = (0,)


def _sentinelList():
    """`(0)` or `(0, -1)` -- a SQL IN-list, never a Python tuple repr."""
    return "(" + ", ".join(str(int(v)) for v in _TEAM_ID_SENTINELS) + ")"


# ------------------------------------------------------------------ #
# CHUNK 0 -- KEY EXPRESSIONS
# ------------------------------------------------------------------ #

def _nodeKeyExpr(alias="r"):
    """
    The SQL expression that turns a result row into a graph NODE key.

    ★ WHY NOT THE SCHOOL STRING. Five distinct institutions share the string
      "La Salle" -- team_ids 1723 / 21012 / 8346 / 599 / 27495, each with its
      own multi-decade date range. As ONE node the graph cannot tell them
      apart, so the first level to reach the node claims all five. Same for
      Syracuse.

    team_id does two things at once, and both are wins:
      SPLITS   five La Salles into five nodes
      COLLAPSES 'La Salle Academy' / '... RI' / '...-RI' into one

    Coverage is 89.6%; the rest fall back to the trimmed string and behave
    exactly as before -- no worse than today.

    NAMESPACED PREFIXES so the two key spaces cannot collide: without them a
    school literally named "1723" would merge with team_id 1723. Absurd, but
    free to prevent and silent if it ever happened.

    ⚠ KNOWN COST: a team appearing on some rows WITH a team_id and on others
      WITHOUT becomes two nodes. That loses connectivity (the graph knows less)
      but never merges two institutions (the graph is never wrong). Losing
      recall is the safe direction.

    Returns a SQL fragment for interpolation -- built from a fixed alias and
    module constants, never from user input.
    """
    return f"""
        CASE WHEN {alias}.team_id IS NOT NULL
              AND {alias}.team_id NOT IN {_sentinelList()}
             THEN 'T:' || {alias}.team_id::text
             ELSE 'S:' || btrim({alias}.school)
        END"""


def _raceKeyExpr(alias="r"):
    """
    The RACE identity: (meet_id, div_id, source) hashed to a bigint.

    Unchanged in behaviour -- extracted only so the two places that build it
    cannot drift apart. Two copies of a hash definition is one edit away from
    a graph whose races do not match its seeds.

    md5 returns 32 hex chars; substr(...,1,15) takes 60 bits; bit(60)::bigint
    is safe where bit(64) would overflow into the sign bit.
    """
    return f"""('x' || substr(md5({alias}.meet_id::text || ':' ||
                                  {alias}.div_id::text  || ':' ||
                                  COALESCE({alias}.source,'')), 1, 15)
               )::bit(60)::bigint"""


# ------------------------------------------------------------------ #
# CHUNK 1 -- THE RACE / TEAM GRAPH
# ------------------------------------------------------------------ #

def buildGraph(cur, min_teams=_MIN_TEAMS):
    """
    tmp_race_team: one row per (race, distinct school).

    Built once. Every round afterwards is a join against this rather than a
    scan of 230M result rows -- the same optimisation that took pro_flag from
    90 minutes to 12.

    Races below min_teams are dropped here rather than filtered later: a race
    with three teams cannot express "90% of teams" meaningfully, and keeping it
    only lets one seed leak into an unrelated cluster.
    """
    cur.execute("DROP TABLE IF EXISTS tmp_race_team")
    cur.execute("""CREATE TEMP TABLE tmp_race_team
                   (race bigint, school text, label text)""")

    # `school` now holds a NODE KEY, not a raw name -- the column keeps its
    # name so every downstream query is untouched. `label` rides alongside for
    # reporting, because 'T:1723' tells a human nothing.
    #
    # ★ GROUP BY, NOT DISTINCT. A node with two spellings would emit TWO rows
    #   for one race under DISTINCT, and propagate's known_frac divides by
    #   count(*) -- so the denominator inflates and races silently stop
    #   qualifying. min(label) collapses them in the same pass. (An earlier
    #   version did this with a self-join DELETE afterwards; on an unindexed
    #   temp table of tens of millions of rows that is a nested loop, and it
    #   dominated the whole run.)
    for table in ("results", "results_tf"):
        cur.execute(f"""
            INSERT INTO tmp_race_team (race, school, label)
            SELECT {_raceKeyExpr('r')},
                   {_nodeKeyExpr('r')},
                   min(btrim(r.school))
            FROM {table} r
            WHERE r.school IS NOT NULL
              AND btrim(r.school) !~* %s
            GROUP BY 1, 2
        """, (_JUNK,))
        print(f"    {table}: {cur.rowcount:,} race-team rows")

    cur.execute(f"""
        DELETE FROM tmp_race_team t
        WHERE t.race IN (SELECT race FROM tmp_race_team
                         GROUP BY race HAVING count(*) < {min_teams})
    """)
    cur.execute("CREATE INDEX ON tmp_race_team (school)")
    cur.execute("CREATE INDEX ON tmp_race_team (race)")
    cur.execute("ANALYZE tmp_race_team")

    cur.execute("""SELECT count(DISTINCT race), count(DISTINCT school),
                          count(DISTINCT label)
                   FROM tmp_race_team""")
    races, nodes, labels = cur.fetchone()
    print(f"    graph: {races:,} races, {nodes:,} nodes from {labels:,} "
          f"distinct name strings (races with >= {min_teams} teams)")
    return races, nodes


# ------------------------------------------------------------------ #
# CHUNK 2 -- SEEDING
# ------------------------------------------------------------------ #

def seed(cur, seeds=_SEEDS):
    """
    Teams appearing in the hand-picked races, labelled with that race's level.

    A team seeded at two different levels is dropped from the seed entirely.
    That is not a failure -- it is the Cornell case surfacing immediately, and
    guessing here would poison every later round.
    """
    # ★ SEED FROM THE GRAPH, NOT FROM results. The previous version ran two
    #   full scans of results/results_tf per level -- TEN scans of 66M rows --
    #   joining meets per row. tmp_race_team already holds one row per
    #   (race, team) and is indexed, so the seed becomes a regex on meets
    #   (808k rows, cheap) joined to an indexed temp table.
    cur.execute("DROP TABLE IF EXISTS tmp_race_meet")
    cur.execute(f"""CREATE TEMP TABLE tmp_race_meet AS
        SELECT DISTINCT
               {_raceKeyExpr('r')} AS race,
               r.meet_id, m.meet_name
        FROM (SELECT meet_id, div_id, source FROM results
              UNION ALL
              SELECT meet_id, div_id, source FROM results_tf) r
        JOIN (SELECT meet_id, div_id, meet_name FROM meets
              UNION ALL
              SELECT meet_id, div_id, meet_name FROM meets_tf) m
          ON m.meet_id = r.meet_id AND m.div_id = r.div_id""")
    cur.execute("CREATE INDEX ON tmp_race_meet (race)")
    cur.execute("ANALYZE tmp_race_meet")

    cur.execute("DROP TABLE IF EXISTS tmp_seed")
    cur.execute("CREATE TEMP TABLE tmp_seed (school text, level text)")

    for level, spec in seeds.items():
        cur.execute("""
            INSERT INTO tmp_seed (school, level)
            SELECT DISTINCT t.school, %s
            FROM tmp_race_meet rm
            JOIN tmp_race_team t ON t.race = rm.race
            WHERE (rm.meet_name ~* %s AND rm.meet_name !~* %s)
               OR rm.meet_id = ANY(%s)
        """, (level, spec["include"], spec["exclude"],
              spec["meet_ids"] or [-1]))
        print(f"    seed {level:<8}: {cur.rowcount:,} team-rows")

    cur.execute("DROP TABLE IF EXISTS tmp_level")
    cur.execute("""CREATE TEMP TABLE tmp_level
                   (school text PRIMARY KEY, level text, round int)""")
    cur.execute("""
        INSERT INTO tmp_level (school, level, round)
        SELECT school, min(level), 0
        FROM tmp_seed
        GROUP BY school
        HAVING count(DISTINCT level) = 1
    """)
    seeded = cur.rowcount

    cur.execute("""SELECT count(*) FROM (
                     SELECT school FROM tmp_seed GROUP BY school
                     HAVING count(DISTINCT level) > 1) q""")
    contested = cur.fetchone()[0]
    cur.execute("ANALYZE tmp_level")
    print(f"    seeded {seeded:,} teams; {contested:,} contested at seed "
          f"time (dropped)")
    return seeded


# ------------------------------------------------------------------ #
# CHUNK 3 -- PROPAGATION
# ------------------------------------------------------------------ #

#
# THE OLD SHAPE (one phase):
#     for each round:  find qualifying races -> INSERT their unknown teams
# A node assigned in round 1 was PERMANENT: `WHERE e.school IS NULL` meant a
# contradicting vote in round 3 never reached it. The unanimity check existed,
# but it only saw one round's worth of evidence. That is how La Salle -> hs and
# Syracuse -> ms both landed "in propagation round 1".
#
# THE NEW SHAPE (two phases):
#     PHASE 1  each round RECORDS votes; nothing is committed. A node with any
#              vote counts as "known" for the next round, so propagation still
#              spreads exactly as far as before.
#     PHASE 2  commit only nodes whose votes are unanimous ACROSS ALL ROUNDS.
#
# The behaviour change is confined to nodes that received conflicting votes.
# Those used to be assigned by whoever voted first; they are now dropped --
# the same treatment seed() already gives a contested seed.


def _voteRound(cur, rnd, threshold):
    """
    One propagation round. Writes to tmp_vote, NEVER to tmp_level.

    The inner subquery is unchanged: a race qualifies when >= threshold of its
    teams are already known AND they are unanimous on level.

    `count(l.school)::float / count(*)` -- count(expr) skips NULLs while
    count(*) does not, so on a LEFT JOIN this is exactly "known / total".
    """
    cur.execute(f"""
        INSERT INTO tmp_vote (school, level, round)
        SELECT DISTINCT t.school, q.level, {rnd}
        FROM (
            SELECT rt.race,
                   min(l.level)                      AS level,
                   count(l.school)::float / count(*) AS known_frac,
                   count(DISTINCT l.level)           AS n_levels
            FROM tmp_race_team rt
            LEFT JOIN tmp_known l ON l.school = rt.school
            GROUP BY rt.race
            HAVING count(l.school)::float / count(*) >= {threshold}
               AND count(DISTINCT l.level) = 1
        ) q
        JOIN tmp_race_team t ON t.race = q.race
        LEFT JOIN tmp_known e ON e.school = t.school
        WHERE e.school IS NULL
    """)
    return cur.rowcount


def _refreshKnown(cur):
    """
    tmp_known = the seeds, plus every node that has received any vote so far.

    ★ THIS IS WHAT KEEPS PROPAGATION SPREADING while the commit stays global.
      A node with conflicting votes is treated as known here (so it is not
      re-voted every round forever) but is still dropped at commit time.

      min(level) for a conflicted node is arbitrary ON PURPOSE. It only affects
      whether some race counts as unanimous in the NEXT round -- and a race
      leaning on a node the graph cannot decide should not be assigning levels
      to anyone anyway. Either choice suppresses it.
    """
    cur.execute("DROP TABLE IF EXISTS tmp_known")
    cur.execute("""CREATE TEMP TABLE tmp_known AS
                   SELECT school, min(level) AS level FROM (
                       SELECT school, level FROM tmp_level
                       UNION ALL
                       SELECT school, level FROM tmp_vote
                   ) u GROUP BY school""")
    cur.execute("CREATE INDEX ON tmp_known (school)")
    cur.execute("ANALYZE tmp_known")


def _commitUnanimous(cur):
    """
    PHASE 2. A node joins tmp_level only if EVERY vote it ever received agrees.

    `HAVING count(DISTINCT level) = 1` over the whole vote table is the fix.
    min(round) is recorded so report() can still show which round first reached
    the node.
    """
    cur.execute("""
        INSERT INTO tmp_level (school, level, round)
        SELECT school, min(level), min(round)
        FROM tmp_vote
        GROUP BY school
        HAVING count(DISTINCT level) = 1
    """)
    committed = cur.rowcount

    cur.execute("""SELECT count(*) FROM (
                     SELECT school FROM tmp_vote GROUP BY school
                     HAVING count(DISTINCT level) > 1) q""")
    conflicted = cur.fetchone()[0]
    return committed, conflicted


def propagate(cur, threshold=_THRESHOLD, rounds=_MAX_ROUNDS):
    """
    Vote for `rounds` rounds, then commit only the unanimous nodes.

    ★ UNANIMITY IS REQUIRED, NOT JUST DOMINANCE. A race that is 90% known but
      split between two levels assigns nothing. Mixed-level meets exist -- an
      open section at a college meet -- and they are exactly where a threshold
      alone would leak.

    ★ EXCLUSION STAYS VISIBLE: the conflicted count is printed, not swallowed.
      Those nodes are the ones the graph genuinely cannot decide, and they are
      the worklist. Previously they were invisible, because the first vote won.
    """
    cur.execute("DROP TABLE IF EXISTS tmp_vote")
    cur.execute("""CREATE TEMP TABLE tmp_vote
                   (school text, level text, round int)""")
    cur.execute("CREATE INDEX ON tmp_vote (school)")

    _refreshKnown(cur)                       # round 0 = the seeds alone

    for rnd in range(1, rounds + 1):
        added = _voteRound(cur, rnd, threshold)
        print(f"    round {rnd}: +{added:,} votes")
        if added == 0:
            break
        _refreshKnown(cur)

    committed, conflicted = _commitUnanimous(cur)
    print(f"    committed {committed:,} nodes; "
          f"{conflicted:,} DROPPED as contested across rounds")
    cur.execute("ANALYZE tmp_level")
    return committed


# ------------------------------------------------------------------ #
# CHUNK 3b -- RACE VERDICTS
# ------------------------------------------------------------------ #

def raceLevels(cur, threshold=_THRESHOLD):
    """
    race_level: the level of each RACE, derived from its known teams.

    ★ WHY THIS EXISTS. `_JUNK` excludes 'unattached' from the graph, and that
      exclusion is CORRECT and must stay: Unattached appears in hs, college and
      pro races alike, so as a node it is a hub joining every level to every
      other, and one propagation round would leak across the whole graph.

      But that means a school-level graph can NEVER classify an unattached
      athlete -- there is no school to classify. The evidence has to attach
      somewhere else, and the race is the only place left.

      This is the same subquery propagate() already runs internally; it was
      computed and discarded once per round.

    ⚠ CONSUMERS MUST READ THIS PER ATHLETE-SEASON, NOT PER RACE. Pooling by
      race would split one athlete across two pools mid-season, and the engine
      keys on (person_id, pool) -- see the Cooper Lutkenhaus note in
      normalize_distance.poolFor. A high schooler with one pro-meet appearance
      must stay 'hs'; only a season that is UNANIMOUSLY something else moves.
    """
    cur.execute("DROP TABLE IF EXISTS race_level")
    cur.execute("""CREATE TABLE race_level (
                       race        bigint NOT NULL PRIMARY KEY,
                       level       text   NOT NULL,
                       known_frac  real   NOT NULL,
                       n_teams     int    NOT NULL)""")
    cur.execute(f"""
        INSERT INTO race_level (race, level, known_frac, n_teams)
        SELECT rt.race,
               min(l.level),
               (count(l.school)::float / count(*))::real,
               count(*)
        FROM tmp_race_team rt
        LEFT JOIN tmp_level l ON l.school = rt.school
        GROUP BY rt.race
        HAVING count(l.school)::float / count(*) >= {threshold}
           AND count(DISTINCT l.level) = 1
    """)
    n = cur.rowcount
    cur.execute("CREATE INDEX ON race_level (level)")

    cur.execute("SELECT count(DISTINCT race) FROM tmp_race_team")
    total = cur.fetchone()[0]
    print(f"    race verdicts: {n:,} of {total:,} races decided "
          f"({100.0 * n / max(total, 1):.1f}%)")
    return n


# ★★ THE LEVEL ORDER, IN SQL, ONCE. The same ranking pool_resolve._LEVEL_RANK
#    uses. It exists because `min(level)` is ALPHABETICAL -- 'college' sorts
#    before 'hs' before 'ms' before 'pro' -- which is not the level order at
#    all. raceLevels above gets away with min() only because its HAVING
#    already forced a single distinct level; the moment a race is allowed to
#    hold two, the order has to be stated.
_LEVEL_RANK_SQL = """CASE lv
                         WHEN 'elem'    THEN 0
                         WHEN 'ms'      THEN 1
                         WHEN 'hs'      THEN 2
                         WHEN 'college' THEN 3
                         WHEN 'pro'     THEN 4
                     END"""


def raceTopLevels(cur, threshold=_THRESHOLD):
    """
    race_top_level: the HIGHEST level present in each race, not the unanimous
    one.

    ★★ WHY A SECOND TABLE AND NOT A COLUMN ON race_level (owner, 2026-09-20:
       "if a runner is unattached they should resolve to the highest pool in
       the race they're running in").

       race_level answers a different question and is consumed by
       season_level, which relies on its meaning: a race is decided only when
       its known teams are UNANIMOUS, so a mixed meet decides nothing and a
       high schooler with one pro-meet appearance stays 'hs'. That rule is
       right for the athlete-season it feeds and must not be weakened.

       This table answers the owner's question instead: given a race, what is
       the highest level anybody in it is racing at? A mixed open section at a
       college meet DOES have an answer here -- 'college' -- where race_level
       correctly has none.

    ★ IT IS FOR ATHLETES WITH NO SCHOOL, AND ONLY THEM. An unattached entry
      carries no team, so every school-based rule in the system is blind to
      it and pool_resolve fell back to calling it professional. The race is
      the only evidence there is, and the owner's rule is that the race's
      ceiling is what an unattached runner is racing at.

    ⚠ THE COST, STATED: THIS IS PER RACE, AND raceLevels' own warning applies.
      An unattached athlete who races a high school meet and an open meet in
      one season is now TWO pools, and the engine keys the athlete on
      (person_id, pool) -- so they are fitted as two people on half the
      evidence each. That is the owner's explicit instruction and the trade is
      real: against it, the status quo pooled every one of those rows
      professional, which is wrong in every race rather than in one of them.
      resolvePool counts the rows this repools so the cost stays visible.

    ! SAME known_frac BAR AS raceLevels. A race whose teams are mostly unknown
      has no ceiling worth trusting, and a row with no verdict here falls back
      to the old behaviour rather than to a guess.
    """
    cur.execute("DROP TABLE IF EXISTS race_top_level")
    cur.execute("""CREATE TABLE race_top_level (
                       race        bigint NOT NULL PRIMARY KEY,
                       top_level   text   NOT NULL,
                       n_levels    int    NOT NULL,
                       known_frac  real   NOT NULL,
                       n_teams     int    NOT NULL)""")
    # ⚠⚠ NO count(DISTINCT ...) OVER (...) -- POSTGRES DOES NOT IMPLEMENT IT.
    #    The first version used window functions to get n_levels and the top
    #    rank in one pass, and Postgres answered "DISTINCT is not implemented
    #    for window functions". It failed AFTER two and a half hours of graph
    #    building, inside the same transaction, so school_level_graph and
    #    race_level rolled back with it and the whole run was lost.
    #
    # ★ PLAIN AGGREGATION INSTEAD, and it is better anyway: max() over the
    #   rank is one grouped scan with no window to sort, and the rank maps
    #   back to a name at the end. Tested against a real Postgres.
    cur.execute(f"""
        INSERT INTO race_top_level (race, top_level, n_levels, known_frac, n_teams)
        SELECT race,
               CASE top_rank
                   WHEN 0 THEN 'elem' WHEN 1 THEN 'ms'   WHEN 2 THEN 'hs'
                   WHEN 3 THEN 'college' WHEN 4 THEN 'pro'
               END,
               n_levels, known_frac, n_teams
        FROM (
            SELECT rt.race,
                   count(DISTINCT l.level)                          AS n_levels,
                   (count(l.school)::float / count(*))::real        AS known_frac,
                   count(*)                                         AS n_teams,
                   max({_LEVEL_RANK_SQL.replace("lv", "l.level")})  AS top_rank
            FROM   tmp_race_team rt
            LEFT   JOIN tmp_level l ON l.school = rt.school
            GROUP  BY rt.race
            HAVING count(l.school)::float / count(*) >= {threshold}
        ) q
        WHERE top_rank IS NOT NULL
    """)
    n = cur.rowcount
    cur.execute("CREATE INDEX ON race_top_level (top_level)")
    cur.execute("SELECT count(DISTINCT race) FROM tmp_race_team")
    total = cur.fetchone()[0]
    print(f"    race ceilings: {n:,} of {total:,} races have one "
          f"({100.0 * n / max(total, 1):.1f}%)")
    cur.execute("""SELECT top_level, count(*), sum((n_levels > 1)::int)
                   FROM race_top_level GROUP BY 1
                   ORDER BY 2 DESC""")
    print(f"      {'level':<9} {'races':>10} {'of which mixed':>16}")
    for lvl, cnt, mixed in cur.fetchall():
        print(f"      {lvl:<9} {cnt:>10,} {int(mixed or 0):>16,}")
    return n


# ------------------------------------------------------------------ #
# CHUNK 4 -- CONTESTED SCHOOLS
# ------------------------------------------------------------------ #

def contested(cur, threshold=_THRESHOLD, limit=40):
    """
    ★ THE CORNELL DETECTOR. Schools assigned one level that also appear in races
      dominated by another.

      These are the genuinely ambiguous strings -- one name, two institutions.
      They cannot be resolved by name, level graph, or any amount of iteration,
      and they need a second key: the meet, or the state. Reported rather than
      guessed.
    """
    cur.execute(f"""
        SELECT l.school, l.level AS assigned, q.level AS also_seen,
               q.races
        FROM tmp_level l
        JOIN (
            SELECT t.school, q2.level, count(*) AS races
            FROM (
                SELECT rt.race, min(l2.level) AS level
                FROM tmp_race_team rt
                LEFT JOIN tmp_level l2 ON l2.school = rt.school
                GROUP BY rt.race
                HAVING count(l2.school)::float / count(*) >= {threshold}
                   AND count(DISTINCT l2.level) = 1
            ) q2
            JOIN tmp_race_team t ON t.race = q2.race
            GROUP BY t.school, q2.level
        ) q ON q.school = l.school AND q.level <> l.level
        ORDER BY q.races DESC
        LIMIT {limit}
    """)
    rows = cur.fetchall()
    print(f"\n[level] CONTESTED -- one name, two levels ({len(rows)} shown)")
    print("    assigned  also seen   races   school")
    for school, assigned, also, races in rows:
        print(f"    {assigned:<9} {also:<10} {races:>6,}   {school}")
    return rows


# ------------------------------------------------------------------ #
# CHUNK 5 -- OUTPUT
# ------------------------------------------------------------------ #

def report(cur):
    cur.execute("""SELECT level, round, count(*) FROM tmp_level
                   GROUP BY 1, 2 ORDER BY 1, 2""")
    print("\n[level] teams by level and round")
    print("    level     round   teams")
    for level, rnd, n in cur.fetchall():
        print(f"    {level:<9} {rnd:>5}  {n:>7,}")

    cur.execute("""SELECT l.level, count(*) FROM tmp_level l GROUP BY 1
                   ORDER BY 2 DESC""")
    print("\n[level] totals")
    for level, n in cur.fetchall():
        print(f"    {level:<9} {n:>7,}")


def write(cur):
    cur.execute("DROP TABLE IF EXISTS school_level_graph")
    cur.execute("""CREATE TABLE school_level_graph (
                       school text NOT NULL PRIMARY KEY,
                       level  text NOT NULL,
                       round  int  NOT NULL)""")
    cur.execute("INSERT INTO school_level_graph SELECT * FROM tmp_level")
    n = cur.rowcount
    cur.execute("CREATE INDEX ON school_level_graph (level)")
    return n


# ------------------------------------------------------------------ #
# CHUNK 4b -- SEED DISCOVERY
# ------------------------------------------------------------------ #

# Candidate patterns, ranked by how unambiguous they are. These are STARTING
# POINTS for --find, not seeds: run them, read the meet names, then paste the
# ones you trust into _SEEDS.
_CANDIDATES = {
    "college": [
        (r"\yncaa\y",                          "NCAA anything"),
        (r"(acc|sec|big ten|big 12|pac-?12|ivy league|patriot league"
         r"|big east|atlantic 10|mountain west|conference usa)"
         r".*(championship|champs)",            "D-I conference champs"),
        (r"(naia|njcaa)\y",                     "NAIA / juco"),
        (r"pre-?nationals?",                    "NCAA pre-nats"),
    ],
    "hs": [
        (r"nike cross nationals?|\ynxn\y",      "NXN"),
        (r"nike cross regionals?|\ynxr\y",      "NXR"),
        (r"foot ?locker.*(national|regional|championship)", "Foot Locker"),
        (r"(cif|uil|ohsaa|mhsaa|nysphsaa|phiaa|piaa|vhsl|tssaa|ghsa|fhsaa)"
         r".*(state|championship)",             "state associations"),
        (r"state (championship|meet|final)",    "generic state meet"),
        (r"running ?lane.*(national|championship)", "RunningLane"),
    ],
    "pro": [
        (r"(diamond league|wanda diamond)",     "Diamond League"),
        (r"world athletics champ|olympiad|olympic games", "Worlds / Olympics"),
        (r"european athletics champ|continental tour", "Euros / Continental"),
    ],
    "ms": [
        (r"(middle school|junior high|jr\.? high).*(state|championship|invitational)",
                                                "MS championships"),
        (r"\y(ms|jh)\y.*(state|championship)",  "MS abbreviations"),
    ],
    "elem": [
        (r"elementary.*(championship|invitational|meet)", "elementary"),
        (r"(k-?[5-8]|grade school).*(championship|meet)", "grade school"),
    ],
}


def buildMeetIndex(cur, refresh=False, min_teams=_MIN_TEAMS):
    """
    meet_team_index: one row per race, with its distinct-team count.

    ★ BUILT ONCE, NOT PER PATTERN. --find previously ran ~20 patterns x 2 tables,
      and every one joined results (39M rows) to meets and grouped -- 40 scans of
      the corpus to answer 20 questions. The team count does not depend on the
      pattern, so it is computed once into a real table and every pattern
      afterwards is a regex filter on a few million small rows.

      First run: one pass per results table. Every run after that: seconds.

    PERSISTED, not temp, so re-running --find while you tune patterns costs
    nothing. Pass --refresh after new results are scraped.
    """
    cur.execute("SELECT to_regclass('meet_team_index')")
    if cur.fetchone()[0] is not None and not refresh:
        cur.execute("SELECT count(*) FROM meet_team_index")
        print(f"[find] meet_team_index: {cur.fetchone()[0]:,} races (cached; "
              f"--refresh to rebuild)")
        return

    cur.execute("DROP TABLE IF EXISTS meet_team_index")
    cur.execute("""CREATE TABLE meet_team_index (
                       sport     text,
                       meet_id   bigint,
                       div_id    bigint,
                       source    text,
                       meet_name text,
                       teams     int,
                       first_yr  text,
                       last_yr   text)""")

    for sport, table, meets in (("XC", "results", "meets"),
                                ("TF", "results_tf", "meets_tf")):
        cur.execute(f"""
            INSERT INTO meet_team_index
            SELECT %s, r.meet_id, r.div_id, r.source,
                   min(m.meet_name),
                   count(DISTINCT btrim(r.school)),
                   min(left(r.date, 4)), max(left(r.date, 4))
            FROM {table} r
            JOIN {meets} m ON m.meet_id = r.meet_id AND m.div_id = r.div_id
            WHERE r.school IS NOT NULL
              AND btrim(r.school) !~* %s
            GROUP BY r.meet_id, r.div_id, r.source
            HAVING count(DISTINCT btrim(r.school)) >= %s
        """, (sport, _JUNK, min_teams))
        print(f"    {table}: {cur.rowcount:,} races with >= {min_teams} teams")

    # The regex filter is the hot path now, so index what it orders by.
    cur.execute("CREATE INDEX ON meet_team_index (teams DESC)")
    cur.execute("ANALYZE meet_team_index")


def find(cur, level=None, top=15, min_teams=_MIN_TEAMS):
    """
    ★ SEED DISCOVERY. Candidate meets ranked by DISTINCT TEAMS, because breadth
      is what makes round 1 fire -- a national championship touches hundreds of
      teams in one race, a dual meet seeds almost nothing.

    Read the names before trusting any of them. Every pattern here has a known
    failure mode: 'olympic games' caught AAU Junior Olympics (8,984 athletes),
    'diamond league' caught a Youth Diamond League Series, and 'state
    championship' will catch high school AND college state meets in states that
    hold both.
    """
    wanted = [level] if level else list(_CANDIDATES)
    for lvl in wanted:
        print(f"\n[find] ===== {lvl.upper()} =====")
        for pattern, label in _CANDIDATES.get(lvl, []):
            cur.execute("""
                SELECT meet_name, meet_id, sport, teams, first_yr, last_yr
                FROM meet_team_index
                WHERE meet_name ~* %s AND teams >= %s
                ORDER BY teams DESC LIMIT %s
            """, (pattern, min_teams, top))
            rows = cur.fetchall()
            print(f"\n  --- {label}   /{pattern}/")
            if not rows:
                print("      (no meets match)")
                continue
            for name, mid, sport, teams, first, last in rows:
                print(f"      {teams:>5} teams  {sport}  id={mid:<9} "
                      f"{first}-{last}  {name}")


def preflight(cur):
    """★★ RUN THE EXPENSIVE STATEMENTS AGAINST EMPTY TABLES FIRST (2026-09-20).

    raceTopLevels used count(DISTINCT ...) OVER (...), which Postgres does not
    implement. It failed AFTER two and a half hours of graph building, inside
    the same transaction, so school_level_graph and race_level rolled back
    with it and the entire run was lost.

    A statement that cannot parse cannot parse on an empty table either, so
    this costs milliseconds and catches every syntax error, unknown column and
    unimplemented feature before the first row is read.

    ! IT RUNS IN A SAVEPOINT and rolls back, so nothing it creates survives
      and the real run is unaffected.
    """
    cur.execute("SAVEPOINT preflight")
    try:
        cur.execute("CREATE TEMP TABLE tmp_race_team "
                    "(race bigint, school text, label text) ON COMMIT DROP")
        cur.execute("CREATE TEMP TABLE tmp_level "
                    "(school text, level text) ON COMMIT DROP")
        raceLevels(cur)
        raceTopLevels(cur)
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT preflight")
    print("    preflight ok: every race query parses")


def main(live=False):
    from database import getConn

    with getConn() as conn:
        with conn.cursor() as cur:
            print("[level] preflight (seconds, before hours of work)...")
            preflight(cur)
            print("[level] building the race/team graph...")
            buildGraph(cur)

            print("\n[level] seeding from hand-picked races...")
            if not seed(cur):
                print("[level] EMPTY SEED -- edit _SEEDS. Nothing to propagate.")
                return

            print("\n[level] propagating...")
            propagate(cur)

            report(cur)
            contested(cur)

            if live:
                n = write(cur)
                # Race verdicts are derived from the SAME tmp_level this run
                # committed, so the two artifacts can never disagree.
                print("\n[level] deriving race verdicts...")
                raceLevels(cur)
                # ! THE CEILING IS A SEPARATE ARTIFACT, from the same
                #   tmp_level, so the two can never disagree about a race.
                raceTopLevels(cur)
                conn.commit()
                print(f"\n[level] wrote school_level_graph ({n:,} rows)")
            else:
                conn.rollback()
                print("\n[level] DRY RUN -- pass --write to save")


# ⚠ A REAL PARSER, NOT `"--write" in sys.argv` (2026-09-20). The hand-rolled
#   scan honoured --write, --find, --level= and --refresh and silently ignored
#   everything else -- so a typo ran a DRY RUN and said nothing, and
#   `--level hs` (a space, not an equals) read as no level at all.
#
#   It also made this script indistinguishable, to
#   tests/test_chain_invocations.py, from launcher.py -- whose missing argparse
#   is why `launcher.py --retry-failed` did nothing at night. That test's rule
#   is "a script with NO argparse must be passed none", and it is a good rule;
#   the fix is for a script that DOES take flags to declare them, not to carve
#   an exception into the test.
def _parser():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="commit school_level_graph, race_level and "
                         "race_top_level (without it, a dry run)")
    ap.add_argument("--find", action="store_true",
                    help="report candidate seed races instead of building")
    ap.add_argument("--level", default=None,
                    help="with --find, restrict to this level")
    ap.add_argument("--refresh", action="store_true",
                    help="with --find, rebuild meet_team_index first")
    return ap


if __name__ == "__main__":
    _HERE = os.path.dirname(os.path.abspath(__file__))
    for _p in (_HERE, os.path.dirname(_HERE)):
        if os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)

    _args = _parser().parse_args()
    if _args.find:
        from database import getConn
        with getConn() as conn:
            with conn.cursor() as cur:
                buildMeetIndex(cur, refresh=_args.refresh)
            conn.commit()            # keep the index for the next run
            with conn.cursor() as cur:
                find(cur, level=_args.level)
    else:
        main(live=_args.write)