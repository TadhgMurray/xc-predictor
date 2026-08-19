# Project: xc-predictor
# File:    scripts/diag_drop_collisions.py
# Purpose: READ-ONLY. Answer one question before the pipeline runs:
#          _RESULT_DROP is keyed by BARE result_id (backfill_normalize.py:1211)
#          and shared by both sports. If the id spaces of `results` (XC) and
#          `results_tf` (TF) overlap, every drop aimed at one sport also
#          executes against any same-numbered row in the OTHER sport's table.
#          This script counts those innocent casualties exactly.
#
# Reads:   the four triage drop files (scripts\result_drop_*_{xc,tf}.py),
#          the pre-merge base from the corrections .bak, and the two results
#          tables. Writes: nothing. Safe to run at any time.
#
# Usage:
#   python scripts\diag_drop_collisions.py --bak engine\corrections.py.bak-20260713-115200
#
# Exit codes (same convention as verify_redetect):
#   0  id spaces effectively disjoint -> bare keying is safe, run the pipeline
#   1  contamination found            -> fix the keying BEFORE the pipeline
#   2  a precondition failed          -> the printed check names it

import argparse
import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader

sys.path.insert(0, "scripts")
from database import getConn, initPool

# Ask Postgres about ids in batches of this many. One = ANY(array) probe per
# batch; 50k int index probes per query is comfortable, and batching keeps any
# single query's array parameter a sane size.
_CHUNK = 50_000

_DROP_FILES = {
    "XC": ["result_drop_oneoff_xc.py", "result_drop_slow_xc.py"],
    "TF": ["result_drop_oneoff_tf.py", "result_drop_slow_tf.py"],
}


# ================================================================== #
# LOADERS -- get the drop ids back out of the source files
# ================================================================== #

# _importByPath
# Purpose : load a Python source file as a module WITHOUT it being on sys.path,
#           and regardless of its filename.
# Syntax  : spec_from_file_location builds an import "spec" (a recipe); BUT it
#           infers the loader from the FILE EXTENSION, so for a non-.py name
#           (the .bak-<timestamp> backup) it returns None. Passing an explicit
#           SourceFileLoader overrides the inference: "this is Python source,
#           whatever it's called." module_from_spec makes an empty module from
#           the spec; exec_module runs the file's code into it.
def _importByPath(path):
    if not os.path.exists(path):
        sys.exit(f"!! missing file: {path}  (exit 2)")
    loader = SourceFileLoader("_probe", path)
    spec = importlib.util.spec_from_file_location("_probe", path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# _asIdSet
# Purpose : the additions files may hold a dict (id -> anything) or a set of
#           ids; normalize either to a plain set of ints.
def _asIdSet(obj, where):
    if obj is None:
        sys.exit(f"!! no _RESULT_DROP* object found in {where}  (exit 2)")
    return set(obj.keys()) if isinstance(obj, dict) else set(obj)


# _loadSportDrops
# Purpose : one sport's full proposed drop set = union of its oneoff + slow
#           files. Also returns the per-file sizes so the report can show
#           where any within-sport duplicate lives.
def _loadSportDrops(sport):
    total, sizes = set(), {}
    for name in _DROP_FILES[sport]:
        path = os.path.join("scripts", name)
        mod = _importByPath(path)
        ids = _asIdSet(getattr(mod, "_RESULT_DROP_ADDITIONS", None), path)
        sizes[name] = len(ids)
        total |= ids
    return total, sizes


# _loadBakBase
# Purpose : the 4 (or so) manual drops that existed BEFORE this merge, read
#           from the timestamped backup apply_triage made. Overlap with these
#           is the benign explanation for missing keys.
def _loadBakBase(bak_path):
    mod = _importByPath(bak_path)
    return _asIdSet(getattr(mod, "_RESULT_DROP", None), bak_path)


# ================================================================== #
# DATABASE PROBES -- all SELECTs, nothing else
# ================================================================== #

def _chunks(seq, n):
    """Yield consecutive slices of size n. sorted() first so each ANY(array)
    probe walks the PK index in order instead of hopping randomly."""
    seq = sorted(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# _presentIn
# Purpose : which of `ids` exist as result_id in `table`?
# Syntax  : result_id = ANY(%s) -- psycopg adapts a Python list to a Postgres
#           array; ANY() is an indexed membership test, so this is len(chunk)
#           PK lookups, not a table scan.
# Output  : the set of ids found (not just a count -- if contamination exists
#           we want the actual ids, they become the pardon/re-key worklist).
def _presentIn(cur, table, ids):
    found = set()
    for chunk in _chunks(ids, _CHUNK):
        cur.execute(
            f"SELECT result_id FROM {table} WHERE result_id = ANY(%s)",
            (chunk,))
        found.update(r[0] for r in cur.fetchall())
    return found


def _idRange(cur, table):
    cur.execute(f"SELECT min(result_id), max(result_id) FROM {table}")
    return cur.fetchone()


# ================================================================== #
# REPORT
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Count cross-sport casualties of the sport-blind "
                    "_RESULT_DROP key. Read-only.")
    ap.add_argument("--bak", required=True,
                    help=r"pre-merge backup, e.g. engine\corrections.py.bak-...")
    ap.add_argument("--sample", type=int, default=10,
                    help="how many example ids to print per finding")
    args = ap.parse_args()

    # ---- 1. rebuild the sets from source ------------------------------- #
    xc, xc_sizes = _loadSportDrops("XC")
    tf, tf_sizes = _loadSportDrops("TF")
    base = _loadBakBase(args.bak)

    print("== drop sets, from source files ==")
    for name, n in {**xc_sizes, **tf_sizes}.items():
        print(f"  {name:<28} {n:>8,}")
    print(f"  {'base (from .bak)':<28} {len(base):>8,}")

    # ---- 2. explain the merge arithmetic (the missing 9) --------------- #
    # The merged dict's size is |base U xc U tf|; every id claimed by more
    # than one source costs exactly one key. Show each overlap by name.
    file_sum = sum(xc_sizes.values()) + sum(tf_sizes.values())
    union = base | xc | tf
    print("\n== merge arithmetic ==")
    print(f"  sum of file entries          {file_sum:>8,}")
    print(f"  |base|                       {len(base):>8,}")
    print(f"  |union| (= merged dict)      {len(union):>8,}")
    print(f"  keys lost to duplication     {file_sum + len(base) - len(union):>8,}")
    print(f"    xc n tf   (CROSS-SPORT)    {len(xc & tf):>8,}")
    print(f"    xc n base                  {len(xc & base):>8,}")
    print(f"    tf n base                  {len(tf & base):>8,}")
    within_xc = sum(xc_sizes.values()) - len(xc)   # oneoff n slow, same sport:
    within_tf = sum(tf_sizes.values()) - len(tf)   # should be 0 -- a row
    print(f"    within XC (oneoff n slow)  {within_xc:>8,}   <- should be 0")
    print(f"    within TF (oneoff n slow)  {within_tf:>8,}   <- should be 0")

    # ---- 3. the question that matters: presence in the OTHER table ----- #
    initPool()
    with getConn() as conn, conn.cursor() as cur:
        print("\n== id ranges ==")
        for t in ("results", "results_tf"):
            lo, hi = _idRange(cur, t)
            print(f"  {t:<12} result_id {lo:,} .. {hi:,}")

        print("\n== cross-table casualties (the verdict) ==")
        xc_hits_tf = _presentIn(cur, "results_tf", xc)   # innocent TF rows
        tf_hits_xc = _presentIn(cur, "results",    tf)   # innocent XC rows
        conn.rollback()   # end the read txn cleanly; we never write

    print(f"  XC drop ids present in results_tf : {len(xc_hits_tf):,}")
    print(f"  TF drop ids present in results    : {len(tf_hits_xc):,}")
    for label, hits in (("results_tf", xc_hits_tf), ("results", tf_hits_xc)):
        if hits:
            ex = ", ".join(str(i) for i in sorted(hits)[:args.sample])
            print(f"    e.g. in {label}: {ex}")

    # ---- 4. verdict ----------------------------------------------------- #
    contaminated = len(xc_hits_tf) + len(tf_hits_xc)
    if contaminated == 0:
        print("\nVERDICT: id spaces are disjoint in practice. Bare result_id")
        print("keying is safe for THIS apply. (Re-check after any id-space")
        print("change, e.g. the fly-db sync.)  exit 0")
        sys.exit(0)
    print(f"\nVERDICT: {contaminated:,} innocent rows in the other sport's")
    print("table WILL be dropped by the sport-blind key. Fix the keying")
    print("(per-sport drop sets) BEFORE running the pipeline.  exit 1")
    sys.exit(1)


if __name__ == "__main__":
    main()