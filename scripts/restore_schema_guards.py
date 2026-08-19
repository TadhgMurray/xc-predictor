# Project: xc-predictor
# File:    scripts/restore_schema_guards.py
# Purpose: Put back the NOT NULL and FOREIGN KEY constraints that the copy-path
#          merge destroyed, reconstructing them from the VERIFIED schema doc
#          rather than from the <table>_old copies (which have been dropped).
#
# ============================================================================
# WHAT HAPPENED
# ============================================================================
# backfill_normalize.py's merge rebuilds the table with
#
#     CREATE TABLE results_new AS SELECT ... FROM results r LEFT JOIN ...
#
# `CREATE TABLE ... AS SELECT` copies COLUMN NAMES AND TYPES and nothing else.
# It drops NOT NULL, DEFAULT, CHECK, UNIQUE, and FOREIGN KEY. The merge re-added
# the PRIMARY KEY and every index (it reads those from pg_indexes). It re-added
# nothing else.
#
# The right recovery is to read the DDL back off <table>_old before dropping it
# (scripts/repair_constraints.py does exactly that). That window has closed:
# <table>_old is gone. So we reconstruct from the schema doc instead.
#
# ============================================================================
# WHAT WE KNOW, AND HOW WE KNOW IT
# ============================================================================
# From the project master doc, section "FULL DATABASE SCHEMA [VERIFIED 2026-06-29]":
#
#     results.source       text  NOT NULL
#     results.id_system    text  NOT NULL
#     results_tf.source    text  NOT NULL
#     results_tf.id_system text  NOT NULL
#     (result_id NOT NULL comes free with the PRIMARY KEY, already restored)
#
# and, from its "Foreign keys" block:
#
#     results.athlete_id     -> athletes.athlete_id   (composite (athlete_id, school))
#     results.school         -> athletes.school
#     results_tf.athlete_id  -> athletes.athlete_id   (composite (athlete_id, school))
#     results_tf.school      -> athletes.school
#     "meet_id is NOT a FK anywhere. All other tables have no declared FKs."
#
# Those four lines are TWO composite foreign keys, one per results table.
#
# Corroborating evidence that this list is complete: `DROP TABLE results_old`
# ran WITHOUT `CASCADE` and succeeded. A dependent foreign key would have raised.
# So nothing REFERENCED the old tables -- every lost FK was outbound.
#
# NOT RECOVERABLE FROM THE DOC: column DEFAULTs and CHECK constraints. The doc
# does not record them. `database.py`'s CREATE TABLE statements are the source of
# truth for those; `scraped_at` is the likely candidate. This script reports what
# it finds and does not guess.
#
# ============================================================================
# WHY `NOT VALID` THEN `VALIDATE`
# ============================================================================
# `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY` scans the WHOLE table under an
# ACCESS EXCLUSIVE lock. On results_tf (191M rows) that blocks everything for
# minutes. Adding it `NOT VALID` takes a brief lock and skips the scan: the
# constraint is enforced on all FUTURE writes, existing rows are not checked.
# `VALIDATE CONSTRAINT` then does the scan under a weaker SHARE UPDATE EXCLUSIVE
# lock, so readers and writers keep working.
#
# The two-step also SEPARATES the two questions: "is the guard in place for new
# writes" (instant) and "does the existing data satisfy it" (slow, and possibly
# NO -- seven years of scraping may contain orphans). If VALIDATE fails, the
# guard still protects everything written from now on.
#
# USAGE
#   python scripts/restore_schema_guards.py                    # report
#   python scripts/restore_schema_guards.py --apply            # add guards
#   python scripts/restore_schema_guards.py --apply --validate # + scan old rows

import argparse
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# THE DOCUMENTED SCHEMA  —  what SHOULD be true
# ================================================================== #

# Columns declared NOT NULL in the verified schema doc, excluding primary-key
# columns (the merge's ADD PRIMARY KEY already restores their NOT NULL).
_NOT_NULL = {
    "results":    ["source", "id_system"],
    "results_tf": ["source", "id_system"],
}

# (constraint_name, local columns, referenced table, referenced columns)
# One composite FK per results table, matching athletes' composite primary key.
_FOREIGN_KEYS = {
    "results": [
        ("results_athlete_id_school_fkey", ("athlete_id", "school"),
         "athletes", ("athlete_id", "school")),
    ],
    "results_tf": [
        ("results_tf_athlete_id_school_fkey", ("athlete_id", "school"),
         "athletes", ("athlete_id", "school")),
    ],
}


# ================================================================== #
# INSPECTION
# ================================================================== #

# _isNotNull
# Purpose : is this column currently NOT NULL?
# Syntax  : pg_attribute.attnotnull is the flag. attnum > 0 skips system columns.
def _isNotNull(cur, table, column):
    cur.execute("""
        SELECT attnotnull FROM pg_attribute
        WHERE attrelid = %s::regclass AND attname = %s AND attnum > 0
    """, (table, column))
    row = cur.fetchone()
    return row[0] if row else None          # None => the column does not exist


# _nullCount
# Purpose : how many rows would block a SET NOT NULL.
# This is a full scan. On 191M rows it is slow, and it is the only honest way to
#   know: SET NOT NULL on a column containing NULLs raises and aborts.
def _nullCount(cur, table, column):
    cur.execute(f"SELECT count(*) FROM {table} WHERE {column} IS NULL")
    return cur.fetchone()[0]


# _foreignKeys
# Purpose : the foreign keys currently declared ON `table`.
# Syntax  : contype 'f' is FOREIGN KEY. `convalidated` distinguishes a constraint
#           added NOT VALID (enforced going forward, existing rows unchecked)
#           from a fully validated one.
def _foreignKeys(cur, table):
    cur.execute("""
        SELECT conname, convalidated, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = %s::regclass AND contype = 'f'
        ORDER BY conname
    """, (table,))
    return cur.fetchall()


# _orphanCount
# Purpose : rows whose (athlete_id, school) has no matching athletes row.
# Syntax  : `NOT EXISTS` with a correlated subquery. The `IS NOT NULL` guards
#           mirror the SQL standard's MATCH SIMPLE rule: a composite foreign key
#           with ANY null column is automatically satisfied and never checked.
#           results.athlete_id is ~10% null, so most rows are exempt by design --
#           counting them as orphans would be wrong.
def _orphanCount(cur, table, cols, ref_table, ref_cols):
    a, b = cols
    ra, rb = ref_cols
    cur.execute(f"""
        SELECT count(*) FROM {table} t
        WHERE t.{a} IS NOT NULL AND t.{b} IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM {ref_table} r
              WHERE r.{ra} = t.{a} AND r.{rb} = t.{b})
    """)
    return cur.fetchone()[0]


# ================================================================== #
# THE PLAN
# ================================================================== #

# _planNotNull
# Purpose : which NOT NULLs are missing, and would any of them fail?
# Output  : (statements, blockers) -- blockers are (table, col, n_nulls).
def _planNotNull(cur, check_nulls):
    stmts, blockers = [], []
    for table, cols in _NOT_NULL.items():
        for col in cols:
            state = _isNotNull(cur, table, col)
            if state is None:
                print(f"  ?? {table}.{col} does not exist -- skipping")
                continue
            if state:
                print(f"  ok {table}.{col} is already NOT NULL")
                continue
            n = _nullCount(cur, table, col) if check_nulls else -1
            if n > 0:
                blockers.append((table, col, n))
                print(f"  !! {table}.{col} is nullable and has {n:,} NULLs")
                continue
            found = "0 NULLs" if n == 0 else "not checked"
            print(f"  -> {table}.{col} is nullable ({found}) -- will restore")
            stmts.append(f"ALTER TABLE {table} ALTER COLUMN {col} SET NOT NULL")
    return stmts, blockers


# _planForeignKeys
# Purpose : which FKs are missing. Always added NOT VALID; VALIDATE is a separate,
#           optional, slow step.
def _planForeignKeys(cur):
    stmts = []
    for table, fks in _FOREIGN_KEYS.items():
        have = {defn for _n, _v, defn in _foreignKeys(cur, table)}
        for name, cols, ref_table, ref_cols in fks:
            col_list = ", ".join(cols)
            ref_list = ", ".join(ref_cols)
            if any(f"({col_list})" in d and ref_table in d for d in have):
                print(f"  ok {table} already has an FK on ({col_list})")
                continue
            print(f"  -> {table} ({col_list}) -> {ref_table} ({ref_list}) missing")
            stmts.append(
                f"ALTER TABLE {table} ADD CONSTRAINT {name} "
                f"FOREIGN KEY ({col_list}) REFERENCES {ref_table} ({ref_list}) "
                f"NOT VALID")
    return stmts


# _planValidate
# Purpose : the VALIDATE statements, run only with --validate.
def _planValidate():
    out = []
    for table, fks in _FOREIGN_KEYS.items():
        for name, _c, _rt, _rc in fks:
            out.append(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")
    return out


# ================================================================== #
# REPORTING THE UNKNOWNS
# ================================================================== #

# _reportDefaults
# Purpose : list every column that currently has NO default, so the owner can
#           diff it against database.py's CREATE TABLE. We cannot know which of
#           them SHOULD have one -- the schema doc does not record defaults.
def _reportDefaults(cur, table):
    cur.execute("""
        SELECT a.attname
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
          AND d.adbin IS NULL
        ORDER BY a.attnum
    """, (table,))
    cols = [r[0] for r in cur.fetchall()]
    cur.execute("""
        SELECT count(*) FROM pg_constraint
        WHERE conrelid = %s::regclass AND contype = 'c'
    """, (table,))
    n_checks = cur.fetchone()[0]
    print(f"\n  {table}: {len(cols)} columns have no DEFAULT, "
          f"{n_checks} CHECK constraints")
    print(f"    The schema doc records neither. Diff against database.py's")
    print(f"    CREATE TABLE for {table}. `scraped_at` is the likely candidate.")


# ================================================================== #
# ORCHESTRATION
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Restore NOT NULL + FK guards lost by the merge.")
    ap.add_argument("--apply", action="store_true", help="run the statements")
    ap.add_argument("--validate", action="store_true",
                    help="also VALIDATE the foreign keys against existing rows "
                         "(a full scan of 191M rows; minutes)")
    ap.add_argument("--skip-null-check", action="store_true",
                    help="skip the pre-flight NULL scan")
    args = ap.parse_args()

    initPool()
    with getConn() as conn:
        with conn.cursor() as cur:
            print("=" * 74)
            print("NOT NULL")
            print("=" * 74)
            nn_stmts, blockers = _planNotNull(cur, not args.skip_null_check)

            print(f"\n{'=' * 74}\nFOREIGN KEYS\n{'=' * 74}")
            fk_stmts = _planForeignKeys(cur)

            print(f"\n{'=' * 74}\nNOT RECOVERABLE FROM THE DOC\n{'=' * 74}")
            for table in _NOT_NULL:
                _reportDefaults(cur, table)

            if args.validate:
                print(f"\n{'=' * 74}\nORPHAN CHECK (would VALIDATE fail?)\n{'=' * 74}")
                for table, fks in _FOREIGN_KEYS.items():
                    for _n, cols, rt, rc in fks:
                        n = _orphanCount(cur, table, cols, rt, rc)
                        verdict = "clean" if n == 0 else f"{n:,} ORPHANS"
                        print(f"  {table} ({', '.join(cols)}) -> {rt}: {verdict}")
        conn.rollback()

        stmts = nn_stmts + fk_stmts
        if args.validate:
            stmts += _planValidate()

        print(f"\n{'-' * 74}")
        if blockers:
            print("BLOCKED: columns contain NULLs that SET NOT NULL would reject.")
            print("Investigate before restoring -- the merge may have lost data,")
            print("or the column's meaning changed.")
            sys.exit(1)
        if not stmts:
            print("Nothing to restore.")
            return
        print(f"{len(stmts)} statements:\n")
        for s in stmts:
            print(f"  {s};")

        if not args.apply:
            print(f"\n{'-' * 74}")
            print("Report only. To apply:")
            print("  python scripts/restore_schema_guards.py --apply")
            print("\nThen, when you can afford a full scan of results_tf:")
            print("  python scripts/restore_schema_guards.py --apply --validate")
            return

        # ONE transaction: all of it, or none. VALIDATE is separate below because
        # it must not hold a long transaction open.
        with conn.cursor() as cur:
            for s in stmts:
                if s.startswith("ALTER TABLE") and "VALIDATE" in s:
                    continue
                print(f"  {s}")
                cur.execute(s)
        conn.commit()

        if args.validate:
            for s in _planValidate():
                print(f"  {s}   (full scan, weak lock)")
                with conn.cursor() as cur:
                    cur.execute(s)
                conn.commit()

        print("\nguards restored.")


if __name__ == "__main__":
    main()