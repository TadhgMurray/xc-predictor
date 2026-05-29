# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Scraper
# Date: 5/28/2026
# File Title: database.py
# Purpose: Creates and manages the SQLite database that stores the scraped meet
# results data.

import sqlite3
import os

# Path to the database file, sotored in the data folder
# __file__ is the path to current script, .. is go up one level,
# then into /data to store databse. os.path.join is used to create a path 
# that works on any operating system.
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "xc.db")

# createTables
# Purpose: Creates and manages the SQLite database, the three tables
# (athletes, meets, results), that store the scraped meet results data.
# Arguments: None
# Output: None, creates database tables if they don't exist.
def createTables():

    try:
        # Connect to the database file, creates it if it doesn't exist.
        conn = sqlite3.connect(DB_PATH)

        # Cursor is what you use to execute SQL commands.
        cursor = conn.cursor()

        # Creates three tables: athletes, meets, and results.
        # INTEGER PRIMARY KEY means this column is the unique identifier 
        # for each row, and it will auto-increment. TEXT is for text data, 
        # REAL is for numbers with decimals, Integer for whole numbers.

        # Athletes table  - one row per athlete.
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
    except Exception as e:
        print(f"Failed to save athlete: {e}")

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

    # Save changes and close connection.
    conn.commit()
    conn.close()

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

    # Opens connection to database and creates cursor to execute SQL commands.
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Inserts athlete data into the athletes table. Uses INSERT OR IGNORE to avoid
    # inserting duplicate athletes if the same athlete appears. Uses ? to pass
    # the values safely and prevent SQL injection by deciding the values type
    # by the values inserted.
    cursor.execute("""
        INSERT OR IGNORE INTO athletes (
            athlete_id,
            first_name,
            last_name,
            gender,
            school
            ) VALUES (?, ?, ?, ?, ?)
    """, (
        # Used .get() for these to avoid directly accessing keys that might not exist
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Inserts result data into the results table.
    # Pass meet_id and date in manually as arguments because they are 
    # not include in the result object.
    cursor.execute("""
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
        resultData["IDResult"],
        resultData["AthleteID"],
        meetData["ID"],
        resultData["IDDiv"],
        resultData["SortValue"],
        resultData["Grade"],
        meetData["MeetDate"].split("T")[0] # Extract only the data part, not the time
    ))

    conn.commit()
    conn.close()

def countRows():
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Inserts meet data into the meets table.
    cursor.execute("""
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
