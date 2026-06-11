# # Project: xc-predictor
# Author: Tadhg Murray
# Subset: Infrastructure
# Date: 6/7/2026
# File Title: backup.py
# Purpose: Dumps the xc_predictor PostgreSQL database to a compressed file
#          and uploads it to GCP Cloud Storage. Deletes backups older than
#          90 days from the bucket automatically.
#          Run manually or via cron job on the VM.
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth application-default login)
#   - Cloud SQL Auth Proxy running on localhost:5432
#   - GCP Cloud Storage bucket created (see setup instructions below)
#   - pg_dump installed (comes with PostgreSQL client tools)
#
# ─────────────────────────────────────────────────────────────────────────────
# One-time bucket setup (run these in your terminal once):
#
#   gcloud storage buckets create gs://xc-predictor-backups \
#       --project=project-d8c4b484-c8fa-4e09-9fc \
#       --location=us-west1 \
#       --uniform-bucket-level-access
#
# ─────────────────────────────────────────────────────────────────────────────

import subprocess  # Lets Python run external command-line programs, like pg_dump.
import sys
import os
os.environ["PGPASSWORD"] = "Tigger5959!" # Adds password via enviromental variables so pg_dump doesn't prompt for it.
import datetime

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

# The GCP project that owns the Cloud Storage bucket.
# This is how GCP knows which account to bill and which permissions to check
GCP_PROJECT = "project-d8c4b484-c8fa-4e09-9fc"

# The name of the Cloud Storage bucket we're uploading backups to.
BUCKET_NAME = "xc-predictor-backups"

# The full URI (address) of the bucket. GCS uses the gs:// scheme instead
# of https:// — it's Google's own protocol for accessing Cloud Storage.
# Think of it like a file path but in the cloud: gs://bucket-name/filename.
BUCKET_URI = f"gs://{BUCKET_NAME}"

# Database connection details. We connect through the Cloud SQL Auth Proxy
# which listens on localhost (127.0.0.1) and forwards to Cloud SQL.
# 127.0.0.1 always means "this machine" — it never goes over the network.
DB_HOST = "127.0.0.1"

# The port the proxy is listening on. PostgreSQL always uses 5432 by default.
# A port is like a door number on a building — the host is the building,
# the port is which door to knock on.
DB_PORT = "5432"

# The name of the db inside Postgres and the user to connect as.
DB_NAME = "xc_predictor"
DB_USER = "scraper"

# How many days of backups to keep before deleting old ones.
# 90 days = 3 months of history. Any backup file older than this
# gets automatically deleted from the bucket to save storage costs.
RETENTION_DAYS = 90

# Path to the GCloud cmd file.
GCLOUD_PATH = r"C:\Users\TADHGM~1\AppData\Local\Google\CLOUDS~1\GOOGLE~1\bin\gcloud.cmd"


# Where to temporarily store the dump file on disk before uploading.
# os.path.abspath(__file__) gets the full path of THIS script file.
# os.path.dirname() strips the filename, leaving just the directory.
# So SCRIPT_DIR is whatever folder backup.py lives in — in our case
# the infrastructure/ folder. This means the temp file lands next to
# the script, not wherever you happen to run the command from.
SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
TEMP_DUMP_PATH = os.path.join(SCRIPT_DIR, "temp_backup.dump")

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# _getTimestamp
# Purpose: Returns today's date as a string for the backup filename.
# Arguments: None.
# Output: String in format "YYYY_MM_DD".
def _getTimestamp() -> str:

    # strfttime converts a datetime object into a string using
    # specified format.
    return datetime.datetime.now(datetime.UTC).strftime("%Y_%m_%d")

# _getBackupFilename
# Purpose: Builds the timestamped filename for this backup.
# Arguments: None.
# Output: String e.g. "backup_2026_06_07.dump".
def _getBackupFilename() -> str:

    # .dump is the file extension for PostgreSQL custom-format dump files.
    # It's not a standard extension like .zip or .txt — it's just convention
    # for pg_dump output so you know what program created it.
    return f"backup_{_getTimestamp()}.dump"

# _runCommand
# Purpose: Runs an external command-line program from Python.
# Arguments:
#           cmd: list of strings representing the command and its arguments.
#                e.g. ["pg_dump", "-h", "127.0.0.1", "-U", "scraper"]
#                Each space-separated word in a terminal command becomes
#                a separate string in the list.
#           label: short description printed to the console for logging.
# Output: None. Calls sys.exit(1) on failure, which stops the whole script.
def _runCommand(cmd: list, label: str):

    print(f"[backup] {label}...")

    # Launches an external program from Python. Like typing
    # the command in the terminal. cmd is the list of parts
    # that is joined automatically, and the other two args
    # say to return the output as a text string.
    result = subprocess.run(cmd, capture_output=True, text=True)

    # Every program that runs exits with a return code when it
    # finishes. 0 means success. Anything else means something went
    # wrong. We check that here.
    if result.returncode != 0:
        print(f"[backup] FAILED: {label}")
 
        # stderr is the "error output" stream — programs write error messages
        # here separately from normal output (stdout). .strip() removes
        # leading/trailing whitespace.
        print(f"[backup] Error: {result.stderr.strip()}")
 
        # sys.exit(1) immediately stops the entire Python script.
        # 1 signals failure (same convention as return codes — 0 = success).
        # We stop here because if pg_dump failed, there's nothing to upload,
        # and if the upload failed, there's nothing to clean up.
        sys.exit(1)

    # Prints command output if it's given.
    if result.stdout.strip():
        print(f"[backup] {result.stdout.strip()}")

# _cleanTempFile
# Purpose: Deletes the temporary local dump file.
# Arguments: None.
# Output: None.
def _cleanupTempFile():

    # Checks whether a file exists at the given path. If it
    # does exist we delete it from the disk.
    if os.path.exists(TEMP_DUMP_PATH):
        os.remove(TEMP_DUMP_PATH)
        print(f"[backup] Temp file deleted")

# ─────────────────────────────────────────────────────────────────────────────
# Backup steps
# ─────────────────────────────────────────────────────────────────────────────

# _dumpDatabase
# Purpose: Runs pg_dump to export the entire databse to a compressed
#          local file. pg_dump contains all the INSERT INTO statements
#          for every row, which includes the data.
# Arguments: None.
# Output: None. Creates a file at TEMP_DUMP_PATH on disk.
def _dumpDatabase():

    
    # pg_dump flags explained:
    #   -h  host to connect to (127.0.0.1 = through the proxy)
    #   -p  port to connect on (5432 = PostgreSQL default)
    #   -U  PostgreSQL username to connect as
    #   -d  name of the database to dump
    #   -Fc custom format — this is PostgreSQL's own compressed binary format.
    #       It's smaller than plain SQL and can be restored with pg_restore.
    #       Plain SQL (-Fp) would be a huge text file with millions of INSERT
    #       statements. Custom format compresses it and is faster to restore.
    #   -f  output file path to write the dump to
    _runCommand([
        "pg_dump",
        "-h", DB_HOST,
        "-p", DB_PORT,
        "-U", DB_USER,
        "-d", DB_NAME,
        "-Fc",
        "-f", TEMP_DUMP_PATH
    ], "Dumping database")

# _uploadToGCS
# Purpose: Uploads the local dump file to the GCS bucket using the gcloud CLI.
# Arguments:
#           filename: the destination filename inside the bucket
#                     e.g. "backup_2026_06_07.dump".
# Output: None.
def _uploadToGCS(filename: str):

    # Build the full destination URI by combining the bucket URI and filename.
    # e.g. gs://xc-predictor-backups/backup_2026_06_07.dump
    destination = f"{BUCKET_URI}/{filename}"

    # gcloud storage cp is the Cloud Storage copy command.
    # It works like the Unix cp command but one of the paths is a GCS URI.
    # Source is the local file, destination is the GCS location.
    # --project tells gcloud which GCP project to bill the storage to.
    _runCommand([
        GCLOUD_PATH, "storage", "cp",
        TEMP_DUMP_PATH,
        destination,
        f"--project={GCP_PROJECT}"
    ], f"Uploading to {destination}")

# _deleteOldBackups
# Purpose: Lists all backup files in the bucket and deltes any that are
#          older than 90 days.
# Arguments: None.
# Output: None.
def _deleteOldBackups():

    print(f"[backup] Checking for backups older than {RETENTION_DAYS} days...")

    # List all files currently in the bucket.
    # We use subprocess.run directly here instead of _runCommand because
    # we need to read the output (the list of files) rather than just
    # checking if the command succeeded.
    result = subprocess.run([
        GCLOUD_PATH, "storage", "ls",
        BUCKET_URI,
        f"--project={GCP_PROJECT}"
    ], capture_output=True, text=True)

    if result.returncode != 0:
        # Not fatal — if we can't list the bucket, just skip cleanup.
        # The backup itself already succeeded at this point.
        print(f"[backup] Could not list bucket: {result.stderr.strip()}")
        return
    
    # Calculate the cutoff date — any backup older than this gets deleted.
    # datetime.timedelta(days=RETENTION_DAYS) creates a time duration of
    # 90 days. Subtracting it from now gives us the cutoff point.
    # e.g. if today is 2026-06-07 and RETENTION_DAYS=90,
    # cutoff = 2026-03-09. Any backup from before that date gets deleted.
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=RETENTION_DAYS)

    # result.stdout is a string with one GCS URI per line, e.g.:
    # gs://xc-predictor-backups/backup_2026_01_01.dump
    # gs://xc-predictor-backups/backup_2026_02_01.dump
    # .strip() removes leading/trailing whitespace.
    # .splitlines() splits the string into a list, one item per line.
    # Basically, for each files curreently in the bucket find if
    # we need to delete it and do so.
    for line in result.stdout.strip().splitlines():

        uri = line.strip()
        if not uri:
            continue

        # Extract just the filename from the full GCS URI.
        # uri.split("/") splits on every slash, giving a list of parts.
        # [-1] takes the last part, which is the filename.
        # e.g. "gs://xc-predictor-backups/backup_2026_01_01.dump".split("/")
        # → ["gs:", "", "xc-predictor-backups", "backup_2026_01_01.dump"]
        # [-1] → "backup_2026_01_01.dump"
        filename = uri.split("/")[-1]

        # Parse the date out of the filename so we can compare it to cutoff.
        # We strip the "backup_" prefix and ".dump" suffix to get "2026_01_01",
        # then parse that string into a datetime object using strptime.
        # strptime is the reverse of strftime — it parses a string into a
        # datetime using a format pattern. "%Y_%m_%d" matches "2026_01_01".
        try:
            date_str  = filename.replace("backup_", "").replace(".dump", "")
            file_date = datetime.datetime.strptime(date_str, "%Y_%m_%d")
        except ValueError:
            # If the filename doesn't match our pattern (e.g. some other file
            # ended up in the bucket), skip it rather than crashing.
            continue

        # If the backup is older than the cutoff, delete it.
        if file_date < cutoff:
            print(f"[backup] Deleting old backup: {filename}")
            _runCommand([
                "gcloud", "storage", "rm",
                uri,
                f"--project={GCP_PROJECT}"
            ], f"Deleting {filename}")

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

# main
# Purpose: Runs the full backup pipeline in order - dump, upload, cleanup
#          old backups, delete temp file.
# Arguments: None.
# Output: None.
def main():

    filename = _getBackupFilename()
    print(f"[backup] Starting backup → {filename}")

    # try/finally guarantees _cleanupTempFile() always runs, even if
    # pg_dump or the upload fails halfway through. Without finally, a
    # failure would leave a partial dump file sitting on disk forever.
    try:

        # Step 1 - export the db to a compressed local file
        _dumpDatabase()

        # Step 2 - upload that file to GCS with a timestamped name.
        _uploadToGCS(filename)

        # Step 3 - delete backups older than 90 days from the bucket.
        _deleteOldBackups()

        print(f"[backup] Backup complete — {filename}")
 
    finally:
        # Always delete the temp file whether we succeeded or failed.
        # If pg_dump failed, the file may be empty or partial — either way
        # we don't want it sitting around taking up space.
        _cleanupTempFile()
 
 
if __name__ == "__main__":
    main()