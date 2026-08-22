"""
panels.py -- precompute the landing-page leaderboards.

RUN IT OFFLINE, after an engine run:      python panels.py
It writes one small table, `homepage_panels` (~1200 rows), which the landing
page reads directly. The landing page NEVER touches `results`.

WHY PRECOMPUTE: the pool of a result is decided by poolFor(), which is Python
(string normalisation + the school_levels pickle). Postgres cannot compute it,
so there is no `GROUP BY pool` available -- every rated row must pass through
Python. That is a full-corpus scan, which is fine once per engine run and
impossible per page load.
"""

import sys
import heapq
import datetime
import argparse
import itertools
from collections import defaultdict, Counter
from functools import lru_cache

import psycopg2.extras

# Same two lines app.py opens with: `scripts` on the path, then the engine's
# own connection helper. One place knows the DSN, and it is not this file.
# NOTE: this path is relative to the CWD, so run panels.py from racecast/,
# exactly like app.py.
sys.path.insert(0, "scripts")
from database import getConn


# ===================================================================== #
#  1. THE SSOT IMPORT
# ===================================================================== #
#
# poolFor is the engine's single source of truth for grade -> level -> pool.
# We IMPORT it rather than reimplement it in SQL. Reimplementing is how the
# site and the engine drift into disagreeing about who is a college athlete.
#
# ADJUST THIS to wherever poolFor actually lives.
sys.path.insert(0, "engine")
try:
    from normalize_distance  import poolFor          # <-- TODO: real module path
except ImportError as exc:                    # fail loudly, not silently
    raise SystemExit(
        f"Could not import poolFor ({exc}).\n"
        "Fix the import path at the top of panels.py. Do NOT reimplement the "
        "pool logic here -- it must stay identical to the engine's."
    )


# ===================================================================== #
#  2. CONFIG -- every tunable in one place
# ===================================================================== #

TOP_N       = 25     # rows SHOWN per board
# ⚠ HOW MANY SLOTS ONE PERSON MAY HOLD ON A PERFORMANCE BOARD.
#
#   _drainRanked has carried this cap, and the reason for it, since one
#   middle-school girl held 12 of 25 TF slots -- but _writePanels passed None
#   for every board except "athlete", so the performance boards ran uncapped
#   and the exact thing the cap was written for came back: Luke Surface held
#   12 of 25 MS boys slots and Brianna Reilly 12 of 25 MS girls, because an
#   800, a 1500 and a 3000 are three rated performances by one runner.
#
#   Each of those rows is a real result and belongs on the site. What it does
#   not belong to is a list of 25 whose job is to say who the best are. Two
#   keeps a genuinely dominant athlete visibly dominant without letting them
#   BE the board.
PERF_PER_PERSON = 2
COLLECT_N   = 250    # rows COLLECTED before thinning -- must exceed TOP_N by
                     # enough that capping per athlete can't leave the board short      # rows kept per board / scope / sport / pool
SEASON_MIN_RACES   = 4       # race floor for a season average to be listed
ALLTIME_MIN_RACES  = 6       # race floor for the best-season-ever board

# Sanity rails. Ratings are pool-relative with 100 = pool mean; observed real
# values span roughly 30-170. Anything outside this band is a fossil row
# (e.g. speed_rating 7528 on a 20.6s time) and must never reach the front page.
RATING_MIN         = 20.0
RATING_MAX         = 200.0

DNF_SENTINEL       = 999999  # time_seconds value meaning "did not finish"

# Which sport the site DEFAULTS to showing, by calendar month.
# Wall-clock, not data: August means cross country regardless of the corpus.
XC_MONTHS          = {8, 9, 10, 11}

# Rows streamed from the server per network round-trip.
FETCH_BATCH        = 20000

# WHICH COLUMN OF `athletes` DO WE JOIN ON?  app.py currently does both:
#     athlete() route     ->  WHERE person_id = %s
#     get_race_results()  ->  WHERE a.athlete_id = COALESCE(r.person_id, ...)
# Those disagree for tfrrs rows, where athlete_id is 0. See the note in the
# chat -- flip this one constant if the athlete_id form turns out to be right.
ATHLETE_KEY        = "person_id"

# Boards withheld from the public page. Keys are (board, scope); the value is
# WHY, and it travels with the data so the reason is never lost.
#
# performance/alltime: dominated by Mt. SAC, where one year's field ran the
# RAIN COURSE (flat) while course_difficulties still carried the normal
# course's difficulty -- whole field over-credited. Engine fix pending;
# re-check this board after it lands, it is how we watch the problem.
SUPPRESSED_BOARDS = {
    ("performance", "alltime"): "mt-sac-rain-course-difficulty",
}

# HARDCODED EXCLUSIONS -- temporary. Each is a specific meet whose ratings are
# known-bad and not yet fixed upstream. This is a SITE patch the engine does
# not share, so the two disagree until the real fix lands. Delete entries here
# the moment the engine handles them.
#   <meet_id>: Mt. SAC <year>, field ran the flat rain course but got the
#              normal course's difficulty -> whole field over-credited.
EXCLUDED_MEET_IDS = frozenset({
    31741,   # Mt. SAC CIF-SS Div Finals 2010-11-20: cold/rain, switched to the
             # flat rain course, kept the normal course's difficulty. +7.2 over
             # the meet's own baseline. Chapus 18:41, JSerra team title.
    226604,  # Mt. SAC CIF-SS Div Finals 2023: rain course again, but ran
             # sunny/dry -> fast field, over-credited. +4.8 over baseline.
    256823   # ANOTHER FUCKING RAIN COURSE WHEN ITS SUNNY.
})

# Schools that mean "not a school" -- the athlete is a pro, national-team, or
# club competitor whose grade field is stale/garbage. A row whose school matches
# is DROPPED from pooling rather than believed. Site-layer stopgap; the real fix
# is in the engine + identity. Match is case-insensitive, on the trimmed school.
#
# Tune against your data's actual spellings, not official names (that's why this
# is hardcoded, not pycountry). "Unattached" is deliberately NOT here -- it also
# means real kids at open meets.
_NON_SCHOOL = frozenset(s.lower() for s in {
    # --- countries / national teams (distance-running ones that appear) ---
    "mexico", "ireland", "belgium", "kenya", "netherlands", "canada",
    "japan", "new zealand", "australia", "great britain", "united states",
    "usa", "united states of america", "spain", "france", "germany", "italy",
    "ethiopia", "uganda", "norway", "sweden", "guatemala", "portugal", "Uruguay", "Poland"
    # --- pro clubs / brand teams ---
    "saucony", "brooks beasts", "brooks", "nike", "adidas", "on", "on running",
    "puma", "hoka", "hoka aggie running club", "minnesota distance elite",
    "under armour/dark sky", "ua dark sky distance", "under armour / dark sky",
    "bowerman track club", "on athletics club", "athletics tauranga", "joma", "new balance"
})


_NON_SCHOOL_FRAGMENTS = ("dark sky", "under arm", "under armour", "under armor")

# The season rule, shared with the engine and build_ranking_results. Imported
# rather than reimplemented for the same reason poolFor is: if the boards and
# the rankings tables disagree about which season a race is in, the home card
# and the page it links to silently show different sets of athletes.
sys.path.insert(0, "engine")
from season_year import seasonYearFromIso, seasonYearSql, seasonYearSqlInt
from pool_resolve import resolvePool, inScope
from dbfast import dictRows, tuneSession


# ===================================================================== #
#  DoDEA FAR EAST -- A STOPGAP, NOT A FIX
# ===================================================================== #
#
# ⚠ THESE ARE REAL AMERICAN HIGH SCHOOLS AND THEIR RATINGS ARE WRONG.
#   Yokota, Humphreys, Kinnick and Osan are Department of Defense schools in
#   Japan, Korea and Guam. They race a closed Far East circuit -- DoDEA
#   Pacific, KAIAC, Kanto -- against each other and almost nobody else, so
#   their whole scale is free to drift together. They surfaced at 144-147 on
#   the HS boards, above every mainland athlete.
#
#   shrinkByLinkage does not catch them because it measures linkage per CELL,
#   and their athletes DO race outside any single cell -- just never outside
#   the circuit. component_check.py measures the right thing (a component with
#   no path to a well-measured cell) and the principled fix is to force
#   delta = 0 for those components in the solver.
#
#   Until that exists, this hides them. It does not correct anything: their
#   ratings are still wrong, they are merely not on a leaderboard. Delete this
#   the day the component fix lands.
#
# ★ EXACT MATCH, NEVER SUBSTRING. poolFor's own comment records what substring
#   matching did here: 'hoka' caught Tahoka, 'on running' caught Central Oregon
#   Running Klub, 'Mexico' caught Mexico High School, Missouri. A school named
#   "Zama" must not take "Zamalek" with it. Spellings below are as they appear
#   in the data, not as DoDEA writes them officially.
_DODEA_SCHOOLS = frozenset(s.lower() for s in {
    "yokota", "humphreys", "osan american", "zama", "zama american",
    "n.c. kinnick", "nile c. kinnick", "kinnick",
    "kadena", "kubasaki", "seoul american", "daegu", "taegu",
    "e.j. king", "robert d. edgren", "edgren", "matthew c. perry",
    "guam high", "yongsan", "misawa",
    "american school in japan", "christian academy in japan",
})


# ★ CACHED, BECAUSE THESE ARE ASKED THE SAME QUESTION MILLIONS OF TIMES.
#   build_ranking_results calls both on EVERY ONE of 61.6M rows, and there are
#   only a few hundred thousand distinct school names behind them --
#   _is_non_school in particular walks a fragment list with a substring test
#   per fragment, which is the most expensive single thing in that loop, for a
#   value that cannot change. Pure functions of one string, so the cache is
#   exact. Bounded rather than unbounded: a corrupt corpus with millions of
#   distinct school strings should get slower, not run out of memory.
@lru_cache(maxsize=1 << 18)
def _is_dodea(school):
    """Exact match against the Far East DoDEA list. See the warning above."""
    return bool(school) and school.strip().lower() in _DODEA_SCHOOLS


@lru_cache(maxsize=1 << 18)
def _is_non_school(school):
    if not school:
        return False
    s = school.strip().lower()
    if s in _NON_SCHOOL:
        return True
    return any(frag in s for frag in _NON_SCHOOL_FRAGMENTS)


# ===================================================================== #
#  3. SMALL HELPERS
# ===================================================================== #

def _streamingCursor(conn, name):
    """A SERVER-SIDE cursor.

    app.py's `conn.cursor(cursor_factory=RealDictCursor)` pulls the entire
    result set into Python before you touch it -- fine for one athlete, fatal
    on 39M rows. Passing `name=` makes it a *named* cursor: the rows stay on
    the Postgres side and arrive in batches of `itersize`, so memory stays
    flat no matter how big the query is.

    ★ A PLAIN CURSOR, WRAPPED BY dbfast.dictRows. The rows are still dicts and
      row["col"] still works -- but RealDictCursor builds an ordered-dict
      subclass per row, and over the 39M rows this function exists for that is
      minutes. Measured on 2M rows: 11.8s against 4.3s. Callers iterate
      dictRows(cur), never the cursor itself.

    ★ cursor_tuple_fraction = 1.0 IS THE POINT OF THIS FUNCTION NOW.
      A named cursor is planned as DECLARE CURSOR, and Postgres plans that for
      FAST FIRST ROWS, not lowest total cost -- the default
      cursor_tuple_fraction of 0.1 tells it only a tenth of the rows will be
      read. That biases it hard toward NESTED LOOPS, which are excellent for
      the first thousand rows and ruinous across sixty million.

      This query streams EVERY row. Setting the fraction to 1.0 says so, and
      the planner then costs it the way EXPLAIN does -- hash joins, one pass.

    ⚠ THIS IS WHY EXPLAIN LOOKED FINE WHILE THE RUN DID NOT. EXPLAIN shows the
      full-retrieval plan; the cursor was executing a different one. If a
      future query here is mysteriously slower than its EXPLAIN suggests, this
      setting is the first thing to check.

      SET LOCAL, so it lasts only as long as the surrounding transaction and
      cannot leak onto whatever else uses this connection."""
    with conn.cursor() as setup:
        setup.execute("SET LOCAL cursor_tuple_fraction = 1.0")

    cur = conn.cursor(name=name)
    cur.itersize = FETCH_BATCH
    return cur


def _yearOf(date_text):
    """CALENDAR year as text. `date` is a TEXT column, so this is a string
    slice -- never .year, which raises on these values.

    ⚠ NOT THE SEASON. For TF a Nov/Dec race belongs to the following season;
      use seasonYearFromIso(sport, date_text) for anything that groups,
      labels or filters a season. This is for date arithmetic only.
    """
    return (date_text or "")[:4]


def _defaultSport(month):
    """Sport the landing page opens on, from the wall clock."""
    return "XC" if month in XC_MONTHS else "TF"


def _fullName(row):
    """Prefer the athletes table; fall back to the result's own name column,
    which is ~90% null on `results`."""
    first = (row.get("first_name") or "").strip()
    last = (row.get("last_name") or "").strip()
    if first or last:
        return f"{first} {last}".strip()
    return (row.get("athlete_name") or "").strip() or "Unknown"


# ===================================================================== #
#  4. BOUNDED TOP-N
# ===================================================================== #
#
# We cannot sort 39M rows, and we do not need to: only 25 survive per bucket.
# A MIN-heap of size 25 keeps the 25 LARGEST values seen. heap[0] is always the
# weakest survivor, so the test for any new row is a single comparison.

# The display tilt is optional: without tilt.py the board falls back to raw
# stored ratings, which is exactly what it showed before.
try:
    from tilt import ratingFor as _ratingFor
except Exception:
    _ratingFor = None

_TIEBREAK = itertools.count()   # keeps heapq from ever comparing two payloads


def _pushTop(heap, k, score, payload):
    """Offer `payload` to a top-k heap. O(log k), constant memory."""
    if len(heap) < k:
        heapq.heappush(heap, (score, next(_TIEBREAK), payload))
    elif score > heap[0][0]:                  # beats the weakest survivor
        heapq.heapreplace(heap, (score, next(_TIEBREAK), payload))


def _drainRanked(heap, top_n=TOP_N, max_per_person=2):
    """Heap -> ranked list, with a cap on how many slots one athlete may hold.

    WHY THE CAP: without it a dominant athlete monopolises the board -- one
    middle-school girl held 12 of 25 TF slots, because her 800, 1500 and 3000
    are all separate rated performances. Legitimate, but not a leaderboard.

    This is why we collect COLLECT_N and show TOP_N: the thinning happens after
    ranking, so capping never leaves the board short.
    """
    ordered = sorted(heap, key=lambda t: t[0], reverse=True)
    seen, out = Counter(), []

    for score, _tie, payload in ordered:
        pid = payload.get("person_id")
        if max_per_person is not None and pid is not None:
            if seen[pid] >= max_per_person:
                continue
            seen[pid] += 1
        payload["rating"] = round(float(score), 2)
        out.append(payload)
        if len(out) >= top_n:
            break

    for rank, entry in enumerate(out, start=1):
        entry["rank"] = rank
    return out


# ===================================================================== #
#  5. POOLING A ROW
# ===================================================================== #

def _asDate(text):
    """'YYYY-MM-DD' -> date, for the college and upperclass gates.

    ⚠ THOSE GATES COMPARE BY DATE, NOT YEAR, and that is load-bearing: a
      senior's spring high-school track season and their first autumn of
      college share a calendar year. Comparing years promoted 288,264 genuine
      high-school athlete-seasons. first_date arrives as a date, so the row's
      own date has to be one too.
    """
    if not text:
        return None
    if not isinstance(text, str):
        return text                    # already a date object
    try:
        return datetime.date(int(text[:4]), int(text[5:7]), int(text[8:10]))
    except (ValueError, IndexError):
        return None


def _poolOf(row, sport):
    """The pool for a DB row, or None when the level is unknowable.

    ! NO LONGER CALLED BY THE BOARDS. Both loops now read `pool` straight from
      ranking_results and athlete_season, which build_ranking_results filled
      using this same decision twenty minutes earlier. Kept because it is the
      only place the row-shaped call is spelled out, and anything added here
      later will want it; delete it once that is certainly not true.


    Guard first: if the school is a country or pro club, the grade is not
    trustworthy (a pro's grade field is stale -- Eduardo Herrera reads grade 12
    while racing for Mexico). Drop rather than pool them as a high schooler.
    """
    if _is_non_school(row.get("school")):
        return None
    # Hidden, not corrected -- see the warning above _DODEA_SCHOOLS.
    if _is_dodea(row.get("school")):
        return None                    # pro/international -> unrankable, drop

    # ★ THE ENGINE'S DECISION, NOT A COPY OF IT. resolvePool is the same
    #   function speed_ratings.poolOf calls; the five facts come from the
    #   joins in the queries above rather than from the engine's in-memory
    #   dicts. Calling poolFor directly here -- as this did -- skipped every
    #   gate that runs after it, which is the ~332k hs<->college split.
    #
    #   merge=True: the boards are already per sport, so the pool key carries
    #   no sport suffix.
    # The season, for the hand-listed professionals -- per athlete-season now.
    # A row with no parseable date simply does not narrow the span.
    try:
        season = seasonYearFromIso(sport, row.get("date"))
    except (TypeError, ValueError, IndexError):
        season = None

    return resolvePool(row.get("grade"),
                       row.get("gender"),
                       row.get("source"),
                       row.get("school"),
                       sport,
                       season=season,
                       person_id=row.get("person_id"),
                       season_level=row.get("season_level"),
                       grade_untrusted=bool(row.get("grade_untrusted")),
                       fixed_grade=row.get("fixed_grade"),
                       fixed_level=row.get("fixed_level"),
                       is_pro=bool(row.get("is_pro")),
                       # ★ NO GATE ARGUMENTS AND NO DATE PARSE. Their only
                       #   readers were the promotion gates, now removed from
                       #   resolvePool.
                       merge=True)

def _perfKey(row, sport):
    """A key identical for two source-copies of one physical race.

    ⚠ THIS WAS A NO-OP. The old key read row['canon_meet_id'], and NEITHER
      performance query selects that column -- so the key was always None,
      every row skipped the dedupe, and both copies reached the board. A
      leaderboard showed Trent Daniels at places 1 AND 2 with an identical
      9:07.02, every finisher twice, all the way down.

    ★ PERSON + DATE + TIME, NOT A MEET MAPPING. anet and tfrrs record the
      same race independently, so the meet ids differ and tying them together
      needs a canonical mapping that may or may not exist. But one athlete
      cannot run two different races on one day in the same time -- and both
      copies carry the person, the date and the clock. That is sufficient on
      its own and needs no extra table.

      Rounded to a tenth because the two sources round differently: 9:07.02
      against 9:07.0 is one race. Rounding further would start merging
      genuinely different marks.

      Returns None when the person or the time is missing -- an unknown key
      is never deduped, just kept.
    """
    pid = row.get("person_id")
    t = row.get("time_seconds")
    day = (row.get("date") or "")[:10]
    if pid is None or t is None or not day:
        return None
    return (sport, int(pid), day, round(float(t), 1))


def _isRankablePool(pool):
    """unknown_gender pools exist but hold 9 athletes corpus-wide. Not a board."""
    return bool(pool) and not pool.endswith("_unknown_gender")


# ===================================================================== #
#  6. SEASON DETECTION
# ===================================================================== #

_SANE_YEAR = r"^(19|20)[0-9]{2}"


# A real season carries at least this share of the busiest recent year's rated
# rows. Absolute thresholds don't work: 2026 XC clears any small number because
# middle school leagues run XC in spring, but it's ~0.5% of a normal season.
SEASON_MIN_SHARE = 0.25
SEASON_LOOKBACK  = 12        # years of history used to judge "normal volume"


def _yearCounts(conn, sport):
    """Rated rows per SEASON year, newest first, junk years already excluded.

    ⚠ TWO DIFFERENT YEARS IN ONE QUERY, ON PURPOSE.

      GROUP BY  the SEASON year. This histogram decides which season the
                boards show, so it has to count seasons, not calendars. For TF
                that moves Nov/Dec rows into the season ahead -- which is the
                whole point: a December indoor opener is evidence for the
                season it opens, not the one that just ended.

      BETWEEN   the CALENDAR year, unchanged. It is a junk-date guard whose
                only job is to reject 2222 and friends. Rolling it too would
                make a real 2026-12 TF race fail the '2026' ceiling for being
                season 2027 and vanish from the histogram entirely.
    """
    table = "results" if sport == "XC" else "results_tf"
    season = seasonYearSql(sport, "date")
    sql = f"""
        SELECT {season} AS yr, COUNT(*) AS n
        FROM   {table}
        WHERE  speed_rating IS NOT NULL
          AND  date ~ %s
          AND  substring(date, 1, 4) BETWEEN '1995' AND '2026'
        GROUP  BY 1
        ORDER  BY 1 DESC
        LIMIT  %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (_SANE_YEAR, SEASON_LOOKBACK))
        return cur.fetchall()          # [(yr, n), ...] newest first


def _seasonYear(conn, sport):
    """The most recent year that held a REAL season.

    Not max(date): junk years (2222, 2223) exist. Not 'most recent year with
    any rows' either -- that picked 2026 for XC, which is entirely middle
    school spring leagues, leaving both college boards empty.

    Instead: compare each year against the busiest year in living memory and
    take the newest one that's within a quarter of it.
    """
    rows = _yearCounts(conn, sport)
    if not rows:
        return None

    floor = max(n for _, n in rows) * SEASON_MIN_SHARE
    for yr, n in rows:                 # newest first
        if n >= floor:
            return yr
    return rows[0][0]                  # nothing qualifies -> newest anyway


# ===================================================================== #
#  7. QUERY A -- BEST SINGLE PERFORMANCES
# ===================================================================== #

_PERF_COMMON_FILTERS = """
          r.speed_rating IS NOT NULL
      AND r.speed_rating BETWEEN %(rmin)s AND %(rmax)s
      AND r.time_seconds < %(dnf)s
      -- ★ (col IS NULL OR col = x), NOT COALESCE(col, x) = x.
      --   Postgres keeps NO STATISTICS for an expression, so COALESCE(...) = x
      --   gets the default selectivity of 0.005. Four of them multiplied --
      --   assuming independence -- gave 6e-10, which against 191M rows
      --   estimated ONE surviving row. The planner then chose a nested loop
      --   nine times over, because looping once over a seq-scanned table is
      --   free. That is the nine hours.
      --
      --   The OR form is two ordinary predicates on a plain column, both of
      --   which the planner HAS statistics for: the null fraction and the
      --   value frequency. Same rows, an estimate that is not off by seven
      --   orders of magnitude.
      AND (r.official   IS NULL OR r.official   = 1)
      AND (r.exhibition IS NULL OR r.exhibition = 0)
      -- ★ RANGE, NOT REGEX. `date` is TEXT in ISO form, so string order IS
      --   date order and a plain comparison does the same job as the year
      --   regex -- but a regex is a function call per row across ~62M rows
      --   and can never use an index, while a range can. Same rows out.
      --   (The old %(year_re)s param is still accepted and ignored by
      --   callers that pass it, so no call site breaks.)
      AND r.date >= %(ymin)s
      AND r.date <  %(ymax)s
      AND r.meet_id <> ALL(%(excl)s)
"""

# Bounds for the range above. Must bracket the same years the old
# _SANE_YEAR regex accepted; '2036' as an exclusive upper bound because
# '2035-12-31' < '2036' lexicographically.
_YEAR_MIN_STR = "1980"
_YEAR_MAX_STR = "2036"


# Same LATERAL shape app.py uses, with one addition: an ORDER BY.
# `athletes` holds roughly one row per (person, school), so a prolific athlete
# has several -- and some are minted by the linking scripts with NO name and NO
# gender. A bare LIMIT 1 returns an arbitrary one. Named rows sort first, then
# gendered rows. Booleans sort false < true, hence DESC.
#
# Note the gender filter moved OUT of the WHERE and INTO the sort: filtering it
# would discard a named-but-genderless row entirely, losing the name too.
# ★ ONE ROW PER PERSON, PRE-AGGREGATED, THEN A HASH JOIN.
#
#   The LATERAL this replaces ran once per RESULT ROW -- roughly 62M times
#   -- to fetch a name and gender that depend only on the person. Postgres
#   cannot hoist a correlated LIMIT 1 out of the loop, so it paid an index
#   probe per row and could never hash-join.
#
#   DISTINCT ON collapses athletes to one row per person up front, keeping
#   the SAME preference the LATERAL's ORDER BY encoded: named rows first,
#   then gendered. `athletes` holds roughly one row per (person, school)
#   and the linking scripts mint some with no name and no gender, so an
#   arbitrary row would lose the name.
_ATHLETE_ONE = """
    ath AS (
        SELECT DISTINCT ON (person_id)
               person_id,
               NULLIF(TRIM(first_name), '') AS first_name,
               NULLIF(TRIM(last_name),  '') AS last_name,
               gender
        FROM   athletes
        WHERE  gender IN ('M', 'F')
        ORDER  BY person_id,
                  (NULLIF(TRIM(last_name), '') IS NOT NULL) DESC,
                  (gender IS NOT NULL) DESC
    )
"""


# ★ THE CTE ABOVE IS STILL A ~35M-ROW SORT, AND IT RUNS ONCE PER QUERY.
#
#   DISTINCT ON requires an ORDER BY over the whole table, so Postgres must
#   sort every athletes row before emitting one -- a blocking node with no
#   streaming. Fixing the per-ROW LATERAL was the big win; this is the
#   per-QUERY one that was left behind. There are four query executions in a
#   full run (perf + athletes, x XC + TF), so the same sort is paid four times
#   for a result that cannot change between them.
#
#   Building it ONCE into an indexed temp table turns four sorts into one, and
#   gives the planner real statistics plus an index to hash or nest-loop
#   against instead of an opaque CTE it must materialise blind.
#
#   The SELECT is byte-identical to the CTE body, so the chosen row per person
#   is exactly the same -- named rows first, then gendered. This is a
#   performance change only.
_ATH_TEMP_DDL = """
    DROP TABLE IF EXISTS ath;
    CREATE TEMP TABLE ath AS
        SELECT DISTINCT ON (person_id)
               person_id,
               NULLIF(TRIM(first_name), '') AS first_name,
               NULLIF(TRIM(last_name),  '') AS last_name,
               gender
        FROM   athletes
        WHERE  gender IN ('M', 'F')
        ORDER  BY person_id,
                  (NULLIF(TRIM(last_name), '') IS NOT NULL) DESC,
                  (gender IS NOT NULL) DESC;
    CREATE INDEX ON ath (person_id);
    ANALYZE ath;
"""


# _buildAthleteTemp
# Purpose : materialise `ath` once for the whole run.
# Argument: conn — the panels connection. MUST be the same connection every
#           query uses: a TEMP table is session-scoped, so a pooled connection
#           handing out a different session would make the queries fail with
#           "relation ath does not exist" rather than silently return wrong
#           rows. Loud is the right failure here.
# Output  : row count, printed so a suspicious drop is visible in the log.
def _buildAthleteTemp(conn):
    with conn.cursor() as cur:
        cur.execute(_ATH_TEMP_DDL)
        cur.execute("SELECT count(*) FROM ath")
        n = cur.fetchone()[0]
    conn.commit()
    print(f"[panels] ath: {n:,} people (built once, reused by every query)")
    return n


_GENDER_LATERAL = """
    LEFT JOIN ath a ON a.person_id = COALESCE(r.person_id, r.athlete_id)
"""

# ★ THE SEASON VERDICT, keyed on the JULY-START academic year exactly as
#   speed_ratings._academicYear does. Feeding poolFor's step 0; without it the
#   boards pool on a different basis than the engine that produced the ratings.
# The four gates poolOf applies after poolFor. See pool_resolve.resolvePool.
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
          -- ! ACADEMIC, NOT CALENDAR. pro_flag writes pro_athlete_season on
          --   the academic year now, same as grade_fix. This join said
          --   substring(date,1,4) and would have missed every spring race.
          AND pas.season = ({season})::int
    -- ★ THE TWO PROMOTION-GATE JOINS ARE GONE. resolvePool no longer
    --   consults college_first_season or upperclass_first_season -- they
    --   promoted on a per-person date with no reference to the row's own
    --   grade, and both were measured putting middle schoolers on the
    --   college board. Keeping them cost two index probes and two output
    --   columns on every one of 61.6M rows, to produce arguments the
    --   decision function now discards.
"""

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
      to hard-code a JULY seam while the engine looked the same table up on
      the AUGUST seam (speed_ratings._academicYear -> seasonYearFor).
      season_level._academicYearExpr now writes `ay` on this expression too,
      so writer, engine and this join cannot drift again.
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

# ! ONE JOIN INSTEAD OF SIX. ranking_results is written by
#   build_ranking_results, which runs immediately before this script, and its
#   `pool` column is resolvePool's answer for that exact result_id. The two
#   season-level joins and the four gate joins existed only to feed a
#   resolvePool call this file no longer makes.
#
# * AND IT IS AN INNER JOIN, DELIBERATELY. ranking_results holds only rows
#   that pooled to something rankable, so joining it filters 101M raw rows
#   down to the 61.6M that could ever reach a board -- work the Python loop
#   used to do one row at a time.
#
# * THE TWO BOARDS NOW AGREE BY CONSTRUCTION. panels and /rankings pooled
#   independently from the same five facts, and keeping six joins in step
#   across two files is how they drifted before.
_RANKING_JOIN_XC = """
        JOIN ranking_results rr
             ON rr.result_id = r.result_id
            AND rr.sport = 'XC'
"""

_RANKING_JOIN_TF = """
        JOIN ranking_results rr
             ON rr.result_id = r.result_id
            AND rr.sport = 'TF'
"""

_PERF_SQL_XC = f"""
    SELECT r.result_id, r.person_id, r.speed_rating, r.time_seconds, r.date,
           r.grade, r.school, r.source, r.athlete_name,
           r.meet_id, r.div_id,
           COALESCE(m.meet_name, mt.meet_name)   AS meet_name,
           m.course_name,
           -- ! FOR _perfDetail. The board shows the time and what it was run
           --   over; without this the XC half could only show the clock.
           --   No new join: the difficulty ON clause below already reads it.
           m.distance,
           -- Geography for tfrrs rows, which `meets` cannot supply at all.
           COALESCE(m.state, mt.state)           AS state,
           cd.difficulty,
           a.gender, a.first_name, a.last_name,
           -- ! THE POOL, ALREADY RESOLVED. These seven columns existed only
           --   to feed a resolvePool call this file no longer makes: two
           --   season-level lookups and the four gates. build_ranking_results
           --   ran twenty minutes earlier and wrote the answer for this exact
           --   result_id.
           rr.pool                     AS pool
    FROM   results r
    -- ⚠ meet_id IS PART OF THE KEY. Joining on div_id and source alone
    --   matches every meet that happens to share a division number, so a
    --   row could be labelled with an unrelated meet's name and course --
    --   and the row count multiplies, which is also most of why this
    --   query was slow.
    LEFT JOIN meets m ON m.meet_id = r.meet_id
                     AND m.div_id  = r.div_id
                     AND m.source  = r.source
    -- ⚠ `meets` IS ANET-ONLY (zero tfrrs rows). Without this second join every
    --   tfrrs result rendered as "Unknown meet". meets_tfrrs is keyed
    --   (meet_id, sport), so the sport is pinned here rather than trusted to
    --   be unique.
    LEFT JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id
                            AND mt.sport   = 'XC'
    -- the stored difficulty for this venue, so the display tilt can
    -- charge the course at the athlete's own level. See tilt.py.
    LEFT JOIN course_difficulties cd
           ON cd.course_name = 'XC:' || m.course_name
          AND cd.distance_m  = (round(m.distance / 100.0) * 100)::int
    {_GENDER_LATERAL}{_RANKING_JOIN_XC}
    WHERE {_PERF_COMMON_FILTERS}
"""

_PERF_SQL_TF = f"""
    SELECT r.result_id, r.person_id, r.speed_rating, r.time_seconds, r.date,
           r.grade, r.school, r.source, r.athlete_name,
           r.meet_id, r.div_id, r.event_id, r.event_short,
           COALESCE(m.meet_name, mt.meet_name)   AS meet_name,
           COALESCE(m.state, mt.state)           AS state,
           a.gender, a.first_name, a.last_name,
           -- ! THE POOL, ALREADY RESOLVED. These seven columns existed only
           --   to feed a resolvePool call this file no longer makes: two
           --   season-level lookups and the four gates. build_ranking_results
           --   ran twenty minutes earlier and wrote the answer for this exact
           --   result_id.
           rr.pool                     AS pool
    FROM   results_tf r
    -- ★ THE FULL KEY, NOT JUST meet_id. meets_tf is keyed
    --   (div_id, meet_id, event_id) and holds 14.17M rows. Matching on
    --   meet_id alone CANNOT use that index -- div_id is the leading column --
    --   so the old LATERAL scanned to find its first match, once per row,
    --   across 62M TF rows. That is the whole reason TF took nine hours while
    --   XC did not: the XC join supplies both parts of meets' (meet_id,
    --   div_id) key, so it has always been an index lookup.
    --
    --   Supplying all three turns it into one. LIMIT 1 is also gone, and with
    --   it the quiet bug it was hiding: a TF meet has one meets_tf row PER
    --   EVENT, so "first match" was an arbitrary event's row. meet_name is the
    --   same across them, but `state` need not be.
    LEFT JOIN meets_tf m ON m.meet_id  = r.meet_id
                        AND m.div_id   = r.div_id
                        AND m.event_id = r.event_id
    -- ⚠ meets_tf IS ANET-ONLY, same as meets. Same fix, same reason.
    LEFT JOIN meets_tfrrs mt ON mt.meet_id = r.meet_id
                            AND mt.sport   = 'TF'
    {_GENDER_LATERAL}{_RANKING_JOIN_TF}
    WHERE {_PERF_COMMON_FILTERS}
      AND (r.is_relay IS NULL OR r.is_relay = 0)  -- a relay leg is not an individual mark
      AND (r.is_field IS NULL OR r.is_field = 0)  -- field events have no speed rating
"""


def _perfLink(sport, row):
    """Prebuild the href. The two boards link to different page types, so
    building it once here beats branching in Jinja on every render."""
    if sport == "XC":
        return f"/race/xc/{row['meet_id']}/{row['div_id']}"
    return f"/race/tf/{row['meet_id']}/{row['event_id']}/{row['div_id']}"


def _tilted(row):
    """
    The rating this performance is worth once the course is charged at the
    athlete's own level.

    Falls back to the stored rating whenever tilt.py is absent or the row
    carries no difficulty -- a missing venue must not silently drop a
    performance off the board.
    """
    raw = float(row["speed_rating"])
    if _ratingFor is None:
        return raw
    d = row.get("difficulty")
    return raw if d is None else float(_ratingFor(raw, d))


def _fmtTime(seconds):
    """Seconds -> m:ss.d, keeping only the precision the value carries.

    ⚠ A DISPLAY TWIN OF app.format_time, AND THEY MUST AGREE. This runs at
      build time and writes text into homepage_panels, so the site cannot
      reformat it later -- but a reader comparing the home board with a race
      page is comparing these two functions. Change one, change the other.
      Kept local rather than imported because app.py builds a Flask app at
      import time and this is a pipeline script.
    """
    if seconds is None:
        return None
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value >= DNF_SENTINEL:
        return None
    whole = int(value)
    hundredths = round((value - whole) * 100)
    tail = ("" if hundredths == 0
            else f".{hundredths // 10}" if hundredths % 10 == 0
            else f".{hundredths:02d}")
    if value < 60:
        return f"{whole}{tail}"
    hours, minutes, secs = whole // 3600, (whole % 3600) // 60, whole % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}{tail}"
    return f"{minutes}:{secs:02d}{tail}"


def _perfDetail(sport, row):
    """The one-line context shown next to the rating on the home board.

    ★ THE TIME AND THE MEET. Both, in that order, and the order is the whole
      design: the time is short and fixed-width so it always survives, and
      the meet name takes whatever room is left and ellipsises. A board
      headed "Best Performances" has to show the performance, and a person
      reading it wants to know where it happened.

    ⚠ THIS COLUMN HAS NOW BEEN WRONG TWICE, IN OPPOSITE DIRECTIONS. It read
      "{meet} ({course})" and showed a truncated fragment of a meet name and
      no performance at all. It was then cut to the time and the distance,
      which fits perfectly and throws away the one thing people recognise a
      race by. Neither end of that trade is right: put the fixed-width thing
      first and let the variable-width thing run out of room.

    ! THE COURSE IS THE PART THAT GOES. It is the least identifying of the
      three -- a meet name usually implies its course, and the race page one
      click away carries both. The distance goes with it: the RATING sits in
      the very next column and is the distance-normalised comparison, which
      is what the distance was there to enable.

    Falls back to the bare time when there is no meet name, and to the meet
    name when there is no usable time -- an empty cell says less than either.
    """
    meet = (row.get("meet_name") or "").strip()
    time_text = _fmtTime(row.get("time_seconds"))

    if time_text is None:
        return meet or "Unknown meet"
    if not meet:
        return time_text
    return f"{time_text} \u00b7 {meet}"


def _collectPerformances(conn, sport, season_year, buckets, stats):
    """Stream every rated result once; feed the season and all-time heaps.

    Each row is offered to the all-time heap always, and to the season heap
    only if its year matches. One pass, two outputs."""
    sql = _PERF_SQL_XC if sport == "XC" else _PERF_SQL_TF
    params = {"rmin": RATING_MIN, "rmax": RATING_MAX,
              "dnf": DNF_SENTINEL, "year_re": _SANE_YEAR,
              "ymin": _YEAR_MIN_STR, "ymax": _YEAR_MAX_STR,
              "excl": list(EXCLUDED_MEET_IDS)}

    seen = {}   # perf key -> best speed_rating seen for that physical race

    cur = _streamingCursor(conn, f"perf_{sport.lower()}")
    cur.execute(sql, params)
    try:
        for row in dictRows(cur):
            stats["perf_seen"] += 1
            # Already resolved, by build_ranking_results, from the same
            # resolvePool this loop used to call 101M times.
            pool = row["pool"]
            # ★ US ONLY. There is no country column; state is the whole
            #   signal, and inScope keeps anything it cannot PROVE is foreign.
            #   Without it the boards fill with New Zealand and Canadian
            #   athletes whose ratings are solved on a circuit that barely
            #   touches the American one, so they are not comparable even when
            #   they are correct.
            if not inScope(row.get("state")):
                stats["out_of_scope"] = stats.get("out_of_scope", 0) + 1
                continue
            if not _isRankablePool(pool):
                stats["perf_nopool"] += 1
                continue

            # --- cross-source dedupe -----------------------------------
            key = _perfKey(row, sport)
            # ★ THE DISPLAY TILT. A course does not slow every runner by the
            #   same factor -- measured over 51M rows, the effect scales with
            #   ability at corr = -0.993 against difficulty. Steens Mountain
            #   costs a 140-rated athlete 5.4 rating points less than delta
            #   says; Woodbridge gives one back. Applied HERE rather than in
            #   the engine so the coefficient stays tunable without a
            #   three-hour pipeline run. See tilt.py.
            score = _tilted(row)
            # Keep only the FIRST copy of a physical race, not the best.
            #
            # The old version kept the best, but never removed the worse copy
            # already pushed to the heap -- so anet 140.0 then tfrrs 140.1
            # produced BOTH on the board. Cross-source copies of one race differ
            # only in rounding, so first-wins loses nothing and cannot duplicate.
            if key is not None:
                if key in seen:
                    stats["perf_dup"] += 1
                    continue
                seen[key] = score
            # ------------------------------------------------------------

            payload = {
                "person_id":   row["person_id"],
                "name":        _fullName(row),
                "school":      row.get("school"),
                # SEASON year, not calendar: matches _yearCounts above and
                # ranking_results.year, so this board, the season-detection and
                # the /rankings page all agree on what "2026" contains.
                # ⚠ str(), NOT the bare int. This value is compared against
                #   _seasonYear()'s output two lines below, and that comes
                #   from substring(date,1,4) in SQL -- i.e. TEXT. The old
                #   _yearOf() returned a string slice, so the types matched by
                #   accident. seasonYearFromIso returns an int, and "2026" ==
                #   2026 is False in Python, so EVERY season performance row
                #   silently failed the test and the whole board went empty.
                #   It is also stored in homepage_panels.season_year, which is
                #   a text column.
                "season_year": str(seasonYearFromIso(sport, row["date"])),
                "detail":      _perfDetail(sport, row),
                "link":        _perfLink(sport, row),
                "name_link":   f"/athlete/{row['person_id']}",
            }

            _pushTop(buckets[("performance", "alltime", sport, pool)],
                     COLLECT_N, score, dict(payload))
            if payload["season_year"] == season_year:
                _pushTop(buckets[("performance", "season", sport, pool)],
                         COLLECT_N, score, dict(payload))
    finally:
        cur.close()



# ===================================================================== #
#  8. QUERY B -- ATHLETE SEASON AVERAGES
# ===================================================================== #
#
# athlete_ratings cannot serve this board: its pool has no SPORT in it
# (values are hs_m / college_f), and every page on the site splits XC from TF.
# So the athlete board is computed from results too.
#
# Postgres does the averaging (cheap, set-based); Python only pools and ranks.
# grade/school/source vary row-to-row within a season, so we take the MODE --
# the most common value -- rather than trusting an arbitrary row.

# ! READS athlete_season, NOT THE RAW TABLES. That table is written by
#   build_ranking_results, which runs immediately before this script, and it
#   already holds one row per (person, pool, sport, year) with the pool
#   RESOLVED. The previous version aggregated 101M raw rows through seven
#   joins and then called resolvePool once per group, reproducing work that
#   had finished twenty minutes earlier.
#
#   15.8M pre-aggregated rows against 101M raw ones, no gate joins, no
#   season-level joins, and no pooling in Python.
#
# * AND THE TWO BOARDS NOW AGREE BY CONSTRUCTION. panels and /rankings used to
#   pool independently from the same five facts; keeping five joins in step
#   across two files is exactly how they drifted before. One source, one
#   answer.
#
# ! THE NAME STILL NEEDS A JOIN. athlete_season carries no name, so `ath` --
#   the temp table built once at the top of this run -- supplies it. Its
#   DISTINCT ON prefers rows that HAVE a last name, which is why the athlete
#   boards stopped printing "Unknown" for people the performance boards named.
_ATHLETE_SQL_TEMPLATE = """
    SELECT s.person_id,
           s.year          AS yr,
           s.pool,
           s.mean_rating   AS avg_rating,
           s.n_races,
           s.school,
           s.state,
           s.grade,
           a.first_name,
           a.last_name,
           a.gender
    FROM   athlete_season s
    LEFT   JOIN ath a ON a.person_id = s.person_id
    WHERE  s.sport = %(sport)s
      AND  s.mean_rating IS NOT NULL
      AND  s.mean_rating BETWEEN %(rmin)s AND %(rmax)s
      AND  s.n_races >= %(minraces)s
"""


def _seasonJoinFor(sport):
    """The season-verdict join for one sport, with the combined fallback."""
    return _seasonLevelJoin(sport)


def _athleteSql(sport):
    """No holes left to fill.

    The template used to be parameterised by results table, extra filters,
    athletes key and season expression. Reading athlete_season removes all
    four: the sport is a bind parameter, the season is a stored column, the
    relay and field exclusions were applied when that table was built, and the
    name join is the same `ath` for both sports.
    """
    return _ATHLETE_SQL_TEMPLATE


def _collectAthletes(conn, sport, season_year, buckets, stats):
    """One row per (athlete, year). Feeds two boards:

      season  -- that athlete's average in the CURRENT season
      alltime -- the best SINGLE SEASON anyone ever had

    All-time is best-season, not best-career, deliberately: a career average
    spans high school and college and has no honest pool. Best-season pools
    cleanly by that season's own grade."""
    # The date, year and meet filters are gone: athlete_season was built from
    # rows that already passed them.
    params = {"rmin": RATING_MIN, "rmax": RATING_MAX, "sport": sport,
              "minraces": min(SEASON_MIN_RACES, ALLTIME_MIN_RACES)}

    cur = _streamingCursor(conn, f"ath_{sport.lower()}")
    cur.execute(_athleteSql(sport), params)
    try:
        for row in dictRows(cur):
            stats["ath_seen"] += 1
            # Already resolved, by build_ranking_results, using the same
            # resolvePool this file used to call 15.8M times.
            pool = row["pool"]
            # ★ US ONLY. There is no country column; state is the whole
            #   signal, and inScope keeps anything it cannot PROVE is foreign.
            #   Without it the boards fill with New Zealand and Canadian
            #   athletes whose ratings are solved on a circuit that barely
            #   touches the American one, so they are not comparable even when
            #   they are correct.
            if not inScope(row.get("state")):
                stats["out_of_scope"] = stats.get("out_of_scope", 0) + 1
                continue
            if not _isRankablePool(pool):
                stats["ath_nopool"] += 1
                continue

            n = int(row["n_races"])
            payload = {
                "person_id":   row["person_id"],
                "name":        _fullName(row),
                "school":      row.get("school"),
                # ! str(), AND THIS EXACT BUG HAS NOW HAPPENED TWICE.
                #   _seasonYear() returns TEXT from SQL. The old athlete query
                #   built `yr` with substring(), also text, so the comparison
                #   below matched. athlete_season.year is an INTEGER, so
                #   2025 == "2025" is False and the season board silently came
                #   back empty. The performance board carries the same cast at
                #   line ~899 for the same reason.
                "season_year": str(row["yr"]),
                "detail":      f"{n} races",
                "link":        f"/athlete/{row['person_id']}",
                "name_link":   f"/athlete/{row['person_id']}",  # same; detail isn't a link
            }
            score = float(row["avg_rating"])

            if n >= ALLTIME_MIN_RACES:
                _pushTop(buckets[("athlete", "alltime", sport, pool)],
                         COLLECT_N, score, dict(payload))
            if str(row["yr"]) == str(season_year) and n >= SEASON_MIN_RACES:
                _pushTop(buckets[("athlete", "season", sport, pool)],
                         COLLECT_N, score, dict(payload))
    finally:
        cur.close()


# ===================================================================== #
#  9. WRITING THE PANEL TABLE
# ===================================================================== #

_DDL = """
CREATE TABLE IF NOT EXISTS homepage_panels (
    board       text    NOT NULL,     -- 'athlete' | 'performance'
    scope       text    NOT NULL,     -- 'season'  | 'alltime'
    sport       text    NOT NULL,     -- 'XC'      | 'TF'
    pool        text    NOT NULL,     -- 'hs_m', 'college_f', ...
    rank        integer NOT NULL,     -- 1..TOP_N, precomputed
    person_id   bigint,
    name        text,
    school      text,
    rating      real,
    season_year text,
    detail      text,
    link        text,
    name_link   text,
    visible     boolean NOT NULL DEFAULT true,
    note        text,
    PRIMARY KEY (board, scope, sport, pool, rank)
);

CREATE TABLE IF NOT EXISTS homepage_meta (
    key   text PRIMARY KEY,
    value text
);
"""


# ===================================================================== #
#  9b. HERO FACTS
# ===================================================================== #
#
# Three numbers under the tagline on the landing page. Each one is chosen to
# carry a different half of "Every result on one comparable scale":
#
#   results   the corpus is real          -- RATED rows, not total rows.
#   courses   the scale is one scale      -- the part nobody else has.
#   years     the scale reaches backwards -- era correction, made visible.
#
# ★ RATED, NOT TOTAL. results + results_tf hold ~61.8M rows against ~59.0M
#   rated. Quoting the bigger number buys 4.8% and costs the claim: an unrated
#   row is precisely a row NOT on the comparable scale, so it would contradict
#   the sentence directly above it on the page.
#
# ★ PRE-FORMATTED HERE, NOT IN THE TEMPLATE. Whether 58,958,108 reads as "59M"
#   or "58.9M" or "59 million" is an editorial choice, and it is far easier to
#   change in Python than to build a rounding filter in Jinja. The template
#   owns the LABEL; this owns the NUMBER.

# Same guards _yearCounts uses: the regex rejects malformed dates and the range
# rejects the junk years (2222, 2223) that survive it.
_FACT_SPAN_SQL = """
    SELECT count(*)                    AS n,
           min(substring(date, 1, 4))  AS y0,
           max(substring(date, 1, 4))  AS y1
    FROM   {table}
    WHERE  speed_rating IS NOT NULL
      AND  date ~ %s
      AND  substring(date, 1, 4) BETWEEN '1990' AND '2036'
"""


def _formatCount(n):
    """58958108 -> '59M'. A fact strip is scanned, not read."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _ratedSpan(conn, sport):
    """(rows, first_year, last_year) for one sport, in ONE scan.

    Count and span together because they come from the same predicate over the
    same 34M rows -- asking twice would scan twice for no reason.
    """
    table = "results" if sport == "XC" else "results_tf"
    with conn.cursor() as cur:
        cur.execute(_FACT_SPAN_SQL.format(table=table), (_SANE_YEAR,))
        n, y0, y1 = cur.fetchone()
    return (n or 0,
            int(y0) if y0 else None,
            int(y1) if y1 else None)


def _courseCount(conn):
    """Solved difficulty cells, or None if the table is not there yet.

    to_regclass rather than a try/except on the query: a missing table inside a
    transaction poisons it for every statement after, and this runs on the same
    connection that is about to write the panels.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('course_difficulties')")
        if cur.fetchone()[0] is None:
            return None
        cur.execute("SELECT count(*) FROM course_difficulties")
        return cur.fetchone()[0]


def _siteFacts(conn, sports):
    """The three hero numbers, pre-formatted, as meta keys.

    Returns a PARTIAL dict on any failure rather than raising. A fact that
    cannot be computed becomes an em-dash on the page; a fact that raises
    would take the whole panel rebuild down with it.
    """
    facts = {}
    try:
        total, first, last = 0, None, None
        for sport in sports:
            n, y0, y1 = _ratedSpan(conn, sport)
            total += n
            if y0 and (first is None or y0 < first):
                first = y0
            if y1 and (last is None or y1 > last):
                last = y1

        if total:
            facts["fact_results"] = _formatCount(total)
        if first and last:
            # Inclusive: 1990..2026 is 37 years of racing, not 36.
            facts["fact_years"] = str(last - first + 1)

        courses = _courseCount(conn)
        if courses:
            facts["fact_courses"] = _formatCount(courses)

    except Exception as exc:
        print(f"[panels] hero facts unavailable ({exc}) -- page shows dashes")

    return facts


def _writePanels(conn, buckets, meta):
    """Swap in the new panels atomically.

    DELETE + INSERT inside ONE transaction: the site either sees the whole old
    snapshot or the whole new one, never a half-empty front page mid-write."""
    rows = []
    for (board, scope, sport, pool), heap in buckets.items():
        # "athlete" is one row per person by definition; a performance
        # board is many per person, capped. See PERF_PER_PERSON.
        cap = 1 if board == "athlete" else PERF_PER_PERSON
        for entry in _drainRanked(heap, max_per_person=cap):
            note    = SUPPRESSED_BOARDS.get((board, scope))
            visible = note is None
            rows.append((board, scope, sport, pool, entry["rank"],
                         entry["person_id"], entry["name"], entry["school"],
                         entry["rating"], entry["season_year"],
                         entry["detail"], entry["link"], entry["name_link"], visible, note))

    with conn.cursor() as cur:
        cur.execute(_DDL)
        cur.execute("DELETE FROM homepage_panels")
        psycopg2.extras.execute_values(cur, """
            INSERT INTO homepage_panels
                (board, scope, sport, pool, rank, person_id, name,
                 school, rating, season_year, detail, link, name_link,
                 visible, note)
            VALUES %s
        """, rows, page_size=1000)

        cur.execute("DELETE FROM homepage_meta")
        psycopg2.extras.execute_values(cur,
            "INSERT INTO homepage_meta (key, value) VALUES %s",
            list(meta.items()))
    conn.commit()          # one commit: DELETE + INSERT land together or not at all
    return len(rows)


# ===================================================================== #
#  10. MAIN
# ===================================================================== #

def main():
    ap = argparse.ArgumentParser(description="Rebuild landing-page panels.")
    ap.add_argument("--sport", choices=["XC", "TF"], action="append",
                    help="limit to one sport (repeatable). Default: both.")
    args = ap.parse_args()
    sports = args.sport or ["XC", "TF"]

    buckets = defaultdict(list)      # (board, scope, sport, pool) -> heap
    stats = defaultdict(int)
    meta = {}

    with getConn() as conn:                      # app.py's helper, app.py's style
        # Room to work: the aggregates and the temp-table build below are
        # ordinary statements and can use parallel workers. The streaming
        # cursors cannot -- see dbfast.
        tuneSession(conn)
        # ★ ONCE, BEFORE THE SPORT LOOP. `ath` is session-scoped, so it must be
        #   built on the SAME connection every query below uses -- and it must
        #   be built before the first query, not lazily, so a failure surfaces
        #   here rather than as a confusing "relation ath does not exist" in
        #   the middle of a scan.
        _buildAthleteTemp(conn)

        for sport in sports:
            season_year = _seasonYear(conn, sport)
            # ! THE META CARRIES THE LABEL, NOT THE STORED YEAR. A TF season
            #   is STORED under the year it opens in (Dec 2025 - Jul 2026 is
            #   2025) but NAMED year + 1 everywhere a person reads it -- and
            #   /rankings' year filter takes the label (rankings._yearClause).
            #   Publishing the stored year here made the home page head a
            #   season "TF -- 2025" that /rankings calls 2026, and its
            #   View-all link's year=2025 then filtered the season BEFORE the
            #   one on screen.
            #
            # ⚠ season_year itself stays stored: every query below compares
            #   it against stored columns. Only what leaves for the reader is
            #   relabelled.
            label = season_year
            if sport == "TF" and season_year:
                label = str(int(season_year) + 1)
            meta[f"season_year_{sport}"] = label or ""
            print(f"[{sport}] season year = {season_year} "
                  f"(displayed as {label})")

            _collectPerformances(conn, sport, season_year, buckets, stats)
            print(f"[{sport}] performances scanned: {stats['perf_seen']:,} "
                  f"(no pool: {stats['perf_nopool']:,})")
            print(f"[{sport}] perf dups skipped: {stats['perf_dup']:,}")

            _collectAthletes(conn, sport, season_year, buckets, stats)
            print(f"[{sport}] athlete-seasons scanned: {stats['ath_seen']:,} "
                  f"(no pool: {stats['ath_nopool']:,})")

        meta["built_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        meta["default_sport"] = _defaultSport(datetime.date.today().month)

        # Hero facts. Last, so a slow or failing count cannot delay the scans
        # that actually matter, and so `meta` is otherwise complete already.
        meta.update(_siteFacts(conn, sports))
        print(f"[panels] hero facts: "
              + ", ".join(f"{k.removeprefix('fact_')}={v}"
                          for k, v in sorted(meta.items())
                          if k.startswith("fact_")) or "[panels] hero facts: none")

        written = _writePanels(conn, buckets, meta)
        print(f"wrote {written:,} panel rows across {len(buckets)} buckets")


if __name__ == "__main__":
    main()