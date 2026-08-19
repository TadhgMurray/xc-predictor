# Project: xc-predictor
# File:    scripts/rollback_shifted_stamps.py
# ---------------------------------------------------------------------------
# THE IDEA
#   Pass 2 (unique-name) welded ~3.4K COLUMN-SHIFTED tfrrs rows onto real
#   people. On a shifted row the parser slid fields over: athlete_name holds
#   the SCHOOL ("Johns Hopkins") and `school` holds a TIME ("4:11.98") or the
#   real name. Those rows are not that person -- null their person_id.
#
#   PREDICATE: id-less tfrrs row, currently stamped, whose `school` looks like
#   a time (starts with a digit, or contains ':'). Evidence: the top-15 school
#   values among stamped rows are all real schools, so this predicate is
#   specific -- it does not touch the ~1.04M good stamps.
#
#   Read-only census by default. --apply nulls them, commits, re-censuses.
#   Idempotent: re-running after apply finds 0.
# ---------------------------------------------------------------------------

import argparse
import sys

sys.path.insert(0, "scripts")
from database import getConn, initPool

# The shift signature: `school` is a time, not a school.
_SHIFT_PREDICATE = r"""
      source = 'tfrrs'
  AND athlete_id IS NULL
  AND person_id IS NOT NULL
  AND school ~ '^[0-9]+:[0-9]{2}(\.[0-9]+)?$|^[0-9]+\.[0-9]+$'
"""


def _census(cur, table):
    """Count stamped shifted rows; show a sample so you can eyeball them."""
    cur.execute(f"SELECT count(*) FROM {table} WHERE {_SHIFT_PREDICATE}")
    n = cur.fetchone()[0]
    print(f"  stamped column-shifted rows: {n:,}")
    if n:
        cur.execute(f"""
            SELECT athlete_name, school, person_id
            FROM {table} WHERE {_SHIFT_PREDICATE} LIMIT 5
        """)
        for row in cur.fetchall():
            print(f"    name={row[0]!r}  school={row[1]!r}  welded_to={row[2]}")
    return n


def _rollback(cur, table):
    """Null person_id on the shifted rows. Returns rows updated."""
    cur.execute(f"UPDATE {table} SET person_id = NULL WHERE {_SHIFT_PREDICATE}")
    return cur.rowcount


def main():
    ap = argparse.ArgumentParser(description="Un-weld column-shifted tfrrs rows.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="TF")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    table = "results" if args.sport == "XC" else "results_tf"

    initPool()
    with getConn() as conn, conn.cursor() as cur:
        print(f"=== {args.sport}: rollback column-shifted stamps ===")
        print("BEFORE:")
        n = _census(cur, table)

        if not args.apply:
            print(f"\n[census only] {n:,} rows would be un-stamped. --apply to write.")
            conn.rollback()
            return

        updated = _rollback(cur, table)
        conn.commit()
        print(f"\n[apply] nulled person_id on {updated:,} rows.")

        print("AFTER (read-back):")
        _census(cur, table)
        conn.rollback()


if __name__ == "__main__":
    main()