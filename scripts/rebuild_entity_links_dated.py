#!/usr/bin/env python3
# Project: xc-predictor
# File:    scripts/rebuild_entity_links_dated.py
# Purpose: Rebuild entity_links from the EXISTING athlete_meet_match table with
#          two new filters the original build lacked:
#            1. DATE GATE  -- a meet pair must be within --max-gap days
#               (the old build joined on YEAR only, so athlete consistency
#               across weeks forged ~10x fake pairs in TF).
#            2. MUTUAL BEST -- each meet's top pair must pick it back;
#               catches the same-weekend collisions the date gate can't see.
#          NO re-matching happens: the expensive name x time x year join's
#          output is already in athlete_meet_match. This script only re-derives
#          the confirmation layer. Minutes, not hours.
#
#          Missing dates PASS the date gate (you can't disqualify on absent
#          evidence -- same philosophy as geometry defaulting to the 400m
#          reference); mutual-best still applies to them.
#
#          DESTRUCTIVE ONLY TO entity_links (TRUNCATE + rebuild), which is
#          itself derived data. athlete_meet_match and all result tables are
#          untouched. After this: null the merge stamps, re-run the merge.
#
#          Run:  python scripts/rebuild_entity_links_dated.py
#                python scripts/rebuild_entity_links_dated.py --max-gap 1
#                python scripts/rebuild_entity_links_dated.py --sport TF --dry-run

import argparse
import sys
import time

sys.path.insert(0, "scripts")
from database import getConn

MIN_SHARED_RESULTS = 5      # same gate as the original build
DEFAULT_MAX_GAP    = 3      # days; census says <=1 is the efficient point,
                            # <=3 is safe-generous once mutual-best runs too
SPORTS = ("TF", "XC")


# ================================================================== #
# CHUNK 1 -- TIMED RUNNER (same pattern as build_entity_links_fast)
# ================================================================== #
# Every DB step goes through _run so the console shows a live, timed log of
# what the script is doing -- a mostly-SQL script's version of a progress bar.


# _run
# Purpose:   execute ONE SQL statement and print a timed, row-counted log line.
# Arguments: cur    -- the single shared cursor for the whole run. Shared
#                      because temp tables exist only on the connection that
#                      created them; a second connection would not see them.
#            label  -- short human text for the log line, e.g. "build gated".
#            sql    -- the statement text; %s / %(name)s placeholders allowed.
#            params -- tuple or dict feeding those placeholders; () if none.
# Output:    int -- cur.rowcount: rows the statement touched (INSERT/UPDATE/
#            DELETE) or returned (SELECT); -1 for statements with no count
#            (e.g. CREATE), which the print suppresses.
def _run(cur, label, sql, params=()):
    t0 = time.time()
    cur.execute(sql, params)
    dt = time.time() - t0
    n = cur.rowcount
    print(f"  [{dt:6.1f}s] {label}" + (f"  ({n:,} rows)" if n >= 0 else ""))
    return n


# _count
# Purpose:   fetch one scalar count -- used for stage-by-stage reporting.
# Arguments: cur -- the shared cursor.
#            sql -- a SELECT that returns exactly one row with one integer,
#                   e.g. "SELECT count(*) FROM stage_gated".
# Output:    int -- that scalar.
def _count(cur, sql):
    cur.execute(sql)
    return cur.fetchone()[0]      # fetchone() -> the row tuple; [0] -> its only column


# ================================================================== #
# CHUNK 2 -- PRECHECK
# ================================================================== #


# _precheck
# Purpose:   verify athlete_meet_match still holds the match rows this whole
#            script depends on. If someone dropped/emptied it, we want a loud
#            one-line abort, not a silent empty rebuild that nukes
#            entity_links and writes nothing back.
# Arguments: cur -- the shared cursor.
# Output:    int -- the row count (also printed). Raises SystemExit if zero.
def _precheck(cur):
    n = _count(cur, "SELECT count(*) FROM athlete_meet_match")
    print(f"  athlete_meet_match rows: {n:,}")
    if n == 0:
        sys.exit("  ABORT: athlete_meet_match is empty -- re-run "
                 "build_entity_links_fast.py first.")
    return n


# ================================================================== #
# CHUNK 3 -- DATE LOOKUP TEMP TABLES
# ================================================================== #
# One temp table per side. Dates in the DB are TEXT of mostly-ISO shape; each
# is regex-guarded before ::date so one malformed row can't crash the cast.
# left(x,10) trims any trailing time component before casting.


# _buildTfrrsDates
# Purpose:   build tfrrs_dt: each tfrrs meet's date RANGE. A range, not a
#            point, because championships span 2-3 days -- an anet date
#            landing anywhere inside it should count as gap 0.
# Arguments: cur   -- the shared cursor.
#            sport -- 'TF' or 'XC'; meets_tfrrs is keyed (meet_id, sport), so
#                     the temp table is rebuilt per sport pass.
# Output:    none. Side effect: temp table tfrrs_dt(meet_id, d0, d1) where
#            d0 = start date, d1 = end date (= d0 for single-day meets).
def _buildTfrrsDates(cur, sport):
    _run(cur, "drop old tfrrs_dt", "DROP TABLE IF EXISTS tfrrs_dt")
    _run(cur, "build tfrrs_dt", """
        CREATE TEMP TABLE tfrrs_dt AS
        SELECT meet_id,
               left(date, 10)::date AS d0,
               -- COALESCE: if date_end is absent/malformed, the range
               -- collapses to the single start day.
               COALESCE(
                 CASE WHEN date_end ~ '^\\d{4}-\\d{2}-\\d{2}'
                      THEN left(date_end, 10)::date END,
                 left(date, 10)::date) AS d1
        FROM meets_tfrrs
        WHERE sport = %s
          AND date ~ '^\\d{4}-\\d{2}-\\d{2}'   -- guard the ::date cast
    """, (sport,))


# _buildAnetDates
# Purpose:   build anet_dt: ONE date per anet meet. The source differs by
#            sport because the schema does: TF has a real meet-level date
#            column (meets_tf_meta.meet_date); the XC meets table has NO date
#            column at all, so the meet's date is derived as the earliest
#            result-row date it contains.
# Arguments: cur   -- the shared cursor.
#            sport -- 'TF' or 'XC'; picks the source described above.
# Output:    none. Side effect: temp table anet_dt(meet_id, d). Meets absent
#            here (e.g. the ~1.7K TF meets with no meta row) simply have no
#            date -- the gap join treats them as "unknown", which PASSES.
def _buildAnetDates(cur, sport):
    _run(cur, "drop old anet_dt", "DROP TABLE IF EXISTS anet_dt")
    if sport == "TF":
        _run(cur, "build anet_dt (from meets_tf_meta)", """
            CREATE TEMP TABLE anet_dt AS
            SELECT meet_id, left(meet_date, 10)::date AS d
            FROM meets_tf_meta
            WHERE meet_date ~ '^\\d{4}-\\d{2}-\\d{2}'
        """)
    else:
        # Restrict the 34M-row results scan to meets that actually appear in
        # matches: the JOIN to the DISTINCT subquery lets the planner drive
        # idx_results_meet_id per meet instead of scanning the table.
        _run(cur, "build anet_dt (XC: min result date per matched meet)", """
            CREATE TEMP TABLE anet_dt AS
            SELECT r.meet_id, min(left(r.date, 10))::date AS d
            FROM results r
            JOIN (SELECT DISTINCT anet_meet FROM athlete_meet_match
                  WHERE sport = 'XC') m
              ON m.anet_meet = r.meet_id
            WHERE r.source = 'anet'
              AND r.date ~ '^\\d{4}-\\d{2}-\\d{2}'
            GROUP BY r.meet_id
        """)


# ================================================================== #
# CHUNK 4 -- THE STAGED PIPELINE (one temp table per filter)
# ================================================================== #
# Stages exist as separate temp tables INSTEAD of one big CTE for exactly one
# reason: each stage's row count is then queryable, so the report can show
# where pairs died. Same rows, better eyes.


# _stageShared
# Purpose:   stage 1: collapse athlete_meet_match to meet pairs with their
#            shared-finisher counts. DISTINCT first so one athlete racing two
#            events at a meet counts once (matches the original build).
# Arguments: cur   -- shared cursor.
#            sport -- 'TF' or 'XC'; athlete_meet_match holds both.
# Output:    none. Side effect: temp stage_shared(anet_meet, tfrrs_meet,
#            shared) -- 'shared' = distinct co-appearing athletes = what
#            confidence has meant all along.
def _stageShared(cur, sport):
    _run(cur, "drop old stage_shared", "DROP TABLE IF EXISTS stage_shared")
    _run(cur, "stage 1: shared counts per meet pair", """
        CREATE TEMP TABLE stage_shared AS
        SELECT anet_meet, tfrrs_meet, count(*) AS shared
        FROM (SELECT DISTINCT anet_id, tfrrs_id, anet_meet, tfrrs_meet
              FROM athlete_meet_match WHERE sport = %s) am
        GROUP BY anet_meet, tfrrs_meet
    """, (sport,))


# _stageDated
# Purpose:   stage 2: the DATE GATE. Attach each pair's day-gap and keep pairs
#            within max_gap days -- or with an UNKNOWN gap (missing date on
#            either side), which passes on principle: absence of evidence
#            isn't evidence of mismatch. gap := 0 when the anet date falls
#            inside the tfrrs [d0, d1] range, else distance to the nearer edge.
# Arguments: cur     -- shared cursor.
#            max_gap -- int days; the --max-gap flag (default 3).
# Output:    none. Side effect: temp stage_dated = stage_shared columns + gap
#            (int days, NULL = unknown), filtered.
def _stageDated(cur, max_gap):
    _run(cur, "drop old stage_dated", "DROP TABLE IF EXISTS stage_dated")
    _run(cur, f"stage 2: date gate (max gap {max_gap}d, unknown passes)", """
        CREATE TEMP TABLE stage_dated AS
        SELECT s.anet_meet, s.tfrrs_meet, s.shared,
               CASE
                 WHEN ad.d IS NULL OR td.d0 IS NULL THEN NULL   -- unknown
                 WHEN ad.d BETWEEN td.d0 AND td.d1 THEN 0       -- inside range
                 -- date - date yields integer days in Postgres; nearer edge:
                 ELSE LEAST(abs(ad.d - td.d0), abs(ad.d - td.d1))
               END AS gap
        FROM stage_shared s
        LEFT JOIN anet_dt  ad ON ad.meet_id = s.anet_meet
        LEFT JOIN tfrrs_dt td ON td.meet_id = s.tfrrs_meet
    """)
    # The filter is separate from the CREATE so the report can count both
    # sides of it (kept vs dropped) from one table if we ever want to.
    _run(cur, "stage 2: apply the gate",
         "DELETE FROM stage_dated WHERE gap IS NOT NULL AND gap > %s", (max_gap,))


# _stageGated
# Purpose:   stage 3: the original shared-finisher minimum, unchanged in
#            meaning from the first build -- a pair needs >= gate distinct
#            shared finishers to be believable at all.
# Arguments: cur  -- shared cursor.
#            gate -- int; the --gate flag (default MIN_SHARED_RESULTS = 5).
# Output:    none. Side effect: temp stage_gated, same columns as stage_dated.
def _stageGated(cur, gate):
    _run(cur, "drop old stage_gated", "DROP TABLE IF EXISTS stage_gated")
    _run(cur, f"stage 3: shared-finisher gate (>= {gate})", """
        CREATE TEMP TABLE stage_gated AS
        SELECT * FROM stage_dated WHERE shared >= %s
    """, (gate,))

    # _stageRecovery
# Purpose:   stage 3b: LEAK-C RECOVERY. The main gate demands >=5 shared
#            finishers, which starves out real meets whose cross-source name
#            variants (or thin HS/college overlap) leave only 2-4 EXACT
#            matches -- measured at ~980 TF / ~57 XC pairs, ~75% real by
#            name inspection. This stage admits that band under STRICTER
#            contextual proof instead: the date gap must be KNOWN-ZERO
#            (unknown does NOT qualify here -- absence of evidence can
#            block disqualification, never grant qualification), and the
#            pair must still win mutual-best downstream against the whole
#            pool, so a recovered pair can never displace a gate-5 pair.
# Arguments: cur       -- shared cursor (reads stage_dated, which still holds
#                         every below-gate pair -- stage 3 SELECTed out of it,
#                         it didn't consume it).
#            recover_lo -- int; bottom of the recovery band (--recover-min,
#                          default 2; 0 disables recovery entirely).
# Output:    none. Side effect: temp stage_recovery, same columns as
#            stage_gated (anet_meet, tfrrs_meet, shared, gap).
def _stageRecovery(cur, recover_lo):
    _run(cur, "drop old stage_recovery", "DROP TABLE IF EXISTS stage_recovery")
    if recover_lo <= 0:                    # flag says: recovery disabled
        _run(cur, "stage 3b: recovery DISABLED (empty table)", """
            CREATE TEMP TABLE stage_recovery AS
            SELECT * FROM stage_dated WHERE false
        """)                               # WHERE false: schema, zero rows
        return
    _run(cur, f"stage 3b: Leak-C recovery (shared {recover_lo}..4, gap=0 only)", """
        CREATE TEMP TABLE stage_recovery AS
        SELECT anet_meet, tfrrs_meet, shared, gap
        FROM stage_dated
        WHERE shared BETWEEN %s AND 4
          AND gap = 0                      -- KNOWN zero; NULL fails this test
    """, (recover_lo,))


# _stageMutual
# Purpose:   stage 4: MUTUAL BEST. Keep a pair only if it is BOTH the anet
#            meet's highest-shared pair AND the tfrrs meet's highest-shared
#            pair. This is the layer that kills same-weekend collisions the
#            date gate is blind to: a fake pair can fool one side's argmax,
#            but the counterpart meet's own best match points at its real
#            twin, so the fake fails mutuality.
# Arguments: cur -- shared cursor.
# Output:    none. Side effect: temp stage_mutual(anet_meet, tfrrs_meet,
#            shared, gap) -- the final clean meet links.
def _stageMutual(cur):
    _run(cur, "drop old stage_mutual", "DROP TABLE IF EXISTS stage_mutual")
    _run(cur, "stage 4: mutual best match", """
        CREATE TEMP TABLE stage_mutual AS
        -- DISTINCT ON (col): keep only the FIRST row per value of col under
        -- the ORDER BY -- Postgres's "argmax per group" idiom. Tie-break on
        -- the partner id so reruns are deterministic.
        WITH pool AS (
            -- the combined candidate pool: main gate + recovery. UNION (not
            -- UNION ALL) so a pair somehow in both appears once.
            SELECT anet_meet, tfrrs_meet, shared, gap FROM stage_gated
            UNION
            SELECT anet_meet, tfrrs_meet, shared, gap FROM stage_recovery
        ),
        best_a AS (
            SELECT DISTINCT ON (anet_meet) anet_meet, tfrrs_meet, shared, gap
            FROM pool
            ORDER BY anet_meet, shared DESC, tfrrs_meet
        ),
        best_t AS (
            SELECT DISTINCT ON (tfrrs_meet) tfrrs_meet, anet_meet
            FROM pool
            ORDER BY tfrrs_meet, shared DESC, anet_meet
        )
        SELECT a.anet_meet, a.tfrrs_meet, a.shared, a.gap
        FROM best_a a
        JOIN best_t t                        -- the mutuality handshake:
          ON t.anet_meet  = a.anet_meet      -- tfrrs side's favorite anet meet
         AND t.tfrrs_meet = a.tfrrs_meet     -- must be exactly this pair too
    """)


# _reportStages
# Purpose:   the eyes-first drop story: pairs alive after each stage, so the
#            filters' individual bite is visible (and comparable across
#            --max-gap settings).
# Arguments: cur   -- shared cursor.
#            sport -- for the header only.
# Output:    dict {stage_name: count} -- also printed.
def _reportStages(cur, sport):
    counts = {
        "shared (all pairs)":   _count(cur, "SELECT count(*) FROM stage_shared"),
        "after date gate":      _count(cur, "SELECT count(*) FROM stage_dated"),
        "after finisher gate":  _count(cur, "SELECT count(*) FROM stage_gated"),
        "recovery candidates":  _count(cur, "SELECT count(*) FROM stage_recovery"),  # ← moved up
        "after mutual best":    _count(cur, "SELECT count(*) FROM stage_mutual"),
    }
    print(f"\n  {sport} drop story:")
    prev = None
    for name, n in counts.items():
        drop = f"  (-{prev - n:,})" if prev is not None else ""
        print(f"    {name:<22}: {n:,}{drop}")
        prev = n
    return counts


# ================================================================== #
# CHUNK 5 -- WRITE (meets from stage_mutual; athletes RE-HARVESTED)
# ================================================================== #


# _writeSportLinks
# Purpose:   append one sport's clean links into entity_links: the mutual
#            meet pairs as meet links, and athlete links RE-DERIVED from
#            athlete_meet_match restricted to those clean meets. Re-derived,
#            not copied: the old athlete links were harvested from fake meets
#            too, so they are contaminated at the source.
# Arguments: cur   -- shared cursor.
#            sport -- 'TF' or 'XC'.
# Output:    none (row counts print via _run).
def _writeSportLinks(cur, sport):
    _run(cur, f"insert {sport} meet links", """
        INSERT INTO entity_links (entity_type, sport, anet_id, tfrrs_id, confidence, method)
        SELECT 'meet', %s, anet_meet, tfrrs_meet, shared,
               -- provenance: which door did this pair enter through?
               CASE WHEN shared >= 5 THEN 'shared-results-dated'
                    ELSE 'shared-results-recovered' END
        FROM stage_mutual
    """, (sport,))
    _run(cur, f"insert {sport} athlete links (re-harvested)", """
        INSERT INTO entity_links (entity_type, sport, anet_id, tfrrs_id, confidence, method)
        SELECT 'athlete', %s, am.anet_id, am.tfrrs_id, count(*), 'confirmed-meet-finisher'
        FROM (SELECT DISTINCT anet_id, tfrrs_id, anet_meet, tfrrs_meet
              FROM athlete_meet_match WHERE sport = %s) am
        JOIN stage_mutual m
          ON m.anet_meet = am.anet_meet AND m.tfrrs_meet = am.tfrrs_meet
        GROUP BY am.anet_id, am.tfrrs_id
    """, (sport, sport))


# ================================================================== #
# CHUNK 6 -- DRIVER
# ================================================================== #


# _parseArgs
# Purpose:   the CLI. Every knob is a flag, per house style.
# Arguments: none (reads sys.argv via argparse).
# Output:    argparse.Namespace with .gate (int), .max_gap (int),
#            .sport (str or None = both), .dry_run (bool).
def _parseArgs():
    p = argparse.ArgumentParser(description="Rebuild entity_links with date gate + mutual best.")
    p.add_argument("--gate", type=int, default=MIN_SHARED_RESULTS,
                   help=f"min shared finishers per meet pair (default {MIN_SHARED_RESULTS})")
    p.add_argument("--recover-min", type=int, default=2,
                   help="bottom of the Leak-C recovery band (shared "
                        "recover_min..4 at gap 0). 0 disables recovery.")
    p.add_argument("--max-gap", type=int, default=DEFAULT_MAX_GAP,
                   help=f"max days between paired meets (default {DEFAULT_MAX_GAP})")
    p.add_argument("--sport", choices=list(SPORTS), help="one sport (default: both)")
    p.add_argument("--dry-run", action="store_true",
                   help="run all stages and print the drop story, write NOTHING")
    return p.parse_args()


def main():
    args = _parseArgs()
    sports = (args.sport,) if args.sport else SPORTS
    mode = "DRY RUN" if args.dry_run else "APPLY"
    print(f"=== dated entity_links rebuild -- {mode} "
          f"(gate {args.gate}, max gap {args.max_gap}d) ===")

    # ONE connection for the whole run: every stage is a temp table, and temp
    # tables are visible only to the connection that created them.
    with getConn() as conn:
        cur = conn.cursor()
        cur.execute("SET work_mem = '1GB'")   # room for the GROUP BYs / sorts

        _precheck(cur)

        # Stage results per sport are computed BEFORE any destructive write,
        # so a dry run exercises everything real except the final TRUNCATE.
        results = {}
        for sport in sports:
            print(f"\n--- {sport} ---")
            _buildTfrrsDates(cur, sport)
            _buildAnetDates(cur, sport)
            _stageShared(cur, sport)
            _stageDated(cur, args.max_gap)
            _stageGated(cur, args.gate)
            _stageRecovery(cur, args.recover_min)      # <- new
            _stageMutual(cur)
            _reportStages(cur, sport)
            if not args.dry_run:
                # Per-sport delete, NOT TRUNCATE: entity_links holds both
                # sports' links in one table, so a --sport TF run must clear
                # ONLY the TF rows -- TRUNCATE would silently wipe XC's links
                # and never rewrite them. Same pattern the builder's _match
                # uses on athlete_meet_match for the same reason.
                _run(cur, f"clear old {sport} links",
                     "DELETE FROM entity_links WHERE sport = %s", (sport,))
                _writeSportLinks(cur, sport)

        if args.dry_run:
            print("\nDRY RUN -- entity_links untouched. Re-run without --dry-run to apply.")
            conn.rollback()      # discard even the temp-table work, belt-and-braces
        else:
            conn.commit()
            print("\nDone. NEXT: null the merge stamps, re-run merge_links, "
                  "then the (rapidfuzz) fuzzy pass on the clean links.")


if __name__ == "__main__":
    main()