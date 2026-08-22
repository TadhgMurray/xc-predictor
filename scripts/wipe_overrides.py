# Project: xc-predictor / scripts
# File:    wipe_overrides.py
# Purpose: Clear the overrides the three-pass rebuild regenerates, WITHOUT
#          touching 1.45M lines of history.
#
#     python scripts/wipe_overrides.py            # report
#     python scripts/wipe_overrides.py --write
#     python scripts/wipe_overrides.py --undo
#
# ★ IT APPENDS FIVE LINES INSTEAD OF DELETING A MILLION. Every block after the
#   base dicts is `_X.update({...})`, so the last word wins -- a trailing
#   .clear() is exactly equivalent to deleting every entry above it, and is
#   undone by deleting the block rather than restoring a backup. The file is
#   in git to be the record of decisions nobody can recompute; a wipe that
#   destroys the record to clear the working set trades the wrong thing.
#
# ⚠ THE DROPS SURVIVE, DELIBERATELY. _DISTANCE_DROP and _RESULT_DROP remove
#   junk that NONE of the three passes regenerate:
#
#       pass 1 -> _DISTANCE_OVERRIDES     (regenerated)
#       pass 2 -> _RESULT_OVERRIDE        (regenerated)
#       pass 3 -> _RESULT_DROP            (ADDS to it, never rebuilds it)
#
#   Clearing the drops would delete filtering with nothing to replace it, and
#   pass 3 only finds rows that are still rated -- a row dropped for being
#   unrateable is invisible to it. Pass --drops to clear them anyway.
#
# ! _GENDER_OVERRIDES GOES, because pass 2 rewrites gender per row. Two rows
#   in corrections.py today; both are division-level sex fixes, which is
#   precisely pass 2's job.

import argparse
import io

PATH = "engine/corrections.py"
MARK = "# === WIPE FOR REBUILD (scripts/wipe_overrides.py) ==="

_REGENERATED = ["_DISTANCE_OVERRIDES_XC", "_DISTANCE_OVERRIDES_TF",
                "_RESULT_OVERRIDE_XC", "_RESULT_OVERRIDE_TF",
                "_GENDER_OVERRIDES_XC", "_GENDER_OVERRIDES_TF"]
_DROPS = ["_DISTANCE_DROP_XC", "_DISTANCE_DROP_TF",
          "_RESULT_DROP_XC", "_RESULT_DROP_TF"]


def sizes(path):
    """{dict name: entries} as the file currently resolves."""
    import importlib.util
    import sys
    sys.path.insert(0, "engine")
    spec = importlib.util.spec_from_file_location("corrections_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {n: len(getattr(mod, n, ()) or ())
            for n in _REGENERATED + _DROPS}


def block(names):
    lines = [
        "", MARK,
        "#",
        "# Cleared so the three passes in scripts/rebuild_overrides.py can",
        "# rebuild from evidence. Every entry above this line is still here,",
        "# in git, unchanged -- .update() order means this simply wins.",
        "# Undo with: python scripts/wipe_overrides.py --undo",
    ]
    lines += [f"{n}.clear()" for n in names]
    lines.append(f"# === END WIPE ===")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(
        description="Clear the regenerable overrides by appending a .clear() "
                    "block. Keeps the history.")
    ap.add_argument("--path", default=PATH)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--drops", action="store_true",
                    help="also clear _DISTANCE_DROP / _RESULT_DROP. The "
                         "passes do not regenerate these -- see the header.")
    args = ap.parse_args()

    text = io.open(args.path, encoding="utf-8").read()
    present = MARK in text

    if args.undo:
        if not present:
            print("  no wipe block present. Nothing to undo.")
            return 0
        head, _, rest = text.partition("\n" + MARK)
        _, _, tail = rest.partition("# === END WIPE ===\n")
        out = head + tail
        if not args.write:
            print("  would remove the wipe block. DRY RUN -- pass --write.")
            return 0
        io.open(args.path, "w", encoding="utf-8", newline="\n").write(out)
        print(f"  removed the wipe block from {args.path}")
        for n, c in sizes(args.path).items():
            print(f"    {n:<26} {c:>6,}")
        return 0

    if present:
        print("  a wipe block is already in place. --undo removes it.")
        return 0

    names = list(_REGENERATED) + (list(_DROPS) if args.drops else [])
    before = sizes(args.path)
    print(f"\n  {args.path}\n")
    for n in _REGENERATED + _DROPS:
        fate = "CLEARED" if n in names else "kept"
        print(f"    {n:<26} {before.get(n, 0):>6,}   {fate}")
    if not args.drops:
        print("\n    the drops are kept: no pass regenerates them, and pass 3 "
              "only sees\n    rows that are still rated. --drops overrides "
              "this.")

    if not args.write:
        print("\n  DRY RUN -- pass --write to append the block.\n")
        return 0

    io.open(args.path, "a", encoding="utf-8", newline="\n").write(block(names))
    after = sizes(args.path)
    print("\n  after:")
    for n in _REGENERATED + _DROPS:
        print(f"    {n:<26} {after.get(n, 0):>6,}")
    print(f"\n  appended to {args.path}. Undo with --undo --write.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
