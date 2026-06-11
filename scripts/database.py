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
import platform

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
MAX_CONN = 40

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
        ON CONFLICT (athlete_id) DO NOTHING
    """, (
        athleteData.get("AthleteID"),
        athleteData.get("FirstName"),
        athleteData.get("LastName"),
        athleteData.get("Gender"),
        athleteData.get("SchoolName")
    ))

# saveMeet
# Purpose: Inserts one XC meet division into the meets table.
# Arguments:
#           conn: connection from the pool.
#           meetData: dict with meet-level info from getMeetData.
#           divData: dict with division-level info.
# Output: None.
def saveMeet(conn, meetData: dict, divData: dict):

    cursor = conn.cursor()

    executeWithRetry(cursor, """
        INSERT INTO meets (div_id, meet_id, meet_name, course_name,
                          distance, gps_lat, gps_long, state)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (div_id) DO NOTHING
    """, (
        divData["IDMeetDiv"],
        meetData["ID"],
        meetData["Name"],
        meetData["Location"]["Name"],
        divData["Meters"],
        meetData["Location"]["Lat"],
        meetData["Location"]["Long"],
        meetData["Location"]["State"]
    ))

# saveResult
# Purpose: Inserts one XC result into the results table.
# Arguments:
#           conn: connection from the pool.
#           resultData: dict with result-level info from getMeetResults.
#           meetData: dict with meet-level info.
# Output: None.
def saveResult(conn, resultData: dict, meetData: dict):

    cursor = conn.cursor()

    meet_date_raw = meetData.get("MeetDate", "")
    meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""

    executeWithRetry(cursor, """
        INSERT INTO results (result_id, athlete_id, meet_id, div_id,
                            time_seconds, grade, date)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (result_id) DO NOTHING
    """, (
        resultData.get("IDResult"),
        resultData.get("AthleteID"),
        meetData.get("ID"),
        resultData.get("IDDiv"),
        resultData.get("SortValue"),
        resultData.get("Grade", ""),
        meet_date
    ))

# saveMeetTF
# Purpose: Inserts one TF event/division combo into meets_tf.
# Arguments:
#           conn: connection from the pool.
#           meet_info: dict from getMeetDataTF.
#           div_id: division ID integer.
#           event_id: numeric event ID.
#           event_short: event code string e.g. "1mile".
#           distance_meters: float or None.
# Output: None.
def saveMeetTF(conn, meet_info: dict, div_id: int, event_id: int,
               event_short: str, distance_meters):

    cursor = conn.cursor()

    location = meet_info.get("Location", {})

    executeWithRetry(cursor, """
        INSERT INTO meets_tf (div_id, meet_id, meet_name, event_short,
                             event_id, distance_meters, gps_lat, gps_long,
                             state, is_indoor)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (div_id, event_id) DO NOTHING
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
        1 if location.get("Indoor") else 0
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
# Output: None.
def saveResultTF(conn, result: dict, meet_info: dict, div_id: int,
                event_id: int, event_short: str, is_relay: int):
    
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

    executeWithRetry(cursor, """
        INSERT INTO results_tf (result_id, athlete_id, meet_id, div_id,
                               event_id, event_short, time_seconds, grade,
                               date, is_relay)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (result_id) DO NOTHING
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
        is_relay
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
            ON CONFLICT (meet_id) DO NOTHING
        """, (meet_id, sport))
        conn.commit()

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
    rows = [
        (
            a.get("AthleteID"),
            a.get("FirstName"),
            a.get("LastName"),
            a.get("Gender"),
            a.get("SchoolName")
        )
        for a in athletes
        if a.get("AthleteID")  # Skip any without an ID
    ]

    if not rows:
        return
    cursor = conn.cursor()

    # execute_values sends all rows in one round trip to Postgres
    # %s in the template is filled by execute_)values based on the order
    # of the tuple in the list. Stops duplicates with ON CONFLICT of athlete
    # id DO NOTHING.
    psycopg2.extras.execute_values(cursor, """
        INSERT INTO athletes (athlete_id, first_name, last_name, gender, school)
        VALUES %s
        ON CONFLICT (athlete_id) DO NOTHING
    """, rows)

# saveResultsBulk
# Purpose: Inserts a list of (resultData, meetData) tuples in one query into
#            the results table.
# Arguments:
#           conn: connection from the pool.
#           results: list of (resultData dict, meetData dict) tuples.
# Output: None.
def saveResultsBulk(conn, results: list):

    if not results:
        return
    
    # Builds a list of tuples with resultData and meetData to insert in bulk.
    rows = []
    for resultData, meetData in results:
        
        # Gets meet date mm/dd/yy.
        meet_date_raw = meetData.get("MeetDate", "")
        meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""

        rows.append((
            resultData.get("IDResult"),
            resultData.get("AthleteID"),
            meetData.get("ID"),
            resultData.get("IDDiv"),
            resultData.get("SortValue"),
            resultData.get("Grade", ""),
            meet_date
        ))
    
    cursor = conn.cursor()
    psycopg2.extras.execute_values(cursor, """
        INSERT INTO results (result_id, athlete_id, meet_id, div_id,
                            time_seconds, grade, date)
        VALUES %s
        ON CONFLICT (result_id) DO NOTHING
    """, rows)

# saveResultsTFBulk
# Purpose: Inserts a list of TF result tuples in one query.
# Arguments:
#           conn: connection from the pool.
#           results: list of (result, meet_info, div_id, event_id,
#                             event_short, is_relay) tuples.
# Output: None.
def saveResultsTFBulk(conn, results: list):

    if not results:
        return
    
    # Builds a list of tuples with resultData and meetData to insert in bulk.
    rows = []
    for result, meet_info, div_id, event_id, event_short, is_relay in results:

        # Converts milliseconds to seconds unless it doesn't exist or is
        # a sentinel value
        sort_int = result.get("SortInt")
        time_seconds = sort_int / 1000 if (sort_int is not None and sort_int < 100000000) else None

        # Gets meet date mm/dd/yy.
        meet_date_raw = meet_info.get("MeetDate", "")
        meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""

        rows.append((
            result.get("IDResult"),
            result.get("AthleteID"),
            meet_info.get("ID"),
            div_id,
            event_id,
            event_short,
            time_seconds,
            result.get("Grade", ""),
            meet_date,
            is_relay
        ))
 
    cursor = conn.cursor()
    psycopg2.extras.execute_values(cursor, """
        INSERT INTO results_tf (result_id, athlete_id, meet_id, div_id,
                               event_id, event_short, time_seconds, grade,
                               date, is_relay)
        VALUES %s
        ON CONFLICT (result_id) DO NOTHING
    """, rows)

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

# markScraped
# Purpose: Updates a meet's scraped status in the queue.
# Arguments:
#           meet_id: meet to update.
#           status: 1=done, 2=failed, 0=reset, 3=in-progress, 4=skipped
# Output: None.
def markScraped(meet_id: int, status: int = 1):

    # The with triggers the context manager. It checks a connection out
    # of the pool, binds it to the name conn, and returns it to the pool.
    with getConn() as conn:
        cursor = conn.cursor()
        executeWithRetry(cursor,
            "UPDATE meet_queue SET scraped = %s WHERE meet_id = %s",
            (status, meet_id)
        )
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
        cursor.execute("UPDATE meet_queue SET scraped = 0 WHERE scraped = 3")
        count = cursor.rowcount
        conn.commit()
    print(f"[DB] Reset {count} in-progress meets to unscraped")

# updateMeetSport
# Purpose: Updates the sport tag for a meet in the queue.
#          Called after sport detection in scrapeMeetUnified.
# Arguments:
#           meet_id: athletic.net meet ID.
#           sport: "XC" or "TF".
# Output: None. Updates one row in meet_queue.
def updateMeetSport(meet_id: int, sport: str):

    
    # The with triggers the context manager. It checks a connection out
    # of the pool, binds it to the name conn, and returns it to the pool.
    with getConn() as conn:
        cursor = conn.cursor()
        executeWithRetry(cursor,
            "UPDATE meet_queue SET sport = %s WHERE meet_id = %s",
            (sport, meet_id)
        )
        conn.commit()

# getUnscrapedMeets
# Purpose: Queries the db for up to 500 meets that are unscraped (scraped = 0).
# Arguments:
#           limit: the limit for how many meets to return. Default is 500.
# Output: Returns a list of tuples containing up to 500 meets and what sport
# the meet was.
def getUnscrapedMeets(limit: int = 500) -> list[tuple[int, str]]:

    with getConn() as conn:
        cursor = conn.cursor()
 
        # RETURNING makes UPDATE return the rows it just changed, so we
        # mark and fetch in one atomic operation with no gap between them.
        if platform.system() == "Linux":
            # On Linux (VM), skip status 4 (TF meets flagged for Windows).
            cursor.execute("""
                UPDATE meet_queue SET scraped = 3
                WHERE meet_id IN (
                    SELECT meet_id FROM meet_queue
                    WHERE scraped = 0
                    LIMIT %s
                )
                RETURNING meet_id, sport
            """, (limit,))
        else:
            # On Windows, pick up unscraped (0) AND TF-flagged (4) meets.
            cursor.execute("""
                UPDATE meet_queue SET scraped = 3
                WHERE meet_id IN (
                    SELECT meet_id FROM meet_queue
                    WHERE scraped = 0 OR scraped = 4
                    LIMIT %s
                )
                RETURNING meet_id, sport
            """, (limit,))

        # Fetches all rows, returning a list of tuples with the meet ids and sport. 
        rows = cursor.fetchall()
        conn.commit()
 
    return rows

# countRemaining
# Purpose: returns the number of meets that are still unscraped.
# Arguments: none.
# Output: Retunrs the number of meets that are still unscraped.
def countRemaining() -> int:
    
    with getConn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM meet_queue WHERE scraped = 0")
        n = cursor.fetchone()[0]
 
    return n

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
 

if __name__ == "__main__":
    initPool()
    createTables()
    closePool()