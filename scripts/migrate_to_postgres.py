# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Database
# Date: 6/6/2026
# File Title: migrate_to_postgres.py
# Purpose: One-time migration script that reads all data from the local
#          SQLite database (xc.db) and writes it to the shared Postgres
#          instance on GCP Cloud SQL. Runs in batches to avoid memory issues.

import sqlite3
import psycopg2
import os

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

# Path to local SQLite database.
SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "xc.db")

# Postgres connection string — replace PASSWORD with your scraper user password.
from config import PG_CONFIG

# How many rows to insert per batch. 10,000 is safe for memory.
BATCH_SIZE = 10000

# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def getSQLiteConn():
    # Opens a read-only connection to the local SQLite database.
    conn = sqlite3.connect(SQLITE_PATH)
    # Row factory makes rows behave like dicts so we can access
    # columns by name instead of index.
    conn.row_factory = sqlite3.Row
    return conn


def getPGConn():
    # Opens a connection to the Postgres instance on GCP.
    return psycopg2.connect(**PG_CONFIG)

# Generic helper that migrates one table from SQLite to Postgres.
# Arguments:
#   table: name of the SQLite table to read from
#   insert_sql: the Postgres INSERT statement to run
#   row_to_tuple: function that converts a sqlite3.Row to a tuple
#                 matching the INSERT statement's placeholders
def migrateTable(sqlite_conn, pg_conn, table: str, insert_sql: str, 
                 row_to_tuple):
    
    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    # Count total rows for progress reporting.
    sqlite_cur.execute(f"SELECT COUNT(*) FROM {table}")
    total = sqlite_cur.fetchone()[0]
    print(f"Migrating {table}: {total} rows...")

    #Read and insert in batches to avoid loading everything into memory.
    offset = 0
    inserted = 0

    while offset < total:
        # Read one batch from SQLite.
        sqlite_cur.execute(
            f"SELECT * FROM {table} LIMIT ? OFFSET ?",
            (BATCH_SIZE, offset)
        )
        rows = sqlite_cur.fetchall()

        if not rows:
            break

        # Convert rows to tuples and insert into Postgres.
        # executemany() inserts all rows in one round trip — faster than
        # calling execute() in a loop.
        pg_cur.executemany(insert_sql, [row_to_tuple(r) for r in rows])
        pg_conn.commit()

        inserted += len(rows)
        offset += BATCH_SIZE
        print(f"  {table}: {inserted}/{total} rows inserted")

    print(f"  {table}: done.")

# ─────────────────────────────────────────────────────────────────────────────
# Table migration functions
# ─────────────────────────────────────────────────────────────────────────────

def migrateAthletes(sqlite_conn, pg_conn):
    migrateTable(
        sqlite_conn, pg_conn,
        table="athletes",
        insert_sql="""
            INSERT INTO athletes (athlete_id, first_name, last_name, gender, school)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (athlete_id) DO NOTHING
        """,
        # Converts a sqlite3.Row to a tuple matching the INSERT placeholders.
        # ON CONFLICT DO NOTHING skips duplicates safely.
        row_to_tuple=lambda r: (
            r["athlete_id"], r["first_name"], r["last_name"],
            r["gender"], r["school"]
        )
    )

def migrateMeets(sqlite_conn, pg_conn):
    migrateTable(
        sqlite_conn, pg_conn,
        table="meets",
        insert_sql="""
            INSERT INTO meets (div_id, meet_id, meet_name, course_name, 
                             distance, gps_lat, gps_long, state)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (div_id) DO NOTHING
        """,
        row_to_tuple=lambda r: (
            r["div_id"], r["meet_id"], r["meet_name"], r["course_name"],
            r["distance"], r["gps_lat"], r["gps_long"], r["state"]
        )
    )

def migrateResults(sqlite_conn, pg_conn):
    migrateTable(
        sqlite_conn, pg_conn,
        table="results",
        insert_sql="""
            INSERT INTO results (result_id, athlete_id, meet_id, div_id,
                               time_seconds, grade, date, normalized_time,
                               speed_rating)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (result_id) DO NOTHING
        """,
        row_to_tuple=lambda r: (
            r["result_id"], r["athlete_id"], r["meet_id"], r["div_id"],
            r["time_seconds"], r["grade"], r["date"],
            r["normalized_time"], r["speed_rating"]
        )
    )

def migrateMeetQueue(sqlite_conn, pg_conn):
    migrateTable(
        sqlite_conn, pg_conn,
        table="meet_queue",
        insert_sql="""
            INSERT INTO meet_queue (meet_id, sport, scraped)
            VALUES (%s, %s, %s)
            ON CONFLICT (meet_id) DO NOTHING
        """,
        row_to_tuple=lambda r: (r["meet_id"], r["sport"], r["scraped"])
    )

def migrateMeetsTF(sqlite_conn, pg_conn):
    migrateTable(
        sqlite_conn, pg_conn,
        table="meets_tf",
        insert_sql="""
            INSERT INTO meets_tf (div_id, meet_id, meet_name, event_short,
                                event_id, distance_meters, gps_lat, gps_long,
                                state, is_indoor)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (div_id, event_id) DO NOTHING
        """,
        row_to_tuple=lambda r: (
            r["div_id"], r["meet_id"], r["meet_name"], r["event_short"],
            r["event_id"], r["distance_meters"], r["gps_lat"], r["gps_long"],
            r["state"], r["is_indoor"]
        )
    )

def migrateResultsTF(sqlite_conn, pg_conn):
    migrateTable(
        sqlite_conn, pg_conn,
        table="results_tf",
        insert_sql="""
            INSERT INTO results_tf (result_id, athlete_id, meet_id, div_id,
                                  event_id, event_short, time_seconds, grade,
                                  date, is_relay, normalized_time, speed_rating)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (result_id) DO NOTHING
        """,
        row_to_tuple=lambda r: (
            r["result_id"], r["athlete_id"], r["meet_id"], r["div_id"],
            r["event_id"], r["event_short"], r["time_seconds"], r["grade"],
            r["date"], r["is_relay"], r["normalized_time"], r["speed_rating"]
        )
    )

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to SQLite...")
    sqlite_conn = getSQLiteConn()

    print("Connecting to Postgres...")
    pg_conn = getPGConn()

    # Migrate tables in dependency order — athletes and meets before results
    # since results reference them via athlete_id and meet_id.
    migrateAthletes(sqlite_conn, pg_conn)
    migrateMeets(sqlite_conn, pg_conn)
    migrateResults(sqlite_conn, pg_conn)
    migrateMeetQueue(sqlite_conn, pg_conn)
    migrateMeetsTF(sqlite_conn, pg_conn)
    migrateResultsTF(sqlite_conn, pg_conn)

    sqlite_conn.close()
    pg_conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    main()
    
