# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Database
# Date: 6/6/2026
# File Title: migrate_to_postgres.py
# Purpose: One-time migration script that reads all data from the local
#          SQLite database (xc.db) and writes it to the shared Postgres
#          instance on GCP Cloud SQL. Runs in batches to avoid memory issues.

import os
import sqlite3
import psycopg2
import psycopg2.extras


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
def migrateTable(sqlite_conn, pg_conn, table: str, columns: str, 
                 row_to_tuple, conflict_column: str = None):
    
    # Uses PostgreSQL COPY for bulk loading instead of INSERT.
    # COPY streams all rows in one shot — much faster than executemany
    # which does one round trip per batch.

    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    # Count total rows for progress reporting.
    sqlite_cur.execute(f"SELECT COUNT(*) FROM {table}")
    total = sqlite_cur.fetchone()[0]
    print(f"Migrating {table}: {total} rows...")

    # Read ALL rows from SQLite at once — 2.2GB fits in RAM fine.
    sqlite_cur.execute(f"SELECT * FROM {table}")
    rows = sqlite_cur.fetchall()

    # Convert all rows to tuples.
    tuples = [row_to_tuple(r) for r in rows]

    # Use execute_values for fast bulk insert — sends all rows in one
    # network round trip instead of one per batch.
    # %s in the template is replaced with (val1, val2, ...) per row.
    psycopg2.extras.execute_values(
        pg_cur, # The cursor to run the query on.
        # Values %s is a placeholder for all rows, expands it into
        # values automatically. Join puts the column strings into
        # a list so we can send all as a list.
        f"""
            INSERT INTO {table} ({', '.join(columns)})
            VALUES %s
            ON CONFLICT DO NOTHING
        """,
        tuples,
        page_size=10000  # How many rows per network packet.
    )

    pg_conn.commit()
    print(f"  {table}: {total} rows inserted.")

# ─────────────────────────────────────────────────────────────────────────────
# Table migration functions
# ─────────────────────────────────────────────────────────────────────────────

def migrateAthletes(sqlite_conn, pg_conn):
    migrateTable(
        sqlite_conn, pg_conn,
        table="athletes",
        columns=["athlete_id", "first_name", "last_name", "gender", "school"],
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
        columns=["div_id", "meet_id", "meet_name", "course_name",
                "distance", "gps_lat", "gps_long", "state"],
        row_to_tuple=lambda r: (
            r["div_id"], r["meet_id"], r["meet_name"], r["course_name"],
            r["distance"], r["gps_lat"], r["gps_long"], r["state"]
        )
    )

def migrateResults(sqlite_conn, pg_conn):
    migrateTable(
        sqlite_conn, pg_conn,
        table="results",
        columns=["result_id", "athlete_id", "meet_id", "div_id",
                "time_seconds", "grade", "date", "normalized_time",
                "speed_rating"],
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
        columns=["meet_id", "sport", "scraped"],
        row_to_tuple=lambda r: (r["meet_id"], r["sport"], r["scraped"])
    )

def migrateMeetsTF(sqlite_conn, pg_conn):
    migrateTable(
        sqlite_conn, pg_conn,
        table="meets_tf",
        columns=["div_id", "meet_id", "meet_name", "event_short",
                "event_id", "distance_meters", "gps_lat", "gps_long",
                "state", "is_indoor"],
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
        columns=["result_id", "athlete_id", "meet_id", "div_id",
                "event_id", "event_short", "time_seconds", "grade",
                "date", "is_relay", "normalized_time", "speed_rating"],
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
    
