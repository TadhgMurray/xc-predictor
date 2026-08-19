# Project: xc-predictor
# File:    scripts/repair_constraints.py
# Purpose: Restore the NOT NULLs, DEFAULTs, CHECKs and FOREIGN KEYs that the
#          copy-path merge silently destroyed, by reading them back off the
#          <table>_old copy before it is dropped.
#
# ============================================================================
# WHAT WENT WRONG
# ============================================================================
# backfill_normalize.py's merge rebuilds the table with
#
#     CREATE TABLE results_tf_new AS SELECT ... FROM results_tf r LEFT JOIN ...
#
# `CREATE TABLE ... AS SELECT` copies COLUMN NAMES AND TYPES. Nothing else.
# It does NOT copy:
#     * NOT NULL constraints        * column DEFAULTs
#     * CHECK constraints           * UNIQUE constraints
#     * FOREIGN KEY constraints     * triggers, comments, storage params
#
# The merge re-added the PRIMARY KEY and every index (it reads them from
# pg_indexes). It re-added nothing else. Verified on PG16:
#
#     before:  source NOT NULL,  scraped_at DEFAULT now(),  CHECK (place >= 0)
#     after :  source nullable,  no default,                no check
#
# Worse, `ALTER TABLE results RENAME TO results_old` does not move constraints
# that point AT the table. A foreign key in athlete_ratings that referenced
# `results` now references `results_old` -- the table you are about to drop.
#
# ============================================================================
# WHY THIS IS RECOVERABLE
# ============================================================================
# <table>_old is the ORIGINAL relation, renamed. It still carries every
# constraint and default. So the repair is: read the DDL off <table>_old, apply
# it to <table>, repoint the inbound foreign keys, THEN drop <table>_old.
#
# Run this BEFORE verify_merge.py --drop. Once <table>_old is gone the
# definitions are gone with it.
#
# ============================================================================
# USAGE
#   python scripts/repair_constraints.py --table results_tf           # report
#   python scripts/repair_constraints.py --table results_tf --apply   # fix
#
# The report prints every statement it would run. Read it. --apply runs them in
# ONE transaction: all of it lands or none of it does.

import argparse
import sys

sys.path.insert(0, "scripts")

from database import getConn, initPool


# ================================================================== #
# READING THE OLD DEFINITIONS
# ================================================================== #

# _exists
# Purpose : to_regclass returns the relation's OID or NULL. Exception-free, so a
#           missing table cannot abort the surrounding transaction.
def _exists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
    return cur.fetchone()[0] is not None


# _columnFacts
# Purpose : per column: is it NOT NULL, and what is its DEFAULT expression?
# Syntax  : pg_attribute holds one row per column. `attnotnull` is the NOT NULL
#           flag. Defaults live in a SIDE TABLE, pg_attrdef, joined on
#           (adrelid, adnum) -- hence the LEFT JOIN, because most columns have
#           none. pg_get_expr() turns the stored parse tree back into SQL text.
#           attnum > 0 excludes system columns (ctid, xmin...); NOT attisdropped
#           excludes columns removed by ALTER TABLE DROP COLUMN, whose slots
#           linger in the catalog.
def _columnFacts(cur, table):
    cur.execute("""
        SELECT a.attname, a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = %s::regclass
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
    """, (table,))
    return {name: (notnull, default) for name, notnull, default in cur.fetchall()}


# _tableConstraints
# Purpose : every constraint ON `table`, as the SQL text needed to recreate it.
# Syntax  : pg_get_constraintdef() emits the clause that follows ADD CONSTRAINT,
#           e.g. `CHECK ((place >= 0))` or `FOREIGN KEY (rid) REFERENCES orig(id)`.
#           contype: 'p' primary key, 'u' unique, 'c' check, 'f' foreign key.
# We SKIP 'p': the merge already re-added the primary key.
def _tableConstraints(cur, table):
    cur.execute("""
        SELECT conname, contype, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = %s::regclass AND contype <> 'p'
        ORDER BY contype, conname
    """, (table,))
    return cur.fetchall()


# _inboundForeignKeys
# Purpose : foreign keys in OTHER tables that REFERENCE `table`.
# This is the silent one. `ALTER TABLE t RENAME TO t_old` leaves every inbound
#   FK attached to the same relation OID -- which is now called t_old. Nothing
#   errors. The child table simply enforces its key against the wrong table, and
#   `DROP TABLE t_old` will refuse (or CASCADE the constraint away).
# Syntax  : confrelid is the REFERENCED relation; conrelid is the referencing
#           one. `::regclass` renders an OID as a table name.
def _inboundForeignKeys(cur, table):
    cur.execute("""
        SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE confrelid = %s::regclass AND contype = 'f'
        ORDER BY 1, 2
    """, (table,))
    return cur.fetchall()


# ================================================================== #
# BUILDING THE REPAIR
# ================================================================== #

# _notNullFixes
# Purpose : columns that were NOT NULL on the old table and are nullable now.
# We compare against the LIVE table rather than assuming the merge dropped them
#   all: the primary key column gets NOT NULL back for free when the PK is added,
#   and re-issuing it would be harmless but noisy.
def _notNullFixes(old_cols, new_cols, table):
    out = []
    for col, (notnull, _default) in old_cols.items():
        if col not in new_cols:
            continue                                   # column no longer exists
        if notnull and not new_cols[col][0]:
            out.append(f"ALTER TABLE {table} ALTER COLUMN {col} SET NOT NULL")
    return out


# _defaultFixes
# Purpose : columns that had a DEFAULT and now have none.
# Syntax  : the stored expression comes back as SQL text ("now()", "0"), so it
#           can be spliced straight after SET DEFAULT.
def _defaultFixes(old_cols, new_cols, table):
    out = []
    for col, (_notnull, default) in old_cols.items():
        if col not in new_cols or default is None:
            continue
        if new_cols[col][1] is None:
            out.append(f"ALTER TABLE {table} ALTER COLUMN {col} SET DEFAULT {default}")
    return out


# _constraintFixes
# Purpose : CHECK / UNIQUE / FK constraints present on old, absent on new.
# We compare by DEFINITION, not by name: the merge's index rebuild may already
#   have recreated a UNIQUE index under a different name, and re-adding it would
#   fail. Two constraints are the same iff their definitions match.
def _constraintFixes(old_cons, new_cons, table):
    have = {defn for _n, _t, defn in new_cons}
    out = []
    for name, ctype, defn in old_cons:
        if defn in have:
            continue
        out.append(f"ALTER TABLE {table} ADD CONSTRAINT {name} {defn}")
    return out


# _inboundFkFixes
# Purpose : repoint foreign keys that now reference <table>_old back at <table>.
# There is no ALTER ... ALTER CONSTRAINT for this; a foreign key's target is
#   immutable. The constraint must be dropped and re-added. `pg_get_constraintdef`
#   gives us the old definition with `REFERENCES results_old(...)` baked in, so
#   we rewrite that one token.
# NOT VALID + VALIDATE would let this run without a long ACCESS EXCLUSIVE lock on
#   the child; we do it plainly because the launcher should be off anyway, and a
#   quiet correct answer beats a clever one.
def _inboundFkFixes(inbound, table, old_table):
    out = []
    for child, name, defn in inbound:
        fixed = defn.replace(f"REFERENCES {old_table}(", f"REFERENCES {table}(")
        fixed = fixed.replace(f"REFERENCES public.{old_table}(",
                              f"REFERENCES public.{table}(")
        if fixed == defn:
            out.append(f"-- MANUAL: could not rewrite {child}.{name}: {defn}")
            continue
        out.append(f"ALTER TABLE {child} DROP CONSTRAINT {name}")
        out.append(f"ALTER TABLE {child} ADD CONSTRAINT {name} {fixed}")
    return out


# ================================================================== #
# ORCHESTRATION
# ================================================================== #

def _plan(cur, table):
    old = f"{table}_old"
    old_cols = _columnFacts(cur, old)
    new_cols = _columnFacts(cur, table)
    old_cons = _tableConstraints(cur, old)
    new_cons = _tableConstraints(cur, table)
    inbound = _inboundForeignKeys(cur, old)

    print("=" * 74)
    print(f"CONSTRAINT DIFF: {table}  vs  {old}")
    print("=" * 74)

    missing_cols = set(old_cols) - set(new_cols)
    if missing_cols:
        print(f"\n  !! columns present on {old} and MISSING on {table}: "
              f"{sorted(missing_cols)}")
        print(f"     The rebuild dropped data. Do not proceed.")

    print(f"\n  {old}: {sum(1 for nn, _ in old_cols.values() if nn)} NOT NULL, "
          f"{sum(1 for _, d in old_cols.values() if d)} defaults, "
          f"{len(old_cons)} non-PK constraints")
    print(f"  {table}: {sum(1 for nn, _ in new_cols.values() if nn)} NOT NULL, "
          f"{sum(1 for _, d in new_cols.values() if d)} defaults, "
          f"{len(new_cons)} non-PK constraints")

    if inbound:
        print(f"\n  !! {len(inbound)} foreign keys now point at {old}:")
        for child, name, defn in inbound:
            print(f"       {child}.{name}")
        print(f"     DROP TABLE {old} would fail or cascade these away.")
    else:
        print(f"\n  no inbound foreign keys reference {old}")

    stmts = (_notNullFixes(old_cols, new_cols, table)
             + _defaultFixes(old_cols, new_cols, table)
             + _constraintFixes(old_cons, new_cons, table)
             + _inboundFkFixes(inbound, table, old))
    return stmts


# _violations
# Purpose : before SET NOT NULL, check the column actually has no NULLs.
# A SET NOT NULL on a column containing NULLs raises and aborts the transaction.
#   Better to say WHICH column and HOW MANY rows than to hand back a bare error.
# This is a full scan per column. On 191M rows it is slow. It is also the only
#   honest way to know.
def _violations(cur, stmt):
    if "SET NOT NULL" not in stmt:
        return None
    parts = stmt.split()
    table, col = parts[2], parts[5]
    cur.execute(f"SELECT count(*) FROM {table} WHERE {col} IS NULL")
    n = cur.fetchone()[0]
    return (table, col, n) if n else None


def main():
    ap = argparse.ArgumentParser(
        description="Restore constraints the CREATE TABLE AS merge destroyed.")
    ap.add_argument("--table", default="results_tf")
    ap.add_argument("--apply", action="store_true",
                    help="run the statements. Without this, report only.")
    ap.add_argument("--skip-null-check", action="store_true",
                    help="skip the pre-flight NULL scan (slow on 191M rows)")
    args = ap.parse_args()

    table, old = args.table, f"{args.table}_old"
    initPool()
    with getConn() as conn:
        with conn.cursor() as cur:
            if not _exists(cur, old):
                print(f"{old} does not exist. Either the merge never ran, or "
                      f"{old} was already dropped -- in which case the original "
                      f"constraint definitions are GONE and must be recovered "
                      f"from your schema DDL by hand.")
                sys.exit(1)
            stmts = _plan(cur, table)

            print(f"\n{'-' * 74}")
            if not stmts:
                print("Nothing to repair.")
                return
            print(f"{len(stmts)} statements to run:\n")
            for s in stmts:
                print(f"  {s};")

            if not args.skip_null_check:
                print(f"\n{'-' * 74}\nchecking for NULLs that would block SET NOT NULL...")
                blocked = [v for v in (_violations(cur, s) for s in stmts) if v]
                for t, c, n in blocked:
                    print(f"  !! {t}.{c} has {n:,} NULLs -- SET NOT NULL will fail")
                if blocked:
                    print("\n  Those columns changed meaning, or the merge lost data.")
                    print("  Investigate before repairing.")
                    sys.exit(1)
                print("  clean.")
        conn.rollback()

        if not args.apply:
            print(f"\n{'-' * 74}")
            print("Report only. Nothing changed. To apply:")
            print(f"  python scripts/repair_constraints.py --table {table} --apply")
            print(f"\nRun this BEFORE verify_merge.py --drop. Once {old} is gone,")
            print("these definitions are gone with it.")
            return

        # ONE transaction: all of it, or none of it.
        with conn.cursor() as cur:
            for s in stmts:
                if s.startswith("--"):
                    print(f"  SKIPPED {s}")
                    continue
                print(f"  {s}")
                cur.execute(s)
        conn.commit()
        print(f"\nrepaired. {table} now matches {old}'s constraints.")
        print(f"Safe to run: python scripts/verify_merge.py --table {table} --drop")


if __name__ == "__main__":
    main()