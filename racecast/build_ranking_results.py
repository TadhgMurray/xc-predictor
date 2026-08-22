"""
build_ranking_results.py -- fill the rankings tables.

    python scripts/build_ranking_results.py
    python scripts/build_ranking_results.py --sport XC
    python scripts/build_ranking_results.py --since 2015-01-01

Run from the PROJECT ROOT, same as panels.py, so the sys.path lines resolve.

RUN AFTER EVERY ENGINE RUN. speed_rating changes on every solve and these
tables are a copy of it. A stale ranking_results shows stale ratings silently.

CREATE THE TABLES FIRST with migrate_rankings.sql in DBeaver (Alt+X). This
script only loads; it never touches schema.

WHY IT EXISTS
    The pool of a result is decided by poolFor(), which is Python -- string
    normalisation plus the school_levels pickle. Postgres cannot compute it, so
    there is no GROUP BY pool available and every rated row must pass through
    Python once. That is a full-corpus scan: fine once per engine run,
    impossible per page load. Same reasoning panels.py opens with.

COST
    ~15-25 minutes for 61.6M rows. Streaming cursor in, COPY out.
"""

import io
import sys
import time
import argparse
import datetime

import psycopg2.extras

sys.path.insert(0, "scripts")
from database import getConn

# poolFor is the engine's SSOT for grade -> level -> pool. IMPORT it; do not
# reimplement it in SQL. Reimplementing is how the site and the engine drift
# into disagreeing about who is a college athlete.
sys.path.insert(0, "engine")
# seasonYearFromIso comes from the same module the engine uses, for the same
# reason poolFor is imported rather than reimplemented: if the site and the
# engine disagree about which season a race is in, every board silently splits
# the athletes the engine deliberately joined.
from season_year import seasonYearFromIso, seasonYearSql, seasonYearSqlInt

try:
    from normalize_distance import poolFor
    from anchor_check import mismatch as anchorMismatch
    # ★ THE SAME READER THE ENGINE AND anchor_check USE. Track keeps its
    #   distance in the event name and event_parse is where that is read; a
    #   second copy of that logic here would be a third way to get it wrong.
    from event_parse import distanceFromEventShort
    from dbfast import tuneSession
    from pool_ceiling import ceilingFor
    from pool_resolve import resolvePool, inScope
except ImportError as exc:
    raise SystemExit(
        f"Could not import poolFor ({exc}).\n"
        "Fix the import path at the top of this file. Do NOT reimplement the "
        "pool logic here -- it must stay identical to the engine's."
    )


# Rows streamed per network round-trip. Big enough that the round-trip cost
# disappears, small enough that a batch fits comfortably in memory.
_FETCH_BATCH = 50_000

# Rows buffered before each COPY. COPY has fixed per-call overhead, so batching
# amortises it; past ~200k the payload string costs more than it saves.
_COPY_BATCH = 200_000

# Sanity rails, same as panels.py. Ratings are pool-relative with 100 = pool
# mean and real values span roughly 30-170. Anything outside is a fossil row
# (observed: speed_rating 7528 on a 20.6s time) and must never reach a board.
_RATING_MIN = 20.0
_RATING_MAX = 200.0


# ------------------------------------------------------------------ #
#  1. SOURCE QUERIES
# ------------------------------------------------------------------ #
#
# The joins mirror the ENGINE's exactly:
#   XC  ->  meets     ON div_id + source
#   TF  ->  tmp_tf_state ON meet_id + div_id + source (see prepareTfStateTemp)
#
# TF USED to need all four because meets_tf holds one row per EVENT: 14.17M rows
# over 658K (meet_id, div_id) pairs, ~21.5 each. It now reads a collapsed copy
# instead -- the only column it wanted was `state`, which is a venue property
# and identical across a meet's events. Dropping meet_id and event_id from
# a diagnostic once produced a 900-billion-row nested loop.
#
# LEFT JOIN throughout. meets is anet-only (812,079 rows, zero tfrrs), so an
# INNER join would silently delete every tfrrs XC row -- 1.13M rated results.
# A row with no meets entry still ranks; it just has no state.
#
# gender comes from the same LATERAL the engine uses, so poolFor() sees exactly
# what the engine saw. ORDER BY school + LIMIT 1 makes the pick deterministic
# when an athlete has several rows.

# ★ THE GENDER LATERAL IS THE MOST EXPENSIVE THING IN THIS SCRIPT, AND IT IS
#   RESOLVED ONCE PER ROW. It is a correlated subquery with ORDER BY + LIMIT 1
#   over `athletes`, executed 61.6M times -- once for every rated result --
#   even though `athletes` has only ~18M rows and one answer per person.
#
#   prepareGenderTemp() below collapses it to ONE pass with DISTINCT ON, into
#   an indexed temp table. The per-row lateral becomes a hash join.
#
#   ⚠ THE TIE-BREAK MUST NOT CHANGE. `ORDER BY a.school LIMIT 1` is what makes
#     the pick deterministic when an athlete has several rows, and the engine
#     uses the same ordering. DISTINCT ON with the same ORDER BY reproduces it
#     exactly -- a different tie-break would silently repool athletes.
_GENDER_TEMP_SQL = """
    DROP TABLE IF EXISTS tmp_person_gender;
    CREATE TEMP TABLE tmp_person_gender AS
    SELECT DISTINCT ON (person_id) person_id, gender
    FROM (
        SELECT COALESCE(a.athlete_id, a.athlete_id) AS person_id,
               a.gender, a.school
        FROM   athletes a
        WHERE  a.gender IN ('M', 'F')
    ) s
    ORDER BY person_id, school;
    CREATE UNIQUE INDEX ON tmp_person_gender (person_id);
    ANALYZE tmp_person_gender;
"""

_GENDER_JOIN = """
        LEFT JOIN tmp_person_gender a
               ON a.person_id = COALESCE(r.person_id, r.athlete_id)
"""


def prepareGenderTemp(conn):
    """One pass over `athletes`, so the per-row lateral becomes a hash join.

    Same tie-break as the lateral it replaces (ORDER BY school, first row
    wins), so every athlete resolves to the gender they resolved to before.
    """
    with conn.cursor() as cur:
        cur.execute(_GENDER_TEMP_SQL)
        cur.execute("SELECT count(*) FROM tmp_person_gender")
        n = cur.fetchone()[0]
    conn.commit()
    print(f"  gender index: {n:,} people (built once, replaces a per-row lateral)")


_TF_STATE_TEMP_SQL = """
    DROP TABLE IF EXISTS tmp_tf_state;
    CREATE TEMP TABLE tmp_tf_state AS
        SELECT DISTINCT ON (meet_id, div_id, source)
               meet_id, div_id, source, state
        FROM   meets_tf
        WHERE  state IS NOT NULL
        ORDER  BY meet_id, div_id, source, state;
    CREATE INDEX ON tmp_tf_state (meet_id, div_id, source);
    ANALYZE tmp_tf_state;
"""


def prepareTfStateTemp(conn):
    """One pass over meets_tf, so the TF query stops paying for its EVENT axis.

    ★ THE ONLY COLUMN TF WANTED FROM meets_tf WAS `state`, AND STATE IS A
      PROPERTY OF THE VENUE. meets_tf holds one row per EVENT -- 14.17M rows
      over 658K (meet_id, div_id) pairs, ~21.5 each -- so the query joined
      four keys into a table 21.5x larger than the fact it needed. The event
      axis was carried purely to avoid the fan-out that dropping it causes.

      Collapsing first removes both costs at once: the probe table shrinks by
      ~21.5x, the join key drops from four columns to three, and there is no
      fan-out because the temp table is unique on that key. XC was always
      fast because `meets` has no event axis to pay for.

    ⚠ DISTINCT ON, NOT A BARE GROUP BY. If two events at one (meet, div,
      source) ever disagree about state, DISTINCT ON takes one
      deterministically rather than multiplying rows or erroring. Disagreement
      would be a data fault -- a venue does not move between events -- and
      silently picking one is the same answer the old join gave, minus 21
      identical copies of it.
    """
    with conn.cursor() as cur:
        cur.execute(_TF_STATE_TEMP_SQL)
        cur.execute("SELECT count(*) FROM tmp_tf_state")
        n = cur.fetchone()[0]
    conn.commit()
    print(f"  TF state index: {n:,} meet-divisions "
          f"(built once, replaces a 14.17M-row 4-key join)")


_GENDER_LATERAL = """
        LEFT JOIN LATERAL (
               SELECT a.gender FROM athletes a
               WHERE a.athlete_id = COALESCE(r.person_id, r.athlete_id)
                 AND a.gender IN ('M', 'F')
               ORDER BY a.school
               LIMIT 1
        ) a ON TRUE
"""

# ★ THE SEASON VERDICT. Without this join poolFor is called with
#   season_level=None, which its own docstring calls "byte-for-byte the old
#   one" -- i.e. the site runs the version from before athlete_season_level
#   existed, and pools athletes on a different basis than the engine that
#   rated them. See the module docstring.
# The four gates poolOf applies AFTER poolFor returns. Without them the site
# promotes on the tfrrs class label alone, which is the ~332k hs<->college
# disagreement -- the engine waits for an actual collegiate race, by DATE.
_GATE_JOINS = """
        -- * ONE JOIN, BOTH FACTS. grade_fix carries the resolved grade or
        --   level AND, by its existence, the fact that the recorded grade is
        --   not the one to use. grade_untrusted holds the same keys, so
        --   joining it as well would be a second thing to keep in step.
        LEFT JOIN grade_fix gu
               ON gu.person_id = r.person_id
              -- ! THE ACADEMIC SEASON, NOT THE CALENDAR YEAR, AND THE SPORT
              --   DECIDES WHICH. grade_fix is keyed on the school year: a
              --   calendar year holds two of them for anyone who graduates.
              --   Dylan Weniger ran fifteen races as grade 12 through May
              --   2025, then 2025-12-13 as Fr -- one calendar year, and the
              --   majority handed his first collegiate race grade 12.
              --
              AND gu.season = ({season})::int
        LEFT JOIN pro_athlete_season pas
               ON pas.person_id = r.person_id
              -- ! ACADEMIC, NOT CALENDAR. pro_flag writes this table on the
              --   academic year now, same as grade_fix. substring(date,1,4)
              --   would have missed every spring race silently.
              AND pas.season = ({season})::int
        -- ★ THE TWO PROMOTION-GATE JOINS ARE GONE. resolvePool no longer
        --   consults college_first_season or upperclass_first_season -- they
        --   promoted on a per-person date with no reference to the row's own
        --   grade, and both were measured putting middle schoolers on the
        --   college board. Keeping them cost two index probes and two output
        --   columns on every one of 61.6M rows, to produce arguments the
        --   decision function now discards.
"""


def _gateJoins(sport):
    """The gate joins for one sport.

    ! THE GRADE SEASON IS THE SAME FOR BOTH SPORTS. It rolls in August because
      that is when school starts, and an athlete has ONE grade that year
      whether they are running cross country or track. An earlier version made
      this per-sport by reusing seasonYearSql, which rolls TF at October for
      the indoor campaign -- and that put a freshman's autumn races beside his
      eighth-grade spring.

      The argument stays so the two call sites keep reading symmetrically and
      so a future per-sport need has somewhere to go.
    """
    return _GATE_JOINS.replace("{season}", seasonYearSql(sport, "r.date"))

def _seasonLevelJoin(sport):
    """The athlete_season_level joins for one sport.

    ⚠ TWO EQUALITY JOINS, NOT A LATERAL. The first version used
      LEFT JOIN LATERAL (... ORDER BY (sport = ...) DESC LIMIT 1), which
      Postgres executes ONCE PER ROW with a sort each time. Against 39M XC
      rows and an athlete_season_level that had just tripled to 61.8M, that
      turned a four-minute panels run into three and a half hours for one
      sport. Both of these hit the (person_id, ay, sport) primary key
      directly, so the planner can hash or index-nested-loop them once.

      COALESCE at the SELECT picks the sport-specific verdict when it exists
      and the combined 'ALL' row otherwise -- identical semantics to the
      LATERAL's ORDER BY, without the per-row work.

    ! THE SEASON KEY COMES FROM season_year, NOT A LOCAL CASE. This join used
      to hard-code a JULY seam while grade_fix (three lines up in the same
      query) joined on AUGUST -- two seams in one statement, and neither
      matched how the engine looked the table up. season_level._academicYearExpr
      now writes `ay` on this same expression, so writer, engine and this
      join cannot drift again.
    """
    ay = seasonYearSqlInt(None, "r.date")
    return f"""
        LEFT JOIN athlete_season_level asl_s
               ON asl_s.person_id = r.person_id
              AND asl_s.sport = '{sport}'
              AND asl_s.ay = {ay}
        LEFT JOIN athlete_season_level asl_a
               ON asl_a.person_id = r.person_id
              AND asl_a.sport = 'ALL'
              AND asl_a.ay = {ay}
"""

# ! XC HAS NO EVENT DIMENSION. Its race page is /race/xc/<meet>/<div>, two
#   parts, so the column exists only to keep the two UNION halves the same
#   shape -- and NULL is the honest value rather than a zero that would look
#   like an event.
_EVENT_ID = "NULL::bigint"

_SQL = {
    "XC": f"""
        SELECT r.result_id, r.person_id, r.speed_rating, r.date,
               r.grade, r.source, r.school, r.time_seconds,
               -- ! FOR THE ANCHOR GATE IN poolOf. Without this column the
               --   check reads None on every row and silently never fires,
               --   which is worse than not having it: the build would report
               --   a gate that is not gating.
               r.normalized_time,
               r.meet_id, r.div_id, r.canon_meet_id,
               COALESCE(dov.distance, m.distance) AS distance,
               -- ★ THE EVENT, FOR THE RACE LINK. A TF race page is
               --   /race/tf/<meet>/<event>/<div> -- three parts -- and
               --   without this column the frontend can only build two, which
               --   matches no route and 404s. XC has no event dimension and
               --   emits NULL.
               {_EVENT_ID} AS event_id,
               m.state, a.gender, COALESCE(asl_s.level, asl_a.level) AS season_level,
               (gu.person_id IS NOT NULL)  AS grade_untrusted,
                   gu.grade                    AS fixed_grade,
                   gu.level                    AS fixed_level,
                   -- ! THE METHOD, so rule 6's explicit 'no_evidence' can be
                   --   told apart from a verdict that merely has no grade.
                   gu.method                   AS grade_verdict,
                   -- ! TRUST. A verdict can be the best reading of the
                   --   evidence and still be too thin to headline a board.
                   COALESCE(gu.trust, 'high')  AS grade_trust,
               (pas.person_id IS NOT NULL) AS is_pro
        FROM results r
        LEFT JOIN meets m
               ON m.div_id = r.div_id AND m.source = r.source
        -- ★ THE DISTANCE, FOR THE PR BOARDS. A rating is comparable across
        --   distances by construction; a TIME is not, so a board of best 5000s
        --   needs to know which races were 5000s. Carrying it here costs one
        --   real per row and saves a 61.6M-row join at query time.
        --
        -- ⚠ THE OVERRIDE WINS, and that is the whole reason this is a
        --   COALESCE rather than m.distance. dist_override is where a
        --   corrected distance lives -- 4,154 divisions of them -- and a PR
        --   board reading the stored value would rank times at distances the
        --   corpus has already decided were wrong.
        LEFT JOIN dist_override dov
               ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
        {_GENDER_JOIN}{_seasonLevelJoin("XC")}{_gateJoins("XC")}
        WHERE r.speed_rating IS NOT NULL
          AND r.person_id IS NOT NULL
          AND r.date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}$'
          AND r.date >= %(since)s
    """,
    "TF": f"""
        SELECT r.result_id, r.person_id, r.speed_rating, r.date,
               r.grade, r.source, r.school, r.time_seconds,
               -- ! FOR THE ANCHOR GATE IN poolOf. Without this column the
               --   check reads None on every row and silently never fires,
               --   which is worse than not having it: the build would report
               --   a gate that is not gating.
               r.normalized_time,
               r.meet_id, r.div_id, r.canon_meet_id,
               -- ⚠ NO m.distance ON THIS SIDE. `m` here is tmp_tf_state, a
               --   collapse of meets_tf on (meet, div, source) -- and it
               --   cannot carry a distance even in principle, because on
               --   track the distance belongs to the EVENT, not the meeting.
               --   The 800 and the 3200 at one meet are one row of
               --   tmp_tf_state and two distances. This query asked for
               --   m.distance anyway and had done since that collapse landed;
               --   it failed the first time a TF build ran afterwards, with
               --   "column m.distance does not exist".
               --
               -- ★ SO TRACK TAKES ITS DISTANCE FROM THE EVENT NAME, which is
               --   where track keeps it. event_parse.distanceFromEventShort
               --   is the one reader of that -- anchor_check already uses it
               --   for exactly this reason -- and prepareRow calls it below,
               --   memoised on the event string.
               dov.distance AS distance,
               r.event_short,
               -- ★ THE EVENT, FOR THE RACE LINK. A TF race page is
               --   /race/tf/<meet>/<event>/<div> -- three parts -- and
               --   without this column the frontend can only build two, which
               --   matches no route and 404s. XC has no event dimension and
               --   emits NULL.
               COALESCE(r.event_id, -1) AS event_id,
               m.state, a.gender, COALESCE(asl_s.level, asl_a.level) AS season_level,
               (gu.person_id IS NOT NULL)  AS grade_untrusted,
                   gu.grade                    AS fixed_grade,
                   gu.level                    AS fixed_level,
                   -- ! THE METHOD, so rule 6's explicit 'no_evidence' can be
                   --   told apart from a verdict that merely has no grade.
                   gu.method                   AS grade_verdict,
                   -- ! TRUST. A verdict can be the best reading of the
                   --   evidence and still be too thin to headline a board.
                   COALESCE(gu.trust, 'high')  AS grade_trust,
               (pas.person_id IS NOT NULL) AS is_pro
        FROM results_tf r
        LEFT JOIN dist_override dov
               ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
        -- ! THREE KEYS INTO A COLLAPSED TABLE, NOT FOUR INTO meets_tf. The
        --   event_id was only ever there to stop the ~21.5x fan-out that
        --   meets_tf's per-event grain causes; the one column this query
        --   reads -- state -- does not vary across events. See
        --   prepareTfStateTemp.
        LEFT JOIN tmp_tf_state m
               ON m.meet_id = r.meet_id
              AND m.div_id  = r.div_id
              AND m.source  = r.source
        {_GENDER_JOIN}{_seasonLevelJoin("TF")}{_gateJoins("TF")}
        WHERE r.speed_rating IS NOT NULL
          AND r.person_id IS NOT NULL
          AND COALESCE(r.is_relay, 0) = 0
          AND COALESCE(r.is_field, 0) = 0
          AND r.date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}$'
          AND r.date >= %(since)s
    """,
}

_COLUMNS = ("sport", "result_id", "person_id", "pool", "speed_rating",
            "race_date", "year", "state", "school", "grade",
            "meet_id", "div_id", "canon_meet_id", "time_seconds",
            # ! LAST, so an older ranking_results is a column short rather
            #   than a column SHIFTED. COPY matches by position.
            "distance", "event_id")


# ------------------------------------------------------------------ #
#  2. ROW FILTERING
# ------------------------------------------------------------------ #

# Schools that are countries or pro clubs. A pro's grade field is stale --
# Eduardo Herrera reads grade 12 while racing for Mexico -- so the grade cannot
# be trusted and the row is dropped rather than pooled as a high schooler.
#
# Imported from panels.py so one edit covers both. The fallback keeps this
# script runnable if panels.py moves; it is deliberately permissive, because
# dropping nothing is safer than dropping the wrong rows.
try:
    sys.path.insert(0, "racecast")
    from panels import _is_non_school, _is_dodea
except ImportError:
    def _is_non_school(school):
        return False

    def _is_dodea(school):
        return False


# ! MEMOISED ON THE EVENT STRING, NOT PER ROW. distanceFromEventShort is a
#   dict lookup and then a parse; there are a few thousand distinct event
#   names against ~24M track rows, so caching turns 24M parses into a few
#   thousand. The cache is keyed on exactly what the query returns, including
#   None, which resolves to None once rather than being re-parsed forever.
_TF_DISTANCE = {}


def _tfDistance(event_short):
    """Metres for a track event name, or None. See event_parse."""
    if event_short in _TF_DISTANCE:
        return _TF_DISTANCE[event_short]
    metres = None
    if event_short:
        got = distanceFromEventShort(event_short)
        # ! (distance, gender) -- only the first is wanted here.
        metres = got[0] if isinstance(got, (tuple, list)) else got
    _TF_DISTANCE[event_short] = metres
    return metres


def _asDate(text):
    """'YYYY-MM-DD' -> date, for the college/upperclass gates.

    ⚠ THOSE GATES COMPARE BY DATE, NOT YEAR, and the reason is load-bearing: a
      senior's spring high-school track season and their first autumn of
      college share a calendar year. Comparing years promoted 288,264 genuine
      high-school athlete-seasons. The SQL hands back a date for first_date,
      so the row's own date has to be one too or the comparison raises.
    """
    return datetime.date(int(text[:4]), int(text[5:7]), int(text[8:10]))


# ------------------------------------------------------------------ #
#  HOT-PATH CACHES
# ------------------------------------------------------------------ #
#
# This loop runs 61.6M times. Everything below is a cache or a cheaper
# spelling of the same computation -- none of it changes a single output row.

# ⚠ NO poolFor MEMOISATION HERE, AND THAT IS DELIBERATE. It was tried and
#   benchmarked at production cardinality -- 61.6M rows over ~300k distinct
#   schools, so each cache key recurs ~200 times -- and it LOST: 0.60s against
#   0.40s at a 59% hit rate. The key is a five-tuple containing two strings,
#   and building it plus a dict probe costs more than the call it avoids.
#   speed_ratings memoises because it also passes through a much hotter loop;
#   here it is pure overhead. Do not "optimise" this back in without a
#   measurement.

# _is_non_school scans a string against a list of country and club names. The
# same few hundred thousand school strings recur across 61.6M rows, so the
# answer is worth remembering.
_SCHOOL_OK = {}


def _isNonSchoolCached(school):
    hit = _SCHOOL_OK.get(school, None)
    if hit is None:
        hit = _is_non_school(school)
        _SCHOOL_OK[school] = hit
    return hit


# COPY's TEXT format needs four characters escaped, and essentially no row
# contains any of them. Measured over 2M strings: four chained .replace() calls
# 0.36s, str.translate 2.02s (it always allocates), a membership guard that
# returns the original string untouched 0.27s. The guard wins because the
# common case does no work at all.
_COPY_ESCAPES = str.maketrans({
    "\\": "\\\\",
    "\t": "\\t",
    "\n": "\\n",
    "\r": "\\r",
})


# How much higher a SINGLE RACE may rate than the pool's season ceiling.
# A season mean averages an athlete's good days with their bad ones, so one
# race legitimately sits above it -- but not by half again, which is what a
# mis-anchored row does. 10 points is about 7% at these ceilings.
#
# ⚠ NOT MEASURED YET, AND SAYING SO. The season ceilings in pool_ceiling.py
#   were set from audit_pool_ceilings against the corpus; this margin is a
#   first guess on top of them. audit_pool_ceilings --races prints the
#   single-race distribution and the athletes each candidate would drop, and
#   this number should be set from that the same way the others were.
RACE_MARGIN = 10.0


def raceCeiling(pool):
    """The highest rating one RACE may carry and still reach a board."""
    return ceilingFor(pool) + RACE_MARGIN


# What the two rails did, per sport, so a build that stops gating says so.
_GATE = {"XC": {"checked": 0, "mismatched": 0, "unchecked": 0,
                "outside_pool": 0},
         "TF": {"checked": 0, "mismatched": 0, "unchecked": 0,
                "outside_pool": 0}}


def isRankablePool(pool):
    """unknown_gender pools exist but hold 9 athletes corpus-wide. Not a board."""
    return bool(pool) and not pool.endswith("_unknown_gender")


def prepareRow(row, sport):
    """One DB row -> one COPY tuple, or None to drop it.

    ★ `row` IS A NAMEDTUPLE, NOT A DICT, AND THAT IS WORTH MINUTES. Measured
      over 2M rows from a real server-side cursor: RealDictCursor 11.8s,
      DictCursor 9.9s, NamedTupleCursor 3.3s, a bare tuple 2.9s. At 61.6M rows
      the dict version spends about five minutes of the build building one
      dictionary per row and throwing it away. The namedtuple keeps the field
      names -- row.school still reads as row.school -- for 0.4s over the
      fastest option there is.

    DROPS, and why none of them is silent data loss:
      non-school       grade is untrustworthy, so the pool would be WRONG
                       rather than missing.
      unrankable pool  unknown_gender, 9 athletes corpus-wide.
      out of rails     fossil rows from an older engine.

    `year` is the SEASON year, not the calendar year, and it is stored rather
    than derived for two reasons now. It is the commonest filter, so an int
    column beats an EXTRACT on every row of every query -- and it is no longer
    recoverable from race_date alone: a TF race in Nov/Dec belongs to the
    following season, so deriving it needs the sport and the rule as well.
    EXTRACT(year FROM race_date) IS NO LONGER EQUIVALENT to this column; any
    SQL that assumes it is will be wrong for TF by one year.
    """
    school = row.school
    if _isNonSchoolCached(school):
        return None
    # Hidden, not corrected -- see panels._DODEA_SCHOOLS.
    if _is_dodea(school):
        return None

    # ! US ONLY, AND THIS WAS MISSING WHILE panels.py HAD IT. The two boards
    #   are meant to agree; filtering one and not the other is how the
    #   rankings page ends up showing New Zealand and Canadian athletes that
    #   the homepage does not.
    #
    #   inScope keeps anything it cannot PROVE is foreign, including a null
    #   state, so this removes only what the data is explicit about.
    if not inScope(row.state):
        return None

    # ★ THE ENGINE'S DECISION, NOT A REIMPLEMENTATION OF IT. resolvePool is
    #   the same function speed_ratings.poolOf calls; the five facts come from
    #   the joins above instead of from the engine's in-memory dicts.
    #
    #   merge=True: this table stores the pool WITHOUT the sport suffix, since
    #   `sport` is already its own column.
    # ★ THE SEASON, COMPUTED ONCE AND USED TWICE. resolvePool needs it for the
    #   hand-listed professionals (see pool_resolve._PRO_SEASONS, which is per
    #   athlete-season now, not per person), and the row's own `year` column
    #   is the same number. seasonYearFromIso is memoised, so this is a dict
    #   hit rather than a second parse.
    season = seasonYearFromIso(sport, row.date)

    pool = resolvePool(row.grade, row.gender, row.source, school,
                       sport,
                       season=season,
                       season_level=row.season_level,
                       grade_untrusted=bool(row.grade_untrusted),
                       fixed_grade=row.fixed_grade,
                       fixed_level=row.fixed_level,
                       grade_verdict=row.grade_verdict,
                       # ! FOR _PRO_PEOPLE -- see pool_resolve.
                       person_id=row.person_id,
                       is_pro=bool(row.is_pro),
                       # ★ NO race_date, AND NO DATE PARSE AT ALL. Its only
                       #   readers were the two promotion gates, now gone. This
                       #   call was already lazy about building the date; now
                       #   it does not build one -- up to 61.6M _asDate calls
                       #   removed outright.
                       merge=True)
    if not isRankablePool(pool):
        return None

    # ★ UNTRUSTED SEASONS ARE RATED BUT NOT RANKED, AND THIS IS THE ONLY
    #   PLACE THAT DISTINCTION IS ENFORCED.
    #
    #   ranking_results is the BOARDS table. results.speed_rating is written
    #   separately by the engine and is what an athlete's own page shows, so
    #   skipping a row here removes it from every ranking while leaving its
    #   rating intact where the athlete can see it.
    #
    #   Measured: Dominic Colussi has five seasons of ONE RACE each, every
    #   verdict a field verdict, and was ranked as a collegian since 2018 --
    #   from a middle school. His level is a guess the evidence permits; a
    #   national board is not the place to publish a guess.
    #
    # ⚠ COALESCE'd TO 'high' IN THE QUERY, so a grade_fix written before this
    #   column existed ranks everything exactly as it used to rather than
    #   ranking nothing.
    if row.grade_trust == "low":
        return None

    # ★ THE TWO STAGES MUST HAVE USED THE SAME POOL, OR THE RATING IS ON THE
    #   WRONG SCALE AND THE ROW IS NOT A FACT ABOUT THE ATHLETE.
    #
    #   normalized_time was written by the backfill with the pool it decided
    #   THEN; speed_rating divides by the pool mean of the pool the solve
    #   decided LATER. Those two are computed by different code from facts
    #   that can change in between -- a grade_fix verdict, a season_level
    #   verdict, a school that acquired a level -- and until now nothing
    #   checked they agree.
    #
    # ⚠ THE ANCHOR IS PER POOL, so disagreeing is not a rounding difference.
    #   normalize_distance.targetFor anchors ms at 3200m and hs at 5000m, so a
    #   3200m-anchored ability over a 5000m-anchored mean is inflated about
    #   1.64x. Person 29346285, an eighth grader with no recorded grade, rated
    #   187 in hs_m on races his own exponents place squarely at the ms
    #   anchor; on one scale he is a 112.
    #
    # ! CHECKED BY RECOMPUTING, NOT BY INFERRING. normalizeTime is
    #   deterministic given the row's own time and distance, so running it
    #   with the pool the row is RATED in and comparing settles it -- no
    #   exponent recovered, no anchor guessed. See engine/anchor_check.py,
    #   which is the same function and can measure the corpus before this
    #   drops anything.
    #
    # ! AND IT DROPS FROM THE BOARDS ONLY, exactly like grade_trust='low'
    #   above. results.speed_rating is untouched, so the athlete's own page
    #   still shows what the engine computed; what is refused is a place in a
    #   national ranking built on a scale the row was never measured on.
    # ★ TRACK'S DISTANCE COMES FROM THE EVENT NAME, and it has to be resolved
    #   BEFORE the gate below, which cannot check a row whose distance is
    #   None -- it would return "not a finding" on every TF row and the gate
    #   would silently never fire. A hand-corrected override still wins.
    distance = row.distance
    if distance is None and sport == "TF":
        # ! getattr, NOT row.event_short: the XC query does not select it, and
        #   a namedtuple has no attribute it was not given. The TF guard is
        #   already there; this is belt and braces against the two SELECT
        #   lists drifting apart again.
        distance = _tfDistance(getattr(row, "event_short", None))

    # ⚠ AND IT COUNTS WHAT IT COULD NOT CHECK. mismatch() returns "not a
    #   finding" when the row has no distance -- the honest answer to an
    #   unanswerable question, but it means the row is PUBLISHED UNCHECKED.
    #   Cross country takes its distance from dist_override or meets, and
    #   where neither has one the gate is silently inert for that row. A
    #   build that cannot check a third of its rows and does not say so is
    #   the "gate that is not gating" this file's own comment warns about, so
    #   the counts are printed per sport at the end of buildSport.
    is_bad, _expected, ratio = anchorMismatch(row.time_seconds, distance,
                                              row.normalized_time, pool, sport)
    if ratio is None:
        _GATE[sport]["unchecked"] += 1
    elif is_bad:
        _GATE[sport]["mismatched"] += 1
        return None
    else:
        _GATE[sport]["checked"] += 1

    rating = float(row.speed_rating)
    if not (_RATING_MIN <= rating <= _RATING_MAX):
        return None

    # ★ AND A RATING OUTSIDE ITS OWN POOL IS A POOLING ERROR, NOT A RECORD.
    #   The rail above is a FOSSIL: 20..200 exists to catch a speed_rating of
    #   7528 on a 20-second time, and 159 sails through it in every pool
    #   because 159 is a real number for somebody -- just not for a high
    #   schooler running 21:32 for three miles.
    #
    # ⚠ THE BOARDS WERE FULL OF EXACTLY THAT. The whole top-25 of the 2025
    #   hs_m XC board was one meet, times 21:32 to 21:55, every one rated
    #   157-159. A 21:32 three-mile normalised on its own pool's anchor rates
    #   92. The same athlete's page showed 14:28.1 and 14:45.9 over the SAME
    #   3219m course rated 177.2 and 108.1 -- a ratio of 1.639, which is the
    #   ms-against-hs anchor ratio and nothing else. The anchor gate above is
    #   meant to catch that, and cannot when the row has no distance to
    #   recompute with.
    #
    # ! SO THIS IS THE RAIL THAT DOES NOT NEED A DISTANCE. It is the same
    #   ceiling build_team_season applies per athlete-season and the same one
    #   audit_pool_ceilings measures, applied per RACE -- and it drops from
    #   the BOARDS only, exactly like grade_trust='low' and the anchor gate.
    #   The athlete's own page still shows what the engine computed.
    #
    # ⚠ A SEASON MEAN AND A SINGLE RACE ARE NOT THE SAME DISTRIBUTION, which
    #   is why this is not simply ceilingFor(pool). One race can beat a
    #   season average, so the line sits RACE_MARGIN above it. Measure the
    #   single-race distribution with audit_pool_ceilings --races before
    #   moving it, and read the names it drops: a real athlete above the line
    #   means the line is wrong.
    if rating > raceCeiling(pool):
        _GATE[sport]["outside_pool"] += 1
        return None

    date_text = row.date

    return (sport,
            row.result_id,
            row.person_id,
            pool,
            rating,
            date_text,                 # Postgres parses YYYY-MM-DD directly
            # SEASON year, not calendar year. A TF race in Nov/Dec belongs to
            # the season ahead (indoor opens then), so `year` here matches the
            # engine's grouping. athlete_season's GROUP BY ... year inherits
            # it, which is what stops one real season showing as two rows on
            # the boards. XC is unaffected -- see season_year.py.
            season,
            row.state,
            school,
            row.grade,
            row.meet_id,
            row.div_id,
            row.canon_meet_id,
            row.time_seconds,
            distance,
            row.event_id)


# ------------------------------------------------------------------ #
#  3. WRITING
# ------------------------------------------------------------------ #

def copyField(value):
    """Render one value for COPY's TEXT format.

    COPY's NULL marker is the two characters \\N. str(None) is the literal
    "None", which Postgres rejects against any typed column -- that exact bug
    killed an engine run when a NULL canonical_id reached an integer column.

    Tab, newline and backslash are field and row separators, so they must be
    escaped in free text. School names contain all three.
    """
    if value is None:
        return "\\N"
    if type(value) is str:
        if "\\" in value or "\t" in value or "\n" in value or "\r" in value:
            return value.translate(_COPY_ESCAPES)
        return value
    return str(value)


def copyRows(cur, rows):
    """Push one batch with a single COPY.

    COPY bypasses the SQL parser and planner. execute_values would build a
    multi-row VALUES literal that Postgres re-parses on every call -- an order
    of magnitude slower at this volume.

    "".join(...) builds the payload in ONE allocation; repeated `s += ...`
    would be quadratic in the batch size.
    """
    # map, not a generator expression: 401k rows/s against 341k, measured over
    # 200k rows of the real shape. Identical bytes; one less frame per row.
    payload = "".join("\t".join(map(copyField, row)) + "\n" for row in rows)
    cur.copy_expert(
        f"COPY {_LOAD_TABLE} ({', '.join(_COLUMNS)}) FROM STDIN",
        io.StringIO(payload))


def buildSport(conn, sport, since, stats):
    """Stream one sport's rated results and COPY the rankable ones.

    A NAMED cursor is server-side: Postgres holds the result set and ships it
    in batches instead of materialising 34M rows in client memory. Named
    cursors must live inside a transaction, which `with getConn()` provides.

    Read and write cursors are separate -- a named cursor is mid-fetch and
    cannot issue other statements.
    """
    print(f"\n  {sport}: streaming...")
    buffer = []
    seen = 0

    with conn.cursor(name=f"rank_src_{sport.lower()}",
                     cursor_factory=psycopg2.extras.NamedTupleCursor) as src:
        src.itersize = _FETCH_BATCH
        src.execute(_SQL[sport], {"since": since})

        with conn.cursor() as dst:
            for row in src:
                seen += 1
                prepared = prepareRow(row, sport)
                if prepared is None:
                    stats[f"{sport}_dropped"] += 1
                    continue

                buffer.append(prepared)
                if len(buffer) >= _COPY_BATCH:
                    copyRows(dst, buffer)
                    stats[f"{sport}_written"] += len(buffer)
                    buffer.clear()
                    print(f"    {sport}: {seen:,} read, "
                          f"{stats[f'{sport}_written']:,} written")

            if buffer:
                copyRows(dst, buffer)
                stats[f"{sport}_written"] += len(buffer)

    stats[f"{sport}_read"] = seen
    # ⚠ SAY HOW MUCH OF TRACK GOT A DISTANCE AT ALL. It now comes from the
    #   event name, and an event name the parser does not recognise leaves the
    #   row with no distance -- which silently costs it a place on the
    #   distance-filtered PR boards AND makes the anchor gate unanswerable for
    #   it. A number here is how that gets noticed; event_parse's own header
    #   records 7.1M rows once dropped by a narrower reader.
    if sport == "TF" and _TF_DISTANCE:
        unknown = sorted(k for k, v in _TF_DISTANCE.items() if v is None and k)
        print(f"    TF distances: {len(_TF_DISTANCE) - len(unknown):,} of "
              f"{len(_TF_DISTANCE):,} distinct event names parsed"
              + (f" -- unparsed: {', '.join(unknown[:6])}"
                 f"{' ...' if len(unknown) > 6 else ''}" if unknown else ""))
    g = _GATE[sport]
    total = g["checked"] + g["mismatched"] + g["unchecked"]
    if total:
        print(f"    anchor gate: {g['checked']:,} checked, "
              f"{g['mismatched']:,} dropped as mis-anchored, "
              f"{g['unchecked']:,} UNCHECKED "
              f"({100.0 * g['unchecked'] / total:.1f}% -- no distance, so the "
              f"gate could not fire on them)")
    if g["outside_pool"]:
        print(f"    pool ceiling: {g['outside_pool']:,} races dropped as "
              f"implausible for their pool (see RACE_MARGIN)")
    print(f"    {sport}: {seen:,} read, {stats[f'{sport}_written']:,} written, "
          f"{stats[f'{sport}_dropped']:,} dropped")


# ===================================================================== #
#  3b. SHADOW TABLES
# ===================================================================== #
#
# Everything is loaded into a shadow copy and swapped in at the end, so the
# live tables are never empty or half-full while a load is running.

_LOAD_TABLE = "ranking_results_new"
_LOAD_SEASON = "athlete_season_new"


def createShadow(conn, name, like):
    """Fresh empty `name` shaped exactly like `like`.

    Defaults and constraints are copied; the indexes are NOT. See below. The COPY
    runs against an INDEXED table -- which sounds slow, but it is exactly what
    happens today: TRUNCATE does not drop indexes, so the current load already
    pays that cost. Same speed, no regression, and no need for this script to
    know the schema (which lives in migrate_rankings.sql, not here).

    The copied indexes get auto-generated names based on `name`, so after the
    swap the live table's indexes are called ranking_results_new_*. Cosmetic
    only -- Postgres does not care, and renaming them would add failure modes
    to the one step that must not fail.
    """
    # ! NOT "INCLUDING ALL". THAT BUILT THE INDEXES BEFORE THE LOAD.
    #
    #   With every index present, each of the 61.6M COPYed rows maintains
    #   every one of them as it lands: random page writes per row, WAL for
    #   each index entry, and page splits throughout. Building them AFTER the
    #   data is in is a sequential scan plus a sort per index, which Postgres
    #   does far faster and with far less WAL.
    #
    #   DEFAULTS and CONSTRAINTS are still copied, because those affect what
    #   the COPY is allowed to write. Only the indexes are deferred.
    # ★ THE SHADOW IS SHAPED FROM THE LIVE TABLE, so a column this script
    #   writes must exist there FIRST or the COPY fails on a count mismatch --
    #   and it fails after the 61.6M-row read, not before it.
    #
    #   `distance` was added to _COLUMNS for the PR boards. Adding it here,
    #   idempotently, means the first run after that change migrates the
    #   schema instead of dying on it, and every later run is a no-op.
    #
    # ⚠ ON THE LIVE TABLE, NOT THE SHADOW. The shadow is created from it a
    #   line later; adding the column to the shadow alone would be undone by
    #   the next swap.
    with conn.cursor() as cur:
        cur.execute(f"""
            ALTER TABLE IF EXISTS {like}
            ADD COLUMN IF NOT EXISTS distance real
        """)
        cur.execute(f"""
            ALTER TABLE IF EXISTS {like}
            ADD COLUMN IF NOT EXISTS event_id bigint
        """)
        cur.execute(f"DROP TABLE IF EXISTS {name}")

        # ! UNLOGGED, AND THIS IS THE BIGGEST SINGLE WIN AVAILABLE HERE. A
        #   logged table writes every one of the 61.6M rows TWICE: once to the
        #   write-ahead log and once to the heap. UNLOGGED writes it once.
        #
        #   The trade is that an UNLOGGED table does not survive a crash. That
        #   is exactly right for this one: it is a shadow that is dropped and
        #   rebuilt from `results` on every run, so a crash means re-running
        #   the script, which is what a crash means anyway.
        #
        # ⚠ IT MUST BE SET BACK TO LOGGED BEFORE THE SWAP, or the live
        #   ranking_results stops being crash-safe. See swapIn.
        cur.execute(f"CREATE UNLOGGED TABLE {name} "
                    f"(LIKE {like} INCLUDING DEFAULTS INCLUDING CONSTRAINTS)")

        # Index builds sort; the default 64MB spills a 61.6M row sort to disk.
        cur.execute("SET maintenance_work_mem = '2GB'")
        # Nothing here needs to survive a crash: the whole table is rebuilt.
        cur.execute("SET synchronous_commit = off")
    conn.commit()


# ⚠ THE INDEXES THIS SITE CANNOT RUN WITHOUT, WRITTEN DOWN.
#
#   They used to live ONLY in the live table's catalogue, read back by
#   indexDefs and replayed onto the shadow. That works exactly as long as the
#   live table has them -- and it is self-erasing the moment it does not:
#   indexDefs returns nothing, buildIndexes returns early (it used to do so
#   SILENTLY), the shadow swaps in bare, and every run after that faithfully
#   copies "no indexes" forward. A 56M-row ranking_results with no index turns
#   every athlete page and every board into a sequential scan.
#
#   athlete_season had it worse: buildIndexes was never called for it at all,
#   so createShadow's deliberate "no indexes" was permanent. 12.9M rows,
#   scanned in full for every ability board.
#
#   So the list is here, in the code, and the catalogue is a SUPPLEMENT to it
#   rather than the source: anything added by hand on the live table is still
#   picked up and replayed, but nothing here can be lost by having been lost
#   once.
#
#   Each entry names the query that needs it. Do not add one without one.
_CANONICAL_INDEXES = {
    "ranking_results": [
        # app.py _athletePaces, conversions.athlete_paces, rankings PR ranks:
        #   WHERE person_id = %s
        ("rr_person_idx", "(person_id)"),
        # rankings performance boards: pool/sport/year equality, then
        #   ORDER BY speed_rating DESC LIMIT cand
        ("rr_board_rating_idx", "(pool, sport, year, speed_rating DESC)"),
        # rankings PR boards: same filters, ORDER BY time_seconds ASC
        ("rr_board_time_idx", "(pool, sport, year, time_seconds)"),
        # school.py: WHERE school = %s AND sport = %s ORDER BY speed_rating DESC
        ("rr_school_idx", "(school, sport, speed_rating DESC)"),
    ],
    "athlete_season": [
        # athlete pages and "where am I": WHERE person_id = %s
        ("as_person_idx", "(person_id)"),
        # rankings ability boards: WHERE n_races >= .. AND pool/sport/year,
        #   ORDER BY mean_rating DESC
        ("as_board_mean_idx", "(pool, sport, year, mean_rating DESC)"),
        # the same boards sorted on the season best instead
        ("as_board_best_idx", "(pool, sport, year, best_rating DESC)"),
    ],
}


def indexDefs(conn, like):
    """The live table's index definitions, ready to replay on the shadow.

    THE CATALOGUE PLUS THE CANONICAL LIST, not one or the other. Reading the
    catalogue keeps indexes this project added by hand; the canonical list
    keeps the ones the site cannot run without even when the live table has
    already lost them. See _CANONICAL_INDEXES.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT indexname, indexdef
            FROM   pg_indexes
            WHERE  schemaname = 'public' AND tablename = %s
        """, (like,))
        defs = list(cur.fetchall())

    # Compare on the column list, not the name: the same index built by an
    # earlier run carries an auto-generated name, and adding ours beside it
    # would build the same tree twice.
    def _cols(ddl):
        i = ddl.find("(")
        return ddl[i:].replace(" ", "").lower() if i >= 0 else ddl

    have = {_cols(ddl) for _n, ddl in defs}
    for name, cols in _CANONICAL_INDEXES.get(like, []):
        if _cols(cols) in have:
            continue
        defs.append((f"{like}_{name}",
                     f"CREATE INDEX {like}_{name} ON public.{like} {cols}"))
    return defs


def buildIndexes(conn, name, like):
    """Create the deferred indexes on the shadow, after the data is in.

    ! THE NAME IS DERIVED FROM THE LIVE ONE, NOT PREFIXED ONTO WHATEVER IS
      THERE. An earlier version prepended the shadow's name unconditionally,
      which broke twice over:

        1. A partial run leaves indexes already called ranking_results_new_*,
           so the next run prefixed them AGAIN and produced
           ranking_results_new_ranking_results_new_pkey.
        2. Postgres truncates identifiers at 63 characters, so two different
           long names collapsed to the same string and the second CREATE hit
           "relation already exists".

      Replacing the live table's name inside the index name is stable: it
      produces the same result however many times it runs.

    ! AND `CREATE INDEX IF NOT EXISTS`, so a resumed run skips what already
      built rather than failing on it. The shadow is dropped and recreated on
      a normal run, so this only matters after a crash.
    """
    defs = indexDefs(conn, like)
    if not defs:
        # Unreachable while _CANONICAL_INDEXES has an entry for this table,
        # which is the point -- but if someone empties it, say so instead of
        # swapping in a bare table and letting the site find out.
        print(f"  ⚠ NO INDEXES to build on {name}. The swapped-in table will "
              f"be scanned in full by every query that touches it.")
        return
    print(f"  building {len(defs)} indexes on {name} (deferred until after "
          f"the load)")
    with conn.cursor() as cur:
        # Session-scoped, for these index builds only. The default 64MB spills
        # a sort over 61.6M rows to disk.
        cur.execute("SET maintenance_work_mem = '2GB'")
        for idxname, ddl in defs:
            t0 = time.time()
            # The shadow's own name for this index, derived not accumulated.
            newname = (idxname if idxname.startswith(name)
                       else idxname.replace(like, name, 1)
                       if like in idxname else f"{name}_{idxname}")
            sql = ddl.replace(f" ON public.{like} ", f" ON public.{name} ")
            sql = sql.replace(f" ON {like} ", f" ON {name} ")
            sql = sql.replace(f"INDEX {idxname} ",
                              f"INDEX IF NOT EXISTS {newname} ")
            sql = sql.replace(f"INDEX IF NOT EXISTS {idxname} ",
                              f"INDEX IF NOT EXISTS {newname} ")
            cur.execute(sql)
            print(f"    [{time.time() - t0:7.1f}s] {newname[:60]}")
        cur.execute(f"ANALYZE {name}")
    conn.commit()


def seedOtherSports(conn, keep_sports):
    """Single-sport runs: copy the OTHER sport's rows into the shadow first.

    `--sport XC` used to top up in place by deleting only XC rows. With a swap
    there is no in-place, so the shadow has to start with everything we are not
    about to rebuild -- otherwise a single-sport run silently drops the other
    sport from the site.

    Server-side INSERT ... SELECT, so the rows never travel to Python.
    """
    missing = [s for s in ("XC", "TF") if s not in keep_sports]
    if not missing:
        return
    cols = ", ".join(_COLUMNS)
    with conn.cursor() as cur:
        cur.execute(f"INSERT INTO {_LOAD_TABLE} ({cols}) "
                    f"SELECT {cols} FROM ranking_results WHERE sport = ANY(%s)",
                    (missing,))
        moved = cur.rowcount
    conn.commit()
    print(f"  carried over {moved:,} {'/'.join(missing)} rows from the live table")


def swapIn(conn):
    """Four renames in ONE transaction. This is the only visible moment.

    RENAME takes ACCESS EXCLUSIVE, but it is a catalogue update -- no data
    moves, so readers block for milliseconds rather than minutes. Both tables
    swap together because athlete_season is derived from ranking_results: a
    reader must never see the new one beside the old one.

    The _old tables are dropped AFTER the commit, not inside it. Dropping 23GB
    inside the transaction would hold the exclusive lock for the length of the
    drop, which is the thing this whole function exists to avoid.
    """
    print("\n  swapping in...")
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS ranking_results_old, athlete_season_old")
        conn.commit()

        # ! BACK TO LOGGED BEFORE IT GOES LIVE. The shadow is built UNLOGGED so
        #   the 61.6M row COPY writes the heap once instead of heap plus WAL,
        #   which is the single biggest saving in this script. But an UNLOGGED
        #   table is TRUNCATED by a crash, and ranking_results is what the site
        #   reads: leaving it unlogged would trade a rebuild for an empty site.
        #
        #   This rewrites the table once, so it is not free -- but it is a
        #   sequential write with no index maintenance and no per-row overhead,
        #   which is far cheaper than having logged every insert.
        t0 = time.time()
        cur.execute(f"ALTER TABLE {_LOAD_TABLE} SET LOGGED")
        conn.commit()
        print(f"    [{time.time() - t0:7.1f}s] set logged")

        cur.execute("BEGIN")
        cur.execute("ALTER TABLE ranking_results RENAME TO ranking_results_old")
        cur.execute("ALTER TABLE athlete_season  RENAME TO athlete_season_old")
        cur.execute(f"ALTER TABLE {_LOAD_TABLE}  RENAME TO ranking_results")
        cur.execute(f"ALTER TABLE {_LOAD_SEASON} RENAME TO athlete_season")
        conn.commit()
    print("  swapped. dropping the old copies...")

    with conn.cursor() as cur:
        cur.execute("DROP TABLE ranking_results_old, athlete_season_old")
    conn.commit()
    print("  done -- the site was never served an empty board")


# athlete_season is a pure aggregate of ranking_results -- pool is already
# materialized there, so Postgres can GROUP BY it and no second Python pass is
# needed. That was the point of materializing it.
#
# THE DECAY MATCHES THE ENGINE. speed_ratings.py weights races by
# DECAY_K ** days_ago when solving ability; this does the same. decayed_rating
# still means "form at season's end" and mean_rating "the season as a whole" --
# see _ANCHOR for why the season's end no longer has to be computed to say so.
#
# Subtracting two dates in Postgres yields int days, not an interval, so
# power() takes it directly.
#
# state/school/grade are the MODE, not an arbitrary row: an athlete can change
# school mid-season, and mode() picks what they mostly were.
_DECAY_K = 0.996

# ★ THE ANCHOR CANCELS, SO THE WINDOW FUNCTION WAS NEVER NEEDED.
#
#       decayed = sum(r * k^(end - d)) / sum(k^(end - d))
#               = sum(r * k^end * k^-d) / sum(k^end * k^-d)
#
#   and k^end is CONSTANT WITHIN THE GROUP, so it divides out of both sums.
#   Any anchor gives the same number -- the athlete's own last race, or a
#   fixed date, or none at all. This used to compute the per-group maximum
#   with `max(race_date) OVER (PARTITION BY person_id, pool, sport, year)`,
#   which sorts 61.6M rows before the GROUP BY that then re-aggregates them.
#
#   Measured on 3M rows: 4.0s with the window, 2.2s without.
#
# ⚠ AND THE TWO ARE THE SAME NUMBER, THOUGH NOT ALWAYS THE SAME BITS. Summing
#   the same terms in a different order can land on a different last bit:
#   across 80,000 athlete-seasons of 7 races each, 2 of them moved, by
#   7.6e-06 on a scale of 100 -- which IS float4's resolution there, so it is
#   the smallest difference the column is able to hold. Every other group was
#   identical. The claim is "equal to the precision this column stores", not
#   "byte-identical", because the second one would be false.
#
# ! A FIXED FUTURE DATE, so every exponent is positive and every weight is at
#   most 1. Anchoring at 1990 instead would make them k^-d, which grows, and
#   a wide --since would eventually overflow. This direction shrinks: even a
#   1900 race lands at 1e-127, comfortably inside float8. A race after 2100
#   would merely make its weight exceed 1, which is still correct.
_ANCHOR = "DATE '2100-01-01'"

# ★ THE SEASON NUMBER IS AN UPPER QUANTILE, NOT A MEAN, AND THE COLUMN IS
#   STILL CALLED mean_rating. Renaming it means 58 call sites in 13 files, so
#   the name stayed and this note is the contract: mean_rating holds the
#   athlete's 80th-percentile race, not their average one.
#
# ⚠ THE MEAN REWARDED A THIN SEASON. What is on file is not a season, it is
#   whatever got scraped, and for older years that is disproportionately the
#   championship races -- the ones run all out. A full modern season carries
#   duals, tempo efforts and JV races that a championship-only season simply
#   does not have, so averaging punishes the team that raced more. Simulated
#   against one athlete with a true all-out rating of 100, three championship
#   races read 3.9 points above twelve mixed ones, and 8.0 in the worst
#   corner of the parameter sweep. Team boards are decided by less than that.
#
# ! AND "JUST USE THE BEST RACES" IS WORSE THAN THE BUG. The obvious fix --
#   average the top 3, or the top 5 -- introduces the opposite bias, because
#   the best 3 of 20 draws beats the best 3 of 3 on sampling alone. Worst
#   case over the same sweep, as |points| of error:
#
#       estimator        old/new gap   n-sampling   worse of the two
#       80th pct                 2.6          2.5                2.6
#       85th pct                 2.0          2.7                2.7
#       top-half mean            4.0          0.5                4.0
#       max                      2.0          4.9                4.9
#       mean (what this was)     8.0          0.1                8.0
#       top-5 mean               2.2          9.3                9.3
#       top-3 mean               3.3         10.5               10.5
#
#   A quantile is the only family that is flat in BOTH directions: it
#   estimates the same point of an athlete's own distribution whether they
#   raced three times or twenty, which is exactly the invariance needed.
#
# ! NO NEW COST. This aggregate already carries three mode() WITHIN GROUP
#   calls, so it was never going to get a parallel plan; one more ordered-set
#   aggregate over the same groups changes nothing about the plan shape.
_SEASON_Q = 0.80

# Reads and writes the SHADOW tables. No TRUNCATE: the live athlete_season is
# untouched until swapIn renames it away.
_ATHLETE_SEASON_SQL = f"""
INSERT INTO {{season_table}}
    (person_id, pool, sport, year, mean_rating, decayed_rating, best_rating,
     n_races, first_race, last_race, state, school, grade)
SELECT person_id, pool, sport, year,
       (percentile_cont({_SEASON_Q}) WITHIN GROUP (ORDER BY speed_rating))::real,
       (sum(speed_rating * power({_DECAY_K}, {_ANCHOR} - race_date))
        / nullif(sum(power({_DECAY_K}, {_ANCHOR} - race_date)), 0))::real,
       max(speed_rating)::real,
       count(*),
       min(race_date),
       max(race_date),
       mode() WITHIN GROUP (ORDER BY state),
       mode() WITHIN GROUP (ORDER BY school),
       mode() WITHIN GROUP (ORDER BY grade)
FROM {{load_table}} base
GROUP BY person_id, pool, sport, year;

ANALYZE {{load_table}};
ANALYZE {{season_table}};
"""


def refreshAthleteSeason(conn):
    """Rebuild athlete_season from ranking_results.

    Runs AFTER the load. It reads ranking_results, so a stale or partial one
    yields a stale athlete_season with no error. The two are always rebuilt
    together.
    """
    print("\n  building athlete_season...")
    createShadow(conn, _LOAD_SEASON, "athlete_season")
    sql = _ATHLETE_SEASON_SQL.format(load_table=_LOAD_TABLE,
                                     season_table=_LOAD_SEASON)
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(f"SELECT count(*) FROM {_LOAD_SEASON}")
        n = cur.fetchone()[0]
    conn.commit()
    print(f"  athlete_season: {n:,} person-seasons")
    # ! AND ITS INDEXES. createShadow copies structure without them by design,
    #   and this call was missing -- so every run since swapped in a
    #   12.9M-row table with no index on it at all.
    buildIndexes(conn, _LOAD_SEASON, "athlete_season")


# ------------------------------------------------------------------ #
#  4. ENTRY POINT
# ------------------------------------------------------------------ #

def main():
    global RACE_MARGIN
    parser = argparse.ArgumentParser(
        description="Fill ranking_results and athlete_season. "
                    "Run after every engine run.")
    parser.add_argument("--sport", choices=["XC", "TF", "both"], default="both")
    parser.add_argument("--since", default="1990-01-01",
                        help="earliest race date to include")
    # ! SO THE RAIL CAN BE MEASURED. audit_pool_ceilings --races can only see
    #   what this build admitted, which is circular while the rail is on. One
    #   pass with a huge margin publishes everything, and then the
    #   distribution above the line is visible and RACE_MARGIN can be set from
    #   it rather than guessed.
    parser.add_argument("--race-margin", type=float, default=RACE_MARGIN,
                        help=f"how far above its pool's season ceiling one "
                             f"race may rate (default {RACE_MARGIN:.0f}; pass "
                             f"1000 to disable the rail for a measuring run)")
    args = parser.parse_args()

    RACE_MARGIN = args.race_margin
    if args.race_margin > 100:
        print(f"  ⚠ RACE MARGIN {args.race_margin:.0f} -- the pool rail is "
              f"effectively OFF for this run. Measure, then set it back.")

    sports = ("XC", "TF") if args.sport == "both" else (args.sport,)
    stats = {f"{s}_{k}": 0
             for s in sports for k in ("read", "written", "dropped")}

    started = datetime.datetime.now()

    print("=" * 68)
    print(f"BUILD rankings tables -- {', '.join(sports)}, since {args.since}")
    print("=" * 68)

    with getConn() as conn:
        # ★ NOTHING DESTRUCTIVE HAPPENS UNTIL THE SWAP. The old TRUNCATE /
        #   DELETE left the site with an empty or half-loaded board for the
        #   entire 15-25 minute load. Everything now goes into a shadow copy
        #   and the live tables are replaced in one atomic step at the end.
        # Built BEFORE the shadow load: the streaming queries join it, and a
        # temp table lives for the session, so it must exist first.
        # ★ ROOM TO WORK, BEFORE ANY OF IT STARTS. The index builds after the
        #   COPY and the athlete_season aggregate both want memory and
        #   workers; the streaming cursors cannot use workers at all, which is
        #   a property of cursors rather than of this query. See dbfast.
        tuneSession(conn)
        prepareGenderTemp(conn)
        prepareTfStateTemp(conn)

        createShadow(conn, _LOAD_TABLE, "ranking_results")
        seedOtherSports(conn, sports)

        for sport in sports:
            buildSport(conn, sport, args.since, stats)
            conn.commit()

        # ! AFTER THE LOAD, NOT BEFORE IT. createShadow deliberately leaves the
        #   indexes off so the 61.6M COPYed rows do not maintain them one row
        #   at a time. Built here, each is a sequential scan plus a sort, and
        #   refreshAthleteSeason below reads this table, so they have to exist
        #   before it runs.
        buildIndexes(conn, _LOAD_TABLE, "ranking_results")

        refreshAthleteSeason(conn)
        swapIn(conn)

    elapsed = (datetime.datetime.now() - started).total_seconds()
    total = sum(v for k, v in stats.items() if k.endswith("_written"))
    print(f"\n  {total:,} rows in ranking_results")
    print(f"  {elapsed / 60:.1f} minutes\n")


if __name__ == "__main__":
    main()