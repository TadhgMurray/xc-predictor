# Project: xc-predictor
# File:    tfrrs/reset_tfrrs_for_rescrape.py
# Purpose: Reset every tfrrs meet in meet_queue back to scraped=0 so the FIXED
#          pipeline (meta-save + TF score + officials/championship) re-drains all
#          of them. Scoped to source='tfrrs' so it NEVER touches anet rows. Old
#          results_tf rows are left in place — the re-scrape upserts over them by
#          content-hashed result_id (option 2: no missing-data window). Prints
#          before/after counts. WRITES (queue status only); prompts first.

import sys
sys.path.insert(0, "scripts")

from database import initPool, getConn, closePool


# tfrrsQueueCounts
# Purpose: Count tfrrs meets by (sport, scraped) so the reset's effect is visible
#          before and after. Scoped to source='tfrrs'.
# Arguments:
#           conn: open connection
# Output:  list of (sport, scraped, count) rows
def tfrrsQueueCounts(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT sport, scraped, COUNT(*)
            FROM   meet_queue
            WHERE  source = 'tfrrs'
            GROUP  BY sport, scraped
            ORDER  BY sport, scraped
        """)
        return cur.fetchall()


# resetTFRRSToUnscraped
# Purpose: Flip every tfrrs row (any status) back to scraped=0. Scoped to
#          source='tfrrs'. Returns rows changed.
# Arguments:
#           conn: open connection (caller commits)
# Output:  int rows updated
def resetTFRRSToUnscraped(conn):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE meet_queue
            SET    scraped = 0
            WHERE  source = 'tfrrs' AND scraped <> 0
        """)
        return cur.rowcount


# main
def main():
    initPool()
    try:
        with getConn() as conn:
            print("===== tfrrs meet_queue BEFORE =====")
            for sport, status, n in tfrrsQueueCounts(conn):
                print(f"    sport={sport!r} scraped={status}: {n:,}")

        confirm = input("\nReset ALL tfrrs meets to scraped=0? Type 'yes': ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            return

        with getConn() as conn:
            changed = resetTFRRSToUnscraped(conn)
            conn.commit()
            print(f"\nReset {changed:,} tfrrs rows to scraped=0.")

            print("\n===== tfrrs meet_queue AFTER =====")
            for sport, status, n in tfrrsQueueCounts(conn):
                print(f"    sport={sport!r} scraped={status}: {n:,}")

        print("\nReady. Launch the TFRRS drain (launch_tfrrs.py) to re-scrape with")
        print("the fixed pipeline. Old results_tf rows upsert over by result_id.")

    finally:
        closePool()


if __name__ == "__main__":
    main()