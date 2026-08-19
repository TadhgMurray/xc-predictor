# Project: xc-predictor
# File:    scripts/apply_triage.py
# Purpose: ONE-COMMAND merge of the triage output blocks into corrections.py.
#          Nothing is edited by hand. This script:
#            1. finds corrections.py (same candidate list diag_suspects uses)
#            2. backs it up next to itself (corrections.py.bak-<timestamp>)
#            3. appends each generated *_ADDITIONS file, followed by one merge
#               line, followed by `del` of the temporary name (so consecutive
#               pastes can never bleed into each other)
#            4. re-imports corrections.py and prints before/after sizes, so
#               success is visible as numbers, not faith
#          IDEMPOTENT by content hash: re-running with the same generated
#          files skips them; re-running after triage REGENERATES a file (new
#          content, new hash) applies the new version.
#
# USAGE
#   python scripts/apply_triage.py
#   python scripts/apply_triage.py --dir scripts --corrections engine/corrections.py
# ============================================================================

import argparse
import datetime
import hashlib
import importlib.util
import os
import shutil
import sys


# ================================================================== #
# CHUNK 1 -- WHAT GETS MERGED WHERE
# ================================================================== #
_CANDIDATES = ("engine/corrections.py", "scripts/corrections.py",
               "corrections.py", "backfill/corrections.py")

# (generated filename pattern, name it defines, TARGET PATTERN). Every merge
# uses .update(): set.update takes any iterable, dict.update a dict, so one
# verb covers both containers. Targets are PER-SPORT ({S} = XC|TF from the
# filename): corrections.py keys these structures by sport since the
# 2026-07-13 cross-sport collision incident, and this routing is what puts
# each generated file's entries into the sport it was triaged for.
_MERGES = (
    ("result_drop_oneoff_{s}.py", "_RESULT_DROP_ADDITIONS", "_RESULT_DROP_{S}"),
    ("result_drop_slow_{s}.py", "_RESULT_DROP_ADDITIONS", "_RESULT_DROP_{S}"),
    ("distance_override_{s}.py", "_DISTANCE_OVERRIDES_ADDITIONS",
     "_DISTANCE_OVERRIDES_{S}"),
    # tier-3 splitter output: per-row (distance, gender) pins for merged/mixed
    # divisions (triage_split_divisions.py, added 7/14)
    ("result_override_{s}.py", "_RESULT_OVERRIDE_ADDITIONS",
     "_RESULT_OVERRIDE_{S}"),
    # gender-split output: per-row (distance, gender) pins for divisions where
    # duplicated places proved two races under one div_id AND the label was
    # refuted for exactly one gender (triage_gender_splits.py).
    # Same var and same target as the splitter above -- that is fine: _blockFor
    # emits `del _RESULT_OVERRIDE_ADDITIONS` after each merge line, so two
    # files defining the same name cannot bleed into each other.
    ("result_override_gender_{s}.py", "_RESULT_OVERRIDE_ADDITIONS",
     "_RESULT_OVERRIDE_{S}"),
    ("result_drop_{s}.py", "_RESULT_DROP_ADDITIONS", "_RESULT_DROP_{S}"),
)
_SPORTS = ("xc", "tf")

# Every concrete target name, for sizing and type checks.
_ALL_TARGETS = tuple(sorted({t.format(S=s.upper())
                             for s in _SPORTS for _, _, t in _MERGES}))


# ================================================================== #
# CHUNK 2 -- SMALL HELPERS (find / import / measure / hash)
# ================================================================== #

def _findCorrections(path):
    """Explicit --corrections wins; otherwise first existing candidate."""
    if path:
        if not os.path.exists(path):
            sys.exit(f"--corrections {path}: not found")
        return path
    for c in _CANDIDATES:
        if os.path.exists(c):
            return c
    sys.exit("corrections.py not found; pass --corrections PATH")


def _importByPath(path):
    """Import a module BY FILE PATH (no package assumptions), the same way
    diag_suspects loads corrections."""
    spec = importlib.util.spec_from_file_location("_corr_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sizes(mod):
    """len() of every merge target that exists -- the before/after numbers."""
    return {t: len(getattr(mod, t))
            for t in _ALL_TARGETS if hasattr(mod, t)}


def _md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


# _checkTargets : fail LOUDLY before touching anything if corrections.py's
#   containers are not the shapes the merge lines assume. All four per-sport
#   targets are checked; a missing name means corrections.py predates the
#   per-sport restructure and MUST NOT be merged into.
def _checkTargets(mod):
    want = {"_RESULT_DROP_XC": set, "_RESULT_DROP_TF": set,
            "_DISTANCE_OVERRIDES_XC": dict, "_DISTANCE_OVERRIDES_TF": dict,
            "_RESULT_OVERRIDE_XC": dict, "_RESULT_OVERRIDE_TF": dict}
    for name, kind in want.items():
        obj = getattr(mod, name, None)
        if obj is None:
            sys.exit(f"{name} missing from corrections.py -- this looks like a "
                     "pre-restructure (sport-blind) copy. Restore the "
                     "per-sport base before applying.")
        if not isinstance(obj, kind):
            sys.exit(f"{name} is {type(obj).__name__}, expected "
                     f"{kind.__name__}. Fix the declaration, then re-run.")


# ================================================================== #
# CHUNK 3 -- BUILD THE APPEND TEXT
# ================================================================== #

# _blockFor : one generated file -> the text appended to corrections.py:
#   a hash-stamped marker (idempotence), the file's own content verbatim,
#   the merge line, and a del so the temporary *_ADDITIONS name cannot
#   collide with the next paste.
def _blockFor(gen_path, var, target, digest):
    content = open(gen_path, encoding="utf-8").read().rstrip("\n")
    fname = os.path.basename(gen_path)
    return (f"\n# === triage-applied {fname} md5={digest} "
            f"({datetime.date.today()}) ===\n"
            f"{content}\n"
            f"{target}.update({var})\n"
            f"del {var}\n")


# _plan : walk every (sport x merge) slot; return (blocks to append,
#   human-readable notes about skips).
def _plan(gen_dir, corr_text):
    blocks, notes = [], []
    for s in _SPORTS:
        for pattern, var, target_pat in _MERGES:
            target = target_pat.format(S=s.upper())   # per-sport routing
            fname = pattern.format(s=s)
            path = os.path.join(gen_dir, fname)
            if not os.path.exists(path):
                notes.append(f"  [skip] {fname}: not generated (yet)")
                continue
            digest = _md5(path)
            if digest in corr_text:
                notes.append(f"  [skip] {fname}: this exact version already applied")
                continue
            blocks.append(_blockFor(path, var, target, digest))
            notes.append(f"  [apply] {fname} -> {target}")
    return blocks, notes


# ================================================================== #
# CHUNK 4 -- ORCHESTRATION
# ================================================================== #

def main():
    ap = argparse.ArgumentParser(
        description="Merge triage-generated *_ADDITIONS files into "
                    "corrections.py (backup + verify, idempotent).")
    ap.add_argument("--dir", default="scripts",
                    help="dir holding the generated files (default scripts)")
    ap.add_argument("--corrections", default=None,
                    help="path to corrections.py (default: auto-detect)")
    args = ap.parse_args()

    corr_path = _findCorrections(args.corrections)
    before_mod = _importByPath(corr_path)
    _checkTargets(before_mod)
    before = _sizes(before_mod)

    corr_text = open(corr_path, encoding="utf-8").read()
    blocks, notes = _plan(args.dir, corr_text)
    print(f"corrections.py: {corr_path}")
    for n in notes:
        print(n)
    if not blocks:
        print("nothing new to apply.")
        return

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{corr_path}.bak-{stamp}"
    shutil.copy2(corr_path, backup)
    with open(corr_path, "a", encoding="utf-8") as f:
        f.writelines(blocks)

    # Re-import and prove it: if this import fails, corrections.py is broken
    # -- restore the backup and stop.
    try:
        after = _sizes(_importByPath(corr_path))
    except Exception as e:
        shutil.copy2(backup, corr_path)
        sys.exit(f"merged file failed to import ({e}); corrections.py "
                 f"RESTORED from {backup}")

    print(f"\nbackup: {backup}")
    for t in sorted(set(before) | set(after)):
        b, a = before.get(t, 0), after.get(t, 0)
        print(f"  {t:<22} {b:>9,} -> {a:>9,}  (+{a - b:,})")
    print("done. re-run diag_suspects.py to confirm the flags clear.")


if __name__ == "__main__":
    main()