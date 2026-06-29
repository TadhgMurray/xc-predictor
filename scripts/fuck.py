from database import getConn

# ─── Chunk 0: sport value discovery ────────────────────────────────────────

# getSportBreakdown
# Purpose: Shows every distinct value in meet_queue.sport along with
#          scraped/unscraped counts for each — tells us what values
#          actually exist (e.g. 'tf' vs 'TF' vs 'track', and whether
#          XC uses this table at all).
# Arguments:
#           cursor: an open database cursor.
# Output: None — prints directly.
def getSportBreakdown(cursor):
    cursor.execute(
        "SELECT sport, source, scraped, count(*) FROM meet_queue "
        "GROUP BY sport, source, scraped ORDER BY sport, source, scraped"
    )

    print("--- meet_queue breakdown (sport, scraped, count) ---")
    for sport, source, scraped_flag, n in cursor.fetchall():
        print(f"  sport={sport!r}, source={source!r}, scraped={scraped_flag}: {n}")

# ─── Chunk 1: meet_queue stats ─────────────────────────────────────────────
# meet_queue has one row per meet, with `sport` ('tf' or 'xc') and
# `scraped` (0 = not yet scraped, 1 = scraped successfully).

# getMeetQueueCounts
# Purpose: Counts total/scraped/unscraped TF meets in meet_queue.
# Arguments:
#           cursor: an open database cursor.
# Output: A tuple (total, scraped, unscraped) of integers.
def getMeetQueueCounts(cursor) -> tuple[int, int, int]:
    cursor.execute(
        "SELECT scraped, count(*) FROM meet_queue "
        "WHERE sport = 'TF' GROUP BY scraped"
    )

    # Build a dict like {0: 12345, 1: 67890} from the result rows, so
    # we don't depend on row ORDER — GROUP BY doesn't guarantee one.
    counts = {scraped_flag: n for scraped_flag, n in cursor.fetchall()}

    # .get(key, 0) returns 0 if that flag value didn't appear at all
    # (e.g. if every meet is scraped, there'd be no "0" row).
    unscraped = counts.get(0, 0)
    scraped = counts.get(1, 0)
    total = unscraped + scraped

    return total, scraped, unscraped


# ─── Chunk 2: tf_recovery_queue stats ──────────────────────────────────────
# tf_recovery_queue tracks individual (meet, event, div) combos that
# failed and need retrying. `scraped` here means "recovery succeeded".

# getRecoveryQueueCounts
# Purpose: Counts total/recovered/still-pending entries in the recovery
#          queue.
# Arguments:
#           cursor: an open database cursor.
# Output: A tuple (total, recovered, pending) of integers.
def getRecoveryQueueCounts(cursor) -> tuple[int, int, int]:
    cursor.execute(
        "SELECT scraped, count(*) FROM tf_recovery_queue GROUP BY scraped"
    )

    counts = {scraped_flag: n for scraped_flag, n in cursor.fetchall()}

    pending = counts.get(0, 0)
    recovered = counts.get(1, 0)
    total = pending + recovered

    return total, recovered, pending


# ─── Chunk 3: results count ────────────────────────────────────────────────

# getTotalResults
# Purpose: Counts total TF result rows saved so far — a rough sense of
#          data volume, separate from meet-level progress.
# Arguments:
#           cursor: an open database cursor.
# Output: An integer count.
def getTotalResults(cursor) -> int:
    cursor.execute("SELECT count(*) FROM results")
    xc = cursor.fetchone()[0]
    cursor.execute("SELECT count(*) FROM results_tf")
    tf = cursor.fetchone()[0]
    return xc + tf


# ─── Chunk 4: report ────────────────────────────────────────────────────────

# printReport
# Purpose: Pulls all counts together and prints a human-readable
#          summary with percentages.
# Arguments: None.
# Output: None — prints directly.
def printReport():
    with getConn() as conn:
        cursor = conn.cursor()

        getSportBreakdown(cursor)

        total, scraped, unscraped = getMeetQueueCounts(cursor)
        rec_total, rec_done, rec_pending = getRecoveryQueueCounts(cursor)
        total_results = getTotalResults(cursor)

        pct = (scraped / total * 100) if total else 0

        print("===== TF Scraping Progress =====")
        print(f"Total TF meets:        {total:>8}")
        print(f"Scraped:               {scraped:>8}")
        print(f"Unscraped (remaining): {unscraped:>8}")
        print(f"Percent complete:      {pct:>7.2f}%")
        print()
        print("--- Recovery queue ---")
        print(f"Total entries:         {rec_total:>8}")
        print(f"Recovered:             {rec_done:>8}")
        print(f"Still pending:         {rec_pending:>8}")
        print()
        print(f"Total TF results saved: {total_results:>8}")
        print("=================================")


# ─── Chunk 5: entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    printReport()