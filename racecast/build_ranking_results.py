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
import re
import sys
import time
import argparse
import datetime
import concurrent.futures as cf

import psycopg2.errors
import psycopg2.extras

sys.path.insert(0, "scripts")
from database import getConn

# poolFor is the engine's SSOT for grade -> level -> pool. IMPORT it; do not
# reimplement it in SQL. Reimplementing is how the site and the engine drift
# into disagreeing about who is a college athlete.
sys.path.insert(0, "engine")
import person_gender as _pg      # gender by the rows (issue 164)
# seasonYearFromIso comes from the same module the engine uses, for the same
# reason poolFor is imported rather than reimplemented: if the site and the
# engine disagree about which season a race is in, every board silently splits
# the athletes the engine deliberately joined.
from season_year import seasonYearFromIso, seasonYearSql, seasonYearSqlInt

try:
    from normalize_distance import poolFor, poolBandFor
    from anchor_check import mismatch as anchorMismatch
    # ★ THE SAME READER THE ENGINE AND anchor_check USE. Track keeps its
    #   distance in the event name and event_parse is where that is read; a
    #   second copy of that logic here would be a third way to get it wrong.
    from event_parse import distanceFromEventShort, sprintDistanceFromEventShort
    # the field-event mark parser, beside this file (racecast/marks.py)
    from marks import parseMark, normalizeFieldEvent, saneMark
    from dbfast import tuneSession
    from pool_ceiling import ceilingFor
    from pool_resolve import resolvePool, inScope
except ImportError as exc:
    raise SystemExit(
        f"Could not import poolFor ({exc}).\n"
        "Fix the import path at the top of this file. Do NOT reimplement the "
        "pool logic here -- it must stay identical to the engine's."
    )


# ------------------------------------------------------------------ #
#  THE PHASE CLOCK
# ------------------------------------------------------------------ #
#
# ★ 112 MINUTES WITH NO BREAKDOWN IS NOT A MEASUREMENT. The 2026-08 run logged
#   one number for the whole build and printed nothing between the last TF
#   progress line and "swapping in", so the only way to find the cost was to
#   guess at it. Profiled separately, the Python half of this build --
#   prepareRow plus the COPY payload -- is 8 us/row, which is EIGHT minutes for
#   60M rows. The other hundred are Postgres, and this is how the next run says
#   which statement.
_PHASES = []


class phase:
    """Time one named phase and remember it for the summary."""

    def __init__(self, label):
        self.label = label

    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *exc):
        _PHASES.append((self.label, time.time() - self.t0))
        return False


def phaseReport():
    if not _PHASES:
        return
    total = sum(dt for _l, dt in _PHASES)
    print("\n  WHERE THE TIME WENT")
    for label, dt in _PHASES:
        share = 100.0 * dt / total if total else 0.0
        print(f"    {dt / 60:7.1f} min  {share:5.1f}%  {label}")
    print(f"    {total / 60:7.1f} min          (phases; the rest is startup)")


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

# How many indexes to build at once. Each CREATE INDEX already uses up to
# max_parallel_maintenance_workers on its own, so this multiplies rather than
# replaces it -- 3 stays inside max_parallel_workers (8) with room for the
# three leader processes.
_INDEX_JOBS = 3


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
#   ⚠ THE RULE MUST MATCH THE ENGINE'S, WHATEVER IT IS. DISTINCT ON with the
#     same ORDER BY reproduces the lateral exactly; a different tie-break here
#     would silently repool athletes relative to the ratings they carry.
#     tests/test_gender_pick.py pins the two together.
#
#   ⚠ IT WAS `ORDER BY a.school`, AND THAT WAS THE BUG (owner, 2026-09-01).
#     Deterministic, but arbitrary: when one person_id carries rows of both
#     genders -- two real people merged under one id, or a mis-sexed feed row
#     -- the winner was whichever SCHOOL NAME sorted first. Reported for Cam
#     Kuss, who is two people: "Broughton (NC)" sorts before "Unattached
#     (TX)", so the boy was pooled and rated as a girl for his whole career.
#
#   ! THE MAJORITY OF `athletes` ROWS DECIDES, ties to 'M' (gender DESC puts
#     'M' above 'F'). Both keys are needed -- count alone is not
#     deterministic. This does NOT fix a merged person; it makes the merge
#     land on the more-evidenced side instead of on an alphabetical accident.
#     Separating them is #93.
# ★ THE ROWS DECIDE WHEN person_gender EXISTS (issue 164): the majority of
#   the divisions the person raced under, then the profile majority for
#   anyone with no labelled race; `split` marks a person who raced both
#   ways enough to be two athletes, and the board query then takes the
#   ROW's own label for them (boardGenderExpr).
_GENDER_TEMP_SQL = """
    DROP TABLE IF EXISTS tmp_person_gender;
    CREATE TEMP TABLE tmp_person_gender AS
    SELECT p.person_id,
           COALESCE(pg.gender, p.gender)      AS gender,
           COALESCE(pg.split, false)          AS split
    FROM (
        SELECT DISTINCT ON (person_id) person_id, gender
        FROM (
            SELECT a.athlete_id AS person_id, a.gender, count(*) AS n
            FROM   athletes a
            WHERE  a.gender IN ('M', 'F')
            GROUP  BY a.athlete_id, a.gender
        ) s
        ORDER BY person_id, n DESC, gender DESC
    ) p
    LEFT JOIN {pg_table} pg ON pg.person_id = p.person_id;
    CREATE UNIQUE INDEX ON tmp_person_gender (person_id);
    ANALYZE tmp_person_gender;
"""

# without the table, an empty stand-in with the same shape
_PG_EMPTY = "(SELECT NULL::bigint AS person_id, NULL::text AS gender, NULL::boolean AS split WHERE false)"

_GENDER_JOIN = """
        LEFT JOIN tmp_person_gender a
               ON a.person_id = COALESCE(r.person_id, r.athlete_id)
"""


def prepareGenderTemp(conn):
    """One pass over `athletes`, so the per-row lateral becomes a hash join.

    Same rule as the lateral it replaces -- majority of `athletes` rows, ties
    to 'M' -- so every athlete resolves to the gender the engine gave them.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.person_gender')")
        have_pg = cur.fetchone()[0] is not None
        print(f"  person_gender: {'rows decide' if have_pg else 'absent -- profile majority'}")
        cur.execute(_GENDER_TEMP_SQL.format(
            pg_table='person_gender' if have_pg else _PG_EMPTY))
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


_XC_TFRRS_DIST_SQL = """
    DROP TABLE IF EXISTS tmp_xc_tfrrs_dist;
    CREATE TEMP TABLE tmp_xc_tfrrs_dist AS
        -- ⚠ XC ONLY, AND IT WAS NOT. Without this filter the table carries
        --   the TF meets too, so a meeting with both sports contributes two
        --   rows for the same (meet_id, div_id, source) -- and the LEFT JOIN
        --   below FANS OUT rather than looking up. Measured: the TF stream
        --   went from ~10 minutes to 130.8 of a 151.4 minute build.
        --
        -- ★ DISTINCT ON, so the key is unique BY CONSTRUCTION and the index
        --   below can prove it. min() over a jsonb blob would hide a genuine
        --   disagreement; picking deterministically and letting the unique
        --   index fail loudly is the trade this file makes everywhere else.
        SELECT DISTINCT ON (m.meet_id, (kv.key)::bigint, m.source)
               m.meet_id,
               (kv.key)::bigint                        AS div_id,
               m.source,
               (kv.value ->> 'distance')::float        AS distance
        FROM   meets_tfrrs m,
               LATERAL jsonb_each(m.division_distances) kv
        WHERE  m.division_distances IS NOT NULL
          AND  m.sport = 'XC'
          AND  kv.value ->> 'distance' IS NOT NULL
          AND  kv.key ~ '^[0-9]+$'
        ORDER  BY m.meet_id, (kv.key)::bigint, m.source,
                  (kv.value ->> 'distance')::float;
    -- ! UNIQUE, so a future fan-out is an ERROR rather than an hour.
    CREATE UNIQUE INDEX ON tmp_xc_tfrrs_dist (meet_id, div_id, source);
    ANALYZE tmp_xc_tfrrs_dist;
"""


def prepareXcTfrrsDistTemp(conn):
    """The XC distances that are NOT in `meets`, which is half the corpus.

    ⚠ THE XC QUERY JOINED `meets` AND NOTHING ELSE, AND `meets` IS THE ANET
      SOURCE. tfrrs cross-country divisions keep their per-division distance
      in meets_tfrrs.division_distances -- a JSONB blob keyed by div_id as a
      string, with no div_id column to join on -- so every one of them
      reached ranking_results with distance NULL.

      That is why propose_distances._DISTANCES has to UNION two sources to
      build div_distance, and why audit_overrides prints them apart:

          anet:   812,079 divisions with a distance
          tfrrs:   41,508 divisions with a distance

      This file referenced neither meets_tfrrs nor div_distance. Measured
      consequence, from 10_rankings' own log:

          anchor gate: 585,213 UNCHECKED (1.8% -- no distance, so the gate
          could not fire on them)

      A NULL distance is not a rating error, which is why every
      rating-based audit walked past it: the ratings in `results` are fine.
      It is the absence of the one column the anchor gate and the PR boards
      need. Meet 26359 -- Ox Bow Park, the JV Minutemen Classic -- carries
      568 tfrrs rows across divisions 0-3 and has no `meets` row at all.

    ! SAME SHAPE AS prepareTfStateTemp, for the same reason: collapse the
      blob once into a table unique on (meet_id, div_id, source), index it,
      and let the streaming query do an indexed lookup instead of expanding
      JSON 34.5M times.
    """
    with conn.cursor() as cur:
        cur.execute(_XC_TFRRS_DIST_SQL)
        cur.execute("SELECT count(*) FROM tmp_xc_tfrrs_dist")
        n = cur.fetchone()[0]
    conn.commit()
    print(f"  XC tfrrs distances: {n:,} meet-divisions "
          f"(built once; these are absent from `meets` entirely)")


# Purpose:   the TF events the engine deliberately declines to rate, so their
#            rows can still reach the TIME-ranked boards. Issue #46 (and #42).
# Output:    tmp_sprint_events(event_short), one row per sprint event name.
# Detail:
#   ★ THE SPRINT BOARDS WERE EMPTY BY CONSTRUCTION, not by a bug in the board.
#     rankings.PR_DISTANCES has offered 55 through 600 all along, but the
#     candidate rows come from ranking_results, which this script filled only
#     from RATED rows -- and speed_ratings_db._tfQuery rates nothing under
#     800m. The page was working perfectly on an empty input.
#
#   ⚠ AND THE OBVIOUS FIX IS WRONG. Lowering the engine's 800m gate would
#     apply the distance law outside its fitted domain: (D/5000)^b turns an
#     11-second 100m into a ~693-second "5K", which lands inside the sanity
#     band and is complete garbage.
#
#   ★ SO THE PR BOARD RANKS THE CLOCK, AND NEVER NEEDED A RATING. These rows
#     reach ranking_results with speed_rating NULL: present for the
#     time-ranked boards, absent from every rating board, which now excludes
#     NULL explicitly (rankings.getPerformances) rather than by luck.
#
#   ! RESOLVED IN PYTHON, ONCE, BECAUSE THE MAPPING IS PYTHON. What a distance
#     an event name means is event_parse.distanceFromEventShort's answer and
#     nobody else's; reimplementing it as a SQL LIKE would be a second answer
#     that drifts. results_tf has few DISTINCT event_short values relative to
#     its rows, so one pass over them is cheap and the join is then a hash.
# ! 800 STAYS HERE WHILE THE ENGINE'S FLOOR IS 600 (issue 42, owner
#   2026-09-02): a 600 m row is admitted to the times board by this
#   whitelist whether or not the backfill has normalised it yet, and once
#   it carries a rating the rated path above takes it first.
_SPRINT_MAX_DISTANCE = 800.0


def ensureWheelchairPerson(conn):
    """wheelchair_person exists, possibly empty, before any query anti-joins
    it. Built for real by engine/wheelchair_flag.py --write (step 04b).

    ★ THE PERSON-LEVEL CHAIR EXCLUSION, ON THE BOARDS AND THE PRICER
      (2026-09-03). The engine refuses every row of a chair athlete
      (speed_ratings_db._chairFilter), so their rows leave the go-live with
      speed_rating NULL -- and fill_ratings, which inverts _SQL below to
      price every row the solve refused, priced them at K / normalized_time.
      The backfill only blanks normalized_time for athletes the DIVISION
      LABELS name; wheelchair_person carries the wider rule (para words, the
      T/F class codes, both feeds), and those athletes came back with flat
      ratings on their pages and, when the pace band let them through, on
      the boards. The anti-join in _SQL keeps them out of both: the boards
      read it directly, the pricer through the inverted WHERE."""
    from wheelchair_flag import ensureTable
    with conn.cursor() as cur:
        n = ensureTable(cur)
    conn.commit()
    if n:
        print(f"  wheelchair_person: {n:,} chair athletes excluded by person")
    else:
        print("  ⚠ wheelchair_person is EMPTY -- no chair athlete is excluded "
              "by person. Run: python engine/wheelchair_flag.py --write "
              "(step 04b)")
    return n


def ensureResultTwin(conn):
    """result_twin exists, possibly empty, before any query anti-joins it.
    Built for real by engine/twin_flag.py --write (step 04c)."""
    from twin_flag import ensureTable
    with conn.cursor() as cur:
        ensureTable(cur)
    conn.commit()


def prepareSprintEvents(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT event_short FROM results_tf "
                    "WHERE event_short IS NOT NULL")
        names = [r[0] for r in cur.fetchall()]
        sprints = []
        n_nonflat = 0
        for ev in names:
            d = _tfDistance(ev)
            if d is not None and 0 < float(d) < _SPRINT_MAX_DISTANCE:
                sprints.append(ev)
            elif timedEventKind(ev)[0] is not None:
                # ★ HURDLES AND THE STEEPLE ENTER THE SAME WAY (owner,
                #   2026-09-02): timed, unrated, for the times board only.
                sprints.append(ev)
                n_nonflat += 1

        cur.execute("DROP TABLE IF EXISTS tmp_sprint_events")
        cur.execute("CREATE TEMP TABLE tmp_sprint_events (event_short text)")
        if sprints:
            psycopg2.extras.execute_values(
                cur, "INSERT INTO tmp_sprint_events (event_short) VALUES %s",
                [(e,) for e in sprints], page_size=1000)
        cur.execute("CREATE UNIQUE INDEX ON tmp_sprint_events (event_short)")
        cur.execute("ANALYZE tmp_sprint_events")
    conn.commit()
    print(f"  sprint events (<{_SPRINT_MAX_DISTANCE:.0f}m, rated by nobody): "
          f"{len(sprints) - n_nonflat:,} of {len(names):,} event names, "
          f"plus {n_nonflat:,} hurdle/steeple names (timed, unrated)")



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
               -- ⚠ THREE SOURCES, NOT TWO. dov is the override, m.distance
               --   is anet, and xtd is tfrrs -- which lives in a JSONB blob
               --   in meets_tfrrs and was missing entirely. See
               --   prepareXcTfrrsDistTemp.
               COALESCE(dov.distance, m.distance, xtd.distance) AS distance,
               -- ! FOR THE CORRECTED-DIVISION GATE below. An override that
               --   DISAGREES with the scrape marks a division whose recorded
               --   distance was wrong; owner's rule (2026-08-27): such a
               --   division is displayed, never ranked. Agreement entries
               --   and sole-source fills are not corrections and pass.
               (dov.distance IS NOT NULL
                AND COALESCE(m.distance, xtd.distance) IS NOT NULL
                AND abs(dov.distance - COALESCE(m.distance, xtd.distance))
                    >= 1)                             AS dist_corrected,
               -- ★ THE EVENT, FOR THE RACE LINK. A TF race page is
               --   /race/tf/<meet>/<event>/<div> -- three parts -- and
               --   without this column the frontend can only build two, which
               --   matches no route and 404s. XC has no event dimension and
               --   emits NULL.
               {_EVENT_ID} AS event_id,
               m.state, {_pg.boardGenderExpr('XC')} AS gender,
               COALESCE(asl_s.level, asl_a.level) AS season_level,
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
        LEFT JOIN tmp_xc_tfrrs_dist xtd
               ON xtd.meet_id = r.meet_id AND xtd.div_id = r.div_id
              AND xtd.source  = r.source
        {_GENDER_JOIN}{_seasonLevelJoin("XC")}{_gateJoins("XC")}
        WHERE r.speed_rating IS NOT NULL
          AND r.person_id IS NOT NULL
          -- ! A FLAGGED TWIN NEVER RANKS (issue 94): result_twin is the one
          --   verdict every reader shares; ensureResultTwin makes the table
          --   exist (possibly empty) before this runs.
          AND NOT EXISTS (SELECT 1 FROM result_twin x
                          WHERE x.sport = 'XC' AND x.result_id = r.result_id)
          -- ★ NOT ONE RACE OF A CHAIR ATHLETE (issue 14, 2026-09-03), by
          --   person: the same list the engine consults. fill_ratings
          --   inverts only the speed_rating clause of this WHERE, so this
          --   keeps chair athletes out of the flat pricing too. See
          --   ensureWheelchairPerson.
          AND NOT EXISTS (SELECT 1 FROM wheelchair_person wc
                          WHERE wc.person_id = r.person_id)
          AND r.date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}$'
          AND r.date >= %(since)s
          AND r.date <  %(until)s
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
               -- the field-event mark text and flag, for the marks board
               r.mark,
               COALESCE(r.is_field, 0) AS is_field,

               -- ★ THE EVENT, FOR THE RACE LINK. A TF race page is
               --   /race/tf/<meet>/<event>/<div> -- three parts -- and
               --   without this column the frontend can only build two, which
               --   matches no route and 404s. XC has no event dimension and
               --   emits NULL.
               COALESCE(r.event_id, -1) AS event_id,
               m.state, {_pg.boardGenderExpr('TF')} AS gender,
               COALESCE(asl_s.level, asl_a.level) AS season_level,
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
        -- ⚠ NO tmp_xc_tfrrs_dist JOIN HERE, AND THERE USED TO BE ONE.
        --   Track takes its distance from the EVENT NAME -- the select list
        --   above reads dov.distance and r.event_short and nothing else -- so
        --   the join was dead weight: 25.4M lookups whose result was never
        --   read. It was also the cross-sport fan-out described in
        --   _XC_TFRRS_DIST_SQL.
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
        LEFT JOIN tmp_sprint_events se ON se.event_short = r.event_short
        -- ★ RATED, OR A SPRINT (issue #46). The engine rates nothing under
        --   800m on purpose, so a sprint has no speed_rating and never
        --   reached the boards -- which is why every sprint PR board was
        --   empty. It comes through unrated instead: the PR boards rank a
        --   CLOCK and never needed a rating, and every rating board excludes
        --   NULL. Nothing else unrated is admitted; se.event_short is the
        --   whitelist.
        -- ★ OR A FIELD EVENT (owner, 2026-09-02): admitted for its MARK,
        --   never rated, published to the times/marks board only. prepareRow
        --   parses the mark and refuses what it cannot read (marks.py).
        WHERE (r.speed_rating IS NOT NULL OR se.event_short IS NOT NULL
               OR COALESCE(r.is_field, 0) = 1)
          -- ! A RUNNING ROW WITH NO TIME IS A NON-FINISH (issue 59): the
          --   scraper now keeps DNF/DNS/DQ rows with their letters in
          --   `mark`. They belong on the athlete page, never on a board.
          AND NOT (COALESCE(r.is_field, 0) = 0 AND r.time_seconds IS NULL)
          AND r.person_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM result_twin x
                          WHERE x.sport = 'TF' AND x.result_id = r.result_id)
          -- ★ NOT ONE RACE OF A CHAIR ATHLETE (issue 14, 2026-09-03), by
          --   person; see the XC half and ensureWheelchairPerson.
          AND NOT EXISTS (SELECT 1 FROM wheelchair_person wc
                          WHERE wc.person_id = r.person_id)
          AND COALESCE(r.is_relay, 0) = 0
          AND r.date ~ '^(19|20)[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}$'
          AND r.date >= %(since)s
          AND r.date <  %(until)s
    """,
}


# The swap's patience. See the comment at the rename below.
_SWAP_LOCK_TIMEOUT = "3s"
_SWAP_ATTEMPTS = 20
_SWAP_BACKOFF = 15


_COLUMNS = ("sport", "result_id", "person_id", "pool", "speed_rating",
            "race_date", "year", "state", "school", "grade",
            "meet_id", "div_id", "canon_meet_id", "time_seconds",
            # ! LAST, so an older ranking_results is a column short rather
            #   than a column SHIFTED. COPY matches by position.
            "distance", "event_id",
            # ★ THE SCHOOL'S UNITS, DENORMALISED ONTO EVERY ROW. The rankings
            #   filters previously reached school_unit with a semi-join per
            #   request; a column is one comparison on a row already being
            #   read, and the planner can combine it with the existing
            #   (pool, year, rating) index instead of hashing a second
            #   relation. See rankings._whereClauses.
            #
            # ! COLLEGE AND HIGH SCHOOL BOTH, in one set of columns. A school
            #   is one or the other, so the unused ones are simply NULL --
            #   cheaper and far simpler than two column families, and it is
            #   what school_unit already stores.
            "division", "region", "conference", "league",
            "state_div", "section_div", "district", "county", "class",
            "area", "section",
            # ★ THE EVENT AXIS OF THE TIMES/MARKS BOARD (owner, 2026-09-02).

            #   event_kind: NULL for a flat race; 'hurdles' / 'steeple' for
            #   a timed non-flat race, whose metres ride in `distance`; or a
            #   field key (marks.normalizeFieldEvent) for a field event,
            #   whose parsed metres ride in `mark` and whose time_seconds is
            #   NULL. Last, for the same positional reason as `distance`.
            "event_kind", "mark")



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


# ★ ONE ROW PER SCHOOL, HELD IN MEMORY, BECAUSE THE ALTERNATIVE IS 61.6M
#   LOOKUPS. school_unit is ~150k rows; the corpus is 61.6M. Streaming a join
#   would re-read the same handful of schools millions of times.
#
# ! KEYED (school, state) WITH A BARE-school FALLBACK, which is the same
#   tie-break school_units.unitsFor uses: state narrows a shared name to one
#   real school, and without it the row with the most votes wins. A board row
#   carries its state, so the precise key is usually available.
#
# ⚠ AND IT DEGRADES TO NOTHING. No school_unit table (an old database,
#   mid-rebuild) means every unit column is NULL and the filters return
#   empty -- never an error, exactly as school_identity behaves.
_UNIT_COLS = ("division", "region", "conference", "league",
              "state_div", "section_div", "district", "county", "class",
              "area", "section")

_UNITS = {"loaded": False, "by_key": {}, "by_school": {}, "campus": {}}


def _campusState(school):
    """The college directory's state for a name it knows, else None.
    Cached per name: normName runs regexes."""
    if not school or not _UNITS["campus"]:
        return None
    hit = _UNITS.setdefault("campus_cache", {}).get(school, "")
    if hit != "":
        return hit
    try:
        from build_college_directory import lookup
        st = lookup(_UNITS["campus"], school)
    except Exception:                               # noqa: BLE001
        st = None
    _UNITS["campus_cache"][school] = st
    return st


def _loadUnits(conn):
    if _UNITS["loaded"]:
        return
    _UNITS["loaded"] = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.school_unit')")
            if cur.fetchone()[0] is None:
                print("    school_unit not found -- unit columns will be NULL")
                return
            # ★ ONLY THE COLUMNS THE TABLE HAS. school_unit is rebuilt by
            #   step 10d, which runs AFTER this step, so a column added to
            #   its DDL does not exist here until the run after -- `area`
            #   took the 2026-09-03 run down this way. A missing column
            #   reads NULL, exactly like a missing table.
            cur.execute("""SELECT column_name FROM information_schema.columns
                           WHERE table_schema = 'public'
                             AND table_name = 'school_unit'""")
            have = {r[0] for r in cur.fetchall()}
            missing = [c for c in _UNIT_COLS if c not in have]
            if missing:
                print(f"    school_unit lacks {missing} (older than the "
                      f"code; step 10d rebuilds it) -- those columns NULL")
            cols = ", ".join(f'"{c}"' if c in have else f"NULL::text AS \"{c}\""
                             for c in _UNIT_COLS)
            # votes DESC so the bare-school fallback keeps the best-attested
            # row, and the (school, state) key keeps the exact one.
            cur.execute(f"SELECT school, state, {cols} FROM school_unit "
                        f"ORDER BY votes ASC")
            for row in cur:
                school, state, vals = row[0], row[1], tuple(row[2:])
                _UNITS["by_school"][school] = vals
                if state:
                    _UNITS["by_key"][(school, state)] = vals
            # ★ A COLLEGE IS LOOKED UP BY ITS CAMPUS STATE (owner, 2026-09-07:
            #   "aren't they already separated by state?"). They are, in
            #   school_unit; a row's state is where the RACE was, so
            #   (Tufts, CT) missed and fell back to the name, which is wrong
            #   for every shared name (Loyola, St. Thomas, Trinity). The
            #   directory says where the campus is.
            from build_college_directory import loadDirectory
            _UNITS["campus"] = loadDirectory(cur, "state")
    except Exception as exc:                        # noqa: BLE001
        print(f"    school_unit unreadable ({exc}) -- unit columns NULL")
        # ! THE TRANSACTION IS ABORTED BY THE FAILED STATEMENT, and every
        #   later statement on this connection fails with
        #   InFailedSqlTransaction until it is rolled back. That, not the
        #   missing column, is what killed step 10.
        try:
            conn.rollback()
        except Exception:                           # noqa: BLE001
            pass


def _unitsOf(school, state):
    """The nine unit values for a row, or nine Nones."""
    if not school:
        return (None,) * len(_UNIT_COLS)
    campus = _campusState(school)
    got = _UNITS["by_key"].get((school, campus)) if campus else None
    if got is None:
        got = _UNITS["by_key"].get((school, state))
    if got is None:
        got = _UNITS["by_school"].get(school)
    return got if got is not None else (None,) * len(_UNIT_COLS)


def _tfDistance(event_short):
    """Metres for a track event name, or None. See event_parse."""
    if event_short in _TF_DISTANCE:
        return _TF_DISTANCE[event_short]
    metres = None
    if event_short:
        got = distanceFromEventShort(event_short)
        # ! (distance, gender) -- only the first is wanted here.
        metres = got[0] if isinstance(got, (tuple, list)) else got
        # ★ THE SPRINT PARSER SECOND (2026-09-06). The rated parser floors
        #   at 600 m, right for a rating and fatal for the boards: '60m',
        #   '100m', '200m', '400m' all came back None here, so
        #   prepareSprintEvents never whitelisted them and prepareRow
        #   dropped every one -- the sprint PR boards were empty from the
        #   day they were written. A sprint distance only ever lands on a
        #   row with no rating (the anchor gate needs a normalised time and
        #   skips those), so nothing rated changes.
        if metres is None:
            metres = sprintDistanceFromEventShort(event_short)[0]
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
# ★ ISSUE #37: A HIGH SCHOOL CROSS COUNTRY RACE OVER 5K IS RATED, NEVER
#   RANKED (owner). 5000m is the high school championship distance; a 6k or
#   an 8k is an open or collegiate race that a high schooler has entered, and
#   ranking it puts a time set against college fields on a high school board.
#
# ! RATE, NOT REFUSE -- the owner's answer to the open question. The athlete
#   ran it and their own page should say so; what is withheld is a place on a
#   national board, exactly like grade_trust='low', the anchor gate and the
#   pool ceiling. Nothing here touches results.speed_rating.
#
# ⚠ THE TOLERANCE IS FOR MEASUREMENT NOISE, NOT FOR ANOTHER DISTANCE. A
#   nominal 5k arrives as 5000, 5030 or 4998 depending on the feed and the
#   course survey, so an exact `> 5000` would drop real 5k races. The next
#   distance actually raced above it is 6000m -- twenty percent up -- so 5%
#   absorbs every plausible survey error and still cannot reach a 6k.
#
# ! AND A ROW WITH NO DISTANCE IS NOT DROPPED. Half the XC corpus reached
#   ranking_results with distance NULL at one point (see _tfrrsBlobDistances);
#   dropping on absence would refuse those boards entirely, and "we cannot
#   tell" is not "it was too long". Same choice the anchor gate makes, and it
#   is counted as unchecked there for the same reason.
HS_XC_MAX_DISTANCE = 5000.0
HS_XC_DISTANCE_TOL = 1.05          # 5250m: past every 5k, short of every 6k
_HS_POOLS = ("hs_m", "hs_f")

_GATE = {"XC": {"checked": 0, "mismatched": 0, "unchecked": 0,
                "outside_pool": 0, "outside_band": 0, "time_only": 0,
                "corrected": 0, "wheelchair": 0,
                "over_hs_distance": 0, "hs_no_distance": 0,
                "field_mark": 0, "field_refused": 0},
         "TF": {"checked": 0, "mismatched": 0, "unchecked": 0,
                "outside_pool": 0, "outside_band": 0, "time_only": 0,
                "corrected": 0, "wheelchair": 0,
                "over_hs_distance": 0, "hs_no_distance": 0,
                "field_mark": 0, "field_refused": 0}}

# ★ TIMED NON-FLAT EVENTS. event_parse rejects hurdles and the steeple on
#   purpose (the distance law is not fitted for them), so _tfDistance is
#   None for both and the sprint whitelist could not see them. This reads
#   the metres off the name for exactly those two kinds and nothing else:
#   "110H", "110m Hurdles", "300 Hurdles", "400mh"; "3000m Steeplechase",
#   "3k steeple", "2000 SC". Relays, walks and medleys stay rejected.
_HURDLE_RX = re.compile(
    r"(?<![\d.])(\d{2,3})\s*(?:m|meter|metre)?s?\s*(?:h\b|hh\b|hurdles?\b)",
    re.IGNORECASE)
_STEEPLE_RX = re.compile(r"steeple|\bsc\b", re.IGNORECASE)
_STEEPLE_NUM_RX = re.compile(r"(\d+(?:\.\d+)?)\s*(k\b|km\b|m\b|meter|metre)?",
                             re.IGNORECASE)
_RELAYISH_RX = re.compile(r"relay|medley|\d\s*[x×]\s*\d|shuttle|walk",
                          re.IGNORECASE)
_TIMED_KIND = {}


def timedEventKind(event_short):
    """('hurdles', metres) | ('steeple', metres) | (None, None), memoised."""
    if event_short in _TIMED_KIND:
        return _TIMED_KIND[event_short]
    out = (None, None)
    s = (event_short or "").strip()
    if s and not _RELAYISH_RX.search(s):
        m = _HURDLE_RX.search(s)
        if m:
            d = float(m.group(1))
            if 50.0 <= d <= 400.0:
                out = ("hurdles", d)
        elif _STEEPLE_RX.search(s):
            d = None
            for num, unit in _STEEPLE_NUM_RX.findall(s):
                v = float(num)
                if unit and unit.lower().startswith("k"):
                    v *= 1000.0
                if v < 10:                    # "3k" with no unit, "2.0"
                    v *= 1000.0
                if 1500.0 <= v <= 3200.0:
                    d = v
                    break
            out = ("steeple", d if d is not None else 3000.0)
    _TIMED_KIND[event_short] = out
    return out


# Same trio as the engine loader and the backfill nuke -- one pattern,
# three spellings, all named "wheelchair" so a grep finds the family.
_WHEELCHAIR_RX = re.compile(r"wheelchair|seated|ambulator", re.IGNORECASE)


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

    # ★ THE ENGINE'S SANITY BAND, ENFORCED HERE NOW THAT EVERY ROW CARRIES A
    #   RATING. packResults keeps an out-of-band row out of the SOLVE, and
    #   fill_ratings then prices it anyway so the race page shows a number
    #   instead of a dash -- which means "speed_rating IS NOT NULL" stopped
    #   implying "the engine stood behind this". The band is that missing
    #   distinction: a normalized_time outside 2:00-12:00/km of the pool's
    #   own anchor is a row the solve refused, and it gets a rating but never
    #   a place on a board. Same constants as the engine, imported, not
    #   copied (normalize_distance.PACE_FLOOR / PACE_CEIL).
    nt = row.normalized_time
    if nt is not None:
        lo, hi = poolBandFor(pool, sport)
        if not lo <= float(nt) <= hi:
            _GATE[sport]["outside_band"] += 1
            return None

    # ★ A CORRECTED DIVISION IS DISPLAYED, NEVER RANKED (owner's rule,
    #   2026-08-27). Its recorded distance was wrong once already; a board
    #   place built on a corrected number is a claim the correction
    #   machinery should not get to mint. The race page keeps the rating
    #   and says "corrected"; the boards decline the row. XC carries the
    #   verdict from the query (dist_corrected); TF compares its override
    #   against the event name's own distance here.
    if sport == "XC":
        if getattr(row, "dist_corrected", False):
            _GATE[sport]["corrected"] += 1
            return None
    else:
        _ev = getattr(row, "event_short", None)
        # Wheelchair belt for the boards: the backfill nuke removes these
        # rows at the NEXT full backfill; this keeps chairs off boards
        # rebuilt from the current disk in the meantime.
        if _ev and _WHEELCHAIR_RX.search(_ev):
            _GATE[sport]["wheelchair"] += 1
            return None
        if row.distance is not None:
            _ev_d = _tfDistance(_ev)
            if _ev_d is not None and abs(float(row.distance)
                                         - float(_ev_d)) >= 1:
                _GATE[sport]["corrected"] += 1
                return None

    # ★ #37: HS CROSS COUNTRY OVER 5K IS RATED BUT NOT RANKED. See
    #   HS_XC_MAX_DISTANCE for why the tolerance exists and why a NULL
    #   distance is counted rather than dropped.
    if sport == "XC" and pool in _HS_POOLS:
        if row.distance is None:
            _GATE[sport]["hs_no_distance"] += 1
        elif float(row.distance) > HS_XC_MAX_DISTANCE * HS_XC_DISTANCE_TOL:
            _GATE[sport]["over_hs_distance"] += 1
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

    # ★ #46: A TIME-ONLY ROW. The engine rates nothing under 800m, so a sprint
    #   arrives unrated and every rail below is about a rating it does not
    #   have. It is published for the TIME-ranked boards and excluded from
    #   every rating board by their own NULL filter.
    #
    # ⚠ NARROW ON PURPOSE. The SQL whitelist (tmp_sprint_events) is what lets
    #   an unrated row through at all; this re-derives the distance and checks
    #   it independently, so a widened join can never quietly admit the rest
    #   of the unrated corpus -- rows with no pool, bad data, a failed solve.
    #   Two locks, because the failure here is silent and site-wide.
    if row.speed_rating is None:
        if sport != "TF":
            return None
        # ★ A FIELD EVENT: no time, a MARK (owner, 2026-09-02). The mark is
        #   parsed here with the same refusals the athlete page's bests
        #   use -- unreadable, sentinel, or outside the event's physical
        #   range, and the row is not published. Counted either way.
        if getattr(row, "is_field", 0):
            key = normalizeFieldEvent(getattr(row, "event_short", None))
            metres, _kind = parseMark(getattr(row, "mark", None))
            if key is None or metres is None or not saneMark(key, metres):
                _GATE[sport]["field_refused"] += 1
                return None
            _GATE[sport]["field_mark"] += 1
            return (sport, row.result_id, row.person_id, pool, None,
                    row.date, season, row.state, school, row.grade,
                    row.meet_id, row.div_id, row.canon_meet_id,
                    None, None, row.event_id,
                    *_unitsOf(school, row.state),
                    key, float(metres))
        # ★ A TIMED NON-FLAT EVENT: hurdles or the steeple, metres from the
        #   name, kind stamped so the 100 m board never lists the 100 m
        #   hurdles.
        kind, kind_d = timedEventKind(getattr(row, "event_short", None))
        if kind is not None:
            _GATE[sport]["time_only"] += 1
            return (sport, row.result_id, row.person_id, pool, None,
                    row.date, season, row.state, school, row.grade,
                    row.meet_id, row.div_id, row.canon_meet_id,
                    row.time_seconds, kind_d, row.event_id,
                    *_unitsOf(school, row.state),
                    kind, None)
        if distance is None \
                or not (0 < float(distance) < _SPRINT_MAX_DISTANCE):
            return None
        _GATE[sport]["time_only"] += 1
        return (sport, row.result_id, row.person_id, pool, None,
                row.date, season, row.state, school, row.grade,
                row.meet_id, row.div_id, row.canon_meet_id,
                row.time_seconds, distance, row.event_id,
                *_unitsOf(school, row.state),
                None, None)


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
            row.event_id,
            # ! SPREAD in _UNIT_COLS order, matching _COLUMNS. COPY is
            #   positional, so these two orderings are one fact written
            #   twice -- _UNIT_COLS is the copy that _COLUMNS quotes, so a
            #   unit added there flows to both.
            *_unitsOf(school, row.state),
            # a rated row is a flat race with no mark
            None, None)



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


def buildSport(conn, sport, since, stats, until="2100-01-01"):
    """Stream one sport's rated results and COPY the rankable ones.

    A NAMED cursor is server-side: Postgres holds the result set and ships it
    in batches instead of materialising 34M rows in client memory. Named
    cursors must live inside a transaction, which `with getConn()` provides.

    Read and write cursors are separate -- a named cursor is mid-fetch and
    cannot issue other statements.
    """
    # ! BEFORE THE NAMED CURSOR OPENS. A named cursor is mid-fetch and cannot
    #   issue other statements on the same connection, so the unit table has
    #   to be read first. It is cached, so the second sport is free.
    _loadUnits(conn)

    print(f"\n  {sport}: streaming...")
    buffer = []
    seen = 0

    with conn.cursor(name=f"rank_src_{sport.lower()}",
                     cursor_factory=psycopg2.extras.NamedTupleCursor) as src:
        src.itersize = _FETCH_BATCH
        src.execute(_SQL[sport], {"since": since, "until": until})

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
    if g["outside_band"]:
        print(f"    sanity band: {g['outside_band']:,} races rated but "
              f"outside the engine's pace band -- filled ratings the solve "
              f"refused to stand behind (visible on the race page, never "
              f"on a board)")
    if g["corrected"]:
        print(f"    corrected distance: {g['corrected']:,} races in "
              f"overridden divisions -- displayed, never ranked "
              f"(owner's rule)")
    if g["over_hs_distance"] or g["hs_no_distance"]:
        print(f"    hs distance (#37): {g['over_hs_distance']:,} HS races "
              f"over {HS_XC_MAX_DISTANCE * HS_XC_DISTANCE_TOL:.0f}m rated but "
              f"not ranked; {g['hs_no_distance']:,} HS races carry no "
              f"distance and were left on the boards")
    if g["time_only"]:
        print(f"    time-only (#46): {g['time_only']:,} sprint races "
              f"published with no rating -- they rank on the clock, and "
              f"every rating board excludes them")
    if g["field_mark"] or g["field_refused"]:
        print(f"    field marks: {g['field_mark']:,} published by parsed mark, "
              f"{g['field_refused']:,} refused (unreadable, sentinel or "
              f"outside the event's range -- marks.py)")
    if g["wheelchair"]:
        print(f"    wheelchair: {g['wheelchair']:,} races dropped by event "
              f"title (belt; the backfill nuke removes them at the next "
              f"full run)")
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


def _columnMissing(cur, table, col):
    cur.execute("""SELECT is_nullable FROM information_schema.columns
                   WHERE table_name = %s AND column_name = %s""", (table, col))
    return cur.fetchone() is None


def _columnNotNull(cur, table, col):
    cur.execute("""SELECT is_nullable FROM information_schema.columns
                   WHERE table_name = %s AND column_name = %s""", (table, col))
    row = cur.fetchone()
    return row is not None and row[0] == "NO"


def _migrateLive(conn, like):
    """The idempotent schema migrations on the LIVE table, before the
    shadow is created from it (adding the column to the shadow alone would
    be undone by the next swap).

    ! ONLY THE ALTERs THAT CHANGE SOMETHING, UNDER A LOCK TIMEOUT, COMMITTED
      AT ONCE (issue 300, 2026-09-07). ADD COLUMN IF NOT EXISTS takes
      ACCESS EXCLUSIVE even when the column exists; that lock queues behind
      any open read (a site page) and every later read queues behind it,
      which is how the fill step took the site down for hours. So each
      migration is looked up first in information_schema and run only when
      needed, the batch waits at most five seconds for the lock and tries
      again a few times, and it commits before the shadow build so the
      lock is held for milliseconds, not the length of step 10.

    ⚠ THESE MIGRATIONS BELONG TO ranking_results ONLY, except the unit
      columns, which athlete_season carries too (2026-09-06): the ability
      board, the rank line and the counts filter that table, and a unit
      filter on a table without the columns is a 400."""
    wanted = []                       # (probe, ddl)
    if like == "athlete_season":
        for _u in _UNIT_COLS:
            wanted.append((lambda c, u=_u: _columnMissing(c, like, u),
                           f'ALTER TABLE IF EXISTS {like} ADD COLUMN IF NOT EXISTS "{_u}" text'))
    if like == "ranking_results":
        for col, typ in (("distance", "real"), ("event_id", "bigint"),
                         ("event_kind", "text"), ("mark", "real")):
            wanted.append((lambda c, col=col: _columnMissing(c, like, col),
                           f"ALTER TABLE IF EXISTS {like} ADD COLUMN IF NOT EXISTS {col} {typ}"))
        # #46: a time-only row has no rating; a field row has no time
        for col in ("speed_rating", "time_seconds"):
            wanted.append((lambda c, col=col: _columnNotNull(c, like, col),
                           f"ALTER TABLE IF EXISTS {like} ALTER COLUMN {col} DROP NOT NULL"))
        for _u in ("division", "region", "conference", "league", "state_div",
                   "section_div", "district", "county", "class", "area", "section"):
            wanted.append((lambda c, u=_u: _columnMissing(c, like, u),
                           f'ALTER TABLE IF EXISTS {like} ADD COLUMN IF NOT EXISTS "{_u}" text'))
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (like,))
        if cur.fetchone()[0] is None:
            conn.rollback()
            return                     # first run: no live table to migrate
        todo = [ddl for probe, ddl in wanted if probe(cur)]
    conn.rollback()
    if not todo:
        return
    for attempt in range(6):
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '5s'")
                for ddl in todo:
                    cur.execute(ddl)
            conn.commit()
            print(f"[rankings] {like}: {len(todo)} schema migration(s) applied", flush=True)
            return
        except psycopg2.errors.LockNotAvailable:
            conn.rollback()
            print(f"[rankings] {like}: migration waited on a lock (try {attempt + 1}/6)", flush=True)
            time.sleep(10)
    raise RuntimeError(f"{like}: could not migrate the live table (lock held by another session)")



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
    _migrateLive(conn, like)
    with conn.cursor() as cur:
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
        # pool_view.stampRowsHs, on EVERY race/compiled/course/compare
        #   page: WHERE sport = %s AND result_id = ANY(%s). Without it
        #   each of those pages seq-scans 56M rows (measured 11.6s a hit,
        #   56s across one page sweep).
        ("rr_result_idx", "(result_id)"),
        # stampRecordFlags (the PR/SR badges on every race page) reads a
        #   field's worth of athletes' careers: person probes were 1.4s
        #   COLD because every row was a random heap fetch. INCLUDE makes
        #   it an index-only scan -- the badge query never touches the
        #   heap at all.
        ("rr_person_cover_idx",
         "(person_id) INCLUDE (sport, race_date, year, distance, "
         "time_seconds)"),
        # rankings performance boards: pool/sport/year equality, then
        #   ORDER BY speed_rating DESC LIMIT cand
        ("rr_board_rating_idx", "(pool, sport, year, speed_rating DESC)"),
        # ★ THE DEFAULT BOARDS WALK THE RATING IN INDEX ORDER (owner,
        #   2026-09-02: "make the rankings faster"). A board narrowed only
        #   by pool, or by pool and sport, has no year to pin the index
        #   above, so its candidate stage sorted every row of the pool
        #   before taking the first fifty. These two let it stop after the
        #   fifty; the state and floor filters drop few rows along the way.
        ("rr_pool_rating_idx", "(pool, speed_rating DESC)"),
        ("rr_pool_sport_rating_idx", "(pool, sport, speed_rating DESC)"),
        # rankings PR boards: same filters, ORDER BY time_seconds ASC
        ("rr_board_time_idx", "(pool, sport, year, time_seconds)"),
        # rankings marks boards: WHERE event_kind = 'shot_put' ...
        #   ORDER BY mark DESC. Partial: flat rows carry NULL and are the
        #   whole table; the kinds are a sliver.
        ("rr_board_mark_idx",
         "(event_kind, mark DESC) WHERE event_kind IS NOT NULL"),

        # school.py: WHERE school = %s AND sport = %s ORDER BY speed_rating DESC
        ("rr_school_idx", "(school, sport, speed_rating DESC)"),
        # ★ THE PAGE INDEXES, HERE AND NOT ONLY IN 11b (2026-09-06). These
        #   three were add_page_indexes' (step 11b), built AFTER the swap;
        #   run12's finish was re-run by hand after a crash, 11b never
        #   followed, and every race, course, compare and school page
        #   seq-scanned 61M rows for a fortnight-of-an-afternoon: 15.5 s
        #   per race page, all of it in stampRowsHs' one lookup by
        #   result_id. The swap must never go live without them. Same
        #   names as 11b's, so its CREATE IF NOT EXISTS no-ops.
        ("idx_rr_result", "(result_id)"),
        ("idx_rr_school", "(school)"),
        ("idx_rr_person_cover",
         "(person_id) INCLUDE (sport, race_date, year, distance, time_seconds)"),
    ],
    "athlete_season": [
        # athlete pages and "where am I": WHERE person_id = %s
        ("as_person_idx", "(person_id)"),
        # rankings ability boards: WHERE n_races >= .. AND pool/sport/year,
        #   ORDER BY mean_rating DESC
        ("as_board_mean_idx", "(pool, sport, year, mean_rating DESC)"),
        # the same two for the athletes board (its ORDER BY is mean_rating
        # DESC, person_id -- the tiebreak rides in the index)
        ("as_pool_mean_idx", "(pool, mean_rating DESC, person_id)"),
        ("as_pool_sport_mean_idx", "(pool, sport, mean_rating DESC, person_id)"),
        # the same boards sorted on the season best instead
        ("as_board_best_idx", "(pool, sport, year, best_rating DESC)"),
        # school.py roster/years/currentSeason: WHERE school = %s
        #   (+ sport/year equality) -- every school page view
        ("as_school_idx", "(school, sport, year)"),
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


def analyze(conn, name):
    """Fresh planner statistics, timed.

    ★ NOT OPTIONAL, AND IT USED TO BE. A table this script CREATE'd has no
      statistics at all: Postgres falls back to a 10-page, ~2,550-row guess
      and plans against that. The next reader of ranking_results_new is
      refreshAthleteSeason's aggregate over 56.6M rows, and the one after that
      is the whole site.

      It used to be the third statement inside _ATHLETE_SEASON_SQL -- i.e.
      AFTER the aggregate that needed it.
    """
    with conn.cursor() as cur:
        t0 = time.time()
        cur.execute(f"ANALYZE {name}")
        print(f"    [{time.time() - t0:7.1f}s] ANALYZE {name}")
    conn.commit()


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
        # ! STILL ANALYZE. A table this script created has NO statistics at
        #   all, and the next reader is refreshAthleteSeason's aggregate.
        analyze(conn, name)
        return
    print(f"  building {len(defs)} indexes on {name} (deferred until after "
          f"the load, {_INDEX_JOBS} at a time)")

    def rename(idxname, ddl):
        """The shadow's own name for this index, derived not accumulated."""
        newname = (idxname if idxname.startswith(name)
                   else idxname.replace(like, name, 1)
                   if like in idxname else f"{name}_{idxname}")
        sql = ddl.replace(f" ON public.{like} ", f" ON public.{name} ")
        sql = sql.replace(f" ON {like} ", f" ON {name} ")
        sql = sql.replace(f"INDEX {idxname} ",
                          f"INDEX IF NOT EXISTS {newname} ")
        sql = sql.replace(f"INDEX IF NOT EXISTS {idxname} ",
                          f"INDEX IF NOT EXISTS {newname} ")
        return newname, sql

    # ★ ONE CONNECTION EACH, IN PARALLEL. Four indexes over 56.6M rows built
    #   one after another is four full sorts in series, and the server sits
    #   mostly idle through all of them: a single CREATE INDEX saturates
    #   max_parallel_maintenance_workers (4) and nothing else.
    #
    #   They are independent -- different columns, same table, no shared
    #   state -- so there is no ordering to preserve. psycopg2 releases the
    #   GIL inside execute(), so threads are the right shape here; the work is
    #   entirely on the server.
    #
    # ⚠ SAFE ONLY BECAUSE THIS IS A SHADOW. Nothing reads `name` until swapIn
    #   renames it, so concurrent ACCESS EXCLUSIVE-taking DDL on it blocks
    #   nobody. Do NOT reuse this shape against a live table -- use CREATE
    #   INDEX CONCURRENTLY there, which is what ensure_ranking_indexes.py does.
    #
    # ! AND THE POOL IS ThreadedConnectionPool, checked -- see
    #   scripts/database.py. A SimpleConnectionPool would corrupt here.
    def build(job):
        idxname, ddl = job
        newname, sql = rename(idxname, ddl)
        t0 = time.time()
        with getConn() as c:
            with c.cursor() as cur:
                # Per-session, so each builder gets its own sort memory.
                cur.execute("SET maintenance_work_mem = '2GB'")
                cur.execute("SET max_parallel_maintenance_workers = 4")
                cur.execute(sql)
            c.commit()
        return newname, time.time() - t0

    jobs = min(_INDEX_JOBS, len(defs))
    if jobs > 1:
        with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
            for newname, dt in pool.map(build, defs):
                print(f"    [{dt:7.1f}s] {newname[:60]}")
    else:
        for job in defs:
            newname, dt = build(job)
            print(f"    [{dt:7.1f}s] {newname[:60]}")

    analyze(conn, name)


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


# ★ THE DEFAULT BOARDS' LENGTHS, WRITTEN ONCE PER BUILD. The rankings pager's
#   Last button needs the board's length, and counting a board narrowed
#   only by pool and sport is a twenty-million-row scan on the live corpus
#   (owner, 2026-09-02: "pressing last doesn't work"). Counted here, after
#   the swap, through rankings.countRows -- the same code the page uses --
#   so the stored number is the number the page would have computed.
_BOARD_SIZE_SPORTS = ("XC", "TF", "both")
_BOARD_SIZE_BOARDS = ("ability", "performance")


def buildBoardSizes(conn):
    from werkzeug.datastructures import MultiDict
    import rankings as RK
    t0 = time.time()
    rows = []
    with conn.cursor() as cur:
        for board in _BOARD_SIZE_BOARDS:
            for pool in sorted(RK.POOLS):
                for sport in _BOARD_SIZE_SPORTS:
                    f, err = RK.parseFilters(MultiDict(
                        {"board": board, "pool": pool, "sport": sport}))
                    if err:
                        continue
                    n = RK.countRows(cur, f, timeout_ms=0)
                    rows.append((board, pool, sport, int(n or 0)))
        cur.execute("""
            CREATE TABLE IF NOT EXISTS board_size (
                board text NOT NULL, pool text NOT NULL, sport text NOT NULL,
                n bigint NOT NULL, built_at timestamptz DEFAULT now(),
                PRIMARY KEY (board, pool, sport))
        """)
        cur.execute("DELETE FROM board_size")
        psycopg2.extras.execute_values(
            cur, "INSERT INTO board_size (board, pool, sport, n) VALUES %s", rows)
    conn.commit()
    print(f"    [{time.time() - t0:7.1f}s] board_size: {len(rows)} default boards counted")


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
        # ⚠ BOTH TABLES, AND THE SECOND ONE WAS MISSING. Only ranking_results
        #   was set back to logged; athlete_season went live UNLOGGED, and an
        #   unlogged table is TRUNCATED by crash recovery. The first unclean
        #   Postgres shutdown after a build silently emptied it while the
        #   logged ranking_results kept its rows -- performance boards full,
        #   athlete boards gone, rank lines gone, team boards built from
        #   nothing, and not one error anywhere. Found from exactly that
        #   state: panels printing "athlete-seasons scanned: 0" beside 37M
        #   scanned performances.
        t0 = time.time()
        cur.execute(f"ALTER TABLE {_LOAD_TABLE} SET LOGGED")
        cur.execute(f"ALTER TABLE {_LOAD_SEASON} SET LOGGED")
        conn.commit()
        print(f"    [{time.time() - t0:7.1f}s] set logged (both tables)")

        # ★ IMPATIENT, SO THE SITE CAN STAY UP THROUGH A PIPELINE RUN.
        #   These renames need ACCESS EXCLUSIVE, which conflicts with the
        #   ACCESS SHARE every gunicorn worker holds while it reads. Postgres
        #   queues lock requests IN ORDER, so a rename waiting behind one slow
        #   page makes every NEW request queue behind the rename -- one slow
        #   query freezes the whole site. lock_timeout gives up instead of
        #   queueing, the backlog drains, and the next attempt takes the gap.
        #   Same pattern and constants as backfill_normalize._swapWithRetry.
        #
        # ⚠ ALL FOUR IN ONE TRANSACTION. Half a swap leaves ranking_results
        #   renamed away with nothing in its place, and every page 500s. A
        #   timeout rolls the whole thing back, which is what makes the retry
        #   safe to simply start over.
        from maintenance import siteMaintenance
        # the site answers 503 while the rename waits for or holds the lock (300)
        with siteMaintenance("swap ranking_results"):
            for attempt in range(1, _SWAP_ATTEMPTS + 1):
                try:
                    cur.execute("BEGIN")
                    cur.execute(f"SET LOCAL lock_timeout = '{_SWAP_LOCK_TIMEOUT}'")
                    # ⚠ BOTH TABLES UP FRONT, IN ONE STATEMENT, AND THIS IS WHAT
                    #   THE DEADLOCK WAS. The renames took ACCESS EXCLUSIVE on
                    #   ranking_results first and asked for athlete_season second;
                    #   a page reading athlete_season and then ranking_results
                    #   holds those two in the OPPOSITE order. Classic ABBA, and
                    #   lock_timeout does not save you from it -- Postgres's
                    #   deadlock detector fires at deadlock_timeout (1s by
                    #   default), well before a 3s lock_timeout, so the build died
                    #   with DeadlockDetected after six hours of work.
                    #
                    # ! TAKING THEM TOGETHER MAKES THE TIMEOUT THE FAILURE MODE
                    #   AGAIN, which is the one the retry below was written for.
                    cur.execute("LOCK TABLE ranking_results, athlete_season "
                                "IN ACCESS EXCLUSIVE MODE")
                    cur.execute("ALTER TABLE ranking_results RENAME TO ranking_results_old")
                    cur.execute("ALTER TABLE athlete_season  RENAME TO athlete_season_old")
                    cur.execute(f"ALTER TABLE {_LOAD_TABLE}  RENAME TO ranking_results")
                    cur.execute(f"ALTER TABLE {_LOAD_SEASON} RENAME TO athlete_season")
                    conn.commit()
                    break
                # ⚠ DEADLOCK IS THE SIBLING OF TIMEOUT, NOT A DIFFERENT
                #   PROBLEM, and catching only one of them is why a six-hour
                #   build threw its work away. Both mean "a reader was in the
                #   way", both roll the whole transaction back, and both are
                #   fixed by waiting and trying again. tests/test_swap_retry.py
                #   holds every swap site to catching the pair.
                except (psycopg2.errors.LockNotAvailable,
                        psycopg2.errors.DeadlockDetected):
                    conn.rollback()
                    if attempt == _SWAP_ATTEMPTS:
                        # ! THE SHADOW SURVIVES, so a rerun resumes at the swap
                        #   rather than reloading 61.6M rows. Raising beats
                        #   swapping half of it.
                        raise RuntimeError(
                            "ranking_results swap: could not take ACCESS "
                            f"EXCLUSIVE in {_SWAP_ATTEMPTS} attempts. Check "
                            "pg_stat_activity for a long read; the shadow "
                            "tables are loaded and waiting.")
                    print(f"    readers hold the tables, attempt {attempt}"
                          f"/{_SWAP_ATTEMPTS} -- retrying in {_SWAP_BACKOFF}s")
                    time.sleep(_SWAP_BACKOFF)
    print("  swapped. dropping the old copies...")

    # The old ranking_results is ~23GB. Timed because a drop that size is not
    # instant and it is the last thing between here and the next step.
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute("DROP TABLE ranking_results_old, athlete_season_old")
    conn.commit()
    print(f"    [{time.time() - t0:7.1f}s] dropped the old copies")
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
# ★ A RACE FIVE SIGMA UNDER THE SEASON IS NOT A RACE (owner, 2026-09-05).
#   An athlete's own race-to-race spread is about 4 points; twenty points
#   under the season's median is nothing a real run at the right distance
#   produces, and one such row dragged the decayed rating and every plain
#   average. Rows that far under their season's median leave the season's
#   aggregates (all of them, so n_races and the percentile agree with the
#   mean); never the fast side, a breakthrough is real and a wrong
#   distance is the rowguard's. The athlete page marks the same rows with
#   the same rule (app.enrich_seasons, pinned together by test).
_SEASON_OUTLIER_PTS = 20.0

_ATHLETE_SEASON_SQL = f"""
WITH season_med AS (
    SELECT person_id, pool, sport, year,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_rating) AS med
    FROM   {{load_table}}
    WHERE  speed_rating IS NOT NULL
    GROUP  BY person_id, pool, sport, year)
INSERT INTO {{season_table}}
    (person_id, pool, sport, year, mean_rating, decayed_rating, best_rating,
     n_races, first_race, last_race, state, school, grade)
SELECT base.person_id, base.pool, base.sport, base.year,
       (percentile_cont({_SEASON_Q}) WITHIN GROUP (ORDER BY speed_rating))::real,
       (sum(speed_rating * power({_DECAY_K}, {_ANCHOR} - race_date))
        / nullif(sum(power({_DECAY_K}, {_ANCHOR} - race_date)), 0))::real,
       max(speed_rating)::real,
       count(*),
       min(race_date),
       max(race_date),
       mode() WITHIN GROUP (ORDER BY state),
       -- ★ THE TEAM THEY RACED FOR, NOT "UNATTACHED" (owner, 2026-09-04):
       --   a season mostly unattached with a few races for a school is that
       --   school's; only a season with no school at all stays unattached.
       -- ★ A COLLEGE SEASON'S TEAM IS THE COLLEGE (owner, 2026-09-06:
       --   Liam Lucas headed "Loyola Blakefield" over a Tufts season). A
       --   college athlete's rows come from two feeds, and the one that
       --   still names the high school can outnumber the one that names
       --   the college. The school that carries a college division on
       --   its rows is the college; only when no row does is the plain
       --   majority used.
       COALESCE(mode() WITHIN GROUP (ORDER BY school)
                    FILTER (WHERE base.pool LIKE 'college%%'
                              AND division IS NOT NULL
                              AND school IS NOT NULL),
                mode() WITHIN GROUP (ORDER BY school)
                    FILTER (WHERE school IS NOT NULL
                              AND lower(school) NOT LIKE 'unattached%%'
                              AND lower(school) NOT IN
                                  ('unat', 'independent', 'individual',
                                   'no team', 'none', 'n/a', '')),
                mode() WITHIN GROUP (ORDER BY school)),
       -- ★ A COLLEGE SEASON'S GRADE IS ITS ELIGIBILITY (owner, 2026-09-07,
       --   Joey Sullivan: "wrong eligibility"). Two feeds grade a college
       --   athlete: tfrrs with the eligibility year ("JR-3"), athletic.net
       --   with a class word that runs a year off it, and the majority of
       --   rows was the wrong feed's. The eligibility spelling wins when
       --   any row carries it; otherwise the plain majority.
       COALESCE(mode() WITHIN GROUP (ORDER BY grade)
                    FILTER (WHERE base.pool LIKE 'college%%'
                              AND grade ~* '^(FR|SO|JR|SR)-?[1-6]$'),
                mode() WITHIN GROUP (ORDER BY grade))
FROM {{load_table}} base
JOIN season_med sm ON sm.person_id = base.person_id AND sm.pool = base.pool
                  AND sm.sport = base.sport AND sm.year = base.year
-- ⚠ RATED ROWS ONLY, SINCE #46. ranking_results now also carries time-only
--   sprint rows. Without this line count(*) would inflate n_races with races
--   that have no rating, and -- worse -- decayed_rating's denominator
--   sum(power(...)) counts every row while its numerator skips the NULLs, so
--   every sprinter's decayed rating would be silently diluted toward zero.
WHERE speed_rating IS NOT NULL
  AND speed_rating >= sm.med - {_SEASON_OUTLIER_PTS}
GROUP BY base.person_id, base.pool, base.sport, base.year;
"""


# ⚠ THE SORT THIS AGGREGATE CANNOT AVOID, AND THE MEMORY IT WAS NOT GIVEN.
#
#   percentile_cont and the three mode()s are ORDERED-SET aggregates. Postgres
#   implements those only as GroupAggregate, so the plan is forced to sort all
#   56.6M rows of the shadow by (person_id, pool, sport, year) first -- there
#   is no HashAggregate available and no parallel plan available, whatever
#   max_parallel_workers_per_gather says.
#
#   That sort carries speed_rating, race_date, state, school and grade along
#   with the key. `school` alone averages ~25 bytes, so the sort is several GB
#   -- against dbfast's session-wide work_mem of 256MB. Every run of this
#   aggregate has therefore been a multi-pass external merge sort spilling
#   gigabytes to disk, which is the shape of a step that takes an hour and
#   prints nothing while it does.
#
# ! SET LOCAL, so it lasts exactly one transaction and dbfast's 256MB is back
#   for everything after. And a LADDER, because a managed server may refuse
#   the top of it -- the same reason tuneSession swallows its failures.
#   4GB is the number the rest of this project already uses for a bulk sort
#   (speed_ratings_db._tuneForBulkBuild sets maintenance_work_mem there), and
#   at ~60 bytes a row a 56.6M-row sort is ~3.4GB -- so 4GB is the smallest
#   value that keeps it in memory rather than on disk. The rest of the ladder
#   is for a server that says no.
_SEASON_WORK_MEM = ("4GB", "2GB", "1GB", "512MB")


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
        # ★ ASK FOR ROOM BEFORE THE STATEMENT, NOT AFTER IT. See
        #   _SEASON_WORK_MEM: this is the sort that decides the step.
        for want in _SEASON_WORK_MEM:
            try:
                cur.execute(f"SET LOCAL work_mem = '{want}'")
                print(f"    work_mem {want} for the group-by sort")
                break
            except Exception as exc:                      # noqa: BLE001
                conn.rollback()
                print(f"    (server refused work_mem {want}: "
                      f"{str(exc).splitlines()[0]})")
        t0 = time.time()
        cur.execute(sql)
        print(f"    [{time.time() - t0:7.1f}s] GROUP BY -> {_LOAD_SEASON}")
        cur.execute(f"SELECT count(*) FROM {_LOAD_SEASON}")
        n = cur.fetchone()[0]
    conn.commit()

    _stampSeasonUnits(conn, _LOAD_SEASON)
    analyze(conn, _LOAD_SEASON)
    print(f"  athlete_season: {n:,} person-seasons")
    # ! AND ITS INDEXES. createShadow copies structure without them by design,
    #   and this call was missing -- so every run since swapped in a
    #   12.9M-row table with no index on it at all.
    buildIndexes(conn, _LOAD_SEASON, "athlete_season")


def _stampSeasonUnits(conn, season_table):
    """The unit columns onto every season row, the same lookup the result
    rows got (_unitsOf: school_unit by (school, state), then by school
    alone), so the ability board's unit filter and the athlete page's
    chips read one answer. An UPDATE after the group-by rather than
    eleven more ordered-set aggregates inside it: the group-by's sort is
    the step's cost and it carries nothing extra this way."""
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT to_regclass('public.school_unit')")
            if cur.fetchone()[0] is None:
                print("    school_unit not found -- season unit columns NULL")
                return
            cur.execute("""SELECT column_name FROM information_schema.columns
                           WHERE table_schema = 'public' AND table_name = 'school_unit'""")
            have = {r[0] for r in cur.fetchall()}
            cols = [c for c in _UNIT_COLS if c in have]
            if not cols:
                return
            sets = ", ".join(f'"{c}" = u."{c}"' for c in cols)
            collist = ", ".join(f'"{c}"' for c in cols)
            t0 = time.time()
            # ★ COLLEGES FIRST, BY CAMPUS STATE: a college season's state is
            #   where it raced. The directory's state for every season
            #   school it knows goes into a temp table and keys the lookup.
            n0 = 0
            try:
                from build_college_directory import lookup, loadDirectory
                campus = loadDirectory(cur, "state")
            except ImportError:
                lookup, campus = None, {}
            if campus:
                if lookup and campus:
                    cur.execute(f"SELECT DISTINCT school FROM {season_table} WHERE school IS NOT NULL")
                    pairs = []
                    for (sch,) in cur.fetchall():
                        st = lookup(campus, sch)
                        if st:
                            pairs.append((sch, st))
                    cur.execute("DROP TABLE IF EXISTS tmp_campus")
                    cur.execute("CREATE TEMP TABLE tmp_campus (school text PRIMARY KEY, state text)")
                    psycopg2.extras.execute_values(
                        cur, "INSERT INTO tmp_campus (school, state) VALUES %s", pairs)
                    cur.execute(f"""
                        UPDATE {season_table} s SET {sets}
                        FROM tmp_campus c
                        JOIN (SELECT DISTINCT ON (school, state) school, state, {collist}
                              FROM school_unit ORDER BY school, state, votes DESC) u
                          ON u.school = c.school AND u.state = c.state
                        WHERE s.school = c.school
                    """)
                    n0 = cur.rowcount
            # then the exact (school, state) for the rest
            cur.execute(f"""
                UPDATE {season_table} s SET {sets}
                FROM (SELECT DISTINCT ON (school, state) school, state, {collist}
                      FROM school_unit ORDER BY school, state, votes DESC) u
                WHERE u.school = s.school AND u.state = s.state
                  AND s."{cols[0]}" IS NULL AND s.school NOT IN (SELECT school FROM tmp_campus)
            """) if n0 else cur.execute(f"""
                UPDATE {season_table} s SET {sets}
                FROM (SELECT DISTINCT ON (school, state) school, state, {collist}
                      FROM school_unit ORDER BY school, state, votes DESC) u
                WHERE u.school = s.school AND u.state = s.state
            """)
            n1 = cur.rowcount
            # then the best-attested row of the name for the rest
            cur.execute(f"""
                UPDATE {season_table} s SET {sets}
                FROM (SELECT DISTINCT ON (school) school, {", ".join(f'"{c}"' for c in cols)}
                      FROM school_unit ORDER BY school, votes DESC) u
                WHERE u.school = s.school AND s."{cols[0]}" IS NULL
                  AND NOT EXISTS (SELECT 1 FROM school_unit x
                                  WHERE x.school = s.school AND x.state = s.state)
            """)
            n2 = cur.rowcount
            conn.commit()
            print(f"    [{time.time() - t0:7.1f}s] season units: {n0:,} colleges by campus, "
                  f"{n1:,} by (school, state), {n2:,} by school")
        except Exception as exc:                        # noqa: BLE001
            conn.rollback()
            print(f"    season units not stamped ({exc})")


# ------------------------------------------------------------------ #
#  4. ENTRY POINT
# ------------------------------------------------------------------ #

def main():
    global RACE_MARGIN
    parser = argparse.ArgumentParser(
        description="Fill ranking_results and athlete_season. "
                    "Run after every engine run.")
    parser.add_argument("--sport", choices=["XC", "TF", "both"], default="both")
    # ★ THREE STAGES SO THE TWO SPORTS STREAM AT ONCE (2026-09-05). The
    #   Python row walk is the whole cost of this step (30-80 min) and the
    #   sports share nothing until the indexes: `prepare` makes the shadow
    #   once, two `stream --sport` processes COPY into it side by side
    #   (each builds its own session temps), and `finish` indexes, builds
    #   athlete_season and swaps. No stage = the old one-process run.
    parser.add_argument("--stage", choices=["prepare", "stream", "finish", "restamp"],
                        default=None,
                        help="restamp: the unit columns onto the LIVE athlete_season "
                             "from school_unit, no rebuild (after a directory or "
                             "units change; the result rows follow at the next full run)")
    parser.add_argument("--since", default="1990-01-01",
                        help="earliest race date to include")
    # ★ A SPORT IN TWO HALVES (2026-09-06, the owner: "speed up the
    #   pipeline"). The row walk is Python per row, so two streams per
    #   sport on a date seam run in parallel into the same shadow: four
    #   processes where there were two. The seam is exclusive on --until.
    parser.add_argument("--until", default="2100-01-01",
                        help="stream rows dated BEFORE this (exclusive); pairs "
                             "with --since to split a sport across processes")
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

    stage = args.stage
    with getConn() as conn:
        if stage == "restamp":
            # ★ THE SEASON TABLE'S UNIT COLUMNS, REFRESHED IN PLACE (304):
            #   a directory rebuild or a units change used to wait for the
            #   next full step 10 to reach the ability board. The old values
            #   are cleared first, because the stamping's fallback passes fill
            #   only NULLs; then the same three passes run on the live table.
            #   UPDATEs take row locks only; the site keeps reading.
            with phase("restamp athlete_season units from school_unit"):
                with conn.cursor() as cur:
                    cur.execute("UPDATE athlete_season SET "
                                + ", ".join(f'"{c}" = NULL' for c in _UNIT_COLS))
                    print(f"    cleared {cur.rowcount:,} rows")
                conn.commit()
                _stampSeasonUnits(conn, "athlete_season")
                analyze(conn, "athlete_season")
            print("  restamped. The performance board's rows carry the old units "
                  "until the next full run.")
            return
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
        if stage in (None, "stream"):
            with phase("temp indexes (gender, tfrrs distance, TF state, sprints)"):
                prepareGenderTemp(conn)
                prepareXcTfrrsDistTemp(conn)
                ensureResultTwin(conn)
                ensureWheelchairPerson(conn)
                prepareTfStateTemp(conn)
                prepareSprintEvents(conn)

        if stage in (None, "prepare"):
            with phase("create shadow"):
                createShadow(conn, _LOAD_TABLE, "ranking_results")
                seedOtherSports(conn, sports if stage is None else ("XC", "TF"))
        if stage == "prepare":
            conn.commit()
            print("  shadow ready; stream the sports, then --stage finish")
            return

        if stage in (None, "stream"):
            for sport in sports:
                with phase(f"stream + COPY {sport}"):
                    buildSport(conn, sport, args.since, stats, until=args.until)
                    conn.commit()
        if stage == "stream":
            total = sum(v for k, v in stats.items() if k.endswith("_written"))
            print(f"\n  {total:,} rows COPYed for {', '.join(sports)}; "
                  f"--stage finish indexes and swaps")
            phaseReport()
            return

        # ! AFTER THE LOAD, NOT BEFORE IT. createShadow deliberately leaves the
        #   indexes off so the 61.6M COPYed rows do not maintain them one row
        #   at a time. Built here, each is a sequential scan plus a sort, and
        #   refreshAthleteSeason below reads this table, so they have to exist
        #   before it runs.
        with phase("index ranking_results"):
            buildIndexes(conn, _LOAD_TABLE, "ranking_results")

        with phase("athlete_season"):
            refreshAthleteSeason(conn)
        with phase("swap in + drop old"):
            swapIn(conn)
        # ! VACUUM AFTER THE SWAP, OUTSIDE A TRANSACTION. A freshly built table
        #   has an empty visibility map, so every index-only count and every
        #   board scan visits the heap until the first vacuum; autovacuum may
        #   take hours to get to a table this size. Minutes here buy the fast
        #   counts Last relies on for every filtered board.
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                t0 = time.time()
                cur.execute("VACUUM (ANALYZE) ranking_results")
                cur.execute("VACUUM (ANALYZE) athlete_season")
                print(f"    [{time.time() - t0:7.1f}s] VACUUM ANALYZE both tables")
        finally:
            conn.autocommit = False
        buildBoardSizes(conn)

    elapsed = (datetime.datetime.now() - started).total_seconds()
    total = sum(v for k, v in stats.items() if k.endswith("_written"))
    print(f"\n  {total:,} rows in ranking_results")
    phaseReport()
    print(f"  {elapsed / 60:.1f} minutes\n")


if __name__ == "__main__":
    main()