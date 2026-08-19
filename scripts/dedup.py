#!/usr/bin/env python3
# Project: xc-predictor
# File:    dedup/build_entity_links_fast.py
# Purpose: The SAME dedup as build_entity_links.py, but set-based inside Postgres
#          instead of row-by-row in Python. The slow version pulled ~100M rows over
#          the wire and normalized/matched each in Python; this pushes ALL of that
#          into the database as one join per sport. Hours -> minutes.
#
#          IT PRODUCES THE SAME TWO TABLES:
#            athlete_meet_match  - one row per (athlete-pair, meet-pair) co-appearance
#            entity_links        - the meet + athlete links at MIN_SHARED_RESULTS
#          so rebuild_entity_links.py, inspect, merge, and fuzzy_pass all work
#          unchanged on top of it.
#
#          FAITHFULNESS (where it matches the Python matcher, exactly):
#            - same finisher filters (non-finishers out, anet 999999 sentinel out)
#            - same relay filter (names with '<', 'relay', blank, 'team' excluded)
#            - same test-meet year guard (plausible years only)
#            - same name key: lower -> de-accent -> drop [.,] -> drop jr/sr/ii-iv
#              -> collapse spaces (dedup_norm_name mirrors _normName)
#            - same time rule: |anet_time - tfrrs_time| <= 0.3 on 0.1s-rounded times
#            - WITHIN-YEAR ONLY: the join is keyed on year too, so it can't pair an
#              anet 2005 race with a tfrrs 2010 race -- exactly what per-year
#              chunking enforced in Python.
#
#          ONE INTENTIONAL IMPROVEMENT: meet confidence here is DISTINCT shared
#          finishers (via DISTINCT in athlete_meet_match), not the Python build's
#          raw row-count -- so a runner in two events at one meet counts once, not
#          twice. This is the cleaner definition rebuild_entity_links.py already
#          uses; the two are now consistent.
#
#          FUZZY is still a separate fast pass (fuzzy_pass.py) run AFTER this.
#
#          Run:  python scripts/build_entity_links_fast.py
#                python scripts/build_entity_links_fast.py --gate 3   (different gate)

import argparse
import sys
import time

sys.path.insert(0, "scripts")
from database import getConn

SPORTS = {"TF": "results_tf", "XC": "results"}
MIN_SHARED_RESULTS = 5
MIN_OK_YEAR, MAX_OK_YEAR = 1900, 2030
TIME_BAND = 0.35            # < 0.35 on 0.1s-rounded times == |dt| <= 0.3 inclusive


# ================================================================== #
# CHUNK 1 -- A RUNNER THAT TIMES + REPORTS EACH STEP
# ================================================================== #


# _run
# Purpose: Execute one statement on the shared cursor, print how long it took and
#          how many rows it touched. Progress for a mostly-SQL script.
# Arguments: cur (the ONE cursor -- temp tables live on its connection);
#            label (human step name); sql; params.
# Output:  cur.rowcount.
def _run(cur, label, sql, params=()):
    t0 = time.time()
    cur.execute(sql, params)
    dt = time.time() - t0
    n = cur.rowcount
    print(f"  [{dt:6.1f}s] {label}" + (f"  ({n:,} rows)" if n is not None and n >= 0 else ""))
    return n


# ================================================================== #
# CHUNK 2 -- THE NORMALIZATION FUNCTION (dedup_norm_name == _normName)
# ================================================================== #


# _createNormFunction
# Purpose: Define dedup_norm_name(text) in SQL so the DB canonicalizes names the
#          SAME way Python's _normName did: lower/trim, de-accent, drop [.,], drop
#          generational suffixes, collapse whitespace. IMMUTABLE so the planner can
#          reuse it freely.
def _createNormFunction(cur):
    _run(cur, "create dedup_norm_name()", """
        CREATE OR REPLACE FUNCTION dedup_norm_name(raw text) RETURNS text AS $$
          SELECT btrim(
                   regexp_replace(                              -- 5. collapse spaces
                     regexp_replace(                            -- 4. drop suffixes
                       regexp_replace(                          -- 3. drop . and ,
                         translate(lower(btrim(raw)),           -- 1-2. lower, de-accent
                           'áàâäãéèêëíìîïóòôöõúùûüñç',
                           'aaaaaeeeeiiiiooooouuuunc'),
                         '[.,]', '', 'g'),
                       '\\y(jr|sr|ii|iii|iv)\\y', '', 'g'),
                     '\\s+', ' ', 'g'))
        $$ LANGUAGE sql IMMUTABLE;
    """)


# ================================================================== #
# CHUNK 3 -- OUTPUT TABLES (create if missing; do not drop user data)
# ================================================================== #


def _ensureTables(cur):
    _run(cur, "ensure athlete_meet_match", """
        CREATE TABLE IF NOT EXISTS athlete_meet_match (
            sport TEXT, anet_id BIGINT, tfrrs_id BIGINT,
            anet_meet BIGINT, tfrrs_meet BIGINT
        )
    """)
    _run(cur, "ensure entity_links", """
        CREATE TABLE IF NOT EXISTS entity_links (
            entity_type TEXT, sport TEXT, anet_id BIGINT, tfrrs_id BIGINT,
            confidence INTEGER, method TEXT,
            PRIMARY KEY (entity_type, sport, anet_id, tfrrs_id)
        )
    """)


# ================================================================== #
# CHUNK 4 -- PER-SPORT FINISHER TABLES (filtered + normalized)
# ================================================================== #
#
# Build a temp table of clean, normalized finishers for each side. All the
# faithfulness lives in the WHERE: finisher filter, year guard, relay filter (the
# last applied on the NORMALIZED name in the outer WHERE, since '<'/'relay'/blank
# survive normalization).


def _buildFinishers(cur, table):
    # anet: name comes from the athletes join (result rows lack a reliable name).
    _run(cur, "drop old temps", "DROP TABLE IF EXISTS anet_fin; DROP TABLE IF EXISTS tfrrs_fin;")
    _run(cur, "build anet_fin", f"""
        CREATE TEMP TABLE anet_fin AS
        SELECT * FROM (
            SELECT r.athlete_id AS anet_id, r.meet_id AS anet_meet,
                   left(r.date,4) AS yr,
                   dedup_norm_name(coalesce(a.first_name,'')||' '||coalesce(a.last_name,'')) AS nm,
                   round(r.time_seconds::numeric, 1) AS rt
            FROM {table} r
            LEFT JOIN athletes a ON a.athlete_id = r.athlete_id AND a.school = r.school
            WHERE r.source = 'anet'
              AND r.time_seconds IS NOT NULL AND r.time_seconds <> 999999
              AND r.place IS NOT NULL AND r.place <> 0
              AND r.date IS NOT NULL AND left(r.date,4) ~ '^[0-9]{{4}}$'
              AND left(r.date,4)::int BETWEEN {MIN_OK_YEAR} AND {MAX_OK_YEAR}
        ) s
        WHERE nm <> '' AND position('<' in nm) = 0
          AND nm NOT LIKE '%%relay%%' AND nm <> 'team'
    """)
    # tfrrs: name is on the result row (athlete_name); no join needed.
    _run(cur, "build tfrrs_fin", f"""
        CREATE TEMP TABLE tfrrs_fin AS
        SELECT * FROM (
            SELECT r.native_id AS tfrrs_id, r.meet_id AS tfrrs_meet,
                   left(r.date,4) AS yr,
                   dedup_norm_name(r.athlete_name) AS nm,
                   round(r.time_seconds::numeric, 1) AS rt
            FROM {table} r
            WHERE r.source = 'tfrrs'
              AND r.time_seconds IS NOT NULL
              AND r.place IS NOT NULL AND r.place <> 0
              AND r.date IS NOT NULL AND left(r.date,4) ~ '^[0-9]{{4}}$'
              AND left(r.date,4)::int BETWEEN {MIN_OK_YEAR} AND {MAX_OK_YEAR}
        ) s
        WHERE nm <> '' AND position('<' in nm) = 0
          AND nm NOT LIKE '%%relay%%' AND nm <> 'team'
          AND tfrrs_id IS NOT NULL
    """)
    # stats so the planner picks a good join; no indexes needed for the hash join.
    _run(cur, "analyze anet_fin", "ANALYZE anet_fin")
    _run(cur, "analyze tfrrs_fin", "ANALYZE tfrrs_fin")


# ================================================================== #
# CHUNK 5 -- THE MATCH (the join that replaces the Python loop)
# ================================================================== #


# _match
# Purpose: Insert this sport's (athlete-pair, meet-pair) co-appearances into
#          athlete_meet_match. Join on YEAR + normalized NAME + a 0.3s time band.
#          DISTINCT collapses a runner's multiple events at one meet to one row.
def _match(cur, sport):
    _run(cur, f"clear {sport} rows in athlete_meet_match",
         "DELETE FROM athlete_meet_match WHERE sport = %s", (sport,))
    _run(cur, f"match {sport} (join anet<->tfrrs)", f"""
        INSERT INTO athlete_meet_match (sport, anet_id, tfrrs_id, anet_meet, tfrrs_meet)
        SELECT DISTINCT %s, a.anet_id, t.tfrrs_id, a.anet_meet, t.tfrrs_meet
        FROM anet_fin a
        JOIN tfrrs_fin t
          ON a.yr = t.yr
         AND a.nm = t.nm
         AND abs(a.rt - t.rt) < {TIME_BAND}
        WHERE a.anet_id IS NOT NULL AND t.tfrrs_id IS NOT NULL
    """, (sport,))


# ================================================================== #
# CHUNK 6 -- ENTITY_LINKS (confirm meets at the gate; score athletes)
# ================================================================== #
#
# Identical logic to rebuild_entity_links.py: DISTINCT the raw rows, count distinct
# athletes per meet pair (=shared finishers), keep those >= gate as meet links, and
# count distinct confirmed meets per athlete pair as athlete confidence.


def _buildEntityLinks(cur, gate):
    _run(cur, "index athlete_meet_match",
         "CREATE INDEX IF NOT EXISTS idx_amm ON athlete_meet_match (sport, anet_meet, tfrrs_meet)")
    _run(cur, "truncate entity_links", "TRUNCATE entity_links")
    _run(cur, f"build entity_links (gate {gate})", """
        WITH am AS (
            SELECT DISTINCT sport, anet_id, tfrrs_id, anet_meet, tfrrs_meet
            FROM athlete_meet_match
        ),
        meet_shared AS (
            SELECT sport, anet_meet, tfrrs_meet, count(*) AS shared
            FROM am GROUP BY sport, anet_meet, tfrrs_meet
        ),
        confirmed AS (
            SELECT sport, anet_meet, tfrrs_meet, shared
            FROM meet_shared WHERE shared >= %(gate)s
        ),
        athlete_conf AS (
            SELECT am.sport, am.anet_id, am.tfrrs_id, count(*) AS confidence
            FROM am JOIN confirmed c USING (sport, anet_meet, tfrrs_meet)
            WHERE am.anet_id IS NOT NULL AND am.tfrrs_id IS NOT NULL
            GROUP BY am.sport, am.anet_id, am.tfrrs_id
        )
        INSERT INTO entity_links (entity_type, sport, anet_id, tfrrs_id, confidence, method)
        SELECT 'meet', sport, anet_meet, tfrrs_meet, shared, 'shared-results' FROM confirmed
        UNION ALL
        SELECT 'athlete', sport, anet_id, tfrrs_id, confidence, 'confirmed-meet-finisher'
        FROM athlete_conf
        ON CONFLICT (entity_type, sport, anet_id, tfrrs_id)
            DO UPDATE SET confidence = EXCLUDED.confidence
    """, {"gate": gate})


# ================================================================== #
# CHUNK 7 -- DRIVER (ONE connection: temp tables must survive between steps)
# ================================================================== #


def _parseArgs():
    p = argparse.ArgumentParser(description="Fast set-based dedup build.")
    p.add_argument("--gate", type=int, default=MIN_SHARED_RESULTS,
                   help=f"shared-finisher minimum for a meet (default {MIN_SHARED_RESULTS}).")
    p.add_argument("--sport", choices=["TF", "XC"], help="one sport (default: both).")
    return p.parse_args()


def main():
    args = _parseArgs()
    sports = {args.sport: SPORTS[args.sport]} if args.sport else SPORTS
    t0 = time.time()
    print(f"=== fast dedup build (gate {args.gate}) ===")

    # ONE connection for the whole run: temp tables (anet_fin/tfrrs_fin) only exist
    # for the connection that made them, so every step shares this cursor.
    with getConn() as conn:
        cur = conn.cursor()
        # give the big hash join and sorts room, and let it parallelize.
        cur.execute("SET work_mem = '1GB'")
        cur.execute("SET max_parallel_workers_per_gather = 4")

        _createNormFunction(cur)
        _ensureTables(cur)

        for sport, table in sports.items():
            print(f"\n--- {sport} ({table}) ---")
            _buildFinishers(cur, table)
            _match(cur, sport)

        print("\n--- entity_links ---")
        _buildEntityLinks(cur, args.gate)

        conn.commit()   # getConn does not autocommit; one commit at the end

    print(f"\n=== done in {time.time()-t0:.0f}s. "
          f"Run fuzzy_pass.py next, then inspect + merge. ===")


if __name__ == "__main__":
    main()