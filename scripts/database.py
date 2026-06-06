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
import time
import random
from config import PG_CONFIG

# ─────────────────────────────────────────────────────────────────────────────
# Connection
# ─────────────────────────────────────────────────────────────────────────────

# getConn
# Purpose: Opens a connection to the Postgres database with consistent settings.
#          All functions use this instead of psycopg2.connect() directly so
#          we only have one place to change connection settings.
# Arguments: None
# Output: Returns a psycopg2 connection object.
def getConn():
    conn = psycopg2.connect(**PG_CONFIG)
    # autocommit off by default in psycopg2 — we commit manually after
    # each write so we control exactly when data is saved.
    return conn

# ─────────────────────────────────────────────────────────────────────────────
# Table creation
# ─────────────────────────────────────────────────────────────────────────────

# createTables
# Purpose: Creates all tables in Postgres if they don't already exist.
#          Safe to run multiple times — IF NOT EXISTS prevents errors.
# Arguments: None
# Output: None.
def createTables():
    try:
        conn = getConn()
        cursor = conn.cursor()

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
        # scraped: 0 = unscraped, 1 = done, 2 = failed, 3 = in progress.
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

        # Indexes for fast lookups.
        # Postgres syntax for IF NOT EXISTS on indexes is different from SQLite.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_athlete_id
            ON results (athlete_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_meet_id
            ON results (meet_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_normalized_time
            ON results (normalized_time)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_tf_athlete_id
            ON results_tf (athlete_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_tf_meet_id
            ON results_tf (meet_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_tf_normalized_time
            ON results_tf (normalized_time)
        """)

        conn.commit()
        conn.close()
        print("Tables created successfully.")

    except Exception as e:
        print(f"Failed to create tables: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Write helpers
# ─────────────────────────────────────────────────────────────────────────────

# executeWithRetry
# Purpose: Wraps a single SQL execute call with retry logic. If Postgres
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
# Save functions
# ─────────────────────────────────────────────────────────────────────────────

# saveAthlete
# Purpose: Inserts one athlete into the athletes table.
#          ON CONFLICT DO NOTHING skips duplicates safely.
# Arguments:
#           athleteData: dict with keys AthleteID, FirstName, LastName,
#                        Gender, SchoolName.
# Output: None.
def saveAthlete(athleteData: dict):
    if not athleteData.get("AthleteID"):
        return

    conn = getConn()
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

    conn.commit()
    conn.close()

# saveMeet
# Purpose: Inserts one XC meet division into the meets table.
# Arguments:
#           meetData: dict with meet-level info from getMeetData.
#           divData: dict with division-level info.
# Output: None.
def saveMeet(meetData: dict, divData: dict):
    conn = getConn()
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

    conn.commit()
    conn.close()

# saveResult
# Purpose: Inserts one XC result into the results table.
# Arguments:
#           resultData: dict with result-level info from getMeetResults.
#           meetData: dict with meet-level info.
# Output: None.
def saveResult(resultData: dict, meetData: dict):
    conn = getConn()
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

    conn.commit()
    conn.close()

# saveMeetTF
# Purpose: Inserts one TF event/division combo into meets_tf.
# Arguments:
#           meet_info: dict from getMeetDataTF.
#           div_id: division ID integer.
#           event_id: numeric event ID.
#           event_short: event code string e.g. "1mile".
#           distance_meters: float or None.
# Output: None.
def saveMeetTF(meet_info: dict, div_id: int, event_id: int,
               event_short: str, distance_meters):
    conn = getConn()
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

    conn.commit()
    conn.close()

# saveResultTF
# Purpose: Inserts one TF result into results_tf.
# Arguments:
#           result: dict from resultsTF array.
#           meet_info: dict from getMeetDataTF.
#           div_id: division ID.
#           event_id: numeric event ID.
#           event_short: event code string.
#           is_relay: 1 if relay, 0 if individual.
# Output: None.
def saveResultTF(result: dict, meet_info: dict, div_id: int,
                event_id: int, event_short: str, is_relay: int):
    conn = getConn()
    cursor = conn.cursor()

    sort_int = result.get("SortInt")
    if sort_int is not None and sort_int < 100000000:
        time_seconds = sort_int / 1000
    else:
        time_seconds = None

    meet_date_raw = meet_info.get("MeetDate", "")
    meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""

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

    conn.commit()
    conn.close()

# saveMeetQueue
# Purpose: Inserts a meet into the scraping queue.
# Arguments:
#           meet_id: athletic.net meet ID.
#           sport: "XC" or "TF".
# Output: None.
def saveMeetQueue(meet_id: int, sport: str):
    conn = getConn()
    cursor = conn.cursor()

    executeWithRetry(cursor, """
        INSERT INTO meet_queue (meet_id, sport)
        VALUES (%s, %s)
        ON CONFLICT (meet_id) DO NOTHING
    """, (meet_id, sport))

    conn.commit()
    conn.close()

# logScrapedEventTF
# Purpose: Records that a TF event/div was successfully scraped.
#          Used by recovery script to find missed events.
# Arguments:
#           meet_id: athletic.net meet ID.
#           event_short: event code string.
#           div_id: division ID.
# Output: None.
def logScrapedEventTF(meet_id: int, event_short: str, div_id: int):
    conn = getConn()
    cursor = conn.cursor()

    executeWithRetry(cursor, """
        INSERT INTO tf_scraped_events (meet_id, event_short, div_id, scraped_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (meet_id, event_short, div_id) DO NOTHING
    """, (
        meet_id,
        event_short,
        div_id,
        __import__('datetime').datetime.utcnow().isoformat()[:19]
    ))

    conn.commit()
    conn.close()

# ─────────────────────────────────────────────────────────────────────────────
# Queue management
# ─────────────────────────────────────────────────────────────────────────────

# markScraped
# Purpose: Updates a meet's scraped status in the queue.
# Arguments:
#           meet_id: meet to update.
#           status: 1 = done, 2 = failed, 0 = reset to unscraped.
# Output: None.
def markScraped(meet_id: int, status: int = 1):
    conn = getConn()
    cursor = conn.cursor()

    executeWithRetry(cursor, """
        UPDATE meet_queue SET scraped = %s WHERE meet_id = %s
    """, (status, meet_id))

    conn.commit()
    conn.close()

# resetInProgress
# Purpose: Resets any meets stuck in-progress (scraped=3) back to
#          unscraped (scraped=0). Called on startup to clean up after crashes.
# Arguments: None.
# Output: None.
def resetInProgress():
    conn = getConn()
    cursor = conn.cursor()

    cursor.execute("UPDATE meet_queue SET scraped = 0 WHERE scraped = 3")
    count = cursor.rowcount

    conn.commit()
    conn.close()
    print(f"Reset {count} in-progress meets back to unscraped")

# countRows
# Purpose: Prints row counts for main tables. Used in final summary.
# Arguments: None.
# Output: None.
def countRows():
    conn = getConn()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM athletes")
    athletes = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM results")
    results = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM meets")
    meets = cursor.fetchone()[0]

    conn.close()
    print(f"Athletes: {athletes}, Results: {results}, Meets: {meets}")

# countQueue
# Purpose: Prints how many meets are in the queue.
# Arguments: None.
# Output: None.
def countQueue():
    conn = getConn()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM meet_queue")
    count = cursor.fetchone()[0]

    conn.close()
    print(f"Meet queue: {count}")

if __name__ == "__main__":
    createTables()