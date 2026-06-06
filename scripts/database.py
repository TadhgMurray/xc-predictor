# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Database for Scraper
# Date: 5/28/2026
# File Title: database.py
# Purpose: Creates and manages the SQLite database that stores the scraped meet
# results data. It contains three tables: athletes, meets, and results so we
# don't have to store redundant data. Instead we link respective rows in each
# table to other

import sqlite3
import os
import time
import random

# Path to the database file, sotored in the data folder
# __file__ is the path to current script, .. is go up one level,
# then into /data to store databse. os.path.join is used to create a path 
# that works on any operating system.
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "xc.db")

# getConn
# Purpose: Opens a connectio to the db with consistent settings
#          All functions shsould sue this instead of sqlite3.connect() directly.
# Arguments: None
# Output: Returns a sqlite3 connection object.
def getConn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout = {random.randint(15000, 45000)}")
    return conn

# createTables
# Purpose: Creates and manages the SQLite database, the three tables
# (athletes, meets, results), that store the scraped meet results data.
# Creates a quaternary table to facilitate this, containing all the meets.
# Arguments: None
# Output: None, creates database tables if they don't exist.
def createTables():

    try:
        # Connect to the database file, creates it if it doesn't exist.
        conn = getConn()

        # WAL mode lets the scraper and normalizer write simultaneously
        # without blocking each other. Default mode locks the entire file
        # on any write — WAL uses a separate log file instead. Only need
        # to do this once in file because it sets WAL mode for all future
        # connections. PRAGAM sets db-wide configuration options.
        # journal_mode is the specific setting, and WAL is the value.
        conn.execute("PRAGMA journal_mode=WAL")

        # Cursor is what you use to execute SQL commands.
        cursor = conn.cursor()

        # Creates three tables: athletes, meets, and results.
        # INTEGER PRIMARY KEY means this column is the unique identifier 
        # for each row, and it will auto-increment. TEXT is for text data, 
        # REAL is for numbers with decimals, Integer for whole numbers.

        # Athletes table - one row per athlete.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS athletes ( 
                athlete_id      INTEGER PRIMARY KEY,
                first_name      TEXT,
                last_name       TEXT,
                gender          TEXT,
                school          TEXT
            )
        """)

        # Meets table - one row per race division.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meets (
                div_id          INTEGER PRIMARY KEY,
                meet_id         INTEGER,
                meet_name       TEXT,
                course_name     TEXT,
                distance        REAL,
                gps_lat         REAL,
                gps_long        REAL,
                state           TEXT
            )
        """)

        # Results table - one row per individual performance.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS results (
                result_id       INTEGER PRIMARY KEY,
                athlete_id      INTEGER,
                meet_id         INTEGER,
                div_id          INTEGER,
                time_seconds    REAL,
                grade           TEXT,
                date            TEXT
            )
        """)

        # Table specifically for scraping all meets. Holds Meet ID and sport(XC/TF).
        # For scraped, 0 means not yet processed (default), 1 means processed,
        # 2 means failed, 3 means in progress.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meet_queue (
                meet_id     INTEGER PRIMARY KEY,
                sport       TEXT,
                scraped     INTEGER DEFAULT 0
            )
        """)

        # Indexes to make reading db faster. Only do this for some so we
        # don't have a write overhead.

        # Index on normalized_time. SQLite stores the location of every NULL in this
        # column so WHERE normalized_time IS NULL can jump straight to unprocessed
        # rows instead of reading all 10M rows to find them.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_normalized_time 
            ON results (normalized_time)
        """)

        # Index on athlete_id. SQLite stores the location of every result for each
        # athlete so SELECT * FROM results WHERE athlete_id = ? can jump straight
        # to that athlete's rows instead of scanning the whole table.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_athlete_id
            ON results (athlete_id)
        """)

        # Index on meet_id. SQLite stores the location of every result for each
        # meet so SELECT * FROM results WHERE meet_id = ? can jump straight
        # to that meet's rows instead of scanning the whole table.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_meet_id
            ON results (meet_id)
        """)

        # Stores the computed difficulty rating for each course.
        # One row per unique course_name. Recomputed every engine run.
        # difficulty is a percantage - 0.03 m,eans 3 % slower than flat neutral.
        # n_results and n_athletes are used to flag low-confidence ratings
        # (courses with few athlete are less reliable).
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS course_difficulties (
                course_name     TEXT PRIMARY KEY,
                difficulty      REAL,
                n_results       INTEGER,
                n_athletes      INTEGER,
                last_updated    TEXT
            )
        """)

        # Stores the computed speed rating for each athlete in each pool.
        # One row per (athlete_id, pool) pair — an athlete who competed in
        # both hs and college gets two rows.
        # speed_rating is on a points scale where 100 = average for that pool.
        # Higher is faster.
        # PRIMARY KEY (athlete_id, pool) means the combination of both columns
        # must be unique — same as saying "one rating per athlete per pool".
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS athlete_ratings (
                div_id          INTEGER PRIMARY KEY,
                meet_id         INTEGER,
                meet_name       TEXT,
                event_short     TEXT,
                distance_meters REAL,
                gps_lat         REAL,
                gps_long        REAL,
                state           TEXT
            )
        """)

        # TF meets table - one row per division per TF meet.
        # Separate from meets because TF divisions don't have a single
        # course or GPS — the meet has a location but divisions have events.
        # event_short is the athletic.net event code e.g. "1mile", "3000m".
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meets_tf (
                div_id          INTEGER,
                meet_id         INTEGER,
                meet_name       TEXT,
                event_short     TEXT,
                event_id        INTEGER,
                distance_meters REAL,
                gps_lat         REAL,
                gps_long        REAL,
                state           TEXT,
                is_indoor       INTEGER DEFAULT 0,
                PRIMARY KEY (div_id, event_id)
            )
        """)

        # TF results table - one row per individual TF performance.
        # Separate from results because TF needs event_short and is_relay
        # which XC doesn't have.
        # time_seconds comes from SortInt / 100 — SortInt is centiseconds.
        # is_relay: 1 if this is a relay leg, 0 if individual event.
        # normalized_time: flat 5K equivalent, NULL for events under 800m,
        # field events, relays, and any eventShort not in EVENT_DISTANCES_TF.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS results_tf (
                result_id       INTEGER PRIMARY KEY,
                athlete_id      INTEGER,
                meet_id         INTEGER,
                div_id          INTEGER,
                event_id        INTEGER,
                event_short     TEXT,
                time_seconds    REAL,
                grade           TEXT,
                date            TEXT,
                is_relay        INTEGER DEFAULT 0,
                normalized_time REAL DEFAULT NULL,
                speed_rating    REAL DEFAULT NULL
            )
        """)

        # Index on athlete_id for fast per-athlete lookups.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_tf_athlete_id
            ON results_tf (athlete_id)
        """)

        # Index on meet_id for fast per-meet lookups.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_tf_meet_id
            ON results_tf (meet_id)
        """)

        # Index on normalized_time for fast backfill queries.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_tf_normalized_time
            ON results_tf (normalized_time)
        """)

        # Tracks every TF event/division that was successfully scraped.
        # One row per (meet_id, event_short, div_id) combo.
        # Purpose: lets a recovery script compare what was actually captured
        # against what getMeetDataTF says should exist, so we can re-queue
        # only the missing events instead of re-scraping entire meets.
        # scraped_at is an ISO timestamp string e.g. "2026-06-06T14:32:11".
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tf_scraped_events(
                meet_id         INTEGER,
                event_short     TEXT,
                div_id          INTEGER,
                scraped_at      TEXT,
                PRIMARY KEY (meet_id, event_short, div_id)
            )
        """)
            


        # Save changes and close connection.
        conn.commit()
        conn.close()

    except Exception as e:
        print(f"Failed to create tables: {e}")

# Run createTables if this script is run directly
if __name__ == "__main__":
    createTables()

# saveAthlete
# Purpose: Takes an athlete dictionary and inserts it into the athletes table
# Arguments: athleteData is a dictionary with keys: first_name, last_name,
# gender, school.
# Output: None, inserts athlete data into database.
def saveAthlete(athleteData: dict):

    # Skip results with no athlete ID
    if not athleteData.get("AthleteID"):
        return

    # Opens connection to the db.
    conn = getConn()

    # Creates cursor to execute SQL commands.
    cursor = conn.cursor()

    # Inserts athlete data into the athletes table. Uses INSERT OR IGNORE to avoid
    # inserting duplicate athletes if the same athlete appears. Uses ? to pass
    # the values safely and prevent SQL injection by deciding the values type
    # by the values inserted.
    # Executes with retries to keep retrying when two browsers 
    # attempt to write to the db at the same time.
    executeWithRetry(cursor, """
        INSERT OR IGNORE INTO athletes (
            athlete_id,
            first_name,
            last_name,
            gender,
            school
        ) VALUES (?, ?, ?, ?, ?)
    """, (
        # Used .get() for these to avoid accessing info that may not exist.
        athleteData.get("AthleteID", ""),
        athleteData.get("FirstName", ""),
        athleteData.get("LastName", ""),
        athleteData.get("Gender", ""),
        athleteData.get("SchoolName", "")
    ))

    conn.commit()
    conn.close()

# saveResult
# Purpose: Takes a result dictionary and inserts it into the results table
# Arguments: resultData is a dictionary with keys: athlete_id, meet_id, div_id,
# time_seconds, grade, date. MeetData is a dictionary with keys: meet_id, 
# div_id, meet_name, course_name, distance, gps_lat, gps_long, state.
# Output: None, inserts result data into database.
def saveResult(resultData: dict, meetData: dict):

    # Opens connection to database and creates cursor to execute SQL commands.
    conn = getConn()
    cursor = conn.cursor()

    # Split MeetDate safely — .get() returns "" if missing,
    # and we only call .split() if we actually got a string back, so we
    # only extract the date part, not the time.
    meet_date_raw = meetData.get("MeetDate", "")
    meet_date = meet_date_raw.split("T")[0] if meet_date_raw else ""

    # Inserts result data into the results table.
    # Pass meet_id and date in manually as arguments because they are 
    # not include in the result object.
    # Executes with retries to keep retrying when two browsers 
    # attempt to write to the db at the same time.
    executeWithRetry(cursor, """
        INSERT OR IGNORE INTO results (
            result_id,
            athlete_id,
            meet_id,
            div_id,
            time_seconds,
            grade,
            date
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
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

# saveMeet
# Purpose: Takes a meet dictionary and inserts it into the meets table
# Arguments: meetData is a dictionary with keys: meet_id, div_id, meet_name,
# course_name, distance, gps_lat, gps_long, state. divData is a dictionary 
# with keys: IDMeetDiv, Distance.
# Output: None, inserts meet data into database.
def saveMeet(meetData: dict, divData: dict):

    # Opens connection to database and creates cursor to execute SQL commands.
    conn = getConn()
    cursor = conn.cursor()

    # Inserts meet data into the meets table.
    # Executes with retries to keep retrying when two browsers 
    # attempt to write to the db at the same time.
    executeWithRetry(cursor, """
        INSERT OR IGNORE INTO meets (
            meet_id,
            div_id,
            meet_name,
            course_name,
            distance,
            gps_lat,
            gps_long,
            state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        meetData["ID"],
        divData["IDMeetDiv"],
        meetData["Name"],
        meetData["Location"]["Name"],
        divData["Meters"],
        meetData["Location"]["Lat"],
        meetData["Location"]["Long"],
        meetData["Location"]["State"]
    ))

    conn.commit()
    conn.close()

# saveMeetTF
# Purpose: Saves one event/division combination from a TF meet to
#          meets_tf.
# Arguments:
#           meet_info: dict from getMeetDataTF with meet-level info.
#           div_id: the IDMeetDiv for this division.
#           event_id: the numeric event ID from the events lookup table.
#           event_short: the event code string e.g. "1mile", "3000m".
#           distance_meters: float or None if event has no meaningful distance.
# Output: None.
def saveMeetTF(meet_info: dict, div_id: int, event_id: int, 
               event_short: str, distance_meters):
    
    # Opens connection to database and creates cursor to execute SQL commands.
    conn = getConn()
    cursor = conn.cursor()

    # Extract location fields - TF meet location is nested under "Location"
    # unlike XC where GPS is directly on the meet object.
    location = meet_info.get("Location", {})

    # Saves a event/division combo to meets_tf table.
    executeWithRetry(cursor, """
        INSERT OR IGNORE INTO meets_tf (
            div_id,
            meet_id,
            meet_name,
            event_short,
            event_id,
            distance_meters,
            gps_lat,
            gps_long,
            state,
            is_indoor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        # Indoor is a boolean in the API - convert to 1/0 for SQLite/
        # bool(None) is False so .get() with no default is safe here.
        1 if location.get("Indoor") else 0
    ))

    conn.commit()
    conn.close()

# saveResultTF
# Purpose: Saves one TF result to results_tf table in db.
# Arguments:
#           result: dict from resultsTF array in GetResultsData3 response.
#           meet_info: dict from getMeetDataTF with meet-level info.
#           div_id: the IDMeetDiv for this division.
#           event_id: numeric event ID.
#           event_short: event code string e.g. "1mile".
#           is_relay: 1 if this is a relay event, 0 if individual.
# Output: None.
def saveResultTF(result: dict, meet_info: dict, div_id: int, 
                event_id: int, event_short: str, is_relay: int):
    
    # Opens connection to database and creates cursor to execute SQL commands.
    conn = getConn()
    cursor = conn.cursor()

    # SortInt is in millisceconds, so divide by 1000 to get seconds.
    sort_int = result.get("SortInt")
    # Calculates sort_int into seconds as long as it exists and is
    # not a sentinel value(999999 etc due to DNFs and such).
    if sort_int is not None and sort_int < 100000000:
        time_seconds = sort_int / 1000
    else:
        time_seconds = None

    # Date comes from the meet, not the result — same as XC.
    meet_date_raw = meet_info.get("MeetDate", "")

    # Gets date portion from meet_date_raw.
    if meet_date_raw:
        meet_date = meet_date_raw.split("T")[0]
    else:
        meet_date = ""

    # Saves a result to results_tf.
    executeWithRetry(cursor, """
        INSERT OR IGNORE INTO results_tf (
            result_id,
            athlete_id,
            meet_id,
            div_id,
            event_id,
            event_short,
            time_seconds,
            grade,
            date,
            is_relay
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (# Creates a tuple to pass for args.
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
# Purpose: Saves meet id and sport into SQL table. Has a specific table for
# this to store all meets for later scraping,
# Arguments:
#           meet_id: id of meet
#           sport: sport of the meet, XC vs TF.
# Output: None, inserts meet_queue data into database.
def saveMeetQueue(meet_id: int, sport: str):

    # Opens connection to databse and creates cursor to execute SQL commands.
    conn = getConn()
    cursor = conn.cursor()

    # Inserts meet_queue data into the meet_queue table.
    # Executes with retries to keep retrying when two browsers 
    # attempt to write to the db at the same time.
    executeWithRetry(cursor,"""
        INSERT OR IGNORE INTO meet_queue (meet_id, sport) 
        VALUES (?, ?)
    """, (meet_id, sport))

    conn.commit()
    conn.close()

# countQueue
# Purpose: Count how many rows are in meet_queue table and prints it.
# Arguments: none.
# Output: none, but prints out how many rows are in meet_queue table.
def countQueue():

    # Opens connection to databse and creates cursor to execute SQL commands.
    conn = getConn()
    cursor = conn.cursor() 

    # Runs an SQL query to count every row in the table (COUNT(*)). Returns
    # one row with one value - the count.
    cursor.execute("SELECT COUNT(*) FROM meet_queue")

    # Gets that one row back as a tuple, e.g. (9,), extracts the number from it.
    count = cursor.fetchone()[0]

    conn.close()
    print(f"Meet queue: {count}")

# executeWithRetry
# Purpose: Wraps a single SQL execute vall with retry logic. If SQLite says
# the db is locked, it waits a short time and dv again. This is necessary
# when multiple sessions are writing to the browser simultaneously.
# Arguments:
#           cursor: the SQLite cursor to execute the SQL on.
#           sql: the SQL string to execute.
#           params: the tuples of values to pass to the SQL.
#           retries: how many times to retry writing to the db (default 5).
# Output: None. Raise the error if all retries are exhausted.
def executeWithRetry(cursor, sql: str, params: tuple, retries: int = 5):

    # Retries writing to the db at random intervals if the error is dude
    # to multiple write at the same time. Does this up to retries (default 5) times.
    for attempt in range(retries):
        try:
            cursor.execute(sql, params)
            return
        except sqlite3.OperationalError as e:
            # "locked" means another session is writing right now.
            if "locked" in str(e) and attempt < retries - 1:
                time.sleep(0.05 + (attempt * 0.05) + random.uniform(0, 0.05))
            else:
                # If it's a different error or we've exhausedted retries, we
                # raise the original error.
                raise

# markScraped
# purpose: Changes a meet's scraped status to either 
# scraped or crashed while scraping.
# arguments:
#           meet_id: id of the meet whose scraped status we're changing.
#           status: what to change meet's scraped status to. 1 is default.
#                   1 means scraped, 2 means crashed while scraping.
# Output: None, but changes meet's scraped status in db.
def markScraped(meet_id: int, status: int = 1):

    # Connects to the db file, creates a cursor object to execute SQL commands.
    conn = getConn()
    cursor = conn.cursor()

    # Sets status of scraped column for meet. The default is 1 (scraped).
    # 2 means it crashed here. 0 means unscraped. Executes with retries to
    # keep retrying when two browsers attempt to write to the db at the same time.
    executeWithRetry(cursor, """
        UPDATE meet_queue SET scraped = ? WHERE meet_id = ?
    """, (status, meet_id))

    conn.commit()
    conn.close()

# resetInProgess
# Purpose: Resets in progress meet scrapes (scraped = 3 in meet_queue table),
# to unscraped (scraped = 0).
# Arguments: None.
# Output: None, prints how many meets were reset.
def resetInProgress():

    # Connects to the db file, creates a cursor object to execute SQL commands.
    conn = getConn()
    cursor = conn.cursor()

    cursor.execute("UPDATE meet_queue SET scraped = 0 WHERE scraped = 3")

    # Gets the count of how many rows were in progress and changed back.
    count = cursor.rowcount

    # Commits changes.
    conn.commit()

    conn.close()
    
    print(f"Reset {count} in-progress meets back to unscraped")

def migrateAddNormalizedTime():

    # Connects to the db file, creates a cursor object to execute SQL commands.
    conn = getConn()
    cursor = conn.cursor()

    # Get the list of existing columns in the results table.
    # PRAGMA table_info returns one row per column with its names and type.
    cursor.execute("PRAGMA table_info(results)")

    # fetchall() returns a list of tuples. Each tuple is one column.
    # The column name is at index 1 of each tuple: (id, name, type, ...).
    columns = [row[1] for row in cursor.fetchall()]

    # Only add the column if it doesn't already exist.
    if "normalized_time" not in columns:
        # Use """ for multi-line SQL strings. Adds a new column to the results table to store
        # the normalized time, which is the time adjusted for distance to allow comparison
        # between distances.
        cursor.execute("""
            ALTER TABLE results 
            ADD COLUMN normalized_time REAL DEFAULT NULL
        """)
        print("Added normalized_time column to results table.")
    else:
        print("normalized_time column already exists, skipping migration.")

    conn.commit()
    conn.close()


# migrateAddSpeedRating
# Purpose: Adds speed_rating column to the results table.
# Arguments: None.
# Output: None.
def migrateAddSpeedRating():

    conn = getConn()
    cursor = conn.cursor()

    # PRAGMA table_info returns one row per column in the table.
    # We check if speed_rating already exists before trying to add it —
    # ALTER TABLE fails if the column is already there.
    cursor.execute("PRAGMA table_info(results)")
    columns = [row[1] for row in cursor.fetchall()]

    # Add a speed rating column if it doesn't already exist in the results table.
    if "speed_rating" not in columns:
        cursor.execute("""
            ALTER TABLE results
            ADD COLUMN speed_rating REAL DEFAULT NULL
        """)
        print("Added speed_rating column to results table.")
    else:
        print("speed_rating column already exists, skipping migration.")

    conn.commit()
    conn.close()

# logScrapedEventTF
# Purpose: Records that a specific event/div in a TF meet was successfully
#          scraped
# Arguments:
#           meet_id: athletic.net meet ID.
#           event_short: event code string e.g. "100m", "1mile".
#           div_id: division ID for this event.
# Output: None.
def logScrapedEventTF(meet_id: int, event_short: str, div_id: int):

    conn = getConn()
    cursor = conn.cursor()

    # INSERT OR IGNORE so re-runs don't error — if this event was already
    # logged from a previous partial scrape, we just skip it.
    executeWithRetry(cursor, """
        INSERT OR IGNORE INTO tf_scraped_events (
            meet_id,
            event_short,
            div_id,
            scraped_at
        ) VALUES (?, ?, ?, ?)
    """, (
        meet_id,
        event_short,
        div_id,
        # datetime.utcnow() gives us a UTC timestamp. .isoformat() converts
        # it to a string like "2026-06-06T14:32:11.123456". We slice [:19]
        # to drop the microseconds — we only need second precision.
        __import__('datetime').datetime.utcnow().isoformat()[:19]
    ))

    conn.commit()
    conn.close()
