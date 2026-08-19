# Project: xc-predictor
# File:    scripts/diag_tf_pardons.py
# Purpose: Un-sentence TF drop verdicts that were computed on POISONED data.
#
# THE PROBLEM THIS SOLVES
# -----------------------
# Before the per-sport keying fix, XC-intent _DISTANCE_OVERRIDES keys collided
# with 47 real TF (meet, div) divisions (8,734 rows): the TF loader forced an
# XC distance onto those rows, so their normalized times were wrong, so pass-2
# triage sentenced them on manufactured evidence. Those verdicts are VOID --
# not necessarily wrong, but unsafe to execute. This script finds every id in
# the two TF drop files whose row lives in a colliding division and removes
# ("pardons") it, so the row survives to be RE-JUDGED by pass 3 on clean data.
# A pardoned row that is genuinely bad will simply be re-flagged and
# re-sentenced next pass; a pardoned row that was innocent is saved.
#
# RUN THIS BEFORE apply_triage.py (it edits the generated TF drop files, and
# apply's content-hash idempotence then applies the pardoned versions).
#
# Default is READ-ONLY (report). --apply rewrites the two files (with .bak).
#
# USAGE
#   python scripts\diag_tf_pardons.py                # report only
#   python scripts\diag_tf_pardons.py --apply        # rewrite the TF files
#
# Exit codes: 0 = ok (report or apply done) / 2 = precondition failed.

import argparse
import datetime
import importlib.util
import os
import re
import shutil
import sys
from importlib.machinery import SourceFileLoader

sys.path.insert(0, "scripts")
from database import getConn, initPool

_TF_DROP_FILES = ("result_drop_oneoff_tf.py", "result_drop_slow_tf.py")
_XC_OVERRIDE_ADDITIONS = "distance_override_xc.py"


# ================================================================== #
# CHUNK 1 -- LOADERS
# ================================================================== #

def _importByPath(path):
    """Path-based, filename-agnostic module load (explicit SourceFileLoader --
    the .bak lesson: the loader is inferred from the extension otherwise)."""
    if not os.path.exists(path):
        sys.exit(f"!! missing file: {path}  (exit 2)")
    loader = SourceFileLoader("_probe", path)
    spec = importlib.util.spec_from_file_location("_probe", path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# _xcOverrideKeys
# Purpose : the FULL set of XC-intent (meet, div) override keys, independent
#           of whether apply_triage has merged yet: base keys from
#           corrections.py's _DISTANCE_OVERRIDES_XC, plus the pass-2 additions
#           file if it is still sitting in scripts\. Using the union makes the
#           pardon list identical whichever side of apply this runs on.
def _xcOverrideKeys(corr_path, gen_dir):
    mod = _importByPath(corr_path)
    base = getattr(mod, "_DISTANCE_OVERRIDES_XC", None)
    if base is None:
        sys.exit("!! corrections.py has no _DISTANCE_OVERRIDES_XC -- this "
                 "script requires the per-sport restructured base.  (exit 2)")
    keys = set(base)
    add_path = os.path.join(gen_dir, _XC_OVERRIDE_ADDITIONS)
    if os.path.exists(add_path):
        add = _importByPath(add_path)
        keys |= set(getattr(add, "_DISTANCE_OVERRIDES_ADDITIONS", {}))
    return keys


# _tfDropIds
# Purpose : every id in one TF drop file, mapped to its raw line so a pardon
#           can later be executed as "omit this exact line".
# Syntax  : the writer emits entries as `    <int>,  # comment`; the regex
#           anchors on optional whitespace + digits + comma so header comments,
#           braces, and blank lines are never mistaken for entries.
_ENTRY_RE = re.compile(r"^\s*(\d+)\s*,")

def _tfDropIds(path):
    ids = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = _ENTRY_RE.match(line)
            if m:
                ids[int(m.group(1))] = line.rstrip("\n")
    return ids


# ================================================================== #
# CHUNK 2 -- DATABASE: which TF rows live in the colliding divisions?
# ================================================================== #

# _collidingDivisionRows
# Purpose : the result_ids of every results_tf row whose (meet_id, div_id)
#           matches an XC override key -- i.e. every row whose pass-2
#           normalized_time was computed under a forced XC distance.
# Syntax  : unnest(a, b) AS k(m, d) zips two parallel arrays into a virtual
#           2-column table; the JOIN makes each key an indexed meet_id probe.
#           ~8.7K rows expected, so fetching the ids outright is cheap and the
#           intersection with the drop files happens in Python.
def _collidingDivisionRows(cur, keys):
    meets = [m for m, _ in sorted(keys)]
    divs = [d for _, d in sorted(keys)]
    cur.execute("""
        SELECT t.result_id, t.meet_id, t.div_id
        FROM unnest(%s::bigint[], %s::bigint[]) AS k(m, d)
        JOIN results_tf t ON t.meet_id = k.m AND t.div_id = k.d
    """, (meets, divs))
    return cur.fetchall()


# ================================================================== #
# CHUNK 3 -- THE PARDON (report, and optionally rewrite)
# ================================================================== #

# _rewriteWithout
# Purpose : rewrite one drop file with the pardoned ids' lines omitted, a
#           timestamped .bak beside it, and an audit comment (which ids, why)
#           appended to the header so the file explains its own history.
def _rewriteWithout(path, pardoned, stamp):
    backup = f"{path}.bak-{stamp}"
    shutil.copy2(path, backup)
    out, removed = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = _ENTRY_RE.match(line)
            if m and int(m.group(1)) in pardoned:
                removed += 1
                continue
            out.append(line)
    # audit note goes directly under the first header line
    note = (f"# PARDONED {removed} ids {datetime.date.today()}: rows in "
            f"XC-override-colliding divisions; verdicts were computed on "
            f"poisoned normalized times. Pass 3 re-judges them clean.\n")
    for i, line in enumerate(out):
        if not line.startswith("#"):
            out.insert(i, note)
            break
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)
    return removed, backup


def main():
    ap = argparse.ArgumentParser(
        description="Pardon TF drop verdicts from XC-override-poisoned "
                    "divisions so pass 3 re-judges them on clean data.")
    ap.add_argument("--dir", default="scripts",
                    help="dir holding the generated drop files (default scripts)")
    ap.add_argument("--corrections",
                    default=os.path.join("engine", "corrections.py"))
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the TF drop files (default: report only)")
    args = ap.parse_args()

    keys = _xcOverrideKeys(args.corrections, args.dir)
    print(f"XC-intent override keys considered: {len(keys):,}")

    initPool()
    with getConn() as conn, conn.cursor() as cur:
        rows = _collidingDivisionRows(cur, keys)
        conn.rollback()                      # read-only txn, end it cleanly
    poisoned = {rid: (m, d) for rid, m, d in rows}
    n_divs = len(set(poisoned.values()))
    print(f"colliding TF divisions: {n_divs}   poisoned rows: {len(poisoned):,}")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    total = 0
    for fname in _TF_DROP_FILES:
        path = os.path.join(args.dir, fname)
        ids = _tfDropIds(path)
        pardoned = set(ids) & set(poisoned)
        total += len(pardoned)
        print(f"\n{fname}: {len(ids):,} drops, {len(pardoned):,} to pardon")
        for rid in sorted(pardoned)[:10]:
            m, d = poisoned[rid]
            print(f"    {rid}  (meet/div {m}/{d})")
        if len(pardoned) > 10:
            print(f"    ... and {len(pardoned) - 10:,} more")
        if args.apply and pardoned:
            removed, backup = _rewriteWithout(path, pardoned, stamp)
            print(f"    [apply] {removed:,} lines removed; backup: {backup}")

    if not args.apply:
        print(f"\nREPORT ONLY: {total:,} verdicts would be pardoned. "
              f"Re-run with --apply to execute.")
    else:
        print(f"\nDONE: {total:,} verdicts pardoned. Run apply_triage.py "
              f"next -- the edited files hash differently, so they apply "
              f"fresh, minus the pardons.")


if __name__ == "__main__":
    main()