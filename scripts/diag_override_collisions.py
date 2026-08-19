# Project: xc-predictor
# File:    scripts/diag_override_collisions.py
# Purpose: READ-ONLY. The drops probe proved result_id spaces overlap between
#          `results` (XC) and `results_tf` (TF). This script asks the same
#          question for the REMAINING sport-blind correction structures:
#
#            _RESULT_OVERRIDE    result_id      (wins FIRST in the loader)
#            _DISTANCE_OVERRIDES (meet, div)    (beats event_short parsing)
#            _DISTANCE_DROP      (meet, div)    (skips the whole division)
#            _GENDER_OVERRIDES   (meet, div)    (forces gender)
#
#          For each structure it counts (a) how many keys match rows in EACH
#          table and (b) how many ROWS those matches touch -- a single
#          (meet,div) collision can force a distance onto hundreds of rows,
#          so key counts alone understate the damage.
#
# ASSUMPTION (veto if wrong): every key currently in these structures was
#          created with XC intent -- the base entries came from the XC audit,
#          and the merged additions came from distance_override_xc.py (the TF
#          file is on HOLD). Under that assumption, matches in `results` are
#          the corrections DOING THEIR JOB and matches in `results_tf` are
#          CONTAMINATION. If any base entry was TF-intent, say so and we
#          re-read the verdict.
#
# Writes:  nothing. Usage:
#   python scripts\diag_override_collisions.py
#   python scripts\diag_override_collisions.py --corrections engine\corrections.py
#
# Exit codes: 0 no TF-side matches (clean) / 1 contamination / 2 precondition.

import argparse
import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader

sys.path.insert(0, "scripts")
from database import getConn, initPool

_CHUNK = 50_000          # ids per ANY() probe (same rationale as drops probe)
_TABLES = ("results", "results_tf")


# ================================================================== #
# LOADING -- pull the four structures out of corrections.py
# ================================================================== #

# _importByPath
# Purpose : execute a Python source file as a module, path-based, filename-
#           agnostic. Explicit SourceFileLoader because spec_from_file_location
#           infers the loader from the extension (the .bak lesson).
def _importByPath(path):
    if not os.path.exists(path):
        sys.exit(f"!! missing file: {path}  (exit 2)")
    loader = SourceFileLoader("_probe", path)
    spec = importlib.util.spec_from_file_location("_probe", path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# _loadStructures
# Purpose : the four sport-blind structures, normalized: composite-keyed ones
#           to a set of (meet, div) tuples, _RESULT_OVERRIDE to a set of ids.
#           .keys() works on dicts (overrides map key -> value); sets pass
#           through set() unchanged (_DISTANCE_DROP is a set of tuples).
def _loadStructures(path):
    mod = _importByPath(path)

    def grab(name):
        obj = getattr(mod, name, None)
        if obj is None:
            sys.exit(f"!! {name} not found in {path}  (exit 2)")
        return set(obj.keys()) if isinstance(obj, dict) else set(obj)

    return {
        "_RESULT_OVERRIDE":    grab("_RESULT_OVERRIDE"),     # result_id keys
        "_DISTANCE_OVERRIDES": grab("_DISTANCE_OVERRIDES"),  # (meet, div)
        "_DISTANCE_DROP":      grab("_DISTANCE_DROP"),       # (meet, div)
        "_GENDER_OVERRIDES":   grab("_GENDER_OVERRIDES"),    # (meet, div)
    }


# ================================================================== #
# SCHEMA CHECK -- never guess column names
# ================================================================== #

# _checkColumns
# Purpose : confirm result_id / meet_id / div_id exist in both tables before
#           interpolating them into SQL. information_schema.columns is the
#           standard catalog view; one query per table.
# Output  : nothing on success; on failure prints the ACTUAL column list so
#           the fix is a rename in this file, not archaeology.
def _checkColumns(cur):
    need = {"result_id", "meet_id", "div_id"}
    for t in _TABLES:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
        """, (t,))
        have = {r[0] for r in cur.fetchall()}
        if not have:
            sys.exit(f"!! table {t} not found  (exit 2)")
        missing = need - have
        if missing:
            print(f"!! {t} lacks expected column(s) {sorted(missing)}.")
            print(f"   actual columns: {sorted(have)}")
            sys.exit("   edit the column names in this script to match. (exit 2)")
    print(f"  [ok] result_id/meet_id/div_id present in {' and '.join(_TABLES)}")


# ================================================================== #
# PROBES -- all SELECTs
# ================================================================== #

def _chunks(seq, n):
    seq = sorted(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# _idHits
# Purpose : how many of `ids` exist as result_id in `table`? For a PK, keys
#           hit = rows hit, so one number suffices.
def _idHits(cur, table, ids):
    found = 0
    for chunk in _chunks(ids, _CHUNK):
        cur.execute(
            f"SELECT count(*) FROM {table} WHERE result_id = ANY(%s)",
            (chunk,))
        found += cur.fetchone()[0]
    return found


# _pairHits
# Purpose : which (meet, div) keys match rows in `table`, and how many rows
#           each -- the row count is the blast radius of a composite key.
# Syntax  : unnest(%s::bigint[], %s::bigint[]) AS k(m, d) turns two parallel
#           arrays into a 2-column virtual table (row i = (meets[i], divs[i])),
#           which we JOIN against the results table. The planner runs this as
#           ~len(pairs) index probes on meet_id -- no composite index needed.
#           NULL div_ids in the table simply never match (= is NULL-strict).
# Output  : (number of keys that matched, total rows touched).
def _pairHits(cur, table, pairs):
    keys_hit, rows_hit = 0, 0
    for chunk in _chunks(pairs, _CHUNK):
        meets = [m for m, _ in chunk]
        divs = [d for _, d in chunk]
        cur.execute(f"""
            SELECT count(DISTINCT (k.m, k.d)), count(*)
            FROM unnest(%s::bigint[], %s::bigint[]) AS k(m, d)
            JOIN {table} t ON t.meet_id = k.m AND t.div_id = k.d
        """, (meets, divs))
        k, r = cur.fetchone()
        keys_hit += k
        rows_hit += r
    return keys_hit, rows_hit


# ================================================================== #
# REPORT
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Cross-sport collision probe for the (meet,div)-keyed and "
                    "_RESULT_OVERRIDE corrections. Read-only.")
    ap.add_argument("--corrections", default=os.path.join("engine", "corrections.py"),
                    help="which corrections file to probe (default: the live one)")
    args = ap.parse_args()

    structs = _loadStructures(args.corrections)
    print(f"== structures loaded from {args.corrections} ==")
    for name, keys in structs.items():
        print(f"  {name:<22} {len(keys):>6,} keys")

    initPool()
    tf_rows_total = 0                     # contamination accumulator
    with getConn() as conn, conn.cursor() as cur:
        print("\n== schema check ==")
        _checkColumns(cur)

        # -- result_id-keyed: same probe shape as the drops script --------- #
        print("\n== _RESULT_OVERRIDE (result_id keys, wins FIRST) ==")
        ov_ids = structs["_RESULT_OVERRIDE"]
        for t in _TABLES:
            hits = _idHits(cur, t, ov_ids)
            print(f"  present in {t:<12}: {hits:,}")
            if t == "results_tf":
                tf_rows_total += hits

        # -- (meet, div)-keyed: keys hit AND rows touched ------------------ #
        for name in ("_DISTANCE_OVERRIDES", "_DISTANCE_DROP", "_GENDER_OVERRIDES"):
            print(f"\n== {name} ((meet, div) keys) ==")
            pairs = structs[name]
            if not pairs:
                print("  (empty)")
                continue
            for t in _TABLES:
                k, r = _pairHits(cur, t, pairs)
                print(f"  {t:<12}: {k:,} keys match, touching {r:,} rows")
                if t == "results_tf":
                    tf_rows_total += r
        conn.rollback()                   # end the read txn; we never write

    # -- verdict, under the XC-intent assumption --------------------------- #
    print("\n" + "=" * 66)
    if tf_rows_total == 0:
        print("VERDICT: no key touches results_tf. The composite-key and")
        print("override structures are clean; only _RESULT_DROP needs the")
        print("per-sport fix.  exit 0")
        sys.exit(0)
    print(f"VERDICT: {tf_rows_total:,} results_tf rows are touched by")
    print("XC-intent corrections. This contamination is ALREADY LIVE (the")
    print("base entries shipped in every previous TF pipeline run). The")
    print("per-sport restructure must cover these structures too.  exit 1")
    sys.exit(1)


if __name__ == "__main__":
    main()