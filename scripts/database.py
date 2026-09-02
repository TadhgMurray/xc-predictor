# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Database
# Date: 6/6/2026
# File Title: database.py
# Purpose: Creates and manages the PostgreSQL database that stores scraped
#          meet results. Replaces the old SQLite version. All functions
#          use the same interface as before so scraper code doesn't change.

import psycopg2
import psycopg2.extras
import psycopg2.pool
import time
import random
from contextlib import contextmanager
from config import PG_CONFIG
import datetime

# ─────────────────────────────────────────────────────────────────────────────
# Connection Pool
# ─────────────────────────────────────────────────────────────────────────────

# Why a pool?
# Without a pool, every save function opened a TCP connection to Cloud SQL,
# ran one query, then closed it. For a 100-result meet that's ~200 connection
# handshakes. At even 20ms each that's 4 seconds of pure overhead per meet.
# With a pool we open connections ONCE at startup and reuse them forever.

# MIN_CONN: connections opened immediately at startup.
# MAX_CONN: hard ceiling. If all connections are in use and another is
#           requested, psycopg2 raises PoolError instead of hanging forever.
#           Sized for 25 VM sessions + overhead. Easy to bump when scaling.
 
MIN_CONN = 5
MAX_CONN = 250

# We add connect_timeout to PG_CONFIG here so it applies to every connection
# the pool opens. Without it, if Cloud SQL is slow to accept, psycopg2
# waits forever and the session hangs silently.
# We build a new dict so the original PG_CONFIG in config.py isn't mutated.
_PG_CONFIG_WITH_TIMEOUT = {**PG_CONFIG, "connect_timeout": 10}

# Module-level pool — created once when database.py is first imported.
# Every script that does `from database import ...` shares this same pool.
# None until initPool() is called explicitly. This is just a 
# list of open connections. It tracks who is using a connection.
_pool = None

# initPool
# Purpose: Creates the connection pool. Must be called once at startup.
#          Safe to call multiple times - if pool exists it won't do anything.
# Arguments: None.
# Output: None.
def initPool():
    global _pool

    # Giuard: don't create a second pool if one already exists.
    if _pool is not None:
        return
    
    try:
        # Creates a pool with at least MIN_CONN connections and less than MAX_CONN
        # connections. Other functions can take/open the connections as they please
        # within these bounds. PG_CONFIG_WITH_TIMEOUT is where the pool should
        # connect to and how long it should try before giving up connecting. Opens
        # 5 connections in the pool instantly.
        _pool = psycopg2.pool.ThreadedConnectionPool(
            MIN_CONN,
            MAX_CONN,
            **_PG_CONFIG_WITH_TIMEOUT
        )
        print(f"[DB] Connection pool created ({MIN_CONN}-{MAX_CONN} connections)")
    except Exception as e:
        # Pool creation failure is fatal — nothing can run without it.
        raise RuntimeError(f"[DB] Failed to create connection pool: {e}")

# closePool
# Purpose: Closes all connections in the pool.
# Arguments: None.
# Purpose: None.
def closePool():
    global _pool

    # Closes all connections in the pool.
    if _pool is not None:
        _pool.closeall()
        _pool = None
        print("[DB] Connection pool closed")

# ─────────────────────────────────────────────────────────────────────────────
# Connection context manager
# ─────────────────────────────────────────────────────────────────────────────

# getConn
# Purpose: Context manager that checks out a connection from the pool,
#          yields it to the caller, then returns it to the pool when the
#          with block exits — even if an exception was raised inside.
#
# Usage:
#   with getConn() as conn:
#       saveMeet(conn, meet_info, div)
#       saveResult(conn, result, meet_info)
#   # conn is automatically returned to pool here
#
# Why a context manager?
# Without it, every caller would need to remember to call putconn() after
# use. If an exception fires mid-meet and the caller forgets, the connection
# leaks. With a context manager, the finally block guarantees return.
#
# Why not just call pool.getconn() directly?
# Because then the caller owns the return responsibility. One missed putconn
# leaks a connection slot. When MAX_CONN slots are all leaked, the pool
# raises PoolError and the scraper dies. The context manager makes leaks
# impossible by design.
#
# Arguments: None.
# Output: Yields a psycopg2 connection object.
@contextmanager
def getConn():

    # Lazy init - if initPool() wasn't called explicitlym init now.
    if _pool is None:
        initPool()
    conn = _pool.getconn()

    try:
        # yield hands the connection to the caller's with block.
        yield conn
    except Exception:
        # If the caller's code raised an exception, roll back any
        # uncommitted writes so the connection is clean when returned.
        conn.rollback()
        raise
    # finally always does something no matter what.
    finally:
        try:
            conn.rollback()      # ensure no open/aborted txn rides back to the pool
        except Exception:
            pass
        # Always return the connection — whether the block succeeded or failed.
        # putconn() does NOT close the connection, it just marks it as
        # available for the next caller to grab.
        _pool.putconn(conn)

# ─────────────────────────────────────────────────────────────────────────────
# Write helpers
# ─────────────────────────────────────────────────────────────────────────────

# executeWithRetry
# Purpose: Wraps a single cursor.execute() call with retry logic. If Postgres
#          says the connection is busy or locked, waits and retries.
#          Same interface as the SQLite version so callers don't change.
# Arguments:
#           cursor: psycopg2 cursor to execute on.
#           sql: SQL string with %s placeholders.
#           params: tuple of values to substitute.
#           retries: how many times to retry on failure.
# Output: None. Raises on exhausted retries.
def executeWithRetry(cursor, sql: str, params: tuple, retries: int = 5):
    for attempt in range(retries):
        try:
            cursor.execute(sql, params)
            return
        except psycopg2.OperationalError as e:
            if attempt < retries - 1:
                # Wait a bit longer each retry — same backoff as SQLite version.
                time.sleep(0.05 + (attempt * 0.05) + random.uniform(0, 0.05))
            else:
                raise

# _flag
# Purpose: API boolean-ish -> INTEGER flag column (is_relay/is_indoor style).
#          None stays None so a MISSING key reads as "unknown", not a fake 0.
# Arguments: value: result.get("SomeFlag").
# Output: 1 truthy / 0 present-falsy / None absent.
def _flag(value):
    return None if value is None else (1 if value else 0)
 
# _videoCount
# Purpose: Pulls the video count out of a result's MediaCount, which is {} or
#          {"videos": N}. Defensive so a missing/oddly-typed value -> 0.
# Arguments: media_count: result.get("MediaCount").
# Output: integer video count (0 if none).
def _videoCount(media_count):
    if isinstance(media_count, dict):
        return media_count.get("videos", 0)
    return 0

# _toInt
# Purpose: Best-effort convert an API place/score value to int for an INTEGER
#          column, SALVAGING the leading number rather than discarding it. The
#          place arrives as a string and can carry a trailing marker: a tie
#          ("1T"), a display dot ("1."), etc. We keep the numeric part ("1T" ->
#          1, "1." -> 1) and only fall back to None when there's no leading
#          number at all ("DNF", "-", "", None) — so a tied athlete still gets
#          their real place instead of NULL. (The tie MARKER itself isn't
#          preserved here — that was the Option-B path we chose not to take.)
# Arguments:
#           value:
#               Raw value off a result dict — an int, a str like "1" / "1T", or
#               None.
# Output:
#           The leading integer when one exists; None otherwise.
def _toInt(value):
    if value is None:
        return None
    # Already an int (or int-like) — hand it straight back.
    if isinstance(value, int):
        return value
 
    s = str(value).strip()
    if not s:
        return None
    
    # Walk the leading characters, collecting digits until the first non-digit.
    # "1T" -> "1", "12." -> "12", "DNF" -> "" (no leading digit).
    lead = ""
    for ch in s:
        if ch.isdigit():
            lead += ch
        else:
            break
 
    return int(lead) if lead else None

# _toReal
# Purpose: Best-effort convert an API value to float for a REAL column. The
#          payload sends some numeric fields as strings, and crucially sends
#          EMPTY STRINGS ("") for missing values — "" cannot coerce to REAL and
#          crashes the whole execute_values batch (this is what killed meet
#          599825). Returns None for "", None, or any non-numeric value.
# Arguments:
#           value:
#               Raw value off a result dict — a float, an int, a numeric string
#               ("1.4"), an empty string, or None.
# Output:
#           float(value) when it parses; None otherwise.
def _toReal(value):
    if value is None:
        return None
    # Already numeric (and not a bool, which is an int subclass we don't want).
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None
    
# _resolveSchool
# Purpose: The single source of truth for "what school is this row?". Both the
#          athlete insert and the result insert must use this so their school
#          strings always agree (the composite FK depends on them matching).
#          Mirrors the result path's existing `SchoolName or TeamName`.
# Arguments:
#           obj:
#               a raw result dict OR a buildAthleteDict output — both expose
#               SchoolName (and results also TeamName).
# Output:
#           The resolved school string, or None if neither field is set.
def _resolveSchool(obj):
    return obj.get("SchoolName") or obj.get("TeamName")



# ─────────────────────────────────────────────────────────────────────────────
# Table creation
# ─────────────────────────────────────────────────────────────────────────────

# createTables()
# Purpose: Creates all tables and indexes if they don't already exist.
#          Safe to run multiple times.
# Arguments: None.
# Outputs: None.
def createTables():

    try:
        with getConn() as conn:
            cursor = conn.cursor()
            _createCoreTables(cursor)
            _createTFTables(cursor)
            _createRecoveryTable(cursor)
            _migrateLocationID(cursor)
            _migrateMeetQueueCompositeKey(cursor)
            _migrateMeetQueueAddSource(cursor)
            _migrateMeetExtrasAddSource(cursor)
            _createMeetsTFMetaTable(cursor)
            _createIndexes(cursor)
            conn.commit()
        print("[DB] Tables ready")
    except Exception as e:
        print(f"[DB] Failed to create tables: {e}")
 

# _createCoreTables
# Purpose: Create the XC tables: athletes, meets, results, meet_queue,
#          course_difficulties, athlete-Ratings.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output: None.
def _createCoreTables(cursor):

    # Athletes table — one row per athlete.
    # BIGINT instead of INTEGER because athletic.net athlete IDs
    # are large numbers that exceed SQLite's INTEGER range.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS athletes (
            athlete_id      BIGINT PRIMARY KEY,
            first_name      TEXT,
            last_name       TEXT,
            gender          TEXT,
            school          TEXT
        )
    """)

    # Meets table — one row per race division.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meets (
            div_id          BIGINT PRIMARY KEY,
            meet_id         BIGINT,
            meet_name       TEXT,
            course_name     TEXT,
            distance        REAL,
            gps_lat         REAL,
            gps_long        REAL,
            state           TEXT
        )
    """)

    # Results table — one row per individual XC performance.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS results (
            result_id       BIGINT PRIMARY KEY,
            athlete_id      BIGINT,
            meet_id         BIGINT,
            div_id          BIGINT,
            time_seconds    REAL,
            grade           TEXT,
            date            TEXT,
            normalized_time REAL DEFAULT NULL,
            speed_rating    REAL DEFAULT NULL
        )
    """)

    # Meet queue — tracks which meets have been scraped.
    # scraped: 0=unscraped, 1=done, 2=failed, 3=in-progress, 4=skipped(TF on Linux)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meet_queue (
            meet_id     BIGINT PRIMARY KEY,
            sport       TEXT,
            scraped     INTEGER DEFAULT 0
        )
    """)

    # Course difficulties — one row per unique course name.
    # difficulty is a multiplier — 1.03 means 3% slower than flat neutral.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS course_difficulties (
            course_name     TEXT PRIMARY KEY,
            difficulty      REAL,
            n_results       INTEGER,
            n_athletes      INTEGER,
            last_updated    TEXT
        )
    """)

    # Athlete ratings — one row per (athlete_id, pool) pair.
    # pool is "hs" or "college". Higher speed_rating = faster athlete.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS athlete_ratings (
            athlete_id      BIGINT,
            pool            TEXT,
            speed_rating    REAL,
            n_races         INTEGER,
            last_updated    TEXT,
            PRIMARY KEY (athlete_id, pool)
        )
    """)

    # Per-meet auxiliary JSONB blobs from GetAllResultsData.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meet_extras (
            meet_id          BIGINT,
            sport            TEXT,
            teams_json       JSONB,
            event_types_json JSONB,
            relay_legs_json  JSONB,
            PRIMARY KEY (meet_id, sport)
        )
    """)

# _createTFTables
# Purpose: Creates the Track & Field tables: meets_tf, results_tf,
#          tf_scraped_events.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output: None.
def _createTFTables(cursor):

    # TF meets table — one row per event/division combo.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meets_tf (
            div_id          BIGINT,
            meet_id         BIGINT,
            meet_name       TEXT,
            event_short     TEXT,
            event_id        BIGINT,
            distance_meters REAL,
            gps_lat         REAL,
            gps_long        REAL,
            state           TEXT,
            is_indoor       INTEGER DEFAULT 0,
            PRIMARY KEY (div_id, event_id)
        )
    """)

    # TF results table — one row per individual TF performance.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS results_tf (
            result_id       BIGINT PRIMARY KEY,
            athlete_id      BIGINT,
            meet_id         BIGINT,
            div_id          BIGINT,
            event_id        BIGINT,
            event_short     TEXT,
            time_seconds    REAL,
            grade           TEXT,
            date            TEXT,
            is_relay        INTEGER DEFAULT 0,
            normalized_time REAL DEFAULT NULL,
            speed_rating    REAL DEFAULT NULL
        )
    """)

    # TF scraped events — tracks which TF events were successfully scraped.
    # Used by recovery script to find missed events.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tf_scraped_events (
            meet_id         BIGINT,
            event_short     TEXT,
            div_id          BIGINT,
            scraped_at      TEXT,
            PRIMARY KEY (meet_id, event_short, div_id)
        )
    """)

# _createRecoveryTable
# Purpose: Creates tf_recovery_queue - the table that holds TF event/div
#          combos that failed during the main scrape and need to be retried.
#          One row per (meet_id, event_short, div_id) combo.
#          Status codes mirror meet_queue:
#            0 = unscraped (needs retry)
#            1 = done (successfully recovered)
#            2 = failed (gave up after retries)
#            3 = in-progress (currently being processed)
# Arguments:
#           cursor: open psycopg2 cursor from createTables.
# Output: None. Creates table if it doesn't exist.
def _createRecoveryTable(cursor):

    cursor.execute("""
        Create TABLE IF NOT EXISTS tf_recovery_queue (
            meet_id         BIGINT,
            event_short     TEXT,
            div_id          BIGINT,
            scraped         INTEGER DEFAULT 0,
            PRIMARY KEY(meet_id, event_short, div_id)
        )
    """)

# ─────────────────────────────────────────────────────────────────────────────
# meets_tf_meta — meet-level TF metadata (one row per meet_id)
# ─────────────────────────────────────────────────────────────────────────────
#
# WHY a separate table: meets_tf is per-(meet_id, div_id, event_id) — many rows
# per meet. The fields below are meet-CONSTANT (same venue/season/address for
# every event), so storing them on meets_tf would duplicate a big GoogleData
# blob across hundreds of rows. meets_tf_meta holds them once, keyed on meet_id.
# meets_tf is left UNTOUCHED (kept stable) — its per-event geometry columns stay
# as they are; this table is additive and read by the geocoding/feature layer
# later, not by the existing engine join.
 
 
# _createMeetsTFMetaTable
# Purpose: Create meets_tf_meta if missing. One row per TF meet_id. Idempotent
#          (CREATE TABLE IF NOT EXISTS), so safe to call from createTables every
#          run. source/id_system NOT NULL to match the rest of the schema's
#          provenance convention; the saver supplies 'anet'.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output:   None.
def _createMeetsTFMetaTable(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meets_tf_meta (
            meet_id         BIGINT PRIMARY KEY,
            meet_name       TEXT,
            meet_date       TEXT,
            season_id       INTEGER,
            gender          TEXT,
            template        TEXT,
            level_mask      INTEGER,
            finalized       INTEGER,
            has_results     INTEGER,
            location_id     BIGINT,
            venue_name      TEXT,
            address         TEXT,
            city            TEXT,
            state           TEXT,
            postal_code     TEXT,
            country         TEXT,
            gps_lat         REAL,
            gps_long        REAL,
            track_type      TEXT,
            track_length    REAL,
            is_indoor       INTEGER,
            altitude_meters REAL,
            google_data     JSONB,
            source          TEXT NOT NULL,
            id_system       TEXT NOT NULL,
            native_id       BIGINT
        )
    """)

# _migrateLocationID
# Purpose: Adds the location_id column to meets and meets_tf if it
#          doesn't already exist. ALTER TABLE ADD COLUMN IF NOT EXISTS
#          is idempotent — safe to run every startup.
#          location_id comes from meet_info["Location"]["ID"] — the
#          venue/facility ID, distinct from meet_id. Useful for
#          deduplicating courses scraped under different meet names.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output: None.
def _migrateLocationID(cursor):

    # ALTER TABLE ... ADD COLUMN takes an ACCESS EXCLUSIVE lock EVEN when it
    # no-ops (column already there). That exclusive lock queues behind any
    # in-flight reader of the table and then blocks everything behind it — which
    # is what wedged the launcher at startup. So check the catalog first (a
    # lock-free SELECT) and only ALTER when the column is genuinely missing.
    for table in ("meets", "meets_tf"):
        cursor.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
              AND column_name = 'location_id'
        """, (table,))
        if cursor.fetchone() is None:
            # Only reached on a fresh DB that predates the column.
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN location_id BIGINT")

# _createIndexes
# Purpose: Creates Indexes for fast lookups on the above tables.
#          Indexes speed up SELECT queries on the indexed column but slightly
#          slow down INSERTs - worth it for our read patterns.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output: None.
def _createIndexes(cursor):

    # Indexes for fast lookups.
    indexes = [
        # XC results — fast lookup by athlete or meet.
        ("idx_results_athlete_id",      "results",    "athlete_id"),
        ("idx_results_meet_id",         "results",    "meet_id"),
        ("idx_results_normalized_time", "results",    "normalized_time"),
        # TF results — same pattern.
        ("idx_results_tf_athlete_id",      "results_tf", "athlete_id"),
        ("idx_results_tf_meet_id",         "results_tf", "meet_id"),
        ("idx_results_tf_normalized_time", "results_tf", "normalized_time"),
        # Recovery Table for track events. Index on scraped so we can
        # get unscraped rows quickly.
        ("idx_tf_recovery_scraped", "tf_recovery_queue ", "scraped"),
    ]

    # Postgres syntax for IF NOT EXISTS on indexes is different from SQLite.
    for name, table, column in indexes:
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})
        """)


# ─────────────────────────────────────────────────────────────────────────────
# Save functions
# ─────────────────────────────────────────────────────────────────────────────
#
# IMPORTANT INTERFACE CHANGE from old database.py:
# Every save function now takes `conn` as its first argument.
# The caller is responsible for:
#   1. Getting a connection from the pool via `with getConn() as conn:`
#   2. Passing that conn to every save function for one meet
#   3. Calling conn.commit() once after all saves for that meet
#
# This means one meet = one connection checkout = one commit.
# Previously: one meet = ~200 connection open/close cycles.
#
# The caller (scrape_results.py) looks like:
#
#   with getConn() as conn:
#       saveMeet(conn, meet_info, div)
#       for athlete in athletes:
#           saveAthlete(conn, athlete)
#       for result in results:
#           saveResult(conn, result, meet_info)
#       conn.commit()
#
# ─────────────────────────────────────────────────────────────────────────────

# saveAthlete
# Purpose: Inserts one athlete into the athletes table.
#          ON CONFLICT DO NOTHING skips duplicates safely.
# Arguments:
#           conn: connection from the pool (passed in by caller).
#           athleteData: dict with keys AthleteID, FirstName, LastName,
#                        Gender, SchoolName.
# Output: None.
def saveAthlete(conn, athleteData: dict):

    # Guard — if no AthleteID, we can't insert a useful row.
    if not athleteData.get("AthleteID"):
        return

    cursor = conn.cursor()

    executeWithRetry(cursor, """
        INSERT INTO athletes (athlete_id, first_name, last_name, gender, school)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (athlete_id, school) DO NOTHING
    """, (
        athleteData.get("AthleteID"),
        athleteData.get("FirstName"),
        athleteData.get("LastName"),
        athleteData.get("Gender"),
        athleteData.get("SchoolName") or "Unknown"
    ))

# saveMeet
# Purpose: Upsert one XC meets row (one row per division / div_id). Now also
#          stores the division metadata unlocked by the 6/22 captures
#          (Division, LevelMask, CourseId) and is backfill-safe via ON
#          CONFLICT — re-scraping an existing div_id now refreshes its
#          enrichment fields instead of crashing on a duplicate-key error.
# Arguments:
#           conn:
#               Open DB connection (caller owns the transaction/commit).
#           meetData:
#               Meet-level dict from getMeetData — provides ID, Name, and the
#               nested Location{} (Name=venue, Lat, Long, State, ID=venue key).
#           divData:
#               One xcDivisions[] entry — provides IDMeetDiv (div_id/PK),
#               Meters (this division's distance), Division, LevelMask,
#               CourseId.
# Output:
#           None. Inserts or updates exactly one meets row.
def saveMeet(conn, meetData: dict, divData: dict):

    cursor = conn.cursor()
 
    # Location may be absent on malformed responses — default to {} so the
    # .get() calls below can't raise. (The original indexed Location["Name"]
    # directly, which would KeyError on a meet missing Location.)
    location = meetData.get("Location", {}) or {}
 
    executeWithRetry(cursor, """
        INSERT INTO meets (
            div_id,
            meet_id,
            meet_name,
            course_name,
            distance,
            gps_lat,
            gps_long,
            state,
            location_id,
            division,
            level_mask,
            course_id,
            source,
            id_system
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (div_id) DO UPDATE SET
            course_name = EXCLUDED.course_name,
            distance    = EXCLUDED.distance,
            gps_lat     = EXCLUDED.gps_lat,
            gps_long    = EXCLUDED.gps_long,
            state       = EXCLUDED.state,
            location_id = EXCLUDED.location_id,
            division    = EXCLUDED.division,
            level_mask  = EXCLUDED.level_mask,
            course_id   = EXCLUDED.course_id
    """, (
        divData["IDMeetDiv"],          # PK — must exist; bracket access on purpose
        meetData["ID"],
        meetData["Name"],
        location.get("Name"),          # course_name = venue name (6/22: course identity not in API)
        divData.get("Meters"),         # distance VARIES per division — never assume 5000
        location.get("Lat"),
        location.get("Long"),
        location.get("State"),
        location.get("ID"),            # location_id = stable venue key (Location.ID)
        divData.get("Division"),       # e.g. "Varsity", "Junior Varsity"
        divData.get("LevelMask"),      # 4=HS, 8=college, 12=both
        divData.get("CourseId"),       # usually 0; stored as-is in case it's ever populated
        "anet",                        # source     — NOT NULL
        "anet",                        # id_system  — NOT NULL
    ))

# saveResult
# Purpose: Inserts one XC result into the results table.
# Arguments:
#           conn: connection from the pool.
#           resultData: dict with result-level info from getMeetResults.
#           meetData: dict with meet-level info.
# Output: None.
def saveResult(conn, resultData: dict, meetData: dict, school: str = None):

    cursor = conn.cursor()

    meet_date_raw = meetData.get("MeetDate", "")
    meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""

    # school_source records WHERE school came from:
    #   'scraped'  - real per-result value, passed in by caller
    #   NULL       - no school passed (legacy call site, or
    #                 athlete had no resolvable school at all) —
    #                 marks this row as a future re-scrape target.
    school_source = "scraped" if school else None

    executeWithRetry(cursor, """
        INSERT INTO results (
            result_id,
            athlete_id,
            meet_id,
            div_id,
            time_seconds,
            grade,
            date,
            school,
            school_source
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        -- If result is re-scraped for school/school_source
        -- update them.
        ON CONFLICT (result_id) DO UPDATE SET
            school        = EXCLUDED.school,
            school_source = EXCLUDED.school_source,
            scraped_at    = now()
        WHERE results.school_source IS NULL
    """, (
        resultData.get("IDResult"),
        resultData.get("AthleteID"),
        meetData.get("ID"),
        resultData.get("IDDiv"),
        resultData.get("SortValue"),
        resultData.get("Grade", ""),
        meet_date,
        school,
        school_source
    ))

# saveMeetTF
# Purpose: Inserts one TF event/division row into meets_tf. Now also writes
#          division (competition tier / multi / para, from the flatEvents
#          wrapper) and level_mask (meet level: 4=HS, 8=college, 12=both).
#          ON CONFLICT (div_id, event_id) DO NOTHING — prelim and final share
#          that key, so one metadata row per event/div is correct.
# Arguments:
#           conn: connection from the pool.
#           meet_info: dict from getMeetDataTF (ID/Name/Location/LevelMask).
#           div_id: division ID.
#           event_id: numeric event ID.
#           event_short: event code string e.g. "1mile".
#           distance_meters: float, or None/-1 for field/failed events.
#           division: division name string, or None (per-div fallback path).
# Output: None.
def saveMeetTF(conn, meet_info: dict, div_id: int, event_id: int,
               event_short: str, distance_meters, division=None):

    cursor = conn.cursor()

    location = meet_info.get("Location", {})

    executeWithRetry(cursor, """
        INSERT INTO meets_tf (div_id, meet_id, meet_name, event_short,
                            event_id, distance_meters, gps_lat, gps_long,
                            state, is_indoor, location_id,
                            track_type, track_length, ustfccca_id,
                            division, level_mask,
                            source, id_system)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (meet_id, div_id, event_id) DO NOTHING
    """, (
        div_id,
        meet_info.get("ID"),
        meet_info.get("Name"),
        event_short,
        event_id,
        distance_meters,
        location.get("Lat"),
        location.get("Long"),
        location.get("State"),
        1 if location.get("Indoor") else 0,
        location.get("ID"),
        location.get("TrackType"),
        location.get("TrackLength"),
        meet_info.get("UstfcccaID"),
        division,
        meet_info.get("LevelMask"),
        "anet",                        # source     — NOT NULL
        "anet",                        # id_system  — NOT NULL
    ))

# saveResultTF
# Purpose: Inserts one TF result into results_tf.
# Arguments:
#           conn: connection from the pool.
#           result: dict from resultsTF array.
#           meet_info: dict from getMeetDataTF.
#           div_id: division ID.
#           event_id: numeric event ID.
#           event_short: event code string.
#           is_relay: 1 if relay, 0 if individual.
#           school: string of the school name this result was run by.
# Output: None.
def saveResultTF(conn, result: dict, meet_info: dict, div_id: int,
                event_id: int, event_short: str, is_relay: int, school: str = None):
    
    # Convert milliseconds to seconds. < 100000000 to filter out
    # sentinel values (DNS, DNF, DQ).
    sort_int = result.get("SortInt")
    if sort_int is not None and sort_int < 100000000:
        time_seconds = sort_int / 1000
    else:
        time_seconds = None

    # Gets the meet date including the time, then splits off
    # and saves the mm/dd/yy.
    meet_date_raw = meet_info.get("MeetDate", "")
    meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""

    cursor = conn.cursor()

    school_source = "scraped" if school else None

    executeWithRetry(cursor, """
        INSERT INTO results_tf (
            result_id,
            athlete_id,
            meet_id,
            div_id,
            event_id,
            event_short,
            time_seconds,
            grade,
            date,
            is_relay,
            school,
            school_source
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (result_id) DO UPDATE SET
            school        = EXCLUDED.school,
            school_source = EXCLUDED.school_source 
        WHERE results.school_source IS NULL
    """, (
        result.get("IDResult"),
        result.get("AthleteID"),
        meet_info.get("ID"),
        div_id,
        event_id,
        event_short,
        time_seconds,
        result.get("Grade", ""),
        meet_date,
        is_relay,
        school,
        school_source
    ))

# saveMeetQueue
# Purpose: Inserts a meet into the scraping queue.
# Arguments:
#           meet_id: athletic.net meet ID.
#           sport: "XC" or "TF".
# Output: None.
def saveMeetQueue(meet_id: int, sport: str):

    with getConn() as conn:
        cursor = conn.cursor()
        executeWithRetry(cursor, """
            INSERT INTO meet_queue (meet_id, sport)
            VALUES (%s, %s)
            ON CONFLICT (meet_id, sport, source) DO NOTHING
        """, (meet_id, sport))
        conn.commit()

# saveMeetTeams
# Purpose: Upserts one meet's raw teams[] roster (the school/conference
#          reference list that rides along in every GetResultsData3 response)
#          into meet_teams as JSONB. One row per (meet_id, sport); re-scraping
#          a meet refreshes that row instead of inserting a duplicate. Takes
#          the caller's already-open connection so the write lands inside the
#          same transaction as the rest of the meet's save (saveMeetTF, the
#          athlete/result bulks), matching saveMeetTF/saveResultsTFBulk's
#          conn-taking pattern — never opens its own connection.
# Arguments:
#           conn: open psycopg2 connection from the caller's getConn() block.
#                 Passed in (not created here) to stay atomic with the meet.
#           meet_id: athletic.net meet ID. First half of the composite PK.
#           sport: "xc" or "tf". Second half of the PK, so a single meet_id
#                  can hold a separate roster per sport.
#           teams_array: the Python list parsed from the response's "teams"
#                        key. Wrapped in psycopg2.extras.Json so it stores as
#                        real JSONB (queryable) rather than a stringified blob.
#                        Caller guarantees it's non-empty (see _saveTFMeet).
# Output: None. Side effect only — one upserted row in meet_teams.
#
# Storage note: this keeps the roster for EVERY meet that has one (~a dozen
# teams on a dual, hundreds on a state meet). If that ever bloats, gate the
# call in _saveTFMeet on size, e.g. `if teams_array and len(teams_array) > 30`,
# to keep only the big statewide rosters and drop the redundant per-meet ones.
def saveMeetTeams(conn, meet_id, sport, teams_array):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO meet_teams (meet_id, sport, teams_json)
        VALUES (%s, %s, %s)
        ON CONFLICT (meet_id, sport) DO UPDATE
            SET teams_json = EXCLUDED.teams_json
        """,
        (meet_id, sport, Json(teams_array)),    # Json(...) → real JSONB, not text
    )

# saveMeetTFMeta
# Purpose: Upsert the ONE meet-level row for a TF meet into meets_tf_meta, from
#          the GetMeetData(sport=tf) payload's meet dict. Reads meet.Location for
#          venue/address/geometry and the meet-level scalars (season/gender/etc).
#          Conn-taking (runs inside the caller's transaction). ON CONFLICT
#          refreshes everything so a re-scrape updates in place.
# Arguments:
#           conn:      open connection from the caller's getConn() block.
#           meet_info: the `meet` dict from getMeetDataTF (carries ID, Name,
#                      SeasonID, Gender, Template, LevelMask, Finalized,
#                      HasResults, MeetDate, and the nested Location{}).
# Output:   None. One upserted row in meets_tf_meta.
def saveMeetTFMeta(conn, meet_info: dict):
 
    cursor = conn.cursor()
 
    # Location may be absent/None on malformed responses — default to {} so the
    # .get() calls can't raise (same guard saveMeet uses).
    location = meet_info.get("Location", {}) or {}
 
    # MeetDate as YYYY-MM-DD (strip the ISO time half).
    meet_date_raw = meet_info.get("MeetDate", "") or ""
    meet_date = meet_date_raw.split("T")[0] if meet_date_raw else None
 
    # Finalized is a timestamp string when finalized, null otherwise -> 1/0 flag.
    finalized = 1 if meet_info.get("Finalized") else 0
 
    # GoogleData is a nested dict (the embedded Places geocode). Wrap in Json so
    # it stores as real JSONB; _cleanJson strips any NULs. None -> SQL NULL.
    google_data = location.get("GoogleData")
    google_json = (psycopg2.extras.Json(_cleanJson(google_data))
                   if google_data is not None else None)
 
    executeWithRetry(cursor, """
        INSERT INTO meets_tf_meta (
            meet_id, meet_name, meet_date, season_id, gender, template,
            level_mask, finalized, has_results,
            location_id, venue_name, address, city, state, postal_code, country,
            gps_lat, gps_long, track_type, track_length, is_indoor,
            altitude_meters, google_data,
            source, id_system, native_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (meet_id) DO UPDATE SET
            meet_name      = EXCLUDED.meet_name,
            meet_date      = EXCLUDED.meet_date,
            season_id      = EXCLUDED.season_id,
            gender         = EXCLUDED.gender,
            template       = EXCLUDED.template,
            level_mask     = EXCLUDED.level_mask,
            finalized      = EXCLUDED.finalized,
            has_results    = EXCLUDED.has_results,
            location_id    = EXCLUDED.location_id,
            venue_name     = EXCLUDED.venue_name,
            address        = EXCLUDED.address,
            city           = EXCLUDED.city,
            state          = EXCLUDED.state,
            postal_code    = EXCLUDED.postal_code,
            country        = EXCLUDED.country,
            gps_lat        = EXCLUDED.gps_lat,
            gps_long       = EXCLUDED.gps_long,
            track_type     = EXCLUDED.track_type,
            track_length   = EXCLUDED.track_length,
            is_indoor      = EXCLUDED.is_indoor,
            google_data    = EXCLUDED.google_data
            -- altitude_meters intentionally NOT refreshed: it's filled by the
            -- elevation backfill, not this scrape, so don't null it back out.
    """, (
        meet_info.get("ID"),
        meet_info.get("Name"),
        meet_date,
        meet_info.get("SeasonID"),
        meet_info.get("Gender"),
        meet_info.get("Template"),
        meet_info.get("LevelMask"),
        finalized,
        meet_info.get("HasResults"),
        location.get("ID"),
        location.get("Name"),
        location.get("Address"),
        location.get("City"),
        location.get("State"),
        location.get("PostalCode"),
        location.get("Country"),
        location.get("Lat"),
        location.get("Long"),
        location.get("TrackType"),
        location.get("TrackLength"),
        1 if location.get("Indoor") else 0,
        None,                              # altitude_meters — elevation backfill
        google_json,
        "anet",                            # source     — NOT NULL
        "anet",                            # id_system  — NOT NULL
        meet_info.get("ID"),               # native_id  — matches migration (=meet_id)
    ))

# ─────────────────────────────────────────────────────────────────────────────
# Bulk save helpers
# ─────────────────────────────────────────────────────────────────────────────
#
# These take a list of rows and insert them all in ONE query using
# execute_values. Much faster than calling saveAthlete/saveResult in a loop
# when you have hundreds of rows ready to go.
# Used by the collect-then-save pattern in scrape_results.py.

# saveAthletesBulk
# Purpose: Inserts a list of athlete dics in one query.
# Arguments:
#           conn: connection from the pool.
#           athletes: list of athlete dicts (same format as saveAthlete).
# Output: None.
def saveAthletesBulk(conn, athletes: list):

    if not athletes:
        return
    
    # Builds a list of tuples, one per athlete, for execute_values
    rows = []
    for a in athletes:
        if not a.get("AthleteID"):
            continue
        row = (
            a.get("AthleteID"),
            a.get("FirstName"),
            a.get("LastName"),
            a.get("Gender"),
            _resolveSchool(a) or "Unknown",
            "anet",    # source
            "anet",    # id_system
            a.get("AthleteID"),   # person_id: seeded = athlete_id AT INSERT
        )
        cleaned = []
        for v in row:
            cleaned.append(_clean(v))
        rows.append(tuple(cleaned))


    if not rows:
        return
    cursor = conn.cursor()

    # execute_values sends all rows in one round trip to Postgres
    # %s in the template is filled by execute_)values based on the order
    # of the tuple in the list. Stops duplicates with ON CONFLICT of athlete
    # id DO NOTHING.
    psycopg2.extras.execute_values(cursor, """
        INSERT INTO athletes (athlete_id, first_name, last_name, gender, school,
                              source, id_system, person_id)
        VALUES %s
        ON CONFLICT (athlete_id, school) DO NOTHING
    """, rows)

# saveResultsBulk
# Purpose: Bulk-upsert XC results, now capturing the full per-result set the
#          6/22 console work surfaced (place, score, exhibition, official,
#          team_id, is_pr, is_sr, has_splits, video_count, age_grade) in
#          addition to the existing fields.
#
#          BEHAVIOR CHANGE (ON CONFLICT): previously this only updated
#          school/school_source on conflict, and ONLY where school_source was
#          still NULL. That guard blocked enrichment backfill — an
#          already-schooled row could never receive its new capture fields.
#          Now, on conflict we refresh ALL capture columns plus time_seconds
#          (a re-scrape's SortValue repairs the historic corrupt MM:SS-as-
#          seconds times), and protect school with COALESCE so a re-scrape can
#          never null out a good school value.
# Arguments:
#           conn:
#               Open DB connection (caller owns the transaction/commit).
#           results:
#               List of (resultData, meetData, school) tuples, where
#               resultData is the raw resultsXC[] dict — every capture field
#               below is read straight off it.
# Output:
#           None. Inserts/updates one row per result.
# ★ THE NON-FINISH, PRESERVED (issue 59). Athletic.net writes one sentinel
#   SortValue for every kind of non-finish, so DNF, DNS and DQ were the same
#   999999 by the time anything read them. The feed's own text carries the
#   letters; this keeps them in a `status` column beside the sentinel time,
#   so the page can say which. Only the vocabulary below is stored -- a
#   result string that is a real time is not a status.
_STATUS_VOCAB = {"DNF", "DNS", "DQ", "DSQ", "NT", "SCR", "WD", "FS", "NH",
                 "ND", "NM", "DNP"}


def _statusOf(resultData: dict):
    for key in ("Result", "ShortCode", "Status", "ResultText"):
        v = resultData.get(key)
        if v is None:
            continue
        t = str(v).strip().upper().replace(".", "")
        if t in _STATUS_VOCAB:
            return "DQ" if t == "DSQ" else t
    return None


_RESULTS_STATUS_READY = False


def _ensureResultsStatus(conn):
    """results.status, added once per process. IF NOT EXISTS, so a database
    that already has it is untouched."""
    global _RESULTS_STATUS_READY
    if _RESULTS_STATUS_READY:
        return
    cur = conn.cursor()
    cur.execute("ALTER TABLE results ADD COLUMN IF NOT EXISTS status TEXT")
    _RESULTS_STATUS_READY = True


def saveResultsBulk(conn, results: list):
    _ensureResultsStatus(conn)

    if not results:
        return
 
    # One timestamp for the whole batch, captured before the loop so every
    # row in this batch shares it (used for future local -> Fly incremental sync).
    scraped_at = datetime.datetime.now(datetime.timezone.utc)
 
    rows = []
    # 4-tuple now: div_id rides alongside the result (set by the two XC collectors
    # from the division's IDMeetDiv). This is the correct, non-NULL division id;
    # it REPLACES the old resultData.get("IDDiv") read below, which always
    # returned None for XC (wrong key, and absent on result rows) and is the whole
    # reason 6M XC rows saved with div_id NULL.
    for i, (resultData, meetData, school, div_id) in enumerate(results):

        # Meet date as YYYY-MM-DD (strip the time half of the ISO string).
        meet_date_raw = meetData.get("MeetDate", "")
        meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""
 
        # school_source records WHERE school came from:
        #   'scraped' - real per-result value from this scrape
        #   NULL      - no school resolved -> marks this row as a future
        #               re-scrape target.
        school_source = "scraped" if school else None
 
        rows.append((
            resultData.get("IDResult"),
            resultData.get("AthleteID"),
            meetData.get("ID"),
            div_id,                                            # from the 4-tuple (division's IDMeetDiv); replaces broken IDDiv read
            resultData.get("SortValue"),                       # XC time = SortValue (NOT SortInt)
            resultData.get("Grade", ""),
            meet_date,
            school,
            school_source,
            scraped_at,
            # ---- 6/22 capture columns ----
            resultData.get("Place"),
            resultData.get("Score"),                           # 0 = non-scoring / displaced
            _flag(resultData.get("Exhibition")),
            _flag(resultData.get("Official")),
            resultData.get("TeamID"),
            _flag(resultData.get("isPr")),
            _flag(resultData.get("isSr")),
            _flag(resultData.get("hasSplitsSeries")),
            _videoCount(resultData.get("MediaCount")),
            resultData.get("AgeGrade"),
            "anet",    # source
            "anet",    # id_system
            resultData.get("AthleteID"),   # person_id: seeded = athlete_id AT INSERT
            _statusOf(resultData),         # the letters behind a sentinel time (issue 59)
        ))
    
    # Insert every athlete this batch references, under the SAME school the
    # result carries. An athlete can have multiple schools across years (e.g.
    # middle school then high school) - each (athlete_id, school) is its own
    # row, so this adds the ones not already present. Built from `rows` so the
    # school is byte-identical to what the result writes.
    seen = set()
    athlete_rows = []
    for row in rows:
        aid, school = row[1], row[7]
        if aid is None:
            continue
        pair = (aid, school)
        if pair in seen:
            continue
        seen.add(pair)
        athlete_rows.append((aid, "", "", "", school, "anet", "anet", aid))  # last aid = person_id seed

    if athlete_rows:
        acur = conn.cursor()
        psycopg2.extras.execute_values(acur, """
            INSERT INTO athletes (athlete_id, first_name, last_name, gender, school,
                              source, id_system, person_id)
            VALUES %s
            ON CONFLICT (athlete_id, school) DO NOTHING
        """, athlete_rows)

    # ON CONFLICT refreshes capture columns + time_seconds on every conflict
    # (enables backfill). school/school_source use COALESCE(EXCLUDED, existing)
    # so a re-scrape that somehow lacks a school can't overwrite a good one.
    # "results" (the table name, not an alias) in the SET targets is required
    # by Postgres; EXCLUDED refers to the row we tried to insert.
    cursor = conn.cursor()
    try:
        psycopg2.extras.execute_values(cursor, """
            INSERT INTO results (
                result_id, athlete_id, meet_id, div_id,
                time_seconds, grade, date, school, school_source, scraped_at,
                place, score, exhibition, official, team_id,
                is_pr, is_sr, has_splits, video_count, age_grade,
                source, id_system, person_id, status
            )
            VALUES %s
            ON CONFLICT (result_id) DO UPDATE SET
            athlete_id    = EXCLUDED.athlete_id,
            status        = COALESCE(EXCLUDED.status, results.status),
            -- fill person_id only if missing; NEVER overwrite one dedup wrote
            person_id     = COALESCE(results.person_id, EXCLUDED.person_id),
            -- fill div_id only if missing; a re-scrape of an EXISTING orphan row
            -- (div_id NULL, keyed by result_id) hits this UPDATE path, not the
            -- INSERT, so without this line the collector/saver div_id fix could
            -- never reach already-stored rows. COALESCE (not bare EXCLUDED)
            -- mirrors person_id above: backfill a NULL, but never clobber a good
            -- stored div_id with a re-scrape that happened to yield NULL.
            div_id        = COALESCE(results.div_id, EXCLUDED.div_id),
            time_seconds  = EXCLUDED.time_seconds,
            place         = EXCLUDED.place,
            score         = EXCLUDED.score,
            exhibition    = EXCLUDED.exhibition,
            official      = EXCLUDED.official,
            team_id       = EXCLUDED.team_id,
            is_pr         = EXCLUDED.is_pr,
            is_sr         = EXCLUDED.is_sr,
            has_splits    = EXCLUDED.has_splits,
            video_count   = EXCLUDED.video_count,
            age_grade     = EXCLUDED.age_grade,
            school        = COALESCE(EXCLUDED.school, results.school),
            school_source = COALESCE(EXCLUDED.school_source, results.school_source),
            scraped_at    = EXCLUDED.scraped_at
        """, rows)
    except Exception as e:
        print(f"[FK-FAIL] {e}", flush=True)
        for r in rows:
            if r[1] == 23636800:
                print(f"[HIM] full row: {r!r}", flush=True)
        print(f"[HIM-COUNT] {sum(1 for r in rows if r[1]==23636800)} rows with him", flush=True)
        raise

# _clean
# Purpose: Strip NUL (0x00) bytes from a string before it goes to Postgres -
#          Postgres text columns reject 0x00 even though Python allows it, and a
#          single embedded NUL from anet's data crashes the whole batch insert.
#          Pass-through for non-strings (None, ints, etc.).
# Arguments:
#           value: any field value headed for a text column.
# Output:   the value with NULs removed if it's a string, else unchanged.
def _clean(value):
    if isinstance(value, str):
        return value.replace("\x00", "")
    return value

# saveResultsTFBulk
# Purpose: Bulk-inserts a list of TF result rows in one query. Field events
#          store their mark (Result) with time_seconds NULL; running events
#          parse time from SortInt. Writes the full per-result capture set
#          (exhibition/official/wind/place/score/round/heat/has_splits/
#          is_field/mark/team_id/event_type_id/video_count/age_grade/pr/sr).
#          ON CONFLICT only refreshes school/school_source/scraped_at on
#          still-legacy rows; the capture columns populate on INSERT only.
# Arguments:
#           conn: connection from the pool.
#           results: list of 8-tuples
#                    (result, meet_info, div_id, event_id, event_short,
#                     is_relay, school, is_field).
# Output: None.
def saveResultsTFBulk(conn, results: list):

    if not results:
        return
 
    scraped_at = datetime.datetime.now(datetime.timezone.utc)
 
    rows = []
    for result, meet_info, div_id, event_id, event_short, is_relay, school, is_field in results:
 
        # Field events carry a mark, not a time: keep the mark string, leave
        # time_seconds NULL (the engine filters on is_field so these never enter
        # the time-based ratings). Running events parse time from SortInt/1000.
        if is_field:
            time_seconds = None
            mark = result.get("Result")
        else:
            sort_int = result.get("SortInt")
            time_seconds = sort_int / 1000 if (sort_int is not None and sort_int < 100000000) else None
            # a running non-finish keeps its letters in `mark`, the text
            # column the page already reads (issue 59); a real time keeps
            # mark NULL as before
            mark = _statusOf(result) if time_seconds is None else None
 
        # Meet date as YYYY-MM-DD.
        meet_date_raw = meet_info.get("MeetDate", "")
        meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""
 
        # school_source: 'scraped' if we resolved a school, else NULL (re-scrape target).
        school_source = "scraped" if school else None

        row = (                          # <-- this assignment must exist
            result.get("IDResult"),
            None if is_relay else result.get("AthleteID"),
            meet_info.get("ID"),
            div_id,
            event_id,
            event_short,
            time_seconds,
            result.get("Grade", ""),
            meet_date,
            is_relay,
            school,
            school_source,
            scraped_at,
            _flag(result.get("Exhibition")),
            _flag(result.get("Official")),
            _toReal(result.get("Wind")),
            _toInt(result.get("Place")),
            _toInt(result.get("Score")),
            result.get("Round"),
            result.get("Heat"),
            _flag(result.get("hasSplitsSeries")),
            is_field,
            mark,
            result.get("TeamID"),
            result.get("EventTypeID"),
            _videoCount(result.get("MediaCount")),
            _toReal(result.get("AgeGrade")),
            result.get("pr"),
            result.get("sr"),
            "anet",    # source
            "anet",    # id_system
            None if is_relay else result.get("AthleteID"),   # person_id: seed at insert; relays have no person
        )


        # For each row cleans it and appends it to rows.
        cleaned = []
        for v in row:
            cleaned.append(_clean(v))
        rows.append(tuple(cleaned))

    deduped = {}
    for row in rows:
        deduped[row[0]] = row
    rows = list(deduped.values())
 
    cursor = conn.cursor()
    psycopg2.extras.execute_values(cursor, """
        INSERT INTO results_tf (result_id, athlete_id, meet_id, div_id,
                            event_id, event_short, time_seconds, grade,
                            date, is_relay, school, school_source, scraped_at,
                            exhibition, official, wind, place, score, round,
                            heat, has_splits, is_field, mark, team_id,
                            event_type_id, video_count, age_grade, pr, sr,
                            source, id_system, person_id)
        VALUES %s
        ON CONFLICT (result_id) DO UPDATE SET
            athlete_id    = EXCLUDED.athlete_id,
            -- fill person_id only if missing; NEVER overwrite one dedup wrote
            person_id     = COALESCE(results_tf.person_id, EXCLUDED.person_id),
            time_seconds  = EXCLUDED.time_seconds,
            exhibition    = EXCLUDED.exhibition,
            official      = EXCLUDED.official,
            wind          = EXCLUDED.wind,
            place         = EXCLUDED.place,
            score         = EXCLUDED.score,
            round         = EXCLUDED.round,
            heat          = EXCLUDED.heat,
            has_splits    = EXCLUDED.has_splits,
            is_field      = EXCLUDED.is_field,
            mark          = EXCLUDED.mark,
            team_id       = EXCLUDED.team_id,
            event_type_id = EXCLUDED.event_type_id,
            video_count   = EXCLUDED.video_count,
            age_grade     = EXCLUDED.age_grade,
            pr            = EXCLUDED.pr,
            sr            = EXCLUDED.sr,
            school        = COALESCE(EXCLUDED.school, results_tf.school),
            school_source = COALESCE(EXCLUDED.school_source, results_tf.school_source),
            scraped_at    = EXCLUDED.scraped_at
    """, rows)

# _cleanJson
# Purpose: Recursively strip NUL (0x00) from every string inside a JSON-able
#          structure (dict/list/str), since psycopg2's Json() adapter will
#          serialize an embedded NUL straight into the JSONB value and Postgres
#          rejects it. Non-strings pass through.
# Arguments:
#           obj: a dict, list, str, or scalar headed for a JSONB column.
# Output:   the same shape with all NULs removed from string values/keys.
def _cleanJson(obj):
    if isinstance(obj, str):
        return obj.replace("\x00", "")
    if isinstance(obj, list):
        out = []
        for item in obj:
            out.append(_cleanJson(item))
        return out
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            out[_cleanJson(key)] = _cleanJson(value)
        return out
    return obj


# saveMeetExtras
# Purpose: Upserts (update + insert) one meet's auxiliary JSONB blobs. One row
#          per (meet_id, sport). Conn-taking, runs inside the caller's
#          transaction.
#
#          CHANGED: added the team_scores_array param + the team_scores_json
#          column. XC passes its raw teamScores[] blob here; TF leaves it None
#          (TF reconstructs team scores from per-result Score + TeamID).
# Arguments:
#           conn:
#               Open connection from the caller's getConn() block.
#           meet_id:
#               athletic.net meet ID (first half of the PK).
#           sport:
#               "xc" or "tf" (second half of the PK).
#           teams_array:
#               teams[] roster list, or None.
#           event_types_array:
#               eventTypes[] implement-spec catalog list, or None. Structurally
#               None for XC (no implements).
#           relay_legs_array:
#               relayLegs[] composition list, or None. Structurally None for XC
#               (no relays).
#           team_scores_array:
#               teamScores[] list, or None. Populated for XC; None for TF.
#               (Each array is Json-wrapped -> JSONB; None -> SQL NULL.)
# Output:
#           None. One upserted row in meet_extras.
def saveMeetExtras(conn, meet_id, sport, teams_array, event_types_array,
                   relay_legs_array, team_scores_array=None):
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO meet_extras (
            meet_id, sport,
            teams_json, event_types_json, relay_legs_json, team_scores_json,
            source
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (meet_id, sport, source) DO UPDATE
            SET teams_json       = EXCLUDED.teams_json,
                event_types_json = EXCLUDED.event_types_json,
                relay_legs_json  = EXCLUDED.relay_legs_json,
                team_scores_json = EXCLUDED.team_scores_json

        """,
        (
            meet_id,
            sport,
            psycopg2.extras.Json(_cleanJson(teams_array))       if teams_array       is not None else None,
            psycopg2.extras.Json(_cleanJson(event_types_array)) if event_types_array is not None else None,
            psycopg2.extras.Json(_cleanJson(relay_legs_array))  if relay_legs_array  is not None else None,
            psycopg2.extras.Json(_cleanJson(team_scores_array)) if team_scores_array is not None else None,
            "anet",
        ),
    )

# ─────────────────────────────────────────────────────────────────────────────
# Recovery Tracking
# ─────────────────────────────────────────────────────────────────────────────


# logScrapedEventsTFBulk
# Purpose: Records all successfully scraped TF event/div combos in one query.
#          Called after saveResultsTFBulk so we only log events whose results
#          actually made it to the DB.
# Arguments:
#           events: list of (meet_id, event_short, div_id) tuples.
# Output: None. Inserts rows into tf_scraped_events, skips duplicates.
def logScrapedEventsTFBulk(events: list):

    if not events:
        return

    now = datetime.datetime.utcnow().isoformat()[:19]

    # Add scraped_at timestamp to every tuple.
    rows = [(meet_id, event_short, div_id, now) for meet_id, event_short, div_id in events]

    # The with triggers the context manager. It checks a connection out
    # of the pool, binds it to the name conn, and returns it to the pool.
    with getConn() as conn:
        cursor = conn.cursor()
        psycopg2.extras.execute_values(cursor, """
            INSERT INTO tf_scraped_events (meet_id, event_short, div_id, scraped_at)
            VALUES %s
            ON CONFLICT (meet_id, event_short, div_id) DO NOTHING
        """, rows)
        conn.commit()

# populateRecoveryQueue
# Purpose: Bulk-inserts failed event/div combos into tf_recovery_queue.
#          Safe to call multiple times.
# Arguments:
#           rows: list of (meet_id, event_short, div_id) tuples.
# Output: None. Returns count of newly inserted rows for logging.
def populateRecoveryQueue(rows: list) -> int:
 
    if not rows:
        return 0
 
    with getConn() as conn:
        cursor = conn.cursor()
        
        # Uses execute_values for bulk insert.
        psycopg2.extras.execute_values(cursor, """
            INSERT INTO tf_recovery_queue (meet_id, event_short, div_id, scraped)
            VALUES %s
            ON CONFLICT (meet_id, event_short, div_id) DO NOTHING
        """, [(meet_id, event_short, div_id, 0)
              for meet_id, event_short, div_id in rows])
 
        # rowcount tells us how many rows were actually inserted
        # (vs skipped by ON CONFLICT). Useful for logging.
        inserted = cursor.rowcount
        conn.commit()
 
    return inserted

# ─────────────────────────────────────────────────────────────────────────────
# Queue management
# ─────────────────────────────────────────────────────────────────────────────


# getBatchUnscrapedMeets
# Purpose: Claims up to batch_size meet_id/sport pairs from the shared
#          meet_queue pool and marks them status=3 (in-progress) so no
#          other session claims the same work. Replaces the old session-
#          stride sweep — sessions now pull from one shared pool instead
#          of each owning a disjoint range.
#
#          Unlike the old version, this does NOT generate a candidate
#          range or LEFT JOIN against it — meet_queue is assumed to
#          already contain a status=0 row for every (meet_id, sport) in
#          range, via the one-time backfill script. That's what makes
#          this a single fast atomic claim instead of an expensive
#          range-generation query on every call.
#
# Arguments:
#           batch_size: how many (meet_id, sport) pairs to claim.
# Output: dict mapping meet_id (int) → list of sports still needing
#         work, e.g. {101: ["XC", "TF"], 5601: ["TF"]}.
#         Empty dict means no status=0 work remains — scraping is done.
def getBatchUnscrapedMeets(batch_size: int) -> dict:
    
    with getConn() as conn:
        cursor = conn.cursor()
        rows = _claimMeetBatch(cursor, batch_size)
        conn.commit()  # commit the claim so other sessions see status=3 immediately
 
    return _buildMeetSportDict(rows)

# _claimMeetBatch
# Purpose: Atomically claims up to batch_size rows from meet_queue that
#          are still status=0 (queued, never touched), flipping them to
#          status=3 (in-progress) in the same statement that selects
#          them. This is the standard "claim a job from a queue table"
#          pattern.
#
#          The inner SELECT ... FOR UPDATE SKIP LOCKED finds status=0
#          rows and locks them. SKIP LOCKED means: if another session's
#          claim query already has a lock on a row (mid-claim right now),
#          this query skips it instead of waiting — so two sessions
#          calling this at the same instant partition the available rows
#          between them instead of racing or blocking each other.
#
#          The outer UPDATE then flips exactly those locked rows to
#          status=3 and returns them — one round trip, no separate
#          SELECT-then-UPDATE pair, so there's no gap between "found it"
#          and "claimed it" for another session to slip into.
#
# Arguments:
#           cursor: open DB cursor.
#           batch_size: max number of (meet_id, sport) rows to claim.
# Output: list of (meet_id, sport) tuples actually claimed this call.
#         May be shorter than batch_size if fewer than batch_size rows
#         are left at status=0 (i.e. scraping is nearly done).
def _claimMeetBatch(cursor, batch_size):

    cursor.execute(
        """
        UPDATE meet_queue
        SET scraped = 3
        WHERE (meet_id, sport, source) IN (
            SELECT meet_id, sport, source
            FROM meet_queue
            WHERE scraped = 0 AND source = 'anet'      -- <-- scope to anet
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        )
        RETURNING meet_id, sport
        """,
        (batch_size,),
    )
    return cursor.fetchall()

# _buildMeetSportDict
# Purpose: Reshapes flat (meet_id, sport) rows into a dict grouping all
#          sports needing work per meet_id. Pure Python — no DB call.
#          e.g. rows=[(101,"XC"),(101,"TF"),(5601,"TF")] →
#               {101: ["XC", "TF"], 5601: ["TF"]}
#
# Arguments:
#           rows: list of (meet_id, sport) tuples, as returned by
#                 _claimMeetBatch.
# Output: dict mapping meet_id (int) → list of sport strings.
def _buildMeetSportDict(rows):
    result = {}

    for meet_id, sport in rows:
        # setdefault(meet_id, []) creates an empty list the first time
        # we see a meet_id so we can have up to two sports as the value.
        result.setdefault(meet_id, []).append(sport)

    return result

# _migrateMeetQueueCompositeKey
# Purpose: Changes meet_queue's primary key from (meet_id) to
#          (meet_id, sport) — a meet_id can now have separate rows for
#          XC and TF, since athletic.net meets can have both. Existing
#          rows are unaffected (each meet_id had exactly one sport
#          before, so (meet_id, sport) is still unique for them).
#          Idempotent — checks if the old single-column PK still exists
#          before doing anything.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output: None.
def _migrateMeetQueueCompositeKey(cursor):

     # Finds the current primary key constraint name on meet_queue.
    cursor.execute("""
        -- Finds the constraint on table (conrelid) meet_queue where
        -- the constraint type (contype) is primary ('p').
        -- Names this constraint conname. pg_constraint
        -- is Postgres's built-in system catalogue table
        -- (tables, constraints, indexes, etc).
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'meet_queue'::regclass and contype = 'p'
    """)
    row = cursor.fetchone()

    # If the PK is already the 3-column (meet_id, sport, source) form, the
    # source migration has run — do NOT try to reassert the 2-col PK (it now
    # fails on legitimate anet+tfrrs duplicate (meet_id, sport) pairs).
    cursor.execute("""
        SELECT array_length(conkey, 1) FROM pg_constraint
        WHERE conrelid = 'meet_queue'::regclass AND contype = 'p'
    """)
    existing = cursor.fetchone()
    if existing is not None and existing[0] == 3:
        return
 
    if row is None:
        # No primary key at all, add the composite one
        cursor.execute("""
            ALTER TABLE meet_queue ADD PRIMARY KEY (meet_id, sport)
        """)
        return
 
    constraint_name = row[0]
 
    # If it's already composite (2 columns), nothing to do.
    cursor.execute("""
        -- conkey is a column in pg_constraint that stores which columns
        -- this constraint covers, returned as an array of column
        -- position numbers, e.g. {1, 2, 3} if it covers columns 1, 2, and 3.
        -- The , 1 means check only the 1st dimension. This gets how many
        -- columns the primary key takes up.
        SELECT array_length(conkey, 1) FROM pg_constraint
        WHERE conname = %s
    """, (constraint_name,))
    n_cols = cursor.fetchone()[0]
 
    if n_cols == 2:
        return  # already migrated
 
    # Drop the old single-column PK and add the composite one.
    cursor.execute(f"ALTER TABLE meet_queue DROP CONSTRAINT {constraint_name}")
    cursor.execute("ALTER TABLE meet_queue ADD PRIMARY KEY (meet_id, sport)")

# getQueueStatus
# Purpose: Looks up the scraped status for one (meet_id, sport) pair.
# Arguments:
#           meet_id: athletic.net meet ID.
#           sport: "XC" or "TF".
# Output: Integer scraped status (0=unscraped, 1=done, 2=failed) if a
#         row exists for this (meet_id, sport), or None if no row
#         exists at all — meaning we've never attempted this combo.
def getQueueStatus(meet_id: int, sport: str):

    with getConn() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT scraped FROM meet_queue
            WHERE meet_id = %s AND sport = %s
        """, (meet_id, sport))

        # fetchone() returns either a one-element tuple like (1,)
        # or None if no row matched the WHERE clause.
        row = cursor.fetchone()

    # row[0] unpacks the scraped value out of the tuple.
    # If row is None (no match), we return None to the caller —
    # this is the "never attempted" signal.
    return row[0] if row is not None else None

# markScraped
# Purpose: Updates a meet's scraped status in the queue.
# Arguments:
#           meet_id: meet to update.
#           sport: "XC" or "TF" — which sport's row to write.
#           status: 1=done, 2=failed, 0=reset, 3=in-progress, 4=skipped
# Output: None.
def markScraped(meet_id: int, sport: str, status: int = 1):

    # The with triggers the context manager. It checks a connection out
    # of the pool, binds it to the name conn, and returns it to the pool.
    with getConn() as conn:
        cursor = conn.cursor()
        executeWithRetry(cursor, """
            INSERT INTO meet_queue (meet_id, sport, scraped, source)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (meet_id, sport, source)
            DO UPDATE SET scraped = EXCLUDED.scraped
        """, (meet_id, sport, status, "anet"))
        conn.commit()

# resetInProgress
# Purpose: Resets any meets stuck in-progress (scraped=3) back to
#          unscraped (scraped=0).
# Arguments: None.
# Output: None.
def resetInProgress():
    
    # The with triggers the context manager. It checks a connection out
    # of the pool, binds it to the name conn, and returns it to the pool.
    with getConn() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE meet_queue SET scraped = 0 WHERE scraped = 2 AND source = 'anet'")
        count = cursor.rowcount
        conn.commit()
    print(f"[DB] Reset {count} in-progress meets to unscraped")


# getUnscrapedRecoveryEvents
# Purpose: Atomically fetches and locks a batch of unscraped recovery events.
#          Same UPDATE...RETURNING pattern as getUnscrapedMeets — marks rows
#          as in-progress (scraped=3) and returns them in one query so two
#          sessions can never grab the same event.
# Arguments:
#           limit: max rows to return. Default 200 — recovery events are
#                  slower than meets since each needs a JWT fetch.
# Output: List of (meet_id, event_short, div_id) tuples.
def getUnscrapedRecoveryEvents(limit: int = 200) -> list[tuple[int, str, int]]:
 
    with getConn() as conn:
        cursor = conn.cursor()
 
        # UPDATE...RETURNING atomically marks rows in-progress and
        # returns them. No other session can grab the same rows because
        # Postgres row-locks them during the UPDATE.
        cursor.execute("""
            UPDATE tf_recovery_queue SET scraped = 3
            -- Select these values where they exist and are unscraped.
            WHERE (meet_id, event_short, div_id) IN (
                SELECT meet_id, event_short, div_id
                FROM tf_recovery_queue
                Where scraped = 0
                Limit %s
            )
            -- This means we get back what we locked to the table in one query.
            RETURNING meet_id, event_short, div_id
        """, (limit,))
 
        rows = cursor.fetchall()
        conn.commit()
 
    return rows
 
 
# markRecoveryEventScraped
# Purpose: Updates the scraped status of one event in tf_recovery_queue.
# Arguments:
#           meet_id: athletic.net meet ID.
#           event_short: event code e.g. "100m", "1mile".
#           div_id: division ID.
#           status: 1=done, 2=failed.
# Output: None.
def markRecoveryEventScraped(meet_id: int, event_short: str,
                              div_id: int, status: int):
 
    with getConn() as conn:
        cursor = conn.cursor()
        executeWithRetry(cursor, """
            UPDATE tf_recovery_queue
            SET scraped = %s
            WHERE meet_id = %s AND event_short = %s AND div_id = %s
        """, (status, meet_id, event_short, div_id))
        conn.commit()

# getLegacySchoolMeets
# Purpose: Finds every meet_id that has at least one result with
#          school_source IS NULL — i.e. scraped before the
#          per-result school fix. Feed this list into the
#          existing scrapeMeetBySport(page, meet_id, sport, label)
#          to re-scrape and backfill school/school_source via the
#          ON CONFLICT ... DO UPDATE ... WHERE school_source IS NULL
#          clause in saveResult/saveResultTF above (which only
#          touches still-legacy rows, so partial progress is safe
#          to re-run).
# Arguments:
#           conn: connection from the pool.
#           sport: "xc" or "tf" — which table to check.
# Output: list of meet_ids (ints) needing re-scrape for this sport.
def getLegacySchoolMeets(conn, sport: str) -> list[int]:
 
    table = "results" if sport == "xc" else "results_tf"
 
    cursor = conn.cursor()
 
    # DISTINCT — many results share a meet_id; we only need each
    # meet once for the re-scrape list.
    cursor.execute(f"""
        SELECT DISTINCT meet_id
        FROM {table}
        WHERE school_source IS NULL
    """)
 
    # fetchall() returns a list of single-element tuples, e.g.
    # [(101,), (102,), ...] — the [0] pulls the int out of each.
    return [row[0] for row in cursor.fetchall()]

# ─────────────────────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────────────────────

# countRows
# Purpose: Prints row counts for main tables. Used in final summary.
# Arguments: None.
# Output: None.
def countRows():

    with getConn() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM athletes")
        athletes = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM results")
        results = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM meets")
        meets = cursor.fetchone()[0]

    print(f"[DB] Athletes: {athletes} | Results: {results} | Meets: {meets}")


# countQueue
# Purpose: Prints the total number of meets in the queue. Used for diagnostics.
# Arguments: None.
# Output: None.
def countQueue():

    with getConn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM meet_queue")
        n = cursor.fetchone()[0]

    print(f"[DB] Meet queue: {n} meets")

# countRecoveryRemaining
# Purpose: Returns count of unscraped rows in tf_recovery_queue.
# Arguments: None.
# Output: Integer count.
def countRecoveryRemaining() -> int:
 
    with getConn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM tf_recovery_queue WHERE scraped = 0
        """)
        n = cursor.fetchone()[0]
 
    return n

# _migrateMeetQueueAddSource
# Purpose: Add a `source` column to meet_queue and widen the PK to
#          (meet_id, sport, source), so anet and tfrrs can share the numeric
#          id space without colliding. Existing rows are all anet, so the
#          column defaults to 'anet' — every current row is tagged correctly
#          in one shot. Idempotent: re-running is a no-op (checks the PK arity
#          the same way _migrateMeetQueueCompositeKey does).
# Arguments:
#           cursor: open psycopg2 cursor.
# Output:   None.
def _migrateMeetQueueAddSource(cursor):

    # Add the column if missing. DEFAULT 'anet' backfills every existing row
    # (all anet today) in the same statement; NOT NULL so the PK can include it.
    cursor.execute("""
        ALTER TABLE meet_queue
        ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'anet'
    """)

    # Find the current PK constraint name (same catalog lookup as the composite
    # migration). If the PK already covers 3 columns, we've run before -> stop.
    cursor.execute("""
        SELECT conname, array_length(conkey, 1)
        FROM pg_constraint
        WHERE conrelid = 'meet_queue'::regclass AND contype = 'p'
    """)
    row = cursor.fetchone()

    if row is not None and row[1] == 3:
        return  # already (meet_id, sport, source)

    # Drop the old PK (whatever its arity) and add the 3-column one. Quoting the
    # name with the f-string is safe: it came straight from the catalog.
    if row is not None:
        cursor.execute(f"ALTER TABLE meet_queue DROP CONSTRAINT {row[0]}")
    cursor.execute("ALTER TABLE meet_queue ADD PRIMARY KEY (meet_id, sport, source)")

# _migrateMeetExtrasAddSource
# Purpose: Same migration for meet_extras: add `source` + widen the PK to
#          (meet_id, sport, source). This is what lets the tfrrs driver write
#          team_scores_json under its own row instead of overwriting the anet
#          meet that shares the id. Existing rows are all anet -> DEFAULT 'anet'.
#          Idempotent (checks PK arity). Touches only source/PK — independent of
#          which JSON columns the table has.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output:   None.
def _migrateMeetExtrasAddSource(cursor):
 
    cursor.execute("""
        ALTER TABLE meet_extras
        ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'anet'
    """)
 
    cursor.execute("""
        SELECT conname, array_length(conkey, 1)
        FROM pg_constraint
        WHERE conrelid = 'meet_extras'::regclass AND contype = 'p'
    """)
    row = cursor.fetchone()
 
    if row is not None and row[1] == 3:
        return  # already (meet_id, sport, source)
 
    if row is not None:
        cursor.execute(f"ALTER TABLE meet_extras DROP CONSTRAINT {row[0]}")
    cursor.execute("ALTER TABLE meet_extras ADD PRIMARY KEY (meet_id, sport, source)")

# ─────────────────────────────────────────────────────────────────────────────
# Tfrrs Queue management
# ─────────────────────────────────────────────────────────────────────────────


# claimTFRRSMeetBatch
# Purpose: TFRRS analog of getBatchUnscrapedMeets, but (a) scoped to
#          source='tfrrs' so it never claims anet rows sharing the id space, and
#          (b) returns a flat list of (meet_id, sport) TUPLES — the shape the
#          drain loop iterates — not anet's {meet_id: [sports]} dict. Opens and
#          commits its OWN connection (the driver does NOT pass one in).
# Arguments:
#           batch_size: max rows to claim this round.
# Output:   list of (meet_id, sport) tuples actually claimed (status 0 -> 3).
#           Empty list = no tfrrs work left.
def claimTFRRSMeetBatch(batch_size: int) -> list:
    with getConn() as conn:
        cursor = conn.cursor()
        rows = _claimTFRRSBatch(cursor, batch_size)
        conn.commit()   # publish the 0->3 flip so a concurrent run can't re-grab
    return rows
 
 
# _claimTFRRSBatch
# Purpose: The atomic claim itself — same UPDATE ... WHERE (...) IN (SELECT ...
#          FOR UPDATE SKIP LOCKED LIMIT n) RETURNING pattern as anet's
#          _claimMeetBatch, but keyed on the FULL TRIPLE (meet_id, sport, source)
#          so it can never touch the anet sibling at the same (meet_id, sport).
# Arguments:
#           cursor:     open cursor.
#           batch_size: max rows to claim.
# Output:   list of (meet_id, sport) tuples.
def _claimTFRRSBatch(cursor, batch_size):
    cursor.execute(
        """
        UPDATE meet_queue
        SET scraped = 3
        WHERE (meet_id, sport, source) IN (
            SELECT meet_id, sport, source
            FROM meet_queue
            WHERE scraped = 0 AND source = 'tfrrs'
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        )
        RETURNING meet_id, sport
        """,
        (batch_size,),
    )
    return cursor.fetchall()
 
 
# resetTFRRSInProgress
# Purpose: At launch, flip any tfrrs rows stranded in-progress (scraped=3) back
#          to 0 — scoped to source='tfrrs' so a crashed tfrrs run resumes WITHOUT
#          disturbing anet's queue. Mirrors resetInProgress, source-filtered.
# Arguments: None.
# Output:   None (prints the count).
def resetTFRRSInProgress():
    with getConn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE meet_queue SET scraped = 0 WHERE scraped = 3 AND source = 'tfrrs'"
        )
        count = cursor.rowcount
        conn.commit()
    print(f"[DB] Reset {count} in-progress tfrrs meets to unscraped")
 

if __name__ == "__main__":
    initPool()
    createTables()
    closePool()