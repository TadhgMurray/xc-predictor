"""
build_school_identity.py -- who every athlete and school string IS.

    python racecast/build_school_identity.py

Pipeline step 10b, right after 10_rankings: everything here reads
ranking_results. Two tables out:

    person_home_state (person_id PK, state)
        an athlete's HOME state = the state they race in most.
        `state` on a row is the venue's, and kids race mostly at home,
        so the mode is the home state -- one Arcadia trip cannot move
        a Utah kid.

    school_identity   (school, state, n_athletes, share, is_primary)
        each school STRING's athletes clustered by home state. One
        cluster = a normal school; two real clusters = two schools
        wearing one string ("Highland" UT and CA), which is what the
        state chips, the meet-scoring split and the search index read.

Swap discipline: build into _new, rename over the old -- readers keep
the previous tables until the new ones are whole.
"""

import sys
import time

import psycopg2.errors           # LockNotAvailable / DeadlockDetected

sys.path.insert(0, "scripts")

from database import getConn

# ! THE SAME PATIENCE build_ranking_results AND backfill_normalize USE. Three
#   seconds is long enough for a normal page and short enough that a stuck
#   one does not hold the swap; twenty attempts at fifteen seconds is five
#   minutes of trying, which outlasts any single request.
_SWAP_LOCK_TIMEOUT = "3s"
_SWAP_ATTEMPTS = 20
_SWAP_BACKOFF = 15


def main():
    t0 = time.time()
    with getConn() as conn:
        cur = conn.cursor()

        cur.execute("DROP TABLE IF EXISTS person_home_state_new")
        cur.execute("""
            CREATE TABLE person_home_state_new AS
            SELECT person_id,
                   mode() WITHIN GROUP (ORDER BY state) AS state
            FROM   ranking_results
            WHERE  person_id IS NOT NULL AND state IS NOT NULL
            GROUP  BY person_id
        """)
        cur.execute("""
            ALTER TABLE person_home_state_new
            ADD PRIMARY KEY (person_id)
        """)
        cur.execute("SELECT count(*) FROM person_home_state_new")
        print(f"  person_home_state: {cur.fetchone()[0]:,} athletes "
              f"({(time.time() - t0) / 60:.1f} min)", flush=True)

        # one vote per (school, athlete): an athlete who raced for the
        # school in five seasons is still one athlete of it
        cur.execute("DROP TABLE IF EXISTS school_identity_new")
        cur.execute("""
            CREATE TABLE school_identity_new AS
            WITH votes AS (
                SELECT DISTINCT rr.school, rr.person_id
                FROM   ranking_results rr
                WHERE  COALESCE(TRIM(rr.school), '') <> ''
                  AND  rr.person_id IS NOT NULL
            ),
            clusters AS (
                SELECT v.school, ph.state, count(*) AS n_athletes
                FROM   votes v
                JOIN   person_home_state_new ph USING (person_id)
                GROUP  BY v.school, ph.state
            )
            SELECT school, state, n_athletes,
                   round(n_athletes::numeric
                         / sum(n_athletes) OVER (PARTITION BY school),
                         4)                                   AS share,
                   (row_number() OVER (PARTITION BY school
                        ORDER BY n_athletes DESC, state) = 1) AS is_primary
            FROM   clusters
        """)
        cur.execute("""
            CREATE INDEX school_identity_new_school_idx
            ON school_identity_new (school)
        """)
        cur.execute("SELECT count(*), count(DISTINCT school) "
                    "FROM school_identity_new")
        n, ns = cur.fetchone()
        cur.execute("""
            SELECT count(*) FROM (
                SELECT school FROM school_identity_new
                WHERE n_athletes >= 3 AND share >= 0.10
                GROUP BY school HAVING count(*) >= 2
            ) x
        """)
        multi = cur.fetchone()[0]
        print(f"  school_identity: {ns:,} schools, {n:,} clusters, "
              f"{multi:,} names split across states", flush=True)

        # ---- the swap: old tables serve until the new ones are whole ----
        #
        # ⚠ THIS HAD NO RETRY AT ALL, AND IT DEADLOCKED (2026-09-01). The
        #   DROPs take ACCESS EXCLUSIVE on two tables the running site reads,
        #   in a fixed order; a page holding them in the other order is an
        #   ABBA deadlock, and Postgres kills whichever transaction it picks
        #   -- here, this one, after the whole build was already done.
        #
        # ! SAME SHAPE AS build_ranking_results.swapIn, deliberately: lock
        #   BOTH tables in one statement so the failure mode is a timeout
        #   rather than a deadlock, be impatient rather than queueing (a
        #   waiting ACCESS EXCLUSIVE makes every NEW reader queue behind it,
        #   so one slow page would freeze the site), and treat timeout and
        #   deadlock as the same retryable "a reader was in the way".
        #
        # ⚠ ALL OF IT IN ONE TRANSACTION. Half a swap leaves school_identity
        #   dropped with nothing in its place, and every page that renders a
        #   school label 500s.
        for attempt in range(1, _SWAP_ATTEMPTS + 1):
            try:
                cur.execute("BEGIN")
                cur.execute(f"SET LOCAL lock_timeout = '{_SWAP_LOCK_TIMEOUT}'")
                cur.execute("LOCK TABLE person_home_state, school_identity "
                            "IN ACCESS EXCLUSIVE MODE")
                for t in ("person_home_state", "school_identity"):
                    cur.execute(f"DROP TABLE IF EXISTS {t}")
                    cur.execute(f"ALTER TABLE {t}_new RENAME TO {t}")
                cur.execute("ALTER INDEX school_identity_new_school_idx "
                            "RENAME TO school_identity_school_idx")
                cur.execute("ALTER TABLE person_home_state RENAME CONSTRAINT "
                            "person_home_state_new_pkey TO "
                            "person_home_state_pkey")
                conn.commit()
                break
            except (psycopg2.errors.LockNotAvailable,
                    psycopg2.errors.DeadlockDetected):
                conn.rollback()
                if attempt == _SWAP_ATTEMPTS:
                    # ! THE _new TABLES SURVIVE, so a rerun redoes the build
                    #   rather than leaving the site without these tables.
                    raise RuntimeError(
                        "school_identity swap: could not take ACCESS "
                        f"EXCLUSIVE in {_SWAP_ATTEMPTS} attempts. Check "
                        "pg_stat_activity for a long read.")
                print(f"  readers hold the tables, attempt {attempt}"
                      f"/{_SWAP_ATTEMPTS} -- retrying in {_SWAP_BACKOFF}s",
                      flush=True)
                time.sleep(_SWAP_BACKOFF)

        # ANALYZE after the commit, not inside it: it takes no exclusive lock
        # and holding the swap open for it would defeat the point.
        cur.execute("ANALYZE person_home_state")
        cur.execute("ANALYZE school_identity")
        conn.commit()

    print(f"done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
