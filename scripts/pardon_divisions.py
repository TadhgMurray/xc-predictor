# Project: xc-predictor
# File:    scripts/pardon_divisions.py
# Purpose: guard PASS-3 triage output against the 22 divisions whose data was
#          wrong when the pass-2/3 worksheets were computed. Two attack
#          surfaces, both covered:
#
#          1. DROP ROWS -- any pass-3 drop verdict on a row living in one of
#             these divisions was sentenced on poisoned normalized times.
#             Void, not proven wrong: remove the line, let pass 4 re-judge on
#             clean data. (Same rule as the 47 TF divisions on 7/13.)
#          2. OVERRIDE KEYS -- pass-3's auto distance proposals for these
#             divisions were computed pre-fix and are attenuation-biased;
#             apply's update() would OVERWRITE the page-verified values with
#             them (the 9308-collision mechanism). Remove the keys.
#
# READ-ONLY by default; --apply rewrites the generated files (timestamped
# .bak + audit comment, same mechanics as diag_tf_pardons.py).
#
# USAGE
#   python scripts\pardon_divisions.py --sport XC --pairs-file the22.txt
#   python scripts\pardon_divisions.py --sport XC --pairs-file the22.txt --apply

import argparse
import datetime
import os
import re
import shutil
import sys

sys.path.insert(0, "scripts")
from database import getConn, initPool

_TABLE = {"XC": "results", "TF": "results_tf"}
_CHUNK = 50_000

# entry-line shapes in the generated files
_DROP_RE = re.compile(r"^\s*(\d+)\s*,")                 # `    12345,  # ...`
_KEY_RE = re.compile(r"^\s*\((\d+)\s*,\s*(\d+)\)\s*:")  # `    (m, d): 5000,  # ...`


# ================================================================== #
# CHUNK 1 -- WHICH ROWS LIVE IN THE PARDONED DIVISIONS
# ================================================================== #

def _chunks(seq, n):
    seq = sorted(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# _rowsInDivisions
# Purpose : of the candidate drop ids, which belong to a pardoned (meet, div)?
#           PK probes via ANY(array); the ids are the drop files' contents so
#           this is tens of thousands of indexed lookups, not a scan.
def _rowsInDivisions(cur, table, ids, pairs):
    hit = set()
    for chunk in _chunks(ids, _CHUNK):
        cur.execute(
            f"SELECT result_id, meet_id, div_id FROM {table} "
            f"WHERE result_id = ANY(%s)", (chunk,))
        hit.update(rid for rid, m, d in cur.fetchall() if (m, d) in pairs)
    return hit


# ================================================================== #
# CHUNK 2 -- FILE SURGERY (parse / filter / rewrite with audit)
# ================================================================== #

def _entryIds(path):
    ids = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            m = _DROP_RE.match(line)
            if m:
                ids.add(int(m.group(1)))
    return ids


def _rewrite(path, keep_line, reason, stamp):
    """Rewrite `path` keeping only lines where keep_line(line) is True; .bak
    beside it; audit comment under the header. Returns lines removed."""
    backup = f"{path}.bak-{stamp}"
    shutil.copy2(path, backup)
    out, removed = [], 0
    for line in open(path, encoding="utf-8"):
        if keep_line(line):
            out.append(line)
        else:
            removed += 1
    note = f"# PARDONED {removed} entries {datetime.date.today()}: {reason}\n"
    for i, line in enumerate(out):
        if not line.startswith("#"):
            out.insert(i, note)
            break
    open(path, "w", encoding="utf-8").writelines(out)
    return removed, backup


# ================================================================== #
# CHUNK 3 -- ORCHESTRATION
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(description="Strip pass-3 verdicts touching "
                                 "the pardoned divisions (drop rows + override "
                                 "keys). Read-only unless --apply.")
    ap.add_argument("--sport", choices=["XC", "TF"], default="XC")
    ap.add_argument("--pairs-file", required=True,
                    help="text file of 'meet div' lines")
    ap.add_argument("--dir", default="scripts")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    pairs = set()
    for line in open(args.pairs_file, encoding="utf-8"):
        if line.strip():
            m, d = line.split()
            pairs.add((int(m), int(d)))
    print(f"pardoned divisions: {len(pairs)}")

    s = args.sport.lower()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    # ---- surface 1: drop rows ---------------------------------------- #
    drop_files = [os.path.join(args.dir, f"result_drop_oneoff_{s}.py"),
                  os.path.join(args.dir, f"result_drop_slow_{s}.py")]
    all_ids = set()
    for p in drop_files:
        all_ids |= _entryIds(p)
    print(f"drop candidates parsed: {len(all_ids):,}")

    initPool()
    with getConn() as conn, conn.cursor() as cur:
        pardoned = _rowsInDivisions(cur, _TABLE[args.sport], all_ids, pairs)
        conn.rollback()
    print(f"drop rows inside pardoned divisions: {len(pardoned):,}")

    for p in drop_files:
        if not os.path.exists(p):
            print(f"  [skip] {p}: not present")
            continue
        mine = _entryIds(p) & pardoned
        print(f"  {os.path.basename(p)}: {len(mine):,} to pardon")
        if args.apply and mine:
            def keep(line, mine=mine):
                m = _DROP_RE.match(line)
                return not (m and int(m.group(1)) in mine)
            removed, backup = _rewrite(
                p, keep, "rows in divisions whose data was wrong at "
                "sentencing; pass 4 re-judges them clean", stamp)
            print(f"    [apply] {removed:,} removed; backup: {backup}")

    # ---- surface 2: override keys ------------------------------------ #
    ov_path = os.path.join(args.dir, f"distance_override_{s}.py")
    if os.path.exists(ov_path):
        keys = set()
        for line in open(ov_path, encoding="utf-8"):
            m = _KEY_RE.match(line)
            if m:
                keys.add((int(m.group(1)), int(m.group(2))))
        clash = keys & pairs
        print(f"{os.path.basename(ov_path)}: {len(keys):,} keys, "
              f"{len(clash):,} collide with pardoned divisions")
        for k in sorted(clash):
            print(f"    would strip {k} (protects the page-verified value)")
        if args.apply and clash:
            def keep(line, clash=clash):
                m = _KEY_RE.match(line)
                return not (m and (int(m.group(1)), int(m.group(2))) in clash)
            removed, backup = _rewrite(
                ov_path, keep, "auto proposals for divisions with page-"
                "verified values in corrections; update() must not "
                "overwrite them", stamp)
            print(f"    [apply] {removed:,} removed; backup: {backup}")
    else:
        print(f"  [skip] {ov_path}: not present")

    if not args.apply:
        print("\nREPORT ONLY. Re-run with --apply to execute.")


if __name__ == "__main__":
    main()