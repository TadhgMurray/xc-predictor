# Project: xc-predictor
# Author:  Tadhg Murray
# Subset:  Speed Rating Engine
# File:    speed_ratings_db.py
# Purpose: DB reads/writes for the speed rating engine. BOTH sports, BOTH sources.
#
# WHAT THE OLD VERSION SILENTLY DELETED
#   1. INNER JOIN athletes ON r.athlete_id -- tfrrs XC has athlete_id NULL on
#      100% of rows, so every tfrrs result vanished before the engine saw it.
#   2. INNER JOIN meets -- anet-only table; deleted tfrrs again.
#   3. athlete_id as identity -- person_id is the CROSS-SOURCE id (anet 100%,
#      tfrrs ~70%). Keyed on athlete_id, an athlete's anet and tfrrs races were
#      two different people, neither with enough races to rate.
#   4. grade NOT IN ('1'..'8') -- excluded middle school outright.
#   5. TF was never loaded at all.
#
# WHAT THIS REVISION FIXED (three bugs, all provable from SQL semantics)
#   A. `LEFT JOIN athletes a ON a.athlete_id = r.athlete_id` FANNED OUT.
#      athletes' primary key is (athlete_id, school) -- an athlete who changed
#      schools has several rows. Every one of their results was duplicated, once
#      per school, and the engine counted each copy as a separate race. Fixed by
#      joining on the full key. (This is the same composite the FK enforces.)
#   B. `LEFT JOIN meets_tf ... WHERE m.distance_meters >= 800` was an INNER JOIN
#      wearing a LEFT JOIN's clothes. A TF row with no meets_tf match has
#      m.distance_meters NULL; `NULL >= 800` is NULL, not TRUE, so the row was
#      filtered out. Exactly the failure this file's header says it fixed.
#   C. That predicate was also REDUNDANT. backfill_normalize._distanceSane gates
#      [800, 12000] before it writes anything, so `normalized_time IS NOT NULL`
#      ALREADY implies distance >= 800m. Deleting it removes a bug and a filter.
#
# WHAT THIS REVISION MADE FASTER
#   * COPY, not execute_values, into staging. COPY skips the SQL parser entirely;
#     execute_values builds and parses a giant multi-row VALUES literal.
#   * saveResultSpeedRatings no longer does `pairs = list(pairs)` -- 30M Python
#     tuples is ~2.5GB of RAM for nothing. It streams the generator into COPY in
#     fixed-size chunks, so peak memory is one chunk.
#   * The final write is a HEAP REBUILD, not an UPDATE. Measured on this DB: the
#     UPDATE path costs ~150-290us/row (a new heap tuple plus a random-page
#     insertion into every index, because fillfactor=100 forbids HOT). Rebuilding
#     39M rows and sorting each index once took 122 SECONDS. Pass mode="update"
#     for the old path.
#
# WHY TF NEEDS VENUE DIFFICULTY TOO
#   geometry_spline already corrects track LENGTH and BANKING inside
#   normalized_time. What it cannot see is altitude, surface compound, wind
#   exposure, and the rest -- and outdoor tracks genuinely differ on those. So TF
#   venues get a difficulty exactly like XC courses do. The venue key is the
#   location, since a track has no "course name".
#
# WHY THE TWO SPORTS STAY SEPARATE
#   Owner's call, and it is the right one: a pool is (level, gender, SPORT). The
#   distance/era fitters already key their curves `pool|sport`, so keeping the
#   same convention here means an athlete's XC ability and TF ability are solved
#   independently and never contaminate each other.
#
# WHY TF IS FILTERED TO >= 800m
#   normalized_time scales a race to a 5k equivalent with (D/5000)^b. That law is
#   fitted on 800m and up. Apply it to a 100m dash and an 11s sprint becomes a
#   ~693s "5k" -- inside the sanity band, and complete garbage. Relays and field
#   events are excluded for the same reason: they are not one runner's race.

import io                        # in-memory buffer for COPY payloads
import sys
import time
from datetime import date
from itertools import islice     # lazy chunking; never materialises the source

import psycopg2
import psycopg2.errors
import psycopg2.extras

sys.path.insert(0, "scripts")
sys.path.insert(0, "engine")
from database import getConn
from merge_column import mergeColumn      # heap-rebuild write path (see below)
import person_gender as _pg               # gender by the rows (issue 164)

_PG_AVAILABLE = None


def _personGenderAvailable():
    """Once per process: does person_gender exist? Without it the pack
    pools by the profile majority exactly as before."""
    global _PG_AVAILABLE
    if _PG_AVAILABLE is None:
        try:
            with getConn() as conn, conn.cursor() as cur:
                _PG_AVAILABLE = _pg.available(cur)
        except Exception:                                    # noqa: BLE001
            _PG_AVAILABLE = False
        print(f"[engine] person_gender: "
              f"{'ON (rows decide, split people are two athletes)' if _PG_AVAILABLE else 'absent -- profile majority'}")
    return _PG_AVAILABLE


def _personGenderJoin():
    if not _personGenderAvailable():
        return ""
    return ("\n        LEFT JOIN person_gender pg "
            "ON pg.person_id = COALESCE(r.person_id, r.athlete_id)")


_TEAM_COLS = {}


def _teamColumns(table):
    """The SELECT fragment for the team: the real columns when the table
    has them (results.team_id came with the scraper, team_slug with the
    2026-09-13 migration), NULLs otherwise, so a database that predates
    either still packs. Probed once per table."""
    got = _TEAM_COLS.get(table)
    if got is None:
        have = set()
        try:
            with getConn() as conn, conn.cursor() as cur:
                cur.execute("""SELECT column_name FROM information_schema.columns
                               WHERE table_schema = 'public' AND table_name = %s
                                 AND column_name IN ('team_id', 'team_slug')""", (table,))
                have = {r[0] for r in cur.fetchall()}
        except Exception:                                    # noqa: BLE001
            have = set()
        got = ", ".join([("r.team_id" if "team_id" in have else "NULL::bigint AS team_id"),
                         ("r.team_slug" if "team_slug" in have else "NULL::text AS team_slug")])
        _TEAM_COLS[table] = got
        if have != {"team_id", "team_slug"}:
            print(f"[engine] {table}: team columns {sorted(have) or 'absent'} -- the "
                  f"pack pools without the feed's team level where they are missing")
    return got


# Column order every loader yields. The engine unpacks by these indices.
COLUMNS = ("result_id", "person_id", "normalized_time", "grade", "source",
           "school", "date", "sport", "venue", "gender",
           # ★ THE ROW'S OWN DISTANCE (issue 148): the corrected one for XC,
           #   the event's for track. The joint solve fits one offset per
           #   (pool, track distance) from it; a pack without it runs with
           #   that block off.
           "dist_m",
           # ★ THE MEET'S CHAMPIONSHIP CLASS BY NAME (issue #22, 2026-09-11):
           #   0 ordinary, 1 league, 2 qualifying round, 3 final, from the
           #   meet's NAME and the tfrrs flag through engine/meet_class.py.
           #   A DIAGNOSTIC ONLY: the joint solve's taper term reads the
           #   athletes' own calendars (run_joint.seasonEndShare) and never
           #   a name; it cross-tabulates that share against this class so
           #   the log can say whether the finals show the highest share.
           #   A pack without it runs without the cross-tab.
           "meet_class",
           # ★ THE RAW TIME (2026-09-13): with it the pack can tell which
           #   pool's scale a stored normalized_time is on and move the row
           #   onto the pool it is RATED in (speed_ratings.rescaleToPool).
           #   The 230 ratings were rows normalised as hs_m and rated as
           #   college_m; the DB repair (anchor_repair) runs at step 5 and
           #   never reaches a pack built earlier or a pool decided later.
           "time_seconds",
           # ★ THE TEAM (2026-09-14, the pooling redo): anet's TeamID, which
           #   anet_team names and LEVELS (college, high school, club...), and
           #   tfrrs's team slug, whose second token is the level. A club or
           #   an elite squad is not a school, and its gradeless rows were
           #   landing in the college pool because they raced college fields
           #   (pool_resolve.resolvePool, team_level).
           "team_id", "team_slug")


# ------------------------------------------------------------------ #
# CROSS-SOURCE DEDUP
# ------------------------------------------------------------------ #
#
# At a canon-linked meet the SAME physical race exists twice: an anet row and a
# tfrrs row. backfill_normalize drops the tfrrs copy as `dedup_twin` and never
# writes it a normalized_time. But its merge writes
# `COALESCE(s.nt, r.normalized_time)`, so a twin carrying a STALE
# normalized_time from an earlier run keeps it and reaches the engine as a
# second race for the same (person_id, pool): n_races inflated, ability pulled
# toward a duplicate, and the duplicate votes twice on its venue's difficulty.
#
# The rule, identical to the backfill's: at a canon-linked meet where BOTH
# sources have a row for the same person, KEEP THE ANET COPY. anet is richer --
# it has athlete_id and grade; tfrrs XC has athlete_id NULL on 100% of rows.
#
# ---------------------------------------------------------------------------
# WHY THIS IS A PRECOMPUTED TABLE AND NOT A SUBQUERY
# ---------------------------------------------------------------------------
# The first version expressed the rule inline:
#
#     AND NOT (r.source = 'tfrrs' AND r.canon_meet_id IS NOT NULL
#              AND EXISTS (SELECT 1 FROM results_tf t WHERE ...))
#
# `NOT (A AND B AND EXISTS(...))` is `NOT A OR NOT B OR NOT EXISTS(...)`. That
# DISJUNCTION blocks Postgres' anti-join transformation: it cannot pull the
# EXISTS out of an OR. So the planner falls back to a correlated SubPlan and
# re-executes it ONCE PER ROW -- 34M probes into a 191M-row table that has no
# index on person_id. Observed: 25 minutes on the FIRST fetch, no progress.
#
# The fix is to make the planner see a JOIN. We materialise the twin keys once
# (one scan + hash aggregate), index them, and LEFT JOIN + `IS NULL`. That is
# the textbook anti-join shape and the planner hash-joins it.
#
# The twin table is UNLOGGED: no WAL, and a crash simply loses a cache we can
# rebuild. It is dropped when the stream finishes.


def _twinTable(sport: str) -> str:
    return f"sr_twins_{sport.lower()}"

# loadPriorRatings
# Purpose:   {(person_id, pool): speed_rating} from the LAST engine run.
# Detail:    The fitness term is a function of rating, and rating is what the
#            engine solves. Frozen at load time, the correction is a fixed
#            input for the whole run -- read from the live table inside the
#            loop, it would chase its own output.
#            Returns {} on an empty table so a first-ever run still works;
#            every athlete then falls back to rating 100.
def loadPriorRatings() -> dict:
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT athlete_id, pool, speed_rating "
                        "FROM athlete_ratings WHERE speed_rating > 0")
            return {(pid, pool): rating for pid, pool, rating in cur.fetchall()}


# _buildTwinKeys
# Purpose:   materialise every (person_id, canon_meet_id) that has a row from
#            BOTH sources carrying a normalized_time.
# Output:    (table_name, n_rows). n_rows == 0 means there is nothing to dedup
#            and the caller should skip the join entirely.
# Syntax:    `count(DISTINCT source) > 1` is the twin test. The GROUP BY runs
#            over the FILTERED rows only (normalized_time NOT NULL cuts 191M to
#            ~29M on results_tf), so this is one scan, not a self-join.
def _buildTwinKeys(table: str, sport: str):
    tw = _twinTable(sport)
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%s
                  AND column_name='canon_meet_id'
            """, (table,))
            if cur.fetchone() is None:
                print(f"[db] {sport}: {table} has no canon_meet_id -- no dedup")
                return None, 0

            cur.execute(f"DROP TABLE IF EXISTS {tw}")
            cur.execute(f"""
                CREATE UNLOGGED TABLE {tw} AS
                SELECT person_id, canon_meet_id
                FROM {table}
                WHERE normalized_time IS NOT NULL
                  AND person_id IS NOT NULL
                  AND canon_meet_id IS NOT NULL
                GROUP BY person_id, canon_meet_id
                HAVING count(DISTINCT source) > 1
            """)
            cur.execute(f"CREATE INDEX ON {tw} (person_id, canon_meet_id)")
            cur.execute(f"ANALYZE {tw}")
            cur.execute(f"SELECT count(*) FROM {tw}")
            n = cur.fetchone()[0]
        conn.commit()
    print(f"[db] {sport}: {n:,} cross-source twin keys "
          f"({'dedup active' if n else 'nothing to dedup'})")
    if n == 0:
        _dropTwinKeys(sport)
        return None, 0
    return tw, n


def _dropTwinKeys(sport: str) -> None:
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {_twinTable(sport)}")
        conn.commit()


# _dedupJoin / _dedupFilter
# Purpose:   the anti-join, in the two places SQL needs it.
# Syntax:    putting `r.source = 'tfrrs'` in the ON clause (not the WHERE) means
#            an anet row NEVER matches, so `tw.person_id IS NULL` keeps it. A
#            tfrrs row matches only when it has an anet twin, and the IS NULL
#            filter then drops it. A tfrrs row with no twin does not match, so it
#            survives. Exactly the backfill's rule, as a join.
# ★ AND THE WRITTEN-DOWN VERDICT (issue 94): result_twin, built by
#   engine/twin_flag.py (step 04c), lists the tfrrs copies the person-keyed
#   rule cannot see -- a twin attached to another person_id, matched on
#   place and time at the canon meet -- and exact duplicates inside one
#   feed. Every reader anti-joins it; this one probes for it once per run.
_RESULT_TWIN = {"present": None}


def _probeResultTwin() -> bool:
    if _RESULT_TWIN["present"] is None:
        with getConn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('result_twin')")
                _RESULT_TWIN["present"] = cur.fetchone()[0] is not None
        print(f"[db] result_twin "
              f"{'present: flagged rows excluded' if _RESULT_TWIN['present'] else 'absent (run twin_flag.py --write)'}")
    return _RESULT_TWIN["present"]


def _dedupJoin(tw: str, sport: str = None) -> str:
    out = ""
    if tw:
        out += f"""
        LEFT JOIN {tw} tw
               ON r.source = 'tfrrs'
              AND tw.person_id     = r.person_id
              AND tw.canon_meet_id = r.canon_meet_id"""
    if sport and _RESULT_TWIN["present"]:
        out += f"""
        LEFT JOIN result_twin rtw
               ON rtw.sport = '{sport}' AND rtw.result_id = r.result_id"""
    return out


def _dedupFilter(tw: str, sport: str = None) -> str:
    out = "          AND tw.person_id IS NULL" if tw else ""
    if sport and _RESULT_TWIN["present"]:
        out += "\n          AND rtw.result_id IS NULL"
    return out


# _chairFilter
# Purpose:   exclude every athlete who races a chair, by PERSON, from both
#            sports. Issue #14.
# Output:    a SQL fragment, or "" when wheelchair_person has not been built.
# Detail:
#   ★ THE PERSON, NOT THE RACE, AND THAT IS THE WHOLE CHANGE. The division
#     match this replaced kept a chair race out of the solve and let the
#     ATHLETE in. One chair race under an ordinarily-named division sets an
#     ability a racing chair earned, every ordinary race of theirs is rated
#     against it, and the pair solve carries the error out to everyone they
#     raced. engine/wheelchair_flag.py resolves the question once, reading
#     BOTH feeds -- meets.division is anet-only and a tfrrs wheelchair
#     division was invisible to the old test.
#
#   ! IT DEGRADES RATHER THAN CRASHES. wheelchair_flag.py is a new step and
#     an older database has not run it; a missing optional table must leave
#     the engine runnable, exactly as loadCanonicalNames does for
#     course_canonical. It says so out loud, once, because silently rating
#     chair athletes is the bug this closes.
#
#   ! PROBED ONCE PER PROCESS. Both query builders call this and the pack
#     builds many queries; to_regclass per call would be a round trip each
#     time for an answer that cannot change mid-run.
_CHAIR_READY = None


# _ageBandGrade / _ageBandJoin
# Purpose:   apply issue #47 to the pack -- a banded grade in a division that
#            writes AGE ranges reaches poolOf as NO grade.
# Output:    a SELECT expression and a JOIN fragment, for one sport.
# Detail:
#   ★ THE ENGINE POOLS INDEPENDENTLY, WHICH IS WHY THIS SITE EXISTS. The
#     backfill nulls the same grade in its own stream, but speed_ratings
#     re-reads `r.grade` straight from results and calls poolOf on it
#     (speed_ratings.py:917). Wiring only the backfill would leave the engine
#     still reading "11-12" as eleventh and twelfth grade -- which is the
#     pooling that put Sean McGorty, a professional, in hs_m at 152.4 beside
#     the 129.9 of the man who beat him.
#
#   ! SAME DEGRADE-DON'T-CRASH CONTRACT AS _chairFilter, and probed once per
#     process for the same reason: the pack builds many queries and the
#     answer cannot change mid-run.
_AGEBAND_READY = None


def _ageBandReady() -> bool:
    global _AGEBAND_READY
    if _AGEBAND_READY is None:
        try:
            with getConn() as conn, conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.age_band_result')")
                _AGEBAND_READY = cur.fetchone()[0] is not None
        except Exception:                               # noqa: BLE001
            _AGEBAND_READY = False
        if not _AGEBAND_READY:
            print("[db] age_band_result not found -- banded grades will be "
                  "read as GRADES and youth fields will pool as high school. "
                  "Run engine/age_band_grades.py --write first (issue #47).")
    return _AGEBAND_READY


def _ageBandGrade() -> str:
    if not _ageBandReady():
        return "r.grade"
    return "CASE WHEN ab.result_id IS NULL THEN r.grade END AS grade"


def _ageBandJoin(sport: str) -> str:
    if not _ageBandReady():
        return ""
    return (f"\n        LEFT JOIN age_band_result ab"
            f"\n               ON ab.sport = '{sport}'"
            f"\n              AND ab.result_id = r.result_id")


def _chairFilter() -> str:
    global _CHAIR_READY
    if _CHAIR_READY is None:
        try:
            with getConn() as conn, conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.wheelchair_person')")
                _CHAIR_READY = cur.fetchone()[0] is not None
                # ! AND NOT EMPTY. The boards' ensureWheelchairPerson creates
                #   the table bare so their anti-join can run; an empty table
                #   excludes nobody and deserves the same banner as no table.
                if _CHAIR_READY:
                    cur.execute("SELECT count(*) FROM wheelchair_person")
                    _CHAIR_READY = cur.fetchone()[0] > 0
        except Exception:                               # noqa: BLE001
            _CHAIR_READY = False
        if not _CHAIR_READY:
            # ⚠ LOUD, BECAUSE THIS ALREADY HAPPENED ONCE (owner, 2026-09-01:
            #   "somehow wheelchair athletes snuck back into the engine").
            #   wheelchair_flag.py was never wired into run_pipeline.sh, so
            #   the table was never built and every run degraded to rating
            #   chair athletes -- past a single grey line in a four-hour log
            #   that nobody was going to catch. It is a banner now, and it
            #   names the fix.
            print("\n" + "!" * 70)
            print("!! wheelchair_person NOT FOUND -- CHAIR ATHLETES WILL BE "
                  "RATED.")
            print("!! A racing chair's normalized time is not a running time; "
                  "one such")
            print("!! race sets an ability the pair solve then spreads to "
                  "everyone they")
            print("!! raced.  Fix:  python engine/wheelchair_flag.py --write")
            print("!! (pipeline step 04b_wheelchair, which must run before "
                  "07_pack)")
            print("!" * 70 + "\n")
    if not _CHAIR_READY:
        return ""
    return ("\n          AND NOT EXISTS (SELECT 1 FROM wheelchair_person wc"
            "\n                          WHERE wc.person_id = r.person_id)")

# loadCanonicalNames
# Purpose:   {canonical_id_as_text: canonical_name} for display.
# Output:    dict, or {} if course_canonical does not exist yet.
# Detail:    The engine keys XC venues on canonical_id, but course_difficulties
#            keeps a human-readable course_name so the website and conversions.py
#            keep working unchanged. This is that lookup.
#
#            Keyed by TEXT, not int, because the venue string arriving from the
#            engine is text ("XC:1234") and converting once here is cheaper and
#            less error-prone than int() on every save row.
#
#            Returns {} rather than raising when the table is missing, so the
#            engine still runs before the migration -- it just falls back to
#            name-based display. A missing optional table must degrade, not crash.
def loadCanonicalNames() -> dict:
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT to_regclass('public.course_canonical') IS NOT NULL
            """)
            if not cur.fetchone()[0]:
                print("[db] course_canonical not found -- display names will "
                      "fall back to ids. Run build_course_canonical.py --apply.")
                return {}

            cur.execute("""
                SELECT DISTINCT canonical_id::text, canonical_name
                FROM course_canonical
            """)
            return dict(cur.fetchall())


# ------------------------------------------------------------------ #
# QUERIES
# ------------------------------------------------------------------ #


# These are not places. They are what a scraper writes when the venue was
# unknown, and a "difficulty" fitted on them means nothing.
#
# WHAT THIS IS NOT FOR. course_canonical keys on (name, lat, lng), so a generic
# name at many coordinates ALREADY splits into many venues -- 11 distinct "City
# Park"s stay 11 venues, correctly. Generic-but-real names must therefore stay
# OFF this list; blocklisting "city park" would discard 436 rows of legitimate
# course votes. The test is semantic, not statistical: does the string assert
# that no venue is known?
#
# ★ COORDINATE COUNT IS NOT THE TEST. "bye", "pending" and "various locations"
# each resolve to a single coordinate -- a geocoder fallback, not a place. A low
# place-count means "not fused", which is a different claim from "real".
#
# CONSERVATIVE. A placeholder we miss costs one noisy venue; a real venue we
# blocklist silently loses every course vote it ever cast. "Encampment" is a
# Wyoming town, "Midway" and "Liberty" are real schools -- all stay off.
#
# Compared lowercase and trimmed, so "TBA" / "tba" / " TBD " all match.
_PLACEHOLDER_VENUES = frozenset({
    # junk / empty markers
    "", "-", "--", "?", "??", "???", "n/a", "na", "none", "null", "y",
    # venue explicitly not yet known
    "tba", "tbd", "to be updated later", "pending",
    "undetermined", "as yet undetermined", "unknown", "unspecified",
    # venue explicitly not singular
    "various", "various locations", "multiple locations",
    "various courses around japan",
    # venue explicitly not fixed
    "anywhere", "anywhere in oregon", "open", "bye", "virtual",
    "test location", "choose your own location & be safe",
    "your own school", "your track course of choosing",
    # not venues at all
    "records", "stats", "districts", "sectionals",
    "district meet/resource center",
})


# _placeholderSql
# Purpose:   render the blocklist as a SQL IN-list literal.
# Output:    "'tba', 'tbd', ..."
# Detail:    Doubling a single quote is SQL's own escape for a literal quote,
#            which is what "choose your own location & be safe" needs if it ever
#            gains an apostrophe. These are hardcoded constants, not user input,
#            so there is no injection surface -- this is correctness, not safety.
def _placeholderSql() -> str:
    return ", ".join("'" + v.replace("'", "''") + "'"
                     for v in sorted(_PLACEHOLDER_VENUES))

# _xcQuery
# Purpose:   XC rows. Venue = canonical course id + race distance.
# Arguments: min_time, max_time -- normalized_time sanity band.
# Output:    SQL string.
#
# VENUE KEY: "<canonical_id>:d<distance>", e.g. "1234:d4800". packResults
#   prefixes the sport, giving "XC:1234:d4800". The distance is part of the key
#   because a venue can host many distances and one difficulty cannot cover
#   them: measured at UCSB Lagoon, per-distance deviation runs +0.13 at 2301m
#   to -0.03 at 8000m, all currently blended into a single +0.063.
#
# LEFT JOINs everywhere. Every INNER JOIN in the old query was a silent tfrrs
#   delete; anything dropped now is dropped on purpose. A row with no venue
#   still informs its athlete's ability, it just votes on no course.
# ★ THE CHAMPIONSHIP CLASS OF A MEET, FROM ITS NAME (issue #22), AS A
#   DIAGNOSTIC. 0 ordinary, 1 a league / conference / county / metro
#   championship, 2 a qualifying round (section, region, district, prelim,
#   the tfrrs is_championship flag), 3 a final (the state meet, NXN, NXR,
#   Foot Locker, the NCAA meets). POSIX regexes, case-insensitive (no `%`,
#   no braces: the query is an f-string and psycopg2 scans for `%`). The
#   joint solve's taper term does NOT read this column: its covariate is
#   the season-end share from the athletes' own calendars
#   (run_joint.seasonEndShare). The solve cross-tabulates that share
#   against this class in the log, so a misread name can move nothing.
#   The rule lives in engine/meet_class.py (one place, with a Python twin
#   the tests and scripts/meet_class_census.py run); this is its SQL.
from meet_class import sql as _meetClassSql          # noqa: E402


def _xcQuery(min_time: float, max_time: float, tw: str = "") -> str:
    return f"""
        SELECT r.result_id, r.person_id, r.normalized_time,
               {_ageBandGrade()}, r.source, r.school, r.date,
               'XC' AS sport,
               -- ★ A CORRECTED DIVISION VOTES ON NO COURSE (owner's rule,
               --   2026-08-27: a corrected distance CANNOT change the
               --   difficulty). A division whose distance was overridden was
               --   mislabelled once already; letting its renormalized times
               --   vote difficulty lets one correction move every OTHER race
               --   at the venue -- the second-order effect measured at
               --   Cabell Midland (+0.155, ~19 fake points on a clean race).
               --   Venue NULL = the row still informs its athlete's ability
               --   and votes on nothing, the loader's own venueless rule.
               --   Agreement entries (override == stored) keep their venue.
               CASE WHEN dov.distance IS NOT NULL
                     AND COALESCE(m.distance,
                                  (mt.division_distances -> r.div_id::text
                                     ->> 'distance')::real,
                                  mt.distance) IS NOT NULL
                     AND abs(dov.distance
                             - COALESCE(m.distance,
                                        (mt.division_distances -> r.div_id::text
                                           ->> 'distance')::real,
                                        mt.distance)) >= 1
                    THEN NULL
                    WHEN lower(btrim(COALESCE(m.course_name, mt.venue_name)))
                         IN ({_placeholderSql()})
                    THEN NULL
                    WHEN COALESCE(m.course_name, mt.venue_name) IS NULL
                    THEN NULL
                    ELSE COALESCE(
                            cc.canonical_id::text,
                            'name:' || btrim(COALESCE(m.course_name,
                                                      mt.venue_name)))
                         || ':d'
                         || COALESCE(
                              (round(COALESCE(
                                    m.distance,
                                    -- ★ PER-DIVISION, from the jsonb. tfrrs stores a
                                    --   distance PER DIVISION in division_distances
                                    --   and leaves the scalar mt.distance NULL:
                                    --   measured, 16,402 meets carry the jsonb and ALL
                                    --   16,402 have a NULL scalar, 15,639 of them with
                                    --   more than one division.
                                    --
                                    --   Reading only the scalar meant a women's 5000 and
                                    --   a men's 8000 at one meet shared one distance. The
                                    --   women's times then normalised as if run over 8k
                                    --   and came out at 895s -- a 14:55 5k -- which put
                                    --   twelve college_f athletes above every real
                                    --   performance in the corpus.
                                    (mt.division_distances -> r.div_id::text
                                       ->> 'distance')::real,
                                    mt.distance)
                                     / 100.0) * 100)::int::text,
                              'NA')
               END AS venue,
               {_pg.packGenderExpr('XC', _personGenderAvailable())} AS gender,
               COALESCE(dov.distance,
                        m.distance,
                        (mt.division_distances -> r.div_id::text
                           ->> 'distance')::real,
                        mt.distance)::real AS dist_m,
               {_meetClassSql("COALESCE(m.meet_name, mt.meet_name, '')",
                              "COALESCE(mt.is_championship, 0) = 1")} AS meet_class,
               r.time_seconds::real AS time_seconds,
               {_teamColumns('results')}
        FROM results r{_ageBandJoin('XC')}
        LEFT JOIN meets m
               ON m.div_id = r.div_id AND m.source = r.source
        LEFT JOIN meets_tfrrs mt
               ON r.source = 'tfrrs'
              AND mt.meet_id = r.meet_id
              AND mt.sport = 'XC'
        LEFT JOIN course_canonical cc
               ON cc.course_name = COALESCE(m.course_name, mt.venue_name)
              AND round(cc.gps_lat::numeric,  5)
                = round(COALESCE(m.gps_lat,  mt.gps_lat)::numeric,  5)
              AND round(cc.gps_long::numeric, 5)
                = round(COALESCE(m.gps_long, mt.gps_long)::numeric, 5)
        -- (meet_id, div_id) is dist_override's whole key -- no source column,
        -- no fan-out. Same join every other reader uses.
        LEFT JOIN dist_override dov
               ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
        LEFT JOIN LATERAL (
               -- ⚠ MAJORITY, NOT ALPHABETICAL-BY-SCHOOL. This was
               --   `ORDER BY a.school LIMIT 1`, which is deterministic but
               --   arbitrary: when one person_id carries rows of both
               --   genders -- two real people merged, or a mis-sexed feed
               --   row -- the winner was whichever SCHOOL NAME sorted first.
               --   Reported for Cam Kuss (owner, 2026-09-01), where
               --   "Broughton (NC)" beat "Unattached (TX)" and a boy was
               --   rated in a girls' pool for his whole career.
               -- ! COUNT FIRST, THEN 'M'. The count is the evidence; the
               --   letter is only the tie-break, and DESC puts 'M' above
               --   'F' so an exact 50/50 lands male, which is what the
               --   owner asked for. Both keys are needed: count alone is
               --   not deterministic.
               -- ⚠ THIS ORDERING IS DUPLICATED in
               --   racecast/build_ranking_results._GENDER_TEMP_SQL and the
               --   two MUST match -- a different tie-break there would
               --   silently repool athletes relative to the engine.
               --   tests/test_gender_pick.py pins them together.
               SELECT a.gender FROM athletes a
               WHERE a.athlete_id = COALESCE(r.person_id, r.athlete_id)
                 AND a.gender IN ('M', 'F')
               GROUP BY a.gender
               ORDER BY count(*) DESC, a.gender DESC
               LIMIT 1
        ) a ON TRUE{_personGenderJoin()}{_dedupJoin(tw, 'XC')}
        WHERE r.normalized_time IS NOT NULL
          AND r.normalized_time BETWEEN {min_time} AND {max_time}
          AND r.date IS NOT NULL
          AND r.person_id IS NOT NULL
          -- ★ WHEELCHAIR AND SEATED RACES ARE NOT RUNNING RACES. A racing
          --   chair covers 1500m far faster than a runner, so its normalized
          --   time is extreme and the athlete rates ~147 in a youth pool.
          --   Measured at Pine Cone Classic and USATF Inland NW: division
          --   reads 'Wheelchair/Seated', '17-18 Wheelchair', '15-16
          --   Wheelchair', always on its own div_id and never mixed with the
          --   running divisions -- so this drops the event class without
          --   touching a single runner.
          AND COALESCE(m.division, '') !~* '(wheelchair|seated|ambulator)'
          -- ★ AND THE ATHLETE TOO, NOT ONLY THE RACE. The line above is a
          --   RACE filter on an anet-only column; one chair race under an
          --   ordinarily-named division, or any tfrrs chair division at all,
          --   slipped past it and set that athlete's ability. See
          --   _chairFilter and engine/wheelchair_flag.py (issue #14). The
          --   division test is KEPT: it costs nothing and still holds when
          --   wheelchair_person has not been built.{_chairFilter()}
{_dedupFilter(tw, 'XC')}
    """


# _tfQuery
# Purpose:   TF rows. Venue = the LOCATION (a track has no course name), split by
#            indoor/outdoor because a 200m indoor oval and an outdoor 400m are
#            different places even at one address.
# Arguments: min_time, max_time.
# Output:    SQL string.
# Detail:    distance_meters >= 800 keeps the distance law inside its fitted
#            domain. is_relay / is_field excluded: not one runner's own race.
def _eventMetersSql(alias: str) -> str:
    """Metres from an event name, in SQL: '5000m' 5000, '10,000m' 10000,
    '8k' 8000, 'Mile' 1609.34, '2 Mile' 3218.7; NULL where no number and
    no mile (hurdles and relays never reach the pack: no normalized_time)."""
    # ! THE FIRST NUMBER, NOT EVERY DIGIT AND DOT (run16c, 2026-09-07): an
    #   event_short of "3000.." left "3000.." after the strip, and the cast
    #   killed the pack. Commas go first ("10,000m"), then one number token;
    #   (?:...) so substring returns the whole match, not the group.
    num = (f"substring(regexp_replace({alias}.event_short, ',', '', 'g') "
           f"from '[0-9]+(?:\\.[0-9]+)?')::real")
    return (f"CASE WHEN {alias}.event_short IS NULL THEN NULL "
            # a steeplechase or a walk is not the flat event of its metres:
            # no class (as before), rather than the 3000's offset
            f"WHEN lower({alias}.event_short) ~ '(steeple|walk|hurdle)' THEN NULL "
            f"WHEN lower({alias}.event_short) LIKE '%mile%' "
            f"THEN COALESCE({num}, 1) * 1609.34 "
            f"WHEN {num} IS NULL THEN NULL "
            f"WHEN {num} < 100 THEN {num} * 1000 "
            f"ELSE {num} END")


def _tfQuery(min_time: float, max_time: float, tw: str = "") -> str:
    return f"""
        SELECT r.result_id, r.person_id, r.normalized_time,
               {_ageBandGrade()}, r.source, r.school, r.date,
               'TF' AS sport,
               CASE WHEN m.location_id IS NULL THEN NULL
                    ELSE 'loc:' || m.location_id::text ||
                         CASE WHEN COALESCE(m.is_indoor, 0) = 1 THEN ':in'
                              ELSE ':out' END
               END AS venue,
               {_pg.packGenderExpr('TF', _personGenderAvailable())} AS gender,
               -- ★ THE EVENT'S METRES, FROM THE NAME WHEN meets_tf HAS NONE
               --   (2026-09-06): the college 5000 and 10000 carried no
               --   dist_m (no meets_tf distance for those events), so they
               --   had no event offset, no endurance slope and no pairs --
               --   the owner's "5k/10k too low" on the college boards.
               --   Same parse the backfill uses, in SQL: digits, 'k' =
               --   thousands, 'mile' = 1609.34 each.
               COALESCE(m.distance_meters::real, {_eventMetersSql('r')}) AS dist_m,
               {_meetClassSql("COALESCE(m.meet_name, '')")} AS meet_class,
               r.time_seconds::real AS time_seconds,
               {_teamColumns('results_tf')}
        FROM results_tf r{_ageBandJoin('TF')}
        LEFT JOIN meets_tf m
               ON m.meet_id = r.meet_id AND m.div_id = r.div_id
              AND m.event_id = r.event_id AND m.source = r.source
        LEFT JOIN LATERAL (
               -- ⚠ MAJORITY, NOT ALPHABETICAL-BY-SCHOOL. This was
               --   `ORDER BY a.school LIMIT 1`, which is deterministic but
               --   arbitrary: when one person_id carries rows of both
               --   genders -- two real people merged, or a mis-sexed feed
               --   row -- the winner was whichever SCHOOL NAME sorted first.
               --   Reported for Cam Kuss (owner, 2026-09-01), where
               --   "Broughton (NC)" beat "Unattached (TX)" and a boy was
               --   rated in a girls' pool for his whole career.
               -- ! COUNT FIRST, THEN 'M'. The count is the evidence; the
               --   letter is only the tie-break, and DESC puts 'M' above
               --   'F' so an exact 50/50 lands male, which is what the
               --   owner asked for. Both keys are needed: count alone is
               --   not deterministic.
               -- ⚠ THIS ORDERING IS DUPLICATED in
               --   racecast/build_ranking_results._GENDER_TEMP_SQL and the
               --   two MUST match -- a different tie-break there would
               --   silently repool athletes relative to the engine.
               --   tests/test_gender_pick.py pins them together.
               SELECT a.gender FROM athletes a
               WHERE a.athlete_id = COALESCE(r.person_id, r.athlete_id)
                 AND a.gender IN ('M', 'F')
               GROUP BY a.gender
               ORDER BY count(*) DESC, a.gender DESC
               LIMIT 1
        ) a ON TRUE{_personGenderJoin()}{_dedupJoin(tw, 'TF')}
        WHERE r.normalized_time IS NOT NULL
          AND r.normalized_time BETWEEN {min_time} AND {max_time}
          AND r.date IS NOT NULL
          AND r.person_id IS NOT NULL
          AND COALESCE(r.is_relay, 0) = 0
          AND COALESCE(r.is_field, 0) = 0
          -- ★ WHEELCHAIR AND SEATED RACES ARE NOT RUNNING RACES. A racing
          --   chair covers 1500m far faster than a runner, so its normalized
          --   time is extreme and the athlete rates ~147 in a youth pool.
          --   Measured at Pine Cone Classic and USATF Inland NW: division
          --   reads 'Wheelchair/Seated', '17-18 Wheelchair', '15-16
          --   Wheelchair', always on its own div_id and never mixed with the
          --   running divisions -- so this drops the event class without
          --   touching a single runner.
          AND COALESCE(m.division, '') !~* '(wheelchair|seated|ambulator)'
          -- ★ AND THE ATHLETE TOO, NOT ONLY THE RACE. The line above is a
          --   RACE filter on an anet-only column; one chair race under an
          --   ordinarily-named division, or any tfrrs chair division at all,
          --   slipped past it and set that athlete's ability. See
          --   _chairFilter and engine/wheelchair_flag.py (issue #14). The
          --   division test is KEPT: it costs nothing and still holds when
          --   wheelchair_person has not been built.{_chairFilter()}
{_dedupFilter(tw, 'TF')}
    """


# ------------------------------------------------------------------ #
# THE VENUES' COORDINATES, PER COURSE KEY (the place prior, 2026-09-14)
# ------------------------------------------------------------------ #
#
# ★ A venue keyed in pieces -- Foot Locker's final under one canonical id,
#   the rest of Balboa under others -- is several thin cells the engine
#   can only pull toward the sport's average. Its coordinates say which
#   cells are one place; the bracket engine (bracket_engine.placeClusters)
#   pulls cells within a few hundred metres, at one distance, toward each
#   other before it pulls them toward the average course. The pack carries
#   one (lat, lon) per course key: an XC key's canonical id through
#   course_canonical, a TF key's location through meets_tf; NaN where the
#   key has neither (a name-keyed XC cell, a venueless row).
# ★ WHAT ANET'S TEAM LEVEL CODES MEAN, LEARNED FROM OUR OWN GRADES (the
#   pooling redo, 2026-09-14). anet_team.level is an integer anet does not
#   document; the rows tell us: a code whose rows mostly carry grades 9-12
#   is a high school code, 6-8 middle school, 1-5 elementary. A code whose
#   rows carry NO grade is a college or a club, and those two are told
#   apart by tfrrs: a school whose tfrrs slug says college is a college.
#   The table is printed, and XCP_ANET_LEVELS="4=college,3=hs,5=club"
#   states any code outright.
def loadTeamLevels(min_rows=200, share=0.5, college_share=0.25):
    """({anet team_id: level name}, {code: level name}, table rows). level
    names: 'college', 'hs', 'ms', 'elem', 'club'; a code that cannot be
    named is left out. Empty dicts without anet_team."""
    import os
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('anet_team')")
        if cur.fetchone()[0] is None:
            return {}, {}, []
        cur.execute("SELECT team_id, level, school FROM anet_team WHERE level IS NOT NULL")
        teams = {int(t): (int(lv), sc) for t, lv, sc in cur.fetchall()}
        # the rows' grades per code, from the XC table (the anet grades)
        cur.execute("""
            SELECT t.level,
                   CASE WHEN r.grade ~ '^(9|10|11|12)$' THEN 'hs'
                        WHEN r.grade ~ '^[678]$' THEN 'ms'
                        WHEN r.grade ~ '^[1-5]$' THEN 'elem'
                        WHEN r.grade IS NULL OR btrim(r.grade) IN ('', '-') THEN 'none'
                        ELSE 'other' END AS lv,
                   count(*)
            FROM   results r JOIN anet_team t ON t.team_id = r.team_id
            WHERE  t.level IS NOT NULL
            GROUP  BY 1, 2""")
        tally = {}
        for code, lv, n in cur.fetchall():
            tally.setdefault(int(code), {})[lv] = int(n)
        # the schools tfrrs calls colleges
        cur.execute("""SELECT DISTINCT lower(btrim(school)) FROM results_tf
                       WHERE team_slug IS NOT NULL AND team_slug LIKE '%%\_college\_%%'
                         AND school IS NOT NULL""")
        colleges = {r[0] for r in cur.fetchall()}
    # per code: the share of its TEAMS whose school tfrrs calls a college
    by_code_teams = {}
    for t, (code, sc) in teams.items():
        by_code_teams.setdefault(code, []).append((sc or "").strip().lower() in colleges)
    meaning, rows = {}, []
    for code in sorted(set(tally) | set(by_code_teams)):
        t = tally.get(code, {})
        n = sum(t.values())
        shares = {k: v / n for k, v in t.items()} if n else {}
        c_teams = by_code_teams.get(code, [])
        c_share = (sum(c_teams) / len(c_teams)) if c_teams else 0.0
        name = None
        if n >= min_rows:
            for lv in ("hs", "ms", "elem"):
                if shares.get(lv, 0.0) >= share:
                    name = lv
            if name is None and shares.get("none", 0.0) >= share:
                name = "college" if c_share >= college_share else "club"
        rows.append((code, n, shares, len(c_teams), c_share, name))
        if name:
            meaning[code] = name
    stated = os.environ.get("XCP_ANET_LEVELS", "")
    for part in stated.split(","):
        k, _, v = part.partition("=")
        if k.strip().isdigit() and v.strip():
            meaning[int(k)] = v.strip()
    by_team = {t: meaning[code] for t, (code, _sc) in teams.items() if code in meaning}
    return by_team, meaning, rows


# ★ A CLUB WITH PROFESSIONALS IN IT HAS NO MIDDLE SCHOOLERS (owner,
#   2026-09-14: "lots of club runners are labeled as msers because they
#   are in their '6th' pro year ... separate ms and pro clubs based on if
#   there's any pros in the club. If there are, make that club unable to
#   have msers"). A youth club's grade 6 is a sixth grader; an elite
#   squad's grade 6 is a sixth year, and it advances every season like a
#   grade does, so no grade rule can tell them apart. The club can: a
#   team any of whose athletes pro_flag has called professional in a
#   season they raced for it is a professional team, and its rows with a
#   grade of 1-8 or no grade are repooled pro (pool_resolve, team_has_pros).
#   Rows with a high-school grade on such a team keep it -- a sponsor's
#   youth squad and its elite group can wear one name.
def loadClubPros(min_pros=1):
    """({anet team_id: n pro athletes}, {normalised school: n}) for teams
    with at least min_pros professional athletes (pro_athlete_season) in
    a season they raced for the team. Empty without the table."""
    by_team, by_school = {}, {}
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('pro_athlete_season')")
        if cur.fetchone()[0] is None:
            return by_team, by_school
        for table in ("results", "results_tf"):
            cur.execute("""SELECT column_name FROM information_schema.columns
                           WHERE table_schema = 'public' AND table_name = %s
                             AND column_name = 'team_id'""", (table,))
            has_team = cur.fetchone() is not None
            team_expr = "r.team_id" if has_team else "NULL::bigint"
            cur.execute(f"""
                WITH pr AS (SELECT person_id, min(season) AS s0, max(season) AS s1
                            FROM pro_athlete_season GROUP BY person_id)
                SELECT {team_expr} AS team_id, lower(btrim(r.school)) AS school,
                       count(DISTINCT r.person_id) AS n
                FROM   {table} r JOIN pr ON pr.person_id = r.person_id
                WHERE  r.date ~ '^(19|20)[0-9][0-9]-'
                  AND  substr(r.date, 1, 4)::int BETWEEN pr.s0 AND pr.s1 + 1
                  AND  (({team_expr}) IS NOT NULL OR r.school IS NOT NULL)
                GROUP  BY 1, 2""")
            for team_id, school, n in cur.fetchall():
                if team_id is not None:
                    by_team[int(team_id)] = by_team.get(int(team_id), 0) + int(n)
                elif school:
                    by_school[school] = by_school.get(school, 0) + int(n)
    by_team = {k: v for k, v in by_team.items() if v >= min_pros}
    by_school = {k: v for k, v in by_school.items() if v >= min_pros
                 and not k.startswith("unattached") and k not in ("unat", "independent", "individual", "none", "n/a")}
    return by_team, by_school


# ★ ONLY WHEN THE CLUB IS WHERE THEY RACE (owner, 2026-09-14: "it should
#   only be if they run the majority of their races with their club /
#   national team. So any collegiate runner running the Euros would be
#   fine"). The club rules above are per ROW; a college runner's one
#   national-team race in July is a row on a team with professionals. So
#   the rules fire only for athlete-years in which MORE THAN HALF of the
#   athlete's rows are on a club or a team with professionals.
def loadClubMajority(club_team_ids, pro_team_ids, pro_schools):
    """{(person_id, calendar year)} whose rows that year are mostly on a
    club-level anet team, a team with professionals, or a school string
    with professionals. Empty when there is nothing to test against."""
    team_ids = sorted(set(int(t) for t in club_team_ids) | set(int(t) for t in pro_team_ids))
    schools = sorted(set(str(x) for x in pro_schools))
    if not team_ids and not schools:
        return set()
    agg = {}
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE club_teams (team_id bigint PRIMARY KEY) ON COMMIT DROP")
        cur.execute("CREATE TEMP TABLE club_schools (school text PRIMARY KEY) ON COMMIT DROP")
        if team_ids:
            _copyInto(cur, "club_teams", ("team_id",), [(t,) for t in team_ids])
        if schools:
            _copyInto(cur, "club_schools", ("school",), [(_escape(x),) for x in schools])
        for table in ("results", "results_tf"):
            cur.execute("""SELECT column_name FROM information_schema.columns
                           WHERE table_schema = 'public' AND table_name = %s
                             AND column_name = 'team_id'""", (table,))
            has_team = cur.fetchone() is not None
            on_team = ("ct.team_id IS NOT NULL" if has_team else "FALSE")
            join_team = (f"LEFT JOIN club_teams ct ON ct.team_id = r.team_id" if has_team else "")
            cur.execute(f"""
                SELECT r.person_id, substr(r.date, 1, 4)::int AS yr,
                       count(*) FILTER (WHERE {on_team} OR cs.school IS NOT NULL) AS n_club,
                       count(*) AS n
                FROM   {table} r
                {join_team}
                LEFT   JOIN club_schools cs ON cs.school = lower(btrim(r.school))
                WHERE  r.person_id IS NOT NULL AND r.date ~ '^(19|20)[0-9][0-9]-'
                GROUP  BY 1, 2
                HAVING count(*) FILTER (WHERE {on_team} OR cs.school IS NOT NULL) > 0""")
            for pid, yr, n_club, n in cur.fetchall():
                k = (int(pid), int(yr))
                a = agg.get(k, [0, 0])
                a[0] += int(n_club); a[1] += int(n)
                agg[k] = a
        conn.rollback()                                  # the temp tables
    # ! BOTH SPORTS TOGETHER: a season's rows are summed across the two
    #   tables before the majority is judged (a HAVING that kept only the
    #   athlete-years with a club row means a year with none is not here,
    #   which is the same answer: no club rows, no majority)
    return {k for k, (n_club, n) in agg.items() if n_club * 2 > n}


# ★ A CLUB IS A TEAM WITH NO SCHOOL IN IT, FROM THE DATA (owner, 2026-09-14:
#   Garden State TC, Atlanta TC, Saucony, Pacific Athletics on the college
#   boards). anet names some teams' levels and pro_flag names some
#   professionals; the tfrrs half of the corpus has neither for a club, so
#   the rows have to say it: a team with at least min_rows rows of which
#   fewer than max_grade_share carry a school grade, that no tfrrs slug
#   calls a college and the college directory does not list, is a club --
#   an elite squad, an adult club, a national team. A college on anet is
#   gradeless too, which is what the directory and the slugs are for.
def loadClubTeams(min_rows=20, max_grade_share=0.05):
    """({anet team_id: n rows}, {normalised school: n rows}) of teams whose
    rows carry (almost) no school grade and that are not colleges."""
    by_team, by_school = {}, {}
    grade_expr = ("CASE WHEN r.grade ~ '^([1-9]|1[0-2])$' OR lower(btrim(r.grade)) "
                  "IN ('fr','so','jr','sr','fr-1','so-2','jr-3','sr-4') THEN 1 ELSE 0 END")
    with getConn() as conn, conn.cursor() as cur:
        colleges = set()
        cur.execute("SELECT to_regclass('college_directory')")
        if cur.fetchone()[0] is not None:
            cur.execute("SELECT lower(btrim(name)) FROM college_directory")
            colleges |= {r[0] for r in cur.fetchall() if r[0]}
        for table in ("results", "results_tf"):
            cur.execute("""SELECT column_name FROM information_schema.columns
                           WHERE table_schema = 'public' AND table_name = %s
                             AND column_name IN ('team_id', 'team_slug')""", (table,))
            have = {r[0] for r in cur.fetchall()}
            if "team_slug" in have:
                cur.execute(f"""SELECT DISTINCT lower(btrim(school)) FROM {table}
                                WHERE team_slug LIKE '%%\\_college\\_%%' AND school IS NOT NULL""")
                colleges |= {r[0] for r in cur.fetchall() if r[0]}
            team_expr = "r.team_id" if "team_id" in have else "NULL::bigint"
            cur.execute(f"""
                SELECT {team_expr} AS team_id, lower(btrim(r.school)) AS school,
                       count(*) AS n, sum({grade_expr}) AS n_graded
                FROM   {table} r
                WHERE  r.school IS NOT NULL
                GROUP  BY 1, 2
                HAVING count(*) >= %s""", (int(min_rows),))
            for team_id, school, n, n_graded in cur.fetchall():
                if isClubName(school, colleges) and (int(n_graded) / max(int(n), 1)) < max_grade_share:
                    if team_id is not None:
                        by_team[int(team_id)] = by_team.get(int(team_id), 0) + int(n)
                    else:
                        by_school[school] = by_school.get(school, 0) + int(n)
    return by_team, by_school


_SCHOOL_WORDS = (" high", " middle", " elementary", " school", " hs", " ms", " academy",
                 " prep", " college", "university", "univ ", " jr", " sr ", " intermediate")


def isClubName(school, colleges=()):
    """A school string that can be a club: not a college, not unattached,
    not a name that says school. Pure; the grade share is the caller's."""
    s = (school or "").strip().lower()
    if not s or s in colleges:
        return False
    if s.startswith("unattached") or s in ("unat", "independent", "individual", "none", "n/a"):
        return False
    padded = " " + s + " "
    return not any(w in padded for w in _SCHOOL_WORDS)


def printTeamLevels(meaning, rows):
    print("[engine] anet team levels (code -> meaning, from our rows' grades and "
          "tfrrs's college slugs; XCP_ANET_LEVELS=\"4=college,5=club\" states one):")
    print(f"        {'code':>5}{'rows':>11}{'hs':>7}{'ms':>7}{'elem':>7}{'none':>7}"
          f"{'teams':>8}{'tfrrs col.':>11}   meaning")
    for code, n, shares, n_teams, c_share, name in rows:
        print(f"        {code:>5}{n:>11,}"
              + "".join(f"{100 * shares.get(k, 0.0):>6.0f}%" for k in ("hs", "ms", "elem", "none"))
              + f"{n_teams:>8,}{100 * c_share:>10.0f}%   {meaning.get(code) or '(unnamed)'}")


def loadCourseCoords(course_keys):
    """(lat, lon) float arrays, one per course key, NaN where unknown."""
    import numpy as np
    n = len(course_keys)
    lat = np.full(n, np.nan)
    lon = np.full(n, np.nan)
    xc, tf = {}, {}
    with getConn() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('course_canonical')")
        if cur.fetchone()[0] is not None:
            cur.execute("""SELECT canonical_id, avg(gps_lat), avg(gps_long)
                           FROM course_canonical
                           WHERE gps_lat IS NOT NULL AND gps_long IS NOT NULL
                           GROUP BY canonical_id""")
            xc = {int(c): (float(a), float(b)) for c, a, b in cur.fetchall()}
        cur.execute("SELECT to_regclass('meets_tf')")
        if cur.fetchone()[0] is not None:
            cur.execute("""SELECT location_id, avg(gps_lat), avg(gps_long)
                           FROM meets_tf
                           WHERE location_id IS NOT NULL
                             AND gps_lat IS NOT NULL AND gps_long IS NOT NULL
                           GROUP BY location_id""")
            tf = {int(c): (float(a), float(b)) for c, a, b in cur.fetchall()}
    for i, key in enumerate(course_keys):
        k = str(key)
        got = None
        if k.startswith("XC:"):
            body = k[3:].split(":d", 1)[0]
            if body.isdigit():
                got = xc.get(int(body))
        elif k.startswith("TF:loc:"):
            body = k[7:].split(":", 1)[0]
            if body.isdigit():
                got = tf.get(int(body))
        if got is not None:
            lat[i], lon[i] = got
    return lat, lon


# ------------------------------------------------------------------ #
# STREAMING LOAD
# ------------------------------------------------------------------ #

# streamResults
# Purpose:   yield BATCHES of rows for one sport, never materialising the whole
#            corpus. results_tf alone is ~121M rows; a Python list of tuples that
#            size is well over 100GB. A server-side cursor plus batching keeps
#            peak client memory at one batch.
# Arguments: sport -- 'XC' | 'TF'; min_time, max_time; batch -- rows per yield.
# Output:    generator of list[tuple], columns per COLUMNS.
# Memory:    one batch of Python tuples at a time. 200k x 10 columns is roughly
#            150-200MB transient; packResults converts each batch to typed numpy
#            (24 bytes/row) and lets the tuples go. 500k was fine on 64GB but
#            there is nothing to buy with it -- the server-side cursor is already
#            doing the streaming, and fetchmany() only controls Python-side churn.
# ⚠ THE BAND HERE IS GARBAGE-ONLY NOW, AND HAS TO BE. It filters
#   normalized_time, and since per-pool anchors that number means something
#   different in every pool: an ms row normalises to 3200 and an hs row to
#   5000, so the same seconds are a different performance. The old 600 floor
#   was an hs floor applied to everyone.
#
#   Measured cost: Luke Surface, corroborated grade 8, ran six 2025 XC races
#   normalising to 552-596 -- every one under 600, all six silently excluded,
#   and he vanished from the middle-school board entirely. 287 rows corpus-wide
#   sit under the old floor and every one of them is grade 6, 7 or 8. Grades
#   9-12: zero. The floor was deleting the fastest middle schoolers and
#   nobody else.
#
# ★ THE REAL BAND MOVED TO packResults, where the pool is known and it can be
#   expressed as a PACE rather than a time. See _POOL_PACE_BAND there.
def streamResults(sport: str, min_time: float = 200.0, max_time: float = 6000.0,
                  batch: int = 200_000):
    # Materialise the twin keys BEFORE opening the stream. This is one scan and
    # one hash aggregate; it replaces a correlated SubPlan that the planner would
    # otherwise re-execute once per row (see CROSS-SOURCE DEDUP above).
    table = {"XC": "results", "TF": "results_tf"}[sport]
    tw, _n_tw = _buildTwinKeys(table, sport)
    tw = tw or ""
    _probeResultTwin()

    sql = {"XC": _xcQuery, "TF": _tfQuery}[sport](min_time, max_time, tw)
    total = 0
    with getConn() as conn:
        cur = conn.cursor(name=f"speed_ratings_{sport.lower()}")
        cur.itersize = batch
        cur.execute(sql)
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            total += len(rows)
            print(f"[db] {sport}: streamed {total:,}")
            yield rows
        cur.close()
    if tw:
        _dropTwinKeys(sport)       # scratch; rebuilt on the next run
    print(f"[db] {sport}: {total:,} rows total")


# ------------------------------------------------------------------ #
# COPY HELPERS  —  the fast, low-memory path into Postgres
# ------------------------------------------------------------------ #

# _chunks
# Purpose:   yield an iterable in fixed-size lists, WITHOUT materialising it.
# Syntax:    islice(it, size) takes the next `size` items lazily; when it comes
#            back empty the source is exhausted. This is what lets
#            saveResultSpeedRatings accept a 30M-element GENERATOR and never hold
#            more than `size` tuples at once.
def _chunks(iterable, size):
    it = iter(iterable)
    while True:
        block = list(islice(it, size))
        if not block:
            return
        yield block


# _field
# Purpose:   render ONE value for COPY's TEXT format.
# Detail:    THE BUG THIS FIXES: the old code was `map(str, r)`, and str(None)
#            is the literal "None". Postgres then rejects it against an integer
#            column -- "invalid input syntax for type integer: None" -- and the
#            whole save aborts. COPY's NULL marker is the two characters \N.
#            Nothing passed None until canonical_id, which is NULL on every TF
#            row, so this was latent rather than harmless.
#
#            Only None is handled here. Free-text values are still escaped by
#            the CALLERS via _escape, and escaping again here would double every
#            backslash a course name contains.
def _field(v):
    return "\\N" if v is None else str(v)


# _copyChunk
# Purpose:   push one chunk into `table` with a single COPY.
# Syntax:    COPY's default TEXT format is tab-separated fields, newline-ended
#            rows, no quoting. `"".join(generator)` builds the payload in ONE
#            allocation; repeated `s += ...` would be quadratic.
#            copy_expert reads from a file object, hence io.StringIO.
# Why COPY:  it bypasses the SQL parser and planner. execute_values builds a
#            multi-row VALUES literal that Postgres must parse every statement.
def _copyChunk(cur, table, columns, rows):
    payload = "".join("\t".join(_field(v) for v in r) + "\n" for r in rows)
    cur.copy_expert(f"COPY {table} ({', '.join(columns)}) FROM STDIN",
                    io.StringIO(payload))


# _copyInto
# Purpose:   stream any iterable of tuples into `table`, chunk by chunk.
# Output:    total rows copied.
def _copyInto(cur, table, columns, rows, chunk=200_000):
    total = 0
    for block in _chunks(rows, chunk):
        _copyChunk(cur, table, columns, block)
        total += len(block)
    return total


# _escape
# Purpose:   make a text value safe for COPY's TEXT format.
# Detail:    course names are free text and CAN contain a tab or a backslash.
#            COPY treats backslash as an escape introducer and tab as the field
#            separator, so both must be doubled/encoded. NULL is the literal \N.
def _escape(v):
    if v is None:
        return "\\N"
    return (str(v).replace("\\", "\\\\")
                  .replace("\t", "\\t")
                  .replace("\n", "\\n")
                  .replace("\r", "\\r"))


# ------------------------------------------------------------------ #
# SAVE — VENUE (COURSE) DIFFICULTIES
# ------------------------------------------------------------------ #

# _splitVenueKey
# Purpose:   one engine venue key -> (display_name, canonical_id, distance_m).
# Arguments: key   -- "XC:1234:d4800", "XC:name:Some Course:d5000",
#                     or "TF:loc:44:out".
#            names -- {canonical_id_text: canonical_name} from loadCanonicalNames.
# Output:    (course_name_for_db, canonical_id_or_None, distance_m_or_None)
#
# Detail:    partition(":") splits on the FIRST colon only, so "TF:loc:44:out"
#            yields ("TF", "loc:44:out") with the inner colons intact.
#
#            rpartition(":d") splits on the LAST occurrence, which matters
#            because a course name can itself contain ":d" -- "name:Camp:dusk"
#            must split at the trailing distance tag, not inside the name.
#
#            An unrecognised shape passes through untouched rather than raising.
#            Keys written before this change have no ":d" suffix and must still
#            load; a save that crashes on old data is worse than one that
#            carries it forward.
def _splitVenueKey(key: str, names: dict):
    # an era-split cell ('<key>@e<k>', --era-years) publishes under its bare
    # key: the page looks venues up by the bare key, and a race date belongs
    # to one era anyway
    key = key.partition("@e")[0]
    sport, _, rest = key.partition(":")

    if sport != "XC":
        return (key, None, None)                      # TF, untouched

    venuePart, tag, distPart = rest.rpartition(":d")

    if not tag:                                       # pre-split key, no ":d"
        venuePart, distPart = rest, ""

    distance = int(distPart) if distPart.isdigit() else None

    if venuePart.isdigit():
        # An id with no name means course_canonical was rebuilt without the
        # engine re-running. Fall back to the raw id rather than a blank name --
        # visibly odd beats silently empty.
        return (f"XC:{names.get(venuePart, venuePart)}", int(venuePart), distance)

    if venuePart.startswith("name:"):
        return (f"XC:{venuePart[5:]}", None, distance)

    return (f"XC:{venuePart}", None, distance)


# saveCourseDifficulties
# Purpose:   scoped replace of course_difficulties.
# Arguments: difficulties -- {venue_key: {"difficulty","n_results","n_athletes"}}
#            sports       -- the tuple actually solved this run, e.g. ("TF",).
# Output:    none.
# Detail:    SCOPED REPLACE, not TRUNCATE. Keys are namespaced ("XC:...",
#            "TF:loc:44:out"), so deleting only this run's namespace lets
#            `--sport TF` run without wiping every XC venue.
#
#            course_name still holds the namespaced DISPLAY name, unchanged from
#            before, so app.py and conversions.py keep working. canonical_id is
#            the engine's real key and is what distinguishes the 18 different
#            venues that are all called "Central Park".
#
#            ⚠ Two rows CAN now share a course_name. That is not a bug -- it is
#            the collision that was previously being hidden by fusing them into
#            one difficulty. A consumer selecting by name alone gets several
#            correct rows where it used to get one wrong one.
def saveCourseDifficulties(difficulties: dict, sports=("XC", "TF")) -> None:
    today = date.today().isoformat()
    names = loadCanonicalNames()

    rows = []
    for key, d in difficulties.items():
        displayName, canonicalId, distanceM = _splitVenueKey(key, names)
        rows.append((_escape(displayName), canonicalId, distanceM,
                     d["difficulty"], d["n_results"], d["n_athletes"], today))

    with getConn() as conn:
        with conn.cursor() as cur:
            for sp in sports:
                cur.execute(
                    "DELETE FROM course_difficulties WHERE course_name LIKE %s",
                    (f"{sp}:%",))
                print(f"[db] cleared {cur.rowcount:,} {sp} venues")

            n = _copyInto(cur, "course_difficulties",
                          ("course_name", "canonical_id", "distance_m",
                           "difficulty", "n_results", "n_athletes",
                           "last_updated"), rows)
        conn.commit()

    n_canon = sum(1 for r in rows if r[1] is not None)
    print(f"[db] saved {n:,} venue difficulties ({n_canon:,} canonical-keyed)")


# ------------------------------------------------------------------ #
# SAVE — ATHLETE RATINGS
# ------------------------------------------------------------------ #

# saveAthleteRatings
# Purpose:   scoped replace of athlete_ratings.
# Arguments: ratings -- {(person_id, pool): {"speed_rating", "n_races"}}
#            sports  -- the sports this run solved.
#
# TWO POOL SHAPES, and the delete has to match the one being written:
#   per-sport run  -> pools carry a sport suffix ("hs_m|XC"), so deleting
#                     '%|XC' clears exactly that sport and leaves TF alone.
#   MERGED run     -> pools are BARE ("hs_m"), because one ability spans both
#                     sports. '%|XC' then matches NOTHING, the old rows survive,
#                     and the COPY dies on the primary key.
#
# The shape is read off the ratings themselves rather than inferred from
# `sports`, because `sports` is ("XC","TF") in BOTH cases and cannot tell them
# apart.
def saveAthleteRatings(ratings: dict, sports=("XC", "TF")) -> None:
    today = date.today().isoformat()

    # Generator over dict keys: short-circuits on the first bare pool, and
    # never materialises 4.4M rows just to answer a yes/no question.
    merged = any("|" not in pool for _, pool in ratings.keys())

    rows = ((pid, _escape(pool), d["speed_rating"], d["n_races"], today)
            for (pid, pool), d in ratings.items())

    with getConn() as conn:
        with conn.cursor() as cur:
            if merged:
                # NOT LIKE '%|%' is the complement of the per-sport pattern, so
                # a merged run clears every bare-pool row and leaves any
                # suffixed leftovers from an older per-sport run untouched.
                cur.execute("DELETE FROM athlete_ratings "
                            "WHERE pool NOT LIKE '%|%'")
                print(f"[db] cleared {cur.rowcount:,} merged athlete ratings")
            else:
                for sp in sports:
                    cur.execute("DELETE FROM athlete_ratings WHERE pool LIKE %s",
                                (f"%|{sp}",))
                    print(f"[db] cleared {cur.rowcount:,} {sp} athlete ratings")

            n = _copyInto(cur, "athlete_ratings",
                          ("athlete_id", "pool", "speed_rating",
                           "n_races", "last_updated"), rows)
        conn.commit()
    print(f"[db] saved {n:,} athlete ratings")


# ------------------------------------------------------------------ #
# SAVE — PER-RESULT SPEED RATINGS
# ------------------------------------------------------------------ #

_SR_STAGING = "sr_staging"


# _asPairs
# Purpose:   accept EITHER an iterable of (result_id, rating) pairs OR a tuple of
#            two numpy arrays, and yield pairs LAZILY in both cases.
# Why:       speed_ratings.buildResultRatings used to end with
#                return list(zip(rid.tolist(), val.tolist()))
#            which allocates 30M Python ints, 30M Python floats, and 30M tuples
#            in a list -- roughly 3.7GB, all of it thrown away one row later.
#            Returning the two numpy arrays instead costs 30M*12 = 360MB, and
#            this generator walks them without ever building the list.
# Syntax:    `zip(a.tolist(), b.tolist())` would defeat the point; `map` over the
#            arrays' .item() keeps one Python object alive at a time. numpy's
#            iterator yields scalars, and str() of a np.float32 round-trips fine
#            for COPY.
def _asPairs(pairs):
    """(result_id, rating, pool) triples from arrays (rid, val) or (rid,
    val, pool), or an iterable of pairs / triples. The pool is the one the
    rating was computed in (issue 171: written on the row as rating_pool
    so no page has to guess it); None where the caller has none."""
    if isinstance(pairs, tuple) and hasattr(pairs[0], "size"):
        rid, val = pairs[0], pairs[1]
        pool = pairs[2] if len(pairs) > 2 else None
        for i in range(rid.size):
            yield int(rid[i]), float(val[i]), (None if pool is None else pool[i])
        return
    for row in pairs:
        yield (row[0], row[1], row[2] if len(row) > 2 else None)


# _stagingFor
# Purpose:   one scratch table per sport, so a `both` run cannot collide.
def _stagingFor(sport: str) -> str:
    return f"{_SR_STAGING}_{sport.lower()}"


# _fillStaging
# Purpose:   COPY every (result_id, speed_rating) pair into a scratch table.
# Detail:    UNLOGGED skips the WAL entirely. On a server crash Postgres simply
#            TRUNCATEs the table, which costs nothing: the engine is rerunnable.
#            That is the whole safety argument -- it is scratch.
#            NO INDEX yet: we never look a row up in it here. mergeColumn hash-
#            joins it once, and the update path indexes it just before use.
# Output:    rows copied. `pairs` may be a GENERATOR of 30M items; we never
#            build a list of it.
def _fillStaging(conn, sport, pairs):
    table = _stagingFor(sport)
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(f"CREATE UNLOGGED TABLE {table} "
                    f"(result_id bigint NOT NULL, val real NOT NULL, pool text)")
        n = _copyInto(cur, table, ("result_id", "val", "pool"), _asPairs(pairs))
    conn.commit()
    print(f"[db] {sport}: COPYed {n:,} ratings into {table}")
    return table, n


# _updateFromStaging
# Purpose:   the ORIGINAL write path, kept as the reference and the fallback.
# Cost:      one new heap tuple per row, plus a random-page insertion into EVERY
#            index (fillfactor=100 forbids a HOT update). ~150-290us/row here,
#            i.e. 75-100 minutes for 30M rows. Correct, slow, and it touches
#            nothing but the one column.
# _tuneForBulkBuild
# Purpose:   widen the memory Postgres may use for the heap rebuild and the
#            index builds that follow it.
# Arguments: conn -- the connection mergeColumn will run on.
# Output:    None; two session GUCs set.
#
# WHY. mergeColumn rebuilds the heap and then recreates every index serially.
# On results_tf that is 519s of CREATE TABLE plus ~570s of index builds -- about
# 18 minutes, and by far the largest single block in a full run.
#
#   maintenance_work_mem defaults to 64MB. A B-tree build on 27.6M rows needs
#   far more than that, so it spills the sort to disk and does an external merge.
#   Giving it 4GB usually keeps the sort in memory.
#
#   max_parallel_maintenance_workers defaults to 2 and applies PER INDEX BUILD.
#
# ⚠ SESSION-SCOPED, NOT SET LOCAL. `SET LOCAL` would be reverted at the next
#   COMMIT, and mergeColumn commits between steps -- so LOCAL would silently do
#   nothing. This lives for the connection and dies with it.
#
# ⚠ MEMORY IS PER WORKER. 4GB x (workers + 1) is the worst case for ONE index
#   build. On a 64GB box that is fine; on a smaller one, lower both numbers.
def _tuneForBulkBuild(conn):
    with conn.cursor() as cur:
        # index-build sort memory; default 64MB spills 27.6M rows to disk
        cur.execute("SET maintenance_work_mem = '4GB'")
        # parallel B-tree builds, PER INDEX
        cur.execute("SET max_parallel_maintenance_workers = 4")
        # ⚠ WITHOUT THIS THE LINE ABOVE DOES ALMOST NOTHING. Maintenance workers
        # are drawn from the same shared pool as query workers; if the pool has
        # no free slots the index build silently runs serially. That is why the
        # first attempt at this tuning moved the TF index phase only 570s->525s.
        # (max_worker_processes is the hard ceiling and needs a server restart --
        # if this still does not help, that is the reason.)
        cur.execute("SET max_parallel_workers = 8")
        # the CTAS joins results against sr_staging; more work_mem keeps the
        # hash table in RAM instead of batching it to disk.
        cur.execute("SET work_mem = '512MB'")
        # ★ THE BIG ONE FOR THE HEAP REBUILD. CREATE TABLE AS is WAL-bound, and
        # synchronous_commit=off stops each commit waiting on an fsync. Safe
        # here BECAUSE THIS IS A DERIVED TABLE: a crash loses at most the last
        # few transactions, and the remedy is to re-run the engine, which is
        # what you would do anyway. Do NOT copy this to scraper writes.
        cur.execute("SET synchronous_commit = off")
    conn.commit()
    print("[db] bulk-build tuning: maintenance_work_mem=4GB, "
          "parallel_maintenance=4, max_parallel_workers=8, "
          "work_mem=512MB, synchronous_commit=off")


def _updateFromStaging(conn, sport, table, staging):
    with conn.cursor() as cur:
        _t = time.time()
        cur.execute(f"CREATE INDEX ON {staging} (result_id)")
        cur.execute(f"ANALYZE {staging}")
        print(f"[db] {sport}: staged; bulk UPDATE (this is the slow path)...")
        cur.execute(f"""
            UPDATE {table}
               SET speed_rating = t.val,
                   rating_pool  = COALESCE(t.pool, {table}.rating_pool)
              FROM {staging} t
             WHERE {table}.result_id = t.result_id
        """)
        print(f"[db] {sport}: UPDATE took {time.time() - _t:.0f}s")
    conn.commit()


# saveResultSpeedRatings
# Purpose:   one speed_rating per result, into the right table for the sport.
# Arguments: sport -- 'XC' | 'TF'
#            pairs -- ITERABLE (may be a generator) of (result_id, rating)
#            mode  -- "rebuild" (default, ~10x faster) or "update"
# Output:    none.
#
# mode="rebuild" rebuilds the heap in one sequential pass and sorts each index
#   once, instead of scattering ~30M random index insertions. Measured on this
#   database: 122 seconds for a 39M-row table, against 75-100 minutes for the
#   UPDATE. It leaves <table>_old behind as an undo copy and REQUIRES exclusive
#   access -- rows written by the launcher mid-rebuild land in <table>_old and
#   are LOST. Run with the launcher off.
#
# mode="update" is the old path: slower, but concurrent-safe and it leaves no
#   _old table. Use it if the launcher must keep running.
# ★ ONE GO-LIVE AT A TIME (2026-09-04). run9's go-live died 5.4 h in with
#   `relation "sr_staging_xc" does not exist` between its own COPY and its
#   own ANALYZE on the same connection. Nothing in this process drops that
#   table there; a SECOND go-live's _fillStaging does (DROP TABLE IF EXISTS
#   sr_staging_xc), and two pipelines were running. A session-level advisory
#   lock, taken before the staging table is touched and held for the whole
#   write, makes the second one fail at once with a sentence instead of
#   killing the first one hours later.
_GOLIVE_LOCK_KEY = 0x5C0_11FE                  # arbitrary, project-wide


def _takeGoLiveLock(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_GOLIVE_LOCK_KEY,))
        got = bool(cur.fetchone()[0])
    conn.commit()
    if not got:
        raise RuntimeError(
            "another process holds the go-live lock: a second run_joint "
            "--golive (or apply_tilt / fill) is writing speed ratings right "
            "now. Let it finish, or kill it, then rerun this step.")


def _ensureRatingPool(conn, table):
    """rating_pool on the results table, added only when missing.

    ! ADD COLUMN IF NOT EXISTS STILL TAKES ACCESS EXCLUSIVE, even when the
      column exists, and that lock queues behind every open read and puts
      every later read behind itself (the site's SELECTs, which gunicorn
      then kills at 60 s, leaving orphaned backends: 2026-09-07). So: look
      first, and when the column really is missing, wait at most five
      seconds for the lock, a few times, rather than forever."""
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM information_schema.columns
                       WHERE table_name = %s AND column_name = 'rating_pool'""", (table,))
        if cur.fetchone():
            conn.rollback()
            return
    for attempt in range(6):
        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '5s'")
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS rating_pool text")
            conn.commit()
            return
        except psycopg2.errors.LockNotAvailable:
            conn.rollback()
            print(f"[db] {table}: ADD COLUMN rating_pool waited on a lock (try {attempt + 1}/6)", flush=True)
            time.sleep(10)
    raise RuntimeError(f"{table}: could not add rating_pool (lock held by another session)")


def saveResultSpeedRatings(sport: str, pairs, mode: str = "rebuild") -> None:
    table = {"XC": "results", "TF": "results_tf"}[sport]
    with getConn() as conn:
        _takeGoLiveLock(conn)
        staging, n = _fillStaging(conn, sport, pairs)
        if n == 0:
            print(f"[db] {sport}: nothing to save")
            return
        if mode == "rebuild":
            _tuneForBulkBuild(conn)

            # ! CLEAR THE PREVIOUS RUN'S LEFTOVER BEFORE MERGING, NOT ONLY
            #   AFTER. The drop below runs after a SUCCESSFUL merge, so a run
            #   that dies anywhere later -- a crash, a killed process, a failure
            #   in apply_tilt -- leaves <table>_old behind, and the next
            #   --golive refuses to start because mergeColumn checks for it.
            #
            #   That check is right: a swap onto an existing _old fails AFTER
            #   the rebuild, wasting the fifteen minutes it just spent. But the
            #   leftover is this function's own output from last time, so this
            #   is the one place that knows it is safe to discard.
            #
            # ⚠ SAFE BECAUSE THE PREVIOUS MERGE COMPLETED. mergeColumn only
            #   renames the live table aside once the new one is built, so an
            #   existing _old means last run's swap SUCCEEDED and `table` holds
            #   its result. A half-finished merge leaves no _old at all.
            with conn.cursor() as cur:
                cur.execute(f"SELECT to_regclass('{table}_old')")
                if cur.fetchone()[0] is not None:
                    cur.execute(f"DROP TABLE IF EXISTS {table}_old")
                    print(f"[db] {sport}: dropped a stale {table}_old "
                          f"from an earlier run")
            conn.commit()
            # preserve_unmatched=False: this is a FULL RECOMPUTE. The engine saw
            # every row and DECLINED to rate some (out-of-band normalized_time,
            # no venue, pool with no mean). Those must read NULL, not a value an
            # older engine wrote. COALESCE left 4,216 fossils behind -- rows with
            # speed_rating 7528 on a normalized_time of 20.6 seconds.
            _ensureRatingPool(conn, table)
            mergeColumn(conn, table, "speed_rating", staging,
                        extra=(("rating_pool", "pool"),),
                        key="result_id", val="val", preserve_unmatched=False)
        else:
            _ensureRatingPool(conn, table)
            _updateFromStaging(conn, sport, table, staging)
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {staging}")

            # mergeColumn leaves <table>_old behind as an undo copy and never
            # removes it. On a 34M-row table that is several GB per run, and it
            # accumulates across runs.
            #
            # Dropped only AFTER the swap succeeded. If mergeColumn had failed,
            # the original table would still be sitting under its _old name and
            # this line would never be reached.
            #
            # ⚠ THIS REMOVES THE ONLY ROLLBACK. If a run produces bad ratings
            #   the fix is to re-run the engine, not to restore a copy.
            if mode == "rebuild":
                cur.execute(f"DROP TABLE IF EXISTS {table}_old")
                print(f"[db] {sport}: dropped {table}_old")
        conn.commit()
    print(f"[db] {sport}: saved {n:,} result speed ratings ({mode})")