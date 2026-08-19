# Project: xc-predictor
# File:    scripts/diag_person_id.py
# Purpose: Measure `person_id` coverage, and decide whether seeding
#          `person_id = athlete_id` for anet rows is SAFE.
#
# ============================================================================
# WHY
# ============================================================================
# The speed engine filters `AND r.person_id IS NOT NULL` in both loaders. On the
# first real run it emitted 1,009,281 XC ratings against ~35M rows carrying a
# normalized_time -- 2.9%.
#
# That is not a random 2.9%. `person_id` is stamped by merge_links.py on athletes
# the dedup matcher linked across anet and tfrrs -- i.e. athletes who appear in
# BOTH sources. Those skew hard toward higher competition levels. Fitting pool
# means, course difficulties and athlete ratings on that slice is a selection
# bias, not a sample.
#
# The master doc says the intent was different: "person_id seeded = athlete_id
# for anet". If that seeding never ran (or was overwritten), the fix is to run
# it. But `person_id` must be a GLOBAL identity, so seeding it from athlete_id is
# only safe if the id spaces do not collide. This script checks that before
# anyone writes 35M rows.
#
# ============================================================================
# THE THREE QUESTIONS
# ============================================================================
#   1. COVERAGE     -- how many rows have person_id, split by source and by
#                      whether they carry a normalized_time (the engine's input).
#   2. CONSISTENCY  -- where merge_links already stamped a person_id on an anet
#                      row, does it EQUAL that row's athlete_id? merge_links
#                      "reuses anet id as canonical", so it must. If it does not,
#                      seeding would contradict the matcher.
#   3. COLLISION    -- would seeding create a person_id that already belongs to a
#                      DIFFERENT athlete? This is the question that makes seeding
#                      safe or catastrophic, and it cannot be answered by reading
#                      the docs.
#
# SAFETY: read-only. Rolls back immediately.
#
# USAGE
#   python scripts/diag_person_id.py

import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


TABLES = ("results", "results_tf")


# ================================================================== #
# 1. COVERAGE
# ================================================================== #

# _coverage
# Purpose : per source: rows, rows with person_id, rows with athlete_id, and the
#           two intersections with normalized_time that the engine actually sees.
# Syntax  : COUNT(*) FILTER (WHERE ...) counts a subset inside the same single
#           pass. Five filters, one sequential scan, instead of five scans.
# `usable_now` is exactly the engine's WHERE clause. `usable_if_seeded` is what
#   it would become if every anet row got person_id = athlete_id.
def _coverage(cur, table):
    cur.execute(f"""
        SELECT source,
               count(*),
               count(person_id),
               count(athlete_id),
               count(*) FILTER (WHERE normalized_time IS NOT NULL),
               count(*) FILTER (WHERE normalized_time IS NOT NULL
                                  AND person_id IS NOT NULL),
               count(*) FILTER (WHERE normalized_time IS NOT NULL
                                  AND COALESCE(person_id, athlete_id) IS NOT NULL)
        FROM {table}
        GROUP BY source ORDER BY 2 DESC
    """)
    return cur.fetchall()


def _printCoverage(table, rows):
    print(f"\n{table}")
    print("-" * 92)
    print(f"{'source':<8}{'rows':>14}{'person_id':>14}{'athlete_id':>14}"
          f"{'normalized':>13}{'ENGINE SEES':>14}{'if seeded':>13}")
    for src, n, pid, aid, norm, usable, seeded in rows:
        print(f"{src:<8}{n:>14,}{pid:>14,}{aid:>14,}{norm:>13,}"
              f"{usable:>14,}{seeded:>13,}")
    tot_usable = sum(r[5] for r in rows)
    tot_seeded = sum(r[6] for r in rows)
    tot_norm = sum(r[4] for r in rows)
    if tot_norm:
        print(f"\n  engine currently sees {tot_usable:,} of {tot_norm:,} "
              f"normalized rows ({100.0 * tot_usable / tot_norm:.1f}%)")
        print(f"  with person_id seeded from athlete_id: {tot_seeded:,} "
              f"({100.0 * tot_seeded / tot_norm:.1f}%)")
        if tot_usable:
            print(f"  -> {tot_seeded / tot_usable:.1f}x more data")


# ================================================================== #
# 2. CONSISTENCY  —  does merge_links agree with the seeding rule?
# ================================================================== #

# _consistency
# Purpose : among anet rows that ALREADY have a person_id, how often does it
#           equal athlete_id?
# merge_links.py "reuses anet id as canonical", so for an anet row the canonical
#   person_id IS the anet athlete_id. Any row where they differ means either the
#   matcher chose a different canonical, or something else wrote person_id. Either
#   way, seeding the remaining rows with athlete_id would contradict it.
# Output  : (rows_with_both, agree, disagree)
def _consistency(cur, table):
    cur.execute(f"""
        SELECT count(*),
               count(*) FILTER (WHERE person_id = athlete_id),
               count(*) FILTER (WHERE person_id <> athlete_id)
        FROM {table}
        WHERE source = 'anet'
          AND person_id IS NOT NULL AND athlete_id IS NOT NULL
    """)
    return cur.fetchone()


# _disagreementSamples
# Purpose : if any anet row has person_id <> athlete_id, SHOW them. A count is
#           a fact; a sample is a diagnosis.
def _disagreementSamples(cur, table, n=8):
    cur.execute(f"""
        SELECT result_id, athlete_id, person_id, school
        FROM {table}
        WHERE source = 'anet' AND person_id IS NOT NULL
          AND person_id <> athlete_id
        LIMIT {n}
    """)
    return cur.fetchall()


# ================================================================== #
# 3. COLLISION  —  is it safe to seed?
# ================================================================== #

# _collision
# Purpose : would seeding person_id = athlete_id give two DIFFERENT athletes the
#           same person_id?
# The danger: a tfrrs row already carries person_id = P (assigned by the matcher,
#   equal to some anet athlete's id). If a DIFFERENT anet athlete happens to have
#   athlete_id = P... they would merge into one person. Since merge_links reuses
#   the anet id as canonical, this cannot happen BY CONSTRUCTION -- but "cannot
#   happen by construction" is a claim about code, and this is a query about data.
#
# We ask it directly: take every person_id currently stamped on a NON-anet row,
#   and check whether that value is the athlete_id of an anet athlete who has NO
#   person_id yet. If so, seeding would fuse two identities.
def _collision(cur, table):
    cur.execute(f"""
        WITH stamped AS (
            SELECT DISTINCT person_id AS pid
            FROM {table}
            WHERE source <> 'anet' AND person_id IS NOT NULL
        ), would_seed AS (
            SELECT DISTINCT athlete_id AS aid
            FROM {table}
            WHERE source = 'anet' AND person_id IS NULL
              AND athlete_id IS NOT NULL
        )
        SELECT count(*) FROM stamped JOIN would_seed ON pid = aid
    """)
    collisions = cur.fetchone()[0]

    # And the reverse: an anet athlete_id that is ALREADY someone else's stamped
    # person_id on another anet row.
    cur.execute(f"""
        SELECT count(*) FROM (
            SELECT athlete_id FROM {table}
            WHERE source = 'anet' AND athlete_id IS NOT NULL
            GROUP BY athlete_id
            HAVING count(DISTINCT person_id) > 1
        ) x
    """)
    multi = cur.fetchone()[0]
    return collisions, multi


# ================================================================== #
# 4. THE UNRECOVERABLE REMAINDER
# ================================================================== #

# _unrecoverable
# Purpose : rows that would STILL have no identity after seeding -- normalized,
#           but with neither person_id nor athlete_id.
# For XC these are overwhelmingly tfrrs rows (tfrrs XC has athlete_id NULL on
#   ~100% of rows). Most were already dropped as dedup_twin. The rest simply
#   cannot be attributed to an athlete and cannot be rated. Report, don't hide.
def _unrecoverable(cur, table):
    cur.execute(f"""
        SELECT source, count(*)
        FROM {table}
        WHERE normalized_time IS NOT NULL
          AND person_id IS NULL AND athlete_id IS NULL
        GROUP BY source ORDER BY 2 DESC
    """)
    return cur.fetchall()


# ================================================================== #
# ORCHESTRATION
# ================================================================== #

def _verdict(agree, disagree, collisions, multi):
    print(f"\n{'=' * 92}")
    print("VERDICT")
    print("=" * 92)
    ok = True
    if disagree:
        ok = False
        print(f"  !! {disagree:,} anet rows have person_id <> athlete_id.")
        print(f"     merge_links reuses the anet id as canonical, so this should")
        print(f"     be zero. Seeding would contradict the matcher. INVESTIGATE.")
    else:
        print(f"  ok every anet row with a person_id has person_id = athlete_id")
        print(f"     ({agree:,} rows). The seeding rule agrees with the matcher.")

    if collisions:
        ok = False
        print(f"  !! {collisions:,} person_ids stamped on non-anet rows collide")
        print(f"     with anet athlete_ids that would be seeded. Seeding would")
        print(f"     FUSE two different athletes into one. DO NOT SEED.")
    else:
        print(f"  ok no collisions: seeding creates no duplicate identities")

    if multi:
        ok = False
        print(f"  !! {multi:,} anet athlete_ids carry more than one person_id.")
        print(f"     The identity map is not a function. INVESTIGATE.")
    else:
        print(f"  ok each anet athlete_id maps to at most one person_id")

    print()
    if ok:
        print("  SAFE TO SEED:")
        print("    UPDATE <table> SET person_id = athlete_id")
        print("     WHERE person_id IS NULL AND athlete_id IS NOT NULL")
        print("       AND source = 'anet';")
        print("\n  On 35M/191M rows use the heap-rebuild path, not an UPDATE:")
        print("    engine/merge_column.py mergeColumn(conn, table, 'person_id', ...)")
    else:
        print("  NOT SAFE. Do not seed until the flagged items are explained.")


def main():
    initPool()
    with getConn() as conn:
        with conn.cursor() as cur:
            print("=" * 92)
            print("1. COVERAGE  --  what the engine's `person_id IS NOT NULL` costs")
            print("=" * 92)
            for t in TABLES:
                _printCoverage(t, _coverage(cur, t))

            print(f"\n{'=' * 92}")
            print("2. CONSISTENCY  --  does person_id = athlete_id where both exist?")
            print("=" * 92)
            tot_agree = tot_dis = 0
            for t in TABLES:
                both, agree, dis = _consistency(cur, t)
                tot_agree += agree
                tot_dis += dis
                print(f"  {t:<12} {both:>12,} anet rows with both   "
                      f"{agree:>12,} agree   {dis:>10,} disagree")
                if dis:
                    print(f"    samples (result_id, athlete_id, person_id, school):")
                    for row in _disagreementSamples(cur, t):
                        print(f"      {row}")

            print(f"\n{'=' * 92}")
            print("3. COLLISION  --  would seeding fuse two athletes?")
            print("=" * 92)
            tot_coll = tot_multi = 0
            for t in TABLES:
                coll, multi = _collision(cur, t)
                tot_coll += coll
                tot_multi += multi
                print(f"  {t:<12} {coll:>10,} colliding ids   "
                      f"{multi:>10,} athlete_ids with >1 person_id")

            print(f"\n{'=' * 92}")
            print("4. UNRECOVERABLE  --  normalized, but no identity even after seeding")
            print("=" * 92)
            for t in TABLES:
                for src, n in _unrecoverable(cur, t):
                    print(f"  {t:<12} {src:<8} {n:>14,} rows")
        conn.rollback()          # release ACCESS SHARE

    _verdict(tot_agree, tot_dis, tot_coll, tot_multi)


if __name__ == "__main__":
    main()