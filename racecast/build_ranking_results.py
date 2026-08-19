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
from season_year import seasonYearFromIso, seasonYearSql

try:
    from normalize_distance import poolFor
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

_SEASON_LEVEL_JOIN_XC = """
        -- ⚠ TWO EQUALITY JOINS, NOT A LATERAL. The first version used
        --   LEFT JOIN LATERAL (... ORDER BY (sport = 'XC') DESC LIMIT 1), which
        --   Postgres executes ONCE PER ROW with a sort each time. Against 39M XC
        --   rows and an athlete_season_level that had just tripled to 61.8M, that
        --   turned a four-minute panels run into three and a half hours for one
        --   sport. Both of these hit the (person_id, ay, sport) primary key
        --   directly, so the planner can hash or index-nested-loop them once.
        --
        --   COALESCE below picks the sport-specific verdict when it exists and the
        --   combined 'ALL' row otherwise -- identical semantics to the LATERAL's
        --   ORDER BY, without the per-row work.
        LEFT JOIN athlete_season_level asl_s
               ON asl_s.person_id = r.person_id
              AND asl_s.sport = 'XC'
              AND asl_s.ay = CASE WHEN substring(r.date, 6, 2) >= '07'
                                THEN substring(r.date, 1, 4)::int
                                ELSE substring(r.date, 1, 4)::int - 1 END
        LEFT JOIN athlete_season_level asl_a
               ON asl_a.person_id = r.person_id
              AND asl_a.sport = 'ALL'
              AND asl_a.ay = CASE WHEN substring(r.date, 6, 2) >= '07'
                                THEN substring(r.date, 1, 4)::int
                                ELSE substring(r.date, 1, 4)::int - 1 END
"""

_SEASON_LEVEL_JOIN_TF = """
        -- ⚠ TWO EQUALITY JOINS, NOT A LATERAL. The first version used
        --   LEFT JOIN LATERAL (... ORDER BY (sport = 'XC') DESC LIMIT 1), which
        --   Postgres executes ONCE PER ROW with a sort each time. Against 39M XC
        --   rows and an athlete_season_level that had just tripled to 61.8M, that
        --   turned a four-minute panels run into three and a half hours for one
        --   sport. Both of these hit the (person_id, ay, sport) primary key
        --   directly, so the planner can hash or index-nested-loop them once.
        --
        --   COALESCE below picks the sport-specific verdict when it exists and the
        --   combined 'ALL' row otherwise -- identical semantics to the LATERAL's
        --   ORDER BY, without the per-row work.
        LEFT JOIN athlete_season_level asl_s
               ON asl_s.person_id = r.person_id
              AND asl_s.sport = 'TF'
              AND asl_s.ay = CASE WHEN substring(r.date, 6, 2) >= '07'
                                THEN substring(r.date, 1, 4)::int
                                ELSE substring(r.date, 1, 4)::int - 1 END
        LEFT JOIN athlete_season_level asl_a
               ON asl_a.person_id = r.person_id
              AND asl_a.sport = 'ALL'
              AND asl_a.ay = CASE WHEN substring(r.date, 6, 2) >= '07'
                                THEN substring(r.date, 1, 4)::int
                                ELSE substring(r.date, 1, 4)::int - 1 END
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
        {_GENDER_JOIN}{_SEASON_LEVEL_JOIN_XC}{_gateJoins("XC")}
        WHERE r.speed_rating IS NOT NULL
          AND r.person_id IS NOT NULL
          AND r.date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}$'
          AND r.date >= %(since)s
    """,
    "TF": f"""
        SELECT r.result_id, r.person_id, r.speed_rating, r.date,
               r.grade, r.source, r.school, r.time_seconds,
               r.meet_id, r.div_id, r.canon_meet_id,
               COALESCE(dov.distance, m.distance) AS distance,
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
        {_GENDER_JOIN}{_SEASON_LEVEL_JOIN_TF}{_gateJoins("TF")}
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


def isRankablePool(pool):
    """unknown_gender pools exist but hold 9 athletes corpus-wide. Not a board."""
    return bool(pool) and not pool.endswith("_unknown_gender")


def prepareRow(row, sport):
    """One DB row -> one COPY tuple, or None to drop it.

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
    school = row["school"]
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
    if not inScope(row.get("state")):
        return None

    # ★ THE ENGINE'S DECISION, NOT A REIMPLEMENTATION OF IT. resolvePool is
    #   the same function speed_ratings.poolOf calls; the five facts come from
    #   the joins above instead of from the engine's in-memory dicts.
    #
    #   merge=True: this table stores the pool WITHOUT the sport suffix, since
    #   `sport` is already its own column.
    pool = resolvePool(row["grade"], row["gender"], row["source"], school,
                       sport,
                       season_level=row.get("season_level"),
                       grade_untrusted=bool(row.get("grade_untrusted")),
                       fixed_grade=row.get("fixed_grade"),
                       fixed_level=row.get("fixed_level"),
                       grade_verdict=row.get("grade_verdict"),
                       # ! FOR _PRO_PEOPLE -- see pool_resolve.
                       person_id=row.get("person_id"),
                       is_pro=bool(row.get("is_pro")),
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
    if row.get("grade_trust") == "low":
        return None

    rating = float(row["speed_rating"])
    if not (_RATING_MIN <= rating <= _RATING_MAX):
        return None

    date_text = row["date"]

    return (sport,
            row["result_id"],
            row["person_id"],
            pool,
            rating,
            date_text,                 # Postgres parses YYYY-MM-DD directly
            # SEASON year, not calendar year. A TF race in Nov/Dec belongs to
            # the season ahead (indoor opens then), so `year` here matches the
            # engine's grouping. athlete_season's GROUP BY ... year inherits
            # it, which is what stops one real season showing as two rows on
            # the boards. XC is unaffected -- see season_year.py.
            seasonYearFromIso(sport, date_text),
            row["state"],
            school,
            row["grade"],
            row["meet_id"],
            row["div_id"],
            row["canon_meet_id"],
            row["time_seconds"],
            row.get("distance"),
            row.get("event_id"))


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
    payload = "".join("\t".join(copyField(v) for v in row) + "\n"
                      for row in rows)
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
                     cursor_factory=psycopg2.extras.RealDictCursor) as src:
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


def indexDefs(conn, like):
    """The live table's index definitions, ready to replay on the shadow.

    Read from the catalogue rather than hardcoded: this project has added
    indexes to ranking_results more than once, and a hardcoded list would
    silently drop any that were added since. The same reasoning as
    merge_column._columns.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT indexname, indexdef
            FROM   pg_indexes
            WHERE  schemaname = 'public' AND tablename = %s
        """, (like,))
        return cur.fetchall()


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
# DECAY_K ** days_ago when solving ability; this does the same, anchored to the
# athlete's LAST race of that season. So decayed_rating means "form at season's
# end" while mean_rating means "the season as a whole".
#
# season_end - race_date is integer days: subtracting two dates in Postgres
# yields int, not an interval, so power() takes it directly.
#
# state/school/grade are the MODE, not an arbitrary row: an athlete can change
# school mid-season, and mode() picks what they mostly were.
_DECAY_K = 0.996

# Reads and writes the SHADOW tables. No TRUNCATE: the live athlete_season is
# untouched until swapIn renames it away.
_ATHLETE_SEASON_SQL = f"""
INSERT INTO {{season_table}}
    (person_id, pool, sport, year, mean_rating, decayed_rating, best_rating,
     n_races, first_race, last_race, state, school, grade)
SELECT person_id, pool, sport, year,
       avg(speed_rating)::real,
       (sum(speed_rating * power({_DECAY_K}, season_end - race_date))
        / nullif(sum(power({_DECAY_K}, season_end - race_date)), 0))::real,
       max(speed_rating)::real,
       count(*),
       min(race_date),
       max(race_date),
       mode() WITHIN GROUP (ORDER BY state),
       mode() WITHIN GROUP (ORDER BY school),
       mode() WITHIN GROUP (ORDER BY grade)
FROM (
    SELECT *,
           max(race_date) OVER (
               PARTITION BY person_id, pool, sport, year) AS season_end
    FROM {{load_table}}
) base
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


# ------------------------------------------------------------------ #
#  4. ENTRY POINT
# ------------------------------------------------------------------ #

def main():
    parser = argparse.ArgumentParser(
        description="Fill ranking_results and athlete_season. "
                    "Run after every engine run.")
    parser.add_argument("--sport", choices=["XC", "TF", "both"], default="both")
    parser.add_argument("--since", default="1990-01-01",
                        help="earliest race date to include")
    args = parser.parse_args()

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